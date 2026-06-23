"""Lightweight visual diagnostics for candidate gameplay moments.

These helpers intentionally compute cheap frame statistics instead of running a
heavy object detector. They provide local, explainable signals for ranking
debug and future learning work.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
from subprocess_utils import run as _run


VISUAL_DIAGNOSTICS_SCHEMA_VERSION = 1
MAX_VISUAL_CANDIDATES = 90
VISUAL_SAMPLE_WIDTH = 160


def analyze_candidate_visuals(
    video_path: str | Path,
    candidates: list[dict],
    *,
    video_duration: float = 0.0,
    max_candidates: int = MAX_VISUAL_CANDIDATES,
) -> tuple[list[dict], dict]:
    """Sample small frames around candidates and return compact diagnostics."""
    started = time.monotonic()
    results = [_empty_candidate_visual("not_sampled") for _ in candidates]
    report = {
        "schema_version": VISUAL_DIAGNOSTICS_SCHEMA_VERSION,
        "status": "unknown",
        "candidate_count": len(candidates),
        "sampled_candidate_count": 0,
        "max_candidates": int(max_candidates),
        "frames_read": 0,
        "elapsed_seconds": 0.0,
        "warnings": [],
    }

    if not candidates:
        report["status"] = "no_candidates"
        return results, report
    if not shutil.which("ffmpeg"):
        # OpenCV can still read many files without ffmpeg, but this app relies on
        # ffmpeg-backed media support. Treat missing ffmpeg as unavailable so the
        # fallback path is explicit in debug.
        report["status"] = "ffmpeg_missing"
        return [_empty_candidate_visual("ffmpeg_missing") for _ in candidates], report

    try:
        import cv2
    except Exception as exc:
        report["status"] = "opencv_missing"
        report["warnings"].append(str(exc)[:180])
        return [_empty_candidate_visual("opencv_missing") for _ in candidates], report

    path = Path(video_path)
    try:
        duration = float(video_duration or 0.0)
        if duration <= 0:
            duration = _probe_video_duration(path)

        limit = max(0, min(len(candidates), int(max_candidates or 0)))
        read_timeouts = 0
        for idx, candidate in enumerate(candidates[:limit]):
            sample_times = _candidate_sample_times(candidate, duration)
            frames = []
            actual_times = []
            for sample_time in sample_times:
                ok, frame, read_status = _read_frame_at(cv2, path, sample_time)
                if read_status == "timeout":
                    read_timeouts += 1
                    if "frame_read_timeout" not in report["warnings"]:
                        report["warnings"].append("frame_read_timeout")
                    if read_timeouts >= 3:
                        report["status"] = "read_timeout"
                        report["warnings"].append("read_timeout_limit_reached")
                        for rest_idx in range(idx, len(candidates)):
                            results[rest_idx] = _empty_candidate_visual("read_timeout")
                        return results, report
                    continue
                if not ok or frame is None:
                    continue
                frames.append(frame)
                actual_times.append(round(float(sample_time), 3))
                report["frames_read"] += 1
            summary = score_visual_frames(frames, sample_times=actual_times)
            results[idx] = summary
            if summary.get("status") == "ok":
                report["sampled_candidate_count"] += 1

        for idx in range(limit, len(candidates)):
            results[idx] = _empty_candidate_visual("not_sampled_limit")

        report["status"] = "ok" if report["sampled_candidate_count"] else "no_frames"
        if len(candidates) > limit:
            report["warnings"].append("candidate_limit_reached")
    except Exception as exc:
        report["status"] = "analysis_failed"
        report["warnings"].append(str(exc)[:180])
        results = [_empty_candidate_visual("analysis_failed") for _ in candidates]
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)

    return results, report


def score_visual_frames(frames: list[np.ndarray], *, sample_times: list[float] | None = None) -> dict:
    """Return visual statistics for already-loaded frames."""
    try:
        import cv2
    except Exception:
        return _empty_candidate_visual("opencv_missing")

    clean_frames = [frame for frame in frames or [] if isinstance(frame, np.ndarray) and frame.size]
    if not clean_frames:
        result = _empty_candidate_visual("no_frames")
        result["sample_times"] = sample_times or []
        return result

    stats = []
    grays = []
    for frame in clean_frames:
        small = _resize_frame(cv2, frame)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        edges = cv2.Canny(gray, 60, 140)
        b, g, r = cv2.split(small)
        red_mask = (r > 110) & (r.astype(np.float32) > g.astype(np.float32) * 1.30) & (r.astype(np.float32) > b.astype(np.float32) * 1.30)
        sat = hsv[:, :, 1].astype(np.float32) / 255.0
        bright = gray.astype(np.float32) / 255.0
        bright_edge_mask = (edges > 0) & (bright > 0.62) & (sat < 0.38)
        stat = {
            "brightness": float(np.mean(bright)),
            "contrast": float(np.std(bright) * 2.0),
            "darkness_ratio": float(np.mean(gray < 45)),
            "highlight_ratio": float(np.mean(gray > 220)),
            "saturation": float(np.mean(sat)),
            "edge_density": float(np.mean(edges > 0)),
            "red_ratio": float(np.mean(red_mask)),
            "ui_text_density": float(np.mean(bright_edge_mask)),
            "black_frame": bool(np.mean(bright) < 0.08 and np.std(bright) < 0.08),
        }
        stats.append(stat)
        grays.append(gray)

    motion_values = []
    for prev, current in zip(grays, grays[1:]):
        motion_values.append(float(np.mean(np.abs(current.astype(np.float32) - prev.astype(np.float32))) / 255.0))
    motion = float(np.mean(motion_values)) if motion_values else 0.0

    mean_stats = {
        key: float(np.mean([row[key] for row in stats]))
        for key in ("brightness", "contrast", "darkness_ratio", "highlight_ratio", "saturation", "edge_density", "red_ratio", "ui_text_density")
    }
    black_frame_ratio = float(np.mean([1.0 if row["black_frame"] else 0.0 for row in stats]))
    visual_energy = _score01(
        0.36 * (motion * 3.5)
        + 0.24 * (mean_stats["edge_density"] * 4.0)
        + 0.22 * mean_stats["contrast"]
        + 0.18 * (mean_stats["red_ratio"] * 5.0)
    )
    dark_scene_score = _score01(mean_stats["darkness_ratio"] * 1.05 + max(0.0, 0.34 - mean_stats["brightness"]) * 0.9)
    red_flash_score = _score01(mean_stats["red_ratio"] * 5.0)
    ui_density = _score01(mean_stats["ui_text_density"] * 12.0 + mean_stats["edge_density"] * 1.2 + mean_stats["highlight_ratio"] * 0.5)
    possible_failure_score = _score01(
        0.38 * red_flash_score
        + 0.24 * dark_scene_score
        + 0.18 * ui_density
        + 0.12 * black_frame_ratio
        + 0.08 * (1.0 - min(1.0, motion * 4.0))
    )
    scenic_score = _score01(
        0.30 * mean_stats["saturation"]
        + 0.24 * mean_stats["contrast"]
        + 0.22 * mean_stats["brightness"]
        + 0.16 * (1.0 - ui_density)
        + 0.08 * (1.0 - min(1.0, motion * 4.0))
    )

    labels = []
    if visual_energy >= 0.45:
        labels.append("high_motion")
    if dark_scene_score >= 0.48:
        labels.append("dark_scene")
    if red_flash_score >= 0.28:
        labels.append("red_flash")
    if ui_density >= 0.35:
        labels.append("ui_overlay")
    if possible_failure_score >= 0.42:
        labels.append("possible_failure_screen")
    if scenic_score >= 0.48 and ui_density < 0.55:
        labels.append("scenic_frame")

    return {
        "schema_version": VISUAL_DIAGNOSTICS_SCHEMA_VERSION,
        "status": "ok",
        "sample_count": len(clean_frames),
        "sample_times": sample_times or [],
        "brightness": round(_score01(mean_stats["brightness"]), 4),
        "contrast": round(_score01(mean_stats["contrast"]), 4),
        "darkness_ratio": round(_score01(mean_stats["darkness_ratio"]), 4),
        "highlight_ratio": round(_score01(mean_stats["highlight_ratio"]), 4),
        "saturation": round(_score01(mean_stats["saturation"]), 4),
        "edge_density": round(_score01(mean_stats["edge_density"]), 4),
        "motion": round(_score01(motion * 4.0), 4),
        "red_ratio": round(_score01(mean_stats["red_ratio"]), 4),
        "ui_density": round(ui_density, 4),
        "black_frame_ratio": round(_score01(black_frame_ratio), 4),
        "visual_energy": round(visual_energy, 4),
        "dark_scene_score": round(dark_scene_score, 4),
        "red_flash_score": round(red_flash_score, 4),
        "possible_failure_score": round(possible_failure_score, 4),
        "scenic_score": round(scenic_score, 4),
        "labels": labels,
    }


def disabled_visual_diagnostics(candidates: list[dict], *, status: str = "disabled") -> tuple[list[dict], dict]:
    """Return an explicit disabled/unavailable visual diagnostics payload."""
    rows = [_empty_candidate_visual(status) for _ in candidates]
    return rows, {
        "schema_version": VISUAL_DIAGNOSTICS_SCHEMA_VERSION,
        "status": status,
        "candidate_count": len(candidates),
        "sampled_candidate_count": 0,
        "max_candidates": 0,
        "frames_read": 0,
        "elapsed_seconds": 0.0,
        "warnings": [],
    }


def _candidate_sample_times(candidate: dict, video_duration: float) -> list[float]:
    start = _safe_float(candidate.get("start"), 0.0)
    end = _safe_float(candidate.get("end"), start + 1.0)
    if end <= start:
        end = start + 1.0
    peak = _safe_float(candidate.get("peak_time"), start + (end - start) * 0.5)
    duration = max(0.0, float(video_duration or 0.0))
    raw_times = [
        start + (end - start) * 0.18,
        peak,
        start + (end - start) * 0.82,
    ]
    times = []
    for value in raw_times:
        value = max(0.0, float(value))
        if duration > 0:
            value = min(duration, value)
        rounded = round(value, 3)
        if all(abs(rounded - existing) >= 0.25 for existing in times):
            times.append(rounded)
    return times


def _resize_frame(cv2, frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    if width <= VISUAL_SAMPLE_WIDTH:
        return frame
    scale = VISUAL_SAMPLE_WIDTH / float(width)
    return cv2.resize(frame, (VISUAL_SAMPLE_WIDTH, max(1, int(height * scale))), interpolation=cv2.INTER_AREA)


def _read_frame_at(cv2, video_path: Path, seconds: float, timeout: float = 4.0):
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-ss", f"{max(0.0, float(seconds or 0.0)):.3f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-f", "image2pipe",
        "-vcodec", "png",
        "-",
    ]
    try:
        result = _run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, None, "timeout"
    except Exception:
        return False, None, "error"
    if result.returncode != 0 or not result.stdout:
        return False, None, "error"
    frame = cv2.imdecode(np.frombuffer(result.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
    return frame is not None, frame, "ok" if frame is not None else "error"


def _probe_video_duration(video_path: Path) -> float:
    try:
        result = _run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            errors="replace",
        )
        return max(0.0, float(str(result.stdout).strip()))
    except Exception:
        return 0.0


def _empty_candidate_visual(status: str) -> dict:
    return {
        "schema_version": VISUAL_DIAGNOSTICS_SCHEMA_VERSION,
        "status": status,
        "sample_count": 0,
        "sample_times": [],
        "visual_energy": 0.0,
        "dark_scene_score": 0.0,
        "red_flash_score": 0.0,
        "possible_failure_score": 0.0,
        "scenic_score": 0.0,
        "ui_density": 0.0,
        "labels": [],
    }


def _safe_float(value, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return parsed


def _score01(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(0.0, min(1.0, number))
