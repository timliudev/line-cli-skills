"""sync.py: first sync, incremental dedupe, rename, name resolution, rendering."""

from __future__ import annotations

import json
from datetime import datetime

from linecore import render, store

from .helpers import GID, resp, rule
from .sync_helpers import (
    ALICE,
    BOB,
    CHAT,
    ME,
    Seq,
    archive_dir,
    at,
    base_rules,
    count_calls,
    messages_rule,
    msg,
    read_jsonl,
    run_sync,
)


def clock(minutes: int) -> str:
    return datetime.fromtimestamp(at(minutes) / 1000).strftime("%H:%M")


def test_first_sync_creates_archive(fake_line, archive_root, capsys):
    window = [
        msg(1, 0, "good morning", ALICE),
        msg(2, 1, "hi!\nsecond line", ME),
        msg(3, 2, "later", BOB),
    ]
    fake_line.scenario(base_rules(messages_rule(window)))
    code, out = run_sync(capsys, CHAT, "--name", "Family")

    assert code == 0
    assert out["chat"] == "Family" and out["chat_id"] == CHAT
    assert out["new_messages"] == 3 and out["total_messages"] == 3
    assert out["new_files"] == [] and out["failed_files"] == []
    assert out["possibly_unsent"] == [] and out["gap_possible"] is False
    assert out["warnings"] == []

    folder = archive_dir(archive_root)
    assert folder.name == f"Family__{CHAT}"
    assert out["archive_dir"] == str(folder)
    assert [r["id"] for r in read_jsonl(folder / "messages.jsonl")] == ["1", "2", "3"]

    md = (folder / "messages.md").read_text(encoding="utf-8")
    assert f"{clock(0)} **Alice**: good morning" in md
    assert f"{clock(1)} **Me**: hi!\n  second line" in md  # continuation lines are indented
    assert "**Bob**: later" in md
    assert md.startswith("# Family\n") and f"`{CHAT}`" in md

    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    assert state["chat_id"] == CHAT and state["newest_message_id"] == "3"
    assert state["name"] == "Family" and state["last_sync_at"]
    assert out["new_messages_from_line"] == 1 + md.splitlines().index(
        next(line for line in md.splitlines() if "good morning" in line)
    )

    # an explicit --limit 100 is always sent (upstream's default would be 20)
    assert ["messages", CHAT, "--json", "--limit", "100"] in fake_line.argv_calls()
    # names are cached for later syncs
    cache = json.loads((archive_root / "contacts.json").read_text(encoding="utf-8"))
    assert cache["names"][ALICE]["name"] == "Alice"


def test_incremental_sync_dedupes_by_message_id(fake_line, archive_root, capsys):
    first = [msg(1, 0, "one"), msg(2, 1, "two")]
    second = [msg(2, 1, "two"), msg(3, 2, "three"), msg(4, 3, "four")]
    fake_line.scenario(base_rules(messages_rule(Seq(first, second, second))))

    run_sync(capsys, CHAT, "--name", "Family")
    folder = archive_dir(archive_root)
    before = (folder / "messages.jsonl").read_bytes()

    code, out = run_sync(capsys, CHAT, "--name", "Family")
    assert code == 0 and out["new_messages"] == 2 and out["total_messages"] == 4
    after = (folder / "messages.jsonl").read_bytes()
    assert after.startswith(before)  # appended, existing lines untouched
    assert [r["id"] for r in read_jsonl(folder / "messages.jsonl")] == ["1", "2", "3", "4"]
    md = (folder / "messages.md").read_text(encoding="utf-8")
    assert md.count("**Alice**: two") == 1
    md_lines = md.splitlines()
    assert out["new_messages_from_line"] == 1 + next(
        i for i, line in enumerate(md_lines) if "three" in line
    )

    # a third sync with nothing new changes nothing
    code, out = run_sync(capsys, CHAT, "--name", "Family")
    assert out["new_messages"] == 0 and out["new_messages_from_line"] is None
    assert (folder / "messages.jsonl").read_bytes() == after


def test_known_messages_are_refreshed_but_never_downgraded(fake_line, archive_root, capsys):
    broken = msg(1, 0, "", status="decryption_failed", error="no keys")
    reacted = msg(
        1,
        0,
        "now readable",
        reactions=[
            {"fromUserMid": BOB, "atMillis": 1, "reactionType": {"predefinedReactionType": 3}}
        ],
    )
    again_broken = msg(1, 0, "", status="decryption_failed", error="no keys")
    fake_line.scenario(base_rules(messages_rule(Seq([broken], [reacted], [again_broken]))))

    run_sync(capsys, CHAT, "--name", "Family")
    folder = archive_dir(archive_root)
    assert "could not decrypt: no keys" in (folder / "messages.md").read_text(encoding="utf-8")

    run_sync(capsys, CHAT, "--name", "Family")
    md = (folder / "messages.md").read_text(encoding="utf-8")
    assert "now readable" in md and "reactions: ❤️ love (Bob, " in md

    run_sync(capsys, CHAT, "--name", "Family")  # a later failure must not erase the good copy
    assert read_jsonl(folder / "messages.jsonl")[0]["text"] == "now readable"


def test_name_is_resolved_and_archive_folder_renamed(fake_line, archive_root, capsys):
    window = [msg(1, 0, "hello")]
    chats = rule(
        ["chats", "--search", "Family", "--json"],
        stdout=[{"id": CHAT, "type": 0, "unread_count": 0, "name": "Family", "updated_at": 5}],
    )
    contacts = rule(["contacts", "--search", "Family", "--json"], stdout=[])
    fake_line.scenario(base_rules(chats, contacts, messages_rule(window)))

    code, out = run_sync(capsys, "Family")
    assert code == 0 and out["chat"] == "Family"
    folder = archive_dir(archive_root)
    assert folder.name == f"Family__{CHAT}"

    # the group is renamed: same archive (keyed by ID), only the folder suffix changes
    (folder / "files").mkdir()
    (folder / "files" / "keep.txt").write_text("x", encoding="utf-8")
    code, out = run_sync(capsys, CHAT, "--name", "Family: 2027/trip?")
    assert code == 0
    renamed = archive_dir(archive_root)
    assert renamed.name == f"Family_ 2027_trip___{CHAT}"
    assert (renamed / "files" / "keep.txt").is_file()
    assert [p.name for p in archive_root.iterdir() if p.is_dir()] == [renamed.name]
    assert read_jsonl(renamed / "messages.jsonl")[0]["id"] == "1"
    assert (renamed / "messages.md").read_text(encoding="utf-8").startswith("# Family: 2027/trip?")

    # syncing by ID without a name keeps the existing folder name
    run_sync(capsys, CHAT)
    assert archive_dir(archive_root).name == renamed.name


def test_direct_chat_is_named_after_the_contact(fake_line, archive_root, capsys):
    direct = "U" + "9" * 43
    peer_mid = "u" + "9" * 32
    window = [msg(1, 0, "hey", peer_mid)]
    fake_line.scenario(
        [
            messages_rule(window, chat=direct),
            rule(["whoami", "--json"], stdout={"mid": ME}),
            rule(
                prefix=["contacts", "--mid"],
                stdout=[
                    {
                        "mid": peer_mid,
                        "effectiveDisplayName": "Carol",
                        "displayName": "Carol",
                        "status": "resolved",
                    },
                    {"mid": direct, "effectiveDisplayName": "Carol", "status": "resolved"},
                ],
            ),
        ]
    )
    code, out = run_sync(capsys, direct)
    assert code == 0 and out["chat"] == "Carol"
    assert archive_dir(archive_root, direct).name == f"Carol__{direct}"


def test_ambiguous_name_creates_nothing(fake_line, archive_root, capsys):
    other = "C" + "D" * 43
    chats = rule(
        ["chats", "--search", "Team", "--json"],
        stdout=[
            {"id": GID, "type": 0, "unread_count": 0, "name": "Team", "updated_at": 1},
            {"id": other, "type": 0, "unread_count": 0, "name": "team", "updated_at": 2},
        ],
    )
    contacts = rule(["contacts", "--search", "Team", "--json"], stdout=[])
    fake_line.scenario(base_rules(chats, contacts))
    code, out = run_sync(capsys, "Team")
    assert code == 3 and out["status"] == "ambiguous" and len(out["candidates"]) == 2
    assert not archive_root.exists()
    assert count_calls(fake_line, "messages") == 0


def test_unknown_name_exits_4(fake_line, archive_root, capsys):
    fake_line.scenario(
        base_rules(
            rule(["chats", "--search", "Nobody", "--json"], stdout=[]),
            rule(["contacts", "--search", "Nobody", "--json"], stdout=[]),
        )
    )
    code, out = run_sync(capsys, "Nobody")
    assert code == 4 and out["status"] == "not_found"


def test_line_failure_is_reported_not_raised(fake_line, archive_root, capsys):
    not_logged_in = resp(1, "", "line: no saved LINE session; run line login")
    fake_line.scenario(
        base_rules(rule(["messages", CHAT, "--json", "--limit", "100"], responses=[not_logged_in]))
    )
    code, out = run_sync(capsys, CHAT)
    assert code == 1 and out["chat_id"] == CHAT
    assert out["error"]["kind"] == "not_logged_in"
    assert not archive_root.exists()


def test_empty_chat_creates_no_folder(fake_line, archive_root, capsys):
    fake_line.scenario(base_rules(messages_rule([])))
    code, out = run_sync(capsys, CHAT)
    assert code == 0 and out["archive_dir"] is None and out["new_messages"] == 0
    assert not archive_root.exists()


def test_corrupt_jsonl_line_is_skipped_with_a_warning(fake_line, archive_root, capsys):
    fake_line.scenario(base_rules(messages_rule(Seq([msg(1, 0, "one")], [msg(2, 1, "two")]))))
    run_sync(capsys, CHAT, "--name", "Family")
    folder = archive_dir(archive_root)
    with open(folder / "messages.jsonl", "a", encoding="utf-8") as handle:
        handle.write('{"id": "9", "text": "cut off\n')  # a torn write
    code, out = run_sync(capsys, CHAT, "--name", "Family")
    assert code == 0 and any("unreadable" in w for w in out["warnings"])
    assert [r["id"] for r in read_jsonl_ok(folder / "messages.jsonl")] == ["1", "2"]


def read_jsonl_ok(path):
    records, _ = store.read_jsonl(path)
    return records


# ---------------------------------------------------------------- render unit tests


def render_for(records, **kw):
    params = dict(
        chat_id=CHAT,
        chat_name="Family",
        names={ALICE: "Alice", BOB: "Bob"},
        self_mid=ME,
        files={},
        failures={},
        gaps=[],
        synced_at=datetime(2026, 10, 12, 9, 30).astimezone(),
    )
    params.update(kw)
    return render.render_markdown(records, **params).text


def test_render_replies_stickers_and_reactions():
    records = [
        msg(1, 0, "lunch?", ALICE),
        msg(2, 1, "yes", ME, reply_to="1"),
        msg(3, 2, "", ALICE, content_type=7, status="unsupported"),
        msg(
            4,
            3,
            "ok",
            BOB,
            reactions=[
                {"fromUserMid": ALICE, "atMillis": 1, "reactionType": {"predefinedReactionType": 2}}
            ],
        ),
    ]
    md = render_for(records)
    assert '_(in reply to Alice: "lunch?")_' in md
    assert "**Alice**: [Sticker]" in md
    assert "_(reactions: 👍 like (Alice, " in md
    assert md.count("\n## ") == 1  # one day section


def test_render_unknown_sender_falls_back_to_short_mid():
    md = render_for([msg(1, 0, "x", sender="u" + "f" * 32)])
    assert "**ufffff...ffff**: x" in md


def test_render_marks_unsent_gap_and_attachments():
    unsent = msg(1, 0, "oops", ALICE, possibly_unsent_at="2026-10-12T00:00:00Z")
    records = [
        unsent,
        {
            **msg(2, 1, "", ALICE),
            "content_type": 14,
            "status": "attachment",
            "file_name": "a[1].pdf",
        },
        {**msg(3, 2, "", BOB), "content_type": 2, "status": "attachment"},
        msg(4, 3, "after the gap", BOB),
    ]
    md = render_for(
        records,
        files={"2": "20251009-000100_2.pdf"},
        failures={"3": {"reason": "too_large", "limit": 20 * 1024 * 1024}},
        gaps=[{"before_id": "4", "after_id": "3"}],
    )
    assert "**Alice**: oops (possibly unsent)" in md
    assert "[File: a(1).pdf](files/20251009-000100_2.pdf)" in md
    assert "[Video] over 20 MiB, not downloaded" in md
    gap_at = md.index("Possible gap")
    assert md.index("[Video]") < gap_at < md.index("after the gap")


def test_render_neutral_labels_for_system_event_and_unsupported_types():
    records = [
        msg(1, 0, "", ALICE, content_type=18, status="unsupported"),
        msg(2, 1, "", ALICE, content_type=99, status="unsupported"),
    ]
    md = render_for(records)
    assert "[System event (type 18)]" in md
    assert "[Unsupported content (type 99)]" in md


def test_render_quiet_gap_line_between_distant_messages():
    day = 24 * 60
    records = [msg(1, 0, "a", ALICE), msg(2, 5 * day, "b", BOB), msg(3, 6 * day, "c", BOB)]
    md = render_for(records)
    assert md.count("no messages for") == 1
    assert "— no messages for 5 days (2025-10-09 → 2025-10-14) —" in md
    assert md.index("no messages for") < md.index("## 2025-10-14")
    assert "no messages for" not in render_for(records, quiet_gap_days=0)
    assert "no messages for" not in render_for(records, quiet_gap_days=5)  # not more than 5


_DEFAULT_AT = at(5)


def _reaction_md(rtype, at_millis=_DEFAULT_AT, sender=ALICE):
    item = {"fromUserMid": sender, "reactionType": rtype}
    if at_millis is not None:
        item["atMillis"] = at_millis
    return render_for([msg(1, 0, "hi", BOB, reactions=[item])])


def test_render_predefined_reactions_show_emoji_and_name():
    names = {2: "👍 like", 3: "❤️ love", 4: "😆 laugh",
             5: "😮 surprise", 6: "😢 sad", 7: "😡 angry"}
    for code, label in names.items():
        md = _reaction_md({"predefinedReactionType": code})
        assert f"_(reactions: {label} (Alice, {clock(5)}))_" in md
    assert f"{names[3]} (Alice, {clock(5)})" in _reaction_md({"predefinedReactionType": "3"})


def test_render_paid_reaction_links_the_public_sticon_image():
    paid = {"productId": "abc123", "emojiId": "003", "resourceType": 1, "version": 5}
    md = _reaction_md({"paidReactionType": paid})
    url = "https://stickershop.line-scdn.net/sticonshop/v1/sticon/abc123/android/003.png"
    assert f"[LINE emoji abc123/003]({url}) (Alice, {clock(5)})" in md


def test_render_paid_reaction_without_a_safe_image_id_has_no_link():
    paid = {"productId": "a b/c", "emojiId": "3"}
    md = _reaction_md({"paidReactionType": paid})
    assert "LINE emoji a b/c/3 (Alice" in md and "](http" not in md


def test_render_unknown_reaction_shapes_and_missing_time():
    assert "reaction (type unknown) (Alice, " in _reaction_md({"somethingNew": 1})
    assert "reaction (type unknown) (Alice, " in _reaction_md({"predefinedReactionType": 99})
    assert "reaction (type unknown) (Alice, " in _reaction_md("weird")
    assert "(reactions: ❤️ love (Alice))" in _reaction_md(
        {"predefinedReactionType": 3}, at_millis=None
    )
    md = _reaction_md({"predefinedReactionType": 3}, sender=ME)
    assert "(Me, " in md
