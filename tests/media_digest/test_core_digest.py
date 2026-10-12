from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import digest
import pytest
from mdcore import probe

from .conftest import needs_ffmpeg

SCRIPT = Path(digest.__file__)


def run(*argv, capsys):
    code = digest.main([str(a) for a in argv])
    return code, json.loads(capsys.readouterr().out)


def test_passthrough_without_any_ffmpeg(tmp_path, pdf_file, monkeypatch, capsys):
    monkeypatch.setattr(probe, "find_ffmpeg", lambda: None)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypisom")
    out = tmp_path / "out"
    code, summary = run(pdf_file, video, "--out", out, capsys=capsys)
    assert code == 0
    assert summary["needs"] == "ffmpeg"
    assert summary["install_hint"] == probe.install_hint()
    assert set(summary["install_hints"]) == {"windows", "macos", "linux"}
    kinds = {Path(i["source"]).name: i for i in summary["items"]}
    assert kinds["doc.pdf"]["read"] == str(pdf_file.resolve())
    assert kinds["clip.mp4"]["status"] == "needs_ffmpeg"
    data = json.loads((out / "digest.json").read_text(encoding="utf-8"))
    assert data["needs"] == "ffmpeg"
    assert "Missing dependency" in (out / "digest.md").read_text(encoding="utf-8")


def test_only_video_without_ffmpeg_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(probe, "find_ffmpeg", lambda: None)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00" * 16)
    code, summary = run(video, "--out", tmp_path / "o", capsys=capsys)
    assert code == 1
    assert summary["ok"] is False


def test_missing_and_bad_args(tmp_path, capsys):
    code, summary = run(tmp_path / "nope.mp4", "--out", tmp_path / "o", capsys=capsys)
    assert code == 1
    assert summary["items"][0]["error"] == "file not found"
    with pytest.raises(SystemExit) as exc:
        digest.main(["--out", str(tmp_path)])
    assert exc.value.code == 2
    assert digest.main([str(tmp_path), "--out", str(tmp_path), "--max-frames", "0"]) == 2


@needs_ffmpeg
def test_video_end_to_end_with_mock_asr(scene_video, tmp_path, fake_asr, capsys):
    out = tmp_path / "out"
    code, summary = run(scene_video, "--out", out, "--lang", "en", capsys=capsys)
    assert code == 0
    item = summary["items"][0]
    assert item["type"] == "video"
    assert item["frames"] == 3
    assert item["transcript"] == "ok"
    assert item["adapter"] == "fake"
    audio_path, lang, _ = fake_asr.calls[0]
    assert lang == "en"
    assert not Path(audio_path).exists()  # normalized wav is removed unless --keep-audio

    data = json.loads((out / "digest.json").read_text(encoding="utf-8"))
    entry = data["items"][0]
    times = [e["t"] for e in entry["timeline"]]
    assert times == sorted(times)
    assert entry["timeline"][0]["type"] == "frame"
    for f in entry["frames"]:
        assert not Path(f["path"]).is_absolute()
        assert (out / f["path"]).is_file()
    md = (out / "digest.md").read_text(encoding="utf-8")
    assert "![frame at 0:00]" in md
    assert "> [0:00] hello world" in md  # 0.5 s truncates to 0:00
    assert "> [0:03] again" in md
    for f in entry["frames"]:
        assert f["path"] in md


@needs_ffmpeg
def test_cache_reuses_and_invalidates(scene_video, tmp_path, fake_asr, capsys):
    out = tmp_path / "out"
    src = tmp_path / "copy.mp4"
    src.write_bytes(scene_video.read_bytes())
    _, first = run(src, "--out", out, capsys=capsys)
    assert first["items"][0]["cached"] is False
    _, second = run(src, "--out", out, capsys=capsys)
    assert second["items"][0]["cached"] is True
    assert len(fake_asr.calls) == 1

    _, third = run(src, "--out", out, "--max-frames", "2", capsys=capsys)
    assert third["items"][0]["cached"] is False
    assert third["items"][0]["frames"] == 2

    _, forced = run(src, "--out", out, "--max-frames", "2", "--force", capsys=capsys)
    assert forced["items"][0]["cached"] is False

    src.write_bytes(scene_video.read_bytes() + b"\x00")  # size changed
    _, changed = run(src, "--out", out, "--max-frames", "2", capsys=capsys)
    assert changed["items"][0]["cached"] is False


@needs_ffmpeg
def test_failed_asr_is_retried_next_run(scene_video, tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    monkeypatch.setitem(sys.modules, "mdcore.asr", None)  # makes the import raise ImportError
    _, first = run(scene_video, "--out", out, capsys=capsys)
    assert first["items"][0]["transcript"] == "unavailable"
    assert first["items"][0]["frames"] == 3
    assert any("ASR" in n for n in first["items"][0]["notes"])
    _, second = run(scene_video, "--out", out, capsys=capsys)
    assert second["items"][0]["cached"] is False  # worth retrying
    _, third = run(scene_video, "--out", out, "--no-asr", capsys=capsys)
    assert third["items"][0]["cached"] is True


@needs_ffmpeg
def test_no_asr_flag_never_calls_transcribe(scene_video, tmp_path, fake_asr, capsys):
    _, summary = run(scene_video, "--out", tmp_path / "o", "--no-asr", capsys=capsys)
    assert summary["items"][0]["transcript"] == "skipped"
    assert fake_asr.calls == []


@needs_ffmpeg
def test_asr_failure_degrades_to_frames(scene_video, tmp_path, fake_asr, capsys):
    fake_asr.result.update(ok=False, adapter=None, error="no adapter", segments=[])
    code, summary = run(scene_video, "--out", tmp_path / "o", capsys=capsys)
    assert code == 0
    item = summary["items"][0]
    assert item["transcript"] == "unavailable"
    assert item["frames"] == 3
    assert "references/adapters.md" in " ".join(item["notes"])


@needs_ffmpeg
def test_video_without_audio_track(static_video, tmp_path, fake_asr, capsys):
    _, summary = run(static_video, "--out", tmp_path / "o", capsys=capsys)
    assert summary["items"][0]["transcript"] == "no_audio"
    assert fake_asr.calls == []


@needs_ffmpeg
def test_audio_input(tone_m4a, tmp_path, fake_asr, capsys):
    out = tmp_path / "out"
    code, summary = run(tone_m4a, "--out", out, capsys=capsys)
    assert code == 0
    item = summary["items"][0]
    assert item["type"] == "audio"
    assert item["frames"] == 0
    md = (out / "digest.md").read_text(encoding="utf-8")
    assert "> [0:00] hello world" in md
    assert "![" not in md


@needs_ffmpeg
def test_mixed_inputs_and_unsupported(scene_video, jpg_image, pdf_file, tmp_path, fake_asr, capsys):
    text = tmp_path / "notes.txt"
    text.write_text("hello", encoding="utf-8")
    code, summary = run(
        jpg_image, pdf_file, scene_video, text, "--out", tmp_path / "o", capsys=capsys
    )
    assert code == 0
    types = [i["type"] for i in summary["items"]]
    assert types == ["image", "pdf", "video", "other"]
    assert summary["items"][3]["error"] == "unsupported file type"
    assert summary["items"][0]["read"] == str(jpg_image.resolve())


def test_cli_subprocess_text_mode(tmp_path, pdf_file):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(pdf_file), "--out", str(tmp_path / "o"), "--text"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "digest.md" in proc.stdout
