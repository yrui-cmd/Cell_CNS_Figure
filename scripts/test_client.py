#!/usr/bin/env python3
"""Production-oriented mock integration tests for cell_figure."""

from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import zipfile


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
KEY = "img_live_test_secret_1234"


def minimal_pptx() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<p:presentation/>")
    return output.getvalue()


PPTX = minimal_pptx()


class Handler(BaseHTTPRequestHandler):
    post_count = 0
    status_count = 0
    result_count = 0
    pptx_count = 0
    balance = 39
    status_errors_left = 3
    result_errors_left = 3
    result_not_ready_left = 2

    def log_message(self, *_args):
        return

    def send_json(self, value, status=200):
        raw = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            self.send_json({"ok": False, "error": "unauthorized"}, 401)
            return False
        return True

    def do_POST(self):
        if not self.authorized():
            return
        if self.path != "/api/journal-figure-jobs":
            return self.send_json({"error": "not found"}, 404)
        if not self.headers.get("Content-Type", "").startswith("multipart/form-data;"):
            return self.send_json({"error": "multipart required"}, 400)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if b'name="brief"' not in body:
            return self.send_json({"error": "brief missing"}, 400)
        Handler.post_count += 1
        self.send_json({
            "ok": True,
            "job_id": "jfig_test_001",
            "status": "received",
            "reserved_credits": 3,
            "credits_left": 36,
        }, 202)

    def do_GET(self):
        if not self.authorized():
            return
        if self.path == "/api/balance":
            return self.send_json({
                "ok": True,
                "available_credits": Handler.balance,
                "credits_used": 4,
                "services": {"journal_figure": {"enabled": True}},
                "checked_at": "2026-09-10T00:00:00Z",
            })
        if self.path == "/api/journal-figure-jobs/jfig_test_001":
            if Handler.status_errors_left:
                Handler.status_errors_left -= 1
                return self.send_json({"error": "temporary"}, 503)
            Handler.status_count += 1
            state = "processing" if Handler.status_count <= 3 else "completed"
            return self.send_json({"job_id": "jfig_test_001", "status": state})
        if self.path == "/api/journal-figure-jobs/jfig_test_001/result":
            Handler.result_count += 1
            if Handler.result_not_ready_left:
                Handler.result_not_ready_left -= 1
                return self.send_json({"error": "not ready"}, 409)
            if Handler.result_errors_left:
                Handler.result_errors_left -= 1
                return self.send_json({"error": "temporary"}, 503)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG)))
            self.send_header("X-Credits-Spent", "3")
            self.end_headers()
            return self.wfile.write(PNG)
        self.send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        if not self.authorized():
            return
        self.send_json({"job_id": "jfig_test_001", "status": "cancelled"})

    def do_PUT(self):
        if not self.authorized():
            return
        if self.path != "/api/journal-figure-jobs/jfig_test_001/pptx":
            return self.send_json({"error": "not found"}, 404)
        Handler.pptx_count += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.presentationml.presentation")
        self.send_header("Content-Length", str(len(PPTX)))
        self.end_headers()
        self.wfile.write(PPTX)


def call(script: Path, env: dict[str, str], *args: str, expect: int = 0, stdin: str | None = None):
    result = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", str(script), *args],
        input=stdin,
        text=True,
        encoding="utf-8",
        capture_output=True,
        env=env,
        check=False,
        timeout=15,
    )
    assert result.returncode == expect, (result.stdout, result.stderr)
    combined = result.stdout + result.stderr
    assert KEY not in combined, "API key leaked to process output"
    stream = result.stdout if expect == 0 else result.stderr
    return json.loads(stream)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    script = Path(__file__).with_name("xiaomiao_client.py")
    try:
        with tempfile.TemporaryDirectory() as temp_name:
            temp = Path(temp_name)
            desktop = temp / "Desktop"
            desktop.mkdir()
            env = dict(os.environ)
            env.update({
                "XIAOMIAO_CLIENT_DATA_DIR": str(temp / "data"),
                "XIAOMIAO_DESKTOP_DIR": str(desktop),
                "XIAOMIAO_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "XIAOMIAO_API_KEY": KEY,
                "XIAOMIAO_RETRY_DELAYS": "0.01,0.01",
                "XIAOMIAO_POLL_INTERVAL": "1",
            })

            balance = call(script, env, "balance")
            assert balance["available_credits"] == 39
            assert balance["journal_available"] is True

            reference = temp / "reference.png"
            reference.write_bytes(PNG)
            result = call(
                script, env, "run", "--interval", "0.1", "--timeout", "5",
                "--text", "SLC7A11 disulfidptosis", "--reference", str(reference),
            )
            assert result["status"] == "completed"
            assert result["downloaded"] is True
            assert Path(result["result_path"]).read_bytes().startswith(PNG[:8])
            assert Handler.post_count == 1
            assert Handler.status_count == 6, "processing and completed/result-409 must keep polling"
            assert Handler.result_count == 6, "409 and exhausted 503 retries must not finish early"

            pptx = call(script, env, "fetch-pptx", "jfig_test_001")
            assert pptx["pptx_downloaded"] is True
            assert Path(pptx["pptx_path"]).read_bytes() == PPTX
            repeated_pptx = call(script, env, "fetch-pptx", "jfig_test_001")
            assert repeated_pptx["pptx_path"] == pptx["pptx_path"]
            assert Handler.pptx_count == 1, "cached PPTX must not call the server again"

            repeated = call(
                script, env, "run", "--wait", "--interval", "0.1", "--timeout", "5",
                "--text", "SLC7A11 disulfidptosis", "--reference", str(reference),
            )
            assert repeated["reused"] is True
            assert Handler.post_count == 1

            # Lost cached output must recover the same job instead of another paid POST.
            Path(result["result_path"]).unlink()
            recovered = call(
                script, env, "run", "--interval", "0.1", "--timeout", "5",
                "--text", "SLC7A11 disulfidptosis", "--reference", str(reference),
            )
            assert recovered["reused"] is True
            assert Path(recovered["result_path"]).read_bytes() == PNG
            assert Handler.post_count == 1

            # Resume without --wait also waits for repeated processing / result-409 states.
            Handler.status_count = 0
            Handler.result_count = 0
            Handler.result_not_ready_left = 2
            resume_env = {**env, "XIAOMIAO_CLIENT_DATA_DIR": str(temp / "resume-data")}
            resumed = call(
                script, resume_env, "resume", "jfig_test_001", "--interval", "0.1", "--timeout", "5",
            )
            assert resumed["downloaded"] is True
            assert Path(resumed["result_path"]).read_bytes() == PNG
            assert Handler.status_count == 6
            assert Handler.result_count == 3
            assert Handler.post_count == 1

            Handler.balance = 2
            low = call(script, env, "submit", "--text", "different brief", expect=2)
            assert "额度不足" in low["error"]
            assert Handler.post_count == 1

            too_many = []
            for index in range(7):
                item = temp / f"r{index}.png"
                item.write_bytes(PNG)
                too_many.extend(["--reference", str(item)])
            invalid = call(script, env, "submit", "--text", "new", *too_many, expect=2)
            assert "最多 6" in invalid["error"]

            (desktop / "Cell_skills.txt").write_text(f"小描={KEY}\n", encoding="utf-8")
            test_env = dict(env)
            test_env.pop("XIAOMIAO_API_KEY")
            test_env["XIAOMIAO_CLIENT_DATA_DIR"] = str(temp / "desktop-data")
            test_env["XIAOMIAO_DESKTOP_DIR"] = str(desktop)
            Handler.balance = 39
            discovered = call(script, test_env, "balance")
            assert discovered["available_credits"] == 39
            pure_text = call(script, test_env, "submit", "--text", "text only multipart test")
            assert pure_text["job_id"] == "jfig_test_001"
            assert Handler.post_count == 2

            db = temp / "data" / "journal_jobs.sqlite"
            assert db.is_file()
            raw_db = db.read_bytes()
            assert KEY.encode() not in raw_db

            print("cell_figure mock integration tests: PASS")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
