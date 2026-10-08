from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from repoagent.sandbox import DockerSandbox


@unittest.skipUnless(
    os.environ.get("SHI_AGENT_RUN_DOCKER_TESTS") == "1",
    "set SHI_AGENT_RUN_DOCKER_TESTS=1 to run Docker integration tests",
)
class DockerLiveTest(unittest.TestCase):
    def test_example_project_runs_inside_sandbox(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        example = project_root / "examples" / "pagination_service"
        result = DockerSandbox(image="shi-agent-python:test").execute(
            ["pytest", "-q"], example, timeout_seconds=60
        )
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn("2 passed", result.output)

    def test_java_seed_runs_offline_inside_sandbox(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        seed = project_root / "docker" / "java" / "seed"
        result = DockerSandbox(image="shi-agent-java:test").execute(
            ["mvn", "-B", "-o", "test"], seed, timeout_seconds=90
        )
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn("Tests run: 1", result.output)

    def test_cancel_stops_running_container(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        stop = threading.Event()
        timer = threading.Timer(1.0, stop.set)
        timer.start()
        try:
            result = DockerSandbox(image="shi-agent-python:test", cancelled=stop.is_set).execute(
                ["python", "-c", "import time; time.sleep(30)"],
                project_root / "examples" / "pagination_service",
                45,
            )
        finally:
            timer.cancel()
        self.assertIsNone(result.exit_code)
        self.assertIn("cancelled", result.output)

    def test_git_metadata_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            head = (root / ".git" / "HEAD").read_text(encoding="utf-8")
            result = DockerSandbox(image="shi-agent-python:test").execute(
                [
                    "python",
                    "-c",
                    "from pathlib import Path; Path('.git/HEAD').write_text('changed')",
                ],
                root,
                30,
            )
            self.assertNotEqual(0, result.exit_code)
            self.assertEqual(head, (root / ".git" / "HEAD").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
