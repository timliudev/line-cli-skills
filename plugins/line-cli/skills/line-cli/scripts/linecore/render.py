"""messages.jsonl records -> messages.md (stdlib only).

The markdown is derived data: it can always be regenerated from messages.jsonl, the cached
contact names and the failure list in state.json.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import Any

from .media import LABEL, kind_of
from .store import safe_id

SELF_LABEL = "Me"
REACTIONS = {2: "like", 3: "love", 4: "laugh", 5: "surprise", 6: "sad", 7: "angry"}
UNSUPPORTED = {7: "Sticker", 13: "Contact card", 22: "Rich message"}
POSSIBLY_UNSENT = "(possibly unsent)"
GAP_TEXT = "⚠ Possible gap: messages between the previous ones and here may be missing."
FAILURE_TEXT = {
    "expired": "expired (no longer available on LINE)",
    "external": "external link, cannot be downloaded",
    "processing": "still processing on LINE, will retry on the next sync",
}


@dataclass
class Rendered:
    text: str
    first_line: dict[str, int] = field(default_factory=dict)  # message id -> 1-based line


def human_bytes(count: int) -> str:
    if count >= 1024 * 1024:
        value = count / (1024 * 1024)
        return f"{value:.0f} MiB" if value == int(value) else f"{value:.1f} MiB"
    if count >= 1024:
        return f"{count / 1024:.0f} KiB"
    return f"{count} B"


def short_mid(mid: str) -> str:
    return mid if len(mid) <= 12 else f"{mid[:6]}...{mid[-4:]}"


def sender_label(mid: Any, names: Mapping[str, str], self_mid: str | None) -> str:
    if not isinstance(mid, str) or not mid:
        return "(unknown)"
    if self_mid and mid == self_mid:
        return SELF_LABEL
    return names.get(mid) or short_mid(mid)


def _clean(text: str) -> str:
    return text.replace("[", "(").replace("]", ")")


def _snippet(text: str, limit: int = 40) -> str:
    one = " ".join(text.split())
    return one if len(one) <= limit else one[: limit - 3] + "..."


def failure_text(failure: Mapping[str, Any]) -> str:
    reason = failure.get("reason")
    if reason == "too_large":
        limit = failure.get("limit")
        return (
            f"over {human_bytes(limit)}, not downloaded"
            if isinstance(limit, int)
            else ("too large, not downloaded")
        )
    if reason in FAILURE_TEXT:
        return FAILURE_TEXT[reason]
    return f"download failed ({reason or 'error'})"


def attachment_text(
    record: Mapping[str, Any],
    files: Mapping[str, str],
    failures: Mapping[str, Mapping[str, Any]],
    link_prefix: str,
) -> str:
    kind = kind_of(record) or "file"
    label = LABEL[kind]
    if kind == "file" and isinstance(record.get("file_name"), str) and record["file_name"]:
        label = f"File: {_clean(record['file_name'])}"
    stored = files.get(safe_id(record["id"]))
    if stored:
        return f"[{label}]({link_prefix}{stored})"
    failure = failures.get(record["id"])
    if failure:
        return f"[{label}] {failure_text(failure)}"
    return f"[{label}] not downloaded"


def body_text(
    record: Mapping[str, Any],
    files: Mapping[str, str],
    failures: Mapping[str, Mapping[str, Any]],
    link_prefix: str,
) -> str:
    status = record.get("status")
    if kind_of(record):
        return attachment_text(record, files, failures, link_prefix)
    if status == "decryption_failed":
        detail = record.get("error")
        return f"[could not decrypt: {detail}]" if detail else "[could not decrypt]"
    if status == "unsupported":
        label = UNSUPPORTED.get(record.get("content_type"))
        return f"[{label}]" if label else f"[Unsupported message type {record.get('content_type')}]"
    text = record.get("text")
    return text if isinstance(text, str) and text else "(empty message)"


def _reactions(record: Mapping[str, Any], names: Mapping[str, str], self_mid: str | None) -> str:
    items = record.get("reactions")
    if not isinstance(items, list) or not items:
        return ""
    parts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rtype = item.get("reactionType") or {}
        code = rtype.get("predefinedReactionType") if isinstance(rtype, dict) else None
        label = REACTIONS.get(code, "reaction")
        parts.append(f"{label} ({sender_label(item.get('fromUserMid'), names, self_mid)})")
    return f"reactions: {', '.join(parts)}" if parts else ""


def _fmt_time(ms: Any, tz: tzinfo | None) -> datetime | None:
    if isinstance(ms, int) and not isinstance(ms, bool) and ms > 0:
        return datetime.fromtimestamp(ms / 1000, tz)
    return None


def render_markdown(
    records: Sequence[Mapping[str, Any]],
    *,
    chat_id: str,
    chat_name: str | None,
    names: Mapping[str, str],
    self_mid: str | None,
    files: Mapping[str, str],
    failures: Mapping[str, Mapping[str, Any]],
    gaps: Sequence[Mapping[str, Any]],
    synced_at: datetime,
    tz: tzinfo | None = None,
    link_prefix: str = "files/",
) -> Rendered:
    order = sorted(
        enumerate(records),
        key=lambda pair: (
            pair[1].get("created_time") if isinstance(pair[1].get("created_time"), int) else 0,
            pair[0],
        ),
    )
    ordered = [rec for _, rec in order]
    by_id = {r["id"]: r for r in ordered}
    gap_before = {g.get("before_id"): g for g in gaps if g.get("before_id")}

    lines: list[str] = []
    first_line: dict[str, int] = {}

    first = _fmt_time(ordered[0].get("created_time"), tz) if ordered else None
    last = _fmt_time(ordered[-1].get("created_time"), tz) if ordered else None
    zone = synced_at.astimezone(tz).strftime("UTC%z")
    zone = f"{zone[:6]}:{zone[6:]}" if len(zone) == 8 else zone

    lines.append(f"# {chat_name or chat_id}")
    lines.append("")
    lines.append(f"- Chat ID: `{chat_id}`")
    span = f" ({first:%Y-%m-%d} to {last:%Y-%m-%d})" if first and last else ""
    lines.append(f"- Messages: {len(ordered)}{span}")
    lines.append(f"- Last synced: {synced_at.astimezone(tz):%Y-%m-%d %H:%M} ({zone})")
    lines.append(f"- Times are local to the machine that synced ({zone}).")
    lines.append("")

    current_day: str | None = None
    for rec in ordered:
        when = _fmt_time(rec.get("created_time"), tz)
        day = f"{when:%Y-%m-%d}" if when else "Unknown date"
        gap = gap_before.get(rec["id"])
        if gap:
            if lines[-1] != "":
                lines.append("")
            lines.append(f"> {GAP_TEXT}")
            lines.append("")
        if day != current_day:
            current_day = day
            if lines[-1] != "":
                lines.append("")
            lines.append(f"## {day}")
            lines.append("")

        sender = sender_label(rec.get("from"), names, self_mid)
        clock = f"{when:%H:%M}" if when else "--:--"
        body = body_text(rec, files, failures, link_prefix)
        extras = []
        reply = rec.get("reply_to")
        if isinstance(reply, str) and reply:
            target = by_id.get(reply)
            if target and isinstance(target.get("text"), str) and target["text"]:
                who = sender_label(target.get("from"), names, self_mid)
                extras.append(f'in reply to {who}: "{_snippet(target["text"])}"')
            else:
                extras.append("in reply to an earlier message")
        reaction_text = _reactions(rec, names, self_mid)
        if reaction_text:
            extras.append(reaction_text)
        body_lines = body.split("\n")
        if rec.get("possibly_unsent_at"):
            body_lines[-1] += f" {POSSIBLY_UNSENT}"
        first_line[rec["id"]] = len(lines) + 1
        lines.append(f"- {clock} **{sender}**: {body_lines[0]}")
        lines.extend(f"  {extra_line}" if extra_line else "" for extra_line in body_lines[1:])
        for extra in extras:
            lines.append(f"  _({extra})_")

    return Rendered(text="\n".join(lines).rstrip("\n") + "\n", first_line=first_line)
