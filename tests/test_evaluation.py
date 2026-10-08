from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from repoagent.contracts import ModelAction
from repoagent.evaluation import evaluate
from repoagent.model import ScriptedModel
from repoagent.sandbox import ExecutionResult, FakeSandbox


class EvaluationTest(unittest.TestCase):
    def test_batch_records_success_and_environment_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "value.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(source),
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-q",
                    "-m",
                    "initial",
                ],
                check=True,
            )
            commit = subprocess.run(
                ["git", "-C", str(source), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            manifest = root / "cases.jsonl"
            cases = [
                {
                    "name": "fix",
                    "repo": "source",
                    "base_commit": commit,
                    "instruction": "Set VALUE to 2",
                },
                {"name": "missing", "repo": "missing", "instruction": "Missing repository"},
            ]
            manifest.write_text(
                "".join(json.dumps(case) + "\n" for case in cases), encoding="utf-8"
            )
            patch = "diff --git a/value.py b/value.py\n--- a/value.py\n+++ b/value.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n"
            summary = evaluate(
                manifest,
                root / "data",
                sandbox=FakeSandbox(
                    [ExecutionResult(0, "1 passed"), ExecutionResult(0, "1 passed")]
                ),
                model_factory=lambda _: ScriptedModel(
                    [
                        ModelAction.call("patch", "apply_patch", patch=patch),
                        ModelAction.done("Fixed"),
                    ]
                ),
            )
            self.assertEqual(2, summary["total"])
            self.assertEqual(1, summary["succeeded"])
            self.assertEqual("environment_error", summary["results"][1]["status"])
            self.assertTrue((root / "data" / "evaluations" / "latest.json").is_file())
