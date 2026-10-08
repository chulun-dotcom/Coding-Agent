"""Local HTTP API. Install the 'api' extra to enable it."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .model import ChatCompletionsModel
from .service import PROFILES, TaskService

DATA_ROOT = Path(os.getenv("SHI_AGENT_DATA", "data"))
WEB_ROOT = Path(__file__).resolve().parent / "web"
service = TaskService(DATA_ROOT)
app = FastAPI(title="SHI Agent", version="0.1.0")
app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")


class CreateTask(BaseModel):
    repo: Path
    task: str = Field(min_length=1)
    profile: str = "python"
    max_steps: int = Field(default=40, ge=1, le=500)
    base_commit: str = "HEAD"
    acceptance: Path | None = None


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html", media_type="text/html")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/tasks", status_code=202)
def create_task(body: CreateTask) -> dict:
    if body.profile not in PROFILES:
        raise HTTPException(422, "unknown profile")
    if not body.repo.resolve().is_dir():
        raise HTTPException(422, "repository directory does not exist")
    try:
        ChatCompletionsModel.from_env()
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
    task_id = uuid.uuid4().hex[:12]
    service.db.create(task_id, str(body.repo.resolve()), "", body.task, "pending")
    service.db.update(task_id, "queued")

    def run() -> None:
        try:
            service.run(
                body.repo,
                body.task,
                profile=body.profile,
                task_id=task_id,
                max_steps=body.max_steps,
                base_commit=body.base_commit,
                acceptance=body.acceptance,
            )
        except Exception as exc:
            if (service.db.get(task_id) or {}).get("status") in {"queued", "preparing", "running"}:
                service.db.update(task_id, "failed", f"{type(exc).__name__}: {exc}")

    threading.Thread(target=run, daemon=True).start()
    return {"task_id": task_id, "status": "queued"}


@app.get("/tasks")
def list_tasks() -> list[dict]:
    return service.db.list()


@app.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    task = service.db.get(task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    return task


@app.get("/tasks/{task_id}/events")
def get_events(task_id: str, last_event_id: str | None = Header(default=None)) -> StreamingResponse:
    if service.db.get(task_id) is None:
        raise HTTPException(404, "task not found")
    events = DATA_ROOT / "tasks" / task_id / "events.jsonl"

    try:
        after = max(0, int(last_event_id or 0))
    except ValueError as exc:
        raise HTTPException(400, "invalid Last-Event-ID") from exc

    def stream():
        cursor = after
        while True:
            if events.exists():
                for line in events.read_text(encoding="utf-8").splitlines():
                    item = json.loads(line)
                    if item["seq"] > cursor:
                        cursor = item["seq"]
                        yield f"id: {cursor}\ndata: {line}\n\n"
            state = (service.db.get(task_id) or {}).get("status")
            if state not in {"queued", "preparing", "running", "cancel_requested"}:
                break
            time.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> dict:
    try:
        service.cancel(task_id)
    except KeyError as exc:
        raise HTTPException(404, "task not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"task_id": task_id, "status": service.status(task_id)["status"]}


@app.post("/tasks/{task_id}/resume", status_code=202)
def resume_task(task_id: str) -> dict:
    try:
        status = service.status(task_id)["status"]
    except KeyError as exc:
        raise HTTPException(404, "task not found") from exc
    if status != "interrupted":
        raise HTTPException(409, "only interrupted tasks can be resumed")

    def run() -> None:
        try:
            service.resume(task_id)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()
    return {"task_id": task_id, "status": "resuming"}


@app.get("/tasks/{task_id}/artifacts/{name}")
def get_artifact(task_id: str, name: str) -> FileResponse:
    if name not in {"report.json", "summary.md", "patch.diff"}:
        raise HTTPException(404, "artifact not found")
    path = DATA_ROOT / "tasks" / task_id / "artifacts" / name
    if not path.is_file():
        raise HTTPException(404, "artifact not found")
    return FileResponse(path)


@app.get("/tasks/{task_id}/artifacts")
def list_artifacts(task_id: str) -> dict:
    if service.db.get(task_id) is None:
        raise HTTPException(404, "task not found")
    root = DATA_ROOT / "tasks" / task_id / "artifacts"
    names = [
        name for name in ("report.json", "summary.md", "patch.diff") if (root / name).is_file()
    ]
    return {"task_id": task_id, "artifacts": names}
