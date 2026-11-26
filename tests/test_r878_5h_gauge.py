"""R-878 (I-1400, mock docs/design/2026-11-09-r865-5h-gauge.html): the 5-hour gauge on the bottom bar, Pro/Max only."""
import json
import re
import time
import unittest

import studiolib as sl

ANSI = re.compile("\x1b\\[[0-9;]*m")
RESET = 1766400000.0
HHMM = time.strftime("%H:%M", time.localtime(RESET))     # local time, so the test holds in any time zone
BASE = dict(doing=16, next=88, later=39, week=72, forecast="out", out_day="dom", out_hour=8)
RGB = lambda h: "\x1b[38;2;%d;%d;%dm" % (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
BLUE, AMBER, CORAL = RGB("7cc4ff"), RGB("f5c04a"), RGB("ff8f70")


def pro(h5, reset=RESET, **over):
    return dict(BASE, h5_pct=h5, h5_reset=reset, **over)


class FiveHourGaugeTest(unittest.TestCase):
    def test_pro_max_both_windows_at_20_75_98_percent_pick_the_colour_band(self):
        # replay of rate_limits {five_hour, seven_day} with the week at 72 %: blue below 70, amber from 70, coral from 90
        for used, colour, filled in ((20, BLUE, 1), (75, AMBER, 5), (98, CORAL, 6)):
            got = sl.status_line(pro(used), "es", ansi=True)
            self.assertIn(colour + "▰" * filled + "\x1b[0m", got, used)
            self.assertIn(colour + "%d %%\x1b[0m" % used, got, used)
            self.assertIn(BLUE + "72 %\x1b[0m", got)                     # the week stays blue
            plain = ANSI.sub("", got)
            self.assertIn("5h " + "▰" * filled + "▱" * (6 - filled) + " %d %% ↻ %s ┊ sem " % (used, HHMM), plain)
            self.assertLessEqual(len(plain), 80, plain)

    def test_the_band_edges_are_70_and_90(self):
        for used, colour in ((69, BLUE), (70, AMBER), (89, AMBER), (90, CORAL)):
            self.assertIn(colour + "%d %%\x1b[0m" % used, sl.status_line(pro(used, doing=1, next=1, later=1), "es", ansi=True), used)

    def test_ultra_with_no_five_hour_window_is_byte_identical_to_the_old_row(self):
        old = "budgit  ✿ 16 en curso ⁘ 88 por hacer ✧ 39 ideas ┊ ▰▰▰▰▱▱ 72 % · fin dom 08h"
        self.assertEqual(sl.status_line(dict(BASE), "es"), old)
        self.assertEqual(sl.status_line(dict(BASE, h5=0, h5_pct=None, h5_reset=None), "es"), old)
        self.assertEqual(sl.status_line(dict(BASE, h5=95), "es", extras=True), old)     # the legacy h5 key alone shows nothing
        self.assertNotIn("sem ", old)
        wait = sl.status_line(dict(BASE, waiting=2), "es")                              # R-361 order is unchanged
        self.assertEqual(wait, "budgit  te esperan: 2 · ✿ 16 en curso ⁘ 88 por hacer ┊ ▰▰▰▰▱▱ 72 % · fin dom 08h")

    def test_five_hour_without_resets_at_shows_the_gauge_and_no_time_and_never_crashes(self):
        for reset in (None, "garbage", float("nan"), 1e30):
            got = sl.status_line(pro(40, reset=reset), "es")
            self.assertIn("5h ▰▰▱▱▱▱ 40 % ┊ sem ", got, reset)
            if reset is None:
                self.assertNotIn("↻", got)
        self.assertNotIn("↻", sl.status_line(pro(40, reset=None), "es"))

    def test_the_80_column_give_way_order_ideas_then_forecast_then_reset_then_labels(self):
        seen = set()
        for lang in ("es", "en"):
            for wait in (0, 2, 12):
                for n in (1, 16, 160, 1600):
                    for fc in ("out", "ok"):
                        sit = pro(75, doing=n, next=n * 5, later=n * 2, forecast=fc, waiting=wait)
                        line = sl.status_line(sit, lang)
                        flags = ("✧" in line, "dom" in line or "llegas" in line or "you make" in line, "↻" in line, "5h " in line)
                        seen.add(flags)
                        self.assertEqual(list(flags), sorted(flags), (flags, line))   # what is gone is always a prefix of the order
                        self.assertIn("75 %", line)                                                  # the gauges themselves never give way
                        self.assertIn("72 %", line)
                        if n <= 160:                      # 4-digit counts cannot fit even with every piece gone
                            self.assertLessEqual(len(line), 80, line)
        for step in ((False, False, True, True),
                     (False, False, False, True), (False, False, False, False)):
            self.assertIn(step, seen, step)

    def test_each_step_is_exactly_the_mock_row(self):
        mk = lambda **o: sl.status_line(pro(75, waiting=2, **o), "es")
        full = mk()           # 2 waiting + everything: 98 columns -> ideas go first
        self.assertNotIn("ideas", full)
        self.assertEqual(full, "budgit  te esperan: 2 · ✿ 16 en curso ⁘ 88 por hacer ┊ ▰▰▰▰▰▱ 75 % ┊ ▰▰▰▰▱▱ 72 %")   # mock step 4: 80 columns
        self.assertEqual(len(full), 80)
        self.assertNotIn("fin dom", full)
        self.assertLessEqual(len(full), 80)

    def test_the_widest_english_row_fits_80_columns(self):
        sit = pro(75, doing=160, next=800, later=390, waiting=2)
        for lang in ("en", "es"):
            line = sl.status_line(sit, lang)
            self.assertLessEqual(len(line), 80, line)
            self.assertIn("75 %", line)
            self.assertIn("72 %", line)

    def test_the_no_project_row_carries_the_gauge(self):
        got = sl.status_line(dict(noproject=True, general=False, week=72, forecast="ok", h5_pct=20, h5_reset=RESET), "es")
        self.assertIn("◌ sin libreta aquí ┊ 5h ▰▱▱▱▱▱ 20 % ↻ " + HHMM + " ┊ sem ▰▰▰▰▱▱ 72 %", got)
        self.assertLessEqual(len(got), 80, got)

    def test_the_name_is_cut_before_anything_the_mock_calls_kept(self):
        line = sl.status_line(pro(75, name="a-rather-long-project-name", waiting=2, doing=1, next=5, later=3), "es")
        self.assertIn("75 %", line)
        self.assertIn("72 %", line)
        self.assertLessEqual(len(line), 80, line)
        self.assertIn("…", line)                                  # the name gave way, the gauges did not

    def test_spanish_and_english_strings(self):
        es = sl.status_line(pro(20, doing=1, next=1, later=1), "es")
        en = sl.status_line(pro(20, doing=1, next=1, later=1), "en")
        self.assertIn("5h ", es)
        self.assertIn(" sem ", es)
        self.assertIn("5h ", en)
        self.assertIn(" wk ", en)
        self.assertNotIn(" sem ", en)
        self.assertNotIn(" wk ", es)
        self.assertIn("↻ " + HHMM, en)
        self.assertEqual(ANSI.sub("", sl.status_line(pro(20, doing=1, next=1, later=1), "en", ansi=True)), en)   # ansi strips to plain
        import i18n
        for lang, wk in (("es", "sem"), ("en", "wk")):
            self.assertEqual((i18n.t("status.h5", lang), i18n.t("status.wk", lang)), ("5h", wk))

    def test_the_old_5h_al_n_text_is_gone_with_extras_on_and_off(self):
        for extras in (False, True):
            for lang in ("es", "en"):
                for used in (69, 70, 85, 100):
                    got = sl.status_line(dict(pro(used, stale=2, doing=1, next=1, later=1), h5=used), lang, extras=extras)
                    self.assertNotRegex(got, r"5 h (al|at) ", (extras, lang, used))
        stalled = sl.status_line(pro(85, stale=2, doing=1, next=1, later=1), "es", extras=True)
        self.assertTrue(stalled.endswith("· 2 estancadas"))       # the stalled count stays an extra, last

    def test_the_gauge_alone_when_there_is_no_weekly_figure(self):
        got = sl.status_line(pro(75, week=None, forecast=None, doing=1, next=1, later=1), "es")
        self.assertIn("5h ▰▰▰▰▰▱ 75 %", got)
        self.assertNotIn("sem", got)


if __name__ == "__main__":
    unittest.main()
