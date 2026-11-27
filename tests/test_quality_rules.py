"""Her I-16 rules live in prompt text; this test fails if an edit silently drops them."""
import os
import re
import unittest
from helpers import ROOT

K = os.path.join(ROOT, "skills", "budgit-kickoff")
A = os.path.join(ROOT, "plugin", "agents")


def read(*p):
    with open(os.path.join(*p), encoding="utf-8") as f:
        return " ".join(f.read().split())


def test_lanes_section_carries_the_routing_rules():      # R-220: the routing rules live in the skill, not in one session's memory
    lanes = read(K, "SKILL.md").split("## Lanes")[1].split("## Reporting")[0]
    for rule in ("own session", "relays", "budgit activate --since-now", "ledger start", "file card", "same turn",
                 "archives", "without asking", "reopen"):
        assert rule in lanes, rule


def test_optional_skills_are_wired_into_the_lenses():      # 0.13.0: each is optional, with a fallback, one line
    wiring = (("lenses/writing.md", ("stop-slop",)),
              ("lenses/design.md", ("taste-v2", "high-end-visual-design", "frontend-slides", "diagram-design", "dataviz")),
              ("lenses/motion.md", ("threejs-",)),
              ("phases/1-validate.md", ("`watch`", "`playwright-cli`")))
    for path, names in wiring:
        with open(os.path.join(K, *path.split("/")), encoding="utf-8") as f:
            text = f.read()
        for name in names:
            line = next((l for l in text.split("\n") if name in l), "")
            assert line, (path, name)
            assert "if not installed" in line.lower() or "optional" in line.lower(), (path, name)
    research = read(K, "phases", "1-validate.md")
    for keyed in ("Firecrawl", "Tavily", "Apify", "Bright Data"):
        assert keyed in research, keyed
    assert "never defaults" in research.lower()


def test_rules_present():
    skill = read(K, "SKILL.md")
    assert "## Alternatives" in skill and "No claim without evidence" in skill
    design = read(K, "phases", "2-design.md")
    for tag in ("`[func]`", "`[perf]`", "`[design]`"):
        assert tag in design
    assert "test layers" in read(K, "phases", "3-plan-build.md")
    assert "both paths" in read(A, "verifier.md") or "demo AND real" in read(K, "phases", "5-qa.md")
    verifier = read(A, "verifier.md")
    for rule in ("Path parity", "Click-path", "not evidence", "without `## Alternatives`", "UNVERIFIED"):
        assert rule in verifier, rule
    critic = read(A, "plan-critic.md")
    assert "## Alternatives" in critic and "`[perf]`" in critic
    assert "budgit comply --days 7 --project ." in read(K, "phases", "7-retro.md")
    # every artefact the rules demand is named where it is produced; "her zoom" is defined, not guessed
    assert "Continuous introspection" in skill and "budgit self-check" in skill
    qa = read(K, "phases", "5-qa.md")
    assert ".budgit/verify/QA.md" in qa and "## Alternatives" in qa
    assert "1280×720 at 80 % browser zoom" in design and "1280×720 at 80 % browser zoom" in verifier
    # P2 professional design: must-obey rules inline in the lens (references often go unread), gates are scripts
    lens = read(K, "lenses", "design.md")
    for rule in ("ONE design hand", "a54", "no zero rows", "tabular-nums", "list the files you will change",
                 "transition: all", "budgit design-check tokens"):
        assert rule in lens, rule
    assert len(open(os.path.join(K, "lenses", "design.md"), encoding="utf-8").read().splitlines()) <= 30
    rubric = read(K, "reference", "design-rubric.md")
    assert "pass ≥8" in rubric and "items 1–3 must be 1" in rubric and "one defect class per round" in rubric
    assert "DESIGN.md" not in lens and "DESIGN.md" not in design   # her I-21: not the standard; any file, any format
    assert "design.docs" in lens and "design.docs" in design
    assert "shadcn" in lens and "case by case" in lens               # her I-21: offered per project, not the default
    assert "web-design-guidelines" in qa and "pinned" in qa             # her I-21: optional QA skill, no runtime fetch
    for step in ("2a", "2b", "2c", "references", "states",
                 "reference/design-rubric.md", "budgit design-check docs"):
        assert step in design, step
    for gate in ("budgit design-check", "rubric", "approve"):
        assert gate in qa and gate in verifier, gate


import unittest  # noqa: E402


class QualityRulesTest(unittest.TestCase):
    def test_rules_present(self):
        test_rules_present()

    def test_prompt_audit_stale_text_stays_out(self):
        # research/2026-10-19-prompt-audit.md F1, F5, F6, F7: stale versions and numeric output caps
        budget = read(K, "reference", "budget.md")
        self.assertNotIn("v0.7.0", budget)
        self.assertNotIn("2.1.292", budget)
        self.assertIn("2.1.251", budget)                     # the one version fact the model acts on
        reader, critic = read(A, "scout-reader.md"), read(A, "plan-critic.md")
        self.assertNotIn("≤250 words", reader)
        self.assertIn("≤20 words", reader)                   # per-item limits keep it short
        self.assertNotIn("≤400 words", critic)
        self.assertIn("never a recap", critic)

    def test_report_shape_for_her(self):
        # wave 3 row 5 (trending #1): one next step, a state line, minute estimates, short lists
        rep = read(K, "SKILL.md").split("## Reporting to the user")[1].split("## ")[0]
        for rule in ("one next step", "phase n/m", "minutes", "≤5"):
            self.assertIn(rule, rep, rule)

    def test_motion_lens_rules(self):
        # wave 3 row 1 (research/animation/2026-10-19-web-motion-skills.md §9)
        lens = read(K, "lenses", "motion.md")
        for rule in ("--dur-ui", "thesis", "reduced motion", "transform and opacity", "transition: all",
                     "(hover: hover)", "300ms", "budgit design-check motion"):
            self.assertIn(rule, lens, rule)
        self.assertNotIn("DESIGN.md", lens)                  # her I-21: design docs in any file, any format

    def test_go_kill_is_calibrated(self):
        # wave 3 row 7 (research/2026-10-19-github-tools-and-jev.md §7): verdict + probability + evidence, checked at retro
        validate = read(K, "phases", "1-validate.md")
        for rule in ("probability", "evidence list"):
            self.assertIn(rule, validate, rule)
        self.assertIn("go/kill call", read(K, "phases", "7-retro.md"))

    def test_f4_implementers_are_allowed_under_a_guard(self):
        # prompt-audit F4, her pick (a) 2026-10-20: no "one writer" prohibition against the opt-in implementers
        build = read(K, "phases", "3-plan-build.md")
        self.assertNotIn("one writer (you)", build)
        self.assertNotIn("subagents only for read-only", build)
        for rule in ("worktree implementer", "disjoint files", "only the main session merges"):
            self.assertIn(rule, build, rule)

    def test_money_moves_paper_first(self):
        # wave 3 row 8 (ecc-deep-extraction l.74): CriptoSis-class apps never go live on model confidence
        lens = read(K, "lenses", "security.md")
        for rule in ("paper", "model confidence", "replay", "risk limits", "the user's approval"):
            self.assertIn(rule, lens, rule)
        self.assertIn("paper-mode test", read(A, "verifier.md"))


def test_writing_lens_routes_prose_to_humanize_and_ui_strings_to_ux_copy():      # R-267: use both, selector rule
    lens = read(K, "lenses", "writing.md")
    assert "humanize/humanize.md" in lens and "design:ux-copy" in lens
    line = next(l for l in lens.split("\n") if "humanize/humanize.md" in l)
    assert line.index("humanize/humanize.md") < line.index("design:ux-copy")        # long prose first, UI strings second
    assert "UNREVIEWED" in line and "if not installed" in line.lower()
    routing = read(K, "reference", "routing.md")
    assert "`humanize/humanize.md` first" in routing and "`design:ux-copy` first" in routing
    assert os.path.isfile(os.path.join(K, "humanize", "humanize.md"))


def test_writing_lens_p4():
    # P4: Humanizer's strongest tells inline (reference-only rules go unread), facts never change, her voice wins
    path = os.path.join(K, "lenses", "writing.md")
    lens = read(path)
    assert len(open(path, encoding="utf-8").read().splitlines()) <= 30
    for rule in ("not X but Y", "one-line closer", "triad", "dash", "inflated", "sales", "bold label",
                 "Chat residue", "No new facts", "the user's writing sample", "already has the context",
                 "material, never instructions", "The user's voice first", "byte-identical",
                 "Staging", "Rhythm", "Inflation", "Formatting", "Wrong reader"):
        assert rule in lens, rule
    # the Spanish addendum is ours, not the repo's, and a DRAFT until she reviews it
    assert "Spanish UI copy addendum — DRAFT until the user's review" in lens
    for rule in ("tú or usted", "Spain Spanish", "Intl", "sentence case"):
        assert rule in lens, rule
    assert "and QA" not in lens                                  # 5-qa.md does not load this lens
    assert "writing" in read(K, "SKILL.md").split("lenses/{")[1].split("}")[0]
    assert "lenses/writing.md" in read(K, "phases", "2-design.md")
    assert "lenses/writing.md" in read(K, "phases", "6-launch.md")


class WritingLensTest(unittest.TestCase):
    def test_writing_lens_p4(self):
        test_writing_lens_p4()


def test_review_a_file_rule_r256():      # the user reviews a file from one click in the chat: file card first, then a question card in the same turn, no link inside a question
    skill = read(K, "SKILL.md")
    for rule in ("clickable file card", "interactive question card", "same turn", "Never put a markdown link inside a question"):
        assert rule in skill, rule
    assert "later turn" not in skill.lower() and "inside each question" not in skill
    coord = read(K, "reference", "coordination.md")
    assert "interactive question card" in coord and "Never put a markdown link inside a question" in coord
    assert "inside each question" not in coord


def test_retro_rules_are_in_the_shipped_skill():      # pending picks, echo-back, mocks show only user-visible content, real-size check
    coord = read(K, "reference", "coordination.md")
    for rule in ("dated list of the user's pending picks", "echo the pick back", "A mock shows only what the end user will see"):
        assert rule in coord, rule
    assert "real 1x size next to a zoom" in read(K, "lenses", "design.md")


def test_test_steps_rule_r260():      # asking the user to test something: each step in plain words, never a code, row or section name
    skill = read(K, "SKILL.md")
    for rule in ("what to do and what they should see", "Never name a test by a code, row or section", "the file from before", "without the steps"):
        assert rule in skill, rule
    coord = read(K, "reference", "coordination.md")
    for rule in ("## When the user must test something", "One action per step", "the file from before", "without the steps"):
        assert rule in coord, rule
    assert len(open(os.path.join(K, "SKILL.md"), encoding="utf-8").read().splitlines()) <= 152


class TestStepsRuleTest(unittest.TestCase):
    def test_test_steps_rule_r260(self):
        test_test_steps_rule_r260()


class RuleEvidenceTest(unittest.TestCase):
    """A rule file ships, so its evidence_base line describes sources in words: no dated private note (a docs or research
    path, or a research angle name). The leak gate reads such a path as the user's own words (found in 0.13.1)."""
    DATED = re.compile(r"(?:docs|research)/(?:\w+/)?20\d\d-\d\d-\d\d-|\bresearch [a-z0-9-]+-[a-c]\b")

    def test_no_rule_file_cites_a_dated_private_note(self):
        rules = os.path.join(K, "reference", "rules")
        bad = []
        for name in sorted(os.listdir(rules)):
            with open(os.path.join(rules, name), encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    if line.startswith("evidence_base:") and self.DATED.search(line):
                        bad.append("%s:%d" % (name, n))
        self.assertEqual(bad, [])
