"""I-1526: the Claude-attribution refusal follows the user's setting `no_claude_attribution` (default on)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts plugin/scripts on sys.path before the plugin import)
import attribution

COMMIT = "git commit -m 'fix\n\nCo-Authored-By: Claude <noreply@anthropic.com>'"


def run_with_config(text):
    """attribution.decision for COMMIT with BUDGIT_HOME holding config.json = text (None: no file)."""
    with tempfile.TemporaryDirectory() as home:
        if text is not None:
            with open(os.path.join(home, "config.json"), "w") as fh:
                fh.write(text)
        with mock.patch.dict(os.environ, {"BUDGIT_HOME": home}):
            return attribution.decision("Bash", {"command": COMMIT}, home)


class AttributionSettingTest(unittest.TestCase):
    def test_setting_on_refuses_a_commit_with_a_claude_trailer(self):
        self.assertEqual(run_with_config(json.dumps({"no_claude_attribution": True}))[0], "deny")

    def test_setting_off_allows_it(self):
        self.assertIsNone(run_with_config(json.dumps({"no_claude_attribution": False})))

    def test_no_setting_refuses(self):
        self.assertEqual(run_with_config(json.dumps({"codex": "off"}))[0], "deny")
        self.assertEqual(run_with_config(None)[0], "deny")

    def test_unreadable_config_refuses(self):
        self.assertEqual(run_with_config('{"no_claude_attribution": false,')[0], "deny")
        self.assertEqual(run_with_config("[]")[0], "deny")

    def test_a_failing_setting_read_refuses(self):
        with mock.patch("studiolib.no_claude_attribution", side_effect=RuntimeError("boom")):
            self.assertEqual(attribution.decision("Bash", {"command": COMMIT}, "/tmp")[0], "deny")


CLI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin", "bin", "budgit")


class AttributionCommandTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def cli(self, *args, lang="en"):
        env = dict(os.environ, BUDGIT_HOME=self.home, BUDGIT_LANG=lang)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI] + list(args), cwd=self.home, env=env, capture_output=True, text=True)

    def decision(self):
        with mock.patch.dict(os.environ, {"BUDGIT_HOME": self.home}):
            return attribution.decision("Bash", {"command": COMMIT}, self.home)

    def saved(self):
        with open(os.path.join(self.home, "config.json")) as fh:
            return json.load(fh)

    def test_attribution_off_sets_false_and_allows_a_trailer_commit(self):
        r = self.cli("attribution", "off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIs(self.saved()["no_claude_attribution"], False)
        self.assertIsNone(self.decision())

    def test_attribution_on_refuses_again(self):
        self.cli("attribution", "off")
        r = self.cli("attribution", "on")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIs(self.saved()["no_claude_attribution"], True)
        self.assertEqual(self.decision()[0], "deny")

    def test_bare_attribution_shows_the_state_in_both_languages(self):
        self.assertIn("refused (on)", self.cli("attribution").stdout)
        self.assertIn("se rechazan (activado)", self.cli("attribution", lang="es").stdout)
        self.cli("attribution", "off")
        self.assertIn("allowed (off)", self.cli("attribution").stdout)
        self.assertIn("se permiten (desactivado)", self.cli("attribution", lang="es").stdout)

    def test_an_invalid_value_exits_non_zero_and_changes_nothing(self):
        self.cli("attribution", "off")
        before = self.saved()
        r = self.cli("attribution", "maybe")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.saved(), before)


if __name__ == "__main__":
    unittest.main()
