"""R-1360 F37: a verifier (subagent) may run the READ verbs of a sibling installed version of this plugin (a long session keeps the
hooks of the version it loaded; the installed copy moved on). Any other verb, a worktree, a symlink and a look-alike folder stay
refused with the "other checkout" text. A temp fake plugins/cache tree; the real home is never touched."""
import os
import sys
import tempfile
import unittest
from unittest import mock

import studiolib as sl
import updatelib

TABLE = sl._cli_table()           # the real verb table, read before _own_budgit is pointed at a fake tree


def _make(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/usr/bin/env python3\n")
    os.chmod(path, 0o755)
    return path


class SiblingDoorTest(unittest.TestCase):
    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="f37-"))
        self.parent = os.path.join(self.base, "plugins", "cache", "mkt", "studio")
        self.own = _make(os.path.join(self.parent, "0.2.0", "bin", "budgit"))
        self.sib = _make(os.path.join(self.parent, "0.1.0", "bin", "budgit"))
        self.root = os.path.join(self.base, "proj")
        os.makedirs(self.root)
        self.reg = mock.patch.object(updatelib, "registered_root", return_value="0.3.0")   # registered and NEWER than the own 0.2.0
        self.reg_mock = self.reg.start()
        self.addCleanup(self.reg.stop)
        for p in (mock.patch.object(updatelib, "running_version", return_value="0.2.0"),
                  mock.patch.object(sl, "_own_budgit", return_value=self.own),
                  mock.patch.object(sl, "_cli_table", return_value=TABLE),
                  mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"})):
            p.start()
            self.addCleanup(p.stop)

    def run_cmd(self, cli, rest, subagent=True, verifier=True):
        return sl.home_write_check("Bash", {"command": "%s --project %s %s" % (cli, self.root, rest)}, self.root,
                                   sl.VERIFIER if verifier else "budgit:builder", subagent=subagent)[0]

    def refused(self, got):
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "deny")
        self.assertEqual(got[1], sl._OTHER_CLI_DENY)

    def test_repro_without_the_door_a_sibling_is_the_other_cli_refusal(self):
        with mock.patch.object(sl, "_is_sibling_budgit", return_value=False):
            self.refused(self.run_cmd(self.sib, "budget"))
            self.refused(self.run_cmd(self.sib, "secrets"))

    def test_a_sibling_version_with_budget_is_allowed(self):
        self.assertIsNone(self.run_cmd(self.sib, "budget"))

    def test_b_sibling_version_with_secrets_is_allowed(self):
        self.assertIsNone(self.run_cmd(self.sib, "secrets"))

    def test_c_sibling_version_with_status_is_allowed(self):
        self.assertIsNone(self.run_cmd(self.sib, "status --line"))     # round 3: plain `status` is refused (see test_n_f)

    def test_n_a_registered_newer_sibling_is_allowed(self):
        self.assertIsNone(self.run_cmd(self.sib, "budget"))
        self.reg_mock.assert_called()

    def test_n_b_unregistered_sibling_is_refused(self):
        self.reg_mock.return_value = None
        self.refused(self.run_cmd(self.sib, "budget"))

    def test_n_c_older_sibling_is_refused(self):
        self.reg_mock.return_value = "0.1.0"
        self.refused(self.run_cmd(self.sib, "budget"))

    def test_n_d_equal_version_is_refused(self):
        self.reg_mock.return_value = "0.2.0"
        self.refused(self.run_cmd(self.sib, "budget"))

    def test_n_e_hard_linked_cli_is_refused(self):
        os.link(self.sib, os.path.join(self.base, "other-name"))
        self.refused(self.run_cmd(self.sib, "budget"))

    def test_n_f_plain_status_is_refused_and_line_or_brote_allowed(self):
        self.refused(self.run_cmd(self.sib, "status"))
        self.refused(self.run_cmd(self.sib, "status --extras"))
        self.assertIsNone(self.run_cmd(self.sib, "status --line"))
        self.assertIsNone(self.run_cmd(self.sib, "status --brote"))

    def test_n_g_registry_error_is_refused(self):
        self.reg_mock.side_effect = RuntimeError("boom")
        self.refused(self.run_cmd(self.sib, "budget"))

    def test_c2_sibling_read_ledger_and_names_check_are_allowed(self):
        for rest in ("ledger show R-1", "ledger list", "ledger findings R-1", "names-check"):
            self.assertIsNone(self.run_cmd(self.sib, rest), rest)

    def test_d_sibling_budget_account_is_refused(self):
        self.refused(self.run_cmd(self.sib, "budget account"))

    def test_e_sibling_ledger_done_is_refused(self):
        self.refused(self.run_cmd(self.sib, "ledger done R-1 --evidence x"))

    def test_e2_sibling_writing_flags_are_refused(self):
        for rest in ("secrets --install-hook", "ledger list --mine", "status --mine", "budget set high", "export /x --out /y"):
            self.refused(self.run_cmd(self.sib, rest))

    def test_f_worktree_checkout_is_refused(self):
        wt = _make(os.path.join(self.base, "wt", "plugin", "bin", "budgit"))
        self.refused(self.run_cmd(wt, "budget"))

    def test_f2_the_door_is_for_a_subagent_only(self):
        got = self.run_cmd(self.sib, "budget", subagent=False)
        self.assertFalse(got and got[1] == sl._OTHER_CLI_DENY)
        self.assertFalse(sl._cli_call([self.sib, "budget"], [self.sib, "budget"]))

    def test_j_a_builder_subagent_with_sibling_secrets_is_refused(self):
        self.refused(self.run_cmd(self.sib, "secrets", verifier=False))

    def test_k_a_builder_subagent_with_sibling_budget_is_refused(self):
        self.refused(self.run_cmd(self.sib, "budget", verifier=False))

    def test_l_a_verifier_with_sibling_budget_account_is_still_refused(self):
        self.refused(self.run_cmd(self.sib, "budget account", verifier=True))

    def test_m_a_verifier_with_a_worktree_cli_is_still_refused(self):
        wt = _make(os.path.join(self.base, "wt3", "plugin", "bin", "budgit"))
        self.refused(self.run_cmd(wt, "budget", verifier=True))

    def test_g_symlink_to_a_sibling_bin_is_refused(self):
        link = os.path.join(self.parent, "0.1.5", "bin", "budgit")
        os.makedirs(os.path.dirname(link))
        os.symlink(self.sib, link)
        self.refused(self.run_cmd(link, "budget"))

    def test_g2_a_symlinked_folder_on_the_path_is_refused(self):
        os.symlink(os.path.join(self.parent, "0.1.0"), os.path.join(self.parent, "0.1.7"))
        self.refused(self.run_cmd(os.path.join(self.parent, "0.1.7", "bin", "budgit"), "budget"))

    def test_h_look_alike_version_folder_outside_the_cache_parent_is_refused(self):
        fake = _make(os.path.join(self.base, "plugins", "cache", "mkt", "other", "0.1.0", "bin", "budgit"))
        self.refused(self.run_cmd(fake, "budget"))
        fake2 = _make(os.path.join(self.base, "elsewhere", "studio", "0.1.0", "bin", "budgit"))
        self.refused(self.run_cmd(fake2, "budget"))

    def test_h2_a_hook_whose_own_copy_is_not_in_the_cache_allows_no_sibling(self):
        own = _make(os.path.join(self.base, "wt2", "plugin", "bin", "budgit"))
        with mock.patch.object(sl, "_own_budgit", return_value=own):
            self.refused(self.run_cmd(self.sib, "budget"))

    def test_i_own_copy_behaviour_is_unchanged(self):
        self.assertIsNone(self.run_cmd(self.own, "budget"))
        self.assertIsNone(self.run_cmd(self.own, "status"))
        got = self.run_cmd(self.own, "budget account")
        self.assertIsNotNone(got)
        self.assertNotEqual(got[1], sl._OTHER_CLI_DENY)


if __name__ == "__main__":
    unittest.main()
