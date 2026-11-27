"""R-880 F2: CI good practice for the tests workflow and the radar workflow (text-only reading, no YAML library).

Items: 1 concurrency, 2 a fast-tier job every shard needs, 3 nightly skip on an already green sha, 4 pinned radar
actions, 5 shard count stays 8.
"""
import os
import re
import unittest

WF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".github", "workflows")


def read(name):
    with open(os.path.join(WF, name), encoding="utf-8") as f:
        return f.read()


def code_lines(text):
    return [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]


def top_block(text, key):
    """The lines under a top-level `key:` (indent 0), up to the next top-level key."""
    lines = code_lines(text)
    for i, l in enumerate(lines):
        if re.match(r"^%s:" % re.escape(key), l):
            out = []
            for sub in lines[i + 1:]:
                if not sub.startswith(" "):
                    break
                out.append(sub)
            return "\n".join(out)
    return ""


def job(text, name):
    """The text of one job under `jobs:` (indent 2 key)."""
    jobs = top_block(text, "jobs")
    m = re.search(r"^  %s:\n((?:    .*\n|\n)*)" % re.escape(name), jobs + "\n", re.M)
    return m.group(1) if m else ""


def uses(text):
    return re.findall(r"^\s*-?\s*uses:\s*(\S+)(.*)$", text, re.M)


class Concurrency(unittest.TestCase):
    def test_1_group_per_workflow_and_ref_cancels_in_progress(self):
        text = read("tests.yml")
        block = top_block(text, "concurrency")
        self.assertIn("github.workflow", block)
        self.assertIn("github.ref", block)
        self.assertRegex(block, r"cancel-in-progress:\s*true\b")

    def test_1b_nightly_has_its_own_group_so_a_push_never_cancels_it(self):
        block = top_block(read("tests.yml"), "concurrency")
        self.assertIn("github.event_name == 'schedule'", block)
        self.assertIn("'nightly'", block)
        # only trusted contexts in the group expression
        for ctx in re.findall(r"github\.[\w.]+", block):
            self.assertIn(ctx, ("github.workflow", "github.event_name", "github.ref"))


class FastTier(unittest.TestCase):
    def test_2_fast_job_exists_and_reuses_the_shard_picker(self):
        fast = job(read("tests.yml"), "fast")
        self.assertTrue(fast, "no `fast` job")
        self.assertIn("ci_shard", fast)
        self.assertIn("slow_modules", fast)
        self.assertIn("tools/runtests.py", fast)

    def test_2_every_shard_job_needs_fast(self):
        text = read("tests.yml")
        tests = job(text, "tests")
        m = re.search(r"^    needs:\s*(.*)$", tests, re.M)
        self.assertIsNotNone(m, "tests job has no needs:")
        self.assertIn("fast", re.findall(r"[\w-]+", m.group(1)))


class FastEmptyList(unittest.TestCase):
    def test_2c_fast_job_fails_loudly_on_an_empty_module_list(self):
        fast = job(read("tests.yml"), "fast")
        self.assertRegex(fast, r'if \[ -z "\$mods" \]')
        self.assertIn("exit 1", fast)
        self.assertLess(fast.index('-z "$mods"'), fast.index("tools/runtests.py"))


class NightlySkip(unittest.TestCase):
    def test_3_gate_job_asks_for_a_green_run_of_the_sha(self):
        gate = job(read("tests.yml"), "gate")
        self.assertTrue(gate, "no `gate` job")
        self.assertRegex(gate, r"actions:\s*read")
        self.assertIn("gh api", gate)
        self.assertIn("head_sha=", gate)
        self.assertIn("status=success", gate)
        self.assertIn("github.event_name", gate)
        self.assertIn("schedule", gate)
        self.assertRegex(gate, r"outputs:")

    def test_3_only_schedule_can_skip_and_other_jobs_gate_on_it(self):
        text = read("tests.yml")
        gate = job(text, "gate")
        # the skip decision is made only when the event is schedule; any other event writes run=true
        self.assertRegex(gate, r"run=true")
        self.assertRegex(gate, r"run=false")
        for name in ("fast", "tests"):
            body = job(text, name)
            self.assertRegex(body, r"if:\s*needs\.gate\.outputs\.run\s*==\s*'true'", name)
            self.assertIn("gate", re.findall(r"[\w-]+", re.search(r"^    needs:\s*(.*)$", body, re.M).group(1)), name)

    def test_3_no_third_party_action_in_the_gate(self):
        self.assertEqual(uses(job(read("tests.yml"), "gate")), [])

    def test_3_triggers_unchanged(self):
        text = read("tests.yml")
        on = top_block(text, "on")
        self.assertIn('cron: "17 3 * * *"', on)
        self.assertIn("branches: [main]", on)
        self.assertIn("workflow_dispatch", on)
        self.assertNotIn("pull_request", on)


class RadarPins(unittest.TestCase):
    def test_4_every_radar_use_is_a_full_sha_with_version_comment(self):
        found = uses(read("radar.yml"))
        self.assertTrue(found)
        for ref, rest in found:
            with self.subTest(ref):
                self.assertRegex(ref, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
                self.assertRegex(rest, r"#\s*v\d")

    def test_4_radar_shas_equal_the_ones_tests_yml_uses(self):
        mine = {r.split("@")[0]: (r.split("@")[1], c.strip()) for r, c in uses(read("tests.yml"))}
        for ref, rest in uses(read("radar.yml")):
            action, sha = ref.split("@")
            with self.subTest(action):
                self.assertIn(action, mine)
                self.assertEqual((sha, rest.strip()), mine[action])


class ShardCount(unittest.TestCase):
    def test_5_shards_stay_8(self):
        text = read("tests.yml")
        self.assertIn("shard: [1, 2, 3, 4, 5, 6, 7, 8]", text)
        self.assertIn("ci_shard.py ${{ matrix.shard }}/8", text)


if __name__ == "__main__":
    unittest.main()
