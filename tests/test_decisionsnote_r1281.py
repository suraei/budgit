"""The decisions line: a PM session is told, with a time and a folder, that the TM left new sections in its relay file."""
import builtins, json, os, re
from unittest import mock
from helpers import StudioTestCase, ROOT
import studiolib as sl
import decisionsnote
import hook
import startbudget

H1 = "## 20 Nov 11:40"
H2 = "## 20 Nov 11:40 (2), answer to your 11:40 cycle"
BODY = "SECRET BODY text of a decision"


class DecisionsNoteTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.relay = os.path.join(self.tmp, "relay")
        os.makedirs(self.relay)
        self.mine = os.path.join(self.relay, "pm-demo.md")
        self.other = os.path.join(self.relay, "pm-other.md")
        self.write_map({self.project: "pm-demo.md", os.path.join(self.tmp, "other"): "pm-other.md"})
        self.put(self.mine, "")
        self.put(self.other, H1 + "\nOTHER BODY\n")
        self.real_load = sl.load_config
        cfg = mock.patch.object(sl, "load_config", lambda: {"relay_dir": self.relay})
        cfg.start()
        self.addCleanup(cfg.stop)
        self.inp = {"session_id": "demo"}
        self.out = []
        emit = mock.patch.object(hook, "emit", self.out.append)
        emit.start()
        self.addCleanup(emit.stop)
        del hook._START[:]
        self.addCleanup(lambda: hook._START.clear())

    def write_map(self, data):
        with open(os.path.join(self.relay, "map.json"), "w") as f:
            f.write(json.dumps(data))

    def put(self, path, text):
        with open(path, "w") as f:
            f.write(text)

    def add(self, text):
        with open(self.mine, "a") as f:
            f.write(text)

    def role(self, name):
        return mock.patch("roles.detect", return_value={"role": name, "name": "demo", "path": self.project})

    def marker(self):
        with open(os.path.join(self.relay, ".read", "pm-demo.md.marker")) as f:
            return f.read()

    def spy_open(self):
        seen = []
        real = builtins.open

        def spy(path, *a, **k):
            seen.append(str(path))
            return real(path, *a, **k)
        return seen, mock.patch("builtins.open", spy)

    def test_pm_session_prints_line_and_marker_advances_on_session_start_and_any_pm_prompt(self):
        self.add(H1 + "\n" + BODY + "\n" + H2 + "\n" + BODY + "\n")
        with self.role("PM"):
            hook._decisions_context("SessionStart", self.inp, self.project)
            self.assertEqual([s["tag"] for s in hook._START], ["decisions"])
            self.assertIn("2 sections", hook._START[0]["text"])
            hook._decisions_context("UserPromptSubmit", self.inp, self.project)       # nothing new since the start
            self.assertEqual(self.out, [])
            self.add(H1 + "\n" + BODY + "\n")
            hook._decisions_context("UserPromptSubmit", self.inp, self.project)       # any PM prompt reads and advances
            self.assertEqual(len(self.out), 1)
            self.assertIn("1 sections", self.out[0]["hookSpecificOutput"]["additionalContext"])
            hook._decisions_context("UserPromptSubmit", self.inp, self.project)
            self.assertEqual(len(self.out), 1)

    def test_subagent_lane_and_tm_sessions_print_nothing(self):
        self.add(H1 + "\n" + BODY + "\n")
        sub = {"session_id": "demo", "agent_id": "a1"}
        hook._decisions_context("UserPromptSubmit", sub, self.project)
        for name in ("lane", "TM"):
            with self.role(name):
                hook._decisions_context("UserPromptSubmit", self.inp, self.project)
                hook._decisions_context("SessionStart", self.inp, None)
        self.assertEqual(self.out, [])
        self.assertEqual(hook._START, [])
        self.assertFalse(os.path.exists(os.path.join(self.relay, ".read")))

    def test_only_own_mapped_file_opened_and_no_relay_dir_means_no_output(self):
        self.add(H1 + "\n")
        seen, spy = self.spy_open()
        with spy:
            self.assertTrue(decisionsnote.line(self.inp, self.project, "PM"))
        self.assertIn(self.mine, seen)
        self.assertNotIn(self.other, seen)
        seen[:] = []
        with mock.patch.object(sl, "load_config", lambda: {"relay_dir": None}), spy:
            self.assertIsNone(decisionsnote.line(self.inp, self.project, "PM"))
        self.assertEqual(seen, [])                       # no open call at all, anywhere

    def test_name_that_leaves_the_folder_is_never_opened(self):
        escape = os.path.join(self.tmp, "escape.md")
        self.put(escape, H1 + "\nESCAPED BODY\n")
        self.write_map({self.project: "../escape.md"})
        seen, spy = self.spy_open()
        with spy:
            self.assertIsNone(decisionsnote.line(self.inp, self.project, "PM"))
        self.assertEqual([p for p in seen if "escape" in p], [])

    def test_load_config_default_relay_dir_is_none(self):
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump({"codex": "off"}, f)               # a config that does not name the key
        cfg = self.real_load()
        self.assertIn("relay_dir", cfg)
        self.assertIsNone(cfg["relay_dir"])

    def test_output_has_captured_time_only_and_marker_has_time_and_offset_only(self):
        self.add(H1 + "\n" + BODY + "\n" + H2 + "\n" + BODY + "\n")
        text = decisionsnote.line(self.inp, self.project, "PM")
        self.assertIn("20 Nov 11:40", text)
        self.assertIn(self.relay, text)
        for leak in (BODY, "answer to your", "pm-demo", "pm-other", "OTHER BODY"):
            self.assertNotIn(leak, text)
        self.assertRegex(self.marker().strip(), r"^\d\d? [A-Za-z]{3} \d\d:\d\d \d+$")

    def test_order_by_byte_offset_fixtures_from_real_headers_marker_from_real_writer(self):
        self.add(H1 + "\n" + BODY + "\n")
        decisionsnote.line(self.inp, self.project, "PM")           # the real writer makes the marker
        self.add("## 19 Nov 09:00\n" + BODY + "\n" + H2 + "\n")    # a later offset with an earlier label is still new
        text = decisionsnote.line(self.inp, self.project, "PM")
        self.assertIn("2 sections", text)
        self.assertIn("newest 20 Nov 11:40", text)                 # the last header by offset, label as captured
        self.assertIsNone(decisionsnote.line(self.inp, self.project, "PM"))
        with mock.patch("roles.detect", return_value={"role": "PM", "name": "d", "path": self.project}):
            self.add(H1 + "\n")
            self.assertIn("1 sections", decisionsnote.line(self.inp, None, "PM"))   # a registered PM with no project: key from roles.detect

    def test_truncated_file_shows_new_decisions(self):
        self.add(H1 + "\n" + "y" * 500 + "\n")
        self.assertIn("1 sections", decisionsnote.line(self.inp, self.project, "PM"))   # the marker now sits past 500 bytes
        self.put(self.mine, H2 + "\n")                                                   # rewritten, shorter than the marker
        self.assertIn("1 sections", decisionsnote.line(self.inp, self.project, "PM"))
        self.assertIsNone(decisionsnote.line(self.inp, self.project, "PM"))

    def test_roles_detect_runs_only_for_a_pm_with_no_project(self):
        self.add(H1 + "\n")
        with mock.patch("roles.detect", return_value={"role": "PM", "name": "d", "path": self.project}) as det:
            decisionsnote.line(self.inp, self.project, "PM")
            decisionsnote.line(self.inp, self.project, "lane")
            decisionsnote.line(self.inp, None, "TM")
            det.assert_not_called()
            self.add(H1 + "\n")
            decisionsnote.line(self.inp, None, "PM")
            self.assertEqual(det.call_count, 1)

    def test_big_or_bad_file_skips_silently_line_follows_time_line_tag_decisions_degrades_after_notice_and_before_summary_action(self):
        self.add("x" * 200000 + "\n" + H1 + "\n" + BODY + "\n")
        self.assertIn("1 sections", decisionsnote.line(self.inp, self.project, "PM"))
        with open(self.mine, "wb") as f:
            f.write(b"\xff\xfe\x00\x01" * 3000)
        self.assertIsNone(decisionsnote.line(self.inp, self.project, "PM"))
        self.put(os.path.join(self.relay, "map.json"), "{not json")
        self.assertIsNone(decisionsnote.line(self.inp, self.project, "PM"))
        src = open(os.path.join(ROOT, "plugin", "scripts", "hook.py")).read()
        self.assertLess(src.index('local_time_context("UserPromptSubmit")'), src.index('_decisions_context("UserPromptSubmit"'))
        self.assertLess(src.index('local_time_context("SessionStart")'), src.index('_decisions_context("SessionStart"'))
        rank = startbudget.TAGS["decisions"][0]
        self.assertTrue(startbudget.TAGS["summary.action"][0] < rank < startbudget.TAGS["notice"][0])
        sec = startbudget.section
        _, report = startbudget.fit("PM", [sec("role", "role line"), sec("notice", "\n".join("n%d" % i for i in range(15))),
                                           sec("decisions", "decisions line\nsecond\nthird"),
                                           sec("summary.action", "\n".join("a%d" % i for i in range(34)))])
        self.assertIn("notice", report["pointers"])
        self.assertIn("decisions", report["pointers"])
        self.assertNotIn("summary.action", report["pointers"])
