"""SQLite task index. Events and artifacts stay in each task directory."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import psutil


class TaskDatabase:
    def __init__(self, data_root: Path) -> None:
        data_root.mkdir(parents=True, exist_ok=True)
        self.path = data_root / "tasks.sqlite3"
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    source_repo TEXT NOT NULL,
                    base_commit TEXT NOT NULL,
                    instruction TEXT NOT NULL,
                    model TEXT NOT NULL,
                    error TEXT,
                    owner_pid INTEGER
                )"""
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
            if "owner_pid" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN owner_pid INTEGER")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def create(
        self, task_id: str, source_repo: str, base_commit: str, instruction: str, model: str
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute(
                "INSERT INTO tasks (task_id,status,created_at,updated_at,source_repo,base_commit,instruction,model,error,owner_pid) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    "preparing",
                    now,
                    now,
                    source_repo,
                    base_commit,
                    instruction,
                    model,
                    None,
                    os.getpid(),
                ),
            )

    def update(self, task_id: str, status: str, error: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute(
                "UPDATE tasks SET status=?, updated_at=?, error=? WHERE task_id=?",
                (status, now, error, task_id),
            )

    def prepared(self, task_id: str, source_repo: str, base_commit: str, model: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE tasks SET source_repo=?, base_commit=?, model=?, status='running', owner_pid=?, updated_at=? WHERE task_id=?",
                (
                    source_repo,
                    base_commit,
                    model,
                    os.getpid(),
                    datetime.now(timezone.utc).isoformat(),
                    task_id,
                ),
            )

    def claim_resume(self, task_id: str) -> bool:
        with self._connect() as db:
            result = db.execute(
                "UPDATE tasks SET status='running', owner_pid=?, updated_at=? WHERE task_id=? AND status='interrupted'",
                (os.getpid(), datetime.now(timezone.utc).isoformat(), task_id),
            )
            return result.rowcount == 1

    def recover_orphans(self) -> None:
        with self._connect() as db:
            rows = db.execute(
                "SELECT task_id,status,owner_pid FROM tasks WHERE status IN ('queued','running','cancel_requested')"
            ).fetchall()
            for row in rows:
                if row["owner_pid"] is None or not psutil.pid_exists(row["owner_pid"]):
                    state = "failed" if row["status"] == "queued" else "interrupted"
                    db.execute(
                        "UPDATE tasks SET status=?, error='worker process exited', updated_at=? WHERE task_id=?",
                        (state, datetime.now(timezone.utc).isoformat(), row["task_id"]),
                    )

    def get(self, task_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def list(self, limit: int = 100) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]
