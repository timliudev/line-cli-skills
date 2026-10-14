"""Locate and run the upstream ``line`` binary; classify its failures (stdlib only).

Safety rules enforced here (see references/safety.md):
  * Mutations (send, react, unsend, login, logout, update without --check,
    auth migrate, and anything unrecognised) get exactly ONE attempt.
  * Only read commands are retried, and only when the session lock is busy (exit 75).
  * Credentials are never logged: ``login`` output is never surfaced, stdin text is never
    echoed back, and results never contain the raw stderr.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config, current_os, load_config_safe

# ---------------------------------------------------------------- locating `line`

ENV_NO_COLOR = "NO_COLOR"
READ_COMMANDS = frozenset(
    {"chats", "contacts", "messages", "watch", "whoami", "version", "help", "download"}
)
DEFAULT_TIMEOUT = 60.0
DOWNLOAD_TIMEOUT = 150.0  # upstream transfer timeout is 2 minutes
UPDATE_TIMEOUT = 300.0
DEFAULT_READ_ATTEMPTS = 3
DEFAULT_BACKOFF = 1.0

_sleep = time.sleep  # indirection so tests can run without waiting


@dataclass(frozen=True)
class Located:
    path: str
    source: str  # "config" | "path" | "known"


def known_install_paths(
    env: Mapping[str, str] | None = None, os_name: str | None = None
) -> list[Path]:
    """Places the official installers / Homebrew put the binary."""
    env = os.environ if env is None else env
    os_name = os_name or current_os()
    if os_name == "windows":
        local = env.get("LOCALAPPDATA")
        base = (
            Path(local)
            if local
            else Path(env.get("USERPROFILE") or Path.home()) / "AppData" / "Local"
        )
        return [base / "line-cli" / "bin" / "line.exe"]
    home = Path(env.get("HOME") or Path.home())
    paths = [home / ".local" / "bin" / "line"]
    if os_name == "macos":
        paths.append(Path("/opt/homebrew/bin/line"))
    paths.append(Path("/usr/local/bin/line"))
    return paths


def _resolve_configured(line_path: str) -> str | None:
    expanded = os.path.expandvars(os.path.expanduser(line_path))
    candidate = Path(expanded)
    if candidate.is_dir():
        for name in ("line.exe", "line"):
            inner = candidate / name
            if inner.is_file():
                return str(inner)
        return None
    if candidate.is_file():
        return str(candidate)
    return shutil.which(expanded)


def find_line(
    config: Config | None = None,
    env: Mapping[str, str] | None = None,
    os_name: str | None = None,
) -> Located | None:
    """config.line_path -> PATH -> known install paths.

    The configured path wins so a fork/pinned build can shadow the system one.
    (docs/PLAN.md 4.1.0 lists PATH first; 4.4 requires line_path to be able to override.)
    """
    if config is None:
        config, _ = load_config_safe(env, os_name)
    if config.line_path:
        found = _resolve_configured(config.line_path)
        if found:
            return Located(found, "config")
    on_path = shutil.which("line")
    if on_path:
        return Located(on_path, "path")
    for candidate in known_install_paths(env, os_name):
        if candidate.is_file():
            return Located(str(candidate), "known")
    return None


# ---------------------------------------------------------------- classification


@dataclass(frozen=True)
class ErrorInfo:
    kind: str
    message: str
    retryable: bool
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.hint:
            out["hint"] = self.hint
        return out


def _flag_true(args: Sequence[str], name: str) -> bool:
    """True when ``--name`` / ``-name`` / ``--name=true`` is present (Go flag syntax)."""
    for arg in args:
        if arg == "--":
            break
        m = re.fullmatch(rf"--?{re.escape(name)}(?:=(.*))?", arg)
        if m:
            value = m.group(1)
            return value is None or value.lower() in {"1", "t", "true"}
    return False


def is_mutation(args: Sequence[str]) -> bool:
    """Anything that changes LINE state, the saved session, or the installed binary.

    Unknown commands count as mutations: the safe default is a single attempt.
    """
    if not args:
        return False
    command = args[0]
    if command in READ_COMMANDS:
        return False
    if command == "auth":
        return not (len(args) > 1 and args[1] == "status")
    if command == "update":
        return not _flag_true(args[1:], "check")
    return True


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_PIN = re.compile(r"(?i)\bpin\b[:\s]*\d+")


def _summarize_stderr(stderr: str, limit: int = 300) -> str:
    text = _ANSI.sub("", stderr).replace("\r", "")
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return ""
    prefixed = [ln for ln in lines if ln.startswith("line:")]
    chosen = (prefixed or lines)[-1]
    chosen = re.sub(r"^line:\s*", "", chosen)
    chosen = _PIN.sub("PIN [hidden]", chosen)
    return chosen[:limit]


# (substring, kind, retryable-for-reads, hint). Matched case-insensitively, first hit wins.
_STDERR_RULES: tuple[tuple[str, str, bool, str | None], ...] = (
    (
        "chats or contacts have that name",
        "ambiguous_name",
        False,
        "Several chats share this name. Use `lineq.py resolve NAME`, then pass the full ID.",
    ),
    (
        "no chat or contact has that exact name",
        "name_not_found",
        False,
        "Names must match exactly (case-insensitive). Use `lineq.py resolve NAME` to search.",
    ),
    (
        "invalidated or replaced by another client",
        "session_replaced",
        False,
        "Another computer or Chrome LINE took over this account's single session. Do not log "
        "in again without asking the user: logging in here signs that other device out.",
    ),
    (
        "run line login",
        "not_logged_in",
        False,
        "No usable session. Ask the user to log in (login.py --yes).",
    ),
    (
        "was logged out",
        "not_logged_in",
        False,
        "LINE ended the session. Ask the user to log in again (login.py --yes).",
    ),
    (
        "another line watch is already running",
        "watcher_running",
        False,
        "Stop the running `line watch` first; only one watcher can run at a time.",
    ),
    (
        "another line command is using the session",
        "busy",
        True,
        "Another line command holds the session lock; retry shortly.",
    ),
    (
        "attachment is no longer available",
        "attachment_expired",
        False,
        "LINE no longer serves this attachment; it cannot be downloaded.",
    ),
    (
        "external media urls",
        "attachment_external",
        False,
        "This attachment is an external URL, which the CLI cannot download.",
    ),
    (
        "still processing",
        "attachment_processing",
        True,
        "LINE is still processing the attachment; try again later.",
    ),
    (
        "exceeds the cli limit of 20 mib",
        "attachment_too_large",
        False,
        "The CLI handles attachments up to 20 MiB only.",
    ),
    (
        "utf-16 units",
        "message_too_long",
        False,
        "Text is limited to 10,000 UTF-16 units.",
    ),
    ("invalid options", "usage", False, "Check the flags with `line COMMAND --help`."),
    ("unexpected arguments", "usage", False, "Check the arguments with `line COMMAND --help`."),
    ("unknown command", "usage", False, "Run `line help` for the command list."),
    ("provide exactly one of", "usage", False, "send takes exactly one of --text/--stdin/--file."),
    ("limit must be", "usage", False, None),
    ("dial tcp", "network", True, None),
    ("no such host", "network", True, None),
    ("connection refused", "network", True, None),
    ("connection reset", "network", True, None),
    ("i/o timeout", "network", True, None),
    ("tls handshake", "network", True, None),
)

_EXIT_KINDS: dict[int, tuple[str, str, bool, str]] = {
    65: (
        "storage_invalid",
        "Saved session data is invalid, a key is missing, or authentication failed.",
        False,
        "Run `line auth status`; the user may need to log in again.",
    ),
    69: (
        "storage_unavailable",
        "The credential storage (keyring/helper) is unavailable or timed out.",
        False,
        "On Linux check that a Secret Service keyring is installed, running and unlocked "
        "(`setup.py --doctor`).",
    ),
    74: (
        "storage_uncertain",
        "A local storage write may or may not have completed.",
        False,
        "Repeat the same local operation; do not restore an older session copy.",
    ),
    75: (
        "busy",
        "Another line command is using the session (or storage changed during login).",
        True,
        "Retry shortly; for mutations check state first.",
    ),
    78: (
        "storage_config",
        "Storage needs configuration, consent, repair or an interactive check.",
        False,
        "Run `line auth status` in a terminal and follow its instructions.",
    ),
    130: ("cancelled", "The command was interrupted (SIGINT).", False, ""),
    143: ("cancelled", "The command was terminated (SIGTERM).", False, ""),
}

_NO_AUTO_RETRY = "Do not retry automatically; verify with `messages` whether it took effect."


def classify_error(
    exit_code: int | None,
    stderr: str,
    *,
    mutation: bool = False,
    command: str = "",
) -> ErrorInfo:
    """Map an exit code + stderr into ``{kind, message, retryable}`` (plus a hint).

    ``retryable`` is advisory and always False for mutations.
    """
    summary = _summarize_stderr(stderr)
    if command == "login":
        summary = ""  # login output can contain QR links / PINs; never surface it
    lowered = stderr.lower()

    kind = message = hint = None
    retryable = False

    if exit_code in (130, 143):
        kind, message, retryable, hint = _EXIT_KINDS[exit_code]
    else:
        for needle, rule_kind, rule_retry, rule_hint in _STDERR_RULES:
            if needle in lowered:
                kind, retryable, hint = rule_kind, rule_retry, rule_hint
                message = summary or rule_kind.replace("_", " ")
                break
        if kind is None and exit_code in _EXIT_KINDS:
            kind, default_msg, retryable, hint = _EXIT_KINDS[exit_code]
            message = summary or default_msg
    if kind is None:
        kind = "error"
        message = summary or f"line exited with status {exit_code}"
        hint = _NO_AUTO_RETRY if mutation else None

    if mutation:
        retryable = False
        if kind == "busy":
            hint = "Nothing was sent. Ask the user before trying again; never retry silently."
        elif kind in {"network", "error"}:
            hint = _NO_AUTO_RETRY
    return ErrorInfo(kind=kind, message=message or kind, retryable=retryable, hint=hint or None)


# ---------------------------------------------------------------- running


@dataclass
class LineResult:
    ok: bool
    exit_code: int | None
    data: Any = None
    text: str | None = None
    error: ErrorInfo | None = None
    warnings: list[str] = field(default_factory=list)
    partial: bool = False
    attempts: int = 1
    mutation: bool = False
    command: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "data": self.data,
            "error": self.error.to_dict() if self.error else None,
        }
        if self.text is not None and self.data is None:
            out["text"] = self.text
        if self.warnings:
            out["warnings"] = list(self.warnings)
        if self.partial:
            out["partial"] = True
        out["attempts"] = self.attempts
        return out


def default_timeout(args: Sequence[str]) -> float:
    command = args[0] if args else ""
    if command == "download":
        return DOWNLOAD_TIMEOUT
    if command == "update" and not _flag_true(args[1:], "check"):
        return UPDATE_TIMEOUT
    return DEFAULT_TIMEOUT


def _parse_stdout(stdout: str, command: str) -> tuple[Any, bool]:
    """Return (data, parsed). Handles a single JSON document or JSON lines (watch)."""
    text = stdout.strip()
    if not text:
        return None, False
    try:
        return json.loads(text), True
    except json.JSONDecodeError:
        pass
    if command == "watch":
        events: list[Any] = []
        for raw in text.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError:
                return None, False
        return events, True
    return None, False


def _env_for_child(extra: Mapping[str, str] | None) -> dict[str, str]:
    env = dict(os.environ)
    env[ENV_NO_COLOR] = "1"
    env.pop("QRCODE_DEBUG", None)  # upstream: debug mode can write QR data
    if extra:
        env.update(extra)
    return env


def _invoke(
    binary: str,
    args: Sequence[str],
    stdin_text: str | None,
    timeout: float,
    cwd: str | None,
    env: Mapping[str, str] | None,
) -> tuple[int | None, str, str, str | None]:
    """Run once -> (exit_code, stdout, stderr, failure); failure is None, 'timeout' or 'os'."""
    kwargs: dict[str, Any] = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, no shell
            [binary, *args],
            input=stdin_text.encode("utf-8") if stdin_text is not None else None,
            stdin=None if stdin_text is not None else subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            cwd=cwd,
            env=_env_for_child(env),
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        return None, "", "", "timeout"
    except OSError as exc:
        return None, "", str(exc.strerror or exc), "os"
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", errors="replace"),
        proc.stderr.decode("utf-8", errors="replace"),
        None,
    )


def start_line(binary: str, args: Sequence[str]) -> subprocess.Popen[bytes]:
    """Start ``line ARGS`` as a long-running child with piped stdout and stderr (for ``watch``).

    The caller owns the process: read both pipes, and terminate it when done. No retry happens
    here; the child gets the same clean environment as ``run_line`` and no console window.
    """
    kwargs: dict[str, Any] = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(  # noqa: S603 - argv list, no shell
        [binary, *_validate_args(args)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_env_for_child(None),
        **kwargs,
    )


def _validate_args(args: Sequence[str]) -> list[str]:
    out = []
    for arg in args:
        if not isinstance(arg, str) or "\x00" in arg:
            raise ValueError("line arguments must be strings without NUL bytes")
        out.append(arg)
    return out


def run_line(
    args: Sequence[str],
    *,
    stdin_text: str | None = None,
    timeout: float | None = None,
    binary: str | None = None,
    config: Config | None = None,
    max_attempts: int | None = None,
    backoff: float = DEFAULT_BACKOFF,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> LineResult:
    """Run ``line ARGS`` once (mutations) or with busy-retry (reads) and classify the outcome."""
    argv = _validate_args(args)
    mutation = is_mutation(argv)
    command = argv[0] if argv else ""

    if binary is None:
        located = find_line(config)
        if located is None:
            return LineResult(
                ok=False,
                exit_code=None,
                error=ErrorInfo(
                    "binary_missing",
                    "The `line` executable was not found.",
                    False,
                    "Run `python setup.py --check`, then `--install` (needs user confirmation).",
                ),
                mutation=mutation,
                command=argv,
            )
        binary = located.path

    effective_timeout = timeout if timeout is not None else default_timeout(argv)
    attempts_allowed = 1 if mutation else max(1, max_attempts or DEFAULT_READ_ATTEMPTS)

    attempt = 0
    while True:
        attempt += 1
        code, stdout, stderr, failure = _invoke(
            binary, argv, stdin_text, effective_timeout, cwd, env
        )
        if failure == "timeout":
            message = f"`line {command}` timed out after {effective_timeout:g}s."
            hint = (
                "The action may have completed; check with `messages` before any retry."
                if mutation
                else None
            )
            return LineResult(
                ok=False,
                exit_code=None,
                error=ErrorInfo("timeout", message, not mutation, hint),
                attempts=attempt,
                mutation=mutation,
                command=argv,
            )
        if failure == "os":
            return LineResult(
                ok=False,
                exit_code=None,
                error=ErrorInfo("exec_failed", f"Cannot execute line: {stderr}", False),
                attempts=attempt,
                mutation=mutation,
                command=argv,
            )

        data, parsed = _parse_stdout(stdout, command)
        if code == 0:
            warnings: list[str] = []
            if "--json" in argv and not parsed and stdout.strip():
                warnings.append("stdout was not valid JSON; returned as text")
            return LineResult(
                ok=True,
                exit_code=0,
                data=data,
                text=None if parsed else stdout.strip() or None,
                warnings=warnings,
                attempts=attempt,
                mutation=mutation,
                command=argv,
            )

        error = classify_error(code, stderr, mutation=mutation, command=command)

        # Partial result: messages / contacts --mid exit 1 but still wrote the full JSON.
        if (
            code == 1
            and parsed
            and isinstance(data, list)
            and command in {"messages", "contacts"}
            and "--json" in argv
        ):
            note = _summarize_stderr(stderr) or "some entries could not be read"
            return LineResult(
                ok=True,
                exit_code=code,
                data=data,
                warnings=[note],
                partial=True,
                attempts=attempt,
                mutation=mutation,
                command=argv,
            )

        if error.kind == "busy" and not mutation and attempt < attempts_allowed:
            _sleep(backoff * (2 ** (attempt - 1)))
            continue

        return LineResult(
            ok=False,
            exit_code=code,
            data=data if parsed else None,  # e.g. `auth status --json` still writes status
            error=error,
            attempts=attempt,
            mutation=mutation,
            command=argv,
        )


# ---------------------------------------------------------------- convenience helpers

_VERSION_RE = re.compile(r"\b(v?\d+\.\d+\.\d+[0-9A-Za-z.+-]*|dev)\b")


def parse_version(text: str) -> str | None:
    """`line version` prints ``line v0.4.0`` (source builds print ``line dev``)."""
    match = _VERSION_RE.search(text or "")
    return match.group(1) if match else None


def line_version(binary: str | None = None, config: Config | None = None) -> str | None:
    result = run_line(["version"], binary=binary, config=config, timeout=20)
    if not result.ok:
        return None
    return parse_version(result.text or "")


def check_login(
    binary: str | None = None, config: Config | None = None, max_attempts: int | None = None
) -> tuple[str, LineResult]:
    """Return ("logged_in" | "logged_out" | "unknown", result) from ``whoami --json``."""
    result = run_line(
        ["whoami", "--json"], binary=binary, config=config, timeout=30, max_attempts=max_attempts
    )
    if result.ok:
        return "logged_in", result
    kind = result.error.kind if result.error else ""
    if kind == "not_logged_in":
        return "logged_out", result
    return "unknown", result


# ---------------------------------------------------------------- output helpers


def configure_stdio() -> None:
    """Make stdout/stderr UTF-8 regardless of the Windows console code page.

    Under ``pythonw`` (Task Scheduler, background runs) the streams do not exist (``None``); they
    are replaced by a null device so printing never raises.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - process lifetime
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def emit(obj: Any) -> None:
    """Write one JSON document to stdout."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()


def log(message: str) -> None:
    """Diagnostics go to stderr."""
    sys.stderr.write(message.rstrip("\n") + "\n")
    sys.stderr.flush()
