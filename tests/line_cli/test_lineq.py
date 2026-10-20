import json
import os
import subprocess
import sys

import pytest

from .helpers import GID, NOT_LOGGED_IN, SCRIPTS, UID, load_script, resp, rule

lineq = load_script("lineq")

UID2 = "U" + "C" * 43


def chats_rule(query, rows, **kw):
    return rule(["chats", "--search", query, "--json"], stdout=rows, **kw)


def contacts_rule(query, rows, **kw):
    return rule(["contacts", "--search", query, "--json"], stdout=rows, **kw)


def chat(cid, name, updated=0):
    return {"id": cid, "type": 0, "unread_count": 0, "name": name, "updated_at": updated}


def contact(mid, name, override=""):
    return {"mid": mid, "displayName": name, "displayNameOverridden": override}


def test_resolve_exact_single_merges_chat_and_contact(fake_line):
    fake_line.scenario(
        [
            chats_rule("Alice", [chat(UID, "alice", 5)]),
            contacts_rule("Alice", [contact(UID, "ALICE")]),
        ]
    )
    payload, code = lineq.resolve_name("Alice")  # exact match ignores case
    assert code == 0
    assert payload["status"] == "resolved" and payload["id"] == UID
    assert payload["type"] == "direct" and len(payload["candidates"]) == 1
    assert payload["candidates"][0]["sources"] == ["chats", "contacts"]
    # plain `chats --json` has no names, so resolve must always use --search
    assert all("--search" in a for a in fake_line.argv_calls())


def test_resolve_uses_custom_contact_name(fake_line):
    fake_line.scenario(
        [
            chats_rule("Mom", []),
            contacts_rule("Mom", [contact(UID, "Linda Chen", override="Mom")]),
        ]
    )
    payload, code = lineq.resolve_name("Mom")
    assert code == 0 and payload["id"] == UID and payload["name"] == "Mom"


def test_resolve_ambiguous_exact_names(fake_line):
    fake_line.scenario(
        [
            chats_rule("Team", [chat(GID, "Team", 10), chat("C" + "D" * 43, "team", 20)]),
            contacts_rule("Team", []),
        ]
    )
    payload, code = lineq.resolve_name("Team")
    assert code == 3 and payload["status"] == "ambiguous"
    assert len(payload["candidates"]) == 2 and "id" not in payload
    assert {c["type"] for c in payload["candidates"]} == {"group"}


def test_resolve_substring_candidates_exact_ranked_first(fake_line):
    fake_line.scenario(
        [
            chats_rule("Bob", [chat(UID, "Bobby", 50), chat(GID, "Bob's Burgers fans", 99)]),
            contacts_rule("Bob", [contact(UID2, "Bob")]),
        ]
    )
    payload, code = lineq.resolve_name("Bob")
    # exactly one *exact* name ("Bob") -> resolved, others still listed
    assert code == 0 and payload["id"] == UID2
    assert [c["name"] for c in payload["candidates"]][0] == "Bob"

    fake_line.scenario(
        [
            chats_rule("Bo", [chat(UID, "Bobby", 50), chat(GID, "Bo-fans", 99)]),
            contacts_rule("Bo", []),
        ]
    )
    payload, code = lineq.resolve_name("Bo")
    assert code == 3 and payload["status"] == "candidates"
    assert [c["id"] for c in payload["candidates"]] == [GID, UID]  # newest activity first


def test_resolve_single_substring_match_resolves(fake_line):
    fake_line.scenario(
        [chats_rule("Bo", [chat(GID, "Bo-fans", 99)]), contacts_rule("Bo", [])]
    )
    payload, code = lineq.resolve_name("Bo")
    assert code == 0 and payload["status"] == "resolved"
    assert payload["id"] == GID and payload["matched_by"] == "substring"
    assert len(payload["candidates"]) == 1


def test_resolve_single_substring_match_keeps_incomplete_flag(fake_line):
    fake_line.scenario(
        [
            rule(["chats", "--search", "Bo", "--json"], exit=1, stderr="LINE request failed"),
            contacts_rule("Bo", [contact(UID, "Bobby")]),
        ]
    )
    payload, code = lineq.resolve_name("Bo")
    assert code == 0 and payload["id"] == UID and payload["incomplete"] is True


def test_resolve_not_found(fake_line):
    fake_line.scenario([chats_rule("Zed", []), contacts_rule("Zed", [])])
    payload, code = lineq.resolve_name("Zed")
    assert code == 4 and payload["status"] == "not_found" and payload["candidates"] == []


def test_resolve_cjk_names(fake_line):
    fake_line.scenario(
        [
            chats_rule("爸爸", [chat(UID, "爸爸")]),
            contacts_rule("爸爸", []),
        ]
    )
    payload, code = lineq.resolve_name("爸爸")
    assert code == 0 and payload["id"] == UID


def test_resolve_one_search_failing_flags_incomplete(fake_line):
    fake_line.scenario(
        [
            chats_rule("Alice", [chat(UID, "Alice")]),
            rule(["contacts", "--search", "Alice", "--json"], exit=1, stderr="line: boom"),
        ]
    )
    payload, code = lineq.resolve_name("Alice")
    assert code == 0 and payload["incomplete"] is True and payload["warnings"]


def test_resolve_both_failing_reports_error(fake_line):
    fake_line.scenario(
        [
            rule(prefix=["chats"], responses=[NOT_LOGGED_IN]),
            rule(prefix=["contacts"], responses=[NOT_LOGGED_IN]),
        ]
    )
    payload, code = lineq.resolve_name("Alice")
    assert code == 1 and payload["status"] == "error"
    assert payload["error"]["kind"] == "not_logged_in"


def test_resolve_full_id_short_circuits(fake_line):
    payload, code = lineq.resolve_name(UID)
    assert code == 0 and payload["id"] == UID and payload["type"] == "direct"
    payload, _ = lineq.resolve_name("id:" + GID)
    assert payload["id"] == GID and payload["type"] == "group"
    assert fake_line.calls() == []


def test_resolve_empty_name_is_usage_error():
    payload, code = lineq.resolve_name("   ")
    assert code == 2 and payload["status"] == "error"


# ---------------------------------------------------------------- run / CLI


def run_cli(*args, stdin=None):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "lineq.py"), *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=os.environ.copy(),
    )
    return proc.returncode, (json.loads(proc.stdout) if proc.stdout.strip() else None), proc.stderr


def test_cli_help_works():
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "lineq.py"), "--help"], capture_output=True, text=True
    )
    assert proc.returncode == 0 and "resolve" in proc.stdout
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "lineq.py"), "run", "--help"], capture_output=True, text=True
    )
    assert proc.returncode == 0 and "--yes" in proc.stdout


def test_cli_resolve_end_to_end(fake_line):
    fake_line.scenario([chats_rule("Alice", [chat(UID, "Alice")]), contacts_rule("Alice", [])])
    code, out, _ = run_cli("resolve", "Alice")
    assert code == 0 and out["id"] == UID


def test_cli_run_read_returns_envelope(fake_line):
    fake_line.scenario([rule(["chats", "--json"], stdout=[chat(UID, "A")])])
    code, out, _ = run_cli("run", "--", "chats", "--json")
    assert code == 0
    assert out["ok"] is True and out["exit_code"] == 0 and out["error"] is None
    assert out["data"][0]["id"] == UID


def test_cli_run_error_envelope(fake_line):
    fake_line.scenario(
        [
            rule(
                prefix=["messages"],
                exit=1,
                stderr="line: 2 chats or contacts have that name; use --show-ids",
            )
        ]
    )
    code, out, _ = run_cli("run", "--", "messages", "Team", "--json")
    assert code == 1 and out["ok"] is False and out["exit_code"] == 1
    assert out["error"]["kind"] == "ambiguous_name" and out["error"]["retryable"] is False


def test_cli_run_partial_messages(fake_line):
    fake_line.scenario(
        [
            rule(
                prefix=["messages"],
                exit=1,
                stdout=[{"id": "m1", "status": "decrypt_failed"}],
                stderr="line: 1 message could not be decrypted",
            )
        ]
    )
    code, out, _ = run_cli("run", "--", "messages", UID, "--json")
    assert code == 0 and out["ok"] and out["partial"] and out["warnings"]


def test_cli_run_mutation_needs_yes_and_never_reaches_line(fake_line):
    fake_line.scenario([rule(prefix=["send"], stdout={"id": "m1"})])
    code, out, _ = run_cli("run", "--stdin", "--", "send", UID, "--stdin", "--json", stdin="hi")
    assert code == 3 and out["error"]["kind"] == "confirmation_required"
    assert fake_line.calls() == []


def test_cli_run_mutation_with_yes_forwards_stdin_once(fake_line):
    fake_line.scenario(
        [
            rule(
                prefix=["send"],
                responses=[
                    resp(75, "", "line: another line command is using the session"),
                    resp(0, {"id": "m1"}),
                ],
            )
        ]
    )
    code, out, _ = run_cli(
        "run", "--yes", "--stdin", "--", "send", UID, "--stdin", "--json", stdin="哈囉 hello"
    )
    assert code == 1 and out["error"]["kind"] == "busy" and out["error"]["retryable"] is False
    assert len(fake_line.calls()) == 1
    assert fake_line.calls()[0]["stdin"] == "哈囉 hello"


def test_cli_run_forwards_stdin_when_only_line_args_have_it(fake_line):
    fake_line.scenario([rule(prefix=["send"], stdout={"id": "m1"})])
    code, out, _ = run_cli("run", "--yes", "--", "send", UID, "--stdin", "--json", stdin="hello")
    assert code == 0 and out["ok"]
    assert fake_line.calls()[0]["stdin"] == "hello"


def test_cli_run_does_not_read_stdin_without_the_flag(fake_line):
    fake_line.scenario([rule(["chats", "--json"], stdout=[chat(UID, "A")])])
    code, _, _ = run_cli("run", "--", "chats", "--json", stdin="ignored")
    assert code == 0 and fake_line.calls()[0]["stdin"] == ""


def test_cli_run_refuses_login_unbounded_watch_and_stdout_download(fake_line):
    code, out, _ = run_cli("run", "--yes", "--", "login")
    assert code == 2 and "login.py" in out["error"]["message"]
    code, out, _ = run_cli("run", "--", "watch")
    assert code == 2 and "--timeout" in out["error"]["message"]
    code, out, _ = run_cli("run", "--", "watch", "--timeout", "1s")
    assert code in (0, 1)  # allowed through (fake has no rule -> error)
    code, out, _ = run_cli("run", "--", "download", UID, "--message", "1", "--output", "-")
    assert code == 2
    code, out, _ = run_cli("run", "--")
    assert code == 2
    assert [c for c in fake_line.argv_calls() if c[0] in {"login", "download"}] == []


def test_cli_run_binary_missing_reports_cleanly():
    code, out, _ = run_cli("run", "--", "chats", "--json")
    assert code == 1 and out["error"]["kind"] == "binary_missing" and out["exit_code"] is None


def test_cli_run_busy_read_is_retried(fake_line, monkeypatch):
    # the subprocess uses real sleeps; keep the backoff tiny by limiting attempts to 2
    fake_line.scenario(
        [
            rule(
                prefix=["chats"],
                responses=[
                    resp(75, "", "line: another line command is using the session"),
                    resp(0, "[]"),
                ],
            )
        ]
    )
    code, out, _ = run_cli("run", "--max-attempts", "2", "--", "chats", "--json")
    assert code == 0 and out["attempts"] == 2


@pytest.mark.parametrize("flag", ["--timeout", "--max-attempts"])
def test_cli_rejects_bad_numbers(flag):
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "lineq.py"), "run", flag, "abc", "--", "chats"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2


def test_cli_run_send_requires_e2ee_by_default(fake_line):
    fake_line.scenario([rule(prefix=["send"], stdout={"id": "m1"})])
    code, _, _ = run_cli("run", "--yes", "--", "send", UID, "--stdin", "--json", stdin="hi")
    assert code == 0
    assert fake_line.argv_calls()[0] == ["send", UID, "--stdin", "--json", "--require-e2ee"]


def test_cli_run_allow_non_e2ee_drops_the_guard(fake_line):
    fake_line.scenario([rule(prefix=["send"], stdout={"id": "m1"})])
    code, _, _ = run_cli(
        "run", "--yes", "--allow-non-e2ee", "--", "send", UID, "--stdin", "--json", stdin="hi"
    )
    assert code == 0 and fake_line.argv_calls()[0] == ["send", UID, "--stdin", "--json"]


def test_e2ee_policy_leaves_other_commands_and_explicit_flags_alone():
    assert lineq.with_e2ee_policy(["react", UID], allow_non_e2ee=False) == ["react", UID]
    explicit = ["send", UID, "--text", "x", "--require-e2ee"]
    assert lineq.with_e2ee_policy(explicit, allow_non_e2ee=False) == explicit


def test_cli_run_reports_encryption_required(fake_line):
    stderr = (
        "line: This chat doesn't support end-to-end encryption for this message. "
        "Nothing was sent.\n"
        "This send requires E2EE. Use the default send policy to allow HTTPS/TLS delivery "
        "(LINE can read the message)."
    )
    fake_line.scenario([rule(prefix=["send"], exit=1, stderr=stderr)])
    code, out, _ = run_cli("run", "--yes", "--", "send", UID, "--text", "hi", "--json")
    assert code == 1 and out["error"]["kind"] == "encryption_required"
    assert "LINE could read" in out["error"]["hint"]
