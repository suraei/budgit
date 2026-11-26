"""R-973: `git commit -F -` / `gh pr ... --body-file -` messages given by heredoc or pipe are checked."""
import json
import os
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts plugin/scripts on sys.path before the plugin import)
import attribution

TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"
HEREDOC = "git commit -F - <<'EOF'\nfix thing\n\n%s\nEOF"
PIPED = "printf 'fix thing\\n\\n%s\\n' | git commit -F -"
PR = "gh pr create --body-file - <<'EOF'\nbody\n\n%s\nEOF"


def decide(command, enabled=None):
    with tempfile.TemporaryDirectory() as home:
        with open(os.path.join(home, "config.json"), "w") as fh:
            fh.write(json.dumps({"no_claude_attribution": True}))
        with mock.patch.dict(os.environ, {"BUDGIT_HOME": home}):
            return attribution.decision("Bash", {"command": command}, home, enabled)


class StdinTest(unittest.TestCase):
    def test_heredoc_with_trailer_denied(self):
        self.assertEqual(decide(HEREDOC % TRAILER)[0], "deny")

    def test_piped_printf_with_trailer_denied(self):
        self.assertEqual(decide(PIPED % TRAILER)[0], "deny")

    def test_pr_heredoc_with_trailer_denied(self):
        self.assertEqual(decide(PR % TRAILER)[0], "deny")

    def test_without_trailer_allowed(self):
        self.assertIsNone(decide(HEREDOC % "Mentions Claude in prose."))
        self.assertIsNone(decide(PIPED % "plain"))

    def test_switch_off_allowed(self):
        self.assertIsNone(decide(HEREDOC % TRAILER, enabled=False))
        self.assertIsNone(decide(PIPED % TRAILER, enabled=False))

    def test_unreadable_message_asks(self):       # R-1037: a message the guard cannot read asks, it no longer passes
        self.assertEqual(decide('MSG="x"; git commit -F - <<< "$MSG"')[0], "ask")
        self.assertEqual(decide("echo $(cat m) | git commit -F -")[0], "ask")


if __name__ == "__main__":
    unittest.main()
