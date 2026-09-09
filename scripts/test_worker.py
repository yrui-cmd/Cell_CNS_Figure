#!/usr/bin/env python3
"""Isolated, offline regression tests for worker liveness and recovery."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from job_store import JobStore
from journal_worker import JournalWorker
from xiaomiao_client import ClientError, SUCCESS, process_is_running, start_worker_detached


class WorkerTests(unittest.TestCase):
    def test_repeated_start_probe_keeps_existing_process_alive(self):
        """Exercise the actual launcher branch without starting an API worker."""
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        child = subprocess.Popen(
            [sys.executable, "-u", "-c", "import sys\nfor line in sys.stdin:\n print(line.strip(), flush=True)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            **kwargs,
        )
        try:
            with tempfile.TemporaryDirectory() as temp_name:
                root = Path(temp_name)
                (root / "worker.pid").write_text(str(child.pid), encoding="ascii")
                with patch.dict(os.environ, {"XIAOMIAO_CLIENT_DATA_DIR": str(root)}):
                    for index in range(3):
                        self.assertTrue(process_is_running(child.pid))
                        self.assertEqual(start_worker_detached(), child.pid)
                        child.stdin.write(f"probe-{index}\n")
                        child.stdin.flush()
                        self.assertEqual(child.stdout.readline().strip(), f"probe-{index}")
                        self.assertIsNone(child.poll())
        finally:
            child.stdin.close()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=5)
            child.stdout.close()
        self.assertFalse(process_is_running(child.pid))
        self.assertFalse(process_is_running(0))

    def test_success_aliases_resume_download_after_transient_failure(self):
        with tempfile.TemporaryDirectory() as temp_name:
            store = JobStore(Path(temp_name))

            class OfflineClient:
                def __init__(self):
                    self.store = store
                    self.attempts = {}

                def download(self, job_id):
                    self.attempts[job_id] = self.attempts.get(job_id, 0) + 1
                    if self.attempts[job_id] == 1:
                        raise ClientError("Temporary local test download failure")
                    return self.store.update(job_id, downloaded=True, status="completed")

                def status(self, _job_id):
                    raise AssertionError("Completed jobs must retry download directly")

            client = OfflineClient()
            worker = JournalWorker(client)
            for state in SUCCESS:
                job_id = f"offline_{state}"
                store.put({
                    "job_id": job_id, "task_hash": job_id, "brief_hash": "offline",
                    "status": state,
                })
                self.assertEqual(store.find_reusable(job_id)["job_id"], job_id)
                worker.process_once(job_id)
                self.assertFalse(store.get(job_id)["downloaded"])

            self.assertEqual(len(store.recoverable()), len(SUCCESS))
            for job in store.recoverable():
                worker.process_once(job["job_id"])
                self.assertTrue(store.get(job["job_id"])["downloaded"])
                self.assertEqual(client.attempts[job["job_id"]], 2)
            self.assertEqual(store.recoverable(), [])


if __name__ == "__main__":
    unittest.main()
