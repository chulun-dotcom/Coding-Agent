"""Small inspection CLI for the M0 implementation."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from .service import TaskService
from .tools import build_default_registry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shi-agent", description="SHI Agent local coding agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("tools", help="print the built-in tool schemas")
    subparsers.add_parser("doctor", help="check local commands required by SHI Agent")
    run = subparsers.add_parser("run", help="run one coding task in an isolated copy")
    run.add_argument("--repo", required=True, type=Path)
    run.add_argument("--task", required=True)
    run.add_argument("--profile", choices=["python", "java"], default="python")
    run.add_argument("--data", type=Path, default=Path("data"))
    run.add_argument("--max-steps", type=int, default=40)
    run.add_argument("--base-commit", default="HEAD")
    run.add_argument("--acceptance", type=Path, help="external hidden pytest directory")
    status = subparsers.add_parser("status", help="read a saved task")
    status.add_argument("task_id")
    status.add_argument("--data", type=Path, default=Path("data"))
    resume = subparsers.add_parser(
        "resume", help="resume an interrupted task from its last stable checkpoint"
    )
    resume.add_argument("task_id")
    resume.add_argument("--data", type=Path, default=Path("data"))
    cancel = subparsers.add_parser("cancel", help="request cancellation between agent steps")
    cancel.add_argument("task_id")
    cancel.add_argument("--data", type=Path, default=Path("data"))
    export = subparsers.add_parser("export", help="copy saved artifacts")
    export.add_argument("task_id")
    export.add_argument("--data", type=Path, default=Path("data"))
    export.add_argument("--output", type=Path, required=True)
    evaluate_parser = subparsers.add_parser("evaluate", help="run a JSONL task manifest")
    evaluate_parser.add_argument("--manifest", type=Path, required=True)
    evaluate_parser.add_argument("--data", type=Path, default=Path("data"))
    serve = subparsers.add_parser("serve", help="start the local HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "tools":
        print(json.dumps(build_default_registry().schemas(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "doctor":
        checks = {}
        for command in (
            ["git", "--version"],
            ["docker", "version", "--format", "{{.Server.Version}}"],
        ):
            name = command[0]
            try:
                result = subprocess.run(
                    command, capture_output=True, text=True, timeout=10, check=False
                )
                checks[name] = {
                    "ok": result.returncode == 0,
                    "output": (result.stdout or result.stderr).strip(),
                }
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                checks[name] = {"ok": False, "output": str(exc)}
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        return 0 if all(item["ok"] for item in checks.values()) else 1
    if args.command == "run":
        report = TaskService(args.data).run(
            args.repo,
            args.task,
            profile=args.profile,
            max_steps=args.max_steps,
            base_commit=args.base_commit,
            acceptance=args.acceptance,
        )
        print(report.model_dump_json(indent=2))
        return 0 if report.status.value == "succeeded" else 1
    if args.command == "status":
        print(json.dumps(TaskService(args.data).status(args.task_id), ensure_ascii=False, indent=2))
        return 0
    if args.command == "resume":
        report = TaskService(args.data).resume(args.task_id)
        print(report.model_dump_json(indent=2))
        return 0 if report.status.value == "succeeded" else 1
    if args.command == "cancel":
        service = TaskService(args.data)
        service.cancel(args.task_id)
        print(json.dumps(service.status(args.task_id), ensure_ascii=False, indent=2))
        return 0
    if args.command == "export":
        source = args.data.resolve() / "tasks" / args.task_id / "artifacts"
        if not source.is_dir():
            parser = build_parser()
            parser.error("task artifacts not found")
        args.output.mkdir(parents=True, exist_ok=True)
        for name in ("report.json", "summary.md", "patch.diff"):
            if (source / name).is_file():
                shutil.copy2(source / name, args.output / name)
        return 0
    if args.command == "evaluate":
        from .evaluation import evaluate

        print(json.dumps(evaluate(args.manifest, args.data), ensure_ascii=False, indent=2))
        return 0
    if args.command == "serve":
        import uvicorn

        uvicorn.run("repoagent.api:app", host=args.host, port=args.port)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
