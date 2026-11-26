import os
from helpers import StudioTestCase
import studiolib as sl


class SymlinkSafetyTest(StudioTestCase):
    """A cloned repo could ship .studio/* as symlinks to the user's files; writes must not follow them."""

    def setUp(self):
        super().setUp()
        self.victim = os.path.join(self.tmp, "victim.txt")
        with open(self.victim, "w") as f:
            f.write("precious\n")
        os.makedirs(sl.studio_dir(self.project))

    def plant(self, name):
        os.symlink(self.victim, os.path.join(sl.studio_dir(self.project), name))

    def assertVictimIntact(self):
        with open(self.victim) as f:
            self.assertEqual(f.read(), "precious\n")

    def test_lock_symlink_is_refused(self):
        self.plant(".lock")
        with self.assertRaises(sl.StudioError):
            with sl.locked(self.project):
                pass
        self.assertVictimIntact()

    def test_tmp_symlink_is_not_followed(self):
        self.plant("ledger.json.tmp")
        sl.save_ledger(self.project, sl.empty_ledger())
        self.assertVictimIntact()

    def test_inbox_symlink_is_refused(self):
        self.plant("inbox.jsonl")
        with self.assertRaises(sl.StudioError):
            sl.append_inbox(self.project, "hola", "s1", "prompt")
        self.assertVictimIntact()

    def test_ledger_symlink_is_replaced_not_written_through(self):
        self.plant("ledger.json")
        sl.save_ledger(self.project, sl.empty_ledger())
        self.assertVictimIntact()
        self.assertFalse(os.path.islink(os.path.join(sl.studio_dir(self.project), "ledger.json")))

    def test_studio_dir_symlink_is_refused(self):
        os.rmdir(sl.studio_dir(self.project))
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        os.symlink(elsewhere, sl.studio_dir(self.project))
        with self.assertRaises(sl.StudioError):
            sl.activate(self.project, bind=False)


class SymlinkReadTest(StudioTestCase):
    """Planted symlinks must never leak a file's content into the repo or into model context."""

    def setUp(self):
        super().setUp()
        self.secret = os.path.join(self.tmp, "id_rsa")
        with open(self.secret, "w") as f:
            f.write("| R1 | SECRET-KEY-MATERIAL | open | |\n")
        os.makedirs(sl.studio_dir(self.project))

    def test_activate_does_not_archive_symlinked_v0_ledger(self):
        os.symlink(self.secret, os.path.join(sl.studio_dir(self.project), "ledger.md"))
        with self.assertRaises(sl.StudioError):
            sl.activate(self.project, bind=False)
        archive = os.path.join(sl.studio_dir(self.project), "ledger-v0.md")
        self.assertFalse(os.path.exists(archive) and "SECRET" in open(archive).read())

    def test_import_refuses_symlinked_source(self):
        sl.activate(self.project, bind=False)
        link = os.path.join(self.tmp, "looks-innocent.md")
        os.symlink(self.secret, link)
        with self.assertRaises(sl.StudioError):
            sl.import_v0_markdown(self.project, link)

    def test_session_summary_skips_symlinked_progress(self):
        sl.activate(self.project, bind=False)
        p = os.path.join(sl.studio_dir(self.project), "progress.md")
        os.remove(p)
        os.symlink(self.secret, p)
        self.assertNotIn("SECRET", sl.session_summary(self.project, "s1"))

    def test_symlinked_inbox_is_not_read(self):
        os.symlink(self.secret, os.path.join(sl.studio_dir(self.project), "inbox.jsonl"))
        self.assertEqual(sl.load_inbox(self.project), [])
