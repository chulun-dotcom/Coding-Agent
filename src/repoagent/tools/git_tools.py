"""Workspace mutation and diff inspection tools backed by Git."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Sequence

from .base import Tool, ToolExecutionError
from .workspace import safe_path


def run_git(
    workspace: Path, argv: Sequence[str], stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(workspace), *argv],
        input=stdin.encode("utf-8") if stdin is not None else None,
        capture_output=True,
        timeout=30,
        check=False,
    )
    return subprocess.CompletedProcess(
        result.args,
        result.returncode,
        result.stdout.decode("utf-8", "replace"),
        result.stderr.decode("utf-8", "replace"),
    )


def require_git_workspace(workspace: Path) -> None:
    result = run_git(workspace, ["rev-parse", "--is-inside-work-tree"])
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise ToolExecutionError("NOT_A_GIT_WORKSPACE", "workspace must be a Git work tree")


class ApplyPatchTool(Tool):
    name = "apply_patch"
    description = "Atomically apply a unified Git patch inside the isolated workspace."
    input_schema = {
        "type": "object",
        "required": ["patch"],
        "properties": {"patch": {"type": "string", "minLength": 1}},
        "additionalProperties": False,
    }

    def run(
        self, workspace: Path, patch: str, **_: Any
    ) -> tuple[str, bool, int | None, str | None]:
        require_git_workspace(workspace)
        if not patch.strip():
            raise ToolExecutionError("INVALID_ARGUMENT", "patch must not be empty")
        paths = run_git(workspace, ["apply", "--numstat", "-z", "-"], patch)
        if paths.returncode != 0:
            raise ToolExecutionError("INVALID_PATCH", paths.stderr.strip())
        for item in paths.stdout.split("\0"):
            if not item:
                continue
            fields = item.split("\t", 2)
            if len(fields) != 3:
                raise ToolExecutionError("INVALID_PATCH", "cannot inspect patch paths")
            safe_path(workspace, fields[2], must_exist=False)
        check = run_git(workspace, ["apply", "--check", "--whitespace=nowarn", "-"], patch)
        if check.returncode != 0 and "\r" not in patch:
            crlf_patch = patch.replace("\n", "\r\n")
            crlf_check = run_git(
                workspace, ["apply", "--check", "--whitespace=nowarn", "-"], crlf_patch
            )
            if crlf_check.returncode == 0:
                patch = crlf_patch
                check = crlf_check
        if check.returncode != 0:
            raise ToolExecutionError(
                "PATCH_CHECK_FAILED", f"patch check failed: {check.stderr.strip()}"
            )
        applied = run_git(workspace, ["apply", "--whitespace=nowarn", "-"], patch)
        if applied.returncode != 0:
            raise OSError(f"patch application failed: {applied.stderr.strip()}")
        return "patch applied", True, applied.returncode, None


class ReplaceTextTool(Tool):
    name = "replace_text"
    description = "Replace one exact text occurrence in an existing UTF-8 file."
    input_schema = {
        "type": "object",
        "required": ["path", "old_text", "new_text"],
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string", "minLength": 1},
            "new_text": {"type": "string"},
        },
        "additionalProperties": False,
    }

    def run(
        self, workspace: Path, path: str, old_text: str, new_text: str, **_: Any
    ) -> tuple[str, bool, int | None, str | None]:
        target = safe_path(workspace, path)
        if not target.is_file():
            raise ToolExecutionError("NOT_A_FILE", f"not a file: {path}")
        if not old_text:
            raise ToolExecutionError("INVALID_ARGUMENT", "old_text must not be empty")

        raw = target.read_bytes()
        if b"\x00" in raw or len(raw) > 1_000_000:
            raise ToolExecutionError("FILE_NOT_SUPPORTED", "file must be UTF-8 text below 1 MB")
        content = raw.decode("utf-8")
        if "\r\n" in content:
            old_text = old_text.replace("\r\n", "\n").replace("\n", "\r\n")
            new_text = new_text.replace("\r\n", "\n").replace("\n", "\r\n")
        if content.count(old_text) != 1:
            raise ToolExecutionError(
                "TEXT_NOT_UNIQUE", "old_text must occur exactly once in the file"
            )

        updated = content.replace(old_text, new_text, 1)
        target.write_bytes(updated.encode("utf-8"))
        return f"replaced one occurrence in {path}", True, 0, None


class InspectDiffTool(Tool):
    name = "inspect_diff"
    description = "Show tracked and untracked workspace changes relative to HEAD."
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}

    def run(self, workspace: Path, **_: Any) -> tuple[str, bool, int | None, str | None]:
        require_git_workspace(workspace)
        tracked = run_git(workspace, ["diff", "--no-ext-diff", "--binary", "HEAD"])
        if tracked.returncode != 0:
            raise OSError(tracked.stderr.strip())
        untracked = run_git(workspace, ["ls-files", "--others", "--exclude-standard"])
        if untracked.returncode != 0:
            raise OSError(untracked.stderr.strip())
        output = tracked.stdout
        names = [line for line in untracked.stdout.splitlines() if line]
        if names:
            output += "\nUntracked files:\n" + "\n".join(names)
        return output.strip(), False, tracked.returncode, None
