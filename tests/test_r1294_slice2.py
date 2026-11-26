"""R-1294 slice 2: read forms in the live plugin checkout guard (plan v3 plus the v3.1 build notes, EARS 1-15).

One shared checking function (studiolib `_git_reader_ok(verb, args, extra=None)`) judges every read verb for the live-checkout guard
(mergeguard passes `_LIVE_EXTRA_SPECS`) and for the real-data exception (extra=None: exactly the verb set it had before).
The program's name is built here as chr() pieces and written in command lines as the placeholder VCS: no literal name in this file."""
import os
import re
import shutil
import statistics
import tempfile
import time
import unittest
from unittest import mock

from helpers import ROOT
from test_guard_trust import GuardCase, CLI
from test_r1199_guards import make_repo
import mergeguard
import writeguard
import studiolib as sl

V = chr(103) + chr(105) + chr(116)
SCAN = V + "leaks"
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "r1294_live_reads.txt")
SMOKE = ("VCS status", "VCS log --oneline -3", "ls")
REAL_CHECKOUT_ROOT = mergeguard.checkout_root


def c(text):
    return text.replace("VCS", V)


def X():
    return sl._LIVE_EXTRA_SPECS


def ok(verb, args, extra=False):
    return sl._git_reader_ok(verb, list(args), extra=X()) if extra else sl._git_reader_ok(verb, list(args))


class LiveCase(unittest.TestCase):
    """A temp checkout standing in for the live plugin checkout; cwd is its root (mergeguard 299)."""

    def setUp(self):
        self.live = make_repo()
        self.addCleanup(shutil.rmtree, self.live, True)
        patcher = mock.patch.object(mergeguard, "checkout_root", return_value=self.live)
        patcher.start()
        self.addCleanup(patcher.stop)

    def d(self, command, cwd=None):
        return mergeguard.decision("Bash", {"command": c(command)}, cwd or self.live)

    def refused(self, *commands):
        for command in commands:
            got = self.d(command)
            self.assertIsNotNone(got, command)
            self.assertEqual(got[0], "deny", command)

    def passes(self, *commands):
        for command in commands:
            self.assertIsNone(self.d(command), command)


# --- EARS 1: textconv, ext-diff, filters, output, show-signature and their abbreviations -----------------------------------------
BAD_FLAGS = ["--ext-diff", "--textconv", "--filters", "--output=/tmp/x", "--show-signature", "--ext-d", "--textc", "--show-sig",
             "--out=/tmp/x", "--filt"]


class Ears1Test(LiveCase):
    def test_1a_pin_the_shared_function_refuses_them_without_extra(self):          # pin: already refused by studiolib
        for verb in ("log", "show", "diff", "blame", "grep", "cat-file"):
            for flag in BAD_FLAGS:
                self.assertFalse(sl._git_reader_ok(verb, [flag]), (verb, flag))

    def test_1b_the_shared_function_refuses_them_with_the_live_extra_table(self):
        for verb in ("log", "show", "diff", "blame", "grep", "cat-file"):
            for flag in BAD_FLAGS:
                self.assertFalse(ok(verb, [flag], extra=True), (verb, flag))

    def test_1c_mergeguard_refuses_each_read_verb_with_them(self):
        for verb in ("log", "show", "diff", "blame", "grep", "cat-file"):
            for flag in BAD_FLAGS:
                self.refused("VCS %s %s" % (verb, flag))
        self.refused("VCS cat-file --textconv HEAD:a.txt", "VCS blame --textconv a.txt", "VCS log --show-signature -1")

    def test_1d_a_plain_log_still_passes(self):
        self.passes("VCS log --oneline -3", "VCS status", "VCS diff --stat HEAD~1")


# --- EARS 2: %G and a non-allowlisted %( atom ------------------------------------------------------------------------------------
class Ears2Test(LiveCase):
    def test_2a_a_signature_placeholder_is_refused_in_both_guards_whatever_the_spelling(self):
        for verb, args in (("log", ["--format=%GG"]), ("log", ["--pretty=format:%G?"]), ("show", ["--format=%GK"]),
                           ("log", ["--format=%h%GS"]), ("log", ["--format=%(signature)"]), ("shortlog", ["--format=%GG"]),
                           ("branch", ["--format=%(signature)"]), ("branch", ["--format=%G?"]), ("diff", ["100%GOOD"]),
                           ("log", ["--grep=x", "--format=%GG"]), ("rev-list", ["--grep=x", "--format=%GG"])):
            self.assertFalse(ok(verb, args), (verb, args))
            self.assertFalse(ok(verb, args, extra=True), (verb, args))

    def test_2b_a_non_allowlisted_atom_is_refused_and_the_allowlisted_ones_pass(self):
        for atom in ("signature", "contents", "refname:strip=2", "if", "trailers", "body"):
            self.assertFalse(ok("branch", ["--format=%(" + atom + ")"]), atom)
        for atom in ("refname", "refname:short", "refname:lstrip=2", "refname:rstrip=-1", "objectname", "objectname:short", "objecttype",
                     "HEAD", "upstream", "upstream:short", "upstream:track", "upstream:trackshort", "push", "push:short",
                     "subject", "contents:subject", "authorname", "authoremail", "authordate", "committername",
                     "committeremail", "committerdate", "creatordate"):
            self.assertTrue(ok("branch", ["--format=%(" + atom + ")"]), atom)

    def test_2c_plain_placeholders_pass(self):
        self.assertTrue(ok("log", ["--format=%h %s %D"]))
        self.assertTrue(ok("log", ["--format=%(refname:short)"]))
        self.assertTrue(ok("log", ["--format=%h %s %D"], extra=True))

    def test_2d_a_grep_pattern_is_not_a_format(self):
        self.assertTrue(ok("log", ["--grep=100%GOOD"]))
        self.assertTrue(ok("rev-list", ["--grep=100%GOOD"]))
        self.assertTrue(ok("grep", ["100%GOOD"]))
        self.assertTrue(ok("grep", ["-e", "100%GOOD"]))
        self.assertTrue(ok("log", ["--grep=100%GOOD"], extra=True))

    def test_2e_the_exemption_is_tied_to_its_verb(self):
        self.assertFalse(ok("diff", ["100%GOOD"]))                                  # diff has no pattern word
        self.assertFalse(ok("show", ["--grep=x", "100%GOOD"]))
        self.assertFalse(ok("log", ["--grep=x", "--format=%GG"]))

    def test_2f_mergeguard_refuses_the_atom_and_the_text_names_it_in_both_languages(self):
        got = self.d("VCS for-each-ref --format='%(signature)'")
        self.assertEqual(got[0], "deny")
        self.assertIn("%(signature)", got[1])
        self.assertIn("allowlist", got[1])
        self.assertIn("lista de lectura", got[1])
        got = self.d("VCS log --format='%GG' -1")
        self.assertEqual(got[0], "deny")
        self.assertIn("%GG", got[1])
        got = self.d("VCS log --show-signature -1")
        self.assertIn("--show-signature", got[1])

    def test_2g_mergeguard_passes_the_plain_format(self):
        self.passes("VCS log --format='%h %s %D' -3", "VCS for-each-ref --format='%(refname:short)'")


# --- EARS 3: --pretty aliases and email -------------------------------------------------------------------------------------------
class Ears3Test(LiveCase):
    def test_3a_an_alias_and_email_are_refused(self):
        for value in ("email", "myalias", "mboxrd", "x"):
            for flag in ("--pretty=", "--format="):
                self.assertFalse(ok("log", [flag + value]), (flag, value))
                self.assertFalse(ok("show", [flag + value], extra=True), (flag, value))
        self.refused("VCS log --pretty=email -1", "VCS log --pretty=myalias -1")

    def test_3b_the_built_in_names_and_a_format_body_pass(self):
        for name in ("oneline", "short", "medium", "full", "fuller", "reference", "raw", "format:%h %s", "tformat:%h", "%h"):
            self.assertTrue(ok("log", ["--pretty=" + name]), name)
            self.assertTrue(ok("log", ["--pretty=" + name], extra=True), name)
        self.assertTrue(ok("log", ["--format=oneline"]))
        self.passes("VCS log --pretty=oneline -3", "VCS log --pretty=format:%h -3")

    def test_3d_shortlog_format_alias_is_refused(self):
        for value in ("email", "myalias"):
            self.assertFalse(ok("shortlog", ["--format=" + value]), value)
            self.assertFalse(ok("shortlog", ["--format=" + value], extra=True), value)
        self.assertTrue(ok("shortlog", ["-sn"]))
        self.assertTrue(ok("shortlog", ["--format=%h"]))
        self.refused("VCS shortlog --format=email")
        self.passes("VCS shortlog -sn")

    def test_3c_a_bare_pretty_is_refused(self):
        self.assertFalse(ok("log", ["--pretty"]))
        self.refused("VCS log --pretty -1")


# --- EARS 4-8: the new per-verb checks -------------------------------------------------------------------------------------------
class NewVerbsTest(LiveCase):
    def test_4_check_ignore(self):
        self.assertTrue(ok("check-ignore", ["-v", "--", "a.txt"], extra=True))
        self.assertTrue(ok("check-ignore", ["-q", "-n", "--no-index", "--non-matching", "a.txt"], extra=True))
        for args in (["--stdin"], ["-z"], ["-v", "-x"], ["-v", "-dash"], ["--stdin", "--", "a"], ["-v"]):
            self.assertFalse(ok("check-ignore", args, extra=True), args)
        self.assertTrue(ok("check-ignore", ["-v", "--", "-after"], extra=True))      # a dash path after `--` is a path
        self.assertFalse(ok("check-ignore", ["-v", "--", "a.txt"]))                   # not on the real-data exception's list
        self.passes("VCS check-ignore -v -- a.txt")
        self.refused("VCS check-ignore --stdin", "VCS check-ignore -z a.txt")

    def test_5_cherry(self):
        self.assertTrue(ok("cherry", ["-v", "A", "B"], extra=True))
        self.assertTrue(ok("cherry", ["main"], extra=True))
        for args in (["--verbose", "A"], ["-v", "-x"], ["-v", "A", "B", "C", "D"], ["a..b"], ["-v", "--upstream"], []):
            self.assertFalse(ok("cherry", args, extra=True), args)
        self.assertFalse(ok("cherry", ["-v", "A", "B"]))
        self.passes("VCS cherry -v main other")
        self.refused("VCS cherry --verbose main")

    def test_6_diff_tree(self):
        self.assertTrue(ok("diff-tree", ["--cc", "--name-only", "abc123"], extra=True))
        self.assertTrue(ok("diff-tree", ["-r", "-m", "--root", "--no-commit-id", "--name-status", "--stat", "-p", "HEAD"], extra=True))
        for args in (["--stdin"], ["--ext-diff", "abc"], ["--textconv", "abc"], ["--output=/tmp/x", "abc"]):
            self.assertFalse(ok("diff-tree", args, extra=True), args)
        self.assertFalse(ok("diff-tree", ["--cc", "abc"]))
        self.passes("VCS diff-tree --cc --name-only HEAD")
        self.refused("VCS diff-tree --stdin")

    def test_7_branch(self):
        self.assertTrue(ok("branch", ["--points-at", "HEAD"]))
        self.assertTrue(ok("branch", ["--sort=-committerdate"]))
        for args in (["--sort=signature"], ["--points-at", "-x"], ["--points-at=-x"], ["--contains", "a..b"], ["-D", "x"],
                     ["--sort=committerdate,signature"]):
            self.assertFalse(ok("branch", args), args)
            self.assertFalse(ok("branch", args, extra=True), args)
        self.passes("VCS branch --points-at HEAD", "VCS branch --sort=-committerdate", "VCS branch -vv", "VCS branch --list 'feat*'",
                    "VCS branch -a --contains main")
        self.refused("VCS branch --sort=signature", "VCS branch --points-at -x", "VCS branch -D x", "VCS branch newbranch")

    def test_7b_branch_merged_contains_take_an_optional_ref(self):
        for flag in ("--merged", "--no-merged", "--contains", "--no-contains"):
            for args in ([flag], [flag, "main"], [flag, "-v"], ["-a", flag], ["--list", flag, "main", "x*"]):
                self.assertTrue(ok("branch", args), args)
                self.assertTrue(ok("branch", args, extra=True), args)
            for args in ([flag, "-x"], [flag, "a..b"], [flag, "-v", "-x"]):
                self.assertFalse(ok("branch", args), args)
        self.passes("VCS branch --merged", "VCS branch --merged main", "VCS branch --no-merged", "VCS branch -a --contains")
        self.refused("VCS branch --merged -x", "VCS branch --merged a..b")
        self.assertFalse(ok("branch", ["--points-at"]))                  # --points-at still needs its ref

    def test_8_for_each_ref_show_ref_and_merge_tree(self):
        self.assertTrue(ok("for-each-ref", ["--format=%(refname:short)", "--sort=-committerdate"], extra=True))
        self.assertTrue(ok("for-each-ref", ["--count=5", "--points-at", "HEAD", "refs/heads/"], extra=True))
        self.assertTrue(ok("for-each-ref", ["--contains", "main", "--merged", "HEAD", "--no-merged", "x", "refs/heads/*"], extra=True))
        for args in (["--format=%(signature)"], ["--sort=signature"], ["--points-at", "-x"], ["--merged", "a..b"], ["--count=x"],
                     ["--shell"], ["--python"], ["--perl"], ["--stdin"]):
            self.assertFalse(ok("for-each-ref", args, extra=True), args)
        for args in (["--heads"], ["--tags", "-d"], ["--hash", "refs/heads/main"], ["--hash=7", "-s"], ["--verify", "-q", "HEAD"]):
            self.assertTrue(ok("show-ref", args, extra=True), args)
        for args in (["--exclude-existing"], ["--stdin"], ["-x"], ["--verify", "-x"]):
            self.assertFalse(ok("show-ref", args, extra=True), args)
        self.passes("VCS for-each-ref --format='%(refname:short)' --sort=-committerdate", "VCS show-ref --heads")
        self.refused("VCS merge-tree HEAD main other", "VCS for-each-ref --format='%(signature)'")
        self.passes("VCS merge-tree --write-tree main other")                  # R-1360 G1: the one exact merge-tree door


class ShapeTest(unittest.TestCase):
    def test_ref_shape(self):                                       # B-8
        for word in ("HEAD@{-1}", "@{u}", "origin/main", "HEAD~3", "v1.0^{commit}", "main", "abc123"):
            self.assertTrue(sl._ref_shape_ok(word), word)
        for word in ("a..b", "-x", "", "a b", "~x", "a;b", "$x", "a'b"):
            self.assertFalse(sl._ref_shape_ok(word), word)
        self.assertTrue(sl._ref_shape_ok("/abs/path"), "pinned: the shape allows a slash-led word")

    def test_sort_key(self):
        for value in ("refname", "-committerdate", "creatordate", "authordate", "objectname", "HEAD", "version:refname", "-version:refname"):
            self.assertTrue(sl._sort_key_ok(value), value)
        for value in ("signature", "", "committerdate,refname", "contents", "refname:short", "-"):
            self.assertFalse(sl._sort_key_ok(value), value)

    def test_lstrip_and_rstrip_accept_a_negative_number(self):
        self.assertTrue(ok("branch", ["--format=%(refname:lstrip=-2)"]))
        self.assertTrue(ok("branch", ["--format=%(refname:rstrip=-1)"]))
        self.assertFalse(ok("branch", ["--format=%(refname:lstrip=x)"]))

    def test_extra_rows_union_with_the_base_rows(self):             # B-3
        self.assertTrue(ok("log", ["--stat", "--oneline", "--no-ext-diff"], extra=True))
        self.assertTrue(ok("diff", ["--stat", "--exit-code", "--quiet"], extra=True))
        self.assertFalse(ok("diff", ["--exit-code"]))               # only the live table adds it


# --- EARS 9: config reads ---------------------------------------------------------------------------------------------------------
class Ears9Test(LiveCase):
    def test_9_config_reads(self):
        self.passes("VCS config --get a.b", "VCS config --get-regexp x", "VCS config --local --list --name-only",
                    "VCS config --list --show-origin", "VCS config -l -z", "VCS config --global --get user.name", "VCS config --get-all a.b",
                    "VCS config --get a.b 'v.*'")
        self.refused("VCS config --local --unset k", "VCS config --list --file x", "VCS config --get a --unset", "VCS config --global k v",
                     "VCS config -e", "VCS config a.b c", "VCS config --get", "VCS config --list --global", "VCS config --get --file x a.b",
                     "VCS config --add a b", "VCS config --edit", "VCS config --unset a.b", "VCS config --get-regexp")


# --- EARS 10: xcrun ---------------------------------------------------------------------------------------------------------------
class Ears10Test(LiveCase):
    def test_10a_the_exact_lookup_passes(self):
        self.passes("xcrun -f VCS", "xcrun --find VCS")

    def test_10b_every_other_shape_is_refused(self):
        self.refused("./xcrun -f VCS", "xcrun -f VCS --run", "xcrun -f VCS > /tmp/x", "FOO=1 xcrun -f VCS", "xcrun VCS",
                     "xcrun -f VCS; ls", "xcrun -f VCS | cat", "/tmp/xcrun -f VCS", "xcrun -f VCS extra", "xcrun --run VCS log",
                     "xcrun -f $(echo VCS)", "xcrun -f 'VCS log'")

    def test_10c_pin_a_lookup_of_another_program_passes(self):
        self.passes("xcrun -f clang++", "xcrun --find clang")


# --- EARS 11: the three holes ----------------------------------------------------------------------------------------------------
class Ears11Test(GuardCase):
    def test_11a_a_planted_budgit_with_a_quoted_note_is_not_one_segment(self):
        note = ' ledger add "a; b"'
        self.assertTrue(sl._quoted_note_call(CLI + note, self.project, [], self.project))
        planted = os.path.join(self.project, "budgit")
        shutil.copy(CLI, planted)
        self.assertFalse(sl._quoted_note_call("./budgit" + note, self.project, [], self.project))
        self.assertFalse(sl._quoted_note_call(planted + note, self.project, [], self.project))

    def test_11b_a_quote_error_segment_gets_no_exception(self):
        self.assertFalse(sl._segment_exception("VCS log 'data/real/x.csv".replace("VCS", V), None, self.project,
                                               ["data/real/**"], self.project, True))
        self.asks("cp a data/real/y.csv; echo \"it", "cp a data/real/y.csv; echo 'it")

    def test_11b2_a_planted_budgit_does_not_hide_a_protected_path_in_its_note(self):
        for line in ('./budgit ledger retext R-12 "data/real/x.csv && data/real/y.csv are quoted"',
                     './budgit ledger outcome R-12 "ran cat data/real/x.csv | head; ok"'):
            self.asks(line)

    def test_11c_pin_apostrophes_in_prose_add_no_refusal(self):
        self.passes('echo "it\'s here"', "echo it\\'s", "cat <<EOF\nit's the plan\nEOF", "echo \"don't\" && ls docs/a.md")
        self.assertEqual(sl._base_segments('echo "it\'s here"', self.project, ["data/real/**"], self.project, "%s %s"), [])



class Ears11ScannerTest(LiveCase):
    def test_11d_the_leak_scanner_word_must_be_literal(self):
        self.refused("./%s VCS --redact" % SCAN, "../x/%s VCS --redact" % SCAN, "/tmp/%s VCS --redact" % SCAN)

    def test_11e_the_literal_scanner_and_a_trusted_absolute_path_pass(self):
        self.passes("%s VCS --redact" % SCAN, "/usr/local/bin/%s VCS --redact" % SCAN, "/opt/homebrew/bin/%s VCS --redact --no-banner" % SCAN)

    def test_11f_the_scanner_still_needs_redact(self):
        self.refused("%s VCS" % SCAN, "./%s VCS --redact" % SCAN)


# --- EARS 12: forms that stay refused (one pinned test each) ---------------------------------------------------------------------
class StaysRefusedTest(LiveCase):
    def test_ls_remote(self):
        self.refused("VCS ls-remote origin")

    def test_clone(self):
        self.refused("VCS clone https://example.invalid/x /tmp/x")

    def test_archive_output(self):
        self.refused("VCS archive -o /tmp/x.tar HEAD")

    def test_archive_remote(self):
        self.refused("VCS archive --remote=origin HEAD")

    def test_archive_exec(self):
        self.refused("VCS archive --exec=sh HEAD")

    def test_merge_tree(self):
        self.refused("VCS merge-tree HEAD main other")

    def test_for_loop(self):
        self.refused("for b in a b; do VCS log $b; done")

    def test_sh_dash_c(self):
        self.refused("sh -c 'VCS log -1'")

    def test_process_substitution_in(self):
        self.refused("cat <(VCS log -1)")

    def test_process_substitution_out(self):
        self.refused("echo x > >(VCS log -1)")

    def test_env_prefix(self):
        self.refused("GIT_PAGER=x VCS log -1", "GIT_EXTERNAL_DIFF=x VCS diff")

    def test_variable_command_word(self):
        self.refused("X=VCS; $X log -1")

    def test_printf_prose_with_the_program_word(self):
        self.refused("printf 'VCS log' | sh")

    def test_heredoc_prose_with_the_program_word(self):
        self.refused("cat <<EOF\nthe VCS tree is clean\nEOF")

    def test_for_each_ref_signature_format(self):
        self.refused("VCS for-each-ref --format='%(signature)'")

    def test_branch_sort_signature(self):
        self.refused("VCS branch --sort=signature")


# --- EARS 13: differential and the real-data exception ---------------------------------------------------------------------------
NEW_VERBS = [("for-each-ref", []), ("show-ref", []), ("check-ignore", ["-v", "--", "a"]), ("cherry", ["-v", "A", "B"]),
             ("diff-tree", ["--cc", "abc"])]
RO_BEFORE = {"status", "log", "show", "diff", "blame", "grep", "rev-parse", "ls-files", "describe", "shortlog", "merge-base", "rev-list",
             "ls-tree", "cat-file", "branch"}


def fixture_rows():
    rows = []
    with open(FIXTURE, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split(" => ", 2)
            rows.append((parts[0], parts[1]))
    return rows


class Ears13Test(LiveCase):
    def test_13a_every_fixture_form_has_the_decision_the_report_gives(self):
        rows = fixture_rows()
        self.assertGreater(len(rows), 40)
        for expected, form in rows:
            got = self.d(form)
            self.assertEqual("pass" if got is None else "refuse", expected, form)

    def test_13b_heredoc_and_apostrophe_forms_show_no_new_refusal(self):
        forms = [form for expected, form in fixture_rows() if "'" in form or "<<" in form]
        self.assertGreater(len(forms), 2)
        for form in forms:
            self.assertEqual(self.d(form) is None, dict((f, e) for e, f in fixture_rows())[form] == "pass", form)

    def test_13c_the_real_data_exception_admits_no_new_form(self):
        self.assertEqual(set(sl._RO_SPECS), RO_BEFORE)
        for verb, args in NEW_VERBS:
            self.assertFalse(sl._git_reader_ok(verb, args), verb)
        for verb in ("for-each-ref", "show-ref", "merge-tree", "check-ignore", "cherry", "diff-tree"):
            self.assertNotIn(verb, sl._RO_SPECS)

    def test_13d_nothing_is_added_to_the_real_data_table(self):
        for verb in ("log", "diff", "show", "describe", "rev-list", "rev-parse", "cat-file"):
            for flag in ("--no-ext-diff", "--no-textconv", "--exit-code", "--batch", "--objects", "--git-common-dir", "--dirty"):
                spec = sl._RO_SPECS[verb]
                self.assertNotIn(flag, spec[1] | spec[2] | spec[3], (verb, flag))

    def test_13e_the_live_table_holds_the_new_verbs(self):
        for verb in ("for-each-ref", "show-ref", "check-ignore", "cherry", "diff-tree"):
            self.assertIn(verb, X())
        for flag in ("--no-ext-diff", "--no-textconv"):
            self.assertIn(flag, X()["diff"][1])


# --- EARS 14, 15: fail closed, availability, no subprocess, timing ----------------------------------------------------------------
class Ears14Test(LiveCase):
    def test_14a_a_fault_in_the_shared_function_denies_the_call(self):
        with mock.patch.object(writeguard.sl, "_git_reader_ok", side_effect=RuntimeError("boom")):
            for form in ("VCS log --oneline -3", "VCS status", "VCS branch -vv"):
                got = self.d(form)
                self.assertIsNotNone(got, form)                      # B-2: a deny, never None
                self.assertEqual(got[0], "deny", form)

    def test_14b_the_hook_wraps_the_merge_guard_in_closed(self):
        with open(os.path.join(ROOT, "plugin", "scripts", "hook.py"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('closed("deny", mergeguard.decision, tool, tool_input, cwd, inp.get("agent_id") or inp.get("agent_type"))', text)

    def test_14e_the_hooks_closed_wrapper_denies_when_the_function_raises(self):
        """`closed` is nested in the hook's main and cannot be imported without running it. So its REAL source is cut out of hook.py
        and executed alone (it needs only `sl.log_error`): a raising decision function must yield a deny."""
        import textwrap
        import types
        with open(os.path.join(ROOT, "plugin", "scripts", "hook.py"), encoding="utf-8") as fh:
            text = fh.read()
        found = re.search(r"^    def closed\(kind, fn, \*args, \*\*kw\):\n(?:(?:        .*)?\n)+", text, re.M)
        self.assertIsNotNone(found, "the closed() wrapper was not found in hook.py")
        logged = []
        scope = {"sl": types.SimpleNamespace(log_error=logged.append)}
        exec(textwrap.dedent(found.group(0)), scope)

        def boom(*a, **k):
            raise RuntimeError("boom")
        got = scope["closed"]("deny", boom, "Bash", {}, "/x")
        self.assertEqual(got[0], "deny")
        self.assertEqual(logged, ["guard boom"])
        self.assertIsNone(scope["closed"]("deny", lambda *a: None, 1))      # a normal result passes through

    def test_14c_the_smoke_lines_pass_in_the_stand_in_checkout(self):
        self.passes(*SMOKE)


class SmokeOnTheWorktreeTest(unittest.TestCase):
    def test_14d_the_smoke_lines_pass_on_the_worktree_root(self):
        self.assertTrue(os.path.exists(os.path.join(ROOT, ".git")))
        for line in SMOKE:
            self.assertIsNone(mergeguard.decision("Bash", {"command": c(line)}, ROOT), line)


class Ears15Test(unittest.TestCase):
    def test_15a_no_subprocess_is_started(self):
        with mock.patch("subprocess.Popen", side_effect=AssertionError("a subprocess was started")):
            for line in SMOKE:
                self.assertIsNone(mergeguard.decision("Bash", {"command": c(line)}, ROOT), line)

    def test_15b_the_smoke_lines_stay_under_twice_the_cost_without_the_shared_check(self):
        """Median of 20 runs of the smoke lines. The baseline is measured in the same run with the shared reader check stubbed to a
        constant (the cost of the walk alone, which is all the read verbs cost before this slice); the ceiling is twice that."""
        def median_run():
            runs = []
            for _ in range(20):
                start = time.perf_counter()
                for line in SMOKE:
                    mergeguard.decision("Bash", {"command": c(line)}, ROOT)
                runs.append(time.perf_counter() - start)
            return statistics.median(runs)
        median_run()                                                 # warm up
        with mock.patch.object(writeguard.sl, "_git_reader_ok", lambda *a, **k: True):
            before = median_run()
        after = median_run()
        self.assertLess(after, 2 * before + 0.002, (before, after))


# --- security review fixes ---------------------------------------------------------------------------------------------------------
class SecurityFixTest(LiveCase):
    def test_med1_a_dollar_or_backtick_in_a_read_argument_is_refused(self):
        """The allowlist judges the words after quote removal, not what the shell expands them to. No word of a read verb may hold `$`
        or a backtick. Which: all of them, a single-quoted pattern such as grep '^a$' too (the walker drops the quote marks, so it
        cannot prove a word was single-quoted); the false refusal is accepted."""
        self.refused("X=--output=/tmp/pwn; VCS log $X",
                     "X=--open-files-in-pager=/tmp/p.sh; VCS grep $X foo",
                     "VCS log $(echo --output=/tmp/pwn)",
                     "VCS log `echo --output=/tmp/pwn`",
                     "VCS log \"$X\"", "VCS grep 'a$' a.txt", "VCS branch $X")
        for verb in ("log", "grep", "show", "branch", "diff"):
            self.assertFalse(ok(verb, ["$X"]), verb)
            self.assertFalse(ok(verb, ["`x`"]), verb)
            self.assertFalse(ok(verb, ["--grep=$X"]), verb)
        self.assertFalse(ok("log", ["--grep=a$"], extra=True))
        self.assertFalse(ok("log", ["--format=%h$X"], extra=True))

    def test_med1_the_refusal_names_the_rule_and_the_token(self):
        got = self.d("VCS log $X")
        self.assertEqual(got[0], "deny")
        self.assertIn("$X", got[1])
        self.assertIn("expansion", got[1])
        self.assertIn("expansion de shell", got[1])

    def test_med2_the_scanner_exemption_takes_no_assignment_prefix(self):
        self.refused("PATH=/tmp/p VCSleaks VCS --redact", "DYLD_INSERT_LIBRARIES=/tmp/x VCSleaks VCS --redact")
        self.passes("VCSleaks VCS --redact")

    def test_l1_paginate_is_not_an_admitted_global(self):
        self.refused("PAGER=/tmp/p.sh VCS --paginate log -1", "VCS --paginate log -1", "VCS -p log -1")
        self.passes("VCS --no-pager log -1")

    def test_l2_branch_list_detection_reads_only_the_options_before_dashes(self):
        for args in (["topic", "--", "-l"], ["topic", "--", "--list"]):
            self.assertFalse(ok("branch", args), args)
            self.assertFalse(ok("branch", args, extra=True), args)
        self.assertTrue(ok("branch", ["-l", "x*", "--"]))
        self.refused("VCS branch topic -- -l")

    def test_l3_only_ascii_digits_count(self):
        digit = chr(0x663)
        self.assertTrue(ok("for-each-ref", ["--count=3"], extra=True))
        self.assertFalse(ok("for-each-ref", ["--count=" + digit], extra=True))
        self.assertTrue(ok("show-ref", ["--hash=7"], extra=True))
        self.assertFalse(ok("show-ref", ["--hash=" + digit], extra=True))
        self.assertTrue(ok("branch", ["--format=%(refname:lstrip=2)"]))
        self.assertFalse(ok("branch", ["--format=%(refname:lstrip=" + digit + ")"]))
        self.assertFalse(ok("branch", ["--format=%(refname:rstrip=-" + digit + ")"]))

    def test_l5_the_echoed_token_holds_no_control_or_bidi_character(self):
        for char in (chr(0x202E), chr(0x1B), chr(0x7), chr(0x2066), chr(0x200B)):
            got = self.d("VCS log --textconv" + char + "x -1")
            self.assertEqual(got[0], "deny")
            self.assertNotIn(char, got[1], hex(ord(char)))
            self.assertIn("--textconv", got[1])
            got = self.d("VCS log --format='%G" + char + "' -1")
            self.assertNotIn(char, got[1], hex(ord(char)))


class SecurityHomeTest(LiveCase):
    """The walker rewrites $HOME and ${HOME} to the real home before it lexes, so the `$` scan never sees them. On a line where HOME can
    be changed first (another segment, or the word HOME anywhere else) the shell would pass what HOME holds, an option included."""

    def test_home_set_on_the_line_then_used_as_a_read_argument_is_refused(self):
        self.refused("HOME=--output=/tmp/pwn; VCS log $HOME",
                     "export HOME=--output=/tmp/pwn; VCS log ${HOME}",
                     "HOME=--open-files-in-pager=/tmp/p.sh; VCS grep $HOME foo",
                     "declare -x HOME=--output=/tmp/pwn\nVCS log $HOME",
                     "HOME=--output=/tmp/pwn VCS log $HOME",
                     "cd /tmp && VCS log $HOME",
                     "VCS log $HOME; echo done",
                     "echo x | VCS log $HOME",
                     "VCS branch $HOME && ls",
                     "VCS log $HOME && read HOME")

    def test_the_refusal_names_the_rule_first_and_the_token_in_both_languages(self):
        got = self.d("HOME=--output=/tmp/pwn; VCS log $HOME")
        self.assertEqual(got[0], "deny")
        self.assertIn("HOME", got[1])
        self.assertIn("regla HOME", got[1])
        self.assertLess(got[1].index("HOME rule"), got[1].index("git log"))
        self.assertIn(os.path.expanduser("~"), got[1])

    def test_a_single_segment_with_no_other_home_word_is_admitted(self):
        self.passes("VCS -C $HOME/x log --oneline", "HOME=/x VCS -C $HOME/y status", "VCS log --oneline -- $HOME/x",
                    "VCS log --oneline -- ${HOME}/x", "cd $HOME/x && VCS status", "VCS log -3")

    def test_the_line_rule_is_one_shared_function(self):
        risky = sl._home_expansion_risky
        for line in ("HOME=x; v log $HOME", "a && v log ${HOME}", "export HOME=x v log $HOME", "v log $HOME | cat", "v log $HOME\nls",
                     "v log $HOME `x`", "v log $HOME $(x)", "v log $HOME & ls", "( v log $HOME )", "v log $HOMEBREW $HOME"):
            self.assertTrue(risky(line), line)
        for line in ("v -C $HOME/x log", "v log -- ${HOME}/x", "v log", "cd /tmp && v log"):
            self.assertFalse(risky(line), line)


class Round3Test(LiveCase):
    """R-1300 (security round 3): a leading tilde in a read argument, and any shell expansion in the arguments of the non-read verbs."""

    def test_a_tilde_in_a_read_argument_is_refused(self):
        self.refused("HOME=--output=/tmp/pwn; VCS log ~/x", "PWD=--output=/tmp/p; VCS log ~+", "OLDPWD=--output=/tmp/p; VCS log ~-",
                     "VCS log --oneline -- ~/x", "VCS grep foo -- ~/x", "VCS branch ~/x", "VCS log --since=x:~y")

    def test_a_shell_expansion_in_a_fetch_merge_commit_or_add_argument_is_refused(self):
        self.refused("X=--upload-pack=/tmp/p.sh; VCS fetch $X", "X='--no-ff topic'; VCS merge --ff-only $X", "X=--amend; VCS commit -m m $X",
                     "VCS add `x`", "VCS fetch ~/x", "VCS commit -m \"$(date)\"", "VCS commit -m \"Cla${X}ude\"", "VCS commit -m \"a${x:--}b\"",
                     "VCS fetch $(echo origin)")

    def test_home_rewritten_into_a_non_read_verb_argument_is_refused(self):
        self.refused("HOME=--upload-pack=/tmp/p.sh; VCS fetch $HOME", "HOME=--amend; VCS commit -m m $HOME", "HOME=x; VCS merge --ff-only $HOME",
                     "HOME=-A; VCS add $HOME")

    def test_the_plain_forms_stay_admitted(self):
        self.passes("VCS fetch origin", "VCS merge --ff-only topic", 'VCS commit -m "plain words"', "VCS -C $HOME/x status",
                    "VCS log --oneline -- docs")

    def test_the_commit_refusal_names_the_rule_first_the_token_and_the_way_out(self):
        got = self.d("VCS commit -m \"a${x:--}b\"")
        self.assertEqual(got[0], "deny")
        self.assertIn("expansion rule", got[1])
        self.assertIn("regla de expansion", got[1])
        self.assertLess(got[1].index("expansion rule"), got[1].index("git commit"))
        self.assertIn("${x:--}", got[1])
        self.assertIn("rephrase the message without", got[1])
        self.assertIn("reescribe el mensaje", got[1])
        self.assertNotIn("-F", got[1])                   # -F is refused by the commit rule, so it is never offered as a way out
        self.assertNotIn("rephrase", self.d("VCS fetch $X")[1])


class WritePathHomeTest(unittest.TestCase):
    """R-1300 (R-1299 folded in): `writeguard._walk` rewrites the HOME expansion to the real home before it lexes, so on a line that can
    change HOME first the persistence guard judges a path the shell will not use. On such a line a write whose target came from the
    expansion asks (the only decision this guard has; it is also what an unknown target gets). The dollar and brace text is built with
    chr() so no command line or test line holds it."""
    D, OB, CB = chr(36), chr(123), chr(125)

    def ask(self, command):
        return writeguard.persistence_decision("Bash", {"command": command}, cwd=os.path.realpath(tempfile.gettempdir()))

    def test_a_write_to_a_home_expansion_on_a_line_that_can_change_home_asks(self):
        for line in ("HOME=/tmp/x; cp a %sHOME/f" % self.D,
                     "export HOME=/tmp/x; echo hi > %s%sHOME%s/f" % (self.D, self.OB, self.CB),
                     "cd /tmp && cp a %sHOME/f" % self.D):
            got = self.ask(line)
            self.assertIsNotNone(got, line)
            self.assertEqual(got[0], "ask", line)

    def test_a_single_segment_write_to_home_keeps_todays_decision(self):
        for line in ("cp a %sHOME/Documents/x" % self.D, "echo hi > %sHOME/tmp-note" % self.D):
            self.assertIsNone(self.ask(line), line)

    def test_a_read_of_a_home_expansion_on_a_risky_line_does_not_ask(self):
        self.assertIsNone(self.ask("HOME=/tmp/x; cat %sHOME/f" % self.D))

    def test_the_risky_line_rule_is_the_one_shared_function(self):
        with mock.patch.object(sl, "_home_expansion_risky", return_value=False):
            self.assertIsNone(self.ask("HOME=/tmp/x; cp a %sHOME/f" % self.D))

    def test_a_tilde_write_target_on_a_line_that_sets_home_pwd_or_oldpwd_asks(self):
        T = chr(126)
        for line in ("HOME=/tmp/x; cp a %s/f" % T, "PWD=/tmp/x; cp k %s+/f" % T, "OLDPWD=/tmp/x; cp k %s-/f" % T,
                     "export HOME=/tmp/x; echo hi > %s/f" % T):
            got = self.ask(line)
            self.assertIsNotNone(got, line)
            self.assertEqual(got[0], "ask", line)

    def test_a_destructive_git_verb_with_a_home_argument_on_a_risky_line_asks(self):
        """R-1300 LOW-4: `_git_text` also judges the HOME expansion as unknown on a risky line."""
        D = self.D
        for line in ("HOME=--hard; %s reset %sHOME" % (V, D), "export HOME=--force; %s push %s%sHOME%s" % (V, D, self.OB, self.CB)):
            got = writeguard.destructive_git_decision("Bash", {"command": line})
            self.assertIsNotNone(got, line)
            self.assertEqual(got[0], "ask", line)
        for line in ("%s reset %sHOME/x" % (V, D), "HOME=x; %s status %sHOME" % (V, D)):      # one segment; a verb that throws nothing away
            self.assertIsNone(writeguard.destructive_git_decision("Bash", {"command": line}), line)

    def test_a_home_stand_in_in_the_verb_position_or_with_any_destructive_verb_asks(self):
        """R-1300 security r2 LOW-4: the verb itself can come from HOME, and the verb set is the one `_destructive_why` branches on."""
        D = self.D
        for line in ("HOME=reset; %s %sHOME --hard HEAD~3" % (V, D), "export HOME=--delete; %s tag %sHOME v1" % (V, D),
                     "export HOME=--force; %s worktree remove %sHOME" % (V, D)):
            got = writeguard.destructive_git_decision("Bash", {"command": line})
            self.assertIsNotNone(got, line)
            self.assertEqual(got[0], "ask", line)
        self.assertIsNone(writeguard.destructive_git_decision("Bash", {"command": "HOME=x; %s -C %sHOME status" % (V, D)}))

    def test_the_verb_set_covers_every_verb_destructive_why_branches_on(self):
        import inspect
        src = inspect.getsource(writeguard._destructive_why)
        named = set(re.findall(r'verb == "([a-z-]+)"', src))
        for group in re.findall(r"verb in \(([^)]*)\)", src):
            named |= set(re.findall(r'"([a-z-]+)"', group))
        self.assertTrue(named)
        self.assertEqual(named - writeguard._DESTRUCTIVE_VERBS, set())

    def test_a_directory_tilde_write_target_always_asks(self):
        T = chr(126)
        for line in ("cp a %s-/pre-commit" % T, "cp a %s+/f" % T, "echo hi > %s1/f" % T, "cd /x/.git/hooks; cd /tmp; cp a %s-/pre-commit" % T):
            got = self.ask(line)
            self.assertIsNotNone(got, line)
            self.assertEqual(got[0], "ask", line)
        self.assertIsNone(self.ask("cp a %s/Documents/x" % T))

    def test_a_single_segment_tilde_write_keeps_todays_decision(self):
        self.assertIsNone(self.ask("cp a %s/Documents/x" % chr(126)))

    def test_a_multi_segment_tilde_write_asks_even_with_a_hidden_home_assignment(self):
        """R-1300 security r2 MED-1: a multi-segment tilde line is risky; the word HOME may be quoted (`"H"OME`), so no text is matched."""
        T = chr(126)
        for line in ("cd /tmp && cp a %s/Documents/x" % T, "export \"H\"OME=/x; cp a %s/f" % T):
            got = self.ask(line)
            self.assertIsNotNone(got, line)
            self.assertEqual(got[0], "ask", line)

    def test_the_risky_rule_also_fires_on_a_tilde_word_with_a_home_pwd_or_oldpwd_word(self):
        T, D = chr(126), self.D
        # R-1300 security r2 MED-1: a multi-segment tilde line is risky (the line `cd /tmp && v log ~/y` moved from False to True)
        for line in ("HOME=x; v log %s/y" % T, "PWD=x v log %s+" % T, "OLDPWD=x; v log %s-" % T, "v log %sHOME %s/y; ls" % (D, T),
                     "cd /tmp && v log %s/y" % T, "export \"H\"OME=/x; v -C %s merge x" % T):
            self.assertTrue(sl._home_expansion_risky(line), line)
        self.assertFalse(sl._home_expansion_risky("v log 'a; b' %s/y" % T))           # a separator inside quotes is not one
        for line in ("v log %s/y" % T, "v log"):
            self.assertFalse(sl._home_expansion_risky(line), line)


class GlobNonReadTest(LiveCase):
    """R-1300 MED-3: a glob word can expand to an option only when its FIRST character is a glob character (the shell keeps a literal
    prefix, and an option starts with a dash). Such a word before `--` is refused in every verb, read verbs and branch included, through
    the one shared word test. After `--` stays as it was."""

    def test_a_word_starting_with_a_glob_character_before_dashdash_is_refused(self):
        self.refused("VCS fetch *", "VCS add *.py", "VCS log [-]-output=x", "VCS config --get-regexp *", "VCS add [a].py", "VCS log ?x",
                     "VCS branch --list *")

    def test_a_glob_word_after_dashdash_or_with_a_literal_first_character_stays_admitted(self):
        self.passes("VCS add -- *.py", "VCS config --get-regexp alias.*", "VCS commit -m \"fix [x]?\"", "VCS branch --list x*",
                    "VCS for-each-ref refs/heads/*", "VCS fetch origin", "VCS log --oneline -- *.py")

    def test_the_glob_refusal_names_the_rule_first_the_token_and_the_way_out(self):
        for line, verb, token in (("VCS add *.py", "git add", "*.py"), ("VCS log [-]-output=x", "git log", "[-]-output=x")):
            got = self.d(line)
            self.assertEqual(got[0], "deny")
            self.assertIn("glob rule", got[1])
            self.assertIn("regla de glob", got[1])
            self.assertLess(got[1].index("glob rule"), got[1].index(c(verb)))
            self.assertIn(token, got[1])
            self.assertIn("put pathspecs after `--`, or start the pattern with a literal character", got[1])
            self.assertIn("pon las rutas despues de `--`, o empieza el patron con un caracter literal", got[1])

    def test_a_consumed_dashdash_does_not_end_the_glob_check(self):
        """R-1300 security r2 MED-3a: after `-e` or `-m` the `--` is that option's value, not the end of options."""
        self.refused("VCS grep -e -- *", "VCS commit -m -- *")

    def test_an_unquoted_paren_on_a_line_that_runs_the_program_is_refused(self):
        """R-1300 security r2 MED-3b: a zsh glob qualifier, `(a|b)`, an extglob or a subshell cannot be judged; a quoted paren can."""
        self.refused("VCS log -- .*(e:'x':)", "VCS log -- @(x)", "(cd /tmp; VCS status)", "VCS log -- (a|b)")
        self.passes("VCS commit -m \"fix (R-1)\"", "VCS log --oneline -3")
        got = self.d("VCS log -- .*(e:'x':)")
        self.assertIn("parenthesis rule", got[1])
        self.assertIn("regla de parentesis", got[1])
        self.assertIn("(e:", got[1])

    def test_the_unquoted_raw_text_is_one_shared_function(self):
        raw = sl._unquoted_raw("a 'b (c)' \"d (e)\" f\\(g (h) \"H\"OME")
        self.assertEqual(len(raw), len("a 'b (c)' \"d (e)\" f\\(g (h) \"H\"OME"))
        self.assertEqual(raw.count("("), 1)
        self.assertNotIn("HOME", raw)
        self.assertIn("$(", sl._unquoted_raw("x \"$(y)\""))                # a substitution inside double quotes still runs

    def test_the_glob_test_is_one_shared_function(self):
        self.assertTrue(sl._glob_word("*") and sl._glob_word("?x") and sl._glob_word("[-]-o"))
        self.assertFalse(sl._glob_word("alias.*") or sl._glob_word("refs/heads/*") or sl._glob_word("-x") or sl._glob_word(""))


class CdpathTest(LiveCase):
    """R-1300 security r2 MED-2: CDPATH can send a relative `cd` anywhere (the shell ignores it for `/`, `./` and `../` only), so on a line
    that runs the program a relative target that does not start with `./` or `../` is an unknown folder."""

    def setUp(self):
        LiveCase.setUp(self)
        self.other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.other, True)

    def test_a_bare_relative_cd_target_before_the_program_is_unknown(self):
        for line in ("CDPATH=/tmp; cd sub; VCS merge x", "cd sub && VCS merge x", "pushd sub; VCS checkout b"):
            got = self.d(line, cwd=self.other)
            self.assertIsNotNone(got, line)
            self.assertEqual(got[0], "deny", line)

    def test_dot_and_absolute_targets_and_lines_without_the_program_keep_todays_decision(self):
        for line in ("cd ./sub && VCS status", "cd ./sub && VCS merge x", "cd ../sub && VCS merge x", "cd /tmp && VCS merge x", "cd sub && ls",
                     "cd sub; VCS status"):
            self.assertIsNone(self.d(line, cwd=self.other), line)


class TargetsHomeTest(LiveCase):
    """R-1300 MED-1: on a risky line, a -C, --git-dir, --work-tree or cd value that came from a HOME or tilde word is unknown, so it
    counts as reaching the live checkout (the walker and the shell may disagree on what HOME holds)."""
    D, T = chr(36), chr(126)

    def test_home_spelled_targets_after_a_same_line_home_assignment_reach_live(self):
        D, T = self.D, self.T
        self.refused("HOME=/tmp/x; VCS -C %s merge x" % T, "HOME=/tmp/x; VCS -C %sHOME checkout b" % D,
                     "HOME=/tmp/x; VCS --git-dir=%s/.git merge x" % T, "HOME=/tmp/x; cd %s; VCS commit --amend" % T,
                     "HOME=/tmp/x; VCS --work-tree=%s merge x" % T)

    def test_a_hidden_home_assignment_before_a_tilde_target_reaches_live(self):
        """R-1300 security r2 MED-1: `export "H"OME=/x` hides the word HOME from a text match; the unquoted separator still makes it risky."""
        T = self.T
        self.refused("export \"H\"OME=/x; VCS -C %s merge x" % T, "eval \"export HO\"ME=/x; VCS -C %s merge x" % T)
        self.passes("VCS -C %s/proj status" % T)

    def test_a_directory_tilde_is_always_an_unknown_target(self):
        """R-1300 security r2 MED-1: `~+`, `~-`, `~N`, `~+N` and `~-N` read PWD, OLDPWD and the directory stack, which the walker never
        resolves; on any line they are unknown, so they count as reaching the live checkout."""
        T = self.T
        self.refused("VCS -C %s- merge x" % T, "VCS -C %s1 merge x" % T, "cd /a; cd /tmp; VCS -C %s- merge x" % T, "VCS -C %s+2 merge x" % T,
                     "VCS --git-dir=%s-/.git merge x" % T)
        self.passes("VCS -C %s/proj status" % T)
        self.assertTrue(all(sl._dir_tilde(w) for w in ("%s-" % T, "%s+/x" % T, "%s1" % T, "%s-2/y" % T, "a=%s-/x" % T, "a:%s+" % T)))
        self.assertFalse(any(sl._dir_tilde(w) for w in (T, "%s/x" % T, "%sroot" % T, "x%s-" % T, "%s+x" % T)))

    def test_a_single_segment_home_target_keeps_todays_decision(self):
        self.passes("VCS -C %sHOME/x status" % self.D, "VCS -C %sHOME/x commit -m \"plain words\"" % self.D)


class InterimLexerTest(LiveCase):
    """R-1300 security r3 (interim, durable fix is R-1301): the walker's quote model differs from the shell's for `#` comments, `$'..'` and
    heredoc bodies, so on a line that names the program and holds one of them the walk is not trusted: the line counts as reaching live
    and `_unquoted_raw` returns the raw line unblanked."""

    def setUp(self):
        LiveCase.setUp(self)
        self.other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.other, True)

    def test_a_comment_with_a_quote_cannot_hide_a_merge_into_live(self):
        line = "echo hi # it's\nVCS -C %s merge x\n# won't" % self.live
        got = self.d(line, cwd=self.other)
        self.assertIsNotNone(got, line)
        self.assertEqual(got[0], "deny")

    def test_an_ansi_c_quote_cannot_hide_a_merge_into_live(self):
        D = chr(36)
        line = "echo %s'it\\'s'\nVCS -C %s merge x\necho %s'a'" % (D, self.live, D)
        got = self.d(line, cwd=self.other)
        self.assertIsNotNone(got, line)
        self.assertEqual(got[0], "deny")

    def test_a_heredoc_line_that_holds_the_program_and_a_non_read_verb_is_refused(self):
        for line in ("sh <<EOF\nVCS -C %s merge x\nEOF" % self.live, "cat <<EOF\nVCS -C %s merge x\nEOF" % self.live):
            got = self.d(line, cwd=self.other)
            self.assertIsNotNone(got, line)
            self.assertEqual(got[0], "deny")

    def test_pins_a_heredoc_with_no_program_word_and_a_plain_status_keep_their_decision(self):
        plain = "cat > /tmp/x <<EOF\nhello (world)\nEOF"
        self.assertIsNone(self.d(plain, cwd=self.other))
        self.assertIsNone(self.d(plain))
        self.assertIsNone(writeguard.persistence_decision("Bash", {"command": plain}, cwd=self.other))
        self.assertIsNone(self.d("VCS status", cwd=self.other))
        self.assertIsNone(self.d("VCS status"))

    def test_the_unsafe_line_test_and_the_unblanked_raw_text_are_one_shared_function(self):
        D = chr(36)
        for line in ("echo hi # it's\nVCS merge x", "echo %s'a' VCS" % D, "VCS merge x <<EOF\nEOF", "GIT merge x <<EOF"):
            line = c(line)
            self.assertTrue(sl._lexer_unsafe(line), line)
            self.assertEqual(sl._unquoted_raw(line), line)
        for line in ("VCS status", "cat <<EOF", "echo a#b VCS"):   # `echo $'a'` now counts (a `$` can build the name), so it left this list
            self.assertFalse(sl._lexer_unsafe(c(line)), line)
        self.assertNotIn("(", sl._unquoted_raw(c("VCS commit -m 'a (b)'")))
        self.assertNotIn("(", sl._unquoted_raw("cat <<EOF 'a (b)'"))

    def test_low_a_value_option_written_as_a_separate_word_is_not_the_verb(self):
        D = chr(36)
        for opt in ("--config-env", "--super-prefix", "--attr-source"):
            line = "HOME=reset; %s %s a=B %sHOME --hard" % (V, opt, D)
            got = writeguard.destructive_git_decision("Bash", {"command": line})
            self.assertIsNotNone(got, line)
            self.assertEqual(got[0], "ask", line)


if __name__ == "__main__":
    unittest.main()
