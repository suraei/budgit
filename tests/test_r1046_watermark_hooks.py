"""R-1046: the watermark hook (`budgit watermark on|off`) and the R-1042 attribution hooks (`budgit githooks install`) coexist
in any order. The R-1042 hook owns commit-msg; the watermark hook is then commit-msg.local, which the stub runs first. Temp repos
only, fake HOME, plugin installed from a copy outside git."""
import io
import os
import unittest

from test_r1042_githooks import RepoCase

import githooks
import watermark

FOLDED = "Co-Authored-By:\n Claude <noreply@anthropic.com>"
STANDARD = "Co-Authored-By: Claude <noreply@anthropic.com>"


class WatermarkChainTest(RepoCase):
    def paths(self):
        hooks = os.path.join(self.repo, ".git", "hooks")
        return os.path.join(hooks, "commit-msg"), os.path.join(hooks, "commit-msg.local")

    def text(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def status_line(self):
        out = io.StringIO()
        githooks.run("status", self.repo, out=out)
        return out.getvalue()

    def check_commits(self):
        ok = self.commit(self.repo, "R-1046 subject\n\nbody\n\n" + STANDARD)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        msg = self.git(self.repo, "log", "-1", "--format=%B").stdout
        self.assertNotIn("Claude", msg)                              # the watermark hook stripped the standard trailer
        bad = self.commit(self.repo, "R-1046 subject\n\nbody\n\n" + FOLDED)
        self.assertNotEqual(bad.returncode, 0)                       # the folded one is past the watermark sed: R-1042 rejects it
        self.assertIn("R-1042", bad.stderr)
        self.assertIn("installed, chained: watermark", self.status_line())

    def test_watermark_on_then_githooks_install_keeps_chaining_it(self):
        self.assertEqual(watermark.install(self.repo, "strip-only"), "installed")
        self.install()
        msg, local = self.paths()
        self.assertIn("# budgit-githook commit-msg", self.text(msg))
        self.assertIn("# budgit-watermark", self.text(local))
        self.check_commits()

    def test_githooks_install_then_watermark_on_writes_commit_msg_local(self):
        self.install()
        before = self.text(self.paths()[0])
        self.assertEqual(watermark.install(self.repo, "strip-only"), "installed")
        msg, local = self.paths()
        self.assertEqual(self.text(msg), before)                     # the R-1042 hook is still at commit-msg, untouched
        self.assertIn("# budgit-watermark", self.text(local))
        self.assertEqual(watermark.status(self.repo), "on (strip-only)")
        self.assertEqual(watermark.install(self.repo, "strip-only"), "already installed")
        self.check_commits()

    def test_watermark_off_after_both_keeps_the_r1042_hook_and_drops_the_local(self):
        self.install()
        watermark.install(self.repo, "strip-only")
        msg, local = self.paths()
        before = self.text(msg)
        self.assertEqual(watermark.remove(self.repo), "removed")
        self.assertTrue(os.path.isfile(msg))
        self.assertEqual(self.text(msg), before)
        self.assertFalse(os.path.lexists(local))
        self.assertEqual(watermark.status(self.repo), "off")
        self.assertEqual(watermark.remove(self.repo), "not installed")
        self.assertNotEqual(self.commit(self.repo, "R-1046 x\n\n" + FOLDED).returncode, 0)   # R-1042 still guards

    def test_watermark_off_with_no_r1042_hook_is_unchanged(self):
        self.assertEqual(watermark.install(self.repo, "strip-only"), "installed")
        msg, local = self.paths()
        self.assertIn("# budgit-watermark", self.text(msg))
        self.assertFalse(os.path.lexists(local))
        self.assertEqual(watermark.remove(self.repo), "removed")
        self.assertFalse(os.path.lexists(msg))
        self.assertEqual(watermark.remove(self.repo), "not installed")

    def guarded(self, wm_on, step):
        """R-1042 hook at commit-msg; a standard trailer is stripped+passes when the watermark is on, rejected when off; folded rejected."""
        msg, local = self.paths()
        self.assertIn("# budgit-githook commit-msg", self.text(msg), step)
        self.assertEqual(watermark.status(self.repo), "on (strip-only)" if wm_on else "off", step)
        self.assertEqual(os.path.isfile(local), wm_on, step)
        before = self.git(self.repo, "rev-list", "--count", "--all", check=False).stdout.strip()
        std = self.commit(self.repo, "R-1046 s\n\nbody\n\n" + STANDARD)
        if wm_on:
            self.assertEqual(std.returncode, 0, step + std.stderr)
            self.assertNotIn("Claude", self.git(self.repo, "log", "-1", "--format=%B").stdout, step)
        else:
            self.assertNotEqual(std.returncode, 0, step)
            self.assertIn("R-1042", std.stderr, step)
        bad = self.commit(self.repo, "R-1046 s\n\n" + FOLDED)
        self.assertNotEqual(bad.returncode, 0, step)
        self.assertIn("R-1042", bad.stderr, step)
        after = self.git(self.repo, "rev-list", "--count", "--all", check=False).stdout.strip()
        self.assertEqual(int(after or 0) - int(before or 0), 1 if wm_on else 0, step)

    def test_full_sequence_on_install_off_on_install(self):
        self.assertEqual(watermark.install(self.repo, "strip-only"), "installed")
        self.install()
        self.guarded(True, "on+install")
        self.assertEqual(watermark.remove(self.repo), "removed")
        self.guarded(False, "off")
        self.assertEqual(watermark.install(self.repo, "strip-only"), "installed")
        self.guarded(True, "on again")
        self.assertEqual(self.install()["hooks"], {"commit-msg": "unchanged", "pre-push": "unchanged"})
        self.guarded(True, "install again")
        self.assertIn("installed, chained: watermark", self.status_line())

    def test_reverse_order_install_on_off(self):
        self.install()
        self.guarded(False, "install")
        watermark.install(self.repo, "strip-only")
        self.guarded(True, "on")
        self.assertEqual(watermark.remove(self.repo), "removed")
        self.guarded(False, "off")

    def foreign(self, path):
        with open(path, "w") as fh:
            fh.write('#!/bin/sh\necho ran >> "$(dirname "$0")/foreign.ran"\nexit 0\n')
        os.chmod(path, 0o755)

    def test_foreign_hook_then_watermark_on_then_githooks_install_keeps_the_foreign_hook(self):
        msg, local = self.paths()
        self.foreign(msg)
        self.assertEqual(watermark.install(self.repo, "strip-only"), "refused: existing commit-msg hook")
        self.assertIn("foreign.ran", self.text(msg))
        self.install()
        self.assertIn("foreign.ran", self.text(local))                 # moved, not lost
        self.assertIn("# budgit-githook commit-msg", self.text(msg))
        ran = os.path.join(os.path.dirname(msg), "foreign.ran")
        self.assertNotEqual(self.commit(self.repo, "R-1046 x\n\n" + FOLDED).returncode, 0)
        self.assertTrue(os.path.isfile(ran))                           # the foreign hook still ran
        self.assertEqual(watermark.install(self.repo, "strip-only"), "refused: existing commit-msg hook")
        self.assertIn("foreign.ran", self.text(local))                 # watermark never overwrites it

    def test_old_watermark_at_local_when_watermark_on_runs_again_no_duplicate(self):
        self.install()
        watermark.install(self.repo, "strip-only")
        msg, local = self.paths()
        before = self.text(local)
        self.assertEqual(watermark.install(self.repo, "strip-only"), "already installed")
        self.assertEqual(self.text(local), before)
        names = os.listdir(os.path.dirname(msg))
        self.assertFalse(any(n.startswith("commit-msg.") and n not in ("commit-msg.local", "commit-msg.sample") for n in names))

    def test_watermark_off_with_a_foreign_local_does_not_delete_it(self):
        self.install()
        msg, local = self.paths()
        self.foreign(local)
        self.assertEqual(watermark.remove(self.repo), "refused: not Budgit's hook")
        self.assertIn("foreign.ran", self.text(local))
        self.assertIn("# budgit-githook commit-msg", self.text(msg))


if __name__ == "__main__":
    unittest.main()
