"""R-335 class 4: one typed `/budgit-trust work` is the standing yes of ONE project and cannot be replayed for another."""
import json
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_guard_trust import GuardCase, sl  # noqa: E402


class GrantBindingTest(GuardCase):
    def setUp(self):
        super().setUp()
        self.other = os.path.join(self.tmp, "other-proj")
        shutil.copytree(self.project, self.other, symlinks=True)
        sl.activate(self.other, bind=False)

    def entries(self):
        return json.load(open(sl.trust_path())).get("work") or {}

    def test_a_grant_is_stored_with_the_project_it_was_typed_for(self):
        self.assertTrue(self.grant())
        self.assertEqual(list(self.entries()), [sl._trust_key(self.project)])
        self.assertFalse(sl.trust_work_on(self.other))

    def test_an_entry_copied_to_another_projects_key_is_no_grant_there(self):
        self.assertTrue(self.grant())
        data = json.load(open(sl.trust_path()))
        data["work"][sl._trust_key(self.other)] = data["work"][sl._trust_key(self.project)]
        sl._save_dict(sl.trust_path(), data)
        self.assertFalse(sl.trust_work_on(self.other))
        self.assertFalse(sl.trust_work_on(self.project))               # a proof used twice voids both: retype the yes

    def test_an_entry_moved_to_another_projects_key_is_no_grant_there(self):
        self.assertTrue(self.grant())
        data = json.load(open(sl.trust_path()))
        data["work"] = {sl._trust_key(self.other): data["work"][sl._trust_key(self.project)]}
        sl._save_dict(sl.trust_path(), data)
        self.assertFalse(sl.trust_work_on(self.other), "the entry names the project it was typed for")
        self.assertFalse(sl.trust_work_on(self.project))

    def test_the_same_typed_line_cannot_back_two_projects(self):
        path = self.transcript()
        self.assertTrue(sl.grant_trust(self.project, path))
        self.assertTrue(sl.grant_trust(self.other, path))             # the same line, typed once
        self.assertFalse(sl.trust_work_on(self.project), "the earlier project lost it: one line, one project")
        self.assertTrue(sl.trust_work_on(self.other))
        self.assertEqual(len(self.entries()), 1)

    def test_a_later_typed_line_grants_each_project_on_its_own(self):
        path = self.transcript("/budgit-trust work")
        self.assertTrue(sl.grant_trust(self.project, path))
        with open(path, "a") as fh:
            fh.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "ok"}}) + "\n")
            fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": "/budgit-trust work"}}) + "\n")
        self.assertTrue(sl.grant_trust(self.other, path))
        self.assertTrue(sl.trust_work_on(self.project))
        self.assertTrue(sl.trust_work_on(self.other))

    def test_two_entries_sharing_one_proof_by_hand_grant_neither(self):
        self.assertTrue(self.grant())
        data = json.load(open(sl.trust_path()))
        data["work"]["zzz-other"] = dict(data["work"][sl._trust_key(self.project)], project=self.other)
        data["work"][sl._trust_key(self.other)] = data["work"]["zzz-other"]
        sl._save_dict(sl.trust_path(), data)
        self.assertFalse(sl.trust_work_on(self.project))
        self.assertFalse(sl.trust_work_on(self.other))


if __name__ == "__main__":
    unittest.main()
