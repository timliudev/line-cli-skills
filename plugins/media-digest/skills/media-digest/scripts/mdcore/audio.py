"""Audio helpers: normalize to 16 kHz mono wav, and split/compress for HTTP upload limits."""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

from .probe import find_ffmpeg, run_tool

SAMPLE_RATE = 16000
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
_WAV_BYTES_PER_SECOND = SAMPLE_RATE * 2  # 16-bit mono


class AudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class Chunk:
    path: str
    offset: float  # seconds from the start of the source wav
    duration: float


def extract_wav(
    ffmpeg: str, src: str, dst: Path, has_audio_stream: bool = True, timeout: float = 3600
) -> Path:
    """Decode the first audio stream to 16 kHz mono pcm_s16le wav."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", src]
    if has_audio_stream:
        cmd += ["-map", "0:a:0"]
    cmd += ["-vn", "-sn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(dst)]
    res = run_tool(cmd, timeout=timeout)
    if res.returncode != 0 or not dst.is_file() or dst.stat().st_size <= 44:
        raise AudioError(f"ffmpeg could not extract audio: {res.stderr.strip()[-300:]}")
    return dst


def wav_duration(path: str | Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate())


def _encoders(ffmpeg: str) -> str:
    return run_tool([ffmpeg, "-hide_banner", "-encoders"], timeout=30).stdout.decode(
        "utf-8", "replace"
    )


def _pick_codec(ffmpeg: str) -> tuple[str, str, list[str]] | None:
    """(extension, codec, extra args) for a speech-friendly compressed format, or None."""
    listing = _encoders(ffmpeg)
    if "libopus" in listing:
        return ".ogg", "libopus", ["-b:a", "24k", "-application", "voip"]
    if "libmp3lame" in listing:
        return ".mp3", "libmp3lame", ["-b:a", "32k"]
    return None


def _encode(
    ffmpeg: str,
    wav: str,
    dest: Path,
    codec: str,
    extra: list[str],
    start: float,
    length: float | None,
) -> bool:
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", wav]
    if length is not None:
        cmd += ["-t", f"{length:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", codec, *extra, str(dest)]
    res = run_tool(cmd, timeout=3600)
    return res.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def _split_wav(path: str, out_dir: Path, seconds: float, total: float) -> list[Chunk]:
    """Pure-stdlib fallback: cut the wav into pcm chunks (no encoder needed)."""
    chunks: list[Chunk] = []
    with wave.open(path, "rb") as src:
        params = src.getparams()
        rate = src.getframerate()
        per_chunk = max(1, int(seconds * rate))
        index = 1
        while True:
            data = src.readframes(per_chunk)
            if not data:
                break
            dest = out_dir / f"chunk_{index:03d}.wav"
            with wave.open(str(dest), "wb") as dst:
                dst.setparams(params)
                dst.writeframes(data)
            offset = (index - 1) * per_chunk / rate
            length = min(seconds, total - offset)
            chunks.append(Chunk(str(dest), round(offset, 3), round(length, 3)))
            index += 1
    return chunks


def prepare_for_upload(
    wav: str | Path,
    max_bytes: int = DEFAULT_MAX_BYTES,
    out_dir: str | Path | None = None,
    ffmpeg: str | None = None,
) -> list[Chunk]:
    """Return audio files that each fit under ``max_bytes`` (HTTP ASR upload limits).

    - already small enough: the wav itself, one chunk at offset 0
    - otherwise compress to opus (ogg) or mp3 mono; if that fits, one chunk
    - still too big: split into time chunks, each re-encoded; ``offset`` is the chunk start
      in the source, so segment timestamps can be shifted back onto the original timeline
    - no usable encoder: split the wav into smaller wav files instead
    """
    wav = str(wav)
    total = wav_duration(wav)
    size = Path(wav).stat().st_size
    if size <= max_bytes:
        return [Chunk(wav, 0.0, round(total, 3))]

    out = Path(out_dir) if out_dir else Path(wav).parent / "upload"
    out.mkdir(parents=True, exist_ok=True)
    for stale in list(out.glob("chunk_*")) + list(out.glob("compressed.*")):
        stale.unlink(missing_ok=True)

    if ffmpeg is None:
        tools = find_ffmpeg()
        ffmpeg = tools.ffmpeg if tools else None
    codec = _pick_codec(ffmpeg) if ffmpeg else None
    if codec is None:
        seconds = max(1.0, (max_bytes * 0.95 - 44) / _WAV_BYTES_PER_SECOND)
        chunks = _split_wav(wav, out, seconds, total)
        if any(Path(c.path).stat().st_size > max_bytes for c in chunks):
            raise AudioError(f"max_bytes={max_bytes} is too small to hold even a short wav chunk")
        return chunks
    assert ffmpeg is not None
    ext, name, extra = codec

    whole = out / f"compressed{ext}"
    if _encode(ffmpeg, wav, whole, name, extra, 0.0, None) and whole.stat().st_size <= max_bytes:
        return [Chunk(str(whole), 0.0, round(total, 3))]
    compressed = whole.stat().st_size if whole.is_file() else size
    whole.unlink(missing_ok=True)

    seconds = max(1.0, total * (max_bytes * 0.85) / compressed)
    for _ in range(6):
        chunks = []
        ok = True
        offset = 0.0
        index = 1
        while offset < total - 0.01:
            length = min(seconds, total - offset)
            dest = out / f"chunk_{index:03d}{ext}"
            if not _encode(ffmpeg, wav, dest, name, extra, offset, length):
                raise AudioError(f"ffmpeg failed to encode chunk {index}")
            if dest.stat().st_size > max_bytes:
                ok = False
                break
            chunks.append(Chunk(str(dest), round(offset, 3), round(length, 3)))
            offset += length
            index += 1
        if ok:
            return chunks
        for stale in out.glob("chunk_*"):
            stale.unlink(missing_ok=True)
        seconds = max(1.0, seconds / 2)
    raise AudioError(f"could not fit audio into chunks of at most {max_bytes} bytes")
