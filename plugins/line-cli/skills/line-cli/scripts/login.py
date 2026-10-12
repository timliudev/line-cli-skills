#!/usr/bin/env python3
"""login - open a NEW terminal window running `line login`, then wait for `whoami` to succeed.

The user scans the QR code (or types a password) in that window; this script never sees
credentials. Logging in replaces any active LINE Chrome-extension session, so the caller
must have the user's consent and passes --yes.

Exit codes
  0  logged_in (or already_logged_in)
  1  failed or timed out
  2  usage error
  3  confirmation_required (no --yes), or manual (no GUI terminal; run `line login` yourself)
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

if sys.version_info < (3, 11):  # noqa: UP036 - friendly message instead of an ImportError
    sys.exit("This script needs Python 3.11 or newer (found " + sys.version.split()[0] + ").")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from linecore import runner  # noqa: E402
from linecore.config import current_os, load_config_safe  # noqa: E402

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_USER = 0, 1, 2, 3
DEFAULT_TIMEOUT = 300.0
DEFAULT_INTERVAL = 3.0

# (executable, argv-prefix placed before the command), tried in order on Linux.
LINUX_TERMINALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("x-terminal-emulator", ("-e",)),
    ("gnome-terminal", ("--",)),
    ("konsole", ("-e",)),
    ("xterm", ("-e",)),
)
# Keeps the window open after line exits so the user can read the result.
LINUX_WRAPPER = (
    '"$0" "$@"; status=$?; '
    "printf '\\n(line exited with status %s) Press Enter to close...' \"$status\"; read _"
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

Launcher = Callable[[str, list[str]], dict[str, Any]]


def ps_quote(text: str) -> str:
    """Quote for a PowerShell single-quoted string."""
    return "'" + text.replace("'", "''") + "'"


def applescript_quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def windows_command(line_path: str, extra: list[str]) -> list[str]:
    inner = "& " + ps_quote(line_path) + " login" + "".join(" " + ps_quote(a) for a in extra)
    outer = "Start-Process powershell -ArgumentList '-NoExit','-Command'," + ps_quote(inner)
    shell = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
    return [shell, "-NoProfile", "-Command", outer]


def macos_command(line_path: str, extra: list[str]) -> list[str]:
    shell_line = " ".join(shlex.quote(p) for p in [line_path, "login", *extra])
    return [
        "osascript",
        "-e",
        'tell application "Terminal" to activate',
        "-e",
        f'tell application "Terminal" to do script {applescript_quote(shell_line)}',
    ]


def linux_commands(line_path: str, extra: list[str]) -> list[list[str]]:
    out = []
    for exe, prefix in LINUX_TERMINALS:
        found = shutil.which(exe)
        if found:
            out.append([found, *prefix, "sh", "-c", LINUX_WRAPPER, line_path, "login", *extra])
    return out


def _no_gui_reason(os_name: str) -> str | None:
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return "this looks like an SSH session; a window would open on the remote machine"
    if os_name == "linux" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return "no graphical display (DISPLAY/WAYLAND_DISPLAY is not set)"
    return None


def default_launcher(line_path: str, extra: list[str]) -> dict[str, Any]:
    """Open a terminal window running `line login`. Never waits for the window to close."""
    os_name = current_os()
    reason = _no_gui_reason(os_name)
    if reason:
        return {"launched": False, "reason": reason}
    try:
        if os_name == "windows":
            proc = subprocess.run(  # noqa: S603
                windows_command(line_path, extra),
                capture_output=True,
                timeout=30,
                stdin=subprocess.DEVNULL,
            )
            if proc.returncode != 0:
                return {"launched": False, "reason": f"Start-Process failed ({proc.returncode})"}
            return {"launched": True, "method": "powershell Start-Process"}
        if os_name == "macos":
            proc = subprocess.run(  # noqa: S603
                macos_command(line_path, extra),
                capture_output=True,
                timeout=30,
                stdin=subprocess.DEVNULL,
            )
            if proc.returncode != 0:
                return {"launched": False, "reason": f"osascript failed ({proc.returncode})"}
            return {"launched": True, "method": "osascript Terminal"}
        candidates = linux_commands(line_path, extra)
        if not candidates:
            return {"launched": False, "reason": "no terminal emulator found"}
        for cmd in candidates:
            try:
                subprocess.Popen(  # noqa: S603
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError:
                continue
            return {"launched": True, "method": Path(cmd[0]).name}
        return {"launched": False, "reason": "could not start any terminal emulator"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"launched": False, "reason": f"{type(exc).__name__}"}


def run_login(
    *,
    timeout: float,
    interval: float,
    extra: list[str],
    manual_only: bool,
    launcher: Launcher | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    config=None,
) -> tuple[dict[str, Any], int]:
    config = config or load_config_safe()[0]
    located = runner.find_line(config)
    if not located:
        return (
            {
                "status": "failed",
                "error": {
                    "kind": "binary_missing",
                    "message": "The `line` executable was not found.",
                    "hint": "Run `python setup.py --install` first.",
                },
            },
            EXIT_FAIL,
        )
    binary = located.path

    state, result = runner.check_login(binary=binary)
    if state == "logged_in":
        name = result.data.get("displayName") if isinstance(result.data, dict) else None
        return (
            {
                "status": "already_logged_in",
                "account": {"display_name": name},
                "note": "To switch accounts, `line logout` first (ask the user), "
                "then run this again.",
            },
            EXIT_OK,
        )

    manual = {
        "status": "manual",
        "command": " ".join(["line", "login", *(shlex.quote(a) for a in extra)]),
        "command_full_path": " ".join(
            [f'"{binary}"' if " " in binary else binary, "login", *(shlex.quote(a) for a in extra)]
        ),
        "instructions": "Ask the user to open a terminal, run the command, scan the QR code with "
        "LINE on their phone, and wait for 'Session saved securely'. Then run "
        "`python setup.py --check` to confirm.",
    }
    if manual_only:
        return {**manual, "reason": "--manual requested"}, EXIT_USER

    launch = (launcher or default_launcher)(binary, extra)
    if not launch.get("launched"):
        return {**manual, "reason": launch.get("reason", "could not open a terminal")}, EXIT_USER

    runner.log(f"Opened a terminal window ({launch.get('method')}); waiting for login ...")
    deadline = clock() + timeout
    polls = 0
    while True:
        sleep(interval)
        polls += 1
        # busy is expected while the login window holds the session lock; the poll loop
        # is the retry, so don't let the runner back off inside it
        state, result = runner.check_login(binary=binary, max_attempts=1)
        if state == "logged_in":
            name = result.data.get("displayName") if isinstance(result.data, dict) else None
            return (
                {
                    "status": "logged_in",
                    "account": {"display_name": name},
                    "polls": polls,
                    "launch": launch,
                },
                EXIT_OK,
            )
        if result.error and result.error.kind in {"binary_missing", "exec_failed"}:
            return {"status": "failed", "error": result.error.to_dict()}, EXIT_FAIL
        if clock() >= deadline:
            return (
                {
                    "status": "timeout",
                    "waited_seconds": round(timeout),
                    "polls": polls,
                    "launch": launch,
                    "message": "Login was not completed in time. If the user finished it, run "
                    "`python setup.py --check`; otherwise run this script again.",
                },
                EXIT_FAIL,
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="login.py",
        description=(__doc__ or "").split("\n\n")[0],
        epilog="Exit codes: 0 logged in, 1 failed/timeout, 2 usage, 3 confirmation or manual step.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="the user agreed that logging in signs out the LINE Chrome extension",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"seconds to wait for login (default {DEFAULT_TIMEOUT:g})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help=f"seconds between whoami checks (default {DEFAULT_INTERVAL:g})",
    )
    parser.add_argument(
        "--email",
        metavar="ADDRESS",
        help="use email/password login (the user types the password in the new "
        "window) instead of the QR code",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Linux: enrol with headless storage when no keyring is unlocked",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="do not open a window; just return the command for the user",
    )
    return parser


def main(argv: list[str] | None = None, *, launcher: Launcher | None = None) -> int:
    runner.configure_stdio()
    args = build_parser().parse_args(argv)
    if args.timeout <= 0 or args.interval <= 0:
        runner.emit({"status": "failed", "error": "--timeout and --interval must be positive"})
        return EXIT_USAGE
    if not args.yes:
        runner.emit(
            {
                "status": "confirmation_required",
                "message": "Logging in signs the LINE Chrome extension (or any other Chrome-style "
                "session) out. Tell the user, get a clear yes, then rerun with --yes.",
                "rerun": "python login.py --yes",
            }
        )
        return EXIT_USER

    extra: list[str] = []
    if args.email and not EMAIL_RE.fullmatch(args.email):
        runner.emit({"status": "failed", "error": "--email must look like name@example.com"})
        return EXIT_USAGE
    if args.email:
        extra += ["--email", args.email]
    if args.headless:
        extra.append("--headless")
    payload, code = run_login(
        timeout=args.timeout,
        interval=args.interval,
        extra=extra,
        manual_only=args.manual,
        launcher=launcher,
    )
    runner.emit(payload)
    return code


if __name__ == "__main__":
    sys.exit(main())
