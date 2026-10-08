from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from repoagent.contracts import ToolCall
from repoagent.sandbox import ExecutionResult, FakeSandbox
from repoagent.tools import build_default_registry


class RunTestsToolTest(unittest.TestCase):
    def test_failed_tests_are_valid_tool_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = FakeSandbox([ExecutionResult(exit_code=1, output="1 failed, 8 passed")])
            registry = build_default_registry(sandbox, ["pytest", "-q"])
            result = registry.execute(
                ToolCall(call_id="test-1", name="run_tests", arguments={}),
                Path(directory),
            )

        self.assertTrue(result.ok)
        self.assertEqual(1, result.exit_code)
        self.assertIn("1 failed", result.output_excerpt)
        self.assertEqual(["pytest", "-q"], sandbox.calls[0][0])


if __name__ == "__main__":
    unittest.main()
