from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from repoagent.repository.workspace import WorkspaceError, WorkspaceManager


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


class WorkspaceManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source"
        self.source.mkdir()
        git(self.source, "init", "-q")
        git(self.source, "config", "user.email", "test@example.com")
        git(self.source, "config", "user.name", "SHI Agent Test")
        (self.source / "app.py").write_text("value = 1\n", encoding="utf-8")
        git(self.source, "add", ".")
        git(self.source, "commit", "-q", "-m", "initial")
        self.manager = WorkspaceManager(self.root / "data")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_prepare_creates_independent_workspace_at_exact_commit(self) -> None:
        expected_commit = git(self.source, "rev-parse", "HEAD")
        info = self.manager.prepare("task-1", self.source)

        self.assertEqual(expected_commit, info.base_commit)
        self.assertEqual(expected_commit, git(info.workspace, "rev-parse", "HEAD"))
        self.assertEqual("value = 1\n", (info.workspace / "app.py").read_text(encoding="utf-8"))

        (info.workspace / "app.py").write_text("value = 2\n", encoding="utf-8")
        self.assertEqual("value = 1\n", (self.source / "app.py").read_text(encoding="utf-8"))
        self.assertTrue((info.workspace.parent / "workspace.json").is_file())

    def test_dirty_source_is_rejected(self) -> None:
        (self.source / "new_file.py").write_text("dirty = True\n", encoding="utf-8")
        with self.assertRaises(WorkspaceError) as raised:
            self.manager.prepare("task-2", self.source)
        self.assertEqual("SOURCE_REPOSITORY_DIRTY", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
