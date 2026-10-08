"""Model boundary and a deterministic implementation used by tests."""

from __future__ import annotations

import json
import os
from collections import deque
from pathlib import Path
from typing import Protocol, Sequence

import httpx
from dotenv import load_dotenv

from .context import ContextBuilder
from .contracts import ModelAction, TaskSpec, TraceEvent


class ModelGateway(Protocol):
    def next_action(
        self,
        task: TaskSpec,
        events: Sequence[TraceEvent],
        tool_schemas: list[dict[str, object]],
    ) -> ModelAction:
        """Select the next action without executing it."""


class ScriptedModel:
    """Return a fixed action sequence for deterministic loop tests and demos."""

    def __init__(self, actions: Sequence[ModelAction]) -> None:
        self._actions = deque(actions)

    def next_action(
        self,
        task: TaskSpec,
        events: Sequence[TraceEvent],
        tool_schemas: list[dict[str, object]],
    ) -> ModelAction:
        del task, events, tool_schemas
        if not self._actions:
            return ModelAction.done(
                "Scripted model exhausted its actions.",
                ["No explicit finish action was provided."],
            )
        return self._actions.popleft()


class ChatCompletionsModel:
    """OpenAI-compatible chat completions endpoint with JSON actions."""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        context: ContextBuilder | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not model or not api_key:
            raise ValueError("model and API key are required")
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.context = context or ContextBuilder()
        self.client = client or httpx.Client(timeout=90)
        self.input_tokens = 0
        self.output_tokens = 0

    @classmethod
    def from_env(cls) -> "ChatCompletionsModel":
        env_file = Path(os.getenv("SHI_AGENT_ENV_FILE", str(Path.cwd() / ".env")))
        load_dotenv(env_file, override=False)
        return cls(
            model=os.getenv("SHI_AGENT_MODEL", ""),
            api_key=os.getenv("SHI_AGENT_API_KEY", ""),
            base_url=os.getenv("SHI_AGENT_BASE_URL", "https://api.openai.com/v1"),
        )

    def next_action(
        self,
        task: TaskSpec,
        events: Sequence[TraceEvent],
        tool_schemas: list[dict[str, object]],
    ) -> ModelAction:
        messages = self.context.build(task, events, tool_schemas)
        for attempt in range(2):
            content = self._complete(messages)
            try:
                action_data, _ = json.JSONDecoder().raw_decode(content.lstrip())
                return ModelAction.model_validate(action_data)
            except ValueError as error:
                if attempt == 1:
                    raise ValueError(f"invalid model action after retry: {error}") from error
                messages.extend(
                    [
                        {"role": "assistant", "content": content},
                        {
                            "role": "user",
                            "content": (
                                f"上一个动作不是合法的 JSON：{error}。"
                                "请修正 JSON 编码，只返回一个符合原格式的动作。"
                            ),
                        },
                    ]
                )
        raise AssertionError("unreachable")

    def _complete(self, messages: list[dict[str, str]]) -> str:
        response = self.client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": 4096,
            },
        )
        response.raise_for_status()
        data = response.json()
        usage = data.get("usage") or {}
        self.input_tokens += int(usage.get("prompt_tokens") or 0)
        self.output_tokens += int(usage.get("completion_tokens") or 0)
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("model response did not contain JSON text")
        return content
