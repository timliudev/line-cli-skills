"""Parse `line chats --limit 0 --show-ids` (text) into rows; used when `chats --search` fails.

Upstream `chats --search` names every chat, including inactive ones, in batches, and one failing
batch fails the whole call. The plain listing only covers active chats but is reliable.

Row layout (the ID sits on the next, indented line)::

    UPDATED           UNREAD  TYPE     CHAT
    2026-10-12 12:05       -  Group    Some name
                                        C0123...
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from . import runner
from .config import Config

UNNAMED = "(name unavailable)"
FALLBACK_WARNING = "searched active chats only (line chats --search failed upstream)"
LIST_ARGV = ["chats", "--limit", "0", "--show-ids"]

_ROW = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+(\S+)\s+(Direct|Group|Room)\b[ \t]*(.*?)\s*$"
)
_ID = re.compile(r"^\s+([A-Za-z0-9_-]{20,})\s*$")
_KIND = {"Direct": "direct", "Group": "group", "Room": "room"}


def parse_chat_list(text: str) -> list[dict[str, Any]]:
    """Rows ``{id, name, type, updated, unread}``; header/footer/unparseable lines are skipped.

    A row whose ID line is missing is dropped (no ID, nothing to act on). A blank name becomes
    ``(name unavailable)``.
    """
    rows: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    for raw in (text or "").splitlines():
        line = raw.rstrip("\r\n")
        match = _ROW.match(line)
        if match:  # a new row; a previous row still waiting for its ID is dropped
            updated, unread, kind, name = match.groups()
            pending = {
                "name": name.strip() or UNNAMED,
                "type": _KIND[kind],
                "updated": updated,
                "unread": None if not unread.isdigit() else int(unread),
            }
            continue
        id_match = _ID.match(line)
        if id_match and pending is not None:
            rows.append({"id": id_match.group(1), **pending})
            pending = None
    return rows


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold().strip()


def search_rows(rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    wanted = _norm(query)
    return [r for r in rows if wanted and wanted in _norm(r["name"])]


def fetch_active_chats(
    *, config: Config | None = None, binary: str | None = None
) -> tuple[list[dict[str, Any]], runner.LineResult]:
    result = runner.run_line(LIST_ARGV, config=config, binary=binary)
    rows = parse_chat_list(result.text or "") if result.ok else []
    return rows, result


def named_only(rows: list[dict[str, Any]]) -> dict[str, str]:
    """``{chat_id: name}`` for rows that have a real name."""
    return {r["id"]: r["name"] for r in rows if r["name"] != UNNAMED}
