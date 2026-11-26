"""R-1135 (a), I-2041 (her yes to accept the link): `budgit denies --project <root> --since 7d` is a read-only report of the project's own
task data. The real-data guard cleared it only for the plugin's own bin/budgit by an absolute path or a PATH entry that is the
file itself, and asked for a PATH SYMLINK to that file. A symlink whose realpath is the own file now counts as the own CLI,
for `_own_task_lines` and `_budgit_exception`; a copy, a hard link, a link to anything else, a chain ending elsewhere and a name
no PATH entry holds are not, and a real write after the call still asks. Hidden characters are built with chr() only."""
import os
import shutil
import unittest
from unittest import mock

import studiolib as sl
from test_guard_trust import GuardCase, CLI


class OwnLinkTest(GuardCase):
    def setUp(self):
        super().setUp()
        self.bindir = os.path.join(self.tmp, "linkbin")
        os.makedirs(self.bindir)
        self.old_path = os.environ.get("PATH")
        self.addCleanup(self.restore_path)

    def restore_path(self):
        if self.old_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = self.old_path

    def budgit_as(self, how):
        """`budgit` as the shell finds it, first on PATH."""
        target = os.path.join(self.bindir, "budgit")
        if os.path.lexists(target):
            os.remove(target)
        if how == "link":
            os.symlink(CLI, target)
        elif how == "chain":                                      # a link to a link to the own file
            hop = os.path.join(self.bindir, "hop")
            if os.path.lexists(hop):
                os.remove(hop)
            os.symlink(CLI, hop)
            os.symlink(hop, target)
        elif how == "copy":
            shutil.copy(CLI, target)
        elif how == "hard":
            try:
                os.link(CLI, target)
            except OSError:
                self.skipTest("no hard link across these folders")
        elif how == "touch":
            os.symlink("/usr/bin/touch", target)                  # never executed: only the guard verdict is read
        elif how == "chain-elsewhere":
            hop = os.path.join(self.bindir, "hop")
            if os.path.lexists(hop):
                os.remove(hop)
            os.symlink("/usr/bin/touch", hop)
            os.symlink(hop, target)
        elif how == "none":
            os.environ["PATH"] = self.bindir
            return "budgit"
        os.environ["PATH"] = self.bindir + os.pathsep + (self.old_path or os.defpath)
        return "budgit"

    def denies(self, cli):
        return "%s denies --project %s --since 7d" % (cli, self.project)

    def test_a_path_symlink_to_the_own_file_passes_the_denies_report(self):
        self.passes(self.denies(self.budgit_as("link")))

    def test_a_chain_of_links_ending_at_the_own_file_passes(self):
        self.passes(self.denies(self.budgit_as("chain")))

    def test_a_link_to_the_own_file_passes_a_ledger_read_of_the_bound_project(self):
        cli = self.budgit_as("link")
        self.passes("%s ledger list --project %s" % (cli, self.project))

    def test_the_absolute_own_path_still_passes(self):
        self.budgit_as("none")
        self.passes(self.denies(CLI))

    def test_a_copy_a_hard_link_a_link_elsewhere_and_an_unknown_name_still_ask(self):
        for how in ("copy", "hard", "touch", "chain-elsewhere", "none"):
            self.asks(self.denies(self.budgit_as(how)))

    def test_a_link_to_the_own_file_then_an_unquoted_copy_still_asks(self):
        cli = self.budgit_as("link")
        self.asks(self.denies(cli) + "; cp a data/real/y.csv",
                  self.denies(cli) + " && cp a data/real/y.csv",
                  self.denies(cli) + " > data/real/y.csv",
                  self.denies(cli) + "\ncp a data/real/y.csv")

    def test_a_link_to_the_own_file_with_another_project_or_a_data_path_still_asks(self):
        cli = self.budgit_as("link")
        self.asks("%s denies --project %s --since 7d" % (cli, os.path.join(self.root, "data", "real")),
                  "%s ledger list --file %s" % (cli, os.path.join(self.root, "data", "real", "x.csv")))

    def test_an_absolute_link_inside_protected_data_asks(self):
        link = os.path.join(self.root, "data", "real", "budgit")
        os.symlink(CLI, link)
        self.asks(self.denies(link))

    def test_a_path_entry_inside_protected_data_asks(self):
        os.symlink(CLI, os.path.join(self.root, "data", "real", "budgit"))
        os.environ["PATH"] = os.path.join(self.root, "data", "real") + os.pathsep + (self.old_path or os.defpath)
        self.asks(self.denies("budgit"))

    def test_a_chain_whose_middle_hop_is_inside_protected_data_asks(self):
        hop = os.path.join(self.root, "data", "real", "hop")
        os.symlink(CLI, hop)                                      # the middle hop lives in the data folder
        os.symlink(hop, os.path.join(self.bindir, "budgit"))
        os.environ["PATH"] = self.bindir + os.pathsep + (self.old_path or os.defpath)
        self.asks(self.denies("budgit"), self.denies(os.path.join(self.bindir, "budgit")))

    def test_a_relative_hop_into_protected_data_asks(self):
        hop = os.path.join(self.root, "data", "real", "hop")
        link = os.path.join(self.bindir, "budgit")
        os.symlink(CLI, hop)
        os.symlink(os.path.relpath(hop, os.path.realpath(self.bindir)), link)      # a RELATIVE hop (both sides as real paths)
        self.assertFalse(os.path.isabs(os.readlink(link)))
        self.assertEqual(os.path.realpath(link), os.path.realpath(CLI))          # it really resolves to the own file ...
        self.assertEqual(os.path.normpath(os.path.join(os.path.realpath(self.bindir), os.readlink(link))), hop)    # ... through the hop in data/real
        os.environ["PATH"] = self.bindir + os.pathsep + (self.old_path or os.defpath)
        self.asks(self.denies("budgit"))

    def test_a_link_outside_the_data_folder_still_passes(self):
        self.passes(self.denies(self.budgit_as("link")), self.denies(os.path.join(self.bindir, "budgit")))

    def test_a_link_that_cannot_be_read_mid_walk_asks(self):
        self.budgit_as("link")
        cand = os.path.join(os.path.realpath(self.bindir), "budgit")    # a real link outside the data folder (folder as a real path: no other link to read)
        patterns = sl.load_project_config(self.project).get("protected_paths") or []
        self.assertFalse(sl._link_chain_in_protected(cand, self.project, patterns, self.project))     # readable: outside
        with mock.patch.object(sl.os, "readlink", side_effect=OSError("swapped")):
            self.assertTrue(sl._link_chain_in_protected(cand, self.project, patterns, self.project))

    def test_a_hidden_character_in_the_name_is_not_the_own_file(self):
        cli = self.budgit_as("link")
        self.asks(self.denies(cli + chr(0xA0)), self.denies(cli + chr(0x0B)))


if __name__ == "__main__":
    unittest.main()
