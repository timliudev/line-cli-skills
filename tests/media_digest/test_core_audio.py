from __future__ import annotations

import wave
from pathlib import Path

import pytest
from mdcore import audio

from .conftest import needs_ffmpeg


@needs_ffmpeg
def test_extract_wav_normalizes(sine_wav, tools, tmp_path):
    out = audio.extract_wav(tools.ffmpeg, str(sine_wav), tmp_path / "a.wav")
    with wave.open(str(out), "rb") as wf:
        assert (wf.getframerate(), wf.getnchannels(), wf.getsampwidth()) == (16000, 1, 2)
        assert wf.getnframes() / 16000 == pytest.approx(10, abs=0.2)


@needs_ffmpeg
def test_extract_wav_from_video_and_m4a(scene_video, tone_m4a, tools, tmp_path):
    a = audio.extract_wav(tools.ffmpeg, str(scene_video), tmp_path / "v.wav")
    assert audio.wav_duration(a) == pytest.approx(6, abs=0.3)
    b = audio.extract_wav(tools.ffmpeg, str(tone_m4a), tmp_path / "m.wav")
    assert audio.wav_duration(b) == pytest.approx(4, abs=0.3)


@needs_ffmpeg
def test_extract_wav_fails_without_audio_stream(static_video, tools, tmp_path):
    with pytest.raises(audio.AudioError):
        audio.extract_wav(tools.ffmpeg, str(static_video), tmp_path / "x.wav")


@needs_ffmpeg
def test_prepare_small_file_is_passthrough(sine_wav, tools, tmp_path):
    wav = audio.extract_wav(tools.ffmpeg, str(sine_wav), tmp_path / "a.wav")
    chunks = audio.prepare_for_upload(wav, max_bytes=audio.DEFAULT_MAX_BYTES)
    assert len(chunks) == 1
    assert chunks[0].path == str(wav)
    assert chunks[0].offset == 0


@needs_ffmpeg
def test_prepare_compresses_when_that_is_enough(sine_wav, tools, tmp_path):
    wav = audio.extract_wav(tools.ffmpeg, str(sine_wav), tmp_path / "a.wav")  # ~320 KB
    chunks = audio.prepare_for_upload(wav, max_bytes=150_000, out_dir=tmp_path / "up")
    assert len(chunks) == 1
    assert Path(chunks[0].path).suffix in (".ogg", ".mp3")
    assert Path(chunks[0].path).stat().st_size <= 150_000


@needs_ffmpeg
def test_prepare_splits_with_offsets(sine_wav, tools, tmp_path):
    wav = audio.extract_wav(tools.ffmpeg, str(sine_wav), tmp_path / "a.wav")
    limit = 12_000  # 24 kbps opus for 10 s is ~30 KB, so it has to be split
    chunks = audio.prepare_for_upload(wav, max_bytes=limit, out_dir=tmp_path / "up")
    assert len(chunks) >= 2
    assert chunks[0].offset == 0
    offsets = [c.offset for c in chunks]
    assert offsets == sorted(offsets)
    for c in chunks:
        assert Path(c.path).stat().st_size <= limit
    assert chunks[-1].offset + chunks[-1].duration == pytest.approx(10, abs=0.1)
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        assert prev.offset + prev.duration == pytest.approx(nxt.offset, abs=0.01)


def test_prepare_wav_split_without_encoder(tmp_path, monkeypatch):
    """No ffmpeg at all: stdlib wav splitting still honours the byte limit."""
    wav = tmp_path / "s.wav"
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 16000 * 5)  # 5 s, 160 KB
    monkeypatch.setattr(audio, "find_ffmpeg", lambda: None)
    chunks = audio.prepare_for_upload(wav, max_bytes=70_000, out_dir=tmp_path / "up")
    assert len(chunks) == 3
    assert [c.offset for c in chunks] == pytest.approx([0.0, 2.08, 4.15], abs=0.02)
    assert all(Path(c.path).stat().st_size <= 70_000 for c in chunks)
