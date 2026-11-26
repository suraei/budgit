"""R-360: the approval guard asks less and the deny guards sound an alarm.
 - the ask on `prepare` is skipped for one exact, fully written form when the user's recorded approval of this project is for
   the very list of prepare commands the project file holds now (a grant keyed on the hash of the approved list);
 - the ask on the approval code is skipped for plain readers (grep, cat, git log ...) with nothing redirected; the answers file stays asked;
 - guard-ask audit lines carry sha256 and a masked head of the command, so a later count can tell one repeated command from many;
 - more than 5 unmarked denies of one guard in 7 days open one ledger item per guard per week."""
import json
import os
import unittest

from helpers import StudioTestCase
import studiolib as sl
import denylog
import hook

CLI = os.path.join(os.path.dirname(os.path.abspath(sl.__file__)), "..", "bin", "budgit")
WORD = "approved" + "_prepare"                         # the answers file; written in two pieces so this file is not itself asked about


class GrantBase(StudioTestCase):
    def setUp(self):
        super().setUp()
        sl.activate(self.project, bind=False)
        self.pj = os.path.join(sl.studio_dir(self.project), "project.json")
        self.set_prepare(["touch one"])

    def set_prepare(self, cmds):
        cfg = json.load(open(self.pj))
        cfg.setdefault("verify", {})["prepare"] = cmds
        json.dump(cfg, open(self.pj, "w"))

    def approve_now(self):
        sl._write_approval(self.project, sl.load_project_config(self.project)["verify"]["prepare"])

    def asked(self, command, project=None):
        got = sl.approval_decision(command, self.project if project is None else project)
        if got is not None:
            self.assertEqual(got[0], "ask", command)
        return got is not None


class GrantByHashTest(GrantBase):
    def forms(self):
        p = self.project
        return ["python3 %s --project %s prepare --approve" % (CLI, p), "python3 %s --project %s prepare" % (CLI, p),
                "budgit --project %s prepare --approve" % p, "budgit prepare --approve", "'%s' prepare --approve" % CLI]

    def test_without_an_approval_the_approve_form_still_asks(self):
        for cmd in self.forms()[:1] + self.forms()[2:4]:
            self.assertTrue(self.asked(cmd), cmd)

    def test_a_recorded_approval_of_the_current_list_lets_the_exact_form_through(self):
        self.approve_now()
        for cmd in self.forms():
            if "--approve" in cmd:
                self.assertFalse(self.asked(cmd), cmd)

    def test_a_changed_list_asks_again(self):
        self.approve_now()
        self.set_prepare(["touch one", "touch two"])
        self.assertTrue(self.asked(self.forms()[0]))
        self.set_prepare(["touch one"])                      # back to the approved list: the grant is for the hash
        self.assertFalse(self.asked(self.forms()[0]))

    def test_an_approval_of_another_project_covers_nothing_here(self):
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        sl.activate(other, bind=False)
        cfg_path = os.path.join(sl.studio_dir(other), "project.json")
        cfg = json.load(open(cfg_path))
        cfg.setdefault("verify", {})["prepare"] = ["touch one"]
        json.dump(cfg, open(cfg_path, "w"))
        sl._write_approval(other, ["touch one"])
        self.assertTrue(self.asked("python3 %s --project %s prepare --approve" % (CLI, self.project)))
        self.assertFalse(self.asked("python3 %s --project %s prepare --approve" % (CLI, other)))

    def test_nothing_but_the_exact_form_is_covered(self):
        self.approve_now()
        p = self.project
        for cmd in ("python3 %s --project %s prepare --approve; echo x" % (CLI, p),
                    "python3 %s --project %s prepare --approve > out.txt" % (CLI, p),
                    "python3 %s --project \"$P\" prepare --approve" % CLI, "python3 $CLI --project %s prepare --approve" % p,
                    "python3 %s --project %s prepare --approve --other" % (CLI, p),
                    "python3 /tmp/evil/budgit --project %s prepare --approve" % p,
                    "echo --approve | xargs budgit prepare", "eval budgit prepare --approve",
                    "budgit --project '*' prepare --approve", "cd x && budgit prepare --approve",
                    "budgit prepare --appr", "budgit prepare --approve | cat", "python3 -c 'x' %s prepare --approve" % CLI):
            self.assertTrue(self.asked(cmd), cmd)

    def test_a_script_named_budgit_in_the_working_folder_is_not_budgit(self):
        self.approve_now()
        with open(os.path.join(self.project, "budgit"), "w") as f:
            f.write("print(1)\n")
        for cmd in ("python3 budgit --project %s prepare --approve" % self.project, "python3 ./budgit prepare --approve"):
            self.assertIsNotNone(sl.approval_decision(cmd, self.project, self.project), cmd)
        self.assertIsNone(sl.approval_decision("python3 %s prepare --approve" % CLI, self.project, self.project))

    def test_a_relative_project_is_read_against_the_commands_folder_not_the_hooks(self):
        self.approve_now()
        sub = os.path.join(self.project, "sub")
        os.makedirs(sub)
        self.assertIsNone(sl.approval_decision("python3 %s --project .. prepare --approve" % CLI, self.project, sub))
        self.assertIsNotNone(sl.approval_decision("python3 %s --project . prepare --approve" % CLI, self.project, sub))
        old = os.getcwd()
        os.chdir(self.project)                                   # the hook's own folder must not decide it
        self.addCleanup(os.chdir, old)
        self.assertIsNotNone(sl.approval_decision("python3 %s --project . prepare --approve" % CLI, self.project, sub))

    def test_a_project_with_no_prepare_commands_has_nothing_to_grant(self):
        self.approve_now()
        self.set_prepare([])
        self.assertTrue(self.asked(self.forms()[0]))


class ReaderTest(GrantBase):
    READS = ("grep -n approve_prepare plugin/scripts/studiolib.py", "grep -rn 'approve_prepare' plugin | head",
             "git log -S approve_prepare --oneline", "git -C /tmp/x grep -n approve_prepare", "echo approve_prepare",
             "wc -l plugin/scripts/studiolib.py | grep approve_prepare")
    ASKS = ("cat %s.json | head" % WORD, "ls -la ~/.budgit/%s.json" % WORD, "wc -l %s.json" % WORD,     # the answers file stays asked
            "find . -name '%s*'" % WORD, "grep x > %s.json" % WORD, "echo x >> %s.json" % WORD, "python3 -c 'import studiolib; studiolib.approve_prepare(1)'",
            "sed -i s/a/b/ %s.json" % WORD, "cp a %s.json" % WORD, "git diff --output=%s.json" % WORD,
            "git diff --output %s.json" % WORD, "grep x $(echo %s.json)" % WORD, "cat %s.json; python3 x" % WORD,
            "echo approve_prepare | sh", "find . -name '%s*' -delete" % WORD, "tee %s.json" % WORD, "git commit -m approve_prepare",
            "git branch approve_prepare", "git grep -O approve_prepare", "FOO=1 grep approve_prepare x", "grep approve_prepare < x",
            "rm %s.json" % WORD, "grep 'unbalanced approve_prepare")

    def test_plain_readers_are_not_asked(self):
        for cmd in self.READS:
            self.assertFalse(self.asked(cmd), cmd)

    def test_anything_else_that_names_the_approval_is_still_asked(self):
        for cmd in self.ASKS:
            self.assertTrue(self.asked(cmd), cmd)


class AuditLineTest(GrantBase):
    def audit_lines(self):
        return open(os.path.join(sl.studio_dir(self.project), "audit.log"), encoding="utf-8").read().splitlines()

    def test_a_guard_ask_line_keeps_its_tag_and_adds_the_hash_and_a_masked_head(self):
        cmd = "python3 /tmp/other/budgit --project %s prepare --approve 4111222233334444" % self.project
        hook._OUT.clear()
        hook.on_pre_tool_use({"tool_name": "Bash", "cwd": self.project, "tool_input": {"command": cmd}}, self.project)
        self.assertEqual(hook._OUT["hookSpecificOutput"]["permissionDecision"], "ask")
        hook._OUT.clear()
        lines = [l for l in self.audit_lines() if " guard-ask Bash " in l]
        self.assertEqual(len(lines), 1, self.audit_lines())
        import hashlib
        self.assertIn("sha256=" + hashlib.sha256(cmd.encode()).hexdigest(), lines[0])
        self.assertIn(' cmd="', lines[0])
        self.assertNotIn("4111222233334444", lines[0])
        self.assertIn('cmd="', lines[0])


class AlarmTest(GrantBase):
    def deny(self, n, guard="verify-forgery", tool="Bash"):
        for i in range(n):
            denylog.log_denies(self.project, tool, {"command": "echo %d" % i}, [(guard, ("deny", "no"))])

    def items(self):
        return [i for i in sl.load_ledger(self.project)["items"] if i["text"].startswith("guard-alarm")]

    def test_a_forged_long_guard_name_opens_nothing_and_leaves_no_inbox_row(self):
        before = len(sl.read_text(os.path.join(sl.studio_dir(self.project), "inbox.md")).splitlines()) \
            if os.path.exists(os.path.join(sl.studio_dir(self.project), "inbox.md")) else 0
        self.deny(7, guard="x" * 60)
        self.assertEqual(denylog.alarm(self.project), [])
        self.assertEqual(self.items(), [])
        after = len(sl.read_text(os.path.join(sl.studio_dir(self.project), "inbox.md")).splitlines()) \
            if os.path.exists(os.path.join(sl.studio_dir(self.project), "inbox.md")) else 0
        self.assertEqual(before, after)

    def test_five_denies_are_quiet_six_open_one_item(self):
        self.deny(5)
        self.assertEqual(denylog.alarm(self.project), [])
        self.deny(1)
        opened = denylog.alarm(self.project)
        self.assertEqual(len(opened), 1)
        item = self.items()[0]
        self.assertIn("guard-alarm verify-forgery:", item["text"])
        self.assertIn("6 denies", item["text"])
        self.assertEqual(item["status"], "open")
        self.assertTrue(item["src"] and item["src"][0].startswith("I-"))

    def test_one_item_per_guard_per_week_closed_ones_included(self):
        self.deny(7)
        self.assertEqual(len(denylog.alarm(self.project)), 1)
        self.deny(3)
        self.assertEqual(denylog.alarm(self.project), [])
        data = sl.load_ledger(self.project)
        data["items"][-1]["status"] = "dropped"
        sl.save_ledger(self.project, data)
        self.assertEqual(denylog.alarm(self.project), [])
        self.assertEqual(len(self.items()), 1)

    def test_an_item_older_than_a_week_does_not_hold_a_new_one_back(self):
        self.deny(7)
        denylog.alarm(self.project)
        data = sl.load_ledger(self.project)
        data["items"][-1]["created"] = "2000-01-01T00:00:00.000Z"
        sl.save_ledger(self.project, data)
        self.assertEqual(len(denylog.alarm(self.project)), 1)
        self.assertEqual(len(self.items()), 2)

    def test_each_guard_counts_on_its_own(self):
        self.deny(4)
        self.deny(4, guard="raw-store")
        self.assertEqual(denylog.alarm(self.project), [])
        self.deny(2, guard="raw-store")
        self.assertEqual(len(denylog.alarm(self.project)), 1)
        self.assertIn("raw-store", self.items()[0]["text"])

    def test_denies_marked_as_false_positives_do_not_count(self):
        self.deny(6)
        denies, _ = denylog._parse(self.project)
        denylog.mark_fp(self.project, denies[0]["id"], "was fine")
        self.assertEqual(denylog.alarm(self.project), [])

    def test_old_denies_do_not_count(self):
        path = os.path.join(sl.studio_dir(self.project), "audit.log")
        with open(path, "a", encoding="utf-8") as fh:
            for i in range(8):
                fh.write('2000-01-01T00:00:00.000Z guard-deny %08x Bash raw-store sha256=ab cmd="x" why="y"\n' % i)
        self.assertEqual(denylog.alarm(self.project), [])

    def test_a_ledger_with_a_hand_edit_not_accepted_yet_is_left_alone(self):
        self.deny(7)
        path = os.path.join(sl.studio_dir(self.project), "ledger.json")
        data = json.load(open(path))
        data["items"].append({"id": "R-9", "src": ["legacy"], "text": "hand edit", "status": "open", "evidence": ""})
        json.dump(data, open(path, "w"))
        self.assertEqual(denylog.alarm(self.project), [])
        self.assertEqual(len(json.load(open(path))["items"]), 1)

    def test_a_project_without_a_ledger_is_left_alone(self):
        os.remove(os.path.join(sl.studio_dir(self.project), "ledger.json"))
        self.deny(7)
        self.assertEqual(denylog.alarm(self.project), [])

    def test_the_hook_sounds_it_on_the_sixth_deny(self):
        cmd = "echo PASS > %s/verify/R-1.md" % sl.studio_dir(self.project)
        for _ in range(6):
            hook._OUT.clear()
            hook.on_pre_tool_use({"tool_name": "Bash", "cwd": self.project, "tool_input": {"command": cmd}}, self.project)
            self.assertEqual(hook._OUT["hookSpecificOutput"]["permissionDecision"], "deny")
        hook._OUT.clear()
        self.assertEqual(len(self.items()), 1)
        self.assertIn("guard-alarm verify-forgery:", self.items()[0]["text"])


if __name__ == "__main__":
    unittest.main()
