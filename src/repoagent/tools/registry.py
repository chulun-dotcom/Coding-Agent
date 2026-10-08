"""Tool registration, schema exposure and validated dispatch."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from repoagent.contracts import ToolCall, ToolResult
from repoagent.sandbox import Sandbox

from .base import Tool
from .execution import RunCommandTool, RunTestsTool
from .git_tools import ApplyPatchTool, InspectDiffTool, ReplaceTextTool
from .workspace import ListFilesTool, ReadFileTool, SearchCodeTool


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool]) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"duplicate tool: {tool.name}")
            self._tools[tool.name] = tool

    def schemas(self) -> list[dict[str, object]]:
        return [self._tools[name].schema() for name in sorted(self._tools)]

    def execute(self, call: ToolCall, workspace: Path) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.name,
                ok=False,
                error_code="UNKNOWN_TOOL",
                output_excerpt=f"unknown tool: {call.name}",
            )
        return tool.invoke(call, workspace)


def build_default_registry(
    sandbox: Sandbox | None = None,
    test_command: list[str] | None = None,
) -> ToolRegistry:
    tools: list[Tool] = [
        ListFilesTool(),
        SearchCodeTool(),
        ReadFileTool(),
        ApplyPatchTool(),
        ReplaceTextTool(),
        InspectDiffTool(),
    ]
    if sandbox is not None and test_command:
        tools.append(RunTestsTool(sandbox, test_command))
        tools.append(RunCommandTool(sandbox))
    return ToolRegistry(tools)
