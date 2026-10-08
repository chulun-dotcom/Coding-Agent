from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from repoagent.contracts import TestPhase
from repoagent.sandbox import ExecutionResult, FakeSandbox
from repoagent.verification import VerificationRunner, parse_test_counts


class TestCountParserTest(unittest.TestCase):
    def test_parse_pytest_summary(self) -> None:
        counts = parse_test_counts("1 failed, 8 passed, 2 skipped, 1 error in 0.42s")
        self.assertEqual(8, counts.passed)
        self.assertEqual(1, counts.failed)
        self.assertEqual(2, counts.skipped)
        self.assertEqual(1, counts.errors)
        self.assertEqual(12, counts.total)

    def test_parse_unittest_summary(self) -> None:
        counts = parse_test_counts(
            "Ran 8 tests in 0.100s\n\nFAILED (failures=1, errors=2, skipped=1)"
        )
        self.assertEqual(4, counts.passed)
        self.assertEqual(1, counts.failed)
        self.assertEqual(2, counts.errors)
        self.assertEqual(1, counts.skipped)
        self.assertEqual(8, counts.total)


class VerificationRunnerTest(unittest.TestCase):
    def test_timeout_has_separate_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = FakeSandbox(
                [ExecutionResult(exit_code=None, output="still running", timed_out=True)]
            )
            report = VerificationRunner(sandbox).run(
                ["pytest", "-q"], Path(directory), phase=TestPhase.FINAL
            )
        self.assertFalse(report.passed)
        self.assertTrue(report.timed_out)
        self.assertEqual("TEST_TIMEOUT", report.error_code)

    def test_sandbox_failure_has_separate_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = FakeSandbox([])
            report = VerificationRunner(sandbox).run(
                ["pytest", "-q"], Path(directory), phase=TestPhase.BASELINE
            )
        self.assertFalse(report.passed)
        self.assertEqual("TEST_ENVIRONMENT_FAILED", report.error_code)


if __name__ == "__main__":
    unittest.main()
