"""watch.py: event handling, de-duplication, resync, restart/backoff decisions, VIP filtering."""

from __future__ import annotations

import io
import json
import shutil
import threading
import time

import pytest
from linecore import store
from linecore.config import config_path, load_config_safe

from .helpers import load_script, rule
from .sync_helpers import (
    ALICE,
    BOB,
    CHAT,
    ME,
    archive_dir,
    at,
    base_rules,
    messages_rule,
    msg,
    read_jsonl,
)

watch = load_script("watch")
SyncError = watch.SyncError

OTHER = "C" + "E" * 43
WATCHER_RUNNING = "line: another line watch is already running"


def event(message, chat_id=CHAT, type_=26, revision="10"):
    return {
        "event": "message",
        "revision": revision,
        "type": type_,
        "chat_id": chat_id,
        "message": message,
    }


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeSyncer:
    def __init__(self, fail=None):
        self.calls: list[str] = []
        self.fail = fail or {}

    def sync_chat(self, chat_id, name_hint=None):
        self.calls.append(chat_id)
        if chat_id in self.fail:
            raise SyncError({"kind": "network", "message": "offline"})
        return {"chat_id": chat_id, "new_messages": 1, "new_files": [], "gap": None}


class RecordingNotifier:
    def __init__(self):
        self.shown: list[tuple[str, str]] = []
        self.fallbacks: list[str | None] = []

    def notify(self, title, body, *, fallback=None):
        self.shown.append((title, body))
        self.fallbacks.append(fallback)
        return True


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    pauses = []
    monkeypatch.setattr(watch, "_sleep", pauses.append)
    return pauses


def make_loop(archive_root, *, syncer=None, vip=None, notifier=None, clock=None, **kw):
    config, _ = load_config_safe()
    syncer = syncer or FakeSyncer()
    clock = clock or Clock()
    loop = watch.WatchLoop(
        config=config,
        make_syncer=lambda: syncer,
        archiver=watch.Archiver(config.archive_root),
        vip=vip or {},
        notifier=notifier,
        self_mid=ME,
        initial_sync=kw.pop("initial_sync", False),
        archive_all=kw.pop("archive_all", True),  # mechanics tests; privacy tests turn it off
        download_types=kw.pop("download_types", ("image", "video", "audio", "file")),
        clock=clock,
        wall_ms=lambda: at(5),
        emit=lambda _m: None,
        **kw,
    )
    loop.test_syncer, loop.test_clock = syncer, clock
    return loop


# ------------------------------------------------------------------ pure decisions


def test_plan_restart_backs_off_exponentially_up_to_the_cap():
    delay, delays = watch.BACKOFF_START, []
    for _ in range(10):
        plan = watch.plan_restart("network", 1.0, delay, cap=30.0)
        assert not plan.fatal
        delays.append(plan.delay)
        delay = plan.next_backoff
    assert delays == [2, 4, 8, 16, 30, 30, 30, 30, 30, 30]


def test_plan_restart_resets_after_a_stable_run_and_stops_on_fatal_errors():
    assert watch.plan_restart("error", watch.STABLE_AFTER, 64.0).delay == watch.BACKOFF_START
    assert watch.plan_restart(None, 5.0, 8.0).delay == 8.0  # a clean exit is restarted too
    for kind in ("watcher_running", "not_logged_in", "binary_missing", "storage_invalid"):
        assert watch.plan_restart(kind, 1.0, 2.0).fatal


def test_should_notify_only_for_fresh_incoming_vip_messages():
    vip = {CHAT: "Team"}
    ok = event(msg(1, 4, "hi", ALICE))
    now = at(5)
    assert watch.should_notify(ok, vip, ME, now_ms=now)
    assert not watch.should_notify(ok, {OTHER: "x"}, ME, now_ms=now)  # not a VIP chat
    assert not watch.should_notify(event(msg(2, 4, "mine", ME)), vip, ME, now_ms=now)
    assert not watch.should_notify(event(msg(3, 4, "sent", ALICE), type_=25), vip, ME, now_ms=now)
    old = event(msg(4, 0, "backlog", ALICE))
    assert not watch.should_notify(old, vip, ME, now_ms=at(0) + 3_600_000)
    assert not watch.should_notify({"event": "operation", "revision": "1"}, vip, ME, now_ms=now)


def test_preview_text_attachments_and_truncation():
    assert watch.preview({"text": "a  b\nc"}) == "a b c"
    assert watch.preview({"text": "x" * 200}).endswith("…")
    assert len(watch.preview({"text": "x" * 200})) == watch.PREVIEW_CHARS
    assert watch.preview({"status": "attachment", "content_type": 1}) == "[Image]"
    assert watch.preview({"status": "decryption_failed"}) == "[could not be decrypted]"
    assert watch.preview({"content_type": 7}) == "[message]"


def test_archived_chat_ids_reads_every_folder_layout(archive_root):
    for name in (f"Family__{CHAT}", OTHER, f"{'U' + 'A' * 43}__Old name", "_line_emoji", "notes"):
        (archive_root / name).mkdir(parents=True)
    (archive_root / "contacts.json").write_text("{}", encoding="utf-8")
    assert sorted(watch.archived_chat_ids(archive_root)) == sorted([CHAT, OTHER, "U" + "A" * 43])


def test_parse_event_ignores_garbage():
    assert watch.parse_event(b'{"event":"operation","revision":"1"}')["event"] == "operation"
    assert watch.parse_event(b"") is None
    assert watch.parse_event(b"not json") is None
    assert watch.parse_event(b"[1]") is None


# ------------------------------------------------------------------ archive writes


def test_archiver_appends_dedupes_and_uses_an_existing_named_folder(archive_root):
    archiver = watch.Archiver(archive_root)
    assert archiver.add(CHAT, msg(1, 0, "first"), create=True) is True
    assert archiver.add(CHAT, msg(1, 0, "first"), create=True) is False  # same ID: ignored
    assert archiver.add(CHAT, msg(2, 1, "second"), create=True) is True
    folder = archive_dir(archive_root)
    assert folder.name == CHAT  # no name known yet: bare ID, like sync.py
    assert [r["id"] for r in read_jsonl(folder / "messages.jsonl")] == ["1", "2"]

    folder.rename(archive_root / f"Family__{CHAT}")  # a later sync names the folder
    fresh = watch.Archiver(archive_root)
    assert fresh.add(CHAT, msg(2, 1, "second"), create=False) is False  # known from the file
    assert fresh.add(CHAT, msg(3, 2, "third"), create=False) is True
    assert [p.name for p in archive_root.iterdir()] == [f"Family__{CHAT}"]
    assert len(read_jsonl(archive_root / f"Family__{CHAT}" / "messages.jsonl")) == 3


# ------------------------------------------------------------------ events


def test_message_events_are_archived_once_and_mark_the_chat(archive_root):
    loop = make_loop(archive_root)
    first = event(msg(1, 4, "hello", ALICE))
    loop.handle_event(first)
    loop.handle_event(first)  # the CLI may replay an event after a restart
    loop.handle_event({"event": "operation", "revision": "11", "type": 5})
    assert loop.stats.messages_archived == 1 and loop.stats.duplicates == 1
    assert loop.stats.events == 3
    assert list(loop.dirty) == [CHAT]
    assert [r["id"] for r in read_jsonl(archive_dir(archive_root) / "messages.jsonl")] == ["1"]


def test_malformed_message_events_are_ignored(archive_root):
    loop = make_loop(archive_root)
    for bad in (
        {"event": "message", "chat_id": CHAT},
        {"event": "message", "message": {"id": "1"}},
        {"event": "message", "chat_id": CHAT, "message": {"text": "no id"}},
    ):
        loop.handle_event(bad)
    assert loop.stats.messages_archived == 0 and not loop.pending()


def test_chat_is_synced_after_it_settles_not_before(archive_root):
    clock = Clock()
    loop = make_loop(archive_root, clock=clock, settle=5.0, max_hold=60.0)
    loop.handle_event(event(msg(1, 4, "a", ALICE)))
    assert not loop.flush_due()
    clock.now += 4
    loop.handle_event(event(msg(2, 4, "b", ALICE)))  # still chatty: the quiet timer restarts
    clock.now += 4
    assert not loop.flush_due()
    clock.now += 2
    assert loop.flush_due()
    loop.flush()
    assert loop.test_syncer.calls == [CHAT] and not loop.pending()
    assert loop.stats.chats_synced == 1


def test_a_constantly_busy_chat_is_still_synced_after_max_hold(archive_root):
    clock = Clock()
    loop = make_loop(archive_root, clock=clock, settle=5.0, max_hold=60.0)
    for i in range(40):
        loop.handle_event(event(msg(i + 1, 4, "x", ALICE)))
        clock.now += 2
    assert loop.flush_due()


def test_resync_required_syncs_every_archived_chat(archive_root):
    (archive_root / f"Family__{CHAT}").mkdir(parents=True)
    (archive_root / OTHER).mkdir()
    (archive_root / "_line_emoji").mkdir()
    loop = make_loop(archive_root, settle=0.0)
    loop.handle_event({"event": "resync_required", "revision": "99"})
    assert loop.stats.resyncs == 1 and loop.flush_due()
    loop.flush()
    assert sorted(loop.test_syncer.calls) == sorted([CHAT, OTHER])
    assert not loop.pending()


def test_initial_sync_catches_up_all_archived_chats(archive_root):
    (archive_root / CHAT).mkdir(parents=True)
    loop = make_loop(archive_root, initial_sync=True, settle=0.0)
    assert loop.flush_due()
    loop.flush()
    assert loop.test_syncer.calls == [CHAT]


def test_failed_sync_is_retried_later_then_given_up(archive_root):
    clock = Clock()
    syncer = FakeSyncer(fail={CHAT: True})
    loop = make_loop(archive_root, syncer=syncer, clock=clock, settle=0.0)
    loop.handle_event(event(msg(1, 4, "a", ALICE)))
    for attempt in range(1, watch.MAX_SYNC_ATTEMPTS + 1):
        assert loop.flush_due()
        loop.flush()
        assert loop.stats.sync_failures == attempt
        assert not loop.flush_due()  # waits SYNC_RETRY_DELAY before the next attempt
        clock.now += watch.SYNC_RETRY_DELAY + 1
    assert not loop.pending()  # given up; the message is still safe in messages.jsonl
    assert len(syncer.calls) == watch.MAX_SYNC_ATTEMPTS
    assert read_jsonl(archive_dir(archive_root) / "messages.jsonl")[0]["id"] == "1"


def test_gap_reported_by_sync_is_collected(archive_root):
    class GapSyncer(FakeSyncer):
        def sync_chat(self, chat_id, name_hint=None):
            summary = super().sync_chat(chat_id)
            summary["gap"] = {"after_id": "1", "before_id": "9", "from": "A", "to": "B"}
            return summary

    loop = make_loop(archive_root, syncer=GapSyncer(), settle=0.0)
    loop.handle_event(event(msg(1, 4, "a", ALICE)))
    loop.flush()
    assert loop.stats.gaps == [
        {"chat_id": CHAT, "after_id": "1", "before_id": "9", "from": "A", "to": "B"}
    ]


# ------------------------------------------------------------------ VIP notifications


def test_vip_message_notifies_with_chat_sender_and_preview(archive_root):
    store.write_json(
        archive_root / "contacts.json",
        {"schema": 1, "names": {ALICE: {"name": "Alice", "ts": 4_000_000_000_000}}},
    )
    notifier = RecordingNotifier()
    loop = make_loop(archive_root, vip={CHAT: "Project Team"}, notifier=notifier)
    loop.handle_event(event(msg(1, 4, "Lunch at noon?\nBring Bob", ALICE)))
    assert notifier.shown == [("Project Team", "Alice: Lunch at noon? Bring Bob")]
    assert notifier.fallbacks == ["new message in Project Team from Alice"]  # no message text
    assert loop.stats.notifications == 1


def test_non_vip_own_and_replayed_messages_do_not_notify(archive_root):
    notifier = RecordingNotifier()
    loop = make_loop(archive_root, vip={CHAT: "Team"}, notifier=notifier)
    loop.handle_event(event(msg(1, 4, "elsewhere", ALICE), chat_id=OTHER))
    loop.handle_event(event(msg(2, 4, "I wrote this", ME)))
    loop.handle_event(event(msg(3, 4, "echo of my phone", ALICE), type_=25))
    assert notifier.shown == []
    real = event(msg(4, 4, "real", ALICE))
    loop.handle_event(real)
    loop.handle_event(real)  # replayed after a restart: no second toast
    assert len(notifier.shown) == 1


def test_no_notifier_means_no_lookups(archive_root, fake_line):
    fake_line.scenario([])
    loop = make_loop(archive_root, vip={CHAT: "Team"}, notifier=None)
    loop.handle_event(event(msg(1, 4, "hi", ALICE)))
    assert fake_line.calls() == []


def test_chat_label_falls_back_to_state_name_then_short_id(archive_root):
    folder = archive_root / f"Family__{CHAT}"
    folder.mkdir(parents=True)
    store.write_json(folder / "state.json", {**store.new_state(CHAT), "name": "Family"})
    loop = make_loop(archive_root)
    assert loop.chat_label(CHAT) == "Family"
    assert loop.chat_label(OTHER).startswith("chat ")


def test_vip_config_and_cli_resolution(fake_line, archive_root):
    config, _ = load_config_safe()
    fake_line.scenario(
        [
            rule(["chats", "--search", "Team", "--json"], stdout=[
                {"id": CHAT, "type": 0, "unread_count": 0, "name": "Team", "updated_at": 1}
            ]),
            rule(["contacts", "--search", "Team", "--json"], stdout=[]),
            rule(["chats", "--search", "Nobody", "--json"], stdout=[]),
            rule(["contacts", "--search", "Nobody", "--json"], stdout=[]),
        ]
    )
    messages = []
    vip = watch.resolve_vip(["Team", "Nobody", OTHER, "Team"], config, messages.append)
    assert vip == {CHAT: "Team", OTHER: ""}
    assert any("Nobody" in m and "ignored" in m for m in messages)


# ------------------------------------------------------------------ notifier


class Completed:
    def __init__(self, code=0):
        self.returncode = code


def test_notifier_uses_powershell_toast_with_text_in_env_not_in_script():
    calls, logs = [], []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Completed(0)

    notifier = watch.Notifier(windows=True, run=run, emit=logs.append)
    assert notifier.notify("Team <&>", "Alice: '\"hi\"\x07") is True
    (cmd, kwargs), = calls
    assert "-EncodedCommand" in cmd and "Team" not in " ".join(cmd)
    assert kwargs["env"]["LINE_SKILL_TOAST_TITLE"] == "Team <&>"
    assert kwargs["env"]["LINE_SKILL_TOAST_BODY"] == "Alice: '\"hi\" "  # control char removed
    assert logs == []


def test_notifier_falls_back_to_a_log_line():
    logs = []

    def boom(cmd, **kwargs):
        raise FileNotFoundError("powershell")

    assert watch.Notifier(windows=True, run=boom, emit=logs.append).notify("T", "B") is False
    assert logs[-1] == "[VIP] T" and "failed" in logs[0]  # the body (preview) is never logged

    logs.clear()
    bad = watch.Notifier(windows=True, run=lambda *a, **k: Completed(1), emit=logs.append)
    assert bad.notify("T", "secret text", fallback="new message in T from A") is False
    assert logs[-1] == "[VIP] new message in T from A" and "secret" not in " ".join(logs)

    logs.clear()

    def never(*a, **k):
        raise AssertionError("must not run PowerShell off Windows")

    assert watch.Notifier(windows=False, run=never, emit=logs.append).notify("T", "B") is False
    assert logs == ["[VIP] T"]


# ------------------------------------------------------------------ process loop


class FakeProc:
    """Stands in for the `line watch` Popen: scripted stdout lines, stderr and exit code."""

    def __init__(self, lines=(), stderr="", code=0, hang=False):
        self.stdout = self._stream(lines, hang)
        self.stderr = io.BytesIO(stderr.encode())
        self.code = code
        self.terminated = False
        self._release = threading.Event()
        self.hang = hang

    def _stream(self, lines, hang):
        proc = self

        def gen():
            for line in lines:
                yield (json.dumps(line) + "\n").encode() if isinstance(line, dict) else line
            if hang:
                proc._release.wait(10)

        return gen()

    def poll(self):
        return None if self.hang and not self.terminated else self.code

    def terminate(self):
        self.terminated = True
        self._release.set()

    def kill(self):
        self.terminate()

    def wait(self, timeout=None):
        return self.code


def proc_factory(*procs):
    queue = list(procs)

    def start():
        return queue.pop(0)

    start.remaining = queue
    return start


def test_run_archives_events_and_restarts_with_backoff(archive_root, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(watch, "_sleep", clock.sleep)
    loop = make_loop(archive_root, clock=clock, settle=0.0)
    start = proc_factory(
        FakeProc([event(msg(1, 4, "one", ALICE))], "line: dial tcp: i/o timeout", 1),
        FakeProc([event(msg(2, 5, "two", ALICE))], "line: dial tcp: i/o timeout", 1),
        FakeProc([], "", 0),
    )
    reason, error = loop.run(start, max_restarts=2)
    assert reason == "gave_up" and loop.stats.restarts == 2
    assert loop.stats.messages_archived == 2
    # waited 2s then 4s before the restarts (the fake clock only moves in the backoff waits)
    assert clock.now == 1000.0 + 2 + 4
    assert [r["id"] for r in read_jsonl(archive_dir(archive_root) / "messages.jsonl")] == ["1", "2"]
    assert loop.test_syncer.calls  # synced while running


def test_another_watcher_is_fatal_and_not_restarted(archive_root, monkeypatch):
    monkeypatch.setattr(watch, "_sleep", lambda s: pytest.fail("must not wait to restart"))
    loop = make_loop(archive_root)
    start = proc_factory(FakeProc([], WATCHER_RUNNING, 1), FakeProc([], "", 0))
    reason, error = loop.run(start)
    assert reason == "fatal" and error["kind"] == "watcher_running"
    assert len(start.remaining) == 1 and loop.stats.restarts == 0


def test_login_loss_is_fatal(archive_root):
    loop = make_loop(archive_root)
    gone = "line: no saved LINE session; run line login"
    reason, error = loop.run(proc_factory(FakeProc([], gone, 1)))
    assert reason == "fatal" and error["kind"] == "not_logged_in"


def test_missing_binary_is_fatal(archive_root):
    loop = make_loop(archive_root)

    def start():
        raise FileNotFoundError(2, "No such file")

    reason, error = loop.run(start)
    assert reason == "fatal" and error["kind"] == "exec_failed"


def test_duration_stops_a_running_watcher(archive_root):
    proc = FakeProc([event(msg(1, 4, "x", ALICE))], hang=True)
    loop = make_loop(archive_root, clock=time.monotonic)
    reason, error = loop.run(proc_factory(proc), duration=0.3)
    assert reason == "duration" and proc.terminated and error is None
    assert loop.stats.messages_archived == 1


def test_ctrl_c_stops_cleanly_and_saves_pending_chats(archive_root):
    proc = FakeProc([event(msg(1, 4, "x", ALICE)), event(msg(2, 5, "y", ALICE))], hang=True)
    loop = make_loop(archive_root, settle=1000.0)
    original = loop.handle_line
    seen = []

    def interrupt(raw):
        original(raw)
        seen.append(raw)
        if len(seen) == 2:
            raise KeyboardInterrupt

    loop.handle_line = interrupt
    reason, error = loop.run(proc_factory(proc))
    assert reason == "interrupted" and proc.terminated
    assert loop.test_syncer.calls == [CHAT]  # pending chat flushed on the way out
    assert loop.stats.messages_archived == 2


# ------------------------------------------------------------------ main() with a fake `line`


def watch_rules(*extra, stdout="", exit=0, stderr=""):
    return base_rules(
        *extra,
        rule(["watch", "--json"], stdout=stdout, exit=exit, stderr=stderr),
        names={ALICE: "Alice", BOB: "Bob"},
    )


def test_main_archives_events_from_the_subprocess_and_syncs(fake_line, archive_root, capsys):
    events = "\n".join(
        json.dumps(e)
        for e in (
            event(msg(1, 0, "hello", ALICE)),
            {"event": "operation", "revision": "11", "type": 5},
            event(msg(1, 0, "hello", ALICE)),
            event(msg(2, 1, "again", BOB)),
        )
    )
    fake_line.scenario(
        watch_rules(messages_rule([msg(1, 0, "hello", ALICE), msg(2, 1, "again", BOB)]),
                    stdout=events + "\n")
    )
    code = watch.main(
        ["--archive", CHAT, "--max-restarts", "0", "--settle", "0", "--no-initial-sync"]
        + ["--no-notify"]
    )
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["stopped"] == "gave_up"  # the fake `line watch` simply ends
    # the first event is archived, the sync then fetches message 2, so later events are duplicates
    assert out["messages_archived"] + out["duplicate_messages"] == 3
    assert out["chats_synced"] >= 1
    folder = archive_dir(archive_root)
    assert [r["id"] for r in read_jsonl(folder / "messages.jsonl")] == ["1", "2"]
    assert "again" in (folder / "messages.md").read_text(encoding="utf-8")  # the sync rendered it
    assert ["watch", "--json"] in fake_line.argv_calls()


def test_main_exits_3_when_another_watcher_runs(fake_line, archive_root, capsys):
    fake_line.scenario(watch_rules(exit=1, stderr=WATCHER_RUNNING))
    code = watch.main(["--no-initial-sync"])
    out = json.loads(capsys.readouterr().out)
    assert code == 3 and out["stopped"] == "fatal"
    assert out["error"]["kind"] == "watcher_running" and "already running" in out["message"]
    assert [a for a in fake_line.argv_calls() if a[:1] == ["watch"]] == [["watch", "--json"]]


def test_main_without_line_binary_fails_with_a_clear_error(archive_root, capsys):
    code = watch.main([])
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["error"]["kind"] == "binary_missing"


def test_main_vip_from_config_notifies_for_a_fresh_incoming_message(
    fake_line, archive_root, capsys, monkeypatch
):
    cfg = config_path()
    cfg.parent.mkdir(parents=True)
    cfg.write_text(f'[watch]\nvip_chats = ["{CHAT}"]\n', encoding="utf-8")
    notifier = RecordingNotifier()
    monkeypatch.setattr(watch, "Notifier", lambda: notifier)  # never a real toast in tests
    fresh = msg(7, 0, "urgent", ALICE, created_time=int(time.time() * 1000))
    fake_line.scenario(
        watch_rules(
            messages_rule([fresh]),
            stdout=json.dumps(event(fresh, revision="1")) + "\n",
        )
    )
    code = watch.main(["--max-restarts", "0", "--settle", "0", "--no-initial-sync"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["notifications"] == 1
    assert [body for _title, body in notifier.shown] == ["Alice: urgent"]


def test_main_no_notify_skips_vip_resolution(fake_line, archive_root, capsys):
    fake_line.scenario(watch_rules(stdout=""))
    watch.main(["--max-restarts", "0", "--no-initial-sync", "--no-notify", "--vip", "Anyone"])
    capsys.readouterr()
    assert not any(a[:1] in (["chats"], ["contacts"]) for a in fake_line.argv_calls())


def test_help_lists_the_options(capsys):
    with pytest.raises(SystemExit) as exc:
        watch.main(["--help"])
    assert exc.value.code == 0
    text = capsys.readouterr().out
    for flag in ("--vip", "--duration", "--no-notify", "--max-restarts", "--log"):
        assert flag in text


def test_watch_config_default_is_empty(archive_root):
    config, _ = load_config_safe()
    assert config.to_dict()["watch"] == {"vip_chats": [], "archive_chats": []}


# ------------------------------------------------------------------ privacy: only chosen chats


def test_unarchived_chats_are_ignored_nothing_written(archive_root):
    loop = make_loop(archive_root, archive_all=False)
    logs = []
    loop.emit = logs.append
    loop.handle_event(event(msg(1, 4, "private words", ALICE), chat_id=OTHER))
    assert loop.stats.ignored == 1 and loop.stats.messages_archived == 0
    assert not loop.pending() and not archive_root.exists()  # no folder, no file, no cache
    assert logs == []  # not logged either


def test_chats_with_an_archive_folder_are_archived(archive_root):
    (archive_root / f"Family__{CHAT}").mkdir(parents=True)
    loop = make_loop(archive_root, archive_all=False)
    loop.handle_event(event(msg(1, 4, "hello", ALICE)))
    loop.handle_event(event(msg(2, 4, "elsewhere", ALICE), chat_id=OTHER))
    assert loop.stats.messages_archived == 1 and loop.stats.ignored == 1
    saved = read_jsonl(archive_root / f"Family__{CHAT}" / "messages.jsonl")
    assert [r["id"] for r in saved] == ["1"]
    assert [p.name for p in archive_root.iterdir()] == [f"Family__{CHAT}"]


def test_allowlisted_chat_is_archived_without_a_folder(archive_root):
    loop = make_loop(archive_root, archive_all=False, archive_chats={CHAT: "Family"})
    loop.handle_event(event(msg(1, 4, "hello", ALICE)))
    loop.handle_event(event(msg(2, 4, "private", ALICE), chat_id=OTHER))
    assert loop.stats.messages_archived == 1 and loop.stats.ignored == 1
    assert [p.name for p in archive_root.iterdir()] == [CHAT]


def test_resync_and_initial_sync_only_touch_archived_or_allowed_chats(archive_root):
    (archive_root / f"Family__{CHAT}").mkdir(parents=True)
    third = "R" + "F" * 43
    loop = make_loop(
        archive_root,
        archive_all=False,
        archive_chats={third: "", CHAT: "Family"},
        initial_sync=True,
        settle=0.0,
    )
    loop.handle_event({"event": "resync_required", "revision": "9"})
    loop.flush()
    assert loop.test_syncer.calls == [CHAT, third]  # never OTHER or any unknown chat


def test_a_pause_separates_chats_in_one_sync_run(archive_root, no_waiting):
    (archive_root / CHAT).mkdir(parents=True)
    (archive_root / OTHER).mkdir()
    loop = make_loop(archive_root, archive_all=False, initial_sync=True, settle=0.0)
    loop.flush()
    assert len(loop.test_syncer.calls) == 2 and no_waiting == [watch.CHAT_PAUSE]


def test_vip_notifies_for_an_unarchived_chat_without_touching_the_disk(archive_root, fake_line):
    fake_line.scenario(
        [rule(prefix=["contacts", "--mid"], stdout=[
            {"mid": ALICE, "effectiveDisplayName": "Alice", "displayName": "Alice",
             "status": "resolved"}
        ])]
    )
    notifier = RecordingNotifier()
    loop = make_loop(archive_root, archive_all=False, vip={CHAT: "Team"}, notifier=notifier)
    loop.handle_event(event(msg(1, 4, "ping", ALICE)))
    assert notifier.shown == [("Team", "Alice: ping")]
    assert "ping" not in notifier.fallbacks[0]
    assert loop.stats.ignored == 1 and not loop.pending()
    assert not archive_root.exists()  # no archive folder and no name cache were written


def test_default_settle_is_relaxed_to_free_the_session():
    assert watch.SETTLE >= 15 and watch.MAX_HOLD > watch.SETTLE and watch.CHAT_PAUSE > 0


def test_main_ignores_unarchived_chats_by_default(fake_line, archive_root, capsys):
    fake_line.scenario(
        watch_rules(stdout=json.dumps(event(msg(1, 0, "secret", ALICE))) + "\n")
    )
    code = watch.main(["--max-restarts", "0", "--settle", "0", "--no-notify"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["ignored_messages"] == 1 and out["messages_archived"] == 0
    assert not archive_root.exists()
    assert not any(a[:1] == ["messages"] for a in fake_line.argv_calls())


def test_main_archive_all_is_an_explicit_opt_in(fake_line, archive_root, capsys):
    fake_line.scenario(
        watch_rules(
            messages_rule([msg(1, 0, "x", ALICE)]),
            stdout=json.dumps(event(msg(1, 0, "x", ALICE))) + "\n",
        )
    )
    watch.main(["--archive-all", "--max-restarts", "0", "--settle", "0", "--no-initial-sync",
                "--no-notify"])
    out = json.loads(capsys.readouterr().out)
    assert out["messages_archived"] == 1 and archive_dir(archive_root)


# ------------------------------------------------------------------ review fixes


def test_vip_and_archive_entries_reject_a_partial_name_match(fake_line, archive_root):
    config, _ = load_config_safe()
    fake_line.scenario(
        [
            rule(
                ["chats", "--search", "Tea", "--json"],
                stdout=[{"id": CHAT, "type": 0, "unread_count": 0, "name": "Team"}],
            ),
            rule(["contacts", "--search", "Tea", "--json"], stdout=[]),
        ]
    )
    messages = []
    for what in ("VIP", "archive"):
        assert watch.resolve_vip(["Tea"], config, messages.append, what=what) == {}
    assert all("only part of the name" in m and "full chat ID" in m for m in messages)


def test_deleted_archive_folder_is_not_recreated_by_the_next_message(archive_root):
    folder = archive_root / f"Family__{CHAT}"
    folder.mkdir(parents=True)
    clock = Clock()
    loop = make_loop(archive_root, archive_all=False, clock=clock)
    loop.handle_event(event(msg(1, 4, "kept", ALICE)))
    assert loop.stats.messages_archived == 1
    shutil.rmtree(folder)  # the user deletes the archive while watch.py runs
    loop.handle_event(event(msg(2, 4, "after delete", ALICE)))  # still inside the folder TTL
    assert not archive_root.exists() or not list(archive_root.iterdir())  # nothing recreated
    assert loop.stats.ignored == 1
    clock.now += watch.FOLDER_TTL + 1
    loop.handle_event(event(msg(3, 4, "later", ALICE)))
    assert loop.stats.ignored == 2 and loop.stats.messages_archived == 1
    assert not archive_root.exists() or not list(archive_root.iterdir())


def test_allowlisted_chat_is_recreated_after_deletion(archive_root):
    archiver = watch.Archiver(archive_root)
    assert archiver.add(CHAT, msg(1, 0, "a"), create=True) is True
    shutil.rmtree(archive_root / CHAT)
    assert archiver.add(CHAT, msg(1, 0, "a"), create=False) is None  # not allowed to recreate
    assert archiver.add(CHAT, msg(1, 0, "a"), create=True) is True  # stale ID cache was dropped


def test_folder_set_is_scanned_at_most_once_per_ttl(archive_root, monkeypatch):
    scans = []
    real = watch.archived_chat_ids
    monkeypatch.setattr(watch, "archived_chat_ids", lambda root: scans.append(1) or real(root))
    clock = Clock()
    loop = make_loop(archive_root, archive_all=False, clock=clock)
    for i in range(20):  # a chatty private chat that is not archived
        loop.handle_event(event(msg(i + 1, 4, "x", ALICE), chat_id=OTHER))
    assert len(scans) == 1 and loop.stats.ignored == 20
    clock.now += watch.FOLDER_TTL + 1
    (archive_root / f"New__{OTHER}").mkdir(parents=True)  # archived meanwhile: noticed after TTL
    loop.handle_event(event(msg(99, 4, "y", ALICE), chat_id=OTHER))
    assert len(scans) == 2 and loop.stats.messages_archived == 1


def test_sender_name_lookup_is_remembered_for_the_process(archive_root, fake_line):
    fake_line.scenario(
        [
            rule(
                prefix=["contacts", "--mid"],
                stdout=[
                    {
                        "mid": ALICE,
                        "effectiveDisplayName": "Alice",
                        "displayName": "Alice",
                        "status": "resolved",
                    }
                ],
            )
        ]
    )
    notifier = RecordingNotifier()
    loop = make_loop(archive_root, archive_all=False, vip={CHAT: "Team"}, notifier=notifier)
    for i in range(3):  # three messages from one sender in an unarchived VIP chat
        loop.handle_event(event(msg(i + 1, 4, f"m{i}", ALICE)))
    assert [b for _t, b in notifier.shown] == ["Alice: m0", "Alice: m1", "Alice: m2"]
    asked = [a for a in fake_line.argv_calls() if a[:1] == ["contacts"]]
    assert len(asked) == 1
    assert not archive_root.exists()


def named_chat(archive_root, sender=ALICE, name="Alice"):
    """A chat that is archived, named, and whose sender is known: nothing needs `line`."""
    folder = archive_root / f"Family__{CHAT}"
    folder.mkdir(parents=True)
    store.write_json(folder / "state.json", {**store.new_state(CHAT), "name": "Family"})
    store.write_json(
        archive_root / "contacts.json",
        {"schema": 1, "names": {sender: {"name": name, "ts": 4_000_000_000_000}}},
    )
    return folder


def test_plain_text_messages_cause_no_line_calls_and_rewrite_messages_md(archive_root, fake_line):
    fake_line.scenario([])
    folder = named_chat(archive_root)
    loop = make_loop(archive_root, archive_all=False, settle=0.0)
    for i in range(3):
        loop.handle_event(event(msg(i + 1, 4 + i, f"plain text {i}", ALICE)))
    assert loop.flush_due()
    loop.flush()
    assert loop.test_syncer.calls == [] and fake_line.calls() == []  # the session stays free
    assert loop.stats.local_renders == 1 and loop.stats.messages_archived == 3
    md = (folder / "messages.md").read_text(encoding="utf-8")
    assert "plain text 0" in md and "plain text 2" in md and "**Alice**" in md
    assert not loop.pending()
    state = store.read_state(folder / "state.json", CHAT)
    assert state["newest_message_id"] == "3"


def test_attachment_needs_a_full_sync_but_not_when_downloads_are_off(archive_root):
    named_chat(archive_root)
    photo = event(att_msg(5, 5))
    loop = make_loop(archive_root, archive_all=False, settle=0.0)
    loop.handle_event(photo)
    loop.flush()
    assert loop.test_syncer.calls == [CHAT]  # LINE expires attachments: download now

    off = make_loop(archive_root, archive_all=False, settle=0.0, download_types=())
    off.handle_event(event(att_msg(6, 6)))
    off.flush()
    assert off.test_syncer.calls == [] and off.stats.local_renders == 1


def test_unknown_sender_or_unnamed_chat_triggers_one_sync_then_local(archive_root):
    folder = archive_root / CHAT  # bare-ID folder: the chat has no name yet
    folder.mkdir(parents=True)
    loop = make_loop(archive_root, archive_all=False, settle=0.0)
    loop.handle_event(event(msg(1, 4, "first", ALICE)))
    loop.flush()
    assert loop.test_syncer.calls == [CHAT]
    loop.test_syncer.calls.clear()
    # the (fake) sync did not name anything, but the same chat/sender is not asked for twice
    loop.handle_event(event(msg(2, 5, "second", ALICE)))
    loop.flush()
    assert loop.test_syncer.calls == [] and loop.stats.local_renders == 1


def test_resync_still_runs_full_syncs(archive_root):
    named_chat(archive_root)
    loop = make_loop(archive_root, archive_all=False, settle=0.0)
    loop.handle_event({"event": "resync_required", "revision": "5"})
    loop.flush()
    assert loop.test_syncer.calls == [CHAT]


def att_msg(mid, minutes):
    item = msg(mid, minutes, "", ALICE, content_type=1, status="attachment")
    return item
