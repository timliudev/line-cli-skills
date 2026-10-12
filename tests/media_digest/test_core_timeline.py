from __future__ import annotations

from mdcore import timeline


def test_format_ts():
    assert timeline.format_ts(0) == "0:00"
    assert timeline.format_ts(65.9) == "1:05"
    assert timeline.format_ts(3725) == "1:02:05"
    assert timeline.format_ts(None) == "?"


def test_build_timeline_orders_and_frames_first_on_ties():
    frames = [
        {"t": 0.0, "path": "a/frame_0001.jpg", "kind": "start"},
        {"t": 3.0, "path": "a/frame_0002.jpg", "kind": "scene"},
    ]
    segments = [
        {"start": 3.0, "end": 4.0, "text": " later "},
        {"start": 1.0, "end": 2.0, "text": "first"},
        {"start": 9.0, "end": 9.5, "text": "  "},
    ]
    entries = timeline.build_timeline(frames, segments)
    assert [(e["type"], e["t"]) for e in entries] == [
        ("frame", 0.0),
        ("speech", 1.0),
        ("frame", 3.0),
        ("speech", 3.0),
    ]
    assert entries[3]["text"] == "later"


def test_render_item_links_are_relative_and_quoted():
    item = {
        "source": "/x/a b.mp4",
        "name": "a b.mp4",
        "type": "video",
        "duration": 75,
        "width": 320,
        "height": 240,
        "notes": ["careful"],
        "transcript": {"status": "ok", "adapter": "fake", "language": "en"},
        "timeline": [
            {"t": 0.0, "type": "frame", "path": "a_b-1234\\frame_0001.jpg", "kind": "start"},
            {"t": 1.0, "end": 2.0, "type": "speech", "text": "hello\nworld"},
        ],
    }
    md = timeline.render_item(1, item)
    assert "## 1. a b.mp4 (video, 1:15, 320x240)" in md
    assert "![frame at 0:00](a_b-1234/frame_0001.jpg)" in md
    assert "> [0:01] hello world" in md
    assert "> Note: careful" in md
    assert "Transcript: fake, language en" in md


def test_render_digest_shows_missing_dependency_and_passthrough():
    items = [
        {
            "source": "/x/p.pdf",
            "name": "p.pdf",
            "type": "pdf",
            "passthrough": "/x/p.pdf",
            "notes": [],
        }
    ]
    md = timeline.render_digest(items, "ffmpeg", "brew install ffmpeg")
    assert "Missing dependency: **ffmpeg**" in md
    assert "brew install ffmpeg" in md
    assert "Read this file directly: `/x/p.pdf`" in md
