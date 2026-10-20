"""broadcast.py: dry run, --yes, stop at the first failure, no retries."""

from __future__ import annotations

import io
import json

import pytest

from .helpers import load_script, rule

broadcast = load_script("broadcast")

ALICE = "U" + "A" * 43
BOB = "U" + "C" * 43
CAROL = "U" + "D" * 43
GROUP = "C" + "E" * 43

SENT = {"id": "1", "chat_id": ALICE}


def chat(cid, name):
    return {"id": cid, "type": 0, "unread_count": 0, "name": name, "updated_at": 1}


def lookup(query, *rows):
    """Resolution rules for one recipient name: `chats --search` and an empty contact search."""
    return [
        rule(["chats", "--search", query, "--json"], stdout=list(rows)),
        rule(["contacts", "--search", query, "--json"], stdout=[]),
    ]


def sends(fake_line, chat_id=None):
    return [
        c
        for c in fake_line.calls()
        if c["args"][:1] == ["send"] and (chat_id is None or c["args"][1] == chat_id)
    ]


@pytest.fixture
def delays(monkeypatch):
    recorded = []
    monkeypatch.setattr(broadcast, "_sleep", recorded.append)
    return recorded


@pytest.fixture
def recipients(tmp_path):
    path = tmp_path / "list.txt"
    path.write_text(
        "# team announcement\n\nAlice\n   Bob  \n# Carol is on holiday\nCarol\n", encoding="utf-8"
    )
    return path


def run(capsys, *argv):
    code = broadcast.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def go(capsys, *argv):
    """The approved flow: a dry run first, then `--yes --plan` with that dry run's plan."""
    _, dry = run(capsys, *argv)
    return run(capsys, *argv, "--yes", "--plan", dry["plan"])


def three_people(*extra):
    return [
        *extra,
        *lookup("Alice", chat(ALICE, "Alice")),
        *lookup("Bob", chat(BOB, "Bob")),
        *lookup("Carol", chat(CAROL, "Carol")),
    ]


def test_read_recipients_skips_comments_and_blank_lines(recipients):
    assert broadcast.read_recipients(recipients) == ["Alice", "Bob", "Carol"]


def test_dry_run_resolves_and_sends_nothing(fake_line, recipients, capsys, delays):
    fake_line.scenario(three_people())
    code, out = run(capsys, "--recipients", str(recipients), "--text", "Hello team")
    assert code == 0
    assert out["mode"] == "dry_run" and out["sent"] is False
    assert [r["id"] for r in out["recipients"]] == [ALICE, BOB, CAROL]
    assert [r["name"] for r in out["recipients"]] == ["Alice", "Bob", "Carol"]
    assert out["unresolved"] == [] and out["text"] == "Hello team"
    assert out["plan"] and "--yes" in out["message"]
    assert sends(fake_line) == []
    assert delays == []


def test_dry_run_reports_ambiguous_inexact_and_missing(fake_line, tmp_path, capsys):
    lst = tmp_path / "l.txt"
    lst.write_text("Alice\nBo\nZed\n" + GROUP + "\n", encoding="utf-8")
    fake_line.scenario(
        [
            *lookup("Alice", chat(ALICE, "Alice"), chat(BOB, "alice")),  # two exact matches
            *lookup("Bo", chat(BOB, "Bobby")),  # one partial match only
            *lookup("Zed"),
        ]
    )
    code, out = run(capsys, "--recipients", str(lst), "--text", "hi")
    assert code == 3  # needs the user
    by_query = {u["query"]: u for u in out["unresolved"]}
    assert by_query["Alice"]["status"] == "ambiguous" and len(by_query["Alice"]["candidates"]) == 2
    assert by_query["Bo"]["status"] == "inexact"  # never trust a partial name in a bulk send
    assert by_query["Bo"]["candidates"][0]["name"] == "Bobby"
    assert by_query["Zed"]["status"] == "not_found"
    assert [r["id"] for r in out["recipients"]] == [GROUP]  # a full ID is taken as is
    assert out["sent"] is False and sends(fake_line) == []


def test_only_unmatched_names_exit_4(fake_line, tmp_path, capsys):
    lst = tmp_path / "l.txt"
    lst.write_text("Zed\n", encoding="utf-8")
    fake_line.scenario(lookup("Zed"))
    code, out = run(capsys, "--recipients", str(lst), "--text", "hi")
    assert code == 4 and out["unresolved"][0]["status"] == "not_found"


def test_yes_refuses_when_something_is_unresolved(fake_line, tmp_path, capsys):
    lst = tmp_path / "l.txt"
    lst.write_text("Alice\nZed\n", encoding="utf-8")
    fake_line.scenario([*lookup("Alice", chat(ALICE, "Alice")), *lookup("Zed")])
    code, out = go(capsys, "--recipients", str(lst), "--text", "hi")
    assert code in (3, 4) and out["sent"] is False
    assert sends(fake_line) == []


def test_yes_sends_in_order_with_a_pause_between_recipients(fake_line, recipients, capsys, delays):
    fake_line.scenario(three_people(rule(prefix=["send"], stdout=SENT)))
    code, out = go(
        capsys,
        "--recipients",
        str(recipients),
        "--text",
        'Say "hi"\nline 2',
        "--delay",
        "1.5",
    )
    assert code == 0 and out["mode"] == "send" and out["sent"] is True
    assert out["summary"] == {"sent": 3, "failed": 0, "not_attempted": 0}
    assert [r["status"] for r in out["results"]] == ["sent"] * 3
    calls = sends(fake_line)
    assert [c["args"][1] for c in calls] == [ALICE, BOB, CAROL]
    # text always travels over stdin, so quotes and newlines survive
    assert all(
        c["args"] == ["send", c["args"][1], "--stdin", "--json", "--require-e2ee"] for c in calls
    )
    assert all(c["stdin"] == 'Say "hi"\nline 2' for c in calls)
    assert delays == [1.5, 1.5]  # between recipients, not after the last one


def test_text_from_stdin_drops_the_heredoc_newline(
    fake_line, recipients, capsys, delays, monkeypatch
):
    fake_line.scenario(three_people(rule(prefix=["send"], stdout=SENT)))
    def feed():
        monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO("Hallo\n晚安\n".encode())))

    feed()
    _, dry = run(capsys, "--recipients", str(recipients), "--stdin")
    feed()
    code, out = run(
        capsys, "--recipients", str(recipients), "--stdin", "--yes", "--plan", dry["plan"]
    )
    assert code == 0 and out["text"] == "Hallo\n晚安"
    assert {c["stdin"] for c in sends(fake_line)} == {"Hallo\n晚安"}


def test_stops_at_first_failure_and_never_retries(fake_line, recipients, capsys, delays):
    fake_line.scenario(
        three_people(
            rule(prefix=["send", ALICE], stdout=SENT),
            rule(prefix=["send", BOB], exit=1, stderr="line: LINE request failed: dial tcp: x"),
            rule(prefix=["send", CAROL], stdout=SENT),
        )
    )
    code, out = go(capsys, "--recipients", str(recipients), "--text", "hi")
    assert code == 1 and out["sent"] is False
    assert [(r["id"], r["status"]) for r in out["results"]] == [
        (ALICE, "sent"),
        (BOB, "failed"),
        (CAROL, "not_attempted"),
    ]
    assert out["summary"] == {"sent": 1, "failed": 1, "not_attempted": 1}
    failed = out["results"][1]
    assert failed["error"]["kind"] == "network"
    assert failed["may_have_been_delivered"] is True  # the answer may simply have been lost
    assert "not_attempted" in out["message"]
    assert [c["args"][1] for c in sends(fake_line)] == [ALICE, BOB]  # one attempt each, no Carol
    assert delays == [3.0]  # default pause, only before Bob


def test_failure_that_provably_sent_nothing_is_marked(fake_line, recipients, capsys, delays):
    fake_line.scenario(
        three_people(
            rule(prefix=["send"], exit=1, stderr="line: no saved LINE session; run line login")
        )
    )
    code, out = go(capsys, "--recipients", str(recipients), "--text", "hi")
    assert code == 1 and out["results"][0]["status"] == "failed"
    assert out["results"][0]["error"]["kind"] == "not_logged_in"
    assert out["results"][0]["may_have_been_delivered"] is False
    assert [r["status"] for r in out["results"][1:]] == ["not_attempted"] * 2
    assert len(sends(fake_line)) == 1


def test_busy_session_is_not_retried_for_a_send(fake_line, recipients, capsys, delays):
    busy = "line: another line command is using the session; retry when it finishes"
    fake_line.scenario(three_people(rule(prefix=["send"], exit=75, stderr=busy)))
    code, out = go(capsys, "--recipients", str(recipients), "--text", "hi")
    assert code == 1 and len(sends(fake_line)) == 1


def test_plan_mismatch_refuses_to_send(fake_line, recipients, capsys, delays):
    fake_line.scenario(three_people(rule(prefix=["send"], stdout=SENT)))
    _, dry = run(capsys, "--recipients", str(recipients), "--text", "hi")
    code, out = run(
        capsys, "--recipients", str(recipients), "--text", "changed", "--yes", "--plan", dry["plan"]
    )
    assert code == 3 and out["sent"] is False and "changed" in out["message"]
    assert sends(fake_line) == []
    code, out = run(
        capsys, "--recipients", str(recipients), "--text", "hi", "--yes", "--plan", dry["plan"]
    )
    assert code == 0 and len(sends(fake_line)) == 3


def test_duplicate_recipients_are_sent_once(fake_line, tmp_path, capsys, delays):
    lst = tmp_path / "l.txt"
    lst.write_text("Alice\n" + ALICE + "\n", encoding="utf-8")
    fake_line.scenario([*lookup("Alice", chat(ALICE, "Alice")), rule(prefix=["send"], stdout=SENT)])
    code, out = go(capsys, "--recipients", str(lst), "--text", "hi")
    assert code == 0 and len(sends(fake_line)) == 1
    assert out["duplicates"] == [{"query": ALICE, "id": ALICE, "same_as": "Alice"}]


def test_text_then_file_and_partial_failure(fake_line, tmp_path, capsys, delays):
    lst = tmp_path / "l.txt"
    lst.write_text(ALICE + "\n" + BOB + "\n", encoding="utf-8")
    attachment = tmp_path / "menu.pdf"
    attachment.write_bytes(b"%PDF-1.7\n")
    fake_line.scenario(
        [
            rule(prefix=["send", ALICE, "--stdin"], stdout=SENT),
            rule(prefix=["send", ALICE, "--file"], exit=1, stderr="line: boom"),
            rule(prefix=["send", BOB], stdout=SENT),
        ]
    )
    code, out = go(
        capsys,
        "--recipients",
        str(lst),
        "--text",
        "menu attached",
        "--file",
        str(attachment),
    )
    assert code == 1
    alice, bob = out["results"]
    assert alice["status"] == "failed" and alice["delivered"] == ["text"]
    assert alice["failed_step"] == "file"
    assert bob["status"] == "not_attempted"
    assert [c["args"][2] for c in sends(fake_line)] == ["--stdin", "--file"]


@pytest.mark.parametrize(
    "extra, needle",
    [
        (["--text", "   "], "empty"),
        (["--text", "x" * 10_001], "longer"),
        ([], "message"),
        (["--text", "hi", "--file", "does-not-exist.bin"], "not a file"),
    ],
)
def test_usage_errors_exit_2_without_resolving(fake_line, recipients, capsys, extra, needle):
    code, out = run(capsys, "--recipients", str(recipients), *extra)
    assert code == 2 and needle in out["error"]["message"]
    assert fake_line.calls() == []


def test_empty_or_oversized_list_is_a_usage_error(fake_line, tmp_path, capsys):
    empty = tmp_path / "empty.txt"
    empty.write_text("# nobody\n\n", encoding="utf-8")
    code, out = run(capsys, "--recipients", str(empty), "--text", "hi")
    assert code == 2 and "empty" in out["error"]["message"]
    big = tmp_path / "big.txt"
    big.write_text("\n".join(f"Person {i}" for i in range(4)), encoding="utf-8")
    code, out = run(capsys, "--recipients", str(big), "--text", "hi", "--max-recipients", "3")
    assert code == 2 and "max-recipients" in out["error"]["message"]
    assert fake_line.calls() == []


def test_text_limit_counts_utf16_units():
    assert broadcast.utf16_units("a") == 1 and broadcast.utf16_units("😀") == 2


def test_yes_without_plan_is_a_usage_error(fake_line, recipients, capsys, delays):
    fake_line.scenario(three_people(rule(prefix=["send"], stdout=SENT)))
    code, out = run(capsys, "--recipients", str(recipients), "--text", "hi", "--yes")
    assert code == 2 and "--plan" in out["error"]["message"]
    assert fake_line.calls() == []  # not even a lookup: nothing can slip past the dry run


def test_changed_attachment_content_changes_the_plan(fake_line, tmp_path, capsys, delays):
    lst = tmp_path / "l.txt"
    lst.write_text(ALICE + "\n", encoding="utf-8")
    attachment = tmp_path / "menu.pdf"
    attachment.write_bytes(b"%PDF-1.7 version A")
    fake_line.scenario([rule(prefix=["send"], stdout=SENT)])
    args = ["--recipients", str(lst), "--text", "hi", "--file", str(attachment)]
    _, dry = run(capsys, *args)
    attachment.write_bytes(b"%PDF-1.7 version B")  # same size, different bytes
    code, out = run(capsys, *args, "--yes", "--plan", dry["plan"])
    assert code == 3 and out["sent"] is False and sends(fake_line) == []


def test_incomplete_name_lookup_is_not_resolved_but_a_full_id_is(
    fake_line, tmp_path, capsys, delays
):
    lst = tmp_path / "l.txt"
    lst.write_text("Alice\n" + BOB + "\n", encoding="utf-8")
    listing = (
        "UPDATED           UNREAD  TYPE     CHAT\n"
        "2026-10-12 12:05       -  Direct   Alice\n"
        f"                                        {ALICE}\n"
    )
    fake_line.scenario(
        [
            # `chats --search` fails upstream, so the lookup falls back to the active-chat listing
            rule(["chats", "--search", "Alice", "--json"], exit=1, stderr="line: boom"),
            rule(["contacts", "--search", "Alice", "--json"], stdout=[]),
            rule(["chats", "--limit", "0", "--show-ids"], stdout=listing),
            rule(prefix=["send"], stdout=SENT),
        ]
    )
    code, out = run(capsys, "--recipients", str(lst), "--text", "hi")
    assert code == 3 and out["sent"] is False
    (item,) = out["unresolved"]
    assert item["query"] == "Alice" and item["status"] == "incomplete"
    assert "full chat ID" in item["message"] and item["candidates"][0]["id"] == ALICE
    assert [r["id"] for r in out["recipients"]] == [BOB]  # the full ID is fine
    code, out = run(
        capsys, "--recipients", str(lst), "--text", "hi", "--yes", "--plan", out["plan"]
    )
    assert code == 3 and sends(fake_line) == []  # still refused: the unresolved entry blocks it

    lst.write_text(ALICE + "\n" + BOB + "\n", encoding="utf-8")  # the user switched to full IDs
    code, out = go(capsys, "--recipients", str(lst), "--text", "hi")
    assert code == 0 and [c["args"][1] for c in sends(fake_line)] == [ALICE, BOB]


def test_allow_non_e2ee_is_part_of_the_plan_and_drops_the_guard(
    fake_line, recipients, capsys, delays
):
    fake_line.scenario(three_people(rule(prefix=["send"], stdout=SENT)))
    _, strict = run(capsys, "--recipients", str(recipients), "--text", "hi")
    _, loose = run(capsys, "--recipients", str(recipients), "--text", "hi", "--allow-non-e2ee")
    assert strict["require_e2ee"] is True and loose["require_e2ee"] is False
    assert strict["plan"] != loose["plan"]
    code, out = run(
        capsys,
        "--recipients",
        str(recipients),
        "--text",
        "hi",
        "--allow-non-e2ee",
        "--yes",
        "--plan",
        loose["plan"],
    )
    assert code == 0 and out["sent"] is True
    assert all("--require-e2ee" not in c["args"] for c in sends(fake_line))


def test_encryption_required_stops_and_says_nothing_was_sent(
    fake_line, recipients, capsys, delays
):
    stderr = "line: Nothing was sent.\nThis send requires E2EE. Use the default send policy."
    fake_line.scenario(three_people(rule(prefix=["send"], exit=1, stderr=stderr)))
    code, out = go(capsys, "--recipients", str(recipients), "--text", "hi")
    assert code == 1
    first = out["results"][0]
    assert first["error"]["kind"] == "encryption_required"
    assert first["may_have_been_delivered"] is False
    assert [r["status"] for r in out["results"][1:]] == ["not_attempted", "not_attempted"]
