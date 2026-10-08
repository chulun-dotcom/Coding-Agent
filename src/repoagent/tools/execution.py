"""Sandbox-backed test execution tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from repoagent.sandbox import Sandbox

from .base import Tool


class RunTestsTool(Tool):
    name = "run_tests"
    description = "Run the task's configured test command in the sandbox."
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}

    def __init__(
        self,
        sandbox: Sandbox,
        command: Sequence[str],
        timeout_seconds: int = 300,
    ) -> None:
        if not command:
            raise ValueError("test command must not be empty")
        self._sandbox = sandbox
        self._command = list(command)
        self._timeout_seconds = timeout_seconds

    def run(self, workspace: Path, **_: Any) -> tuple[str, bool, int | None, str | None]:
        result = self._sandbox.execute(self._command, workspace, self._timeout_seconds)
        if result.timed_out:
            return "test command timed out\n" + result.output, False, result.exit_code, None
        return result.output, False, result.exit_code, None


class RunCommandTool(Tool):
    name = "run_command"
    description = "Run an argv command inside the network-disabled task container."
    input_schema = {
        "type": "object",
        "required": ["argv"],
        "properties": {
            "argv": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
        },
        "additionalProperties": False,
    }

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    def run(
        self,
        workspace: Path,
        argv: list[str],
        timeout_seconds: int = 120,
        **_: Any,
    ) -> tuple[str, bool, int | None, str | None]:
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(item, str) for item in argv)
        ):
            raise ValueError("argv must be a non-empty string list")
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120")
        result = self.sandbox.execute(argv, workspace, timeout_seconds)
        return result.output, True, result.exit_code, None
