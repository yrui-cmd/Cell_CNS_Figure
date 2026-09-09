#!/usr/bin/env python3
"""Offline regressions for foreground completion and real failure boundaries."""

from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests

import xiaomiao_client as module
from xiaomiao_client import (
    AuthenticationError, ClientError, Expired, FAILURE, QuotaError,
    TemporaryServiceError, XiaomiaoClient, build_parser,
)


class WaitCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = XiaomiaoClient(root=Path(self.temp.name))
        self.addCleanup(self.client.session.close)
        self.job_id = "offline_wait"
        self.client.store.put({
            "job_id": self.job_id, "task_hash": self.job_id, "brief_hash": "offline",
            "status": "processing",
        })

    def test_terminal_states_raise_without_downloading_or_resubmitting(self):
        for state in FAILURE:
            with self.subTest(state=state):
                self.client.store.update(self.job_id, status="processing")
                with patch.object(self.client, "status", return_value={"status": state}), \
                     patch.object(self.client, "download") as download, \
                     patch.object(self.client, "submit") as submit:
                    with self.assertRaisesRegex(ClientError, state):
                        self.client.process(self.job_id, interval=0.1)
                    download.assert_not_called()
                    submit.assert_not_called()

    def test_authentication_quota_expiry_and_other_errors_do_not_loop(self):
        for error in (AuthenticationError, QuotaError, Expired, ClientError):
            for phase in ("status", "download"):
                with self.subTest(error=error.__name__, phase=phase):
                    self.client.store.update(
                        self.job_id, status="completed" if phase == "download" else "processing",
                    )
                    with patch.object(self.client, phase, side_effect=error("offline failure")) as action, \
                         patch.object(module.time, "sleep") as sleep:
                        with self.assertRaises(error):
                            self.client.process(self.job_id)
                        action.assert_called_once_with(self.job_id)
                        sleep.assert_not_called()

    def test_download_flag_without_complete_png_is_not_completion(self):
        for contents in (None, b"not a PNG", b"\x89PNG\r\n\x1a\ntruncated"):
            with self.subTest(contents=contents):
                result_path = Path(self.temp.name) / "missing-or-broken.png"
                if contents is not None:
                    result_path.write_bytes(contents)
                self.client.store.update(
                    self.job_id, status="completed", downloaded=True, result_path=str(result_path),
                )
                with patch.object(self.client, "download", return_value={
                    "status": "completed", "downloaded": True, "result_path": str(result_path),
                }) as download:
                    with self.assertRaisesRegex(ClientError, "等待超时"):
                        self.client.process(self.job_id, timeout=0)
                    download.assert_called_once_with(self.job_id)
                    self.assertFalse(self.client.store.get(self.job_id)["downloaded"])

    def test_connection_failure_after_request_retries_is_identifiably_transient(self):
        with patch.object(module, "RETRY_DELAYS", ()), \
             patch.object(self.client.session, "request", side_effect=requests.ConnectionError):
            with self.assertRaises(TemporaryServiceError):
                self.client._request("GET", "http://127.0.0.1/offline")

    def test_explicit_timeout_is_failure_with_original_job_preserved(self):
        with patch.object(self.client, "status", side_effect=TemporaryServiceError("offline temporary")):
            with self.assertRaisesRegex(ClientError, "等待超时"):
                self.client.process(self.job_id, timeout=0)
        stored = self.client.store.get(self.job_id)
        self.assertFalse(stored["downloaded"])
        self.assertEqual(stored["retry_count"], 1)
        self.assertEqual(stored["resubmit_count"], 0)

    def test_cli_defaults_and_explicit_background(self):
        parser = build_parser()
        for argv in (["run", "--text", "offline"], ["resume", self.job_id]):
            with self.subTest(argv=argv):
                self.assertFalse(parser.parse_args(argv).background)
                self.assertTrue(parser.parse_args([*argv, "--wait"]).wait)
                self.assertTrue(parser.parse_args([*argv, "--background"]).background)
                with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
                    parser.parse_args([*argv, "--wait", "--background"])

    def test_only_explicit_background_launches_worker(self):
        for command in ("run", "resume"):
            for background in (False, True):
                argv = ["client", command]
                argv += ["--text", "offline"] if command == "run" else [self.job_id]
                if background:
                    argv.append("--background")
                with self.subTest(command=command, background=background), \
                     patch.object(module, "XiaomiaoClient", return_value=self.client), \
                     patch.object(self.client, "submit", return_value=self.client.store.get(self.job_id)), \
                     patch.object(self.client, "process", return_value={"downloaded": True}) as process, \
                     patch.object(module, "start_worker_detached") as worker, \
                     patch.object(module, "emit"), patch("sys.argv", argv):
                    self.assertEqual(module.main(), 0)
                    if background:
                        worker.assert_called_once()
                        process.assert_not_called()
                    else:
                        process.assert_called_once()
                        worker.assert_not_called()

    def test_cli_terminal_failure_returns_nonzero(self):
        with patch.object(module, "XiaomiaoClient", return_value=self.client), \
             patch.object(self.client, "status", return_value={"status": "failed"}), \
             patch("sys.argv", ["client", "resume", self.job_id]), \
             patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(module.main(), 2)
            self.assertIn("failed", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
