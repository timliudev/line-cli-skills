#!/usr/bin/env python3
"""sync - keep a local archive of LINE chats (messages.jsonl, messages.md, files/).

Usage
  sync.py CHAT [--name NAME] [--types image,video,audio,file] [--max-bytes N] [--no-download]
  sync.py --all [same options]

CHAT is a chat name or a full chat ID. One JSON document is printed on stdout (diagnostics go to
stderr); `--json` is accepted for symmetry and changes nothing.

Exit codes
  0  success (--all: every chat synced)
  1  `line` failed or could not be run (--all: at least one chat failed; the rest were synced)
  2  usage error
  3  CHAT is ambiguous: ask the user which chat (candidates in the output)
  4  CHAT matched nothing
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any

if sys.version_info < (3, 11):  # noqa: UP036 - friendly message instead of an ImportError
    sys.exit("This script needs Python 3.11 or newer (found " + sys.version.split()[0] + ").")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from linecore import chatlist, media, msgtext, names, runner, store  # noqa: E402
from linecore.config import DOWNLOAD_TYPES, Config, load_config_safe  # noqa: E402
from linecore.render import render_markdown  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_DECIDE, EXIT_NOT_FOUND = 0, 1, 2, 3, 4
FETCH_LIMIT = 100  # upstream returns at most this many recent messages per chat

# Failures that say nothing about one attachment: stop trying the rest of this chat.
SYSTEMIC = {
    "not_logged_in",
    "storage_invalid",
    "storage_unavailable",
    "storage_uncertain",
    "storage_config",
    "binary_missing",
    "exec_failed",
    "cancelled",
}


class SyncError(Exception):
    def __init__(self, error: dict[str, Any]):
        super().__init__(error.get("message", "sync failed"))
        self.error = error


@dataclass
class Options:
    types: tuple[str, ...]
    max_bytes: int
    download: bool = True
    tz: tzinfo | None = None  # None = local time


@dataclass
class Syncer:
    config: Config
    options: Options
    binary: str | None = None
    _cache: dict[str, dict[str, Any]] | None = None
    _self_mid: str | None = None
    _self_checked: bool = False
    _notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------ helpers

    def _error_from(self, result: runner.LineResult) -> dict[str, Any]:
        if result.error:
            return result.error.to_dict()
        return {"kind": "error", "message": "line failed", "retryable": False}

    def self_mid(self) -> str | None:
        if not self._self_checked:
            self._self_checked = True
            result = runner.run_line(["whoami", "--json"], config=self.config, binary=self.binary)
            data = result.data if result.ok else None
            if isinstance(data, dict) and isinstance(data.get("mid"), str):
                self._self_mid = data["mid"]
        return self._self_mid

    def fetch(self, chat_id: str) -> tuple[list[dict[str, Any]], list[str]]:
        result = runner.run_line(
            ["messages", chat_id, "--json", "--limit", str(FETCH_LIMIT)],
            config=self.config,
            binary=self.binary,
        )
        if not result.ok:
            raise SyncError(self._error_from(result))
        if not isinstance(result.data, list):
            raise SyncError(
                {
                    "kind": "unexpected_output",
                    "message": "`line messages --json` did not return a list",
                    "retryable": False,
                }
            )
        items = [m for m in result.data if isinstance(m, dict) and isinstance(m.get("id"), str)]
        seen: set[str] = set()
        unique = [m for m in items if not (m["id"] in seen or seen.add(m["id"]))]
        warnings = list(result.warnings)
        if len(unique) != len(result.data):
            warnings.append(f"ignored {len(result.data) - len(unique)} malformed message item(s)")
        return unique, warnings

    def _text_fallback(self, chat_id: str, records: list[dict[str, Any]], now_ms: int) -> bool:
        """Fill missing sender names from `messages --show-ids` text (once per sync of a chat).

        Returns True when the names cache gained at least one name.
        """
        result = runner.run_line(
            ["messages", chat_id, "--limit", str(FETCH_LIMIT), "--show-ids"],
            config=self.config,
            binary=self.binary,
        )
        if not result.ok or not result.text:
            return False
        by_message = msgtext.parse_sender_names(result.text)
        gained = False
        for rec in records:
            mid, name = rec.get("from"), by_message.get(str(rec.get("id")))
            if (
                name
                and isinstance(mid, str)
                and mid
                and not (self._cache or {}).get(mid, {}).get("name")
            ):
                self._cache[mid] = {"name": name, "ts": now_ms, "source": "messages-text"}
                gained = True
        return gained

    # ------------------------------------------------------------ one chat

    def sync_chat(self, chat_id: str, name_hint: str | None = None) -> dict[str, Any]:
        cfg, opts = self.config, self.options
        root = cfg.archive_root
        warnings: list[str] = list(cfg.warnings)
        now = datetime.now(UTC)
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        now_ms = int(now.timestamp() * 1000)

        repo = enclosing_git_repo(root)
        if repo:
            warnings.append(
                f"archive_root ({root}) is inside the git repository {repo}. Chat archives are "
                "private: move archive_root outside the repo or make sure it is git-ignored, and "
                "never commit or publish it."
            )

        fetched, fetch_warnings = self.fetch(chat_id)
        warnings += fetch_warnings

        chat_dir = store.find_chat_dir(root, chat_id)
        if not fetched and chat_dir is None:
            warnings.append("the chat has no messages to archive yet")
            return summary_for(chat_id, name_hint, None, warnings=warnings)

        existing: list[dict[str, Any]] = []
        state = store.new_state(chat_id)
        if chat_dir is not None:
            existing, read_warnings = store.read_jsonl(chat_dir / store.JSONL_NAME)
            warnings += read_warnings
            state = store.read_state(chat_dir / store.STATE_NAME, chat_id)

        merged = store.merge_window(existing, fetched, now_iso=now_iso, window_limit=FETCH_LIMIT)
        records = merged.records

        # --- names
        if self._cache is None:
            self._cache = names.load_cache(root)
        changed, name_warnings = names.refresh_names(
            names.mids_in(records) + ([chat_id] if chat_id[:1].lower() == "u" else []),
            self._cache,
            now_ms=now_ms,
            config=cfg,
            binary=self.binary,
        )
        warnings += name_warnings
        unresolved = [
            m
            for m in dict.fromkeys(names.mids_in(records))
            if m[:1].lower() == "u" and not self._cache.get(m, {}).get("name")
        ]
        if unresolved and self._text_fallback(chat_id, records, now_ms):
            changed = True
            if all(self._cache.get(m, {}).get("name") for m in unresolved):
                warnings = [w for w in warnings if w not in name_warnings]
        name_by_mid = names.name_map(self._cache)
        fresh_name = name_hint or (name_by_mid.get(chat_id) if chat_id[:1].lower() == "u" else None)
        chat_name = fresh_name or state.get("name")
        if not chat_name and chat_dir is not None:
            chat_name = store.folder_label(chat_dir, chat_id)

        # --- folder (keyed by ID; only the name part of the folder changes on a rename)
        wanted = store.folder_name(chat_id, fresh_name) if fresh_name else None
        try:
            root.mkdir(parents=True, exist_ok=True)
            if chat_dir is None:
                chat_dir = root / (wanted or store.folder_name(chat_id, chat_name))
                chat_dir.mkdir(parents=True, exist_ok=True)
            elif wanted and chat_dir.name != wanted:
                target = chat_dir.with_name(wanted)
                if target.exists():
                    warnings.append(f"could not rename {chat_dir.name} to {wanted}: target exists")
                else:
                    try:
                        chat_dir = chat_dir.rename(target)
                    except OSError as exc:
                        warnings.append(
                            f"could not rename the archive folder ({exc.strerror or exc}); "
                            "keeping the old name"
                        )
        except OSError as exc:
            raise SyncError(
                {
                    "kind": "archive_unwritable",
                    "message": f"cannot write the archive at {root}: {exc.strerror or exc}",
                    "retryable": False,
                    "hint": "Choose another archive_root (config.toml) or LINE_SKILL_ARCHIVE.",
                }
            ) from exc

        # --- messages.jsonl (truth) first
        jsonl = chat_dir / store.JSONL_NAME
        if merged.changed or (merged.new_ids and not jsonl.is_file()):
            store.write_jsonl(jsonl, records)
        elif merged.new_ids:
            by_id = {r["id"]: r for r in records}
            store.append_jsonl(jsonl, [by_id[i] for i in merged.new_ids])

        if merged.gap_possible and not any(
            g.get("before_id") == merged.gap_before_id for g in state["gaps"]
        ):
            state["gaps"].append(
                {
                    "after_id": merged.gap_after_id,
                    "before_id": merged.gap_before_id,
                    "detected_at": now_iso,
                }
            )

        # --- attachments
        files_dir = chat_dir / store.FILES_DIRNAME
        stored = media.index_files(files_dir)
        new_files: list[dict[str, Any]] = []
        failed_files: list[dict[str, Any]] = []
        failures: dict[str, dict[str, Any]] = state["failed_files"]
        stopped = False
        for rec in fetched:
            kind = media.kind_of(rec)
            if not kind:
                continue
            sid = store.safe_id(rec["id"])
            if sid in stored:
                failures.pop(rec["id"], None)
                continue
            if stopped or not opts.download or kind not in opts.types:
                continue
            if not media.should_retry(failures.get(rec["id"]), opts.max_bytes):
                continue
            result = media.download_attachment(
                chat_id,
                rec,
                files_dir,
                max_bytes=opts.max_bytes,
                config=cfg,
                binary=self.binary,
                tz=opts.tz,
            )
            if result.ok and result.path:
                stored[sid] = result.path.name
                failures.pop(rec["id"], None)
                new_files.append(
                    {
                        "message_id": rec["id"],
                        "type": kind,
                        "path": str(result.path),
                        "file": f"{store.FILES_DIRNAME}/{result.path.name}",
                        "bytes": result.size,
                    }
                )
                continue
            failed_files.append(
                {
                    "message_id": rec["id"],
                    "type": kind,
                    "reason": result.reason,
                    "kind": result.kind,
                    "message": result.message,
                }
            )
            if result.kind in SYSTEMIC:
                stopped = True
                warnings.append(
                    f"stopped downloading attachments ({result.kind}): {result.message}"
                )
            else:
                failures[rec["id"]] = media.failure_record(rec, result, now_iso)
        known_ids = {r["id"] for r in records}
        state["failed_files"] = {k: v for k, v in failures.items() if k in known_ids}

        # --- state, names cache, messages.md
        newest = max(records, key=_time, default=None)
        state.update(
            {
                "name": chat_name,
                "last_sync_at": now_iso,
                "newest_message_id": newest["id"] if newest else None,
                "newest_created_time": _time(newest) if newest else None,
            }
        )
        rendered = render_markdown(
            records,
            chat_id=chat_id,
            chat_name=chat_name,
            names=name_by_mid,
            self_mid=self.self_mid(),
            files=stored,
            failures=state["failed_files"],
            gaps=[
                {"before_id": g.get("before_id"), "after_id": g.get("after_id")}
                for g in state["gaps"]
            ],
            synced_at=now,
            tz=opts.tz,
        )
        store.atomic_write_text(chat_dir / store.MD_NAME, rendered.text)
        store.write_json(chat_dir / store.STATE_NAME, state)
        if changed:
            names.save_cache(root, self._cache)

        unsent = [
            {
                "message_id": r["id"],
                "created_time": r.get("created_time"),
                "from": name_by_mid.get(r.get("from") or "") or r.get("from"),
                "excerpt": _excerpt(r),
            }
            for r in merged.possibly_unsent
        ]
        if merged.gap_possible:
            warnings.append(
                "possible gap: the oldest of the latest 100 messages is newer than the last "
                "archived one, so messages in between may be unrecoverable"
            )
        first_new = min(
            (rendered.first_line[i] for i in merged.new_ids if i in rendered.first_line),
            default=None,
        )
        return summary_for(
            chat_id,
            chat_name,
            chat_dir,
            new_messages=len(merged.new_ids),
            new_files=new_files,
            failed_files=failed_files,
            possibly_unsent=unsent,
            gap_possible=merged.gap_possible,
            warnings=warnings,
            total_messages=len(records),
            new_messages_from_line=first_new,
        )


# ---------------------------------------------------------------- module helpers


def _time(record: dict[str, Any] | None) -> int:
    value = (record or {}).get("created_time")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _excerpt(record: dict[str, Any], limit: int = 80) -> str:
    text = record.get("text")
    if isinstance(text, str) and text:
        one = " ".join(text.split())
        return one if len(one) <= limit else one[: limit - 3] + "..."
    kind = media.kind_of(record)
    return f"[{media.LABEL[kind]}]" if kind else ""


def enclosing_git_repo(path: Path) -> Path | None:
    """The nearest parent (or the path itself) that contains a `.git` entry."""
    try:
        start = path.resolve()
    except OSError:
        start = path
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def summary_for(
    chat_id: str,
    chat: str | None,
    archive_dir: Path | None,
    *,
    new_messages: int = 0,
    new_files: list[dict[str, Any]] | None = None,
    failed_files: list[dict[str, Any]] | None = None,
    possibly_unsent: list[dict[str, Any]] | None = None,
    gap_possible: bool = False,
    warnings: list[str] | None = None,
    total_messages: int = 0,
    new_messages_from_line: int | None = None,
) -> dict[str, Any]:
    return {
        "chat": chat,
        "chat_id": chat_id,
        "archive_dir": str(archive_dir) if archive_dir else None,
        "messages_md": str(archive_dir / store.MD_NAME) if archive_dir else None,
        "new_messages": new_messages,
        "new_messages_from_line": new_messages_from_line,
        "total_messages": total_messages,
        "new_files": new_files or [],
        "failed_files": failed_files or [],
        "possibly_unsent": possibly_unsent or [],
        "gap_possible": gap_possible,
        "warnings": warnings or [],
    }


def parse_types(value: str) -> tuple[str, ...]:
    chosen = [part.strip().lower() for part in value.split(",") if part.strip()]
    bad = [t for t in chosen if t not in DOWNLOAD_TYPES]
    if bad or not chosen:
        raise argparse.ArgumentTypeError(
            f"--types takes a comma-separated list of: {', '.join(DOWNLOAD_TYPES)}"
        )
    return tuple(dict.fromkeys(chosen))


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync.py",
        description="Archive LINE chats locally: messages.jsonl, messages.md and downloaded files.",
        epilog="Exit codes: 0 ok, 1 failure, 2 usage, 3 ambiguous chat, 4 chat not found.",
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("chat", nargs="?", metavar="CHAT", help="chat name or full chat ID")
    target.add_argument("--all", action="store_true", help="sync every chat, one after another")
    parser.add_argument("--name", help="label for the archive folder when CHAT is a full ID")
    parser.add_argument(
        "--types",
        type=parse_types,
        help=f"attachment types to download (default from config: {','.join(DOWNLOAD_TYPES)})",
    )
    parser.add_argument(
        "--max-bytes", type=positive_int, help="skip attachments larger than this many bytes"
    )
    parser.add_argument(
        "--no-download", action="store_true", help="archive text only; download no attachments"
    )
    parser.add_argument("--json", action="store_true", help="accepted for symmetry; always JSON")
    return parser


def _resolve_target(chat: str, config: Config) -> tuple[str | None, str | None, dict | None, int]:
    """Return (chat_id, name, payload_if_stop, exit_code)."""
    from lineq import FULL_ID, resolve_name  # same folder; imported late to keep --help fast

    bare = chat[3:] if chat.startswith("id:") else chat
    if FULL_ID.match(bare):
        return bare, None, None, EXIT_OK
    payload, code = resolve_name(chat, config=config)
    if code == EXIT_OK and payload.get("status") == "resolved":
        return payload["id"], payload.get("name") or None, None, EXIT_OK
    return None, None, payload, code


def main(argv: list[str] | None = None) -> int:
    runner.configure_stdio()
    args = build_parser().parse_args(argv)
    if args.all and args.name:
        build_parser().error("--name only works with a single CHAT")

    config, config_error = load_config_safe()
    if config_error:
        runner.log(f"warning: {config_error}")
    options = Options(
        types=args.types if args.types is not None else config.download_types,
        max_bytes=args.max_bytes or config.download_max_bytes,
        download=not args.no_download,
    )
    syncer = Syncer(config=config, options=options)

    if not args.all:
        chat_id, name, stop, code = _resolve_target(args.chat, config)
        if stop is not None or chat_id is None:
            runner.emit(stop)
            return code
        try:
            summary = syncer.sync_chat(chat_id, args.name or name)
        except SyncError as exc:
            runner.emit({"chat_id": chat_id, "error": exc.error})
            return EXIT_FAIL
        if config_error:
            summary["warnings"].insert(0, config_error)
        runner.emit(summary)
        return EXIT_OK

    listing = runner.run_line(["chats", "--json"], config=config)
    if not listing.ok or not isinstance(listing.data, list):
        runner.emit({"all": True, "error": syncer._error_from(listing), "chats": [], "failed": []})
        return EXIT_FAIL
    chat_ids = [
        row["id"]
        for row in listing.data
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    ]
    # Active-chat names from the text listing (the JSON listing has IDs only). Best effort.
    listed, text_listing = chatlist.fetch_active_chats(config=config, binary=syncer.binary)
    listed_names = chatlist.named_only(listed)
    done: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for chat_id in chat_ids:
        try:
            done.append(syncer.sync_chat(chat_id, listed_names.get(chat_id)))
        except SyncError as exc:
            failed.append({"chat_id": chat_id, "error": exc.error})
            runner.log(f"warning: {chat_id}: {exc}")
        except Exception as exc:  # noqa: BLE001 - one broken chat must not stop the rest
            failed.append(
                {
                    "chat_id": chat_id,
                    "error": {"kind": "unexpected", "message": str(exc), "retryable": False},
                }
            )
            runner.log(f"warning: {chat_id}: {exc}")
    runner.emit({"all": True, "chats": done, "failed": failed, "total": len(chat_ids)})
    return EXIT_FAIL if failed else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
