"""Brote must not run twice: the throwaway preview mod refuses to build while this hub's plugin/ is already a live plugin
(both would draw the pet, both would set the status line, both would register /brote)."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

from helpers import ROOT

TOOL = os.path.join(ROOT, "tools", "brote", "preview-mod.py")


def fake_claude(bindir, plugins):
    path = os.path.join(bindir, "claude")
    with open(path, "w") as f:
        f.write("#!/bin/sh\ncat <<'EOF'\n%s\nEOF\n" % json.dumps(plugins))
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


class PreviewClashTest(unittest.TestCase):
    def run_tool(self, plugins):
        tmp = tempfile.mkdtemp()
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir)
        fake_claude(bindir, plugins)
        dest = os.path.join(tmp, "market")
        env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"])
        r = subprocess.run([sys.executable, TOOL, "--marketplace", dest], env=env, capture_output=True, text=True)
        return r, dest

    def test_refuses_when_the_hub_plugin_is_live(self):
        live = [{"id": "studio@studio-hub", "enabled": True, "readFromFolder": os.path.join(ROOT, "plugin")}]
        r, dest = self.run_tool(live)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("already live", r.stderr + r.stdout)
        self.assertFalse(os.path.exists(dest), "nothing may be built")

    def test_builds_when_no_plugin_reads_from_the_hub_tree(self):
        other = [{"id": "studio@studio-hub", "enabled": True, "installPath": "/x/cache/studio/0.11.36"},
                 {"id": "studio@studio-hub", "enabled": False, "readFromFolder": os.path.join(ROOT, "plugin")}]
        r, dest = self.run_tool(other)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(dest, "brote-preview", "hooks", "register.jsx")))


if __name__ == "__main__":
    unittest.main()
