import json, os, subprocess, sys
from helpers import StudioTestCase, ROOT
import studiolib as sl

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")


class FinalFixesTest(StudioTestCase):
    def run_hook(self, event, payload):
        env = dict(os.environ, STUDIO_HOME=self.home)
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(payload),
                           env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def test_slash_command_record_matches_prompt_hook_entry(self):
        sl.activate(self.project, bind=False)
        sl.append_inbox(self.project, "/studio-kickoff quiero una app de recetas", "s1", "prompt")
        t = os.path.join(self.tmp, "t.jsonl")
        with open(t, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "uuid": "u1", "timestamp": "t", "origin": {"kind": "human"},
                                "message": {"content": "<command-message>studio-kickoff</command-message>\n"
                                            "<command-name>/studio-kickoff</command-name>\n"
                                            "<command-args>quiero una app de recetas</command-args>"}}) + "\n")
        self.assertEqual(sl.reconcile_transcript(self.project, t, "s1"), [])
        self.assertEqual(len(sl.load_inbox(self.project)), 1)

    def test_slash_command_alone_is_normalised(self):
        sl.activate(self.project, bind=False)
        self.assertEqual(sl.normalise_command(
            "<command-message>x</command-message>\n<command-name>/compact</command-name>\n<command-args></command-args>"),
            "/compact")

    def test_resume_of_existing_ledger_includes_session_history(self):
        sl.activate(self.project, "s1")
        data = sl.load_ledger(self.project)
        sl.add_item(data, "x", ["legacy"])
        sl.save_ledger(self.project, data)
        sl.activate(self.project, "s2")  # new session resuming a project with a CLI ledger
        self.assertNotIn("s2", sl.load_state(self.project)["since"])

    def test_include_history_clears_previous_since(self):
        os.makedirs(sl.studio_dir(self.project))
        with open(os.path.join(sl.studio_dir(self.project), "ledger.md"), "w") as f:
            f.write("| R0 | hand | open | |\n")
        sl.activate(self.project, "s1")  # v0 migration → since-now
        self.assertIn("s1", sl.load_state(self.project)["since"])
        sl.activate(self.project, "s1", include_history=True)
        self.assertNotIn("s1", sl.load_state(self.project)["since"])

    def test_hook_error_is_visible_to_her(self):
        sl.activate(self.project, "s1")
        with open(os.path.join(sl.studio_dir(self.project), "ledger.json"), "w") as f:
            f.write("{broken")
        out = self.run_hook("stop", {"session_id": "s1", "stop_hook_active": False})
        self.assertIn("enforcement off", out["systemMessage"])

    def test_non_dict_inbox_lines_and_bad_state_are_tolerated(self):
        sl.activate(self.project, bind=False)
        sdir = sl.studio_dir(self.project)
        with open(os.path.join(sdir, "inbox.jsonl"), "w") as f:
            f.write("123\n[]\n")
        with open(os.path.join(sdir, "state.json"), "w") as f:
            f.write("[1, 2]")
        self.assertEqual(sl.load_inbox(self.project), [])
        self.assertEqual(sl.load_state(self.project)["offsets"], {})

    def test_redaction_covers_spanish_and_env_vars(self):
        data = sl.empty_ledger()
        it = sl.add_item(data, "contraseña=hunter22 y export AWS_SECRET_ACCESS_KEY=abcd1234XYZ ; pwd=/Users/x/proj", ["I-1"])
        self.assertNotIn("hunter22", it["text"])
        self.assertNotIn("abcd1234XYZ", it["text"])
        self.assertIn("/Users/x/proj", it["text"])
