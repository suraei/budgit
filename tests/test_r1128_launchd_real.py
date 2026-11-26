"""R-1128 / I-1713: ONE real launchd proof run. Skipped unless BUDGIT_REAL_LAUNCHD=1, so the normal suite never touches launchd.

What it does (the owner approved exactly one temporary launch agent and its removal at the end):
  1. copies the plugin to a fresh folder under /private/tmp, with a fresh Budgit home beside it (never ~/.budgit, never ~/Library/LaunchAgents);
  2. writes the launcher and the plist with the product's own housekeeping.launcher_text and plist_text (label com.budgit.proof.<hex>);
  3. `launchctl bootstrap` + `kickstart`, then polls `launchctl print` for the last exit code;
  4. asserts exit 0, no launcher-fail file, and a clean `job health`;
  5. ALWAYS (finally): `launchctl bootout`, moves the whole folder (plist, plugin copy, home) into ~/.Trash under a unique name (a move, never a
     delete), and asserts the label is no longer listed.
The job is `job precheck keepalive`: it goes through the launcher, the interpreter, argument parsing, imports and the Budgit home exactly as
a scheduled job does, but never starts the model and never writes a run record."""
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

import housekeeping

PLUGIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin")
REAL = os.environ.get("BUDGIT_REAL_LAUNCHD") == "1" and sys.platform == "darwin"


def launchctl(*args):
    return subprocess.run(["/bin/launchctl"] + list(args), capture_output=True, text=True, timeout=60)


@unittest.skipUnless(REAL, "set BUDGIT_REAL_LAUNCHD=1 to run the one real launchd proof (macOS only)")
class RealLaunchd(unittest.TestCase):
    def test_OneRealLaunchdRunOfTheLauncherSucceedsAndIsRemoved(self):
        uid = os.getuid()
        label = "com.budgit.proof." + secrets.token_hex(4)
        target = "gui/%d/%s" % (uid, label)
        work = os.path.realpath(tempfile.mkdtemp(prefix="budgit-proof-", dir="/private/tmp"))
        booted = False
        report = {}
        try:
            copy = os.path.join(work, "plugin")
            shutil.copytree(PLUGIN, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            home = os.path.join(work, "home")
            os.makedirs(os.path.join(home, "bin"), mode=0o700)
            agents = os.path.join(work, "agents-empty")            # health looks here, never in ~/Library/LaunchAgents
            os.makedirs(agents)
            args = ["job", "precheck", "keepalive"]
            job = {"name": "proof", "label": label, "args": args, "log": "proof.log", "interval": 7200}
            launcher = housekeeping.launcher_path(home)
            with open(launcher, "w", encoding="utf-8") as f:
                f.write(housekeeping.launcher_text(copy, "/usr/bin/python3"))
            os.chmod(launcher, 0o700)
            with unittest.mock.patch.dict(os.environ, {"BUDGIT_HOME": home}):      # so the plist carries BUDGIT_HOME, as for a non-default home
                text = housekeeping.plist_text(job, launcher, home=home)
            plist = os.path.join(work, label + ".plist")
            with open(plist, "w", encoding="utf-8") as f:
                f.write(text)
            os.chmod(plist, 0o644)

            r = launchctl("bootstrap", "gui/%d" % uid, plist)
            report["bootstrap"] = (r.returncode, r.stderr.strip())
            self.assertEqual(r.returncode, 0, "bootstrap: %s %s" % (r.stdout, r.stderr))
            booted = True
            r = launchctl("kickstart", target)
            report["kickstart"] = (r.returncode, r.stderr.strip())
            self.assertEqual(r.returncode, 0, "kickstart: %s %s" % (r.stdout, r.stderr))
            code = None
            deadline = time.time() + 60
            while time.time() < deadline and code is None:
                time.sleep(1)
                m = re.search(r"last exit code = (-?\d+)", launchctl("print", target).stdout)
                code = int(m.group(1)) if m else None
            report["last_exit_code"] = code
            log = os.path.join(home, "proof.log")
            report["log"] = open(log, encoding="utf-8").read()[-500:] if os.path.exists(log) else None
            self.assertEqual(code, 0, "last exit code %r; log: %r" % (code, report["log"]))
            fail = os.path.join(home, housekeeping.launcher_fail_name(args))
            self.assertFalse(os.path.lexists(fail), "the launcher left a failure file: " + fail)

            env = {"HOME": work, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "BUDGIT_HOME": home, "BUDGIT_LAUNCH_AGENTS_DIR": agents,
                   "BUDGIT_HOUSEKEEPING_LAUNCHCTL": "off", "BUDGIT_LANG": "en"}
            h = subprocess.run(["/usr/bin/python3", "-I", "-S", os.path.join(copy, "bin", "budgit"), "job", "health"],
                               capture_output=True, text=True, timeout=120, env=env, cwd="/")
            report["health"] = (h.returncode, h.stdout, h.stderr.strip())
            self.assertEqual(h.returncode, 0, h.stdout + h.stderr)
            for word in ("cannot run", "no puede ejecutarse", "last exit", "jobs needing attention"):
                self.assertNotIn(word, h.stdout)
        finally:
            b = launchctl("bootout", target)          # always: harmless (an error) when nothing was loaded
            report["bootout"] = (b.returncode, b.stderr.strip(), "was loaded" if booted else "never loaded")
            gone = launchctl("print", target).returncode != 0
            trash = os.path.join(os.path.expanduser("~"), ".Trash", "budgit-proof-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), secrets.token_hex(3)))
            try:
                shutil.move(work, trash)
                report["moved_to"] = trash
            except OSError as exc:
                report["moved_to"] = "NOT MOVED (%s); still at %s" % (exc, work)
            print("\nREAL LAUNCHD PROOF %s" % label)
            for k, v in report.items():
                print("  %s: %s" % (k, v))
            print("  label no longer listed: %s" % gone)
        self.assertTrue(gone, "the proof agent is still listed after bootout: " + label)
        self.assertTrue(report["moved_to"].startswith(os.path.expanduser("~")), report["moved_to"])


if __name__ == "__main__":
    unittest.main()
