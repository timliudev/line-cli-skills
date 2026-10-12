from __future__ import annotations

import pytest
from mdcore import probe

from .conftest import needs_ffmpeg


def test_magic_pdf_and_images(tmp_path, pdf_file):
    assert probe.sniff_magic(pdf_file) == "pdf"
    png = tmp_path / "noext"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    assert probe.sniff_magic(png) == "image"
    webp = tmp_path / "w"
    webp.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    assert probe.sniff_magic(webp) == "image"
    heic = tmp_path / "h"
    heic.write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 20)
    assert probe.sniff_magic(heic) == "image"
    mp4 = tmp_path / "m"
    mp4.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 20)
    assert probe.sniff_magic(mp4) is None


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("a.MP4", "video"),
        ("a.m4a", "audio"),
        ("a.jpeg", "image"),
        ("a.pdf", "pdf"),
        ("a.txt", "other"),
        ("noext", "other"),
    ],
)
def test_extension_fallback(name, kind):
    assert probe.kind_from_extension(name) == kind


def test_magic_beats_extension(tmp_path):
    fake = tmp_path / "photo.mp4"  # a PNG that was mislabelled
    fake.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    assert probe.sniff_kind(fake) == "image"


def test_classify_from_streams():
    video = {
        "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    }
    assert probe.classify("x.bin", video) == "video"
    audio_only = {"streams": [{"codec_type": "audio"}], "format": {"format_name": "mp3"}}
    assert probe.classify("x.bin", audio_only) == "audio"
    cover = {
        "streams": [
            {"codec_type": "audio"},
            {"codec_type": "video", "disposition": {"attached_pic": 1}},
        ],
        "format": {"format_name": "mp3"},
    }
    assert probe.classify("x.mp3", cover) == "audio"
    picture = {"streams": [{"codec_type": "video"}], "format": {"format_name": "image2"}}
    assert probe.classify("x.bin", picture) == "image"
    assert probe.classify("x.mp4", None) == "video"


def test_summarize_prefers_format_duration():
    info = {
        "format": {"duration": "12.5", "start_time": "1.4", "format_name": "mpegts"},
        "streams": [
            {"codec_type": "video", "width": 10, "height": 20, "codec_name": "h264"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }
    facts = probe.summarize(info)
    assert facts["duration"] == 12.5
    assert facts["start_time"] == 1.4
    assert facts["width"] == 10
    assert facts["has_audio"] and facts["has_video"]


def test_install_hints_cover_all_platforms():
    assert probe.INSTALL_HINTS["windows"] == "winget install --id Gyan.FFmpeg -e"
    assert probe.INSTALL_HINTS["macos"] == "brew install ffmpeg"
    assert probe.INSTALL_HINTS["linux"] == "sudo apt install ffmpeg"
    assert probe.install_hint() in probe.INSTALL_HINTS.values()


def test_find_tool_uses_override_dir(tmp_path, monkeypatch):
    exe = tmp_path / ("ffmpeg.exe" if probe.current_os() == "windows" else "ffmpeg")
    exe.write_bytes(b"")
    monkeypatch.setenv("MEDIA_DIGEST_FFMPEG_DIR", str(tmp_path))
    monkeypatch.setenv("PATH", "")
    assert probe.find_tool("ffmpeg") == str(exe)


@needs_ffmpeg
def test_ffprobe_on_real_media(scene_video, jpg_image, tone_m4a, tools, tmp_path):
    info = probe.run_ffprobe(scene_video, tools.ffprobe)
    assert info is not None
    assert probe.classify(scene_video, info) == "video"
    facts = probe.summarize(info)
    assert 5.5 < facts["duration"] < 6.5
    assert (facts["width"], facts["height"]) == (320, 240)
    assert facts["has_audio"]

    assert probe.classify(jpg_image, probe.run_ffprobe(jpg_image, tools.ffprobe)) == "image"
    assert probe.classify(tone_m4a, probe.run_ffprobe(tone_m4a, tools.ffprobe)) == "audio"

    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not media at all")
    assert probe.run_ffprobe(junk, tools.ffprobe) is None
