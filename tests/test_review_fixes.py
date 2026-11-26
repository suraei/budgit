import json, os, shutil, subprocess, sys
from helpers import StudioTestCase, ROOT
import studiolib as sl

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")


def rec(**kw):
    return json.dumps(kw, ensure_ascii=False) + "\n"


class ReviewFixesTest(StudioTestCase):
    def run_hook(self, event, payload):
        env = dict(os.environ, BUDGIT_HOME=self.home)
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(payload),
                           env=env, capture_output=True, text=True)
        return json.loads(r.stdout) if r.stdout.strip() else None

    def setUp(self):
        super().setUp()
        sl.activate(self.project, bind=False)
        self.t = os.path.join(self.tmp, "t.jsonl")

    def write(self, *records):
        with open(self.t, "w", encoding="utf-8") as f:
            f.write("".join(records))

    def test_ide_opened_file_is_not_an_input(self):
        self.write(rec(type="user", uuid="u1", timestamp="t", origin={"kind": "human"},
                       message={"content": "<ide_opened_file>The user opened foo.py</ide_opened_file>"}))
        self.assertEqual(sl.reconcile_transcript(self.project, self.t, "s1"), [])

    def test_prompt_mentioning_a_tag_midway_is_kept(self):
        self.assertIsNotNone(sl.append_inbox(self.project, "mira esto: <system-reminder> sale en el log", "s1", "prompt"))

    def test_rejection_needs_error_and_exact_prefix(self):
        self.write(
            rec(type="user", uuid="u1", timestamp="t", message={"content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "grep: ...the user said: hello world\nmore file"}]}),
            rec(type="user", uuid="u2", timestamp="t", message={"content": [
                {"type": "tool_result", "tool_use_id": "t2", "is_error": True,
                 "content": "The user doesn't want to proceed with this tool use. The tool use was rejected. "
                            "To tell you how to proceed, the user said:\nno toques eso"}]}))
        sl.reconcile_transcript(self.project, self.t, "s1")
        texts = [e["text"] for e in sl.load_inbox(self.project)]
        self.assertEqual(texts, ["the user said:\nno toques eso"])

    def test_odd_records_do_not_freeze_reconcile(self):
        self.write(rec(type="user", uuid="u0", timestamp="t", message="not-a-dict"),
                   rec(type="user", uuid="u1", timestamp="t", origin={"kind": "human"}, message={"content": "hola"}))
        self.assertEqual(len(sl.reconcile_transcript(self.project, self.t, "s1")), 1)

    def test_inbox_line_without_id_does_not_break_untracked(self):
        with open(os.path.join(sl.studio_dir(self.project), "inbox.jsonl"), "w") as f:
            f.write("{}\n")
        self.assertEqual(sl.untracked(self.project, sl.load_ledger(self.project)), [])

    def test_stop_reason_is_capped(self):
        sl.activate(self.project, "s1")
        for i in range(60):
            sl.append_inbox(self.project, "petición %d" % i, "s1", "prompt")
        out = self.run_hook("stop", {"session_id": "s1", "stop_hook_active": False})
        self.assertLess(len(out["reason"]), 4000)
        self.assertIn("more", out["reason"])

    def test_two_sessions_each_get_their_stall_guard(self):
        sl.activate(self.project, "s1")
        sl.write_pointer("s2", self.project)
        sl.append_inbox(self.project, "uno", "s1", "prompt")
        sl.append_inbox(self.project, "dos", "s2", "prompt")
        for s in ("s1", "s2"):
            self.assertEqual(self.run_hook("stop", {"session_id": s, "stop_hook_active": False})["decision"], "block")
        for s in ("s1", "s2"):
            out = self.run_hook("stop", {"session_id": s, "stop_hook_active": True})
            self.assertNotIn("decision", out or {}, s)

    def test_private_logs_are_gitignored(self):
        gi = open(os.path.join(self.project, ".gitignore")).read()
        for name in (".budgit/compactions.log", ".budgit/audit.log", ".budgit/ledger.json.prev"):
            self.assertIn(name, gi)

    def test_redaction_spares_plain_spanish(self):
        data = sl.empty_ledger()
        it = sl.add_item(data, "el token: usar JWT y api_key = leer de .env; password=Hunt3r2024!", ["I-1"])
        self.assertIn("usar JWT", it["text"])
        self.assertIn("leer de .env", it["text"])
        self.assertNotIn("Hunt3r2024!", it["text"])
        it2 = sl.add_item(data, "password=correcthorsebattery secret: lowercaseonly", ["I-2"])
        self.assertNotIn("correcthorsebattery", it2["text"])
        self.assertNotIn("lowercaseonly", it2["text"])
