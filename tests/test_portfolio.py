"""R-283 S2: `budgit portfolio` — one private list of every project (portfolio.json in the Budgit home), one read-only
view of all of them (= R-221's `status --all`), protected projects shown as counts only. Fake names and paths only."""
import json
import os
import subprocess
import sys
import time

from test_livework import LiveCase, CLI
import portfolio
import studiolib as sl

SECRET = "PLANTED-" + "SECRET-7Q"


class PortfolioCase(LiveCase):
    def run_cli(self, *args, cwd=None):
        r = subprocess.run([sys.executable, CLI, "portfolio", *args], env=dict(os.environ), capture_output=True,
                           text=True, cwd=cwd or self.uhome)
        return r

    def ok(self, *args):
        r = self.run_cli(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def view(self):
        return json.loads(self.ok("--json"))

    def rows(self):
        return {r["id"]: r for r in self.view()["rows"]}

    def protected_rows(self, out=None):
        return [r for r in (out or self.view())["rows"] if r["protected"]]

    def setup_three(self):
        root = self.proj("Dev")
        hub = self.mk(os.path.join(root, "hubby"), [("R-1", "doing"), ("R-2", "open"), ("R-3", "open", {"phase": "later"}),
                                                   ("R-4", "done")])
        self.mk(os.path.join(root, "huerto"), [("R-1", "open")])
        os.makedirs(os.path.join(root, "taller"))                       # a folder with no ledger: not managed
        self.config(hub=hub, root=root)
        return root, hub


class SeedTest(PortfolioCase):
    def test_first_run_seeds_the_list_with_every_project_and_every_folder_without_a_ledger(self):
        root, hub = self.setup_three()
        rows = self.rows()
        self.assertEqual(set(rows), {"hubby", "huerto", "taller"})
        self.assertTrue(rows["hubby"]["hub"])
        self.assertTrue(rows["huerto"]["managed"])
        self.assertFalse(rows["taller"]["managed"])
        saved = json.load(open(os.path.join(self.bhome, "portfolio.json")))
        self.assertEqual(saved["version"], 1)
        self.assertEqual({os.path.basename(e["path"]) for e in saved["projects"]}, {"hubby", "huerto", "taller"})

    def test_the_list_is_private_to_the_budgit_home_never_in_a_project(self):
        root, hub = self.setup_three()
        self.view()
        for dirpath, _, files in os.walk(root):
            self.assertNotIn("portfolio.json", files, dirpath)

    def test_protected_dirs_in_the_config_mark_a_project_protected(self):
        root, hub = self.setup_three()
        prot = self.mk(os.path.join(self.uhome, "Cofre"), [("R-1", "open")])
        with open(os.path.join(self.bhome, "config.json"), "w") as f:
            json.dump({"hub": hub, "projects_root": root, "protected_dirs": [prot]}, f)
        self.pointer("s-pmpriv01", prot)
        prot = self.protected_rows()
        self.assertEqual(len(prot), 1)
        self.assertTrue(prot[0]["id"].startswith("private~"))
        self.assertEqual(prot[0]["name"], "private")
        for out in (self.ok(), self.ok("--json")):
            self.assertNotIn("cofre", out.lower())

    def test_no_silent_cap(self):
        root = self.proj("Dev")
        hub = self.mk(os.path.join(root, "hubby"))
        for n in range(14):
            self.mk(os.path.join(root, "p%02d" % n), [("R-1", "open")])
        self.config(hub=hub, root=root)
        out = self.view()
        self.assertEqual(len(out["rows"]), 15)
        self.assertEqual(out["footer"]["cut"], 0)

    def test_a_project_found_later_is_proposed_not_added(self):
        root, hub = self.setup_three()
        self.view()
        self.mk(os.path.join(root, "nuevo"), [("R-1", "open")])
        out = self.view()
        self.assertNotIn("nuevo", {r["id"] for r in out["rows"]})
        self.assertIn("nuevo", out["footer"]["found"])


class NameTest(PortfolioCase):
    def test_name_order_project_json_then_list_then_folder(self):
        root, hub = self.setup_three()
        self.ok("add", os.path.join(root, "huerto"), "--name", "Huerto del barrio")
        self.assertEqual(self.rows()["huerto"]["name"], "Huerto del barrio")
        pj = os.path.join(root, "huerto", ".studio", "project.json")
        with open(pj, "w") as f:
            json.dump({"name": "El Huerto"}, f)
        self.assertEqual(self.rows()["huerto"]["name"], "El Huerto")
        self.assertEqual(self.rows()["taller"]["name"], "taller")
        self.assertEqual(portfolio.display_name(os.path.join(root, "huerto")), "El Huerto")


    def test_a_lone_surrogate_in_a_name_prints_as_a_question_mark_not_a_traceback(self):
        root, hub = self.setup_three()
        with open(os.path.join(root, "huerto", ".studio", "project.json"), "w") as f:
            f.write('{"name": "Huerto \\ud800"}')
        self.assertEqual(portfolio.display_name(os.path.join(root, "huerto")), "Huerto ?")
        r = self.run_cli("add", os.path.join(root, "huerto"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Huerto ?", r.stdout)
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class CountsTest(PortfolioCase):
    def test_tasks_and_chats_are_separate_counts_per_project(self):
        root, hub = self.setup_three()
        self.session("s-busy0001", hub, status="busy")
        self.session("s-wait0001", hub, status="waiting")
        self.session("s-idle0001", os.path.join(root, "huerto"), status="idle")
        rows = self.rows()
        self.assertEqual(rows["hubby"]["tasks"], {"doing": 1, "next": 1, "later": 1, "done": 1})
        self.assertEqual(rows["hubby"]["chats"], {"working": 1, "waiting": 1, "resting": 0})
        self.assertEqual(rows["huerto"]["chats"], {"working": 0, "waiting": 0, "resting": 1})
        self.assertIsNone(rows["taller"]["tasks"])

    def test_the_data_folder_kind(self):
        root, hub = self.setup_three()
        both = self.mk(os.path.join(root, "doble"), [("R-1", "open")])
        os.makedirs(os.path.join(both, ".budgit"))
        with open(os.path.join(both, ".budgit", "ledger.json"), "w") as f:
            f.write(json.dumps(sl.empty_ledger()))
        self.ok("add", both)
        rows = self.rows()
        self.assertEqual(rows["hubby"]["data"], ".studio")
        self.assertEqual(rows["doble"]["data"], "both")
        self.assertEqual(rows["taller"]["data"], "none")

    def test_waiting_chats_sort_first(self):
        root, hub = self.setup_three()
        self.session("s-wait0002", os.path.join(root, "huerto"), status="waiting")
        self.assertEqual(self.view()["rows"][0]["id"], "huerto")
        self.assertEqual([r["id"] for r in json.loads(self.ok("--json", "--by-name"))["rows"]], ["hubby", "huerto", "taller"])


class ProtectedTest(PortfolioCase):
    def test_a_planted_secret_never_leaves_a_protected_project(self):
        root, hub = self.setup_three()
        cofre = self.mk(os.path.join(root, "cofre"), [("R-1", "doing", {"title": SECRET, "text": SECRET})])
        with open(os.path.join(cofre, "CLAUDE.md"), "w") as f:
            f.write(SECRET + "\n")
        subprocess.run(["git", "init", "-q", "-b", "rama-" + SECRET, cofre], check=True)
        with open(os.path.join(cofre, SECRET + ".txt"), "w") as f:
            f.write("x")
        self.session("s-pmpriv02", cofre, status="busy", name="Chat " + SECRET)
        self.ok("add", cofre)
        self.ok("protect", "cofre")
        self.ok("pm", "cofre", "s-pmpriv02")
        text, js = self.ok(), self.ok("--json")
        for out in (text, js):
            self.assertNotIn(SECRET, out)
            self.assertNotIn("cofre", out.lower())
        row = self.protected_rows(json.loads(js))[0]
        self.assertTrue(row["protected"])
        self.assertEqual(row["tasks"]["doing"], 1)
        self.assertEqual(row["dirty"], 2)                   # CLAUDE.md and the planted file; the data folder never counts
        self.assertNotIn("path", row)
        self.assertIsNone(row["pm"]["title"])

    def test_project_json_private_alone_protects_and_guard_paths_do_not(self):
        root, hub = self.setup_three()
        own = self.mk(os.path.join(root, "propio"), [("R-1", "open", {"title": SECRET})])
        with open(os.path.join(own, ".studio", "project.json"), "w") as f:
            json.dump({"private": True}, f)
        guarded = self.mk(os.path.join(root, "guardado"), [("R-1", "open")])
        with open(os.path.join(guarded, ".studio", "project.json"), "w") as f:
            json.dump({"protected_paths": ["datos/"]}, f)
        self.ok("add", own)
        self.ok("add", guarded)
        rows = self.rows()
        prot = self.protected_rows()
        self.assertEqual(len(prot), 1)
        self.assertNotIn("path", prot[0])
        self.assertNotIn("propio", rows)
        self.assertFalse(rows["guardado"]["protected"])
        for out in (self.ok(), self.ok("--json")):
            self.assertNotIn(SECRET, out)
            self.assertNotIn("propio", out)

    def test_a_protected_project_shows_the_name_given_in_the_private_list(self):
        root, hub = self.setup_three()
        cofre = self.mk(os.path.join(root, "cofre"), [("R-1", "open")])
        with open(os.path.join(cofre, ".studio", "project.json"), "w") as f:
            json.dump({"private": True, "name": "Nombre interno"}, f)
        self.ok("add", cofre, "--name", "Vaquita")
        self.assertEqual(self.protected_rows()[0]["name"], "Vaquita")
        self.assertNotIn("Nombre interno", self.ok("--json"))

    def test_an_unprotected_pm_shows_its_chat_title(self):
        root, hub = self.setup_three()
        self.session("s-pmhub001", hub, status="busy", name="PM · hubby")
        self.ok("pm", "hubby", "s-pmhub001")
        self.assertEqual(self.rows()["hubby"]["pm"], {"session": "s-pmhub0", "title": "PM · hubby", "live": True})


class ManageTest(PortfolioCase):
    def test_off_keeps_the_row_with_its_reason_and_on_brings_it_back(self):
        root, hub = self.setup_three()
        self.ok("off", "huerto", "--reason", "pausado")
        row = self.rows()["huerto"]
        self.assertEqual(row["channel"], "off")
        self.assertEqual(row["reason"], "pausado")
        self.ok("on", "huerto")
        self.assertEqual(self.rows()["huerto"]["channel"], "current")

    def test_off_needs_a_reason_and_an_unknown_project_fails(self):
        root, hub = self.setup_three()
        self.assertNotEqual(self.run_cli("off", "huerto").returncode, 0)
        r = self.run_cli("pm", "nadie", "s-x0000001")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nadie", r.stderr)

    def test_adopt_marks_a_folder_managed(self):
        root, hub = self.setup_three()
        self.mk(os.path.join(root, "taller"), [("R-1", "open")])
        self.ok("add", os.path.join(root, "taller"))
        self.assertTrue(self.rows()["taller"]["managed"])


class TextViewTest(PortfolioCase):
    def test_spanish_labels_name_the_unit_and_the_scope(self):
        root, hub = self.setup_three()
        out = self.ok()
        self.assertIn("Todos los proyectos", out)
        self.assertIn("Tareas (en curso · por hacer · ideas)", out)
        self.assertIn("Chats (trabajando · te esperan · en reposo)", out)
        self.assertIn("sin llevar", out)

    def test_english_labels(self):
        root, hub = self.setup_three()
        os.environ["BUDGIT_LANG"] = "en"
        out = self.ok()
        self.assertIn("All projects", out)
        self.assertIn("Tasks (in progress · to do · ideas)", out)

    def test_a_private_column_says_si_or_no_from_is_private(self):
        root, hub = self.setup_three()
        own = self.mk(os.path.join(root, "propio"), [("R-1", "open")])
        with open(os.path.join(own, ".studio", "project.json"), "w") as f:
            json.dump({"private": True}, f)
        self.ok("add", own)
        lines = self.ok().splitlines()
        head = next(l for l in lines if l.startswith("Proyecto"))
        self.assertIn("Privado", head)
        col = head.index("Privado")
        cell = lambda name: next(l for l in lines if l.startswith(name + " "))[col:col + 7].strip()
        self.assertEqual(cell("hubby"), "no")
        self.assertEqual(cell("privado"), "sí")              # protected: the name stays private, the counts show

    def test_a_protected_dirs_folder_with_no_ledger_says_si_with_counts_only(self):
        root, hub = self.setup_three()
        taller = os.path.join(root, "taller")
        with open(os.path.join(self.bhome, "config.json"), "w") as f:
            json.dump({"hub": hub, "projects_root": root, "protected_dirs": [taller]}, f)
        self.ok("add", taller)
        prot = self.protected_rows()
        self.assertEqual([(r["name"], r["managed"]) for r in prot], [("private", False)])
        lines = self.ok().splitlines()
        col = next(l for l in lines if l.startswith("Proyecto")).index("Privado")
        self.assertEqual(next(l for l in lines if l.startswith("privado "))[col:col + 7].strip(), "sí")
        for out in (self.ok(), self.ok("--json")):
            self.assertNotIn("taller", out)

    def test_the_pm_column_never_shows_a_session_id(self):
        root, hub = self.setup_three()
        cofre = self.mk(os.path.join(root, "cofre"), [("R-1", "open")])
        with open(os.path.join(cofre, ".studio", "project.json"), "w") as f:
            json.dump({"private": True}, f)
        self.ok("add", cofre)
        self.session("s-pmpriv01", cofre, status="idle", name="PM · cofre")
        self.ok("pm", portfolio.project_id(cofre), "s-pmpriv01")
        self.ok("pm", "huerto", "s-pmgone01")                    # its chat is closed: no title known
        out = self.ok()
        lines = out.splitlines()
        row = lambda name: next(l for l in lines if l.startswith(name + " "))
        self.assertIn("PM · privado", row("privado"))           # the private row's own name, never its chat title
        self.assertIn("PM sin nombre †", row("huerto"))
        self.assertNotIn("s-pm", out)
        self.assertNotIn("cofre", out)
        self.ok("add", cofre, "--name", "Vaquita")
        self.assertIn("PM · Vaquita", self.ok())

    def test_two_private_rows_are_numbered_in_list_order_and_keep_their_numbers(self):
        root, hub = self.setup_three()
        cofre, arca = (self.mk(os.path.join(root, n), [("R-1", "open")]) for n in ("cofre", "arca"))
        with open(os.path.join(self.bhome, "config.json"), "w") as f:
            json.dump({"hub": hub, "projects_root": root, "protected_dirs": [cofre, arca]}, f)
        self.ok("add", cofre)
        self.ok("add", arca)
        self.session("s-pmarca01", arca, status="idle")
        self.ok("pm", portfolio.project_id(arca), "s-pmarca01")
        listed = [e["path"] for e in portfolio.load()["projects"] if e["path"] in (cofre, arca)]
        first = {r["id"]: r["private_n"] for r in self.protected_rows()}
        self.assertEqual(first, {portfolio.project_id(p): n for n, p in enumerate(listed, 1)})
        n_arca = first[portfolio.project_id(arca)]
        self.session("s-busy0001", arca, status="waiting")          # a waiting chat moves arca to the top
        self.assertEqual({r["id"]: r["private_n"] for r in self.protected_rows()}, first)
        lines = self.ok().splitlines()
        self.assertTrue(any(l.startswith("privado %d " % (3 - n_arca)) for l in lines))
        self.assertIn("PM · privado %d" % n_arca, next(l for l in lines if l.startswith("privado %d " % n_arca)))
        self.assertNotIn("cofre", "\n".join(lines))

    def test_one_private_row_has_no_number(self):
        root, hub = self.setup_three()
        cofre = self.mk(os.path.join(root, "cofre"), [("R-1", "open")])
        with open(os.path.join(self.bhome, "config.json"), "w") as f:
            json.dump({"hub": hub, "projects_root": root, "protected_dirs": [cofre]}, f)
        self.ok("add", cofre)
        self.assertNotIn("private_n", self.protected_rows()[0])
        self.assertTrue(any(l.startswith("privado ") and not l.startswith("privado 1") for l in self.ok().splitlines()))

    def test_tasks_and_chats_sit_on_the_line_under_the_project_and_fit_80_columns(self):
        root, hub = self.setup_three()
        self.mk(os.path.join(root, "un-proyecto-con-un-nombre-larguisimo"), [("R-1", "open")])
        self.view()
        self.ok("off", "huerto", "--reason", "en pausa hasta diciembre")
        lines = self.ok().splitlines()
        self.assertTrue(any(l.startswith("un-proyecto-con-un-n…") for l in lines))
        under = lines[next(n for n, l in enumerate(lines) if l.startswith("huerto ")) + 1]
        self.assertTrue(under.strip().startswith("fuera: en pausa hasta diciembre · tareas"))
        self.ok("off", "huerto", "--reason", "esperando a que el cliente conteste el correo del viernes sobre el presupuesto")
        lines = self.ok().splitlines()
        under = lines[next(n for n, l in enumerate(lines) if l.startswith("huerto ")) + 1]
        self.assertIn("…", under)
        i = next(n for n, l in enumerate(lines) if l.startswith("hubby "))
        self.assertEqual(lines[i + 1].strip(), "tareas 1 · 1 · 1    chats 0 · 0 · 0")
        self.assertIn("sin llevar", lines[next(n for n, l in enumerate(lines) if l.startswith("taller ")) + 1])
        table = [l for l in lines if l.startswith(("Proyecto", "hubby", "huerto", "taller", "un-", "    "))]
        self.assertTrue(all(len(l) <= 80 for l in table), "\n".join(lines))         # the lines under each name too
        self.assertTrue(under.rstrip().endswith("chats 0 · 0 · 0"))

    def test_the_found_footer_fits_80_columns(self):
        root, hub = self.setup_three()
        self.view()                                                  # the list is seeded; new folders are "found"
        for name in ("carpeta-nueva-uno", "carpeta-nueva-dos", "carpeta-nueva-tres", "carpeta-nueva-cuatro"):
            self.mk(os.path.join(root, name), [("R-1", "open")])
        lines = self.ok().splitlines()
        found = next(l for l in lines if l.startswith("Encontrados"))
        self.assertLessEqual(len(found), 80, found)
        self.assertIn("más", found)
        self.assertIn("`budgit portfolio add <ruta>` añade uno.", lines)
        self.mk(os.path.join(root, "x" * 90), [("R-1", "open")])
        self.assertTrue(all(len(l) <= 80 for l in self.ok().splitlines()))

    def test_runs_from_a_folder_with_no_ledger(self):
        root, hub = self.setup_three()
        r = self.run_cli(cwd=os.path.join(root, "taller"))
        self.assertEqual(r.returncode, 0, r.stderr)


class ProjectIdTest(PortfolioCase):
    def test_the_id_matches_the_view_and_hides_a_protected_folder(self):
        root, hub = self.setup_three()
        own = self.mk(os.path.join(root, "propio"), [("R-1", "open")])
        with open(os.path.join(own, ".studio", "project.json"), "w") as f:
            json.dump({"private": True}, f)
        self.ok("add", own)
        ids = {r["id"] for r in self.view()["rows"]}
        self.assertEqual(portfolio.project_id(hub), "hubby")
        pid = portfolio.project_id(own)
        self.assertTrue(pid.startswith("private~"))
        self.assertIn(pid, ids)
        self.assertNotIn("propio", pid)

    def test_a_folder_not_in_the_list_still_gets_an_id(self):
        root, hub = self.setup_three()
        self.view()
        self.assertEqual(portfolio.project_id(os.path.join(root, "nuevo")), "nuevo")


class PerfTest(PortfolioCase):
    def test_ten_projects_one_with_a_hundred_dirty_files_under_one_second(self):
        root = self.proj("Dev")
        hub = self.mk(os.path.join(root, "hubby"))
        for n in range(9):
            self.mk(os.path.join(root, "p%d" % n), [("R-1", "open")])
        dirty = os.path.join(root, "p0")
        subprocess.run(["git", "init", "-q", dirty], check=True)
        for i in range(100):
            with open(os.path.join(dirty, "f%03d.txt" % i), "w") as f:
                f.write("x")
        self.config(hub=hub, root=root)
        portfolio.view()                                   # seeds the list
        import resource

        def cpu():                                     # CPU of this process plus its children, not wall seconds (R-1282)
            a, b = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
            return a.ru_utime + a.ru_stime + b.ru_utime + b.ru_stime
        t = cpu()
        out = portfolio.view()
        self.assertLess(cpu() - t, 1.0)
        self.assertEqual({r["id"]: r for r in out["rows"]}["p0"]["dirty"], 100)
