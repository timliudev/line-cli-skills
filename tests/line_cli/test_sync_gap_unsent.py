"""Gap detection and the possibly-unsent heuristic."""

from __future__ import annotations

from linecore import store

from .sync_helpers import (
    CHAT,
    Seq,
    archive_dir,
    at,
    base_rules,
    messages_rule,
    msg,
    read_jsonl,
    run_sync,
    sync,
)

NOW = "2026-10-12T00:00:00Z"


def window(first_id: int, count: int, first_minute: int):
    return [msg(first_id + i, first_minute + i, f"m{first_id + i}") for i in range(count)]


# ------------------------------------------------------------------ gap


def test_gap_detected_when_window_jumps_past_the_archive(fake_line, archive_root, capsys):
    first = window(1, 100, 0)  # minutes 0..99
    second = window(201, 100, 500)  # minutes 500..599, nothing in common
    fake_line.scenario(base_rules(messages_rule(Seq(first, second, second))))

    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert code == 0 and out["gap_possible"] is False

    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert out["gap_possible"] is True and out["new_messages"] == 100
    assert any("gap" in w for w in out["warnings"])

    folder = archive_dir(archive_root)
    md = (folder / "messages.md").read_text(encoding="utf-8")
    marker = md.index("Possible gap")
    assert md.index("m100") < marker < md.index("m201")
    state = store.read_state(folder / "state.json", CHAT)
    assert state["gaps"] == [
        {"after_id": "100", "before_id": "201", "detected_at": state["gaps"][0]["detected_at"]}
    ]

    # the marker is part of the archive, so it survives later syncs
    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert out["gap_possible"] is False
    md = (folder / "messages.md").read_text(encoding="utf-8")
    assert md.count("Possible gap") == 1


def test_no_gap_when_windows_overlap(fake_line, archive_root, capsys):
    first = window(1, 100, 0)
    second = window(51, 100, 50)  # overlaps minutes 50..99
    fake_line.scenario(base_rules(messages_rule(Seq(first, second))))
    run_sync(capsys, CHAT)
    code, out = run_sync(capsys, CHAT)
    assert out["gap_possible"] is False and out["new_messages"] == 50
    assert "Possible gap" not in (archive_dir(archive_root) / "messages.md").read_text("utf-8")


def test_no_gap_when_window_is_not_full(fake_line, archive_root, capsys):
    # fewer than 100 messages means LINE returned everything it has
    first = window(1, 5, 0)
    second = window(10, 3, 100)
    fake_line.scenario(base_rules(messages_rule(Seq(first, second))))
    run_sync(capsys, CHAT)
    code, out = run_sync(capsys, CHAT)
    assert out["gap_possible"] is False


def test_merge_window_gap_unit():
    old = window(1, 3, 0)
    fresh = window(10, 4, 100)
    result = store.merge_window(old, fresh, now_iso=NOW, window_limit=4)
    assert result.gap_possible and result.gap_after_id == "3" and result.gap_before_id == "10"
    quiet = store.merge_window(old, fresh, now_iso=NOW, window_limit=100)
    assert not quiet.gap_possible


# ------------------------------------------------------------------ possibly unsent


def test_missing_message_inside_window_is_marked_not_deleted(fake_line, archive_root, capsys):
    first = [msg(i, i, f"text {i}") for i in range(1, 6)]  # minutes 1..5
    second = [msg(i, i, f"text {i}") for i in (1, 2, 4, 5, 6)]  # message 3 vanished
    fake_line.scenario(base_rules(messages_rule(Seq(first, second, second))))

    run_sync(capsys, CHAT, "--name", "G")
    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert code == 0 and out["new_messages"] == 1
    assert [u["message_id"] for u in out["possibly_unsent"]] == ["3"]
    assert out["possibly_unsent"][0]["excerpt"] == "text 3"

    folder = archive_dir(archive_root)
    records = {r["id"]: r for r in read_jsonl(folder / "messages.jsonl")}
    assert set(records) == {"1", "2", "3", "4", "5", "6"}  # never deleted
    assert records["3"]["text"] == "text 3" and records["3"]["possibly_unsent_at"]
    assert all("possibly_unsent_at" not in r for i, r in records.items() if i != "3")
    md = (folder / "messages.md").read_text(encoding="utf-8")
    assert "text 3 (possibly unsent)" in md

    # already marked: not reported again
    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert out["possibly_unsent"] == []
    assert "text 3 (possibly unsent)" in (folder / "messages.md").read_text(encoding="utf-8")


def test_messages_older_than_the_window_are_left_alone(fake_line, archive_root, capsys):
    first = [msg(i, i, f"text {i}") for i in range(1, 6)]
    second = [msg(i, i, f"text {i}") for i in (4, 5, 6, 7)]  # 1..3 simply scrolled out
    fake_line.scenario(base_rules(messages_rule(Seq(first, second))))
    run_sync(capsys, CHAT)
    code, out = run_sync(capsys, CHAT)
    assert out["possibly_unsent"] == [] and out["gap_possible"] is False
    records = read_jsonl(archive_dir(archive_root) / "messages.jsonl")
    assert len(records) == 7 and all("possibly_unsent_at" not in r for r in records)


def test_message_that_reappears_loses_the_mark(fake_line, archive_root, capsys):
    full = [msg(i, i, f"text {i}") for i in range(1, 5)]
    without = [m for m in full if m["id"] != "2"]
    fake_line.scenario(base_rules(messages_rule(Seq(full, without, full))))
    run_sync(capsys, CHAT)
    run_sync(capsys, CHAT)
    folder = archive_dir(archive_root)
    assert "possibly unsent" in (folder / "messages.md").read_text(encoding="utf-8")
    run_sync(capsys, CHAT)
    assert "possibly unsent" not in (folder / "messages.md").read_text(encoding="utf-8")
    assert all("possibly_unsent_at" not in r for r in read_jsonl(folder / "messages.jsonl"))


def test_merge_window_unsent_unit():
    old = [msg(i, i) for i in range(1, 6)]
    fresh = [msg(i, i) for i in (2, 3, 5)]  # window spans minutes 2..5; 4 is missing, 1 is older
    result = store.merge_window(old, fresh, now_iso=NOW, window_limit=100)
    assert [r["id"] for r in result.possibly_unsent] == ["4"]
    assert result.changed and not result.new_ids
    marked = {r["id"]: r.get("possibly_unsent_at") for r in result.records}
    assert marked == {"1": None, "2": None, "3": None, "4": NOW, "5": None}


def test_gap_field_reports_the_missing_time_range(fake_line, archive_root, capsys):
    first = window(1, 100, 0)  # newest archived: minute 99
    second = window(201, 100, 500)  # oldest fetched: minute 500
    fake_line.scenario(base_rules(messages_rule(Seq(first, second))))

    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert out["gap"] is None and out["gap_possible"] is False

    code, out = run_sync(capsys, CHAT, "--name", "G")
    assert out["gap_possible"] is True
    gap = out["gap"]
    assert gap["after_id"] == "100" and gap["before_id"] == "201"
    assert gap["from"] == sync.iso_utc(at(99)) and gap["to"] == sync.iso_utc(at(500))
    assert gap["from"] < gap["to"]
    assert gap["to"] in " ".join(out["warnings"])  # the warning names the range too
    assert "watch.py" in " ".join(out["warnings"])


def test_no_gap_field_when_the_window_is_not_full(fake_line, archive_root, capsys):
    first = window(1, 10, 0)
    second = window(201, 10, 500)  # far apart, but only 10 messages: nothing was cut off
    fake_line.scenario(base_rules(messages_rule(Seq(first, second))))
    run_sync(capsys, CHAT)
    code, out = run_sync(capsys, CHAT)
    assert out["gap"] is None and out["gap_possible"] is False
