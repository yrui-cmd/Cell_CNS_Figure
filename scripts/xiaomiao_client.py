#!/usr/bin/env python3
"""Production client for the Xiaomiao asynchronous journal-figure API."""

from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image

from credential_manager import CredentialManager, default_data_dir, read_key_from_stdin, redact_text
from job_store import JobStore
from validate_input import InputBundle, InputError, validate


BASE_URL = os.environ.get("XIAOMIAO_BASE_URL", "https://xiaomiao-ai.com").rstrip("/")
ENDPOINTS = {
    "balance": "/api/balance",
    "submit": "/api/journal-figure-jobs",
    "status": "/api/journal-figure-jobs/{job_id}",
    "result": "/api/journal-figure-jobs/{job_id}/result",
    "cancel": "/api/journal-figure-jobs/{job_id}",
}
EXPECTED_COST = 3
POLL_INTERVAL = int(os.environ.get("XIAOMIAO_POLL_INTERVAL", "600"))
RETRY_DELAYS = tuple(float(x) for x in os.environ.get("XIAOMIAO_RETRY_DELAYS", "5,15,30,60,120").split(","))
SUCCESS = {"completed", "complete", "succeeded", "success"}
FAILURE = {"failed", "error", "cancelled", "canceled", "expired"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ClientError(RuntimeError):
    pass


class AuthenticationError(ClientError):
    pass


class QuotaError(ClientError):
    pass


class NotReady(ClientError):
    pass


class Expired(ClientError):
    pass


class XiaomiaoClient:
    def __init__(self, *, base_url: str = BASE_URL, root: Path | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.root = (root or default_data_dir()).resolve()
        self.credentials = CredentialManager(self.root)
        self.store = JobStore(self.root)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "cell_figure/1.0", "Accept": "application/json, image/png"})
        self._key: str | None = None

    def url(self, name: str, **values: str) -> str:
        return urljoin(self.base_url + "/", ENDPOINTS[name].format(**values).lstrip("/"))

    @staticmethod
    def _json(response: requests.Response, context: str) -> dict[str, Any]:
        try:
            value = response.json()
        except ValueError as exc:
            raise ClientError(f"{context}没有返回有效 JSON。") from exc
        if not isinstance(value, dict):
            raise ClientError(f"{context}返回格式无效。")
        return value

    @staticmethod
    def _first(data: dict[str, Any], *names: str) -> Any:
        for name in names:
            if name in data and data[name] is not None:
                return data[name]
        return None

    @staticmethod
    def _safe_message(response: requests.Response) -> str:
        message = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                message = str(XiaomiaoClient._first(body, "message", "detail", "error") or "")
        except ValueError:
            pass
        message = redact_text(message[:500], [])
        return re.sub(r"(?i)(authorization|bearer|api[_ -]?key)\s*[:=]?\s*\S+", r"\1 [REDACTED]", message)

    def _request(
        self,
        method: str,
        url: str,
        *,
        key: str | None = None,
        retry: bool = True,
        **kwargs: Any,
    ) -> requests.Response:
        headers = dict(kwargs.pop("headers", {}))
        if key and urlparse(url).netloc == urlparse(self.base_url).netloc:
            headers["Authorization"] = f"Bearer {key}"
        delays = RETRY_DELAYS if retry else ()
        attempts = len(delays) + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self.session.request(method, url, headers=headers, timeout=(15, 60), **kwargs)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt < len(delays):
                    time.sleep(delays[attempt])
                    continue
                raise ClientError("暂时无法连接小描服务。") from exc
            if response.status_code in {500, 503} and attempt < len(delays):
                time.sleep(delays[attempt])
                continue
            if 200 <= response.status_code < 300:
                return response
            message = self._safe_message(response)
            suffix = f"：{message}" if message else ""
            if response.status_code == 401:
                raise AuthenticationError("小描 API Key 无效。")
            if response.status_code == 402:
                raise QuotaError(f"当前额度不足{suffix}")
            if response.status_code == 409:
                raise NotReady("期刊图仍在处理中。")
            if response.status_code == 410:
                raise Expired("期刊图任务已过期。")
            if response.status_code in {400, 413, 415}:
                raise InputError(f"提交内容不符合接口要求{suffix}")
            if response.status_code == 403:
                raise ClientError(f"当前 API Key 没有期刊图权限{suffix}")
            raise ClientError(f"小描服务返回 HTTP {response.status_code}{suffix}")
        raise ClientError("小描服务暂时不可用。") from last_error

    @classmethod
    def _balance_fields(cls, data: dict[str, Any], headers: requests.structures.CaseInsensitiveDict | None = None) -> dict[str, Any]:
        headers = headers or requests.structures.CaseInsensitiveDict()
        available = cls._first(data, "available_credits", "credits_left", "remaining_credits", "balance")
        used = cls._first(data, "credits_used", "credits_spent", "charged_credits", "consumed")
        if available is None:
            available = headers.get("X-Credits-Left") or headers.get("X-Remaining-Credits")
        if used is None:
            used = headers.get("X-Credits-Spent") or headers.get("X-Charged-Credits")
        return {
            "available_credits": available,
            "credits_left": available,
            "credits_used": used,
            "services": data.get("services"),
            "checked_at": cls._first(data, "checked_at", "timestamp") or int(time.time()),
        }

    @staticmethod
    def _journal_available(services: Any) -> bool:
        if services is None:
            return True
        value: Any = None
        names = {"journal_figure", "journal-figure", "journal", "journal_figure_jobs"}
        if isinstance(services, dict):
            for name in names:
                if name in services:
                    value = services[name]
                    break
        elif isinstance(services, list):
            lowered = {str(item).lower() for item in services}
            return bool(names & lowered)
        else:
            return False
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, dict):
            if "enabled" in value:
                return bool(value["enabled"])
            if "available" in value:
                return bool(value["available"])
            value = value.get("status")
        return str(value).lower() not in {"false", "0", "disabled", "unavailable", "denied", "off"}

    def _balance_with(self, key: str) -> dict[str, Any]:
        response = self._request("GET", self.url("balance"), key=key)
        data = self._json(response, "余额接口")
        if data.get("ok") is False:
            raise AuthenticationError("小描 API Key 验证失败。")
        fields = self._balance_fields(data, response.headers)
        if fields["available_credits"] is None:
            raise ClientError("余额接口未返回可用额度。")
        fields["journal_available"] = self._journal_available(fields["services"])
        return fields

    def authenticate(self, explicit: str | None = None) -> tuple[str, dict[str, Any]]:
        if self._key:
            return self._key, self._balance_with(self._key)
        candidates = self.credentials.discover(explicit)
        if not candidates:
            raise AuthenticationError("没有找到有效的小描 API Key。")
        for source, key in candidates:
            try:
                balance = self._balance_with(key)
            except AuthenticationError:
                continue
            self._key = key
            self.credentials.migrate_if_needed(source, key)
            return key, balance
        raise AuthenticationError("没有找到有效的小描 API Key。")

    def balance(self) -> dict[str, Any]:
        _, value = self.authenticate()
        return value

    @classmethod
    def _result_url(cls, data: dict[str, Any]) -> str | None:
        direct = cls._first(data, "result_url", "result_file_url", "download_url")
        if direct:
            return str(direct)
        nested = data.get("result")
        if isinstance(nested, dict) and nested.get("url"):
            return str(nested["url"])
        return None

    def submit(self, brief: str, references: list[str]) -> dict[str, Any]:
        bundle = validate(brief, references)
        reusable = self.store.find_reusable(bundle.task_hash)
        if reusable:
            reusable["reused"] = True
            return reusable

        key, balance = self.authenticate()
        try:
            available = float(balance["available_credits"])
        except (TypeError, ValueError) as exc:
            raise ClientError("余额接口返回的可用额度无效。") from exc
        if available < EXPECTED_COST:
            raise QuotaError(f"当前额度不足；当前：{balance['available_credits']}，期刊图需要：{EXPECTED_COST}。")
        if not balance["journal_available"]:
            raise ClientError("当前 API Key 未开放期刊图生成权限。")

        with ExitStack() as stack:
            files = [("brief", (None, bundle.brief))] + [
                ("references", (item.path.name, stack.enter_context(item.path.open("rb")), item.mime))
                for item in bundle.references
            ]
            response = self._request(
                "POST", self.url("submit"), key=key, files=files, retry=False
            )
        data = self._json(response, "任务提交接口")
        job_id = self._first(data, "job_id", "task_id", "id")
        if not job_id:
            raise ClientError("小描没有返回任务编号。")
        credits = self._balance_fields(data, response.headers)
        job = {
            "job_id": str(job_id),
            "task_hash": bundle.task_hash,
            "brief_hash": bundle.brief_hash,
            "reference_hashes": [item.sha256 for item in bundle.references],
            "status": str(self._first(data, "status", "state") or "submitted").lower(),
            "submitted_at": int(time.time()),
            "last_checked_at": int(time.time()),
            "result_url": self._result_url(data),
            "downloaded": False,
            "charged": False,
            "retry_count": 0,
            "resubmit_count": 0,
            "credits_left": credits["credits_left"],
            "credits_used": credits["credits_used"],
        }
        self.store.put(job)
        job["reserved_credits"] = self._first(data, "reserved_credits", "credits_reserved") or EXPECTED_COST
        job["reused"] = False
        return job

    def status(self, job_id: str) -> dict[str, Any]:
        key, _ = self.authenticate()
        response = self._request("GET", self.url("status", job_id=job_id), key=key)
        data = self._json(response, "任务状态接口")
        state = str(self._first(data, "status", "state") or "unknown").lower()
        current = self.store.get(job_id)
        if not current:
            current = {
                "job_id": job_id,
                "task_hash": f"external:{job_id}",
                "brief_hash": "unknown",
                "reference_hashes": [],
                "submitted_at": int(time.time()),
                "downloaded": False,
                "charged": False,
            }
            self.store.put({**current, "status": state})
        credits = self._balance_fields(data, response.headers)
        return self.store.update(
            job_id,
            status=state,
            last_checked_at=int(time.time()),
            result_url=self._result_url(data) or current.get("result_url"),
            credits_left=credits["credits_left"] if credits["credits_left"] is not None else current.get("credits_left"),
            credits_used=credits["credits_used"] if credits["credits_used"] is not None else current.get("credits_used"),
            last_error=None,
        )

    @staticmethod
    def _verify_png(raw: bytes) -> tuple[int, int]:
        if len(raw) <= len(PNG_SIGNATURE) or not raw.startswith(PNG_SIGNATURE):
            raise ClientError("结果不是有效 PNG。")
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != "PNG":
                    raise ClientError("结果不是 PNG。")
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
                width, height = image.size
        except ClientError:
            raise
        except Exception as exc:
            raise ClientError("PNG 无法完整解码。") from exc
        if width <= 0 or height <= 0:
            raise ClientError("PNG 尺寸无效。")
        return width, height

    def _result_bytes(self, job_id: str, url: str | None) -> tuple[bytes, requests.Response, dict[str, Any]]:
        key, _ = self.authenticate()
        target = url or self.url("result", job_id=job_id)
        response = self._request("GET", target, key=key)
        metadata: dict[str, Any] = {}
        if response.content.startswith(PNG_SIGNATURE):
            return response.content, response, metadata
        metadata = self._json(response, "结果接口")
        encoded = metadata.get("image_base64")
        if encoded:
            try:
                return base64.b64decode(encoded, validate=True), response, metadata
            except Exception as exc:
                raise ClientError("结果中的 PNG Base64 无效。") from exc
        download = self._result_url(metadata)
        if not download:
            raise ClientError("结果接口没有返回 PNG。")
        follow = self._request("GET", download, key=key)
        return follow.content, follow, metadata

    def download(self, job_id: str) -> dict[str, Any]:
        current = self.store.get(job_id)
        if current and current["downloaded"] and current.get("result_path"):
            if Path(current["result_path"]).is_file():
                return current
        try:
            raw, response, metadata = self._result_bytes(job_id, current.get("result_url") if current else None)
        except NotReady:
            return self.store.update(job_id, status="processing", last_checked_at=int(time.time()))
        width, height = self._verify_png(raw)
        result_dir = self.root / "results" / re.sub(r"[^A-Za-z0-9_.-]", "_", job_id)
        result_dir.mkdir(parents=True, exist_ok=True)
        target = result_dir / "final.png"
        temp = result_dir / "final.png.part"
        temp.write_bytes(raw)
        self._verify_png(temp.read_bytes())
        os.replace(temp, target)

        credits = self._balance_fields(metadata, response.headers)
        fresh = self.balance()
        left = fresh["available_credits"]
        used = fresh["credits_used"] if fresh["credits_used"] is not None else credits["credits_used"]
        charged_raw = self._first(metadata, "charged", "charged_credits", "credits_spent")
        charged = bool(charged_raw) if charged_raw is not None else bool((current or {}).get("charged", False))
        updated = self.store.update(
            job_id,
            status="completed",
            last_checked_at=int(time.time()),
            result_path=str(target),
            downloaded=True,
            charged=charged,
            credits_left=left,
            credits_used=used,
            last_error=None,
        )
        state_file = result_dir / "job.json"
        safe_state = {
            "job_id": job_id,
            "status": "completed",
            "downloaded": True,
            "result": "final.png",
            "width": width,
            "height": height,
            "credits_left": left,
        }
        temp_state = state_file.with_suffix(".json.tmp")
        temp_state.write_text(json.dumps(safe_state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_state, state_file)
        return updated

    def process(self, job_id: str, *, interval: float = POLL_INTERVAL, timeout: float | None = None) -> dict[str, Any]:
        started = time.monotonic()
        while True:
            current = self.store.get(job_id)
            if current and current["downloaded"]:
                return current
            state = str((current or {}).get("status", "")).lower()
            if state in SUCCESS:
                return self.download(job_id)
            if state in FAILURE:
                raise ClientError(f"期刊图任务已结束：{state}")
            job = self.status(job_id)
            state = str(job["status"]).lower()
            if state in SUCCESS:
                return self.download(job_id)
            if state in FAILURE:
                raise ClientError(f"期刊图任务已结束：{state}")
            if timeout is not None and time.monotonic() - started >= timeout:
                raise ClientError("等待超时；任务已保留并可继续恢复。")
            time.sleep(max(float(interval), 0.1))

    def cancel(self, job_id: str) -> dict[str, Any]:
        key, _ = self.authenticate()
        response = self._request("DELETE", self.url("cancel", job_id=job_id), key=key)
        data = self._json(response, "取消接口") if response.content else {}
        state = str(self._first(data, "status", "state") or "cancelled").lower()
        current = self.store.get(job_id)
        if current:
            return self.store.update(job_id, status=state, last_checked_at=int(time.time()))
        return {"job_id": job_id, "status": state}


def process_is_running(pid: int) -> bool:
    """Probe a PID without sending a terminating signal on Windows."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
        if not handle:
            error = ctypes.get_last_error()
            if error == 5:  # Access denied does not mean the process has exited.
                return True
            if error == 87:  # The PID no longer exists.
                return False
            raise ctypes.WinError(error)
        try:
            state = kernel32.WaitForSingleObject(handle, 0)
            if state == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            return state == 0x00000102  # WAIT_TIMEOUT: process is still running.
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def start_worker_detached() -> int:
    script = Path(__file__).with_name("journal_worker.py")
    root = default_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    pid_file = root / "worker.pid"
    if pid_file.is_file():
        try:
            old_pid = int(pid_file.read_text(encoding="ascii").strip())
            if process_is_running(old_pid):
                return old_pid
        except (OSError, ValueError):
            pass
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": str(script.parent),
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([sys.executable, "-X", "utf8", str(script), "--daemon"], **kwargs)
    temp = pid_file.with_suffix(".pid.tmp")
    temp.write_text(str(proc.pid), encoding="ascii")
    os.replace(temp, pid_file)
    return proc.pid


def install_autostart() -> None:
    if os.name != "nt":
        raise ClientError("自动启动安装仅支持 Windows。")
    script = Path(__file__).resolve()
    command = f'"{sys.executable}" -X utf8 "{script}" start-worker'
    result = subprocess.run(
        ["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/TN", "cell_figure_worker", "/TR", command],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ClientError("无法注册 cell_figure 自动恢复任务。")


def emit(value: dict[str, Any]) -> None:
    allowed = {
        "ok", "job_id", "status", "reused", "reserved_credits", "credits_left",
        "available_credits", "credits_used", "journal_available", "result_path", "downloaded",
        "worker_started", "autostart_installed", "configured",
    }
    print(json.dumps({k: v for k, v in value.items() if k in allowed}, ensure_ascii=False))


def text_argument(args: argparse.Namespace) -> str:
    if getattr(args, "text_file", None):
        return Path(args.text_file).expanduser().read_text(encoding="utf-8")
    return getattr(args, "text", None) or ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="cell_figure 小描期刊图客户端")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("configure-key")
    sub.add_parser("balance")
    for name in ("submit", "run"):
        item = sub.add_parser(name)
        item.add_argument("--text")
        item.add_argument("--text-file")
        item.add_argument("--reference", action="append", default=[])
        if name == "run":
            item.add_argument("--wait", action="store_true")
            item.add_argument("--interval", type=float, default=POLL_INTERVAL)
            item.add_argument("--timeout", type=float)
    for name in ("status", "fetch", "cancel"):
        item = sub.add_parser(name)
        item.add_argument("job_id")
    resume = sub.add_parser("resume")
    resume.add_argument("job_id")
    resume.add_argument("--wait", action="store_true")
    resume.add_argument("--interval", type=float, default=POLL_INTERVAL)
    resume.add_argument("--timeout", type=float)
    sub.add_parser("start-worker")
    sub.add_parser("install-autostart")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        client = XiaomiaoClient()
        if args.command == "configure-key":
            key = read_key_from_stdin()
            client._balance_with(key)
            client.credentials.save(key)
            emit({"configured": True})
        elif args.command == "balance":
            emit(client.balance())
        elif args.command in {"submit", "run"}:
            job = client.submit(text_argument(args), args.reference)
            if args.command == "run" and not job.get("downloaded"):
                if args.wait:
                    job = client.process(str(job["job_id"]), interval=args.interval, timeout=args.timeout)
                else:
                    start_worker_detached()
            emit(job)
        elif args.command == "status":
            emit(client.status(args.job_id))
        elif args.command == "fetch":
            emit(client.download(args.job_id))
        elif args.command == "cancel":
            emit(client.cancel(args.job_id))
        elif args.command == "resume":
            if args.wait:
                emit(client.process(args.job_id, interval=args.interval, timeout=args.timeout))
            else:
                start_worker_detached()
                emit({"job_id": args.job_id, "status": "resuming"})
        elif args.command == "start-worker":
            emit({"worker_started": True, "pid": start_worker_detached()})
        elif args.command == "install-autostart":
            install_autostart()
            emit({"autostart_installed": True})
        return 0
    except (ClientError, InputError, OSError, ValueError, json.JSONDecodeError) as exc:
        safe = redact_text(str(exc), [])
        print(json.dumps({"ok": False, "error": safe}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
