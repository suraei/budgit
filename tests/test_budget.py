import json, os, stat, subprocess, sys, time, unittest
from helpers import StudioTestCase, ROOT, without_time_line
import studiolib as sl
from test_context_nudge import usage_line

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
STATUSLINE = os.path.join(ROOT, "plugin", "scripts", "statusline.py")
CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
DAY = 86400
NOW = 1_800_000_000.0


def limits(week_used, week_elapsed, five_used=10.0):
    """A snapshot `week_elapsed` (0..1) of the way through the weekly window."""
    return {"seven_day": {"used": week_used, "resets_at": NOW + (1 - week_elapsed) * 7 * DAY},
            "five_hour": {"used": five_used, "resets_at": NOW + 3600}}


class ClassifyTest(StudioTestCase):
    """Level = how fast she is burning her limit relative to the time left — never a fixed number."""

    def level(self, lim, prev=None):
        import budget
        return budget.classify(lim, prev=prev, now=NOW)[0]

    def test_pace_decides_not_the_raw_percentage(self):
        self.assertEqual(self.level(limits(40, 0.9)), "ample")      # 40% used, 90% of the week gone
        self.assertEqual(self.level(limits(45, 0.5)), "normal")
        self.assertEqual(self.level(limits(63, 0.49)), "tight")     # today's real situation
        self.assertEqual(self.level(limits(80, 0.3)), "critical")   # 50 points ahead of pace

    def test_absolute_ceilings_apply_whatever_the_pace(self):
        self.assertEqual(self.level(limits(92, 0.99)), "critical")
        self.assertEqual(self.level(limits(40, 0.9, five_used=93)), "critical")
        self.assertEqual(self.level(limits(40, 0.9, five_used=75)), "tight")

    def test_a_window_already_reset_counts_as_unused(self):
        lim = limits(95, 0.5)
        lim["seven_day"]["resets_at"] = NOW - 10     # the snapshot is from before the reset
        self.assertEqual(self.level(lim), "normal")

    def test_no_data_means_normal_and_says_so(self):
        import budget
        level, why = budget.classify({}, now=NOW)
        self.assertEqual(level, "normal")
        self.assertIn("no budget data", why)

    def test_hysteresis_stops_flapping_near_a_boundary(self):
        # pace +8: below the tight threshold (+10) but within 5 points of it -> stays tight if it was tight
        lim = limits(58, 0.5)
        self.assertEqual(self.level(lim, prev="normal"), "normal")
        self.assertEqual(self.level(lim, prev="tight"), "tight")
        self.assertEqual(self.level(limits(54, 0.5), prev="tight"), "normal")   # +4: clearly relaxed

    def test_a_reason_is_always_given(self):
        import budget
        self.assertIn("63%", budget.classify(limits(63, 0.49), now=NOW)[1])


class PolicyTest(StudioTestCase):
    def test_every_level_has_every_knob_and_a_one_line_summary(self):
        import budget
        for lv in budget.LEVELS:
            p = budget.policy(lv)
            for k in ("subagents", "agent_calls", "research", "checkpoint_at", "defer", "other_llm", "brain", "models"):
                self.assertIn(k, p)
            self.assertIn("\n", budget.policy_text(lv).strip() + "\n")

    def test_stricter_levels_never_spend_more(self):
        import budget
        ps = [budget.policy(lv) for lv in budget.LEVELS]
        self.assertEqual([p["subagents"] for p in ps], sorted((p["subagents"] for p in ps), reverse=True))
        self.assertEqual([p["agent_calls"] for p in ps], sorted((p["agent_calls"] for p in ps), reverse=True))
        self.assertEqual([p["checkpoint_at"] for p in ps], sorted((p["checkpoint_at"] for p in ps), reverse=True))
        self.assertEqual(budget.policy("critical")["subagents"], 0)

    def test_agent_models_get_cheaper_as_the_budget_tightens(self):
        import budget
        rank = {"opus": 3, "sonnet": 2, "haiku": 1, "none": 0}
        for role in ("judgement", "work", "mechanical"):
            r = [rank[budget.policy(lv)["models"][role]] for lv in budget.LEVELS]
            self.assertEqual(r, sorted(r, reverse=True), role)

    def test_opus_is_the_brain_until_the_budget_is_critical(self):
        import budget
        self.assertEqual([budget.policy(lv)["brain"] for lv in budget.LEVELS], ["opus", "opus", "opus", "sonnet"])
        self.assertEqual(budget.policy("critical")["models"]["work"], "none")

    def test_text_tells_the_brain_to_pass_a_model_on_every_agent_call_and_how_to_escalate(self):
        import budget
        for lv in budget.LEVELS:
            t = budget.policy_text(lv)
            self.assertIn("model=", t)
            self.assertIn("escalate", t.lower())
            self.assertIn(budget.policy(lv)["models"]["judgement"], t)

    def test_the_text_always_restates_what_never_changes(self):
        import budget
        for lv in budget.LEVELS:
            t = budget.policy_text(lv).lower()
            for must in ("ledger", "phase gates", "verification", "progress.md", "scope"):
                self.assertIn(must, t)


class StateTest(StudioTestCase):
    def test_override_beats_limits_and_auto_returns_control(self):
        import budget
        budget.record(seven_day=(10, NOW + 3 * DAY), five_hour=(5, NOW + 3600), now=NOW)
        self.assertEqual(budget.current(now=NOW)["level"], "ample")
        budget.set_override("tight")
        cur = budget.current(now=NOW)
        self.assertEqual((cur["level"], cur["source"]), ("tight", "override"))
        budget.clear_override()
        self.assertEqual(budget.current(now=NOW)["source"], "limits")

    def _tight_with_extra(self, enabled):
        import budget
        budget.record(seven_day=(63, NOW + (1 - 0.49) * 7 * DAY), five_hour=(10, NOW + 3600), extra=(100.28, 101), now=NOW,
                      extra_enabled=enabled)
        cur = budget.current(now=NOW)
        self.assertEqual(cur["level"], "tight")
        return cur["reason"]

    def test_r330_real_money_is_claimed_only_when_extra_usage_is_known_to_be_enabled(self):
        reason = self._tight_with_extra(True)
        self.assertIn("extra usage $100.28 of $101 this month", reason)
        self.assertIn("real money once the plan limit is hit", reason)

    def test_r330_extra_usage_that_is_off_is_not_mentioned_at_all(self):
        reason = self._tight_with_extra(False)
        self.assertNotIn("extra usage", reason)
        self.assertNotIn("real money", reason)

    def test_r330_without_the_flag_it_never_claims_real_money_and_says_if_enabled(self):
        reason = self._tight_with_extra(None)
        self.assertNotIn("real money once the plan limit is hit", reason)
        self.assertIn("if extra usage is enabled", reason)

    def test_r330_the_cli_records_the_flag(self):
        import budget
        r = subprocess.run([sys.executable, CLI, "budget", "record", "--seven-day", "63", "--seven-day-resets", str(int(time.time() + 3 * DAY)),
                            "--extra-spent", "5", "--extra-limit", "50", "--extra-enabled", "no"], env=dict(os.environ, STUDIO_HOME=self.home), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIs(budget.load()["extra"]["enabled"], False)

    def test_the_override_writers_take_the_lock(self):   # security re-review of R-212: L3
        import budget, contextlib
        taken = []

        @contextlib.contextmanager
        def spy():
            taken.append(1)
            yield
        real, budget._locked = budget._locked, spy
        try:
            budget.set_override("tight")
            budget.clear_override()
        finally:
            budget._locked = real
        self.assertEqual(len(taken), 2)

    def test_readings_that_are_not_finite_are_dropped(self):   # security re-review of R-212: L4
        import budget
        budget.record(seven_day=(10, float("-inf")), five_hour=(5, float("nan")), extra=(float("inf"), 50), now=NOW)
        data = budget.load()
        self.assertFalse(data.get("limits"))
        self.assertNotIn("extra", data)

    def test_a_lagging_session_cannot_lower_a_window_it_shares(self):
        # R-212 #4: every session wrote its own snapshot to one file, last writer won (45, 34, 45, 38, 45 in 5 minutes)
        import budget
        resets = NOW + 3 * DAY
        for used in (45, 34, 45, 38):
            budget.record(seven_day=(used, resets), five_hour=(12, NOW + 3600), now=NOW)
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 45)
        budget.record(seven_day=(52, resets + 30), now=NOW)                       # same window, higher: taken
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 52)

    def test_r1309_a_reset_with_the_same_reset_time_is_taken_not_hidden_by_the_max(self):
        import budget
        resets = NOW + 3 * DAY
        budget.record(seven_day=(83, resets), now=NOW)
        self.assertIn(budget.current(now=NOW)["level"], ("tight", "critical"))
        budget.record(seven_day=(2, resets), now=NOW + 60)
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 2.0)
        self.assertNotIn(budget.current(now=NOW + 60)["level"], ("tight", "critical"))

    def test_r1309_a_reset_with_a_later_reset_time_is_taken(self):
        import budget
        budget.record(seven_day=(83, NOW + 3 * DAY), now=NOW)
        budget.record(seven_day=(2, NOW + 3 * DAY + 2 * 3600), now=NOW + 60)
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 2.0)

    def test_r1309_a_small_lag_drop_still_keeps_the_max(self):
        import budget
        resets = NOW + 3 * DAY
        budget.record(seven_day=(45, resets), now=NOW)
        budget.record(seven_day=(34, resets), now=NOW + 60)
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 45.0)
        budget.record(seven_day=(12, resets), now=NOW + 120)      # R-1309: a 33-point drop is far below, not lag: taken
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 12.0)
        budget.record(seven_day=(9, resets), now=NOW + 180)       # 3 points down: lag, the max keeps 12
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 12.0)

    def _weekly(self, *steps):
        import budget
        resets, out = NOW + 3 * DAY, []
        for i, used in enumerate(steps):
            budget.record(seven_day=(used, resets), now=NOW + 60 * i)
            out.append(budget.load()["limits"]["seven_day"]["used"])
        return out

    def test_r1309_a_lagging_session_cannot_send_a_reset_back_up(self):
        self.assertEqual(self._weekly(83, 2, 80), [83.0, 2.0, 2.0])

    def test_r1309_after_a_reset_the_real_growth_still_counts_while_lag_is_ignored(self):
        self.assertEqual(self._weekly(83, 2, 80, 5), [83.0, 2.0, 2.0, 5.0])
        self.assertEqual(self._weekly(83, 2, 80, 5, 80, 3)[-2:], [5.0, 5.0])

    def test_r1309_a_reset_from_35_to_6_is_taken(self):
        self.assertEqual(self._weekly(35, 6), [35.0, 6.0])

    def test_r1309_growth_after_a_reset_rises(self):
        self.assertEqual(self._weekly(83, 2, 5, 9), [83.0, 2.0, 5.0, 9.0])

    def test_r1309_45_then_12_is_a_drop_far_below_and_is_taken(self):
        self.assertEqual(self._weekly(45, 12), [45.0, 12.0])

    def test_r1309_the_reset_marker_survives_the_snapshot_file(self):
        import budget
        self._weekly(83, 2)
        win = budget.load()["limits"]["seven_day"]
        self.assertEqual((win["reset_from"], win["reset_at"]), (83.0, NOW + 60))
        self._weekly(80)                                       # a fresh process reads the marker back from the file
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 2.0)

    def _hand_edit(self, **fields):
        import budget
        data = budget.load()
        for k, v in fields.items():
            if k.startswith("win_"):
                if v is KeyError:
                    data["limits"]["seven_day"].pop(k[4:], None)
                else:
                    data["limits"]["seven_day"][k[4:]] = v
            else:
                data[k] = v
        budget.save(data)

    def test_r1309_a_hand_edited_reset_marker_or_seen_at_never_raises(self):
        import budget
        for fields in ({"win_reset_at": "soon"}, {"win_reset_from": KeyError}, {"win_reset_at": True},
                       {"win_reset_from": float("nan")}, {"seen_at": "yesterday"}):
            self._weekly(83, 2)
            self._hand_edit(**fields)
            budget.record(seven_day=(80, NOW + 3 * DAY), now=NOW + 120)
            self.assertIn(budget.current(now=NOW + 130)["level"], budget.LEVELS, fields)

    def test_r1309_the_stale_notice_cuts_a_long_source_to_one_line_of_40_characters(self):
        import budget
        budget.record(seven_day=(20, NOW + 3 * DAY), now=NOW)
        self._hand_edit(source="ab\ncd\x1b[31m" + "x" * 200)
        reason = budget.current(now=NOW + 2 * 3600)["reason"]
        tail = reason.split("(from ", 1)[1].rstrip(")")
        self.assertLessEqual(len(tail), 40)
        self.assertTrue(tail.startswith("ab cd"))
        self.assertTrue(all(c.isprintable() for c in reason))

    def test_r1309_an_old_snapshot_says_how_old_it_is_and_where_it_came_from(self):
        import budget
        budget.record(seven_day=(20, NOW + 3 * DAY), now=NOW)
        data = budget.load()
        data["source"] = "statusline"
        budget.save(data)
        fresh = budget.current(now=NOW + 60)["reason"]
        self.assertNotIn("stale", fresh)
        old = budget.current(now=NOW + 2 * 3600)["reason"]
        self.assertIn("stale", old)
        self.assertIn("120 min old", old)
        self.assertIn("statusline", old)

    def test_a_newer_window_replaces_and_an_older_one_is_ignored(self):
        import budget
        budget.record(seven_day=(80, NOW + DAY), now=NOW)
        budget.record(seven_day=(10, NOW + 8 * DAY), now=NOW + 2 * DAY)           # after the reset: a fresh window
        self.assertEqual(budget.load()["limits"]["seven_day"], {"used": 10.0, "resets_at": NOW + 8 * DAY})
        budget.record(seven_day=(90, NOW + DAY), now=NOW + 2 * DAY)               # a session still on the old window
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 10.0)

    def test_a_stored_value_older_than_half_an_hour_gives_way_to_a_fresh_one(self):
        import budget
        budget.record(seven_day=(60, NOW + DAY), now=NOW)
        budget.record(seven_day=(41, NOW + DAY), now=NOW + 1900)                  # nobody has reported for 30+ minutes
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 41.0)

    def test_only_accepted_readings_keep_a_value_fresh(self):
        # security review: a stream of ignored lagging readings must not keep a bad stored value alive
        import budget
        budget.record(seven_day=(60, NOW + DAY), now=NOW)
        budget.record(seven_day=(41, NOW + DAY), now=NOW + 1000)                  # ignored, so it refreshes nothing
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 60.0)
        budget.record(seven_day=(41, NOW + DAY), now=NOW + 1900)                  # 1900 s since the last ACCEPTED reading
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 41.0)

    def test_readings_outside_what_a_window_can_be_are_dropped(self):
        import budget
        budget.record(seven_day=(500, NOW + DAY), five_hour=(-3, NOW + 3600), now=NOW)
        budget.record(seven_day=(50, NOW + 400 * DAY), five_hour=(50, NOW + 3 * DAY), now=NOW)
        self.assertEqual(budget.load().get("limits", {}), {})
        budget.record(seven_day=(50, NOW + 7 * DAY), five_hour=(50, NOW + 5 * 3600), now=NOW)
        self.assertEqual(set(budget.load()["limits"]), {"seven_day", "five_hour"})

    def test_what_the_user_types_overrides_the_merge(self):
        import budget
        budget.record(seven_day=(60, NOW + DAY), now=NOW)
        budget.record(seven_day=(41, NOW + DAY), now=NOW, authoritative=True)     # `budgit budget record`
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 41.0)

    def test_snapshots_from_several_sessions_do_not_flip_the_level(self):
        import budget
        flips, last = 0, None
        for i in range(40):                                  # two sessions: a fresh view (72 %) and a lagging one (60 %)
            used = 72 if i % 2 == 0 else 60
            budget.record(seven_day=(used, NOW + 0.4 * 7 * DAY), five_hour=(10, NOW + 3600), now=NOW)
            level = budget.current(now=NOW)["level"]
            flips += last is not None and level != last
            last = level
        self.assertEqual(flips, 0)

    def test_parallel_writers_end_on_the_highest_value(self):
        import budget
        code = ("import sys; sys.path.insert(0, %r); import budget; budget.record(seven_day=(float(sys.argv[1]), %r), now=%r)"
                % (os.path.join(ROOT, "plugin", "scripts"), NOW + 3 * DAY, NOW))
        procs = [subprocess.Popen([sys.executable, "-c", code, str(u)], env=dict(os.environ, STUDIO_HOME=self.home)) for u in (41, 58, 44, 46, 49, 50, 57, 53)]      # R-1309: all within 20 points of each other (a bigger drop is taken)
        for p in procs:
            self.assertEqual(p.wait(), 0)
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 58.0)

    def test_paid_extra_usage_is_shown_in_the_reason(self):
        import budget
        budget.record(seven_day=(100, NOW + DAY), five_hour=(3, NOW + 3600), extra=(14.15, 40.0), now=NOW)
        cur = budget.current(now=NOW)
        self.assertEqual(cur["level"], "critical")
        self.assertIn("extra usage $14.15 of $40", cur["reason"])

    def test_bad_level_is_rejected(self):
        import budget
        with self.assertRaises(sl.StudioError):
            budget.set_override("galactic")

    def test_state_file_is_private_and_corruption_is_survivable(self):
        import budget
        budget.record(seven_day=(10, NOW + DAY), now=NOW)
        path = os.path.join(sl.home_state(), "budget.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        open(path, "w").write("{not json")
        self.assertEqual(budget.current(now=NOW)["level"], "normal")

    def test_a_symlinked_state_file_is_never_followed(self):
        import budget
        os.makedirs(sl.home_state())
        victim = os.path.join(self.tmp, "victim"); open(victim, "w").write("keep")
        os.symlink(victim, os.path.join(sl.home_state(), "budget.json"))
        budget.record(seven_day=(10, NOW + DAY), now=NOW)
        self.assertEqual(open(victim).read(), "keep")

    def test_checkpoint_moves_earlier_as_the_budget_tightens(self):
        import budget
        self.assertGreater(budget.policy("ample")["checkpoint_at"], budget.policy("normal")["checkpoint_at"])
        self.assertLess(budget.policy("critical")["checkpoint_at"], budget.policy("normal")["checkpoint_at"])


class SurfaceTest(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.env = dict(os.environ, STUDIO_HOME=self.home, STUDIO_COMPACT_WINDOW="300000")

    def run_hook(self, event, payload):
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(payload), env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else {}

    def snapshot(self, used, elapsed, authoritative=False):
        t = time.time()
        import budget
        budget.record(seven_day=(used, t + (1 - elapsed) * 7 * DAY), five_hour=(10, t + 3600), now=t,
                      authoritative=authoritative)

    def prompt(self, sid="u1", ctx=1000):
        path = os.path.join(self.tmp, "t.jsonl")
        open(path, "w").write(usage_line(ctx + 1002) + "\n")
        return without_time_line(self.run_hook("user-prompt-submit", {"session_id": sid, "prompt": "x", "transcript_path": path}))   # R-1159: the time line is not under test here

    def test_a_change_of_level_is_announced_once_per_session_to_her_and_to_the_model(self):
        self.snapshot(63, 0.49)
        out = self.prompt()
        self.assertIn("AJUSTADO", out["systemMessage"].upper())
        self.assertIn("tight", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.prompt(), {})                      # same level: silent
        self.snapshot(10, 0.9, authoritative=True)        # a lower reading only replaces the stored one when the user records it
        self.assertIn("HOLGADO", self.prompt()["systemMessage"].upper())

    def test_normal_level_is_silent_in_a_fresh_session(self):
        self.snapshot(45, 0.5)
        self.assertEqual(self.prompt(), {})

    def test_checkpoint_fires_earlier_when_tight(self):
        sl.activate(self.project, "b1")
        self.snapshot(63, 0.49)                                  # tight -> checkpoint at ~0.55 of 300k
        path = os.path.join(self.tmp, "t2.jsonl")
        open(path, "w").write(usage_line(175000) + "\n")         # 58% of the window
        out = self.run_hook("user-prompt-submit", {"session_id": "b1", "prompt": "x", "transcript_path": path})
        self.assertIn("checkpoint", out["hookSpecificOutput"]["additionalContext"])

    def test_session_start_in_a_project_carries_the_current_policy(self):
        sl.activate(self.project, "b1")
        self.snapshot(63, 0.49)
        out = self.run_hook("session-start", {"session_id": "b1", "source": "startup"})
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("budgit budget", ctx)
        self.assertIn("tight", ctx)

    def test_every_session_start_carries_the_model_rules_even_without_a_studio_project(self):
        self.snapshot(45, 0.5)
        out = self.run_hook("session-start", {"session_id": "plain1", "source": "startup"})
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("budgit budget", ctx)
        self.assertIn("model=", ctx)

    def test_statusline_records_limits_and_prints_a_compact_line(self):
        t = int(time.time())
        payload = {"context_window": {"used_percentage": 31.2},
                   "rate_limits": {"five_hour": {"used_percentage": 28, "resets_at": t + 3600},
                                   "seven_day": {"used_percentage": 63, "resets_at": t + 3 * DAY}}}
        r = subprocess.run([sys.executable, STATUSLINE], input=json.dumps(payload), env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("63%", r.stdout)
        import budget
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 63)

    def test_r525_statusline_keeps_a_weekly_with_no_reset_time_and_no_five_hour(self):
        # R-522/R-525: Ultra has no five_hour window and resets_at is optional; the weekly figure must still be recorded and shown
        payload = {"rate_limits": {"seven_day": {"used_percentage": 37}}}
        r = subprocess.run([sys.executable, STATUSLINE], input=json.dumps(payload), env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("wk 37%", r.stdout)
        self.assertNotIn("5h", r.stdout)
        import budget
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 37)

    def test_statusline_survives_missing_fields_and_garbage(self):
        for raw in ('{"context_window": {"used_percentage": 5}}', "not json", ""):
            r = subprocess.run([sys.executable, STATUSLINE], input=raw, env=self.env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_cli_show_set_auto_record(self):
        def cli(*a):
            r = subprocess.run([sys.executable, CLI, "budget", *a], env=self.env, capture_output=True, text=True)
            return r.returncode, r.stdout + r.stderr
        t = int(time.time())
        rc, out = cli("record", "--seven-day", "63", "--seven-day-resets", str(t + 3.5 * DAY),
                      "--five-hour", "20", "--five-hour-resets", str(t + 3600))
        self.assertEqual(rc, 0, out)
        rc, out = cli()
        self.assertIn("tight", out)
        rc, out = cli("set", "ample")
        self.assertIn("ample", cli()[1])
        self.assertIn("override", cli()[1])
        rc, out = cli("auto")
        self.assertIn("tight", cli()[1])
        self.assertNotEqual(cli("set", "galactic")[0], 0)


def _budget():
    import budget
    return budget


class WeekOutlookTest(unittest.TestCase):
    """Her 2026-10-21 catch: 'vas bien' on a Wednesday at 37 % while the pace ran out on Sunday."""
    WEEK = 7 * 86400

    def test_runs_out_before_the_reset_at_the_current_rate(self):
        now = 1_000_000_000.0
        resets = now + 0.68 * self.WEEK          # 32 % of the week elapsed
        o = _budget().week_outlook({"seven_day": {"used": 37.0, "resets_at": resets}}, now=now)
        self.assertGreater(o["projected"], 100)
        self.assertIsNotNone(o["runs_out"])
        self.assertLess(o["runs_out"], resets)

    def test_reaches_the_reset_when_under_pace(self):
        now = 1_000_000_000.0
        o = _budget().week_outlook({"seven_day": {"used": 20.0, "resets_at": now + 0.5 * self.WEEK}}, now=now)
        self.assertIsNone(o["runs_out"])
        self.assertAlmostEqual(o["projected"], 40.0, places=3)

    def test_no_projection_early_in_the_week_or_without_live_data(self):
        now = 1_000_000_000.0
        early = {"seven_day": {"used": 5.0, "resets_at": now + 0.95 * self.WEEK}}
        self.assertIsNone(_budget().week_outlook(early, now=now)["projected"])
        self.assertIsNone(_budget().week_outlook({}, now=now))
        stale = {"seven_day": {"used": 50.0, "resets_at": now - 10}}
        self.assertIsNone(_budget().week_outlook(stale, now=now))
