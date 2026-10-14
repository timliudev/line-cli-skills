"""On-disk archive storage: messages.jsonl, state.json, folder naming (stdlib only).

``messages.jsonl`` is the single source of truth: one raw `line messages --json` item per line,
plus the archive-only key ``possibly_unsent_at``. Everything else (messages.md) is derived.
"""

from __future__ import annotations

import hashlib
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
    """``<name>__<chat_id>``; just ``<chat_id>`` when the name is unknown or has no usable text."""
    base = safe_id(chat_id)
    name = safe_name(chat_name, fallback="") if chat_name else ""
    return f"{name}__{base}" if name else base


_CHAT_ID = r"(?:[CUR][A-Za-z0-9_-]{43}|[cur][0-9a-f]{32})"  # a full LINE chat ID
_CURRENT_LAYOUT = re.compile(rf"(?:^|__)({_CHAT_ID})$")  # "<name>__<id>" or the bare "<id>"
_OLD_LAYOUT = re.compile(rf"^({_CHAT_ID})__(.*)$", re.S)  # "<id>__<name>" (older versions)


def parse_folder(folder_name: str) -> tuple[str, str | None] | None:
    """``(chat_id, label)`` of an archive folder name, or None when it is not a chat folder.

    The chat ID is one exact, full-length segment, never a prefix or suffix of something else:
    ``<name>__<id>`` (current; the ID is the LAST segment), the bare ``<id>``, and the old
    ``<id>__<name>``. The current layout wins, so a chat whose *name* looks like another chat's ID
    (``<other id>__<own id>``) belongs to its own ID only.
    """
    match = _CURRENT_LAYOUT.search(folder_name)
    if match:
        label = folder_name[: match.start()]
        return match.group(1), (label or None)
    match = _OLD_LAYOUT.match(folder_name)
    if match:
        return match.group(1), (match.group(2) or None)
    return None


def _belongs_to(folder_name: str, chat_id: str) -> bool:
    parsed = parse_folder(folder_name)
    if parsed is not None:
        return parsed[0] == safe_id(chat_id)
    # not a full-length ID (never produced by LINE): only the exact current / bare layouts
    base = safe_id(chat_id)
    return folder_name == base or folder_name.endswith("__" + base)


def find_chat_dirs(archive_root: Path, chat_id: str) -> list[Path]:
    """Every folder of ``chat_id``: ``<name>__<id>``, bare ``<id>`` and the old ``<id>__<name>``.

    The ID must match as a whole segment (see ``parse_folder``), so one chat's folder is never
    taken for another's, whatever the chat names look like.
    """
    try:
        entries = sorted(archive_root.iterdir())
    except OSError:
        return []
    return [e for e in entries if e.is_dir() and _belongs_to(e.name, chat_id)]


def _layout_rank(path: Path, base: str) -> int:
    if path.name == base:
        return 1
    return 0 if path.name.endswith("__" + base) else 2


def find_chat_dir(archive_root: Path, chat_id: str) -> Path | None:
    """The existing folder for ``chat_id`` (keyed by ID, so renames never split an archive).

    Prefers the current ``<name>__<id>``, then a bare ``<id>``, then the old ``<id>__<name>``.
    When several exist, ``merge_chat_dirs`` folds them into one.
    """
    found = find_chat_dirs(archive_root, chat_id)
    base = safe_id(chat_id)
    return min(found, key=lambda p: _layout_rank(p, base), default=None)


def folder_label(path: Path, chat_id: str) -> str | None:
    """The name part of the folder (None when the folder is the bare ID)."""
    parsed = parse_folder(path.name)
    if parsed is not None:
        return parsed[1] if parsed[0] == safe_id(chat_id) else None
    base = safe_id(chat_id)
    if path.name.endswith("__" + base):
        return path.name[: -len(base) - 2] or None
    return None


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
    gap_from_time: int = 0  # created_time (ms) of the newest message archived before the gap
    gap_to_time: int = 0  # created_time (ms) of the oldest message that could be fetched


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
            result.gap_from_time = newest_time
            result.gap_to_time = lo

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


# ---------------------------------------------------------------- merging duplicate folders


def _same_file(a: Path, b: Path) -> bool:
    def digest(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()

    try:
        return a.stat().st_size == b.stat().st_size and digest(a) == digest(b)
    except OSError:
        return False


def _suffixed(directory: Path, name: str) -> Path:
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    counter = 0
    while True:
        counter += 1
        candidate = directory / f"{stem}-{counter}{dot}{ext}"
        if not candidate.exists():
            return candidate


def merge_records(
    primary: Mapping[str, Any], other: Mapping[str, Any], primary_is_newer: bool
) -> dict[str, Any]:
    """Two copies of one message: the more complete one wins; reactions and unsent marks union."""

    def readable(rec: Mapping[str, Any]) -> bool:
        return rec.get("status") != "decryption_failed"

    if readable(primary) != readable(other):
        winner, loser = (primary, other) if readable(primary) else (other, primary)
    elif primary_is_newer:
        winner, loser = primary, other
    else:
        winner, loser = other, primary
    merged = dict(winner)
    reactions: list[Any] = []
    seen: set[str] = set()
    for source in (winner, loser):
        items = source.get("reactions")
        for item in items if isinstance(items, list) else []:
            key = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                seen.add(key)
                reactions.append(item)
    if reactions:
        merged["reactions"] = reactions
    marks = [m for m in (winner.get("possibly_unsent_at"), loser.get("possibly_unsent_at")) if m]
    if marks:
        merged["possibly_unsent_at"] = min(marks)
    return merged


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _newest_time(record: Mapping[str, Any]) -> int:
    return _time(record)


def merge_chat_dirs(archive_root: Path, chat_id: str) -> tuple[Path | None, list[str]]:
    """Fold duplicate folders of one chat into the canonical one, losslessly.

    Returns (kept folder, warnings); a no-op without warnings for at most one folder. A source
    folder is removed only after everything in it was merged and it is empty; anything unexpected
    keeps the folder and adds a warning. ``messages.md`` is regenerated by the next render.
    """
    dirs = find_chat_dirs(archive_root, chat_id)
    if len(dirs) < 2:
        return (dirs[0] if dirs else None), []
    base = safe_id(chat_id)
    warnings: list[str] = []
    dirs.sort(key=lambda p: (_layout_rank(p, base), -_mtime(p / JSONL_NAME)))
    target, sources = dirs[0], dirs[1:]

    state = read_state(target / STATE_NAME, chat_id)
    records, read_warnings = read_jsonl(target / JSONL_NAME)
    warnings += read_warnings
    index = {r["id"]: i for i, r in enumerate(records)}
    mergeable: list[Path] = []

    for source in sources:
        src_records, src_warnings = read_jsonl(source / JSONL_NAME)
        warnings += src_warnings
        src_state = read_state(source / STATE_NAME, chat_id)
        src_newer = str(src_state.get("last_sync_at") or "") > str(state.get("last_sync_at") or "")
        for rec in src_records:
            if rec["id"] in index:
                i = index[rec["id"]]
                records[i] = merge_records(rec, records[i], src_newer)
            else:
                index[rec["id"]] = len(records)
                records.append(rec)
        failed = dict(src_state["failed_files"])
        failed.update(state["failed_files"])  # the target's record wins per message
        state["failed_files"] = failed
        known_gaps = {g.get("before_id") for g in state["gaps"] if isinstance(g, dict)}
        state["gaps"] += [
            g
            for g in src_state["gaps"]
            if isinstance(g, dict) and g.get("before_id") not in known_gaps
        ]
        state["last_sync_at"] = max(
            (t for t in (state.get("last_sync_at"), src_state.get("last_sync_at")) if t),
            default=None,
        )
        if not state.get("name") and src_state.get("name"):
            state["name"] = src_state["name"]
        if not src_warnings:
            mergeable.append(source)

    dest_dir = target / FILES_DIRNAME
    for source in sources:
        src_files = source / FILES_DIRNAME
        if not src_files.is_dir():
            continue
        for entry in sorted(src_files.iterdir()):
            if not entry.is_file():
                warnings.append(f"left {entry} in place: not a regular file")
                continue
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / entry.name
            try:
                if not dest.exists():
                    os.replace(entry, dest)
                elif _same_file(entry, dest):
                    entry.unlink()
                else:  # same name, different content: keep both
                    os.replace(entry, _suffixed(dest_dir, entry.name))
            except OSError as exc:
                warnings.append(f"could not move {entry}: {exc.strerror or exc}")

    newest = max(records, key=_newest_time, default=None)
    state["newest_message_id"] = newest["id"] if newest else None
    state["newest_created_time"] = _newest_time(newest) if newest else None
    known = {r["id"] for r in records}
    state["failed_files"] = {k: v for k, v in state["failed_files"].items() if k in known}
    write_jsonl(target / JSONL_NAME, records)
    write_json(target / STATE_NAME, state)

    for source in sources:
        if source not in mergeable:
            warnings.append(f"kept {source.name}: it has unreadable lines that were not merged")
            continue
        for name in (JSONL_NAME, MD_NAME, STATE_NAME):
            try:
                (source / name).unlink(missing_ok=True)
            except OSError as exc:
                warnings.append(f"could not remove {source / name}: {exc.strerror or exc}")
        for folder in (source / FILES_DIRNAME, source):
            try:
                folder.rmdir()  # only succeeds when empty
            except OSError:
                if folder.exists():
                    warnings.append(f"kept {folder}: it still has content after the merge")
                    break
    return target, warnings
