import json
import subprocess
import sys

import pytest
from linecore import runner

from .helpers import BUSY, NOT_LOGGED_IN, SCRIPTS, WHOAMI_OK, load_script, resp, rule

login = load_script("login")


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def __call__(self):
        return self.now


class Launcher:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"launched": True, "method": "test-terminal"}

    def __call__(self, line_path, extra):
        self.calls.append((line_path, list(extra)))
        return self.result


def main(argv, launcher, capsys):
    code = login.main(argv, launcher=launcher)
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(runner, "_sleep", lambda s: None)


# ---------------------------------------------------------------- gating


def test_requires_yes_and_opens_nothing(fake_line, capsys):
    fake_line.scenario([rule(["whoami", "--json"], responses=[NOT_LOGGED_IN])])
    launcher = Launcher()
    code, out = main([], launcher, capsys)
    assert code == 3 and out["status"] == "confirmation_required"
    assert "Chrome" in out["message"]
    assert launcher.calls == [] and fake_line.calls() == []


def test_already_logged_in_short_circuits(fake_line, capsys):
    fake_line.scenario([rule(["whoami", "--json"], stdout=WHOAMI_OK)])
    launcher = Launcher()
    code, out = main(["--yes"], launcher, capsys)
    assert code == 0 and out["status"] == "already_logged_in"
    assert out["account"] == {"display_name": "Tim"}
    assert launcher.calls == []


def test_missing_binary(capsys):
    code, out = main(["--yes"], Launcher(), capsys)
    assert code == 1 and out["error"]["kind"] == "binary_missing"


@pytest.mark.parametrize("bad", ["not-an-email", "a@b.c; calc", "x y@z.com", "a@b.com' ; ls"])
def test_email_is_validated(fake_line, capsys, bad):
    code, out = main(["--yes", "--email", bad], Launcher(), capsys)
    assert code == 2 and out["status"] == "failed"


# ---------------------------------------------------------------- polling


def test_polls_until_whoami_succeeds(fake_line):
    fake_line.scenario(
        [
            rule(
                ["whoami", "--json"],
                responses=[
                    NOT_LOGGED_IN,  # initial check
                    NOT_LOGGED_IN,  # poll 1
                    BUSY,  # poll 2: login holds the session lock
                    resp(0, WHOAMI_OK),
                ],
            )
        ]
    )
    clock, launcher = FakeClock(), Launcher()
    out, code = login.run_login(
        timeout=300,
        interval=3,
        extra=[],
        manual_only=False,
        launcher=launcher,
        sleep=clock.sleep,
        clock=clock,
    )
    assert code == 0 and out["status"] == "logged_in" and out["polls"] == 3
    assert clock.now == 9.0  # 3s between polls
    assert len(launcher.calls) == 1 and launcher.calls[0][1] == []
    assert out["account"] == {"display_name": "Tim"}


def test_times_out(fake_line):
    fake_line.scenario([rule(["whoami", "--json"], responses=[NOT_LOGGED_IN])])
    clock = FakeClock()
    out, code = login.run_login(
        timeout=10,
        interval=3,
        extra=[],
        manual_only=False,
        launcher=Launcher(),
        sleep=clock.sleep,
        clock=clock,
    )
    assert code == 1 and out["status"] == "timeout" and out["waited_seconds"] == 10
    assert out["polls"] == 4  # 3,6,9,12 -> deadline passes after the 4th check
    assert clock.now == 12.0


def test_forwards_email_and_headless_flags(fake_line, capsys):
    fake_line.scenario([rule(["whoami", "--json"], responses=[NOT_LOGGED_IN, resp(0, WHOAMI_OK)])])
    launcher = Launcher()
    code, out = main(
        ["--yes", "--email", "me@example.com", "--headless", "--interval", "0.01"], launcher, capsys
    )
    assert code == 0 and out["status"] == "logged_in"
    assert launcher.calls[0][1] == ["--email", "me@example.com", "--headless"]


def test_no_terminal_available_returns_manual(fake_line, capsys):
    fake_line.scenario([rule(["whoami", "--json"], responses=[NOT_LOGGED_IN])])
    launcher = Launcher({"launched": False, "reason": "no terminal emulator found"})
    code, out = main(["--yes"], launcher, capsys)
    assert code == 3
    assert out["status"] == "manual" and out["command"] == "line login"
    assert out["reason"] == "no terminal emulator found" and "QR" in out["instructions"]
    assert len(fake_line.calls()) == 1  # no polling after a failed launch


def test_manual_flag_never_launches(fake_line, capsys):
    fake_line.scenario([rule(["whoami", "--json"], responses=[NOT_LOGGED_IN])])
    launcher = Launcher()
    code, out = main(["--yes", "--manual", "--email", "me@example.com"], launcher, capsys)
    assert code == 3 and out["status"] == "manual" and launcher.calls == []
    assert out["command"] == "line login --email me@example.com"


def test_login_never_calls_line_login_itself(fake_line, capsys):
    """Only the user's own terminal may run `line login`; this process just polls whoami."""
    fake_line.scenario([rule(["whoami", "--json"], responses=[NOT_LOGGED_IN, resp(0, WHOAMI_OK)])])
    main(["--yes", "--interval", "0.01"], Launcher(), capsys)
    assert all(args[0] == "whoami" for args in fake_line.argv_calls())


# ---------------------------------------------------------------- terminal commands


def test_windows_command_quotes_path_with_spaces_and_quotes():
    cmd = login.windows_command(r"C:\Users\O'Neil\My Apps\line.exe", ["--email", "a@b.com"])
    assert cmd[1:3] == ["-NoProfile", "-Command"]
    script = cmd[3]
    assert script.startswith("Start-Process powershell -ArgumentList '-NoExit','-Command',")
    assert "''C:\\Users\\O''''Neil\\My Apps\\line.exe''" in script  # quote doubled twice
    assert script.endswith("''a@b.com'''")


def test_macos_command_escapes_for_applescript():
    cmd = login.macos_command('/Users/me/My "Bin"/line', [])
    assert cmd[0] == "osascript" and cmd[1] == "-e"
    assert 'do script "' in cmd[-1]
    assert '\\"Bin\\"' in cmd[-1]  # AppleScript string escaping
    assert "My" in cmd[-1] and "'" in cmd[-1]  # shell quoting for the space


def test_linux_commands_in_documented_order(monkeypatch):
    present = {"gnome-terminal", "xterm", "konsole"}
    monkeypatch.setattr(login.shutil, "which", lambda n: f"/usr/bin/{n}" if n in present else None)
    cmds = login.linux_commands("/home/u/.local/bin/line", ["--headless"])
    assert [c[0].rsplit("/", 1)[1] for c in cmds] == ["gnome-terminal", "konsole", "xterm"]
    gnome = cmds[0]
    assert gnome[1] == "--" and gnome[2:4] == ["sh", "-c"]
    assert gnome[-3:] == ["/home/u/.local/bin/line", "login", "--headless"]
    assert "Press Enter" in gnome[4]  # window stays open so the user sees the result


def test_default_launcher_refuses_over_ssh(monkeypatch):
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 22 5.6.7.8 22")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not spawn"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn"))
    result = login.default_launcher("/bin/line", [])
    assert result["launched"] is False and "SSH" in result["reason"]


def test_default_launcher_needs_display_on_linux(monkeypatch):
    monkeypatch.setattr(login, "current_os", lambda: "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("must not spawn"))
    result = login.default_launcher("/bin/line", [])
    assert result["launched"] is False and "display" in result["reason"]


def test_default_launcher_linux_tries_next_terminal_on_oserror(monkeypatch):
    monkeypatch.setattr(login, "current_os", lambda: "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(
        login.shutil, "which", lambda n: f"/usr/bin/{n}" if n in {"konsole", "xterm"} else None
    )
    tried = []

    def popen(cmd, **kwargs):
        tried.append(cmd[0])
        if cmd[0].endswith("konsole"):
            raise OSError("boom")

    monkeypatch.setattr(subprocess, "Popen", popen)
    result = login.default_launcher("/bin/line", [])
    assert result == {"launched": True, "method": "xterm"}
    assert tried == ["/usr/bin/konsole", "/usr/bin/xterm"]


def test_default_launcher_windows_uses_start_process(monkeypatch):
    monkeypatch.setattr(login, "current_os", lambda: "windows")
    seen = {}

    def run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", run)
    result = login.default_launcher(r"C:\x\line.exe", [])
    assert result["launched"] is True and "Start-Process" in seen["cmd"][-1]


def test_default_launcher_windows_failure_is_reported(monkeypatch):
    monkeypatch.setattr(login, "current_os", lambda: "windows")
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, b"", b"")
    )
    assert login.default_launcher("x", [])["launched"] is False


# ---------------------------------------------------------------- CLI surface


def test_cli_help_and_confirmation_exit_code():
    base = [sys.executable, str(SCRIPTS / "login.py")]
    proc = subprocess.run([*base, "--help"], capture_output=True, text=True)
    assert proc.returncode == 0 and "--yes" in proc.stdout and "--timeout" in proc.stdout
    proc = subprocess.run(base, capture_output=True, text=True)
    assert proc.returncode == 3
    assert json.loads(proc.stdout)["status"] == "confirmation_required"
