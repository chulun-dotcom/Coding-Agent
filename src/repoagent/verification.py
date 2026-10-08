"""Deterministic final verification."""

from __future__ import annotations

import re
from pathlib import Path

from .contracts import TestCounts, TestPhase, VerificationReport
from .sandbox import Sandbox
from .tools.base import excerpt


class VerificationRunner:
    def __init__(self, sandbox: Sandbox, timeout_seconds: int = 300) -> None:
        self._sandbox = sandbox
        self._timeout_seconds = timeout_seconds

    def run(
        self,
        command: list[str],
        workspace: Path,
        phase: TestPhase = TestPhase.FINAL,
    ) -> VerificationReport:
        try:
            result = self._sandbox.execute(command, workspace, self._timeout_seconds)
        except (OSError, RuntimeError, TimeoutError) as exc:
            return VerificationReport(
                phase=phase,
                command=command,
                passed=False,
                error_code="TEST_ENVIRONMENT_FAILED",
                output_excerpt=str(exc),
            )
        output, _ = excerpt(result.output)
        error_code = None
        if result.timed_out:
            error_code = "TEST_TIMEOUT"
        elif result.exit_code is None:
            error_code = "TEST_ENVIRONMENT_FAILED"
        elif result.exit_code != 0:
            error_code = "TEST_FAILED"
        return VerificationReport(
            phase=phase,
            command=command,
            passed=result.exit_code == 0 and not result.timed_out,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            error_code=error_code,
            output_excerpt=output,
            counts=parse_test_counts(result.output),
        )


PYTEST_COUNT_PATTERN = re.compile(
    r"(?P<count>\d+)\s+(?P<kind>passed|failed|skipped|error|errors)\b",
    re.IGNORECASE,
)
UNITTEST_RAN_PATTERN = re.compile(r"Ran\s+(?P<count>\d+)\s+tests?", re.IGNORECASE)
UNITTEST_FAILED_PATTERN = re.compile(r"FAILED\s*\((?P<details>[^)]*)\)", re.IGNORECASE)


def parse_test_counts(output: str) -> TestCounts:
    """Read common pytest and unittest summaries without depending on either framework."""

    values = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    found_pytest = False
    for match in PYTEST_COUNT_PATTERN.finditer(output):
        found_pytest = True
        kind = match.group("kind").lower()
        key = "errors" if kind in {"error", "errors"} else kind
        values[key] = max(values[key], int(match.group("count")))

    if found_pytest:
        total = sum(values.values())
        return TestCounts(**values, total=total)

    ran_match = UNITTEST_RAN_PATTERN.search(output)
    if ran_match is None:
        return TestCounts()

    total = int(ran_match.group("count"))
    failed_match = UNITTEST_FAILED_PATTERN.search(output)
    if failed_match:
        for item in failed_match.group("details").split(","):
            name, _, raw_count = item.strip().partition("=")
            if not raw_count.isdigit():
                continue
            if name == "failures":
                values["failed"] = int(raw_count)
            elif name == "errors":
                values["errors"] = int(raw_count)
            elif name == "skipped":
                values["skipped"] = int(raw_count)
    values["passed"] = max(0, total - values["failed"] - values["errors"] - values["skipped"])
    return TestCounts(**values, total=total)
