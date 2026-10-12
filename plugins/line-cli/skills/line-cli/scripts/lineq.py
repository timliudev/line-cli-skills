#!/usr/bin/env python3
"""lineq - thin, safe wrapper around the `line` CLI. JSON on stdout, diagnostics on stderr.

Subcommands
  resolve NAME            Turn a chat/contact name into a full ID (or a candidate list).
  run [opts] -- LINE_ARGS Run `line LINE_ARGS` and return {ok, exit_code, data, error}.

Exit codes
  0  success (resolve: exactly one exact or substring match; run: line succeeded or partial result)
  1  `line` failed or could not be run (see "error")
  2  usage error
  3  needs a decision: resolve found several/inexact matches, or run needs --yes
  4  resolve: nothing matched
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

if sys.version_info < (3, 11):  # noqa: UP036 - friendly message instead of an ImportError
    sys.exit("This script needs Python 3.11 or newer (found " + sys.version.split()[0] + ").")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from linecore import chatlist, runner  # noqa: E402
from linecore.config import load_config_safe  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_DECIDE, EXIT_NOT_FOUND = 0, 1, 2, 3, 4

FULL_ID = re.compile(r"^(?:[uUcCrR][A-Za-z0-9_-]{43}|[ucr][0-9a-f]{32})$")
KIND_BY_PREFIX = {"u": "direct", "c": "group", "r": "room"}


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold().strip()


def id_kind(chat_id: str) -> str:
    return KIND_BY_PREFIX.get(chat_id[:1].lower(), "unknown")


def _error_dict(err: runner.ErrorInfo | None) -> dict[str, Any] | None:
    return err.to_dict() if err else None


# ------------------------------------------------------------------ resolve


def _collect(result: runner.LineResult, source: str) -> list[dict[str, Any]]:
    rows = result.data if isinstance(result.data, list) else []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if source == "chats":
            cid, name = row.get("id"), row.get("name") or ""
            extra = {"unread_count": row.get("unread_count"), "updated_at": row.get("updated_at")}
        else:
            cid = row.get("mid")
            name = row.get("displayNameOverridden") or row.get("displayName") or ""
            extra = {}
        if not isinstance(cid, str) or not cid:
            continue
        out.append({"id": cid, "name": name, "source": source, **extra})
    return out


def resolve_name(
    name: str, *, config=None, binary: str | None = None
) -> tuple[dict[str, Any], int]:
    """Resolve NAME to a chat/contact ID using `chats --search` and `contacts --search`.

    Plain `chats --json` has no names (legacy ID-only format), so we always search.
    """
    query = name.strip()
    if not query:
        return {
            "status": "error",
            "error": {"kind": "usage", "message": "NAME is empty"},
        }, EXIT_USAGE

    bare = query[3:] if query.startswith("id:") else query
    if FULL_ID.match(bare):
        return (
            {
                "status": "resolved",
                "query": name,
                "id": bare,
                "name": None,
                "type": id_kind(bare),
                "note": "input already looks like a full ID; not looked up",
                "candidates": [],
            },
            EXIT_OK,
        )

    results = {}
    for source, argv in (
        ("chats", ["chats", "--search", query, "--json"]),
        ("contacts", ["contacts", "--search", query, "--json"]),
    ):
        results[source] = runner.run_line(argv, config=config, binary=binary)

    warnings: list[str] = []
    fallback_rows: list[dict[str, Any]] | None = None
    if not results["chats"].ok:
        # `chats --search` names ALL chats (incl. inactive) in batches; one bad batch fails it.
        listed, listing = chatlist.fetch_active_chats(config=config, binary=binary)
        if listing.ok:
            fallback_rows = chatlist.search_rows(listed, query)
            results["chats"] = runner.LineResult(
                ok=True,
                exit_code=0,
                data=[
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "unread_count": r["unread"],
                    }
                    for r in fallback_rows
                ],
                command=results["chats"].command,
            )
            warnings.append(chatlist.FALLBACK_WARNING)
    failures = {s: r for s, r in results.items() if not r.ok}
    if len(failures) == len(results):
        first = next(iter(failures.values()))
        return (
            {
                "status": "error",
                "query": name,
                "error": _error_dict(first.error),
                "candidates": [],
            },
            EXIT_FAIL,
        )
    for source, res in failures.items():
        warnings.append(
            f"`{source} --search` failed ({res.error.kind if res.error else 'error'}); "
            "results may be incomplete"
        )
    for res in results.values():
        warnings.extend(res.warnings)

    merged: dict[str, dict[str, Any]] = {}
    for source, res in results.items():
        if not res.ok:
            continue
        for row in _collect(res, source):
            entry = merged.setdefault(
                row["id"],
                {"id": row["id"], "name": row["name"], "type": id_kind(row["id"]), "sources": []},
            )
            if not entry["name"] and row["name"]:
                entry["name"] = row["name"]
            if row["source"] not in entry["sources"]:
                entry["sources"].append(row["source"])
            for key in ("unread_count", "updated_at"):
                if row.get(key) is not None:
                    entry[key] = row[key]

    wanted = _norm(query)
    exact = [c for c in merged.values() if _norm(c["name"]) == wanted]
    candidates = sorted(
        merged.values(), key=lambda c: (_norm(c["name"]) != wanted, -(c.get("updated_at") or 0))
    )

    base: dict[str, Any] = {"query": name, "candidates": candidates}
    if warnings:
        base["warnings"] = warnings
    if len(exact) == 1:
        only = exact[0]
        found = {
            **base,
            "status": "resolved",
            "id": only["id"],
            "name": only["name"],
            "type": only["type"],
        }
        if (
            failures or fallback_rows is not None
        ):  # one search failed, so a second exact match may be hiding
            found["incomplete"] = True
        return found, EXIT_OK
    if not exact and len(candidates) == 1:
        # a single substring hit: nothing to choose from, so do not make the user pick
        only = candidates[0]
        found = {
            **base,
            "status": "resolved",
            "matched_by": "substring",
            "id": only["id"],
            "name": only["name"],
            "type": only["type"],
        }
        if failures or fallback_rows is not None:
            found["incomplete"] = True
        return found, EXIT_OK
    if len(exact) > 1:
        return {
            **base,
            "status": "ambiguous",
            "message": f"{len(exact)} chats or contacts have exactly that name; ask the user "
            "which one and use its full ID.",
        }, EXIT_DECIDE
    if candidates:
        return {
            **base,
            "status": "candidates",
            "message": "No exact match, but these names contain the text. Ask the user to "
            "pick one and use its full ID.",
        }, EXIT_DECIDE
    return {
        **base,
        "status": "not_found",
        "message": "No chat or contact name contains that text.",
    }, EXIT_NOT_FOUND


# ------------------------------------------------------------------ run


def _wants_stdout_bytes(line_args: list[str]) -> bool:
    for i, arg in enumerate(line_args):
        if re.fullmatch(r"--?output=-", arg):
            return True
        if re.fullmatch(r"--?output", arg) and line_args[i + 1 : i + 2] == ["-"]:
            return True
    return False


def _guard(line_args: list[str], yes: bool) -> dict[str, Any] | None:
    """Return an error payload when the request must not run, else None."""
    if not line_args:
        return {
            "kind": "usage",
            "message": "no line arguments given after `--`",
            "retryable": False,
        }
    command = line_args[0]
    if command == "login":
        return {
            "kind": "usage",
            "message": "login is interactive; use login.py (it opens a terminal for the user)",
            "retryable": False,
        }
    if command == "watch" and not any(
        re.fullmatch(r"--?(timeout|limit)(=.*)?", a) for a in line_args[1:]
    ):
        return {
            "kind": "usage",
            "message": "watch never ends on its own; pass --timeout DURATION and/or --limit N",
            "retryable": False,
        }
    if command == "download" and _wants_stdout_bytes(line_args):
        return {
            "kind": "usage",
            "message": "binary stdout (--output -) is not supported through lineq; use a file path",
            "retryable": False,
        }
    if runner.is_mutation(line_args) and not yes:
        return {
            "kind": "confirmation_required",
            "message": "this command changes state (send/react/unsend/logout/update/auth migrate); "
            "ask the user, then rerun with --yes",
            "retryable": False,
        }
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lineq.py",
        description=(__doc__ or "").split("\n\n")[0],
        epilog="Exit codes: 0 ok, 1 failure, 2 usage, 3 needs decision/confirmation, 4 not found.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    res = sub.add_parser("resolve", help="resolve a chat/contact name to an ID")
    res.add_argument("name", metavar="NAME")

    run = sub.add_parser(
        "run",
        help="run `line` with the given arguments",
        description="Usage: lineq.py run [options] -- LINE_ARGS...",
    )
    run.add_argument(
        "--timeout", type=float, default=None, help="seconds (default depends on command)"
    )
    run.add_argument(
        "--stdin",
        action="store_true",
        help="forward this process's stdin to line (for `send --stdin`)",
    )
    run.add_argument(
        "--yes", action="store_true", help="the user has approved this state-changing command"
    )
    run.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help="reads only: attempts on a busy session (default 3); mutations always 1",
    )
    run.add_argument("line_args", nargs=argparse.REMAINDER, metavar="-- LINE_ARGS")
    return parser


def main(argv: list[str] | None = None) -> int:
    runner.configure_stdio()
    args = build_parser().parse_args(argv)
    config, config_error = load_config_safe()
    if config_error:
        runner.log(f"warning: {config_error}")

    if args.cmd == "resolve":
        payload, code = resolve_name(args.name, config=config)
        runner.emit(payload)
        return code

    line_args = list(args.line_args)
    if line_args and line_args[0] == "--":
        line_args = line_args[1:]
    problem = _guard(line_args, args.yes)
    if problem:
        runner.emit({"ok": False, "exit_code": None, "data": None, "error": problem})
        return EXIT_DECIDE if problem["kind"] == "confirmation_required" else EXIT_USAGE

    # `send --stdin` reads its text from stdin; forward ours even when lineq's own flag was omitted
    forward_stdin = args.stdin or ("--stdin" in line_args and not sys.stdin.isatty())
    stdin_text = (
        sys.stdin.buffer.read().decode("utf-8", errors="replace") if forward_stdin else None
    )
    result = runner.run_line(
        line_args,
        stdin_text=stdin_text,
        timeout=args.timeout,
        config=config,
        max_attempts=args.max_attempts,
    )
    runner.emit(result.to_dict())
    return EXIT_OK if result.ok else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
