#!/usr/bin/env python3
"""Deterministic local validation and hashing for journal-figure inputs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import mimetypes
from pathlib import Path


ALLOWED = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".pdf": "application/pdf",
}
MAX_BRIEF_CHARS = 12_000
MAX_REFERENCES = 6
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 30 * 1024 * 1024


class InputError(RuntimeError):
    pass


@dataclass(frozen=True)
class Reference:
    path: Path
    mime: str
    size: int
    sha256: str


@dataclass(frozen=True)
class InputBundle:
    brief: str
    brief_hash: str
    references: tuple[Reference, ...]
    task_hash: str


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate(brief: str, references: list[str]) -> InputBundle:
    if not isinstance(brief, str) or not brief.strip():
        raise InputError("研究内容不能为空。")
    if len(brief) > MAX_BRIEF_CHARS:
        raise InputError(f"研究内容超过 {MAX_BRIEF_CHARS} 字符。")
    if len(references) > MAX_REFERENCES:
        raise InputError(f"参考文件最多 {MAX_REFERENCES} 个。")

    checked: list[Reference] = []
    total = 0
    for value in references:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise InputError(f"参考文件不存在：{path}")
        suffix = path.suffix.lower()
        if suffix not in ALLOWED:
            raise InputError(f"不支持的参考格式：{suffix or '无扩展名'}")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise InputError(f"参考文件超过 10 MB：{path.name}")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise InputError("参考文件合计超过 30 MB。")
        checked.append(Reference(path, ALLOWED[suffix], size, file_hash(path)))

    brief_hash = hashlib.sha256(brief.encode("utf-8")).hexdigest()
    digest = hashlib.sha256()
    digest.update(brief.encode("utf-8"))
    for item in checked:
        digest.update(bytes.fromhex(item.sha256))
    return InputBundle(brief, brief_hash, tuple(checked), digest.hexdigest())
