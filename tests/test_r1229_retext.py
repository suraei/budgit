"""R-1229: `ledger retext` rewrites an input's or item's text with a logged event (never the old text), and a protected
name never lands in a new ledger text. Fake names only, injected through livework.private_names."""
import io
import json
import os
import re
import subprocess
import sys
from unittest import mock

from helpers import StudioTestCase, patch_env
import studiolib as sl
import livework

FAKE = ["zorbquux"]


class RetextTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.init_project()
        data = sl.load_ledger(self.project)
        sl.add_item(data, "first thing to build", ["I-1"])
        sl.save_ledger(self.project, data)
        sl.append_inbox(self.project, "an input text", "s1", "manual")

    def hash_ok(self):
        return not sl.ledger_tampered(self.project)

    def test_retext_changes_text_logs_one_event_without_the_old_text_and_keeps_the_hash(self):
        data = sl.load_ledger(self.project)
        with mock.patch.object(livework, "private_names", return_value=FAKE):
            sl.retext(self.project, data, "R-1", "second wording", "wrong words", names=FAKE)
        sl.save_ledger(self.project, data)
        it = sl.load_ledger(self.project)["items"][0]
        self.assertEqual(it["text"], "second wording")
        ev = [e for e in it["events"] if e["kind"] == "retext"]
        self.assertEqual(len(ev), 1)
        self.assertIn("wrong words", ev[0]["text"])
        self.assertNotIn("first thing", json.dumps(it["events"]))
        self.assertTrue(self.hash_ok())

    def test_retext_of_an_inbox_input_rewrites_it_and_logs_without_the_old_text(self):
        sl.append_inbox(self.project, "talks about zorbquux stuff", "s1", "manual")
        iid = sl.load_inbox(self.project)[-1]["id"]
        data = sl.load_ledger(self.project)
        sl.retext(self.project, data, iid, None, "names", names=FAKE)       # D2: an input takes no text
        sl.save_ledger(self.project, data)
        entry = [e for e in sl.load_inbox(self.project) if e["id"] == iid][0]
        self.assertEqual(entry["text"], "talks about a protected project stuff")
        dumped = json.dumps(entry["events"])
        self.assertNotIn("zorbquux", dumped)
        self.assertIn("old text 26 chars", dumped)
        self.assertTrue(self.hash_ok())

    def test_retext_refuses_an_unknown_id_and_a_text_with_a_protected_name(self):
        data = sl.load_ledger(self.project)
        with self.assertRaises(sl.StudioError):
            sl.retext(self.project, data, "R-99", "x", "r", names=FAKE)
        with self.assertRaises(sl.StudioError):
            sl.retext(self.project, data, "I-99", "x", "r", names=FAKE)
        with self.assertRaises(sl.StudioError):
            sl.retext(self.project, data, "R-1", "talks about Zorbquux here", "r", names=FAKE)
        self.assertEqual(data["items"][0]["text"], "first thing to build")

    def test_retext_cli_refuses_a_tampered_ledger(self):
        path = os.path.join(sl.studio_dir(self.project), "ledger.json")
        with open(path, "a") as f:
            f.write(" ")
        self.assertTrue(sl.ledger_tampered(self.project))
        cli = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin", "bin", "budgit")
        import subprocess, sys
        r = subprocess.run([sys.executable, cli, "--project", self.project, "ledger", "retext", "R-1", "--text", "new", "--reason", "r"],
                           capture_output=True, text=True, env=dict(os.environ, BUDGIT_HOME=self.tmp, STUDIO_HOME=self.tmp))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertEqual(sl.load_ledger(self.project)["items"][0]["text"], "first thing to build")

    def test_retext_cli_refuses_a_protected_name_and_leaves_the_ledger_bytes_alone(self):
        home = os.path.join(self.tmp, "fakehome")
        os.makedirs(os.path.join(home, ".budgit"))
        with open(os.path.join(home, ".budgit", "config.json"), "w", encoding="utf-8") as f:
            json.dump({"protected_dirs": ["/fake/zorbquux"]}, f)
        path = os.path.join(sl.studio_dir(self.project), "ledger.json")
        with open(path, "rb") as f:
            before = f.read()
        cli = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin", "bin", "budgit")
        import subprocess, sys
        r = subprocess.run([sys.executable, cli, "--project", self.project, "ledger", "retext", "R-1",
                            "--text", "talks about Zorbquux here", "--reason", "r"],
                           capture_output=True, text=True,
                           env=dict(os.environ, HOME=home, BUDGIT_HOME=self.tmp, STUDIO_HOME=self.tmp))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_add_item_stores_the_shielded_form_of_a_protected_name(self):
        data = sl.load_ledger(self.project)
        with mock.patch.object(livework, "private_names_ok", return_value=(FAKE, True)):
            it = sl.add_item(data, "work on zorbquux now", ["I-1"], title="zorbquux title")
        self.assertEqual(it["text"], "private")
        self.assertEqual(it["title"], "private")
        with mock.patch.object(livework, "private_names_ok", return_value=(FAKE, True)):
            self.assertEqual(sl.shield_text("see zorbquux"), "private")
            self.assertEqual(sl.shield_text("plain"), "plain")

    def _add(self, text):
        data = sl.load_ledger(self.project)
        err = io.StringIO()
        with mock.patch.object(livework, "private_names_ok", return_value=(FAKE, True)), mock.patch("sys.stderr", err):
            it = sl.add_item(data, text, ["I-1"])
        return it, err.getvalue()

    def test_f1_separator_and_zero_width_forms_are_refused_by_retext_and_private_on_add(self):
        zwsp = chr(0x200B)
        forms = ["zorb" + zwsp + "quux", "zorb.quux", "zorb-quux", "zorb quux", "zorb" + zwsp + zwsp + "quux"]
        for form in forms:
            data = sl.load_ledger(self.project)
            with self.assertRaises(sl.StudioError, msg=repr(form)):
                sl.retext(self.project, data, "R-1", "talks about %s here" % form, "r", names=FAKE)
            it, _ = self._add("work on %s now" % form)
            self.assertEqual(it["text"], "private", repr(form))

    def test_f2_fullwidth_form_is_refused_by_retext_and_shielded_on_add(self):
        full = "".join(chr(ord(c) + 0xFEE0) for c in "zorbquux")
        data = sl.load_ledger(self.project)
        with self.assertRaises(sl.StudioError):
            sl.retext(self.project, data, "R-1", "talks about %s here" % full, "r", names=FAKE)
        it, _ = self._add("work on %s now" % full)
        self.assertEqual(it["text"], "private")

    def test_f3_a_reason_holding_the_name_is_refused_and_the_ledger_bytes_stay(self):
        path = os.path.join(sl.studio_dir(self.project), "ledger.json")
        with open(path, "rb") as f:
            before = f.read()
        data = sl.load_ledger(self.project)
        with self.assertRaises(sl.StudioError):
            sl.retext(self.project, data, "R-1", "second wording", "because of zorbquux", names=FAKE)
        with self.assertRaises(sl.StudioError):
            sl.retext(self.project, data, "R-1", "second wording", "because of zorb.quux", names=FAKE)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(data["items"][0]["text"], "first thing to build")

    def test_f4_an_input_retext_can_only_lose_words(self):
        data = sl.load_ledger(self.project)
        with self.assertRaises(sl.StudioError):
            sl.retext(self.project, data, "I-1", "an input text approved", "r", names=FAKE)
        self.assertEqual(sl.load_inbox(self.project)[0]["text"], "an input text")
        sl.append_inbox(self.project, "talks about zorbquux stuff", "s1", "manual")
        iid = sl.load_inbox(self.project)[-1]["id"]
        with self.assertRaises(sl.StudioError):     # D2: free text on an input is refused, so no word can be added
            sl.retext(self.project, data, iid, "talks about stuff approved", "r", names=FAKE)
        with self.assertRaises(sl.StudioError):     # an input that names nothing has nothing to remove
            sl.retext(self.project, data, "I-1", None, "r", names=FAKE)
        sl.retext(self.project, data, iid, None, "r", names=FAKE)
        new = sl.load_inbox(self.project)[-1]["text"]
        self.assertEqual(new, "talks about a protected project stuff")
        self.assertFalse(set(new.split()) - set("talks about zorbquux stuff".split()) - {"a", "protected", "project"})
        sl.retext(self.project, data, "R-1", "entirely new words approved", "r", names=FAKE)
        self.assertEqual(data["items"][0]["text"], "entirely new words approved")

    def test_f5_an_unreadable_names_config_refuses_retext_and_warns_once_on_add(self):
        data = sl.load_ledger(self.project)
        with mock.patch.object(livework, "private_names_scan", return_value=([], ["/fake/config.json"])), \
                mock.patch.object(livework, "private_names_ok", return_value=([], False)):
            with self.assertRaises(sl.StudioError):
                sl.retext(self.project, data, "R-1", "second wording", "r")
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                it = sl.add_item(data, "plain new work", ["I-1"], title="plain title")
        self.assertEqual(it["text"], "plain new work")
        self.assertEqual(err.getvalue().count("warning"), 1)
        self.assertEqual(err.getvalue().count("\n"), 1)

    def test_f6_the_retext_event_holds_old_text_and_a_number_and_no_hex_run(self):
        data = sl.load_ledger(self.project)
        ev = sl.retext(self.project, data, "R-1", "second wording", "wrong words", names=FAKE)
        self.assertIn("old text", ev["text"])
        self.assertRegex(ev["text"], r"\d+ chars")
        self.assertIsNone(re.search(r"[0-9a-fA-F]{10}", ev["text"]))
        stored = [e for e in data["items"][0]["events"] if e["kind"] == "retext"][0]
        self.assertIsNone(re.search(r"[0-9a-fA-F]{10}", stored["text"]))

    def test_f7_a_failing_ledger_save_leaves_the_inbox_file_bytes_unchanged(self):
        sl.append_inbox(self.project, "an input zorbquux", "s1", "manual")
        iid = sl.load_inbox(self.project)[-1]["id"]
        path = sl._inbox_path(self.project)
        with open(path, "rb") as f:
            before = f.read()
        data = sl.load_ledger(self.project)
        new_inbox = []
        sl.retext(self.project, data, iid, None, "r", names=FAKE, inbox_out=new_inbox)
        with mock.patch.object(sl, "save_ledger", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                sl.save_ledger(self.project, data)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(len(new_inbox), 1)
        sl.write_inbox_replace(self.project, new_inbox[0])
        self.assertEqual([e for e in sl.load_inbox(self.project) if e["id"] == iid][0]["text"],
                         "an input a protected project")

    def test_q3_a_stored_private_prints_one_notice_on_stderr(self):
        it, err = self._add("work on zorbquux now")
        self.assertEqual(it["text"], "private")
        self.assertIn("stored as \"private\"", err)
        it, err = self._add("plain work")
        self.assertEqual(err, "")

    # ---- round 3 (F8 to F11): D1 to D5

    def _spans_cover(self, text, names, want):
        spans = livework.shield_spans(text, names)
        self.assertEqual([text[s:e] for s, e in spans], want, repr(text))
        self.assertEqual(livework.shield(text, names), "private", repr(text))

    def test_d1_split_forms_are_caught_with_spans_and_a_longer_word_is_not(self):
        zwsp = chr(0x200B)
        for form in ("zorb.quux", "zorb quux", "zorb" + zwsp + "quux", "Zorb-QUUX"):
            self._spans_cover("see %s now" % form, FAKE, [form])
        self._spans_cover("a z.o.r b", ["zor"], ["z.o.r"])
        full = "".join(chr(ord(c) + 0xFEE0) for c in "zorbquux")         # NFKC reads it as the plain name
        self._spans_cover("see %s now" % full, FAKE, [full])
        self._spans_cover("zorbquux and zorb quux", FAKE, ["zorbquux", "zorb quux"])
        for text, names in (("toma pastilla", ["mapa"]), ("a zorb x quux", FAKE), ("zorbx", ["zor"])):
            self.assertEqual(livework.shield_spans(text, names), [], text)
            self.assertEqual(livework.shield(text, names), text)

    def test_e1_a_name_of_four_or_more_characters_is_caught_inside_a_longer_word_with_the_whole_word_as_span(self):
        for word, names in (("zorbquuxes", FAKE), ("zorbquuxApp", FAKE), ("zorbquuxly", FAKE), ("mapas", ["mapa"])):
            self._spans_cover("see %s now" % word, names, [word])
        self.assertEqual(livework.shield_spans("toma pastilla", ["mapa"]), [])      # across words: not caught
        self.assertEqual(livework.shield_spans("zorbx", ["zor"]), [])                # a short name only whole words or runs
        self.assertEqual(livework.shield_spans("zorbxzor", ["zor"]), [])

    def test_e5_a_split_name_with_letters_glued_on_is_caught_with_the_whole_run_as_span(self):
        self._spans_cover("zorbquux and the zorb-quuxes plan", FAKE, ["zorbquux", "zorb-quuxes"])
        self._spans_cover("see myzorb-quux now", FAKE, ["myzorb-quux"])
        self._spans_cover("see myzorb-quuxes now", FAKE, ["myzorb-quuxes"])      # E6: glued on both sides
        self._spans_cover("see zorb" + chr(0x200B) + "quuxes now", FAKE, ["zorb" + chr(0x200B) + "quuxes"])
        self._spans_cover("see zorb/quuxes now", FAKE, ["zorb/quuxes"])
        spans = livework.shield_spans("see zorb_quux.app now", FAKE)
        self.assertEqual(livework.shield("see zorb_quux.app now", FAKE), "private")
        self.assertTrue(spans and "see zorb_quux.app now"[spans[0][0]:spans[0][1]].startswith("zorb_quux"), spans)
        for text, names in (("toma pastilla", ["mapa"]), ("zorb x quux", FAKE), ("zorbx", ["zor"]),
                            ("zorb x quuxes", FAKE), ("my zorb", FAKE), ("zorb x", ["zor"]), ("xzor y", ["zor"]),
                            ("the map and the plan", ["mapa"]), ("the roadmap a week", ["mapa"]),
                            ("zo rbx now", ["zorb"]), ("zorb quuxApp", FAKE),    # a space join: the named residual
                            ("zorb, quuxes", FAKE)):
            self.assertEqual(livework.shield_spans(text, names), [], text)
            self.assertEqual(livework.shield(text, names), text)

    def test_e5_an_input_retext_of_a_split_name_with_letters_glued_on_leaves_no_name(self):
        sl.append_inbox(self.project, "zorbquux and the zorb-quuxes plan", "s1", "manual")
        iid = sl.load_inbox(self.project)[-1]["id"]
        data = sl.load_ledger(self.project)
        sl.retext(self.project, data, iid, None, "names", names=FAKE)
        new = [e for e in sl.load_inbox(self.project) if e["id"] == iid][0]["text"]
        self.assertEqual(new, "a protected project and the a protected project plan")
        self.assertEqual(livework.shield_spans(new, FAKE), [])

    def test_e1_an_input_retext_of_a_name_and_its_longer_word_stores_no_name_at_all(self):
        sl.append_inbox(self.project, "zorbquux y los zorbquuxes", "s1", "manual")
        iid = sl.load_inbox(self.project)[-1]["id"]
        data = sl.load_ledger(self.project)
        sl.retext(self.project, data, iid, None, "names", names=FAKE)
        new = [e for e in sl.load_inbox(self.project) if e["id"] == iid][0]["text"]
        self.assertNotIn("zorbquux", new.lower())
        self.assertEqual(new, "a protected project y los a protected project")

    def test_e2_a_reason_cut_at_200_characters_is_shielded_before_the_cut(self):
        data = sl.load_ledger(self.project)
        with self.assertRaises(sl.StudioError):
            sl.retext(self.project, data, "R-1", "second wording", "a" * 192 + " zorbquux", names=FAKE)
        self.assertEqual(data["items"][0]["text"], "first thing to build")
        ok = sl.retext(self.project, data, "R-1", "second wording", "a" * 192 + " fine", names=FAKE)
        self.assertIn("old text", ok["text"])

    def test_e2_hangul_filler_forms_of_the_name_are_caught_in_a_reason_and_in_text(self):
        for filler in (chr(0x3164), chr(0xFFA0), chr(0x115F), chr(0x1160)):
            form = "zorb" + filler + "quux"
            self.assertEqual(livework.shield("see %s now" % form, FAKE), "private", repr(form))
            data = sl.load_ledger(self.project)
            with self.assertRaises(sl.StudioError, msg=repr(form)):
                sl.retext(self.project, data, "R-1", "second wording", "because of " + form, names=FAKE)

    def test_e3_protected_dirs_given_as_text_makes_retext_refuse_and_name_that_file(self):
        cfg, empty, path = self._config_home('{"protected_dirs": "/tmp/zorbquux"}')
        data = sl.load_ledger(self.project)
        with self.assertRaises(sl.StudioError) as cm:
            sl.retext(self.project, data, "R-1", "second wording", "r")
        self.assertIn(path, str(cm.exception))
        self.assertFalse(livework._config_dir_strings_ok(empty)[1])
        # the same in a home-folder config: the refusal names that file, not the live one
        cfg2, empty2, path2 = self._config_home('{"protected_dirs": ["/tmp/other"]}')
        os.makedirs(os.path.join(empty2, ".budgit"), exist_ok=True)
        home_cfg = os.path.join(empty2, ".budgit", "config.json")
        with open(home_cfg, "w", encoding="utf-8") as f:
            f.write('{"protected_dirs": "/tmp/zorbquux"}')
        with self.assertRaises(sl.StudioError) as cm2:
            sl.retext(self.project, data, "R-1", "second wording", "r")
        self.assertIn(home_cfg, str(cm2.exception))
        self.assertNotIn(path2, str(cm2.exception))
        self.assertEqual(data["items"][0]["text"], "first thing to build")

    def test_e3_a_project_marked_private_only_in_its_own_project_json_has_its_name_removed(self):
        root = os.path.join(self.tmp, "projroot")
        child = os.path.join(root, "zorbquux")
        os.makedirs(os.path.join(child, self.DATA), exist_ok=True)
        sl.save_ledger(child, sl.empty_ledger())
        with open(os.path.join(sl.studio_dir(child), "project.json"), "w", encoding="utf-8") as f:
            json.dump({"private": True}, f)
        self._config_home(json.dumps({"projects_root": root}))
        sl.append_inbox(self.project, "talks about zorbquux stuff", "s1", "manual")
        iid = sl.load_inbox(self.project)[-1]["id"]
        data = sl.load_ledger(self.project)
        sl.retext(self.project, data, iid, None, "names")           # no names given: gathered like the live feed
        new = [e for e in sl.load_inbox(self.project) if e["id"] == iid][0]["text"]
        self.assertEqual(new, "talks about a protected project stuff")

    def test_e4_a_transcript_backfill_after_a_retext_adds_no_input_and_sets_the_ref(self):
        original = "talks about zorbquux stuff"
        sl.append_inbox(self.project, original, "s9", "prompt")
        iid = sl.load_inbox(self.project)[-1]["id"]
        before = len(sl.load_inbox(self.project))
        data = sl.load_ledger(self.project)
        sl.retext(self.project, data, iid, None, "names", names=FAKE)
        entry = [e for e in sl.load_inbox(self.project) if e["id"] == iid][0]
        self.assertNotIn("zorbquux", entry["text"])
        self.assertEqual(entry["hash"], sl._norm_hash(original))        # the original hash stays
        self.assertIsNone(sl.append_inbox(self.project, original, "s9", "transcript", ref="t-ref-1"))
        entries = sl.load_inbox(self.project)
        self.assertEqual(len(entries), before)
        self.assertEqual([e for e in entries if e["id"] == iid][0]["ref"], "t-ref-1")

    def test_d2_an_input_retext_takes_no_text_removes_only_the_name_and_refuses_nothing_to_remove(self):
        sl.append_inbox(self.project, "keep  Zorb.Quux, and zorb quux! tail", "s1", "manual")
        iid = sl.load_inbox(self.project)[-1]["id"]
        data = sl.load_ledger(self.project)
        with self.assertRaises(sl.StudioError) as cm:
            sl.retext(self.project, data, iid, "any text", "r", names=FAKE)
        self.assertIn("--text", str(cm.exception))
        with self.assertRaises(sl.StudioError) as cm:
            sl.retext(self.project, data, iid, None, "r", names=[])
        self.assertIn("nothing to remove", str(cm.exception))
        with self.assertRaises(sl.StudioError) as cm:
            sl.retext(self.project, data, "I-1", None, "r", names=FAKE)
        self.assertIn("nothing to remove", str(cm.exception))
        self.assertEqual(sl.load_inbox(self.project)[-1]["text"], "keep  Zorb.Quux, and zorb quux! tail")
        sl.retext(self.project, data, iid, None, "r", names=FAKE)
        self.assertEqual(sl.load_inbox(self.project)[-1]["text"],
                         "keep  a protected project, and a protected project! tail")
        with self.assertRaises(sl.StudioError):                 # R-n keeps --text and needs it
            sl.retext(self.project, data, "R-1", None, "r", names=FAKE)

    def _config_home(self, body=None):
        """A fake BUDGIT_HOME under the temp folder, a HOME with no config, and the config file (None: no file)."""
        cfg = os.path.join(self.tmp, "cfgfake")
        os.makedirs(cfg, exist_ok=True)
        empty = os.path.join(self.tmp, "emptyhome")
        os.makedirs(empty, exist_ok=True)
        path = os.path.join(cfg, "config.json")
        if body is not None:
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
        patch_env(self, HOME=empty, BUDGIT_HOME=cfg, STUDIO_HOME=cfg)
        return cfg, empty, path

    def test_d3_an_unreadable_config_fails_closed_and_the_refusal_names_the_path(self):
        if os.geteuid() == 0:
            self.skipTest("root reads a file with mode 0")
        cfg, empty, path = self._config_home('{"protected_dirs": ["/tmp/zorbquux"]}')
        os.chmod(path, 0)
        try:
            strings, ok = livework._config_dir_strings_ok(empty)
            self.assertFalse(ok)
            data = sl.load_ledger(self.project)
            with self.assertRaises(sl.StudioError) as cm:
                sl.retext(self.project, data, "R-1", "second wording", "r")
        finally:
            os.chmod(path, 0o600)
        self.assertIn(path, str(cm.exception))
        self.assertEqual(data["items"][0]["text"], "first thing to build")

    def test_d3_an_empty_legacy_config_or_a_file_in_its_place_counts_as_missing(self):
        cfg, empty, path = self._config_home('{"protected_dirs": ["/tmp/zorbquux"]}')
        os.makedirs(os.path.join(empty, ".budgit"))
        with open(os.path.join(empty, ".budgit", "config.json"), "w", encoding="utf-8"):
            pass                                                # empty legacy file
        strings, ok = livework._config_dir_strings_ok(empty)
        self.assertTrue(ok)
        self.assertEqual(strings, ["/tmp/zorbquux"])
        with open(os.path.join(empty, ".studio"), "w", encoding="utf-8") as f:
            f.write("a file, not a folder")                     # the legacy folder is a file
        self.assertEqual(livework._config_dir_strings_ok(empty), (["/tmp/zorbquux"], True))
        with open(os.path.join(empty, ".budgit", "config.json"), "w", encoding="utf-8") as f:
            f.write("{not json")                                # a broken legacy file is unreadable, not missing
        self.assertFalse(livework._config_dir_strings_ok(empty)[1])

    def test_d4_a_reason_redact_would_change_is_refused_and_format_characters_are_not_stored(self):
        data = sl.load_ledger(self.project)
        with self.assertRaises(sl.StudioError):
            sl.retext(self.project, data, "R-1", "second wording", "password: hunter22xx", names=FAKE)
        self.assertEqual(data["items"][0]["text"], "first thing to build")
        self.assertEqual([e for e in data["items"][0].get("events", []) if e.get("kind") == "retext"], [])
        with self.assertRaises(sl.StudioError):                 # a name hidden by a format character in the reason
            sl.retext(self.project, data, "R-1", "second wording", "zorb" + chr(0x200B) + "quux", names=FAKE)
        ev = sl.retext(self.project, data, "R-1", "second wording", "wrong" + chr(0x200B) + chr(0x2060) + "words", names=FAKE)
        self.assertRegex(ev["text"], r"^wrong\s?words \(old text")
        self.assertNotIn(chr(0x200B), ev["text"])
        self.assertNotIn(chr(0x2060), ev["text"])

    def test_d4_write_inbox_replace_leaves_mode_0600_and_no_temp_file(self):
        folder = os.path.dirname(sl._inbox_path(self.project))
        before = set(os.listdir(folder))
        entries = sl.load_inbox(self.project)
        sl.write_inbox_replace(self.project, entries)
        self.assertEqual(os.stat(sl._inbox_path(self.project)).st_mode & 0o777, 0o600)
        self.assertEqual(set(os.listdir(folder)), before)
        self.assertEqual(sl.load_inbox(self.project), entries)
        with mock.patch.object(os, "replace", side_effect=OSError("no")):
            with self.assertRaises(OSError):
                sl.write_inbox_replace(self.project, entries)
        self.assertEqual(set(os.listdir(folder)), before)

    def test_d5_the_real_command_rewrites_an_input_and_logs_one_event(self):
        cfg, empty, path = self._config_home(json.dumps({"protected_dirs": ["/tmp/zorbquux"]}))
        # a legacy record saved before R-1366 still holds the name: written straight into the inbox file, not through
        # append_inbox, which now writes the alias in its place
        legacy = {"id": "I-%d" % (len(sl.load_inbox(self.project)) + 1), "ts": sl.now(), "session": "s1", "source": "manual",
                  "ref": None, "hash": sl._norm_hash("talks about zorbquux stuff"), "text": "talks about zorbquux stuff"}
        sl.append_text(sl._inbox_path(self.project), json.dumps(legacy, ensure_ascii=False) + "\n")
        iid = sl.load_inbox(self.project)[-1]["id"]
        cli = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin", "bin", "budgit")
        r = subprocess.run([sys.executable, "-I", cli, "--project", self.project, "ledger", "retext", iid,
                            "--reason", "names"], capture_output=True, text=True,
                           env=dict(os.environ, HOME=empty, BUDGIT_HOME=cfg, STUDIO_HOME=cfg))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(sl._inbox_path(self.project), encoding="utf-8") as f:
            raw = f.read()
        self.assertIn("a protected project", raw)
        self.assertNotIn("zorbquux", raw)
        entry = [e for e in sl.load_inbox(self.project) if e["id"] == iid][0]
        self.assertEqual(entry["text"], "talks about a protected project stuff")
        events = [e for e in entry.get("events", []) if e.get("kind") == "retext"]
        for it in sl.load_ledger(self.project)["items"]:
            events += [e for e in it.get("events", []) if e.get("kind") == "retext"]
        self.assertEqual(len(events), 1)
        self.assertNotIn("zorbquux", json.dumps(events))
        self.assertFalse(sl.ledger_tampered(self.project))
        self.assertIn("old copies may remain", r.stdout)
