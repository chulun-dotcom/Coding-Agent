from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from repoagent.contracts import ToolCall
from repoagent.tools import build_default_registry


class WorkspaceToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(
            ["git", "-C", str(self.workspace), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.workspace), "config", "user.name", "SHI Agent Test"],
            check=True,
        )
        (self.workspace / "pagination.py").write_text(
            "def paginate(items, page, page_size):\n"
            "    start = (page - 1) * page_size\n"
            "    return items[start:start + page_size]\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(self.workspace), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.workspace), "commit", "-q", "-m", "fixture"],
            check=True,
        )
        self.registry = build_default_registry()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_list_search_and_read(self) -> None:
        listed = self.registry.execute(
            ToolCall(call_id="1", name="list_files", arguments={}), self.workspace
        )
        self.assertTrue(listed.ok)
        self.assertIn("pagination.py", listed.output_excerpt)

        searched = self.registry.execute(
            ToolCall(call_id="2", name="search_code", arguments={"query": "paginate"}),
            self.workspace,
        )
        self.assertTrue(searched.ok)
        self.assertIn("pagination.py:1", searched.output_excerpt)

        read = self.registry.execute(
            ToolCall(call_id="3", name="read_file", arguments={"path": "pagination.py"}),
            self.workspace,
        )
        self.assertTrue(read.ok)
        self.assertIn("1 | def paginate", read.output_excerpt)

    def test_path_escape_is_rejected(self) -> None:
        result = self.registry.execute(
            ToolCall(call_id="1", name="read_file", arguments={"path": "../secret.txt"}),
            self.workspace,
        )
        self.assertFalse(result.ok)
        self.assertEqual("PATH_OUTSIDE_WORKSPACE", result.error_code)

    def test_patch_is_checked_applied_and_visible_in_diff(self) -> None:
        patch = """diff --git a/pagination.py b/pagination.py
--- a/pagination.py
+++ b/pagination.py
@@ -1,3 +1,5 @@
 def paginate(items, page, page_size):
+    if page < 1:
+        raise ValueError("page must be at least 1")
     start = (page - 1) * page_size
     return items[start:start + page_size]
"""
        applied = self.registry.execute(
            ToolCall(call_id="1", name="apply_patch", arguments={"patch": patch}),
            self.workspace,
        )
        self.assertTrue(applied.ok, applied.output_excerpt)
        self.assertTrue(applied.workspace_changed)

        diff = self.registry.execute(
            ToolCall(call_id="2", name="inspect_diff", arguments={}), self.workspace
        )
        self.assertTrue(diff.ok)
        self.assertIn("page must be at least 1", diff.output_excerpt)

    def test_replace_text_requires_one_exact_match(self) -> None:
        file = self.workspace / "pagination.py"
        original = file.read_text(encoding="utf-8")
        replaced = self.registry.execute(
            ToolCall(
                call_id="replace",
                name="replace_text",
                arguments={
                    "path": "pagination.py",
                    "old_text": "start = (page - 1) * page_size",
                    "new_text": "start = max(page - 1, 0) * page_size",
                },
            ),
            self.workspace,
        )
        self.assertTrue(replaced.ok)
        self.assertTrue(replaced.workspace_changed)
        self.assertIn("max(page - 1, 0)", file.read_text(encoding="utf-8"))

        missing = self.registry.execute(
            ToolCall(
                call_id="missing",
                name="replace_text",
                arguments={"path": "pagination.py", "old_text": "not there", "new_text": "x"},
            ),
            self.workspace,
        )
        self.assertEqual("TEXT_NOT_UNIQUE", missing.error_code)

        outside = self.registry.execute(
            ToolCall(
                call_id="outside",
                name="replace_text",
                arguments={"path": "../secret.txt", "old_text": original, "new_text": "x"},
            ),
            self.workspace,
        )
        self.assertEqual("PATH_OUTSIDE_WORKSPACE", outside.error_code)


if __name__ == "__main__":
    unittest.main()
