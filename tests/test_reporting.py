from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from repoagent.contracts import (
    RunReport,
    RunStatus,
    TestCounts,
    TestPhase,
    VerificationReport,
)
from repoagent.reporting import ResultExporter


class ResultExporterTest(unittest.TestCase):
    def test_export_writes_report_summary_and_patch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            subprocess.run(
                ["git", "-C", str(workspace), "config", "user.email", "test@example.com"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(workspace), "config", "user.name", "SHI Agent Test"],
                check=True,
            )
            file = workspace / "app.py"
            file.write_text("value = 1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(workspace), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(workspace), "commit", "-q", "-m", "initial"],
                check=True,
            )
            file.write_text("value = 2\n", encoding="utf-8")

            baseline = VerificationReport(
                phase=TestPhase.BASELINE,
                command=["pytest", "-q"],
                passed=True,
                exit_code=0,
                counts=TestCounts(passed=8, total=8),
            )
            final = VerificationReport(
                phase=TestPhase.FINAL,
                command=["pytest", "-q"],
                passed=True,
                exit_code=0,
                counts=TestCounts(passed=9, total=9),
            )
            report = RunReport(
                task_id="export-demo",
                status=RunStatus.SUCCEEDED,
                summary="分页边界已经修复。",
                steps=4,
                events=[],
                baseline=baseline,
                verification=final,
            )

            output_dir = root / "artifacts"
            ResultExporter().export(output_dir, workspace, report)

            saved = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
            self.assertEqual("succeeded", saved["status"])
            self.assertEqual(8, saved["baseline"]["counts"]["passed"])
            self.assertEqual(9, saved["verification"]["counts"]["passed"])
            self.assertIn("value = 2", (output_dir / "patch.diff").read_text(encoding="utf-8"))
            summary = (output_dir / "summary.md").read_text(encoding="utf-8")
            self.assertIn("基线测试：通过", summary)
            self.assertIn("最终测试：通过", summary)


if __name__ == "__main__":
    unittest.main()
