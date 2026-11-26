"""R-1366 F8: the strict name matcher never spans whitespace between other words. Made-up names only (a 4-letter one and an
8-letter one), temp repos, config passed in: the real home is never read."""
import os
import re
import subprocess
import unittest
from unittest import mock

import leak
from test_r1366_push_check import RepoCase

SHORT, LONG = "zork", "quillfox"
CONFIG = {"protected_dirs": ["/nonexistent-r1366/" + SHORT, "/nonexistent-r1366/" + LONG]}

# the matcher before F8, spelled out: any separator, whitespace included, between any two letters
def _old_hits(text, name):
    return re.search(r"[\W_]*".join(re.escape(c) for c in name), text.casefold()) is not None


# (name, text): letters spread over word ends; the old matcher hit, the new one must not
SPREAD = [(SHORT, "a buzz o rk top"), (SHORT, "xzo rkx"), (LONG, "xquil lfoxx"), (LONG, "ab xqu ill fo xtra")]
# (name, text): the whole name in any case, any separator inside it, or inside one word: all must hit
REAL = [(n, t) for n in (SHORT, LONG) for t in (
    n, n.upper(), n.title(), "the %s tool" % n, "x%sx" % n, "%s.py" % n, "my%s" % n,
    n[:2] + " " + n[2:], n[:2] + "." + n[2:], n[:2] + "-" + n[2:], n[:2] + "_" + n[2:], (n[:2] + " " + n[2:]).upper(),
    "see " + " ".join(n) + " here", "x" + n[:2] + "_" + n[2:] + "x")]


class MatcherTest(unittest.TestCase):
    def test_spread_over_word_ends_hit_before_and_no_longer_hit(self):
        for name, text in SPREAD:
            with self.subTest(name=name, text=text):
                self.assertTrue(_old_hits(text, name), "the old matcher hit")
                self.assertEqual(leak.hide(text, [name]), text)

    def test_real_mentions_still_hit(self):
        for name, text in REAL:
            with self.subTest(name=name, text=text):
                self.assertNotEqual(leak.hide(text, [name]), text)

    def test_a_substring_inside_one_word_hits(self):
        for name in (SHORT, LONG):
            self.assertNotEqual(leak.hide("x%sx" % name, [name]), "x%sx" % name)
            self.assertNotEqual(leak.hide("x%s_y" % name, [name]), "x%s_y" % name)

    def test_the_short_and_the_long_name_get_the_same_rules(self):
        for name in (SHORT, LONG):
            half = len(name) // 2
            a, b = name[:half], name[half:]
            self.assertEqual(leak.hide("x%s %sx" % (a, b), [name]), "x%s %sx" % (a, b))    # whitespace inside a word end
            for sep in (" ", ".", "-", "_", ""):
                self.assertEqual(leak.hide("go %s%s%s now" % (a, sep, b), [name]), "go <hidden> now")
                self.assertNotEqual(leak.hide("go%s%s%s" % (a, sep, b), [name]), "go%s%s%s" % (a, sep, b)) if sep != " " else None


class PushCheckF8Test(RepoCase):
    def test_spread_text_is_clean_through_push_check_and_names_report(self):
        for i, (name, text) in enumerate(SPREAD):
            self.commit("f%d.txt" % i, text + "\n")
        hits = leak.push_check(self.repo, CONFIG)
        self.assertEqual(list(hits), [])
        report, code = leak.names_check_cli(self.repo, config=CONFIG)
        self.assertEqual(code, 0, report)

    def test_real_mentions_are_refused_through_push_check_and_names_report(self):
        for i, (name, text) in enumerate(REAL):
            self.commit("r%d.txt" % i, text + "\n")
        hits = leak.push_check(self.repo, CONFIG)
        self.assertEqual(len(hits), len(REAL))
        report, code = leak.names_check_cli(self.repo, config=CONFIG)
        self.assertEqual(code, 1)
        self.assertNotIn(SHORT, report.lower())
        self.assertNotIn(LONG, report.lower())


QZ = "qwjz" + "rk"                                # made-up, built in parts so no tracked file holds it whole


class SeparatorPositionTest(unittest.TestCase):
    """Whitespace needs no word boundary only where the configured name has its own separator."""
    def hit(self, text, name):
        return leak.hide(text, [name]) != text

    def test_whitespace_at_the_names_own_separator_hits_without_boundaries(self):
        for name in (QZ + "-portal", QZ + " portal"):
          for text in ("%s Portals" % QZ.title(), "%s Portals/x" % QZ.title(), "the %s Portal2" % QZ.title(),
                     "%s portalx" % QZ, "My%s Portal" % QZ.title(), "x%s portal" % QZ, "my%s portal/x" % QZ,
                     "%s portalé" % QZ):
            with self.subTest(name=name, text=text):
                self.assertTrue(self.hit(text, name))

    def test_percent_encoded_space_is_out_of_scope_and_does_not_hit(self):
        self.assertFalse(self.hit("%s%%20portal" % QZ, QZ + "-portal"))

    def test_short_name_spread_over_word_ends_stays_clean(self):
        # the old matcher hit "d piece" (end of "structured" + "pie"), so this can go red; the new one must not
        self.assertTrue(_old_hits("becomes a structured piece", DP), "the old matcher hit")
        self.assertFalse(self.hit("becomes a structured piece", DP))

    def test_short_name_spelled_out_alone_still_hits(self):
        self.assertTrue(self.hit("z o r k", SHORT))
        self.assertTrue(self.hit("see z o r k here", SHORT))


DP = "dp" + "ie"                                 # made-up 4-letter name, built in parts

PL = "plqx" + "vz"                                 # made-up 4-letter name, built in parts
OK_WORD = PL + "ible"
ALLOW_CONFIG = {"protected_dirs": ["/nonexistent-r1366/" + PL], "names_allow_words": [OK_WORD]}


class AllowlistPushTest(RepoCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(leak.sl, "log_error")                  # never write to a real home
        self.log = p.start()
        self.addCleanup(p.stop)

    def refused(self, text, config=ALLOW_CONFIG):
        self.commit("f.txt", text + "\n")
        return len(leak.push_check(self.repo, config)) > 0

    def test_an_allowed_word_passes(self):
        self.assertFalse(self.refused("a very %s thing" % OK_WORD))

    def test_the_name_inside_any_other_word_still_fails(self):
        for text in (OK_WORD + "x", "x" + OK_WORD, PL + "ibles", "my" + OK_WORD + "s", PL + "_x " + OK_WORD + PL):
            with self.subTest(text=text):
                self.assertTrue(self.refused(text))

    def test_allowed_word_in_upper_case_and_with_an_accent_passes(self):
        self.assertFalse(self.refused("see " + OK_WORD.upper()))
        self.assertFalse(self.refused("see " + OK_WORD.replace("i", "í")))
        self.assertFalse(self.refused("see " + OK_WORD.title()))

    def test_an_entry_equal_to_the_protected_name_is_ignored_and_logged(self):
        config = {"protected_dirs": ["/nonexistent-r1366/" + PL], "names_allow_words": [PL, PL.upper(), "the " + PL + " end"]}
        self.assertTrue(self.refused("the %s end" % PL, config))
        self.assertTrue(self.log.called)
        self.assertNotIn(PL, self.log.call_args[0][0])
        self.assertNotIn(PL, leak.allowed_words([PL], [PL, "the " + PL + " end"]))

    def test_the_builtin_words_hold_no_protected_name(self):
        names = [PL, QZ, SHORT, LONG, "ab", "vac" + "x"]
        for word in leak.ALLOWED_WORDS:
            for name in names:
                with self.subTest(word=word, name=name):
                    self.assertEqual(leak.hide(word, [name], whole=True, allow=frozenset()), word)
        self.assertEqual(len(leak.allowed_words(names)), len(leak.ALLOWED_WORDS))

    def test_the_builtin_words_pass_a_name_inside_them_and_not_inside_others(self):
        for word in leak.ALLOWED_WORDS:                  # each word holds one of these made-up 4-letter names
            for text in (word, "go %s now" % word, word.upper()):
                self.assertEqual(leak.hide(text, ["acat", "acac"]), text)
        for w in ("vacated", "vacations", "vacaciones", "vacacional"):    # a letter glued on one side, built at run time
            for text in (w + "x", "x" + w):
                with self.subTest(text=text):
                    self.assertNotEqual(leak.hide(text, ["acat", "acac"]), text)

    def test_words_removed_from_the_allowlist_are_refused_whole(self):
        # F13: three words were added once and removed by decision; with made-up names, each one written whole is refused.
        # Built at run time so no tracked file holds them.
        for word, name in (("us" + "aste", "usas"), ("va" + "cas", "acas"), ("vac" + "anti", "acan")):
            self.assertNotIn(word, leak.ALLOWED_WORDS)
            self.assertNotIn(word, leak.allowed_words([name]))
            for text in (word, word.title(), word.upper(), "go %s now" % word):
                with self.subTest(text=text):
                    self.assertNotEqual(leak.hide(text, [name]), text)

    def test_whole_true_is_unchanged_by_the_allowlist(self):
        self.assertEqual(leak.hide("go " + OK_WORD, [PL], whole=True), "go " + OK_WORD)


class NameNextToAllowedWordTest(unittest.TestCase):
    """An allowed ordinary word passes only on its own: the name beside it, glued to it or split inside it is refused."""
    N = "ac" + "at"                                  # made-up 4-letter name, held by the allowed word "vacation"; built at run time

    def refused(self, text, name=None):
        return leak.hide(text, [name or self.N]) != text

    def test_the_name_beside_an_allowed_word_is_refused(self):
        w = "vacation"
        for text in ("%s %s" % (self.N, w), "%s %s" % (w, self.N), w + self.N, self.N + w, "%s,%s" % (w, self.N)):
            with self.subTest(text=text):
                self.assertTrue(self.refused(text))

    def test_the_allowed_word_alone_still_passes_for_that_name(self):
        self.assertFalse(self.refused("go vacation now"))

    def test_a_name_equal_to_the_whole_word_is_refused_split_or_with_a_digit(self):
        w = "vacation"
        spaced = [w[:i] + " " + w[i:] for i in (2, 4, 7)]            # one space at an inner position
        dashed = [w[:i] + "-" + w[i:] for i in (2, 4)]               # one dash
        for text in [w] + spaced + dashed + [w + "1", "go %s now" % w]:
            with self.subTest(text=text):
                self.assertTrue(self.refused(text, name=w))

    def test_an_invisible_character_inside_the_word_passes_and_before_the_name_is_refused(self):
        zw = chr(0x200B)                                            # built at run time: no invisible character in the file
        self.assertFalse(self.refused("va" + zw + "cation"))
        self.assertTrue(self.refused("vacation" + zw + self.N))

    def test_a_config_entry_that_is_the_name_plus_a_suffix_stays_allowed_only_whole(self):
        # by design (F15 closed): the user's own explicit allowance of an ordinary word that holds a name
        allow = leak.allowed_words([PL], [OK_WORD])
        self.assertIn(OK_WORD, allow)
        self.assertEqual(leak.hide("see " + OK_WORD, [PL], allow=allow), "see " + OK_WORD)
        for text in (PL + "ibles", "x" + OK_WORD):
            with self.subTest(text=text):
                self.assertNotEqual(leak.hide(text, [PL], allow=allow), text)


class TrackedFilesHoldNoTestNamesTest(unittest.TestCase):
    def test_no_tracked_file_holds_an_injected_test_name(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, env=dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull))
        self.assertEqual(r.returncode, 0)
        bad = []
        for rel in r.stdout.decode("utf-8", "replace").split("\0"):
            path = os.path.join(root, rel)
            if not rel or not os.path.isfile(path) or os.path.islink(path):
                continue
            with open(path, "rb") as f:
                low = f.read().decode("utf-8", "replace").casefold()
            if any(n in low for n in (PL, QZ, DP)):
                bad.append(rel)
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
