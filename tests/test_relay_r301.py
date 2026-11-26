"""R-301: a chat on an old Budgit renews itself on the user's next typed message (option 3, mixto): it answers first,
then hands off to a successor that echoes the handoff; the old chat is archived only after the echo matched. The line
under the prompt says `/reload-plugins` reloads in place. Plan: docs/plans/2026-10-26-r301-renew-chats-build.md.
Written before the build (test-first). Fake names and paths only."""
import json
import os
import subprocess
import sys
import unittest

from test_portfolio import PortfolioCase
from test_livework import CLI
import studiolib as sl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
with open(os.path.join(ROOT, "plugin", ".claude-plugin", "plugin.json"), encoding="utf-8") as _f:
    MANIFEST = json.load(_f)
OLD, NEW = "s-oldchat1", "s-newchat1"
HANDOFF = ("Estaba con R-12 e I-220 en la rama claude/huerto-riego, último commit abc1234f. "
           "Siguiente paso: tests en /tmp/huerto/tests.")
FACTS = {"R-12", "I-220", "claude/huerto-riego", "abc1234f", "/tmp/huerto/tests"}


class RelayCase(PortfolioCase):
    def setUp(self):
        super().setUp()
        self.root, self.hub = self.setup_three()
        self.view()
        self.installed(MANIFEST["version"])

    def installed(self, version):
        """Claude Code's registry says `version` is installed; higher than the manifest makes this checkout stale."""
        path = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "plugins", "installed_plugins.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"version": 2, "plugins": {"%s@budgit-hub" % MANIFEST["name"]: [
                {"scope": "user", "version": version, "installPath": "/tmp/fake-cache/%s" % version}]}}, f)

    def stale(self):
        self.installed("99.0.0")

    def hook(self, event, session, **payload):
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(dict(session_id=session, **payload)),
                           env=dict(os.environ), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def relay(self, session, *args, stdin=None):
        env = dict(os.environ, CLAUDE_CODE_SESSION_ID=session)
        return subprocess.run([sys.executable, CLI, "relay", *args], env=env, input=stdin, capture_output=True,
                              text=True)

    def handoff(self, text=HANDOFF, session=OLD, *extra, app_id=True):
        path = os.path.join(self.uhome, "handoff-%s.md" % session)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        if app_id and "--app-id" not in extra:
            extra += ("--app-id", "local_" + session[2:])
        return self.relay(session, "handoff", "--file", path, *extra)

    def echo(self, text, session=NEW, old=OLD):
        path = os.path.join(self.uhome, "echo-%s.md" % session)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return self.relay(session, "echo", old, "--file", path)

    def record(self, old=OLD):
        path = os.path.join(sl.home_state(), "relay", "%s.json" % old)
        with open(path, encoding="utf-8") as f:
            return json.load(f)


class StatusLineTest(RelayCase):
    """T1: the stale notice names the in-place reload and keeps the line within 80 columns."""
    def line(self, *args, lang="es"):
        env = dict(os.environ, BUDGIT_LANG=lang)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        r = subprocess.run([sys.executable, CLI, "status", "--line", "--project", self.hub, *args], env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.rstrip("\n")

    def test_the_line_never_nags_to_type_reload_plugins(self):                 # R-467
        for lang in ("es", "en"):
            got = self.line("--loaded", "0.0.1", lang=lang)
            self.assertNotIn("reload-plugins", got)
            self.assertNotIn("↻", got)
        self.assertNotIn("reabre", self.line("--loaded", "0.0.1"))

    def test_the_long_line_stays_within_80_columns_without_the_nag(self):
        long = self.mk(os.path.join(self.root, "un-proyecto-con-un-nombre-larguisimo-de-verdad"), [("R-1", "doing")])
        env = dict(os.environ, BUDGIT_LANG="es")
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        out = subprocess.run([sys.executable, CLI, "status", "--line", "--project", long, "--loaded", "0.0.1"],
                             env=env, capture_output=True, text=True).stdout.rstrip("\n")
        self.assertLessEqual(len(out), 80, out)
        self.assertNotIn("/reload-plugins", out)
        self.assertIn("en curso", out)                                           # the rest of the line is unchanged


class PointerVersionTest(RelayCase):
    """T2: each chat's pointer says which Budgit it runs."""
    def test_a_prompt_records_the_running_version(self):
        self.pointer(OLD, self.hub)
        self.hook("user-prompt-submit", OLD, prompt="hola")
        self.assertEqual(sl.read_pointer(OLD).get("plugin_version"), MANIFEST["version"])


class PortfolioStaleChatsTest(RelayCase):
    """T3: the TM's view counts the chats on an old Budgit; a private project's chat is counted, never named."""
    def test_stale_chats_are_counted_and_named_except_private(self):
        cofre = self.mk(os.path.join(self.root, "cofre"), [("R-1", "open")])
        with open(os.path.join(self.bhome, "config.json"), "w") as f:
            json.dump({"hub": self.hub, "projects_root": self.root, "protected_dirs": [cofre]}, f)
        self.session(OLD, self.hub, status="idle", name="PM · hubby")
        self.session("s-oldpriv1", cofre, status="idle", name="PM · cofre")
        self.session(NEW, self.hub, status="idle", name="lane nueva")
        self.pointer(OLD, self.hub)
        self.pointer("s-oldpriv1", cofre)
        self.pointer(NEW, self.hub)
        for sid, version in ((OLD, "0.0.1"), ("s-oldpriv1", "0.0.1"), (NEW, MANIFEST["version"])):
            rec = sl.read_pointer(sid)
            rec["plugin_version"] = version
            sl.atomic_write(sl.pointer_path(sid), json.dumps(rec) + "\n")
        foot = self.view()["footer"]
        self.assertEqual(foot["stale_chats"], 2)
        self.assertEqual(foot["stale_titles"], ["PM · hubby"])
        text = self.ok()
        self.assertIn("2 chats con Budgit viejo", text)
        self.assertNotIn("cofre", text)
        self.assertTrue(all(len(l) <= 80 for l in text.splitlines()), text)

    def test_more_counts_chats_not_entries(self):
        import portfolio
        import i18n
        t = lambda key, **kw: i18n.t(key, "es", **kw)
        line = portfolio._found_line(["PM · hubby", "x" * 70, "c", "d"], t, "portfolio.foot.stale_many", hidden=2, n=6)
        self.assertEqual(line, "6 chats con Budgit viejo: PM · hubby, 5 más.")
        line = portfolio._found_line(["a", "b"], t, "portfolio.foot.stale_many", hidden=2, n=4)
        self.assertEqual(line, "4 chats con Budgit viejo: a, b, 2 privados.")
        line = portfolio._found_line([], t, "portfolio.foot.stale_many", hidden=2, n=2)
        self.assertEqual(line, "2 chats con Budgit viejo: 2 privados.")


class RelayRecordTest(RelayCase):
    """T4: handoff -> echo (every fact restated, successor on the installed version) -> done; or failed."""
    def test_handoff_is_refused_when_the_chat_is_not_stale(self):
        r = self.handoff()
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(sl.home_state(), "relay", "%s.json" % OLD)))

    def test_handoff_keeps_the_text_and_the_facts(self):
        self.stale()
        r = self.handoff(HANDOFF, OLD, "--title", "PM · hubby", "--app-id", "local_old0001")
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = self.record()
        self.assertEqual((rec["state"], rec["title"], rec["installed"]), ("handoff", "PM · hubby", "99.0.0"))
        self.assertEqual(set(rec["facts"]), FACTS)

    def test_an_echo_missing_a_fact_is_refused_and_names_it(self):
        self.stale()
        self.handoff()
        self.installed(MANIFEST["version"])                 # the successor runs what is installed
        r = self.echo("Sigo con R-12 en claude/huerto-riego desde abc1234f; tests en /tmp/huerto/tests.")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("I-220", r.stdout + r.stderr)
        self.assertEqual(self.record()["state"], "handoff")

    def test_an_echo_from_a_chat_still_on_the_old_version_is_refused(self):
        self.stale()
        self.handoff()
        r = self.echo(HANDOFF)                               # still stale: 99.0.0 installed, this checkout older
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.record()["state"], "handoff")

    def test_a_matching_echo_then_done(self):
        self.stale()
        self.handoff()
        self.assertNotEqual(self.relay(NEW, "done", OLD).returncode, 0)       # not before the echo
        self.installed(MANIFEST["version"])
        r = self.echo("Retomo: " + HANDOFF)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.record()["state"], self.record()["new"]), ("echoed", NEW))
        self.assertEqual(self.relay(NEW, "done", OLD).returncode, 0)
        self.assertEqual(self.record()["state"], "done")

    def test_each_fact_must_be_restated_exactly(self):
        self.stale()
        self.handoff("Con R-1 e I-2 en claude/foo, commit abc1234f, ficheros en /tmp/a.")
        self.installed(MANIFEST["version"])
        r = self.echo("Con R-12 e I-23 en claude/foo-bar, commit abc1234f0, ficheros en /tmp/ab.")
        self.assertNotEqual(r.returncode, 0)
        for fact in ("R-1", "I-2", "claude/foo", "abc1234f", "/tmp/a"):
            self.assertIn(fact, r.stderr)
        self.assertEqual(self.record()["state"], "handoff")
        r = self.echo("Sigo: R-1, I-2 (claude/foo) desde abc1234f; ficheros en /tmp/a.")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_path_is_said_only_with_a_boundary_on_both_sides(self):
        import relay
        for near in ("C:/tmp/a", "x/tmp/a", "/tmp/ab", "/tmp/a/b", "~/tmp/a", "./tmp/a"):
            self.assertFalse(relay.said("/tmp/a", "en %s hoy" % near), near)
        for ok in ("en /tmp/a.", "(/tmp/a)", "`/tmp/a`", "/tmp/a, y", "- /tmp/a"):
            self.assertTrue(relay.said("/tmp/a", ok), ok)

    def test_handoff_needs_the_app_id_the_archive_guard_matches(self):
        self.stale()
        r = self.handoff(app_id=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--app-id", r.stderr)
        self.assertNotEqual(self.handoff(HANDOFF, OLD, "--app-id", "s-oldchat1").returncode, 0)

    def test_fail_keeps_the_old_chat(self):
        self.stale()
        self.handoff()
        r = self.relay(OLD, "fail", OLD, "--reason", "la app no dejó abrir el chat nuevo")
        self.assertEqual(r.returncode, 0, r.stderr)
        rec = self.record()
        self.assertEqual((rec["state"], rec["reason"]), ("failed", "la app no dejó abrir el chat nuevo"))


class ArchiveGuardTest(RelayCase):
    """T5: the old chat is archived only after the echo matched."""
    def archive(self, caller, target):
        return self.hook("pre-tool-use", caller, tool_name="mcp__ccd_session_mgmt__archive_session",
                         tool_input={"session_id": target})

    def denied(self, out):
        return '"deny"' in out

    def test_archive_is_denied_until_the_echo_matched(self):
        self.stale()
        self.handoff(HANDOFF, OLD, "--app-id", "local_old0001")
        self.assertTrue(self.denied(self.archive(NEW, "local_old0001")))
        self.assertTrue(self.denied(self.archive(OLD, "self")))
        self.installed(MANIFEST["version"])
        self.echo(HANDOFF)
        self.assertFalse(self.denied(self.archive(NEW, "local_old0001")))

    def test_a_failed_relay_never_allows_the_archive(self):
        self.stale()
        self.handoff(HANDOFF, OLD, "--app-id", "local_old0001")
        self.relay(OLD, "fail", OLD, "--reason", "x")
        self.assertTrue(self.denied(self.archive(NEW, "local_old0001")))

    def test_a_corrupt_record_never_opens_the_archive(self):
        self.stale()
        self.handoff(HANDOFF, OLD, "--app-id", "local_old0001")
        with open(os.path.join(sl.home_state(), "relay", "%s.json" % OLD), "w") as f:
            f.write("{not json")
        out = self.archive(NEW, "local_old0001")
        self.assertTrue(self.denied(out))
        self.assertTrue(self.denied(self.archive(OLD, "self")))
        self.assertIn("%s.json" % OLD, out)
        self.assertIn("budgit relay reset %s" % OLD, out)
        self.assertIn("unreadable", self.relay(NEW, "status").stdout)
        r = self.relay(NEW, "reset", OLD)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.denied(self.archive(NEW, "local_old0001")))

    def test_reset_never_clears_a_readable_record(self):
        self.stale()
        self.handoff(HANDOFF, OLD, "--app-id", "local_old0001")
        self.assertNotEqual(self.relay(NEW, "reset", OLD).returncode, 0)
        self.assertTrue(self.denied(self.archive(NEW, "local_old0001")))

    def test_sessions_without_a_relay_pass_untouched(self):
        self.assertEqual(self.archive(NEW, "local_other01").strip(), "")


class PromptContextTest(RelayCase):
    """T6: only a prompt the user typed, in a stale chat, gets the block, once per installed version."""
    def say(self, text, session=OLD):
        self.pointer(session, self.hub)
        return self.hook("user-prompt-submit", session, prompt=text)

    def test_a_typed_prompt_in_a_stale_chat_gets_the_block_once(self):
        self.stale()
        out = self.say("¿cómo va?")
        for part in ("Answer the user's message first", "budgit relay handoff", "Este chat se ha renovado",
                     "No he podido renovar este chat"):
            self.assertIn(part, out)
        self.assertNotIn("budgit relay handoff", self.say("¿y ahora?"))

    def test_harness_text_never_gets_the_block(self):
        self.stale()
        for text in ("<task-notification>x</task-notification>", '<cross-session-message from="x">y'
                     "</cross-session-message>", "   "):
            self.assertNotIn("budgit relay handoff", self.say(text))

    def test_a_chat_on_the_installed_version_gets_nothing(self):
        self.assertNotIn("budgit relay handoff", self.say("hola"))

    def test_only_the_chat_typed_to_is_touched(self):
        self.stale()
        self.pointer("s-otherch1", self.hub)
        self.say("hola")
        relay_dir = os.path.join(sl.home_state(), "relay")
        names = os.listdir(relay_dir) if os.path.isdir(relay_dir) else []
        self.assertFalse(any("s-otherch1" in n for n in names))
        self.assertIsNone(sl.read_pointer("s-otherch1").get("relay"))


if __name__ == "__main__":
    unittest.main()
