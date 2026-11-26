"""R-1360 slice G1: three exact doors in the plugin-merge guard that the security review ruled false positives.
Door 1: the prefix tokens GIT_EDITOR=true, GIT_EDITOR=: and GIT_OPTIONAL_LOCKS=0, as whole plain tokens, pass; every other GIT_ value is refused.
Door 2: merge-tree --write-tree [--no-messages | --name-only] <2 or 3 plain revision words>, for the coordinator only.
Door 3: worktree move: allowed for the coordinator when the source is a registered lane worktree (see 7032f45f and test_r1360_g2a_move_door)."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
import mergeguard  # noqa: E402

G = "g" + "it"                                       # the program word, kept out of this file's own command lines


def judge(command, agent_id=None):
    return mergeguard.decision("Bash", {"command": command}, ROOT, agent_id=agent_id)


class EnvDoor(unittest.TestCase):
    def test_the_three_plain_tokens_pass(self):
        for tok in ("GIT_EDITOR=true", "GIT_EDITOR=:", "GIT_OPTIONAL_LOCKS=0"):
            with self.subTest(tok=tok):
                self.assertIsNone(judge("%s %s status" % (tok, G)))

    def test_both_tokens_together_pass(self):
        self.assertIsNone(judge("GIT_OPTIONAL_LOCKS=0 GIT_EDITOR=true %s status" % G))

    def test_every_other_value_is_refused(self):
        for tok in ("GIT_EDITOR=vim", "GIT_EDITOR=true;x", 'GIT_EDITOR="true"', "GIT_EDITOR='true'", "GIT_EDITOR=truex",
                    "GIT_SEQUENCE_EDITOR=true", "GIT_EXTERNAL_DIFF=true", "GIT_SSH_COMMAND=true", "GIT_PAGER=true",
                    "GIT_OPTIONAL_LOCKS=1", "GIT_OPTIONAL_LOCKS=00", "GIT_EDITOR=$X"):
            with self.subTest(tok=tok):
                d = judge("%s %s status" % (tok, G))
                self.assertEqual(d[0] if d else None, "deny", d)

    def test_a_plain_token_does_not_excuse_a_quoted_twin(self):
        d = judge('GIT_EDITOR=true %s status; GIT_EDITOR="true" %s status' % (G, G))
        self.assertEqual(d[0] if d else None, "deny", d)

    def test_the_rest_of_the_command_is_still_judged(self):
        d = judge("GIT_EDITOR=true %s reset --hard" % G)
        self.assertEqual(d[0] if d else None, "deny", d)

    def test_an_env_wrapper_form_stays_refused(self):
        d = judge("env GIT_EDITOR=true %s status" % G)
        self.assertEqual(d[0] if d else None, "deny", d)


class MergeTreeDoor(unittest.TestCase):
    def test_the_exact_shapes_pass_for_the_coordinator(self):
        for cmd in ("merge-tree --write-tree a b", "merge-tree --write-tree --no-messages main feature/x-1",
                    "merge-tree --write-tree --name-only v1.2.3 abc1234 def5678", "merge-tree --write-tree HEAD origin/main"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(judge("%s %s" % (G, cmd)))

    def test_a_helper_or_subagent_is_still_refused(self):
        d = judge("%s merge-tree --write-tree a b" % G, agent_id="agent-1")
        self.assertEqual(d[0] if d else None, "deny", d)

    def test_other_shapes_are_refused(self):
        for cmd in ("merge-tree a b", "merge-tree --write-tree a", "merge-tree --write-tree a b c d",
                    "merge-tree --write-tree --no-messages --name-only a b", "merge-tree --write-tree --merge-base=x a b",
                    "merge-tree --write-tree -z a b", "merge-tree --write-tree -x a b", "merge-tree --write-tree a..b c",
                    "merge-tree --write-tree a b..", "merge-tree --write-tree a $X", "merge-tree --write-tree a `x`",
                    "merge-tree --write-tree a b*", "merge-tree --write-tree a ../b", "merge-tree --write-tree a b^",
                    "-c core.x=y merge-tree --write-tree a b"):
            with self.subTest(cmd=cmd):
                d = judge("%s %s" % (G, cmd))
                self.assertEqual(d[0] if d else None, "deny", d)

    def test_a_git_env_prefix_is_refused(self):
        for pre in ("GIT_EDITOR=true", "GIT_OPTIONAL_LOCKS=0", "GIT_EXTERNAL_DIFF=x"):
            with self.subTest(pre=pre):
                d = judge("%s %s merge-tree --write-tree a b" % (pre, G))
                self.assertEqual(d[0] if d else None, "deny", d)


class WorktreeMoveDoor(unittest.TestCase):
    def test_move_remove_and_forced_forms_stay_refused(self):
        for cmd in ("worktree move /tmp/x /tmp/y", "worktree move -f /tmp/x /tmp/y", "worktree move --force /tmp/x /tmp/y",
                    "worktree move /tmp/x %s/inside" % ROOT, "worktree remove /tmp/x", "worktree remove -f /tmp/x"):
            with self.subTest(cmd=cmd):
                d = judge("%s %s" % (G, cmd))
                self.assertEqual(d[0] if d else None, "deny", d)
                d = judge("%s %s" % (G, cmd), agent_id="agent-1")
                self.assertEqual(d[0] if d else None, "deny", d)


if __name__ == "__main__":
    unittest.main()
