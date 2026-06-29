"""Montage rendering helpers.

Each storyboard beat becomes a normalized temporary MP4 segment, then FFmpeg
concatenates those segments with hard cuts. Optional crop/subtitle data is
handled per segment by the same clip extraction path used for normal clips.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from clipper import _run_ffmpeg, extract_clip
from audio_streams import audio_output_args


MONTAGE_RENDER_SCHEMA_VERSION = 1


def render_draft_montage(
    storyboard: dict,
    output_path: Path,
    *,
    temp_dir: Path,
    preset: str = "ultrafast",
    crf: str = "28",
    render_type: str = "draft_hard_cut",
) -> dict:
    """Render a draft montage from a storyboard render plan."""

    started = time.monotonic()
    output_path = Path(output_path)
    temp_dir = Path(temp_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    plan = storyboard.get("render_plan") if isinstance(storyboard, dict) else None
    if not isinstance(plan, list) or not plan:
        return _render_result("failed", output_path, started, error="storyboard has no render plan", render_type=render_type)

    segments: list[Path] = []
    cleanup_paths: list[Path] = []
    segment_debug: list[dict] = []
    try:
        for index, item in enumerate(plan, 1):
            if not isinstance(item, dict):
                return _render_result("failed", output_path, started, error=f"render plan item {index} is invalid", render_type=render_type)
            source = Path(str(item.get("source_video") or "")).expanduser()
            if not source.exists() or not source.is_file():
                return _render_result("failed", output_path, started, error=f"source video missing for beat {index}", render_type=render_type)
            start = max(0.0, _safe_float(item.get("start"), 0.0) or 0.0)
            end = _safe_float(item.get("end"), start) or start
            duration = max(0.0, end - start)
            if duration <= 0.2:
                return _render_result("failed", output_path, started, error=f"beat {index} has invalid duration", render_type=render_type)
            segment_path = temp_dir / f"{output_path.stem}_seg{index:03d}.mp4"
            cleanup_paths.append(segment_path)
            crop_params = _normalize_crop_params(item.get("crop_params"))
            subtitle_path = _existing_path(item.get("subtitle_path"))
            if crop_params or subtitle_path:
                clip_result = extract_clip(
                    source,
                    start,
                    end,
                    segment_path,
                    subtitle_path=subtitle_path,
                    crop_params=crop_params,
                    preset=str(preset or "ultrafast"),
                    crf=str(crf or "28"),
                )
                segment_debug.append(
                    {
                        "beat_id": str(item.get("beat_id") or ""),
                        "source_video": str(source),
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "duration": round(duration, 3),
                        "returncode": 0 if clip_result and clip_result.path else 1,
                        "crop_applied": bool(crop_params),
                        "subtitle_path": str(subtitle_path) if subtitle_path else "",
                        "subtitles_burned": bool(clip_result and clip_result.subtitles_burned and subtitle_path),
                        "warning": getattr(clip_result, "warning", None),
                        "stderr_tail": "",
                    }
                )
                if not clip_result or not clip_result.path or not segment_path.exists():
                    return _render_result(
                        "failed",
                        output_path,
                        started,
                        error=f"segment {index} render failed",
                        segments=segment_debug,
                        render_type=render_type,
                    )
            else:
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-nostdin",
                    "-ss",
                    f"{start:.3f}",
                    "-i",
                    str(source),
                    "-t",
                    f"{duration:.3f}",
                    "-sn",
                    "-dn",
                    "-c:v",
                    "libx264",
                    "-preset",
                    str(preset or "ultrafast"),
                    "-crf",
                    str(crf or "28"),
                    "-pix_fmt",
                    "yuv420p",
                    *audio_output_args(source, bitrate="160k"),
                    "-movflags",
                    "+faststart",
                    str(segment_path),
                ]
                result = _run_ffmpeg(
                    cmd,
                    duration=duration,
                    phase=f"Montage segment {index}",
                    minimum=90,
                    multiplier=10,
                    maximum=900,
                    capture_output=True,
                    text=True,
                    errors="replace",
                )
                segment_debug.append(
                    {
                        "beat_id": str(item.get("beat_id") or ""),
                        "source_video": str(source),
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "duration": round(duration, 3),
                        "returncode": result.returncode,
                        "crop_applied": False,
                        "subtitle_path": "",
                        "subtitles_burned": False,
                        "stderr_tail": _tail(getattr(result, "stderr", "")),
                    }
                )
                if result.returncode != 0 or not segment_path.exists():
                    return _render_result(
                        "failed",
                        output_path,
                        started,
                        error=f"segment {index} render failed",
                        segments=segment_debug,
                        render_type=render_type,
                    )
            segments.append(segment_path)

        concat_list = temp_dir / f"{output_path.stem}_concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{_ffconcat_path(path)}'" for path in segments) + "\n",
            encoding="utf-8",
        )
        concat_result = _concat_segments(concat_list, output_path, preset=preset, crf=crf)
        if concat_result.returncode != 0 or not output_path.exists():
            return _render_result(
                "failed",
                output_path,
                started,
                error="concat render failed",
                segments=segment_debug,
                concat={"returncode": concat_result.returncode, "stderr_tail": _tail(getattr(concat_result, "stderr", ""))},
                render_type=render_type,
            )
        return _render_result(
            "ok",
            output_path,
            started,
            segments=segment_debug,
            concat={"returncode": concat_result.returncode, "stderr_tail": _tail(getattr(concat_result, "stderr", ""))},
            storyboard_id=str(storyboard.get("storyboard_id") or ""),
            render_type=render_type,
        )
    except Exception as exc:
        return _render_result(
            "failed",
            output_path,
            started,
            error=f"montage render error: {exc}",
            segments=segment_debug,
            render_type=render_type,
        )
    finally:
        _cleanup_segments(cleanup_paths)
        try:
            (temp_dir / f"{output_path.stem}_concat.txt").unlink(missing_ok=True)
        except Exception:
            pass


def _concat_segments(concat_list: Path, output_path: Path, *, preset: str, crf: str):
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-nostdin",
        "-fflags",
        "+genpts",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        str(preset or "ultrafast"),
        "-crf",
        str(crf or "28"),
        "-pix_fmt",
        "yuv420p",
        "-af",
        "aresample=async=1:first_pts=0",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    result = _run_ffmpeg(
        cmd,
        duration=60,
        phase="Montage concat",
        minimum=120,
        multiplier=12,
        maximum=900,
        capture_output=True,
        text=True,
        errors="replace",
    )
    if result.returncode == 0:
        return result

    copy_out = output_path.with_name(output_path.stem + "_copy_tmp.mp4")
    fallback = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-nostdin",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(copy_out),
    ]
    copy_result = _run_ffmpeg(
        fallback,
        duration=60,
        phase="Montage concat fallback",
        minimum=90,
        multiplier=8,
        maximum=900,
        capture_output=True,
        text=True,
        errors="replace",
    )
    if copy_result.returncode == 0 and copy_out.exists():
        try:
            copy_out.replace(output_path)
        except Exception:
            pass
    else:
        try:
            copy_out.unlink(missing_ok=True)
        except Exception:
            pass
    return copy_result


def _render_result(
    status: str,
    output_path: Path,
    started: float,
    *,
    error: str = "",
    segments: list[dict] | None = None,
    concat: dict | None = None,
    storyboard_id: str = "",
    render_type: str = "draft_hard_cut",
) -> dict:
    output_exists = output_path.exists()
    size_bytes = output_path.stat().st_size if output_exists else 0
    return {
        "schema_version": MONTAGE_RENDER_SCHEMA_VERSION,
        "status": status,
        "ok": status == "ok",
        "error": error,
        "storyboard_id": storyboard_id,
        "output_path": str(output_path) if output_exists else "",
        "filename": output_path.name if output_exists else "",
        "size_bytes": size_bytes,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "segments": segments or [],
        "concat": concat or {},
        "stores_raw_media": False,
        "render_type": render_type,
    }


def _ffconcat_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")


def _existing_path(value: Any) -> Path | None:
    if not value:
        return None
    path = Path(str(value)).expanduser()
    return path if path.exists() and path.is_file() else None


def _normalize_crop_params(value: Any):
    if not value:
        return None
    if isinstance(value, tuple):
        return value
    if not isinstance(value, list):
        return None
    if len(value) == 4:
        try:
            return tuple(int(round(float(part))) for part in value)
        except (TypeError, ValueError):
            return None
    if len(value) == 3 and isinstance(value[2], list):
        try:
            crop_w = int(round(float(value[0])))
            crop_h = int(round(float(value[1])))
            keyframes = [
                (float(item[0]), int(round(float(item[1]))), int(round(float(item[2]))))
                for item in value[2]
                if isinstance(item, (list, tuple)) and len(item) >= 3
            ]
        except (TypeError, ValueError):
            return None
        return (crop_w, crop_h, keyframes) if keyframes else None
    return None


def _cleanup_segments(paths: list[Path]):
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            pass


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if result != result or result in (float("inf"), float("-inf")):
        return default
    return result


def _tail(value: str, limit: int = 600) -> str:
    text = str(value or "")
    return text[-limit:]
