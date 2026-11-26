"""R-283 S2b (R-284, R-285): real project names and one vocabulary. The status line starts with the project's name and
says it counts tasks; feed rows carry `project_name`; a chat is labelled PM or "sin tarea", never "en curso".
New feed keys are additive: `counts` and `name` keep their meaning for the current Libreta."""
import json
import os
import subprocess
import sys
import time

from test_livework import LiveCase, CLI, iso
import studiolib as sl


def line(sit, lang="es"):
    base = {"doing": 0, "next": 0, "later": 0, "week": None}
    base.update(sit)
    return sl.status_line(base, lang)


class StatusLineNameTest(LiveCase):
    def test_the_line_starts_with_the_project_name_and_says_tasks(self):
        self.assertEqual(line({"name": "Huerto", "doing": 2, "next": 3, "later": 1}),
                         "Huerto · tareas: ✿ 2 en curso ⁘ 3 por hacer ✧ 1 idea")
        self.assertEqual(line({"name": "Huerto", "doing": 2, "next": 3, "later": 4}, "en"),
                         "Huerto · tasks: ✿ 2 in progress ⁘ 3 to do ✧ 4 ideas")

    def test_nothing_says_no_tasks_in_budgit(self):
        self.assertEqual(line({"name": "Cantera"}), "Cantera · sin tareas en Budgit")

    def test_without_a_name_the_line_is_unchanged(self):
        self.assertEqual(line({}), "budgit  nada pendiente")

    def test_a_long_name_gives_way_so_the_line_stays_within_80(self):
        out = line({"name": "Un nombre de proyecto larguisimo de verdad", "doing": 5, "next": 131, "later": 43,
                    "week": 63, "forecast": "out", "out_day": "dom", "out_hour": 23})
        head = out.split(" · ", 1)[0]
        self.assertTrue(head.endswith("…"))
        self.assertLessEqual(len(out), 80)
        self.assertTrue(out.endswith("fin dom 23h"))

    def test_the_longest_line_fits_80_and_keeps_a_readable_name(self):
        for lang, day in (("es", "mié"), ("en", "Wed")):
            out = line({"name": "Un nombre de proyecto larguisimo", "doing": 12, "next": 144, "later": 139,
                        "week": 100, "forecast": "out", "out_day": day, "out_hour": 23}, lang)
            self.assertLessEqual(len(out), 80, out)
            self.assertGreaterEqual(len(out.split(" · ", 1)[0]), sl.NAME_MIN)

    def test_ansi_colours_do_not_count_toward_the_width(self):
        sit = {"name": "Budgit", "doing": 5, "next": 131, "later": 43, "week": 63, "forecast": "ok",
               "out_day": "", "out_hour": 0}
        plain = sl.status_line(dict(sit), "es")
        coloured = sl.status_line(dict(sit), "es", ansi=True)
        import re
        self.assertEqual(re.sub(r"\x1b\[[0-9;]*m", "", coloured), plain)

    def cli_line(self, project):
        r = subprocess.run([sys.executable, CLI, "status", "--line", "--project", project], env=dict(os.environ),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.rstrip("\n")

    def test_the_cli_uses_the_name_order(self):
        p = self.mk(self.proj("huerto"), [("R-1", "doing")])
        self.config(hub=p)
        self.assertTrue(self.cli_line(p).startswith("huerto · tareas: ✿ 1 en curso"))
        subprocess.run([sys.executable, CLI, "portfolio", "add", p, "--name", "Huerto vecinal"], env=dict(os.environ),
                       capture_output=True, text=True, check=True)
        self.assertTrue(self.cli_line(p).startswith("Huerto vecinal · tareas:"))
        with open(os.path.join(sl.studio_dir(p), "project.json"), "w") as f:
            json.dump({"name": "El Huerto"}, f)
        self.assertTrue(self.cli_line(p).startswith("El Huerto · tareas:"))

    def test_a_private_project_shows_its_own_name_in_its_own_chat(self):
        p = self.mk(self.proj("cofre"), [("R-1", "open")])
        with open(os.path.join(sl.studio_dir(p), "project.json"), "w") as f:
            json.dump({"private": True, "name": "Cofre"}, f)
        self.config(hub=p)
        self.assertTrue(self.cli_line(p).startswith("Cofre · tareas:"))


class FeedNamesTest(LiveCase):
    def setup(self):
        t = time.time()
        hub = self.mk(self.proj("hubby"), [
            ("R-1", "doing", {"doing_by": "s-lane0001", "doing_since": iso(t - 60)}),
            ("R-2", "done", {"done_at": iso(t - 30)})])
        with open(os.path.join(sl.studio_dir(hub), "project.json"), "w") as f:
            json.dump({"name": "Budgit"}, f)
        huerto = self.mk(self.proj("huerto"), [("R-1", "open")])
        self.config(hub=hub, root=os.path.dirname(huerto))
        self.session("s-lane0001", hub, status="busy", name="Carril")
        self.session("s-pmhb0001", hub, status="waiting", name="PM · Budgit")
        self.session("s-free0001", huerto, status="busy", name="Suelto")
        subprocess.run([sys.executable, CLI, "portfolio", "pm", "hubby", "s-pmhb0001"], env=dict(os.environ),
                       capture_output=True, text=True, check=True)
        return hub, huerto

    def test_rows_carry_the_real_project_name(self):
        hub, huerto = self.setup()
        out = self.feed(hub)
        names = {p["id"]: p for p in out["projects"]}
        self.assertEqual(names["hubby"]["project_name"], "Budgit")
        self.assertEqual(names["hubby"]["name"], "hubby")             # unchanged: the panel still reads it
        self.assertNotIn("project_name", names["huerto"])               # same as `name`: not sent twice
        for w in out["working"] + out["paused"] + out["recent_done"]:   # other rows point at a project by id only
            self.assertIn(w["project"], names)

    def test_chats_are_labelled_lane_pm_or_no_task(self):
        hub, huerto = self.setup()
        out = self.feed(hub)
        rows = {w["session"]: w for w in out["working"]}
        self.assertEqual(rows["s-lane00"]["kind"], "claim")
        self.assertNotIn("role", rows["s-lane00"])
        self.assertEqual(rows["s-pmhb00"]["role"], "pm")
        self.assertEqual(rows["s-free00"]["kind"], "session")         # a session row with no role: no task
        self.assertNotIn("role", rows["s-free00"])
        self.assertEqual(rows["s-free00"]["project"], "huerto")
        self.assertEqual(out["t"]["book.role.pm"], "PM")
        self.assertEqual(out["t"]["book.role.no_task"], "sin tarea")

    def test_chat_and_task_counts_are_separate_and_counts_is_unchanged(self):
        hub, huerto = self.setup()
        out = self.feed(hub)
        row = {p["id"]: p for p in out["projects"]}["hubby"]
        self.assertEqual(set(row["counts"]), {"doing", "next", "later", "done"})
        self.assertEqual(row["chats"], {"working": 1, "waiting": 1})      # zero counts are left out
        self.assertEqual(out["totals"]["chats"], {"working": 2, "waiting": 1, "resting": 0})
        self.assertEqual(out["totals"]["tasks"]["doing"], 1)

    def test_a_private_project_keeps_the_name_private_everywhere(self):
        hub, huerto = self.setup()
        cofre = self.mk(self.proj("cofre"), [("R-1", "doing", {"doing_by": "s-cofr0001", "doing_since": iso(time.time())})])
        with open(os.path.join(sl.studio_dir(cofre), "project.json"), "w") as f:
            json.dump({"private": True, "name": "Nombre interno"}, f)
        self.session("s-cofr0001", cofre, status="busy", name="Chat interno")
        out = self.feed(hub)
        dump = json.dumps(out, ensure_ascii=False)
        for word in ("Nombre interno", "Chat interno", "cofre"):
            self.assertNotIn(word, dump)
        prow = [p for p in out["projects"] if p.get("private")][0]
        self.assertEqual(prow.get("project_name", prow["name"]), "private")
        self.assertEqual(prow["chats"]["working"], 1)
