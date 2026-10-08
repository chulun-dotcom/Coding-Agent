from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from repoagent.contracts import ModelAction, RunStatus, TaskSpec
from repoagent.model import ChatCompletionsModel, ScriptedModel
from repoagent.sandbox import ExecutionResult, FakeSandbox
from repoagent.service import TaskService

PATCH = """diff --git a/pagination.py b/pagination.py
--- a/pagination.py
+++ b/pagination.py
@@ -1,3 +1,5 @@
 def paginate(items, page, page_size):
+    if page < 1:
+        raise ValueError("page must be at least 1")
     start = (page - 1) * page_size
     return items[start:start + page_size]
"""


class TaskServiceTest(unittest.TestCase):
    def test_model_reads_ignored_local_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "SHI_AGENT_API_KEY=test-secret\nSHI_AGENT_MODEL=test-model\nSHI_AGENT_BASE_URL=https://example.test/v1\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"SHI_AGENT_ENV_FILE": str(env_file)}):
                for name in ("SHI_AGENT_API_KEY", "SHI_AGENT_MODEL", "SHI_AGENT_BASE_URL"):
                    os.environ.pop(name, None)
                model = ChatCompletionsModel.from_env()
            self.assertEqual("test-model", model.model)
            self.assertEqual("https://example.test/v1", model.base_url)

    def test_whole_task_creates_workspace_patch_events_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(
                ["git", "-C", str(source), "config", "user.email", "test@example.com"], check=True
            )
            subprocess.run(
                ["git", "-C", str(source), "config", "user.name", "SHI Agent Test"], check=True
            )
            original = (
                "def paginate(items, page, page_size):\n"
                "    start = (page - 1) * page_size\n"
                "    return items[start:start + page_size]\n"
            )
            (source / "pagination.py").write_text(original, encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-q", "-m", "initial"], check=True)

            sandbox = FakeSandbox(
                [
                    ExecutionResult(0, "2 passed"),
                    ExecutionResult(0, "3 passed"),
                    ExecutionResult(0, "3 passed"),
                ]
            )
            model = ScriptedModel(
                [
                    ModelAction.call("1", "list_files"),
                    ModelAction.call("2", "read_file", path="pagination.py"),
                    ModelAction.call("3", "apply_patch", patch=PATCH),
                    ModelAction.call("4", "run_tests"),
                    ModelAction.done("Fixed invalid page."),
                ]
            )
            service = TaskService(root / "data", sandbox=sandbox)
            report = service.run(source, "Fix page < 1", model=model, task_id="demo")

            self.assertEqual(RunStatus.SUCCEEDED, report.status)
            self.assertEqual(original, (source / "pagination.py").read_text(encoding="utf-8"))
            task_dir = root / "data" / "tasks" / "demo"
            self.assertIn(
                "page must be at least 1",
                (task_dir / "artifacts" / "patch.diff").read_text(encoding="utf-8"),
            )
            self.assertEqual("succeeded", service.status("demo")["status"])
            self.assertGreater(
                len((task_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()), 5
            )
            self.assertEqual(3, len(sandbox.calls))

    def test_chat_completion_model_parses_action_and_usage(self) -> None:
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "kind": "tool_call",
                                        "tool_call": {
                                            "call_id": "c1",
                                            "name": "list_files",
                                            "arguments": {},
                                        },
                                    }
                                )
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 40, "completion_tokens": 12},
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        model = ChatCompletionsModel(
            "test-model", "fake-key", "https://example.test/v1", client=client
        )
        with tempfile.TemporaryDirectory() as directory:
            task = TaskSpec(task_id="one", instruction="Inspect files", workspace=Path(directory))
            action = model.next_action(
                task, [], [{"name": "list_files", "description": "List files", "input_schema": {}}]
            )

        self.assertEqual("list_files", action.tool_call.name)
        self.assertEqual(40, model.input_tokens)
        self.assertEqual(12, model.output_tokens)
        self.assertEqual("test-model", captured["body"]["model"])

    def test_model_uses_only_first_json_action(self) -> None:
        first = ModelAction.call("first", "list_files").model_dump_json()
        second = ModelAction.call("second", "read_file", path="app.py").model_dump_json()

        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": first + "\n" + second}}]},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        model = ChatCompletionsModel("test-model", "fake-key", client=client)
        with tempfile.TemporaryDirectory() as directory:
            task = TaskSpec(task_id="one", instruction="Inspect files", workspace=Path(directory))
            action = model.next_action(task, [], [])

        self.assertEqual("first", action.tool_call.call_id)

    def test_model_repairs_invalid_json_once(self) -> None:
        responses = iter(
            [
                '{"kind":"tool_call","tool_call":',
                ModelAction.call("fixed", "list_files").model_dump_json(),
            ]
        )
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": next(responses)}}]},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        model = ChatCompletionsModel("test-model", "fake-key", client=client)
        with tempfile.TemporaryDirectory() as directory:
            task = TaskSpec(task_id="one", instruction="Inspect files", workspace=Path(directory))
            action = model.next_action(task, [], [])

        self.assertEqual("fixed", action.tool_call.call_id)
        self.assertEqual(2, len(requests))
        self.assertEqual("assistant", requests[1]["messages"][-2]["role"])


if __name__ == "__main__":
    unittest.main()
