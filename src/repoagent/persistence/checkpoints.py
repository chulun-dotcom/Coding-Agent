"""Stable workspace snapshots for task recovery."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from repoagent.tools.workspace import iter_files


class CheckpointManager:
    def __init__(self, task_dir: Path) -> None:
        self.root = task_dir / "checkpoints"

    def create(self, workspace: Path, seq: int) -> str:
        checkpoint_id = f"{seq:06d}"
        target = self.root / checkpoint_id
        if target.exists():
            raise FileExistsError(target)
        target.mkdir(parents=True)
        names: list[str] = []
        for source in iter_files(workspace):
            relative = source.relative_to(workspace)
            destination = target / "files" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            names.append(relative.as_posix())
        (target / "manifest.json").write_text(json.dumps(names), encoding="utf-8")
        return checkpoint_id

    def latest(self) -> str | None:
        if not self.root.exists():
            return None
        ids = sorted(
            path.name
            for path in self.root.iterdir()
            if path.is_dir() and (path / "manifest.json").is_file()
        )
        return ids[-1] if ids else None

    def restore(self, workspace: Path, checkpoint_id: str) -> None:
        if not checkpoint_id.isdigit():
            raise ValueError("invalid checkpoint ID")
        source = self.root / checkpoint_id
        names = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise ValueError("invalid checkpoint manifest")
        expected = set(names)
        for current in iter_files(workspace):
            if current.relative_to(workspace).as_posix() not in expected:
                current.unlink()
        for name in names:
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
                raise ValueError("unsafe checkpoint path")
            destination = workspace / relative
            if destination.is_symlink() or not destination.resolve().is_relative_to(
                workspace.resolve()
            ):
                raise ValueError("unsafe destination")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / "files" / relative, destination)
