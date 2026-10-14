#!/usr/bin/env python3
"""watch - keep the local archive live from `line watch`, catch up after gaps, notify for VIP chats.

Usage
  watch.py [--archive NAME_OR_ID ...] [--archive-all] [--vip NAME_OR_ID ...] [--no-notify]
           [--no-initial-sync] [--no-download] [--duration SECONDS] [--log FILE]
           [--settle SECONDS] [--max-restarts N]

Long-running. It starts `line watch --json` and appends each `message` event of an ARCHIVED chat to
the same archive `sync.py` writes (`messages.jsonl`, deduplicated by message ID). Shortly after a
chat has new messages its `messages.md` is re-rendered from the local files, without any `line`
call. The full sync (which calls `line`) runs only for an attachment to download, an unknown
sender or chat name, at start and on `resync_required` (LINE reported a gap).
If `line watch` exits it is started again with exponential backoff (capped); if another `line
watch` already runs, or the login is gone, it stops with a clear error instead of looping. Ctrl-C
stops it cleanly. Only ONE `line watch` can run at a time.

PRIVACY: by default only chats that already have an archive folder, or that are listed under
`[watch] archive_chats` in config.toml / --archive, are archived. Events of every other chat are
ignored: nothing is written and nothing is logged. --archive-all archives everything (opt-in).

Chats listed as VIP (`[watch] vip_chats` in config.toml, and/or --vip) show a Windows toast for a
new incoming message: chat, sender and a short preview. Elsewhere, or if the toast fails, a log
line (chat and sender, no text) goes to stderr instead.

Diagnostics go to stderr (message text is never logged). One JSON summary is printed on stdout
when it stops.

Exit codes
  0  stopped by Ctrl-C or --duration
  1  failed: login gone, `line` missing, gave up restarting, ...
  2  usage error
  3  needs the user: another `line watch` is already running
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if sys.version_info < (3, 11):  # noqa: UP036 - friendly message instead of an ImportError
    sys.exit("This script needs Python 3.11 or newer (found " + sys.version.split()[0] + ").")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from linecore import emoji, media, names, runner, store  # noqa: E402
from linecore.config import Config, load_config_safe  # noqa: E402
from linecore.render import render_markdown  # noqa: E402
from sync import Options, Syncer, SyncError  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_DECIDE = 0, 1, 2, 3

BACKOFF_START = 2.0  # seconds before the first restart
BACKOFF_CAP = 120.0  # restart delays never grow beyond this
STABLE_AFTER = 60.0  # a run that lasted this long starts the backoff over
SETTLE = 15.0  # sync a chat once it has been quiet this long ...
MAX_HOLD = 120.0  # ... or after waiting this long at most
FOLDER_TTL = 5.0  # how long the set of archive folders is trusted before it is re-read
NAME_RETRY = 600.0  # a sender name that could not be found is looked up again after this
CHAT_PAUSE = 2.0  # pause between two chats of one sync run: frees the session for other commands
SYNC_RETRY_DELAY = 30.0  # after a failed sync, try again after this
MAX_SYNC_ATTEMPTS = 5
NOTIFY_MAX_AGE = 900  # no toast for messages older than this (a backlog replayed after a pause)
PREVIEW_CHARS = 80
SEEN_LIMIT = 2000  # message IDs remembered for notification de-duplication

# Failures that restarting cannot fix: stop and tell the user.
FATAL_KINDS = {
    "watcher_running",
    "not_logged_in",
    "binary_missing",
    "exec_failed",
    "storage_invalid",
    "storage_unavailable",
    "storage_config",
    "usage",
}

_sleep = time.sleep  # indirection so tests can run without waiting

_CHAT_ID = r"(?:[CUR][A-Za-z0-9_-]{43}|[cur][0-9a-f]{32})"
_FOLDER_ID = re.compile(rf"(?:^|__)({_CHAT_ID})$")
_OLD_FOLDER_ID = re.compile(rf"^({_CHAT_ID})__")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def log(message: str) -> None:
    runner.log(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}")


# ---------------------------------------------------------------- decisions (pure)


@dataclass(frozen=True)
class Restart:
    fatal: bool
    delay: float
    next_backoff: float


def plan_restart(
    error_kind: str | None,
    ran_for: float,
    backoff: float,
    *,
    start: float = BACKOFF_START,
    cap: float = BACKOFF_CAP,
) -> Restart:
    """What to do after `line watch` exited: stop for good, or wait `delay` and start it again."""
    if error_kind in FATAL_KINDS:
        return Restart(True, 0.0, backoff)
    if ran_for >= STABLE_AFTER:
        backoff = start  # it worked for a while: this is a fresh failure, not a crash loop
    return Restart(False, backoff, min(backoff * 2, cap))


def should_notify(
    event: dict[str, Any],
    vip: dict[str, str],
    self_mid: str | None,
    *,
    now_ms: int,
    max_age: float = NOTIFY_MAX_AGE,
) -> bool:
    """True for a fresh incoming message (not sent by me) in a VIP chat."""
    if event.get("event") != "message":
        return False
    chat_id, message = event.get("chat_id"), event.get("message")
    if chat_id not in vip or not isinstance(message, dict):
        return False
    if event.get("type") == 25:  # LINE operation 25 = a message I sent (from any of my devices)
        return False
    sender = message.get("from")
    if self_mid and sender == self_mid:
        return False
    created = message.get("created_time")
    if isinstance(created, int) and not isinstance(created, bool) and created > 0:
        if now_ms - created > max_age * 1000:
            return False
    return True


def preview(record: dict[str, Any], limit: int = PREVIEW_CHARS) -> str:
    """A one-line preview: the text, or the attachment kind in brackets."""
    text = record.get("text")
    if isinstance(text, str) and text.strip():
        one = " ".join(text.split())
        return one if len(one) <= limit else one[: limit - 1] + "…"
    if record.get("status") == "decryption_failed":
        return "[could not be decrypted]"
    kind = media.kind_of(record)
    return f"[{media.LABEL[kind]}]" if kind else "[message]"


def archived_chat_ids(root: Path) -> list[str]:
    """Chat IDs of the chat folders in the archive (non-chat entries start with `_`)."""
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    found: list[str] = []
    for entry in entries:
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        match = _FOLDER_ID.search(entry.name) or _OLD_FOLDER_ID.match(entry.name)
        if match and match.group(1) not in found:
            found.append(match.group(1))
    return found


def parse_event(raw: bytes | str) -> dict[str, Any] | None:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    text = text.strip()
    if not text:
        return None
    try:
        event = json.loads(text)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


# ---------------------------------------------------------------- notifications

_TOAST_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$title = [System.Security.SecurityElement]::Escape($env:LINE_SKILL_TOAST_TITLE)
$body = [System.Security.SecurityElement]::Escape($env:LINE_SKILL_TOAST_BODY)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$title</text><text>$body</text></binding></visual></toast>")
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
"""  # noqa: E501


def powershell_command() -> list[str]:
    """Windows PowerShell 5.1 (it can load the WinRT toast API) running the toast script."""
    exe = shutil.which("powershell") or "powershell"
    encoded = base64.b64encode(_TOAST_SCRIPT.encode("utf-16-le")).decode("ascii")
    flags = ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    return [exe, *flags, "-EncodedCommand", encoded]


class Notifier:
    """Windows toast through PowerShell (stdlib only); a stderr log line everywhere else.

    The text travels in environment variables and is XML-escaped inside PowerShell, so message
    content can never become part of the script.
    """

    def __init__(
        self,
        *,
        windows: bool | None = None,
        run: Callable[..., Any] = subprocess.run,
        emit: Callable[[str], None] = log,
    ) -> None:
        self.windows = sys.platform.startswith("win") if windows is None else windows
        self._run = run
        self._emit = emit

    def notify(self, title: str, body: str, *, fallback: str | None = None) -> bool:
        """Show a toast. Returns True when a toast was shown, False when the log was used.

        The log fallback never contains message text: it is ``fallback`` (the caller passes the
        chat and sender only) or just the chat title. The preview in ``body`` only goes to the
        toast.
        """
        title, body = _CONTROL.sub(" ", title), _CONTROL.sub(" ", body)
        if self.windows and self._toast(title, body):
            return True
        self._emit("[VIP] " + _CONTROL.sub(" ", fallback or title))
        return False

    def _toast(self, title: str, body: str) -> bool:
        env = dict(os.environ, LINE_SKILL_TOAST_TITLE=title, LINE_SKILL_TOAST_BODY=body)
        kwargs: dict[str, Any] = {}
        if self.windows:
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = self._run(
                powershell_command(),
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=20,
                **kwargs,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self._emit(f"warning: toast notification failed ({exc.__class__.__name__})")
            return False
        if proc.returncode != 0:
            self._emit(f"warning: toast notification failed (PowerShell exit {proc.returncode})")
            return False
        return True


# ---------------------------------------------------------------- archive writes


class Archiver:
    """Appends message events to `<chat folder>/messages.jsonl`, deduplicated by message ID."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._known: dict[str, set[str]] = {}

    def _folder(self, chat_id: str) -> Path:
        found = store.find_chat_dir(self.root, chat_id)
        return found or self.root / store.folder_name(chat_id, None)

    def known_ids(self, chat_id: str, folder: Path | None = None) -> set[str]:
        if chat_id not in self._known:
            records, _ = store.read_jsonl((folder or self._folder(chat_id)) / store.JSONL_NAME)
            self._known[chat_id] = {r["id"] for r in records}
        return self._known[chat_id]

    def forget(self, chat_id: str) -> None:
        """Drop the ID cache (a sync rewrote the chat's file)."""
        self._known.pop(chat_id, None)

    def add(self, chat_id: str, record: dict[str, Any], *, create: bool) -> bool | None:
        """Append ``record``. True: new, False: already archived, None: skipped.

        A chat without an archive folder gets one only when ``create`` is true (it is on the
        allowlist or --archive-all was given); otherwise nothing is written (None). This also covers
        a folder the user deleted while watch.py was running.
        """
        folder = store.find_chat_dir(self.root, chat_id)
        if folder is None:
            self._known.pop(chat_id, None)  # the file is gone, so the remembered IDs are stale
            if not create:
                return None
            folder = self.root / store.folder_name(chat_id, None)
        known = self.known_ids(chat_id, folder)
        if record["id"] in known:
            return False
        store.append_jsonl(folder / store.JSONL_NAME, [record])
        known.add(record["id"])
        return True


def render_local(config: Config, chat_id: str, self_mid: str | None, now: datetime) -> bool:
    """Rewrite messages.md (and the newest-message pointers) from the local files only.

    No `line` call is made: names come from the cache, attachments from the files already
    downloaded, emoji from the local cache. False when the chat has no local archive.
    """
    root = config.archive_root
    folder = store.find_chat_dir(root, chat_id)
    if folder is None or not (folder / store.JSONL_NAME).is_file():
        return False
    records, _ = store.read_jsonl(folder / store.JSONL_NAME)
    state = store.read_state(folder / store.STATE_NAME, chat_id)
    name_by_mid = names.name_map(names.load_cache(root))
    chat_name = state.get("name") or store.folder_label(folder, chat_id)
    emoji_index, _ = emoji.ensure(root, emoji.keys_in(records), now=now, download=False)
    rendered = render_markdown(
        records,
        chat_id=chat_id,
        chat_name=chat_name,
        names=name_by_mid,
        self_mid=state.get("self_mid") or self_mid,
        files=media.index_files(folder / store.FILES_DIRNAME),
        failures=state["failed_files"],
        gaps=[
            {"before_id": g.get("before_id"), "after_id": g.get("after_id")}
            for g in state["gaps"]
            if isinstance(g, dict)
        ],
        synced_at=now,
        quiet_gap_days=config.render_quiet_gap_days,
        emoji=emoji.render_info(root, emoji_index),
    )
    store.atomic_write_text(folder / store.MD_NAME, rendered.text)
    newest = max(records, key=_created, default=None)
    state.update(
        {
            "newest_message_id": newest["id"] if newest else None,
            "newest_created_time": _created(newest) if newest else None,
        }
    )
    store.write_json(folder / store.STATE_NAME, state)
    return True


def _created(record: dict[str, Any] | None) -> int:
    value = (record or {}).get("created_time")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


# ---------------------------------------------------------------- the watch loop


@dataclass
class Stats:
    events: int = 0
    messages_archived: int = 0
    duplicates: int = 0
    ignored: int = 0
    local_renders: int = 0
    resyncs: int = 0
    chats_synced: int = 0
    sync_failures: int = 0
    notifications: int = 0
    restarts: int = 0
    gaps: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": self.events,
            "messages_archived": self.messages_archived,
            "duplicate_messages": self.duplicates,
            "ignored_messages": self.ignored,
            "resync_events": self.resyncs,
            "chats_synced": self.chats_synced,
            "local_renders": self.local_renders,
            "sync_failures": self.sync_failures,
            "notifications": self.notifications,
            "restarts": self.restarts,
            "gaps": self.gaps,
        }


@dataclass
class ProcessOutcome:
    stop: str | None  # "duration" when the run time elapsed, else None (the process ended)
    exit_code: int | None = None
    error_kind: str | None = None
    error_message: str = ""
    ran_for: float = 0.0


class StreamReader:
    """Reads a child's stdout lines (into a queue) and stderr (kept as a tail) on threads."""

    def __init__(self, proc: Any, emit: Callable[[str], None] = log) -> None:
        self.lines: queue.Queue[bytes | None] = queue.Queue()
        self.stderr_tail: deque[str] = deque(maxlen=30)
        self._emit = emit
        self._threads = [
            threading.Thread(target=self._pump_out, args=(proc.stdout,), daemon=True),
            threading.Thread(target=self._pump_err, args=(proc.stderr,), daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _pump_out(self, stream: Any) -> None:
        try:
            for raw in stream:
                self.lines.put(raw)
        finally:
            self.lines.put(None)  # end of output

    def _pump_err(self, stream: Any) -> None:
        for raw in stream:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                self.stderr_tail.append(line)
                self._emit(f"line watch: {line}")

    def finish(self, timeout: float = 3.0) -> str:
        for thread in self._threads:
            thread.join(timeout)
        return "\n".join(self.stderr_tail)


class WatchLoop:
    def __init__(
        self,
        *,
        config: Config,
        make_syncer: Callable[[], Syncer],
        archiver: Archiver,
        vip: dict[str, str],
        notifier: Notifier | None,
        self_mid: str | None,
        archive_chats: dict[str, str] | None = None,
        archive_all: bool = False,
        download_types: tuple[str, ...] = (),
        initial_sync: bool = True,
        settle: float = SETTLE,
        max_hold: float = MAX_HOLD,
        clock: Callable[[], float] = time.monotonic,
        wall_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        emit: Callable[[str], None] = log,
    ) -> None:
        self.config = config
        self.make_syncer = make_syncer
        self.archiver = archiver
        self.vip = vip
        self.archive_chats = archive_chats or {}  # the allowlist: chat ID -> label
        self.archive_all = archive_all
        self.download_types = download_types  # attachment kinds a sync would download
        self._folders: set[str] = set()  # archive folders, re-read every FOLDER_TTL seconds
        self._folders_at = float("-inf")
        self._names: dict[str, tuple[str, float]] = {}  # sender MID -> (name, looked up at)
        self._sync_tried: set[str] = set()  # senders / chats a full sync was already asked for
        self.notifier = notifier
        self.self_mid = self_mid
        self.settle, self.max_hold = settle, max_hold
        self.clock, self.wall_ms, self.emit = clock, wall_ms, emit
        self.stats = Stats()
        self.dirty: dict[str, int] = {}  # chat ID -> failed FULL sync attempts so far
        self.render_only: set[str] = set()  # chats whose messages.md only needs a local re-render
        self.dirty_all = initial_sync  # only chats that are archived are touched
        self._first_dirty_at: float | None = clock() if initial_sync else None
        self._last_event_at = clock()
        self._next_flush_at = 0.0
        self._notified: deque[str] = deque(maxlen=SEEN_LIMIT)
        self._notified_set: set[str] = set()

    # ---- events

    def handle_line(self, raw: bytes | str) -> None:
        event = parse_event(raw)
        if event is not None:
            self.handle_event(event)

    def handle_event(self, event: dict[str, Any]) -> None:
        self.stats.events += 1
        kind = event.get("event")
        if kind == "message":
            self._on_message(event)
        elif kind == "resync_required":
            self.stats.resyncs += 1
            self.emit("LINE asked for a resync: syncing the archived chats")
            self._mark_all()

    def archives(self, chat_id: str) -> bool:
        """True when messages of this chat may be written to disk.

        Only chats that already have an archive folder or are on the allowlist (config
        `archive_chats` / --archive); everything else is ignored unless --archive-all was given.
        """
        if self.archive_all or chat_id in self.archive_chats:
            return True
        now = self.clock()
        if now - self._folders_at >= FOLDER_TTL:  # one directory scan every few seconds at most
            self._folders = set(archived_chat_ids(self.config.archive_root))
            self._folders_at = now
        return chat_id in self._folders

    def archived_targets(self) -> list[str]:
        """Chats a resync / initial sync touches: archive folders plus the allowlist."""
        found = archived_chat_ids(self.config.archive_root)
        return found + [c for c in self.archive_chats if c not in found]

    def _mark(self, chat_id: str) -> None:
        self.dirty.setdefault(chat_id, 0)
        self._mark_time()

    def _mark_render(self, chat_id: str) -> None:
        self.render_only.add(chat_id)
        self._mark_time()

    def needs_full_sync(self, chat_id: str, record: dict[str, Any]) -> bool:
        """Does this new message need `line` (attachment download, names), or only a re-render?"""
        kind = media.kind_of(record)
        if kind and kind in self.download_types:
            return True  # LINE expires attachments: fetch it now
        need = False
        if chat_id not in self._sync_tried and not self._chat_named(chat_id):
            self._sync_tried.add(chat_id)
            need = True  # a chat nobody has named yet: let one sync name the folder
        sender = record.get("from")
        if isinstance(sender, str) and sender and sender not in self._sync_tried:
            if not names.load_cache(self.config.archive_root).get(sender, {}).get("name"):
                self._sync_tried.add(sender)
                need = True  # a sender with no known name: one sync looks it up
        return need

    def _chat_named(self, chat_id: str) -> bool:
        folder = store.find_chat_dir(self.config.archive_root, chat_id)
        if folder is None:
            return False
        state = store.read_state(folder / store.STATE_NAME, chat_id)
        return bool(state.get("name") or store.folder_label(folder, chat_id))

    def _mark_new(self, chat_id: str, record: dict[str, Any]) -> None:
        if self.needs_full_sync(chat_id, record):
            self._mark(chat_id)
        else:
            self._mark_render(chat_id)

    def _mark_all(self) -> None:
        self.dirty_all = True
        self._mark_time()

    def _mark_time(self) -> None:
        now = self.clock()
        self._last_event_at = now
        if self._first_dirty_at is None:
            self._first_dirty_at = now

    def _on_message(self, event: dict[str, Any]) -> None:
        chat_id, record = event.get("chat_id"), event.get("message")
        if not isinstance(chat_id, str) or not chat_id or not isinstance(record, dict):
            return
        if not isinstance(record.get("id"), str) or not record["id"]:
            return
        archived = self.archives(chat_id)
        if archived:
            create = self.archive_all or chat_id in self.archive_chats
            try:
                fresh = self.archiver.add(chat_id, record, create=create)
            except OSError as exc:
                self.emit(f"warning: cannot write the archive ({exc.strerror or exc}); syncing")
                fresh = True
            if fresh is None:  # the folder disappeared and the chat is not on the allowlist
                archived = False
                self.stats.ignored += 1
            elif fresh:
                self.stats.messages_archived += 1
                self._mark_new(chat_id, record)
            else:
                self.stats.duplicates += 1
        else:
            self.stats.ignored += 1  # not an archived chat: nothing is written or logged
        if self.notifier and record["id"] not in self._notified_set:
            if should_notify(event, self.vip, self.self_mid, now_ms=self.wall_ms()):
                self._remember(record["id"])
                self._notify(chat_id, record, persist=archived)

    def _remember(self, message_id: str) -> None:
        if len(self._notified) == self._notified.maxlen:
            self._notified_set.discard(self._notified[0])
        self._notified.append(message_id)
        self._notified_set.add(message_id)

    def _notify(self, chat_id: str, record: dict[str, Any], *, persist: bool = False) -> None:
        assert self.notifier is not None
        sender = self.sender_name(record.get("from"), persist=persist)
        title = self.vip.get(chat_id) or self.chat_label(chat_id)
        self.stats.notifications += 1
        self.notifier.notify(
            title,
            f"{sender}: {preview(record)}",
            fallback=f"new message in {title} from {sender}",  # never the message text
        )

    def sender_name(self, mid: Any, *, persist: bool = True) -> str:
        """Display name of a sender; the name cache is only written when ``persist`` (archived)."""
        if not isinstance(mid, str) or not mid:
            return "(unknown)"
        remembered = self._names.get(mid)
        if remembered and (remembered[0] or self.clock() - remembered[1] < NAME_RETRY):
            return remembered[0] or "(unknown)"  # no `line contacts` call for a known sender
        cache = names.load_cache(self.config.archive_root)
        if not cache.get(mid, {}).get("name"):
            changed, _ = names.refresh_names(
                [mid], cache, now_ms=self.wall_ms(), config=self.config
            )
            if changed and persist:
                try:
                    names.save_cache(self.config.archive_root, cache)
                except OSError:
                    pass
        name = cache.get(mid, {}).get("name") or ""
        self._names[mid] = (name, self.clock())
        return name or "(unknown)"

    def chat_label(self, chat_id: str) -> str:
        folder = store.find_chat_dir(self.config.archive_root, chat_id)
        if folder is not None:
            label = store.read_state(folder / store.STATE_NAME, chat_id).get("name")
            if isinstance(label, str) and label:
                return label
        if chat_id[:1].lower() == "u":
            name = names.name_map(names.load_cache(self.config.archive_root)).get(chat_id)
            if name:
                return name
        return f"chat {chat_id[:7]}"

    # ---- syncing

    def pending(self) -> bool:
        return bool(self.dirty) or self.dirty_all or bool(self.render_only)

    def flush_due(self) -> bool:
        if not self.pending():
            return False
        now = self.clock()
        if now < self._next_flush_at:
            return False
        if self._first_dirty_at is not None and now - self._first_dirty_at >= self.max_hold:
            return True
        return now - self._last_event_at >= self.settle

    def flush(self) -> None:
        """Bring the archive up to date.

        Chats that only got plain new messages are re-rendered from their local files (no `line`
        call at all, so the session stays free). A full sync (which does call `line`) runs for
        chats with attachments to download, an unknown sender or name, after `resync_required` and
        at start.
        """
        chats = list(self.dirty)
        if self.dirty_all:
            chats += [c for c in self.archived_targets() if c not in chats]
            self.dirty_all = False
        render = [c for c in sorted(self.render_only) if c not in chats]
        self.render_only.clear()
        self._first_dirty_at = None
        for chat_id in render:
            try:
                done = render_local(self.config, chat_id, self.self_mid, datetime.now(UTC))
            except (OSError, ValueError, KeyError) as exc:
                self.emit(f"warning: local render of chat {chat_id[:7]}… failed ({exc})")
                done = False
            if done:
                self.stats.local_renders += 1
            else:  # no usable local archive: fall back to a real sync
                chats.append(chat_id)
        if not chats:
            return
        syncer = self.make_syncer()
        failed = False
        for index, chat_id in enumerate(chats):
            if index:
                _sleep(CHAT_PAUSE)  # leave the session free for a `send` from elsewhere
            try:
                summary = syncer.sync_chat(chat_id)
            except SyncError as exc:
                failed = self._sync_failed(chat_id, exc.error.get("kind"), str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 - one broken chat must not stop the watcher
                failed = self._sync_failed(chat_id, "unexpected", str(exc))
                continue
            self.dirty.pop(chat_id, None)
            self.archiver.forget(chat_id)
            self.stats.chats_synced += 1
            if summary.get("gap"):
                self.stats.gaps.append({"chat_id": chat_id, **summary["gap"]})
                self.emit(
                    f"warning: gap in chat {chat_id[:7]}…: messages between "
                    f"{summary['gap']['from']} and {summary['gap']['to']} are unrecoverable "
                    "(more than 100 arrived while nothing was archiving)"
                )
            if summary.get("new_messages") or summary.get("new_files"):
                self.emit(
                    f"synced chat {chat_id[:7]}…: {summary.get('new_messages', 0)} new message(s), "
                    f"{len(summary.get('new_files', []))} new file(s)"
                )
        if failed:
            self._next_flush_at = self.clock() + SYNC_RETRY_DELAY
            if self.dirty:
                self._first_dirty_at = self.clock()

    def _sync_failed(self, chat_id: str, kind: str | None, message: str) -> bool:
        self.stats.sync_failures += 1
        attempts = self.dirty.get(chat_id, 0) + 1
        if attempts >= MAX_SYNC_ATTEMPTS:
            self.dirty.pop(chat_id, None)
            self.emit(f"warning: giving up syncing chat {chat_id[:7]}… ({kind}): {message}")
        else:
            self.dirty[chat_id] = attempts
            self.emit(f"warning: sync of chat {chat_id[:7]}… failed ({kind}): {message}")
        return True

    # ---- one `line watch` process

    def run_process(
        self,
        start: Callable[[], Any],
        *,
        deadline: float | None = None,
    ) -> ProcessOutcome:
        """Run one `line watch` until it ends or ``deadline`` (a clock value) passes."""
        began = self.clock()
        try:
            proc = start()
        except OSError as exc:
            return ProcessOutcome(
                None, None, "exec_failed", f"cannot start line: {exc.strerror or exc}"
            )
        reader = StreamReader(proc, self.emit)
        try:
            while True:
                if deadline is not None and self.clock() >= deadline:
                    stop_process(proc)
                    return ProcessOutcome("duration", ran_for=self.clock() - began)
                try:
                    item = reader.lines.get(timeout=0.5)
                except queue.Empty:
                    item = b""
                if item is None:
                    break
                if item:
                    self.handle_line(item)
                if self.flush_due():
                    self.flush()
        except BaseException:
            stop_process(proc)
            raise
        stderr = reader.finish()
        code = proc.wait()
        info = runner.classify_error(code, stderr, command="watch")
        kind = None if code == 0 else info.kind
        return ProcessOutcome(
            None, code, kind, info.message if code != 0 else "", self.clock() - began
        )

    def wait(self, seconds: float, deadline: float | None) -> bool:
        """Sleep ``seconds`` in small steps, still syncing; False when the deadline passed."""
        end = self.clock() + seconds
        while self.clock() < end:
            if deadline is not None and self.clock() >= deadline:
                return False
            if self.flush_due():
                self.flush()
            _sleep(min(0.5, max(0.0, end - self.clock())))
        return True

    def run(
        self,
        start: Callable[[], Any],
        *,
        duration: float | None = None,
        max_restarts: int | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Run until stopped. Returns (reason, error): interrupted | duration | fatal | gave_up."""
        deadline = self.clock() + duration if duration else None
        backoff = BACKOFF_START
        try:
            while True:
                outcome = self.run_process(start, deadline=deadline)
                if outcome.stop == "duration":
                    return "duration", None
                decision = plan_restart(outcome.error_kind, outcome.ran_for, backoff)
                error = {"kind": outcome.error_kind, "message": outcome.error_message}
                if decision.fatal:
                    return "fatal", error
                if max_restarts is not None and self.stats.restarts >= max_restarts:
                    return "gave_up", error
                self.stats.restarts += 1
                self.emit(
                    f"line watch exited (code {outcome.exit_code}"
                    f"{', ' + outcome.error_kind if outcome.error_kind else ''}) after "
                    f"{outcome.ran_for:.0f}s; restarting in {decision.delay:g}s"
                )
                backoff = decision.next_backoff
                if not self.wait(decision.delay, deadline):
                    return "duration", None
        except KeyboardInterrupt:
            self.emit("stopping (Ctrl-C)")
            try:
                if self.pending():
                    self.emit("saving the chats that still have new messages (Ctrl-C to skip)")
                    self.flush()
            except KeyboardInterrupt:
                pass
            return "interrupted", None


def stop_process(proc: Any) -> None:
    """Terminate the child; kill it if it does not exit in a few seconds."""
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    except OSError:
        pass


# ---------------------------------------------------------------- VIP resolution


def resolve_vip(
    entries: list[str],
    config: Config,
    emit: Callable[[str], None] = log,
    what: str = "VIP",
) -> dict[str, str]:
    """``{chat_id: label}`` for the VIP / archive entries (chat names or full IDs).

    A name must resolve to exactly one chat; ambiguous or unknown entries are reported on stderr
    and skipped, never guessed.
    """
    from lineq import FULL_ID, resolve_name  # same folder; imported late to keep --help fast

    vip: dict[str, str] = {}
    for entry in dict.fromkeys(e.strip() for e in entries if e.strip()):
        bare = entry[3:] if entry.startswith("id:") else entry
        if FULL_ID.match(bare):
            vip[bare] = ""  # label filled in from the archive when needed
            continue
        payload, _ = resolve_name(entry, config=config)
        if payload.get("status") == "resolved" and payload.get("matched_by") == "substring":
            emit(
                f"warning: {what} entry {entry!r} ignored: it matches only part of the name "
                f"{payload.get('name')!r}; use the exact chat name or the full chat ID"
            )
        elif payload.get("status") == "resolved":
            vip[payload["id"]] = payload.get("name") or entry
            emit(f"{what} chat {entry!r} resolved to {payload['id'][:7]}…")
        else:
            emit(
                f"warning: {what} entry {entry!r} ignored ({payload.get('status')}): use the exact "
                "chat name or the full chat ID"
            )
    return vip


# ---------------------------------------------------------------- command line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="watch.py",
        description="Live-archive LINE chats from `line watch`, catch up after gaps, notify VIPs.",
        epilog="Exit codes: 0 stopped, 1 failed, 2 usage, 3 another line watch is running.",
    )
    parser.add_argument(
        "--vip",
        action="append",
        default=[],
        metavar="NAME_OR_ID",
        help="also notify for this chat (repeatable; adds to [watch] vip_chats in config.toml)",
    )
    parser.add_argument(
        "--archive",
        action="append",
        default=[],
        metavar="NAME_OR_ID",
        help="also archive this chat (repeatable; adds to [watch] archive_chats in config.toml). "
        "Chats that already have an archive folder are always archived; others are ignored",
    )
    parser.add_argument(
        "--archive-all",
        action="store_true",
        help="PRIVACY: archive every chat that receives a message, including private chats you "
        "never synced (off by default)",
    )
    parser.add_argument("--no-notify", action="store_true", help="never show notifications")
    parser.add_argument(
        "--no-initial-sync",
        action="store_true",
        help="do not sync the archived chats when starting (the default catches up on what "
        "arrived while nothing was watching)",
    )
    parser.add_argument(
        "--no-download", action="store_true", help="archive text only; download no attachments"
    )
    parser.add_argument(
        "--duration", type=float, metavar="SECONDS", help="stop by itself after this many seconds"
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=SETTLE,
        metavar="SECONDS",
        help=f"sync a chat after it was quiet this long (default {SETTLE:g}; a longer wait keeps "
        "the LINE session free for other commands)",
    )
    parser.add_argument(
        "--max-restarts",
        type=int,
        metavar="N",
        help="give up after restarting `line watch` N times (default: keep trying)",
    )
    parser.add_argument(
        "--log", metavar="FILE", help="append diagnostics to FILE (for pythonw / Task Scheduler)"
    )
    parser.add_argument("--json", action="store_true", help="accepted for symmetry; always JSON")
    return parser


def _prepare_streams(log_path: str | None) -> None:
    """--log sends diagnostics to a file (under pythonw there is no stderr to look at)."""
    runner.configure_stdio()
    if log_path:
        sys.stderr = open(  # noqa: SIM115 - lives for the whole process
            log_path, "a", encoding="utf-8", errors="replace", buffering=1
        )


def _raise_interrupt(_signum: int, _frame: Any) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _prepare_streams(args.log)
    except OSError as exc:
        print(f"cannot open the log file: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.duration is not None and args.duration <= 0:
        build_parser().error("--duration must be positive")
    if args.settle < 0 or (args.max_restarts is not None and args.max_restarts < 0):
        build_parser().error("--settle and --max-restarts must not be negative")

    config, config_error = load_config_safe()
    if config_error:
        log(f"warning: {config_error}")

    located = runner.find_line(config)
    if located is None:
        runner.emit(
            {
                "stopped": "fatal",
                "error": {
                    "kind": "binary_missing",
                    "message": "The `line` executable was not found.",
                    "hint": "Run setup.py --check, then --install (needs the user's consent).",
                },
            }
        )
        return EXIT_FAIL

    options = Options(
        types=config.download_types,
        max_bytes=config.download_max_bytes,
        download=not args.no_download,
    )

    def make_syncer() -> Syncer:
        return Syncer(config=config, options=options, binary=located.path)

    vip: dict[str, str] = {}
    notifier: Notifier | None = None
    if not args.no_notify:
        vip = resolve_vip([*config.watch_vip_chats, *args.vip], config)
        notifier = Notifier() if vip else None

    archive_chats = resolve_vip(
        [*config.watch_archive_chats, *args.archive], config, what="archive"
    )
    loop = WatchLoop(
        config=config,
        make_syncer=make_syncer,
        archiver=Archiver(config.archive_root),
        vip=vip,
        notifier=notifier,
        self_mid=make_syncer().self_mid(),
        archive_chats=archive_chats,
        archive_all=args.archive_all,
        download_types=() if args.no_download else config.download_types,
        initial_sync=not args.no_initial_sync,
        settle=args.settle,
    )
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _raise_interrupt)

    scope = (
        "EVERY chat that receives a message (--archive-all)"
        if args.archive_all
        else f"{len(loop.archived_targets())} chat(s) that are already archived or allowed"
    )
    log(f"watching with {located.path}; archive: {config.archive_root}; archiving {scope}")
    log("Ctrl-C to stop")
    reason, error = loop.run(
        lambda: runner.start_line(located.path, ["watch", "--json"]),
        duration=args.duration,
        max_restarts=args.max_restarts,
    )
    summary: dict[str, Any] = {"stopped": reason, "error": error, **loop.stats.to_dict()}
    if error and error.get("kind") == "watcher_running":
        summary["message"] = (
            "Another `line watch` is already running (only one can run at a time). Stop it, or "
            "keep using it, and start watch.py again."
        )
    runner.emit(summary)
    if reason in ("interrupted", "duration"):
        return EXIT_OK
    return EXIT_DECIDE if error and error.get("kind") == "watcher_running" else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
