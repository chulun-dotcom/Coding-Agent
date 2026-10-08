"""Data contracts shared by the model, runtime, tools and verifier."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ActionKind(StrEnum):
    TOOL_CALL = "tool_call"
    FINISH = "finish"


class RunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BUDGET_EXCEEDED = "budget_exceeded"
    NEEDS_VERIFICATION = "needs_verification"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class TestPhase(StrEnum):
    BASELINE = "baseline"
    AGENT = "agent"
    FINAL = "final"
    ACCEPTANCE = "acceptance"


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class FinishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)


class ModelAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ActionKind
    tool_call: ToolCall | None = None
    finish: FinishRequest | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "ModelAction":
        if self.kind is ActionKind.TOOL_CALL and self.tool_call is None:
            raise ValueError("tool_call is required for a tool_call action")
        if self.kind is ActionKind.FINISH and self.finish is None:
            raise ValueError("finish is required for a finish action")
        if self.kind is ActionKind.TOOL_CALL and self.finish is not None:
            raise ValueError("finish must be omitted for a tool_call action")
        if self.kind is ActionKind.FINISH and self.tool_call is not None:
            raise ValueError("tool_call must be omitted for a finish action")
        return self

    @classmethod
    def call(cls, call_id: str, name: str, **arguments: Any) -> "ModelAction":
        return cls(
            kind=ActionKind.TOOL_CALL,
            tool_call=ToolCall(call_id=call_id, name=name, arguments=arguments),
        )

    @classmethod
    def done(cls, summary: str, limitations: list[str] | None = None) -> "ModelAction":
        return cls(
            kind=ActionKind.FINISH,
            finish=FinishRequest(summary=summary, limitations=limitations or []),
        )


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    tool_name: str
    ok: bool
    error_code: str | None = None
    output_excerpt: str = ""
    artifact_ref: str | None = None
    exit_code: int | None = None
    duration_ms: int = Field(default=0, ge=0)
    truncated: bool = False
    workspace_changed: bool = False


class TestCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    errors: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)


class VerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: TestPhase
    command: list[str]
    passed: bool
    exit_code: int | None = None
    timed_out: bool = False
    error_code: str | None = None
    output_excerpt: str = ""
    counts: TestCounts = Field(default_factory=TestCounts)


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(default=40, ge=1, le=500)
    max_seconds: int = Field(default=1800, ge=1)
    max_input_tokens: int = Field(default=250000, ge=1)
    max_output_tokens: int = Field(default=50000, ge=1)


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    workspace: Path
    baseline_command: list[str] | None = None
    verification_command: list[str] | None = None
    acceptance_command: list[str] | None = None
    acceptance_path: Path | None = None
    require_clean_baseline: bool = True
    allow_verification_retry: bool = True
    require_patch: bool = True
    budget: Budget = Field(default_factory=Budget)


class WorkspaceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    source_repo: Path
    workspace: Path
    base_commit: str


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=1)
    type: Literal[
        "model_action",
        "model_error",
        "tool_started",
        "tool_result",
        "checkpoint",
        "verification",
        "run_finished",
    ]
    payload: dict[str, Any]


class RunReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: RunStatus
    summary: str
    steps: int = Field(ge=0)
    events: list[TraceEvent]
    baseline: VerificationReport | None = None
    verification: VerificationReport | None = None
    acceptance: VerificationReport | None = None
    failure_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
