import subprocess
import tempfile
import time
import shutil
import numpy as np
from pydub import AudioSegment
from pathlib import Path

from audio_streams import build_audio_mix_filter, describe_audio_streams, get_audio_streams
from config import SUBTITLES_DIR
from subprocess_utils import run as _run


_LAST_SCENE_DETECTION: dict = {}


def find_viral_moments(
    video_path: Path,
    num_clips: int = 5,
    clip_duration: int = 30,
    min_gap: int = 15,
    candidate_multiplier: int = 1,
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

    # --- Scene change density ---
    print("[*] Analyzing scene changes...")
    scene_density, scene_info = _scene_change_density(
        video_path, len(energies), video_duration=float(total_seconds)
    )

    # --- Combine (normalize each to 0-1) ---
    def norm(a):
        r = a.max() - a.min()
        return (a - a.min()) / r if r > 1e-8 else np.zeros_like(a)

    audio_score = norm(smoothed)
    variance_score = norm(variance)
    scene_score = norm(scene_density[: len(smoothed)])
    combined = 0.45 * audio_score + 0.25 * variance_score + 0.30 * scene_score

    # --- Pick top non-overlapping peaks. The pipeline may ask for a larger
    # candidate pool, then transcript-rerank before rendering.
    target_count = max(num_clips, num_clips * max(1, int(candidate_multiplier)))
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
) -> tuple[np.ndarray, dict]:
    """Count scene changes per second using ffmpeg."""
    global _LAST_SCENE_DETECTION

    density = np.zeros(length + 1)
    ffmpeg = shutil.which("ffmpeg")
    timeout = _scene_timeout_seconds(video_duration or float(length))
    diagnostics = {
        "status": "unknown",
        "command": [],
        "elapsed_seconds": 0.0,
        "timeout_seconds": timeout,
        "returncode": None,
        "timestamp_count": 0,
        "nonzero_density_count": 0,
        "max_density": 0.0,
        "stderr_tail": "",
    }

    if not ffmpeg:
        diagnostics["status"] = "ffmpeg_missing"
        _LAST_SCENE_DETECTION = diagnostics
        print("[!] Scene detection unavailable: ffmpeg not found; using audio only")
        return density, diagnostics

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

        win = 10
        for ts in timestamps:
            lo = max(0, int(ts) - win // 2)
            hi = min(length + 1, int(ts) + win // 2)
            density[lo:hi] += 1

        diagnostics["timestamp_count"] = len(timestamps)
        diagnostics["nonzero_density_count"] = int(np.count_nonzero(density))
        diagnostics["max_density"] = float(density.max()) if len(density) else 0.0
        diagnostics["status"] = "ok" if timestamps else "zero_changes"
        _LAST_SCENE_DETECTION = diagnostics
        if timestamps:
            print(f"[+] Scene detection found {len(timestamps)} scene-change frames")
        else:
            print("[i] Scene detection completed: zero changes above threshold")
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
    """Load audio for moment detection, mixing multi-track sources when needed."""
    streams = get_audio_streams(video_path)
    if len(streams) < 2:
        return AudioSegment.from_file(str(video_path)), None

    mix = build_audio_mix_filter(video_path, mono=True)
    if not mix:
        return AudioSegment.from_file(str(video_path)), None

    filter_graph, out_label = mix
    SUBTITLES_DIR.mkdir(exist_ok=True)
    temp = tempfile.NamedTemporaryFile(
        prefix="analysis_mix_", suffix=".wav", dir=SUBTITLES_DIR, delete=False
    )
    temp_path = Path(temp.name)
    temp.close()

    print(f"[audio] Detection mix uses: {describe_audio_streams(video_path)}")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-filter_complex", filter_graph,
        "-map", f"[{out_label}]",
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(temp_path),
    ]
    r = _run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode == 0 and temp_path.exists():
        return AudioSegment.from_file(str(temp_path)), temp_path

    print(f"[audio] Detection mix failed; falling back to first audio stream:\n{r.stderr[-400:]}")
    try:
        temp_path.unlink(missing_ok=True)
    except OSError:
        pass
    return AudioSegment.from_file(str(video_path)), None


def _gameplay_preroll(clip_duration: int) -> int:
    """Bias gameplay clips toward the setup before the loud/visual peak."""
    if clip_duration <= 16:
        return max(8, clip_duration - 6)
    return min(30, max(12, int(round(clip_duration * 0.73))))


def _scene_timeout_seconds(video_duration: float) -> int:
    """Scale scene detection timeout for long/high-codec recordings."""
    return int(min(1200, max(180, video_duration * 0.16)))


def _tail(text: str, limit: int = 2000) -> str:
    return text[-limit:] if len(text) > limit else text


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
