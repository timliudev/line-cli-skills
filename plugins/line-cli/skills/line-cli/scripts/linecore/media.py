"""Attachment download for the archive: names, extensions, magic bytes (stdlib only).

Downloads go through ``line download CHAT --message ID --output PATH --json``. The CLI never
overwrites an existing path and only handles image/video/audio/file messages up to 20 MiB.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any

from . import runner
from .config import Config
from .store import safe_id

TYPE_BY_CONTENT = {1: "image", 2: "video", 3: "audio", 14: "file"}
CONTENT_BY_TYPE = {v: k for k, v in TYPE_BY_CONTENT.items()}
DEFAULT_EXT = {1: ".jpg", 2: ".mp4", 3: ".m4a"}
LABEL = {"image": "Image", "video": "Video", "audio": "Audio", "file": "File"}

# Failure reasons that are final (retrying cannot help) vs worth another attempt next sync.
UPSTREAM_CAP_BYTES = 20 * 1024 * 1024  # the CLI refuses anything larger
PERMANENT = {"expired", "external", "too_large", "auth_failed"}
_REASON_BY_KIND = {
    "attachment_expired": "expired",
    "attachment_external": "external",
    "attachment_too_large": "too_large",
    "attachment_processing": "processing",
}
_FILE_RE = re.compile(r"^\d{8}-\d{6}_(?P<id>[^.]+)\.[^.]+$")
_EXT_OK = re.compile(r"^[a-z0-9]{1,10}$")

# Equivalent spellings: the declared extension is kept when it already matches the real format.
_EQUIV = {
    "jpg": {"jpg", "jpeg", "jpe", "jfif"},
    "mp4": {"mp4", "m4v", "mov", "3gp"},
    "m4a": {"m4a", "m4b", "aac"},
}


def kind_of(record: Mapping[str, Any]) -> str | None:
    """Download type name for an attachment message, else None."""
    content_type = record.get("content_type")
    if record.get("status") == "attachment" and content_type in TYPE_BY_CONTENT:
        return TYPE_BY_CONTENT[content_type]
    return None


# ---------------------------------------------------------------- names and extensions


def stamp(created_ms: int | None, tz: tzinfo | None = None) -> str:
    """``YYYYMMDD-HHMMSS`` in local time (or ``tz``); a placeholder when LINE gave no time."""
    if not isinstance(created_ms, int) or created_ms <= 0:
        return "00000000-000000"
    return datetime.fromtimestamp(created_ms / 1000, tz).strftime("%Y%m%d-%H%M%S")


def declared_ext(record: Mapping[str, Any]) -> str:
    """Extension implied by content_type (and, for files, the sanitized original file_name)."""
    content_type = record.get("content_type")
    if content_type in DEFAULT_EXT:
        return DEFAULT_EXT[content_type]
    name = record.get("file_name")
    if isinstance(name, str):
        suffix = Path(name.replace("\\", "/").rsplit("/", 1)[-1]).suffix.lower().lstrip(".")
        if _EXT_OK.match(suffix):
            return "." + suffix
    return ".bin"


def sniff_ext(head: bytes) -> str | None:
    """Extension from magic bytes: jpg, png, gif, webp, mp4, m4a, pdf (None when unknown)."""
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head.startswith(b"%PDF-"):
        return ".pdf"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"M4A ", b"M4B ", b"M4P "):
            return ".m4a"
        return ".mp4"
    return None


def final_ext(record: Mapping[str, Any], head: bytes) -> str:
    """Declared extension, corrected by the file's magic bytes when they disagree."""
    declared = declared_ext(record)
    sniffed = sniff_ext(head)
    if sniffed is None:
        return declared
    want, have = sniffed.lstrip("."), declared.lstrip(".")
    if have == want or have in _EQUIV.get(want, ()):
        return declared
    # audio sent as an MP4 container is still an m4a voice message
    if want == "mp4" and record.get("content_type") == 3:
        return ".m4a"
    return sniffed


def target_name(record: Mapping[str, Any], ext: str, tz: tzinfo | None = None) -> str:
    return f"{stamp(record.get('created_time'), tz)}_{safe_id(record['id'])}{ext}"


def index_files(files_dir: Path) -> dict[str, str]:
    """Map message ID -> file name for everything already downloaded."""
    found: dict[str, str] = {}
    try:
        names = sorted(os.listdir(files_dir))
    except OSError:
        return found
    for name in names:
        match = _FILE_RE.match(name)
        if match and not name.endswith(".part"):
            found.setdefault(match.group("id"), name)
    return found


def _unique(directory: Path, name: str) -> Path:
    """``name`` or ``name`` with -1, -2, ... so an existing file is never overwritten."""
    candidate = directory / name
    stem, dot, suffix = name.rpartition(".")
    counter = 0
    while candidate.exists():
        counter += 1
        candidate = directory / f"{stem}-{counter}{dot}{suffix}"
    return candidate


# ---------------------------------------------------------------- download


@dataclass
class DownloadResult:
    ok: bool
    path: Path | None = None
    size: int = 0
    reason: str | None = None  # expired | external | too_large | processing | error | ...
    kind: str | None = None  # runner error kind
    message: str = ""
    limit: int | None = None  # for too_large: the byte limit that was exceeded


def download_attachment(
    chat_id: str,
    record: Mapping[str, Any],
    files_dir: Path,
    *,
    max_bytes: int,
    config: Config | None = None,
    binary: str | None = None,
    tz: tzinfo | None = None,
) -> DownloadResult:
    """Download one attachment into ``files_dir`` as ``<stamp>_<id>.<ext>``; never overwrites."""
    files_dir.mkdir(parents=True, exist_ok=True)
    part = files_dir / f"{stamp(record.get('created_time'), tz)}_{safe_id(record['id'])}.part"
    part.unlink(missing_ok=True)  # a leftover from a crashed run is ours to clean up
    result = runner.run_line(
        ["download", chat_id, "--message", str(record["id"]), "--output", str(part), "--json"],
        config=config,
        binary=binary,
    )
    if not result.ok:
        part.unlink(missing_ok=True)
        err = result.error
        kind = err.kind if err else "error"
        reason = _REASON_BY_KIND.get(kind, kind)
        message = err.message if err else "download failed"
        if reason == "too_large":
            return DownloadResult(
                False,
                reason=reason,
                kind=kind,
                message=message,
                limit=max(UPSTREAM_CAP_BYTES, max_bytes),
            )
        return DownloadResult(False, reason=reason, kind=kind, message=message)

    try:
        size = part.stat().st_size
    except OSError:
        return DownloadResult(
            False, reason="error", kind="missing_output", message="line wrote no file"
        )
    if size > max_bytes:
        part.unlink(missing_ok=True)
        return DownloadResult(
            False,
            reason="too_large",
            kind="over_max_bytes",
            message=f"{size} bytes exceeds the configured limit of {max_bytes}",
            limit=max_bytes,
        )
    with open(part, "rb") as handle:
        head = handle.read(32)
    ext = final_ext(record, head)
    destination = _unique(files_dir, target_name(record, ext, tz))
    os.replace(part, destination)  # destination is guaranteed not to exist
    return DownloadResult(True, path=destination, size=size)


def failure_record(record: Mapping[str, Any], result: DownloadResult, now_iso: str) -> dict:
    out: dict[str, Any] = {
        "reason": result.reason,
        "kind": result.kind,
        "message": result.message,
        "content_type": record.get("content_type"),
        "at": now_iso,
    }
    if result.limit is not None:
        out["limit"] = result.limit
    return out


def should_retry(failure: Mapping[str, Any] | None, max_bytes: int) -> bool:
    """Whether an earlier failure is worth another attempt now."""
    if not failure:
        return True
    reason = failure.get("reason")
    if reason == "too_large":
        limit = failure.get("limit")
        return isinstance(limit, int) and max_bytes > limit
    return reason not in PERMANENT
