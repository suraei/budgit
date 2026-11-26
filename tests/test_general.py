"""R-373: the general notebook, the automatic humanize pass and the notebook offer. Every test builds its own temp user home
and Budgit home; no real folder is read, and the "protected" folders here are made up."""
import json
import os
import subprocess
import sys
import time

from helpers import StudioTestCase, ROOT, without_time_line
import general
import studiolib as sl

HOOK = os.path.join(ROOT, "plugin", "scripts", "hook.py")
CLI = os.path.join(ROOT, "plugin", "bin", "budgit")
SLOP = ("Honestly, this is a game-changer and it really works. We could always circle back and delve into the landscape, "
        "because it is literally the best thing for this team and we want to deliver it with the right care this week.")
CLEAN = ("Hola Marta, te escribo para confirmar la reunión del jueves a las diez en la oficina. Si no te encaja la hora, dime qué "
         "día te va mejor y lo movemos. Gracias por avisar con tiempo y nos vemos pronto en la sala pequeña del segundo piso.")


class GeneralBase(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.user = os.path.join(self.tmp, "user")                  # the user's home folder
        self.dev = os.path.join(self.user, "Documents", "Dev")
        os.makedirs(self.dev)
        self.env = dict(os.environ, HOME=self.user, STUDIO_HOME=self.home, BUDGIT_LANG="en")
        host = ("CLAUDE_CODE_REMOTE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ATTENDED")
        for k in ("BUDGIT_HOME", "CLAUDE_CODE_SESSION_ID") + host:
            self.env.pop(k, None)
        self._saved = {k: os.environ.get(k) for k in ("HOME", "BUDGIT_HOME") + host}
        os.environ["HOME"] = self.user
        for k in ("BUDGIT_HOME",) + host:
            os.environ.pop(k, None)
        os.makedirs(self.home, exist_ok=True)
        self.env["CLAUDE_CODE_ENTRYPOINT"] = os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"       # a person typing in a terminal

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def folder(self, *parts):
        path = os.path.join(self.user, *parts)
        os.makedirs(path, exist_ok=True)
        return path

    def config(self, **values):
        with open(os.path.join(self.home, "config.json"), "w") as f:
            json.dump(values, f)

    def hook(self, event, **payload):
        r = subprocess.run([sys.executable, HOOK, event], input=json.dumps(payload), env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout) if r.stdout.strip() else {}

    def context(self, out):
        return (out.get("hookSpecificOutput") or {}).get("additionalContext", "")

    def pointer(self, session):
        return sl.project_for_session(session)

    def cli(self, *args, lang="en"):
        env = dict(self.env, BUDGIT_LANG=lang)
        return subprocess.run([sys.executable, CLI, *args], env=env, cwd=self.tmp, capture_output=True, text=True)


class EligibilityTest(GeneralBase):
    def test_a_plain_folder_in_the_home_qualifies_and_so_does_the_home_itself(self):
        self.assertTrue(general.eligible(self.dev))
        self.assertTrue(general.eligible(self.user))

    def test_a_folder_with_a_notebook_or_inside_one_does_not(self):
        project = self.folder("Documents", "Dev", "app")
        sl.activate(project, "s0")
        self.assertFalse(general.eligible(project))
        self.assertFalse(general.eligible(self.folder("Documents", "Dev", "app", "src")))

    def test_a_protected_dir_never_binds(self):
        secret = self.folder("Documents", "Cuentas")
        self.config(protected_dirs=[secret])
        self.assertFalse(general.eligible(secret))
        self.assertFalse(general.eligible(self.folder("Documents", "Cuentas", "2026")))
        self.assertTrue(general.eligible(self.folder("Documents", "Otra")))

    def test_a_protected_dir_with_an_accent_is_found_in_either_unicode_form(self):
        import unicodedata
        name = "Cuentas ñandú café"
        secret = self.folder("Documents", unicodedata.normalize("NFC", name))
        self.config(protected_dirs=[secret])
        for form in ("NFC", "NFD"):
            other = os.path.join(self.user, "Documents", unicodedata.normalize(form, name), "2026")
            os.makedirs(other, exist_ok=True)
            self.assertFalse(general.eligible(other), form)
            self.assertFalse(general.eligible(os.path.join(self.user, "Documents", unicodedata.normalize(form, name))), form)
        self.config(protected_dirs=[os.path.join(self.user, "Documents", unicodedata.normalize("NFD", name))])
        self.assertFalse(general.eligible(secret))

    def test_a_protected_dirs_entry_relative_to_home_is_read_from_home_not_from_the_hook_folder(self):
        secret = self.folder("Documents", "Cuentas")
        self.config(protected_dirs=["Documents/Cuentas"])
        elsewhere = self.folder("somewhere", "else")
        old = os.getcwd()
        os.chdir(elsewhere)
        try:
            self.assertFalse(general.eligible(secret))
            self.assertFalse(general.eligible(self.folder("Documents", "Cuentas", "2026")))
            self.assertEqual(sl.protected_dir(secret), "Documents/Cuentas")
            self.assertTrue(general.eligible(self.folder("Documents", "Otra")))
        finally:
            os.chdir(old)

    def test_an_unreadable_protected_dirs_entry_closes_the_general_notebook(self):
        for bad in ([""], [None], [5], "Documents/Cuentas"):
            self.config(protected_dirs=bad)
            self.assertFalse(general.eligible(self.folder("Documents", "Otra")), bad)
        self.config(protected_dirs=[])
        self.assertTrue(general.eligible(self.folder("Documents", "Otra")))

    def test_a_folder_marked_private_never_binds_even_without_a_ledger(self):
        marked = self.folder("Documents", "Privada")
        os.makedirs(os.path.join(marked, ".budgit"))
        with open(os.path.join(marked, ".budgit", "project.json"), "w") as f:
            json.dump({"private": True}, f)
        self.assertFalse(general.eligible(marked))
        self.assertFalse(general.eligible(self.folder("Documents", "Privada", "sub", "deeper")))
        legacy = self.folder("Documents", "Vieja")
        os.makedirs(os.path.join(legacy, ".studio"))
        with open(os.path.join(legacy, ".studio", "project.json"), "w") as f:
            json.dump({"private": True}, f)
        self.assertFalse(general.eligible(legacy))
        broken = self.folder("Documents", "Rota")
        os.makedirs(os.path.join(broken, ".budgit"))
        with open(os.path.join(broken, ".budgit", "project.json"), "w") as f:
            f.write("{not json")
        self.assertFalse(general.eligible(broken))
        plain = self.folder("Documents", "Normal")
        os.makedirs(os.path.join(plain, ".budgit"))
        with open(os.path.join(plain, ".budgit", "project.json"), "w") as f:
            json.dump({"private": False}, f)
        self.assertTrue(general.eligible(plain))

    def test_scripted_sdk_and_unattended_sessions_never_bind(self):
        folder = self.folder("Documents", "Otra")
        for k, v in (("CLAUDE_CODE_ENTRYPOINT", "sdk-cli"), ("CLAUDE_CODE_ENTRYPOINT", "sdk-ts"), ("CLAUDE_CODE_ENTRYPOINT", "mcp"),
                     ("CLAUDE_CODE_SESSION_ATTENDED", "0")):
            os.environ[k] = v
            try:
                self.assertFalse(general.eligible(folder), (k, v))
            finally:
                os.environ.pop(k)
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"
        for k, v in (("CLAUDE_CODE_ENTRYPOINT", "cli"), ("CLAUDE_CODE_ENTRYPOINT", "claude-desktop"), ("CLAUDE_CODE_SESSION_ATTENDED", "1")):
            os.environ[k] = v
            try:
                self.assertTrue(general.eligible(folder), (k, v))
            finally:
                os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"
                os.environ.pop("CLAUDE_CODE_SESSION_ATTENDED", None)

    def test_an_unknown_or_missing_entrypoint_never_binds(self):
        folder = self.folder("Documents", "Otra")
        # every value below was read in the installed Claude Code 2.1.308 binary (see INTERACTIVE_ENTRYPOINTS), except "scheduled",
        # "claude-jetbrains" and "CLI-x", which stand for a value nobody has confirmed
        for entry in (None, "", "local-agent", "claude-code-github-action", "print", "bench", "claude-in-teams", "claude-desktop-3p", "sdk-ts",
                      "sdk-py", "sdk-cli", "mcp", "scheduled", "remote", "claude-jetbrains", "CLI-x"):
            if entry is None:
                os.environ.pop("CLAUDE_CODE_ENTRYPOINT", None)
            else:
                os.environ["CLAUDE_CODE_ENTRYPOINT"] = entry
            self.assertFalse(general.eligible(folder), entry)
        for entry in ("cli", "claude-desktop", "claude-vscode", " CLI "):
            os.environ["CLAUDE_CODE_ENTRYPOINT"] = entry
            self.assertTrue(general.eligible(folder), entry)
        self.env.pop("CLAUDE_CODE_ENTRYPOINT")                      # and through the hook: a host that says nothing gets no notebook
        out = self.hook("session-start", session_id="e1", cwd=folder, source="startup")
        self.assertIsNone(self.pointer("e1"))
        self.assertNotIn("general", json.dumps(out))

    def test_a_config_that_does_not_parse_closes_the_general_notebook(self):
        secret = self.folder("Documents", "Cuentas")
        other = self.folder("Documents", "Otra")
        cfg = os.path.join(self.home, "config.json")
        for text in ('{"protected_dirs": ["%s",]}' % secret, "", "[]", "not json"):
            with open(cfg, "w") as f:
                f.write(text)
            self.assertFalse(general.eligible(secret), text)
            self.assertFalse(general.eligible(other), text)
        os.remove(cfg)
        self.assertTrue(general.eligible(other))

    def test_a_scripted_session_is_not_offered_a_notebook_either(self):
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "sdk-cli"
        try:
            self.assertIsNone(general.offer(self.folder("Documents", "Dev", "nuevo")))
        finally:
            os.environ["CLAUDE_CODE_ENTRYPOINT"] = "cli"
        self.assertIsNotNone(general.offer(self.folder("Documents", "Dev", "nuevo")))

    def test_a_scripted_session_start_binds_nothing_and_gets_no_ledger_check(self):
        self.env["CLAUDE_CODE_ENTRYPOINT"] = "sdk-cli"
        self.hook("session-start", session_id="p1", cwd=self.folder("Documents", "Otra"), source="startup")
        self.assertIsNone(self.pointer("p1"))
        out = self.hook("stop", session_id="p1", cwd=self.folder("Documents", "Otra"), stop_hook_active=False)
        self.assertNotIn("decision", out)

    def test_the_built_in_protected_home_folders_never_bind(self):
        for name in ("Desktop", "Downloads"):
            self.assertFalse(general.eligible(self.folder(name)), name)

    def test_a_private_project_folder_never_binds(self):
        private = self.folder("Documents", "Dev", "secret")
        sl.activate(private, "s0")
        cfg = sl.load_project_config(private)
        cfg["private"] = True
        sl.atomic_write(os.path.join(sl.studio_dir(private), "project.json"), json.dumps(cfg))
        self.assertFalse(general.eligible(self.folder("Documents", "Dev", "secret", "sub")))

    def test_a_folder_outside_the_home_or_inside_budgits_own_never_binds(self):
        outside = os.path.join(self.tmp, "elsewhere")
        os.makedirs(outside)
        self.assertFalse(general.eligible(outside))
        self.assertFalse(general.eligible(self.home))
        self.assertFalse(general.eligible(os.path.join(self.tmp, "does-not-exist")))
        self.assertFalse(general.eligible(None))
        self.assertFalse(general.eligible(""))

    def test_a_cloud_session_or_a_notebook_that_would_sit_in_a_repo_never_binds(self):
        os.environ["CLAUDE_CODE_REMOTE"] = "true"
        self.assertFalse(general.eligible(self.dev))
        os.environ.pop("CLAUDE_CODE_REMOTE")
        os.makedirs(os.path.join(self.home, ".git"))
        self.assertFalse(general.eligible(self.dev))


class BindingTest(GeneralBase):
    def start(self, session, cwd):
        return self.hook("session-start", session_id=session, cwd=cwd, source="startup", hook_event_name="SessionStart")

    def test_session_start_in_a_folder_with_no_notebook_binds_the_general_one(self):
        out = self.start("g1", self.dev)
        general_dir = os.path.join(self.home, "general")
        self.assertEqual(self.pointer("g1"), os.path.realpath(general_dir))
        self.assertTrue(os.path.isfile(os.path.join(general_dir, ".budgit", "ledger.json")) or
                        os.path.isfile(os.path.join(general_dir, ".studio", "ledger.json")))
        self.assertIn("budgit project:", self.context(out))
        self.assertIn("Open ledger items", self.context(out))

    def test_the_general_notebook_is_private_owner_only_and_outside_every_repo(self):
        self.start("g1", self.dev)
        general_dir = os.path.join(self.home, "general")
        self.assertTrue(sl.load_project_config(general_dir)["private"])
        self.assertEqual(os.stat(general_dir).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(sl.studio_dir(general_dir)).st_mode & 0o777, 0o700)
        self.assertFalse(general._walks_up_to_a_repo(general_dir))
        self.assertTrue(general.general_project().startswith(os.path.realpath(self.home)) or
                        general.general_project().startswith(self.home))

    def test_the_general_notebook_is_never_part_of_an_export(self):
        import export
        src = open(export.__file__, encoding="utf-8").read()
        self.assertNotIn("general_project", src)
        self.assertNotIn("home_state", src)

    def test_a_second_chat_shares_the_notebook_and_sees_what_the_first_wrote(self):
        self.start("g1", self.dev)
        self.hook("user-prompt-submit", session_id="g1", cwd=self.dev, prompt="apunta que el jueves llamo a Marta")
        general_dir = self.pointer("g1")
        data = sl.load_ledger(general_dir)
        sl.add_item(data, "llamar a Marta", [sl.load_inbox(general_dir)[0]["id"]])
        sl.save_ledger(general_dir, data)
        out = self.start("g2", self.folder("Documents", "Notas"))
        self.assertEqual(self.pointer("g2"), general_dir)
        self.assertIn("llamar a Marta", self.context(out))

    def test_prompts_are_tracked_like_a_project_and_stop_asks_for_them_to_be_tracked(self):
        self.start("g1", self.dev)
        out = self.hook("user-prompt-submit", session_id="g1", cwd=self.dev, prompt="redacta una nota para mi madre")
        general_dir = self.pointer("g1")
        self.assertEqual(sl.load_inbox(general_dir)[0]["text"], "redacta una nota para mi madre")
        self.assertIn("not yet in the ledger", self.context(out))
        stop = self.hook("stop", session_id="g1", cwd=self.dev, stop_hook_active=False)
        self.assertEqual(stop.get("decision"), "block")
        self.assertIn("untracked", stop["reason"])

    def test_an_old_session_with_no_pointer_binds_at_its_next_prompt(self):
        out = self.hook("user-prompt-submit", session_id="g1", cwd=self.dev, prompt="hola")
        self.assertEqual(self.pointer("g1"), os.path.realpath(os.path.join(self.home, "general")))
        self.assertEqual(sl.load_inbox(self.pointer("g1"))[0]["text"], "hola")
        self.assertIn("I-1", self.context(out))

    def test_the_chats_own_earlier_notebook_inputs_do_not_pile_onto_a_new_chat(self):
        self.start("g1", self.dev)
        self.hook("user-prompt-submit", session_id="g1", cwd=self.dev, prompt="algo privado de otro chat")
        out = self.start("g2", self.dev)
        self.assertNotIn("1 input(s) of this session", self.context(out))
        self.assertEqual(self.hook("stop", session_id="g2", cwd=self.dev, stop_hook_active=False), {})

    def test_a_protected_folder_keeps_todays_behaviour_nothing_is_bound_or_created(self):
        secret = self.folder("Documents", "Cuentas")
        self.config(protected_dirs=[secret])
        out = self.start("p1", secret)
        self.assertIsNone(self.pointer("p1"))
        self.assertFalse(os.path.exists(os.path.join(self.home, "general")))
        self.assertNotIn("budgit project:", self.context(out))
        self.assertEqual(without_time_line(self.hook("user-prompt-submit", session_id="p1", cwd=secret, prompt="hola")), {})   # R-1159: the time line is all there is
        self.assertFalse(os.path.exists(os.path.join(self.home, "general")))

    def test_a_folder_with_its_own_notebook_is_not_rebound_to_the_general_one(self):
        project = self.folder("Documents", "Dev", "app")
        sl.activate(project, "s0")
        self.start("a1", project)
        self.assertEqual(os.path.realpath(self.pointer("a1")), os.path.realpath(project))   # bound to its own project (37e7a501), never the general one
        self.assertFalse(os.path.exists(os.path.join(self.home, "general")))

    def test_no_cwd_in_the_payload_means_no_binding(self):
        self.hook("session-start", session_id="n1", source="startup")
        self.hook("user-prompt-submit", session_id="n1", prompt="hola")
        self.assertIsNone(self.pointer("n1"))

    def test_the_session_end_releases_the_chats_claims_in_the_general_notebook(self):
        self.start("g1", self.dev)
        general_dir = self.pointer("g1")
        data = sl.load_ledger(general_dir)
        item = sl.add_item(data, "algo en curso", ["I-1"])
        sl.save_ledger(general_dir, data)
        sl.start_item(general_dir, data, item["id"], "g1")
        sl.save_ledger(general_dir, data)
        self.assertEqual(sl.load_ledger(general_dir)["items"][0]["status"], "doing")
        self.hook("session-end", session_id="g1", cwd=self.dev, reason="other")
        self.assertEqual(sl.load_ledger(general_dir)["items"][0]["status"], "open")


class BarTest(GeneralBase):
    def line(self, folder, lang="en"):
        r = self.cli("status", "--line", "--project", folder, lang=lang)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_the_bar_says_general_in_the_users_language_where_the_general_notebook_applies(self):
        self.assertIn("general notebook", self.line(self.dev))
        self.assertIn("libreta general", self.line(self.dev, "es"))
        self.assertNotIn("sin libreta", self.line(self.dev, "es"))

    def test_a_protected_folder_keeps_the_no_notebook_text(self):
        secret = self.folder("Documents", "Cuentas")
        self.config(protected_dirs=[secret])
        self.assertIn("sin libreta", self.line(secret, "es"))
        self.assertIn("no ledger here", self.line(secret))

    def test_a_folder_with_a_notebook_shows_its_counts_as_before(self):
        project = self.folder("Documents", "Dev", "app")
        sl.activate(project, "s0")
        self.assertNotIn("general", self.line(project))


class OfferTest(GeneralBase):
    def start(self, session, cwd):
        return self.hook("session-start", session_id=session, cwd=cwd, source="startup")

    def test_a_folder_under_dev_without_a_notebook_is_offered_one_once(self):
        folder = self.folder("Documents", "Dev", "nuevo")
        out = self.start("o1", folder)
        self.assertIn("budgit activate", out["systemMessage"])
        self.assertIn(os.path.realpath(folder), out["systemMessage"])
        self.assertIn("never created on its own", out["systemMessage"])
        again = self.start("o2", folder)
        self.assertNotIn("budgit activate", again.get("systemMessage", ""))

    def test_a_session_bound_to_a_project_is_not_offered_a_notebook(self):
        project = self.folder("Documents", "Dev", "app")
        sl.activate(project, "bound")
        out = self.start("bound", self.folder("Documents", "Dev", "elsewhere"))
        self.assertNotIn("budgit activate", out.get("systemMessage", ""))

    def test_it_never_creates_anything_in_the_folder(self):
        folder = self.folder("Dev", "otro")
        self.start("o1", folder)
        self.assertEqual(os.listdir(folder), [])

    def test_both_dev_roots_count_and_the_root_itself_does_not(self):
        self.assertIn("budgit activate", self.start("o1", self.folder("Dev", "x")).get("systemMessage", ""))
        self.assertIn("budgit activate", self.start("o2", self.folder("Documents", "Dev", "y", "deep")).get("systemMessage", ""))
        self.assertNotIn("budgit activate", self.start("o3", self.dev).get("systemMessage", ""))
        self.assertNotIn("budgit activate", self.start("o4", self.folder("Documents", "Cosas")).get("systemMessage", ""))

    def test_the_configured_projects_root_and_other_dev_folder_names_count(self):
        self.assertIn("budgit activate", self.start("o1", self.folder("Developer", "z")).get("systemMessage", ""))
        self.assertNotIn("budgit activate", self.start("o2", self.folder("Trabajo", "z")).get("systemMessage", ""))
        self.config(projects_root=self.folder("Trabajo"))
        self.assertIn("budgit activate", self.start("o3", self.folder("Trabajo", "z")).get("systemMessage", ""))

    def test_test_and_probe_copies_are_never_offered(self):
        for name in ("brote-prueba-3", "x-prueba-1", "app-test", "probe-2", "demo-local", "test"):
            out = self.start("s-" + name, self.folder("Documents", "Dev", name))
            self.assertNotIn("budgit activate", out.get("systemMessage", ""), name)
        out = self.start("deep", self.folder("Documents", "Dev", "brote-prueba-3", "sub"))
        self.assertNotIn("budgit activate", out.get("systemMessage", ""))
        self.assertIn("budgit activate", self.start("real", self.folder("Documents", "Dev", "contest")).get("systemMessage", ""))

    def test_a_protected_folder_or_one_with_a_notebook_is_not_offered(self):
        secret = self.folder("Documents", "Dev", "privado")
        self.config(protected_dirs=[secret])
        self.assertNotIn("budgit activate", self.start("p1", secret).get("systemMessage", ""))
        project = self.folder("Documents", "Dev", "app")
        sl.activate(project, "s0")
        self.assertNotIn("budgit activate", self.start("p2", project).get("systemMessage", ""))

    def test_the_offer_follows_the_language_and_quotes_odd_names(self):
        self.env["BUDGIT_LANG"] = "es"
        folder = self.folder("Documents", "Dev", "mi proyecto")
        out = self.start("o1", folder)
        self.assertIn("Esta carpeta no tiene libreta", out["systemMessage"])
        self.assertIn("'%s'" % os.path.realpath(folder), out["systemMessage"])


class IntentTest(GeneralBase):
    def test_requests_for_text_meant_for_a_person_are_seen_in_any_language(self):
        for prompt in ("Write an email to my landlord about the heating", "redáctame un correo para el banco",
                       "escribe un mensaje a Marta diciendo que llego tarde", "draft a reply to this message",
                       "Rédige un courriel pour mon patron", "schreib eine Nachricht an Tom", "prepara un post para LinkedIn",
                       "an email to Ana, please write it", "contesta a este correo de forma amable",
                       "scrivi una lettera di presentazione"):
            self.assertTrue(general.wants_person_text(prompt), prompt)

    def test_a_request_after_a_code_clause_is_still_a_request(self):
        for prompt in ("Fix the typo, then write an email to Ana", "Once the test passes, write an email to the client",
                       "Arregla el fallo y luego escribe un correo al cliente", "fix the typo and write an email to my landlord"):
            self.assertTrue(general.wants_person_text(prompt), prompt)

    def test_the_verifiers_mismatches(self):
        for prompt in ("Verfasse einen Brief an das Finanzamt", "write an email to the API vendor asking for a refund",
                       "The bug is annoying. Write an email to my landlord"):
            self.assertTrue(general.wants_person_text(prompt), prompt)
        for prompt in ("fix the bug where the reply message is empty", "Schreibe eine Funktion, die eine E-Mail validiert",
                       "write a mail server in Go", "write an email.py module", "escreva um teste para a função que envia a mensagem"):
            self.assertFalse(general.wants_person_text(prompt), prompt)

    def test_accents_and_endings_do_not_hide_a_request(self):
        for prompt in ("prepárame un correo para Marta", "contéstale este mensaje con calma", "escreva uma mensagem para o Rui",
                       "Schreib einen Brief an meine Bank", "REDÁCTAME UN MENSAJE para el jefe", "write an email to the client about the bug"):
            self.assertTrue(general.wants_person_text(prompt), prompt)

    def test_a_code_word_next_to_the_noun_keeps_it_out(self):
        for prompt in ("escribe el mensaje de commit", "mejora el mensaje de error", "write a post-mortem", "write a message to stdout",
                       "redacta un mensaje de log para el parser"):
            self.assertFalse(general.wants_person_text(prompt), prompt)

    def test_the_code_word_after_a_joiner_in_each_language_keeps_it_out(self):
        for prompt in ("mejora los comentarios del código", "rédige le message d'erreur", "mejora el mensaje del error",
                       "escribe el mensaje del commit", "scrivi un messaggio di errore per la funzione"):
            self.assertFalse(general.wants_person_text(prompt), prompt)
        for prompt in ("Réécris ce courriel", "write an email to my boss about the error in the invoice"):
            self.assertTrue(general.wants_person_text(prompt), prompt)

    def test_a_title_naming_a_protected_folder_matches_in_either_unicode_form(self):
        import unicodedata
        import livework
        self.assertEqual(livework._norm(unicodedata.normalize("NFD", "Cuentas café")), livework._norm(unicodedata.normalize("NFC", "Cuentas café")))

    def test_ordinary_and_code_prompts_are_not(self):
        for prompt in ("fix the failing test", "add a login page", "write a test for the message handler", "refactor the mail queue code",
                       "qué hora es", "lee el log de errores", "escribe una función que envíe el correo", "", None):
            self.assertFalse(general.wants_person_text(prompt), prompt)

    def test_the_prompt_adds_the_routing_in_a_project_session_and_in_an_unbound_one(self):
        project = self.folder("Documents", "Dev", "app")
        sl.activate(project, "b1")
        out = self.hook("user-prompt-submit", session_id="b1", cwd=project, prompt="escribe un correo a Marta")
        self.assertIn("stop-slop", self.context(out))
        secret = self.folder("Documents", "Cuentas")
        self.config(protected_dirs=[secret])
        out = self.hook("user-prompt-submit", session_id="u1", cwd=secret, prompt="escribe un correo a Marta")
        self.assertIn("stop-slop", self.context(out))
        self.assertIsNone(self.pointer("u1"))

    def test_an_ordinary_turn_leaves_no_trace_and_clears_an_old_flag(self):
        flag_dir = os.path.join(self.home, "humanize")
        self.hook("user-prompt-submit", session_id="c1", cwd=self.dev, prompt="añade una columna a la tabla")
        self.assertFalse(os.path.exists(flag_dir) and os.listdir(flag_dir))
        self.hook("user-prompt-submit", session_id="c1", cwd=self.dev, prompt="escribe un correo a Marta")
        self.assertEqual(os.listdir(flag_dir), ["c1.flag"])
        self.hook("user-prompt-submit", session_id="c1", cwd=self.dev, prompt="gracias, ahora otra cosa")
        self.assertEqual(os.listdir(flag_dir), [])


class HumanizeStopTest(GeneralBase):
    def ask(self, session="h1", cwd=None):
        return self.hook("user-prompt-submit", session_id=session, cwd=cwd or self.folder("Documents", "Cuentas"),
                         prompt="escribe un correo a Marta")

    def setUp(self):
        super().setUp()
        self.config(protected_dirs=[self.folder("Documents", "Cuentas")])      # an unbound session: only the humanize pass runs

    def stop(self, text, session="h1", **kw):
        return self.hook("stop", session_id=session, cwd=self.folder("Documents", "Cuentas"), stop_hook_active=kw.pop("active", False),
                         last_assistant_message=text, **kw)

    def test_a_draft_with_findings_is_blocked_once_with_the_findings(self):
        self.ask()
        out = self.stop(SLOP)
        self.assertEqual(out["decision"], "block")
        for needle in ("W2", "W3", "stop-slop", "runs once"):
            self.assertIn(needle, out["reason"])
        self.assertEqual(self.stop(SLOP), {})                                    # the same text again: never a second block

    def test_a_letter_shaped_answer_is_linted_even_when_the_prompt_did_not_look_like_a_request(self):
        for text in ("Subject: Next week\n\n" + SLOP,
                     "Dear Ana,\n\n" + SLOP + "\n\nBest regards,\nMarta",
                     "Estimada Ana:\n\n" + SLOP + "\n\nSaludos,\nMarta",
                     "Asunto: la reunión\n\n" + SLOP,
                     "Sehr geehrte Frau Weber,\n\n" + SLOP + "\n\nMit freundlichen Grüßen\nMarta"):
            self.hook("user-prompt-submit", session_id="h1", cwd=self.folder("Documents", "Cuentas"), prompt="ayúdame con lo de Ana")
            out = self.stop(text)
            self.assertEqual(out.get("decision"), "block", text[:20])
            self.assertEqual(self.stop(text, active=True), {})                      # the continued Stop is left alone

    def test_an_unflagged_letter_blocks_once_per_request_even_if_the_host_never_says_the_stop_was_continued(self):
        letter = "Dear Ana,\n\n" + SLOP + "\n\nBest regards,\nMarta"
        self.hook("user-prompt-submit", session_id="h1", cwd=self.folder("Documents", "Cuentas"), prompt="ayúdame con lo de Ana")
        self.assertEqual(self.stop(letter).get("decision"), "block")
        for _ in range(3):
            self.assertEqual(self.stop(letter), {})                                  # stop_hook_active stays false: still once
        self.hook("user-prompt-submit", session_id="h1", cwd=self.folder("Documents", "Cuentas"), prompt="hazlo más corto")
        self.assertEqual(self.stop(letter).get("decision"), "block")                 # a new request, a new single check

    def test_a_greeting_alone_or_a_plain_answer_is_not_a_letter(self):
        for text in ("Hola Marta, " + SLOP, SLOP, "Dear Ana,\n\n" + SLOP, SLOP + "\n\nBest regards"):
            self.assertEqual(self.stop(text), {}, text[:20])

    def test_a_letter_inside_a_code_fence_is_not_a_letter(self):
        self.assertEqual(self.stop("```\nSubject: x\n" + SLOP + "\n```"), {})

    def test_a_clean_draft_is_not_blocked(self):
        self.ask()
        self.assertEqual(self.stop(CLEAN), {})

    def test_it_never_loops_a_continued_stop_is_left_alone(self):
        self.ask()
        self.assertEqual(self.stop(SLOP, active=True), {})
        self.assertEqual(self.stop(SLOP), {})                                    # and the flag is gone

    def test_no_request_for_text_means_no_check_at_all(self):
        self.hook("user-prompt-submit", session_id="h1", cwd=self.folder("Documents", "Cuentas"), prompt="lista los ficheros")
        self.assertEqual(self.stop(SLOP), {})

    def test_a_short_answer_or_a_code_only_answer_is_not_linted(self):
        self.ask()
        self.assertEqual(self.stop("Hecho, really."), {})
        self.ask()
        self.assertEqual(self.stop("```\n" + SLOP + "\n```"), {})

    def test_an_old_flag_is_ignored(self):
        self.ask()
        flag = os.path.join(self.home, "humanize", "h1.flag")
        with open(flag, "w") as f:
            json.dump({"ts": 1}, f)
        self.assertEqual(self.stop(SLOP), {})
        self.assertFalse(os.path.exists(flag))

    def test_the_text_can_come_from_the_transcript(self):
        self.ask()
        path = os.path.join(self.tmp, "t.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "x"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": SLOP}]}}) + "\n")
        out = self.hook("stop", session_id="h1", cwd=self.dev, stop_hook_active=False, transcript_path=path)
        self.assertEqual(out["decision"], "block")

    def test_findings_are_deduplicated_and_capped(self):
        self.ask()
        words = ["really", "literally", "genuinely", "honestly", "truly", "fundamentally", "inherently", "inevitably", "crucially", "importantly"]
        out = self.stop(("really really literally " + " ".join(words) + " ") * 3 + "and some more plain words to pass the minimum length of text")
        self.assertEqual(out["reason"].count("- W2 'really'"), 1)
        self.assertIn("(+", out["reason"])

    def test_a_bound_session_gets_one_block_that_carries_both_checks(self):
        self.hook("session-start", session_id="b1", cwd=self.dev, source="startup")
        self.hook("user-prompt-submit", session_id="b1", cwd=self.dev, prompt="escribe un correo a Marta")
        out = self.hook("stop", session_id="b1", cwd=self.dev, stop_hook_active=False, last_assistant_message=SLOP)
        self.assertEqual(out["decision"], "block")
        self.assertIn("humanize check", out["reason"])
        self.assertIn("untracked", out["reason"])

    def test_the_linter_still_flags_nothing_in_a_clean_text(self):
        import humanizelint
        self.assertEqual([f.rule for f in humanizelint.scan(CLEAN, "auto", "ui")], [])


class GeneralStopTest(GeneralBase):
    """The Stop ledger check in the general notebook: one block per untracked item, never a loop, one line of how to triage."""
    def setUp(self):
        super().setUp()
        self.hook("session-start", session_id="s1", cwd=self.dev, source="startup")
        self.notebook = general.general_project()

    def say(self, prompt, session="s1"):
        return self.hook("user-prompt-submit", session_id=session, cwd=self.dev, prompt=prompt)

    def stop(self, session="s1", active=False):
        return self.hook("stop", session_id=session, cwd=self.dev, stop_hook_active=active, last_assistant_message="Hecho.")

    def untracked(self):
        return [e["id"] for e in sl.untracked(self.notebook, sl.load_ledger(self.notebook))]

    def test_an_untracked_input_blocks_once_with_a_one_line_triage_hint(self):
        self.say("apunta que tengo que llamar a Marta el jueves")
        out = self.stop()
        self.assertEqual(out["decision"], "block")
        for needle in ("I-1 untracked", "do it now", "fold it", "file it", "drop it", "ledger ack", "once per item"):
            self.assertIn(needle, out["reason"])
        self.assertNotIn("decision", self.stop())

    def test_it_never_loops_whatever_the_host_reports(self):
        self.say("apunta que tengo que llamar a Marta el jueves")
        self.assertEqual(self.stop()["decision"], "block")
        for active in (False, True, False, True):
            self.assertNotIn("decision", self.stop(active=active))

    def test_the_item_stays_flagged_in_the_notebook(self):
        self.say("apunta que tengo que llamar a Marta el jueves")
        self.stop()
        self.stop()
        self.assertEqual(self.untracked(), ["I-1"])

    def test_a_later_input_is_asked_about_once_and_the_first_is_not_repeated(self):
        self.say("apunta que tengo que llamar a Marta el jueves")
        self.stop()
        self.say("y que el viernes hay que renovar el seguro del coche")
        out = self.stop()
        self.assertEqual(out["decision"], "block")
        self.assertIn("I-2 untracked", out["reason"])
        self.assertNotIn("I-1 untracked", out["reason"])
        self.assertNotIn("decision", self.stop())

    def test_another_chat_is_asked_about_its_own_inputs(self):
        self.say("apunta que tengo que llamar a Marta el jueves")
        self.stop()
        self.hook("session-start", session_id="s2", cwd=self.dev, source="startup")
        self.say("apunta que mañana toca revisar el contrato", session="s2")
        out = self.stop(session="s2")
        self.assertEqual(out["decision"], "block")
        self.assertIn("revisar el contrato", out["reason"])
        self.assertNotIn("Marta", out["reason"])

    def test_a_tracked_item_is_not_asked_about(self):
        self.say("apunta que tengo que llamar a Marta el jueves")
        self.assertEqual(self.cli("ledger", "ack", "I-1", "--note", "covered", "--project", self.notebook).returncode, 0)
        self.assertNotIn("decision", self.stop())

    def test_the_memory_of_asked_items_is_bounded(self):
        state = {}
        for n in range(general.ASKED_SESSIONS + 5):
            self.assertIsNotNone(general.stop_reason(state, "s%d" % n, ["I-%d untracked: «x»" % n]))
        self.assertEqual(len(state["asked"]), general.ASKED_SESSIONS)
        many = ["I-%d untracked: «x»" % n for n in range(general.ASKED_PER_SESSION + 50)]
        general.stop_reason(state, "big", many)
        self.assertEqual(len(state["asked"]["big"]), general.ASKED_PER_SESSION)
        self.assertIsNone(general.stop_reason({"asked": "junk"}, "s", []))


DRAFT = ("Hola Marta, te confirmo que la versión 17 de Java sigue con soporte en la imagen que usáis, según la tabla de ciclo de vida "
         "del fabricante, sección Java, fila 17. Si vuestro contrato incluye soporte extendido, la fecha final cambia; dime y lo reviso "
         "con ellos. Gracias por avisar con tiempo.")
REPORT = ("PASS\nDates and figures: read on the vendor lifecycle page today.\nLifecycle rows: section Java, row 17.\n"
          "Visible at link: yes, row 17 of the first table.\nConditions: extended support written as a condition.\n"
          "Fixed claims: none in the text.\n")
SHORT_ACK = ("Hecho, he acortado el texto de arriba y no he tocado ninguna fecha ni ninguna cifra del mensaje anterior, "
             "solo he quitado frases que sobraban y dejado el resto igual que estaba.")


class GateBase(GeneralBase):
    SESSION = "g1234567-aaaa"

    def setUp(self):
        super().setUp()
        self.acked = set()
        self.hook("session-start", session_id=self.SESSION, cwd=self.dev, source="startup")
        self.report = os.path.join(sl.resolve_data_dir(general.general_project()), "verify", "client-%s.md" % self.SESSION)

    def ask(self, prompt="escribe un correo formal al cliente sobre el soporte de Java 17", cwd=None):
        out = self.hook("user-prompt-submit", session_id=self.SESSION, cwd=cwd or self.dev, prompt=prompt)
        general_nb = general.general_project()                     # the bound session's own ledger check is not under test here
        for entry in sl.load_inbox(general_nb):
            if entry["id"] not in self.acked:
                self.acked.add(entry["id"])
                self.cli("ledger", "ack", entry["id"], "--note", "test", "--project", general_nb)
        return out

    def stop(self, text=DRAFT, cwd=None, **kw):
        return self.hook("stop", session_id=self.SESSION, cwd=cwd or self.dev, stop_hook_active=kw.pop("active", False),
                         last_assistant_message=text, **kw)

    def verifier_writes(self, body=REPORT, agent="budgit:verifier"):
        """The report is written and, through the PostToolUse hook, sealed: what the verifier agent's Write does."""
        os.makedirs(os.path.dirname(self.report), exist_ok=True)
        with open(self.report, "w", encoding="utf-8") as f:
            f.write(body)
        return self.hook("post-tool-use", session_id=self.SESSION, cwd=self.dev, tool_name="Write", agent_type=agent,
                         tool_input={"file_path": self.report})


class ClientGateTest(GateBase):
    """R-375 / R-378: text for a client passes stop-slop, a claims checklist and a sealed fresh-context verifier before delivery."""

    def test_the_prompt_check_marks_client_text_and_the_versions_lens(self):
        yes = ("escribe un correo formal al cliente", "draft an email to our customer about Python 3.12", "redacta un correo con la propuesta para el cliente",
               "write a formal email to the vendor")
        no = ("escribe un correo a Marta", "write a script for the client library", "lista los ficheros del cliente")
        for p in yes:
            self.assertTrue(general.wants_client_text(p), p)
        for p in no:
            self.assertFalse(general.wants_client_text(p), p)
        for p in ("is Java 17 still supported on UBI 8?", "¿hasta cuándo tiene soporte Python 3.12 en RHEL 8?", "does CVE-2025-1234 affect version 2.4.1",
                  "end of life for node 18"):
            self.assertTrue(general.wants_versions_lens(p), p)
        for p in ("lista los ficheros", "escribe un correo a Marta", "support me with this", "the version of my CV"):
            self.assertFalse(general.wants_versions_lens(p), p)

    def test_a_client_request_gets_the_gate_and_the_lens_routing(self):
        ctx = self.context(self.ask())
        for needle in ("stop-slop", "client or another third party", "versions/EOL lens", "vulnerabilities"):
            self.assertIn(needle, ctx)
        ctx = self.context(self.ask("is Java 17 still supported on UBI 8?"))      # not a text request: the lens only
        self.assertIn("versions/EOL lens", ctx)
        self.assertNotIn("third party", ctx)
        self.assertNotIn("humanize it before", ctx)

    def test_a_plain_email_is_not_gated(self):
        self.ask("escribe un correo a Marta")
        self.assertEqual(self.stop(), {})

    def test_the_first_stop_blocks_with_the_checklist_and_the_report_path(self):
        self.ask()
        out = self.stop()
        self.assertEqual(out["decision"], "block")
        for needle in ("client-text gate", "budgit:verifier", self.report, "Dates and figures", "Lifecycle rows", "flagged",
                       "RHSA", "at most 2 times"):
            self.assertIn(needle, out["reason"])

    def test_a_sealed_pass_with_every_checklist_line_lets_the_text_go(self):
        self.ask()
        self.assertEqual(self.stop()["decision"], "block")
        self.verifier_writes()
        self.assertEqual(self.stop(active=True), {})

    def test_a_report_the_verifier_wrote_before_the_first_stop_counts(self):
        self.ask()
        self.verifier_writes()
        self.assertEqual(self.stop(), {})

    def test_an_unsealed_report_is_not_a_verification(self):
        self.ask()
        self.stop()
        self.verifier_writes(agent="general-purpose")        # written by another agent: no seal
        out = self.stop(active=True)
        self.assertEqual(out["decision"], "block")
        self.assertIn("no sealed verifier report", out["reason"])

    def test_a_report_older_than_the_request_is_stale(self):
        self.verifier_writes()
        os.utime(self.report, (1, 1))
        self.hook("post-tool-use", session_id=self.SESSION, cwd=self.dev, tool_name="Write", agent_type="budgit:verifier",
                  tool_input={"file_path": self.report})                           # sealed, but written long before this request
        self.ask()
        self.assertEqual(self.stop()["decision"], "block")

    def test_a_fail_or_missing_checklist_lines_are_named(self):
        self.ask()
        self.verifier_writes("FAIL\nDates and figures: the UBI 8 date is wrong.\n")
        out = self.stop()
        self.assertEqual(out["decision"], "block")
        self.assertIn("did not answer PASS", out["reason"])
        self.assertIn("Lifecycle rows", out["reason"].split("Still missing:")[1])

    def test_flagged_sentences_must_change_before_delivery(self):
        sentence = "la versión 17 de Java sigue con soporte en la imagen que usáis"
        self.ask()
        self.verifier_writes(REPORT + "- flagged: %s\n" % sentence)
        out = self.stop()                                                 # still there: blocked, with the sentence
        self.assertEqual(out["decision"], "block")
        self.assertIn("still in the text", out["reason"])
        self.assertIn(sentence, out["reason"])
        reworded = DRAFT.replace(sentence, "si vuestro contrato lo cubre, Java 17 sigue con soporte en la imagen")
        self.assertEqual(self.stop(reworded, active=True), {})            # changed: it can go

    def test_it_blocks_at_most_twice_then_tells_the_user_what_was_skipped(self):
        self.ask()
        self.assertEqual(self.stop()["decision"], "block")                # instructions
        self.verifier_writes("FAIL\n- flagged: la versión 17 de Java sigue con soporte en la imagen que usáis\n")
        self.assertEqual(self.stop(active=True)["decision"], "block")     # concrete defects
        out = self.stop(active=True)                                      # blocks used up
        self.assertNotIn("decision", out)
        self.assertIn("went out without finishing its check", out["systemMessage"])
        self.assertIn("did not pass it", out["systemMessage"])
        out = self.stop(active=False)                                     # whatever the host reports, no loop
        self.assertNotIn("decision", out)

    def test_the_note_to_the_user_follows_her_language(self):
        self.env["BUDGIT_LANG"] = "es"
        self.ask()
        self.stop()
        out = self.stop(active=True)
        self.assertIn("sin terminar su comprobación", out["systemMessage"])

    def test_fixed_needs_an_advisory_the_reader_can_open(self):
        fixed = DRAFT + " Red Hat fixes it through the ca-certificates package."
        self.ask()
        self.verifier_writes()
        out = self.stop(fixed)
        self.assertEqual(out["decision"], "block")
        self.assertIn("ca-certificates", out["reason"])
        self.assertIn("without an advisory", out["reason"])
        with_advisory = fixed + " See RHSA-2025:1234 or https://access.redhat.com/errata/RHSA-2025:1234."
        self.assertEqual(self.stop(with_advisory, active=True), {})
        self.assertEqual(self.stop(DRAFT + " Corregido en la versión 2.4.1 del paquete.", active=True), {})
        self.assertEqual(general.fixed_without_advisory("Ya está solucionado."), ["Ya está solucionado."])
        self.assertEqual(general.fixed_without_advisory("We prefixed the label. The fix is a patch you apply; details in the CVE page."),
                         ["The fix is a patch you apply; details in the CVE page."])

    def test_a_follow_up_that_still_reads_like_the_letter_is_gated_again(self):
        self.ask()
        self.verifier_writes()
        self.assertEqual(self.stop(), {})
        self.ask("hazlo más corto")                                        # not a new request for text, but the same client text
        letter = "Dear Marta,\n\n" + DRAFT.replace("Hola Marta, ", "") + "\n\nBest regards,\nUsagi"
        self.assertEqual(self.stop(letter)["decision"], "block")           # the old report predates this version

    def test_an_ordinary_answer_after_a_client_request_is_left_alone(self):
        self.ask()
        self.verifier_writes()
        self.stop()
        self.ask("lista los ficheros")
        self.assertEqual(self.stop(SHORT_ACK), {})

    def test_a_folder_with_no_notebook_is_told_once_and_then_noted(self):
        cuentas = self.folder("Documents", "Cuentas")
        self.config(protected_dirs=[cuentas])
        self.hook("user-prompt-submit", session_id="u1", cwd=cuentas, prompt="escribe un correo formal al cliente")
        out = self.hook("stop", session_id="u1", cwd=cuentas, stop_hook_active=False, last_assistant_message=DRAFT)
        self.assertEqual(out["decision"], "block")
        self.assertIn("no notebook", out["reason"])
        out = self.hook("stop", session_id="u1", cwd=cuentas, stop_hook_active=True, last_assistant_message=DRAFT)
        self.assertNotIn("decision", out)
        self.assertIn("no notebook", out["systemMessage"])

    def test_the_humanize_findings_and_the_gate_share_one_block(self):
        self.ask()
        out = self.stop(SLOP + " " + CLEAN + " " + CLEAN)             # a draft: 80 words or more, so the request is used up
        self.assertEqual(out["decision"], "block")
        self.assertIn("humanize check", out["reason"])
        self.assertIn("client-text gate", out["reason"])

    def test_the_lens_file_ships_and_is_listed(self):
        lens = os.path.join(ROOT, "skills", "budgit-kickoff", "lenses", "versions-eol.md")
        text = open(lens, encoding="utf-8").read()
        for needle in ("quarterly patch", "section and row", "another date", "condition", "advisory"):
            self.assertIn(needle, text)
        self.assertIn("versions-eol", open(os.path.join(ROOT, "skills", "budgit-kickoff", "SKILL.md"), encoding="utf-8").read())


FILLER = {"en": "we will bring the slides for everyone and keep the meeting short as agreed last week with the whole team",
          "es": "llevaremos las diapositivas para todos y mantendremos la reunion corta como quedamos la semana pasada con todo el equipo",
          "fr": "nous apporterons les diapositives pour tout le monde et garderons la reunion courte comme convenu la semaine derniere",
          "de": "wir bringen die Folien fuer alle mit und halten das Treffen kurz wie in der vergangenen Woche mit dem ganzen Team vereinbart"}


def _letter(lang, middle, subject=False):
    greet = {"en": "Dear Ana,", "es": "Hola Ana,", "fr": "Bonjour Ana,", "de": "Hallo Ana,"}[lang]
    sign = {"en": "Best regards,", "es": "Un saludo,", "fr": "Cordialement,", "de": "Viele Gruesse,"}[lang]
    head = ("Subject: Follow-up\n\n" if subject else "") + greet + "\n\n"
    return head + middle + " " + FILLER[lang] + ".\n\n" + sign + "\nMarta"


# (text, expected class): 'full' = letter with something that can be wrong, 'light' = letter with nothing, None = not a letter
GOLD = [
    (_letter("en", "Java 17 is still supported on the image you use, according to the vendor lifecycle table"), "full"),
    (_letter("en", "The meeting is on May 12 at ten in the small room"), "full"),
    (_letter("en", "Please note the deadline of 12/05/2026 for the signed contract"), "full"),
    (_letter("en", "The issue CVE-2025-1234 affects the package and it is fixed in the next update"), "full"),
    (_letter("en", "Upgrade to openssl 3.0.13 before the end of the month"), "full"),
    (_letter("en", "Python 3.9 reaches end of life soon, so plan the move"), "full"),
    (_letter("en", "The vulnerability was patched last quarter", subject=True), "full"),
    (_letter("es", "La reunión es el 12 de mayo a las diez en la sala pequeña"), "full"),
    (_letter("es", "El soporte de RHEL 8 termina en 2029 según la tabla del fabricante"), "full"),
    (_letter("es", "El problema ya está corregido en la versión 2.4.1 del paquete"), "full"),
    (_letter("es", "Os confirmo que el contrato vence el 30/06/2027"), "full"),
    (_letter("es", "La imagen UBI 8 sigue con soporte hasta que el fabricante diga lo contrario"), "full"),
    (_letter("fr", "La reunion aura lieu le 12 mai a dix heures"), "full"),
    (_letter("fr", "Le support de Java 11 se termine bientot"), "full"),
    (_letter("de", "Das Treffen findet am 12. Mai um zehn Uhr statt"), "full"),
    (_letter("de", "Windows 10 wird nicht mehr unterstuetzt, bitte planen Sie das Update", subject=True), "full"),
    (_letter("en", "Thanks for your note and see you at the usual place"), "light"),
    (_letter("en", "I have attached the slides and the notes you asked for", subject=True), "light"),
    (_letter("en", "Happy to help with the next steps whenever you are ready"), "light"),
    (_letter("es", "Gracias por avisar, nos vemos donde siempre"), "light"),
    (_letter("es", "Te paso las diapositivas y las notas que pediste", subject=True), "light"),
    (_letter("fr", "Merci pour votre message, a tres bientot"), "light"),
    (_letter("de", "Danke fuer die Nachricht, bis bald am ueblichen Ort"), "light"),
    (_letter("es", "Quedo atento a cualquier cosa que necesites de mi parte"), "light"),
    ("The function returns a list of ids. Java 17 is used in the build, and the tests run on 3.12.4 with the flag set to true in config.", None),
    ("He cambiado el fichero y los tests pasan. La versión 2.4.1 del paquete está en el lock desde el 12 de mayo de 2026.", None),
    ("Here is the list: one, two, three. Nothing else changed in the folder, and the build is green on every platform we run.", None),
    ("Hecho. He acortado el texto y no he tocado ninguna fecha ni ninguna cifra del mensaje anterior, solo he quitado frases sobrantes.", None),
    ("Hi there, this answer starts with a greeting but never ends with a sign-off line, so it is only a normal reply from me to you.", None),
    ("Sincerely, the numbers say it all: 2026 sales grew and the 3.1.4 release is out. This is a plain reply, not a letter at all.", None),
]


class ReplyTriggerTest(GateBase):
    """R-375 trigger B: a letter-shaped reply is gated whatever the prompt said. With a date, version, advisory or support word in it
    the full gate applies; otherwise one light question and no verifier."""

    def test_the_gold_set_has_no_misses(self):
        self.assertGreaterEqual(len(GOLD), 30)
        for text, want in GOLD:
            self.assertEqual(general.classify_reply(text), want, text[:80])

    def test_every_letter_with_a_date_or_a_version_gets_the_full_gate(self):
        letters = [t for t, want in GOLD if want == "full"]
        self.assertGreaterEqual(len(letters), 14)
        for text in letters:
            self.assertEqual(general.classify_reply(text), "full", text[:80])

    def test_a_risky_letter_in_a_chat_with_no_client_word_gets_the_full_gate(self):
        self.ask("hazme un borrador para Ana")
        out = self.stop(GOLD[1][0])
        self.assertEqual(out["decision"], "block")
        self.assertIn("client-text gate", out["reason"])
        self.assertIn(self.report, out["reason"])
        self.verifier_writes()
        self.assertEqual(self.stop(GOLD[1][0], active=True), {})

    def test_a_plain_letter_gets_one_light_question_and_no_verifier(self):
        self.ask("hazme un borrador para Ana")
        plain = [t for t, want in GOLD if want == "light"][0]
        out = self.stop(plain)
        self.assertEqual(out["decision"], "block")
        self.assertIn("once per request", out["reason"])
        self.assertNotIn("budgit:verifier", out["reason"])
        self.assertEqual(self.stop(plain, active=True), {})
        self.assertEqual(self.stop(plain), {})                           # whatever the host reports, asked once
        self.ask("otro borrador")                                         # a new request asks again
        self.assertEqual(self.stop(plain)["decision"], "block")

    def test_an_ordinary_reply_is_never_touched_by_trigger_b(self):
        self.ask("lista los ficheros")
        for text, want in GOLD:
            if want is None:
                self.assertEqual(self.stop(text), {}, text[:80])

    def test_a_huge_reply_is_cut_before_it_is_scanned(self):
        huge = "x " * 20000 + _letter("en", "Java 17 is supported")
        self.ask("lista los ficheros")
        self.assertEqual(self.stop(huge), {})        # the tail window holds the sign-off but the greeting is out of the head: no letter, no crash


class GateDefectsTest(GateBase):
    """First verifier round on R-375/R-378: advisory detection, flagged-sentence match, stale seals, session names, false positives."""

    def test_a_url_a_link_a_code_span_or_a_version_at_the_sentence_end_is_an_advisory(self):
        ok = ("The issue was fixed in https://access.redhat.com/errata/RHSA-2025:1234 last week.",
              "The issue was fixed. See https://access.redhat.com/errata/RHSA-2025:1234.",
              "The issue was fixed in [the advisory](https://access.redhat.com/errata/RHSA-2025:1234).",
              "The issue was fixed in `17.0.9`.",
              "The issue was fixed in 17.0.9.",
              "El fallo quedó corregido en 17.0.9.",
              "The issue was patched in 3.2.1. Details follow.")
        for text in ok:
            self.assertEqual(general.fixed_without_advisory(text), [], text)
        bad = ("The issue was fixed on 2025.10.27.", "The issue was fixed on 27.10.2025.", "The issue was fixed in version 17.", "It was fixed.")
        for text in bad:
            self.assertEqual(len(general.fixed_without_advisory(text)), 1, text)
        self.assertEqual(general.fixed_without_advisory("We offer a fixed price for this proposal."), [])

    def test_the_flagged_match_is_case_blind_whole_words_and_skips_one_word_values(self):
        sentence = "la versión 17 de Java sigue con soporte en la imagen que usáis"
        self.ask()
        self.verifier_writes(REPORT + "- flagged: %s\n" % sentence.upper())
        out = self.stop(DRAFT)
        self.assertEqual(out["decision"], "block")                        # same sentence in other case: still there
        self.assertIn("still in the text", out["reason"])
        self.ask("otra vez, escribe un correo formal al cliente sobre el soporte de Java 17")
        self.verifier_writes(REPORT + "- flagged: none\n- unconfirmed: no\n- partial: 2\n")
        self.assertEqual(self.stop(DRAFT + " Nonetheless we know no one with 2 problems."), {})

    def test_touching_an_old_sealed_report_does_not_make_it_new(self):
        self.verifier_writes()                                           # sealed now, before the request below
        time.sleep(0.05)
        self.ask()
        os.utime(self.report, None)                                      # the file looks new; its seal is not
        self.assertEqual(self.stop()["decision"], "block")

    def test_a_symlink_to_another_sealed_report_is_not_this_report(self):
        other = os.path.join(os.path.dirname(self.report), "R-5.md")
        os.makedirs(os.path.dirname(other), exist_ok=True)
        with open(other, "w", encoding="utf-8") as f:
            f.write(REPORT)
        self.ask()
        self.hook("post-tool-use", session_id=self.SESSION, cwd=self.dev, tool_name="Write", agent_type="budgit:verifier",
                  tool_input={"file_path": other})
        os.symlink(other, self.report)
        self.assertEqual(self.stop()["decision"], "block")

    def test_sessions_that_share_their_first_eight_characters_do_not_share_a_report(self):
        self.ask()
        self.verifier_writes()
        self.assertEqual(self.stop(), {})
        other = "g1234567-bbbb"
        self.hook("user-prompt-submit", session_id=other, cwd=self.dev, prompt="escribe un correo formal al cliente sobre el soporte de Java 17")
        for entry in sl.load_inbox(general.general_project()):
            self.cli("ledger", "ack", entry["id"], "--note", "test", "--project", general.general_project())
        out = self.hook("stop", session_id=other, cwd=self.dev, stop_hook_active=False, last_assistant_message=DRAFT)
        self.assertEqual(out["decision"], "block")

    def test_a_cleared_problem_that_returns_is_asked_again(self):
        state = {}
        self.assertIsNotNone(general.stop_reason(state, "s", ["I-1 untracked: «a»", "I-2 untracked: «b»"]))
        self.assertIsNone(general.stop_reason(state, "s", ["I-1 untracked: «a»", "I-2 untracked: «b»"]))
        self.assertIsNone(general.stop_reason(state, "s", ["I-2 untracked: «b»"]))
        self.assertIn("I-1", general.stop_reason(state, "s", ["I-1 untracked: «a»", "I-2 untracked: «b»"]))

    def test_words_that_are_not_a_client_do_not_start_the_gate(self):
        for p in ("write a message to my friend about the external hard drive", "redacta un correo para mi madre, algo formal no hace falta",
                  "write a post about my favorite client library"):
            self.assertFalse(general.wants_client_text(p), p)
        for p in ("escribe un correo formal al cliente", "write an email to our supplier about the proposal", "redacta un correo para el abogado",
                  "escribe un correo a la clienta"):
            self.assertTrue(general.wants_client_text(p), p)
        self.assertFalse(general.wants_client_text("write an email about the client-side validation"))


# (clarifying reply, bare draft) pairs: the client request must still be armed when the draft comes
CLARIFY_THEN_DRAFT = [
    ("Antes de escribirlo: ¿para qué imagen y qué versión de Java quieres que confirme, y a quién va dirigido el correo exactamente?",
     "El soporte de Java 17 en la imagen UBI 8 llega hasta septiembre de 2029 según la tabla del fabricante, sección Java, fila 17. "
     "Si vuestro contrato incluye soporte extendido, esa fecha cambia y habría que revisarla con ellos antes de planificar la migración "
     "del próximo trimestre con el equipo de operaciones."),
    ("Which image and which Java version should I confirm, and is the email for the customer's technical team or for their manager?",
     "Java 17 is supported on the UBI 8 image until September 2029 according to the vendor lifecycle table, section Java, row 17. "
     "If your contract includes extended support the date changes, so please treat it as a condition until we have confirmed it with "
     "the vendor, and plan the migration for the next quarter with the operations team."),
    ("Dime primero el nombre del cliente.", "Hola Marta, Java 17 sigue con soporte hasta septiembre de 2029 en vuestra imagen, según la tabla del fabricante, sección Java, "
     "fila 17, y te lo confirmo por escrito en cuanto el proveedor responda a nuestra consulta."),
]


class GateRound2Test(GateBase):
    """Second verifier round: short letters, upgrade from the light block, more date and version forms, the armed client request."""

    def test_a_one_line_letter_with_a_date_or_a_version_is_gated(self):
        for text in ("Hi Ana,\n\nJava 17 is supported until September 2029.\n\nBest regards,\nLuis",
                     "Hola Ana,\n\nJava 17 tiene soporte hasta septiembre de 2029.\n\nUn saludo,\nLuis",
                     "Subject: Java 17 EOL\n\nSupport ends 2029."):
            self.assertEqual(general.classify_reply(text), "full", text)
        self.ask("hazme un borrador para Ana")
        self.assertEqual(self.stop("Hi Ana,\n\nJava 17 is supported until September 2029.\n\nBest regards,\nLuis")["decision"], "block")

    def test_a_light_block_does_not_disarm_the_full_gate(self):
        plain = [t for t, want in GOLD if want == "light"][0]
        self.ask("hazme un borrador para Ana")
        self.assertEqual(self.stop(plain)["decision"], "block")
        risky = plain.replace("Best regards", "Java 17 is supported until September 2029 and CVE-2025-1234 is fixed.\n\nBest regards")
        out = self.stop(risky, active=True)
        self.assertEqual(out["decision"], "block")
        self.assertIn("client-text gate", out["reason"])

    def test_more_date_and_version_forms_get_the_full_gate(self):
        for middle in ("Dec 5 is the date.", "The call is on Oct. 3rd.", "Deadline: 15/3.", "It ends in Q4.", "Java SE 21 is the target.",
                       "Tomcat 9 and Spring Boot 3 are in use.", "Log4j 2 is installed.", "Use v2 of the API.", "This release is EOL.",
                       "The CVE is still open.", "Se arregló el problema.", "Es wurde behoben.", "O suporte termina em 3 de outubro.",
                       "Il supporto termina il 3 ottobre.", "Mitigated last week."):
            self.assertEqual(general.classify_reply(_letter("en", middle)), "full", middle)

    def test_fixed_price_with_a_hyphen_and_client_side_are_not_claims(self):
        self.assertEqual(general.fixed_without_advisory("A fixed-price contract and a fixed-term plan."), [])

    def test_a_clarifying_reply_leaves_the_client_request_armed(self):
        for question, draft in CLARIFY_THEN_DRAFT:
            self.ask("escribe un correo formal al cliente sobre el soporte de Java 17")
            self.assertEqual(self.stop(question), {}, question[:50])           # no gate on a question, nothing used up
            out = self.stop(draft)
            self.assertEqual(out["decision"], "block", draft[:50])               # the first substantive reply gets the gate
            self.assertIn("client-text gate", out["reason"])
            self.ask("lista los ficheros")
            self.assertEqual(self.stop(SHORT_ACK), {})                          # and only that one

    def test_the_armed_window_is_three_replies(self):
        question = CLARIFY_THEN_DRAFT[0][0]
        self.ask("escribe un correo formal al cliente sobre el soporte de Java 17")
        self.assertEqual(self.stop(question), {})
        self.assertEqual(self.stop(question), {})
        self.assertEqual(self.stop(question), {})        # the window is over; this reply used the request up
        self.assertEqual(self.stop(CLARIFY_THEN_DRAFT[0][1]), {})

    def test_a_letter_after_a_question_is_gated_at_once(self):
        self.ask("escribe un correo formal al cliente sobre el soporte de Java 17")
        self.assertEqual(self.stop(CLARIFY_THEN_DRAFT[0][0]), {})
        self.assertEqual(self.stop(DRAFT)["decision"], "block")                 # DRAFT is short but carries a version and a support word

    def test_the_armed_request_survives_the_users_answer(self):
        draft = CLARIFY_THEN_DRAFT[1][1]
        for answer in ("para el equipo técnico de Acme, imagen UBI 8", "redacta el correo para el equipo técnico de Acme"):
            self.ask("escribe un correo formal al cliente sobre el soporte de Java 17")
            self.assertEqual(self.stop(CLARIFY_THEN_DRAFT[0][0]), {})
            ctx = self.context(self.ask(answer))                                  # a new prompt between the question and the draft
            self.assertIn("client or another third party", ctx)
            out = self.stop(draft)
            self.assertEqual(out["decision"], "block", answer)
            self.assertIn("client-text gate", out["reason"])

    def test_a_client_paragraph_that_ends_in_a_question_is_still_a_draft(self):
        draft = CLARIFY_THEN_DRAFT[0][1] + " ¿Te parece bien así o prefieres otra redacción?"
        self.ask("escribe un correo formal al cliente sobre el soporte de Java 17")
        self.assertEqual(self.stop(draft)["decision"], "block")
        self.assertTrue(general.clarifying("¿Quieres que confirme Java 17 hasta 2029 o solo la fecha del soporte de la imagen que usáis?"))

    def test_an_absurd_window_counter_fails_safe_and_a_question_at_the_window_end_is_not_gated(self):
        self.ask("escribe un correo formal al cliente sobre el soporte de Java 17")
        flag = general._flag_path(self.SESSION)
        data = json.load(open(flag))
        with open(flag, "w") as f:
            f.write(json.dumps(dict(data, left=1)).replace("1}", "1e400}"))
        self.assertEqual(self.stop(CLARIFY_THEN_DRAFT[0][0]), {})                   # no crash; the counter is clamped
        self.ask("escribe un correo formal al cliente sobre el soporte de Java 17")
        data = json.load(open(flag))
        with open(flag, "w") as f:
            json.dump(dict(data, left=1), f)
        question = "Antes de seguir, dime para qué imagen quieres la respuesta y si debo mencionar el contrato de soporte extendido que ya tenéis firmado con el fabricante?"
        self.assertEqual(self.stop(question), {})                                  # the last reply of the window, a question: not gated

    def test_mitigation_and_an_arreglo_are_not_fix_claims(self):
        self.assertEqual(general.fixed_without_advisory("The mitigation plan is attached. El arreglo de pagos queda para el viernes."), [])
        self.assertEqual(len(general.fixed_without_advisory("Se arregló el problema.")), 1)
