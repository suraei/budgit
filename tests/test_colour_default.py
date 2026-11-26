"""R-206 / R-238: the line under the prompt is coloured by default where the host draws it. The mod asks with `--colour auto`
(the CLI's own default stays plain, so scripts and tests read plain text); `budgit colour off` switches it off for good."""
import json
import os
import subprocess
import sys
import unittest

from helpers import StudioTestCase, ROOT

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class ColourDefaultTest(StudioTestCase):
    def cli(self, *args, lang="es"):
        env = dict(os.environ, BUDGIT_HOME=self.home, BUDGIT_LANG=lang)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI] + list(args), cwd=self.tmp, env=env, capture_output=True, text=True)

    def setUp(self):
        super().setUp()
        import time
        self.cli("budget", "record", "--seven-day", "40", "--seven-day-resets", str(time.time() + 3 * 86400))   # a bar to paint

    def line(self, *args):
        r = self.cli("status", "--line", "--project", self.project, *args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.rstrip("\n")

    def test_the_cli_alone_stays_plain(self):
        self.assertNotIn("\x1b", self.line())

    def test_colour_auto_is_coloured_until_the_user_turns_it_off(self):
        self.assertIn("\x1b[38;2;", self.line("--colour", "auto"))
        self.cli("colour", "off")
        self.assertNotIn("\x1b", self.line("--colour", "auto"))
        self.cli("colour", "auto")
        self.assertIn("\x1b[38;2;", self.line("--colour", "auto"))

    def test_explicit_on_and_off_ignore_the_saved_choice(self):
        self.cli("colour", "off")
        self.assertIn("\x1b[38;2;", self.line("--colour", "on"))
        self.cli("colour", "on")
        self.assertNotIn("\x1b", self.line("--colour", "off"))

    def test_coloured_text_is_the_plain_text_with_escapes_removed(self):
        import re
        coloured = self.line("--colour", "on")
        self.assertEqual(re.sub(r"\x1b\[[0-9;]*m", "", coloured), self.line("--colour", "off"))

    def test_the_account_only_line_is_coloured_too(self):
        self.assertIn("\x1b[38;2;", self.line("--colour", "on"))

    def test_the_colour_command_shows_sets_and_refuses_junk(self):
        shown = self.cli("colour")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("color", shown.stdout.lower())
        self.assertEqual(self.cli("colour", "off").returncode, 0)
        self.assertIn("off", self.cli("colour").stdout.lower().replace("apagado", "off"))
        bad = self.cli("colour", "purple")
        self.assertNotEqual(bad.returncode, 0)
        self.assertNotIn("Traceback", bad.stderr)

    def test_the_choice_lives_in_the_home_config_and_keeps_other_keys(self):
        cfg = os.path.join(self.home, "config.json")
        os.makedirs(self.home, exist_ok=True)
        with open(cfg, "w") as f:
            json.dump({"lang": "en", "hub": "/x"}, f)
        self.cli("colour", "off")
        data = json.load(open(cfg))
        self.assertEqual((data["lang"], data["hub"], data["status_colour"]), ("en", "/x", "off"))
        self.cli("colour", "auto")
        self.assertNotIn("status_colour", json.load(open(cfg)))

    def test_a_garbled_saved_value_means_colour_on(self):
        cfg = os.path.join(self.home, "config.json")
        os.makedirs(self.home, exist_ok=True)
        with open(cfg, "w") as f:
            json.dump({"status_colour": {"x": 1}}, f)
        self.assertIn("\x1b[38;2;", self.line("--colour", "auto"))


if __name__ == "__main__":
    unittest.main()
