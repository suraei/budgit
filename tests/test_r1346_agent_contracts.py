"""R-1346: a read-only agent (no Write tool) must never be told to write a file, it hands its full report back
(verdict first) and the caller saves it."""
import glob
import os
import re
import unittest

AGENTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin", "agents")
REPORT_PATH = ".studio/reports/<date>-<R-n>-<kind>.md"

# Phrasings that tell an agent to create a file.
WRITE_FILE = [
    re.compile(r"\b(?:write|create|save)\b[^\n.]{0,60}?\.studio/reports", re.I),
    re.compile(r"\bwrite\s+(?:the\s+|a\s+|your\s+)?(?:report|findings?|verdict|digest)?\s*(?:to|into)\s+(?:the\s+|a\s+)?(?:\S*\.md\b|file\b)", re.I),
    re.compile(r"\bwrite\s+\S*/\S*\.(?:md|json|txt)\b", re.I),
    re.compile(r"\bsave\s+(?:the\s+|your\s+)?(?:report|findings?|verdict|digest)\b", re.I),
    re.compile(r"\bwritten\s+to\s+(?:the\s+|a\s+)?(?:scratch\s+)?file\b", re.I),
]


def split_agent(path):
    text = open(path, encoding="utf-8").read()
    head, _, body = text.partition("\n---\n")
    return head, body


def tools_of(head):
    for line in head.splitlines():
        if line.startswith("tools:"):
            return [t.strip() for t in line[len("tools:"):].split(",")]
    return []


def agent_files():
    return sorted(glob.glob(os.path.join(AGENTS, "*.md")))


def read_only_agents():
    return [p for p in agent_files() if "Write" not in tools_of(split_agent(p)[0])]


class ReadOnlyAgentsDoNotWriteTest(unittest.TestCase):
    def test_a_agent_without_the_write_tool_is_never_told_to_write_a_file(self):
        self.assertTrue(read_only_agents())
        for path in read_only_agents():
            body = split_agent(path)[1]
            for pat in WRITE_FILE:
                m = pat.search(body)
                self.assertIsNone(m, "%s tells a read-only agent to write a file: %r" % (os.path.basename(path), m and m.group(0)))

    def test_the_three_reviewers_hand_the_full_report_back_verdict_first_for_the_caller_to_save(self):
        for name in ("plan-critic", "security-reviewer", "scout-reader"):
            body = split_agent(os.path.join(AGENTS, name + ".md"))[1]
            self.assertIn("hand-back", body, name)
            self.assertIn("first line", body, name)
            self.assertIn(REPORT_PATH, body, name)
            self.assertIn("caller saves", body, name)

    def test_the_pattern_catches_the_phrasings_it_exists_for(self):
        for bad in ("Write the report to .studio/reports/x.md", "save the report to a file", "write notes/out.md",
                    "with the text written to the scratch file first"):
            self.assertTrue(any(p.search(bad) for p in WRITE_FILE), bad)
        for ok in ("switch tools (for example Bash refused, then Write or Edit)", "write what breaks for each",
                   "the code that writes it in production"):
            self.assertFalse(any(p.search(ok) for p in WRITE_FILE), ok)


# The strict replay of every command the agent files name is a done-contract row of the next R-1360 slice (ledger R-1360 F12).


if __name__ == "__main__":
    unittest.main()
