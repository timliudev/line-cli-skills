"""Keyframe selection for videos: scene-change detection with uniform-sampling fallback.

Filter syntax verified against https://ffmpeg.org/ffmpeg-filters.html (2026-10-12, ffmpeg 9.0.2):
``select='gt(scene,T)'`` with the commas escaped, ``showinfo`` logs ``pts_time:`` per frame,
and ``-fps_mode passthrough`` replaces the deprecated ``-vsync``.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .probe import run_tool

MIN_SCENE_GAP = 0.5  # seconds; cuts closer than this are the same scene
DEFAULT_MIN_FRAMES = 3
UNIFORM_COUNT = 8
_PTS_TIME = re.compile(r"pts_time:\s*(-?\d+(?:\.\d+)?)")


def parse_showinfo(stderr: str) -> list[float]:
    """pts_time values (seconds) from ffmpeg's showinfo log lines."""
    times: list[float] = []
    for line in stderr.splitlines():
        if "showinfo" not in line:
            continue
        match = _PTS_TIME.search(line)
        if match:
            times.append(float(match.group(1)))
    return times


def detect_scene_times(
    ffmpeg: str,
    video: str,
    threshold: float = 0.3,
    start_time: float = 0.0,
    timeout: float = 1800,
) -> list[float] | None:
    """Timestamps (relative to the file start) of scene changes, or None if detection failed.

    The video is downscaled first so the scene score is cheap to compute on long files.
    """
    vf = f"scale=320:-2,select=gt(scene\\,{threshold}),showinfo"
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "info", "-i", video,
        "-map", "0:v:0", "-an", "-sn", "-vf", vf, "-fps_mode", "passthrough", "-f", "null", "-",
    ]
    res = run_tool(cmd, timeout=timeout)
    if res.returncode != 0:
        return None
    return sorted(max(0.0, t - start_time) for t in parse_showinfo(res.stderr))


def uniform_times(duration: float, count: int) -> list[float]:
    """Centres of ``count`` equal slices of the duration."""
    if count <= 0 or duration <= 0:
        return []
    step = duration / count
    return [round(step * (i + 0.5), 3) for i in range(count)]


def _dedupe(points: list[tuple[float, str]], gap: float) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    for t, kind in sorted(points):
        if out and t - out[-1][0] < gap:
            continue
        out.append((t, kind))
    return out


def _spread(items: list, limit: int) -> list:
    """Pick ``limit`` items evenly across the list, keeping first and last."""
    if len(items) <= limit:
        return items
    if limit <= 1:
        return items[:1]
    idx = sorted({round(i * (len(items) - 1) / (limit - 1)) for i in range(limit)})
    return [items[i] for i in idx]


def choose_times(
    scene_times: list[float],
    duration: float | None,
    max_frames: int,
    min_frames: int = DEFAULT_MIN_FRAMES,
) -> tuple[list[tuple[float, str]], str]:
    """Decide which (timestamp, kind) frames to extract and name the method used.

    Always includes the first frame. If the start frame plus scene cuts total fewer than
    ``min_frames``, evenly spaced frames are added (method ``scene+uniform`` or ``uniform``).
    More than ``max_frames`` candidates are thinned evenly across the video.
    """
    max_frames = max(1, max_frames)
    points: list[tuple[float, str]] = [(0.0, "start")]
    points += [(t, "scene") for t in scene_times if duration is None or t < duration]
    method = "scene"
    if len(scene_times) + 1 < min_frames and duration:
        count = min(max_frames, UNIFORM_COUNT)
        points += [(t, "uniform") for t in uniform_times(duration, count)]
        method = "scene+uniform" if scene_times else "uniform"
    points = _dedupe(points, MIN_SCENE_GAP)
    return _spread(points, max_frames), method


def extract_frame(
    ffmpeg: str, video: str, t: float, dest: Path, max_width: int = 1280, timeout: float = 120
) -> bool:
    """Write one jpg (width capped, aspect kept, even height). True if the file exists."""
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-ss", f"{t:.3f}", "-i", video, "-map", "0:v:0", "-frames:v", "1",
        "-vf", f"scale=w=min({max_width}\\,iw):h=-2", "-q:v", "3", str(dest),
    ]
    run_tool(cmd, timeout=timeout)
    return dest.is_file() and dest.stat().st_size > 0


def extract_frames(
    ffmpeg: str,
    video: str,
    points: list[tuple[float, str]],
    out_dir: Path,
    max_width: int = 1280,
    workers: int = 4,
) -> list[dict]:
    """Extract frames at the given (t, kind) points; failures are dropped."""
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = [
        (i + 1, t, kind, out_dir / f"frame_{i + 1:04d}.jpg") for i, (t, kind) in enumerate(points)
    ]

    def run(job: tuple[int, float, str, Path]) -> dict | None:
        _, t, kind, dest = job
        if extract_frame(ffmpeg, video, t, dest, max_width):
            return {"t": round(t, 3), "kind": kind, "path": str(dest)}
        return None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(run, jobs))
    return [r for r in results if r]


def sample_video(
    ffmpeg: str,
    video: str,
    out_dir: Path,
    duration: float | None,
    max_frames: int = 24,
    scene_threshold: float = 0.3,
    start_time: float = 0.0,
    max_width: int = 1280,
) -> dict:
    """Full keyframe pipeline. Returns {"frames": [...], "method": str, "notes": [...]}."""
    notes: list[str] = []
    for stale in out_dir.glob("frame_*.jpg"):
        stale.unlink(missing_ok=True)
    scenes = detect_scene_times(ffmpeg, video, scene_threshold, start_time)
    if scenes is None:
        notes.append("Scene detection failed; used uniform sampling.")
        scenes = []
    points, method = choose_times(scenes, duration, max_frames)
    if not duration and len(scenes) < DEFAULT_MIN_FRAMES:
        notes.append("Unknown duration: only the first and scene-change frames are available.")
    frames = extract_frames(ffmpeg, video, points, out_dir, max_width)
    if not frames:
        notes.append("No frame could be extracted from this video.")
    elif len(frames) < len(points):
        notes.append(f"{len(points) - len(frames)} frame(s) could not be extracted.")
    return {"frames": frames, "method": method, "notes": notes, "scene_count": len(scenes)}

