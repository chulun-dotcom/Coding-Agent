"""Run a reproducible local task without an API key."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from repoagent.contracts import ModelAction
from repoagent.model import ScriptedModel
from repoagent.service import TaskService


def make_patch(source: Path) -> str:
    implementation = source / "pagination.py"
    tests = source / "test_pagination.py"
    original_implementation = implementation.read_text(encoding="utf-8")
    original_tests = tests.read_text(encoding="utf-8")
    try:
        implementation.write_text(
            original_implementation.replace(
                "    start = (page - 1) * page_size",
                '    if page < 1:\n        raise ValueError("page must be at least 1")\n    start = (page - 1) * page_size',
            ),
            encoding="utf-8",
            newline="\n",
        )
        tests.write_text(
            "import pytest\n" + original_tests + "\ndef test_negative_page() -> None:\n"
            "    with pytest.raises(ValueError):\n"
            "        paginate([1, 2, 3], page=-1, page_size=2)\n",
            encoding="utf-8",
            newline="\n",
        )
        diff = subprocess.run(
            ["git", "-C", str(source), "diff", "--binary", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return diff.stdout
    finally:
        implementation.write_text(original_implementation, encoding="utf-8", newline="\n")
        tests.write_text(original_tests, encoding="utf-8", newline="\n")


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="shi-agent-demo-") as directory:
        source = Path(directory) / "source"
        shutil.copytree(
            project / "examples" / "pagination_service",
            source,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"),
        )
        (source / ".gitignore").write_text(
            "__pycache__/\n.pytest_cache/\n*.pyc\n", encoding="utf-8"
        )
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=Demo",
                "-c",
                "user.email=demo@example.com",
                "commit",
                "-q",
                "-m",
                "initial",
            ],
            check=True,
        )
        patch = make_patch(source)
        report = TaskService(project / "data").run(
            source,
            "让负数页码抛出 ValueError，并保留正常分页行为。",
            model=ScriptedModel(
                [
                    ModelAction.call("list", "list_files"),
                    ModelAction.call("read", "read_file", path="pagination.py"),
                    ModelAction.call("patch", "apply_patch", patch=patch),
                    ModelAction.call("test", "run_tests"),
                    ModelAction.done("负数页码现在会抛出 ValueError。"),
                ]
            ),
        )
        print(f"task_id={report.task_id} status={report.status.value}")
        print(project / "data" / "tasks" / report.task_id / "artifacts")
        if report.status.value != "succeeded":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
