#!/usr/bin/env python3
"""setup - detect, install and diagnose the upstream `line` CLI. JSON on stdout.

  setup.py --check     Is `line` installed? Is the user logged in? OS keyring state?
  setup.py --install   Show the install plan; with --yes actually run the official
                       installer (pinned release tag, never sudo).
  setup.py --doctor    One report with {status, explain, fix} per item.

Exit codes
  0  ok (--check always 0 once it ran; --doctor 0 unless an item failed)
  1  failure (install failed, doctor found a failing item)
  2  usage error
  3  needs the user: --install without --yes (plan only), or a manual step such as
     installing Linux keyring packages (this script never runs sudo)
"""

from __future__ import annotations

import argparse
import hashlib
import ntpath
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

if sys.version_info < (3, 11):  # noqa: UP036 - friendly message instead of an ImportError
    sys.exit("This script needs Python 3.11 or newer (found " + sys.version.split()[0] + ").")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from linecore import runner  # noqa: E402
from linecore.config import LINE_CLI_REPO, LINE_CLI_VERSION, current_os, load_config_safe  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_USER = 0, 1, 2, 3

# SHA-256 of scripts/install-release.{ps1,sh} at the pinned tag (git show TAG:path | sha256sum).
# Re-compute both whenever LINE_CLI_VERSION is bumped.
PINNED_SCRIPT_SHA256 = {
    "v0.5.0": {
        "install-release.ps1": "7bf73c2db8d8c5e2156d58b69ddc897446dd8775019057362803679c4c47e2f9",
        "install-release.sh": "8a515241793147330c1c491f65271dee034c6bfe8184de3b4ce5b015b7bceb92",
    },
    "v0.4.0": {
        "install-release.ps1": "7bf73c2db8d8c5e2156d58b69ddc897446dd8775019057362803679c4c47e2f9",
        "install-release.sh": "8a515241793147330c1c491f65271dee034c6bfe8184de3b4ce5b015b7bceb92",
    },
}
MAX_SCRIPT_BYTES = 256 * 1024
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
INSTALL_TIMEOUT = 600

KEYRING_FIX = {
    "debian": "sudo apt install libsecret-tools gnome-keyring",
    "fedora": "sudo dnf install libsecret gnome-keyring",
    "arch": "sudo pacman -S libsecret gnome-keyring",
    "suse": "sudo zypper install libsecret-tools gnome-keyring",
}


# ------------------------------------------------------------------ small seams (patched in tests)


def _download(url: str) -> bytes:
    if not url.startswith("https://"):
        raise ValueError("refusing non-HTTPS download")
    request = urllib.request.Request(url, headers={"User-Agent": "line-cli-skill-setup"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - https only
        data = response.read(MAX_SCRIPT_BYTES + 1)
    if len(data) > MAX_SCRIPT_BYTES:
        raise ValueError("installer script is unexpectedly large")
    return data


def _windows_installer_env(env: dict[str, str]) -> dict[str, str]:
    """Put System32 first on PATH for the PowerShell installer.

    The upstream script calls `tar.exe`. When Claude Code runs under Git Bash,
    PATH starts with Git's GNU tar, which reads `C:\\...` as a remote host and
    fails; Windows' bundled bsdtar in System32 handles the archive correctly.
    """
    # Windows-only semantics on purpose (ntpath, ";") so the logic is the same
    # whatever OS runs the unit tests.
    env = dict(env)
    system32 = ntpath.join(
        env.get("SystemRoot") or env.get("SYSTEMROOT") or r"C:\Windows", "System32"
    )
    key = next((k for k in env if k.upper() == "PATH"), "PATH")
    rest = [
        p
        for p in env.get(key, "").split(";")
        if p and ntpath.normcase(p) != ntpath.normcase(system32)
    ]
    env[key] = ";".join([system32, *rest])
    return env


def _run_installer(cmd: list[str], env: dict[str, str]) -> tuple[int, str]:
    """Run the installer; returns (exit_code, combined output tail)."""
    proc = subprocess.run(  # noqa: S603 - fixed argv built by this module
        cmd,
        capture_output=True,
        timeout=INSTALL_TIMEOUT,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    out = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    return proc.returncode, out[-2000:]


def _which(name: str) -> str | None:
    return shutil.which(name)


# ------------------------------------------------------------------ keyring


def _distro_family() -> str:
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return "debian"
    ids = " ".join(
        line.split("=", 1)[1].strip("\"' ")
        for line in text.splitlines()
        if line.startswith(("id=", "id_like="))
    )
    for family, needles in (
        ("fedora", ("fedora", "rhel", "centos")),
        ("arch", ("arch", "manjaro")),
        ("suse", ("suse",)),
    ):
        if any(n in ids for n in needles):
            return family
    return "debian"


def keyring_fix_command() -> str:
    return KEYRING_FIX[_distro_family()]


def keyring_status(os_name: str, binary: str | None) -> dict[str, Any]:
    """Linux needs a running, unlocked Secret Service keyring; Windows/macOS use DPAPI/Keychain."""
    if os_name != "linux":
        return {"status": "not_applicable", "detail": "uses the OS credential store"}
    has_tool = _which("secret-tool") is not None
    if binary:
        result = runner.run_line(["auth", "status", "--json"], binary=binary, timeout=30)
        data = result.data if isinstance(result.data, dict) else {}
        reason = data.get("reason")
        if result.ok or reason == "no_session":
            return {"status": "ok", "backend": data.get("backend"), "detail": "storage accessible"}
        if not has_tool:
            return {
                "status": "missing",
                "detail": "secret-tool not found",
                "fix": keyring_fix_command(),
            }
        if result.exit_code == 69:
            return {
                "status": "unavailable",
                "detail": "keyring tools exist but the keyring is not running/unlocked",
                "fix": "Log into a desktop session, or unlock/start gnome-keyring in the same "
                "D-Bus session; on a server see `line login --headless` in the upstream guide.",
            }
        return {
            "status": "unknown",
            "detail": reason or (result.error.kind if result.error else ""),
        }
    if not has_tool:
        return {
            "status": "missing",
            "detail": "secret-tool not found",
            "fix": keyring_fix_command(),
        }
    return {"status": "unknown", "detail": "secret-tool present; install line to verify"}


# ------------------------------------------------------------------ check


def _version_matches(found: str | None) -> bool | None:
    if not found or found == "dev":
        return None
    return found.lstrip("v") == LINE_CLI_VERSION.lstrip("v")


def gather_status(config) -> dict[str, Any]:
    os_name = current_os()
    located = runner.find_line(config)
    status: dict[str, Any] = {
        "os": os_name,
        "python": platform.python_version(),
        "line": {"found": located is not None, "path": None, "source": None, "version": None},
        "logged_in": None,
        "keyring": keyring_status(os_name, located.path if located else None)["status"],
        "documented_line_version": LINE_CLI_VERSION,
        "archive_root": str(config.archive_root),
    }
    if located:
        version = runner.line_version(binary=located.path)
        status["line"].update(
            {
                "path": located.path,
                "source": located.source,
                "version": version,
                "version_matches_docs": _version_matches(version),
            }
        )
        state, result = runner.check_login(binary=located.path)
        status["logged_in"] = {"logged_in": True, "logged_out": False}.get(state)
        if state == "unknown" and result.error:
            status["login_error"] = result.error.kind
        elif state == "logged_in" and isinstance(result.data, dict):
            status["account"] = {"display_name": result.data.get("displayName")}
    status["next_step"] = _next_step(status)
    return status


def _next_step(status: dict[str, Any]) -> str:
    if not status["line"]["found"]:
        return "install"
    if status["keyring"] in {"missing", "unavailable"}:
        return "fix_keyring"
    if status["logged_in"] is False:
        return "login"
    if status.get("login_error") == "session_replaced":
        return "session_replaced"
    if status["logged_in"] is None:
        return "retry_check"
    return "ready"


# ------------------------------------------------------------------ install


def _install_dir(os_name: str) -> str:
    if os_name == "windows":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return str(Path(local) / "line-cli" / "bin")
    return str(Path.home() / ".local" / "bin")


def build_install_plan(os_name: str, tag: str) -> dict[str, Any]:
    base = f"https://raw.githubusercontent.com/{LINE_CLI_REPO}/{tag}/scripts"
    download_base = f"https://github.com/{LINE_CLI_REPO}/releases/download/{tag}"
    plan: dict[str, Any] = {
        "os": os_name,
        "tag": tag,
        "install_dir": _install_dir(os_name),
        "needs_admin": False,
        "downloads": [],
        "will_modify": [],
    }
    if os_name == "macos" and _which("brew"):
        plan.update(
            method="homebrew",
            command=["brew", "install", "kongesque/tap/line-cli"],
            install_dir="(Homebrew prefix, usually /opt/homebrew/bin)",
            note="Homebrew installs the tap's current release; it cannot be pinned to the tag.",
        )
        plan["downloads"] = ["Homebrew formula kongesque/tap/line-cli (GitHub release archive)"]
        plan["will_modify"] = ["Homebrew prefix"]
        return plan

    script = "install-release.ps1" if os_name == "windows" else "install-release.sh"
    pinned = PINNED_SCRIPT_SHA256.get(tag, {}).get(script)
    plan.update(
        method=script,
        script_url=f"{base}/{script}",
        script_sha256=pinned,
        script_hash_pinned=pinned is not None,
        release_base=download_base,
        downloads=[
            f"{base}/{script}",
            f"{download_base}/line-<os>-<arch>.tar.gz",
            f"{download_base}/SHA256SUMS.txt",
        ],
    )
    if os_name == "windows":
        plan["will_modify"] = [plan["install_dir"], "your user PATH (no admin needed)"]
    else:
        plan["will_modify"] = [
            plan["install_dir"],
            "your shell profile (adds ~/.local/bin to PATH)",
        ]
    return plan


def explain_plan(plan: dict[str, Any]) -> list[str]:
    lines = [
        f"Download the LINE CLI ({plan['tag']}) from github.com/{LINE_CLI_REPO}, the project's "
        "official release page.",
        f"Install it into {plan['install_dir']}. Your own user account only; no administrator "
        "rights and no sudo.",
    ]
    if plan["method"] != "homebrew":
        if plan.get("script_hash_pinned"):
            lines.append(
                "The installer script is checked against a built-in SHA-256 before it runs."
            )
        else:
            lines.append(
                "WARNING: this tag has no built-in script hash, so the installer script itself "
                "cannot be verified here."
            )
        lines.append(
            "The installer then checks the program's SHA-256 against the release's "
            "SHA256SUMS file and aborts if it differs."
        )
    lines.append(
        "It does not log you in. Logging in is a separate step and signs the LINE "
        "Chrome extension out."
    )
    return lines


def _fetch_verified_script(plan: dict[str, Any], workdir: Path) -> Path:
    data = _download(plan["script_url"])
    expected = plan.get("script_sha256")
    digest = hashlib.sha256(data).hexdigest()
    if expected and digest != expected:
        raise ValueError(
            f"installer script hash mismatch (expected {expected[:12]}..., got {digest[:12]}...)"
        )
    path = workdir / plan["method"]
    path.write_bytes(data)
    return path


def _verification(plan: dict[str, Any]) -> dict[str, str]:
    """What this run actually checked; only reached after the installer exited 0."""
    if plan["method"] == "homebrew":
        return {
            "installer_script_sha256": "not applicable (Homebrew)",
            "release_archive": "not pinned or verified by this skill; Homebrew installs the "
            "tap's current release and does its own checks",
        }
    return {
        "installer_script_sha256": "verified against the built-in pinned hash"
        if plan.get("script_hash_pinned")
        else "not verified (this tag has no pinned hash)",
        "release_archive": "checked by the upstream installer against the release's "
        "SHA256SUMS.txt (it aborts on a mismatch)",
    }


def cmd_install(config, *, yes: bool, tag: str, force: bool) -> tuple[dict[str, Any], int]:
    os_name = current_os()
    located = runner.find_line(config)
    if located and not force:
        return (
            {
                "status": "already_installed",
                "line": {"path": located.path, "version": runner.line_version(binary=located.path)},
                "next": "Use `line update --check --json` to look for a newer release.",
            },
            EXIT_OK,
        )

    plan = build_install_plan(os_name, tag)
    plan["explain"] = explain_plan(plan)
    keyring = keyring_status(os_name, located.path if located else None)
    if os_name == "linux" and keyring["status"] == "missing":
        plan["after_install_needs_user_action"] = {
            "what": "keyring packages",
            "command": keyring.get("fix"),
            "why": "line stores its session in the Secret Service keyring on Linux",
        }
    if tag != LINE_CLI_VERSION:
        plan["warning"] = (
            f"--tag {tag} differs from the version these docs describe ({LINE_CLI_VERSION})."
        )
    if not yes:
        return (
            {
                "status": "plan",
                "requires_confirmation": True,
                "plan": plan,
                "rerun": "python setup.py --install --yes",
            },
            EXIT_USER,
        )

    runner.log(f"Installing LINE CLI {tag} via {plan['method']} ...")
    tmp = Path(tempfile.mkdtemp(prefix="line-cli-setup-"))
    try:
        if plan["method"] == "homebrew":
            cmd = list(plan["command"])
            env = dict(os.environ)
        else:
            try:
                script = _fetch_verified_script(plan, tmp)
            except (ValueError, urllib.error.URLError, OSError) as exc:
                return (
                    {"status": "failed", "stage": "download", "error": str(exc), "plan": plan},
                    EXIT_FAIL,
                )
            env = dict(os.environ)
            env["LINE_CLI_DOWNLOAD_BASE"] = plan["release_base"]
            if os_name == "windows":
                shell = _which("powershell") or _which("pwsh")
                if not shell:
                    return (
                        {
                            "status": "failed",
                            "stage": "prepare",
                            "error": "PowerShell was not found",
                        },
                        EXIT_FAIL,
                    )
                env = _windows_installer_env(env)
                cmd = [
                    shell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                ]
            else:
                if not _which("curl"):
                    return (
                        {
                            "status": "needs_user_action",
                            "needs_user_action": [
                                {
                                    "what": "curl",
                                    "command": "install curl with your "
                                    "package manager, then rerun this command",
                                    "why": "the official installer downloads with curl",
                                }
                            ],
                        },
                        EXIT_USER,
                    )
                cmd = ["sh", str(script)]
        try:
            code, output = _run_installer(cmd, env)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return {"status": "failed", "stage": "run", "error": str(exc)}, EXIT_FAIL
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if code != 0:
        runner.log(output)
        return (
            {
                "status": "failed",
                "stage": "run",
                "exit_code": code,
                "output_tail": output.strip().splitlines()[-5:],
            },
            EXIT_FAIL,
        )

    located = runner.find_line(config)
    if not located:
        return (
            {
                "status": "failed",
                "stage": "verify",
                "error": "installer finished but `line` was not found in the expected locations",
            },
            EXIT_FAIL,
        )
    version = runner.line_version(binary=located.path)
    result: dict[str, Any] = {
        "status": "installed",
        "verification": _verification(plan),
        "line": {
            "path": located.path,
            "source": located.source,
            "version": version,
            "version_matches_docs": _version_matches(version),
        },
    }
    if located.source == "known":
        result["path_note"] = (
            "`line` is not on PATH in this session yet; the skill finds it automatically. "
            "Open a new terminal before typing `line` by hand."
        )
    post = keyring_status(os_name, located.path)
    result["keyring"] = post["status"]
    if os_name == "linux" and post["status"] in {"missing", "unavailable"}:
        result["status"] = "needs_user_action"
        result["needs_user_action"] = [
            {"what": "keyring", "command": post.get("fix"), "why": post.get("detail")}
        ]
        result["note"] = (
            "line is installed. This script never runs sudo; ask the user to run the command."
        )
        return result, EXIT_USER
    result["next"] = "login"
    return result, EXIT_OK


# ------------------------------------------------------------------ doctor


def _item(
    item_id: str, status: str, explain: str, fix: str | None = None, **extra: Any
) -> dict[str, Any]:
    return {"id": item_id, "status": status, "explain": explain, "fix": fix, **extra}


def _writable(path: Path) -> tuple[bool, Path]:
    probe = path
    while not probe.exists():
        if probe.parent == probe:
            return False, probe
        probe = probe.parent
    if not probe.is_dir():
        return False, probe
    try:
        with tempfile.NamedTemporaryFile(dir=probe, prefix=".line-skill-probe-"):
            pass
    except OSError:
        return False, probe
    return True, probe


def run_doctor(config, config_error: str | None) -> tuple[dict[str, Any], int]:
    os_name = current_os()
    items: list[dict[str, Any]] = []

    items.append(_item("python", "ok", f"Python {platform.python_version()} (3.11+ required)."))

    located = runner.find_line(config)
    if located:
        items.append(
            _item(
                "binary",
                "ok",
                f"Found line at {located.path} (via {located.source}).",
                path=located.path,
            )
        )
    else:
        items.append(
            _item(
                "binary",
                "fail",
                "The `line` program is not installed.",
                "python setup.py --install  (shows a plan, then asks for --yes)",
            )
        )

    if located:
        version = runner.line_version(binary=located.path)
        match = _version_matches(version)
        if version is None:
            items.append(
                _item(
                    "version",
                    "warn",
                    "Could not read `line version`.",
                    "Run `line version` yourself and share the output.",
                )
            )
        elif match is None:
            items.append(
                _item(
                    "version",
                    "warn",
                    f"line reports `{version}` (source build).",
                    "Command flags may differ from the bundled references.",
                )
            )
        elif match:
            items.append(
                _item(
                    "version",
                    "ok",
                    f"line {version} matches the references ({LINE_CLI_VERSION}).",
                    version=version,
                )
            )
        else:
            items.append(
                _item(
                    "version",
                    "warn",
                    f"line is {version}, but the references describe {LINE_CLI_VERSION}; "
                    "some flags or outputs may differ.",
                    "Update the plugin, or `line update --check` to compare releases.",
                    version=version,
                )
            )
        state, result = runner.check_login(binary=located.path)
        if state == "logged_in":
            items.append(_item("login", "ok", "Logged in."))
        elif state == "logged_out":
            items.append(
                _item(
                    "login",
                    "fail",
                    "Not logged in.",
                    "python login.py --yes  (opens a terminal; tell the user it signs "
                    "the LINE Chrome extension out)",
                )
            )
        else:
            kind = result.error.kind if result.error else "unknown"
            if kind == "session_replaced":
                items.append(
                    _item(
                        "login",
                        "fail",
                        "The LINE session was replaced by another client (another computer or "
                        "Chrome LINE).",
                        "Ask the user first: logging in again here signs the other device out.",
                    )
                )
            else:
                items.append(
                    _item(
                        "login",
                        "warn",
                        f"Could not tell whether you are logged in ({kind}).",
                        "Retry in a moment; if it persists run `line auth status`.",
                    )
                )
    else:
        items.append(_item("version", "skip", "line is not installed."))
        items.append(_item("login", "skip", "line is not installed."))

    keyring = keyring_status(os_name, located.path if located else None)
    if keyring["status"] == "not_applicable":
        items.append(
            _item("keyring", "skip", "Not needed on this OS (uses the system credential store).")
        )
    elif keyring["status"] == "ok":
        items.append(_item("keyring", "ok", "The credential storage is reachable."))
    elif keyring["status"] == "unknown":
        items.append(_item("keyring", "warn", "Could not verify the keyring.", keyring.get("fix")))
    else:
        items.append(
            _item(
                "keyring",
                "fail",
                f"Linux keyring problem: {keyring.get('detail')}.",
                keyring.get("fix"),
            )
        )

    if config_error:
        items.append(_item("config", "fail", config_error, f"Fix or delete {config.config_path}."))
    elif config.warnings:
        items.append(
            _item("config", "warn", "; ".join(config.warnings), f"Edit {config.config_path}.")
        )
    else:
        where = str(config.config_path) if config.config_exists else "defaults (no config.toml)"
        items.append(_item("config", "ok", f"Config: {where}."))

    ok, probe = _writable(config.archive_root)
    if ok:
        items.append(
            _item(
                "archive_dir",
                "ok",
                f"Archive folder {config.archive_root} is writable (source: "
                f"{config.archive_source}).",
                path=str(config.archive_root),
            )
        )
    else:
        items.append(
            _item(
                "archive_dir",
                "fail",
                f"Cannot write under {probe}, so {config.archive_root} cannot be created.",
                "Set LINE_SKILL_ARCHIVE or `archive_root` in config.toml to a folder you own.",
            )
        )

    if located:
        upd = runner.run_line(["update", "--check", "--json"], binary=located.path, timeout=45)
        data = upd.data if isinstance(upd.data, dict) else {}
        status = data.get("status")
        if upd.ok and status == "update_available":
            items.append(
                _item(
                    "update",
                    "warn",
                    f"A newer line is available ({data.get('latest_version')}).",
                    data.get("upgrade_command")
                    or data.get("instructions")
                    or "Run `line update` (changes the install: ask the user first).",
                    latest=data.get("latest_version"),
                )
            )
        elif upd.ok:
            items.append(_item("update", "ok", f"Update check: {status or 'ok'}."))
        else:
            items.append(
                _item(
                    "update",
                    "warn",
                    "Could not check for updates (offline?).",
                    "Retry later; this does not affect normal use.",
                )
            )
    else:
        items.append(_item("update", "skip", "line is not installed."))

    counts = {s: sum(1 for i in items if i["status"] == s) for s in ("ok", "warn", "fail", "skip")}
    report = {"ok": counts["fail"] == 0, "summary": counts, "os": os_name, "items": items}
    return report, EXIT_OK if counts["fail"] == 0 else EXIT_FAIL


# ------------------------------------------------------------------ cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="setup.py",
        description="Detect, install and diagnose the upstream `line` CLI.",
        epilog="Exit codes: 0 ok, 1 failure, 2 usage, 3 needs user confirmation/action.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="report install/login/keyring state")
    mode.add_argument(
        "--install", action="store_true", help="show the install plan (add --yes to run it)"
    )
    mode.add_argument("--doctor", action="store_true", help="diagnose everything, with fixes")
    parser.add_argument(
        "--yes", action="store_true", help="the user confirmed the plan; actually install"
    )
    parser.add_argument(
        "--tag",
        default=LINE_CLI_VERSION,
        help=f"release tag to install (default {LINE_CLI_VERSION})",
    )
    parser.add_argument(
        "--force", action="store_true", help="install even if `line` was already found"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    runner.configure_stdio()
    args = build_parser().parse_args(argv)
    config, config_error = load_config_safe()
    if config_error:
        runner.log(f"warning: {config_error}")

    if args.check:
        runner.emit(gather_status(config))
        return EXIT_OK
    if args.doctor:
        report, code = run_doctor(config, config_error)
        runner.emit(report)
        return code
    if not TAG_RE.match(args.tag):
        runner.emit({"status": "failed", "error": f"invalid tag {args.tag!r}; expected vX.Y.Z"})
        return EXIT_USAGE
    payload, code = cmd_install(config, yes=args.yes, tag=args.tag, force=args.force)
    runner.emit(payload)
    return code


if __name__ == "__main__":
    sys.exit(main())
