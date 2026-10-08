"""Command execution boundary.

Production execution will use Docker. The fake implementation exists so the
agent loop and verification policy can be tested without executing repository
code on the host.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Callable, Protocol, Sequence


@dataclass(frozen=True)
class ExecutionResult:
    exit_code: int | None
    output: str
    timed_out: bool = False


class Sandbox(Protocol):
    def execute(
        self,
        argv: Sequence[str],
        cwd: Path,
        timeout_seconds: int,
    ) -> ExecutionResult:
        """Execute a command inside the configured isolation boundary."""


class FakeSandbox:
    """Deterministic sandbox used by unit tests."""

    def __init__(self, results: Sequence[ExecutionResult]) -> None:
        self._results = list(results)
        self.calls: list[tuple[list[str], Path, int]] = []

    def execute(
        self,
        argv: Sequence[str],
        cwd: Path,
        timeout_seconds: int,
    ) -> ExecutionResult:
        self.calls.append((list(argv), cwd, timeout_seconds))
        if not self._results:
            raise RuntimeError("FakeSandbox has no result for this command")
        return self._results.pop(0)


class DockerSandbox:
    """Run repository commands in a restricted Docker container."""

    def __init__(
        self,
        image: str = "python:3.13-slim",
        memory: str = "1g",
        cpus: str = "1.0",
        pids_limit: int = 256,
        user: str = "65532:65532",
        read_only_mounts: dict[Path, str] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.image = image
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.user = user
        self.read_only_mounts = read_only_mounts or {}
        self.cancelled = cancelled

    @staticmethod
    def _remove_container(cidfile: Path) -> None:
        if cidfile.exists():
            container_id = cidfile.read_text(encoding="ascii").strip()
            if container_id:
                subprocess.run(
                    ["docker", "rm", "-f", container_id],
                    capture_output=True,
                    timeout=15,
                    check=False,
                )

    def build_command(
        self,
        argv: Sequence[str],
        cwd: Path,
    ) -> list[str]:
        workspace = cwd.resolve(strict=True)
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=256m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            self.memory,
            "--cpus",
            self.cpus,
            "--pids-limit",
            str(self.pids_limit),
            "--user",
            self.user,
            "--mount",
            f"type=bind,source={workspace},target=/workspace",
            "--workdir",
            "/workspace",
        ]
        git_dir = workspace / ".git"
        if git_dir.is_dir():
            command.extend(
                ["--mount", f"type=bind,source={git_dir},target=/workspace/.git,readonly"]
            )
        for source, target in sorted(self.read_only_mounts.items(), key=lambda item: str(item[0])):
            resolved_source = source.resolve(strict=True)
            command.extend(
                [
                    "--mount",
                    f"type=bind,source={resolved_source},target={target},readonly",
                ]
            )
        command.extend([self.image, *argv])
        return command

    def execute(
        self,
        argv: Sequence[str],
        cwd: Path,
        timeout_seconds: int,
    ) -> ExecutionResult:
        with tempfile.TemporaryDirectory(prefix="shi-agent-docker-") as directory:
            cidfile = Path(directory) / "container.id"
            command = self.build_command(argv, cwd)
            command[2:2] = ["--cidfile", str(cidfile)]
            if self.cancelled is not None:
                try:
                    process = subprocess.Popen(
                        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                    )
                except FileNotFoundError:
                    return ExecutionResult(exit_code=None, output="docker command was not found")
                deadline = monotonic() + timeout_seconds
                while True:
                    remaining = deadline - monotonic()
                    if self.cancelled() or remaining <= 0:
                        self._remove_container(cidfile)
                        process.kill()
                        stdout, stderr = process.communicate()
                        label = "cancelled" if self.cancelled() else "timed out"
                        return ExecutionResult(
                            exit_code=None,
                            output=(stdout + stderr).decode("utf-8", "replace") + "\n" + label,
                            timed_out=label == "timed out",
                        )
                    try:
                        stdout, stderr = process.communicate(timeout=min(0.5, remaining))
                        return ExecutionResult(
                            exit_code=process.returncode,
                            output=(stdout + b"\n" + stderr).decode("utf-8", "replace").strip(),
                        )
                    except subprocess.TimeoutExpired:
                        continue
            try:
                completed = subprocess.run(
                    command,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                self._remove_container(cidfile)
                stdout = (
                    exc.stdout.decode("utf-8", "replace")
                    if isinstance(exc.stdout, bytes)
                    else (exc.stdout or "")
                )
                stderr = (
                    exc.stderr.decode("utf-8", "replace")
                    if isinstance(exc.stderr, bytes)
                    else (exc.stderr or "")
                )
                return ExecutionResult(
                    exit_code=None, output=(stdout + "\n" + stderr).strip(), timed_out=True
                )
            except FileNotFoundError:
                return ExecutionResult(exit_code=None, output="docker command was not found")

        output = (completed.stdout + "\n" + completed.stderr).strip()
        return ExecutionResult(exit_code=completed.returncode, output=output)
