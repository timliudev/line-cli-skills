from __future__ import annotations

from pathlib import Path

from mdcore import frames, probe

from .conftest import needs_ffmpeg

SHOWINFO = """\
[Parsed_showinfo_2 @ 000001] n:   0 pts:  20000 pts_time:2       duration: 1 fmt:yuv420p
[Parsed_showinfo_2 @ 000001] n:   1 pts:  40000 pts_time:4.04    duration: 1 fmt:yuv420p
frame=  2 fps=0.0 pts_time:99 not-a-filter-line
"""


def test_parse_showinfo():
    assert frames.parse_showinfo(SHOWINFO) == [2.0, 4.04]


def test_uniform_times():
    assert frames.uniform_times(10, 4) == [1.25, 3.75, 6.25, 8.75]
    assert frames.uniform_times(0, 4) == []


def test_choose_scene_only_when_enough_cuts():
    points, method = frames.choose_times([2.0, 4.0, 6.0], 8.0, max_frames=24)
    assert method == "scene"
    assert [t for t, _ in points] == [0.0, 2.0, 4.0, 6.0]
    assert points[0][1] == "start"


def test_choose_falls_back_to_uniform():
    points, method = frames.choose_times([], 10.0, max_frames=24)
    assert method == "uniform"
    assert len(points) == 9  # start + 8 evenly spaced
    mixed, method = frames.choose_times([5.0], 10.0, max_frames=24)
    assert method == "scene+uniform"
    assert any(kind == "scene" for _, kind in mixed)


def test_choose_caps_and_keeps_first_and_last():
    cuts = [float(i) for i in range(1, 101)]
    points, _ = frames.choose_times(cuts, 200.0, max_frames=10)
    assert len(points) == 10
    assert points[0][0] == 0.0
    assert points[-1][0] == 100.0


def test_choose_drops_cuts_closer_than_gap():
    points, _ = frames.choose_times([0.1, 3.0, 3.2, 6.0], 9.0, max_frames=24)
    assert [t for t, _ in points] == [0.0, 3.0, 6.0]


@needs_ffmpeg
def test_detect_scene_times_finds_both_cuts(scene_video, tools):
    times = frames.detect_scene_times(tools.ffmpeg, str(scene_video), 0.3)
    assert times is not None
    assert len(times) == 2
    assert abs(times[0] - 2.0) < 0.3
    assert abs(times[1] - 4.0) < 0.3


@needs_ffmpeg
def test_sample_video_scene_frames(scene_video, tools, tmp_path):
    result = frames.sample_video(tools.ffmpeg, str(scene_video), tmp_path, 6.0, max_frames=24)
    assert result["method"] == "scene"
    assert [round(f["t"]) for f in result["frames"]] == [0, 2, 4]
    for f in result["frames"]:
        assert Path(f["path"]).stat().st_size > 0


@needs_ffmpeg
def test_sample_video_respects_max_frames(scene_video, tools, tmp_path):
    result = frames.sample_video(tools.ffmpeg, str(scene_video), tmp_path, 6.0, max_frames=2)
    assert len(result["frames"]) == 2
    assert len(list(tmp_path.glob("frame_*.jpg"))) == 2


@needs_ffmpeg
def test_sample_video_uniform_fallback(static_video, tools, tmp_path):
    result = frames.sample_video(tools.ffmpeg, str(static_video), tmp_path, 10.0, max_frames=24)
    assert result["method"] == "uniform"
    assert len(result["frames"]) >= 6
    ts = [f["t"] for f in result["frames"]]
    assert ts == sorted(ts)


@needs_ffmpeg
def test_frames_capped_at_1280_wide(wide_video, tools, tmp_path):
    result = frames.sample_video(tools.ffmpeg, str(wide_video), tmp_path, 2.0, max_frames=2)
    first = result["frames"][0]["path"]
    info = probe.run_ffprobe(first, tools.ffprobe)
    assert info is not None
    stream = info["streams"][0]
    assert stream["width"] == 1280
    assert stream["height"] == 720


@needs_ffmpeg
def test_small_video_not_upscaled(scene_video, tools, tmp_path):
    result = frames.sample_video(tools.ffmpeg, str(scene_video), tmp_path, 6.0, max_frames=1)
    stream = probe.run_ffprobe(result["frames"][0]["path"], tools.ffprobe)["streams"][0]
    assert stream["width"] == 320
