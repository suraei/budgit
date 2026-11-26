import json, os, subprocess, sys
from helpers import StudioTestCase, ROOT
import studiolib as sl

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


class CliTest(StudioTestCase):
    def run_cli(self, *args, session="sess-1", cwd=None):
        env = dict(os.environ, BUDGIT_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        if session:
            env["CLAUDE_CODE_SESSION_ID"] = session
        return subprocess.run([sys.executable, CLI] + list(args), cwd=cwd or self.project,
                              env=env, capture_output=True, text=True)

    def test_activate_scaffolds_and_binds_session(self):
        r = self.run_cli("activate", self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        for name in ("ledger.json", "ledger.md", "project.json", "progress.md", "retro.md"):
            self.assertTrue(os.path.isfile(os.path.join(sl.studio_dir(self.project), name)), name)
        self.assertEqual(sl.project_for_session("sess-1"), os.path.realpath(self.project))
        gi = open(os.path.join(self.project, ".gitignore"), encoding="utf-8").read()
        self.assertIn(".budgit/inbox.jsonl", gi)
        self.assertNotIn("sess-1", sl.load_state(self.project)["since"])  # fresh project: full history
        self.assertIn("budgit ledger add --src", r.stdout)
        self.assertIn("captured when this turn ends", r.stdout)

    def test_activate_keeps_the_users_words_out_of_git_unless_asked(self):
        # R-212 #2/#6: the ledger holds the user's requests word for word and full session ids; reports quote paths
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        self.assertEqual(self.run_cli("activate", self.project).returncode, 0)
        d = os.path.relpath(sl.studio_dir(self.project), self.project)
        for rel in ("ledger.json", "ledger.md", "ledger.sha256", "progress.md", "retro.md", "project.json",
                    "reports/20261023-x.md", "friction.md", "ledger-v0.md", "verify/R-1.md"):
            r = subprocess.run(["git", "check-ignore", "-q", os.path.join(d, rel)], cwd=self.project)
            self.assertEqual(r.returncode, 0, rel)

    def test_track_ledger_in_project_json_keeps_the_ledger_trackable(self):
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        os.makedirs(sl.studio_dir(self.project))
        with open(os.path.join(sl.studio_dir(self.project), "project.json"), "w") as f:
            json.dump({"track_ledger": True}, f)
        self.assertEqual(self.run_cli("activate", self.project).returncode, 0)
        self.assertEqual(self.run_cli("activate", self.project).returncode, 0)      # and stays so on the next run
        d = os.path.relpath(sl.studio_dir(self.project), self.project)
        for rel in ("ledger.json", "progress.md", "project.json"):
            r = subprocess.run(["git", "check-ignore", "-q", os.path.join(d, rel)], cwd=self.project)
            self.assertEqual(r.returncode, 1, rel)
        r = subprocess.run(["git", "check-ignore", "-q", os.path.join(d, "inbox.jsonl")], cwd=self.project)
        self.assertEqual(r.returncode, 0)                  # machine-local files stay ignored either way

    def test_no_flag_abbreviation_is_accepted_anywhere(self):
        # R-212 #1: argparse used to take `prepare --app` as `--approve`, which the Bash guard did not recognise
        for argv in (("prepare", "--app"), ("prepare", "--approv"), ("status", "--li"), ("--proj", self.project, "status"),
                     ("ledger", "list", "--op")):
            r = self.run_cli(*argv)
            self.assertEqual(r.returncode, 2, (argv, r.stdout, r.stderr))
            self.assertIn("error", r.stderr, argv)
        self.assertFalse(os.path.exists(os.path.join(self.home, "approved_prepare.json")))

    def test_activate_without_session_fails_unless_no_bind(self):
        r = self.run_cli("activate", self.project, session=None)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--no-bind", r.stderr)
        r = self.run_cli("activate", self.project, "--no-bind", session="hub")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(sl.project_for_session("hub"))

    def test_activate_keeps_existing_files_and_archives_v0_ledger(self):
        os.makedirs(sl.studio_dir(self.project))
        with open(os.path.join(sl.studio_dir(self.project), "progress.md"), "w") as f:
            f.write("mine\n")
        with open(os.path.join(sl.studio_dir(self.project), "ledger.md"), "w") as f:
            f.write("| R0 | hand kept | open | |\n")
        self.run_cli("activate", self.project)
        sdir = sl.studio_dir(self.project)
        self.assertEqual(open(os.path.join(sdir, "progress.md")).read(), "mine\n")
        self.assertIn("hand kept", open(os.path.join(sdir, "ledger-v0.md")).read())
        self.assertIn("sess-1", sl.load_state(self.project)["since"])  # existing history: since-now

    def test_add_copies_text_from_inbox(self):
        self.run_cli("activate", self.project)
        sl.append_inbox(self.project, 'usa "comillas" y $HOME `tal`', "sess-1", "prompt")
        r = self.run_cli("ledger", "add", "--src", "I-1", "--phase", "validate", "--appetite", "small", "--area", "main")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("R-1", r.stdout)
        self.assertEqual(sl.load_ledger(self.project)["items"][0]["text"], 'usa "comillas" y $HOME `tal`')

    def test_ledger_flow_and_multi_ack(self):
        self.run_cli("activate", self.project)
        for t in ("haz la app", "sí", "continua"):
            sl.append_inbox(self.project, t, "sess-1", "prompt")
        self.run_cli("ledger", "add", "--src", "I-1", "--phase", "mvp", "--appetite", "small", "--area", "main")
        r = self.run_cli("ledger", "ack", "I-2", "I-3", "--note", "approvals, no new request")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sl.untracked(self.project, sl.load_ledger(self.project)), [])
        r = self.run_cli("ledger", "done", "R-1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("evidence", r.stderr)
        self.run_cli("ledger", "outcome", "R-1", "You see it closed")
        r = self.run_cli("ledger", "done", "R-1", "--evidence", "screenshot .budgit/shots/1.png abcdef1 test_screenshot")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_cli("ledger", "list", "--open")
        self.assertNotIn("R-1", r.stdout)
        self.assertFalse(sl.ledger_tampered(self.project))

    def test_project_flag_works_after_subcommand(self):
        self.run_cli("activate", self.project)
        r = self.run_cli("ledger", "list", "--project", self.project, session="other", cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_status_reports_binding(self):
        self.run_cli("activate", self.project)
        r = self.run_cli("status")
        self.assertIn("esta sesión: vinculada", r.stdout)
        sub = os.path.join(self.project, "src")
        os.makedirs(sub)
        r = self.run_cli("status", session="other", cwd=sub)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(os.path.realpath(self.project), r.stdout)
        self.assertIn("esta sesión: sin vincular", r.stdout)      # R-1160: the suite runs in Spanish

    def _deny_line(self, ts, deny_id):
        return ('%s guard-deny %s Bash raw-store sha256=%s cmd="x" why="y"\n' % (ts, deny_id, "0" * 64))

    def test_status_shows_guard_denies_of_the_last_7_days_with_the_command_to_read_them(self):
        self.run_cli("activate", self.project)
        log = os.path.join(sl.studio_dir(self.project), "audit.log")
        sl.append_text(log, self._deny_line(sl.now(), "aaaa1111") + self._deny_line(sl.now(), "bbbb2222")
                       + self._deny_line("2020-01-01T00:00:00.000Z", "cccc3333"))
        r = self.run_cli("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("guard denies (7d): 2 \u2014 budgit denies", r.stdout)

    def test_status_shows_nothing_about_denies_when_there_are_none(self):
        self.run_cli("activate", self.project)
        sl.append_text(os.path.join(sl.studio_dir(self.project), "audit.log"),
                       self._deny_line("2020-01-01T00:00:00.000Z", "cccc3333"))
        self.assertNotIn("guard denies", self.run_cli("status").stdout)

    def test_status_line_is_one_short_line(self):
        self.run_cli("activate", self.project)
        r = self.run_cli("status", "--line")
        self.assertEqual(r.returncode, 0, r.stderr)
        line = r.stdout.rstrip("\n")
        self.assertEqual(line, "proj \u00b7 sin tareas en Budgit")   # plain Spanish, no emoji (her 2026-10-21 decision); starts with the project (D6)
        self.assertLessEqual(len(line), 80)

    def _items(self, *statuses):
        """Each status is "open", "doing", ... or "open/later" for an idea parked for later."""
        self.run_cli("activate", self.project)
        data = sl.load_ledger(self.project)
        for n, spec in enumerate(statuses, 1):
            st, _, phase = spec.partition("/")
            data["items"].append({"id": "R-%d" % n, "src": [], "text": "t%d" % n, "status": st,
                                  "evidence": "e" if st == "done" else "", "phase": phase or "build",
                                  "created": "x"})
        with sl.locked(self.project):
            sl.save_ledger(self.project, data)

    def test_status_line_counts_doing_and_open_with_fresh_plan_limits(self):
        import time
        self._items("open", "doing", "doing", "done", "dropped", "open/later", "open/later")
        self.run_cli("budget", "record", "--seven-day", "5", "--seven-day-resets", str(time.time() + 3 * 86400),
                     "--five-hour", "0", "--five-hour-resets", str(time.time() + 3600))
        r = self.run_cli("status", "--line")
        self.assertEqual(r.returncode, 0, r.stderr)
        # R-878: Pro/Max has a five_hour window, so the 5h gauge joins the row and the ideas count and forecast give way (R-361 order)
        self.assertRegex(r.stdout.rstrip("\n"), r"^proj \u00b7 \u273f 2 en curso \u2058 1 por hacer \u250a 5h \u25b1{6} 0 % \u21bb \d\d:\d\d \u250a sem \u25b1{6} 5 %$")
        self.assertNotIn("5 h", r.stdout)

    def test_r1104_five_hour_off_hides_the_5h_gauge_and_reset_but_keeps_the_week(self):
        import time
        self._items("open", "doing")
        self.run_cli("budget", "record", "--seven-day", "5", "--seven-day-resets", str(time.time() + 3 * 86400),
                     "--five-hour", "40", "--five-hour-resets", str(time.time() + 3600))
        self.assertIn("5h ", self.run_cli("status", "--line").stdout)                    # auto: unchanged
        self.assertEqual(self.run_cli("five-hour", "off").returncode, 0)
        out = self.run_cli("status", "--line").stdout.rstrip("\n")
        self.assertNotIn("5h", out)
        self.assertNotIn("↻", out)
        self.assertNotIn("sem ", out)                                                    # no wk label, as when h5 is None
        self.assertRegex(out, r"[▰▱]{6} 5 %")                                     # the weekly gauge stays

    def test_status_line_hides_stale_plan_limits(self):
        # the first build showed a 7-hour-old snapshot as "0%/0%wk" while the plan was at 12 % / 10 %
        import time
        import budget
        self._items("doing")
        budget.record(seven_day=(10, time.time() + 3 * 86400), five_hour=(12, time.time() + 3600),
                      now=time.time() - 3600)
        r = self.run_cli("status", "--line")
        self.assertEqual(r.stdout.rstrip("\n"), "proj \u00b7 tareas: \u273f 1 en curso \u2058 0 por hacer \u2727 0 ideas")   # no fresh snapshot: no bar

    def test_status_line_records_live_limits_from_the_mod(self):
        import time
        import budget
        self._items("doing")
        live = [{"kind": "five_hour", "percentUsed": 12, "resetsAt": "2099-01-01T00:00:00Z"},
                {"kind": "seven_day", "percentUsed": 10.4, "resetsAt": int((time.time() + 86400) * 1000)},
                {"kind": "seven_day_opus", "percentUsed": 99, "resetsAt": None}]
        r = self.run_cli("status", "--line", "--limits-json", json.dumps(live))
        self.assertEqual(r.returncode, 0, r.stderr)
        # R-522: the unknown kind seven_day_opus (99 %) is tighter than the weekly: shown as a neutral sub-limit, never as the weekly
        # the sub-limit takes the room of the tasks word, the ideas count and the forecast
        self.assertEqual(r.stdout.rstrip("\n"), "proj \u00b7 \u273f 1 en curso \u2058 0 por hacer \u250a \u25b0\u25b1\u25b1\u25b1\u25b1\u25b1 10 % \u00b7 sub-l\u00edmite semanal 99 %")
        self.assertEqual(budget.load()["limits"]["seven_day"]["used"], 10.4)   # the team's pacing gets it too
        bad = self.run_cli("status", "--line", "--limits-json", "{not json")
        self.assertEqual((bad.returncode, bad.stdout), (0, r.stdout))

    def test_r522_a_weekly_with_no_reset_time_is_the_figure_shown_not_an_older_stored_one(self):
        import time
        import budget
        self._items("doing")
        budget.record(seven_day=(1, time.time() + 3 * 86400))             # an older reading: 1 %
        r = self.run_cli("status", "--line", "--colour", "off", "--limits-json", json.dumps([{"kind": "seven_day", "percentUsed": 37}]))
        self.assertIn("37 %", r.stdout)
        self.assertNotIn(" 1 %", r.stdout)
        self.assertNotIn("sub-l", r.stdout)

    def test_r522_a_weekly_alone_gives_a_sane_line_with_no_five_hour_window(self):
        import budget
        self._items("doing")
        self.run_cli("status", "--line", "--limits-json", json.dumps([{"kind": "seven_day", "percentUsed": 37}]))
        cur = budget.current()
        self.assertNotIn("no budget data", cur["reason"])
        self.assertIn("weekly 37%", cur["reason"])
        self.assertNotIn("5-hour window 0", cur["reason"])

    def test_r522_an_unknown_kind_tighter_than_the_weekly_shows_a_neutral_label_never_a_model_name(self):
        self._items("doing")
        rows = [{"kind": "seven_day", "percentUsed": 37}, {"kind": "seven_day_fable", "percentUsed": 62}]
        line = self.run_cli("status", "--line", "--colour", "off", "--limits-json", json.dumps(rows)).stdout
        self.assertIn("37 %", line)
        self.assertIn("sub-l\u00edmite semanal 62 %", line)
        self.assertNotIn("able", line.lower().replace("estable", ""))
        with open(os.path.join(self.home, "limit-kinds.json"), encoding="utf-8") as f:     # kind names and counts only
            self.assertEqual(json.load(f), {"seven_day_fable": 1})

    def test_r522_a_host_label_is_never_echoed_and_never_drops_the_sub_limit(self):
        # verify round 1: a host label naming a model was printed as is, and a long one made the sub-limit vanish
        self._items("doing")
        for label in ("Fable Opus 4", "x" * 24):
            rows = [{"kind": "seven_day", "percentUsed": 95}, {"kind": "seven_day_x", "percentUsed": 99, "label": label}]
            line = self.run_cli("status", "--line", "--colour", "off", "--limits-json", json.dumps(rows)).stdout
            self.assertIn("sub-límite semanal 99 %", line)
            self.assertNotIn(label.lower(), line.lower())
            self.assertNotIn("opus", line.lower())

    def test_r522_an_unknown_kind_looser_than_the_weekly_is_not_shown(self):
        self._items("doing")
        rows = [{"kind": "seven_day", "percentUsed": 37}, {"kind": "mystery_cap", "percentUsed": 20}]
        line = self.run_cli("status", "--line", "--colour", "off", "--limits-json", json.dumps(rows)).stdout
        self.assertIn("37 %", line)
        self.assertNotIn("sub-l", line)
        self.assertNotIn("20 %", line)

    def test_status_line_names_a_tight_budget(self):
        import time
        self._items("doing")
        self.run_cli("budget", "record", "--seven-day", "90", "--seven-day-resets", str(time.time() + 6 * 86400))
        r = self.run_cli("status", "--line")
        line = r.stdout.rstrip("\n")
        self.assertRegex(line, r"\u250a \u25b0{5}\u25b1 90 % \u00b7 fin \w{3} \d{2}h$")   # 90 % with 6 days left

    def test_r878_status_line_shows_the_5h_gauge_as_a_gauge_never_as_the_old_text(self):
        # R-878 (was R-206's "no 5 h segment"): the gauge shows with a five_hour window; the "5 h al N %" text is gone
        import time
        self._items("open", "open")
        self.run_cli("budget", "record", "--seven-day", "5", "--seven-day-resets", str(time.time() + 3 * 86400),
                     "--five-hour", "85", "--five-hour-resets", str(time.time() + 3600))
        line = self.run_cli("status", "--line").stdout.rstrip("\n")
        self.assertRegex(line, r"\u250a 5h \u25b0{5}\u25b1 85 % \u21bb \d\d:\d\d \u250a sem ")
        self.assertNotIn("5 h", line)
        self.assertLessEqual(len(line), 80)

    def test_r878_status_line_five_hour_from_the_host_without_resets_at_has_no_time(self):
        import json as _json
        self._items("open", "open")
        rows = [{"kind": "five_hour", "percentUsed": 30}, {"kind": "seven_day", "percentUsed": 37}]
        line = self.run_cli("status", "--line", "--colour", "off", "--limits-json", _json.dumps(rows)).stdout.rstrip("\n")
        self.assertIn("5h \u25b0\u25b0\u25b1\u25b1\u25b1\u25b1 30 % \u250a sem ", line)
        self.assertNotIn("\u21bb", line)

    def test_r878_ultra_has_no_gauge_in_the_cli_row(self):
        import json as _json
        self._items("open", "open")
        line = self.run_cli("status", "--line", "--colour", "off", "--limits-json", _json.dumps([{"kind": "seven_day", "percentUsed": 37}])).stdout
        self.assertNotIn("5h", line)
        self.assertNotIn("sem ", line)

    def test_status_line_outside_a_studio_project_is_the_account_part_only(self):    # R-238: the bar never vanishes
        r = self.run_cli("status", "--line", session="none", cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.rstrip("\n"), "budgit  \u25cc sin libreta aqu\u00ed")
        self.assertEqual(r.stderr, "")

    def test_r878_the_no_ledger_row_also_shows_the_five_hour_gauge(self):
        import json as _json
        rows = [{"kind": "five_hour", "percentUsed": 20}, {"kind": "seven_day", "percentUsed": 72}]
        r = self.run_cli("status", "--line", "--colour", "off", "--limits-json", _json.dumps(rows), session="none", cwd=self.tmp)
        line = r.stdout.rstrip("\n")
        self.assertIn("◌ sin libreta aquí ┊ 5h ▰▱▱▱▱▱ 20 % ┊ sem ", line)
        self.assertLessEqual(len(line), 80)

    def test_accept_edit_command(self):
        self.run_cli("activate", self.project)
        path = os.path.join(sl.studio_dir(self.project), "ledger.json")
        with open(path, "a") as f:
            f.write(" ")
        self.assertTrue(sl.ledger_tampered(self.project))
        r = self.run_cli("ledger", "accept-edit")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(sl.ledger_tampered(self.project))

    def test_import_md_command_uses_archived_v0_by_default(self):
        os.makedirs(sl.studio_dir(self.project))
        with open(os.path.join(sl.studio_dir(self.project), "ledger.md"), "w") as f:
            f.write("| ID | input | status | evidence |\n|---|---|---|---|\n| R3 | hand kept | open | |\n")
        self.run_cli("activate", self.project)
        r = self.run_cli("import-md")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("imported 1", r.stdout)
        self.assertEqual(sl.load_ledger(self.project)["items"][0]["id"], "R-3")

    def test_no_project_gives_clear_error(self):
        r = self.run_cli("status", session="none", cwd=self.tmp)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("budgit activate", r.stderr)
