"""R-335 class 5: an MCP file tool writes the same files Edit and Write do, so the same guards judge it."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_guard_trust import HookCase2, sl, HOOK  # noqa: E402

HOOKS_JSON = os.path.join(os.path.dirname(HOOK), "..", "hooks", "hooks.json")


class McpFileToolTest(HookCase2):
    def setUp(self):
        super().setUp()
        self.protected = os.path.join(self.root, "data.json")
        self.plain = os.path.join(self.root, "docs", "a.md")
        self.trust = os.path.join(self.home, "trust.json")
        sl.activate(self.project, session_id="mcp-bound")


    def mcp(self, name, **args):
        return self.tool("mcp__filesystem__" + name, args, session_id="mcp-bound")

    def test_the_pre_tool_use_matcher_routes_mcp_tools_to_the_guard(self):
        spec = json.load(open(HOOKS_JSON))["hooks"]["PreToolUse"]
        self.assertTrue(any("mcp__" in (g.get("matcher") or "") for g in spec))

    def test_a_write_kind_tool_into_the_budgit_home_is_denied(self):
        for name in ("write_file", "edit_file", "move_file", "create_directory", "delete_file", "append_file", "copy_file"):
            self.assertEqual(self.mcp(name, path=self.trust), "deny", name)
        self.assertEqual(self.mcp("move_file", source=self.plain, destination=self.trust), "deny")
        self.assertEqual(self.mcp("write_file", file_path=self.trust, content="{}"), "deny")
        self.assertEqual(self.mcp("write_file", path="file://" + self.trust), "deny")

    def test_a_write_kind_tool_on_protected_real_data_asks(self):
        for name in ("write_file", "edit_file", "move_file", "delete_file"):
            self.assertEqual(self.mcp(name, path=self.protected), "ask", name)
        self.assertEqual(self.mcp("move_file", source=self.protected, destination=self.plain), "ask")
        self.assertEqual(self.mcp("write_file", paths=[self.plain, self.protected]), "ask")

    def test_a_move_or_copy_whose_destination_is_protected_or_in_the_home_is_judged(self):
        for name in ("move_file", "copy_file", "rename_file"):
            self.assertEqual(self.mcp(name, source=self.plain, destination=self.protected), "ask", name)
            self.assertEqual(self.mcp(name, src=self.plain, dst=self.protected), "ask", name)
            self.assertEqual(self.mcp(name, from_path=self.plain, to_path=self.trust), "deny", name)

    def test_an_unknown_tool_name_with_a_path_is_judged_as_a_write(self):
        self.assertEqual(self.mcp("frobnicate", path=self.trust), "deny")
        self.assertEqual(self.mcp("frobnicate", path=self.protected), "ask")

    def test_plain_files_and_reads_are_left_alone(self):
        self.assertIsNone(self.mcp("write_file", path=self.plain, content="x"))
        for name in ("read_file", "read_text_file", "list_directory", "search_files", "get_file_info", "directory_tree"):
            self.assertIsNone(self.mcp(name, path=self.trust), name)
            self.assertIsNone(self.mcp(name, path=self.protected), name)
        self.assertIsNone(self.tool("mcp__github__create_issue", {"title": "x", "body": "no path here"}, session_id="mcp-bound"))

    def test_a_relative_path_is_judged_from_the_working_folder(self):
        self.assertEqual(self.mcp("write_file", path="data.json"), "ask")
        self.assertIsNone(self.mcp("write_file", path="docs/a.md"))

    def test_a_nested_path_argument_is_found(self):
        self.assertEqual(self.mcp("write_file", files=[{"path": self.trust, "content": "{}"}]), "deny")
        self.assertEqual(self.mcp("write_file", edits={"target": {"file": self.protected}}), "ask")

    def test_a_guard_that_crashes_on_the_arguments_asks(self):
        real = sl.mcp_file_calls
        sl.mcp_file_calls = lambda *a: 1 / 0
        try:
            self.assertEqual(sl.mcp_file_decision("mcp__x__write_file", {"path": self.plain}, self.project, self.project)[0], "ask")
        finally:
            sl.mcp_file_calls = real


if __name__ == "__main__":
    unittest.main()
