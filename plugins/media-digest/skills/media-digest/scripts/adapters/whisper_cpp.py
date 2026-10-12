#!/usr/bin/env python3
"""ASR adapter: whisper.cpp via the `whisper-cli` executable (stdlib only).

Usage:  whisper_cpp.py --audio PATH --lang CODE|auto [--prompt TEXT] --model-path FILE.bin
                       [--binary whisper-cli] [--threads N]
        whisper_cpp.py --probe [--model-path FILE.bin]

Needs a ggml model file (config `model_path`) and 16 kHz 16-bit WAV input (the digest pipeline
normalises audio first; a non-16k file exits 2 so the next adapter can be tried).
Exit codes: 0 ok, 2 environment unavailable, 1 failure.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

HINT = (
    "Install whisper.cpp (https://github.com/ggml-org/whisper.cpp): macOS `brew install "
    "whisper-cpp`; Windows: download the prebuilt zip from the releases page and put "
    "`whisper-cli.exe` on PATH; Linux: build from source. Then download a ggml model, e.g. "
    "`sh ./models/download-ggml-model.sh large-v3-turbo`, and set `model_path` in config.toml."
)


def emit(obj: dict) -> None:
    sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def base_lang(lang: str) -> str:
    lang = lang.strip().lower().replace("_", "-")
    return "auto" if lang in ("", "auto") else lang.split("-")[0]


def find_binary(name: str) -> str | None:
    return shutil.which(name)


def probe(binary: str, model_path: str | None) -> int:
    exe = find_binary(binary)
    if not exe:
        emit(
            {
                "available": False,
                "reason": f"not_found: `{binary}` is not on PATH",
                "install_hint": HINT,
            }
        )
    elif not model_path:
        emit(
            {
                "available": False,
                "reason": "no_model_path: set `model_path` (ggml .bin) in "
                "the [adapters.whisper_cpp] table of config.toml",
                "install_hint": HINT,
            }
        )
    elif not Path(model_path).expanduser().is_file():
        emit(
            {
                "available": False,
                "reason": f"model_missing: {model_path} does not exist",
                "install_hint": HINT,
            }
        )
    else:
        emit({"available": True, "reason": f"{exe}; model {model_path}", "install_hint": None})
    return 0


def check_wav(path: str) -> str | None:
    """None if 16 kHz PCM WAV, else a short reason string."""
    try:
        with wave.open(path, "rb") as wf:
            if wf.getframerate() != 16000:
                return f"needs_16khz_wav: input is {wf.getframerate()} Hz"
            if wf.getsampwidth() != 2:
                return f"needs_16khz_wav: input is {wf.getsampwidth() * 8}-bit, need 16-bit"
    except (wave.Error, EOFError):
        return "needs_16khz_wav: input is not a PCM WAV file (convert with ffmpeg first)"
    return None


def parse_whisper_json(data: dict) -> dict:
    """Parse whisper-cli `-oj`: {result:{language}, transcription:[{offsets:{from,to},text}]}."""
    segments = []
    for item in data.get("transcription", []):
        text = str(item.get("text", "")).strip()
        offsets = item.get("offsets") or {}
        start = float(offsets.get("from", 0)) / 1000.0
        end = float(offsets.get("to", 0)) / 1000.0
        if text:
            segments.append({"start": round(start, 3), "end": round(end, 3), "text": text})
    language = (data.get("result") or {}).get("language")
    return {
        "text": " ".join(s["text"] for s in segments),
        "language": language,
        "segments": segments,
    }


def run(args: argparse.Namespace) -> int:
    exe = find_binary(args.binary)
    if not exe:
        print(f"not_found: `{args.binary}` is not on PATH", file=sys.stderr)
        return 2
    if not args.model_path:
        print("no_model_path: set `model_path` in [adapters.whisper_cpp]", file=sys.stderr)
        return 2
    model = Path(args.model_path).expanduser()
    if not model.is_file():
        print(f"model_missing: {model} does not exist", file=sys.stderr)
        return 2
    if not Path(args.audio).is_file():
        print(f"audio file not found: {args.audio}", file=sys.stderr)
        return 1
    problem = check_wav(args.audio)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="mdasr-") as tmp:
        stem = str(Path(tmp) / "out")
        cmd = [
            exe,
            "-m",
            str(model),
            "-f",
            args.audio,
            "-l",
            base_lang(args.lang),
            "-oj",
            "-of",
            stem,
            "-np",
        ]
        if args.prompt:
            cmd += ["--prompt", args.prompt]
        if args.threads:
            cmd += ["-t", str(args.threads)]
        proc = subprocess.run(
            cmd, capture_output=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL
        )
        out_file = Path(stem + ".json")
        if proc.returncode != 0 or not out_file.is_file():
            tail = [ln for ln in proc.stderr.splitlines() if ln.strip()]
            print(
                f"whisper_cli_failed: exit {proc.returncode}: {tail[-1] if tail else ''}",
                file=sys.stderr,
            )
            return 1
        try:
            data = json.loads(out_file.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            print(f"whisper_cli_failed: unreadable JSON output: {exc}", file=sys.stderr)
            return 1
    emit(parse_whisper_json(data))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="whisper.cpp ASR adapter")
    ap.add_argument("--audio")
    ap.add_argument("--lang", default="auto")
    ap.add_argument("--prompt")
    ap.add_argument("--model", help="ignored; whisper.cpp uses --model-path")
    ap.add_argument("--model-path")
    ap.add_argument("--binary", default="whisper-cli")
    ap.add_argument("--threads", type=int)
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args(argv)
    if args.probe:
        return probe(args.binary, args.model_path)
    if not args.audio:
        ap.error("--audio is required")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
