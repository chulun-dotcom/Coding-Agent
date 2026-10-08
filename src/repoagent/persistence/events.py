"""Write task events to a local JSONL file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from repoagent.contracts import TraceEvent


class EventStore(Protocol):
    def append(self, task_id: str, event: TraceEvent) -> None:
        """Save one event before the next Agent step starts."""


class JsonlEventStore:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root.resolve()

    def append(self, task_id: str, event: TraceEvent) -> None:
        task_dir = self.data_root / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        event_file = task_dir / "events.jsonl"
        line = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        with event_file.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line + "\n")
            stream.flush()

    def read(self, task_id: str) -> list[TraceEvent]:
        path = self.data_root / "tasks" / task_id / "events.jsonl"
        if not path.exists():
            return []
        return [
            TraceEvent.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]
