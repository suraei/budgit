"""R-241: the line under the prompt says when this chat runs an older Budgit than the disk holds (D1) or a newer release
is already in the marketplace clone (D2); `budgit update --check` asks the public repo once (D3); `budgit update` only
prints the commands. Nothing here ever runs `claude plugin`."""
import json
import os
import stat
import subprocess
import sys
import unittest

from helpers import StudioTestCase, ROOT

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
MANIFEST = os.path.join(ROOT, "plugin", ".claude-plugin", "plugin.json")


def manifest():
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


class UpdateNoticeCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.config = os.environ["CLAUDE_CONFIG_DIR"]          # the base class points Claude Code's folder at an empty temp dir
        os.makedirs(os.path.join(self.config, "plugins"))
        self.name = manifest()["name"]
        self.version = manifest()["version"]

    def cli(self, *args, lang="es", extra_env=None, path=None):
        env = dict(os.environ, BUDGIT_HOME=self.home, BUDGIT_LANG=lang, **(extra_env or {}))
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        if path:
            env["PATH"] = path + os.pathsep + env.get("PATH", "")
        return subprocess.run([sys.executable, CLI] + list(args), cwd=self.tmp, env=env, capture_output=True, text=True)

    def write_json(self, rel, data):
        path = os.path.join(self.config, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return path

    def install(self, marketplace, installed, clone, source="github"):
        """A plugin installed at `installed` from `marketplace`, whose clone on disk carries `clone`."""
        loc = os.path.join(self.config, "plugins", "marketplaces", marketplace)
        self.write_json("plugins/installed_plugins.json", {"version": 2, "plugins": {
            "%s@%s" % (self.name, marketplace): [{"scope": "user", "version": installed, "installPath": loc}]}})
        self.write_json("plugins/known_marketplaces.json", {marketplace: {"source": {"source": source}, "installLocation": loc}})
        os.makedirs(os.path.join(loc, ".claude-plugin"), exist_ok=True)
        with open(os.path.join(loc, ".claude-plugin", "marketplace.json"), "w", encoding="utf-8") as f:
            json.dump({"name": marketplace, "plugins": [{"name": self.name, "source": "./plugin"}]}, f)
        os.makedirs(os.path.join(loc, "plugin", ".claude-plugin"), exist_ok=True)
        with open(os.path.join(loc, "plugin", ".claude-plugin", "plugin.json"), "w", encoding="utf-8") as f:
            json.dump({"name": self.name, "version": clone}, f)


class StaleChatTest(UpdateNoticeCase):
    def line(self, *args, **kw):
        r = self.cli("status", "--line", "--project", self.project, *args, **kw)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.rstrip("\n")

    def test_a_chat_older_than_the_disk_gets_no_nag_on_the_line(self):      # R-467: the mod reloads it by itself
        for lang in ("es", "en"):
            got = self.line("--loaded", "0.0.1", lang=lang)
            self.assertNotIn("reload-plugins", got)
            self.assertNotIn("↻", got)
        self.assertEqual(self.line("--loaded", "0.0.1"), self.line())

    def test_a_chat_on_the_disk_version_or_without_the_flag_gets_no_suffix(self):
        for args in (("--loaded", self.version), ()):
            self.assertNotIn("↻", self.line(*args))

    def test_garbage_in_loaded_is_ignored(self):
        self.assertNotIn("↻", self.line("--loaded", "x" * 500 + "\x1b[31m"))
        self.assertNotIn("\x1b", self.line("--loaded", "\x1b[31m9.9.9"))

    def test_the_suffix_is_also_on_the_account_only_line(self):
        self.assertIn("sin libreta aquí", self.line("--loaded", "0.0.1"))
        self.assertNotIn("reload-plugins", self.line("--loaded", "0.0.1"))

    def test_brote_json_carries_the_update_field_only_when_there_is_one(self):
        self.init_project()
        raw = self.cli("status", "--brote", "--project", self.project, "--loaded", "0.0.1").stdout
        self.assertEqual(json.loads(raw)["update"]["kind"], "stale-chat")
        self.assertEqual(json.loads(raw)["update"]["running"], "0.0.1")
        raw = self.cli("status", "--brote", "--project", self.project).stdout
        self.assertNotIn("update", json.loads(raw))


class StateForTheModTest(UpdateNoticeCase):
    """R-353: `budgit update --state --loaded V` is the mod's one question before it reloads the plugin by itself: one JSON line, {}
    unless this chat is behind what is installed, and then the words to show in the user's language. It runs nothing."""
    def state(self, loaded, **kw):
        r = self.cli("update", "--state", "--loaded", loaded, **kw)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(r.stdout.strip().splitlines()), 1)
        return json.loads(r.stdout)

    def test_a_chat_behind_the_disk_gets_the_words_in_its_language(self):
        es, en = self.state("0.0.1"), self.state("0.0.1", lang="en")
        for got in (es, en):
            self.assertEqual((got["kind"], got["running"], got["available"]), ("stale-chat", "0.0.1", self.version))
        self.assertEqual(es["said"], "Budgit actualizado a " + self.version)
        self.assertEqual(en["said"], "Budgit updated to " + self.version)
        self.assertEqual((es["button"], en["button"]), ("Recargar", "Reload"))
        self.assertIn("0.0.1", es["notice"])
        self.assertIn(self.version, en["notice"])

    def test_a_chat_on_the_disk_version_or_with_a_bad_flag_gets_an_empty_object(self):
        self.assertEqual(self.state(self.version), {})
        self.assertEqual(self.state("x" * 300 + "\x1b[31m"), {})
        self.assertEqual(self.state(""), {})

    def test_a_newer_release_only_in_the_clone_carries_no_words_to_reload_with(self):     # reloading cannot fetch it: that is the update, and the user's
        self.install("budgit-hub", self.version, "99.0.0")
        got = self.state(self.version)
        self.assertEqual(got["kind"], "available")
        self.assertNotIn("said", got)

    def test_a_copy_installed_since_the_chat_loaded_counts_as_newer(self):      # a cache install keeps each version in its own folder
        self.install("budgit-hub", "9.9.9", "9.9.9")
        got = self.state(self.version)
        self.assertEqual((got["kind"], got["running"], got["available"]), ("stale-chat", self.version, "9.9.9"))
        self.assertEqual(self.state("9.9.9"), {})                             # a chat ahead of the disk has nothing to reload for
        self.install("budgit-hub", "0.0.2", "0.0.2")
        self.assertEqual(self.state(self.version), {})                        # an older registry entry is no reason to reload

    def test_it_prints_nothing_else_and_runs_nothing(self):
        r = self.cli("update", "--state", "--loaded", "0.0.1")
        self.assertEqual(r.stderr, "")
        self.assertTrue(r.stdout.startswith("{") and r.stdout.rstrip().endswith("}"))


class AvailableTest(UpdateNoticeCase):
    def line(self):
        return self.cli("status", "--line", "--project", self.project).stdout.rstrip("\n")

    def test_a_newer_release_in_the_clone_shows_its_version(self):
        self.install("budgit-hub", "0.1.0", "99.0.0")
        self.assertTrue(self.line().endswith(" ↑99.0.0"), self.line())

    def test_same_or_older_in_the_clone_shows_nothing(self):
        self.install("budgit-hub", "1.0.0", "1.0.0")
        self.assertNotIn("↑", self.line())
        self.install("budgit-hub", "1.0.0", "0.9.0")
        self.assertNotIn("↑", self.line())

    def test_a_directory_marketplace_is_never_an_update_source(self):       # it loads the folder in place; its registry version means nothing
        self.install("studio-hub", "0.1.0", "99.0.0", source="directory")
        self.assertNotIn("↑", self.line())

    def test_missing_or_broken_registry_files_never_break_the_line(self):
        self.assertTrue(self.line().startswith("budgit "))
        self.write_json("plugins/installed_plugins.json", ["not", "a", "dict"])
        self.assertTrue(self.line().startswith("budgit "))
        with open(os.path.join(self.config, "plugins", "known_marketplaces.json"), "w") as f:
            f.write("{broken")
        self.assertTrue(self.line().startswith("budgit "))

    def test_stale_wins_over_available(self):
        self.install("budgit-hub", "0.1.0", "99.0.0")
        r = self.cli("status", "--line", "--project", self.project, "--loaded", "0.0.1")
        self.assertNotIn("reload-plugins", r.stdout)                          # R-467: a stale chat gets no nag and no "newer release" tail either
        self.assertNotIn("↑", r.stdout)

    def test_brote_json_names_the_available_version(self):
        self.init_project()
        self.install("budgit-hub", "0.1.0", "99.0.0")
        upd = json.loads(self.cli("status", "--brote", "--project", self.project).stdout)["update"]
        self.assertEqual((upd["kind"], upd["running"], upd["available"]), ("available", "0.1.0", "99.0.0"))
        self.assertEqual((upd["plugin"], upd["market"]), (self.name, "budgit-hub"))        # the floating Brote builds its two commands from these

    def test_it_adds_little_time(self):                                      # [perf] under 5 ms of work, measured in-process
        import time
        sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
        import updatelib
        self.install("budgit-hub", "0.1.0", "99.0.0")
        start = time.process_time()                                      # CPU time, not wall: a loaded machine cannot fail it (R-1282)
        for _ in range(20):
            updatelib.update_state(loaded=self.version, config_dir=self.config)
        self.assertLess((time.process_time() - start) / 20, 0.005)


class UpdateCommandTest(UpdateNoticeCase):
    def fake_claude(self):
        """A `claude` on PATH that records any call: nothing in Budgit may run it."""
        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(bin_dir)
        self.called = os.path.join(self.tmp, "claude-was-called")
        script = os.path.join(bin_dir, "claude")
        with open(script, "w") as f:
            f.write("#!/bin/sh\ntouch '%s'\n" % self.called)
        os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR)
        return bin_dir

    def test_update_only_prints_the_two_commands(self):
        r = self.cli("update", path=self.fake_claude())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("claude plugin marketplace update budgit-hub", r.stdout)
        self.assertIn("claude plugin update %s@budgit-hub" % self.name, r.stdout)
        self.assertFalse(os.path.exists(self.called))

    def test_after_a_successful_update_the_command_prints_the_next_chat_line_once(self):
        self.install("budgit-hub", "9.9.9", "9.9.9")                       # the registry now holds a version above the one this chat runs
        for lang, want in (("es", "Budgit actualizado a 9.9.9; tu próximo chat ya lo usa"),
                           ("en", "Budgit updated to 9.9.9; your next chat uses it")):
            r = self.cli("update", lang=lang)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.count(want), 1, r.stdout)
            self.assertEqual(len(r.stdout.strip().splitlines()), 1, r.stdout)

    def test_the_next_chat_line_is_absent_when_nothing_was_updated(self):
        for installed in (self.version, "0.0.2"):
            self.install("budgit-hub", installed, installed)
            for lang in ("es", "en"):
                out = self.cli("update", lang=lang).stdout
                self.assertNotIn("tu próximo chat", out)
                self.assertNotIn("your next chat", out)
                self.assertIn("claude plugin update", out)
        self.assertNotIn("your next chat", self.cli("update", lang="en").stdout)

    def test_update_names_the_marketplace_the_plugin_came_from(self):
        self.install("my-hub", "0.1.0", "0.2.0")
        r = self.cli("update")
        self.assertIn("claude plugin marketplace update my-hub", r.stdout)
        self.assertIn("claude plugin update %s@my-hub" % self.name, r.stdout)

    def test_a_hostile_registry_cannot_inject_into_the_printed_commands(self):
        self.install("bad hub; touch x", "0.1.0", "0.2.0")
        out = self.cli("update").stdout
        self.assertNotIn("touch x", out)
        self.assertIn("claude plugin marketplace update budgit-hub", out)      # falls back to the default

    def test_check_reads_the_public_version_from_the_given_file_url(self):
        remote = os.path.join(self.tmp, "plugin.json")
        with open(remote, "w") as f:
            json.dump({"name": "budgit", "version": "99.0.0"}, f)
        r = self.cli("update", "--check", extra_env={"BUDGIT_UPDATE_URL": "file://" + remote}, path=self.fake_claude())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("99.0.0", r.stdout)
        self.assertIn("nueva", r.stdout)
        self.assertFalse(os.path.exists(self.called))
        with open(remote, "w") as f:
            json.dump({"name": "budgit", "version": self.version}, f)
        self.assertIn("al día", self.cli("update", "--check", extra_env={"BUDGIT_UPDATE_URL": "file://" + remote}).stdout)

    def test_check_with_no_answer_says_so_without_a_traceback(self):
        r = self.cli("update", "--check", extra_env={"BUDGIT_UPDATE_URL": "file://" + os.path.join(self.tmp, "nope.json")})
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr + r.stdout)
        self.assertTrue(r.stdout or r.stderr)

    def test_the_url_override_only_takes_file_urls(self):
        sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
        import updatelib
        root = os.path.join(self.tmp, "exported")
        os.makedirs(os.path.join(root, ".claude-plugin"))
        with open(os.path.join(root, ".claude-plugin", "plugin.json"), "w") as f:
            json.dump({"name": "x", "version": "1.0.0", "homepage": "https://github.com/owner/name"}, f)
        raw = "https://raw.githubusercontent.com/owner/name/main/plugin/.claude-plugin/plugin.json"
        self.assertEqual(updatelib.update_url({"BUDGIT_UPDATE_URL": "http://127.0.0.1:9/x"}, root=root), raw)
        self.assertEqual(updatelib.update_url({"BUDGIT_UPDATE_URL": "file:///tmp/x.json"}, root=root), "file:///tmp/x.json")
        self.assertEqual(updatelib.update_url({}, root=root), raw)

    def test_a_checkout_that_was_never_exported_names_no_public_repo(self):    # the hub's manifest has no homepage; the source holds no account
        sys.path.insert(0, os.path.join(ROOT, "plugin", "scripts"))
        import updatelib
        root = os.path.join(self.tmp, "hub")
        os.makedirs(os.path.join(root, ".claude-plugin"))
        with open(os.path.join(root, ".claude-plugin", "plugin.json"), "w") as f:
            json.dump({"name": "x", "version": "1.0.0", "homepage": "https://evil.example/owner/name"}, f)
        self.assertIsNone(updatelib.update_url({}, root=root))
        r = self.cli("update", "--check", extra_env={"BUDGIT_UPDATE_URL": "http://127.0.0.1:9/x"})
        self.assertNotIn("Traceback", r.stderr)

    def test_a_live_folder_copy_is_current_by_itself_never_offline(self):     # R-326: this checkout has no homepage
        for lang, want in (("es", "carpeta viva"), ("en", "live folder")):
            r = self.cli("update", "--check", lang=lang)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = (r.stdout + r.stderr).lower()
            self.assertIn(want, out)
            for word in ("offline", "conexión", "could not reach", "no se pudo"):
                self.assertNotIn(word, out)

    def test_an_exported_copy_that_gets_no_answer_still_says_so(self):
        for lang, want in (("es", "sin conexión"), ("en", "offline")):
            r = self.cli("update", "--check", lang=lang,
                         extra_env={"BUDGIT_UPDATE_URL": "file://" + os.path.join(self.tmp, "nope.json")})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn(want, r.stderr)
            self.assertNotIn("carpeta viva", r.stderr + r.stdout)


if __name__ == "__main__":
    unittest.main()
