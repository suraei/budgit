"""R-880 F1: the CI workflow runs nightly, on main pushes and by hand only.

Text-only reading of the workflow (no YAML library). The parser below is a test helper, not shipped code.
"""
import os
import re
import unittest

WORKFLOW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".github", "workflows", "tests.yml")


def parse_on(text):
    """Return {trigger: {"branches": [...] or None, "crons": n}} from the top-level `on:` block."""
    lines = [l.split(" #")[0].rstrip() for l in text.splitlines()]
    lines = [l for l in lines if l.strip() and not l.lstrip().startswith("#")]
    out = {}
    for i, line in enumerate(lines):
        m = re.match(r"^on:\s*(.*)$", line)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest:
            names = [n.strip() for n in rest.strip("[]").split(",") if n.strip()]
            return {n: {"branches": None, "crons": 0} for n in names}
        current = None
        for sub in lines[i + 1:]:
            if not sub.startswith(" "):
                break
            indent = len(sub) - len(sub.lstrip())
            body = sub.strip()
            if indent == 2:
                key = body.split(":")[0].strip()
                current = out.setdefault(key, {"branches": None, "crons": 0})
            elif current is not None:
                if body.startswith("branches:"):
                    val = body.split(":", 1)[1].strip().strip("[]")
                    current["branches"] = [b.strip().strip("'\"") for b in val.split(",") if b.strip()]
                elif body.startswith("- cron:"):
                    current["crons"] += 1
        return out
    return out


def violations(text):
    on = parse_on(text)
    bad = []
    for name in ("pull_request", "pull_request_target"):
        if name in on:
            bad.append(name)
    if "push" in on and on["push"]["branches"] != ["main"]:
        bad.append("bare push")
    return bad


class CiTriggers(unittest.TestCase):
    def setUp(self):
        with open(WORKFLOW, encoding="utf-8") as f:
            self.text = f.read()

    def test_a_triggers_exactly_three(self):
        self.assertEqual(set(parse_on(self.text)), {"schedule", "push", "workflow_dispatch"})

    def test_b_push_limited_to_main(self):
        self.assertEqual(parse_on(self.text)["push"]["branches"], ["main"])

    def test_c_pull_request_fails(self):
        self.assertEqual(violations(self.text), [])
        for name in ("pull_request", "pull_request_target"):
            synth = "on:\n  push:\n    branches: [main]\n  %s:\njobs:\n" % name
            self.assertEqual(violations(synth), [name])

    def test_d_bare_push_fails(self):
        forms = {
            "no filter": "on:\n  push:\n  workflow_dispatch:\n",
            "list form": "on: [push]\n",
            "scalar form": "on: push\n",
        }
        for label, synth in forms.items():
            with self.subTest(label):
                self.assertEqual(violations(synth), ["bare push"])

    def test_e_one_cron(self):
        self.assertEqual(parse_on(self.text)["schedule"]["crons"], 1)


if __name__ == "__main__":
    unittest.main()
