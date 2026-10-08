from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from repoagent.agent import AgentLoop
from repoagent.contracts import Budget, ModelAction, RunStatus, TaskSpec
from repoagent.model import ScriptedModel
from repoagent.persistence import JsonlEventStore
from repoagent.sandbox import ExecutionResult, FakeSandbox
from repoagent.tools import build_default_registry
from repoagent.verification import VerificationRunner


class AgentLoopTest(unittest.TestCase):
    def test_finish_triggers_independent_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "example.py").write_text("answer = 42\n", encoding="utf-8")
            sandbox = FakeSandbox(
                [
                    ExecutionResult(exit_code=0, output="8 passed"),
                    ExecutionResult(exit_code=0, output="9 passed"),
                ]
            )
            model = ScriptedModel(
                [
                    ModelAction.call("1", "read_file", path="example.py"),
                    ModelAction.done("The requested change is complete."),
                ]
            )
            loop = AgentLoop(
                model=model,
                tools=build_default_registry(),
                verifier=VerificationRunner(sandbox),
            )
            report = loop.run(
                TaskSpec(
                    task_id="demo",
                    instruction="Check the answer.",
                    workspace=workspace,
                    verification_command=["python", "-m", "unittest"],
                    require_patch=False,
                )
            )

            self.assertEqual(RunStatus.SUCCEEDED, report.status)
            self.assertIsNotNone(report.verification)
            self.assertEqual(2, len(sandbox.calls))
            self.assertEqual("verification", report.events[-2].type)

    def test_step_budget_stops_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            model = ScriptedModel(
                [
                    ModelAction.call("1", "list_files"),
                    ModelAction.call("2", "list_files"),
                ]
            )
            report = AgentLoop(model, build_default_registry()).run(
                TaskSpec(
                    task_id="budget",
                    instruction="Inspect the repository.",
                    workspace=workspace,
                    budget=Budget(max_steps=2),
                )
            )
            self.assertEqual(RunStatus.BUDGET_EXCEEDED, report.status)
            self.assertEqual(2, report.steps)

    def test_repeated_tool_calls_stop_before_spending_full_budget(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            model = ScriptedModel(
                [ModelAction.call(str(number), "list_files") for number in range(20)]
            )
            report = AgentLoop(model, build_default_registry()).run(
                TaskSpec(
                    task_id="repeated",
                    instruction="Inspect the repository.",
                    workspace=workspace,
                    budget=Budget(max_steps=20),
                )
            )

            self.assertEqual(RunStatus.FAILED, report.status)
            self.assertEqual("NO_PROGRESS", report.failure_reason)
            self.assertEqual(8, report.steps)

    def test_failed_verification_is_returned_to_agent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "example.py").write_text("answer = 42\n", encoding="utf-8")
            sandbox = FakeSandbox(
                [
                    ExecutionResult(exit_code=0, output="1 passed"),
                    ExecutionResult(exit_code=1, output="1 failed"),
                    ExecutionResult(exit_code=0, output="1 passed"),
                ]
            )
            model = ScriptedModel(
                [
                    ModelAction.done("First attempt."),
                    ModelAction.call("1", "read_file", path="example.py"),
                    ModelAction.done("Second attempt."),
                ]
            )
            report = AgentLoop(
                model,
                build_default_registry(),
                VerificationRunner(sandbox),
            ).run(
                TaskSpec(
                    task_id="retry",
                    instruction="Fix the example.",
                    workspace=workspace,
                    verification_command=["python", "-m", "unittest"],
                    require_patch=False,
                )
            )

            self.assertEqual(RunStatus.SUCCEEDED, report.status)
            self.assertEqual(3, len(sandbox.calls))
            verification_events = [event for event in report.events if event.type == "verification"]
            self.assertEqual(3, len(verification_events))
            self.assertEqual("baseline", verification_events[0].payload["phase"])
            self.assertFalse(verification_events[1].payload["passed"])
            self.assertTrue(verification_events[2].payload["passed"])

    def test_failed_baseline_stops_before_model_call(self) -> None:
        class ModelMustNotRun:
            def next_action(self, task, events, tool_schemas):
                raise AssertionError("model must not run when baseline tests fail")

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            sandbox = FakeSandbox([ExecutionResult(exit_code=1, output="1 failed")])
            report = AgentLoop(
                ModelMustNotRun(),
                build_default_registry(),
                VerificationRunner(sandbox),
            ).run(
                TaskSpec(
                    task_id="bad-baseline",
                    instruction="Fix the project.",
                    workspace=workspace,
                    verification_command=["pytest", "-q"],
                )
            )

            self.assertEqual(RunStatus.FAILED, report.status)
            self.assertEqual(0, report.steps)
            self.assertEqual("TEST_FAILED", report.verification.error_code)

    def test_baseline_and_final_can_use_different_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            sandbox = FakeSandbox(
                [
                    ExecutionResult(exit_code=0, output="2 passed"),
                    ExecutionResult(exit_code=0, output="3 passed"),
                ]
            )
            report = AgentLoop(
                ScriptedModel([ModelAction.done("Done.")]),
                build_default_registry(),
                VerificationRunner(sandbox),
            ).run(
                TaskSpec(
                    task_id="separate-tests",
                    instruction="Fix the project.",
                    workspace=workspace,
                    baseline_command=["pytest", "-q", "tests"],
                    verification_command=["pytest", "-q", "tests", "/acceptance"],
                    require_patch=False,
                )
            )

            self.assertEqual(RunStatus.SUCCEEDED, report.status)
            self.assertEqual(["pytest", "-q", "tests"], sandbox.calls[0][0])
            self.assertEqual(["pytest", "-q", "tests", "/acceptance"], sandbox.calls[1][0])

    def test_hidden_verification_failure_is_not_returned_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            sandbox = FakeSandbox(
                [
                    ExecutionResult(exit_code=0, output="2 passed"),
                    ExecutionResult(exit_code=1, output="1 failed, 2 passed"),
                ]
            )
            report = AgentLoop(
                ScriptedModel(
                    [
                        ModelAction.done("First attempt."),
                        ModelAction.done("This action must not be used."),
                    ]
                ),
                build_default_registry(),
                VerificationRunner(sandbox),
            ).run(
                TaskSpec(
                    task_id="hidden-tests",
                    instruction="Fix the project.",
                    workspace=workspace,
                    verification_command=["pytest", "-q", "/acceptance"],
                    allow_verification_retry=False,
                )
            )

            self.assertEqual(RunStatus.FAILED, report.status)
            self.assertEqual(1, report.steps)
            self.assertEqual(2, len(sandbox.calls))

    def test_events_are_written_to_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            model = ScriptedModel([ModelAction.done("Waiting for verification.")])
            store = JsonlEventStore(root / "data")
            report = AgentLoop(
                model,
                build_default_registry(),
                event_store=store,
            ).run(
                TaskSpec(
                    task_id="trace",
                    instruction="Record the run.",
                    workspace=workspace,
                )
            )

            event_file = root / "data" / "tasks" / "trace" / "events.jsonl"
            lines = event_file.read_text(encoding="utf-8").splitlines()
            saved = [json.loads(line) for line in lines]
            self.assertEqual(len(report.events), len(saved))
            self.assertEqual([1, 2], [item["seq"] for item in saved])
            self.assertEqual("run_finished", saved[-1]["type"])


if __name__ == "__main__":
    unittest.main()
