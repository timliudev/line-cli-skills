"""Shared fixtures: synthetic media generated with ffmpeg lavfi (skipped when ffmpeg is absent)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from mdcore import probe

TOOLS = probe.find_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(TOOLS is None, reason="ffmpeg/ffprobe not installed")


def run_ffmpeg(*args: str) -> None:
    assert TOOLS is not None
    cmd = [TOOLS.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]
    subprocess.run(cmd, check=True, capture_output=True)


@pytest.fixture(scope="session")
def tools():
    if TOOLS is None:
        pytest.skip("ffmpeg/ffprobe not installed")
    return TOOLS


@pytest.fixture(scope="session")
def scene_video(tmp_path_factory, tools) -> Path:
    """6 s, 320x240: red -> blue -> noisy test pattern (two hard cuts at 2 s and 4 s) + sine."""
    path = tmp_path_factory.mktemp("media") / "scenes.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", "color=c=red:s=320x240:d=2:r=10",
        "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=2:r=10",
        "-f", "lavfi", "-i", "testsrc2=s=320x240:d=2:r=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map", "[v]", "-map", "3:a", "-c:v", "mpeg4", "-q:v", "3", "-c:a", "aac", str(path),
    )
    return path


@pytest.fixture(scope="session")
def static_video(tmp_path_factory, tools) -> Path:
    """10 s of a flat colour: no scene cuts, so uniform sampling must kick in. No audio."""
    path = tmp_path_factory.mktemp("media") / "static.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", "color=c=gray:s=640x360:d=10:r=5",
        "-c:v", "mpeg4", "-q:v", "3", str(path),
    )
    return path


@pytest.fixture(scope="session")
def wide_video(tmp_path_factory, tools) -> Path:
    """1920x1080 so the 1280 px cap is exercised."""
    path = tmp_path_factory.mktemp("media") / "wide.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=1920x1080:d=2:r=5",
        "-c:v", "mpeg4", "-q:v", "5", str(path),
    )
    return path


@pytest.fixture(scope="session")
def sine_wav(tmp_path_factory, tools) -> Path:
    """10 s 44.1 kHz stereo sine, to be normalized."""
    path = tmp_path_factory.mktemp("media") / "tone.wav"
    run_ffmpeg("-f", "lavfi", "-i", "sine=frequency=330:duration=10",
               "-ac", "2", "-ar", "44100", str(path))
    return path


@pytest.fixture(scope="session")
def tone_m4a(tmp_path_factory, tools) -> Path:
    path = tmp_path_factory.mktemp("media") / "voice.m4a"
    run_ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=4", "-c:a", "aac", str(path))
    return path


@pytest.fixture(scope="session")
def jpg_image(tmp_path_factory, tools) -> Path:
    path = tmp_path_factory.mktemp("media") / "pic.jpg"
    run_ffmpeg("-f", "lavfi", "-i", "testsrc=s=64x64:d=1", "-frames:v", "1", str(path))
    return path


@pytest.fixture
def pdf_file(tmp_path) -> Path:
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n")
    return path


@pytest.fixture
def fake_asr(monkeypatch):
    """Install a stand-in mdcore.asr module whose transcribe() records calls."""
    import sys
    import types

    calls: list[tuple] = []
    result = {
        "ok": True, "adapter": "fake", "text": "hello world again", "language": "en",
        "segments": [
            {"start": 0.5, "end": 1.5, "text": "hello world"},
            {"start": 3.0, "end": 4.0, "text": "again"},
        ],
        "attempts": [], "error": None,
    }

    def transcribe(audio_path, lang="auto", prompt=None):
        calls.append((audio_path, lang, prompt))
        return dict(result)

    module = types.ModuleType("mdcore.asr")
    module.transcribe = transcribe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mdcore.asr", module)
    import mdcore

    monkeypatch.setattr(mdcore, "asr", module, raising=False)
    return types.SimpleNamespace(calls=calls, result=result)
