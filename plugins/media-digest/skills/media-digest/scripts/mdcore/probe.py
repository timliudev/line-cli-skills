"""Locate ffmpeg/ffprobe, probe media files and classify them (stdlib only)."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

INSTALL_HINTS = {
    "windows": "winget install --id Gyan.FFmpeg -e",
    "macos": "brew install ffmpeg",
    "linux": "sudo apt install ffmpeg",
}

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif",
}
VIDEO_EXTS = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".3gp", ".flv", ".wmv", ".mpg", ".mpeg", ".ts",
}
AUDIO_EXTS = {
    ".mp3", ".m4a", ".aac", ".wav", ".ogg", ".oga", ".opus", ".flac", ".amr", ".wma", ".aiff",
    ".caf", ".mka",
}
# ffprobe format_name values that mean "a single picture", not a video.
_IMAGE_FORMATS = {
    "image2", "png_pipe", "jpeg_pipe", "webp_pipe", "bmp_pipe", "tiff_pipe", "gif", "ico",
    "heif", "avif", "mpjpeg", "jpegls_pipe", "svg_pipe", "pgm_pipe", "ppm_pipe",
}
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif"}


class Tools(NamedTuple):
    ffmpeg: str
    ffprobe: str


def current_os() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    return "linux"


def install_hint() -> str:
    """Install command for ffmpeg on the current OS."""
    return INSTALL_HINTS[current_os()]


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    override = os.environ.get("MEDIA_DIGEST_FFMPEG_DIR")
    if override:
        dirs.append(Path(override))
    osname = current_os()
    if osname == "windows":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            winget = Path(local) / "Microsoft" / "WinGet"
            dirs.append(winget / "Links")
            pkgs = winget / "Packages"
            for pattern in ("Gyan.FFmpeg*/ffmpeg-*/bin", "BtbN.FFmpeg*/ffmpeg-*/bin"):
                dirs.extend(sorted(pkgs.glob(pattern), reverse=True))
        for var in ("ProgramFiles", "ProgramData"):
            base = os.environ.get(var)
            if base:
                dirs.append(Path(base) / "ffmpeg" / "bin")
        dirs.append(Path("C:/ffmpeg/bin"))
        choco = os.environ.get("ChocolateyInstall")
        if choco:
            dirs.append(Path(choco) / "bin")
        dirs.append(Path.home() / "scoop" / "shims")
    elif osname == "macos":
        dirs += [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    else:
        dirs += [Path("/usr/local/bin"), Path("/usr/bin"), Path("/snap/bin")]
    return dirs


def find_tool(name: str) -> str | None:
    """PATH first, then well-known install directories."""
    found = shutil.which(name)
    if found:
        return found
    exe = name + (".exe" if current_os() == "windows" else "")
    for directory in _candidate_dirs():
        candidate = directory / exe
        if candidate.is_file():
            return str(candidate)
    return None


def find_ffmpeg() -> Tools | None:
    """Return both binaries or None (ffmpeg and ffprobe ship together)."""
    ffmpeg = find_tool("ffmpeg")
    ffprobe = find_tool("ffprobe")
    if ffmpeg and ffprobe:
        return Tools(ffmpeg, ffprobe)
    return None


class ToolResult(NamedTuple):
    returncode: int
    stdout: bytes
    stderr: str


def run_tool(cmd: list[str], timeout: float | None = None) -> ToolResult:
    """Run a subprocess without a shell; never raises on non-zero exit."""
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(124, b"", f"timeout after {timeout}s")
    except OSError as exc:
        return ToolResult(127, b"", str(exc))
    return ToolResult(proc.returncode, proc.stdout, proc.stderr.decode("utf-8", "replace"))


def run_ffprobe(path: str | os.PathLike[str], ffprobe: str, timeout: float = 60) -> dict | None:
    """ffprobe JSON (format + streams) or None when the file is not readable media."""
    cmd = [
        ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
        os.fspath(path),
    ]
    res = run_tool(cmd, timeout=timeout)
    if res.returncode != 0:
        return None
    try:
        data = json.loads(res.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) and data.get("streams") else None


def sniff_magic(path: str | os.PathLike[str]) -> str | None:
    """'pdf' or 'image' judged from magic bytes, else None."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(1024)
    except OSError:
        return None
    if b"%PDF-" in head:
        return "pdf"
    if head.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"BM")):
        return "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image"
    if head[4:8] == b"ftyp" and head[8:12] in _HEIF_BRANDS:
        return "image"
    return None


def kind_from_extension(path: str | os.PathLike[str]) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return "other"


def sniff_kind(path: str | os.PathLike[str]) -> str:
    """Cheap classification without ffprobe: magic bytes, then extension."""
    return sniff_magic(path) or kind_from_extension(path)


def _streams(info: dict, codec_type: str) -> list[dict]:
    return [s for s in info.get("streams", []) if s.get("codec_type") == codec_type]


def _is_cover_art(stream: dict) -> bool:
    return bool((stream.get("disposition") or {}).get("attached_pic"))


def classify(path: str | os.PathLike[str], info: dict | None) -> str:
    """image / pdf / video / audio / other, by magic bytes, ffprobe streams, then extension."""
    magic = sniff_magic(path)
    if magic:
        return magic
    if info is None:
        return kind_from_extension(path)
    videos = [s for s in _streams(info, "video") if not _is_cover_art(s)]
    audios = _streams(info, "audio")
    fmt_names = set(str((info.get("format") or {}).get("format_name", "")).split(","))
    if videos and not audios and fmt_names & _IMAGE_FORMATS:
        return "image"
    if videos:
        return "video"
    if audios:
        return "audio"
    if any(_is_cover_art(s) for s in _streams(info, "video")):
        return "image"
    return "other"


def _float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number == number and number >= 0 else None


def summarize(info: dict) -> dict:
    """Flat facts used by the digest: duration, size, codecs, start_time, ..."""
    fmt = info.get("format") or {}
    videos = [s for s in _streams(info, "video") if not _is_cover_art(s)]
    audios = _streams(info, "audio")
    duration = _float(fmt.get("duration"))
    if duration is None:
        candidates = [_float(s.get("duration")) for s in videos + audios]
        known = [c for c in candidates if c is not None]
        duration = max(known) if known else None
    out: dict = {
        "duration": duration,
        "start_time": _float(fmt.get("start_time")) or 0.0,
        "format": fmt.get("format_name"),
        "has_video": bool(videos),
        "has_audio": bool(audios),
    }
    if videos:
        v = videos[0]
        out.update(width=v.get("width"), height=v.get("height"), video_codec=v.get("codec_name"))
    if audios:
        out["audio_codec"] = audios[0].get("codec_name")
    return out


def stdout_utf8() -> None:
    """Avoid UnicodeEncodeError on legacy Windows code pages."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
