"""Read-only repository exploration tools."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .base import Tool, ToolExecutionError

IGNORED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "dist",
    "build",
    "target",
}


def ignored_path(path: Path) -> bool:
    return any(
        part in IGNORED_PARTS
        or part == ".env"
        or part.startswith(".env.")
        or part in {"id_rsa", "id_ed25519", "credentials.json"}
        or part.endswith((".pem", ".key"))
        for part in path.parts
    )


def safe_path(workspace: Path, relative_path: str, *, must_exist: bool = True) -> Path:
    root = workspace.resolve(strict=True)
    if ignored_path(Path(relative_path)):
        raise ToolExecutionError("PATH_NOT_ALLOWED", f"path is not allowed: {relative_path}")
    candidate = (root / relative_path).resolve(strict=False)
    if candidate != root and root not in candidate.parents:
        raise ToolExecutionError(
            "PATH_OUTSIDE_WORKSPACE", f"path escapes workspace: {relative_path}"
        )
    if must_exist and not candidate.exists():
        raise ToolExecutionError("PATH_NOT_FOUND", f"path does not exist: {relative_path}")
    return candidate


def iter_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        if path.is_file() and not ignored_path(path):
            yield path


class ListFilesTool(Tool):
    name = "list_files"
    description = "List repository files below a relative directory."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "default": "."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
        },
        "additionalProperties": False,
    }

    def run(
        self, workspace: Path, path: str = ".", limit: int = 500, **_: Any
    ) -> tuple[str, bool, int | None, str | None]:
        if not 1 <= limit <= 2000:
            raise ToolExecutionError("INVALID_ARGUMENT", "limit must be between 1 and 2000")
        target = safe_path(workspace, path)
        if not target.is_dir():
            raise ToolExecutionError("NOT_A_DIRECTORY", f"not a directory: {path}")
        files = sorted(
            file.relative_to(workspace.resolve()).as_posix() for file in iter_files(target)
        )
        selected = files[:limit]
        suffix = f"\n... {len(files) - limit} more files" if len(files) > limit else ""
        return "\n".join(selected) + suffix, False, None, None


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read a UTF-8 text file by an inclusive line range."
    input_schema = {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
        },
        "additionalProperties": False,
    }

    def run(
        self,
        workspace: Path,
        path: str,
        start_line: int = 1,
        end_line: int = 400,
        **_: Any,
    ) -> tuple[str, bool, int | None, str | None]:
        if start_line < 1 or end_line < start_line:
            raise ToolExecutionError("INVALID_ARGUMENT", "invalid line range")
        if end_line - start_line > 1000:
            raise ToolExecutionError(
                "READ_LIMIT_EXCEEDED", "a single read may contain at most 1001 lines"
            )
        target = safe_path(workspace, path)
        if not target.is_file():
            raise ToolExecutionError("NOT_A_FILE", f"not a file: {path}")
        raw = target.read_bytes()
        if b"\x00" in raw:
            raise ToolExecutionError(
                "BINARY_FILE_UNSUPPORTED", f"binary files are not supported: {path}"
            )
        lines = raw.decode("utf-8").splitlines()
        selected = lines[start_line - 1 : end_line]
        rendered = "\n".join(
            f"{line_number:>6} | {line}"
            for line_number, line in enumerate(selected, start=start_line)
        )
        return rendered, False, None, None


class SearchCodeTool(Tool):
    name = "search_code"
    description = "Search UTF-8 repository files with a literal or regular expression."
    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "path": {"type": "string", "default": "."},
            "regex": {"type": "boolean", "default": False},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        "additionalProperties": False,
    }

    def run(
        self,
        workspace: Path,
        query: str,
        path: str = ".",
        regex: bool = False,
        limit: int = 100,
        **_: Any,
    ) -> tuple[str, bool, int | None, str | None]:
        if not query:
            raise ToolExecutionError("INVALID_ARGUMENT", "query must not be empty")
        if not 1 <= limit <= 500:
            raise ToolExecutionError("INVALID_ARGUMENT", "limit must be between 1 and 500")
        target = safe_path(workspace, path)
        pattern = re.compile(query if regex else re.escape(query))
        matches: list[str] = []
        for file in iter_files(target):
            try:
                content = file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for line_number, line in enumerate(content.splitlines(), start=1):
                if pattern.search(line):
                    relative = file.relative_to(workspace.resolve()).as_posix()
                    matches.append(f"{relative}:{line_number}:{line[:300]}")
                    if len(matches) >= limit:
                        return "\n".join(matches) + "\n... match limit reached", False, None, None
        return "\n".join(matches), False, None, None
