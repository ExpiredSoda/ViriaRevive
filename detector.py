import hashlib
import json
import math
import subprocess
import tempfile
import time
import shutil
import numpy as np
from pydub import AudioSegment
from pathlib import Path

from audio_streams import build_audio_mix_filter, describe_audio_streams, get_audio_streams
from config import ANALYSIS_CACHE_DIR, SUBTITLES_DIR
from subprocess_utils import run as _run


_LAST_SCENE_DETECTION: dict = {}

_PYSCENEDETECT_CHUNK_SECONDS = 600
_PYSCENEDETECT_LONG_SECONDS = 1800
_PYSCENEDETECT_PROCESS_GUARD_SECONDS = 1800
_SCENE_CACHE_ALGORITHM_VERSION = 2
_PYSCENEDETECT_ENABLED = False


def find_viral_moments(
    video_path: Path,
    num_clips: int = 5,
    clip_duration: int = 30,
    min_gap: int = 15,
    candidate_multiplier: int = 1,
    scene_mode: str = "full",
    max_candidates: int | None = None,
    progress_callback=None,
) -> list:
    """Find viral moments using audio energy + scene change analysis (no AI)."""

    print("[*] Analyzing audio energy...")
    audio, temp_audio = _load_analysis_audio(video_path)
    try:
        total_seconds = len(audio) // 1000

        if total_seconds < 10:
            print("[!] Video too short for analysis")
            return []

        # --- Audio RMS energy (1-second windows) ---
        window_ms = 1000
        energies = np.array(
            [audio[i : i + window_ms].rms for i in range(0, len(audio), window_ms)],
            dtype=float,
        )
    finally:
        if temp_audio:
            try:
                temp_audio.unlink(missing_ok=True)
            except OSError:
                pass

    # Smooth
    kernel = np.ones(5) / 5
    smoothed = np.convolve(energies, kernel, mode="same")

    # --- Volume variance (dynamic = interesting) ---
    var_window = 10
    variance = np.array(
        [
            np.std(energies[max(0, i - var_window // 2) : i + var_window // 2])
            for i in range(len(energies))
        ]
    )

    # --- Combine (normalize each to 0-1) ---
    def norm(a):
        r = a.max() - a.min()
        return (a - a.min()) / r if r > 1e-8 else np.zeros_like(a)

    audio_score = norm(smoothed)
    variance_score = norm(variance)
    target_count = _candidate_target_count(
        num_clips,
        candidate_multiplier,
        max_candidates=max_candidates,
        scene_mode=scene_mode,
    )
    target_windows = None
    if _normalize_scene_mode(scene_mode) == "targeted":
        pre_scene_score = 0.62 * audio_score + 0.38 * variance_score
        target_windows = _targeted_scene_windows(
            pre_scene_score,
            target_count=target_count,
            clip_duration=clip_duration,
            min_gap=min_gap,
            video_duration=float(total_seconds),
        )

    # --- Scene change density ---
    print("[*] Analyzing scene changes...")
    scene_density, scene_info = _scene_change_density(
        video_path,
        len(energies),
        video_duration=float(total_seconds),
        mode=scene_mode,
        target_windows=target_windows,
        progress_callback=progress_callback,
    )

    scene_score = norm(scene_density[: len(smoothed)])
    combined = 0.45 * audio_score + 0.25 * variance_score + 0.30 * scene_score

    # --- Pick top non-overlapping peaks. The pipeline may ask for a larger
    # candidate pool, then transcript-rerank before rendering.
    preroll = _gameplay_preroll(clip_duration)
    postroll = max(6, clip_duration - preroll)
    clips = []
    rank = 1
    include_rescue = int(candidate_multiplier) > 1
    while len(clips) < target_count:
        if combined.max() <= 0:
            break
        peak = int(np.argmax(combined))
        clips.append(
            _candidate(
                start=max(0, peak - preroll),
                end=min(len(combined), peak + postroll),
                total=len(combined),
                clip_duration=clip_duration,
                peak=peak,
                score=float(combined[peak]),
                audio=float(audio_score[peak]),
                variance=float(variance_score[peak]),
                scene=float(scene_score[peak]),
                rank=rank,
                kind="primary",
            )
        )

        # Rescue window: when the peak is aftermath (death/restart/reaction),
        # a pre-event candidate often contains the actual setup/payoff.
        pre_start = max(0, peak - clip_duration - max(5, min_gap // 2))
        if include_rescue and len(clips) < target_count and pre_start < peak - preroll - 2:
            rank += 1
            clips.append(
                _candidate(
                    start=pre_start,
                    end=pre_start + clip_duration,
                    total=len(combined),
                    clip_duration=clip_duration,
                    peak=peak,
                    score=float(combined[peak]) * 0.96,
                    audio=float(audio_score[peak]),
                    variance=float(variance_score[peak]),
                    scene=float(scene_score[peak]),
                    rank=rank,
                    kind="pre_event",
                )
            )

        rank += 1

        # mask out neighbourhood
        lo = max(0, peak - clip_duration - min_gap)
        hi = min(len(combined), peak + clip_duration + min_gap)
        combined[lo:hi] = 0

    clips.sort(key=lambda c: (c["candidate_rank"], c["start"]))
    for clip in clips:
        clip["scene_detection_status"] = scene_info.get("status", "unknown")

    print(f"[+] Found {len(clips)} candidate moments")
    for i, c in enumerate(clips[: min(len(clips), num_clips * 2)]):
        print(
            f"    Candidate {i+1}: {_fmt(c['start'])} - {_fmt(c['end'])} "
            f"peak {_fmt(c['peak_time'])} score {c['score']:.2f} "
            f"({c['candidate_kind']})"
        )
    return clips


def get_last_scene_detection_diagnostics() -> dict:
    """Return diagnostics from the most recent scene detection pass."""
    return dict(_LAST_SCENE_DETECTION)


# ── helpers ──────────────────────────────────────────────────────────────────


def _scene_change_density(
    video_path: Path,
    length: int,
    video_duration: float | None = None,
    mode: str = "full",
    target_windows: list[tuple[float, float]] | None = None,
    progress_callback=None,
) -> tuple[np.ndarray, dict]:
    """Count scene changes per second using PySceneDetect, falling back to FFmpeg."""
    global _LAST_SCENE_DETECTION

    density = np.zeros(length + 1)
    mode = _normalize_scene_mode(mode)
    timeout = _scene_timeout_seconds(video_duration or float(length))
    diagnostics = {
        "status": "unknown",
        "mode": mode,
        "engine": "none",
        "command": [],
        "elapsed_seconds": 0.0,
        "timeout_seconds": timeout,
        "returncode": None,
        "timestamp_count": 0,
        "nonzero_density_count": 0,
        "max_density": 0.0,
        "stderr_tail": "",
        "cache_hit": False,
        "cache_key": "",
        "target_window_count": len(target_windows or []),
        "target_window_seconds": round(sum(float(span) for _, span in (target_windows or [])), 3),
        "pyscenedetect_attempt": {},
    }

    if mode == "skip":
        diagnostics["status"] = "skipped"
        _LAST_SCENE_DETECTION = diagnostics
        print("[i] Scene detection skipped for this processing depth; using audio/transcript ranking")
        return density, diagnostics

    cache_key = _scene_cache_key(video_path, length, video_duration or float(length), mode, target_windows)
    diagnostics["cache_key"] = cache_key
    cached = _load_scene_cache(cache_key)
    if cached:
        timestamps = [float(ts) for ts in cached.get("timestamps", []) if isinstance(ts, (int, float))]
        _populate_scene_density(density, length, timestamps)
        diagnostics.update(
            {
                "status": str(cached.get("status") or "cached"),
                "engine": str(cached.get("engine") or "cache"),
                "elapsed_seconds": 0.0,
                "timestamp_count": len(timestamps),
                "nonzero_density_count": int(np.count_nonzero(density)),
                "max_density": float(density.max()) if len(density) else 0.0,
                "cache_hit": True,
            }
        )
        _LAST_SCENE_DETECTION = diagnostics
        print(f"[scene-cache] Reused {len(timestamps)} scene timestamp(s) for {mode} scan")
        _notify_scene_progress(progress_callback, video_duration or float(length), video_duration or float(length), mode)
        return density, diagnostics

    _notify_scene_progress(progress_callback, 0.0, video_duration or float(length), mode)

    py_density, py_diagnostics, py_success = _try_pyscenedetect_scene_change_density(
        video_path,
        density.copy(),
        length,
        video_duration,
        mode,
        diagnostics,
        target_windows=target_windows,
        progress_callback=progress_callback,
    )
    diagnostics["pyscenedetect_attempt"] = py_diagnostics
    if py_success:
        _save_scene_cache(cache_key, py_diagnostics, _scene_timestamps(py_diagnostics))
        py_diagnostics.pop("_scene_timestamps", None)
        _LAST_SCENE_DETECTION = py_diagnostics
        if py_diagnostics["timestamp_count"]:
            print(
                f"[+] PySceneDetect found {py_diagnostics['timestamp_count']} "
                "scene-change frames"
            )
        else:
            print("[i] PySceneDetect completed: zero changes above threshold")
        return py_density, py_diagnostics

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        diagnostics["status"] = "ffmpeg_missing"
        diagnostics["engine"] = "none"
        _LAST_SCENE_DETECTION = diagnostics
        print("[!] Scene detection unavailable: PySceneDetect/FFmpeg not usable; using audio only")
        return density, diagnostics

    if mode in {"sampled", "targeted"}:
        return _sampled_scene_change_density(
            ffmpeg,
            video_path,
            density,
            length,
            video_duration,
            diagnostics,
            target_windows=target_windows,
            progress_callback=progress_callback,
        )

    diagnostics["engine"] = "ffmpeg"
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-threads", "4",
        "-i", str(video_path),
        "-map", "0:v:0",
        "-an", "-sn", "-dn",
        "-vf", "fps=2,select='gt(scene,0.3)',showinfo",
        "-fps_mode", "vfr",
        "-f", "null", "-",
    ]
    diagnostics["command"] = cmd
    started = time.monotonic()
    try:
        r = _run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
        diagnostics["elapsed_seconds"] = round(time.monotonic() - started, 3)
        diagnostics["returncode"] = r.returncode
        diagnostics["stderr_tail"] = _tail(r.stderr or "")

        if r.returncode != 0:
            diagnostics["status"] = "ffmpeg_error"
            _LAST_SCENE_DETECTION = diagnostics
            print(f"[!] Scene detection ffmpeg error (code {r.returncode}); using audio only")
            if diagnostics["stderr_tail"]:
                print(f"    {diagnostics['stderr_tail'][-400:]}")
            return density, diagnostics

        timestamps = []
        for line in (r.stderr or "").split("\n"):
            if "pts_time:" in line:
                try:
                    timestamps.append(float(line.split("pts_time:")[1].split()[0]))
                except (ValueError, IndexError):
                    pass

        _populate_scene_density(density, length, timestamps)

        diagnostics["timestamp_count"] = len(timestamps)
        diagnostics["nonzero_density_count"] = int(np.count_nonzero(density))
        diagnostics["max_density"] = float(density.max()) if len(density) else 0.0
        diagnostics["status"] = "ok" if timestamps else "zero_changes"
        diagnostics["_scene_timestamps"] = timestamps
        _save_scene_cache(cache_key, diagnostics, timestamps)
        diagnostics.pop("_scene_timestamps", None)
        _LAST_SCENE_DETECTION = diagnostics
        if timestamps:
            print(f"[+] Scene detection found {len(timestamps)} scene-change frames")
        else:
            print("[i] Scene detection completed: zero changes above threshold")
        _notify_scene_progress(progress_callback, video_duration or float(length), video_duration or float(length), mode)
        return density, diagnostics

    except subprocess.TimeoutExpired:
        diagnostics["elapsed_seconds"] = round(time.monotonic() - started, 3)
        diagnostics["status"] = "timeout"
        _LAST_SCENE_DETECTION = diagnostics
        print(f"[!] Scene detection timed out after {timeout}s; using audio only")
        return density, diagnostics
    except FileNotFoundError:
        diagnostics["elapsed_seconds"] = round(time.monotonic() - started, 3)
        diagnostics["status"] = "ffmpeg_missing"
        _LAST_SCENE_DETECTION = diagnostics
        print("[!] Scene detection unavailable: ffmpeg not found; using audio only")
        return density, diagnostics


def _load_analysis_audio(video_path: Path) -> tuple[AudioSegment, Path | None]:
    """Load low-rate WAV audio for moment detection with bounded ffmpeg decode."""
    streams = get_audio_streams(video_path)
    SUBTITLES_DIR.mkdir(exist_ok=True)
    temp = tempfile.NamedTemporaryFile(
        prefix="analysis_audio_", suffix=".wav", dir=SUBTITLES_DIR, delete=False
    )
    temp_path = Path(temp.name)
    temp.close()

    duration = _probe_media_duration(video_path)
    timeout = _analysis_audio_timeout_seconds(duration)
    mix = build_audio_mix_filter(video_path, mono=True) if len(streams) >= 2 else None
    if mix:
        filter_graph, out_label = mix
        print(f"[audio] Detection mix uses: {describe_audio_streams(video_path)}")
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-nostdin",
            "-i", str(video_path),
            "-filter_complex", filter_graph,
            "-map", f"[{out_label}]",
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            str(temp_path),
        ]
    else:
        stream = int(streams[0]["ordinal"]) if streams else 0
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-nostdin",
            "-i", str(video_path),
            "-map", f"0:a:{stream}",
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            str(temp_path),
        ]

    try:
        r = _run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
        if r.returncode == 0 and temp_path.exists() and temp_path.stat().st_size > 44:
            return AudioSegment.from_file(str(temp_path)), temp_path
        print(f"[audio] Analysis audio extraction failed:\n{(r.stderr or '')[-400:]}")
    except subprocess.TimeoutExpired:
        print(f"[audio] Analysis audio extraction timed out after {timeout}s")
    except FileNotFoundError:
        print("[audio] Analysis audio extraction failed: ffmpeg not found")
    except Exception as exc:
        print(f"[audio] Analysis audio extraction failed: {exc}")

    try:
        temp_path.unlink(missing_ok=True)
    except OSError:
        pass
    return AudioSegment.silent(duration=0), None


def _probe_media_duration(video_path: Path) -> float:
    try:
        r = _run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            errors="replace",
        )
        return max(0.0, float(str(r.stdout).strip()))
    except Exception:
        return 0.0


def _analysis_audio_timeout_seconds(video_duration: float | int | None) -> int:
    duration = max(0.0, float(video_duration or 0.0))
    return int(min(3600, max(300, duration * 0.5)))


def _gameplay_preroll(clip_duration: int) -> int:
    """Bias gameplay clips toward the setup before the loud/visual peak."""
    if clip_duration <= 16:
        return max(8, clip_duration - 6)
    return min(30, max(12, int(round(clip_duration * 0.73))))


def _scene_timeout_seconds(video_duration: float) -> int:
    """Scale scene detection timeout for long/high-codec recordings."""
    return int(min(1200, max(180, video_duration * 0.16)))


def _normalize_scene_mode(value: str | None) -> str:
    mode = str(value or "full").strip().lower()
    return mode if mode in {"full", "sampled", "targeted", "skip"} else "full"


def _candidate_target_count(
    num_clips: int,
    candidate_multiplier: int,
    *,
    max_candidates: int | None = None,
    scene_mode: str = "full",
) -> int:
    requested = max(1, int(num_clips or 1))
    multiplier = max(1, int(candidate_multiplier or 1))
    raw_target = max(requested, requested * multiplier)
    if max_candidates is None:
        mode = _normalize_scene_mode(scene_mode)
        cap = {"skip": 36, "sampled": 56, "targeted": 72, "full": 84}.get(mode, 72)
    else:
        cap = max(requested, int(max_candidates or requested))
    return max(requested, min(raw_target, cap))


def _targeted_scene_windows(
    pre_scene_score: np.ndarray,
    *,
    target_count: int,
    clip_duration: int,
    min_gap: int,
    video_duration: float,
) -> list[tuple[float, float]]:
    duration = max(0.0, float(video_duration or len(pre_scene_score) or 0.0))
    if duration <= 0 or len(pre_scene_score) == 0:
        return []

    window_seconds = float(max(28, min(60, int(clip_duration or 30) + 12)))
    peak_limit = max(8, min(40, int(target_count or 1)))
    anchor_count = 10 if duration >= 7200 else (8 if duration >= 3600 else 6)
    windows: list[tuple[float, float]] = []

    scores = np.array(pre_scene_score, dtype=float, copy=True)
    for _ in range(peak_limit):
        if len(scores) == 0 or float(np.max(scores)) <= 0:
            break
        peak = int(np.argmax(scores))
        start = max(0.0, float(peak) - window_seconds * 0.55)
        windows.append((start, min(window_seconds, max(0.0, duration - start))))
        mask = int(max(window_seconds, clip_duration + min_gap))
        lo = max(0, peak - mask)
        hi = min(len(scores), peak + mask)
        scores[lo:hi] = 0.0

    if anchor_count > 0:
        anchor_window = min(window_seconds, 45.0)
        max_start = max(0.0, duration - anchor_window)
        for start in np.linspace(0.0, max_start, anchor_count).tolist():
            windows.append((float(start), min(anchor_window, max(0.0, duration - float(start)))))

    return _merge_scene_windows(windows, duration=duration, max_windows=56)


def _merge_scene_windows(
    windows: list[tuple[float, float]],
    *,
    duration: float,
    max_windows: int = 56,
) -> list[tuple[float, float]]:
    normalized = []
    for start, span in windows:
        start = max(0.0, min(float(start or 0.0), max(0.0, duration)))
        end = max(start, min(duration, start + max(0.0, float(span or 0.0))))
        if end - start >= 1.0:
            normalized.append((start, end))
    if not normalized:
        return []
    normalized.sort()
    merged: list[list[float]] = []
    for start, end in normalized:
        if not merged or start > merged[-1][1] + 4.0:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    trimmed = merged[: max(1, int(max_windows or 1))]
    return [(round(start, 3), round(end - start, 3)) for start, end in trimmed]


def _scene_cache_key(
    video_path: Path,
    length: int,
    video_duration: float,
    mode: str,
    target_windows: list[tuple[float, float]] | None = None,
) -> str:
    if not Path(video_path).exists():
        return ""
    try:
        stat = Path(video_path).stat()
        source = {
            "path": str(Path(video_path).resolve()).lower(),
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }
    except OSError:
        source = {"path": str(video_path).lower(), "size": 0, "mtime_ns": 0}
    payload = {
        "schema_version": 1,
        "algorithm_version": _SCENE_CACHE_ALGORITHM_VERSION,
        "detector": {
            "ffmpeg_scene_threshold": 0.3,
            "ffmpeg_full_fps": 2,
            "ffmpeg_sampled_fps": 1,
            "pyscenedetect": {
                "adaptive_threshold": 3.0,
                "min_scene_len": 15,
                "min_content_val": 15.0,
                "long_video_guard_seconds": _PYSCENEDETECT_PROCESS_GUARD_SECONDS,
            },
        },
        "source": source,
        "length": int(length),
        "duration": round(float(video_duration or 0.0), 3),
        "mode": _normalize_scene_mode(mode),
        "target_windows": [
            [round(float(start), 3), round(float(span), 3)]
            for start, span in (target_windows or [])
        ],
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _scene_cache_path(cache_key: str) -> Path:
    return ANALYSIS_CACHE_DIR / f"scene_{cache_key}.json"


def _load_scene_cache(cache_key: str) -> dict | None:
    if not cache_key:
        return None
    path = _scene_cache_path(cache_key)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        data.get("schema_version") != 1
        or data.get("algorithm_version") != _SCENE_CACHE_ALGORITHM_VERSION
        or data.get("cache_key") != cache_key
    ):
        return None
    timestamps = data.get("timestamps")
    if not isinstance(timestamps, list):
        return None
    return data


def _save_scene_cache(cache_key: str, diagnostics: dict, timestamps: list[float]) -> None:
    status = str(diagnostics.get("status") or "")
    cacheable = {
        "ok",
        "zero_changes",
        "sampled_ok",
        "sampled_zero_changes",
        "targeted_ok",
        "targeted_zero_changes",
    }
    if not cache_key or status not in cacheable:
        return
    try:
        ANALYSIS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "algorithm_version": _SCENE_CACHE_ALGORITHM_VERSION,
            "cache_key": cache_key,
            "status": status,
            "engine": str(diagnostics.get("engine") or "unknown"),
            "mode": str(diagnostics.get("mode") or ""),
            "timestamp_count": len(timestamps or []),
            "target_window_count": diagnostics.get("target_window_count", 0),
            "target_window_seconds": diagnostics.get("target_window_seconds", 0),
            "timestamps": [round(float(ts), 3) for ts in (timestamps or [])],
            "created_at": int(time.time()),
        }
        tmp = _scene_cache_path(cache_key).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(_scene_cache_path(cache_key))
    except OSError:
        pass


def _scene_timestamps(diagnostics: dict) -> list[float]:
    values = diagnostics.get("_scene_timestamps")
    if not isinstance(values, list):
        return []
    clean = []
    for value in values:
        try:
            clean.append(float(value))
        except (TypeError, ValueError):
            continue
    return clean


def _try_pyscenedetect_scene_change_density(
    video_path: Path,
    density: np.ndarray,
    length: int,
    video_duration: float | None,
    mode: str,
    base_diagnostics: dict,
    target_windows: list[tuple[float, float]] | None = None,
    progress_callback=None,
) -> tuple[np.ndarray, dict, bool]:
    """Run PySceneDetect when available; return success=False for FFmpeg fallback."""
    diagnostics = {
        **base_diagnostics,
        "engine": "pyscenedetect",
        "backend": "opencv",
        "detector": "AdaptiveDetector",
        "chunked": False,
        "chunk_count": 0,
        "chunk_seconds": None,
        "sample_count": 0,
        "sample_starts": [],
        "downscale": None,
        "frame_skip": 0,
        "processed_frame_count": 0,
        "chunk_results": [],
        "fallback_reason": "",
        "pyscenedetect_attempt": {},
    }

    duration = float(video_duration or length or 0)
    if duration <= 0:
        diagnostics["status"] = "zero_changes"
        return density, diagnostics, True
    if not _PYSCENEDETECT_ENABLED:
        diagnostics["status"] = "pyscenedetect_disabled_timeout_safety"
        diagnostics["engine"] = "none"
        diagnostics["fallback_reason"] = "Bounded FFmpeg scene scanning is used for public runtime safety"
        diagnostics["elapsed_seconds"] = 0.0
        return density, diagnostics, False
    if duration >= _PYSCENEDETECT_PROCESS_GUARD_SECONDS:
        diagnostics["status"] = "pyscenedetect_skipped_timeout_guard"
        diagnostics["fallback_reason"] = (
            "Long-video PySceneDetect is routed to bounded FFmpeg scene scanning"
        )
        diagnostics["elapsed_seconds"] = 0.0
        return density, diagnostics, False

    try:
        from scenedetect import SceneManager, open_video
        from scenedetect.detectors import AdaptiveDetector
    except Exception as exc:
        diagnostics["status"] = "pyscenedetect_missing"
        diagnostics["engine"] = "none"
        diagnostics["fallback_reason"] = f"pyscenedetect_import_failed: {exc}"
        return density, diagnostics, False

    windows = (
        _merge_scene_windows(target_windows or [], duration=duration, max_windows=56)
        if mode == "targeted"
        else _pyscenedetect_scan_windows(mode, duration)
    )
    config = _pyscenedetect_config(mode, duration)
    diagnostics.update(config)
    diagnostics["chunked"] = len(windows) > 1
    diagnostics["chunk_count"] = len(windows)
    diagnostics["chunk_seconds"] = _PYSCENEDETECT_CHUNK_SECONDS if mode == "full" else None
    diagnostics["sample_count"] = len(windows) if mode == "sampled" else 0
    diagnostics["target_window_count"] = len(windows) if mode == "targeted" else diagnostics.get("target_window_count", 0)
    diagnostics["target_window_seconds"] = (
        round(sum(float(span) for _, span in windows), 3)
        if mode == "targeted"
        else diagnostics.get("target_window_seconds", 0)
    )
    diagnostics["sample_starts"] = [round(float(start), 2) for start, _ in windows]

    timestamps: list[float] = []
    started = time.monotonic()
    try:
        for start, window_duration in windows:
            video = open_video(str(video_path), backend=diagnostics["backend"])
            try:
                if start > 0:
                    video.seek(float(start))
                scene_manager = SceneManager()
                scene_manager.auto_downscale = False
                scene_manager.downscale = diagnostics["downscale"]
                scene_manager.add_detector(
                    AdaptiveDetector(
                        adaptive_threshold=3.0,
                        min_scene_len=15,
                        min_content_val=15.0,
                    )
                )
                frames = scene_manager.detect_scenes(
                    video=video,
                    duration=float(window_duration),
                    frame_skip=int(diagnostics["frame_skip"]),
                    show_progress=False,
                )
                diagnostics["processed_frame_count"] += int(frames or 0)
                try:
                    cuts = scene_manager.get_cut_list(show_warning=False)
                except TypeError:
                    cuts = scene_manager.get_cut_list()

                chunk_timestamps = []
                for cut in cuts:
                    seconds = _timecode_seconds(cut)
                    if seconds is None:
                        continue
                    if start > 0 and seconds < start - 1:
                        seconds += float(start)
                    if 0 <= seconds <= duration:
                        timestamps.append(seconds)
                        chunk_timestamps.append(round(float(seconds), 3))
                diagnostics["chunk_results"].append(
                    {
                        "start": round(float(start), 3),
                        "duration": round(float(window_duration), 3),
                        "cut_count": len(chunk_timestamps),
                        "cuts": chunk_timestamps[:25],
                    }
                )
                _notify_scene_progress(
                    progress_callback,
                    min(duration, float(start) + float(window_duration)),
                    duration,
                    mode,
                )
            finally:
                release = getattr(video, "release", None)
                if callable(release):
                    release()

        timestamps = sorted(set(round(float(ts), 3) for ts in timestamps))
        _populate_scene_density(density, length, timestamps)
        diagnostics["elapsed_seconds"] = round(time.monotonic() - started, 3)
        diagnostics["timestamp_count"] = len(timestamps)
        diagnostics["nonzero_density_count"] = int(np.count_nonzero(density))
        diagnostics["max_density"] = float(density.max()) if len(density) else 0.0
        prefix = "sampled_" if mode == "sampled" else ("targeted_" if mode == "targeted" else "")
        diagnostics["status"] = f"{prefix}ok" if timestamps else f"{prefix}zero_changes"
        diagnostics["_scene_timestamps"] = timestamps
        return density, diagnostics, True
    except Exception as exc:
        diagnostics["elapsed_seconds"] = round(time.monotonic() - started, 3)
        diagnostics["status"] = "pyscenedetect_error"
        diagnostics["fallback_reason"] = str(exc)[:400]
        diagnostics["stderr_tail"] = str(exc)[-2000:]
        print(f"[!] PySceneDetect scene scan failed; falling back to FFmpeg: {exc}")
        return density, diagnostics, False


def _pyscenedetect_scan_windows(mode: str, duration: float) -> list[tuple[float, float]]:
    """Return scan windows for PySceneDetect without exposing another UI option."""
    duration = max(0.0, float(duration or 0))
    if duration <= 0:
        return []

    if mode == "sampled":
        window_seconds = 75 if duration >= 3600 else 45
        sample_count = 10 if duration >= 7200 else 8
        sample_count = max(
            1,
            min(sample_count, int(max(1, duration // max(window_seconds, 1)))),
        )
        if sample_count <= 1:
            starts = [0.0]
        else:
            max_start = max(0.0, duration - window_seconds)
            starts = np.linspace(0, max_start, sample_count).tolist()
        return [
            (float(start), min(float(window_seconds), max(0.0, duration - float(start))))
            for start in starts
        ]

    if duration <= _PYSCENEDETECT_LONG_SECONDS:
        return [(0.0, duration)]

    chunk_count = int(math.ceil(duration / _PYSCENEDETECT_CHUNK_SECONDS))
    return [
        (
            float(index * _PYSCENEDETECT_CHUNK_SECONDS),
            min(
                float(_PYSCENEDETECT_CHUNK_SECONDS),
                max(0.0, duration - float(index * _PYSCENEDETECT_CHUNK_SECONDS)),
            ),
        )
        for index in range(chunk_count)
    ]


def _pyscenedetect_config(mode: str, duration: float) -> dict:
    """Tune internal scene analysis from Processing Depth without new UI switches."""
    if mode == "targeted":
        return {"downscale": 4 if duration >= 3600 else 3, "frame_skip": 2}
    if mode == "sampled":
        return {"downscale": 4, "frame_skip": 2}
    if duration >= 7200:
        return {"downscale": 4, "frame_skip": 1}
    if duration >= 3600:
        return {"downscale": 3, "frame_skip": 1}
    if duration >= _PYSCENEDETECT_LONG_SECONDS:
        return {"downscale": 2, "frame_skip": 0}
    return {"downscale": 1, "frame_skip": 0}


def _timecode_seconds(value) -> float | None:
    if value is None:
        return None
    get_seconds = getattr(value, "get_seconds", None)
    if callable(get_seconds):
        return float(get_seconds())
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _populate_scene_density(
    density: np.ndarray,
    length: int,
    timestamps: list[float],
    window: int = 10,
) -> None:
    for ts in timestamps:
        lo = max(0, int(ts) - window // 2)
        hi = min(length + 1, int(ts) + window // 2)
        density[lo:hi] += 1


def _sampled_scene_change_density(
    ffmpeg: str,
    video_path: Path,
    density: np.ndarray,
    length: int,
    video_duration: float | None,
    diagnostics: dict,
    target_windows: list[tuple[float, float]] | None = None,
    progress_callback=None,
) -> tuple[np.ndarray, dict]:
    """Scan short windows across long videos instead of decoding every frame."""
    global _LAST_SCENE_DETECTION

    diagnostics["engine"] = "ffmpeg"
    duration = float(video_duration or length or 0)
    if duration <= 0:
        diagnostics["status"] = "zero_changes"
        _LAST_SCENE_DETECTION = diagnostics
        return density, diagnostics

    window_seconds = 75 if duration >= 3600 else 45
    sample_count = 10 if duration >= 7200 else 8
    sample_count = max(1, min(sample_count, int(max(1, duration // max(window_seconds, 1)))))
    if _normalize_scene_mode(diagnostics.get("mode")) == "targeted" and target_windows:
        scan_windows = _merge_scene_windows(target_windows, duration=duration, max_windows=56)
    elif sample_count <= 1:
        scan_windows = [(0.0, float(window_seconds))]
    else:
        max_start = max(0.0, duration - window_seconds)
        scan_windows = [(float(start), float(window_seconds)) for start in np.linspace(0, max_start, sample_count).tolist()]

    commands = []
    timestamps = []
    stderr_parts = []
    started = time.monotonic()
    diagnostics["window_seconds"] = window_seconds
    diagnostics["sample_count"] = len(scan_windows)
    diagnostics["sample_starts"] = [round(float(start), 2) for start, _ in scan_windows]
    diagnostics["sample_windows"] = [
        {"start": round(float(start), 2), "duration": round(float(span), 2)}
        for start, span in scan_windows
    ]
    diagnostics["timeout_seconds"] = max(60, min(180, int(max(float(span) for _, span in scan_windows) * 2)))

    try:
        for start, scan_seconds in scan_windows:
            per_window_timeout = max(60, min(180, int(float(scan_seconds) * 2)))
            cmd = [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-threads", "4",
                "-ss", str(round(float(start), 3)),
                "-t", str(round(float(scan_seconds), 3)),
                "-i", str(video_path),
                "-map", "0:v:0",
                "-an", "-sn", "-dn",
                "-vf", "fps=1,select='gt(scene,0.3)',showinfo",
                "-fps_mode", "vfr",
                "-f", "null", "-",
            ]
            commands.append(cmd)
            r = _run(
                cmd,
                capture_output=True,
                text=True,
                timeout=per_window_timeout,
                errors="replace",
            )
            diagnostics["returncode"] = r.returncode
            stderr = r.stderr or ""
            stderr_parts.append(stderr)
            if r.returncode != 0:
                diagnostics["status"] = "ffmpeg_error"
                diagnostics["command"] = commands
                diagnostics["stderr_tail"] = _tail("\n".join(stderr_parts))
                diagnostics["elapsed_seconds"] = round(time.monotonic() - started, 3)
                _LAST_SCENE_DETECTION = diagnostics
                print(f"[!] Sampled scene detection ffmpeg error (code {r.returncode}); using audio only")
                return density, diagnostics
            for line in stderr.split("\n"):
                if "pts_time:" not in line:
                    continue
                try:
                    timestamps.append(float(start) + float(line.split("pts_time:")[1].split()[0]))
                except (ValueError, IndexError):
                    pass
            _notify_scene_progress(
                progress_callback,
                min(duration, float(start) + float(scan_seconds)),
                duration,
                str(diagnostics.get("mode") or "sampled"),
            )

        _populate_scene_density(density, length, timestamps)

        diagnostics["command"] = commands
        diagnostics["elapsed_seconds"] = round(time.monotonic() - started, 3)
        diagnostics["stderr_tail"] = _tail("\n".join(stderr_parts))
        diagnostics["timestamp_count"] = len(timestamps)
        diagnostics["nonzero_density_count"] = int(np.count_nonzero(density))
        diagnostics["max_density"] = float(density.max()) if len(density) else 0.0
        prefix = "targeted_" if _normalize_scene_mode(diagnostics.get("mode")) == "targeted" else "sampled_"
        diagnostics["status"] = f"{prefix}ok" if timestamps else f"{prefix}zero_changes"
        diagnostics["_scene_timestamps"] = timestamps
        _save_scene_cache(str(diagnostics.get("cache_key") or ""), diagnostics, timestamps)
        diagnostics.pop("_scene_timestamps", None)
        _LAST_SCENE_DETECTION = diagnostics
        if timestamps:
            print(f"[+] {prefix.rstrip('_').title()} scene detection found {len(timestamps)} scene-change frames")
        else:
            print(f"[i] {prefix.rstrip('_').title()} scene detection completed: zero changes above threshold")
        return density, diagnostics
    except subprocess.TimeoutExpired:
        diagnostics["command"] = commands
        diagnostics["elapsed_seconds"] = round(time.monotonic() - started, 3)
        diagnostics["status"] = "timeout"
        diagnostics["stderr_tail"] = _tail("\n".join(stderr_parts))
        _LAST_SCENE_DETECTION = diagnostics
        print("[!] Sampled scene detection timed out; using audio only")
        return density, diagnostics
    except FileNotFoundError:
        diagnostics["elapsed_seconds"] = round(time.monotonic() - started, 3)
        diagnostics["status"] = "ffmpeg_missing"
        _LAST_SCENE_DETECTION = diagnostics
        print("[!] Scene detection unavailable: ffmpeg not found; using audio only")
        return density, diagnostics


def _tail(text: str, limit: int = 2000) -> str:
    return text[-limit:] if len(text) > limit else text


def _notify_scene_progress(callback, current: float, total: float, mode: str = "full") -> None:
    if not callable(callback):
        return
    try:
        current = max(0.0, min(float(current or 0.0), float(total or 0.0)))
        total = max(0.0, float(total or 0.0))
    except (TypeError, ValueError):
        return
    label = "Sampling scenes" if _normalize_scene_mode(mode) == "sampled" else "Detecting scenes"
    try:
        callback(f"{label}: {_fmt_hms(current)} / {_fmt_hms(total)}")
    except Exception:
        pass


def _candidate(
    start: int,
    end: int,
    total: int,
    clip_duration: int,
    peak: int,
    score: float,
    audio: float,
    variance: float,
    scene: float,
    rank: int,
    kind: str,
) -> dict:
    start = int(max(0, min(start, max(0, total - 1))))
    end = int(max(start + 1, min(end, total)))
    if end - start < clip_duration and start > 0:
        start = int(max(0, end - clip_duration))
    if end - start < clip_duration and end < total:
        end = int(min(total, start + clip_duration))
    return {
        "start": start,
        "end": end,
        "duration": end - start,
        "score": score,
        "peak_time": int(peak),
        "candidate_rank": int(rank),
        "candidate_kind": kind,
        "detector_scores": {
            "audio": audio,
            "variance": variance,
            "scene": scene,
        },
    }


def _fmt(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_hms(seconds: float) -> str:
    seconds = int(max(0, round(float(seconds or 0))))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
