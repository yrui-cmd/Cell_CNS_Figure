#!/usr/bin/env python3
"""Zero-LLM background worker for persisted Xiaomiao journal-figure jobs."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import time

from credential_manager import default_data_dir, redact_text
from xiaomiao_client import ClientError, FAILURE, POLL_INTERVAL, SUCCESS, XiaomiaoClient


class JournalWorker:
    """Recover and poll persisted jobs without involving an LLM."""

    def __init__(self, client: XiaomiaoClient | None = None, *, interval: float = POLL_INTERVAL) -> None:
        self.client = client or XiaomiaoClient()
        self.interval = max(float(interval), 1.0)

    def process_once(self, job_id: str) -> None:
        current = self.client.store.get(job_id)
        if not current or current.get("downloaded"):
            return
        state = str(current.get("status", "")).lower()
        try:
            if state in SUCCESS:
                self.client.download(job_id)
                return
            if state in FAILURE:
                return
            current = self.client.status(job_id)
            if str(current.get("status", "")).lower() in SUCCESS:
                self.client.download(job_id)
        except ClientError as exc:
            current = self.client.store.get(job_id)
            if current:
                self.client.store.update(
                    job_id,
                    retry_count=int(current.get("retry_count", 0)) + 1,
                    last_error=redact_text(str(exc), [])[:500],
                    last_checked_at=int(time.time()),
                )

    def run_forever(self) -> None:
        root = self.client.root
        root.mkdir(parents=True, exist_ok=True)
        pid_file = root / "worker.pid"
        pid_file.write_text(str(os.getpid()), encoding="ascii")
        try:
            while True:
                for job in self.client.store.recoverable():
                    self.process_once(str(job["job_id"]))
                time.sleep(self.interval)
        finally:
            try:
                if pid_file.read_text(encoding="ascii").strip() == str(os.getpid()):
                    pid_file.unlink()
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="cell_figure 后台任务恢复器")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--job-id")
    parser.add_argument("--interval", type=float, default=POLL_INTERVAL)
    args = parser.parse_args()
    client = XiaomiaoClient()
    if args.job_id:
        client.process(args.job_id, interval=args.interval)
        return 0
    JournalWorker(client, interval=args.interval).run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
