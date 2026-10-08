from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import httpx

from repoagent.agent.loop import AgentLoop
from repoagent.contracts import ModelAction, RunStatus, TaskSpec
from repoagent.model import ChatCompletionsModel, ScriptedModel
from repoagent.persistence.checkpoints import CheckpointManager
from repoagent.persistence.database import TaskDatabase
from repoagent.sandbox import DockerSandbox, ExecutionResult, FakeSandbox
from repoagent.service import TaskService
from repoagent.tools import build_default_registry

PATCH = """diff --git a/answer.py b/answer.py
--- a/answer.py
+++ b/answer.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
"""


def source_repo(root: Path) -> Path:
    source = root / "source"
    source.mkdir()
    (source / "answer.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "test_answer.py").write_text(
        "from answer import VALUE\n\ndef test_positive():\n    assert VALUE > 0\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "add",
            ".",
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-q",
            "-m",
            "initial",
        ],
        check=True,
    )
    return source


class CrashingModel:
    def __init__(self) -> None:
        self.calls = 0

    def next_action(self, *_):
        self.calls += 1
        if self.calls == 1:
            return ModelAction.call("patch", "apply_patch", patch=PATCH)
        raise RuntimeError("simulated crash")


class InvalidModel:
    def next_action(self, *_):
        raise ValueError("invalid JSON")


class RecoveryTest(unittest.TestCase):
    def test_checkpoint_restores_added_and_deleted_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "original.txt").write_text("before", encoding="utf-8")
            checkpoints = CheckpointManager(root)
            checkpoint_id = checkpoints.create(workspace, 1)
            (workspace / "original.txt").unlink()
            (workspace / "new.txt").write_text("after", encoding="utf-8")
            checkpoints.restore(workspace, checkpoint_id)
            self.assertEqual("before", (workspace / "original.txt").read_text(encoding="utf-8"))
            self.assertFalse((workspace / "new.txt").exists())

    def test_invalid_model_protocol_stops_after_three_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task = TaskSpec(
                task_id="bad-model",
                instruction="inspect",
                workspace=Path(directory),
                baseline_command=None,
                verification_command=None,
            )
            report = AgentLoop(InvalidModel(), build_default_registry()).run(task)
            self.assertEqual("MODEL_PROTOCOL_ERROR", report.failure_reason)
            self.assertEqual(3, sum(event.type == "model_error" for event in report.events))

    def test_dead_worker_is_marked_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = TaskDatabase(Path(directory))
            database.create("orphan", "source", "commit", "task", "model")
            with database._connect() as connection:
                connection.execute(
                    "UPDATE tasks SET status='running', owner_pid=2147483647 WHERE task_id='orphan'"
                )
            database.recover_orphans()
            self.assertEqual("interrupted", database.get("orphan")["status"])

    def test_resume_restores_last_stable_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = source_repo(root)
            service = TaskService(
                root / "data", sandbox=FakeSandbox([ExecutionResult(0, "1 passed")])
            )
            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                service.run(source, "Set VALUE to 2", model=CrashingModel(), task_id="recovery")
            self.assertEqual("interrupted", service.status("recovery")["status"])
            workspace = root / "data" / "tasks" / "recovery" / "workspace"
            (workspace / "answer.py").write_text("VALUE = 999\n", encoding="utf-8")
            resumed = TaskService(
                root / "data", sandbox=FakeSandbox([ExecutionResult(0, "1 passed")])
            )
            report = resumed.resume(
                "recovery", model=ScriptedModel([ModelAction.done("Fixed value")])
            )
            self.assertEqual(RunStatus.SUCCEEDED, report.status)
            self.assertEqual("VALUE = 2\n", (workspace / "answer.py").read_text(encoding="utf-8"))
            self.assertEqual(
                list(range(1, len(report.events) + 1)), [event.seq for event in report.events]
            )

    @unittest.skipUnless(
        os.environ.get("SHI_AGENT_RUN_DOCKER_TESTS") == "1", "set SHI_AGENT_RUN_DOCKER_TESTS=1"
    )
    def test_real_docker_task_exports_verified_patch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = source_repo(root)
            service = TaskService(
                root / "data", sandbox=DockerSandbox(image="shi-agent-python:test")
            )
            report = service.run(
                source,
                "Set VALUE to 2",
                model=ScriptedModel(
                    [
                        ModelAction.call("read", "read_file", path="answer.py"),
                        ModelAction.call("patch", "apply_patch", patch=PATCH),
                        ModelAction.call("test", "run_tests"),
                        ModelAction.done("Set value to 2"),
                    ]
                ),
                task_id="docker-e2e",
            )
            self.assertEqual(RunStatus.SUCCEEDED, report.status)
            self.assertTrue(report.verification and report.verification.passed)
            self.assertIn(
                "+VALUE = 2",
                (root / "data" / "tasks" / "docker-e2e" / "artifacts" / "patch.diff").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertEqual("VALUE = 1\n", (source / "answer.py").read_text(encoding="utf-8"))

    @unittest.skipUnless(
        os.environ.get("SHI_AGENT_RUN_DOCKER_TESTS") == "1", "set SHI_AGENT_RUN_DOCKER_TESTS=1"
    )
    def test_http_model_protocol_drives_real_docker_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = source_repo(root)
            actions = iter(
                [
                    ModelAction.call("read", "read_file", path="answer.py"),
                    ModelAction.call("patch", "apply_patch", patch=PATCH),
                    ModelAction.done("Set value to 2"),
                ]
            )

            def handler(request: httpx.Request) -> httpx.Response:
                body = json.loads(request.content)
                self.assertEqual("mock-coder", body["model"])
                return httpx.Response(
                    200,
                    json={
                        "choices": [{"message": {"content": next(actions).model_dump_json()}}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                    },
                )

            model = ChatCompletionsModel(
                "mock-coder",
                "test-key",
                "https://example.test/v1",
                client=httpx.Client(transport=httpx.MockTransport(handler)),
            )
            report = TaskService(
                root / "data", sandbox=DockerSandbox(image="shi-agent-python:test")
            ).run(source, "Set VALUE to 2", model=model, task_id="http-e2e")
            self.assertEqual(RunStatus.SUCCEEDED, report.status)
            self.assertEqual(30, report.input_tokens)

    @unittest.skipUnless(
        os.environ.get("SHI_AGENT_RUN_DOCKER_TESTS") == "1", "set SHI_AGENT_RUN_DOCKER_TESTS=1"
    )
    def test_hidden_acceptance_rejects_visible_test_false_positive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = source_repo(root)
            hidden = root / "hidden"
            hidden.mkdir()
            (hidden / "test_acceptance.py").write_text(
                "from answer import VALUE\n\ndef test_exact_value():\n    assert VALUE == 2\n",
                encoding="utf-8",
                newline="\n",
            )
            wrong_patch = PATCH.replace("+VALUE = 2", "+VALUE = 3")
            report = TaskService(
                root / "data", sandbox=DockerSandbox(image="shi-agent-python:test")
            ).run(
                source,
                "Set VALUE to 2",
                acceptance=hidden,
                model=ScriptedModel(
                    [
                        ModelAction.call(
                            "probe",
                            "run_command",
                            argv=[
                                "python",
                                "-c",
                                "from pathlib import Path; print(Path('/acceptance').exists())",
                            ],
                        ),
                        ModelAction.call("patch", "apply_patch", patch=wrong_patch),
                        ModelAction.done("Set value"),
                    ]
                ),
                task_id="hidden-failure",
            )
            self.assertEqual(RunStatus.FAILED, report.status)
            self.assertEqual("ACCEPTANCE_FAILED", report.failure_reason)
            self.assertTrue(report.verification and report.verification.passed)
            self.assertTrue(report.acceptance and not report.acceptance.passed)
            probe = next(
                event.payload
                for event in report.events
                if event.type == "tool_result" and event.payload.get("call_id") == "probe"
            )
            self.assertIn("False", probe["output_excerpt"])
