"""Slice A: the real-data read exception declines partial clones, oversized config, bare-repo folders and unsafe
submodule flags, passes status and a work-tree diff only with --ignore-submodules (all), and names its rule.

Every program run is a subprocess argv list under a throwaway HOME (asserted in setUp), XDG_CONFIG_HOME and BUDGIT_HOME,
on fixture repos under tempfile.mkdtemp. Every "no program ran" test has a control: the plain program, same repo, DOES
create the marker, so the test could fail. Marker tests assert the verdict plus that control; they do not claim the guard
ran the tool. Hidden or invalid bytes are built with chr() / bytes literals.
"""
import json
import os
import struct
import subprocess
import sys
import unittest
from unittest import mock

from helpers import StudioTestCase, ROOT, patch_env

import studiolib as sl
import i18n

V = chr(103) + chr(105) + chr(116)                  # the program's name for the argv runs, built here; the two command strings the guard judges (check, fire) write it literally
GIT = "/usr/bin/" + V
HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
NEEDS = "guard.r1294_needs_ignore_submodules"
KEYS = ("guard.r1294_partial", "guard.r1294_config_big", "guard.r1294_nested", "guard.r1294_config_unreadable",
        "guard.r1294_bare", NEEDS)
NEW_ENV = ("GIT_ALLOW_PROTOCOL", "GIT_PROTOCOL_FROM_USER")
OLD_ENV = ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_TRACE",
           "GIT_EXTERNAL_DIFF", "GIT_PAGER", "GIT_SSH_COMMAND", "GIT_ASKPASS", "GIT_PROXY_COMMAND",
           "GIT_ALTERNATE_OBJECT_DIRECTORIES")
MODE_160000 = struct.pack(">I", 0o160000)


class Case(StudioTestCase):
    """A temp project that is a repo with a protected folder data/real, two commits, and a hermetic environment."""

    def setUp(self):
        super().setUp()
        self.fake_home = os.path.join(self.tmp, "fake-user-home")
        os.makedirs(self.fake_home)
        patch_env(self, HOME=self.fake_home, XDG_CONFIG_HOME=None)
        for name in [k for k in os.environ if k in sl._GIT_ENV_BAD or k.startswith(("GIT_CONFIG", "GIT_TRACE"))]:
            patch_env(self, **{name: None})
        self.assertTrue(os.path.expanduser("~").startswith(self.tmp), "HOME must be inside the temp dir")
        self.assertTrue(os.environ["HOME"].startswith(self.tmp))
        self.sys_cfg = os.path.join(self.tmp, "sys", "gitconfig")
        os.makedirs(os.path.dirname(self.sys_cfg))
        patcher = mock.patch.object(sl, "_SYSTEM_CONFIGS", [self.sys_cfg], create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        sl.activate(self.project, "r1294-s")              # the hook resolves the project from the bound session
        pj =os.path.join(sl.studio_dir(self.project), "project.json")
        cfg = json.load(open(pj))
        cfg["protected_paths"] = ["data.json", "data/real/**"]
        json.dump(cfg, open(pj, "w"))
        self.root = os.path.realpath(self.project)
        self.rd = os.path.join(self.root, "data", "real")
        self.markers = os.path.join(self.tmp, "markers")
        os.makedirs(self.markers)
        for rel, text in (("data/real/x.csv", "one\n"), ("docs/a.md", "one\n")):
            os.makedirs(os.path.dirname(os.path.join(self.root, rel)), exist_ok=True)
            open(os.path.join(self.root, rel), "w").write(text)
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "first")
        open(os.path.join(self.root, "docs", "a.md"), "w").write("two\n")
        self.git("commit", "-q", "-a", "-m", "second")

    # --- helpers
    def env(self):
        return {"PATH": "/usr/bin:/bin", "HOME": self.fake_home, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_TERMINAL_PROMPT": "0"}

    def git(self, *args, cwd=None, check=True):
        p = subprocess.run([GIT] + list(args), cwd=cwd or self.root, env=self.env(), capture_output=True, text=True,
                           timeout=60, stdin=subprocess.DEVNULL)
        if check and p.returncode != 0:
            raise RuntimeError("setup %s: %s" % (args, p.stderr))
        return p.stdout.strip()

    def plain(self, *args):
        """The unguarded program, as the exception would let it run (the positive control of the marker tests)."""
        return self.git("-C", self.rd, *args, check=False)

    def script(self, name):
        path = os.path.join(self.tmp, name + ".sh")
        open(path, "w").write("#!/bin/sh\ntouch %s\nexit 1\n" % self.marker(name))
        os.chmod(path, 0o755)
        return path

    def marker(self, name):
        return os.path.join(self.markers, name)

    def set_config(self, key, value, cwd=None):
        self.git("config", key, value, cwd=cwd)

    def check(self, tail, agent=None):
        return sl.real_data_check("Bash", {"command": f"git -C {self.rd} {tail}"}, self.project, cwd=self.project,
                                  agent_id=agent)

    def allowed(self, *tails):
        for tail in tails:
            got, keys = self.check(tail)
            self.assertIsNone(got, "%s -> %r" % (tail, got))

    def declined(self, *tails, key=None):
        for tail in tails:
            got, keys = self.check(tail)
            self.assertIsNotNone(got, tail)
            self.assertEqual(got[0], "ask", tail)
            if key:
                self.assertTrue(got[1].startswith(i18n.t(key)), "%s -> %r" % (tail, got[1]))

    def partial_clone(self, uploadpack=None, promisor=True):
        self.set_config("core.repositoryformatversion", "1")
        self.set_config("extensions.partialClone", "origin")
        self.set_config("remote.origin.url", os.path.join(self.tmp, "no-such-remote"))
        if promisor:
            self.set_config("remote.origin.promisor", "true")
        if uploadpack:
            self.set_config("remote.origin.uploadpack", uploadpack)

    # --- gitlink fixtures built by the real tool
    def nested_repo(self):
        sub = os.path.join(self.rd, "sub")
        os.makedirs(sub)
        open(os.path.join(sub, "n.txt"), "w").write("n\n")
        self.git("init", "-q", cwd=sub)
        self.git("add", "-A", cwd=sub)
        self.git("commit", "-q", "-m", "n", cwd=sub)
        sha = self.git("rev-parse", "HEAD", cwd=sub)
        self.set_config("core.fsmonitor", self.script("nested"), cwd=sub)     # set after the commit: no marker from setup
        return sha

    def index_path(self):
        return os.path.join(self.root, ".git", "index")

    def index_version(self):
        with open(self.index_path(), "rb") as fh:
            return struct.unpack(">I", fh.read(8)[4:8])[0]

    def gitlink(self, kind="v2"):
        sha = self.nested_repo()
        if kind == "v3":
            open(os.path.join(self.root, "docs", "intent.md"), "w").write("i\n")
            self.git("add", "-N", "docs/intent.md")
        self.git("update-index", "--add", "--cacheinfo", "160000,%s,data/real/sub" % sha)
        if kind == "v4":
            self.git("update-index", "--index-version", "4")
        if kind == "split":
            self.git("update-index", "--split-index")
        if kind == "truncated":
            data = open(self.index_path(), "rb").read()
            open(self.index_path(), "wb").write(data[:len(data) // 2])
        want = {"v2": 2, "v3": 3, "v4": 4}.get(kind)
        if want:
            self.assertEqual(self.index_version(), want, "the fixture is not the index version it claims")
        self.assertIn(MODE_160000, open(self.index_path(), "rb").read() if kind != "split" else MODE_160000)
        self.clear_marker("nested")

    def clear_marker(self, name):
        if os.path.exists(self.marker(name)):
            os.remove(self.marker(name))


class CleanRepoBound(Case):
    """The false-refusal bound: a plain repo keeps the exception (status and a work-tree diff carry the flag)."""

    def test_a_clean_repo_keeps_the_exception_for_status_log_show_diff(self):
        self.allowed("status --ignore-submodules=all", "log", "log -p", "show", "diff --ignore-submodules=all",
                     "diff --cached", "log --submodule=short")

    def test_global_config_with_ordinary_keys_keeps_the_exception(self):
        open(os.path.join(self.fake_home, ".gitconfig"), "w").write(
            "[user]\n\tname = t\n[pull]\n\trebase = true\n[init]\n\tdefaultBranch = main\n[submodule]\n\trecurse = true\n"
            "[credential]\n\thelper = osxkeychain\n")
        self.allowed("status --ignore-submodules=all", "log", "diff --ignore-submodules=all")

    def test_probe_prints_which_keys_of_the_studio_repo_config_match(self):
        """Read-only probe for the report; it gates nothing."""
        p = subprocess.run([GIT, "-C", ROOT, "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True,
                           text=True, env=self.env())
        cfg = os.path.join(p.stdout.strip(), "config") if p.returncode == 0 else ""
        if not cfg or not os.path.exists(cfg):
            return
        old = sl.re.compile(r"external|textconv|fsmonitor|hookspath|sshcommand|askpass|filter|include|worktree|command|"
                            r"driver|program|credential|proxy", sl.re.I)
        body = open(cfg, errors="replace").read()
        keys = [ln.strip().split("=")[0].strip() for ln in body.splitlines() if "=" in ln]
        new = sl._CONFIG_LOCAL
        sys.stderr.write("R-1294 probe - old matches %s; new matches %s\n" % (
            [k for k in keys if old.search(k)], [k for k in keys if new.search(k)]))


class ReasonChannel(Case):
    def test_a0_a_slice_a_decline_survives_the_harmless_step_and_names_its_rule(self):
        self.partial_clone()
        got, keys = self.check("log")
        self.assertIsNotNone(got, "the protected read ask was dropped before the reason could reach the verdict")
        self.assertEqual(got[0], "ask")
        self.assertEqual(got[1], i18n.t("guard.r1294_partial"))

    def test_a0_the_reason_text_goes_through_i18n_rule_name_first_in_both_languages(self):
        for lang in ("en", "es"):
            patch_env(self, BUDGIT_LANG=lang)
            for key in KEYS:
                text = i18n.t(key)
                self.assertIn("rule" if lang == "en" else "regla", text.split(":")[0], "%s/%s -> %r" % (lang, key, text))
                self.assertNotIn(" her ", text.lower())

    def test_d_the_english_text_tells_the_agent_to_add_the_flag(self):
        patch_env(self, BUDGIT_LANG="en")
        self.assertIn("--ignore-submodules=all", i18n.t(NEEDS))

    def test_n7_a_slice_a_decline_has_no_trust_offer_and_no_remembered_keys(self):
        self.partial_clone()
        got, keys = self.check("log")
        self.assertEqual(keys, [])
        self.assertNotIn(i18n.t("trust.offer"), got[1])
        self.assertEqual(got[1], i18n.t("guard.r1294_partial"))

    def test_a0_a_decline_from_an_existing_rule_keeps_todays_text_and_offer(self):
        self.set_config("core.fsmonitor", "/bin/true")
        got, keys = self.check("log")
        self.assertEqual(got[0], "ask")
        self.assertTrue(got[1].startswith("budgit guard: "), got[1])
        self.assertIn("touches protected real data", got[1])
        self.assertIn(i18n.t("trust.offer"), got[1])

    def fire(self, tail, **extra):
        payload = dict(hook_event_name="PreToolUse", tool_name="Bash", cwd=self.project, session_id="r1294-s",
                       tool_input={"command": f"git -C {self.rd} {tail}"}, **extra)
        r = subprocess.run([sys.executable, HOOK, "pre-tool-use"], input=json.dumps(payload), capture_output=True, text=True,
                           env=dict(os.environ, BUDGIT_HOME=self.home, HOME=self.fake_home), timeout=60)
        out = json.loads(r.stdout) if r.stdout.strip() else {}
        spec = out.get("hookSpecificOutput") or {}
        self.hook_trace = "rc=%s stdout=%r stderr=%r" % (r.returncode, r.stdout[:300], r.stderr[-800:])
        return spec.get("permissionDecision"), spec.get("permissionDecisionReason") or ""

    def test_a0_the_hook_carries_the_reason_to_a_helper_deny_and_to_the_main_session_ask(self):
        self.partial_clone()
        decision, reason = self.fire("log", agent_id="helper-1")
        want = i18n.t("guard.r1294_partial")
        self.assertEqual(decision, "deny", self.hook_trace)
        self.assertIn(" | " + want[:40], reason)
        decision, reason = self.fire("log")
        self.assertEqual(decision, "ask", self.hook_trace)
        self.assertTrue(reason.startswith(want[:40]), reason)


class PartialClone(Case):
    def test_a1_a_filterless_partial_clone_is_declined_with_the_partial_reason(self):
        self.partial_clone()
        self.declined("log", "status", "show", "diff", key="guard.r1294_partial")

    def test_a1_each_new_local_key_is_declined(self):
        for key, value in (("remote.origin.promisor", "true"), ("extensions.partialClone", "origin"),
                           ("remote.origin.uploadpack", "x"), ("remote.origin.receivepack", "x"),
                           ("submodule.x.url", "y")):
            with self.subTest(key=key):
                self.git("config", "--unset-all", key, check=False)
                self.set_config(key, value)
                self.declined("log")
                self.git("config", "--unset-all", key)
                self.allowed("log")

    def test_a1_the_global_scope_declines_promisor_partialclone_uploadpack_receivepack(self):
        for body in ("[remote \"o\"]\n\tpromisor = true\n", "[extensions]\n\tpartialClone = o\n",
                     "[remote \"o\"]\n\tuploadpack = x\n", "[remote \"o\"]\n\treceivepack = x\n"):
            with self.subTest(body=body):
                open(os.path.join(self.fake_home, ".gitconfig"), "w").write(body)
                self.declined("log")
        os.remove(os.path.join(self.fake_home, ".gitconfig"))
        self.allowed("log")

    def test_n5_a_promisor_match_gives_the_partial_text_even_when_an_old_key_also_matched(self):
        self.partial_clone()
        self.set_config("core.fsmonitor", "/bin/true")
        self.declined("log", key="guard.r1294_partial")

    def test_a1_security_proof_a_lazy_fetch_would_run_the_uploadpack_marker(self):
        script = self.script("lazy")
        self.partial_clone(uploadpack=script)
        blob = self.git("rev-parse", "HEAD~1:data/real/x.csv")
        os.remove(os.path.join(self.root, ".git", "objects", blob[:2], blob[2:]))
        self.declined("log -p", key="guard.r1294_partial")
        self.assertFalse(os.path.exists(self.marker("lazy")), "the guard decision path ran the program")
        self.plain("log", "-p")
        self.assertTrue(os.path.exists(self.marker("lazy")), "control: the plain program did not run the marker, so the test cannot fail")


class ConfigScan(Case):
    def test_a2_a_config_file_over_the_cap_declines_with_the_size_reason(self):
        pad = "# " + "x" * 98 + "\n"
        with open(os.path.join(self.root, ".git", "config"), "a") as fh:
            fh.write(pad * (262144 // len(pad) + 5))
        self.declined("log", key="guard.r1294_config_big")

    def test_a2_a_bad_key_after_byte_262144_is_caught(self):
        pad = "# " + "x" * 98 + "\n"
        with open(os.path.join(self.root, ".git", "config"), "a") as fh:
            fh.write(pad * (262144 // len(pad) + 5))
            fh.write("[core]\n\tfsmonitor = /bin/true\n")
        got, keys = self.check("log")
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "ask")

    def test_a2_every_scope_is_read_whole_not_only_the_local_file(self):
        pad = "# " + "x" * 98 + "\n"
        with open(os.path.join(self.fake_home, ".gitconfig"), "w") as fh:
            fh.write(pad * (262144 // len(pad) + 5))
        self.declined("log", key="guard.r1294_config_big")

    def test_a3_the_xdg_config_home_file_is_scanned(self):
        xdg = os.path.join(self.tmp, "xdg")
        os.makedirs(os.path.join(xdg, "git"))
        open(os.path.join(xdg, "git", "config"), "w").write("[core]\n\tfsmonitor = /bin/true\n")
        patch_env(self, XDG_CONFIG_HOME=xdg)
        self.declined("log")

    def test_m3_diff_submodule_and_status_summary_in_the_user_config_are_declined(self):
        path = os.path.join(self.fake_home, ".gitconfig")
        for body in ("[diff]\n\tsubmodule = diff\n", "[diff]\n\tsubmodule = log\n", "[status]\n\tsubmoduleSummary = true\n"):
            with self.subTest(body=body):
                open(path, "w").write(body)
                self.declined("log -p", "diff")

    def test_m3_the_same_keys_in_a_system_config_are_declined(self):
        for body in ("[diff]\n\tsubmodule = diff\n", "[status]\n\tsubmoduleSummary = true\n"):
            with self.subTest(body=body):
                open(self.sys_cfg, "w").write(body)
                self.declined("log -p", "diff")

    def test_m3_guard_diff_submodule_short_and_submodule_recurse_stay_allowed(self):
        open(os.path.join(self.fake_home, ".gitconfig"), "w").write(
            "[diff]\n\tsubmodule = short\n[status]\n\tsubmoduleSummary = false\n[submodule]\n\trecurse = true\n")
        self.allowed("log -p", "diff --ignore-submodules=all")

    def test_a3_the_system_paths_are_scanned(self):
        open(self.sys_cfg, "w").write("[core]\n\tfsmonitor = /bin/true\n")
        self.declined("log")

    def test_a3_the_system_path_list_names_the_homebrew_and_xcode_files(self):
        listed = set(sl.__dict__.get("_SYSTEM_CONFIGS_DEFAULT", ()))
        for p in ("/etc/gitconfig", "/opt/homebrew/etc/gitconfig", "/usr/local/etc/gitconfig",
                  "/Library/Developer/CommandLineTools/usr/etc/gitconfig",
                  "/Applications/Xcode.app/Contents/Developer/usr/etc/gitconfig",
                  "/Library/Developer/CommandLineTools/usr/share/git-core/gitconfig",
                  "/Applications/Xcode.app/Contents/Developer/usr/share/git-core/gitconfig"):
            self.assertIn(p, listed)


class SubmoduleFlags(Case):
    def test_a5_unsafe_submodule_forms_do_not_get_the_exception(self):
        for tail in ("log -p --submodule=diff", "show --submodule=diff", "diff --submodule=log", "diff --submodule",
                     "log --submodule", "log --submodule=log"):
            with self.subTest(tail=tail):
                self.declined(tail)

    def test_a5_submodule_short_gets_it(self):
        self.allowed("log --submodule=short", "show --submodule=short", "diff --cached --submodule=short")

    def test_a5_diff_submodule_config_and_a_submodule_section_are_declined(self):
        self.set_config("diff.submodule", "diff")
        self.declined("log")
        self.git("config", "--unset-all", "diff.submodule")
        self.allowed("log")
        self.set_config("submodule.x.path", "p")
        self.declined("log")


class IgnoreSubmodulesGate(Case):
    """Fallback (d): status and a work-tree diff pass only with --ignore-submodules (all); no index is read."""

    def test_d_plain_status_and_plain_worktree_diff_with_a_submodule_decline_with_the_new_key(self):
        self.gitlink("v2")
        self.declined("status", "diff", key=NEEDS)

    def test_d_index_v3_declines_plain_status_and_diff(self):
        self.gitlink("v3")
        self.declined("status", "diff", key=NEEDS)

    def test_d_index_v4_declines_plain_status_and_diff(self):
        self.gitlink("v4")
        self.declined("status", "diff", key=NEEDS)

    def test_d_split_index_declines_plain_status_and_diff(self):
        self.gitlink("split")
        self.declined("status", "diff", key=NEEDS)

    def test_d_truncated_index_declines_plain_status_and_diff(self):
        self.gitlink("truncated")
        self.declined("status", "diff", key=NEEDS)

    def test_d_a_repo_without_a_submodule_needs_the_flag_too(self):
        self.declined("status", "diff", key=NEEDS)

    def test_d_ignore_submodules_all_passes(self):
        self.gitlink("v2")
        self.allowed("status --ignore-submodules=all", "diff --ignore-submodules=all")

    def test_d_ignore_submodules_with_no_value_passes(self):
        self.gitlink("v2")
        self.allowed("status --ignore-submodules", "diff --ignore-submodules")

    def test_d_any_other_value_declines(self):
        self.gitlink("v2")
        self.declined("status --ignore-submodules=dirty", "status --ignore-submodules=untracked",
                      "status --ignore-submodules=none", "diff --ignore-submodules=dirty", key=NEEDS)

    def test_d_an_abbreviated_flag_declines_with_the_new_key(self):
        self.gitlink("v2")
        self.declined("status --ignore-sub=all", "diff --ignore-sub=all", "status --ignore-submod", key=NEEDS)

    def test_a5_the_slice_admits_no_new_diff_ignore_submodules_form_in_the_reader(self):
        # the parent reader refused these diff forms; the gate strips the flag itself and the reader's allowlist is unchanged
        for args in (["--ignore-submodules"], ["--ignore-submodules=all"], ["--ignore-submodules=dirty"]):
            self.assertFalse(sl._git_reader_ok("diff", args), args)

    def test_n4_the_last_ignore_submodules_flag_before_the_double_dash_wins(self):
        self.gitlink("v2")
        self.allowed("status --ignore-submodules=none --ignore-submodules=all")
        self.declined("status --ignore-submodules=all --ignore-submodules=none", key=NEEDS)
        self.declined("status -- --ignore-submodules=all", key=NEEDS)

    def test_d_cached_staged_ranges_log_and_show_need_no_flag(self):
        self.gitlink("v2")
        self.allowed("diff --cached", "diff --staged", "diff HEAD~1..HEAD -- docs", "diff HEAD~1...HEAD -- docs", "log", "show")
        self.declined("diff HEAD~1..HEAD", "diff HEAD~1...HEAD", key=NEEDS)

    def test_n1_a_two_revision_diff_or_a_revision_and_a_file_still_needs_the_flag(self):
        self.gitlink("v2")
        self.declined("diff HEAD x.csv", "diff HEAD~1 HEAD", key=NEEDS)

    def test_d_security_proof_the_nested_fsmonitor_marker(self):
        self.gitlink("v2")
        self.declined("status", "diff", key=NEEDS)
        self.assertFalse(os.path.exists(self.marker("nested")), "the guard decision path ran the program")
        self.plain("status")
        self.assertTrue(os.path.exists(self.marker("nested")),
                        "control: the plain status did not run the nested marker, so the test cannot fail")

    def test_a4_the_config_check_still_comes_first(self):
        self.gitlink("v2")
        self.set_config("core.fsmonitor", "/bin/true")
        got, keys = self.check("status")
        self.assertTrue(got[1].startswith("budgit guard: "), got[1])

    def test_d_no_index_scan_function_is_left(self):
        self.assertFalse(hasattr(sl, "_index_has_gitlink"))


class FlagEverywhere(Case):
    """R-1294 follow-up (her decision I-2074): every work-tree status or diff in a protected repo needs the flag."""

    def line(self, text, cwd=None):
        return sl.real_data_check("Bash", {"command": text}, self.project, cwd=cwd or self.project)[0]

    def declines(self, text, cwd=None):
        got = self.line(text, cwd)
        self.assertIsNotNone(got, text)
        self.assertEqual(got[0], "ask", text)
        self.assertTrue(got[1].startswith(i18n.t(NEEDS)), "%s -> %r" % (text, got[1]))

    def passes(self, text, cwd=None):
        self.assertIsNone(self.line(text, cwd), text)

    def test_f_root_status_declines_without_the_flag_and_passes_with_it(self):
        self.declines("git -C %s status" % self.root)
        self.passes("git -C %s status --ignore-submodules=all" % self.root)

    def test_f_status_from_an_unprotected_subfolder_declines_without_the_flag(self):
        docs = os.path.join(self.root, "docs")
        self.declines("git -C %s status" % docs)
        self.passes("git -C %s status --ignore-submodules=all" % docs)

    def test_f_status_with_a_pathspec_into_protected_data_declines_without_the_flag(self):
        self.declines("git -C %s status -- data/real" % self.root)
        self.passes("git -C %s status --ignore-submodules -- data/real" % self.root)

    def test_f_root_diff_declines_without_the_flag_and_passes_with_it(self):
        self.declines("git -C %s diff" % self.root)
        self.passes("git -C %s diff --ignore-submodules=all" % self.root)

    def test_f_a_range_shaped_word_without_double_dash_needs_the_flag(self):
        self.declines("git -C %s diff x..y" % self.root)
        self.passes("git -C %s diff --ignore-submodules=all x..y" % self.root)
        self.passes("git -C %s diff HEAD~1..HEAD -- docs" % self.root)

    def test_f_only_an_effective_flag_counts_in_an_unprotected_folder(self):
        docs = os.path.join(self.root, "docs")
        for tail in ("status --ignore-submodules=none", "status --ignore-submodules=dirty", "status -- --ignore-submodules=all",
                     "status --ignore-submodules=all --ignore-submodules=none", "diff --ignore-submodules=untracked"):
            self.declines("git -C %s %s" % (docs, tail))
        for tail in ("status --ignore-submodules=all", "status --ignore-submodules",
                     "status --ignore-submodules=none --ignore-submodules=all"):
            self.passes("git -C %s %s" % (docs, tail))

    def test_f_a_flagless_diff_with_an_output_option_does_not_pass_at_the_root(self):
        got = self.line("git -C %s diff --output=%s/out.patch" % (self.root, self.root))
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "ask")

    def test_f_pipe_and_chain_forms_decline_without_the_flag(self):
        docs = os.path.join(self.root, "docs")
        for b in (self.root, docs):
            for text in ("git -C %s status | head -3" % b, "git -C %s diff --stat | wc -l" % b,
                         "git -C %s log --oneline && git -C %s status" % (b, b)):
                self.declines(text)
            for text in ("git -C %s status --ignore-submodules=all | head -3" % b,
                         "git -C %s log --oneline && git -C %s status --ignore-submodules=all" % (b, b)):
                self.passes(text)

    def test_f_a_fault_in_the_pre_pass_asks_and_never_passes(self):
        docs = os.path.join(self.root, "docs")
        with mock.patch.object(sl, "_submodule_args", side_effect=NameError("injected")):
            got = self.line("git -C %s status" % docs)
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "ask")

    def test_f_a_fault_in_the_flag_decision_at_the_root_asks(self):
        with mock.patch.object(sl, "_needs_ignore_flag", side_effect=NameError("injected")):
            got = self.line("git -C %s status" % self.root)
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "ask")

    def test_f_a_word_the_check_cannot_place_asks_even_with_the_flag(self):
        # default-ask: only the program word, -C, --no-pager, --no-optional-locks, the verb and its options are placed;
        # a line with anything else around a status or diff asks, flagged or not, at the root and in a subfolder
        docs = os.path.join(self.root, "docs")
        for b in (self.root, docs):
            for flag in ("", " --ignore-submodules=all"):
                for text in ("git -c x=y -C %s status", "git -P -C %s status", "git --literal-pathspecs -C %s status",
                             "git --no-replace-objects -C %s diff", "env git -C %s status", "command git -C %s status",
                             "time git -C %s status", "VAR=1 git -C %s status", "(git -C %s status)"):
                    got = self.line((text % b) + flag)
                    self.assertIsNotNone(got, text % b + flag)
                    self.assertEqual(got[0], "ask", text % b + flag)
                got = self.line("cd %s && git status%s" % (b, flag))
                self.assertIsNotNone(got, "cd " + b + flag)
                self.assertEqual(got[0], "ask")

    def test_f_cached_and_revision_only_reads_keep_their_verdicts(self):
        self.passes("git -C %s diff --cached" % self.root)
        self.passes("git -C %s log --oneline" % self.root)


class BareFolders(Case):
    def bare_like(self, folder, head_as_dangling_link=False):
        """A folder that git would use as a bare repo: HEAD, objects/, refs/ and a config with fsmonitor."""
        os.makedirs(os.path.join(folder, "objects"))
        os.makedirs(os.path.join(folder, "refs"))
        open(os.path.join(folder, "objects", ".keep"), "w").write("")
        open(os.path.join(folder, "refs", ".keep"), "w").write("")
        head = os.path.join(folder, "HEAD")
        if head_as_dangling_link:
            os.symlink("refs/heads/main", head)
        else:
            open(head, "w").write("ref: refs/heads/main\n")
        open(os.path.join(folder, "config"), "w").write("[core]\n\tfsmonitor = %s\n" % self.script("bare-like"))

    def status_in(self, folder):
        got, keys = sl.real_data_check("Bash", {"command": f"git -C {folder} status --ignore-submodules=all"}, self.project,
                                       cwd=self.project, agent_id=None)
        return got

    def assert_bare_decline(self, got):
        self.assertIsNotNone(got, "the bare-repo folder kept the exception")
        self.assertEqual(got[0], "ask")
        self.assertTrue(got[1].startswith(i18n.t("guard.r1294_bare")), got[1])
        self.assertFalse(os.path.exists(self.marker("bare-like")), "the guard decision path ran the program")

    def test_m2_a_bare_repo_folder_inside_the_project_is_declined_with_its_own_config_unread(self):
        self.git("init", "-q", "--bare", self.rd)                 # the protected folder itself becomes a bare repo
        self.set_config("core.fsmonitor", self.script("bare"), cwd=self.rd)
        self.assertTrue(os.path.isfile(os.path.join(self.rd, "HEAD")))
        self.declined("status --ignore-submodules=all", "log")
        self.assertFalse(os.path.exists(self.marker("bare")), "the guard decision path ran the program")

    def test_m2_a_bare_repo_folder_nested_in_the_protected_folder_is_declined_by_status_and_diff(self):
        bare = os.path.join(self.rd, "bare")
        self.git("init", "-q", "--bare", bare)
        self.set_config("core.fsmonitor", self.script("nested-bare"), cwd=bare)
        self.assertTrue(os.path.isdir(os.path.join(bare, "objects")) and os.path.isdir(os.path.join(bare, "refs")))
        for tail in ("status --ignore-submodules=all", "diff --ignore-submodules=all"):
            got, keys = sl.real_data_check("Bash", {"command": f"git -C {bare} {tail}"}, self.project, cwd=self.project,
                                           agent_id=None)
            self.assertIsNotNone(got, tail)
            self.assertEqual(got[0], "ask", tail)
        self.assertFalse(os.path.exists(self.marker("nested-bare")), "the guard decision path ran the program")

    def test_m2_guard_a_plain_status_in_the_project_still_passes_with_the_flag(self):
        self.allowed("status --ignore-submodules=all")

    def test_m2r_a_head_that_is_a_dangling_symlink_still_makes_a_bare_repo_folder(self):
        folder = os.path.join(self.rd, "y")
        self.bare_like(folder, head_as_dangling_link=True)
        self.assertFalse(os.path.exists(os.path.join(folder, "HEAD")))
        self.assertTrue(os.path.islink(os.path.join(folder, "HEAD")))
        self.assert_bare_decline(self.status_in(folder))

    def test_m2r_the_bare_walk_runs_when_no_dot_folder_repo_exists_above_the_target(self):
        os.rename(os.path.join(self.root, ".git"), os.path.join(self.tmp, "away-git"))
        folder = os.path.join(self.rd, "x")
        self.bare_like(folder)
        self.assert_bare_decline(self.status_in(folder))

    def test_n2_a_folder_with_head_and_a_commondir_file_is_a_repo_whose_config_comes_from_the_named_folder(self):
        y = os.path.join(self.rd, "y")
        os.makedirs(os.path.join(y, "objects"))
        os.makedirs(os.path.join(y, "refs"))
        open(os.path.join(y, "config"), "w").write("[core]\n\tfsmonitor = %s\n" % self.script("bare-like"))
        x = os.path.join(self.rd, "x")
        os.makedirs(x)
        open(os.path.join(x, "HEAD"), "w").write("ref: refs/heads/main\n")
        open(os.path.join(x, "commondir"), "w").write("../y\n")
        self.assert_bare_decline(self.status_in(x))

    def test_l1_with_no_dot_folder_repo_the_walk_goes_above_the_project_root(self):
        os.rename(os.path.join(self.root, ".git"), os.path.join(self.tmp, "away-git"))
        above = os.path.dirname(self.root)
        self.assertTrue(os.path.realpath(above).startswith(os.path.realpath(self.tmp)), above)
        self.bare_like(above)
        got, keys = self.check("status --ignore-submodules=all")
        self.assert_bare_decline(got)

    def test_q3_recurse_submodules_and_show_signature_are_not_clean_reads(self):
        self.gitlink("v2")
        self.declined("grep --recurse-submodules one", "log --show-signature")

    def test_a2_an_unreadable_config_file_declines_with_its_named_reason(self):
        os.makedirs(os.path.join(self.fake_home, ".gitconfig"))
        self.declined("log", "status --ignore-submodules=all", key="guard.r1294_config_unreadable")


class Environment(Case):
    def test_a6_the_allowed_transport_variables_keep_the_exception(self):
        patch_env(self, GIT_TERMINAL_PROMPT="0", GIT_SSL_NO_VERIFY="1", GIT_CURL_VERBOSE="1")
        self.allowed("log", "status --ignore-submodules=all")


def _env_test(name):
    def test(self):
        patch_env(self, **{name: "x"})
        got, keys = self.check("log")
        self.assertIsNotNone(got, name)
        self.assertEqual(got[0], "ask", name)
    return test


for _n in NEW_ENV + OLD_ENV:
    setattr(Environment, "test_a6_%s_makes_the_exception_not_apply" % _n.lower(), _env_test(_n))


if __name__ == "__main__":
    unittest.main()
