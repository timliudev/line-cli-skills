#!/usr/bin/env python3
"""broadcast - send one message to many LINE chats, safely (dry run by default).

Usage
  broadcast.py --recipients FILE (--text TEXT | --stdin) [--file PATH]          # dry run
  broadcast.py --recipients FILE (--text TEXT | --stdin) [--file PATH] --yes --plan HASH
  (--plan is the `plan` value of the dry run and is required with --yes)

FILE has one chat name or full chat ID per line; blank lines and lines starting with `#` are
ignored. The message is TEXT (or stdin), optionally followed by a file attachment: LINE's `send`
takes either text or a file, so with both the text goes first and the file second.

Without --yes nothing is sent: every recipient is resolved and the result is printed. A recipient
only counts as resolved with an exact name match from a complete lookup or a full chat ID; partial
names, incomplete lookups (use the full ID), several matches and no match are listed under
`unresolved` and the exit code is not 0. With --yes the messages are sent one after another with a
pause between recipients. The first failure stops the run, nothing is ever retried, and every
recipient is reported as sent, failed or not_attempted.

Exit codes
  0  dry run: all recipients resolved / --yes: everything was sent
  1  a send failed (see `results`; later recipients were not attempted), or `line` could not look
     a recipient up
  2  usage error (bad file, empty or too long text, ...)
  3  needs the user: ambiguous, inexact or incomplete recipients, or the plan changed since the
     dry run
  4  every unresolved recipient matched nothing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if sys.version_info < (3, 11):  # noqa: UP036 - friendly message instead of an ImportError
    sys.exit("This script needs Python 3.11 or newer (found " + sys.version.split()[0] + ").")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from linecore import runner  # noqa: E402
from linecore.config import Config, load_config_safe  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_DECIDE, EXIT_NOT_FOUND = 0, 1, 2, 3, 4

DEFAULT_DELAY = 3.0  # seconds between recipients; keeps clear of LINE's rate limits
DEFAULT_MAX_RECIPIENTS = 50
MAX_TEXT_UNITS = 10_000  # upstream limit, in UTF-16 code units
MAX_FILE_BYTES = 20 * 1024 * 1024  # upstream attachment cap

# A failed send of one of these kinds provably did not go out; any other failure (timeout, network,
# an unknown error, a cancelled process) may have been delivered before the answer was lost.
NOTHING_SENT = {
    "busy",
    "not_logged_in",
    "session_replaced",
    "binary_missing",
    "usage",
    "message_too_long",
    "ambiguous_name",
    "name_not_found",
    "watcher_running",
}

_sleep = time.sleep  # indirection so tests can run without waiting


# ---------------------------------------------------------------- input


def read_recipients(path: Path) -> list[str]:
    """Names / IDs from the list file, in order. Blank lines and `#` comment lines are skipped."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    out: list[str] = []
    for raw in lines:
        entry = raw.strip()
        if entry and not entry.startswith("#"):
            out.append(entry)
    return out


def utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


@dataclass(frozen=True)
class Message:
    text: str | None
    file: Path | None

    def steps(self) -> list[str]:
        return [name for name, present in (("text", self.text), ("file", self.file)) if present]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def plan_hash(chat_ids: list[str], message: Message) -> str:
    """Fingerprint of exactly what a --yes run would send and to whom (order matters).

    An attachment counts by path, size and the SHA-256 of its bytes, so a file edited after the
    dry run changes the plan.
    """
    payload = {
        "ids": chat_ids,
        "text": message.text,
        "file": str(message.file) if message.file else None,
        "file_size": message.file.stat().st_size if message.file else None,
        "file_sha256": file_sha256(message.file) if message.file else None,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:12]


# ---------------------------------------------------------------- resolving


def resolve_recipients(entries: list[str], config: Config) -> dict[str, Any]:
    """Resolve every entry with lineq's chat resolution.

    Returns ``{resolved: [...], unresolved: [...], duplicates: [...]}``. Stricter than lineq for a
    bulk send: only an exact name match from a complete lookup, or a full ID, is accepted. A lone
    partial match is ``inexact``; a name whose lookup was incomplete (one of the searches failed, so
    a second chat with that name may be hiding) is ``incomplete``: the user fixes the list instead
    of trusting a guess, and a full chat ID always resolves.
    """
    from lineq import resolve_name  # same folder; imported late to keep --help fast

    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for entry in entries:
        payload, _code = resolve_name(entry, config=config)
        status = payload.get("status")
        incomplete = status == "resolved" and bool(payload.get("incomplete"))
        if status == "resolved" and payload.get("matched_by") != "substring" and not incomplete:
            chat_id = payload["id"]
            if chat_id in seen:
                duplicates.append({"query": entry, "id": chat_id, "same_as": seen[chat_id]})
                continue
            seen[chat_id] = entry
            row: dict[str, Any] = {
                "query": entry,
                "id": chat_id,
                "name": payload.get("name") or None,
                "type": payload.get("type"),
            }
            resolved.append(row)
            continue
        if incomplete:  # a search failed: another chat with this exact name could be hiding
            kind = "incomplete"
        elif status == "resolved":  # a single partial-name hit: not good enough for a bulk send
            kind = "inexact"
        else:
            kinds = {"ambiguous": "ambiguous", "candidates": "inexact", "not_found": "not_found"}
            kind = kinds.get(status or "", "error")
        item: dict[str, Any] = {"query": entry, "status": kind}
        candidates = [
            {"id": c["id"], "name": c.get("name"), "type": c.get("type")}
            for c in payload.get("candidates", [])
        ]
        if candidates:
            item["candidates"] = candidates
        if payload.get("error"):
            item["error"] = payload["error"]
        if kind == "incomplete":
            item["message"] = (
                "The chat search was incomplete (a lookup failed), so another chat with this name "
                "could exist. Use the full chat ID of the intended chat (see candidates)."
            )
        unresolved.append(item)
    return {"resolved": resolved, "unresolved": unresolved, "duplicates": duplicates}


# ---------------------------------------------------------------- sending


def _send_one(chat_id: str, message: Message, config: Config) -> dict[str, Any]:
    """Send text then file to one chat. Never retries. Returns ``{status, ...}``."""
    delivered: list[str] = []
    for step in message.steps():
        if step == "text":
            result = runner.run_line(
                ["send", chat_id, "--stdin", "--json"], stdin_text=message.text, config=config
            )
        else:
            result = runner.run_line(
                ["send", chat_id, "--file", str(message.file), "--json"], config=config
            )
        if not result.ok:
            error = result.error.to_dict() if result.error else {"kind": "error"}
            out: dict[str, Any] = {
                "status": "failed",
                "delivered": delivered,
                "failed_step": step,
                "error": error,
                "may_have_been_delivered": error.get("kind") not in NOTHING_SENT,
            }
            return out
        delivered.append(step)
    return {"status": "sent", "delivered": delivered}


def send_all(
    recipients: list[dict[str, Any]], message: Message, config: Config, *, delay: float
) -> list[dict[str, Any]]:
    """Sequential send; stops at the first failure; the rest are ``not_attempted``."""
    results: list[dict[str, Any]] = []
    stopped = False
    for index, rec in enumerate(recipients):
        base = {"query": rec["query"], "id": rec["id"], "name": rec.get("name")}
        if stopped:
            results.append({**base, "status": "not_attempted"})
            continue
        if index and delay > 0:
            _sleep(delay)
        outcome = _send_one(rec["id"], message, config)
        results.append({**base, **outcome})
        if outcome["status"] != "sent":
            stopped = True
            runner.log(
                f"stopped: sending to recipient {index + 1} of {len(recipients)} failed "
                f"({outcome['error'].get('kind')}); the rest were not attempted"
            )
    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"sent": 0, "failed": 0, "not_attempted": 0}
    for row in results:
        counts[row["status"]] += 1
    return counts


# ---------------------------------------------------------------- command line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="broadcast.py",
        description="Send one message to many LINE chats. Dry run unless --yes is given.",
        epilog="Exit codes: 0 ok, 1 send failed, 2 usage, 3 needs the user, 4 nothing matched.",
    )
    parser.add_argument(
        "--recipients",
        required=True,
        metavar="FILE",
        help="file with one chat name or full chat ID per line (# comments, blank lines ignored)",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--text", help="message text")
    source.add_argument("--stdin", action="store_true", help="read the message text from stdin")
    parser.add_argument("--file", metavar="PATH", help="also send this file (up to 20 MiB)")
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        metavar="SECONDS",
        help=f"pause between recipients (default {DEFAULT_DELAY:g})",
    )
    parser.add_argument(
        "--max-recipients",
        type=int,
        default=DEFAULT_MAX_RECIPIENTS,
        metavar="N",
        help=f"refuse longer lists (default {DEFAULT_MAX_RECIPIENTS})",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="the user approved this exact list and text: actually send",
    )
    parser.add_argument(
        "--plan",
        metavar="HASH",
        help="`plan` value from the dry run; REQUIRED with --yes (refused if anything changed)",
    )
    parser.add_argument("--json", action="store_true", help="accepted for symmetry; always JSON")
    return parser


def _usage_error(message: str) -> int:
    runner.emit({"status": "error", "error": {"kind": "usage", "message": message}})
    return EXIT_USAGE


def _load_message(args: argparse.Namespace) -> tuple[Message | None, str | None]:
    text: str | None = args.text
    if args.stdin:
        text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    if text is not None:
        text = text.rstrip("\r\n")  # a heredoc adds a final newline; it is not part of the message
        if not text.strip():
            return None, "the message text is empty"
        if utf16_units(text) > MAX_TEXT_UNITS:
            return None, f"the text is longer than {MAX_TEXT_UNITS} UTF-16 units (LINE's limit)"
    file: Path | None = None
    if args.file:
        file = Path(args.file).expanduser()
        if not file.is_file():
            return None, f"--file {args.file}: not a file"
        if file.stat().st_size > MAX_FILE_BYTES:
            return None, "the file is larger than 20 MiB (the CLI's limit)"
        file = file.resolve()
    if text is None and file is None:
        return None, "give the message with --text or --stdin (and/or --file)"
    return Message(text, file), None


def main(argv: list[str] | None = None) -> int:
    runner.configure_stdio()
    args = build_parser().parse_args(argv)
    if args.delay < 0 or args.max_recipients < 1:
        return _usage_error("--delay must be >= 0 and --max-recipients >= 1")
    if args.yes and not args.plan:
        return _usage_error(
            "--yes needs --plan HASH: run the dry run first, show it to the user, and pass the "
            "`plan` value of that dry run"
        )

    list_path = Path(args.recipients).expanduser()
    try:
        entries = read_recipients(list_path)
    except (OSError, UnicodeDecodeError) as exc:
        return _usage_error(f"cannot read the recipient list {list_path}: {exc}")
    if not entries:
        return _usage_error("the recipient list is empty")
    if len(entries) > args.max_recipients:
        return _usage_error(
            f"{len(entries)} recipients is more than --max-recipients {args.max_recipients}; "
            "sending to many chats at once can get an account restricted. Split the list or "
            "raise the limit on purpose."
        )
    message, problem = _load_message(args)
    if message is None:
        return _usage_error(problem or "no message")

    config, config_error = load_config_safe()
    if config_error:
        runner.log(f"warning: {config_error}")

    found = resolve_recipients(entries, config)
    resolved, unresolved = found["resolved"], found["unresolved"]
    ids = [r["id"] for r in resolved]
    plan = plan_hash(ids, message)
    report: dict[str, Any] = {
        "mode": "send" if args.yes else "dry_run",
        "text": message.text,
        "file": (
            {"path": str(message.file), "bytes": message.file.stat().st_size}
            if message.file
            else None
        ),
        "steps_per_recipient": message.steps(),
        "delay_seconds": args.delay,
        "recipients": resolved,
        "unresolved": unresolved,
        "duplicates": found["duplicates"],
        "plan": plan,
    }

    if unresolved:
        report["sent"] = False
        report["message"] = (
            "Some recipients are not resolved, so nothing was sent. Ask the user to fix the list "
            "(use an exact name or the full chat ID), then dry-run again."
        )
        runner.emit(report)
        kinds = {u["status"] for u in unresolved}
        if "error" in kinds:  # `line` itself failed while looking up (not logged in, offline...)
            return EXIT_FAIL
        return EXIT_NOT_FOUND if kinds == {"not_found"} else EXIT_DECIDE
    if not resolved:
        return _usage_error("no recipients")

    if not args.yes:
        report["sent"] = False
        report["message"] = (
            "Dry run: nothing was sent. Show the user every recipient and the exact text, and "
            f"only after an explicit yes run again with --yes --plan {plan}."
        )
        runner.emit(report)
        return EXIT_OK

    if args.plan != plan:
        report["sent"] = False
        report["message"] = (
            f"The recipients or the message changed since the dry run (plan {args.plan} vs "
            f"{plan}), so nothing was sent. Show the user the new dry run and ask again."
        )
        runner.emit(report)
        return EXIT_DECIDE

    results = send_all(resolved, message, config, delay=args.delay)
    counts = summarize(results)
    report.update(
        {
            "sent": counts["failed"] == 0 and counts["not_attempted"] == 0,
            "results": results,
            "summary": counts,
        }
    )
    if counts["failed"]:
        report["message"] = (
            "The run stopped at the first failure; nothing was retried. Tell the user who "
            "received the message (status sent), who failed and whether the failed one may "
            "still have been delivered, and who was not attempted. Do not run it again "
            "automatically: only send to the not_attempted recipients after the user says so."
        )
    runner.emit(report)
    return EXIT_FAIL if counts["failed"] else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
