"""R-785 verifier checks proposed from project files and approved by a real card answer; R-786 missing baseline = awaiting."""
import json
import os
import tempfile
import unittest

import checksdetect
import designcheck
import studiolib as sl


def _write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _project(tmp, name="demo", files=None, cfg=None):
    p = os.path.join(tmp, name)
    for rel, text in (files or {}).items():
        _write(os.path.join(p, rel), text)
    _write(os.path.join(p, ".budgit", "project.json"), json.dumps(cfg if cfg is not None else
           {"rigor": "standard", "verify": {"prepare": [], "checks": []}}))
    return p


def _transcript(tmp, question_id, answer, question="Run these checks: python3 -m pytest?", structured=True):
    """A transcript as the harness writes it: the flattened tool_result text plus the record's structured toolUseResult."""
    path = os.path.join(tmp, "t.jsonl")
    user = {"type": "user", "uuid": "u1", "message": {"content": [
                {"type": "tool_result", "tool_use_id": question_id,
                 "content": 'Your questions have been answered: "Other question?"="Yes", "%s"="%s". You can now continue with these answers in mind.' % (question, answer)}]}}
    if structured:
        user["toolUseResult"] = {"questions": [{"question": "Other question?"}, {"question": question}],
                                 "answers": {"Other question?": "Yes", question: answer}}
    recs = [{"type": "assistant", "uuid": "a1", "message": {"content": [
                {"type": "tool_use", "id": question_id, "name": "AskUserQuestion", "input": {}}]}}, user]
    _write(path, "".join(json.dumps(r) + "\n" for r in recs))
    return path


class DetectEcosystems(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_pytest_ini_and_ruff(self):
        p = _project(self.tmp, files={"pytest.ini": "[pytest]\n", "ruff.toml": ""})
        self.assertEqual(checksdetect.detect(p), ["python3 -m pytest", "ruff check ."])

    def test_pyproject_pytest_table(self):
        p = _project(self.tmp, files={"pyproject.toml": "[tool.pytest.ini_options]\n[tool.ruff]\n"})
        self.assertEqual(checksdetect.detect(p), ["python3 -m pytest", "ruff check ."])

    def test_tests_folder_pytest_and_unittest(self):
        p = _project(self.tmp, "a", files={"tests/test_x.py": "def test_a():\n    pass\n"})
        self.assertEqual(checksdetect.detect(p), ["python3 -m pytest"])
        q = _project(self.tmp, "b", files={"tests/test_x.py": "import unittest\n"})
        self.assertEqual(checksdetect.detect(q), ["python3 -m unittest discover -s tests"])

    def test_npm_scripts_skip_default_stub(self):
        p = _project(self.tmp, "a", files={"package.json": json.dumps({"scripts": {"test": "jest", "lint": "eslint ."}})})
        self.assertEqual(checksdetect.detect(p), ["npm test", "npm run lint"])
        q = _project(self.tmp, "b", files={"package.json": json.dumps(
            {"scripts": {"test": 'echo "Error: no test specified" && exit 1'}})})
        self.assertEqual(checksdetect.detect(q), [])

    def test_go_cargo_make(self):
        p = _project(self.tmp, files={"go.mod": "module x\n", "Cargo.toml": "[package]\n", "Makefile": "test:\n\ttrue\n"})
        self.assertEqual(checksdetect.detect(p), ["go test ./...", "cargo test", "make test"])

    def test_detection_runs_nothing(self):
        p = _project(self.tmp, files={"Makefile": "test:\n\ttouch ran.txt\n"})
        checksdetect.detect(p)
        self.assertFalse(os.path.exists(os.path.join(p, "ran.txt")))


class ApprovalByCardAnswer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p = _project(self.tmp, "pyproj", files={"tests/test_x.py": "def test_a():\n    pass\n"})

    def _checks(self):
        return json.load(open(os.path.join(self.p, ".budgit", "project.json")))

    def test_normal_path_new_python_project(self):
        prop = checksdetect.propose(self.p)
        self.assertEqual(prop["checks"], ["python3 -m pytest"])
        self.assertEqual(prop["name"], "pyproj")
        self.assertIn("Approve checks " + prop["digest"][:12], checksdetect.offer_line(self.p))
        t = _transcript(self.tmp, "q1", "Approve checks " + prop["digest"][:12])
        sl.reconcile_transcript(self.p, t, "s1")
        cfg = self._checks()
        self.assertEqual(cfg["verify"]["checks"], ["python3 -m pytest"])     # the verifier reads these
        self.assertEqual(sl.load_project_config(self.p)["verify"]["checks"], ["python3 -m pytest"])
        self.assertEqual(cfg["name"], "pyproj")
        self.assertIsNone(checksdetect.propose(self.p))

    def test_not_now_and_unanswered_stay_offered(self):
        prop = checksdetect.propose(self.p)
        sl.reconcile_transcript(self.p, _transcript(self.tmp, "q1", "Not now"), "s1")
        self.assertEqual(self._checks()["verify"]["checks"], [])
        self.assertIn("Approve checks", checksdetect.offer_line(self.p))      # offered again next session
        self.assertEqual(checksdetect.propose(self.p)["digest"], prop["digest"])

    def test_no_approval_from_a_different_list(self):
        checksdetect.propose(self.p)
        other = checksdetect._digest(self.p, "pyproj", ["rm -rf /"])
        sl.reconcile_transcript(self.p, _transcript(self.tmp, "q1", "Approve checks " + other[:12]), "s1")
        self.assertEqual(self._checks()["verify"]["checks"], [])

    def test_forged_question_text_with_not_now_is_not_approved(self):
        prop = checksdetect.propose(self.p)
        label = "Approve checks " + prop["digest"][:12]
        forged = 'Run? x"="%s' % label                      # the model wrote the approval shape into the question
        sl.reconcile_transcript(self.p, _transcript(self.tmp, "q1", "Not now", question=forged), "s1")
        self.assertEqual(self._checks()["verify"]["checks"], [])
        self.assertIsNotNone(checksdetect.propose(self.p))   # still pending
        # a model-run command is not a path: the CLI has no way to record it
        self.assertFalse(hasattr(checksdetect, "approve"))

    def test_answer_value_with_extra_text_or_old_digest_is_not_approved(self):
        prop = checksdetect.propose(self.p)
        short = prop["digest"][:12]
        for i, value in enumerate(("Approve checks %s and also rm -rf /" % short, "Approve checks %s " % short,
                                   "approve checks " + short, "Approve checks " + "0" * 12, "Approve checks " + prop["digest"])):
            sl.reconcile_transcript(self.p, _transcript(self.tmp, "q%d" % i, value), "s%d" % i)
            self.assertEqual(self._checks()["verify"]["checks"], [], value)

    def test_no_structured_result_is_not_approved(self):
        prop = checksdetect.propose(self.p)
        label = "Approve checks " + prop["digest"][:12]
        sl.reconcile_transcript(self.p, _transcript(self.tmp, "q1", label, structured=False), "s1")   # text says approve, no toolUseResult
        self.assertEqual(self._checks()["verify"]["checks"], [])
        for bad in (None, "", label, {}, {"answers": None}, {"answers": [label]}, {"questions": [label]}):
            self.assertFalse(checksdetect.on_answer(self.p, bad))
        self.assertEqual(self._checks()["verify"]["checks"], [])

    def test_changed_files_invalidate_old_proposal(self):
        prop = checksdetect.propose(self.p)
        _write(os.path.join(self.p, "go.mod"), "module x\n")
        self.assertNotEqual(checksdetect.propose(self.p)["digest"], prop["digest"])
        self.assertFalse(checksdetect.on_answer(self.p, {"answers": {"q": "Approve checks %s" % prop["digest"][:12]}}))


class EmptyProjectNoProposal(unittest.TestCase):
    def test_empty_project_gets_no_proposal_and_no_checks_line(self):    # R-794
        tmp = tempfile.mkdtemp()
        p = _project(tmp, "empty")
        self.assertIsNone(checksdetect.propose(p))
        self.assertEqual(checksdetect.offer_line(p), "")
        self.assertFalse(os.path.exists(os.path.join(p, ".budgit", "proposed_checks.json")))

    def test_named_empty_project_also_gets_none(self):
        tmp = tempfile.mkdtemp()
        p = _project(tmp, "named", cfg={"name": "named", "verify": {"checks": []}})
        self.assertIsNone(checksdetect.propose(p))
        self.assertEqual(checksdetect.offer_line(p), "")


class ApprovalBoundToCard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p = _project(self.tmp, "pyproj", files={"tests/test_x.py": "def test_a():\n    pass\n"})

    def test_card_listing_other_commands_is_rejected(self):
        prop = checksdetect.propose(self.p)
        label = "Approve checks " + prop["digest"][:12]
        sl.reconcile_transcript(self.p, _transcript(self.tmp, "q1", label, question="Run these checks: echo hello?"), "s1")
        cfg = json.load(open(os.path.join(self.p, ".budgit", "project.json")))
        self.assertEqual(cfg["verify"]["checks"], [])

    def test_card_listing_the_exact_commands_is_approved(self):
        prop = checksdetect.propose(self.p)
        label = "Approve checks " + prop["digest"][:12]
        q = "Run these checks: " + "; ".join(prop["checks"])
        sl.reconcile_transcript(self.p, _transcript(self.tmp, "q1", label, question=q), "s1")
        cfg = json.load(open(os.path.join(self.p, ".budgit", "project.json")))
        self.assertEqual(cfg["verify"]["checks"], prop["checks"])


class SymlinkedDataFolder(unittest.TestCase):
    def test_symlinked_budgit_folder_gets_no_write(self):
        tmp = tempfile.mkdtemp()
        victim = os.path.join(tmp, "victim")
        original = json.dumps({"rigor": "standard", "verify": {"checks": []}})
        _write(os.path.join(victim, "project.json"), original)
        root = os.path.join(tmp, "projects")
        p = os.path.join(root, "proj")
        _write(os.path.join(p, "pytest.ini"), "[pytest]\n")
        os.symlink(victim, os.path.join(p, ".budgit"))
        self.assertIsNone(checksdetect.propose(p))
        self.assertEqual(checksdetect.scan_all({"projects_root": root}), [])
        # a proposal planted in the victim must not be approved through the link either
        digest = checksdetect._digest(p, "proj", ["python3 -m pytest"])
        _write(os.path.join(victim, "proposed_checks.json"), json.dumps(
            {"name": "proj", "checks": ["python3 -m pytest"], "digest": digest}))
        label = "Approve checks " + digest[:12]
        res = {"questions": [{"question": "python3 -m pytest"}], "answers": {"python3 -m pytest": label}}
        self.assertFalse(checksdetect.on_answer(p, res))
        self.assertEqual(sorted(os.listdir(victim)), ["project.json", "proposed_checks.json"])
        self.assertEqual(open(os.path.join(victim, "project.json")).read(), original)


class UpdateReplay(unittest.TestCase):
    def test_update_step_proposes_for_existing_project_then_card_records(self):
        import installstep
        tmp = tempfile.mkdtemp()
        root = os.path.join(tmp, "projects")
        p = _project(root, "old", files={"pytest.ini": "[pytest]\n"})   # as 0.13.7 left it: empty checks, no name
        self.assertIn("checks-v1", [sid for sid, _ in installstep.STEPS])
        found = checksdetect.scan_all({"projects_root": root})
        self.assertEqual(found, [os.path.realpath(p)] if os.path.realpath(p) in found else [p])
        prop = checksdetect.propose(p)
        self.assertTrue(os.path.isfile(os.path.join(p, ".budgit", "proposed_checks.json")))
        sl.reconcile_transcript(p, _transcript(tmp, "q9", "Approve checks " + prop["digest"][:12]), "s1")
        self.assertEqual(sl.load_project_config(p)["verify"]["checks"], ["python3 -m pytest"])


class MissingBaselineIsAwaiting(unittest.TestCase):
    def test_no_baseline_is_awaiting_not_failure(self):
        tmp = tempfile.mkdtemp()
        cur = os.path.join(tmp, "cur")
        _write(os.path.join(cur, "home.png"), "x")
        self.assertEqual(designcheck.baseline_pngs(os.path.join(tmp, "nope")), [])
        text = designcheck.awaiting_report(os.path.join(tmp, "nope"), cur)
        self.assertIn("AWAITING", text)
        self.assertIn("home.png", text)
        self.assertNotEqual(designcheck.AWAITING_RC, 1)                        # 1 stays "drift = FAIL"
        self.assertNotEqual(designcheck.AWAITING_RC, 0)

    def test_existing_baseline_that_differs_still_fails(self):
        tmp = tempfile.mkdtemp()
        base, cur = os.path.join(tmp, "b"), os.path.join(tmp, "c")
        os.makedirs(base)
        os.makedirs(cur)
        _write(os.path.join(base, "home.png"), "x")                            # baseline exists, capture is missing
        self.assertEqual(designcheck.baseline_pngs(base), ["home.png"])
        rows = designcheck.diff_dirs(base, cur)
        self.assertTrue(rows[0]["drift"])
        self.assertEqual(designcheck.diff_report(rows, 0.5)[1], 1)

    def test_cli_diff_exit_codes(self):
        import subprocess
        import sys
        tmp = tempfile.mkdtemp()
        cur = os.path.join(tmp, "c")
        _write(os.path.join(cur, "home.png"), "x")
        cli = os.path.join(os.path.dirname(designcheck.__file__), "..", "bin", "budgit")
        r = subprocess.run([sys.executable, cli, "design-check", "diff", "--baseline", os.path.join(tmp, "none"),
                            "--current", cur], capture_output=True, text=True,
                           env=dict(os.environ, BUDGIT_HOME=os.path.join(tmp, "home")))
        self.assertEqual(r.returncode, designcheck.AWAITING_RC, r.stdout + r.stderr)
        self.assertIn("AWAITING", r.stdout)


if __name__ == "__main__":
    unittest.main()
