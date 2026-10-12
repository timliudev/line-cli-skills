#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["faster-whisper>=1.2.1"]
# ///
"""ASR adapter: local faster-whisper (CTranslate2).

Usage:  faster_whisper.py --audio PATH --lang CODE|auto [--prompt TEXT] [--model NAME]
                          [--device auto|cpu|cuda] [--compute-type auto|int8|float16|...]
        faster_whisper.py --probe

Run it through `uv run` so the dependency is installed on demand (mdcore.asr does this when
uv is available). `--probe` never imports faster-whisper.
Exit codes: 0 ok, 2 environment unavailable, 1 failure.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

DEFAULT_MODEL = "large-v3-turbo"

# faster-whisper's built-in size aliases -> Hugging Face repo (1.2.x), used only to look for a
# cached copy and to estimate the download. Sizes are approximate (fp16 CT2 weights).
_ALIAS_REPO = {
    "tiny": ("Systran/faster-whisper-tiny", "~75 MB"),
    "base": ("Systran/faster-whisper-base", "~145 MB"),
    "small": ("Systran/faster-whisper-small", "~485 MB"),
    "medium": ("Systran/faster-whisper-medium", "~1.5 GB"),
    "large-v2": ("Systran/faster-whisper-large-v2", "~3.1 GB"),
    "large-v3": ("Systran/faster-whisper-large-v3", "~3.1 GB"),
    "large-v3-turbo": ("mobiuslabsgmbh/faster-whisper-large-v3-turbo", "~1.6 GB"),
    "turbo": ("mobiuslabsgmbh/faster-whisper-large-v3-turbo", "~1.6 GB"),
    "distil-large-v3": ("Systran/faster-distil-whisper-large-v3", "~1.5 GB"),
}


def emit(obj: dict) -> None:
    sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def base_lang(lang: str) -> str | None:
    lang = lang.strip().lower().replace("_", "-")
    return None if lang in ("", "auto") else lang.split("-")[0]


def hf_cache_dirs() -> list[Path]:
    dirs = []
    if os.environ.get("HF_HUB_CACHE"):
        dirs.append(Path(os.environ["HF_HUB_CACHE"]))
    if os.environ.get("HF_HOME"):
        dirs.append(Path(os.environ["HF_HOME"]) / "hub")
    dirs.append(Path.home() / ".cache" / "huggingface" / "hub")
    return dirs


def model_cache_state(model: str) -> tuple[bool, str]:
    """(cached?, approx download size) without touching the network or importing anything."""
    if Path(model).is_dir():  # a local CT2 model directory
        return True, "0 (local directory)"
    repo, size = _ALIAS_REPO.get(model, (model if "/" in model else None, "unknown size"))
    if repo is None:
        return False, size
    folder = "models--" + repo.replace("/", "--")
    return any((d / folder / "snapshots").is_dir() for d in hf_cache_dirs()), size


def probe(model: str) -> int:
    have_uv = shutil.which("uv") is not None
    have_pkg = importlib.util.find_spec("faster_whisper") is not None  # no import
    cached, size = model_cache_state(model)
    if not (have_uv or have_pkg):
        emit(
            {
                "available": False,
                "reason": "uv_not_found: neither `uv` nor the faster-whisper package is available",
                "install_hint": "Install uv (https://docs.astral.sh/uv/), "
                "e.g. `winget install astral-sh.uv`, `brew install uv`, or "
                "`curl -LsSf https://astral.sh/uv/install.sh | sh`.",
            }
        )
        return 0
    how = "via uv (dependency installed on first run)" if have_uv else "faster-whisper installed"
    note = "model cached" if cached else f"model '{model}' not cached; first run downloads {size}"
    emit(
        {
            "available": True,
            "reason": f"{how}; {note}",
            "install_hint": None,
            "model_cached": cached,
            "download_size": None if cached else size,
        }
    )
    return 0


def pick_device(device: str, compute_type: str) -> tuple[str, str]:
    if device == "auto":
        try:
            import ctranslate2

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    return device, compute_type


def run(args: argparse.Namespace) -> int:
    if not Path(args.audio).is_file():
        print(f"audio file not found: {args.audio}", file=sys.stderr)
        return 1
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print(
            "missing_dependency: faster-whisper is not installed (run through `uv run`)",
            file=sys.stderr,
        )
        return 2
    device, compute_type = pick_device(args.device, args.compute_type)
    try:
        model = WhisperModel(args.model, device=device, compute_type=compute_type)
        segments, info = model.transcribe(
            args.audio,
            language=base_lang(args.lang),
            initial_prompt=args.prompt or None,
            vad_filter=True,
        )
        out = [
            {"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip()}
            for s in segments
        ]  # generator: decoding happens here
    except Exception as exc:  # model download/load/decoding problems
        print(f"transcribe_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    emit(
        {
            "text": " ".join(s["text"] for s in out).strip(),
            "language": info.language,
            "segments": out,
        }
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="faster-whisper ASR adapter")
    ap.add_argument("--audio")
    ap.add_argument("--lang", default="auto")
    ap.add_argument("--prompt")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--compute-type", default="auto")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args(argv)
    if args.probe:
        return probe(args.model)
    if not args.audio:
        ap.error("--audio is required")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
