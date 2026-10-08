"""Create an independent Git workspace for each task."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Sequence

from repoagent.contracts import WorkspaceInfo

TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class WorkspaceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def run_git(repo: Path, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *argv],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=60,
        check=False,
    )


class WorkspaceManager:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root.resolve()

    def prepare(
        self,
        task_id: str,
        source_repo: Path,
        base_commit: str = "HEAD",
    ) -> WorkspaceInfo:
        if not TASK_ID_PATTERN.fullmatch(task_id):
            raise WorkspaceError("INVALID_TASK_ID", "task_id contains unsupported characters")

        source = source_repo.resolve(strict=True)
        if not source.is_dir():
            raise WorkspaceError("SOURCE_NOT_DIRECTORY", "source repository is not a directory")

        inside = run_git(source, ["rev-parse", "--is-inside-work-tree"])
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            raise WorkspaceError("NOT_A_GIT_REPOSITORY", "source is not a Git repository")

        dirty = run_git(source, ["status", "--porcelain", "--untracked-files=all"])
        if dirty.returncode != 0:
            raise WorkspaceError("GIT_STATUS_FAILED", dirty.stderr.strip())
        if dirty.stdout.strip():
            raise WorkspaceError(
                "SOURCE_REPOSITORY_DIRTY",
                "source repository has uncommitted or untracked changes",
            )

        resolved = run_git(source, ["rev-parse", "--verify", f"{base_commit}^{{commit}}"])
        if resolved.returncode != 0:
            raise WorkspaceError("BASE_COMMIT_NOT_FOUND", resolved.stderr.strip())
        commit = resolved.stdout.strip()

        task_dir = self.data_root / "tasks" / task_id
        workspace = task_dir / "workspace"
        if task_dir.exists():
            raise WorkspaceError("TASK_ALREADY_EXISTS", f"task already exists: {task_id}")
        task_dir.mkdir(parents=True, exist_ok=False)

        cloned = subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-local",
                "--no-hardlinks",
                str(source),
                str(workspace),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=120,
            check=False,
        )
        if cloned.returncode != 0:
            raise WorkspaceError("GIT_CLONE_FAILED", cloned.stderr.strip())

        checked_out = run_git(workspace, ["checkout", "--quiet", "--detach", commit])
        if checked_out.returncode != 0:
            raise WorkspaceError("GIT_CHECKOUT_FAILED", checked_out.stderr.strip())

        info = WorkspaceInfo(
            task_id=task_id,
            source_repo=source,
            workspace=workspace.resolve(strict=True),
            base_commit=commit,
        )
        (task_dir / "workspace.json").write_text(
            json.dumps(info.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return info
