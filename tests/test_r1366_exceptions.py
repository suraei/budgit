"""R-1366 F13: exact-line exceptions (.studio/names-exceptions.json) for push_check; made-up names only."""
import hashlib
import json

import leak
from test_r1366_push_check import RepoCase, CONFIG, NAME_A, NAME_B

EXC = ".studio/names-exceptions.json"
BAD = "the %s tool" % NAME_A
OTHER = "also %s here" % NAME_A


def digest(line):
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def entry(path, line, why="external URL"):
    return {"path": path, "sha256": digest(line), "why": why}


class ExceptionsTest(RepoCase):
    def exc(self, lines, version=1, raw=None):
        import os
        os.makedirs(os.path.join(self.repo, ".studio"), exist_ok=True)
        self.write(EXC, raw if raw is not None else json.dumps({"version": version, "lines": lines}))
        self.git("add", EXC)

    def doc(self, text="ok\n%s\n" % BAD):
        self.write("doc.txt", text)
        self.git("add", "doc.txt")
        self.git("commit", "-q", "-m", "work")

    def hits(self, **kw):
        return [(h.file, h.line, h.alias) for h in leak.push_check(self.repo, CONFIG, **kw)]

    def test_an_excepted_line_passes(self):
        self.exc([entry("doc.txt", BAD)])
        self.doc()
        self.assertEqual(self.hits(), [])

    def test_the_same_files_other_line_still_hits(self):
        self.exc([entry("doc.txt", BAD)])
        self.doc("ok\n%s\n%s\n" % (BAD, OTHER))
        self.assertEqual(self.hits(), [("doc.txt", 3, "protected-2")])

    def test_an_edited_line_hits_again(self):
        self.exc([entry("doc.txt", BAD)])
        self.doc("ok\n%s \n" % BAD)
        self.assertEqual(self.hits(), [("doc.txt", 2, "protected-2")])

    def test_the_same_line_in_another_path_hits(self):
        self.exc([entry("doc.txt", BAD)])
        self.write("other.txt", BAD + "\n")
        self.git("add", "other.txt")
        self.doc()
        self.assertEqual(self.hits(), [("other.txt", 1, "protected-2")])

    def test_a_path_level_hit_is_not_excepted(self):
        pathname = "dir-%s.txt" % NAME_A
        self.exc([entry(pathname, "x")])
        self.write(pathname, "x\n")
        self.git("add", pathname)
        self.git("commit", "-q", "-m", "work")
        self.assertEqual(self.hits(), [("dir-protected-2.txt", 0, "protected-2"),
                                       (EXC, 1, "protected-2")])      # the data file is itself checked: its path field holds the name

    def test_a_message_hit_is_not_excepted(self):
        self.exc([entry("doc.txt", BAD)])
        self.doc("ok\n")
        hits = leak.push_check(self.repo, CONFIG, messages=[("abcdef123456", BAD)])
        self.assertEqual([(h.file, h.line) for h in hits], [("<message abcdef12>", 0)])

    def test_the_exceptions_file_itself_is_never_excepted(self):
        self.exc([entry(EXC, BAD), {"path": "p.txt", "sha256": digest("x"), "why": "name %s" % NAME_A}])
        self.doc("ok\n")
        got = self.hits()
        self.assertEqual([(f, a) for f, _l, a in got], [(EXC, "protected-2")])

    def test_a_malformed_file_fails_closed(self):
        self.exc([], raw="{not json")
        self.doc()
        self.assertEqual([h[2] for h in self.hits()], ["check-failed"])

    def test_a_wrong_version_fails_closed(self):
        self.exc([entry("doc.txt", BAD)], version=2)
        self.doc()
        self.assertEqual([h[2] for h in self.hits()], ["check-failed"])

    def test_an_extra_key_fails_closed(self):
        e = entry("doc.txt", BAD)
        e["line"] = 2
        self.exc([e])
        self.doc()
        self.assertEqual([h[2] for h in self.hits()], ["check-failed"])

    def test_a_missing_file_gives_the_old_behavior(self):
        self.doc()
        self.assertEqual(self.hits(), [("doc.txt", 2, "protected-2")])

    def test_range_mode_honours_it_the_same_way(self):
        self.exc([entry("doc.txt", BAD)])
        self.doc("ok\n%s\n%s\n" % (BAD, OTHER))
        self.assertEqual(self.hits(exclude=[]), [("doc.txt", 3, "protected-2")])

    def test_range_mode_excepted_line_passes(self):
        self.exc([entry("doc.txt", BAD)])
        self.doc()
        self.assertEqual(self.hits(exclude=[]), [])

    def test_an_excepted_line_does_not_hide_a_name_split_by_a_line_break(self):
        self.exc([entry("doc.txt", BAD)])
        self.doc("ok\n%s\n%s\n%s\n" % (BAD, NAME_A[:5], NAME_A[5:]))
        self.assertEqual(self.hits(), [("doc.txt", 1, "protected-2")])
        self.assertEqual(self.hits(exclude=[]), [("doc.txt", 1, "protected-2")])

    def test_an_excepted_line_alone_still_passes_when_no_name_is_split(self):
        self.exc([entry("doc.txt", BAD)])
        self.doc("ok\n%s\nzebra\nplain corp\n" % BAD)
        self.assertEqual(self.hits(), [])

    def test_an_excepted_line_with_a_crlf_ending_passes(self):
        self.exc([entry("doc.txt", BAD)])
        self.doc("ok\r\n%s\r\n" % BAD)
        self.assertEqual(self.hits(), [])

    def test_a_bom_before_an_excepted_first_line_hits(self):
        self.exc([entry("doc.txt", BAD)])
        self.doc(chr(0xFEFF) + BAD + "\n")
        self.assertEqual(self.hits(), [("doc.txt", 1, "protected-2")])

    def test_a_symlink_as_the_exceptions_file_fails_closed(self):
        import os
        os.makedirs(os.path.join(self.repo, ".studio"), exist_ok=True)
        self.write("real-exceptions.json", json.dumps({"version": 1, "lines": [entry("doc.txt", BAD)]}))
        os.symlink("../real-exceptions.json", os.path.join(self.repo, EXC))
        self.git("add", EXC, "real-exceptions.json")
        self.doc()
        self.assertEqual([h[2] for h in self.hits()], ["check-failed"])

    def test_range_mode_the_tip_wins_when_the_entry_comes_in_a_later_commit(self):
        self.doc()                                   # commit 1 adds the line
        self.exc([entry("doc.txt", BAD)])
        self.git("commit", "-q", "-m", "entry")      # commit 2 adds the entry
        self.assertEqual(self.hits(exclude=[]), [])

    def test_duplicate_entries_are_harmless(self):
        self.exc([entry("doc.txt", BAD), entry("doc.txt", BAD, why="again")])
        self.doc()
        self.assertEqual(self.hits(), [])

    def test_a_float_or_a_bool_version_fails_closed(self):
        for raw in ('{"version": 1.0, "lines": []}', '{"version": true, "lines": []}'):
            with self.subTest(raw=raw):
                self.exc([], raw=raw)
                self.doc()
                self.assertEqual([h[2] for h in self.hits()], ["check-failed"])

    def test_an_entry_for_a_path_that_does_not_exist_is_harmless(self):
        self.exc([entry("nowhere/none.txt", BAD)])
        self.doc("ok\n")
        self.assertEqual(self.hits(), [])
