import json
import sys
from pathlib import Path

import pytest
from linecore import runner
from linecore.config import Config, defaults

from .helpers import IS_WINDOWS, FakeLine, resp, rule

BUSY = resp(75, "", "line: another line command is using the session; retry when it finishes")
MSGS = [{"id": "m1", "text": "hi"}, {"id": "m2", "text": "yo", "status": "decrypt_failed"}]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    waits = []
    monkeypatch.setattr(runner, "_sleep", waits.append)
    return waits


def with_config(**kwargs) -> Config:
    base = defaults()
    return Config(**{**base.__dict__, **kwargs})


# ---------------------------------------------------------------- discovery


def test_find_line_none_when_nothing_installed():
    assert runner.find_line(defaults()) is None


def test_find_line_prefers_config_then_path_then_known(fake_line, tmp_path):
    other = FakeLine(tmp_path / "other", tmp_path, None).install(tmp_path / "other")

    located = runner.find_line(with_config(line_path=str(other)))
    assert (located.source, located.path) == ("config", str(other))

    located = runner.find_line(defaults())
    assert located.source == "path"
    assert located.path.lower() == str(fake_line.path).lower()

    # config pointing at a missing file falls through to PATH
    assert runner.find_line(with_config(line_path=str(tmp_path / "nope"))).source == "path"


def test_find_line_config_accepts_directory(tmp_path):
    folder = tmp_path / "forkbin"
    name = "line.exe" if IS_WINDOWS else "line"
    folder.mkdir()
    (folder / name).write_text("x")
    assert runner.find_line(with_config(line_path=str(folder))).path == str(folder / name)


def test_find_line_known_paths_windows(tmp_path):
    env = {"LOCALAPPDATA": str(tmp_path / "L"), "USERPROFILE": str(tmp_path / "h")}
    exe = tmp_path / "L" / "line-cli" / "bin" / "line.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("x")
    assert runner.known_install_paths(env, "windows") == [exe]
    located = runner.find_line(defaults(), env, "windows")
    assert (located.source, located.path) == ("known", str(exe))


def test_find_line_known_paths_unix(tmp_path):
    env = {"HOME": str(tmp_path / "h")}
    assert runner.known_install_paths(env, "linux") == [
        tmp_path / "h" / ".local" / "bin" / "line",
        Path("/usr/local/bin/line"),
    ]
    mac = runner.known_install_paths(env, "macos")
    assert Path("/opt/homebrew/bin/line") in mac and mac[0].name == "line"
    binary = tmp_path / "h" / ".local" / "bin" / "line"
    binary.parent.mkdir(parents=True)
    binary.write_text("x")
    assert runner.find_line(defaults(), env, "linux").source == "known"


# ---------------------------------------------------------------- classification


@pytest.mark.parametrize(
    "code,stderr,kind,retryable",
    [
        (1, "line: 3 chats or contacts have that name; use --show-ids", "ambiguous_name", False),
        (
            1,
            "line: no chat or contact has that exact name; use line chats",
            "name_not_found",
            False,
        ),
        (1, "line: no saved LINE session; run line login", "not_logged_in", False),
        (1, "line: LINE session was logged out; run line login", "not_logged_in", False),
        (
            1,
            "line: LINE session was invalidated or replaced by another client; run line login",
            "session_replaced",
            False,
        ),
        (65, "line: saved Letter Sealing keys are missing; run line login", "not_logged_in", False),
        (75, "line: another line command is using the session; retry", "busy", True),
        (75, "", "busy", True),
        (1, "line: another line command is using the session", "busy", True),
        (1, "line: another line watch is already running", "watcher_running", False),
        (1, "line: attachment is no longer available from LINE", "attachment_expired", False),
        (1, "line: attachment exceeds the CLI limit of 20 MiB", "attachment_too_large", False),
        (
            1,
            "line: downloads from external media URLs are not supported",
            "attachment_external",
            False,
        ),
        (
            1,
            "line: LINE is still processing this attachment; try again",
            "attachment_processing",
            True,
        ),
        (1, "line: message exceeds the CLI limit of 10000 UTF-16 units", "message_too_long", False),
        (1, "line: invalid options; run line chats --help", "usage", False),
        (1, "line: dial tcp: lookup line: no such host", "network", True),
        (65, "line: authentication failed", "storage_invalid", False),
        (69, "line: storage unavailable", "storage_unavailable", False),
        (74, "line: uncertain durability", "storage_uncertain", False),
        (78, "line: consent required", "storage_config", False),
        (130, "Cancelled.", "cancelled", False),
        (143, "Stopped after receiving a signal.", "cancelled", False),
        (1, "line: something unexpected", "error", False),
    ],
)
def test_classify_error(code, stderr, kind, retryable):
    err = runner.classify_error(code, stderr, mutation=False, command="messages")
    assert err.kind == kind
    assert err.retryable is retryable
    assert err.message


def test_classify_error_mutations_are_never_retryable():
    for code, stderr in [(75, ""), (1, "line: dial tcp: i/o timeout"), (1, "line: boom")]:
        err = runner.classify_error(code, stderr, mutation=True, command="send")
        assert err.retryable is False
    assert "verify" in runner.classify_error(1, "boom", mutation=True, command="send").hint


def test_login_stderr_is_never_surfaced():
    err = runner.classify_error(
        1,
        "Open LINE on your phone and enter PIN: 123456\nline: failed",
        mutation=True,
        command="login",
    )
    assert "123456" not in json.dumps(err.to_dict())


def test_pin_is_redacted_elsewhere_too():
    err = runner.classify_error(1, "line: enter PIN: 8841 now", command="whoami")
    assert "8841" not in err.message


@pytest.mark.parametrize(
    "args,expected",
    [
        (["chats", "--json"], False),
        (["contacts", "--mid", "u1"], False),
        (["messages", "X", "--json"], False),
        (["watch", "--timeout", "5s"], False),
        (["whoami"], False),
        (["download", "X", "--message", "1", "--output", "a"], False),
        (["auth", "status"], False),
        (["update", "--check", "--json"], False),
        (["update", "--check=true"], False),
        (["update"], True),
        (["update", "--json"], True),
        (["update", "--check=false"], True),
        (["auth", "migrate", "--storage=headless"], True),
        (["send", "X", "--stdin"], True),
        (["react", "X"], True),
        (["unsend", "X"], True),
        (["login"], True),
        (["logout"], True),
        (["brand-new-command"], True),
    ],
)
def test_is_mutation(args, expected):
    assert runner.is_mutation(args) is expected


# ---------------------------------------------------------------- running


def test_run_line_json_success(fake_line):
    fake_line.scenario([rule(["chats", "--json"], stdout=[{"id": "u1"}])])
    result = runner.run_line(["chats", "--json"])
    assert result.ok and result.data == [{"id": "u1"}] and result.exit_code == 0
    assert result.to_dict()["error"] is None


def test_run_line_text_output_and_version(fake_line):
    fake_line.scenario([rule(["version"], stdout="line v0.4.0\n")])
    assert runner.line_version() == "v0.4.0"
    result = runner.run_line(["version"])
    assert result.data is None and result.text == "line v0.4.0"
    assert runner.parse_version("line dev") == "dev"


def test_run_line_binary_missing():
    result = runner.run_line(["chats", "--json"])
    assert not result.ok and result.error.kind == "binary_missing" and result.exit_code is None


def test_partial_messages_is_ok_with_warnings(fake_line):
    fake_line.scenario(
        [
            rule(
                ["messages", "U", "--json"],
                exit=1,
                stdout=MSGS,
                stderr="line: 1 message could not be decrypted\n",
            )
        ]
    )
    result = runner.run_line(["messages", "U", "--json"])
    assert result.ok and result.partial and result.exit_code == 1
    assert result.data == MSGS
    assert "decrypted" in result.warnings[0]
    assert result.to_dict()["partial"] is True


def test_partial_contacts_mid(fake_line):
    fake_line.scenario(
        [
            rule(
                prefix=["contacts"],
                exit=1,
                stdout=[{"mid": "u1", "status": "unavailable"}],
                stderr="line: 1 contact unavailable",
            )
        ]
    )
    result = runner.run_line(["contacts", "--mid", "u1", "--json"])
    assert result.ok and result.partial


def test_exit1_without_json_is_a_real_error(fake_line):
    fake_line.scenario(
        [rule(prefix=["messages"], exit=1, stderr="line: no chat or contact has that exact name")]
    )
    result = runner.run_line(["messages", "Nobody", "--json"])
    assert not result.ok and result.error.kind == "name_not_found" and not result.partial


def test_exit1_json_object_is_not_treated_as_partial(fake_line):
    fake_line.scenario([rule(prefix=["chats"], exit=1, stdout={"x": 1}, stderr="line: boom")])
    assert not runner.run_line(["chats", "--json"]).ok


def test_auth_status_failure_keeps_status_json(fake_line):
    fake_line.scenario(
        [
            rule(
                prefix=["auth", "status"],
                exit=69,
                stdout={"reason": "storage_unavailable"},
                stderr="line: storage unavailable",
            )
        ]
    )
    result = runner.run_line(["auth", "status", "--json"])
    assert not result.ok and result.error.kind == "storage_unavailable"
    assert result.data == {"reason": "storage_unavailable"}


def test_read_retries_on_busy_with_backoff(fake_line, no_sleep):
    fake_line.scenario([rule(prefix=["chats"], responses=[BUSY, BUSY, resp(0, "[]")])])
    result = runner.run_line(["chats", "--json"], backoff=0.5)
    assert result.ok and result.attempts == 3
    assert len(fake_line.calls()) == 3
    assert no_sleep == [0.5, 1.0]


def test_read_gives_up_after_max_attempts(fake_line):
    fake_line.scenario([rule(prefix=["chats"], responses=[BUSY])])
    result = runner.run_line(["chats", "--json"], max_attempts=2)
    assert not result.ok and result.error.kind == "busy" and result.error.retryable is True
    assert result.attempts == 2 and len(fake_line.calls()) == 2


def test_read_does_not_retry_other_errors(fake_line, no_sleep):
    fake_line.scenario([rule(prefix=["chats"], exit=1, stderr="line: boom")])
    result = runner.run_line(["chats", "--json"])
    assert result.attempts == 1 and no_sleep == []


@pytest.mark.parametrize(
    "args",
    [
        ["send", "U", "--stdin"],
        ["react", "U", "--message", "1", "--reaction", "like"],
        ["unsend", "U", "--message", "1"],
        ["logout"],
        ["update"],
        ["auth", "migrate", "--storage=headless"],
    ],
)
def test_mutations_are_never_retried(fake_line, no_sleep, args):
    prefix = args[:2] if args[0] == "auth" else args[:1]
    fake_line.scenario([rule(prefix=prefix, responses=[BUSY, resp(0, "{}")])])
    result = runner.run_line(args, stdin_text="hello", max_attempts=5)
    assert not result.ok
    assert result.error.kind == "busy" and result.error.retryable is False
    assert result.attempts == 1 and len(fake_line.calls()) == 1
    assert no_sleep == []


def test_mutation_generic_failure_single_attempt(fake_line):
    fake_line.scenario([rule(prefix=["send"], exit=1, stderr="line: dial tcp: i/o timeout")])
    result = runner.run_line(["send", "U", "--text", "x"])
    assert len(fake_line.calls()) == 1
    assert result.error.retryable is False and "verify" in result.error.hint


def test_mutation_timeout_warns_it_may_have_succeeded(fake_line):
    fake_line.scenario([rule(prefix=["send"], stdout="{}", sleep=5)])
    result = runner.run_line(["send", "U", "--text", "x"], timeout=1.0)
    assert result.error.kind == "timeout" and result.error.retryable is False
    assert "may have completed" in result.error.hint


def test_read_timeout_is_retryable_advice(fake_line):
    fake_line.scenario([rule(prefix=["chats"], stdout="[]", sleep=5)])
    result = runner.run_line(["chats", "--json"], timeout=1.0)
    assert result.error.kind == "timeout" and result.error.retryable is True


def test_stdin_is_passed_utf8_and_not_echoed(fake_line):
    fake_line.scenario([rule(prefix=["send"], stdout={"id": "m1"})])
    result = runner.run_line(["send", "U", "--stdin", "--json"], stdin_text="哈囉\nsecond line")
    assert result.ok
    assert fake_line.calls()[0]["stdin"] == "哈囉\nsecond line"
    assert "哈囉" not in json.dumps(result.to_dict(), ensure_ascii=False)


def test_reads_get_empty_stdin(fake_line):
    fake_line.scenario([rule(prefix=["chats"], stdout="[]")])
    runner.run_line(["chats", "--json"])
    assert fake_line.calls()[0]["stdin"] == ""


def test_unicode_argument_round_trips(fake_line):
    fake_line.scenario([rule(prefix=["chats"], stdout="[]")])
    runner.run_line(["chats", "--search", "爸爸 家", "--json"])
    assert fake_line.argv_calls()[0] == ["chats", "--search", "爸爸 家", "--json"]


def test_watch_json_lines_are_collected(fake_line):
    events = [{"type": "message", "revision": 1}, {"type": "operation", "revision": 2}]
    fake_line.scenario(
        [rule(prefix=["watch"], stdout="\n".join(json.dumps(e) for e in events) + "\n")]
    )
    result = runner.run_line(["watch", "--limit", "2"])
    assert result.ok and result.data == events


def test_check_login_states(fake_line):
    fake_line.scenario(
        [
            rule(
                ["whoami", "--json"],
                responses=[
                    resp(0, {"displayName": "Tim"}),
                    resp(1, "", "line: no saved LINE session; run line login"),
                    BUSY,
                ],
            )
        ]
    )
    assert runner.check_login()[0] == "logged_in"
    assert runner.check_login()[0] == "logged_out"
    assert runner.check_login(binary=str(fake_line.path))[0] == "unknown"


def test_child_environment_is_scrubbed(monkeypatch):
    monkeypatch.setenv("QRCODE_DEBUG", "1")
    env = runner._env_for_child({"X": "1"})
    assert "QRCODE_DEBUG" not in env and env["NO_COLOR"] == "1" and env["X"] == "1"


def test_rejects_nul_in_args():
    with pytest.raises(ValueError):
        runner.run_line(["chats", "a\x00b"])


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="windows-only")
def test_cmd_shim_is_found_on_windows(fake_line):
    assert runner.find_line(defaults()).path.lower().endswith("line.cmd")


def test_configure_stdio_survives_missing_streams_under_pythonw(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    runner.configure_stdio()
    runner.emit({"ok": True})  # must not raise
    runner.log("diagnostic")
