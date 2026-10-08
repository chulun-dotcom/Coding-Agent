"""Build a bounded Agent context from the task and recent events."""

from __future__ import annotations

import json
from typing import Sequence

from repoagent.contracts import TaskSpec, TraceEvent
from repoagent.repository.repomap import repository_symbols
from repoagent.tools.workspace import iter_files

SYSTEM_PROMPT = """你是 SHI Agent，一个在隔离工作区中修改代码的 Agent。
每轮只返回一个 JSON 对象，不要 Markdown，不要一次输出多个动作。工具执行结果会在下一轮提供。
要调用工具时返回：
{"kind":"tool_call","tool_call":{"call_id":"唯一编号","name":"工具名","arguments":{}}}
完成时返回：{"kind":"finish","finish":{"summary":"修改摘要","limitations":[]}}
先搜索和阅读代码。小范围修改用 replace_text，大块修改用 apply_patch。
代码已修改后应调用 run_tests，测试通过后返回 finish。
补丁应用失败时，可用 replace_text 精确替换已经读到的旧文本。
不要重复读取内容没有变化的文件；工具失败时先看错误，再决定是否需要修改。
只根据真实工具结果判断，不要声称未执行的验证已通过。"""


class ContextBuilder:
    def __init__(self, max_chars: int = 24000) -> None:
        self.max_chars = max_chars

    def build(
        self,
        task: TaskSpec,
        events: Sequence[TraceEvent],
        tool_schemas: list[dict[str, object]],
    ) -> list[dict[str, str]]:
        root = task.workspace.resolve(strict=True)
        files = sorted(path.relative_to(root).as_posix() for path in iter_files(root))[:150]
        recent = [
            {"type": event.type, "payload": event.payload}
            for event in events[-24:]
            if event.type != "model_action"
        ]
        completed_tools = [
            {
                "name": event.payload.get("tool_name"),
                "ok": event.payload.get("ok"),
                "changed": event.payload.get("workspace_changed"),
            }
            for event in events
            if event.type == "tool_result"
        ]
        fixed = {
            "task": task.instruction,
            "files": files,
            "repo_map": repository_symbols(root, limit=120),
            "tools": tool_schemas,
            "verification_command": task.verification_command,
            "recent_tool_progress": completed_tools[-12:],
        }
        fixed_text = json.dumps(fixed, ensure_ascii=False)
        overhead = len(SYSTEM_PROMPT) + len("\n最近执行结果：") + 2
        while len(fixed_text) + overhead > self.max_chars and (fixed["repo_map"] or fixed["files"]):
            if fixed["repo_map"]:
                fixed["repo_map"] = fixed["repo_map"][: len(fixed["repo_map"]) // 2]
            elif fixed["files"]:
                fixed["files"] = fixed["files"][: len(fixed["files"]) // 2]
            fixed_text = json.dumps(fixed, ensure_ascii=False)
        if len(fixed_text) + overhead > self.max_chars:
            raise RuntimeError("task and tool schemas exceed the configured context size")
        remaining = max(0, self.max_chars - len(fixed_text) - overhead)
        event_text = json.dumps(recent, ensure_ascii=False)
        if len(event_text) > remaining:
            # Keep the latest failures and tool results. Full events stay on disk.
            trimmed: list[dict[str, object]] = []
            for item in reversed(recent):
                candidate = json.dumps([item, *trimmed], ensure_ascii=False)
                if len(candidate) > remaining:
                    break
                trimmed.insert(0, item)
            event_text = json.dumps(trimmed, ensure_ascii=False)
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": fixed_text + "\n最近执行结果：" + event_text},
        ]
