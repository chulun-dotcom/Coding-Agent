"""Tool protocol and shared helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from time import monotonic
from typing import Any

from repoagent.contracts import ToolCall, ToolResult

MAX_EXCERPT_CHARS = 12_000


class ToolExecutionError(ValueError):
    """Expected tool failure with a stable machine-readable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def excerpt(value: str, limit: int = MAX_EXCERPT_CHARS) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    tail_size = min(4_000, limit // 2)
    head_size = limit - tail_size
    return value[:head_size] + "\n... output truncated ...\n" + value[-tail_size:], True


class Tool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any]

    def invoke(self, call: ToolCall, workspace: Path) -> ToolResult:
        started = monotonic()
        try:
            output, changed, exit_code, artifact = self.run(workspace, **call.arguments)
            shortened, truncated = excerpt(output)
            return ToolResult(
                call_id=call.call_id,
                tool_name=self.name,
                ok=True,
                output_excerpt=shortened,
                artifact_ref=artifact,
                exit_code=exit_code,
                duration_ms=int((monotonic() - started) * 1000),
                truncated=truncated,
                workspace_changed=changed,
            )
        except ToolExecutionError as exc:
            return ToolResult(
                call_id=call.call_id,
                tool_name=self.name,
                ok=False,
                error_code=exc.code,
                output_excerpt=str(exc),
                duration_ms=int((monotonic() - started) * 1000),
            )
        except (ValueError, OSError, RuntimeError, TimeoutError) as exc:
            return ToolResult(
                call_id=call.call_id,
                tool_name=self.name,
                ok=False,
                error_code="TOOL_EXECUTION_FAILED",
                output_excerpt=str(exc),
                duration_ms=int((monotonic() - started) * 1000),
            )

    @abstractmethod
    def run(self, workspace: Path, **arguments: Any) -> tuple[str, bool, int | None, str | None]:
        """Return output, workspace_changed, exit_code and artifact_ref."""

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
