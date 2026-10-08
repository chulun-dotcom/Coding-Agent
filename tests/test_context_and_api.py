from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from repoagent.api import app
from repoagent.context import ContextBuilder
from repoagent.contracts import TaskSpec
from repoagent.repository.repomap import repository_symbols


class ContextAndApiTest(unittest.TestCase):
    def test_repo_map_rebuilds_after_edits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "module.py"
            file.write_text("def first():\n    pass\n", encoding="utf-8")
            self.assertEqual("first", repository_symbols(root)[0]["name"])
            file.write_text("def second():\n    pass\n", encoding="utf-8")
            self.assertEqual("second", repository_symbols(root)[0]["name"])

    def test_api_health_and_missing_task(self) -> None:
        client = TestClient(app)
        self.assertEqual(200, client.get("/health").status_code)
        self.assertEqual(404, client.get("/tasks/does-not-exist").status_code)

    def test_context_keeps_task_under_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number in range(40):
                (root / f"module_{number}.py").write_text(
                    f"def function_{number}():\n    pass\n", encoding="utf-8"
                )
            task = TaskSpec(task_id="bounded", instruction="Fix the named function", workspace=root)
            messages = ContextBuilder(max_chars=1000).build(task, [], [])
            self.assertLessEqual(sum(len(message["content"]) for message in messages), 1000)
            self.assertIn("Fix the named function", messages[1]["content"])
