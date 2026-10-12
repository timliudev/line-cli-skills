"""The text-listing fallback used when `line chats --search` fails upstream."""

from __future__ import annotations

from linecore import chatlist

from .helpers import GID, UID, load_script, resp, rule
from .sync_helpers import ALICE, base_rules, messages_rule, msg, run_sync

lineq = load_script("lineq")

ROOM = "R" + "7" * 43
SEARCH_FAILS = resp(
    1, "", "line: LINE request failed; check your connection and LINE account settings"
)


def listing(*entries, footer=True):
    """Build `chats --limit 0 --show-ids` text. entries: (type, name, id|None)."""
    lines = ["UPDATED           UNREAD  TYPE     CHAT"]
    for kind, name, cid in entries:
        lines.append(f"2026-10-12 12:05       -  {kind:<8} {name}".rstrip())
        if cid is not None:
            lines.append(" " * 36 + cid)
    if footer:
        lines += [
            "",
            "Showing 3 of 910 conversations. Use --limit for more.",
            "Read: line messages <id>",
        ]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ parser


def test_parse_pairs_name_row_with_following_id_row():
    rows = chatlist.parse_chat_list(
        listing(("Group", "Team Alpha", GID), ("Direct", "Alice", UID), ("Room", "Lunch", ROOM))
    )
    assert [(r["id"], r["name"], r["type"]) for r in rows] == [
        (GID, "Team Alpha", "group"),
        (UID, "Alice", "direct"),
        (ROOM, "Lunch", "room"),
    ]
    assert rows[0]["unread"] is None and rows[0]["updated"] == "2026-10-12 12:05"


def test_parse_emoji_fullwidth_and_spaces():
    name = "專案｜ＡＢＣ  群 🚀 test"
    rows = chatlist.parse_chat_list(listing(("Group", name, GID)))
    assert rows[0]["name"] == name


def test_parse_unnamed_row_and_numeric_unread():
    text = "2026-10-12 12:05       3  Direct\n" + " " * 36 + UID + "\n"
    rows = chatlist.parse_chat_list(text)
    assert rows == [
        {
            "id": UID,
            "name": chatlist.UNNAMED,
            "type": "direct",
            "updated": "2026-10-12 12:05",
            "unread": 3,
        }
    ]


def test_parse_row_without_id_line_is_dropped_and_does_not_steal_next_id():
    text = listing(("Group", "No id", None), ("Group", "Has id", GID))
    rows = chatlist.parse_chat_list(text)
    assert [(r["id"], r["name"]) for r in rows] == [(GID, "Has id")]


def test_parse_ignores_header_footer_blank_and_crlf():
    text = listing(("Group", "A", GID)).replace("\n", "\r\n")
    assert [r["name"] for r in chatlist.parse_chat_list(text)] == ["A"]
    assert chatlist.parse_chat_list("") == []
    assert chatlist.parse_chat_list("Showing 0 of 0 conversations.\nRead: x\n") == []


def test_search_rows_is_case_insensitive_and_width_insensitive():
    rows = chatlist.parse_chat_list(listing(("Group", "ＡＩ GO Team", GID), ("Direct", "Bob", UID)))
    assert [r["id"] for r in chatlist.search_rows(rows, "ai go")] == [GID]
    assert chatlist.search_rows(rows, "zzz") == []
    assert chatlist.search_rows(rows, "  ") == []


# ------------------------------------------------------------------ resolve fallback


def test_resolve_falls_back_to_active_listing_when_search_fails(fake_line):
    fake_line.scenario(
        [
            rule(["chats", "--search", "team", "--json"], responses=[SEARCH_FAILS]),
            rule(["contacts", "--search", "team", "--json"], stdout=[]),
            rule(
                ["chats", "--limit", "0", "--show-ids"],
                stdout=listing(("Group", "Team Alpha", GID), ("Direct", "Bob", UID)),
            ),
        ]
    )
    payload, code = lineq.resolve_name("team")
    assert code == 0 and payload["status"] == "resolved" and payload["id"] == GID
    assert payload["matched_by"] == "substring" and payload["incomplete"] is True
    assert [c["id"] for c in payload["candidates"]] == [GID]
    assert chatlist.FALLBACK_WARNING in payload["warnings"]


def test_resolve_fallback_exact_match_resolves_but_flags_incomplete(fake_line):
    fake_line.scenario(
        [
            rule(["chats", "--search", "Team Alpha", "--json"], responses=[SEARCH_FAILS]),
            rule(["contacts", "--search", "Team Alpha", "--json"], stdout=[]),
            rule(
                ["chats", "--limit", "0", "--show-ids"],
                stdout=listing(("Group", "team alpha", GID)),
            ),
        ]
    )
    payload, code = lineq.resolve_name("Team Alpha")
    assert code == 0 and payload["id"] == GID and payload["incomplete"] is True


def test_resolve_fallback_nothing_found(fake_line):
    fake_line.scenario(
        [
            rule(["chats", "--search", "nope", "--json"], responses=[SEARCH_FAILS]),
            rule(["contacts", "--search", "nope", "--json"], stdout=[]),
            rule(["chats", "--limit", "0", "--show-ids"], stdout=listing(("Group", "A", GID))),
        ]
    )
    payload, code = lineq.resolve_name("nope")
    assert code == 4 and payload["status"] == "not_found"


def test_resolve_keeps_original_error_when_fallback_also_fails(fake_line):
    fail = resp(1, "", "line: no saved LINE session; run line login")
    fake_line.scenario(
        [
            rule(prefix=["chats"], responses=[fail]),
            rule(prefix=["contacts"], responses=[fail]),
        ]
    )
    payload, code = lineq.resolve_name("x")
    assert code == 1 and payload["error"]["kind"] == "not_logged_in"


# ------------------------------------------------------------------ sync --all naming


def test_sync_all_names_folders_from_active_listing(fake_line, archive_root, capsys):
    fake_line.scenario(
        base_rules(
            rule(["chats", "--json"], stdout=[{"id": GID, "type": 1, "unread_count": 0}]),
            rule(
                ["chats", "--limit", "0", "--show-ids"],
                stdout=listing(("Group", "Team Alpha", GID)),
            ),
            messages_rule([msg(1, 0, "hi", ALICE)]),
        )
    )
    code, out = run_sync(capsys, "--all", "--no-download")
    assert code == 0 and out["failed"] == []
    folders = [p.name for p in archive_root.iterdir() if p.is_dir()]
    assert folders == [f"{GID}__Team Alpha"]


def test_sync_all_still_works_when_text_listing_fails(fake_line, archive_root, capsys):
    fake_line.scenario(
        base_rules(
            rule(["chats", "--json"], stdout=[{"id": GID, "type": 1, "unread_count": 0}]),
            rule(["chats", "--limit", "0", "--show-ids"], exit=1, stderr="line: boom"),
            messages_rule([msg(1, 0, "hi", ALICE)]),
        )
    )
    code, out = run_sync(capsys, "--all", "--no-download")
    assert code == 0 and out["chats"][0]["chat_id"] == GID
