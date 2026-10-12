"""Group sender names taken from the text form of `line messages --show-ids`."""

from __future__ import annotations

from linecore import msgtext

from .helpers import GID, resp, rule
from .sync_helpers import ALICE, base_rules, messages_rule, msg, run_sync

GROUP_MEMBER = "U" + "9" * 43
INA = "Ina \U0001f338 Lee"
TEXT = (
    "CHAT · 3 recent messages\n\n"
    "Sat, 10 Oct 2026\n"
    f"15:34  {INA}\n       ID: 1\n       [Image]\n\n"
    f"17:25  {INA}\n       ID: 2\n       multi-line\n"
    "       12:30  looks like a header but is indented\n\n"
    "Mon, 12 Oct 2026\n"
    "12:05  You\n       ID: 3\n       mine\n"
)


def test_msgtext_maps_message_ids_to_names_and_skips_own():
    assert msgtext.parse_sender_names(TEXT) == {"1": INA, "2": INA}
    assert msgtext.parse_sender_names("") == {}
    assert msgtext.parse_sender_names("15:34  Orphan\n") == {}  # header without an ID line


def test_msgtext_handles_crlf():
    assert msgtext.parse_sender_names(TEXT.replace("\n", "\r\n")) == {"1": INA, "2": INA}


def _member_msg(mid, minutes):
    return msg(mid, minutes, "hi", sender=GROUP_MEMBER)


def test_sync_names_group_sender_from_text_when_contacts_lookup_fails(
    fake_line, archive_root, capsys
):
    text = "x\n\n10:00  Ina\n       ID: 1\n       hi\n\n10:05  Ina\n       ID: 2\n       yo\n"
    fake_line.scenario(
        [
            rule(
                ["contacts", "--mid", GROUP_MEMBER, "--json"],
                responses=[resp(1, "", "line: LINE request failed")],
            ),
            rule(["messages", GID, "--limit", "100", "--show-ids"], stdout=text),
            *base_rules(messages_rule([_member_msg(1, 0), _member_msg(2, 5)])),
        ]
    )
    code, out = run_sync(capsys, "--no-download", GID)
    assert code == 0
    assert not any("could not look up" in w for w in out["warnings"])
    md = next(archive_root.glob("*/messages.md")).read_text(encoding="utf-8")
    assert "**Ina**" in md and GROUP_MEMBER[:6] not in md
    cache = (archive_root / "contacts.json").read_text(encoding="utf-8")
    assert GROUP_MEMBER in cache and "Ina" in cache
    # the text listing is fetched once per sync, not once per message
    assert len([a for a in fake_line.argv_calls() if a[-1:] == ["--show-ids"]]) == 1


def test_sync_skips_text_lookup_when_contacts_resolve_everyone(fake_line, archive_root, capsys):
    fake_line.scenario(base_rules(messages_rule([msg(1, 0, "hi", ALICE)])))
    run_sync(capsys, "--no-download", GID)
    assert not [a for a in fake_line.argv_calls() if a[-1:] == ["--show-ids"]]
