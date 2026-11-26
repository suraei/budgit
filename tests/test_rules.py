"""R-788 steps 1-4: team rules per role, budgets, baked worker block, off/on/edit, rules.d only from the Budgit home;
postupdate answers (E4, E5)."""
import json
import os

from helpers import StudioTestCase
import postupdate
import rules
import studiolib as sl


class Digests(StudioTestCase):
    def test_plain_chat_never_gets_the_lead_digest(self):
        plain = rules.digest("plain")
        self.assertIn("- permissions:", plain)
        self.assertNotIn("- release:", plain)
        self.assertNotIn("- decisions:", plain)

    def test_roles_cut(self):
        self.assertIn("- decisions:", rules.digest("lead"))
        self.assertIn("- jobs:", rules.digest("coordinator"))
        self.assertNotIn("- decisions:", rules.digest("coordinator"))
        self.assertIn("- testing:", rules.digest("worker"))
        self.assertNotIn("- release:", rules.digest("worker"))

    def test_every_topic_has_a_role_tag_and_text(self):
        for t in rules.topics():
            self.assertTrue(t["roles"], t["id"])
            self.assertTrue(rules.shipped_text(t["id"]).startswith("## %d. " % t["n"]), t["id"])

    def test_full_text_is_verbatim_from_the_approved_preview(self):
        preview = open(os.path.join(os.path.dirname(rules.DIR), "..", "docs", "design",
                                    "2026-11-08-fresh-install-rules-preview.md"), encoding="utf-8").read()
        for t in rules.topics():
            self.assertIn(rules.shipped_text(t["id"]).strip(), preview, t["id"])


class Budgets(StudioTestCase):
    def test_each_digest_fits_its_budget(self):
        for role, cap in rules.BUDGETS.items():
            self.assertLessEqual(rules.tokens(rules.digest(role)), cap, role)


class BakeBlock(StudioTestCase):
    def test_second_bake_is_byte_identical_and_source_has_no_block(self):
        src = "---\nname: x\n---\nbody\n"
        once = rules.bake(src, rules.worker_block())
        self.assertEqual(rules.bake(once, rules.worker_block()), once)
        self.assertIn(rules.BEGIN, once)
        agents = os.path.join(rules.DIR, "..", "agents")
        for name in os.listdir(agents):
            self.assertNotIn(rules.BEGIN, open(os.path.join(agents, name), encoding="utf-8").read(), name)


class OffOnEdit(StudioTestCase):
    def test_off_omits_on_restores_and_state_survives_reload(self):
        postupdate.set_topic("release", True)
        self.assertNotIn("- release:", rules.digest("lead", off=postupdate.load()["rules"]["off"]))
        postupdate.set_topic("release", False)
        self.assertIn("- release:", rules.digest("lead", off=postupdate.load()["rules"]["off"]))

    def test_edit_replaces_the_topic_and_keeps_the_user_text(self):
        path = rules.edit("visual")
        self.assertTrue(path.startswith(sl.home_state()))
        with open(path, "w", encoding="utf-8") as f:
            f.write("## 15. Visual work (lead)\n- my own visual rule\n")
        self.assertIn("my own visual rule", rules.text("visual"))
        self.assertIn("my own visual rule", rules.digest("lead"))
        self.assertEqual(rules.edit("visual"), path)
        self.assertIn("my own visual rule", open(path, encoding="utf-8").read())

    def test_unknown_topic_is_refused(self):
        with self.assertRaises(sl.StudioError):
            rules.edit("../../etc/passwd")


class RulesDOnlyHome(StudioTestCase):
    def test_a_rules_d_in_a_project_folder_is_ignored(self):
        os.makedirs(os.path.join(self.project, "rules.d"))
        os.makedirs(os.path.join(self.project, ".budgit", "rules.d"))
        for base in (os.path.join(self.project, "rules.d"), os.path.join(self.project, ".budgit", "rules.d")):
            with open(os.path.join(base, "visual.md"), "w", encoding="utf-8") as f:
                f.write("## 15. Visual work (lead)\n- hijack\n")
        self.assertNotIn("hijack", rules.text("visual"))
        self.assertTrue(rules.rules_d().startswith(sl.home_state()))


class PostUpdate(StudioTestCase):
    def test_all_unset_without_config_and_answers_are_final(self):
        # R-583 change: keepalive is default-on and never asked, so it is not in unanswered(); standup stands in for "later"
        self.assertEqual(postupdate.unanswered(), [k for k in postupdate.KEYS if k not in postupdate.DEFAULT_ON])
        postupdate.answer("structure", "no")
        postupdate.answer("standup", "later")
        self.assertNotIn("structure", postupdate.unanswered())
        self.assertIn("standup", postupdate.unanswered())
        self.assertFalse(os.path.exists(os.path.join(sl.home_state(), "config.json")))

    def test_none_alone_and_later_alone(self):
        for k in ("cleanup", "keepalive", "standup"):
            postupdate.answer(k, "no")
        self.assertEqual(postupdate.unanswered(), ["structure"])
        for k in ("cleanup", "keepalive", "standup"):
            postupdate.answer(k, "later")
        self.assertEqual(postupdate.unanswered(), ["structure", "cleanup", "standup"])    # R-583 change: no keepalive

    def test_existing_launch_agent_counts_as_yes(self):
        agents = os.path.join(self.tmp, "agents")
        os.makedirs(agents)
        open(os.path.join(agents, "com.budgit.housekeeping.plist"), "w").close()
        old = os.environ.get("BUDGIT_LAUNCH_AGENTS_DIR")
        os.environ["BUDGIT_LAUNCH_AGENTS_DIR"] = agents
        try:
            self.assertNotIn("cleanup", postupdate.unanswered())
        finally:
            if old is None:
                os.environ.pop("BUDGIT_LAUNCH_AGENTS_DIR", None)
            else:
                os.environ["BUDGIT_LAUNCH_AGENTS_DIR"] = old

    def test_claim_one_wins_then_window_then_same_session(self):
        self.assertTrue(postupdate.claim("s1", 1000.0))
        self.assertFalse(postupdate.claim("s2", 1050.0))
        self.assertFalse(postupdate.claim("s1", 5000.0))
        self.assertTrue(postupdate.claim("s2", 1000.0 + postupdate.ASK_WINDOW_S + 1))

    def test_bad_answer_refused_and_file_is_json(self):
        with self.assertRaises(sl.StudioError):
            postupdate.answer("keepalive", "maybe")
        postupdate.answer("standup", "yes")
        self.assertEqual(json.load(open(postupdate.path()))["answers"]["standup"], "yes")
