"""R-1090: a read-only `pdftotext -layout <file> - | sed -E 's/[A-Z]{2}[0-9]{2}.../[ACCT-REDACTED]/g'` was refused: the sed
script word reached the glob engine, which raises on the reversed range `[T-R]`, and the guard returned "ask:pattern". A shell
does not expand an invalid pattern, it keeps the word literal; so the guard now judges that word as a literal path (home,
cwd, `~` and `$` checks all still apply). The sed script is NOT parsed. Synthetic fixture: project acme, a synthetic HOME. The
hook is driven in-process (hook.on_pre_tool_use) as a subagent, cwd at the project root unless a test says otherwise."""
import os
import unittest
from unittest import mock

import hook
import studiolib as sl
from test_guard_trust import GuardCase, git


class InvalidGlobIsLiteralTest(GuardCase):
    def setUp(self):
        super().setUp()
        repo = os.path.join(self.tmp, "Dev", "acme")
        os.makedirs(repo)
        git(repo, "init", "-q")
        sl.activate(repo, bind=False)
        open(os.path.join(repo, "f.txt"), "w").write("1")
        git(repo, "add", "-f", "-A")
        git(repo, "commit", "-q", "-m", "init")
        self.repo = os.path.realpath(repo)
        home = os.path.realpath(os.path.expanduser("~"))
        self.dl = os.path.join(home, "Downloads")
        os.makedirs(self.dl)
        self.budgit = os.path.join(home, ".budgit")
        os.makedirs(self.budgit, exist_ok=True)
        open(os.path.join(self.budgit, "trust.json"), "w").write("1")

    def says(self, cmd, agent=True, cwd=None):
        hook._OUT.clear()
        payload = {"tool_name": "Bash", "cwd": cwd or self.repo, "tool_input": {"command": cmd}}
        if agent:
            payload.update(agent_id="a1", session_id="s1")
        hook.on_pre_tool_use(payload, self.repo)
        got = (hook._OUT.get("hookSpecificOutput") or {}).get("permissionDecision")
        hook._OUT.clear()
        return got

    def stops(self, cmd, **kw):
        self.assertIn(self.says(cmd, **kw), ("ask", "deny"), cmd)

    def test_refused_shape_is_allowed(self):
        cmd = "pdftotext -layout %s/acme-rib.pdf - | sed -E 's/[A-Z]{2}[0-9]{2}[ 0-9A-Z]{14,}/[ACCT-REDACTED]/g'" % self.dl
        self.assertIsNone(self.says(cmd))
        self.assertIsNone(self.says(cmd, agent=False))

    def test_allowed_shape_stays_allowed(self):
        cmd = "pdftotext -layout %s/acme-invoice.pdf - | sed -E 's/FR76[ 0-9A-Z]{10,}/[IBAN-REDACTED]/g'" % self.dl
        self.assertIsNone(self.says(cmd))

    def test_invalid_glob_naming_a_home_path_still_stops(self):
        self.stops("cat %s/[z-a]x > %s/trust.json" % (self.budgit, self.budgit))
        self.stops("tee %s/[z-a]" % self.budgit)

    def test_unsafe_sed_shapes_stay_stopped(self):
        t = os.path.join(self.budgit, "trust.json")
        # `sed -n '1w <home file>'` and `s|a|b|w <home file>` are NOT stopped on dbd6f288 either (the word has a space and no glob
        # character, so it is joined to the cwd and judged as one path): a gap of main, outside R-1090, reported to the PM.
        self.stops("sed -i.bak -es/1/2/ %s" % t)

    def test_relative_w_file_with_cwd_inside_a_home_stops(self):
        self.stops("sed -n 's/1/2/w trust.json' /tmp/f", cwd=self.budgit)

    def test_sed_in_place_on_a_home_file_still_stops(self):
        self.stops("sed -i '' 's/1/2/' %s/trust.json" % self.budgit)

    def test_redirect_to_a_home_file_still_stops(self):
        self.stops("sed 's/[A-Z]{2}/x/g' f.txt > %s/trust.json" % self.budgit)


    def test_reversed_range_set_that_names_the_home_stops(self):
        # bash reads `[z-ab]` as the set `b`: `.[z-ab]udgit` is `.budgit`
        self.stops("tee %s/.[z-ab]udgit/trust.json" % os.path.dirname(self.budgit))
        self.stops("echo x > %s/.[z-ab]udgit/trust.json" % os.path.dirname(self.budgit))

    def test_the_except_branch_on_any_python(self):
        real = sl._glob.glob
        calls = []

        def raises_once(pattern, *a, **k):
            calls.append(pattern)
            if len(calls) == 1:
                raise ValueError("bad character range")          # what Python 3.9 fnmatch does
            return real(pattern, *a, **k)

        home = os.path.dirname(self.budgit)
        with mock.patch.object(sl._glob, "glob", side_effect=raises_once):
            self.assertEqual(sl._home_one("%s/.[z-ab]udgit/trust.json" % home, [self.repo], False, False), "deny")
        with mock.patch.object(sl._glob, "glob", side_effect=ValueError("x")):
            self.assertIsNone(sl._home_one("%s/Downloads/[z-a]x" % home, [self.repo], False, False))      # an empty set: literal
            self.assertEqual(sl._home_one("%s/Downloads/[z-ab]x" % home, [self.repo], False, False), "ask:pattern")
        self.assertIn("pattern", sl._HOME_MEMO_CLASSES)

    def test_drop_reversed_ranges(self):
        self.assertEqual(sl._drop_reversed_ranges(".[z-ab]x"), ".[b]x")
        self.assertIsNone(sl._drop_reversed_ranges("[z-a]x"))
        self.assertEqual(sl._drop_reversed_ranges("[a-z]x[!z-a]"), "[a-z]x?")


if __name__ == "__main__":
    unittest.main()
