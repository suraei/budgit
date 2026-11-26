"""R-86 `studio retro-mine` (R-88..R-91): her corrections mined from transcripts. Synthetic fixtures only."""
import json, os, subprocess, sys, time

from helpers import StudioTestCase, ROOT

CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


def human(text, ts="2026-10-19T10:00:00Z", **extra):
    rec = {"type": "user", "timestamp": ts, "sessionId": "s1", "origin": {"kind": "human"},
           "message": {"role": "user", "content": text}}
    rec.update(extra)
    return rec


def assistant(blocks, ts="2026-10-19T10:00:05Z", model="claude-opus-5-5", mid="m1"):
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": mid, "model": model, "content": blocks}}


def tool(name, inp=None, tid="t1"):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp or {}}


def read(path):
    with open(path) as f:
        return f.read()


class RetroMineBase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "projects")
        self.dir = os.path.join(self.root, "-Users-x-proj")
        os.makedirs(self.dir)

    def session(self, sid, recs, d=None):
        d = d or self.dir
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, sid + ".jsonl")
        with open(path, "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        return path

    def run_cli(self, *args, cwd=None):
        env = dict(os.environ, STUDIO_HOME=self.home)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True, cwd=cwd or self.project,
                              env=env)


class PromptsTest(RetroMineBase):
    def texts(self, recs):
        import retromine
        return [p["text"] for p in retromine.prompts(self.session("s1", recs))]

    def test_human_string(self):
        self.assertEqual(self.texts([human("hazlo de nuevo")]), ["hazlo de nuevo"])

    def test_human_list_keeps_text_blocks_only(self):
        rec = human([{"type": "text", "text": "mira esta captura"}, {"type": "image", "source": {"data": "xx"}}])
        self.assertEqual(self.texts([rec]), ["mira esta captura"])

    def test_missing_origin_counts_unless_wrapper(self):
        plain = {"type": "user", "timestamp": "2026-10-19T10:00:00Z", "message": {"content": "texto viejo"}}
        wrapped = {"type": "user", "timestamp": "2026-10-19T10:01:00Z",
                   "message": {"content": "<local-command-stdout>ok</local-command-stdout>"}}
        self.assertEqual(self.texts([plain, wrapped]), ["texto viejo"])

    def test_peer_and_meta_excluded(self):
        peer = {"type": "user", "isMeta": True, "origin": {"kind": "peer", "from": "a1"},
                "message": {"content": "mensaje de un agente"}}
        meta = {"type": "user", "isMeta": True, "message": {"content": "caveat"}}
        self.assertEqual(self.texts([peer, meta]), [])

    def test_task_notification_excluded_with_and_without_origin(self):
        a = {"type": "user", "origin": {"kind": "task-notification"}, "message": {"content": "<task-notification>x"}}
        b = {"type": "user", "message": {"content": "<task-notification>y</task-notification>"}}
        c = {"type": "user", "message": {"content": "<side-sessions-event>z</side-sessions-event>"}}
        self.assertEqual(self.texts([a, b, c]), [])

    def test_compact_summary_excluded(self):
        rec = {"type": "user", "isCompactSummary": True, "message": {"content": "This session is being continued"}}
        self.assertEqual(self.texts([rec]), [])

    def test_command_wrapper_excluded(self):
        recs = [{"type": "user", "message": {"content": "<command-name>/impeccable</command-name>"}},
                {"type": "user", "message": {"content": "<command-message>impeccable</command-message>"}},
                {"type": "user", "message": {"content": "<bash-input>ls</bash-input>"}}]
        self.assertEqual(self.texts(recs), [])

    def test_programmatic_sdk_runs_excluded(self):
        sdk = human("Review this change", entrypoint="sdk-py", userType="external")
        sdk_no_origin = {"type": "user", "entrypoint": "sdk-py", "message": {"content": "auto prompt"}}
        turn = {"type": "user", "turnOrigin": "sdk", "message": {"content": "auto turn"}}
        desktop = human("lo escribio ella", entrypoint="claude-desktop", promptSource="sdk", turnOrigin="human")
        self.assertEqual(self.texts([sdk, sdk_no_origin, turn, desktop]), ["lo escribio ella"])

    def test_tool_results_and_sidechain_excluded(self):
        res = {"type": "user", "toolUseResult": {"x": 1},
               "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "out"}]}}
        side = human("de un subagente", isSidechain=True)
        self.assertEqual(self.texts([res, side]), [])


class DiscoveryTest(RetroMineBase):
    def test_listed_dir_skipped_and_cutoff(self):
        import comply
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump({"exclude_dirs": ["ctf"]}, f)
        self.session("a", [human("uno")])
        self.session("b", [human("dos")], d=os.path.join(self.root, "-Users-x-ctf-box"))
        old = self.session("old", [human("viejo")])
        t = time.time() - 40 * 86400
        os.utime(old, (t, t))
        got = [os.path.basename(p) for p, _ in comply.sessions(self.root, 7)]
        self.assertEqual(got, ["a.jsonl"])

    def test_project_binding_filters(self):
        import comply
        other = os.path.join(self.root, "-Users-x-other")
        self.session("a", [human("uno")])
        self.session("b", [human("dos")], d=other)
        want = os.path.join(self.tmp, "x", "proj")
        os.makedirs(want)
        d = os.path.join(self.root, comply.claude_dir_name(want))
        self.session("c", [human("tres")], d=d)
        got = [os.path.basename(p) for p, _ in comply.sessions(self.root, 7, project=want)]
        self.assertEqual(got, ["c.jsonl"])


class CliTest(RetroMineBase):
    def git_project(self):
        subprocess.run(["git", "init", "-q", self.project], check=True)
        self.init_project()

    def test_cli_writes_private_gitignored_report(self):
        self.git_project()
        self.session("a", [human("uno"), human("dos", ts="2026-10-19T10:30:00Z")])
        r = self.run_cli("retro-mine", "--root", self.root, "--days", "36500")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = os.path.join(self.project, ".studio", "private", "retro-mine-%s.md" % time.strftime("%Y-%m-%d"))
        self.assertTrue(os.path.isfile(out), r.stdout)
        text = read(out)
        self.assertIn("-Users-x-proj", text)
        self.assertNotIn("uno", text)  # counts only in the skeleton: no prompt text
        ign = subprocess.run(["git", "check-ignore", "-q", os.path.relpath(out, self.project)], cwd=self.project)
        self.assertEqual(ign.returncode, 0)
        self.assertEqual(read(os.path.join(self.project, ".gitignore")).count(".studio/private/"), 1)
        again = self.run_cli("retro-mine", "--root", self.root, "--days", "36500")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(read(os.path.join(self.project, ".gitignore")).count(".studio/private/"), 1)

    def test_cli_out_override(self):
        self.git_project()
        self.session("a", [human("uno")])
        out = os.path.join(self.tmp, "o.md")
        gi = os.path.join(self.project, ".gitignore")
        before = read(gi) if os.path.exists(gi) else None       # the ledger writer (card) may have made it already
        r = self.run_cli("retro-mine", "--root", self.root, "--days", "36500", "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(out))
        self.assertEqual(read(gi) if os.path.exists(gi) else None, before)


class ClassifyTest(RetroMineBase):
    def cats(self, text):
        import retromine
        return retromine.classify(text)

    def test_each_category(self):
        self.assertIn("design", self.cats("no me gusta esta pagina morada, los tonos estan feos"))
        self.assertIn("redo", self.cats("hazlo de nuevo con otra estructura, desde cero"))
        self.assertIn("bug", self.cats("la descarga no funciona y la ventana se rompe"))
        self.assertIn("clarity", self.cats("no entiendo el grafico lateral, explicalo mas simple"))
        self.assertIn("should-have", self.cats("ya te lo dije el martes, deberias haberlo anotado"))

    def test_english(self):
        self.assertIn("design", self.cats("this looks ugly, the spacing is off"))
        self.assertIn("redo", self.cats("please start over, do it again"))
        self.assertIn("bug", self.cats("it is broken, the app is not working"))
        self.assertIn("clarity", self.cats("this is confusing, I don't understand"))
        self.assertIn("should-have", self.cats("I already told you, you should have checked"))

    def test_two_categories_one_message(self):
        got = self.cats("ese tono morado no me gusta, hazlo de nuevo")
        self.assertIn("design", got)
        self.assertIn("redo", got)

    def test_accents_optional_and_case(self):
        self.assertEqual(self.cats("Ya te lo dije"), self.cats("YA TE LO DIJE"))
        self.assertIn("should-have", self.cats("ya te lo expliqué"))
        self.assertIn("should-have", self.cats("ya te lo explique"))
        self.assertIn("clarity", self.cats("no lo entendí, el cuadro está confuso"))
        self.assertIn("clarity", self.cats("no lo entendi, el cuadro esta confuso"))

    def test_review_asks_and_pastes_are_not_corrections(self):
        self.assertEqual(self.cats("vuelve a mirar cada seccion y devuelveme un resumen"), [])
        self.assertEqual(self.cats("desde hoy contamos con otra herramienta de diseno para avanzar rapido"), [])
        self.assertEqual(self.cats("adjunto el texto: " + "lorem " * 60 + "la pantalla no funciona"), [])
        self.assertEqual(self.cats("el resumen cita un error de red en el paso dos"), [])

    def test_conditional_mentions_are_not_corrections(self):
        self.assertEqual(self.cats("ejecuta el script para comprobar lo que no funciona y anotalo"), [])
        self.assertEqual(self.cats("si no funciona la descarga, dime"), [])
        self.assertEqual(self.cats("evita ambiguedades y confusiones en los textos"), [])

    def test_neutral_message_has_no_category(self):
        self.assertEqual(self.cats("pasa a la tarea numero cuatro"), [])
        self.assertEqual(self.cats("perfecto, gracias"), [])


class SnippetTest(RetroMineBase):
    def test_token_straddling_the_cut_is_redacted(self):
        import retromine
        token = "sk-" + "A1b2C3d4" * 5
        text = "x" * 150 + " " + token + " y mas texto"
        snip = retromine.snippet(text)
        self.assertNotIn("sk-", snip)
        self.assertNotIn("A1b2C3d4", snip)
        self.assertLessEqual(len(snip), 161)

    def test_short_text_whitespace_collapsed(self):
        import retromine
        self.assertEqual(retromine.snippet("hola \n  mundo"), "hola mundo")


class ReportTest(RetroMineBase):
    def git_project(self):
        subprocess.run(["git", "init", "-q", self.project], check=True)
        self.init_project()

    def test_report_has_category_table_and_recent_snippets(self):
        self.git_project()
        self.session("abc12345", [human("no me gusta nada", ts="2026-10-19T10:00:00Z"),
                                  human("hazlo de nuevo", ts="2026-10-19T11:00:00Z"),
                                  human("sigue", ts="2026-10-19T12:00:00Z")])
        r = self.run_cli("retro-mine", "--root", self.root, "--days", "36500")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = read(os.path.join(self.project, ".studio", "private", "retro-mine-%s.md" % time.strftime("%Y-%m-%d")))
        self.assertIn("## Corrections per category", text)
        self.assertIn("| design |", text)
        self.assertIn("no me gusta nada", text)
        self.assertIn("abc12345", text)
        self.assertIn("2026-10-19", text)
        self.assertNotIn("sigue", text)  # a neutral prompt is counted, never quoted


def slash(name, ts="2026-10-19T09:59:00Z"):
    return {"type": "user", "timestamp": ts, "message": {"content": "<command-message>%s</command-message>\n"
                                                         "<command-name>/%s</command-name>" % (name, name)}}


class BeforeTest(RetroMineBase):
    def before(self, recs, n=-1):
        import retromine
        return list(retromine.prompts(self.session("s1", recs)))[n]["before"]

    def test_window_is_since_her_previous_prompt(self):
        recs = [human("primero", ts="2026-10-19T10:00:00Z"),
                assistant([tool("Skill", {"skill": "impeccable"}, "t1")], mid="m1"),
                human("segundo", ts="2026-10-19T10:10:00Z"),
                assistant([tool("Read", {}, "t2"), tool("Bash", {}, "t3")], mid="m2", model="claude-sonnet-5-5"),
                human("tercero", ts="2026-10-19T10:20:00Z")]
        b = self.before(recs)
        self.assertEqual(b["skills"], [])
        self.assertEqual(b["tools"], 2)
        self.assertEqual(b["model"], "claude-sonnet-5-5")
        self.assertEqual(self.before(recs, 1)["skills"], ["impeccable"])

    def test_skill_tool_and_slash_command_both_count(self):
        via_tool = [assistant([tool("Skill", {"skill": "design:design-critique"})]), human("no me gusta")]
        via_slash = [slash("impeccable"), assistant([tool("Read")]), human("no me gusta")]
        self.assertEqual(self.before(via_tool)["skills"], ["design-critique"])
        self.assertEqual(self.before(via_slash)["skills"], ["impeccable"])

    def test_agent_and_task_names_both_count(self):
        recs = [assistant([tool("Agent", {"subagent_type": "studio:verifier", "model": "opus"}, "ta"),
                           tool("Task", {"subagent_type": "Explore", "model": "haiku"}, "tb"),
                           tool("Agent", {"prompt": "x"}, "tc")]), human("sigue")]
        self.assertEqual(self.before(recs)["agents"],
                         [("studio:verifier", "opus"), ("Explore", "haiku"), ("general-purpose", "")])


class DesignGapTest(RetroMineBase):
    def gap(self, recs):
        import retromine
        self.session("s1", recs)
        return retromine.design_gaps(retromine.mine(self.root, 36500))

    def test_no_skill_before_is_flagged(self):
        got = self.gap([assistant([tool("Read")]), human("no me gusta este tablero morado")])
        self.assertEqual(len(got), 1)

    def test_impeccable_via_skill_not_flagged(self):
        self.assertEqual(self.gap([assistant([tool("Skill", {"skill": "impeccable"})]),
                                   human("no me gusta este tablero morado")]), [])

    def test_impeccable_via_slash_command_not_flagged(self):
        self.assertEqual(self.gap([slash("impeccable"), human("no me gusta este tablero morado")]), [])

    def test_other_skill_does_not_excuse(self):
        self.assertEqual(len(self.gap([assistant([tool("Skill", {"skill": "superpowers:brainstorming"})]),
                                       human("no me gusta este tablero morado")])), 1)

    def test_non_design_correction_never_flagged(self):
        self.assertEqual(self.gap([human("hazlo de nuevo")]), [])


ASK = "quiero que el boton de exportar guarde el informe dentro de la carpeta privada del proyecto"


class ResumedCopyTest(RetroMineBase):
    def test_resumed_session_copies_count_once(self):
        import retromine
        old = [human(ASK, ts="2026-10-18T10:00:00Z"), human("segundo mensaje distinto", ts="2026-10-18T11:00:00Z")]
        self.session("s1", old)
        self.session("s2", old + [human("tercer mensaje nuevo", ts="2026-10-19T09:00:00Z")])   # resumed: history copied
        mined = retromine.mine(self.root, 36500)
        self.assertEqual(sum(len(r["prompts"]) for r in mined.values()), 3)
        got = retromine.repeats(mined)
        self.assertEqual((got["same_session"], got["cross_session"]), ([], []))


class RepeatTest(RetroMineBase):
    def rep(self, *sessions):
        import retromine
        for sid, recs in sessions:
            self.session(sid, recs)
        return retromine.repeats(retromine.mine(self.root, 36500))

    def test_true_repeat_in_session_over_ten_minutes(self):
        got = self.rep(("s1", [human(ASK, ts="2026-10-19T10:00:00Z"),
                               human(ASK + " por favor", ts="2026-10-19T10:30:00Z")]))
        self.assertEqual(len(got["same_session"]), 1)
        self.assertEqual(got["cross_session"], [])

    def test_within_ten_minutes_is_not_said_twice(self):
        got = self.rep(("s1", [human(ASK, ts="2026-10-19T10:00:00Z"), human(ASK, ts="2026-10-19T10:05:00Z")]))
        self.assertEqual(got["same_session"], [])

    def test_near_miss_not_a_repeat(self):
        other = "necesito que la pagina de ajustes muestre el idioma elegido y recuerde la zona horaria siempre"
        got = self.rep(("s1", [human(ASK, ts="2026-10-19T10:00:00Z"), human(other, ts="2026-10-19T11:00:00Z")]))
        self.assertEqual(got["same_session"], [])

    def test_short_prompts_ignored(self):
        got = self.rep(("s1", [human("hazlo otra vez", ts="2026-10-19T10:00:00Z"),
                               human("hazlo otra vez", ts="2026-10-19T11:00:00Z")]))
        self.assertEqual(got["same_session"], [])

    def test_cross_session_listed_apart(self):
        got = self.rep(("s1", [human(ASK, ts="2026-10-18T10:00:00Z")]),
                       ("s2", [human(ASK, ts="2026-10-19T10:00:00Z")]))
        self.assertEqual(got["same_session"], [])
        self.assertEqual(len(got["cross_session"]), 1)

    def test_unreadable_timestamp_is_not_said_twice(self):
        got = self.rep(("s1", [human(ASK, ts="2026-10-19T10:00:00Z"), human(ASK, ts="garbage")]))
        self.assertEqual(got["same_session"], [])

    def test_prefix_filter_matches_brute_force(self):
        import random, retromine
        rng = random.Random(3)
        vocab = ["t%d" % i for i in range(40)]
        sets = [set(rng.sample(vocab, rng.randint(6, 14))) for _ in range(60)]
        for _ in range(90):   # near-copies: swap a few tokens of an existing set
            base = set(rng.choice(sets))
            for t in rng.sample(sorted(base), rng.randint(0, 3)):
                base.discard(t)
                base.add(rng.choice(vocab))
            sets.append(base)
        items = [(frozenset(x), i) for i, x in enumerate(sets)]
        want = {(a, b) for a in range(len(items)) for b in range(a + 1, len(items))
                if len(items[a][0] & items[b][0]) / len(items[a][0] | items[b][0]) >= retromine.REPEAT_JACCARD}
        self.assertTrue(want)
        self.assertEqual(set(retromine._pairs(items)), want)

    def test_report_lists_repeats(self):
        import retromine
        self.session("s1", [human(ASK, ts="2026-10-19T10:00:00Z"), human(ASK, ts="2026-10-19T10:30:00Z")])
        text = retromine.render(retromine.mine(self.root, 36500), 36500)
        self.assertIn("## Said twice", text)
        self.assertIn("same session: 1", text)


if __name__ == "__main__":
    import unittest
    unittest.main()
