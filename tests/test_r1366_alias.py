"""R-1366 slice 1: a protected project name is written as its alias in ledger, inbox and progress text."""
import json
import os

from helpers import StudioTestCase
import studiolib as sl
import leak
import hook

NAME_A = "Mökki-Tool"      # made-up names only
NAME_B = "zebracorp"


class AliasTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        os.makedirs(self.home, exist_ok=True)
        self.write_config({"protected_dirs": ["/nowhere/" + NAME_A, "/nowhere/" + NAME_B]})

    def write_config(self, config):
        with open(os.path.join(self.home, "config.json"), "w", encoding="utf-8") as f:
            json.dump(config, f)

    def raw(self, name):
        with open(os.path.join(sl.studio_dir(self.project), name), encoding="utf-8") as f:
            return f.read()

    def assertClean(self, text):
        low = text.lower()
        self.assertNotIn("zebracorp", low)
        self.assertNotIn("mökki", low)

    def test_alias_text_order_and_variants(self):
        # sorted names: mökki-tool -> protected-1, zebracorp -> protected-2
        self.assertEqual(leak.alias_text("see ZEBRACORP and Mokki_Tool, mökki-tool"),
                         "see protected-2 and protected-1, protected-1")

    def test_other_words_untouched(self):
        self.assertEqual(leak.alias_text("zebra crossing, corp"), "zebra crossing, corp")

    def test_name_inside_a_longer_word_is_kept_but_strict_hide_matches(self):
        for word in ("zebracorpx", "xzebracorp", "zebracorp2"):
            self.assertEqual(leak.alias_text(word), word)
            self.assertNotEqual(leak.hide(word, [NAME_B]), word)      # the strict matcher still catches it
        self.assertTrue(leak._hide_pattern([NAME_B])[0].search("zebracorpx"))

    def test_name_glued_to_punctuation_is_aliased(self):
        self.assertEqual(leak.alias_text("zebracorp's, zebracorp-2 (zebracorp)"),
                         "protected-2's, protected-2-2 (protected-2)")

    def test_spool_line(self):
        self.init_project()
        sl.spool_inbox(self.project, "spooled ZEBRACORP note", "s1", "prompt")
        with open(os.path.join(sl.studio_dir(self.project), "inbox.spool"), encoding="utf-8") as f:
            text = f.read()
        self.assertClean(text)
        self.assertIn("spooled protected-2 note", text)

    def test_no_names_is_byte_identical(self):
        text = "Foto | nevera\né — plain  text \n"
        self.assertEqual(leak.alias_text(text), text)
        self.write_config({"protected_dirs": []})
        self.assertEqual(leak.alias_text("zebracorp"), "zebracorp")

    def test_unreadable_config_still_resolves_what_it_can(self):
        self.write_config({"protected_dirs": ["/nowhere/" + NAME_B, 5]})
        self.assertEqual(leak.alias_text("zebracorp"), "protected-1")

    def test_ledger_item_in_json_and_md(self):
        self.init_project()
        data = sl.load_ledger(self.project)
        item = sl.add_item(data, "Fix the ZebraCorp export for Mökki-Tool", ["I-1"])
        item["evidence"] = "checked ZebraCorp and Mökki-Tool"      # an existing guard may store the item text as "private"
        sl.save_ledger(self.project, data)
        for name in ("ledger.json", "ledger.md"):
            text = self.raw(name)
            self.assertClean(text)
        self.assertIn("checked protected-2 and protected-1", self.raw("ledger.json"))
        self.assertIn("checked protected-2 and protected-1", self.raw("ledger.md"))

    def test_finding_and_directive(self):
        self.init_project()
        sl.append_inbox(self.project, "keep zebracorp private", "s1", "prompt")
        data = sl.load_ledger(self.project)
        item = sl.add_item(data, "x", ["I-1"])
        sl.add_event(item, "finding", "tester", "found zebracorp leak")
        sl.save_ledger(self.project, data)
        sl.add_directive(self.project, data, item["id"], ["I-1"], "never name Mökki-Tool")
        for name in ("ledger.json", "ledger.md", "inbox.jsonl"):
            self.assertClean(self.raw(name))
        self.assertIn("found protected-2 leak", self.raw("ledger.json"))
        self.assertIn("never name protected-1", self.raw("ledger.json"))

    def test_inbox_line(self):
        self.init_project()
        sl.append_inbox(self.project, "talk about ZEBRACORP", "s1", "prompt")
        text = self.raw("inbox.jsonl")
        self.assertClean(text)
        self.assertIn("talk about protected-2", text)

    def test_progress_line(self):
        self.init_project()
        hook._progress_line(self.project, "closed zebracorp work")
        text = self.raw("progress.md")
        self.assertClean(text)
        self.assertIn("closed protected-2 work", text)
