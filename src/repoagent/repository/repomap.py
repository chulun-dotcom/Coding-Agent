"""Small source map for Python and Java repositories.

The map is rebuilt from files on every request so edits and restored snapshots
cannot leave stale line numbers. It records definitions, not inferred calls.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from repoagent.tools.workspace import iter_files

JAVA_DEFINITION = re.compile(
    r"^\s*(?:(?:public|private|protected|static|final|abstract|synchronized)\s+)*(?:class|interface|enum|record)\s+(\w+)|^\s*(?:(?:public|private|protected|static|final|abstract|synchronized)\s+)+[\w<>?,\[\].]+\s+(\w+)\s*\("
)


def repository_symbols(root: Path, limit: int = 300) -> list[dict[str, object]]:
    symbols: list[dict[str, object]] = []
    for file in sorted(iter_files(root)):
        if file.suffix not in {".py", ".java"} or file.stat().st_size > 1_000_000:
            continue
        try:
            source = file.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        relative = file.relative_to(root).as_posix()
        if file.suffix == ".py":
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            nodes = ((node, "") for node in tree.body)
            for node, parent in nodes:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    symbols.append(
                        {
                            "path": relative,
                            "name": node.name,
                            "line": node.lineno,
                            "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                        }
                    )
                    if isinstance(node, ast.ClassDef):
                        for child in node.body:
                            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                symbols.append(
                                    {
                                        "path": relative,
                                        "name": f"{node.name}.{child.name}",
                                        "line": child.lineno,
                                        "kind": "method",
                                    }
                                )
        else:
            for number, line in enumerate(source.splitlines(), 1):
                match = JAVA_DEFINITION.match(line)
                if match:
                    symbols.append(
                        {
                            "path": relative,
                            "name": match.group(1) or match.group(2),
                            "line": number,
                            "kind": "definition",
                        }
                    )
        if len(symbols) >= limit:
            return symbols[:limit]
    return symbols
