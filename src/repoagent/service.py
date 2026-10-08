"""One local task from Git checkout to verified patch and report."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from .agent.loop import AgentLoop
from .contracts import Budget, RunReport, TaskSpec
from .model import ChatCompletionsModel, ModelGateway
from .persistence.checkpoints import CheckpointManager
from .persistence.database import TaskDatabase
from .persistence.events import JsonlEventStore
from .reporting import ResultExporter
from .repository.workspace import WorkspaceManager
from .sandbox import DockerSandbox, Sandbox
from .tools import build_default_registry
from .verification import VerificationRunner

PROFILES = {
    "python": ("shi-agent-python:test", ["pytest", "-q"]),
    "java": ("shi-agent-java:test", ["mvn", "-B", "-o", "test"]),
}


class TaskService:
    def __init__(self, data_root: Path, sandbox: Sandbox | None = None) -> None:
        self.data_root = data_root.resolve()
        self.workspaces = WorkspaceManager(self.data_root)
        self.db = TaskDatabase(self.data_root)
        self.db.recover_orphans()
        self.sandbox = sandbox

    def _cancel_requested(self, task_id: str) -> bool:
        task = self.db.get(task_id) or {}
        return task.get("status") == "cancel_requested"

    def run(
        self,
        repo: Path,
        instruction: str,
        *,
        model: ModelGateway | None = None,
        profile: str = "python",
        task_id: str | None = None,
        max_steps: int = 40,
        base_commit: str = "HEAD",
        acceptance: Path | None = None,
    ) -> RunReport:
        if profile not in PROFILES:
            raise ValueError(f"unknown profile: {profile}")
        task_id = task_id or uuid.uuid4().hex[:12]
        image, command = PROFILES[profile]
        acceptance_path = acceptance.resolve(strict=True) if acceptance is not None else None
        if acceptance_path is not None and (not acceptance_path.is_dir() or profile != "python"):
            raise ValueError("acceptance must be an external directory for a Python task")
        if acceptance_path is not None and acceptance_path.is_relative_to(
            repo.resolve(strict=True)
        ):
            raise ValueError("acceptance tests must be outside the source repository")
        model = model or ChatCompletionsModel.from_env()
        existing = self.db.get(task_id)
        if existing is not None and existing["status"] != "queued":
            raise ValueError(f"task already exists: {task_id}")
        try:
            workspace_info = self.workspaces.prepare(task_id, repo, base_commit=base_commit)
        except Exception as exc:
            if existing is not None:
                self.db.update(task_id, "failed", f"{type(exc).__name__}: {exc}")
            raise
        active_sandbox = self.sandbox or DockerSandbox(
            image=image,
            cancelled=lambda: self._cancel_requested(task_id),
        )
        task = TaskSpec(
            task_id=task_id,
            instruction=instruction,
            workspace=workspace_info.workspace,
            baseline_command=command,
            verification_command=command,
            acceptance_command=["python", "-m", "pytest", "-q", "/acceptance"]
            if acceptance_path
            else None,
            acceptance_path=acceptance_path,
            budget=Budget(max_steps=max_steps),
        )
        task_dir = self.data_root / "tasks" / task_id
        (task_dir / "task.json").write_text(
            json.dumps(task.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        model_name = getattr(model, "model", type(model).__name__)
        if existing is None:
            self.db.create(
                task_id,
                str(workspace_info.source_repo),
                workspace_info.base_commit,
                instruction,
                model_name,
            )
        self.db.prepared(
            task_id, str(workspace_info.source_repo), workspace_info.base_commit, model_name
        )
        try:
            loop = self._loop(
                model=model,
                sandbox=active_sandbox,
                command=command,
                task_dir=task_dir,
                task_id=task_id,
                acceptance_path=acceptance_path,
                image=image,
            )
            report = loop.run(task)
            ResultExporter().export(task_dir / "artifacts", workspace_info.workspace, report)
            self.db.update(task_id, report.status.value, report.failure_reason)
            return report
        except Exception as exc:
            status = "interrupted" if CheckpointManager(task_dir).latest() else "failed"
            self.db.update(task_id, status, f"{type(exc).__name__}: {exc}")
            raise

    def _loop(
        self,
        model: ModelGateway,
        sandbox: Sandbox,
        command: list[str],
        task_dir: Path,
        task_id: str,
        acceptance_path: Path | None,
        image: str,
    ) -> AgentLoop:
        acceptance_sandbox = None
        if acceptance_path is not None:
            acceptance_sandbox = self.sandbox or DockerSandbox(
                image=image,
                cancelled=lambda: self._cancel_requested(task_id),
                read_only_mounts={acceptance_path: "/acceptance"},
            )
        return AgentLoop(
            model=model,
            tools=build_default_registry(sandbox, command),
            verifier=VerificationRunner(sandbox),
            event_store=JsonlEventStore(self.data_root),
            checkpoints=CheckpointManager(task_dir),
            cancelled=lambda: self._cancel_requested(task_id),
            acceptance_verifier=VerificationRunner(acceptance_sandbox)
            if acceptance_sandbox
            else None,
        )

    def cancel(self, task_id: str) -> None:
        row = self.status(task_id)
        if row["status"] == "queued":
            self.db.update(task_id, "cancelled")
        elif row["status"] == "running":
            self.db.update(task_id, "cancel_requested")
        else:
            raise ValueError("task is not active")

    def resume(self, task_id: str, *, model: ModelGateway | None = None) -> RunReport:
        row = self.status(task_id)
        if row["status"] != "interrupted":
            raise ValueError("only interrupted tasks can be resumed")
        task_dir = self.data_root / "tasks" / task_id
        task = TaskSpec.model_validate_json((task_dir / "task.json").read_text(encoding="utf-8"))
        events = JsonlEventStore(self.data_root).read(task_id)
        checkpoints = CheckpointManager(task_dir)
        checkpoint_id = checkpoints.latest()
        if checkpoint_id is None:
            raise ValueError("no stable checkpoint exists")
        checkpoint_event = next(
            (
                event
                for event in reversed(events)
                if event.type == "checkpoint"
                and event.payload.get("checkpoint_id") == checkpoint_id
            ),
            None,
        )
        if checkpoint_event is None:
            raise ValueError("checkpoint has no matching event")
        # Discard the incomplete step. Commands are never replayed blindly.
        stable = events[: checkpoint_event.seq]
        model = model or ChatCompletionsModel.from_env()
        profile = next(
            (
                name
                for name, (_, command) in PROFILES.items()
                if command == task.verification_command
            ),
            None,
        )
        if profile is None:
            raise ValueError("unknown saved verification profile")
        image, command = PROFILES[profile]
        if not self.db.claim_resume(task_id):
            raise ValueError("task was claimed by another runner")
        try:
            checkpoints.restore(task.workspace, checkpoint_id)
            event_path = task_dir / "events.jsonl"
            if len(stable) < len(events):
                abandoned = task_dir / "abandoned-events.jsonl"
                with abandoned.open("a", encoding="utf-8") as stream:
                    for event in events[len(stable) :]:
                        stream.write(event.model_dump_json() + "\n")
                event_path.write_text(
                    "".join(event.model_dump_json() + "\n" for event in stable), encoding="utf-8"
                )
            active_sandbox = self.sandbox or DockerSandbox(
                image=image,
                cancelled=lambda: self._cancel_requested(task_id),
            )
            report = self._loop(
                model=model,
                sandbox=active_sandbox,
                command=command,
                task_dir=task_dir,
                task_id=task_id,
                acceptance_path=task.acceptance_path,
                image=image,
            ).run(task, stable)
            ResultExporter().export(task_dir / "artifacts", task.workspace, report)
            self.db.update(task_id, report.status.value, report.failure_reason)
            return report
        except Exception as exc:
            self.db.update(task_id, "interrupted", f"{type(exc).__name__}: {exc}")
            raise

    def status(self, task_id: str) -> dict:
        row = self.db.get(task_id)
        if row is None:
            raise KeyError(task_id)
        return row
