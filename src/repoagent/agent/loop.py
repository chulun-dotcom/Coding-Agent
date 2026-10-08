"""Explicit single-agent loop with deterministic verification."""

from __future__ import annotations

import json
import subprocess
from time import monotonic
from typing import Any, Callable, Sequence

from repoagent.contracts import (
    ActionKind,
    RunReport,
    RunStatus,
    TaskSpec,
    TestPhase,
    TraceEvent,
)
from repoagent.model import ModelGateway
from repoagent.persistence.checkpoints import CheckpointManager
from repoagent.persistence.events import EventStore
from repoagent.tools.registry import ToolRegistry
from repoagent.verification import VerificationRunner


def repeated_calls_without_change(events: Sequence[TraceEvent]) -> bool:
    """Stop a short cycle of the same tools when no edit has succeeded."""
    calls: list[str] = []
    for event in reversed(events):
        if event.type == "tool_result" and event.payload.get("workspace_changed"):
            break
        if event.type != "model_action":
            continue
        call = event.payload.get("tool_call")
        if not isinstance(call, dict):
            continue
        signature = json.dumps(
            {"name": call.get("name"), "arguments": call.get("arguments")},
            sort_keys=True,
            ensure_ascii=False,
        )
        calls.append(signature)
        if len(calls) == 8:
            return len(set(calls)) <= 3
    return False


class AgentLoop:
    def __init__(
        self,
        model: ModelGateway,
        tools: ToolRegistry,
        verifier: VerificationRunner | None = None,
        event_store: EventStore | None = None,
        checkpoints: CheckpointManager | None = None,
        cancelled: Callable[[], bool] | None = None,
        acceptance_verifier: VerificationRunner | None = None,
    ) -> None:
        self._model = model
        self._tools = tools
        self._verifier = verifier
        self._acceptance_verifier = acceptance_verifier
        self._event_store = event_store
        self._checkpoints = checkpoints
        self._cancelled = cancelled or (lambda: False)

    def run(self, task: TaskSpec, prior_events: Sequence[TraceEvent] = ()) -> RunReport:
        workspace = task.workspace.resolve(strict=True)
        events: list[TraceEvent] = list(prior_events)
        baseline = None
        for previous in events:
            if (
                previous.type == "verification"
                and previous.payload.get("phase") == TestPhase.BASELINE
            ):
                from repoagent.contracts import VerificationReport

                baseline = VerificationReport.model_validate(previous.payload)
                break
        started = monotonic()

        def usage() -> tuple[int, int]:
            return (
                int(getattr(self._model, "input_tokens", 0)),
                int(getattr(self._model, "output_tokens", 0)),
            )

        def exceeded() -> str | None:
            input_tokens, output_tokens = usage()
            if monotonic() - started >= task.budget.max_seconds:
                return "time budget exceeded"
            if input_tokens >= task.budget.max_input_tokens:
                return "input token budget exceeded"
            if output_tokens >= task.budget.max_output_tokens:
                return "output token budget exceeded"
            return None

        def append(event_type: str, payload: dict[str, Any]) -> None:
            event = TraceEvent(
                seq=len(events) + 1,
                type=event_type,
                payload=payload,  # type: ignore[arg-type]
            )
            events.append(event)
            if self._event_store is not None:
                self._event_store.append(task.task_id, event)

        def checkpoint() -> None:
            if self._checkpoints is not None:
                checkpoint_id = self._checkpoints.create(workspace, len(events) + 1)
                append("checkpoint", {"checkpoint_id": checkpoint_id})

        def cancelled_report(steps: int) -> RunReport:
            append("run_finished", {"status": RunStatus.CANCELLED})
            return RunReport(
                task_id=task.task_id,
                status=RunStatus.CANCELLED,
                summary="Task cancelled.",
                steps=steps,
                events=events,
                baseline=baseline,
                failure_reason="CANCELLED",
            )

        baseline_command = task.baseline_command or task.verification_command
        if baseline is None and baseline_command is not None and self._verifier is not None:
            baseline = self._verifier.run(
                baseline_command,
                workspace,
                phase=TestPhase.BASELINE,
            )
            append("verification", baseline.model_dump(mode="json"))
            if self._cancelled():
                return cancelled_report(0)
            if task.require_clean_baseline and not baseline.passed:
                append(
                    "run_finished",
                    {
                        "status": RunStatus.FAILED,
                        "reason": "baseline verification failed",
                        "error_code": baseline.error_code,
                    },
                )
                return RunReport(
                    task_id=task.task_id,
                    status=RunStatus.FAILED,
                    summary="Baseline tests did not pass. The Agent did not modify the workspace.",
                    steps=0,
                    events=events,
                    baseline=baseline,
                    verification=baseline,
                )
            checkpoint()

        completed_steps = sum(event.type in {"model_action", "model_error"} for event in events)
        protocol_errors = 0
        for step in range(completed_steps + 1, task.budget.max_steps + 1):
            if self._cancelled():
                return cancelled_report(step - 1)
            reason = exceeded()
            if reason:
                append("run_finished", {"status": RunStatus.BUDGET_EXCEEDED, "reason": reason})
                return RunReport(
                    task_id=task.task_id,
                    status=RunStatus.BUDGET_EXCEEDED,
                    summary=reason,
                    steps=step - 1,
                    events=events,
                    baseline=baseline,
                    failure_reason=reason,
                    input_tokens=usage()[0],
                    output_tokens=usage()[1],
                )
            try:
                action = self._model.next_action(task, tuple(events), self._tools.schemas())
            except (ValueError, KeyError, IndexError) as exc:
                protocol_errors += 1
                append(
                    "model_error",
                    {"error_code": "MODEL_PROTOCOL_ERROR", "message": str(exc)[:1000]},
                )
                if protocol_errors >= 3:
                    append(
                        "run_finished",
                        {"status": RunStatus.FAILED, "reason": "MODEL_PROTOCOL_ERROR"},
                    )
                    return RunReport(
                        task_id=task.task_id,
                        status=RunStatus.FAILED,
                        summary="Model returned invalid actions three times.",
                        steps=step,
                        events=events,
                        baseline=baseline,
                        failure_reason="MODEL_PROTOCOL_ERROR",
                        input_tokens=usage()[0],
                        output_tokens=usage()[1],
                    )
                continue
            protocol_errors = 0
            append("model_action", action.model_dump(mode="json"))

            if action.kind is ActionKind.TOOL_CALL:
                assert action.tool_call is not None
                append(
                    "tool_started",
                    {"call_id": action.tool_call.call_id, "name": action.tool_call.name},
                )
                result = self._tools.execute(action.tool_call, workspace)
                append("tool_result", result.model_dump(mode="json"))
                checkpoint()
                if repeated_calls_without_change(events):
                    append("run_finished", {"status": RunStatus.FAILED, "reason": "NO_PROGRESS"})
                    return RunReport(
                        task_id=task.task_id,
                        status=RunStatus.FAILED,
                        summary="The Agent repeated the same tools without changing the workspace.",
                        steps=step,
                        events=events,
                        baseline=baseline,
                        failure_reason="NO_PROGRESS",
                        input_tokens=usage()[0],
                        output_tokens=usage()[1],
                    )
                continue

            assert action.finish is not None
            if task.verification_command is None or self._verifier is None:
                append(
                    "run_finished",
                    {"status": RunStatus.NEEDS_VERIFICATION, "reason": "no verifier configured"},
                )
                return RunReport(
                    task_id=task.task_id,
                    status=RunStatus.NEEDS_VERIFICATION,
                    summary=action.finish.summary,
                    steps=step,
                    events=events,
                    baseline=baseline,
                )

            verification = self._verifier.run(
                task.verification_command,
                workspace,
                phase=TestPhase.FINAL,
            )
            append("verification", verification.model_dump(mode="json"))
            if self._cancelled():
                return cancelled_report(step)
            if verification.passed:
                changed = subprocess.run(
                    ["git", "-C", str(workspace), "status", "--porcelain", "--untracked-files=all"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if task.require_patch and (changed.returncode != 0 or not changed.stdout.strip()):
                    append(
                        "run_finished",
                        {"status": RunStatus.NEEDS_VERIFICATION, "reason": "no patch"},
                    )
                    return RunReport(
                        task_id=task.task_id,
                        status=RunStatus.NEEDS_VERIFICATION,
                        summary="Tests passed but the Agent produced no changes.",
                        steps=step,
                        events=events,
                        baseline=baseline,
                        verification=verification,
                        failure_reason="NO_PATCH",
                        input_tokens=usage()[0],
                        output_tokens=usage()[1],
                    )
                acceptance = None
                if task.acceptance_command is not None:
                    verifier = self._acceptance_verifier or self._verifier
                    acceptance = verifier.run(
                        task.acceptance_command, workspace, phase=TestPhase.ACCEPTANCE
                    )
                    append("verification", acceptance.model_dump(mode="json"))
                    if self._cancelled():
                        return cancelled_report(step)
                    if not acceptance.passed:
                        append(
                            "run_finished",
                            {
                                "status": RunStatus.FAILED,
                                "reason": "ACCEPTANCE_FAILED",
                                "error_code": acceptance.error_code,
                            },
                        )
                        return RunReport(
                            task_id=task.task_id,
                            status=RunStatus.FAILED,
                            summary="Independent acceptance tests did not pass.",
                            steps=step,
                            events=events,
                            baseline=baseline,
                            verification=verification,
                            acceptance=acceptance,
                            failure_reason="ACCEPTANCE_FAILED",
                            input_tokens=usage()[0],
                            output_tokens=usage()[1],
                        )
                append("run_finished", {"status": RunStatus.SUCCEEDED})
                return RunReport(
                    task_id=task.task_id,
                    status=RunStatus.SUCCEEDED,
                    summary=action.finish.summary,
                    steps=step,
                    events=events,
                    baseline=baseline,
                    verification=verification,
                    acceptance=acceptance,
                    input_tokens=usage()[0],
                    output_tokens=usage()[1],
                )

            if task.allow_verification_retry:
                # The next model call can read this verification event and continue fixing.
                continue

            append(
                "run_finished",
                {
                    "status": RunStatus.FAILED,
                    "reason": "final verification failed",
                    "error_code": verification.error_code,
                },
            )
            return RunReport(
                task_id=task.task_id,
                status=RunStatus.FAILED,
                summary="Final verification did not pass.",
                steps=step,
                events=events,
                baseline=baseline,
                verification=verification,
                failure_reason=verification.error_code,
                input_tokens=usage()[0],
                output_tokens=usage()[1],
            )

        append("run_finished", {"status": RunStatus.BUDGET_EXCEEDED})
        return RunReport(
            task_id=task.task_id,
            status=RunStatus.BUDGET_EXCEEDED,
            summary="The maximum number of agent steps was reached.",
            steps=task.budget.max_steps,
            events=events,
            baseline=baseline,
            failure_reason="STEP_BUDGET_EXCEEDED",
            input_tokens=usage()[0],
            output_tokens=usage()[1],
        )
