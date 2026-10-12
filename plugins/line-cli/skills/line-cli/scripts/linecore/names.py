"""Sender display names via `line contacts --mid`, cached in <archive_root>/contacts.json."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import runner
from .config import Config
from .store import CONTACTS_NAME, read_json, write_json

TTL_RESOLVED_MS = 7 * 24 * 3600 * 1000  # names change rarely
TTL_EMPTY_MS = 24 * 3600 * 1000  # unavailable / nameless: look again after a day
CHUNK = 40


def cache_path(archive_root: Path) -> Path:
    return archive_root / CONTACTS_NAME


def load_cache(archive_root: Path) -> dict[str, dict[str, Any]]:
    raw = read_json(cache_path(archive_root), {})
    names = raw.get("names") if isinstance(raw, dict) else None
    if not isinstance(names, dict):
        return {}
    return {k: v for k, v in names.items() if isinstance(k, str) and isinstance(v, dict)}


def save_cache(archive_root: Path, cache: dict[str, dict[str, Any]]) -> None:
    write_json(cache_path(archive_root), {"schema": 1, "names": cache})


def _stale(entry: dict[str, Any] | None, now_ms: int) -> bool:
    if not entry:
        return True
    age = now_ms - int(entry.get("ts") or 0)
    return age > (TTL_RESOLVED_MS if entry.get("name") else TTL_EMPTY_MS)


def effective_name(row: dict[str, Any]) -> str:
    for key in ("effectiveDisplayName", "displayNameOverridden", "displayName"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def refresh_names(
    mids: Iterable[str],
    cache: dict[str, dict[str, Any]],
    *,
    now_ms: int,
    config: Config | None = None,
    binary: str | None = None,
) -> tuple[bool, list[str]]:
    """Look up MIDs that are missing or stale. Returns (cache_changed, warnings)."""
    wanted = (m for m in dict.fromkeys(mids) if isinstance(m, str) and m)
    need = [m for m in wanted if _stale(cache.get(m), now_ms)]
    changed = False
    warnings: list[str] = []
    for start in range(0, len(need), CHUNK):
        chunk = need[start : start + CHUNK]
        argv = ["contacts"]
        for mid in chunk:
            argv += ["--mid", mid]
        argv.append("--json")
        result = runner.run_line(argv, config=config, binary=binary)
        if not result.ok or not isinstance(result.data, list):
            kind = result.error.kind if result.error else "error"
            warnings.append(f"could not look up {len(chunk)} sender name(s) ({kind})")
            continue
        rows = {r.get("mid"): r for r in result.data if isinstance(r, dict)}
        for mid in chunk:
            row = rows.get(mid)
            name = effective_name(row) if row and row.get("status") != "unavailable" else ""
            cache[mid] = {"name": name, "ts": now_ms}
            changed = True
    return changed, warnings


def name_map(cache: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {mid: entry.get("name") or "" for mid, entry in cache.items()}


def mids_in(records: Iterable[dict[str, Any]]) -> list[str]:
    """Every sender and reactor MID mentioned in the records."""
    out: list[str] = []
    for rec in records:
        sender = rec.get("from")
        if isinstance(sender, str) and sender:
            out.append(sender)
        for item in rec.get("reactions") or []:
            if isinstance(item, dict) and isinstance(item.get("fromUserMid"), str):
                out.append(item["fromUserMid"])
    return out
