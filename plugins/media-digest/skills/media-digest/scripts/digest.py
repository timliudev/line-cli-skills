#!/usr/bin/env python3
"""media-digest: turn images, PDFs, videos and audio into something Claude can read.

    digest.py INPUT... --out DIR [--max-frames 24] [--scene-threshold 0.3] [--lang auto]
              [--no-asr] [--json | --text] [--force] [--keep-audio]

Writes DIR/digest.json and DIR/digest.md (timeline of keyframes + transcript) and prints a
summary JSON on stdout. Exit codes: 0 = something usable was produced, 1 = nothing usable
(missing files, unsupported types, ffmpeg needed but absent), 2 = bad arguments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from mdcore import audio, frames, probe, timeline

SCHEMA_VERSION = 1
ASR_HINT = (
    "No ASR adapter is available. Configure one (faster_whisper, whisper_cpp or an "
    "openai_compat endpoint) as described in references/adapters.md, or use --no-asr."
)


# --------------------------------------------------------------------------- helpers


def item_id(src: Path) -> str:
    stem = re.sub(r"[^\w.-]+", "_", src.stem, flags=re.UNICODE).strip("._") or "file"
    digest = hashlib.sha1(str(src).encode("utf-8", "replace")).hexdigest()[:8]
    return f"{stem[:40]}-{digest}"


def rel(path: str | Path, base: Path) -> str:
    return Path(os.path.relpath(path, base)).as_posix()


def write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def stat_key(src: Path) -> dict:
    st = src.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}


def load_previous(out_dir: Path) -> dict[str, dict]:
    path = out_dir / "digest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        return {}
    return {i["source"]: i for i in data.get("items", []) if isinstance(i, dict) and "source" in i}


def cache_usable(prev: dict | None, src: Path, params: dict, out_dir: Path, no_asr: bool) -> bool:
    """Reuse a previous item if the file and options are unchanged and nothing is left to retry."""
    if not prev or not prev.get("complete"):
        return False
    if prev.get("cache") != {**stat_key(src), "params": params}:
        return False
    if prev.get("type") in ("video", "audio") and not no_asr:
        has_audio = prev.get("has_audio")
        if has_audio and (prev.get("transcript") or {}).get("status") != "ok":
            return False  # retry ASR: an adapter may have been installed since
    for f in prev.get("frames", []):
        if not (out_dir / f["path"]).is_file():
            return False
    return True


# --------------------------------------------------------------------------- ASR


def run_asr(wav: Path, lang: str) -> dict:
    """Call mdcore.asr.transcribe lazily; any problem degrades to a status dict."""
    try:
        from mdcore.asr import transcribe  # type: ignore[import-not-found]
    except Exception as exc:  # ImportError, or a broken module from a parallel build
        return {
            "status": "unavailable",
            "error": f"ASR module not available: {exc}",
            "hint": ASR_HINT,
        }
    try:
        res = transcribe(str(wav), lang=lang)
    except Exception as exc:
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(res, dict):
        return {"status": "failed", "error": "transcribe() returned an unexpected value"}
    if res.get("ok"):
        return {
            "status": "ok",
            "adapter": res.get("adapter"),
            "language": res.get("language"),
            "text": res.get("text", ""),
            "segments": [
                {
                    "start": float(s.get("start", 0.0)),
                    "end": s.get("end"),
                    "text": s.get("text", ""),
                }
                for s in res.get("segments") or []
            ],
        }
    out = {
        "status": "unavailable" if not res.get("adapter") else "failed",
        "error": res.get("error") or "transcription failed",
        "attempts": res.get("attempts", []),
    }
    if out["status"] == "unavailable":
        out["hint"] = ASR_HINT
    return out


# --------------------------------------------------------------------------- per-input


def base_item(src: Path, kind: str) -> dict:
    item = {"source": str(src), "name": src.name, "type": kind, "notes": []}
    try:
        item.update(stat_key(src))
    except OSError:
        pass
    return item


def process_input(src: Path, out_dir: Path, tools: probe.Tools | None, args: argparse.Namespace,
                  params: dict) -> dict:
    if not src.exists():
        item = base_item(src, "missing")
        item["error"] = "file not found"
        return item
    if src.is_dir():
        item = base_item(src, "other")
        item["error"] = "is a directory"
        return item

    kind = probe.sniff_kind(src)
    item = base_item(src, kind)
    item["id"] = item_id(src)

    if kind in ("image", "pdf"):
        item["passthrough"] = str(src)
        item["complete"] = True
        return item

    if tools is None:
        item["status"] = "needs_ffmpeg"
        what = kind if kind in ("video", "audio") else "this file (type unknown)"
        item["notes"].append(
            f"ffmpeg/ffprobe not found; cannot process {what}. Install: {probe.install_hint()}"
        )
        return item

    info = probe.run_ffprobe(src, tools.ffprobe)
    if info is None:
        item["type"] = "other"
        item["error"] = (
            "unsupported file type" if kind == "other"
            else "ffprobe could not read this file (corrupt or unsupported)"
        )
        return item
    kind = probe.classify(src, info)
    item["type"] = kind
    if kind in ("image", "pdf"):
        item["passthrough"] = str(src)
        item["complete"] = True
        return item
    if kind not in ("video", "audio"):
        item["error"] = "unsupported file type"
        return item

    facts = probe.summarize(info)
    item.update({k: v for k, v in facts.items() if k not in ("start_time", "format")})
    item["format"] = facts.get("format")
    work = out_dir / item["id"]
    work.mkdir(parents=True, exist_ok=True)

    item["frames"] = []
    if kind == "video":
        sampled = frames.sample_video(
            tools.ffmpeg, str(src), work, facts.get("duration"), args.max_frames,
            args.scene_threshold, facts.get("start_time", 0.0),
        )
        item["frames"] = [
            {"t": f["t"], "kind": f["kind"], "path": rel(f["path"], out_dir)}
            for f in sampled["frames"]
        ]
        item["frame_method"] = sampled["method"]
        item["notes"] += sampled["notes"]

    item["transcript"] = transcribe_media(src, work, tools, facts, args)
    tr = item["transcript"]
    if tr["status"] in ("unavailable", "failed") and tr.get("hint"):
        item["notes"].append(tr["hint"])
    segments = tr.get("segments", []) if tr.get("status") == "ok" else []
    item["timeline"] = timeline.build_timeline(item["frames"], segments)
    item["complete"] = not (kind == "video" and not item["frames"])
    return item


def transcribe_media(src: Path, work: Path, tools: probe.Tools, facts: dict,
                     args: argparse.Namespace) -> dict:
    if not facts.get("has_audio"):
        return {"status": "no_audio"}
    if args.no_asr:
        return {"status": "skipped"}
    wav = work / "audio.wav"
    try:
        audio.extract_wav(tools.ffmpeg, str(src), wav)
    except audio.AudioError as exc:
        return {"status": "failed", "error": str(exc)}
    try:
        return run_asr(wav, args.lang)
    finally:
        if not args.keep_audio:
            wav.unlink(missing_ok=True)


# --------------------------------------------------------------------------- main


def summarize_item(item: dict, cached: bool) -> dict:
    tr = item.get("transcript") or {}
    out: dict = {
        "source": item["source"],
        "type": item["type"],
        "cached": cached,
    }
    if item.get("passthrough"):
        out["read"] = item["passthrough"]
    if item["type"] in ("video", "audio"):
        out["duration"] = item.get("duration")
        out["frames"] = len(item.get("frames", []))
        out["transcript"] = tr.get("status")
        if tr.get("adapter"):
            out["adapter"] = tr["adapter"]
        if tr.get("language"):
            out["language"] = tr["language"]
        if tr.get("error"):
            out["transcript_error"] = tr["error"]
    if item.get("status"):
        out["status"] = item["status"]
    if item.get("error"):
        out["error"] = item["error"]
    if item.get("notes"):
        out["notes"] = item["notes"]
    return out


def usable(item: dict) -> bool:
    if item.get("passthrough"):
        return True
    return item["type"] in ("video", "audio") and not item.get("status") and not item.get("error")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="digest.py", description="Digest images, PDFs, videos and audio for Claude."
    )
    p.add_argument("inputs", nargs="+", metavar="INPUT", help="media files")
    p.add_argument("--out", required=True, metavar="DIR", help="output directory")
    p.add_argument("--max-frames", type=int, default=24, help="max keyframes per video (24)")
    p.add_argument("--scene-threshold", type=float, default=0.3,
                   help="scene-change score 0-1, lower = more frames (0.3)")
    p.add_argument("--lang", default="auto", help="speech language code or 'auto'")
    p.add_argument("--no-asr", action="store_true", help="skip transcription")
    p.add_argument("--json", action="store_true", help="print the summary as JSON (default)")
    p.add_argument("--text", action="store_true", help="print a short human-readable summary")
    p.add_argument("--force", action="store_true", help="ignore the cache and redo everything")
    p.add_argument("--keep-audio", action="store_true", help="keep the normalized 16 kHz wav")
    return p


def main(argv: list[str] | None = None) -> int:
    probe.stdout_utf8()
    args = build_parser().parse_args(argv)
    if args.max_frames < 1:
        print("--max-frames must be >= 1", file=sys.stderr)
        return 2
    if not 0 < args.scene_threshold < 1:
        print("--scene-threshold must be between 0 and 1", file=sys.stderr)
        return 2

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    tools = probe.find_ffmpeg()
    params = {"max_frames": args.max_frames, "scene_threshold": args.scene_threshold,
              "lang": args.lang}
    previous = {} if args.force else load_previous(out_dir)

    items: list[dict] = []
    summaries: list[dict] = []
    for raw in args.inputs:
        src = Path(raw).expanduser().resolve()
        prev = previous.get(str(src))
        if src.is_file() and cache_usable(prev, src, params, out_dir, args.no_asr):
            item, cached = prev, True
        else:
            item, cached = process_input(src, out_dir, tools, args, params), False
            if src.is_file():
                item["cache"] = {**stat_key(src), "params": params}
        items.append(item)
        summaries.append(summarize_item(item, cached))

    needs = "ffmpeg" if any(i.get("status") == "needs_ffmpeg" for i in items) else None
    hint = probe.install_hint() if needs else None
    digest = {
        "version": SCHEMA_VERSION,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "out_dir": str(out_dir),
        "params": {**params, "no_asr": args.no_asr},
        "needs": needs,
        "items": items,
    }
    if needs:
        digest["install_hint"] = hint
        digest["install_hints"] = probe.INSTALL_HINTS
    write_json_atomic(out_dir / "digest.json", digest)
    (out_dir / "digest.md").write_text(timeline.render_digest(items, needs, hint), encoding="utf-8")

    ok = any(usable(i) for i in items)
    summary: dict = {
        "ok": ok,
        "out": str(out_dir),
        "digest_md": str(out_dir / "digest.md"),
        "digest_json": str(out_dir / "digest.json"),
        "items": summaries,
    }
    if needs:
        summary["needs"] = needs
        summary["install_hint"] = hint
        summary["install_hints"] = probe.INSTALL_HINTS
    if args.text and not args.json:
        print_text(summary)
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def print_text(summary: dict) -> None:
    for s in summary["items"]:
        line = f"{s['type']:>7}  {s['source']}"
        if "frames" in s:
            line += f"  frames={s['frames']} transcript={s.get('transcript')}"
        if s.get("error") or s.get("status"):
            line += f"  [{s.get('error') or s.get('status')}]"
        print(line)
    if summary.get("needs"):
        print(f"Missing {summary['needs']}: {summary['install_hint']}")
    print(f"Read: {summary['digest_md']}")


if __name__ == "__main__":
    sys.exit(main())
