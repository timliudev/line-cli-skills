"""ASR dispatcher: route by language, run adapters as subprocesses, fall back on failure.

Adapter contract (see docs / references/adapters.md)::

    <adapter> --audio PATH --lang CODE|auto [--prompt TEXT] [--model NAME] [config flags]
      stdout: {"text": "...", "language": "zh",
               "segments": [{"start": 0.0, "end": 3.2, "text": "..."}]}
      exit 0 = ok, 2 = environment unavailable (try next), 1 = failed (try next, recorded)
    <adapter> --probe  ->  {"available": bool, "reason": "...", "install_hint": "..."}

Secrets never travel on a command line: config only names the env var (``--api-key-env``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import config as cfgmod

ADAPTER_DIR = Path(__file__).resolve().parent.parent / "adapters"
DEFAULT_TIMEOUT = 3600  # seconds per adapter run; override with `timeout` in the adapter table
PROBE_TIMEOUT = 30
_SCRIPTS = {
    "faster_whisper": ADAPTER_DIR / "faster_whisper.py",
    "whisper_cpp": ADAPTER_DIR / "whisper_cpp.py",
    "openai_compat": ADAPTER_DIR / "openai_compat.py",
}


# --------------------------------------------------------------------------- invocation


def _flag(argv: list[str], flag: str, value: Any) -> None:
    if value is not None and value != "":
        argv += [flag, str(value)]


def _builtin_argv(name: str, spec: dict[str, Any], *, probe: bool) -> list[str]:
    """Interpreter + script + config flags (no audio/lang/prompt)."""
    typ = spec["type"]
    script = str(_SCRIPTS[typ])
    uv = shutil.which("uv")
    # faster_whisper declares its dependency with PEP 723; `uv run` installs it on demand.
    # Probes always use the plain interpreter so that nothing heavy gets installed or imported.
    if typ == "faster_whisper" and uv and not probe:
        argv = [uv, "run", "--quiet", "--script", script]
    else:
        argv = [sys.executable, script]
    _flag(argv, "--model", spec.get("model"))
    if typ == "faster_whisper":
        _flag(argv, "--device", spec.get("device"))
        _flag(argv, "--compute-type", spec.get("compute_type"))
    elif typ == "whisper_cpp":
        _flag(argv, "--model-path", spec.get("model_path"))
        _flag(argv, "--binary", spec.get("binary"))
        _flag(argv, "--threads", spec.get("threads"))
    elif typ == "openai_compat":
        _flag(argv, "--base-url", spec.get("base_url"))
        _flag(argv, "--api-key-env", spec.get("api_key_env"))
        _flag(argv, "--response-format", spec.get("response_format"))
        _flag(argv, "--language-field", spec.get("language_field"))
        _flag(argv, "--max-mb", spec.get("max_mb"))
        for key, value in (spec.get("extra") or {}).items():
            argv += ["--extra", f"{key}={value}"]
    return argv


def _expand_template(
    cmd: list[str], *, audio: str, lang: str, prompt: str | None, model: str | None
) -> list[str]:
    values = {"audio": audio, "lang": lang, "prompt": prompt or "", "model": model or ""}
    out = []
    for part in cmd:
        part = os.path.expanduser(part) if part.startswith("~") else part
        for key, val in values.items():
            part = part.replace("{" + key + "}", val)
        out.append(part)
    return out


def _resolve_exe(argv: list[str]) -> list[str] | None:
    """Resolve argv[0] on PATH (handles .cmd/.exe on Windows); None if it cannot be found."""
    exe = shutil.which(argv[0])
    if exe is None and os.path.isfile(argv[0]):
        exe = argv[0]
    return [exe, *argv[1:]] if exe else None


def _run(argv: list[str], timeout: float) -> tuple[int | None, str, str]:
    """Run a subprocess. Returns (exit_code, stdout, stderr); exit_code None means timeout."""
    resolved = _resolve_exe(argv)
    if resolved is None:
        return 2, "", f"not_found: executable '{argv[0]}' is not on PATH"
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(
            resolved,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return None, "", f"timeout: no result after {timeout:g}s"
    except OSError as exc:
        return 2, "", f"not_executable: {exc}"
    return proc.returncode, proc.stdout, proc.stderr


def _last_line(text: str, limit: int = 300) -> str | None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1][:limit] if lines else None


# --------------------------------------------------------------------------- output parsing


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_output(stdout: str) -> dict[str, Any] | None:
    """Contract JSON (or plain text -> one segment) -> normalised dict; None if unusable."""
    body = stdout.strip()
    if not body:
        return None
    data: Any = None
    for candidate in (body, body.splitlines()[-1]):  # tolerate stray lines before the JSON
        try:
            data = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    if data is None:
        return {
            "text": body,
            "language": None,
            "segments": [{"start": 0.0, "end": 0.0, "text": body}],
        }
    if not isinstance(data, dict) or ("text" not in data and "segments" not in data):
        return None
    segments = []
    for seg in data.get("segments") or []:
        if isinstance(seg, dict) and "text" in seg:
            segments.append(
                {
                    "start": _num(seg.get("start")),
                    "end": _num(seg.get("end")),
                    "text": str(seg["text"]).strip(),
                }
            )
    text = data.get("text")
    if not isinstance(text, str):
        text = " ".join(s["text"] for s in segments)
    text = text.strip()
    if not segments and text:
        segments = [{"start": 0.0, "end": 0.0, "text": text}]
    lang = data.get("language")
    return {
        "text": text,
        "language": lang if isinstance(lang, str) and lang else None,
        "segments": segments,
    }


# --------------------------------------------------------------------------- public API


def _attempt(name: str, code: int | None, reason: str | None) -> dict[str, Any]:
    return {"adapter": name, "exit_code": code, "reason": reason}


def _fail(attempts: list[dict[str, Any]], error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "adapter": None,
        "text": "",
        "language": None,
        "segments": [],
        "attempts": attempts,
        "error": error,
    }


def _run_adapter(
    name: str, spec: dict[str, Any], audio: str, lang: str, prompt: str | None
) -> tuple[int | None, str, str]:
    timeout = float(spec.get("timeout") or DEFAULT_TIMEOUT)
    if spec["type"] == "command":
        argv = _expand_template(
            spec["cmd"], audio=audio, lang=lang, prompt=prompt, model=spec.get("model")
        )
        return _run(argv, timeout)
    argv = _builtin_argv(name, spec, probe=False)
    argv += ["--audio", audio, "--lang", lang]
    _flag(argv, "--prompt", prompt)
    return _run(argv, timeout)


def transcribe(audio_path: str, lang: str = "auto", prompt: str | None = None) -> dict[str, Any]:
    """Transcribe with the configured adapter chain for ``lang``; first success wins."""
    attempts: list[dict[str, Any]] = []
    try:
        cfg = cfgmod.load_config()
    except cfgmod.ConfigError as exc:
        return _fail(attempts, f"config: {exc}")
    audio = str(Path(audio_path))
    if not Path(audio).is_file():
        return _fail(attempts, f"audio file not found: {audio}")

    lang = (lang or "auto").strip() or "auto"
    chain = list(dict.fromkeys(cfgmod.route_for(cfg, lang)))  # de-duplicate, keep order
    if not chain:
        return _fail(attempts, "no ASR adapters configured (empty order/route)")

    for name in chain:
        spec = cfg["adapters"][name]
        code, stdout, stderr = _run_adapter(name, spec, audio, lang, prompt)
        reason = _last_line(stderr)
        if code == 0:
            parsed = parse_output(stdout)
            if parsed is None:
                attempts.append(
                    _attempt(name, 0, "bad_output: adapter did not print contract JSON")
                )
                continue
            attempts.append(_attempt(name, 0, None))
            return {"ok": True, "adapter": name, **parsed, "attempts": attempts, "error": None}
        attempts.append(_attempt(name, code, reason or ("timeout" if code is None else None)))
    summary = "; ".join(
        f"{a['adapter']}: {a['reason'] or 'exit ' + str(a['exit_code'])}" for a in attempts
    )
    return _fail(attempts, f"all adapters failed or unavailable ({summary})")


def _probe_one(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    typ = spec["type"]
    row: dict[str, Any] = {
        "name": name,
        "type": typ,
        "available": False,
        "reason": None,
        "install_hint": None,
        "cloud": cfgmod.is_cloud(spec),
    }
    if typ == "command":
        probe_cmd = spec.get("probe_cmd")
        if probe_cmd:
            code, out, err = _run(probe_cmd, PROBE_TIMEOUT)
        else:  # no execution: only check that the executable exists
            exe = _resolve_exe(spec["cmd"])
            if exe is None:
                row["reason"] = f"not_found: '{spec['cmd'][0]}' is not on PATH"
                row["install_hint"] = spec.get("install_hint") or "Fix `cmd` in your config."
            else:
                row.update(available=True, reason="executable found (not probed further)")
            return row
    else:
        argv = [*_builtin_argv(name, spec, probe=True), "--probe"]
        code, out, err = _run(argv, PROBE_TIMEOUT)
    try:
        info = json.loads(out.strip().splitlines()[-1]) if out.strip() else None
    except json.JSONDecodeError:
        info = None
    if not isinstance(info, dict):
        row["reason"] = _last_line(err) or f"probe failed (exit {code})"
        return row
    row.update(
        available=bool(info.get("available")),
        reason=info.get("reason"),
        install_hint=info.get("install_hint"),
    )
    for extra in ("model_cached", "download_size"):
        if extra in info:
            row[extra] = info[extra]
    return row


def list_adapters() -> list[dict[str, Any]]:
    """Probe every known adapter: [{name, type, available, reason, install_hint, ...}]."""
    try:
        cfg = cfgmod.load_config()
    except cfgmod.ConfigError as exc:
        return [
            {
                "name": "(config)",
                "type": None,
                "available": False,
                "reason": str(exc),
                "install_hint": f"Fix {cfgmod.config_path()}",
                "cloud": False,
            }
        ]
    return [_probe_one(name, spec) for name, spec in cfg["adapters"].items()]


def main(argv: list[str] | None = None) -> int:
    """Debug helper: `python -m mdcore.asr list` / `python -m mdcore.asr run FILE [--lang zh]`."""
    import argparse

    ap = argparse.ArgumentParser(prog="mdcore.asr")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    run = sub.add_parser("run")
    run.add_argument("audio")
    run.add_argument("--lang", default="auto")
    run.add_argument("--prompt")
    args = ap.parse_args(argv)
    result = (
        list_adapters() if args.cmd == "list" else transcribe(args.audio, args.lang, args.prompt)
    )
    sys.stdout.buffer.write(
        (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    return 0 if args.cmd == "list" or result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
