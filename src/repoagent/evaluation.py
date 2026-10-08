"""Run a fixed set of tasks and record both successes and failures."""

from __future__ import annotations

import json
from pathlib import Path
from time import monotonic
from typing import Callable

from .model import ModelGateway
from .sandbox import Sandbox
from .service import TaskService


def evaluate(
    manifest: Path,
    data_root: Path,
    *,
    model_factory: Callable[[dict], ModelGateway] | None = None,
    sandbox: Sandbox | None = None,
) -> dict:
    service = TaskService(data_root, sandbox=sandbox)
    results = []
    for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        case = json.loads(line)
        started = monotonic()
        try:
            repo = Path(case["repo"])
            if not repo.is_absolute():
                repo = (manifest.parent / repo).resolve()
            report = service.run(
                repo,
                case["instruction"],
                model=model_factory(case) if model_factory else None,
                profile=case.get("profile", "python"),
                max_steps=int(case.get("max_steps", 40)),
                base_commit=case.get("base_commit", "HEAD"),
                acceptance=(manifest.parent / case["acceptance"]).resolve()
                if case.get("acceptance")
                else None,
            )
            results.append(
                {
                    "case": number,
                    "name": case.get("name", f"case-{number}"),
                    "task_id": report.task_id,
                    "status": report.status.value,
                    "steps": report.steps,
                    "input_tokens": report.input_tokens,
                    "output_tokens": report.output_tokens,
                    "failure_reason": report.failure_reason,
                    "duration_seconds": round(monotonic() - started, 3),
                    "baseline_passed": report.baseline.passed if report.baseline else None,
                    "verification_passed": report.verification.passed
                    if report.verification
                    else None,
                    "acceptance_passed": report.acceptance.passed if report.acceptance else None,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "case": number,
                    "name": case.get("name", f"case-{number}"),
                    "status": "environment_error",
                    "failure_reason": f"{type(exc).__name__}: {exc}",
                    "duration_seconds": round(monotonic() - started, 3),
                }
            )
    passed = sum(item["status"] == "succeeded" for item in results)
    summary = {
        "total": len(results),
        "succeeded": passed,
        "failed": len(results) - passed,
        "completion_rate": passed / len(results) if results else 0,
        "results": results,
    }
    output = data_root / "evaluations"
    output.mkdir(parents=True, exist_ok=True)
    (output / "latest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
