"""Merge keyframes and transcript segments into one timeline and render digest.md."""

from __future__ import annotations

from urllib.parse import quote


def format_ts(seconds: float | None) -> str:
    """m:ss, or h:mm:ss from one hour up."""
    if seconds is None:
        return "?"
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def build_timeline(frames: list[dict], segments: list[dict]) -> list[dict]:
    """Chronological entries. At equal times a frame comes before speech.

    frame entry:  {"t", "type": "frame", "path", "kind"}
    speech entry: {"t", "end", "type": "speech", "text"}
    """
    entries: list[dict] = []
    for f in frames:
        entries.append({"t": f["t"], "type": "frame", "path": f["path"], "kind": f.get("kind")})
    for s in segments:
        text = str(s.get("text", "")).strip()
        if not text:
            continue
        start = float(s.get("start", 0.0))
        entries.append({"t": start, "end": s.get("end"), "type": "speech", "text": text})
    entries.sort(key=lambda e: (e["t"], 0 if e["type"] == "frame" else 1))
    return entries


def _link(path: str) -> str:
    return quote(path.replace("\\", "/"), safe="/")


def _meta_line(item: dict) -> str:
    bits = [item.get("type", "?")]
    if item.get("duration"):
        bits.append(format_ts(item["duration"]))
    if item.get("width") and item.get("height"):
        bits.append(f"{item['width']}x{item['height']}")
    return ", ".join(bits)


def _escape_line(text: str) -> str:
    return " ".join(text.split())


def render_item(index: int, item: dict) -> str:
    name = item.get("name", "?")
    lines = [f"## {index}. {name} ({_meta_line(item)})", ""]
    lines.append(f"Source: `{item.get('source', '')}`")
    kind = item.get("type")
    if item.get("passthrough"):
        lines += ["", f"Read this file directly: `{item['passthrough']}`"]
    for note in item.get("notes", []):
        lines += ["", f"> Note: {note}"]
    tr = item.get("transcript") or {}
    if tr.get("status") == "ok":
        lang = tr.get("language") or "unknown"
        lines += ["", f"Transcript: {tr.get('adapter') or '?'}, language {lang}"]
    elif tr.get("status") and tr["status"] != "skipped" and kind in ("video", "audio"):
        detail = f" - {tr['error']}" if tr.get("error") else ""
        lines += ["", f"Transcript: {tr['status']}{detail}"]
    if kind in ("video", "audio"):
        timeline = item.get("timeline") or []
        lines += ["", "### Timeline", ""]
        if not timeline:
            lines.append("(nothing to show)")
        in_speech = False
        for entry in timeline:
            if entry["type"] == "frame":
                if in_speech:
                    lines.append("")
                    in_speech = False
                lines += [f"**[{format_ts(entry['t'])}]** ![frame at {format_ts(entry['t'])}]"
                          f"({_link(entry['path'])})", ""]
            else:
                lines.append(f"> [{format_ts(entry['t'])}] {_escape_line(entry['text'])}")
                in_speech = True
        if in_speech:
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_digest(
    items: list[dict], needs: str | None = None, install_hint: str | None = None
) -> str:
    """Whole digest.md. Image links are relative to the digest.md location."""
    parts = ["# Media digest", ""]
    if needs:
        parts += [
            f"> Missing dependency: **{needs}**. Install it with `{install_hint}`, then re-run.",
            "",
        ]
    for i, item in enumerate(items, 1):
        parts += [render_item(i, item)]
    return "\n".join(parts).rstrip() + "\n"
