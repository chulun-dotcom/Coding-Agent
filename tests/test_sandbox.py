from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from unittest.mock import patch

from repoagent.sandbox import DockerSandbox


class DockerSandboxTest(unittest.TestCase):
    def test_linux_uses_host_user_for_writable_workspace(self) -> None:
        with (
            patch("repoagent.sandbox.os.getuid", return_value=1001, create=True),
            patch("repoagent.sandbox.os.getgid", return_value=1002, create=True),
        ):
            sandbox = DockerSandbox()

        self.assertEqual("1001:1002", sandbox.user)

    def test_command_has_required_isolation_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = DockerSandbox(image="shi-agent-python:test")
            command = sandbox.build_command(["python", "-m", "unittest"], Path(directory))

        self.assertEqual(["docker", "run", "--rm"], command[:3])
        self.assertIn("none", command)
        self.assertIn("--read-only", command)
        self.assertIn("ALL", command)
        self.assertIn("no-new-privileges", command)
        container_user = command[command.index("--user") + 1]
        self.assertNotEqual("0:0", container_user)
        self.assertIn("shi-agent-python:test", command)
        self.assertEqual(["python", "-m", "unittest"], command[-3:])

    def test_read_only_acceptance_tests_can_be_mounted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            acceptance = root / "acceptance"
            workspace.mkdir()
            acceptance.mkdir()
            sandbox = DockerSandbox(read_only_mounts={acceptance: "/acceptance"})
            command = sandbox.build_command(["pytest", "-q", "/acceptance"], workspace)

        mount = next(item for item in command if "target=/acceptance" in item)
        self.assertIn("readonly", mount)

    @patch("repoagent.sandbox.subprocess.run")
    def test_execute_returns_command_output(self, run) -> None:
        run.return_value = CompletedProcess(args=[], returncode=0, stdout="2 passed", stderr="")
        with tempfile.TemporaryDirectory() as directory:
            result = DockerSandbox().execute(["pytest", "-q"], Path(directory), 30)
        self.assertEqual(0, result.exit_code)
        self.assertEqual("2 passed", result.output)
        self.assertFalse(result.timed_out)

    @patch("repoagent.sandbox.subprocess.run")
    def test_execute_reports_timeout(self, run) -> None:
        run.side_effect = TimeoutExpired(cmd=["docker"], timeout=1, output="partial")
        with tempfile.TemporaryDirectory() as directory:
            result = DockerSandbox().execute(["pytest", "-q"], Path(directory), 1)
        self.assertIsNone(result.exit_code)
        self.assertTrue(result.timed_out)
        self.assertIn("partial", result.output)


if __name__ == "__main__":
    unittest.main()
