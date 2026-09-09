#!/usr/bin/env python3
"""Narrow, non-scanning credential discovery for the Xiaomiao API."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import sys
from typing import Iterable


KEY_PATTERN = re.compile(r"(?<![A-Za-z0-9_])(img_live_[A-Za-z0-9._-]+)")


class CredentialError(RuntimeError):
    pass


def default_data_dir() -> Path:
    override = os.environ.get("XIAOMIAO_CLIENT_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "cell_figure"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "cell-figure"


def desktop_dir() -> Path:
    override = os.environ.get("XIAOMIAO_DESKTOP_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                raw, _ = winreg.QueryValueEx(key, "Desktop")
            return Path(os.path.expandvars(str(raw))).resolve()
        except (OSError, ValueError):
            pass
        one_drive = os.environ.get("OneDrive")
        if one_drive and (Path(one_drive) / "Desktop").is_dir():
            return (Path(one_drive) / "Desktop").resolve()
    return (Path.home() / "Desktop").resolve()


def parse_key_candidates(text: str) -> list[str]:
    result: list[str] = []
    for match in KEY_PATTERN.finditer(text):
        key = match.group(1).strip()
        if key not in result:
            result.append(key)
    return result


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, object]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _dpapi_encrypt(data: bytes) -> bytes:
    source, keepalive = _blob(data)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptProtectData(
        ctypes.byref(source), "cell_figure", None, None, None, 0, ctypes.byref(output)
    ):
        raise CredentialError("Windows 安全凭据保存失败。")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del keepalive


def _dpapi_decrypt(data: bytes) -> bytes:
    source, keepalive = _blob(data)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)
    ):
        raise CredentialError("Windows 安全凭据读取失败。")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del keepalive


class CredentialManager:
    """Discover, store and redact a Xiaomiao key without broad filesystem scans."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_data_dir()).resolve()
        self.secure_path = self.root / "credentials" / (
            "xiaomiao_key.dpapi" if os.name == "nt" else "xiaomiao_key.private"
        )

    @staticmethod
    def redact(value: str) -> str:
        return f"img_live_****{value[-4:]}" if value.startswith("img_live_") and len(value) > 12 else "[REDACTED]"

    def save(self, key: str) -> None:
        if not KEY_PATTERN.fullmatch(key.strip()):
            raise CredentialError("API Key 格式无效。")
        self.secure_path.parent.mkdir(parents=True, exist_ok=True)
        payload = key.strip().encode("utf-8")
        if os.name == "nt":
            payload = _dpapi_encrypt(payload)
        temp = self.secure_path.with_suffix(self.secure_path.suffix + ".tmp")
        temp.write_bytes(payload)
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, self.secure_path)

    def load_secure(self) -> str | None:
        if not self.secure_path.is_file():
            return None
        raw = self.secure_path.read_bytes()
        if os.name == "nt":
            raw = _dpapi_decrypt(raw)
        value = raw.decode("utf-8").strip()
        return value if KEY_PATTERN.fullmatch(value) else None

    def load_legacy(self) -> str | None:
        """Read only the previous client's own credential file for one-time migration."""
        legacy = self.root / "credentials" / "api_key"
        if not legacy.is_file():
            return None
        try:
            value = legacy.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return None
        return value if KEY_PATTERN.fullmatch(value) else None

    def desktop_candidates(self) -> list[str]:
        candidate = desktop_dir() / "Cell_skills.txt"
        if not candidate.is_file():
            return []
        try:
            return parse_key_candidates(candidate.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError):
            return []

    def discover(self, explicit: str | None = None) -> list[tuple[str, str]]:
        ordered: list[tuple[str, str]] = []
        env_key = os.environ.get("XIAOMIAO_API_KEY", "").strip()
        if KEY_PATTERN.fullmatch(env_key):
            ordered.append(("environment", env_key))
        try:
            secure = self.load_secure()
        except CredentialError:
            secure = None
        if secure:
            ordered.append(("secure_store", secure))
        legacy = self.load_legacy()
        if legacy:
            ordered.append(("legacy_store", legacy))
        ordered.extend(("desktop", item) for item in self.desktop_candidates())
        if explicit:
            ordered.extend(("explicit", item) for item in parse_key_candidates(explicit))

        unique: list[tuple[str, str]] = []
        seen: set[str] = set()
        for source, key in ordered:
            if key not in seen:
                unique.append((source, key))
                seen.add(key)
        return unique

    def migrate_if_needed(self, source: str, key: str) -> None:
        if source in {"desktop", "explicit", "legacy_store"}:
            self.save(key)


def read_key_from_stdin() -> str:
    value = sys.stdin.readline().strip()
    matches = parse_key_candidates(value)
    if len(matches) != 1:
        raise CredentialError("没有收到唯一有效的小描 API Key。")
    return matches[0]


def redact_text(text: str, secrets: Iterable[str]) -> str:
    safe = text
    for secret in secrets:
        if secret:
            safe = safe.replace(secret, "[REDACTED]")
    return KEY_PATTERN.sub("img_live_[REDACTED]", safe)
