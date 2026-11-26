"""R-114 step 2: Brote's state from the weekly projection, `budgit status --brote`, and the plain (no emoji) status line."""
import json
import os
import re
import statistics
import subprocess
import sys
import time
import unittest

from helpers import StudioTestCase, ROOT, fastest_child
import budget
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
WEEK = 7 * 86400
EMOJI = re.compile("[\U0001f000-\U0001faff\ufe0f\u200d]")   # pictographs and the emoji variation selector; U+273F and the like are text glyphs


def limits(used, day, h5=None, now=None):
    """Weekly window `day` days into the week at `used` %, optional 5-hour window at h5 %."""
    now = now or time.time()
    out = {"seven_day": {"used": used, "resets_at": now + (7 - day) * 86400}}
    if h5 is not None:
        out["five_hour"] = {"used": h5, "resets_at": now + 3600}
    return out


class BroteStateTest(StudioTestCase):
    NOW = 1_800_000_000

    def state(self, used, day, h5=None):
        return budget.brote_state(limits(used, day, h5, now=self.NOW), now=self.NOW)["state"]

    def test_the_seven_states_from_the_projection(self):
        # projection = used / day * 7
        self.assertEqual(self.state(10, 3), "radiante")      # 23 %
        self.assertEqual(self.state(30, 3), "radiante")      # 70 % exactly: not above 70
        self.assertEqual(self.state(31, 3), "contento")      # 72 %
        self.assertEqual(self.state(38, 3), "contento")      # 88.7 %
        self.assertEqual(self.state(39, 3), "justo")         # 91 %
        self.assertEqual(self.state(42, 3), "justo")         # 98 %
        self.assertEqual(self.state(44, 3), "cansado")       # 102.7 %
        self.assertEqual(self.state(51, 3), "cansado")       # 119 %
        self.assertEqual(self.state(53, 3), "agotado")
        self.assertEqual(self.state(60, 3), "agotado")       # 140 % exactly: not above 140
        self.assertEqual(self.state(61, 3), "enfermo")       # 142 %

    def test_precedence_quota_gone_then_sick_then_the_table(self):
        self.assertEqual(self.state(90, 6.9), "enfermo")             # used >= 90 beats a gentle projection
        self.assertEqual(self.state(100, 6), "dormido")
        self.assertEqual(self.state(20, 3, h5=100), "dormido")       # the 5-hour window is gone too
        self.assertEqual(self.state(20, 3, h5=95), "radiante")       # the 5 h only adds an overlay below 100

    def test_quota_precedence_holds_without_a_projection(self):
        self.assertEqual(self.state(95, 0.5), "enfermo")             # used >= 90 needs no projection
        self.assertEqual(self.state(80, 0.5), "contento")
        only_5h = {"five_hour": {"used": 100, "resets_at": self.NOW + 3600}}
        self.assertEqual(budget.brote_state(only_5h, now=self.NOW)["state"], "dormido")   # 5 h gone, no weekly snapshot

    def test_no_projection_in_the_first_tenth_of_the_week_and_no_snapshot(self):
        self.assertEqual(self.state(5, 0.5), "contento")
        r = budget.brote_state({}, now=self.NOW)
        self.assertEqual((r["state"], r["fresh"]), ("unknown", False))
        stale = {"seven_day": {"used": 50, "resets_at": self.NOW - 10}}
        self.assertEqual(budget.brote_state(stale, now=self.NOW)["state"], "unknown")

    def test_r330_early_in_the_week_a_noisy_projection_never_reaches_the_strong_states(self):
        # her real case: 24 % used 17 % into the week is 141 % linear, and read as "enfermo"
        self.assertEqual(self.state(24, 7 * 0.17), "cansado")
        self.assertEqual(self.state(45, 2.0), "cansado")             # 157 %
        self.assertEqual(self.state(80, 2.4), "cansado")             # 233 %, still the first third of the week
        self.assertEqual(self.state(24, 7 * 0.17, h5=60), "cansado") # the 5 h overlay does not change it

    def test_r330_the_strong_states_come_back_once_a_third_of_the_week_is_gone(self):
        self.assertEqual(self.state(45, 2.6), "agotado")             # 121 %, 37 % of the week
        self.assertEqual(self.state(60, 2.6), "enfermo")             # 162 %
        self.assertEqual(self.state(61, 3), "enfermo")               # unchanged late in the week

    def test_r330_real_exhaustion_is_not_gated(self):
        self.assertEqual(self.state(90, 1.0), "enfermo")             # used >= 90, any day
        self.assertEqual(self.state(100, 1.0), "dormido")
        self.assertEqual(self.state(30, 1.0, h5=100), "dormido")


class BroteCliTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        sl.activate(self.project, "s1")

    def cli(self, *args, session="s1", cwd=None):
        env = dict(os.environ, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        if session:
            env["CLAUDE_CODE_SESSION_ID"] = session
        return subprocess.run([sys.executable, CLI, *args, "--project", cwd or self.project], env=env,
                              capture_output=True, text=True)

    def items(self, *specs, stale=()):
        data = sl.load_ledger(self.project)
        for n, spec in enumerate(specs, 1):
            st, _, phase = spec.partition("/")
            upd = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - (9 * 86400 if n in stale else 0)))
            data["items"].append({"id": "R-%d" % n, "src": [], "text": "tarea %d" % n, "status": st, "evidence": "e" if st == "done" else "",
                                  "phase": phase or "build", "created": upd, "updated": upd})
        with sl.locked(self.project):
            sl.save_ledger(self.project, data)

    def record(self, used, day, h5=None):
        args = ["budget", "record", "--seven-day", str(used), "--seven-day-resets", str(time.time() + (7 - day) * 86400)]
        if h5 is not None:
            args += ["--five-hour", str(h5), "--five-hour-resets", str(time.time() + 3600)]
        self.assertEqual(self.cli(*args).returncode, 0)

    def test_r1104_five_hour_off_brote_h5_reads_zero_so_no_suffix(self):
        self.items("doing")
        self.record(40, 3, h5=75)
        self.assertEqual(json.loads(self.cli("status", "--brote").stdout)["h5"], 75)      # auto: unchanged
        self.assertEqual(self.cli("five-hour", "off").returncode, 0)
        self.assertEqual(json.loads(self.cli("status", "--brote").stdout)["h5"], 0)

    def test_brote_json_has_everything_the_module_draws(self):
        self.items("doing", "open", "open/later", "done")
        self.record(40, 3, h5=75)
        r = self.cli("status", "--brote")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual((out["version"], out["state"], out["fresh"], out["h5"]), (2, "justo", True, 75))
        self.assertEqual(out["counts"], {"doing": 1, "next": 1, "later": 1, "done": 1})
        self.assertEqual([i["id"] for i in out["doing"]], ["R-1"])
        self.assertEqual(out["doing"][0]["state"], "unclaimed")
        self.assertEqual([i["id"] for i in out["queue"]], ["R-2"])
        self.assertEqual([i["id"] for i in out["later"]], ["R-3"])
        self.assertEqual((out["stale"], out["idle"]), ([], []))

    def test_brote_json_without_a_snapshot_is_unknown_and_empty_outside_a_project(self):
        self.items("open")
        self.assertEqual(json.loads(self.cli("status", "--brote").stdout)["state"], "unknown")
        r = self.cli("status", "--brote", session=None, cwd=self.tmp)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_stale_doing_items_are_listed_in_the_feed_not_in_the_line(self):
        # R-206: the final line has no stalled count (the Libreta ribbon and Brote's gesture carry it); the feed still lists them
        self.items("doing", "doing", "doing", stale=(1, 2))
        out = json.loads(self.cli("status", "--brote").stdout)
        self.assertEqual(sorted(out["stale"]), ["R-1", "R-2"])
        self.assertNotIn("estancada", self.cli("status", "--line").stdout)

    def test_the_line_has_no_emoji_starts_with_the_project_and_fits_80_columns(self):
        # R-283 S2b (her D6): the line starts with the project's name, not the product's
        self.items(*(["doing"] * 12 + ["open"] * 58), stale=(1, 2, 3))
        self.record(100, 6.5, h5=100)
        line = self.cli("status", "--line").stdout.rstrip("\n")
        name = os.path.basename(os.path.realpath(self.project))
        self.assertRegex(line, "^" + re.escape(name) + " \u00b7 (tareas: )?\u273f 12 en curso \u2058 58 por hacer")
        self.assertIsNone(EMOJI.search(line), line)
        self.assertLessEqual(len(line), 80, line)
        self.assertRegex(line, r"\u250a 5h \u25b0{6} 100 % \u250a sem \u25b0{6} 100 %$")   # R-878: both gauges; the forecast and the reset time gave way

    def test_the_ansi_flag_colours_the_same_line_and_the_default_stays_plain(self):
        self.items("doing", "open")
        self.record(40, 3)
        plain = self.cli("status", "--line").stdout.rstrip("\n")
        self.assertNotIn("\x1b", plain)
        coloured = self.cli("status", "--line", "--ansi").stdout.rstrip("\n")
        self.assertIn("\x1b[38;2;158;211;106m", coloured)
        self.assertEqual(re.sub("\x1b\\[[0-9;]*m", "", coloured), plain)

    def test_the_extras_flag_brings_back_the_stalled_count_and_the_5h_window(self):
        self.items("doing", "doing", stale=(1,))
        self.record(40, 3, h5=85)
        self.assertNotIn("estancada", self.cli("status", "--line").stdout)
        line = self.cli("status", "--line", "--extras").stdout.rstrip("\n")
        self.assertTrue(line.endswith("\u00b7 1 estancada"), line)
        self.assertIn("5h \u25b0\u25b0\u25b0\u25b0\u25b0\u25b1 85 %", line)   # R-878: the gauge, not the old text
        self.assertNotIn("5 h al", line)

    def test_the_tail_names_the_run_out_day_and_hour_in_local_time(self):
        self.items("doing")
        self.record(90, 3)                                  # 90 % after 3 of 7 days: out well before the reset
        line = self.cli("status", "--line").stdout.rstrip("\n")
        m = re.search(r"fin (\w{3}) (\d{2})h$", line)
        self.assertIsNotNone(m, line)
        out = budget.week_outlook(limits(90, 3), now=time.time())["runs_out"]
        tm = time.localtime(out)
        self.assertEqual((m.group(1), int(m.group(2))), (["lun", "mar", "mié", "jue", "vie", "sáb", "dom"][tm.tm_wday], tm.tm_hour))

    def test_the_tail_says_llegas_when_the_forecast_reaches_the_reset(self):
        self.items("doing")
        self.record(20, 3)
        self.assertTrue(self.cli("status", "--line").stdout.rstrip("\n").endswith("\u00b7 llegas"))

    def test_status_line_and_json_answer_fast(self):
        self.items(*(["open"] * 60))
        self.record(30, 3)
        # same-run baseline (timing rule): the child CPU of the same CLI doing nothing (`--version`), measured now here
        base = fastest_child(lambda: self.cli("--version"), 0.0, runs=5)
        limit = base + 0.10                                 # status may cost at most 0.10 s of CPU over a bare CLI start
        for args in (("status", "--line"), ("status", "--brote")):
            self.assertLess(fastest_child(lambda: self.cli(*args), limit, runs=10), limit, (args, base))


SIT = dict(doing=0, next=0, later=0, week=37, forecast="ok", out_day="dom", out_hour=23)
# R-206, the final pick "Barrita y dia" (docs/design/2026-10-23-status-line-final.html): exact strings, each <= 80 columns
LINE_CASES = [
    (dict(doing=6, next=67, later=39, week=63, forecast="out"),
     "budgit  \u273f 6 en curso \u2058 67 por hacer \u2727 39 ideas \u250a \u25b0\u25b0\u25b0\u25b0\u25b1\u25b1 63 % \u00b7 fin dom 23h"),
    (dict(doing=6, next=67, later=39, week=31),
     "budgit  \u273f 6 en curso \u2058 67 por hacer \u2727 39 ideas \u250a \u25b0\u25b0\u25b1\u25b1\u25b1\u25b1 31 % \u00b7 llegas"),
    (dict(doing=1, next=1, later=1, week=8, forecast=None),       # the first 10 % of the week has no forecast: no tail
     "budgit  \u273f 1 en curso \u2058 1 por hacer \u2727 1 idea \u250a \u25b1\u25b1\u25b1\u25b1\u25b1\u25b1 8 %"),
    (dict(doing=0, next=5, later=0, week=None),                   # no live snapshot: no bar, no tail
     "budgit  \u273f 0 en curso \u2058 5 por hacer \u2727 0 ideas"),
    (dict(week=None), "budgit  nada pendiente"),
    (dict(week=31), "budgit  nada pendiente \u250a \u25b0\u25b0\u25b1\u25b1\u25b1\u25b1 31 % \u00b7 llegas"),
    (dict(doing=2, next=3, later=4, week=100, forecast="out", out_day="lun", out_hour=9),
     "budgit  \u273f 2 en curso \u2058 3 por hacer \u2727 4 ideas \u250a \u25b0\u25b0\u25b0\u25b0\u25b0\u25b0 100 % \u00b7 fin lun 09h"),
    (dict(doing=12, next=144, later=139, week=100, forecast="out", out_day="mi\u00e9", out_hour=23),
     "budgit  \u273f 12 en curso \u2058 144 por hacer \u2727 139 ideas \u250a \u25b0\u25b0\u25b0\u25b0\u25b0\u25b0 100 % \u00b7 fin mi\u00e9 23h"),
]
ANSI = re.compile("\x1b\\[[0-9;]*m")


class StatusLineTest(unittest.TestCase):
    """R-206: the pure line builder, so the cases need no ledger and no clock."""

    def test_the_cases_match_exactly_and_fit_80_columns(self):
        for over, want in LINE_CASES:
            got = sl.status_line(dict(SIT, **over), "es")
            self.assertEqual(got, want)
            self.assertLessEqual(len(got), 80, got)

    def test_english_has_the_same_shape_and_fits_too(self):
        got = sl.status_line(dict(SIT, doing=6, next=67, later=39, week=63, forecast="out", out_day="Sun"), "en")
        self.assertEqual(got, "budgit  \u273f 6 in progress \u2058 67 to do \u2727 39 ideas \u250a \u25b0\u25b0\u25b0\u25b0\u25b1\u25b1 63 % \u00b7 ends Sun 23h")
        self.assertTrue(sl.status_line(dict(SIT, doing=6, week=31), "en").endswith("31 % \u00b7 you make it"))
        for over, _want in LINE_CASES:
            line = sl.status_line(dict(SIT, **dict(over, out_day="Wed")), "en")
            self.assertLessEqual(len(line), 80, line)
            self.assertIsNone(EMOJI.search(line), line)

    def test_the_bar_is_six_cells_rounded_from_the_used_share(self):
        for used, filled in ((0, 0), (8, 0), (9, 1), (31, 2), (50, 3), (63, 4), (92, 6), (100, 6), (130, 6)):
            line = sl.status_line(dict(SIT, doing=1, week=used, forecast=None), "es")
            self.assertIn("\u25b0" * filled + "\u25b1" * (6 - filled) + " ", line, (used, line))

    def test_extras_are_off_by_default_and_append_the_stalled_count_and_the_5h_window_when_asked(self):
        # R-209 (her pick, "for now"): the final line drops both; the switch keeps them for when she revisits
        sit = dict(SIT, doing=6, next=67, later=39, week=63, forecast="out", stale=2, h5=85)
        base = sl.status_line(dict(sit, stale=0, h5=0), "es")
        self.assertEqual(sl.status_line(sit, "es"), base)                                   # default: neither
        # R-878: the "5 h al N %" text is gone for good; the colour of the 5h gauge carries it (tests/test_r878_5h_gauge.py)
        self.assertEqual(sl.status_line(sit, "es", extras=True), base + " \u00b7 2 estancadas")
        self.assertEqual(sl.status_line(dict(sit, stale=1, h5=69), "es", extras=True), base + " \u00b7 1 estancada")
        self.assertEqual(sl.status_line(dict(sit, stale=0, h5=70), "es", extras=True), base)
        self.assertEqual(sl.status_line(dict(sit, stale=0, h5=0), "es", extras=True), base)
        self.assertTrue(sl.status_line(sit, "en", extras=True).endswith("\u00b7 2 stalled"))
        coloured = sl.status_line(sit, "es", ansi=True, extras=True)
        self.assertEqual(ANSI.sub("", coloured), sl.status_line(sit, "es", extras=True))

    def test_the_symbols_are_text_not_emoji(self):
        line = sl.status_line(dict(SIT, doing=6, next=67, later=39, week=63, forecast="out"), "es")
        self.assertIsNone(EMOJI.search(line), line)             # no pictograph and no emoji variation selector
        self.assertNotIn("\ufe0f", line)

    def test_the_ansi_variant_colours_each_segment_and_strips_to_the_plain_line(self):
        for over, plain in LINE_CASES:
            got = sl.status_line(dict(SIT, **over), "es", ansi=True)
            self.assertEqual(ANSI.sub("", got), plain)
            self.assertLessEqual(len(ANSI.sub("", got)), 80)
            self.assertTrue(got.endswith("\x1b[0m") or "\x1b" not in got, got)
        got = sl.status_line(dict(SIT, doing=6, next=67, later=39, week=63, forecast="out"), "es", ansi=True)
        rgb = lambda h: "\x1b[38;2;%d;%d;%dm" % (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        self.assertIn(rgb("9ed36a") + "\u273f 6 en curso\x1b[0m", got)        # green
        self.assertIn(rgb("f2a7c3") + "\u2058 67 por hacer\x1b[0m", got)      # pink
        self.assertIn(rgb("c9b6ff") + "\u2727 39 ideas\x1b[0m", got)          # lilac
        self.assertIn(rgb("7cc4ff") + "\u25b0\u25b0\u25b0\u25b0\x1b[0m", got)    # the bar is sky blue, never the ideas lilac
        self.assertIn(rgb("7cc4ff") + "63 %\x1b[0m", got)
        self.assertIn(rgb("ff8f70") + "fin dom 23h\x1b[0m", got)               # coral
        self.assertIn(rgb("9ed36a") + "llegas\x1b[0m", sl.status_line(dict(SIT, doing=6, week=31), "es", ansi=True))
        self.assertNotIn("\x1b", sl.status_line(dict(SIT, doing=6, week=31), "es"))   # plain stays plain


if __name__ == "__main__":
    import unittest
    unittest.main()
