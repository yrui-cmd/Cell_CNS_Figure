#!/usr/bin/env python3
"""SQLite persistence for resumable Xiaomiao journal-figure jobs."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator

from credential_manager import default_data_dir


ACTIVE_STATES = {
    "submitted", "received", "queued", "awaiting_result", "processing",
    "completed", "complete", "succeeded", "success",
}
RECOVERABLE_STATES = ACTIVE_STATES | {"download_pending"}


class JobStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_data_dir()).resolve()
        self.db_path = self.root / "journal_jobs.sqlite"
        self.root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    task_hash TEXT NOT NULL,
                    brief_hash TEXT NOT NULL,
                    reference_hashes TEXT NOT NULL,
                    status TEXT NOT NULL,
                    submitted_at INTEGER NOT NULL,
                    last_checked_at INTEGER,
                    result_path TEXT,
                    result_url TEXT,
                    downloaded INTEGER NOT NULL DEFAULT 0,
                    charged INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    resubmit_count INTEGER NOT NULL DEFAULT 0,
                    credits_left TEXT,
                    credits_used TEXT,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_task_hash ON jobs(task_hash, submitted_at DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        try:
            value["reference_hashes"] = json.loads(value.get("reference_hashes") or "[]")
        except json.JSONDecodeError:
            value["reference_hashes"] = []
        value["downloaded"] = bool(value.get("downloaded"))
        value["charged"] = bool(value.get("charged"))
        return value

    def put(self, job: dict[str, Any]) -> None:
        fields = {
            "job_id": str(job["job_id"]),
            "task_hash": str(job["task_hash"]),
            "brief_hash": str(job["brief_hash"]),
            "reference_hashes": json.dumps(job.get("reference_hashes", []), separators=(",", ":")),
            "status": str(job.get("status", "submitted")).lower(),
            "submitted_at": int(job.get("submitted_at") or time.time()),
            "last_checked_at": job.get("last_checked_at"),
            "result_path": job.get("result_path"),
            "result_url": job.get("result_url"),
            "downloaded": int(bool(job.get("downloaded"))),
            "charged": int(bool(job.get("charged"))),
            "retry_count": int(job.get("retry_count", 0)),
            "resubmit_count": int(job.get("resubmit_count", 0)),
            "credits_left": None if job.get("credits_left") is None else str(job.get("credits_left")),
            "credits_used": None if job.get("credits_used") is None else str(job.get("credits_used")),
            "last_error": job.get("last_error"),
        }
        columns = ",".join(fields)
        placeholders = ",".join("?" for _ in fields)
        updates = ",".join(f"{name}=excluded.{name}" for name in fields if name != "job_id")
        with self.connection() as conn:
            conn.execute(
                f"INSERT INTO jobs ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(job_id) DO UPDATE SET {updates}",
                tuple(fields.values()),
            )

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._row(row)

    def update(self, job_id: str, **values: Any) -> dict[str, Any]:
        allowed = {
            "status", "last_checked_at", "result_path", "result_url", "downloaded", "charged",
            "retry_count", "resubmit_count", "credits_left", "credits_used", "last_error",
        }
        clean = {key: value for key, value in values.items() if key in allowed}
        if not clean:
            current = self.get(job_id)
            if not current:
                raise KeyError(job_id)
            return current
        clean = {
            key: int(bool(value)) if key in {"downloaded", "charged"} else value
            for key, value in clean.items()
        }
        sql = ",".join(f"{key}=?" for key in clean)
        with self.connection() as conn:
            conn.execute(f"UPDATE jobs SET {sql} WHERE job_id=?", (*clean.values(), job_id))
        current = self.get(job_id)
        if not current:
            raise KeyError(job_id)
        return current

    def find_reusable(self, task_hash: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE task_hash=? ORDER BY submitted_at DESC", (task_hash,)
            ).fetchall()
        for row in rows:
            item = self._row(row)
            if not item:
                continue
            state = str(item["status"]).lower()
            if item["downloaded"] and item.get("result_path") and Path(item["result_path"]).is_file():
                return item
            if state in ACTIVE_STATES:
                return item
        return None

    def recoverable(self, job_id: str | None = None) -> list[dict[str, Any]]:
        with self.connection() as conn:
            if job_id:
                rows = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchall()
            else:
                placeholders = ",".join("?" for _ in RECOVERABLE_STATES)
                rows = conn.execute(
                    f"SELECT * FROM jobs WHERE downloaded=0 AND status IN ({placeholders}) "
                    "ORDER BY submitted_at",
                    tuple(sorted(RECOVERABLE_STATES)),
                ).fetchall()
        return [item for row in rows if (item := self._row(row)) is not None]
