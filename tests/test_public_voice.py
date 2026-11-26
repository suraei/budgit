"""Release 0.12 (P2, D1): the shipped text speaks to "you" / "the user", never of the owner as her/she."""
import os

from helpers import StudioTestCase, ROOT
import export


class PublicVoiceTest(StudioTestCase):
    def test_public_text_has_no_owner_voice(self):
        files = export.shipped_text_files(ROOT)
        self.assertGreater(len(files), 50)
        self.assertTrue(any(f.endswith(os.path.join("plugin", "bin", "budgit")) for f in files), "extensionless files count")
        self.assertFalse(any(f.endswith("CHANGELOG.md") for f in files))
        hits = export.voice_hits(files, ROOT)
        self.assertEqual(hits, [], "owner voice (use 'you' / 'the user'): " +
                         ", ".join("%s:%d" % (h.path, h.line) for h in hits[:25]) + " ... %d in all" % len(hits))

    def test_the_gate_also_catches_owner_names_in_identifiers(self):      # R-212 #8
        d = os.path.join(self.tmp, "stage")
        os.makedirs(d)
        path = os.path.join(d, "x.py")
        with open(path, "w") as f:
            f.write("def is_her_prompt(rec):\n    return True\n")
        self.assertEqual([h.line for h in export.voice_hits([path], d)], [1])
