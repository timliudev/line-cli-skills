"""Shared cache of LINE emoji (sticon) images used in reactions (stdlib only).

Layout: ``<archive_root>/_line_emoji/<productId>/<emojiId>.png`` plus ``_line_emoji/index.json``
(per emoji: ``file`` once cached, an optional short ``label``, and ``last_attempt``/``error`` after
a failed download). One image per unique emoji, shared by every chat, never per use.
The URL pattern is unofficial (community API spec), so a failure is normal and just backs off.
"""

from __future__ import annotations

import re
import urllib.request
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .store import read_json, write_json

DIRNAME = "_line_emoji"
INDEX_NAME = "index.json"
URL = "https://stickershop.line-scdn.net/sticonshop/v1/sticon/{product}/android/{emoji}.png"
MAX_BYTES = 1024 * 1024
TIMEOUT = 10
RETRY_AFTER = timedelta(days=7)
LABEL_LIMIT = 40
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PRODUCT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_EMOJI_ID = re.compile(r"^\d{3}$")


def valid(product: Any, emoji_id: Any) -> bool:
    return (
        isinstance(product, str)
        and isinstance(emoji_id, str)
        and bool(_PRODUCT.match(product))
        and bool(_EMOJI_ID.match(emoji_id))
    )


def key_of(product: str, emoji_id: str) -> str:
    return f"{product}/{emoji_id}"


def split_key(key: str) -> tuple[str, str] | None:
    product, _, emoji_id = key.partition("/")
    return (product, emoji_id) if valid(product, emoji_id) else None


def emoji_dir(archive_root: Path) -> Path:
    return archive_root / DIRNAME


def load_index(archive_root: Path) -> dict[str, dict[str, Any]]:
    raw = read_json(emoji_dir(archive_root) / INDEX_NAME, {})
    items = raw.get("emoji") if isinstance(raw, dict) else None
    if not isinstance(items, dict):
        return {}
    return {k: v for k, v in items.items() if isinstance(k, str) and isinstance(v, dict)}


def save_index(archive_root: Path, index: Mapping[str, Mapping[str, Any]]) -> None:
    write_json(emoji_dir(archive_root) / INDEX_NAME, {"schema": 1, "emoji": dict(index)})


def keys_in(records: Iterable[Mapping[str, Any]]) -> list[str]:
    """Every distinct, well-formed LINE emoji reaction in the records."""
    found: dict[str, None] = {}
    for rec in records:
        for item in rec.get("reactions") or []:
            rtype = item.get("reactionType") if isinstance(item, dict) else None
            paid = rtype.get("paidReactionType") if isinstance(rtype, dict) else None
            if isinstance(paid, dict) and valid(paid.get("productId"), paid.get("emojiId")):
                found[key_of(paid["productId"], paid["emojiId"])] = None
    return list(found)


def fetch_bytes(url: str) -> bytes:
    """Download one image (at most MAX_BYTES). Tests replace this function."""
    request = urllib.request.Request(url, headers={"User-Agent": "line-cli-skill"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - fixed https host
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("image larger than 1 MiB")
    return data


def cached_path(archive_root: Path, entry: Mapping[str, Any]) -> Path | None:
    name = entry.get("file")
    if not isinstance(name, str) or not name:
        return None
    path = emoji_dir(archive_root) / name
    return path if path.is_file() else None


def ensure(
    archive_root: Path, keys: Iterable[str], *, now: datetime, download: bool = True
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Make sure each emoji is cached when allowed. Returns (index, warnings)."""
    index = load_index(archive_root)
    warnings: list[str] = []
    changed = False
    failed = 0
    stamp = now.isoformat(timespec="seconds")
    for key in keys:
        parts = split_key(key)
        if not parts or not download:
            continue
        entry = index.get(key, {})
        if cached_path(archive_root, entry):
            continue
        last = entry.get("last_attempt")
        if isinstance(last, str) and entry.get("error"):
            try:
                if now - datetime.fromisoformat(last) < RETRY_AFTER:
                    continue
            except (ValueError, TypeError):
                pass
        rel = f"{parts[0]}/{parts[1]}.png"
        entry = dict(entry)
        try:
            data = fetch_bytes(URL.format(product=parts[0], emoji=parts[1]))
            if not data.startswith(PNG_MAGIC):
                raise ValueError("not a PNG image")
            target = emoji_dir(archive_root) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
            entry["file"] = rel
            entry.pop("error", None)
            entry.pop("last_attempt", None)
        except (OSError, ValueError) as exc:
            entry["error"] = str(exc)[:120]
            entry["last_attempt"] = stamp
            failed += 1
        index[key] = entry
        changed = True
    if changed:
        try:
            save_index(archive_root, index)
        except OSError as exc:
            warnings.append(f"could not save the LINE emoji index: {exc.strerror or exc}")
    if failed:
        warnings.append(f"could not download {failed} LINE emoji image(s); will retry in 7 days")
    return index, warnings


def clean_label(text: str) -> str:
    one = " ".join(str(text).split())
    one = re.sub(r"[\"\[\]()]", "", one).strip()
    return one[:LABEL_LIMIT].strip()


def set_label(archive_root: Path, key: str, label: str) -> bool:
    """Store a label for a known emoji. False when the emoji is not in the index."""
    index = load_index(archive_root)
    if key not in index:
        return False
    cleaned = clean_label(label)
    if cleaned:
        index[key]["label"] = cleaned
    else:
        index[key].pop("label", None)
    save_index(archive_root, index)
    return True


def render_info(archive_root: Path, index: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """The lookup render.reaction_label uses: key -> {label, file (relative to the cache)}."""
    out: dict[str, Any] = {}
    for key, entry in index.items():
        label = entry.get("label")
        info = {
            "label": label if isinstance(label, str) and label else None,
            "file": entry["file"] if cached_path(archive_root, entry) else None,
        }
        if info["label"] or info["file"]:
            out[key] = info
    return out
