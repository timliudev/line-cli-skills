#!/usr/bin/env python3
"""thread - who replied to or reacted to one archived message. Offline: reads messages.jsonl only.

  thread.py CHAT_DIR_OR_ID --message ID [--depth N]
  thread.py CHAT_DIR_OR_ID --text SUBSTRING [--from NAME] [--depth N]

CHAT_DIR_OR_ID is an archive folder (holding messages.jsonl) or a full chat ID (`id:` prefix
allowed), which is looked up under archive_root. Run `sync.py` first so the archive is current.
Replies are the messages whose `reply_to` is the target's ID; reactions come from the target's
`reactions`. Only messages that are in the archive can be found.

Exit codes
  0  success
  1  the archive could not be read
  2  usage error
  3  the target is ambiguous: "candidates" lists them
  4  no archive, or no message matched
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

if sys.version_info < (3, 11):  # noqa: UP036 - friendly message instead of an ImportError
    sys.exit("This script needs Python 3.11 or newer (found " + sys.version.split()[0] + ").")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from linecore import emoji as emoji_mod  # noqa: E402
from linecore import names as names_mod  # noqa: E402
from linecore import runner, store  # noqa: E402
from linecore.config import load_config_safe  # noqa: E402
from linecore.media import LABEL, kind_of  # noqa: E402
from linecore.render import body_text, reaction_label, short_mid  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_DECIDE, EXIT_NOT_FOUND = 0, 1, 2, 3, 4
MAX_CANDIDATES = 20


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold().strip()


def _ms(value: Any) -> int:
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _when(ms: int) -> str | None:
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M") if ms else None


def _sender(mid: Any, names: dict[str, str]) -> str:
    if not isinstance(mid, str) or not mid:
        return "(unknown)"
    return names.get(mid) or short_mid(mid)


def first_line(rec: dict[str, Any]) -> str:
    kind = kind_of(rec)
    if kind:
        return f"[{LABEL[kind]}]"
    for line in body_text(rec, {}, {}, "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _brief(rec: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    return {
        "id": rec["id"],
        "time": _when(_ms(rec.get("created_time"))),
        "sender": _sender(rec.get("from"), names),
        "first_line": first_line(rec),
    }


def build_replies(
    target_id: str, records: list[dict[str, Any]], names: dict[str, str], depth: int
) -> list[dict[str, Any]]:
    out = []
    for rec in sorted(records, key=lambda r: _ms(r.get("created_time"))):
        if rec.get("reply_to") != target_id:
            continue
        brief = _brief(rec, names)
        item: dict[str, Any] = {
            "id": brief["id"],
            "time": brief["time"],
            "sender": brief["sender"],
            "text_first_line": brief["first_line"],
        }
        if depth > 1:
            item["replies"] = build_replies(rec["id"], records, names, depth - 1)
        out.append(item)
    return out


def build_reactions(
    rec: dict[str, Any],
    names: dict[str, str],
    emoji: dict[str, Any] | None = None,
    emoji_prefix: str = "",
) -> list[dict[str, Any]]:
    out = []
    items = rec.get("reactions")
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            out.append(
                {
                    "type_label": reaction_label(item.get("reactionType"), emoji, emoji_prefix),
                    "sender": _sender(item.get("fromUserMid"), names),
                    "time": _when(_ms(item.get("atMillis"))),
                }
            )
    return out


def find_candidates(
    records: list[dict[str, Any]], names: dict[str, str], text: str, sender: str | None
) -> list[dict[str, Any]]:
    needle, who = _norm(text), _norm(sender or "")
    found = []
    for rec in records:
        body = rec.get("text")
        if not isinstance(body, str) or needle not in _norm(body):
            continue
        if who and who not in _norm(_sender(rec.get("from"), names)):
            continue
        found.append(rec)
    return found


def resolve_dir(chat: str) -> tuple[Path | None, str | None]:
    """Return (archive folder, note). A folder wins; otherwise treat CHAT as a chat ID."""
    path = Path(chat)
    if path.is_dir():
        return path, None
    from lineq import FULL_ID  # same folder; imported late to keep --help fast

    bare = chat[3:] if chat.startswith("id:") else chat
    if not FULL_ID.match(bare):
        return None, f"{chat!r} is neither an archive folder nor a full chat ID"
    config, error = load_config_safe()
    if error:
        runner.log(f"warning: {error}")
    return store.find_chat_dir(config.archive_root, bare), None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="thread.py",
        description="List the replies and reactions of one archived LINE message (offline).",
        epilog="Exit codes: 0 ok, 1 archive unreadable, 2 usage, 3 ambiguous target, 4 not found.",
    )
    parser.add_argument("chat", metavar="CHAT_DIR_OR_ID", help="archive folder or full chat ID")
    pick = parser.add_mutually_exclusive_group(required=True)
    pick.add_argument("--message", metavar="ID", help="target message ID")
    pick.add_argument("--text", metavar="SUBSTRING", help="find the target by its text")
    parser.add_argument("--from", dest="sender", metavar="NAME", help="with --text: sender name")
    parser.add_argument("--depth", type=int, default=1, help="reply levels to follow (default 1)")
    return parser


def main(argv: list[str] | None = None) -> int:
    runner.configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.depth < 1:
        parser.error("--depth must be 1 or more")
    if args.sender and args.text is None:
        parser.error("--from only works together with --text")

    folder, note = resolve_dir(args.chat)
    if folder is None:
        runner.emit({"status": "not_found", "message": note or "no archive for this chat yet"})
        return EXIT_NOT_FOUND
    jsonl = folder / store.JSONL_NAME
    if not jsonl.is_file():
        runner.emit({"status": "not_found", "message": f"no {store.JSONL_NAME} in {folder}"})
        return EXIT_NOT_FOUND
    try:
        records, warnings = store.read_jsonl(jsonl)
    except OSError as exc:
        runner.emit({"error": {"kind": "archive_unreadable", "message": str(exc)}})
        return EXIT_FAIL

    config, _ = load_config_safe()
    cache = names_mod.load_cache(config.archive_root)
    if not cache:
        cache = names_mod.load_cache(folder.parent)
    names = names_mod.name_map(cache)
    self_mid = store.read_json(folder / store.STATE_NAME, {}).get("self_mid")
    if isinstance(self_mid, str) and self_mid:
        names[self_mid] = "Me"  # recorded by sync.py; no live whoami call needed

    if args.message is not None:
        target = next((r for r in records if r["id"] == args.message), None)
        if target is None:
            runner.emit({"status": "not_found", "message": "that message is not in the archive"})
            return EXIT_NOT_FOUND
    else:
        matches = find_candidates(records, names, args.text, args.sender)
        if not matches:
            runner.emit({"status": "not_found", "message": "no archived message matched"})
            return EXIT_NOT_FOUND
        if len(matches) > 1:
            runner.emit(
                {
                    "status": "ambiguous",
                    "total": len(matches),
                    "candidates": [_brief(r, names) for r in matches[-MAX_CANDIDATES:]],
                }
            )
            return EXIT_DECIDE
        target = matches[0]

    prefix = emoji_mod.emoji_dir(config.archive_root).as_posix() + "/"
    emoji_info = emoji_mod.render_info(
        config.archive_root, emoji_mod.load_index(config.archive_root)
    )
    result: dict[str, Any] = {
        "target": _brief(target, names),
        "replies": build_replies(target["id"], records, names, args.depth),
        "reactions": build_reactions(target, names, emoji_info, prefix),
    }
    if warnings:
        result["warnings"] = warnings
    runner.emit(result)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
