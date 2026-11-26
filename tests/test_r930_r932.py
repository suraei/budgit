"""R-932: the role comes from registration, never from having a parent; a protected PM is never a side session of a Budgit
coordinator. R-930 (R-834): a clear with a pending handoff resumes it."""
import json
import os

from test_team_roles import RolesBase, sid
import handoff
import portfolio
import roles
import studiolib as sl


def parent_pointer(home, n, project, parent):
    folder = os.path.join(home, "sessions")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, sid(n) + ".json"), "w", encoding="utf-8") as f:
        json.dump({"project": project, "parent": parent}, f)


class RoleFromRegistrationTest(RolesBase):
    def first(self, n, project):
        out = roles.session_start_texts({"session_id": sid(n), "cwd": project}, project)
        return out[0].splitlines()[0] if out else None

    def test_detached_tm_with_a_parent_id_is_tm(self):  # SHALL R-932 detached TM gets role tm
        self.write_config()
        project = self.make_project("alpha")
        parent_pointer(self.home, 1, self.hub, sid(50))
        self.assertEqual(self.first(1, self.hub), "budgit team role: lane")   # R-939: a child in the hub with no registration is a lane
        portfolio.set_tm(sid(2))                                       # registered earlier, pointer still names a parent
        parent_pointer(self.home, 2, project, sid(50))
        self.assertEqual(self.first(2, project), "budgit team role: TM")

    def test_registered_pm_with_a_parent_is_pm(self):  # SHALL R-932 registered PM with a parent gets role pm
        self.write_config()
        project = self.make_project("alpha")
        portfolio.set_pm(project, sid(3))
        parent_pointer(self.home, 3, project, sid(50))
        self.assertEqual(self.first(3, project), "budgit team role: PM")

    def test_unregistered_child_is_lane(self):  # SHALL R-932 unregistered child gets role lane
        self.write_config()
        project = self.make_project("alpha")
        portfolio.set_pm(project, sid(3))
        parent_pointer(self.home, 4, project, sid(3))
        self.assertEqual(self.first(4, project), "budgit team role: lane")

    def test_protected_pm_as_side_session_is_flagged(self):  # SHALL R-932 protected PM as a side session is flagged
        self.write_config()
        project = self.make_project("alpha")
        secret = os.path.realpath(os.path.join(self.tmp, "vault", "p"))
        os.makedirs(secret, exist_ok=True)
        prot = sl.activate(secret, None, bind=False)
        self.write_config(protected_dirs=[os.path.dirname(prot)])
        portfolio.set_pm(project, sid(3))
        parent_pointer(self.home, 5, prot, sid(3))
        out = roles.session_start_texts({"session_id": sid(5), "cwd": prot}, prot)
        self.assertTrue(any("WARNING" in t and "protected" in t for t in out), out)
        self.assertEqual(roles.assign(sid(5), prot, prot), [])
        self.assertIsNone(self.fields()[1].get("p"))

    def test_child_in_hub_without_tm_is_lane(self):  # SHALL R-939 a session with a parent in the hub, no TM recorded, is a lane
        self.write_config()
        self.make_project("alpha")
        parent_pointer(self.home, 6, self.hub, sid(50))
        self.assertEqual(self.first(6, self.hub), "budgit team role: lane")
        self.assertEqual(roles.assign(sid(6), self.hub, self.hub), [])
        self.assertIsNone((portfolio.load() or {}).get("tm"))

    def test_registered_tm_with_parent_in_hub_is_tm(self):  # SHALL R-939 a registered TM with a parent stays TM
        self.write_config()
        portfolio.set_tm(sid(8))
        parent_pointer(self.home, 8, self.hub, sid(50))
        self.assertEqual(self.first(8, self.hub), "budgit team role: TM")
        self.assertEqual((portfolio.load() or {}).get("tm"), sid(8))

    def test_root_in_hub_without_tm_becomes_tm(self):  # SHALL R-939 a session with no parent in the hub, no TM recorded, is TM
        self.write_config()
        self.assertEqual(self.first(9, self.hub), "budgit team role: TM")
        self.assertEqual((portfolio.load() or {}).get("tm"), sid(9))

    def test_reorganize_skips_a_child_hub_pointer_for_tm(self):  # SHALL R-939 reorganize never makes a session with a parent TM
        self.write_config()
        parent_pointer(self.home, 10, self.hub, sid(50))           # child hub pointer
        parent_pointer(self.home, 11, self.hub, None)              # root hub pointer
        data = {"projects": []}
        roles._plan(data)
        self.assertEqual(data.get("tm"), sid(11))                  # root becomes TM
        parent_pointer(self.home, 11, self.hub, sid(50))           # now only a child pointer remains
        data = {"projects": []}
        roles._plan(data)
        self.assertIsNone(data.get("tm"))                          # no TM

    def test_reorganize_skips_a_child_project_pointer_for_pm(self):  # SHALL R-939 reorganize never makes a session with a parent the PM
        self.write_config()
        project = os.path.realpath(self.make_project("alpha"))
        parent_pointer(self.home, 12, project, sid(50))            # child project pointer alone
        data = {"projects": [{"path": project}], "tm": sid(60)}
        roles._plan(data)
        self.assertIsNone(data["projects"][0].get("pm"))           # no PM
        parent_pointer(self.home, 13, project, None)               # root project pointer added
        data = {"projects": [{"path": project}], "tm": sid(60)}
        roles._plan(data)
        self.assertEqual(data["projects"][0].get("pm"), sid(13))   # PM is the root, not the child


class ClearResumeTest(RolesBase):
    def make_handoff(self, project, written_at):
        folder = sl.ensure_subdir(project, "private", "handoff")
        sl.atomic_write(os.path.join(folder, "abc.md"), "## Next steps\nx\n")
        sl.atomic_write(os.path.join(folder, "abc.facts.json"), json.dumps({"session": "s", "written_at": written_at, "facts": {}}))
        return os.path.join(folder, "abc.md")

    def test_clear_with_pending_handoff_resumes_it(self):  # SHALL R-930 clear with a pending handoff yields the resume prompt
        self.write_config()
        project = self.make_project("alpha")
        path = self.make_handoff(project, sl.now())
        line = handoff.resume_line(project)
        self.assertIn(path, line)
        self.assertIn("resume its Next steps", line)
        self.assertIsNone(handoff.resume_line(project))               # once per handoff

    def test_clear_with_no_handoff_yields_nothing(self):  # SHALL R-930 clear with no handoff yields nothing
        self.write_config()
        project = self.make_project("alpha")
        self.assertIsNone(handoff.resume_line(project))
        self.make_handoff(project, "2020-01-01T00:00:00.000Z")        # an old one is not pending
        self.assertIsNone(handoff.resume_line(project))

    def test_hook_clear_event_injects_it(self):  # SHALL R-930 SessionStart source clear carries the resume prompt
        self.write_config()
        project = self.make_project("alpha")
        path = self.make_handoff(project, sl.now())
        out = self.run_hook({"session_id": sid(7), "cwd": project, "source": "clear"})
        self.assertIn(path, json.dumps(out))
        out = self.run_hook({"session_id": sid(7), "cwd": project, "source": "startup"})
        self.assertNotIn("resume its Next steps", json.dumps(out))
