"""Export the result of one Agent task."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from .contracts import RunReport, VerificationReport
from .tools.workspace import ignored_path


class ResultExporter:
    def export(self, output_dir: Path, workspace: Path, report: RunReport) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "report.json"
        report_path.write_text(
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        untracked = subprocess.run(
            ["git", "-C", str(workspace), "ls-files", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if untracked.returncode != 0:
            raise RuntimeError("failed to list new files")
        new_files = [
            name.decode("utf-8")
            for name in untracked.stdout.split(b"\0")
            if name and not ignored_path(Path(name.decode("utf-8"))) and not name.endswith(b".pyc")
        ]
        if new_files:
            intent = subprocess.run(
                ["git", "-C", str(workspace), "add", "-N", "--", *new_files],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if intent.returncode != 0:
                raise RuntimeError("failed to include new files in patch")
        diff = subprocess.run(
            ["git", "-C", str(workspace), "diff", "--no-ext-diff", "--binary", "HEAD"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if diff.returncode != 0:
            raise RuntimeError(
                f"failed to export patch: {diff.stderr.decode('utf-8', 'replace').strip()}"
            )
        if report.status.value == "succeeded":
            with tempfile.TemporaryDirectory(prefix="shi-agent-patch-") as directory:
                clean = Path(directory) / "clean"
                cloned = subprocess.run(
                    ["git", "clone", "--quiet", "--no-local", str(workspace), str(clean)],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                if cloned.returncode != 0:
                    raise RuntimeError(f"cannot verify exported patch: {cloned.stderr.strip()}")
                checked = subprocess.run(
                    ["git", "-C", str(clean), "apply", "--check", "-"],
                    input=diff.stdout,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                if checked.returncode != 0:
                    raise RuntimeError(
                        f"exported patch does not apply to base commit: {checked.stderr.decode('utf-8', 'replace').strip()}"
                    )
        (output_dir / "patch.diff").write_bytes(diff.stdout)
        (output_dir / "summary.md").write_text(self._summary(report), encoding="utf-8")
        return report_path

    @staticmethod
    def _test_line(name: str, result: VerificationReport | None) -> str:
        if result is None:
            return f"- {name}：未执行"
        counts = result.counts
        status = "通过" if result.passed else "失败"
        return (
            f"- {name}：{status}，"
            f"通过 {counts.passed}，失败 {counts.failed}，"
            f"错误 {counts.errors}，跳过 {counts.skipped}"
        )

    def _summary(self, report: RunReport) -> str:
        lines = [
            f"# 任务 {report.task_id}",
            "",
            f"- 状态：{report.status.value}",
            f"- Agent 步数：{report.steps}",
            self._test_line("基线测试", report.baseline),
            self._test_line("最终测试", report.verification),
            self._test_line("独立验收", report.acceptance),
            "",
            "## 结果说明",
            "",
            report.summary,
            "",
        ]
        return "\n".join(lines)
