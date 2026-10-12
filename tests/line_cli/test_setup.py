import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from linecore import runner

from .helpers import BUSY, NOT_LOGGED_IN, SCRIPTS, WHOAMI_OK, load_script, rule

setup = load_script("setup")


def call(argv, capsys):
    code = setup.main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out)


def healthy(fake_line, version="line v0.4.0\n", update=None):
    fake_line.scenario(
        [
            rule(["version"], stdout=version),
            rule(["whoami", "--json"], stdout=WHOAMI_OK),
            rule(["auth", "status", "--json"], stdout={"reason": "ok", "backend": "keychain"}),
            rule(
                ["update", "--check", "--json"],
                stdout=update or {"status": "up_to_date", "current_version": "v0.4.0"},
            ),
        ]
    )


# ---------------------------------------------------------------- --check


def test_check_without_line(capsys):
    code, out = call(["--check"], capsys)
    assert code == 0
    assert out["line"]["found"] is False and out["logged_in"] is None
    assert out["next_step"] == "install" and out["os"] in {"windows", "macos", "linux"}


def test_check_logged_in(fake_line, capsys):
    healthy(fake_line)
    code, out = call(["--check"], capsys)
    assert code == 0
    assert out["line"]["found"] and out["line"]["version"] == "v0.4.0"
    assert out["line"]["version_matches_docs"] is True
    assert out["logged_in"] is True and out["next_step"] == "ready"
    assert out["account"] == {"display_name": "Tim"}
    assert "mid" not in json.dumps(out)  # no account identifiers leak


def test_check_logged_out(fake_line, capsys):
    fake_line.scenario(
        [
            rule(["version"], stdout="line v0.4.0"),
            rule(["whoami", "--json"], responses=[NOT_LOGGED_IN]),
        ]
    )
    _, out = call(["--check"], capsys)
    assert out["logged_in"] is False and out["next_step"] == "login"


def test_check_unknown_login_state_when_busy(fake_line, capsys, monkeypatch):
    monkeypatch.setattr(runner, "_sleep", lambda s: None)
    fake_line.scenario(
        [rule(["version"], stdout="line v0.4.0"), rule(["whoami", "--json"], responses=[BUSY])]
    )
    _, out = call(["--check"], capsys)
    assert out["logged_in"] is None and out["login_error"] == "busy"
    assert out["next_step"] == "retry_check"


def test_check_flags_version_drift(fake_line, capsys):
    healthy(fake_line, version="line v0.5.1\n")
    _, out = call(["--check"], capsys)
    assert out["line"]["version_matches_docs"] is False


def test_check_linux_keyring_missing(fake_line, capsys, monkeypatch):
    monkeypatch.setattr(setup, "current_os", lambda: "linux")
    monkeypatch.setattr(setup, "_which", lambda name: None)
    fake_line.scenario(
        [
            rule(["version"], stdout="line v0.4.0"),
            rule(["whoami", "--json"], responses=[NOT_LOGGED_IN]),
            rule(
                ["auth", "status", "--json"],
                exit=69,
                stdout={"reason": "storage_unavailable"},
                stderr="line: storage unavailable",
            ),
        ]
    )
    _, out = call(["--check"], capsys)
    assert out["keyring"] == "missing" and out["next_step"] == "fix_keyring"


def test_check_linux_keyring_ok_even_without_session(fake_line, capsys, monkeypatch):
    monkeypatch.setattr(setup, "current_os", lambda: "linux")
    monkeypatch.setattr(setup, "_which", lambda name: "/usr/bin/secret-tool")
    fake_line.scenario(
        [
            rule(["version"], stdout="line v0.4.0"),
            rule(["whoami", "--json"], responses=[NOT_LOGGED_IN]),
            rule(
                ["auth", "status", "--json"],
                exit=1,
                stdout={"reason": "no_session"},
                stderr="line: no session",
            ),
        ]
    )
    _, out = call(["--check"], capsys)
    assert out["keyring"] == "ok" and out["next_step"] == "login"


def test_keyring_not_applicable_off_linux():
    assert setup.keyring_status("windows", None)["status"] == "not_applicable"
    assert setup.keyring_status("macos", None)["status"] == "not_applicable"


def test_keyring_locked_vs_missing(fake_line, monkeypatch):
    fake_line.scenario(
        [rule(["auth", "status", "--json"], exit=69, stdout={"reason": "x"}, stderr="no")]
    )
    monkeypatch.setattr(setup, "_which", lambda name: "/usr/bin/secret-tool")
    assert setup.keyring_status("linux", str(fake_line.path))["status"] == "unavailable"
    assert setup.keyring_status("linux", None)["status"] == "unknown"


@pytest.mark.parametrize(
    "osr,needle",
    [
        ("ID=ubuntu\nID_LIKE=debian\n", "apt"),
        ('ID="fedora"\n', "dnf"),
        ("ID=arch\n", "pacman"),
        ('ID=opensuse-leap\nID_LIKE="suse opensuse"\n', "zypper"),
    ],
)
def test_keyring_fix_command_by_distro(monkeypatch, osr, needle):
    real = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *a, **k: osr if str(self).endswith("os-release") else real(self, *a, **k),
    )
    assert needle in setup.keyring_fix_command()
    assert "sudo" in setup.keyring_fix_command()  # shown to the user, never executed here


# ---------------------------------------------------------------- --install


@pytest.fixture
def no_network(monkeypatch):
    def boom(url):
        raise AssertionError("network used before the user confirmed")

    def boom_run(cmd, env):
        raise AssertionError("installer ran before the user confirmed")

    monkeypatch.setattr(setup, "_download", boom)
    monkeypatch.setattr(setup, "_run_installer", boom_run)


@pytest.mark.parametrize(
    "os_name,method",
    [
        ("windows", "install-release.ps1"),
        ("linux", "install-release.sh"),
        ("macos", "install-release.sh"),
    ],
)
def test_install_without_yes_only_prints_plan(os_name, method, capsys, monkeypatch, no_network):
    monkeypatch.setattr(setup, "current_os", lambda: os_name)
    monkeypatch.setattr(setup, "_which", lambda name: None)  # no brew
    code, out = call(["--install"], capsys)
    assert code == 3
    assert out["status"] == "plan" and out["requires_confirmation"] is True
    plan = out["plan"]
    assert plan["method"] == method and plan["needs_admin"] is False
    assert plan["script_url"] == (
        f"https://raw.githubusercontent.com/kongesque/line-cli/v0.4.0/scripts/{method}"
    )
    assert plan["script_hash_pinned"] is True and len(plan["script_sha256"]) == 64
    assert plan["release_base"] == "https://github.com/kongesque/line-cli/releases/download/v0.4.0"
    assert plan["explain"] and out["rerun"].endswith("--install --yes")


def test_install_plan_prefers_homebrew_on_macos(capsys, monkeypatch, no_network):
    monkeypatch.setattr(setup, "current_os", lambda: "macos")
    monkeypatch.setattr(
        setup, "_which", lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None
    )
    _, out = call(["--install"], capsys)
    assert out["plan"]["method"] == "homebrew"
    assert out["plan"]["command"] == ["brew", "install", "kongesque/tap/line-cli"]


def test_install_skipped_when_already_installed(fake_line, capsys, no_network):
    healthy(fake_line)
    code, out = call(["--install", "--yes"], capsys)
    assert code == 0 and out["status"] == "already_installed"


def test_install_invalid_tag(capsys, no_network):
    code, out = call(["--install", "--tag", "main; rm -rf /"], capsys)
    assert code == 2 and out["status"] == "failed"


def test_install_unpinned_tag_warns(capsys, monkeypatch, no_network):
    monkeypatch.setattr(setup, "current_os", lambda: "linux")
    _, out = call(["--install", "--tag", "v9.9.9"], capsys)
    assert out["plan"]["script_hash_pinned"] is False
    assert "WARNING" in " ".join(out["plan"]["explain"]) and "warning" in out["plan"]


def test_install_script_hash_mismatch_aborts(capsys, monkeypatch):
    monkeypatch.setattr(setup, "current_os", lambda: "windows")
    monkeypatch.setattr(setup, "_download", lambda url: b"echo tampered")
    monkeypatch.setattr(
        setup, "_run_installer", lambda cmd, env: pytest.fail("tampered script must not run")
    )
    code, out = call(["--install", "--yes"], capsys)
    assert code == 1 and out["status"] == "failed" and out["stage"] == "download"
    assert "mismatch" in out["error"]


def test_install_windows_runs_pinned_script(fake_line, capsys, monkeypatch):
    script = b"# pretend installer\n"
    monkeypatch.setitem(
        setup.PINNED_SCRIPT_SHA256,
        "v0.4.0",
        {"install-release.ps1": hashlib.sha256(script).hexdigest()},
    )
    monkeypatch.setattr(setup, "current_os", lambda: "windows")
    urls, ran = [], []
    monkeypatch.setattr(setup, "_download", lambda url: urls.append(url) or script)
    fake_line.path.unlink()  # not installed yet
    real_which = setup._which
    known = Path(os.environ["LOCALAPPDATA"]) / "line-cli" / "bin" / fake_line.path.name
    monkeypatch.setattr(runner, "known_install_paths", lambda *a, **k: [known])

    def fake_installer(cmd, env):
        ran.append((cmd, env, Path(cmd[-1]).read_bytes()))
        fake_line.install(known.parent, known.name)
        return 0, "Installed LINE CLI"

    monkeypatch.setattr(setup, "_run_installer", fake_installer)
    monkeypatch.setattr(
        setup,
        "_which",
        lambda n: real_which(n) or ("powershell.exe" if n == "powershell" else None),
    )
    fake_line.scenario([rule(["version"], stdout="line v0.4.0")])
    code, out = call(["--install", "--yes"], capsys)

    assert code == 0, out
    assert urls == [
        "https://raw.githubusercontent.com/kongesque/line-cli/v0.4.0/scripts/install-release.ps1"
    ]
    cmd, env, content = ran[0]
    assert cmd[0].lower().startswith("powershell")
    assert cmd[cmd.index("-ExecutionPolicy") + 1] == "Bypass" and "-File" in cmd
    assert (
        env["LINE_CLI_DOWNLOAD_BASE"]
        == "https://github.com/kongesque/line-cli/releases/download/v0.4.0"
    )
    assert content == script
    # Git Bash's GNU tar must not shadow Windows' bsdtar: System32 leads PATH for the installer
    path_key = next(k for k in env if k.upper() == "PATH")
    assert env[path_key].split(os.pathsep)[0].lower().endswith("system32")
    assert not any("sudo" in part for part in cmd)
    assert out["status"] == "installed" and out["line"]["version"] == "v0.4.0"
    assert out["line"]["source"] == "known"  # not on PATH in this session, still usable
    assert "path_note" in out and out["next"] == "login"
    assert not Path(cmd[-1]).exists()  # temp dir cleaned


def _sys32(root):
    return os.path.join(root, "System32")


def test_windows_installer_env_puts_system32_first():
    root = r"C:\Windows"
    env = setup._windows_installer_env(
        {"SystemRoot": root, "PATH": os.pathsep.join(["gitbin", "other"]), "KEEP": "1"}
    )
    assert env["PATH"].split(os.pathsep) == [_sys32(root), "gitbin", "other"]
    assert env["KEEP"] == "1"


def test_windows_installer_env_path_key_is_case_insensitive():
    root = r"D:\Win"
    original = {"SystemRoot": root, "Path": os.pathsep.join(["a", "b"])}
    env = setup._windows_installer_env(original)
    assert "PATH" not in env  # reuses the existing key spelling, no duplicate variable
    assert env["Path"].split(os.pathsep) == [_sys32(root), "a", "b"]
    assert original["Path"] == os.pathsep.join(["a", "b"])  # input not mutated


def test_windows_installer_env_dedupes_system32_and_empty_entries():
    root = r"C:\Windows"
    path = os.pathsep.join(["x", _sys32(root), "", "y", _sys32(root)])
    env = setup._windows_installer_env({"SystemRoot": root, "PATH": path})
    assert env["PATH"].split(os.pathsep) == [_sys32(root), "x", "y"]


def test_windows_installer_env_without_systemroot_uses_default_and_sets_path():
    env = setup._windows_installer_env({})
    assert env["PATH"] == _sys32(r"C:\Windows")
    upper = setup._windows_installer_env({"SYSTEMROOT": r"E:\W", "PATH": "p"})
    assert upper["PATH"].split(os.pathsep) == [_sys32(r"E:\W"), "p"]


def test_install_linux_never_sudo_and_reports_keyring(fake_line, capsys, monkeypatch):
    script = b"#!/bin/sh\n"
    monkeypatch.setitem(
        setup.PINNED_SCRIPT_SHA256,
        "v0.4.0",
        {"install-release.sh": hashlib.sha256(script).hexdigest()},
    )
    monkeypatch.setattr(setup, "current_os", lambda: "linux")
    monkeypatch.setattr(setup, "_which", lambda n: "/usr/bin/curl" if n == "curl" else None)
    monkeypatch.setattr(setup, "_download", lambda url: script)
    fake_line.path.unlink()
    ran = []
    known = Path(os.environ["HOME"]) / ".local" / "bin" / fake_line.path.name
    monkeypatch.setattr(runner, "known_install_paths", lambda *a, **k: [known])

    def fake_installer(cmd, env):
        ran.append(cmd)
        fake_line.install(known.parent, known.name)
        return 0, ""

    monkeypatch.setattr(setup, "_run_installer", fake_installer)
    fake_line.scenario(
        [
            rule(["version"], stdout="line v0.4.0"),
            rule(
                ["auth", "status", "--json"],
                exit=69,
                stdout={"reason": "storage_unavailable"},
                stderr="no",
            ),
        ]
    )
    code, out = call(["--install", "--yes"], capsys)
    assert ran and ran[0][0] == "sh" and "sudo" not in " ".join(ran[0])
    assert code == 3 and out["status"] == "needs_user_action"
    action = out["needs_user_action"][0]
    assert action["command"].startswith("sudo ") and "gnome-keyring" in action["command"]
    assert out["line"]["version"] == "v0.4.0"


def test_install_linux_without_curl_asks_user(capsys, monkeypatch):
    script = b"#!/bin/sh\n"
    monkeypatch.setitem(
        setup.PINNED_SCRIPT_SHA256,
        "v0.4.0",
        {"install-release.sh": hashlib.sha256(script).hexdigest()},
    )
    monkeypatch.setattr(setup, "current_os", lambda: "linux")
    monkeypatch.setattr(setup, "_which", lambda n: None)
    monkeypatch.setattr(setup, "_download", lambda url: script)
    monkeypatch.setattr(
        setup, "_run_installer", lambda c, e: pytest.fail("must not run without curl")
    )
    code, out = call(["--install", "--yes"], capsys)
    assert code == 3 and out["status"] == "needs_user_action"


def test_install_failure_is_reported(capsys, monkeypatch):
    script = b"x"
    monkeypatch.setitem(
        setup.PINNED_SCRIPT_SHA256,
        "v0.4.0",
        {"install-release.ps1": hashlib.sha256(script).hexdigest()},
    )
    monkeypatch.setattr(setup, "current_os", lambda: "windows")
    monkeypatch.setattr(setup, "_which", lambda n: "powershell.exe")
    monkeypatch.setattr(setup, "_download", lambda url: script)
    monkeypatch.setattr(
        setup, "_run_installer", lambda c, e: (1, "Archive checksum verification failed.")
    )
    code, out = call(["--install", "--yes"], capsys)
    assert code == 1 and out["status"] == "failed" and out["stage"] == "run"
    assert "checksum" in out["output_tail"][-1]


def test_pinned_hashes_cover_default_tag():
    pinned = setup.PINNED_SCRIPT_SHA256[setup.LINE_CLI_VERSION]
    assert set(pinned) == {"install-release.ps1", "install-release.sh"}
    assert all(len(h) == 64 for h in pinned.values())


# ---------------------------------------------------------------- --doctor


def by_id(report):
    return {i["id"]: i for i in report["items"]}


def test_doctor_all_ok(fake_line, capsys, isolated_env):
    healthy(fake_line)
    code, report = call(["--doctor"], capsys)
    items = by_id(report)
    assert code == 0 and report["ok"] is True
    for key in ("python", "binary", "version", "login", "config", "archive_dir", "update"):
        assert items[key]["status"] == "ok", items[key]
    assert items["keyring"]["status"] in {"skip", "ok"}
    for item in report["items"]:  # every item has the promised shape
        assert {"status", "explain", "fix"} <= set(item)


def test_doctor_without_line(capsys):
    code, report = call(["--doctor"], capsys)
    items = by_id(report)
    assert code == 1 and report["ok"] is False
    assert items["binary"]["status"] == "fail" and "--install" in items["binary"]["fix"]
    assert items["login"]["status"] == "skip" and items["update"]["status"] == "skip"


def test_doctor_not_logged_in_and_old_version_and_update(fake_line, capsys):
    fake_line.scenario(
        [
            rule(["version"], stdout="line v0.3.1"),
            rule(["whoami", "--json"], responses=[NOT_LOGGED_IN]),
            rule(["auth", "status", "--json"], stdout={"reason": "ok"}),
            rule(
                ["update", "--check", "--json"],
                stdout={
                    "status": "update_available",
                    "latest_version": "v0.9.0",
                    "upgrade_command": "brew upgrade line-cli",
                },
            ),
        ]
    )
    code, report = call(["--doctor"], capsys)
    items = by_id(report)
    assert code == 1
    assert items["login"]["status"] == "fail" and "login.py" in items["login"]["fix"]
    assert items["version"]["status"] == "warn" and "v0.4.0" in items["version"]["explain"]
    assert items["update"]["status"] == "warn" and items["update"]["fix"] == "brew upgrade line-cli"


def test_doctor_update_check_offline_is_only_a_warning(fake_line, capsys):
    fake_line.scenario(
        [
            rule(["version"], stdout="line v0.4.0"),
            rule(["whoami", "--json"], stdout=WHOAMI_OK),
            rule(["update", "--check", "--json"], exit=1, stderr="line: dial tcp: no such host"),
        ]
    )
    code, report = call(["--doctor"], capsys)
    assert by_id(report)["update"]["status"] == "warn" and code == 0


def test_doctor_archive_not_writable(fake_line, capsys, monkeypatch):
    healthy(fake_line)
    monkeypatch.setattr(setup, "_writable", lambda p: (False, Path("/nowhere")))
    code, report = call(["--doctor"], capsys)
    item = by_id(report)["archive_dir"]
    assert code == 1 and item["status"] == "fail" and "LINE_SKILL_ARCHIVE" in item["fix"]


def test_writable_probe_creates_nothing(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    ok, probe = setup._writable(base / "a" / "b" / "c")
    assert ok and probe == base
    assert list(base.iterdir()) == []
    file_path = tmp_path / "f"
    file_path.write_text("x")
    assert setup._writable(file_path / "sub")[0] is False


def test_doctor_invalid_config(fake_line, capsys, isolated_env):
    healthy(fake_line)
    cfg_dir = Path(os.environ["APPDATA" if os.name == "nt" else "XDG_CONFIG_HOME"]) / "line-skill"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text("not [valid", encoding="utf-8")
    code, report = call(["--doctor"], capsys)
    assert code == 1 and by_id(report)["config"]["status"] == "fail"


def test_doctor_honors_config_line_path(isolated_env, fake_line, capsys):
    """line_path from config.toml wins even if PATH has nothing."""
    healthy(fake_line)
    target = fake_line.path
    cfg_dir = Path(os.environ["APPDATA" if os.name == "nt" else "XDG_CONFIG_HOME"]) / "line-skill"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text(f'line_path = "{target.as_posix()}"\n', encoding="utf-8")
    _, out = call(["--check"], capsys)
    assert out["line"]["source"] == "config"


# ---------------------------------------------------------------- CLI surface


def test_cli_help_and_requires_a_mode():
    base = [sys.executable, str(SCRIPTS / "setup.py")]
    proc = subprocess.run([*base, "--help"], capture_output=True, text=True)
    assert proc.returncode == 0 and "--doctor" in proc.stdout
    assert subprocess.run([*base], capture_output=True, text=True).returncode == 2
    both = subprocess.run([*base, "--check", "--doctor"], capture_output=True, text=True)
    assert both.returncode == 2


def test_cli_check_json_on_stdout_only(fake_line):
    healthy(fake_line)
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "setup.py"), "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=os.environ.copy(),
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["logged_in"] is True
