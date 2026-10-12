"""On-disk archive storage: messages.jsonl, state.json, folder naming (stdlib only).

``messages.jsonl`` is the single source of truth: one raw `line messages --json` item per line,
plus the archive-only key ``possibly_unsent_at``. Everything else (messages.md) is derived.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

JSONL_NAME = "messages.jsonl"
MD_NAME = "messages.md"
STATE_NAME = "state.json"
FILES_DIRNAME = "files"
CONTACTS_NAME = "contacts.json"  # shared name cache, lives in the archive root
STATE_SCHEMA = 1

ARCHIVE_KEYS = ("possibly_unsent_at",)  # keys we add; never present in `line` output

WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
NAME_LIMIT = 60  # characters of the chat name kept in the folder name


# ---------------------------------------------------------------- file names


def safe_name(text: str, *, limit: int = NAME_LIMIT, fallback: str = "unnamed") -> str:
    """Make ``text`` safe as one path component on Windows, macOS and Linux."""
    cleaned = unicodedata.normalize("NFC", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = _BAD_CHARS.sub("_", cleaned)
    cleaned = cleaned[:limit].rstrip(" .")
    if not cleaned.strip("_ ."):
        return fallback
    if cleaned.split(".")[0].strip().lower() in WINDOWS_RESERVED:
        cleaned = "_" + cleaned
    return cleaned


def safe_id(message_id: str) -> str:
    """Message IDs are numeric; anything else is flattened so it is a valid file name part."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(message_id))[:64] or "_"


def folder_name(chat_id: str, chat_name: str | None) -> str:
    base = safe_id(chat_id)
    name = safe_name(chat_name, fallback="") if chat_name else ""
    return f"{base}__{name}" if name else base


def find_chat_dir(archive_root: Path, chat_id: str) -> Path | None:
    """The existing folder for ``chat_id`` (keyed by ID, so renames never split an archive)."""
    base = safe_id(chat_id)
    try:
        entries = sorted(archive_root.iterdir())
    except OSError:
        return None
    for entry in entries:
        if entry.is_dir() and (entry.name == base or entry.name.startswith(base + "__")):
            return entry
    return None


def folder_label(path: Path, chat_id: str) -> str | None:
    """The name part of ``<chat_id>__<name>`` (None when the folder has no name part)."""
    prefix = safe_id(chat_id) + "__"
    return path.name[len(prefix) :] if path.name.startswith(prefix) else None


# ---------------------------------------------------------------- atomic writes


def _replace(src: str, dst: Path) -> None:
    """os.replace with a few retries: on Windows an open reader makes it fail briefly."""
    for attempt in range(5):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 4 or not sys.platform.startswith("win"):
                raise
            time.sleep(0.1 * (attempt + 1))


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _dump(record: Mapping[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------- jsonl


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (records, warnings). Unreadable lines are skipped, never fatal."""
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not path.is_file():
        return records, warnings
    seen: set[str] = set()
    bad = 0
    with open(path, encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                bad += 1
                continue
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                bad += 1
                continue
            if item["id"] in seen:  # defensive: keep the first occurrence
                continue
            seen.add(item["id"])
            records.append(item)
    if bad:
        warnings.append(f"{path.name}: skipped {bad} unreadable line(s)")
    return records, warnings


def write_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    atomic_write_text(path, "".join(_dump(r) + "\n" for r in records))


def append_jsonl(path: Path, records: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = "".join(_dump(r) + "\n" for r in records)
    if not lines:
        return
    needs_newline = False
    if path.is_file() and path.stat().st_size:
        with open(path, "rb") as handle:
            handle.seek(-1, os.SEEK_END)
            needs_newline = handle.read(1) != b"\n"
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        if needs_newline:
            handle.write("\n")
        handle.write(lines)
        handle.flush()
        os.fsync(handle.fileno())


def _core(record: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k not in ARCHIVE_KEYS}


@dataclass
class MergeResult:
    records: list[dict[str, Any]]
    new_ids: list[str] = field(default_factory=list)
    changed: bool = False  # existing lines were modified, so the file must be rewritten
    possibly_unsent: list[dict[str, Any]] = field(default_factory=list)
    gap_possible: bool = False
    gap_after_id: str | None = None
    gap_before_id: str | None = None


def merge_window(
    existing: list[dict[str, Any]],
    fresh: list[dict[str, Any]],
    *,
    now_iso: str,
    window_limit: int,
) -> MergeResult:
    """Fold a freshly fetched window (oldest first) into the archive.

    * New IDs are appended (dedupe by message ID).
    * Known IDs are refreshed (reactions, a decryption that now works) but never downgraded to
      `decryption_failed`; a reappearing message loses its `possibly_unsent_at`.
    * Archived messages whose time falls inside the fresh window but which are missing from it are
      marked `possibly_unsent_at` (a heuristic; they are never deleted).
    * Gap: the oldest fetched message is newer than the newest archived one while the window was
      full, so messages in between may be unreachable.
    """
    by_id = {r["id"]: r for r in existing}
    fresh_by_id = {r["id"]: r for r in fresh}
    result = MergeResult(records=[dict(r) for r in existing])
    index = {r["id"]: i for i, r in enumerate(result.records)}

    times = [t for t in (_time(r) for r in fresh) if t > 0]
    lo, hi = (min(times), max(times)) if times else (0, 0)

    # --- gap (computed against the archive as it was before this sync)
    archived_times = [(_time(r), r["id"]) for r in existing if _time(r) > 0]
    if existing and fresh and len(fresh) >= window_limit and lo > 0 and archived_times:
        newest_time, newest_id = max(archived_times)
        if newest_id not in fresh_by_id and lo > newest_time:
            oldest_fetched = min(fresh, key=lambda r: _time(r) or 1 << 62)
            result.gap_possible = True
            result.gap_after_id = newest_id
            result.gap_before_id = oldest_fetched["id"]

    # --- refresh known, append new
    for item in fresh:
        mid = item["id"]
        if mid in by_id:
            old = result.records[index[mid]]
            new = dict(item)
            if item.get("status") == "decryption_failed" and old.get("status") != (
                "decryption_failed"
            ):
                new = _core(old)  # keep the readable copy we already have
            if _core(old) != new or "possibly_unsent_at" in old:
                result.records[index[mid]] = new
                result.changed = True
        else:
            index[mid] = len(result.records)
            result.records.append(dict(item))
            result.new_ids.append(mid)

    # --- possibly unsent
    if lo > 0:
        for rec in result.records:
            if rec["id"] in fresh_by_id or rec.get("possibly_unsent_at"):
                continue
            if rec["id"] not in by_id:
                continue  # only messages archived before this sync
            created = _time(rec)
            if created > 0 and lo <= created <= hi:
                rec["possibly_unsent_at"] = now_iso
                result.changed = True
                result.possibly_unsent.append(rec)
    return result


def _time(record: Mapping[str, Any]) -> int:
    value = record.get("created_time")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


# ---------------------------------------------------------------- state.json / contacts.json


def read_json(path: Path, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def new_state(chat_id: str) -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "chat_id": chat_id,
        "name": None,
        "last_sync_at": None,
        "newest_message_id": None,
        "newest_created_time": None,
        "gaps": [],
        "failed_files": {},
    }


def read_state(path: Path, chat_id: str) -> dict[str, Any]:
    state = read_json(path, None)
    if not isinstance(state, dict):
        return new_state(chat_id)
    base = new_state(chat_id)
    base.update(state)
    if not isinstance(base["gaps"], list):
        base["gaps"] = []
    if not isinstance(base["failed_files"], dict):
        base["failed_files"] = {}
    return base
