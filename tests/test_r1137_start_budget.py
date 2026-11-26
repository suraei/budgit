"""R-1137 item 1: ONE per-role SessionStart budget (startbudget.TABLE), measured at the real door: hook.py session-start run as a
subprocess with a temp HOME and BUDGIT_HOME, a seeded portfolio, and a big ledger (200 open items, a long progress.md).
Every row is tagged OUTCOME: it measures what a new session of that role is handed, not a count of a file.
Set R1137_BASE_HOOK to another hook.py (e.g. the one before this change) and the same scene is measured there too."""
import json
import os
import subprocess
import sys

from helpers import ROOT
import studiolib as sl
import portfolio
import roles
import startbudget
from test_team_roles import RolesBase, sid

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
HARD_SAFETY = ("Never push, publish or post", "no AI attribution trailer", "Protected project names and content never leave",
               "Never reset --hard, clean, stash or force-push")
TM, PM, LANE, SUB, CHILD_OF_TM = sid(1), sid(2), sid(3), sid(4), sid(5)


class StartBudgetBase(RolesBase):
    def setUp(self):
        super().setUp()
        self.fakehome = os.path.join(self.tmp, "fake-home")
        os.makedirs(self.fakehome)
        self.write_config()
        self.big = self.make_project("big")
        data = sl.load_ledger(self.big)
        for n in range(200):
            sl.add_item(data, "open item number %d with a fairly long description of what it is meant to do for the user" % n, ["legacy"])
        sl.save_ledger(self.big, data)
        with open(os.path.join(sl.studio_dir(self.big), "progress.md"), "w", encoding="utf-8") as f:
            f.write("\n".join("- progress line %d %s" % (n, "x" * 200) for n in range(300)))
        for n in range(8):
            os.makedirs(os.path.join(sl.studio_dir(self.big), "reports"), exist_ok=True)
            with open(os.path.join(sl.studio_dir(self.big), "reports", "2026-11-1%d-report.md" % n), "w", encoding="utf-8") as f:
                f.write("x")
        self.fresh = self.make_project("fresh")
        data = portfolio.registry()
        data["tm"] = TM
        for e in data["projects"]:
            if os.path.basename(e["path"]) == "big":
                e["pm"] = PM
        portfolio.save(data)

    def bind(self, session, project, parent=None):
        folder = os.path.join(self.home, "sessions")
        os.makedirs(folder, exist_ok=True)
        rec = {"project": project, "ts": "2026-11-16T10:00:00", "last_seen": "2026-11-16T10:00:00"}
        if parent:
            rec["parent"] = parent
        with open(os.path.join(folder, session + ".json"), "w", encoding="utf-8") as f:
            json.dump(rec, f)

    def start(self, payload, hook=HOOK):
        env = dict(os.environ, HOME=self.fakehome, BUDGIT_HOME=self.home, STUDIO_HOME=self.home)
        r = subprocess.run([sys.executable, hook, "session-start"], input=json.dumps(payload), env=env,
                           capture_output=True, text=True, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout) if r.stdout.strip() else {}
        return (out.get("hookSpecificOutput") or {}).get("additionalContext") or ""

    def scenes(self):
        """role -> the payload of a real start of that role in the big project."""
        self.bind(TM, self.big)
        self.bind(PM, self.big)
        self.bind(LANE, self.big, parent=PM)
        self.bind(SUB, self.big, parent=PM)
        return {"TM": {"session_id": TM, "source": "startup", "cwd": self.big},
                "PM": {"session_id": PM, "source": "startup", "cwd": self.big},
                "lane": {"session_id": LANE, "source": "startup", "cwd": self.big},
                "subagent": {"session_id": SUB, "source": "startup", "cwd": self.big, "agent_id": "a1", "agent_type": "builder"}}


class StartBudgetTest(StartBudgetBase):
    def test_every_role_starts_within_its_lines_and_bytes_with_the_hard_safety_lines(self):  # OUTCOME
        report = []
        for role, payload in self.scenes().items():
            text = self.start(payload)
            lines, size = len(text.splitlines()), len(text.encode("utf-8"))
            report.append("%s: %d lines, %d bytes" % (role, lines, size))
            max_lines, max_bytes = startbudget.TABLE[role]
            with self.subTest(role=role):
                self.assertLessEqual(lines, max_lines, text)
                self.assertLessEqual(size, max_bytes, text)
                self.assertLess(size, 4096)
                for token in HARD_SAFETY:
                    self.assertIn(token, text)
                self.assertIn("budgit rules show", text)
                self.assertIn("budgit ledger list --open", text)
                self.assertLessEqual(text.count("budgit budget:"), 1, text)       # the budget text is in the start output at most once
        print("\nR-1137 after: " + "; ".join(report))
        base = os.environ.get("R1137_BASE_HOOK")
        if base:
            before = []
            for role, payload in self.scenes().items():
                if role == "subagent":
                    payload = dict(payload)
                text = self.start(payload, hook=base)
                before.append("%s: %d lines, %d bytes" % (role, len(text.splitlines()), len(text.encode("utf-8"))))
            print("R-1137 before: " + "; ".join(before))

    def test_a_degraded_section_leaves_its_pointer_never_a_silent_cut(self):  # OUTCOME
        text = self.start(self.scenes()["lane"])
        self.assertIn("Open ledger items (200):", text)
        self.assertIn("open items: budgit ledger list --open", text)          # the item list became one pointer line
        self.assertNotIn("open item number 5 ", text)
        self.assertIn("team rules digest: budgit rules show <topic>", text)   # the rules digest too

    def test_the_table_has_one_row_per_role_each_under_4_kb(self):  # CAUSE
        self.assertEqual({r: v[0] for r, v in startbudget.TABLE.items()}, {"subagent": 15, "lane": 25, "PM": 40, "TM": 60})
        self.assertTrue(all(v[1] < 4096 for v in startbudget.TABLE.values()))

    def test_fit_never_goes_over_even_when_every_section_is_huge(self):  # OUTCOME
        parts = [startbudget.section(tag, "\n".join("%s line %d %s" % (tag, n, "y" * 150) for n in range(80)))
                 for tag in startbudget.TAGS if startbudget.TAGS[tag][0] is not None]
        parts.insert(0, startbudget.section("role", "budgit team role: lane\n" + "\n".join(HARD_SAFETY)))
        for role, (max_lines, max_bytes) in startbudget.TABLE.items():
            text, report = startbudget.fit(role, parts)
            with self.subTest(role=role):
                self.assertLessEqual(len(text.splitlines()), max_lines)
                self.assertLessEqual(len(text.encode("utf-8")), max_bytes)
                for token in HARD_SAFETY:
                    self.assertIn(token, text)
                self.assertIn("budgit rules show", text)


class RoleFromTheTMTest(StartBudgetBase):
    def test_a_pm_session_started_by_the_tm_gets_the_pm_card_and_is_registered(self):  # OUTCOME
        self.bind(CHILD_OF_TM, self.fresh, parent=TM)
        text = self.start({"session_id": CHILD_OF_TM, "source": "startup", "cwd": self.fresh})
        self.assertIn("budgit team role: PM", text)
        self.assertNotIn("Lane (technician)", text)
        self.assertEqual(self.fields()[1]["fresh"], CHILD_OF_TM)

    def test_a_lane_started_by_a_pm_still_gets_the_lane_card(self):  # OUTCOME
        self.bind(LANE, self.big, parent=PM)
        text = self.start({"session_id": LANE, "source": "startup", "cwd": self.big})
        self.assertIn("budgit team role: lane", text)
        self.assertIn("Lane (technician)", text)
        self.assertEqual(self.fields()[1]["big"], PM)

    def test_a_child_of_the_tm_in_a_project_that_has_a_pm_is_a_lane(self):  # OUTCOME
        self.bind(CHILD_OF_TM, self.big, parent=TM)
        text = self.start({"session_id": CHILD_OF_TM, "source": "startup", "cwd": self.big})
        self.assertIn("budgit team role: lane", text)
        self.assertEqual(self.fields()[1]["big"], PM)

    def test_a_child_of_a_pm_in_a_project_without_a_pm_is_a_lane_and_not_registered(self):  # OUTCOME
        self.bind(LANE, self.fresh, parent=PM)
        text = self.start({"session_id": LANE, "source": "startup", "cwd": self.fresh})
        self.assertIn("budgit team role: lane", text)
        self.assertIsNone(self.fields()[1]["fresh"])

    def test_classify_stays_pure_for_the_tm_child_rule(self):  # CAUSE
        entries = [{"path": self.fresh, "name": "fresh", "pm": None}]
        self.assertEqual(roles.classify("c", "tm-1", entries, self.fresh, parent="tm-1")["role"], "PM")
        self.assertEqual(roles.classify("c", "tm-1", entries, self.fresh, parent="pm-9")["role"], "lane")
        entries[0]["pm"] = "pm-1"
        self.assertEqual(roles.classify("c", "tm-1", entries, self.fresh, parent="tm-1")["role"], "lane")
