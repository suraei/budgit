"""P4 / D3 (R-176): a new user has none of the owner's other skills; every skill the kickoff skill names is optional and
has an inline fallback on the same or the next line."""
import os
import re
import subprocess
import sys
import unittest

from helpers import ROOT

SKILL = os.path.join(ROOT, "skills", "budgit-kickoff")
OPTIONAL = ("superpowers:", "design:", "emil-design-eng", "impeccable", "web-design-guidelines", "pr-review-toolkit:",
            "security-review", "ux-copy", "stop-slop")
NAMES = re.compile(r"(?<![\w-])(?:%s)" % "|".join(re.escape(o) + ("" if o.endswith(":") else r"(?![\w-])") for o in OPTIONAL))
FALLBACK = re.compile(r"if missing|if not installed", re.I)
CLI = os.path.join(ROOT, "plugin", "bin", "budgit")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def files():
    out = [os.path.join(SKILL, "SKILL.md")]
    for sub in ("phases", "lenses"):
        out += [os.path.join(SKILL, sub, n) for n in sorted(os.listdir(os.path.join(SKILL, sub))) if n.endswith(".md")]
    return out


class OptionalSkillsTest(unittest.TestCase):
    def test_skill_md_has_the_optional_rule(self):
        text = read(os.path.join(SKILL, "SKILL.md"))
        self.assertIn("every skill named in a phase or lens is optional", text)
        self.assertIn("say so once", text)

    def test_every_named_skill_has_a_fallback_beside_it(self):
        bare = []
        for path in files():
            lines = read(path).splitlines()
            for n, line in enumerate(lines):
                if NAMES.search(line) and "every skill named in a phase or lens is optional" not in line:
                    window = line + " " + (lines[n + 1] if n + 1 < len(lines) else "")
                    if not FALLBACK.search(window):
                        bare.append("%s:%d %s" % (os.path.relpath(path, ROOT), n + 1, line.strip()[:70]))
        self.assertEqual(bare, [], "optional skills named without 'if missing' / 'if not installed' + a step")

    def test_the_fallback_steps_are_the_planned_ones(self):
        text = "\n".join(read(p) for p in files())
        for step in ("read the log", "run the check, quote its output", "ask at most 4 questions",
                     "lenses/design.md and the rubric", "tasks with files, a failing test first"):
            self.assertIn(step, text)

    def test_design_check_detect_without_impeccable_says_unverified(self):
        env = dict(os.environ, STUDIO_IMPECCABLE=os.path.join(ROOT, "nowhere", "impeccable"))
        r = subprocess.run([sys.executable, CLI, "design-check", "detect", ROOT], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)             # unverified is never a pass
        self.assertIn("UNVERIFIED", r.stdout)
        self.assertIn("lenses/design.md", r.stdout)                          # and it names what to do instead
        self.assertNotIn("Traceback", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
