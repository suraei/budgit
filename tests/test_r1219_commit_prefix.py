"""R-1219: the commit guard skips npm, pnpm and yarn option values (--prefix, --dir, --cwd, -w) before the test word."""
from helpers import StudioTestCase
import commitguard

WT = "/tmp/acme-ledger/.claude/worktrees/agent-adf3bdffc821c830f"


class CommitPrefixTest(StudioTestCase):
    def d(self, cmd, agent="a1"):
        return commitguard.decision("Bash", {"command": cmd}, agent)

    def test_prefix_command_allowed(self):
        cmd = ("npm --prefix %s test -- src/lib/server/conectores && git -C %s commit -m \"x\"" % (WT, WT))
        self.assertIsNone(self.d(cmd))

    def test_other_option_values_allowed(self):
        for cmd in ("pnpm --dir /x test && git commit -m a",
                    "yarn --cwd /x test && git commit -m a",
                    "npm --prefix=/x run test && git -C /x commit -m a",
                    "npm -w pkg test && git commit -m a",
                    "pnpm --filter pkg test && git commit -m a",
                    "npm -C /x test && git commit -m a"):
            self.assertIsNone(self.d(cmd), cmd)

    def test_non_test_scripts_and_broken_chains_refused(self):
        for cmd in ("npm --prefix /x run build && git commit -m a",
                    "npm --prefix test build && git commit -m a",
                    "echo npm test && git commit -m a",
                    "npm --prefix /x test ; git commit -m a",
                    "npm --prefix /x test || git commit -m a"):
            self.assertEqual(self.d(cmd)[0], "deny", cmd)

    def test_unknown_value_options_fail_closed(self):
        for cmd in ("npm --registry test build && git commit -m a",
                    "npm --cache test run build && git commit -m a",
                    "npm --userconfig /x test build && git commit -m a",
                    "pnpm --unknown test && git commit -m a"):
            self.assertEqual(self.d(cmd)[0], "deny", cmd)

    def test_known_flags_and_opt_equals_value_allowed(self):
        for cmd in ("npm --silent test && git commit -m a",
                    "npm -s run test --if-present && git commit -m a",
                    "pnpm -r --frozen-lockfile test && git commit -m a",
                    "npm --loglevel=error --registry=x test && git commit -m a",
                    "yarn --no-color test && git commit -m a"):
            self.assertIsNone(self.d(cmd), cmd)

    def test_npx_keeps_base_behaviour_refused(self):
        for cmd in ("npx jest && git commit -m a", "npx vitest run && git commit -m a"):
            self.assertEqual(self.d(cmd)[0], "deny", cmd)

    def test_coordinator_never_affected(self):
        self.assertIsNone(self.d("git commit -m a", agent=None))
