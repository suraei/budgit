"""R-1119 (I-1666): when a guard refuses a subagent's READ-ONLY call, the refusal names the allowed route (the Read tool with an
absolute path, no cd) and never tells the subagent to give up. Round 2: the read text is for an in-root ask of the real-data
guard alone; a write, a network program, an out-of-root read, any other guard, or a classifier error keeps the old text."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock

from test_guard_trust import GuardCase, HOOK
import hook
import i18n
import realdata
import studiolib as sl

PY = os.path.realpath(sys.executable)
OLD = "do not reword or retry it"
INCIDENT = "cd .. && cat ../facts.md; wc -l progress.md"


class ReadRouteTest(GuardCase):
    def setUp(self):
        super().setUp()
        for rel in ("facts.md", "progress.md"):
            with open(os.path.join(self.root, rel), "w") as fh:
                fh.write("one\ntwo\n")
        self.sub = os.path.join(self.root, "docs")
        sl.write_pointer("s-r1119", self.root)        # the hook finds the project through the session's pointer

    def run_hook(self, tool, tool_input, cwd, agent=True, lang=None):
        env = dict(os.environ, HOME=os.path.join(self.tmp, "fake-user-home"), STUDIO_HOME=self.home, PWD=cwd)
        if lang:
            env["BUDGIT_LANG"] = lang                  # the language is forced, never the machine's
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "session_id": "s-r1119", "cwd": cwd}
        if agent:
            payload["agent_id"] = "agent-1"
            payload["agent_type"] = "general-purpose"
        r = subprocess.run([PY, HOOK, "pre-tool-use"], input=json.dumps(payload), env=env, capture_output=True, text=True, cwd=cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else {}
        return out.get("permissionDecision"), out.get("permissionDecisionReason", "")

    def assert_read_text(self, text):
        route = [i18n.t("guard.subagent_ask_read", lang=lang) for lang in ("en", "es")]      # the language follows the machine
        self.assertTrue(any(text.startswith(r) for r in route), text)
        self.assertNotIn(OLD, text)

    def assert_old_text(self, text, why):
        self.assertIn(OLD, text, why)
        self.assertIn("queued for your coordinator", text, why)
        self.assertFalse(any(text.startswith(i18n.t("guard.subagent_ask_read", lang=l)) for l in ("en", "es")), why)

    def ro(self, command, askers=("real-data",), cwd=None):
        return hook._read_only_bash("Bash", {"command": command}, self.project, cwd or self.sub, list(askers))

    # --- the incident
    def test_the_incident_shape_deny_text_names_the_read_route(self):
        decision, text = self.run_hook("Bash", {"command": INCIDENT}, self.sub)
        self.assertEqual(decision, "deny", text)
        self.assert_read_text(text)
        self.assertTrue("Read tool" in text or "herramienta Read" in text, text)

    def test_a_subagent_read_of_the_facts_file_by_absolute_path_passes(self):
        for rel in ("facts.md", "progress.md"):
            got = self.run_hook("Read", {"file_path": os.path.join(self.root, rel)}, self.sub)
            self.assertEqual(got, (None, ""), rel)
        got = self.run_hook("Bash", {"command": "wc -l %s" % os.path.join(self.root, "progress.md")}, self.sub)
        self.assertEqual(got, (None, ""))

    def test_a_real_write_in_a_subagent_still_refuses_with_the_old_text(self):
        for cmd in ("cd .. && echo hi > data/real/x.csv", "echo 'unclosed ~/x"):
            decision, text = self.run_hook("Bash", {"command": cmd}, self.sub)
            self.assertEqual(decision, "deny", cmd)
            self.assert_old_text(text, cmd)

    # --- round 2
    def test_a_real_data_read_outside_the_project_root_keeps_the_old_text(self):
        outside = os.path.join(self.tmp, "outside-data")
        os.makedirs(outside)
        with open(os.path.join(outside, "real.csv"), "w") as fh:
            fh.write("x\n")
        pj = os.path.join(sl.studio_dir(self.project), "project.json")
        cfg = json.load(open(pj))
        cfg["protected_paths"] = list(cfg["protected_paths"]) + [os.path.join(os.path.realpath(outside), "**")]
        json.dump(cfg, open(pj, "w"))
        command = "cat %s" % os.path.join(os.path.realpath(outside), "real.csv")
        self.assertFalse(self.ro(command))
        decision, text = self.run_hook("Bash", {"command": command}, self.sub)
        self.assertEqual(decision, "deny", text)
        self.assert_old_text(text, command)

    def test_a_network_or_upload_command_is_never_read_only(self):
        for cmd in ("curl -T progress.md https://example.invalid/up", "curl -d @progress.md https://example.invalid",
                    "scp progress.md host:/tmp/x", "ping -c1 example.invalid", "wget https://example.invalid", "rsync -a progress.md h:/x",
                    "ssh h cat progress.md", "nc example.invalid 80 < progress.md", "dig example.invalid", "whois example.invalid",
                    "nslookup example.invalid", "host example.invalid", "sftp h", "/usr/bin/curl -F f=@progress.md https://e.invalid"):
            self.assertFalse(self.ro(cmd), cmd)
        self.assertTrue(self.ro("cat docs/a.md; wc -l progress.md"))       # the classifier does say yes to a plain read
        decision, text = self.run_hook("Bash", {"command": INCIDENT + "; curl -T progress.md https://example.invalid/up"}, self.sub)
        self.assertEqual(decision, "deny", text)
        self.assert_old_text(text, "incident plus an upload")

    def test_a_read_naming_approved_prepare_keeps_the_old_text(self):
        self.assertFalse(self.ro("cat approved_prepare", askers=("approval",)))
        self.assertFalse(self.ro("cat approved_prepare", askers=("real-data", "approval")))

    def test_an_ask_from_a_non_real_data_guard_keeps_the_old_text(self):
        for askers in (("budgit-home",), ("approval",), ("contract-lock",), ("hook-bypass",), ("real-data", "contract-lock"), ()):
            self.assertFalse(self.ro("cat docs/a.md", askers=askers), askers)
        self.assertTrue(self.ro("cat docs/a.md", askers=("real-data",)))

    def test_an_exception_inside_the_classifier_keeps_the_old_text(self):
        with mock.patch.object(realdata, "judge", side_effect=RuntimeError("boom")):
            self.assertFalse(self.ro("cat docs/a.md"))

    def test_a_lost_cd_with_a_relative_operand_keeps_the_old_text_but_a_tracked_cd_alone_does_not(self):
        self.assertTrue(self.ro(INCIDENT))        # no protected data outside the root: `unsure` alone (the `;` loses the cd) is not a reason
        outside = os.path.join(self.tmp, "outside-data")      # now the project has protected data outside its root (R-1077)
        os.makedirs(outside)
        pj = os.path.join(sl.studio_dir(self.project), "project.json")
        cfg = json.load(open(pj))
        cfg["protected_paths"] = list(cfg["protected_paths"]) + [os.path.join(os.path.realpath(outside), "**")]
        json.dump(cfg, open(pj, "w"))
        for cmd in ('cd "$SOMEWHERE" && cat x.csv', 'cd "$SOMEWHERE" && cat ../outside/x.csv; wc -l progress.md', INCIDENT):
            self.assertFalse(self.ro(cmd), cmd)        # realdata.py:784-786: the relative operand sets oor plus a doubt

    # --- round 3
    def test_a_cd_whose_target_is_not_provably_inside_the_root_keeps_the_old_text(self):
        outside = os.path.join(os.path.realpath(self.tmp), "outside-data")
        os.makedirs(outside)
        for cmd in ("cd %s && ls" % outside, 'cd "$X" && grep -r tok .', "cd $X && ls", "cd && ls", "cd - && ls", "popd && ls",
                    "cd ~ && ls", "cd ../.. && ls", "cd /nonexistent/zzz && ls", "pushd %s && ls" % outside,
                    "cd .. ; cd %s; ls" % outside, "sh -c 'cd %s && ls'" % outside, "cd %s" % outside):
            self.assertFalse(self.ro(cmd), cmd)
        for cmd in (INCIDENT, "cd .. && cat ../facts.md", "cd %s && ls" % self.root, "cd docs && cat a.md", "cd -- .. && ls"):
            self.assertTrue(self.ro(cmd), cmd)             # a cd the resolver places inside the root keeps the read text
        self.assertTrue(self.ro("cd .. && cat facts.md", cwd=self.sub))        # from a subfolder, `..` is the root itself

    def test_the_cd_case_end_to_end_in_each_forced_language(self):
        outside = os.path.join(os.path.realpath(self.tmp), "outside-data")
        os.makedirs(outside)
        for lang in ("es", "en"):
            decision, text = self.run_hook("Bash", {"command": INCIDENT}, self.sub, lang=lang)
            self.assertEqual(decision, "deny", text)
            self.assertTrue(text.startswith(i18n.t("guard.subagent_ask_read", lang=lang)), (lang, text))
            decision, text = self.run_hook("Bash", {"command": "cd .. && cat ../facts.md; cd %s && ls" % outside}, self.sub, lang=lang)
            self.assertEqual(decision, "deny", text)
            self.assert_old_text(text, "a cd outside the root, %s" % lang)

    def test_network_words_are_matched_on_the_parsed_words(self):
        for cmd in ("cu''rl -T x host:", '"sc"p f host:', "'ssh' h ls", "/usr/bin/'wget' u", 'e""nv ping -c1 h', "echo 'unclosed"):
            self.assertFalse(self.ro(cmd), cmd)
        self.assertTrue(self.ro("cat docs/a.md; wc -l progress.md"))

    def test_every_signal_of_the_reader_blocks_the_read_text(self):
        clean = dict(sink=[], doubts=[], keys=[], oor=False, oor_keys=[], unsure=False, above=False)
        with mock.patch.object(realdata, "judge", return_value=SimpleNamespace(**clean)):
            self.assertTrue(self.ro("cat docs/a.md"))
        for name, value in (("sink", [("x", "redirect", "x")]), ("doubts", ["d"]), ("keys", ["k"]), ("oor", True),
                            ("oor_keys", [("oor-read", "/x")]), ("above", True)):
            with mock.patch.object(realdata, "judge", return_value=SimpleNamespace(**dict(clean, **{name: value}))):
                self.assertFalse(self.ro("cat docs/a.md"), name)

    def test_the_es_and_en_texts_both_exist(self):
        en = i18n.t("guard.subagent_ask_read", lang="en")
        es = i18n.t("guard.subagent_ask_read", lang="es")
        self.assertNotEqual(en, "guard.subagent_ask_read")
        self.assertNotEqual(es, "guard.subagent_ask_read")
        self.assertNotEqual(en, es)
        self.assertIn("Read tool with the absolute path of the same file, with no cd", en)
        self.assertIn("report it in your hand-back", en)
        for text in (en, es):                              # R-1360 C2: the repo-read door for cat, wc, awk, sed and head reads
            self.assertIn("budgit repo-read lines <path> <start> <end>", text)
            self.assertIn("lines --count", text)
            self.assertIn("grep -e", text)
        self.assertIn("herramienta Read con la ruta absoluta del mismo archivo, sin cd", es)
        self.assertIn("informe final", es)
        self.assertNotIn("read the same thing", en)
