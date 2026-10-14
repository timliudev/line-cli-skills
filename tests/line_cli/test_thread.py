"""thread.py: replies and reactions of one archived message, from synthetic archives."""

from __future__ import annotations

import json

import pytest
from linecore import store

from .helpers import load_script
from .sync_helpers import ALICE, BOB, CHAT, ME, at, msg

thread = load_script("thread")


def reaction(mid, code, minutes):
    return {
        "fromUserMid": mid,
        "reactionType": {"predefinedReactionType": code},
        "atMillis": at(minutes),
    }


@pytest.fixture
def chat_dir(archive_root):
    folder = archive_root / store.folder_name(CHAT, "Family")
    records = [
        msg(1, 0, "all 33 items are done\nsecond line", ME, reactions=[reaction(ALICE, 3, 5)]),
        msg(2, 1, "great!", ALICE, reply_to="1"),
        msg(3, 2, "thanks", BOB, reply_to="1"),
        msg(4, 3, "you're welcome", ME, reply_to="3"),
        msg(5, 4, "unrelated", ALICE),
        msg(6, 5, "all 33 items are done", BOB),
    ]
    store.write_jsonl(folder / store.JSONL_NAME, records)
    store.write_json(
        archive_root / store.CONTACTS_NAME,
        {"schema": 1, "names": {ALICE: {"name": "Alice", "ts": 1}, BOB: {"name": "Bob", "ts": 1}}},
    )
    return folder


def run(capsys, *argv):
    code = thread.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_by_message_id_from_folder(chat_dir, capsys):
    code, out = run(capsys, str(chat_dir), "--message", "1")
    assert code == 0
    assert out["target"]["id"] == "1"
    assert out["target"]["first_line"] == "all 33 items are done"
    assert [r["id"] for r in out["replies"]] == ["2", "3"]
    assert out["replies"][0]["sender"] == "Alice"
    assert out["replies"][0]["text_first_line"] == "great!"
    assert "replies" not in out["replies"][1]
    assert out["reactions"][0]["sender"] == "Alice"
    assert "love" in out["reactions"][0]["type_label"]


def test_chat_id_resolves_through_archive_root(chat_dir, capsys):
    code, out = run(capsys, CHAT, "--message", "1")
    assert code == 0 and len(out["replies"]) == 2
    code, _ = run(capsys, "id:" + CHAT, "--message", "1")
    assert code == 0


def test_depth_follows_nested_replies(chat_dir, capsys):
    code, out = run(capsys, str(chat_dir), "--message", "1", "--depth", "2")
    assert code == 0
    nested = out["replies"][1]["replies"]
    assert [r["id"] for r in nested] == ["4"]
    assert out["replies"][0]["replies"] == []


def test_text_unique_match(chat_dir, capsys):
    code, out = run(capsys, str(chat_dir), "--text", "unrelated")
    assert code == 0 and out["target"]["id"] == "5" and out["replies"] == []


def test_text_ambiguous_then_narrowed_by_sender(chat_dir, capsys):
    code, out = run(capsys, str(chat_dir), "--text", "33 items")
    assert code == 3 and out["status"] == "ambiguous"
    assert {c["id"] for c in out["candidates"]} == {"1", "6"}
    code, out = run(capsys, str(chat_dir), "--text", "33 items", "--from", "bob")
    assert code == 0 and out["target"]["id"] == "6"


def test_not_found(chat_dir, capsys):
    assert run(capsys, str(chat_dir), "--message", "999")[0] == 4
    assert run(capsys, str(chat_dir), "--text", "nothing like this")[0] == 4
    assert run(capsys, "c" + "9" * 32, "--message", "1")[0] == 4
    assert run(capsys, "not-an-id", "--message", "1")[0] == 4


def test_unknown_reaction_and_unnamed_sender(archive_root, capsys):
    folder = archive_root / CHAT
    rec = msg(1, 0, "hi", ME, reactions=[{"fromUserMid": ALICE, "atMillis": at(1)}])
    store.write_jsonl(folder / store.JSONL_NAME, [rec])
    code, out = run(capsys, str(folder), "--message", "1")
    assert code == 0
    assert out["reactions"][0]["type_label"] == "reaction (type unknown)"
    assert out["reactions"][0]["sender"].startswith(ALICE[:6])


def test_usage_errors(chat_dir):
    with pytest.raises(SystemExit) as exc:
        thread.main([str(chat_dir)])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        thread.main([str(chat_dir), "--message", "1", "--from", "x"])
    assert exc.value.code == 2


def test_own_messages_show_me_from_state(chat_dir, capsys):
    store.write_json(chat_dir / store.STATE_NAME, {"self_mid": ME})
    code, out = run(capsys, str(chat_dir), "--message", "1")
    assert code == 0 and out["target"]["sender"] == "Me"
