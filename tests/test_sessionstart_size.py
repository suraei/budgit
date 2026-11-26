"""R-849: the SessionStart payload of a bound coordinator/lead session stays small (target <= 6000 chars). Each context
piece is measured by the hook's own _context calls, so a new nudge shows up here."""
import json
import os
import subprocess
import sys

from helpers import StudioTestCase, ROOT
import studiolib as sl

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
LIMIT = 6000
# runs the hook in-process with _context/emit recorded; prints [(first 60 chars, length)] as JSON
PROBE = (
    "import json,sys\n"
    "sys.path.insert(0, %r)\n"
    "import hook\n"
    "parts=[]\n"
    "orig=hook._context\n"
    "def rec(event, text):\n"
    "    parts.append([text[:70], len(text)])\n"
    "    orig(event, text)\n"
    "hook._context=rec\n"
    "sys.argv=['hook.py','session-start']\n"
    "sys.stdin=__import__('io').StringIO(json.dumps({'session_id':'s1','source':'startup','cwd':%r}))\n"
    "hook.main()\n"
    "sys.stderr.write('PARTS'+json.dumps(parts))\n"
)


class SessionStartSizeTest(StudioTestCase):
    def measure(self):
        sl.activate(self.project, "s1")
        code = PROBE % (os.path.join(ROOT, "plugin", "scripts"), self.project)
        r = subprocess.run([sys.executable, "-c", code], env=dict(os.environ, STUDIO_HOME=self.home),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        parts = json.loads(r.stderr.split("PARTS", 1)[1])
        total = len(json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]) if r.stdout.strip() else 0
        return parts, total

    def test_a_900_item_ledger_and_a_long_progress_file_still_fit(self):
        sl.activate(self.project, "s1")
        data = sl.load_ledger(self.project)
        for n in range(900):
            sl.add_item(data, "open item number %d with a fairly long description of what it is meant to do for the user" % n, ["legacy"])
        sl.save_ledger(self.project, data)
        with open(os.path.join(sl.studio_dir(self.project), "progress.md"), "w", encoding="utf-8") as f:
            f.write("\n".join("- progress line %d %s" % (n, "x" * 280) for n in range(400)))
        parts, total = self.measure()
        detail = "; ".join("%d %s" % (n, t.replace("\n", " ")) for t, n in parts)
        self.assertLessEqual(total, LIMIT, "total %d chars: %s" % (total, detail))
        self.assertIn("more open: `budgit ledger list`", sl.session_summary(self.project, "s1"))

    def test_a_fresh_start_stays_capped_while_a_compact_or_resume_start_lists_the_full_progress(self):
        sl.activate(self.project, "s1")
        with open(os.path.join(sl.studio_dir(self.project), "progress.md"), "w", encoding="utf-8") as f:
            f.write("\n".join("- progress line %d" % n for n in range(100)))
        fresh = sl.session_summary(self.project, "s1")
        self.assertLessEqual(len(fresh), LIMIT)
        self.assertNotIn("progress line 0\n", fresh + "\n")
        self.assertIn("progress line 99", fresh)
        full = sl.session_summary(self.project, "s1", full=True)
        for n in (0, 50, 99):
            self.assertIn("progress line %d" % n, full)

    def fill(self, count, doing=None):
        sl.activate(self.project, "s1")
        data = sl.load_ledger(self.project)
        for n in range(count):
            sl.add_item(data, "open item number %d" % n, ["legacy"])
        if doing is not None:
            data["items"][doing]["status"] = "doing"
        sl.save_ledger(self.project, data)

    def test_the_open_list_caps_at_exactly_eight_then_counts_the_rest(self):
        self.fill(20)
        text = sl.session_summary(self.project, "s1")
        self.assertEqual(sum(1 for l in text.splitlines() if l.startswith("  R-")), 8)
        self.assertIn("(+12 more open", text)

    def test_nine_open_items_show_eight_and_one_more(self):
        self.fill(9)
        text = sl.session_summary(self.project, "s1")
        self.assertEqual(sum(1 for l in text.splitlines() if l.startswith("  R-")), 8)
        self.assertIn("(+1 more open", text)

    def test_a_late_doing_item_is_listed_first(self):
        self.fill(30, doing=25)
        listed = [l for l in sl.session_summary(self.project, "s1").splitlines() if l.startswith("  R-")]
        self.assertIn("[doing]", listed[0])
        self.assertIn("open item number 25", listed[0])

    def test_agents_state_the_ten_line_hand_back(self):
        for name in ("builder", "verifier"):
            with open(os.path.join(ROOT, "plugin", "agents", name + ".md"), encoding="utf-8") as f:
                self.assertIn("at most 10 lines plus the evidence block", f.read(), name)

    def test_a_bound_session_start_payload_stays_under_the_limit(self):
        parts, total = self.measure()
        detail = "; ".join("%d %s" % (n, t.replace("\n", " ")) for t, n in parts)
        self.assertLessEqual(total, LIMIT, "total %d chars: %s" % (total, detail))
