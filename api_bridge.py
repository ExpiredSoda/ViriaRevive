"""
ApiBridge  –  Python ↔ JavaScript bridge for the ViriaRevive GUI.

Every public method is exposed to the frontend as  pywebview.api.<method>().
Long-running work runs on a daemon thread; progress is pushed back to
the UI via  window.evaluate_js()  which calls global JS callback functions.
"""

import functools
import hashlib
import http.server
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import webbrowser
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import yt_dlp

from config import (
    APP_DATA_DIR,
    BIN_DIR,
    CLIPS_DIR,
    CLIP_DURATION,
    CLIENT_SECRETS_FILE,
    CROP_VERTICAL,
    DOWNLOADS_DIR,
    FFMPEG_PRESET,
    MIN_GAP,
    MONTAGES_DIR,
    MUSIC_DIR,
    NUM_CLIPS,
    PERSONALIZATION_FILE,
    PERSONALIZATION_SCHEMA_VERSION,
    PROCESSING_HISTORY_FILE,
    PROCESSING_HISTORY_SCHEMA_VERSION,
    RUN_LEARNING_FILE,
    RUN_LEARNING_SCHEMA_VERSION,
    STATE_FILE,
    STATE_SCHEMA_VERSION,
    SUBTITLE_PLACEMENT,
    SUBTITLE_STYLE,
    SUBTITLES_DIR,
    VIDEO_CRF,
    VOICE_PROFILE_FILE,
    WHISPER_LANGUAGE,
    WHISPER_MODEL,
)

MIN_CLIP_DURATION_SECONDS = 10
MAX_CLIP_DURATION_SECONDS = 180
MIN_MIN_GAP_SECONDS = 5
MAX_MIN_GAP_SECONDS = 60
CANDIDATE_TRANSCRIPT_PROBE_MAX_SECONDS = 90
CANDIDATE_TRANSCRIPT_CHUNK_MAX_SECONDS = 180
CANDIDATE_TRANSCRIPT_CHUNK_MAX_FILES = 8
ETA_PROGRESS_STAGES = ("download", "detect", "candidates", "clips")
ETA_STAGE_TIMING_KEYS = {
    "download": ("download", "game_context"),
    "detect": ("detect", "visual_analysis"),
    "candidates": ("candidate_analysis", "multimodal_analysis"),
    "clips": ("final_render", "auto_metadata"),
}
from detector import find_viral_moments, get_last_scene_detection_diagnostics
from transcriber import transcribe_clip, transcribe_clips
from subtitler import (
    generate_subtitles,
    get_available_styles,
    normalize_subtitle_placement,
    resolve_subtitle_placement,
    subtitles_are_enabled,
)
from clipper import (
    extract_clip, extract_audio_clip,
    add_background_music, apply_video_effect, get_effects_list,
)
from cropper import get_crop_params_dynamic, get_dimensions
from audio_streams import (
    get_audio_streams,
    get_last_audio_stream_diagnostics,
    pick_voice_stream_ordinal,
)
from candidate_ranker import (
    attach_ai_moment_classification,
    apply_ai_moment_scoring,
    apply_learned_scoring,
    apply_moment_category_scoring,
    apply_multi_signal_ai_scoring,
    apply_voice_profile_scoring,
    build_learning_terms,
    build_learning_prompt_context,
    build_learning_status,
    build_ai_moment_ranking_report,
    build_moment_category_ranking_report,
    build_multi_signal_ai_ranking_report,
    build_shadow_scoring_report,
    build_voice_profile_ranking_report,
    build_voice_profile_shadow_report,
    compact_ai_moment_classification,
    evaluate_candidate,
    needs_stream_retry,
    normalize_commentary_subtitle_policy,
    normalize_detection_preference,
    quality_floor_for_preference,
    select_best_candidates,
    select_near_quality_fallback_candidates,
    LEARNED_SELECTION_MAX_ADJUSTMENT,
    AI_MOMENT_SELECTION_MAX_ADJUSTMENT,
    MOMENT_CATEGORY_SELECTION_MAX_ADJUSTMENT,
    GAME_CONTEXT_SELECTION_MAX_ADJUSTMENT,
    MULTI_SIGNAL_AI_MAX_NEGATIVE_ADJUSTMENT,
    MULTI_SIGNAL_AI_MAX_POSITIVE_ADJUSTMENT,
    VOICE_PROFILE_SELECTION_MAX_ADJUSTMENT,
    write_debug_report,
)
from subprocess_utils import CancelledError
from speech_stream_selector import (
    get_last_speech_stream_selection,
    profile_words_for_stream,
    select_speech_stream,
    should_accept_alternate_stream,
)
from downloader import resolve_downloaded_path
from title_generator import (
    DEFAULT_MODEL,
    classify_moment_ai,
    compose_description,
    generate_ai_description_body,
    generated_description_body,
    generate_title,
    generate_tags,
    generate_titles_batch,
    format_short_title,
    list_ollama_models,
    ensure_model,
    is_ollama_model_ready,
    ollama_status,
    summarize_clip_context,
    recommended_hashtags,
    sanitize_creator_title_context,
    OLLAMA_DOWNLOAD_URL,
    OLLAMA_WINDOWS_DOCS_URL,
)
from uploader import (
    upload_to_youtube,
    disconnect,
    list_channels,
    add_account,
    list_accounts,
)
from run_learning import (
    append_event as append_run_learning_event,
    append_run_summary,
    build_clip_deleted_event,
    build_feedback_event,
    build_metadata_event,
    build_montage_feedback_event,
    compact_clip_snapshot,
    empty_run_learning,
    redacted_summary as run_learning_redacted_summary,
    sanitize_run_learning,
)
from montage_storyboard import (
    build_candidate_audit,
    build_storyboard_from_audit,
    write_candidate_audit,
    write_storyboard,
)
from montage_renderer import render_draft_montage
from version import APP_DESCRIPTION, APP_NAME, APP_VERSION, APP_VERSION_DISPLAY
from voice_profile import (
    MIN_VOICE_PROFILE_SAMPLES,
    MIN_VOICE_PROFILE_TOTAL_ACTIVE_SECONDS,
    empty_voice_profile,
    extract_voice_features,
    sanitize_voice_profile,
    score_voice_profile,
    update_voice_profile,
    voice_profile_ready,
    voice_profile_status,
)
from visual_diagnostics import (
    analyze_candidate_visuals,
    disabled_visual_diagnostics,
)
from multimodal_analysis import (
    DEFAULT_VISION_MODEL,
    MULTIMODAL_SELECTION_MAX_ADJUSTMENT,
    analyze_candidate_frames_with_ollama,
    apply_multimodal_scoring,
    build_multimodal_ranking_report,
    ollama_vision_status,
    preflight_ollama_vision_model,
)
from game_context import get_game_context, compact_game_context_for_prompt, normalize_game_title
from game_identity import resolve_game_identity


YOUTUBE_CREDENTIALS_URL = "https://console.cloud.google.com/apis/credentials"
FFMPEG_DOWNLOAD_URL = "https://ffmpeg.org/download.html"
SCHEDULER_MISSED_GRACE = timedelta(minutes=10)
SCHEDULE_PUBLISH_BUFFER = timedelta(minutes=10)
PROCESSING_DEPTHS = {"fast", "balanced", "deep"}
GENERATION_MODES = {"clips", "montage"}
MONTAGE_TEMPLATES = {"panic", "funny", "failure", "combat", "story", "tutorial", "atmosphere", "custom"}
VIDEO_FILE_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm"}
OLLAMA_STARTUP_PATHS = (
    Path("A:/Ollama/ollama.exe"),
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
    Path(os.environ.get("ProgramFiles", "")) / "Ollama" / "ollama.exe",
    Path(os.environ.get("ProgramFiles(x86)", "")) / "Ollama" / "ollama.exe",
)
VOICE_ENROLLMENT_MIN_WORDS = 12
VOICE_ENROLLMENT_MIN_CREATOR_RATIO = 0.70
VOICE_ENROLLMENT_MIN_CREATOR_CONFIDENCE = 0.65
VOICE_ENROLLMENT_MAX_GAME_RATIO = 0.15
VOICE_ENROLLMENT_DUAL_TRACK_MIN_CONFIDENCE = 0.75
VOICE_ENROLLMENT_CREATOR_STREAM_REASONS = {
    "creator_phrase_signal",
    "mic_creator_signal_over_more_words",
    "mic_title_hint_and_speech",
    "mic_title_hint_over_more_words",
}
SCHEDULE_SUCCESS_FIELDS = {
    "uploaded",
    "uploaded_at",
    "youtube_id",
    "youtube_url",
    "upload_state",
    "send_status",
}
SCHEDULE_BACKEND_STATUS_FIELDS = {
    "scheduler_status",
    "scheduler_note",
    "failure_count",
    "last_error",
    "last_failed_at",
    "retry_after",
    "missed_at",
    "upload_attempt_id",
    "upload_attempt_fingerprint",
    "upload_attempt_started_at",
    "upload_attempt_trigger",
    "upload_unknown_at",
}


def _normalize_bool_setting(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, dict):
        return _normalize_bool_setting(value.get("enabled"), default)
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _safe_int_value(value, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _normalize_clip_duration(value, default: int = CLIP_DURATION) -> int:
    duration = _safe_int_value(value, default)
    if duration <= 0:
        duration = int(default or CLIP_DURATION)
    return max(MIN_CLIP_DURATION_SECONDS, min(MAX_CLIP_DURATION_SECONDS, duration))


def _normalize_min_gap(value, default: int = MIN_GAP) -> int:
    try:
        gap = int(value)
    except (TypeError, ValueError):
        gap = int(default or MIN_GAP)
    if gap <= 0:
        gap = int(default or MIN_GAP)
    return max(MIN_MIN_GAP_SECONDS, min(MAX_MIN_GAP_SECONDS, gap))


def _local_naive_datetime(value: datetime) -> datetime:
    """Normalize aware datetimes to local-naive values for persisted scheduler state."""
    if value.tzinfo is not None:
        return value.astimezone().replace(tzinfo=None)
    return value


def _parse_iso_datetime(value) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _safe_float_value(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _normalize_processing_depth(value) -> str:
    depth = str(value or "balanced").strip().lower().replace("_", "-")
    aliases = {
        "normal": "balanced",
        "default": "balanced",
        "quality": "deep",
        "deep-analysis": "deep",
        "deep analysis": "deep",
    }
    depth = aliases.get(depth, depth)
    return depth if depth in PROCESSING_DEPTHS else "balanced"


def _normalize_generation_mode(value) -> str:
    mode = str(value or "clips").strip().lower().replace("_", "-")
    return mode if mode in GENERATION_MODES else "clips"


def _sanitize_montage_prompt(value) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]


def _normalize_montage_template(value) -> str:
    template = str(value or "panic").strip().lower()
    template = re.sub(r"[\s_]+", "-", template)
    return template if template in MONTAGE_TEMPLATES else "panic"


def _normalize_montage_duration(value) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = 60
    return seconds if seconds in {30, 45, 60, 90} else 60


def _normalize_montage_count(value) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = 1
    return max(1, min(5, count))


def _normalize_montage_settings(value) -> dict:
    raw = value if isinstance(value, dict) else {}
    return {
        "template": _normalize_montage_template(raw.get("template")),
        "target_duration": _normalize_montage_duration(raw.get("target_duration")),
        "count": _normalize_montage_count(raw.get("count")),
        "prompt": _sanitize_montage_prompt(raw.get("prompt")),
    }


def _montage_story_shape_for_template(template: str) -> str:
    template = _normalize_montage_template(template)
    return {
        "tutorial": "tutorial_story",
        "story": "story_reveal",
        "atmosphere": "atmosphere_build",
        "failure": "failure_recovery_payoff",
        "combat": "combat_escalation",
        "funny": "setup_escalate_punchline",
        "panic": "hook_escalate_payoff",
        "custom": "hook_escalate_payoff",
    }.get(template, "hook_escalate_payoff")


def _category_snapshot_from_selection(item: dict, moment: dict) -> tuple[str | None, dict | None]:
    selection_moment = item.get("selection_moment") if isinstance(item, dict) else None
    if not isinstance(selection_moment, dict):
        selection_moment = moment if isinstance(moment, dict) else {}
    categories = selection_moment.get("moment_categories")
    if not isinstance(categories, dict) and isinstance(item, dict):
        categories = item.get("moment_categories")
    primary = selection_moment.get("primary_category")
    if primary is None and isinstance(categories, dict):
        primary = categories.get("primary")
    if primary is None and isinstance(item, dict):
        primary = item.get("primary_category")
    return primary, copy.deepcopy(categories) if isinstance(categories, dict) else None


def _candidate_model_for_depth(depth: str, model: str | None) -> str:
    selected = str(model or WHISPER_MODEL or "base").strip() or "base"
    if depth == "fast" and selected in {"small", "medium", "large", "large-v2", "large-v3"}:
        return "base"
    if depth == "deep" and selected in {"medium", "large", "large-v2", "large-v3"}:
        return "small"
    return selected


def _processing_depth_profile(depth: str, detection_preference: str, video_duration: float | None) -> dict:
    depth = _normalize_processing_depth(depth)
    preference = normalize_detection_preference(detection_preference)
    duration = float(video_duration or 0)
    if depth == "fast":
        multiplier = 4 if preference == "quality" else 3
        scene_mode = "sampled" if duration and duration <= 1800 else "skip"
        return {
            "depth": depth,
            "candidate_multiplier": multiplier,
            "scene_mode": scene_mode,
            "candidate_pool_cap": 36,
            "visual_diagnostics": False,
            "multimodal_analysis": False,
            "moment_category_ranking": False,
            "ai_moment_classification": False,
            "voice_profile_ranking": False,
            "visual_max_candidates": 24,
            "multimodal_max_candidates": 0,
        }
    if depth == "deep":
        multiplier = 8 if preference == "quality" else 7
        if preference == "quantity":
            multiplier = 6
        return {
            "depth": depth,
            "candidate_multiplier": multiplier,
            "scene_mode": "targeted" if duration >= 1800 else "full",
            "candidate_pool_cap": 72,
            "visual_diagnostics": True,
            "multimodal_analysis": True,
            "moment_category_ranking": True,
            "ai_moment_classification": True,
            "voice_profile_ranking": None,
            "visual_max_candidates": 64,
            "multimodal_max_candidates": 12,
        }
    multiplier = 6 if preference == "quality" else 5
    return {
        "depth": "balanced",
        "candidate_multiplier": multiplier,
        "scene_mode": "sampled" if duration >= 1200 else "full",
        "candidate_pool_cap": 56,
        "visual_diagnostics": None,
        "multimodal_analysis": False,
        "moment_category_ranking": True,
        "ai_moment_classification": None,
        "voice_profile_ranking": None,
        "visual_max_candidates": 48,
        "multimodal_max_candidates": 0,
    }


def _candidate_analysis_limit(
    depth: str,
    detection_preference: str,
    num_clips: int,
    candidate_count: int,
) -> int:
    """Return how many candidates should get full transcript ranking."""
    count = max(0, int(candidate_count or 0))
    if count <= 0:
        return 0
    depth = _normalize_processing_depth(depth)
    preference = normalize_detection_preference(detection_preference)
    target = max(1, int(num_clips or 1))
    if depth == "fast":
        multiplier = 4 if preference == "quality" else 3
        return min(count, max(target + 4, target * multiplier, 10))
    if depth == "balanced" and preference == "quantity":
        return min(count, max(target * 6, 24))
    return count


def _shortlist_candidates_for_transcription(
    candidates: list[dict],
    *,
    depth: str,
    detection_preference: str,
    num_clips: int,
    min_gap: int,
) -> list[dict]:
    """Cheaply reduce candidate transcription volume while keeping diversity."""
    limit = _candidate_analysis_limit(depth, detection_preference, num_clips, len(candidates))
    if limit <= 0 or limit >= len(candidates):
        return list(candidates)

    def candidate_score(row: dict) -> float:
        detector_scores = row.get("detector_scores") if isinstance(row.get("detector_scores"), dict) else {}
        scene_score = detector_scores.get("scene", row.get("scene", row.get("scene_score", 0.0)))
        return _safe_float_value(row.get("score"), 0.0) + 0.08 * _safe_float_value(scene_score, 0.0)

    selected: list[dict] = []
    min_distance = max(MIN_MIN_GAP_SECONDS, int(min_gap or MIN_GAP))
    for row in sorted(candidates, key=candidate_score, reverse=True):
        peak = _safe_float_value(row.get("peak_time", row.get("start")), 0.0)
        if all(abs(peak - _safe_float_value(other.get("peak_time", other.get("start")), 0.0)) >= min_distance for other in selected):
            selected.append(row)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        seen = {id(row) for row in selected}
        for row in sorted(candidates, key=candidate_score, reverse=True):
            if id(row) not in seen:
                selected.append(row)
                seen.add(id(row))
            if len(selected) >= limit:
                break
    selected_ids = {id(row) for row in selected}
    return [row for row in candidates if id(row) in selected_ids]


def _candidate_transcription_chunks(
    paths: list[Path],
    durations_by_path: dict[str, float] | None = None,
    *,
    max_seconds: float = CANDIDATE_TRANSCRIPT_CHUNK_MAX_SECONDS,
    max_files: int = CANDIDATE_TRANSCRIPT_CHUNK_MAX_FILES,
) -> list[list[Path]]:
    """Group candidate probe WAVs into bounded Whisper batches."""
    clean_paths = [Path(path) for path in paths or []]
    if not clean_paths:
        return []
    durations_by_path = durations_by_path or {}
    max_seconds = max(1.0, float(max_seconds or CANDIDATE_TRANSCRIPT_CHUNK_MAX_SECONDS))
    max_files = max(1, int(max_files or CANDIDATE_TRANSCRIPT_CHUNK_MAX_FILES))

    chunks: list[list[Path]] = []
    current: list[Path] = []
    current_seconds = 0.0

    for path in clean_paths:
        raw_duration = durations_by_path.get(str(path))
        duration = _safe_float_value(raw_duration, CANDIDATE_TRANSCRIPT_PROBE_MAX_SECONDS)
        duration = max(1.0, min(float(duration), float(CANDIDATE_TRANSCRIPT_PROBE_MAX_SECONDS)))
        if current and (
            len(current) >= max_files
            or current_seconds + duration > max_seconds
        ):
            chunks.append(current)
            current = []
            current_seconds = 0.0
        current.append(path)
        current_seconds += duration

    if current:
        chunks.append(current)
    return chunks


def _transcribe_candidate_wav_chunks(
    paths: list[Path],
    durations_by_path: dict[str, float] | None = None,
    *,
    model_size: str,
    language: str | None,
    cancel_check=None,
    progress_callback=None,
) -> tuple[dict[str, list], int]:
    """Transcribe candidate probe WAVs in bounded batches and preserve partials."""
    chunks = _candidate_transcription_chunks(paths, durations_by_path)
    words_by_path: dict[str, list] = {}
    total_chunks = len(chunks)
    for chunk_index, chunk in enumerate(chunks, 1):
        if cancel_check and cancel_check():
            raise CancelledError("Pipeline cancelled")
        chunk_seconds = sum(float((durations_by_path or {}).get(str(path), 0.0) or 0.0) for path in chunk)
        if progress_callback:
            progress_callback(chunk_index, total_chunks, chunk, chunk_seconds)
        chunk_words = transcribe_clips(chunk, model_size=model_size, language=language)
        for path, words in zip(chunk, chunk_words):
            words_by_path[str(path)] = words
    return words_by_path, total_chunks


def _crop_tracking_profile(depth: str) -> dict:
    depth = _normalize_processing_depth(depth)
    if depth == "fast":
        return {"sample_count": 12, "min_sample_rate": 1.0}
    if depth == "balanced":
        return {"sample_count": 24, "min_sample_rate": 2.0}
    return {"sample_count": 50, "min_sample_rate": 4.0}


def _feature_status_label(enabled: bool, depth_override) -> str:
    if depth_override is True:
        return "On by depth"
    if depth_override is False:
        return "Inactive in Fast"
    return "On" if enabled else "Off"


def _depth_feature_statuses(
    depth: str,
    depth_profile: dict,
    *,
    visual_requested: bool,
    ai_requested: bool,
    category_requested: bool,
    voice_requested: bool,
    multimodal_requested: bool = False,
    voice_status: dict | None = None,
) -> dict:
    depth = _normalize_processing_depth(depth)
    scene_mode = str(depth_profile.get("scene_mode") or "unknown")
    scene_label = {
        "skip": "Inactive in Fast",
        "sampled": "Sampled",
        "targeted": "Targeted",
        "full": "Full",
    }.get(scene_mode, scene_mode.capitalize())
    scene_reason = {
        "skip": "Fast keeps scene scanning off for long recordings.",
        "sampled": "Fast/Balanced samples scene changes instead of scanning every frame.",
        "targeted": "Deep inspects likely high-value regions instead of blindly scanning the full file.",
        "full": "This depth can scan the source for scene changes.",
    }.get(scene_mode, "Scene detection mode is decided by Processing Depth.")

    def status(name: str, requested: bool, override, on_reason: str, off_reason: str) -> dict:
        effective = bool(override) if override is not None else bool(requested)
        inactive_reason = "fast_depth" if override is False and depth == "fast" else ""
        reason = "Fast mode keeps this heavier feature inactive." if inactive_reason else (on_reason if effective else off_reason)
        return {
            "name": name,
            "requested": bool(requested),
            "effective": bool(effective),
            "depth_override": override,
            "label": _feature_status_label(effective, override),
            "inactive_reason": inactive_reason,
            "reason": reason,
        }

    voice_status = voice_status if isinstance(voice_status, dict) else {}
    voice = status(
        "voice_profile_ranking",
        voice_requested,
        depth_profile.get("voice_profile_ranking"),
        "Voice ranking can make a tiny capped nudge when the profile is ready.",
        "Voice ranking is off until you opt in.",
    )
    if voice["effective"] and not voice_status.get("ranking_active"):
        if voice_status.get("enrolled"):
            voice["label"] = "Ready"
            voice["reason"] = "Voice ranking is enabled and will influence the next eligible run."
        else:
            voice["label"] = "Needs samples"
            voice["reason"] = "Build the local voice profile from liked creator-commentary clips before voice ranking can help."
            voice["inactive_reason"] = voice["inactive_reason"] or "needs_voice_samples"

    return {
        "scene_detection": {
            "name": "scene_detection",
            "requested": True,
            "effective": scene_mode != "skip",
            "depth_override": scene_mode,
            "mode": scene_mode,
            "label": scene_label,
            "inactive_reason": "fast_depth" if scene_mode == "skip" and depth == "fast" else "",
            "reason": scene_reason,
        },
        "visual_analysis": status(
            "visual_analysis",
            visual_requested,
            depth_profile.get("visual_diagnostics"),
            "Visual analysis samples frames to help local labels understand the clip.",
            "Visual analysis is off in settings.",
        ),
        "vision_context": status(
            "vision_context",
            multimodal_requested,
            depth_profile.get("multimodal_analysis"),
            "Deep Analysis can ask a local Ollama vision model what the sampled frames show.",
            "Vision context is only automatic in Deep Analysis when a local vision model is installed.",
        ),
        "ai_moment_labels": status(
            "ai_moment_labels",
            ai_requested,
            depth_profile.get("ai_moment_classification"),
            "AI labels explain selected moments and enrich titles/metadata.",
            "AI labels are off in settings.",
        ),
        "moment_label_ranking": status(
            "moment_label_ranking",
            category_requested,
            depth_profile.get("moment_category_ranking"),
            "Moment labels can make a tiny capped ranking adjustment.",
            "Moment-label ranking is off in settings.",
        ),
        "voice_profile_ranking": voice,
    }


def _normalize_subtitle_style(value, default: str = SUBTITLE_STYLE) -> str:
    style = str(value or default or SUBTITLE_STYLE).strip().lower()
    allowed = {str(item.get("id", "")).strip().lower() for item in get_available_styles()}
    if style in allowed:
        return style
    fallback = str(default or SUBTITLE_STYLE).strip().lower()
    return fallback if fallback in allowed else SUBTITLE_STYLE


def _normalize_audio_source_settings(settings: dict | None) -> dict:
    """Sanitize user audio-source preferences before ffmpeg mapping."""
    settings = settings or {}
    raw = settings.get("audio_source")
    audio_source = raw if isinstance(raw, dict) else {}

    mode = str(
        audio_source.get("mode")
        or settings.get("audio_source_mode")
        or "auto"
    ).strip().lower()
    if mode not in {"auto", "stream"}:
        mode = "auto"

    stream_raw = audio_source.get("stream", settings.get("transcription_audio_stream"))
    stream = None
    try:
        if stream_raw not in (None, "", "auto"):
            parsed = int(stream_raw)
            if 0 <= parsed <= 31:
                stream = parsed
    except (TypeError, ValueError):
        stream = None

    if mode == "stream" and stream is None:
        mode = "auto"

    commentary_guard = audio_source.get(
        "commentary_guard",
        settings.get("mixed_audio_commentary_guard", True),
    )
    if isinstance(commentary_guard, str):
        commentary_guard = commentary_guard.strip().lower() not in {"0", "false", "no", "off"}
    subtitle_policy = (
        audio_source.get("subtitle_policy")
        or audio_source.get("subtitle_focus")
        or settings.get("mixed_audio_subtitle_policy")
        or settings.get("commentary_subtitle_policy")
    )
    return {
        "mode": mode,
        "stream": stream,
        "commentary_guard": bool(commentary_guard),
        "subtitle_policy": normalize_commentary_subtitle_policy(subtitle_policy),
    }


def _public_audio_stream(stream: dict) -> dict:
    ordinal = int(stream.get("ordinal", 0))
    title = stream.get("title") or f"Track {ordinal + 1}"
    title_lower = str(title).lower()
    if any(hint in title_lower for hint in ("mic", "microphone", "voice", "commentary", "narration")):
        role = "commentary"
    elif any(hint in title_lower for hint in ("game", "desktop", "system", "capture")):
        role = "game"
    else:
        role = "unknown"
    return {
        "ordinal": ordinal,
        "index": stream.get("index"),
        "title": title,
        "codec": stream.get("codec") or "",
        "channels": stream.get("channels"),
        "layout": stream.get("layout") or "",
        "language": stream.get("language") or "",
        "likely_role": role,
    }


def _selected_audio_stream_profile(
    audio_source_debug: dict | None,
    selected_stream=None,
    retry_report: dict | None = None,
) -> dict | None:
    audio_source_debug = audio_source_debug if isinstance(audio_source_debug, dict) else {}
    try:
        selected_ordinal = int(selected_stream)
    except (TypeError, ValueError):
        selected_ordinal = None

    retry_report = retry_report if isinstance(retry_report, dict) else {}
    accepted_stream = retry_report.get("accepted_stream")
    try:
        accepted_ordinal = int(accepted_stream)
    except (TypeError, ValueError):
        accepted_ordinal = None
    if selected_ordinal is not None and accepted_ordinal == selected_ordinal:
        for attempt in retry_report.get("attempts") or []:
            if not isinstance(attempt, dict):
                continue
            try:
                attempt_stream = int(attempt.get("stream"))
            except (TypeError, ValueError):
                continue
            if attempt_stream == selected_ordinal:
                profile = copy.deepcopy(attempt)
                profile["ordinal"] = selected_ordinal
                profile["title"] = profile.get("title") or f"0:a:{selected_ordinal}"
                return profile

    selection = audio_source_debug.get("stream_selection")
    selection = selection if isinstance(selection, dict) else {}
    for profile in selection.get("stream_profiles") or []:
        if not isinstance(profile, dict):
            continue
        try:
            ordinal = int(profile.get("ordinal"))
        except (TypeError, ValueError):
            continue
        if selected_ordinal is not None and ordinal == selected_ordinal:
            enriched = copy.deepcopy(profile)
            selected_reason = selection.get("selected_reason") or audio_source_debug.get("selected_reason")
            selected_confidence = selection.get("confidence", audio_source_debug.get("selected_confidence"))
            if selected_reason and not enriched.get("selected_reason"):
                enriched["selected_reason"] = str(selected_reason)
            if selected_confidence is not None and enriched.get("selected_confidence") is None:
                try:
                    enriched["selected_confidence"] = round(
                        max(0.0, min(1.0, float(selected_confidence))),
                        4,
                    )
                except (TypeError, ValueError):
                    pass
            return enriched

    try:
        selection_stream = int(selection.get("selected_stream"))
    except (TypeError, ValueError):
        selection_stream = None
    if selected_ordinal is not None and selection_stream == selected_ordinal:
        title = selection.get("selected_title")
        if not title:
            for stream in audio_source_debug.get("streams") or []:
                if not isinstance(stream, dict):
                    continue
                try:
                    stream_ordinal = int(stream.get("ordinal"))
                except (TypeError, ValueError):
                    continue
                if stream_ordinal == selected_ordinal:
                    title = str(stream.get("title") or "")
                    break
        title = str(title or f"0:a:{selected_ordinal}")
        title_lower = title.lower()
        voice_hints = [
            hint for hint in ("mic", "microphone", "voice", "commentary", "narration")
            if hint in title_lower
        ]
        game_hints = [
            hint for hint in ("game", "desktop", "system", "capture")
            if hint in title_lower
        ]
        selected_reason = str(
            selection.get("selected_reason") or audio_source_debug.get("selected_reason") or ""
        )
        try:
            selected_confidence = float(
                selection.get("confidence", audio_source_debug.get("selected_confidence", 0.0)) or 0.0
            )
        except (TypeError, ValueError):
            selected_confidence = 0.0
        selection_status = str(selection.get("status") or audio_source_debug.get("status") or "").lower()
        selection_mode = str(selection.get("mode") or audio_source_debug.get("mode") or "").lower()
        creator_selected = (
            "creator" in selected_reason.lower()
            and selected_confidence >= 0.55
            and selection_status not in {"forced", "manual", "manual_stream"}
            and selection_mode != "manual_stream"
        )
        return {
            "ordinal": selected_ordinal,
            "title": title,
            "words": selection.get("selected_words", 0),
            "selected_reason": selected_reason,
            "selected_confidence": round(max(0.0, min(1.0, selected_confidence)), 4),
            "voice_title_hints": voice_hints,
            "game_title_hints": game_hints,
            "creator_likeness_score": 0.62 if creator_selected else 0.0,
            "natural_dialogue_score": 3.6 if creator_selected else 0.0,
            "scripted_game_score": 0.0,
            "acoustic_game_bed_score": 0.0,
            "lyric_likelihood": 0.0,
            "creator_exception_score": 0.0,
        }

    for stream in audio_source_debug.get("streams") or []:
        if not isinstance(stream, dict):
            continue
        try:
            ordinal = int(stream.get("ordinal"))
        except (TypeError, ValueError):
            continue
        if selected_ordinal is not None and ordinal == selected_ordinal:
            title = str(stream.get("title") or f"Track {ordinal + 1}")
            title_lower = title.lower()
            voice_hints = [
                hint for hint in ("mic", "microphone", "voice", "commentary", "narration")
                if hint in title_lower
            ]
            game_hints = [
                hint for hint in ("game", "desktop", "system", "capture")
                if hint in title_lower
            ]
            likely_role = str(stream.get("likely_role") or "")
            return {
                "ordinal": ordinal,
                "title": title,
                "words": 0,
                "voice_title_hints": voice_hints,
                "game_title_hints": game_hints,
                "metadata_hint_only": True,
                "creator_likeness_score": 0.18 if likely_role == "commentary" or voice_hints else 0.0,
                "natural_dialogue_score": 0.0,
                "scripted_game_score": 0.0,
                "acoustic_game_bed_score": 0.0,
                "lyric_likelihood": 0.0,
                "creator_exception_score": 0.0,
            }
    return None


def _audio_stream_selection_summary(
    audio_source_debug: dict | None,
    selected_stream=None,
    retry_report: dict | None = None,
) -> dict:
    audio_source_debug = audio_source_debug if isinstance(audio_source_debug, dict) else {}
    selection = audio_source_debug.get("stream_selection")
    selection = selection if isinstance(selection, dict) else {}
    resolved_stream = selected_stream
    if resolved_stream is None:
        resolved_stream = audio_source_debug.get("selected_stream")
    selected_profile = _selected_audio_stream_profile(
        audio_source_debug,
        selected_stream=resolved_stream,
        retry_report=retry_report,
    )
    summary = {
        "schema_version": selection.get("schema_version", 1),
        "status": selection.get("status") or ("manual" if audio_source_debug.get("mode") == "stream" else "unknown"),
        "mode": selection.get("mode") or audio_source_debug.get("mode") or "auto",
        "selected_stream": resolved_stream,
        "selected_title": (selected_profile or {}).get("title") or selection.get("selected_title"),
        "selected_words": (selected_profile or {}).get("words") or selection.get("selected_words"),
        "selected_reason": (
            selection.get("selected_reason")
            or audio_source_debug.get("selected_reason")
            or ("user_selected_stream" if audio_source_debug.get("mode") == "stream" else None)
        ),
        "runner_up_stream": selection.get("runner_up_stream") or audio_source_debug.get("runner_up_stream"),
        "runner_up_title": selection.get("runner_up_title"),
        "confidence": selection.get("confidence", audio_source_debug.get("selected_confidence")),
    }
    return {key: value for key, value in summary.items() if value is not None}


def _clip_speech_policy_summary(moment: dict | None) -> dict:
    """Summarize which speech source is safe for subtitles and metadata."""
    moment = moment if isinstance(moment, dict) else {}
    audio = moment.get("audio_source") if isinstance(moment.get("audio_source"), dict) else {}
    selection = moment.get("stream_selection") if isinstance(moment.get("stream_selection"), dict) else {}
    if not selection and isinstance(audio.get("stream_selection"), dict):
        selection = audio.get("stream_selection")

    policy = normalize_commentary_subtitle_policy(
        audio.get("subtitle_policy")
        or moment.get("subtitle_policy")
        or "creator"
    )
    selected_words = _safe_int_value(
        moment.get("subtitle_word_count")
        if moment.get("subtitle_word_count") is not None
        else moment.get("word_count"),
        0,
    )
    final_words = _safe_int_value(moment.get("word_count"), 0)
    analysis_words = _safe_int_value(moment.get("analysis_word_count"), 0)
    transcript = str(moment.get("transcript") or "").strip()
    has_selected_track_speech = bool(selected_words > 0 or final_words > 0 or transcript)

    stream_count = _safe_int_value(audio.get("stream_count"), 0)
    selected_stream = audio.get("selected_stream")
    if selected_stream is None:
        selected_stream = selection.get("selected_stream")
    if selected_stream is None:
        selected_stream = moment.get("speech_stream")
    track_aware = bool(audio or selection or stream_count > 1 or selected_stream is not None)

    selected_confidence = _safe_float_value(
        audio.get("selected_confidence")
        if audio.get("selected_confidence") is not None
        else selection.get("confidence"),
        None,
    )
    selected_title = (
        str(audio.get("selected_title") or "").strip()
        or str(selection.get("selected_title") or "").strip()
    )
    selected_reason = (
        str(audio.get("selected_reason") or "").strip()
        or str(selection.get("selected_reason") or "").strip()
    )
    selected_status = str(selection.get("status") or audio.get("status") or "").strip()
    render_audio = str(audio.get("render_audio") or "").strip()
    if not render_audio:
        render_audio = "mixed_all_streams" if stream_count > 1 else "source_audio"

    mixed_speech_without_selected = bool(
        policy == "creator"
        and track_aware
        and not has_selected_track_speech
        and analysis_words > max(selected_words, final_words, 0)
    )
    no_creator_speech = bool(
        policy == "creator"
        and track_aware
        and not has_selected_track_speech
    )
    status = "ok"
    warning = ""
    if no_creator_speech:
        status = "no_selected_commentary_speech"
        warning = "No commentary transcript was found on the selected track."
    if mixed_speech_without_selected:
        warning = "Selected commentary track had no speech, but another analysis path saw speech."

    metadata_backfill_blocked = bool(no_creator_speech)
    metadata_transcript_source = "selected_commentary_track"
    if policy == "all":
        metadata_transcript_source = "all_speech_policy"
    elif policy == "game":
        metadata_transcript_source = "game_audio_policy"
    elif no_creator_speech:
        metadata_transcript_source = "none_selected_track"

    return {
        "schema_version": 1,
        "subtitle_policy": policy,
        "status": status,
        "warning": warning,
        "metadata_transcript_source": metadata_transcript_source,
        "metadata_backfill_blocked": metadata_backfill_blocked,
        "selected_track_has_speech": has_selected_track_speech,
        "selected_track_word_count": max(selected_words, final_words),
        "analysis_word_count": analysis_words,
        "selected_stream": selected_stream,
        "selected_title": selected_title,
        "selected_reason": selected_reason,
        "selected_status": selected_status,
        "selected_confidence": selected_confidence,
        "stream_count": stream_count,
        "render_audio": render_audio,
        "mixed_speech_without_selected_track": mixed_speech_without_selected,
        "track_aware": track_aware,
    }


def _creator_caption_speech_missing(moment: dict | None, *, subtitle_enabled: bool, subtitle_policy: str | None) -> bool:
    """Return true when a creator-caption render has no selected creator speech."""
    if not subtitle_enabled:
        return False
    if normalize_commentary_subtitle_policy(subtitle_policy) != "creator":
        return False
    policy = _clip_speech_policy_summary(moment)
    return bool(
        policy.get("metadata_backfill_blocked")
        or policy.get("status") == "no_selected_commentary_speech"
        or (
            policy.get("track_aware")
            and not policy.get("selected_track_has_speech")
            and int(policy.get("selected_track_word_count") or 0) <= 0
        )
    )


def _subtitle_words_for_render_start(words: list[dict], trim_start: float, render_start: float) -> list[dict]:
    """Move trim-relative subtitle words onto the actual rendered clip timeline."""
    try:
        offset = float(trim_start) - float(render_start)
    except (TypeError, ValueError):
        offset = 0.0
    if abs(offset) < 0.001:
        return words
    shifted: list[dict] = []
    for word in words or []:
        if not isinstance(word, dict):
            continue
        copy = dict(word)
        try:
            start = max(0.0, float(copy.get("start", 0.0)) + offset)
            end = max(start + 0.08, float(copy.get("end", start)) + offset)
            copy["start"] = round(start, 3)
            copy["end"] = round(end, 3)
        except (TypeError, ValueError):
            pass
        shifted.append(copy)
    return shifted


# ── Log interceptor — captures print() and forwards to the GUI console ───────

import sys as _sys
class _LogTee:
    """Wraps stdout/stderr: writes to both the original stream and a callback."""

    def __init__(self, original, callback):
        self._orig = original
        self._cb = callback
        self._encoding = getattr(original, 'encoding', 'utf-8')

    def write(self, text):
        try:
            self._orig.write(text)
        except (UnicodeEncodeError, UnicodeDecodeError):
            # Windows console can't handle some Unicode chars — strip them
            safe = text.encode('ascii', errors='replace').decode('ascii')
            try:
                self._orig.write(safe)
            except Exception:
                pass
        if text and text.strip():
            try:
                self._cb(text.strip())
            except Exception:
                pass
        return len(text)

    def flush(self):
        self._orig.flush()

    def __getattr__(self, name):
        return getattr(self._orig, name)


_log_bridge = None  # set by ApiBridge.__init__


def _install_log_tee():
    """Install stdout/stderr tee that pushes logs to the frontend console."""
    _forwarding = threading.local()

    def _forward(text):
        # Guard against recursion (if evaluate_js triggers a print)
        if getattr(_forwarding, 'active', False):
            return
        _forwarding.active = True
        try:
            if _log_bridge and _log_bridge._window:
                escaped = text.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
                _log_bridge._js(f"window.onConsoleLog(`{escaped}`)")
        finally:
            _forwarding.active = False

    _sys.stdout = _LogTee(_sys.__stdout__ or _sys.stdout, _forward)
    _sys.stderr = _LogTee(_sys.__stderr__ or _sys.stderr, _forward)


# ── Local video server (serves clip files for HTML5 <video> preview) ─────────

def _allowed_media_origin(origin: str | None) -> str | None:
    if not origin:
        return None
    text = str(origin).strip()
    try:
        parsed = urlparse(text)
    except Exception:
        return None
    if parsed.scheme in {"http", "https"} and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return text
    return None


class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    """Serves files from a directory with range support and no logging."""

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        allowed_origin = _allowed_media_origin(self.headers.get("Origin"))
        if allowed_origin:
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range, Content-Type")
        self.send_header("Access-Control-Expose-Headers", "Accept-Ranges, Content-Length, Content-Range")
        self.send_header("Cache-Control", "public, max-age=3600")
        super().end_headers()

    def do_OPTIONS(self):
        origin = self.headers.get("Origin")
        if origin and not _allowed_media_origin(origin):
            self.send_error(403, "Origin not allowed")
            return
        self.send_response(204)
        self.end_headers()

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass  # Browser closed connection early — harmless

    def list_directory(self, path):
        self.send_error(404, "Directory listing disabled")
        return None

    def send_head(self):
        try:
            root = Path(self.directory).resolve()
            requested = Path(self.translate_path(self.path)).resolve()
            requested.relative_to(root)
        except (OSError, ValueError):
            self.send_error(404, "File not found")
            return None
        return super().send_head()


class _SilentHTTPServer(http.server.ThreadingHTTPServer):
    """HTTPServer that suppresses broken-pipe / connection-reset tracebacks."""

    def handle_error(self, request, client_address):
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError,
                            BrokenPipeError, OSError)):
            return  # browser closed connection early — harmless
        super().handle_error(request, client_address)


def _start_video_server(clips_dir: Path) -> int:
    """Start a local HTTP server for video previews; returns the port."""
    handler = functools.partial(_SilentHandler, directory=str(clips_dir))
    # Bind to port 0 → OS picks a free port
    server = _SilentHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[+] Video preview server on http://127.0.0.1:{port}")
    return port


class ApiBridge:
    def __init__(self):
        self._window = None
        self._processing = False
        self._cancel = False
        self._results: list[Path] = []
        self._moments: list[dict] = []
        self._scheduled: list[dict] = []
        self._upload_history: list[dict] = []
        self._video_root = CLIPS_DIR.resolve()
        self._video_port = _start_video_server(CLIPS_DIR)
        self._music_port = _start_video_server(MUSIC_DIR)
        self._scheduler_running = False
        self._delete_after_upload = False   # auto-delete clips after YouTube upload
        self._user_settings: dict = {}      # user settings persisted to disk
        self._download_info_by_path: dict = {}
        self._source_context: dict = {}
        self._pending_js: list[str] = []    # JS calls queued while window was hidden
        self._state_lock = threading.RLock()
        self._upload_lock = threading.Lock()
        self._personalization_lock = threading.RLock()
        self._personalization: dict = self._empty_personalization()
        self._voice_profile_lock = threading.RLock()
        self._voice_profile: dict = empty_voice_profile()
        self._processing_history_lock = threading.RLock()
        self._processing_history: dict = self._empty_processing_history()
        self._run_learning_lock = threading.RLock()
        self._run_learning: dict = empty_run_learning()

        # Install log interceptor so print() output goes to the GUI console
        global _log_bridge
        _log_bridge = self
        _install_log_tee()

        # Load persisted state from previous session
        self._load_state()
        self._load_personalization()
        self._load_voice_profile()
        self._load_processing_history()
        self._load_run_learning()
        self._sync_video_server_root()
        self._cleanup_voice_profile_temp_wavs()

    # ── Exposed: config / deps ───────────────────────────────────────────

    def _clips_dir(self) -> Path:
        """Return the active generated-video folder, falling back to LocalAppData."""
        raw = ""
        try:
            raw = str((self._user_settings or {}).get("output_dir") or "").strip()
        except Exception:
            raw = ""
        if not raw:
            CLIPS_DIR.mkdir(parents=True, exist_ok=True)
            return CLIPS_DIR
        try:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                return CLIPS_DIR
            path.mkdir(parents=True, exist_ok=True)
            resolved = path.resolve()
            if not resolved.exists() or not resolved.is_dir():
                return CLIPS_DIR
            return resolved
        except Exception as exc:
            print(f"[settings] Custom output folder unavailable; using default clips folder: {exc}")
            CLIPS_DIR.mkdir(parents=True, exist_ok=True)
            return CLIPS_DIR

    def _sync_video_server_root(self):
        """Ensure the local preview server serves the active clips folder."""
        try:
            root = self._clips_dir().resolve()
        except Exception:
            root = CLIPS_DIR.resolve()
        if getattr(self, "_video_root", None) == root:
            return
        self._video_root = root
        self._video_port = _start_video_server(root)
        print(f"[settings] Video output folder active: {root}")

    def _clip_url_for_path(self, path: Path) -> str | None:
        try:
            root = self._clips_dir().resolve()
            rel = path.resolve().relative_to(root).as_posix()
            self._sync_video_server_root()
            return f"http://127.0.0.1:{self._video_port}/{quote(rel)}"
        except Exception:
            return None

    def _cleanup_voice_profile_temp_wavs(self):
        """Remove stale voice-profile temp WAVs left by an interrupted enrollment."""
        try:
            root = SUBTITLES_DIR.resolve()
        except Exception:
            return
        try:
            candidates = list(SUBTITLES_DIR.glob("voice_profile_*.wav"))
        except Exception:
            return
        for path in candidates:
            try:
                resolved = path.resolve()
                resolved.relative_to(root)
                if resolved.is_file():
                    resolved.unlink(missing_ok=True)
            except Exception:
                continue

    def get_settings(self):
        """Return user settings (persisted overrides merged with defaults)."""
        defaults = {
            "generation_mode": "clips",
            "num_clips": NUM_CLIPS,
            "processing_depth": "balanced",
            "detection_preference": "auto",
            "game_title_hint": "",
            "montage": {
                "template": "panic",
                "target_duration": 60,
                "prompt": "",
            },
            "clip_duration": CLIP_DURATION,
            "min_gap": MIN_GAP,
            "whisper_model": WHISPER_MODEL,
            "whisper_language": WHISPER_LANGUAGE or "",
            "subtitle_style": SUBTITLE_STYLE,
            "subtitle_placement": dict(SUBTITLE_PLACEMENT),
            "ffmpeg_preset": FFMPEG_PRESET,
            "video_crf": VIDEO_CRF,
            "crop_vertical": CROP_VERTICAL,
            "output_dir": "",
            "description_profile": {
                "auto_hashtags": True,
                "custom_text": "",
            },
            "visual_diagnostics": True,
            "ai_moment_classification": False,
            "moment_category_ranking": False,
            "voice_profile_ranking": False,
            "audio_source": {
                "mode": "auto",
                "stream": None,
                "commentary_guard": True,
                "subtitle_policy": "creator",
            },
        }
        # Merge saved user overrides (from save_settings)
        if self._user_settings:
            defaults.update(self._user_settings)
        # Game is a per-run/source hint, not a global preference. Older builds
        # persisted it in user settings, so hide stale values from the wizard.
        defaults["game_title_hint"] = ""
        defaults["generation_mode"] = _normalize_generation_mode(defaults.get("generation_mode"))
        defaults["montage"] = _normalize_montage_settings(defaults.get("montage"))
        defaults["clip_duration"] = _normalize_clip_duration(defaults.get("clip_duration"))
        defaults["min_gap"] = _normalize_min_gap(defaults.get("min_gap"))
        if defaults.get("output_dir"):
            defaults["output_dir"] = str(defaults.get("output_dir") or "")
        return defaults

    def get_app_metadata(self):
        """Return public app metadata for the UI."""
        return {
            "name": APP_NAME,
            "version": APP_VERSION,
            "version_display": APP_VERSION_DISPLAY,
            "description": APP_DESCRIPTION,
        }

    def get_app_paths(self):
        """Return user-facing app paths for setup and privacy screens."""
        return {
            "app_data_dir": str(APP_DATA_DIR),
            "client_secrets_file": str(CLIENT_SECRETS_FILE),
            "bin_dir": str(BIN_DIR),
            "clips_dir": str(self._clips_dir()),
            "default_clips_dir": str(CLIPS_DIR),
            "music_dir": str(MUSIC_DIR),
        }

    def save_settings(self, settings):
        """Persist user settings to disk so they survive restarts."""
        cleaned = dict(settings or {})
        if "generation_mode" in cleaned:
            cleaned["generation_mode"] = _normalize_generation_mode(
                cleaned.get("generation_mode")
            )
        if "montage" in cleaned:
            cleaned["montage"] = _normalize_montage_settings(
                cleaned.get("montage")
            )
        if "detection_preference" in cleaned:
            cleaned["detection_preference"] = normalize_detection_preference(
                cleaned.get("detection_preference")
            )
        if "processing_depth" in cleaned:
            cleaned["processing_depth"] = _normalize_processing_depth(
                cleaned.get("processing_depth")
            )
        cleaned.pop("game_title_hint", None)
        if "clip_duration" in cleaned:
            cleaned["clip_duration"] = _normalize_clip_duration(
                cleaned.get("clip_duration")
            )
        if "output_dir" in cleaned:
            raw_output_dir = str(cleaned.get("output_dir") or "").strip()
            if raw_output_dir:
                try:
                    path = Path(raw_output_dir).expanduser()
                    cleaned["output_dir"] = str(path.resolve()) if path.is_absolute() else ""
                except Exception:
                    cleaned["output_dir"] = ""
            else:
                cleaned["output_dir"] = ""
        if "min_gap" in cleaned:
            cleaned["min_gap"] = _normalize_min_gap(
                cleaned.get("min_gap")
            )
        if "subtitle_placement" in cleaned:
            cleaned["subtitle_placement"] = normalize_subtitle_placement(
                cleaned.get("subtitle_placement")
            )
        if "subtitle_style" in cleaned:
            cleaned["subtitle_style"] = _normalize_subtitle_style(
                cleaned.get("subtitle_style")
            )
        if "description_profile" in cleaned:
            profile = cleaned.get("description_profile") or {}
            cleaned["description_profile"] = {
                "auto_hashtags": bool(profile.get("auto_hashtags", True)),
                "custom_text": str(profile.get("custom_text", "") or ""),
            }
        if "voice_profile_ranking" in cleaned:
            cleaned["voice_profile_ranking"] = _normalize_bool_setting(
                cleaned.get("voice_profile_ranking"),
                False,
            )
        if "visual_diagnostics" in cleaned:
            cleaned["visual_diagnostics"] = _normalize_bool_setting(
                cleaned.get("visual_diagnostics"),
                True,
            )
        if "ai_moment_classification" in cleaned:
            cleaned["ai_moment_classification"] = _normalize_bool_setting(
                cleaned.get("ai_moment_classification"),
                False,
            )
        if "moment_category_ranking" in cleaned:
            cleaned["moment_category_ranking"] = _normalize_bool_setting(
                cleaned.get("moment_category_ranking"),
                False,
            )
        if "audio_source" in cleaned or "audio_source_mode" in cleaned or "transcription_audio_stream" in cleaned:
            cleaned["audio_source"] = _normalize_audio_source_settings(cleaned)
            cleaned.pop("audio_source_mode", None)
            cleaned.pop("transcription_audio_stream", None)
            cleaned.pop("mixed_audio_commentary_guard", None)
            cleaned.pop("mixed_audio_subtitle_policy", None)
            cleaned.pop("commentary_subtitle_policy", None)
        self._user_settings = cleaned
        self._sync_video_server_root()
        self._save_state()
        return {"ok": True}

    def check_dependencies(self):
        return {
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "ffprobe": shutil.which("ffprobe") is not None,
        }

    def probe_audio_sources(self, source):
        """Return audio stream choices for a local source, or defer remote URLs."""
        source_text = str(source or "").strip()
        if not source_text:
            return {
                "mode": "empty",
                "streams": [],
                "recommended_stream": None,
                "stream_count": 0,
                "message": "No source selected yet",
            }

        path = Path(source_text)
        is_remote = bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", source_text))
        looks_like_online_video = bool(
            re.match(r"^(www\.)?(youtube\.com|youtu\.be|twitch\.tv|kick\.com)/", source_text, re.I)
        )
        looks_local = path.is_absolute() or "\\" in source_text or "/" in source_text or bool(path.suffix)
        if is_remote or looks_like_online_video or not looks_local:
            return {
                "mode": "deferred",
                "streams": [],
                "recommended_stream": None,
                "stream_count": 0,
                "message": "Audio tracks will be checked after download",
            }
        if path.exists() and not path.is_file():
            return {
                "mode": "error",
                "streams": [],
                "recommended_stream": None,
                "stream_count": 0,
                "message": "Select a video file, not a folder",
            }
        if not path.exists():
            return {
                "mode": "error",
                "streams": [],
                "recommended_stream": None,
                "stream_count": 0,
                "message": "Local video file was not found",
            }

        streams = [_public_audio_stream(s) for s in get_audio_streams(path)]
        diagnostics = get_last_audio_stream_diagnostics()

        if not streams:
            status = diagnostics.get("status")
            if status in {"timeout", "ffprobe_error", "ffprobe_missing"}:
                label = {
                    "timeout": "Audio inspection timed out",
                    "ffprobe_error": "Audio inspection failed",
                    "ffprobe_missing": "ffprobe is not available",
                }.get(status, "Audio inspection failed")
                return {
                    "mode": "error",
                    "streams": [],
                    "recommended_stream": None,
                    "stream_count": 0,
                    "message": label,
                    "diagnostics": diagnostics,
                }
            return {
                "mode": "no_audio",
                "streams": [],
                "recommended_stream": None,
                "stream_count": 0,
                "message": "No audio tracks found",
                "diagnostics": diagnostics,
            }

        recommended = next(
            (int(stream["ordinal"]) for stream in streams if stream.get("likely_role") == "commentary"),
            int(streams[0]["ordinal"]),
        )
        mode = "single" if len(streams) == 1 else "multi"
        message = (
            "One mixed audio track found"
            if mode == "single"
            else f"{len(streams)} audio tracks found"
        )
        return {
            "mode": mode,
            "streams": streams,
            "recommended_stream": recommended,
            "stream_count": len(streams),
            "message": message,
            "diagnostics": diagnostics,
        }

    def set_delete_after_upload(self, enabled):
        """Toggle auto-delete clips from disk after successful YouTube upload."""
        self._delete_after_upload = bool(enabled)
        self._save_state()
        return {"ok": True, "enabled": self._delete_after_upload}

    def get_delete_after_upload(self):
        return {"enabled": self._delete_after_upload}

    # ── Exposed: AI title generation ──────────────────────────────────────

    def _infer_game_title_from_path(self, path) -> str:
        generic = {
            "vertical", "horizontal", "clips", "downloads", "recording video files",
            "videos", "video files", "captures", "recordings", "obs", "output",
        }
        try:
            p = Path(path)
        except Exception:
            return ""
        for part in [p.parent.name, p.parent.parent.name if p.parent else ""]:
            cleaned = str(part).strip()
            if not cleaned or cleaned.lower() in generic:
                continue
            if re.match(r"^\d{4}-\d{2}-\d{2}", cleaned):
                continue
            return cleaned
        return ""

    def _game_title_for_clip(self, clip_index: int) -> str:
        if 0 <= clip_index < len(self._moments):
            moment = self._moments[clip_index]
            if moment.get("game_title"):
                return moment["game_title"]
            if moment.get("source_path"):
                title = self._infer_game_title_from_path(moment["source_path"])
                if title:
                    moment["game_title"] = title
                    return title
            stem = moment.get("source_stem")
            if stem:
                for suffix in ("_run_debug.json", "_candidate_debug.json"):
                    path = SUBTITLES_DIR / f"{stem}{suffix}"
                    if not path.exists():
                        continue
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                        title = self._infer_game_title_from_path(data.get("video", ""))
                        if title:
                            moment["game_title"] = title
                            moment["source_path"] = data.get("video", "")
                            return title
                    except Exception:
                        pass
        if 0 <= clip_index < len(self._results):
            return self._infer_game_title_from_path(self._results[clip_index])
        return ""

    @staticmethod
    def _sanitize_game_title_hint(value) -> str:
        cleaned = normalize_game_title(str(value or ""))
        cleaned = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned[:120]

    @staticmethod
    def _compact_game_context_for_state(context: dict | None) -> dict:
        context = context if isinstance(context, dict) else {}
        if not context:
            return {}
        facts = context.get("facts") if isinstance(context.get("facts"), dict) else {}
        return {
            "schema_version": context.get("schema_version", 1),
            "status": str(context.get("status") or "")[:40],
            "provider": str(context.get("provider") or "")[:40],
            "qid": str(context.get("qid") or "")[:40],
            "label": str(context.get("label") or "")[:140],
            "description": str(context.get("description") or "")[:220],
            "aliases": [str(item)[:100] for item in (context.get("aliases") or [])[:10]],
            "source_url": str(context.get("source_url") or "")[:240],
            "license": str(context.get("license") or "")[:80],
            "facts": copy.deepcopy(facts),
        }

    def _compact_game_identity_for_state(self, identity: dict | None) -> dict:
        identity = identity if isinstance(identity, dict) else {}
        if not identity:
            return {}
        context = self._compact_game_context_for_state(identity.get("game_context"))
        return {
            "schema_version": identity.get("schema_version", 1),
            "status": str(identity.get("status") or "")[:40],
            "provider": str(identity.get("provider") or "")[:40],
            "selection_impact": str(identity.get("selection_impact") or "game_context_lookup")[:80],
            "confidence": self._game_identity_confidence(identity),
            "title": str(identity.get("title") or context.get("label") or "")[:140],
            "qid": str(identity.get("qid") or context.get("qid") or "")[:40],
            "matched_via": str(identity.get("matched_via") or "")[:80],
            "matched_candidate": copy.deepcopy(identity.get("matched_candidate")) if isinstance(identity.get("matched_candidate"), dict) else None,
            "evidence": [copy.deepcopy(item) for item in (identity.get("evidence") or [])[:8] if isinstance(item, dict)],
            "candidates": [copy.deepcopy(item) for item in (identity.get("candidates") or [])[:8] if isinstance(item, dict)],
            "game_context": context,
            "game_context_prompt": compact_game_context_for_prompt(context),
        }

    @staticmethod
    def _compact_youtube_context_for_state(context: dict | None) -> dict:
        context = context if isinstance(context, dict) else {}
        if not context:
            return {}
        return {
            "title": str(context.get("title") or "")[:180],
            "uploader": str(context.get("uploader") or "")[:140],
            "webpage_url": str(context.get("webpage_url") or "")[:300],
            "categories": [str(item)[:80] for item in (context.get("categories") or [])[:8]],
            "tags": [str(item)[:60] for item in (context.get("tags") or [])[:20]],
            "context_text": str(context.get("context_text") or "")[:1400],
        }

    @staticmethod
    def _safe_int(value, default: int = 1) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize_download_info_store(store) -> dict:
        if not isinstance(store, dict):
            return {}
        clean = {}
        for path, info in store.items():
            if not isinstance(info, dict):
                continue
            key = str(path or "").strip()
            if not key:
                continue
            clean[key[:500]] = {
                "schema_version": ApiBridge._safe_int(info.get("schema_version"), 1),
                "source": str(info.get("source") or "")[:40],
                "title": str(info.get("title") or "")[:180],
                "uploader": str(info.get("uploader") or "")[:140],
                "channel": str(info.get("channel") or "")[:140],
                "webpage_url": str(info.get("webpage_url") or "")[:300],
                "original_url": str(info.get("original_url") or "")[:300],
                "categories": [str(item)[:80] for item in (info.get("categories") or [])[:8]],
                "tags": [str(item)[:60] for item in (info.get("tags") or [])[:20]],
                "description": str(info.get("description") or "")[:1000],
            }
        return clean

    def _normalize_source_context_store(self, store) -> dict:
        if not isinstance(store, dict):
            return {}
        sources = store.get("sources") if isinstance(store.get("sources"), dict) else store
        clean = {}
        for source_id, record in sources.items():
            if not isinstance(record, dict):
                continue
            key = str(source_id or record.get("source_id") or "").strip()
            if not key:
                continue
            identity = self._compact_game_identity_for_state(record.get("game_identity"))
            context = self._compact_game_context_for_state(
                record.get("game_context")
                or (identity.get("game_context") if isinstance(identity, dict) else {})
            )
            clean[key[:80]] = {
                "schema_version": self._safe_int(record.get("schema_version"), 1),
                "source_id": key[:80],
                "source_path": str(record.get("source_path") or "")[:500],
                "source_name": str(record.get("source_name") or "")[:220],
                "game_title_hint": self._sanitize_game_title_hint(record.get("game_title_hint")),
                "game_title": str(record.get("game_title") or identity.get("title") or context.get("label") or "")[:140],
                "game_identity": identity,
                "game_context": context,
                "youtube_context": self._compact_youtube_context_for_state(record.get("youtube_context")),
                "updated_at": str(record.get("updated_at") or "")[:40],
            }
        return clean

    def _source_record_for(self, video_path: Path | str | None = None, source_id: str = "") -> dict:
        store = getattr(self, "_source_context", {})
        if not isinstance(store, dict):
            self._source_context = {}
            return {}
        key = str(source_id or "").strip()
        if not key and video_path:
            try:
                key = self._source_id_for(video_path)
            except Exception:
                key = ""
        if key and isinstance(store.get(key), dict):
            return store[key]
        if video_path:
            try:
                resolved = str(Path(video_path).resolve())
            except Exception:
                resolved = str(video_path or "")
            for record in store.values():
                if isinstance(record, dict) and str(record.get("source_path") or "") == resolved:
                    return record
        return {}

    def _remember_source_context(
        self,
        video_path: Path | str | None,
        *,
        source_id: str = "",
        game_title_hint: str = "",
        game_identity: dict | None = None,
        game_context: dict | None = None,
        youtube_context: dict | None = None,
        force: bool = False,
        persist: bool = False,
    ) -> dict:
        if not hasattr(self, "_source_context") or not isinstance(self._source_context, dict):
            self._source_context = {}
        if not video_path and not source_id:
            return {}
        try:
            resolved_path = str(Path(video_path).resolve()) if video_path else ""
            source_name = Path(video_path).name if video_path else ""
        except Exception:
            resolved_path = str(video_path or "")
            source_name = Path(str(video_path or "")).name if video_path else ""
        key = str(source_id or "").strip() or self._source_id_for(resolved_path)
        existing = self._source_context.get(key) if isinstance(self._source_context.get(key), dict) else {}
        identity = self._compact_game_identity_for_state(game_identity)
        context = self._compact_game_context_for_state(
            game_context
            or identity.get("game_context")
            or existing.get("game_context")
        )
        old_identity = existing.get("game_identity") if isinstance(existing.get("game_identity"), dict) else {}
        old_confidence = self._game_identity_confidence(old_identity)
        new_confidence = self._game_identity_confidence(identity)
        hint = self._sanitize_game_title_hint(game_title_hint or existing.get("game_title_hint"))
        has_verified_new_identity = bool(
            identity.get("qid")
            and context.get("status") in {"ok", "cache_hit"}
            and new_confidence > 0
        )
        should_replace_identity = bool(
            not old_identity
            or (
                has_verified_new_identity
                and (
                    force
                    or not old_identity.get("qid")
                    or new_confidence >= old_confidence
                )
            )
        )
        if not should_replace_identity and old_identity:
            identity = old_identity
            context = self._compact_game_context_for_state(existing.get("game_context") or old_identity.get("game_context"))
        record = {
            "schema_version": 1,
            "source_id": key,
            "source_path": resolved_path,
            "source_name": source_name,
            "game_title_hint": hint,
            "game_title": (
                identity.get("title")
                or context.get("label")
                or existing.get("game_title")
                or hint
                or ""
            ),
            "game_identity": identity,
            "game_context": context,
            "youtube_context": self._compact_youtube_context_for_state(youtube_context or existing.get("youtube_context")),
            "updated_at": self._utc_now_label(),
        }
        self._source_context[key] = record
        if persist:
            ready_for_state_save = all(
                hasattr(self, attr)
                for attr in ("_results", "_moments", "_scheduled", "_delete_after_upload", "_user_settings")
            )
            if ready_for_state_save:
                try:
                    self._save_state()
                except Exception as exc:
                    print(f"[game-context] Failed to persist source memory: {exc}")
        return record

    def _remembered_game_identity_for_source(
        self,
        video_path: Path | str | None,
        *,
        source_id: str = "",
        explicit_title: str = "",
    ) -> dict:
        record = self._source_record_for(video_path, source_id)
        identity = record.get("game_identity") if isinstance(record.get("game_identity"), dict) else {}
        if not identity:
            return {}
        confidence = self._game_identity_confidence(identity)
        has_verified_context = bool(
            identity.get("qid")
            and isinstance(identity.get("game_context"), dict)
            and identity["game_context"].get("status") in {"ok", "cache_hit"}
            and confidence >= 0.72
        )
        if not has_verified_context:
            return {}
        explicit = self._sanitize_game_title_hint(explicit_title)
        remembered_title = self._sanitize_game_title_hint(
            record.get("game_title_hint") or identity.get("title") or record.get("game_title")
        )
        if explicit and remembered_title and explicit.lower() != remembered_title.lower():
            return {}
        return identity

    def _game_context_for_title(self, game_title: str, *, allow_network: bool = False) -> dict:
        try:
            return get_game_context(
                game_title,
                allow_network=allow_network,
                timeout=8,
            )
        except Exception as exc:
            return {
                "schema_version": 1,
                "status": "query_error",
                "provider": "wikidata",
                "game_title": str(game_title or "")[:120],
                "available": False,
                "error": str(exc)[:180],
            }

    def _game_identity_for_source(
        self,
        video_path: Path,
        *,
        allow_network: bool = False,
        explicit_title: str | None = None,
        creator_context: str | None = None,
        transcript: str | None = None,
    ) -> dict:
        explicit_title = self._sanitize_game_title_hint(explicit_title)
        remembered = self._remembered_game_identity_for_source(
            video_path,
            explicit_title=explicit_title,
        )
        if remembered and not creator_context and not transcript:
            return remembered
        youtube_context = self._youtube_context_for_source(video_path)
        if youtube_context:
            explicit_title = explicit_title or youtube_context.get("title") or ""
            creator_context = " ".join(
                part
                for part in (
                    creator_context or "",
                    youtube_context.get("context_text") or "",
                )
                if str(part or "").strip()
            )
        try:
            identity = resolve_game_identity(
                source_path=video_path,
                explicit_title=explicit_title or self._infer_game_title_from_path(video_path),
                creator_context=creator_context,
                transcript=transcript,
                allow_network=allow_network,
                timeout=8,
            )
            self._remember_source_context(
                video_path,
                game_title_hint=explicit_title,
                game_identity=identity,
                game_context=identity.get("game_context") if isinstance(identity, dict) else {},
                youtube_context=youtube_context,
                force=bool(explicit_title),
                persist=True,
            )
            return identity
        except Exception as exc:
            fallback_title = explicit_title or self._infer_game_title_from_path(video_path)
            identity = {
                "schema_version": 1,
                "status": "query_error",
                "provider": "wikidata",
                "selection_impact": "game_context_lookup",
                "title": str(fallback_title or "")[:120],
                "qid": "",
                "confidence": 0.0,
                "evidence": [],
                "game_context": self._game_context_for_title(fallback_title, allow_network=False),
                "error": str(exc)[:180],
            }
            self._remember_source_context(
                video_path,
                game_title_hint=explicit_title,
                game_identity=identity,
                game_context=identity.get("game_context"),
                youtube_context=youtube_context,
                persist=True,
            )
            return identity

    @staticmethod
    def _game_identity_confidence(identity: dict | None) -> float:
        identity = identity if isinstance(identity, dict) else {}
        try:
            return max(0.0, min(1.0, float(identity.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            return 0.0

    def _refresh_clip_game_identity_for_metadata(
        self,
        clip_index: int,
        *,
        allow_network: bool = False,
        force: bool = False,
    ) -> dict:
        """Refresh weak/missing game identity before AI metadata generation."""
        if clip_index < 0 or clip_index >= len(self._results):
            return {}
        while len(self._moments) <= clip_index:
            self._moments.append({})
        moment = self._moments[clip_index] if isinstance(self._moments[clip_index], dict) else {}
        existing_identity = moment.get("game_identity") if isinstance(moment.get("game_identity"), dict) else {}
        existing_context = moment.get("game_context") if isinstance(moment.get("game_context"), dict) else {}
        existing_confidence = self._game_identity_confidence(existing_identity)
        has_verified_context = bool(
            existing_identity.get("qid")
            and existing_context.get("status") in {"ok", "cache_hit"}
            and existing_confidence >= 0.72
        )
        creator_context = sanitize_creator_title_context(moment.get("creator_title_context"))
        if has_verified_context and not force:
            return existing_identity
        if has_verified_context and force and not creator_context:
            return existing_identity

        try:
            video_path = Path(moment.get("source_path") or self._results[clip_index])
        except Exception:
            video_path = Path(self._results[clip_index])
        source_record = self._source_record_for(video_path, moment.get("source_id", ""))
        game_title_hint = self._sanitize_game_title_hint(
            moment.get("game_title_hint") or source_record.get("game_title_hint")
        )
        explicit_title = game_title_hint or str(moment.get("game_title") or source_record.get("game_title") or "").strip()
        if existing_confidence < 0.55 and not game_title_hint:
            explicit_title = ""
        identity = self._game_identity_for_source(
            video_path,
            allow_network=allow_network,
            explicit_title=explicit_title,
            creator_context=creator_context,
            transcript=str(moment.get("transcript") or "")[:1200],
        )
        if not isinstance(identity, dict):
            return existing_identity
        new_confidence = self._game_identity_confidence(identity)
        game_context = identity.get("game_context") if isinstance(identity.get("game_context"), dict) else {}
        should_apply = bool(
            identity.get("qid")
            and game_context.get("status") in {"ok", "cache_hit"}
            and (
                not existing_identity.get("qid")
                or new_confidence >= existing_confidence
                or (force and existing_confidence < 0.72)
            )
        )
        if not should_apply:
            return existing_identity
        if game_title_hint:
            moment["game_title_hint"] = game_title_hint
        moment["game_title"] = identity.get("title") or game_context.get("label") or moment.get("game_title") or ""
        moment["game_identity"] = identity
        moment["game_context"] = game_context
        self._moments[clip_index] = moment
        return identity

    def _youtube_context_for_source(self, video_path: Path | str | None) -> dict:
        info_by_path = getattr(self, "_download_info_by_path", {})
        if not isinstance(info_by_path, dict) or not video_path:
            return {}
        try:
            resolved = str(Path(video_path).resolve())
        except Exception:
            resolved = str(video_path or "")
        info = info_by_path.get(resolved)
        if not isinstance(info, dict):
            return {}
        title = str(info.get("title") or "").strip()
        uploader = str(info.get("uploader") or info.get("channel") or "").strip()
        webpage_url = str(info.get("webpage_url") or info.get("original_url") or "").strip()
        categories = [
            str(item).strip()
            for item in (info.get("categories") or [])
            if str(item or "").strip()
        ][:6]
        tags = [
            str(item).strip()
            for item in (info.get("tags") or [])
            if str(item or "").strip()
        ][:12]
        description = re.sub(r"\s+", " ", str(info.get("description") or "")).strip()[:700]
        context_text = " ".join(
            part
            for part in (
                f"YouTube title: {title}" if title else "",
                f"Channel: {uploader}" if uploader else "",
                f"Categories: {', '.join(categories)}" if categories else "",
                f"Tags: {', '.join(tags)}" if tags else "",
                f"Description: {description}" if description else "",
            )
            if part
        )
        return {
            "title": title,
            "uploader": uploader,
            "webpage_url": webpage_url,
            "categories": categories,
            "tags": tags,
            "context_text": context_text[:1400],
        }

    def _game_context_for_source(self, video_path: Path, *, allow_network: bool = False) -> dict:
        identity = self._game_identity_for_source(video_path, allow_network=allow_network)
        context = identity.get("game_context") if isinstance(identity.get("game_context"), dict) else {}
        if context:
            return context
        return self._game_context_for_title(identity.get("title") or self._infer_game_title_from_path(video_path), allow_network=allow_network)

    def _feedback_learning_prompt_context(self) -> dict:
        lock = getattr(self, "_personalization_lock", threading.RLock())
        personalization = getattr(self, "_personalization", self._empty_personalization())
        if not isinstance(personalization, dict):
            personalization = self._empty_personalization()
        with lock:
            personalization_snapshot = json.loads(json.dumps(personalization))
        run_lock = getattr(self, "_run_learning_lock", threading.RLock())
        run_learning = getattr(self, "_run_learning", empty_run_learning())
        if not isinstance(run_learning, dict):
            run_learning = empty_run_learning()
        with run_lock:
            run_learning_snapshot = json.loads(json.dumps(run_learning))
        return build_learning_prompt_context(
            personalization_snapshot,
            run_learning=run_learning_snapshot,
        )

    def _title_context_for_clip(self, clip_index: int) -> dict:
        if clip_index < 0 or clip_index >= len(self._moments):
            return {}
        moment = self._moments[clip_index]
        if not isinstance(moment, dict):
            return {}
        ranker = moment.get("ranker") if isinstance(moment.get("ranker"), dict) else {}
        multi_signal_ai = (
            moment.get("multi_signal_ai_scoring")
            if isinstance(moment.get("multi_signal_ai_scoring"), dict)
            else {}
        )
        source_record = self._source_record_for(moment.get("source_path"), moment.get("source_id"))
        source_identity = source_record.get("game_identity") if isinstance(source_record.get("game_identity"), dict) else {}
        source_context = source_record.get("game_context") if isinstance(source_record.get("game_context"), dict) else {}
        game_identity = moment.get("game_identity") if isinstance(moment.get("game_identity"), dict) else source_identity
        game_context = moment.get("game_context") if isinstance(moment.get("game_context"), dict) else source_context
        game_title_hint = self._sanitize_game_title_hint(
            moment.get("game_title_hint") or source_record.get("game_title_hint")
        )
        game_title = (
            str(moment.get("game_title") or "").strip()
            or source_record.get("game_title")
            or source_context.get("label")
            or game_title_hint
            or self._game_title_for_clip(clip_index)
            or ""
        )
        context = {
            "schema_version": 1,
            "clip_id": moment.get("clip_id"),
            "source_id": moment.get("source_id"),
            "source_path": moment.get("source_path"),
            "source_stem": moment.get("source_stem"),
            "source_context": source_record,
            "creator_title_context": sanitize_creator_title_context(moment.get("creator_title_context")),
            "game_title_hint": game_title_hint,
            "game_title": game_title,
            "game_identity": game_identity,
            "game_context": game_context,
            "start": moment.get("start"),
            "end": moment.get("end"),
            "duration": moment.get("duration"),
            "peak_time": moment.get("peak_time"),
            "candidate_rank": moment.get("candidate_rank"),
            "candidate_kind": moment.get("candidate_kind"),
            "detector_score": moment.get("score"),
            "detector_scores": moment.get("detector_scores") if isinstance(moment.get("detector_scores"), dict) else {},
            "scene_detection_status": moment.get("scene_detection_status"),
            "scene_score": moment.get("scene_score"),
            "variance_score": moment.get("variance_score"),
            "quality_score": moment.get("quality_score"),
            "selection_quality_score": moment.get("selection_quality_score"),
            "selection_rank_score": moment.get("selection_rank_score"),
            "selection_score_source": moment.get("selection_score_source"),
            "quality_floor": moment.get("quality_floor"),
            "detection_preference": moment.get("detection_preference"),
            "learned_quality_score": moment.get("learned_quality_score"),
            "learned_adjustment": moment.get("learned_adjustment"),
            "quality_rank": moment.get("quality_rank"),
            "moment_categories": moment.get("moment_categories"),
            "primary_category": moment.get("primary_category"),
            "ai_moment_classification": moment.get("ai_moment_classification"),
            "visual_diagnostics": moment.get("visual_diagnostics"),
            "multimodal_analysis": moment.get("multimodal_analysis"),
            "multi_signal_ai_quality_score": moment.get("multi_signal_ai_quality_score"),
            "multi_signal_ai_adjustment": moment.get("multi_signal_ai_adjustment"),
            "multi_signal_ai_scoring": multi_signal_ai,
            "commentary_guard": moment.get("commentary_guard"),
            "voice_profile": moment.get("voice_profile"),
            "word_count": moment.get("word_count"),
            "analysis_word_count": moment.get("analysis_word_count"),
            "subtitle_word_count": moment.get("subtitle_word_count"),
            "speech_stream": moment.get("speech_stream"),
            "audio_source": moment.get("audio_source"),
            "stream_selection": moment.get("stream_selection"),
            "subtitle_generated": moment.get("subtitle_generated"),
            "subtitles_burned": moment.get("subtitles_burned"),
            "subtitle_placement": moment.get("subtitle_placement"),
            "transcript_source": moment.get("transcript_source"),
            "transcript_backfilled": moment.get("transcript_backfilled"),
            "transcript": str(moment.get("transcript") or "")[:4000],
            "truth_summary": self._clip_truth_summary(moment, moment.get("source_path")),
            "ranker": {
                "hook_points": ranker.get("hook_points"),
                "weak_points": ranker.get("weak_points"),
                "aftermath_points": ranker.get("aftermath_points"),
                "first_word_start": ranker.get("first_word_start"),
                "last_word_end": ranker.get("last_word_end"),
                "reject_reason": ranker.get("reject_reason"),
            },
        }
        if not context["game_context"] and context.get("game_title"):
            context["game_context"] = self._game_context_for_title(context["game_title"], allow_network=False)
        speech_policy = _clip_speech_policy_summary(moment)
        context["speech_policy"] = speech_policy
        context["metadata_warning"] = str(
            moment.get("metadata_warning") or speech_policy.get("warning") or ""
        )
        context["metadata_needs_context"] = bool(
            moment.get("metadata_needs_context")
            or speech_policy.get("metadata_backfill_blocked")
        )
        context["feedback_learning_context"] = self._feedback_learning_prompt_context()
        if 0 <= clip_index < len(self._results):
            context["clip_filename"] = self._results[clip_index].name
        return context

    def _ensure_metadata_vision_context(self, clip_index: int, clip_context: dict | None = None) -> dict:
        """Backfill local vision context from the rendered clip when metadata needs it."""
        context = dict(clip_context or {})
        if clip_index < 0 or clip_index >= len(self._results) or clip_index >= len(self._moments):
            return context
        moment = self._moments[clip_index]
        if not isinstance(moment, dict):
            return context
        existing = moment.get("multimodal_analysis")
        if isinstance(existing, dict) and existing.get("status") == "ok":
            context["multimodal_analysis"] = existing
            return context

        clip_path = Path(self._results[clip_index])
        if not clip_path.exists():
            return context
        try:
            vision_status = ollama_vision_status()
        except Exception:
            return context
        if not vision_status.get("model_ready"):
            return context

        duration = self._probe_media_duration(
            clip_path,
            default=_safe_float_value(context.get("duration"), 30.0) or 30.0,
        )
        duration = max(1.0, float(duration or 0.0))
        candidate = {
            "start": 0.0,
            "end": duration,
            "duration": duration,
            "peak_time": duration / 2.0,
            "candidate_rank": context.get("candidate_rank"),
            "candidate_kind": "rendered_clip_metadata",
            "quality_score": context.get("quality_score"),
            "primary_category": context.get("primary_category"),
            "moment_categories": context.get("moment_categories"),
            "visual_diagnostics": context.get("visual_diagnostics"),
        }
        analysis = analyze_candidate_frames_with_ollama(
            clip_path,
            candidate,
            transcript=str(context.get("transcript") or moment.get("transcript") or ""),
            game_title=str(context.get("game_title") or ""),
            game_context=context.get("game_context") if isinstance(context.get("game_context"), dict) else {},
            learning_context=context.get("feedback_learning_context"),
            video_duration=duration,
            enabled=True,
            model=str(vision_status.get("model") or DEFAULT_VISION_MODEL),
            max_frames=3,
            timeout=45,
        )
        if isinstance(analysis, dict):
            moment["multimodal_analysis"] = analysis
            ranker = moment.get("ranker") if isinstance(moment.get("ranker"), dict) else {}
            ranker["multimodal_analysis"] = analysis
            moment["ranker"] = ranker
            context["multimodal_analysis"] = analysis
        return context

    def _generated_description_for_clip(
        self,
        title: str,
        transcript: str,
        game_title: str,
        clip_context: dict | None,
    ) -> str:
        ai_body = generate_ai_description_body(
            title,
            transcript=transcript,
            game_title=game_title,
            clip_context=clip_context,
        )
        if ai_body:
            return ai_body
        return generated_description_body(title, game_title, clip_context)

    def _tags_for_game(self, game_title: str, transcript: str = "", clip_context: dict | None = None) -> str:
        return generate_tags(game_title, transcript, clip_context=clip_context)

    def _description_profile(self) -> dict:
        profile = (self._user_settings or {}).get("description_profile") or {}
        return {
            "auto_hashtags": bool(profile.get("auto_hashtags", True)),
            "custom_text": str(profile.get("custom_text", "") or ""),
        }

    def _compose_clip_description(
        self,
        title: str,
        game_title: str = "",
        clip_context: dict | None = None,
        custom_text: str | None = None,
        auto_hashtags: bool | None = None,
        generated_text: str | None = None,
    ) -> dict:
        profile = self._description_profile()
        resolved_custom = profile["custom_text"] if custom_text is None else str(custom_text or "")
        resolved_auto = profile["auto_hashtags"] if auto_hashtags is None else bool(auto_hashtags)
        generated = generated_text or generated_description_body(title, game_title, clip_context)
        final = compose_description(
            title,
            game_title=game_title,
            clip_context=clip_context,
            custom_text=resolved_custom,
            auto_hashtags=resolved_auto,
            generated_text=generated,
        )
        return {
            "generated_description": generated,
            "description_custom_text": resolved_custom,
            "description_auto_hashtags": resolved_auto,
            "description": final,
            "final_description": final,
            "recommended_hashtags": recommended_hashtags(game_title) if resolved_auto else [],
        }

    def _write_metadata_sidecar(
        self,
        clip_index: int,
        title: str,
        game_title: str = "",
        description: str | None = None,
        tags: str | None = None,
        clip_context: dict | None = None,
    ) -> str:
        if clip_index < 0 or clip_index >= len(self._results) or not title:
            return ""
        clip_path = Path(self._results[clip_index])
        description = description or self._compose_clip_description(
            title,
            game_title,
            clip_context=clip_context,
        )["description"]
        tags = tags or self._tags_for_game(game_title, clip_context=clip_context)
        context_summary = summarize_clip_context(
            (clip_context or {}).get("transcript", ""),
            game_title,
            clip_context,
        )
        hashtags = recommended_hashtags(game_title)
        lines = [
            f"Title: {title}",
            "",
            "Description:",
            description,
            "",
            f"Tags: {tags}",
            f"Hashtags: {' '.join(hashtags)}",
            f"Game: {game_title or 'Unknown'}",
            f"Clip: {clip_path}",
            "",
            "Analysis Context:",
            f"Moment Type: {context_summary.get('moment_type', 'general gameplay')}",
            f"Hook Phrases: {', '.join(context_summary.get('hook_phrases', [])) or 'None'}",
            f"Quality Score: {context_summary.get('quality_score')}",
            f"Selection Quality Score: {context_summary.get('selection_quality_score')}",
        ]
        speech_policy = context_summary.get("speech_policy") if isinstance(context_summary.get("speech_policy"), dict) else {}
        if speech_policy.get("status") and speech_policy.get("status") != "ok":
            lines.append(f"Speech Policy: {speech_policy.get('status')}")
        if context_summary.get("metadata_warning"):
            lines.append(f"Metadata Warning: {context_summary.get('metadata_warning')}")
        if context_summary.get("creator_title_context"):
            lines.append(f"Creator Context: {context_summary['creator_title_context']}")
        montage_quality = (clip_context or {}).get("montage_quality_explanation") if isinstance(clip_context, dict) else {}
        if isinstance(montage_quality, dict) and montage_quality:
            lines.append("")
            lines.append("Montage Quality:")
            if montage_quality.get("roles"):
                lines.append(f"Roles: {', '.join(str(role) for role in montage_quality.get('roles', [])[:8])}")
            if montage_quality.get("strengths"):
                lines.append(f"Strengths: {'; '.join(str(item) for item in montage_quality.get('strengths', [])[:4])}")
            if montage_quality.get("warnings"):
                lines.append(f"Warnings: {'; '.join(str(item) for item in montage_quality.get('warnings', [])[:4])}")
        game_knowledge = context_summary.get("game_knowledge") if isinstance(context_summary.get("game_knowledge"), dict) else {}
        if game_knowledge.get("available"):
            game_bits = [str(game_knowledge.get("label") or game_title or "Unknown")]
            if game_knowledge.get("release_year"):
                game_bits.append(str(game_knowledge.get("release_year")))
            if game_knowledge.get("genres"):
                game_bits.append(", ".join(game_knowledge.get("genres")[:3]))
            if game_knowledge.get("series"):
                game_bits.append("series: " + ", ".join(game_knowledge.get("series")[:2]))
            lines.append(f"Game Knowledge: {' | '.join(bit for bit in game_bits if bit)}")
        vision = context_summary.get("multimodal_analysis")
        if isinstance(vision, dict) and vision.get("status"):
            vision_bits = [str(vision.get("status") or "unknown")]
            if vision.get("model"):
                vision_bits.append(str(vision.get("model")))
            if vision.get("primary_visual_label"):
                vision_bits.append(str(vision.get("primary_visual_label")).replace("_", " "))
            if vision.get("visible_summary"):
                vision_bits.append(str(vision.get("visible_summary")))
            lines.append(f"Vision Context: {' | '.join(bit for bit in vision_bits if bit)}")
        sidecar = clip_path.with_suffix(".txt")
        try:
            sidecar.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return str(sidecar)
        except Exception as exc:
            print(f"[metadata] Failed to write sidecar for {clip_path.name}: {exc}")
            return ""

    def _delete_metadata_sidecar_path(
        self,
        sidecar_path,
        *,
        keep_path=None,
        reason: str = "",
    ) -> bool:
        """Delete a generated metadata .txt file after validating it is local app data."""
        clips_dir = self._clips_dir()
        sidecar = self._safe_path_under(clips_dir, sidecar_path)
        if not sidecar or sidecar.suffix.lower() != ".txt" or not sidecar.exists() or not sidecar.is_file():
            return False
        keep = self._safe_path_under(clips_dir, keep_path) if keep_path else None
        if keep and sidecar.resolve() == keep.resolve():
            return False
        try:
            sidecar.unlink()
            suffix = f" ({reason})" if reason else ""
            print(f"[cleanup] Deleted clip metadata sidecar: {sidecar.name}{suffix}")
            return True
        except Exception as exc:
            print(f"[cleanup] Failed to delete metadata sidecar {sidecar.name}: {exc}")
            return False

    def _delete_stale_metadata_sidecars(
        self,
        sidecar_paths,
        *,
        keep_path=None,
        reason: str = "",
    ) -> int:
        deleted = 0
        seen: set[str] = set()
        clips_dir = self._clips_dir()
        for sidecar_path in sidecar_paths or []:
            sidecar = self._safe_path_under(clips_dir, sidecar_path)
            if not sidecar:
                continue
            key = str(sidecar)
            if key in seen:
                continue
            seen.add(key)
            if self._delete_metadata_sidecar_path(sidecar, keep_path=keep_path, reason=reason):
                deleted += 1
        return deleted

    def _store_generated_metadata(
        self,
        clip_index: int,
        title: str,
        description: str,
        tags: str,
        game_title: str,
        metadata_file: str,
        clip_context: dict | None = None,
        generated_description: str | None = None,
        custom_text: str | None = None,
        auto_hashtags: bool | None = None,
    ):
        if clip_index < 0 or clip_index >= len(self._moments):
            return
        moment = self._moments[clip_index]
        if not isinstance(moment, dict):
            moment = {}
            self._moments[clip_index] = moment
        clip_filename = ""
        try:
            clip_filename = Path(self._results[clip_index]).name if 0 <= clip_index < len(self._results) else ""
        except Exception:
            clip_filename = ""
        moment["generated_metadata"] = {
            "clip_id": moment.get("clip_id") or "",
            "source_id": moment.get("source_id") or "",
            "clip_filename": clip_filename,
            "title": title,
            "description": description,
            "final_description": description,
            "generated_description": generated_description or generated_description_body(title, game_title, clip_context),
            "description_custom_text": custom_text if custom_text is not None else self._description_profile()["custom_text"],
            "description_auto_hashtags": self._description_profile()["auto_hashtags"] if auto_hashtags is None else bool(auto_hashtags),
            "recommended_hashtags": recommended_hashtags(game_title)
            if (self._description_profile()["auto_hashtags"] if auto_hashtags is None else bool(auto_hashtags))
            else [],
            "tags": tags,
            "game_title": game_title,
            "metadata_file": metadata_file,
            "speech_policy": (clip_context or {}).get("speech_policy")
            if isinstance((clip_context or {}).get("speech_policy"), dict)
            else _clip_speech_policy_summary(moment),
            "metadata_warning": (clip_context or {}).get("metadata_warning") or moment.get("metadata_warning", ""),
            "metadata_needs_context": bool((clip_context or {}).get("metadata_needs_context") or moment.get("metadata_needs_context")),
            "title_context": summarize_clip_context(
                (clip_context or {}).get("transcript", ""),
                game_title,
                clip_context,
            ),
        }
        creator_context = sanitize_creator_title_context((clip_context or {}).get("creator_title_context"))
        if creator_context:
            moment["creator_title_context"] = creator_context
            moment["generated_metadata"]["creator_title_context"] = creator_context

    def _record_metadata_learning_event(
        self,
        clip_index: int,
        title: str,
        game_title: str = "",
        *,
        reason: str = "metadata_generated",
    ):
        if clip_index < 0 or clip_index >= len(self._moments):
            return
        try:
            path = self._results[clip_index] if clip_index < len(self._results) else None
            moment = self._ensure_moment_identity(self._moments[clip_index], path)
            self._moments[clip_index] = moment
            timestamp = self._utc_now_label()
            self._record_run_learning_event(
                build_metadata_event(
                    event_id=self._hash_id(
                        "learn",
                        timestamp,
                        moment.get("clip_id"),
                        title,
                        reason,
                        length=18,
                    ),
                    timestamp=timestamp,
                    clip_id=moment.get("clip_id", ""),
                    source_id=moment.get("source_id", ""),
                    source_stem=moment.get("source_stem", ""),
                    clip_filename=Path(path).name if path else "",
                    title=title,
                    game_title=game_title,
                    reason=reason,
                )
            )
        except Exception as exc:
            print(f"[learning] Failed to record metadata outcome: {exc}")

    def generate_titles(self):
        """Generate titles for all clips using LLM (or heuristic fallback).

        If transcripts are missing (e.g. clips from a previous session where
        moments were lost), auto-transcribe the clip audio first.
        """
        num_clips = len(self._results)
        # Sync moments to match results count exactly
        if len(self._moments) > num_clips:
            self._moments = self._moments[:num_clips]
        while len(self._moments) < num_clips:
            self._moments.append({})

        # Backfill any clips missing transcripts
        missing = [i for i in range(num_clips)
                   if not self._moments[i].get("transcript")]
        if missing:
            for i in missing:
                self._backfill_transcript_single(i)
            self._save_state()

        transcripts = [m.get("transcript", "") for m in self._moments]
        for i in range(num_clips):
            self._refresh_clip_game_identity_for_metadata(i, allow_network=True)
        title_contexts = [
            self._ensure_metadata_vision_context(i, self._title_context_for_clip(i))
            for i in range(num_clips)
        ]
        game_titles = [context.get("game_title", "") for context in title_contexts]
        if not any(transcripts):
            return {"titles": [], "error": "No transcripts available — process clips first"}

        llm_available = is_ollama_model_ready(DEFAULT_MODEL)
        titles = generate_titles_batch(
            transcripts,
            game_titles=game_titles,
            clip_contexts=title_contexts,
        )
        metadata = []
        for i, title in enumerate(titles):
            if not title:
                continue
            game_title = game_titles[i] if i < len(game_titles) else ""
            clip_context = title_contexts[i] if i < len(title_contexts) else {}
            generated_description = self._generated_description_for_clip(
                title,
                transcripts[i] if i < len(transcripts) else "",
                game_title,
                clip_context,
            )
            desc_parts = self._compose_clip_description(
                title,
                game_title,
                clip_context=clip_context,
                generated_text=generated_description,
            )
            description = desc_parts["description"]
            tags = self._tags_for_game(
                game_title,
                transcripts[i] if i < len(transcripts) else "",
                clip_context=clip_context,
            )
            metadata_file = self._write_metadata_sidecar(i, title, game_title, description, tags, clip_context)
            moment = self._moments[i] if i < len(self._moments) and isinstance(self._moments[i], dict) else {}
            clip_filename = ""
            try:
                clip_filename = Path(self._results[i]).name if i < len(self._results) else ""
            except Exception:
                clip_filename = ""
            self._store_generated_metadata(
                i,
                title,
                description,
                tags,
                game_title,
                metadata_file,
                clip_context,
                generated_description=desc_parts["generated_description"],
                custom_text=desc_parts["description_custom_text"],
                auto_hashtags=desc_parts["description_auto_hashtags"],
            )
            metadata.append({
                "index": i,
                "clip_id": moment.get("clip_id") or "",
                "source_id": moment.get("source_id") or "",
                "clip_filename": clip_filename,
                "title": title,
                "game_title": game_title,
                "description": description,
                "final_description": description,
                "generated_description": desc_parts["generated_description"],
                "description_custom_text": desc_parts["description_custom_text"],
                "description_auto_hashtags": desc_parts["description_auto_hashtags"],
                "recommended_hashtags": desc_parts["recommended_hashtags"],
                "tags": tags,
                "creator_title_context": sanitize_creator_title_context(clip_context.get("creator_title_context")),
                "title_context": summarize_clip_context(transcripts[i], game_title, clip_context),
                "metadata_file": metadata_file,
            })
            self._record_metadata_learning_event(
                i,
                title,
                game_title,
                reason="batch_metadata_generated",
            )
        self._save_state()
        return {"titles": titles, "metadata": metadata, "llm": llm_available}

    def generate_title_for_clip(self, clip_index, save=True, creator_title_context=None):
        """Generate a title for a single clip."""
        # Ensure moments list matches results length
        while len(self._moments) < len(self._results):
            self._moments.append({})

        if clip_index < 0 or clip_index >= len(self._moments):
            return {"title": "", "error": "Invalid clip index"}

        if creator_title_context is not None:
            context = sanitize_creator_title_context(creator_title_context)
            moment = self._moments[clip_index] if isinstance(self._moments[clip_index], dict) else {}
            if context:
                moment["creator_title_context"] = context
            else:
                moment.pop("creator_title_context", None)
            self._moments[clip_index] = moment

        transcript = self._moments[clip_index].get("transcript", "")

        # If no transcript, try to transcribe from the clip file
        if not transcript and clip_index < len(self._results):
            self._backfill_transcript_single(clip_index)
            transcript = self._moments[clip_index].get("transcript", "")

        if not transcript:
            policy = _clip_speech_policy_summary(self._moments[clip_index])
            self._moments[clip_index]["speech_policy"] = policy
            if policy.get("warning"):
                self._moments[clip_index]["metadata_warning"] = policy["warning"]
            self._moments[clip_index]["metadata_needs_context"] = bool(policy.get("metadata_backfill_blocked"))
            error = "No commentary transcript for this clip" if policy.get("metadata_backfill_blocked") else "No transcript for this clip"
            if save:
                self._save_state()
            return {
                "title": "",
                "error": error,
                "speech_policy": policy,
                "metadata_warning": self._moments[clip_index].get("metadata_warning", ""),
                "metadata_needs_context": self._moments[clip_index].get("metadata_needs_context", False),
            }
        self._refresh_clip_game_identity_for_metadata(
            clip_index,
            allow_network=True,
            force=creator_title_context is not None,
        )
        clip_context = self._ensure_metadata_vision_context(
            clip_index,
            self._title_context_for_clip(clip_index),
        )
        game_title = clip_context.get("game_title") or self._game_title_for_clip(clip_index)
        title = generate_title(transcript, game_title=game_title, clip_context=clip_context)
        generated_description = self._generated_description_for_clip(
            title,
            transcript,
            game_title,
            clip_context,
        )
        desc_parts = self._compose_clip_description(
            title,
            game_title,
            clip_context=clip_context,
            generated_text=generated_description,
        )
        description = desc_parts["description"]
        tags = self._tags_for_game(game_title, transcript, clip_context=clip_context)
        metadata_file = self._write_metadata_sidecar(clip_index, title, game_title, description, tags, clip_context)
        self._store_generated_metadata(
            clip_index,
            title,
            description,
            tags,
            game_title,
            metadata_file,
            clip_context,
            generated_description=desc_parts["generated_description"],
            custom_text=desc_parts["description_custom_text"],
            auto_hashtags=desc_parts["description_auto_hashtags"],
        )
        if save:
            self._save_state()
            self._record_metadata_learning_event(
                clip_index,
                title,
                game_title,
                reason="clip_metadata_rerolled",
            )
        moment = self._moments[clip_index] if isinstance(self._moments[clip_index], dict) else {}
        try:
            clip_filename = Path(self._results[clip_index]).name if clip_index < len(self._results) else ""
        except Exception:
            clip_filename = ""
        return {
            "clip_id": moment.get("clip_id") or "",
            "source_id": moment.get("source_id") or "",
            "clip_filename": clip_filename,
            "title": title,
            "description": description,
            "final_description": description,
            "generated_description": desc_parts["generated_description"],
            "description_custom_text": desc_parts["description_custom_text"],
            "description_auto_hashtags": desc_parts["description_auto_hashtags"],
            "tags": tags,
            "game_title": game_title,
            "creator_title_context": sanitize_creator_title_context(clip_context.get("creator_title_context")),
            "hashtags": desc_parts["recommended_hashtags"],
            "title_context": summarize_clip_context(transcript, game_title, clip_context),
            "metadata_file": metadata_file,
        }

    def _generate_auto_metadata_for_results(
        self,
        first_clip_index: int,
        clip_count: int,
        final_clip_debug: list[dict] | None = None,
        run_warnings: list[str] | None = None,
    ) -> list[dict]:
        """Write metadata and rename freshly rendered clips to their AI titles."""
        auto_metadata: list[dict] = []
        total = max(0, int(clip_count or 0))
        for offset in range(total):
            if self._cancel:
                raise CancelledError()
            clip_index = int(first_clip_index) + offset
            try:
                self._push(
                    "render",
                    96,
                    f"Clip {offset + 1}/{total}: Writing AI title and description...",
                )
                metadata = self.generate_title_for_clip(clip_index, save=False)
                if metadata and not metadata.get("error"):
                    old_metadata_file = metadata.get("metadata_file")
                    original_path = str(self._results[clip_index]) if 0 <= clip_index < len(self._results) else ""
                    rename_result = self.rename_clip(clip_index, metadata.get("title", ""), save=False)
                    current_path = str(self._results[clip_index]) if 0 <= clip_index < len(self._results) else ""
                    renamed = (
                        "filename" in rename_result
                        or ("path" in rename_result and not rename_result.get("error"))
                        or (current_path and original_path and current_path != original_path)
                    )
                    if renamed:
                        clip_context = self._title_context_for_clip(clip_index)
                        metadata_file = self._write_metadata_sidecar(
                            clip_index,
                            metadata.get("title", ""),
                            metadata.get("game_title", ""),
                            metadata.get("description", ""),
                            metadata.get("tags", ""),
                            clip_context,
                        )
                        self._delete_stale_metadata_sidecars(
                            [
                                old_metadata_file,
                                Path(original_path).with_suffix(".txt") if original_path else "",
                            ],
                            keep_path=metadata_file,
                            reason="title_reroll",
                        )
                        metadata["metadata_file"] = metadata_file
                        self._store_generated_metadata(
                            clip_index,
                            metadata.get("title", ""),
                            metadata.get("description", ""),
                            metadata.get("tags", ""),
                            metadata.get("game_title", ""),
                            metadata_file,
                            clip_context,
                            generated_description=metadata.get("generated_description"),
                            custom_text=metadata.get("description_custom_text"),
                            auto_hashtags=metadata.get("description_auto_hashtags"),
                        )
                    metadata["renamed"] = bool(renamed)
                    metadata["filename"] = rename_result.get(
                        "filename",
                        self._results[clip_index].name if 0 <= clip_index < len(self._results) else "",
                    )
                    metadata["clip_filename"] = metadata["filename"]
                    metadata["path"] = rename_result.get(
                        "path",
                        str(self._results[clip_index]) if 0 <= clip_index < len(self._results) else "",
                    )
                    auto_metadata.append({"clip_index": clip_index, **metadata})
                    if final_clip_debug is not None and offset < len(final_clip_debug):
                        if renamed:
                            final_clip_debug[offset]["path"] = metadata.get("path")
                            final_clip_debug[offset]["filename"] = metadata.get("filename")
                        final_clip_debug[offset]["generated_metadata"] = {
                            "clip_id": metadata.get("clip_id"),
                            "source_id": metadata.get("source_id"),
                            "clip_filename": metadata.get("clip_filename"),
                            "title": metadata.get("title"),
                            "description": metadata.get("description"),
                            "final_description": metadata.get("final_description"),
                            "generated_description": metadata.get("generated_description"),
                            "tags": metadata.get("tags"),
                            "game_title": metadata.get("game_title"),
                            "creator_title_context": metadata.get("creator_title_context"),
                            "metadata_file": metadata.get("metadata_file"),
                            "renamed": metadata.get("renamed"),
                            "filename": metadata.get("filename"),
                        }
                else:
                    error_text = str((metadata or {}).get("error") or "metadata_not_generated")
                    if run_warnings is not None:
                        warning_code = "no_selected_commentary_transcript" if "commentary" in error_text.lower() else "metadata_not_generated"
                        run_warnings.append(f"clip_{offset + 1}_{warning_code}")
                    if final_clip_debug is not None and offset < len(final_clip_debug):
                        moment = self._moments[clip_index] if 0 <= clip_index < len(self._moments) and isinstance(self._moments[clip_index], dict) else {}
                        policy = (metadata or {}).get("speech_policy")
                        if not isinstance(policy, dict):
                            policy = _clip_speech_policy_summary(moment)
                        final_clip_debug[offset]["generated_metadata"] = {
                            "error": error_text,
                            "title": "",
                            "speech_policy": policy,
                            "metadata_warning": (metadata or {}).get("metadata_warning") or moment.get("metadata_warning", ""),
                            "metadata_needs_context": bool((metadata or {}).get("metadata_needs_context") or moment.get("metadata_needs_context")),
                        }
            except CancelledError:
                raise
            except Exception as exc:
                print(f"[metadata] Auto metadata failed for clip {clip_index}: {exc}")
                if run_warnings is not None:
                    run_warnings.append(f"clip_{offset + 1}_auto_metadata_failed")
        return auto_metadata

    def rename_clip(self, clip_index, new_title, save=True):
        """Rename a clip file on disk to match a new title.

        Returns the new filename, or error.
        """
        if clip_index < 0 or clip_index >= len(self._results):
            return {"error": "Invalid clip index"}
        old_path = self._safe_clip_path(self._results[clip_index])
        if not old_path:
            return {"error": "File not found"}

        # Sanitize title for filesystem
        import re
        # Remove emojis and non-ASCII chars that cause issues on Windows
        safe = re.sub(r'[^\x20-\x7E]', '', new_title)
        safe = re.sub(r'[<>:"/\\|?*]', '', safe)
        safe = safe.strip('. ')[:80]
        if not safe:
            return {"error": "Title too short after sanitization"}

        ext = old_path.suffix
        new_name = f"{safe}{ext}"
        new_path = old_path.parent / new_name
        clips_dir = self._clips_dir()
        safe_new_path = self._safe_path_under(clips_dir, new_path)
        if not safe_new_path:
            return {"error": "Unsafe output path"}
        new_path = safe_new_path

        # Avoid collisions
        if new_path.exists() and new_path != old_path:
            counter = 2
            while new_path.exists():
                new_name = f"{safe} ({counter}){ext}"
                safe_new_path = self._safe_path_under(clips_dir, old_path.parent / new_name)
                if not safe_new_path:
                    return {"error": "Unsafe output path"}
                new_path = safe_new_path
                counter += 1

        try:
            old_path.rename(new_path)
            self._results[clip_index] = new_path
            if save:
                self._save_state()
            print(f"[rename] {old_path.name} → {new_path.name}")
            return {"filename": new_path.name, "path": str(new_path)}
        except Exception as e:
            return {"error": str(e)}

    def generate_and_rename_all(self):
        """Generate AI titles for all clips in a background thread.

        Returns immediately with {"ok": True}. Progress and results are
        pushed to the frontend via window.onTitleProgress and
        window.onTitlesDone callbacks.
        """
        threading.Thread(target=self._run_title_gen, daemon=True).start()
        return {"ok": True}

    def generate_and_rename_indices(self, indices):
        """Generate AI titles only for specific clip indices (e.g. a folder).

        Returns immediately with {"ok": True}. Progress and results are
        pushed to the frontend via window.onTitleProgress and
        window.onTitlesDone callbacks.
        """
        threading.Thread(target=self._run_title_gen, args=(indices,), daemon=True).start()
        return {"ok": True}

    def _run_title_gen(self, only_indices=None):
        """Background thread: generate titles, rename files, push results to JS.

        If only_indices is provided (list of ints), only those clip indices
        are transcribed and titled. Otherwise all clips are processed.
        """
        try:
            num_clips = len(self._results)
            print(f"[title-gen] {num_clips} clips, {len(self._moments)} moments in state")

            # Trim moments to match results (moments can accumulate beyond results
            # if clips were deleted or state got out of sync)
            if len(self._moments) > num_clips:
                self._moments = self._moments[:num_clips]
            # Pad if fewer
            while len(self._moments) < num_clips:
                self._moments.append({})

            # Determine which indices to process
            target_indices = only_indices if only_indices is not None else list(range(num_clips))
            # Filter to valid range
            target_indices = [i for i in target_indices if 0 <= i < num_clips]
            if not target_indices:
                self._js("window.onTitlesDone && window.onTitlesDone({error: 'No valid clips to process'})")
                return

            print(f"[title-gen] Processing {len(target_indices)} of {num_clips} clips")

            # Backfill any target clips missing transcripts
            missing = [i for i in target_indices
                       if not self._moments[i].get("transcript")]
            if missing:
                print(f"[title-gen] {len(missing)} clips missing transcripts, backfilling...")
                for idx, i in enumerate(missing):
                    self._js(f"window.onTitleProgress && window.onTitleProgress({idx}, {len(missing)}, 'Transcribing clip {i+1}...')")
                    self._backfill_transcript_single(i)
                self._save_state()

            # Build transcripts list — only for target indices, empty for others
            transcripts = [""] * num_clips
            game_titles = [""] * num_clips
            title_contexts = [{} for _ in range(num_clips)]
            for i in target_indices:
                transcripts[i] = self._moments[i].get("transcript", "")
                self._refresh_clip_game_identity_for_metadata(i, allow_network=True)
                title_contexts[i] = self._ensure_metadata_vision_context(
                    i,
                    self._title_context_for_clip(i),
                )
                game_titles[i] = title_contexts[i].get("game_title") or self._game_title_for_clip(i)
            if not any(transcripts[i] for i in target_indices):
                self._js("window.onTitlesDone && window.onTitlesDone({error: 'No transcripts available'})")
                return

            # Store original stem before renaming
            import re
            for i in target_indices:
                p = self._results[i]
                if i < len(self._moments) and not self._moments[i].get("source_stem"):
                    m = re.match(r'^(.+?)_viral\d+', p.name)
                    self._moments[i]["source_stem"] = m.group(1) if m else p.stem

            llm_available = is_ollama_model_ready(DEFAULT_MODEL)

            def _on_progress(done, total, title):
                self._js(f"window.onTitleProgress && window.onTitleProgress({done}, {total}, `{self._esc(title or '')}`)")

            titles = generate_titles_batch(
                transcripts, DEFAULT_MODEL, on_progress=_on_progress,
                game_titles=game_titles,
                clip_contexts=title_contexts,
            )

            renamed = 0
            results = []
            for i in target_indices:
                title = titles[i] if i < len(titles) else ""
                if not title:
                    results.append({"index": i, "title": "", "renamed": False})
                    continue
                original_path = self._results[i] if i < len(self._results) else None
                old_metadata_file = ""
                if i < len(self._moments) and isinstance(self._moments[i], dict):
                    generated_metadata = self._moments[i].get("generated_metadata")
                    if isinstance(generated_metadata, dict):
                        old_metadata_file = str(generated_metadata.get("metadata_file") or "")
                r = self.rename_clip(i, title, save=False)
                ok = "filename" in r
                if ok:
                    renamed += 1
                game_title = game_titles[i] if i < len(game_titles) else ""
                clip_context = title_contexts[i] if i < len(title_contexts) else {}
                generated_description = self._generated_description_for_clip(
                    title,
                    transcripts[i] if i < len(transcripts) else "",
                    game_title,
                    clip_context,
                )
                desc_parts = self._compose_clip_description(
                    title,
                    game_title,
                    clip_context=clip_context,
                    generated_text=generated_description,
                )
                description = desc_parts["description"]
                tags = self._tags_for_game(
                    game_title,
                    transcripts[i] if i < len(transcripts) else "",
                    clip_context=clip_context,
                )
                metadata_file = self._write_metadata_sidecar(i, title, game_title, description, tags, clip_context)
                self._delete_stale_metadata_sidecars(
                    [
                        old_metadata_file,
                        Path(original_path).with_suffix(".txt") if original_path else "",
                    ],
                    keep_path=metadata_file,
                    reason="title_reroll",
                )
                self._store_generated_metadata(
                    i,
                    title,
                    description,
                    tags,
                    game_title,
                    metadata_file,
                    clip_context,
                    generated_description=desc_parts["generated_description"],
                    custom_text=desc_parts["description_custom_text"],
                    auto_hashtags=desc_parts["description_auto_hashtags"],
                )
                results.append({
                    "index": i,
                    "title": title,
                    "game_title": game_title,
                    "description": description,
                    "final_description": description,
                    "generated_description": desc_parts["generated_description"],
                    "description_custom_text": desc_parts["description_custom_text"],
                    "description_auto_hashtags": desc_parts["description_auto_hashtags"],
                    "recommended_hashtags": desc_parts["recommended_hashtags"],
                    "tags": tags,
                    "creator_title_context": sanitize_creator_title_context(clip_context.get("creator_title_context")),
                    "title_context": summarize_clip_context(transcripts[i], game_title, clip_context),
                    "metadata_file": metadata_file,
                    "renamed": ok,
                    "filename": r.get("filename", self._results[i].name if i < len(self._results) else ""),
                })

            self._save_state()

            # Push results to frontend
            import json
            payload = json.dumps({"titles": results, "renamed": renamed, "llm": llm_available, "total": len(titles)})
            self._js(f"window.onTitlesDone && window.onTitlesDone({payload})")

        except Exception as e:
            print(f"[title-gen] Error: {e}")
            self._js(f"window.onTitlesDone && window.onTitlesDone({{error: `{self._esc(str(e))}`}})")

    def _backfill_transcript_single(self, clip_index):
        """Transcribe a single clip to fill in its transcript."""
        import tempfile
        if clip_index >= len(self._results):
            return
        p = self._results[clip_index]
        if not p.exists():
            return

        # Ensure moments slot exists
        while len(self._moments) <= clip_index:
            self._moments.append({})

        moment = self._moments[clip_index] if isinstance(self._moments[clip_index], dict) else {}
        policy = _clip_speech_policy_summary(moment)
        if policy.get("metadata_backfill_blocked"):
            moment["speech_policy"] = policy
            moment["metadata_warning"] = policy.get("warning", "")
            moment["metadata_needs_context"] = True
            self._moments[clip_index] = moment
            print(
                f"  [title-gen] Clip {clip_index + 1}: skipped rendered-audio backfill "
                "because the selected commentary track had no transcript"
            )
            return

        try:
            wav = Path(tempfile.gettempdir()) / f"viria_backfill_{clip_index}.wav"
            extract_audio_clip(p, 0, 60, wav)  # max 60s
            if wav.exists() and wav.stat().st_size > 1000:
                words = transcribe_clip(wav, model_size=WHISPER_MODEL, language=None)
                transcript = " ".join(w.get("text", "") for w in words).strip()
                if transcript:
                    self._moments[clip_index]["transcript"] = transcript
                    self._moments[clip_index]["transcript_source"] = "backfill"
                    self._moments[clip_index]["transcript_backfilled"] = True
                    self._moments[clip_index].setdefault("subtitle_generated", False)
                    self._moments[clip_index].setdefault("subtitles_burned", False)
                    print(f"  [+] Clip {clip_index + 1}: {len(transcript)} chars transcribed")
            try:
                wav.unlink(missing_ok=True)
            except Exception:
                pass
        except Exception as e:
            print(f"  [!] Backfill failed for clip {clip_index + 1}: {e}")

    def get_ollama_models(self):
        """Return available Ollama models for title generation."""
        models = list_ollama_models()
        return {"models": models, "available": len(models) > 0}

    def _find_ollama_executable(self) -> Path | None:
        """Find an installed Ollama binary without downloading or installing it."""
        for raw in (shutil.which("ollama.exe"), shutil.which("ollama")):
            if raw:
                try:
                    path = Path(raw).resolve()
                    if path.exists() and path.is_file():
                        return path
                except Exception:
                    continue
        for candidate in OLLAMA_STARTUP_PATHS:
            try:
                path = candidate.expanduser().resolve()
                if path.exists() and path.is_file():
                    return path
            except Exception:
                continue
        return None

    def _try_start_ollama_server(self) -> dict:
        """Start a local Ollama server when installed but not already running."""
        now = time.monotonic()
        last_attempt = float(getattr(self, "_ollama_auto_start_attempt", 0.0) or 0.0)
        if now - last_attempt < 20:
            return {"attempted": False, "reason": "recent_attempt"}
        self._ollama_auto_start_attempt = now

        exe = self._find_ollama_executable()
        if not exe:
            return {"attempted": False, "reason": "not_installed"}
        try:
            subprocess.Popen(
                [str(exe), "serve"],
                cwd=str(exe.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            time.sleep(1.0)
            return {"attempted": True, "path": str(exe)}
        except Exception as exc:
            return {"attempted": True, "path": str(exe), "error": str(exc)}

    def get_ollama_status(self):
        """Return whether Ollama is running and ready for text and vision AI."""
        status = ollama_status()
        startup = {"attempted": False}
        if not status.get("running"):
            startup = self._try_start_ollama_server()
            if startup.get("attempted"):
                status = ollama_status()
        vision = ollama_vision_status(status.get("models") if status.get("running") else [])
        status["text_model"] = {
            "model": status.get("model", DEFAULT_MODEL),
            "model_ready": bool(status.get("model_ready")),
            "using_ollama": bool(status.get("using_ollama")),
        }
        status["vision"] = {
            "running": bool(status.get("running")),
            "preferred_model": DEFAULT_VISION_MODEL,
            "model": vision.get("model") or "",
            "model_ready": bool(vision.get("model_ready")),
            "using_vision": bool(status.get("running") and vision.get("model_ready")),
            "supported_model_hints": vision.get("supported_model_hints", []),
        }
        status["vision_model"] = status["vision"]["model"]
        status["vision_model_ready"] = status["vision"]["model_ready"]
        status["using_ollama_vision"] = status["vision"]["using_vision"]
        path_on_env = shutil.which("ollama.exe") or shutil.which("ollama")
        install_path = self._find_ollama_executable()
        if install_path:
            status["install_path"] = str(install_path)
        status["service_running"] = bool(status.get("running"))
        status["binary_on_path"] = bool(path_on_env)
        status["installed"] = bool(install_path)
        status["auto_start"] = startup
        return status

    def open_ollama_folder(self):
        """Open the local Ollama install folder when it can be found."""
        install_path = self._find_ollama_executable()
        if not install_path:
            return {"error": "Ollama was not found on PATH. Use Install Ollama first."}
        folder = Path(install_path).resolve().parent
        try:
            os.startfile(str(folder))
        except Exception as e:
            return {"error": str(e), "path": str(folder)}
        return {"ok": True, "path": str(folder)}

    def open_ollama_download(self):
        """Open the official Ollama Windows download page."""
        try:
            webbrowser.open(OLLAMA_DOWNLOAD_URL)
        except Exception as e:
            return {"error": str(e), "url": OLLAMA_DOWNLOAD_URL}
        return {
            "ok": True,
            "url": OLLAMA_DOWNLOAD_URL,
            "docs_url": OLLAMA_WINDOWS_DOCS_URL,
        }

    def open_youtube_oauth_console(self):
        """Open Google Cloud credentials page for YouTube OAuth setup."""
        try:
            webbrowser.open(YOUTUBE_CREDENTIALS_URL)
        except Exception as e:
            return {"error": str(e), "url": YOUTUBE_CREDENTIALS_URL}
        return {"ok": True, "url": YOUTUBE_CREDENTIALS_URL}

    def open_ffmpeg_download(self):
        """Open the official FFmpeg download page."""
        try:
            webbrowser.open(FFMPEG_DOWNLOAD_URL)
        except Exception as e:
            return {"error": str(e), "url": FFMPEG_DOWNLOAD_URL}
        return {"ok": True, "url": FFMPEG_DOWNLOAD_URL}

    def ensure_ollama_model(self, model=None):
        """Ensure the title generation model is downloaded. Auto-pulls if needed."""
        if model and str(model).strip() != DEFAULT_MODEL:
            return {"ready": False, "model": DEFAULT_MODEL, "error": "Only the approved ViriaRevive text model can be downloaded from the app."}
        model = DEFAULT_MODEL
        ready = ensure_model(model)
        return {"ready": ready, "model": model}

    def ensure_ollama_vision_model(self, model=None):
        """Ensure the local vision model is downloaded. Auto-pulls if needed."""
        if model and str(model).strip() != DEFAULT_VISION_MODEL:
            return {"ready": False, "model": DEFAULT_VISION_MODEL, "error": "Only the approved ViriaRevive vision model can be downloaded from the app."}
        model = DEFAULT_VISION_MODEL
        ready = ensure_model(model)
        return {"ready": ready, "model": model}


    # ── Exposed: YouTube connection ───────────────────────────────────────

    def connect_youtube(self):
        """Add a YouTube account via OAuth flow. Supports multiple accounts."""
        try:
            result = add_account()
            return {"ok": True, "account": result}
        except FileNotFoundError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Connection failed: {e}"}

    def add_youtube_account(self):
        """Alias for connect_youtube — adds another account."""
        return self.connect_youtube()

    def disconnect_youtube(self, account_id=None):
        """Disconnect a specific account, or all accounts if no ID given."""
        before_accounts = list_accounts()
        before_ids = {str(account.get("id")) for account in before_accounts if account.get("id")}
        disconnect(account_id)
        after_ids = {str(account.get("id")) for account in list_accounts() if account.get("id")}
        removed_ids = before_ids - after_ids
        if account_id is not None:
            removed_ids.add(str(account_id))
        if removed_ids:
            changed = False
            with self._get_state_lock():
                for item in self._scheduled:
                    item_account = item.get("account_id")
                    item_account_id = str(item_account) if item_account else ""
                    if item_account_id in removed_ids or (account_id is None and not item_account_id):
                        item["scheduler_status"] = "account_disconnected"
                        item["scheduler_note"] = "Reconnect YouTube or choose another account before upload"
                        changed = True
                if changed:
                    self._save_state()
            if changed:
                self._js("window.onScheduleUpdated()")
        return {"ok": True}

    def youtube_status(self):
        accounts = list_accounts(validate=True)
        return {
            "connected": any(bool(account.get("usable")) for account in accounts),
            "configured_account_count": len(accounts),
            "usable_account_count": sum(1 for account in accounts if account.get("usable")),
            "accounts": accounts,
        }

    def get_channels(self):
        try:
            return {"channels": list_channels()}
        except Exception as e:
            return {"error": str(e), "channels": []}

    def get_subtitle_styles(self):
        """Return available subtitle styles for the UI picker."""
        return {"styles": get_available_styles()}

    def get_effects(self):
        """Return available video effect presets."""
        return {"effects": get_effects_list()}

    def list_music(self):
        """List audio files in the music/ folder."""
        tracks = []
        if MUSIC_DIR.exists():
            for p in sorted(MUSIC_DIR.iterdir()):
                safe_path = self._safe_path_under(MUSIC_DIR, p)
                if not safe_path or not safe_path.is_file():
                    continue
                if safe_path.suffix.lower() in ('.mp3', '.wav', '.aac', '.ogg', '.m4a', '.flac'):
                    tracks.append({
                        "filename": safe_path.name,
                        "path": str(safe_path),
                        "size_mb": round(safe_path.stat().st_size / (1024 * 1024), 1),
                    })
        return {"tracks": tracks, "music_dir": str(MUSIC_DIR)}

    @staticmethod
    def _safe_child_path(root: Path, filename) -> Path | None:
        """Resolve a user-visible filename and keep it under the intended root."""
        name = str(filename or "").strip()
        if not name:
            return None
        try:
            root_resolved = root.resolve()
            path = (root_resolved / name).resolve()
            path.relative_to(root_resolved)
            return path
        except (OSError, ValueError):
            return None

    @staticmethod
    def _safe_path_under(root: Path, path) -> Path | None:
        """Resolve a persisted path and keep it under the intended root."""
        raw = str(path or "").strip()
        if not raw:
            return None
        try:
            root_resolved = root.resolve()
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = root_resolved / candidate
            resolved = candidate.resolve()
            resolved.relative_to(root_resolved)
            return resolved
        except (OSError, ValueError):
            return None

    def _safe_clip_path(self, path) -> Path | None:
        """Return an existing clip path only when it is inside the active clips folder."""
        resolved = self._safe_path_under(self._clips_dir(), path)
        if (
            resolved
            and resolved.exists()
            and resolved.is_file()
            and resolved.suffix.lower() in VIDEO_FILE_EXTS
        ):
            return resolved
        return None

    @staticmethod
    def _is_windows_file_lock_error(exc: Exception) -> bool:
        return isinstance(exc, PermissionError) or getattr(exc, "winerror", None) == 32

    def _unlink_clip_file(self, path: Path, *, attempts: int = 8, delay: float = 0.16) -> tuple[bool, str]:
        """Delete a clip file, allowing the embedded video player a moment to release it."""
        last_error: Exception | None = None
        for attempt in range(max(1, int(attempts))):
            try:
                path.unlink()
                return True, ""
            except FileNotFoundError:
                return True, ""
            except Exception as exc:
                last_error = exc
                if not self._is_windows_file_lock_error(exc) or attempt >= attempts - 1:
                    break
                time.sleep(max(0.01, float(delay)))
        if last_error and self._is_windows_file_lock_error(last_error):
            return (
                False,
                "This video is still open in the preview/player or another app. Close or pause it, then try deleting again.",
            )
        return False, str(last_error or "Delete failed")

    def _delete_clip_sidecar(self, clip_path, *, reason: str = "") -> bool:
        """Delete the generated .txt metadata sidecar that belongs to a clip."""
        video_path = self._safe_path_under(self._clips_dir(), clip_path)
        if not video_path or video_path.suffix.lower() not in VIDEO_FILE_EXTS:
            return False
        return self._delete_metadata_sidecar_path(video_path.with_suffix(".txt"), reason=reason)

    @staticmethod
    def _looks_like_generated_metadata_sidecar(sidecar_path: Path) -> bool:
        try:
            text = sidecar_path.read_text(encoding="utf-8", errors="ignore")[:4096]
        except Exception:
            return False
        return (
            text.startswith("Title:")
            and "\nDescription:" in text
            and "\nAnalysis Context:" in text
        )

    @staticmethod
    def _metadata_sidecar_field(text: str, label: str) -> str:
        match = re.search(rf"(?m)^{re.escape(label)}:\s*(.+?)\s*$", text)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _metadata_sidecar_block(text: str, label: str, next_labels: tuple[str, ...]) -> str:
        next_pattern = "|".join(re.escape(item) for item in next_labels)
        match = re.search(
            rf"(?ms)^{re.escape(label)}:\s*\n(.*?)(?=^\s*(?:{next_pattern}):|\Z)",
            text,
        )
        return match.group(1).strip() if match else ""

    def _read_generated_metadata_sidecar(self, clip_path: Path, clip_context: dict | None = None) -> dict:
        """Recover generated metadata from the .txt sidecar next to a clip."""
        sidecar = clip_path.with_suffix(".txt")
        if not sidecar.exists() or not sidecar.is_file() or not self._looks_like_generated_metadata_sidecar(sidecar):
            return {}
        try:
            text = sidecar.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return {}
        title = self._metadata_sidecar_field(text, "Title")
        description = self._metadata_sidecar_block(
            text,
            "Description",
            ("Tags", "Hashtags", "Game", "Clip", "Analysis Context"),
        )
        tags = self._metadata_sidecar_field(text, "Tags")
        game_title = self._metadata_sidecar_field(text, "Game")
        if game_title.lower() == "unknown":
            game_title = ""
        creator_context = sanitize_creator_title_context(self._metadata_sidecar_field(text, "Creator Context"))
        if not title and not description and not tags:
            return {}
        return {
            "title": title,
            "description": description,
            "final_description": description,
            "generated_description": description.split("\n\n", 1)[0].strip() if description else title,
            "description_custom_text": self._description_profile()["custom_text"],
            "description_auto_hashtags": self._description_profile()["auto_hashtags"],
            "recommended_hashtags": recommended_hashtags(game_title),
            "tags": tags,
            "game_title": game_title,
            "metadata_file": str(sidecar),
            "creator_title_context": creator_context,
            "title_context": summarize_clip_context(
                (clip_context or {}).get("transcript", ""),
                game_title,
                clip_context,
            ),
            "recovered_from_sidecar": True,
        }

    def _hydrate_generated_metadata_from_sidecar(self, idx: int, path: Path, moment: dict) -> bool:
        if not isinstance(moment, dict):
            return False
        existing = moment.get("generated_metadata")
        if isinstance(existing, dict) and existing.get("title") and existing.get("metadata_file"):
            return False
        metadata = self._read_generated_metadata_sidecar(path, moment)
        if not metadata:
            return False
        moment["generated_metadata"] = metadata
        if metadata.get("creator_title_context"):
            moment["creator_title_context"] = metadata["creator_title_context"]
        if metadata.get("game_title"):
            moment["game_title"] = metadata["game_title"]
        if 0 <= idx < len(self._moments):
            self._moments[idx] = moment
        return True

    def _prune_orphan_metadata_sidecars(self) -> int:
        """Remove app-generated .txt metadata files whose matching video is gone."""
        clips_dir = self._clips_dir()
        if not clips_dir.exists():
            return 0
        deleted = 0
        for path in clips_dir.glob("*.txt"):
            sidecar = self._safe_path_under(clips_dir, path)
            if not sidecar or not sidecar.is_file():
                continue
            has_matching_video = any(
                (sidecar.parent / f"{sidecar.stem}{ext}").exists()
                for ext in VIDEO_FILE_EXTS
            )
            if has_matching_video or not self._looks_like_generated_metadata_sidecar(sidecar):
                continue
            if self._delete_metadata_sidecar_path(sidecar, reason="orphan_metadata"):
                deleted += 1
        return deleted

    def _unique_clip_output_path(self, stem: str, clip_num: int) -> Path:
        """Return a clip output path without deleting an existing rendered clip."""
        clean_stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", str(stem or "clip")).strip(" ._") or "clip"
        safe_num = max(1, int(clip_num or 1))
        clips_dir = self._clips_dir()
        base = clips_dir / f"{clean_stem}_viral{safe_num}.mp4"
        if not base.exists():
            return base
        for suffix in range(2, 1000):
            candidate = clips_dir / f"{clean_stem}_viral{safe_num}_{suffix}.mp4"
            if not candidate.exists():
                return candidate
        return clips_dir / f"{clean_stem}_viral{safe_num}_{uuid.uuid4().hex[:8]}.mp4"

    def _unique_montage_output_path(self, stem: str) -> Path:
        """Return a montage output path without deleting existing files."""
        clean_stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", str(stem or "montage")).strip(" ._") or "montage"
        clips_dir = self._clips_dir()
        base = clips_dir / f"{clean_stem}_montage1.mp4"
        if not base.exists():
            return base
        for suffix in range(2, 1000):
            candidate = clips_dir / f"{clean_stem}_montage{suffix}.mp4"
            if not candidate.exists():
                return candidate
        return clips_dir / f"{clean_stem}_montage_{uuid.uuid4().hex[:8]}.mp4"

    def get_music_url(self, filename):
        """Return a local HTTP URL for a music file so the browser can play it."""
        music_path = self._safe_child_path(MUSIC_DIR, filename)
        if music_path and music_path.exists() and music_path.is_file():
            return {"url": f"http://127.0.0.1:{self._music_port}/{music_path.name}"}
        return {"url": None}

    def open_music_folder(self):
        """Open the music folder in system explorer."""
        MUSIC_DIR.mkdir(exist_ok=True)
        try:
            os.startfile(str(MUSIC_DIR))
        except Exception:
            pass
        return {"ok": True}

    def open_data_folder(self):
        """Open the app data folder in system explorer."""
        try:
            os.startfile(str(APP_DATA_DIR))
        except Exception as e:
            return {"error": str(e)}
        return {"ok": True}

    def open_app_bin_folder(self):
        """Open the app-local bin folder used for ffmpeg/ffprobe drop-ins."""
        try:
            BIN_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(BIN_DIR))
        except Exception as e:
            return {"error": str(e)}
        return {"ok": True}

    def get_music_waveform(self, filename):
        """Generate waveform data + duration for a music file.

        Returns {peaks: [...], duration: float} where peaks is ~200 normalized
        amplitude values (0.0-1.0) representing the waveform shape.
        """
        from subprocess_utils import run as _run
        music_path = self._safe_child_path(MUSIC_DIR, filename)
        if not music_path or not music_path.exists() or not music_path.is_file():
            return {"error": "File not found", "peaks": [], "duration": 0}

        try:
            # Get duration
            dr = _run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(music_path)],
                capture_output=True, text=True, timeout=15,
            )
            duration = float(dr.stdout.strip())

            # Extract raw PCM samples at low sample rate for waveform
            # 200 peaks over the full duration → sample_rate ~ 200/duration
            num_peaks = 200
            sample_rate = max(100, int(num_peaks / max(duration, 0.1)))

            pr = _run(
                ["ffmpeg", "-y", "-i", str(music_path),
                 "-ac", "1",  # mono
                 "-ar", str(sample_rate),  # low sample rate
                 "-f", "s16le",  # raw 16-bit PCM
                 "-"],
                capture_output=True, timeout=30,
            )

            if pr.returncode != 0:
                return {"error": "Failed to read audio", "peaks": [], "duration": duration}

            import struct
            raw = pr.stdout
            # Parse 16-bit signed samples
            n_samples = len(raw) // 2
            if n_samples == 0:
                return {"peaks": [], "duration": duration}

            samples = struct.unpack(f"<{n_samples}h", raw[:n_samples * 2])

            # Bucket into num_peaks groups and take max absolute amplitude
            bucket_size = max(1, n_samples // num_peaks)
            peaks = []
            for i in range(0, n_samples, bucket_size):
                bucket = samples[i:i + bucket_size]
                peak = max(abs(s) for s in bucket) / 32768.0
                peaks.append(round(peak, 3))

            # Trim or pad to exactly num_peaks
            peaks = peaks[:num_peaks]

            return {"peaks": peaks, "duration": round(duration, 2)}

        except Exception as e:
            return {"error": str(e), "peaks": [], "duration": 0}

    # ── Exposed: processing ──────────────────────────────────────────────

    def start_processing(self, url, settings):
        if self._processing:
            return {"error": "Already processing"}
        self._processing = True
        self._cancel = False
        from subprocess_utils import reset_cancel
        reset_cancel()
        # Store pre-existing results count so _run_pipeline appends instead of replacing
        self._results_before = len(self._results)
        threading.Thread(target=self._run_pipeline, args=(url, settings), daemon=True).start()
        return {"ok": True}

    def resume_latest_candidate_render(self):
        """Render clips from the newest saved candidate debug without reanalysis."""
        if os.environ.get("VIRIAREVIVE_ENABLE_DEBUG_RECOVERY") != "1":
            return {"error": "Candidate debug recovery is disabled outside developer sessions"}
        if self._processing:
            return {"error": "Already processing"}
        debug_path = self._latest_candidate_debug_path()
        if not debug_path:
            return {"error": "No saved candidate analysis with selected clips was found"}
        self._processing = True
        self._cancel = False
        from subprocess_utils import reset_cancel
        reset_cancel()
        threading.Thread(target=self._run_candidate_debug_recovery, args=(debug_path,), daemon=True).start()
        return {"ok": True, "debug_path": str(debug_path)}

    def cancel_processing(self):
        self._cancel = True
        from subprocess_utils import request_cancel
        request_cancel()
        return {"ok": True}

    def cancel_upload(self):
        """Cancel an active manual upload between YouTube upload chunks."""
        with self._get_state_lock():
            self._cancel = True
        return {"ok": True}

    def _classify_selected_moments(
        self,
        selected: list[dict],
        video_path: Path,
        *,
        enabled: bool,
        max_ollama: int = 8,
        classification_cache: dict | None = None,
        game_context: dict | None = None,
    ) -> dict:
        """Add optional local AI labels to selected moments without changing output."""
        selected = [item for item in (selected or []) if isinstance(item, dict)]
        max_ollama = max(0, int(max_ollama or 0))
        classification_cache = classification_cache if isinstance(classification_cache, dict) else {}
        report = {
            "schema_version": 1,
            "enabled": bool(enabled),
            "status": "disabled" if not enabled else "not_started",
            "model": DEFAULT_MODEL,
            "selection_impact": "none",
            "output_changed": False,
            "prompt_scope": "selected_clips_compact_transcript_ranker_visual_metadata_creator_learning",
            "selected_count": len(selected),
            "classified_count": 0,
            "ollama_ready": False,
            "max_ollama_candidates": max_ollama,
            "ollama_attempted_count": 0,
            "reused_shadow_count": 0,
            "fallback_count": 0,
            "statuses": {},
            "ai_viral_potential": {
                "schema_version": 1,
                "mode": "selected_ai_viral_potential_metadata",
                "diagnostic_only": True,
                "selection_impact": "none",
                "output_changed": False,
                "score_field": "ai_viral_score",
                "average_score": None,
                "scored_count": 0,
            },
        }
        if not enabled or not selected:
            if enabled and not selected:
                report["status"] = "no_selected_clips"
            return report

        try:
            ollama_ready = is_ollama_model_ready(DEFAULT_MODEL)
        except Exception:
            ollama_ready = False
        report["ollama_ready"] = bool(ollama_ready)
        game_context = game_context if isinstance(game_context, dict) else self._game_context_for_source(video_path, allow_network=False)
        game_title = game_context.get("label") or self._infer_game_title_from_path(video_path)
        report["game_context"] = compact_game_context_for_prompt(game_context)
        learning_prompt_context = self._feedback_learning_prompt_context()
        report["learning_context_enabled"] = bool(learning_prompt_context.get("enabled"))
        viral_scores: list[int] = []

        for item in selected:
            moment = item.get("moment") if isinstance(item.get("moment"), dict) else {}
            transcript = moment.get("transcript") or item.get("transcript") or ""
            cache_key = self._ai_moment_cache_key(item)
            cached = classification_cache.get(cache_key) if cache_key else None
            cached_status = str((cached or {}).get("status") or "").lower() if isinstance(cached, dict) else ""
            cache_is_reusable = (
                isinstance(cached, dict)
                and cached_status not in {"ollama_error", "ollama_timeout", "error", "invalid_response"}
            )
            if cache_is_reusable:
                classification = dict(cached)
                report["reused_shadow_count"] += 1
            else:
                use_ollama = bool(ollama_ready and report["ollama_attempted_count"] < max_ollama)
                if use_ollama:
                    report["ollama_attempted_count"] += 1
                classification = classify_moment_ai(
                    transcript,
                    game_title=game_title,
                    clip_context={**moment, "game_context": game_context, "feedback_learning_context": learning_prompt_context},
                    enabled=True,
                    model=DEFAULT_MODEL,
                    ollama_ready=use_ollama,
                )
                if ollama_ready and not use_ollama:
                    classification["status"] = "ollama_skipped_limit"
                    classification["provider"] = "heuristic"
                    classification["fallback_used"] = True
            attach_ai_moment_classification(item, classification)
            report["classified_count"] += 1
            status = str(classification.get("status") or "unknown")
            report["statuses"][status] = int(report["statuses"].get(status, 0)) + 1
            if classification.get("fallback_used"):
                report["fallback_count"] += 1
            clean = item.get("ai_moment_classification") if isinstance(item.get("ai_moment_classification"), dict) else {}
            viral_score = clean.get("ai_viral_score")
            if isinstance(viral_score, int):
                viral_scores.append(viral_score)

        if viral_scores:
            report["ai_viral_potential"]["scored_count"] = len(viral_scores)
            report["ai_viral_potential"]["average_score"] = round(sum(viral_scores) / len(viral_scores), 2)
        report["status"] = "ok" if report["classified_count"] else "no_classifications"
        return report

    @staticmethod
    def _ai_moment_cache_key(item: dict) -> str:
        if not isinstance(item, dict):
            return ""
        candidate = item.get("candidate") if isinstance(item.get("candidate"), dict) else {}
        moment = item.get("moment") if isinstance(item.get("moment"), dict) else {}
        transcript = str(moment.get("transcript") or item.get("transcript") or "")
        transcript_hash = hashlib.sha1(transcript[:1200].encode("utf-8", errors="ignore")).hexdigest()[:12]
        return "|".join(
            str(part)
            for part in (
                candidate.get("candidate_rank", ""),
                candidate.get("candidate_kind", ""),
                moment.get("start", candidate.get("start", "")),
                moment.get("end", candidate.get("end", "")),
                candidate.get("peak_time", ""),
                transcript_hash,
            )
        )

    @staticmethod
    def _creator_safe_near_miss_candidate(
        item: dict,
        *,
        min_quality: float,
        min_words: int = 8,
    ) -> bool:
        """Return True when a rejected candidate is safe to inspect as a best-available option."""
        if not isinstance(item, dict) or item.get("accepted"):
            return False
        if str(item.get("reject_reason") or "") != "low_transcript_quality":
            return False
        quality = _safe_float_value(item.get("quality_score"), 0.0)
        if quality < min_quality:
            return False
        word_count = int(item.get("subtitle_word_count") or item.get("word_count") or 0)
        if word_count < min_words:
            return False
        music_guard = item.get("music_lyrics_guard") if isinstance(item.get("music_lyrics_guard"), dict) else {}
        if music_guard.get("reject_candidate"):
            return False
        speech_source = item.get("speech_source") if isinstance(item.get("speech_source"), dict) else {}
        primary_source = str(speech_source.get("primary_source") or "").lower()
        if primary_source in {"game", "game_or_npc", "npc", "music", "music_or_lyrics"}:
            return False
        if speech_source:
            creator_safe = bool(speech_source.get("creator_safe"))
            game_prob = _safe_float_value(speech_source.get("game_or_npc_probability"), 0.0)
            music_prob = _safe_float_value(speech_source.get("music_or_lyrics_probability"), 0.0)
            if not creator_safe and max(game_prob, music_prob) >= 0.45:
                return False
        commentary_guard = item.get("commentary_guard") if isinstance(item.get("commentary_guard"), dict) else {}
        commentary_summary = (
            commentary_guard.get("summary")
            if isinstance(commentary_guard.get("summary"), dict)
            else {}
        )
        if str(commentary_summary.get("primary_label") or "").lower() == "game_narration":
            return False
        commentary_penalty = _safe_float_value(item.get("commentary_guard_selection_penalty"), 0.0)
        speech_penalty = _safe_float_value(item.get("speech_source_penalty"), 0.0)
        if commentary_penalty >= 0.15 or speech_penalty >= 0.15:
            return False
        return True

    @staticmethod
    def _relative_near_miss_floor(evaluations: list[dict], *, max_count: int, floor_cap: float = 0.45) -> float:
        qualities = sorted(
            (
                _safe_float_value(item.get("quality_score"), 0.0)
                for item in (evaluations or [])
                if isinstance(item, dict) and str(item.get("reject_reason") or "") == "low_transcript_quality"
            ),
            reverse=True,
        )
        if not qualities:
            return 0.34
        idx = min(len(qualities) - 1, max(0, int(max(1, max_count or 1) * 2) - 1))
        return max(0.30, min(float(floor_cap), qualities[idx]))

    def _ai_shadow_shortlist(
        self,
        evaluations: list[dict],
        *,
        max_count: int,
        score_key: str,
        include_near_misses: bool = False,
    ) -> list[dict]:
        def _score(item: dict, key: str, default: float = 0.0) -> float:
            try:
                value = item.get(key, default) if isinstance(item, dict) else default
                if value is None:
                    value = default
                score = float(value)
                return score if math.isfinite(score) else default
            except (TypeError, ValueError):
                return default

        max_count = max(0, int(max_count or 0))
        if max_count <= 0:
            return []
        accepted = [
            item for item in (evaluations or [])
            if isinstance(item, dict) and item.get("accepted")
        ]
        ordered_accepted = sorted(
            accepted,
            key=lambda item: (
                _score(item, score_key, _score(item, "learned_quality_score", _score(item, "quality_score", 0.0))),
                _score(item, "learned_quality_score", _score(item, "quality_score", 0.0)),
                _score(item, "quality_score", 0.0),
                -_score(item.get("candidate") if isinstance(item.get("candidate"), dict) else {}, "candidate_rank", 9999),
            ),
            reverse=True,
        )
        near_miss_budget = max(0, max_count - len(ordered_accepted)) if include_near_misses else 0
        near_misses: list[dict] = []
        if near_miss_budget:
            relative_floor = self._relative_near_miss_floor(evaluations, max_count=max_count, floor_cap=0.44)
            for item in evaluations or []:
                if not self._creator_safe_near_miss_candidate(item, min_quality=relative_floor):
                    continue
                moment = item.get("moment") if isinstance(item.get("moment"), dict) else {}
                ranker = moment.get("ranker") if isinstance(moment.get("ranker"), dict) else {}
                item["ai_rescue_candidate"] = True
                item["ai_rescue_reason"] = "creator_safe_near_miss"
                item["ai_rescue_relative_floor"] = round(float(relative_floor), 4)
                moment["ai_rescue_candidate"] = True
                moment["ai_rescue_reason"] = "creator_safe_near_miss"
                ranker["ai_rescue_candidate"] = True
                ranker["ai_rescue_reason"] = "creator_safe_near_miss"
                ranker["ai_rescue_relative_floor"] = round(float(relative_floor), 4)
                moment["ranker"] = ranker
                near_misses.append(item)
        ordered_near_misses = sorted(
            near_misses,
            key=lambda item: (
                _score(item, score_key, _score(item, "learned_quality_score", _score(item, "quality_score", 0.0))),
                _score(item, "learned_quality_score", _score(item, "quality_score", 0.0)),
                _score(item, "quality_score", 0.0),
                -_score(item.get("candidate") if isinstance(item.get("candidate"), dict) else {}, "candidate_rank", 9999),
            ),
            reverse=True,
        )
        if include_near_misses:
            return (ordered_accepted[:max_count] + ordered_near_misses[:near_miss_budget])[:max_count]
        return ordered_accepted[:max_count]

    def _multimodal_near_miss_shortlist(
        self,
        evaluations: list[dict],
        *,
        max_count: int,
        exclude_ids: set[int] | None = None,
        score_key: str,
    ) -> list[dict]:
        """Return close rejected candidates that local vision may be able to rescue."""
        def _score(item: dict, key: str, default: float = 0.0) -> float:
            try:
                value = item.get(key, default) if isinstance(item, dict) else default
                if value is None:
                    value = default
                score = float(value)
                return score if math.isfinite(score) else default
            except (TypeError, ValueError):
                return default

        max_count = max(0, int(max_count or 0))
        if max_count <= 0:
            return []
        excluded = set(exclude_ids or set())
        near_misses: list[dict] = []
        relative_floor = self._relative_near_miss_floor(evaluations, max_count=max_count, floor_cap=0.45)
        for item in evaluations or []:
            if not isinstance(item, dict) or id(item) in excluded or item.get("accepted"):
                continue
            reject_reason = str(item.get("reject_reason") or "")
            if reject_reason != "low_transcript_quality":
                continue
            quality = _score(item, "quality_score", 0.0)
            floor = _score(item, "quality_floor", 0.60)
            relaxed_floor = min(max(0.30, floor - 0.18), relative_floor)
            if not self._creator_safe_near_miss_candidate(item, min_quality=relaxed_floor):
                continue
            music_guard = item.get("music_lyrics_guard") if isinstance(item.get("music_lyrics_guard"), dict) else {}
            if music_guard.get("reject_candidate"):
                continue
            moment = item.get("moment") if isinstance(item.get("moment"), dict) else {}
            ranker = moment.get("ranker") if isinstance(moment.get("ranker"), dict) else {}
            item["multimodal_rescue_candidate"] = True
            item["multimodal_rescue_reason"] = "near_quality_floor"
            item["multimodal_rescue_relative_floor"] = round(float(relaxed_floor), 4)
            item["original_reject_reason"] = reject_reason
            if isinstance(moment, dict):
                moment["multimodal_rescue_candidate"] = True
                moment["multimodal_rescue_reason"] = "near_quality_floor"
                ranker["multimodal_rescue_candidate"] = True
                ranker["multimodal_rescue_reason"] = "near_quality_floor"
                ranker["multimodal_rescue_relative_floor"] = round(float(relaxed_floor), 4)
                ranker["original_reject_reason"] = reject_reason
                moment["ranker"] = ranker
            near_misses.append(item)

        ordered = sorted(
            near_misses,
            key=lambda item: (
                _score(item, score_key, _score(item, "learned_quality_score", _score(item, "quality_score", 0.0))),
                _score(item, "quality_score", 0.0),
                -_score(item.get("candidate") if isinstance(item.get("candidate"), dict) else {}, "candidate_rank", 9999),
            ),
            reverse=True,
        )
        return ordered[:max_count]

    def _classify_ai_moment_shadow(
        self,
        evaluations: list[dict],
        selected: list[dict],
        video_path: Path,
        *,
        enabled: bool,
        score_key: str,
        max_count: int = 12,
        max_ollama: int = 4,
        attach_to_evaluations: bool = False,
        game_context: dict | None = None,
    ) -> tuple[dict, dict]:
        """Classify a Deep-only pre-final shortlist for diagnostics without changing output."""
        evaluations = [item for item in (evaluations or []) if isinstance(item, dict)]
        selected = [item for item in (selected or []) if isinstance(item, dict)]
        max_count = max(0, int(max_count or 0))
        max_ollama = max(0, int(max_ollama or 0))
        report = {
            "schema_version": 1,
            "enabled": bool(enabled),
            "status": "disabled" if not enabled else "not_started",
            "model": DEFAULT_MODEL,
            "diagnostic_only": True,
            "selection_impact": "none",
            "output_changed": False,
            "prompt_scope": "deep_pre_final_shadow_shortlist_compact_transcript_ranker_visual_metadata_creator_learning",
            "score_key": score_key,
            "candidate_count": len(evaluations),
            "accepted_count": sum(1 for item in evaluations if item.get("accepted")),
            "selected_count": len(selected),
            "shortlist_count": 0,
            "classified_count": 0,
            "ollama_ready": False,
            "max_shortlist_candidates": max_count,
            "max_ollama_candidates": max_ollama,
            "ollama_attempted_count": 0,
            "fallback_count": 0,
            "statuses": {},
            "ai_viral_potential": {
                "schema_version": 1,
                "mode": "deep_ai_viral_potential_shadow",
                "diagnostic_only": True,
                "selection_impact": "none",
                "output_changed": False,
                "score_field": "ai_viral_score",
                "average_score": None,
                "scored_count": 0,
            },
            "rows": [],
        }
        if not enabled or not evaluations:
            if enabled and not evaluations:
                report["status"] = "no_candidates"
            return report, {}

        shortlist = self._ai_shadow_shortlist(
            evaluations,
            max_count=max_count,
            score_key=score_key,
            include_near_misses=True,
        )
        report["shortlist_count"] = len(shortlist)
        if not shortlist:
            report["status"] = "no_shortlist_candidates"
            return report, {}

        try:
            ollama_ready = is_ollama_model_ready(DEFAULT_MODEL)
        except Exception:
            ollama_ready = False
        report["ollama_ready"] = bool(ollama_ready)
        game_context = game_context if isinstance(game_context, dict) else self._game_context_for_source(video_path, allow_network=False)
        game_title = game_context.get("label") or self._infer_game_title_from_path(video_path)
        report["game_context"] = compact_game_context_for_prompt(game_context)
        learning_prompt_context = self._feedback_learning_prompt_context()
        report["learning_context_enabled"] = bool(learning_prompt_context.get("enabled"))
        selected_keys = {
            self._ai_moment_cache_key(item)
            for item in selected
            if self._ai_moment_cache_key(item)
        }
        cache: dict[str, dict] = {}
        viral_scores: list[int] = []

        for index, item in enumerate(shortlist, 1):
            moment = item.get("moment") if isinstance(item.get("moment"), dict) else {}
            transcript = moment.get("transcript") or item.get("transcript") or ""
            use_ollama = bool(ollama_ready and report["ollama_attempted_count"] < max_ollama)
            if use_ollama:
                report["ollama_attempted_count"] += 1
            classification = classify_moment_ai(
                transcript,
                game_title=game_title,
                clip_context={**moment, "game_context": game_context, "feedback_learning_context": learning_prompt_context},
                enabled=True,
                model=DEFAULT_MODEL,
                ollama_ready=use_ollama,
            )
            if ollama_ready and not use_ollama:
                classification["status"] = "ollama_skipped_limit"
                classification["provider"] = "heuristic"
                classification["fallback_used"] = True
            clean = compact_ai_moment_classification(classification)
            cache_key = self._ai_moment_cache_key(item)
            if cache_key:
                cache[cache_key] = clean
            if attach_to_evaluations:
                attach_ai_moment_classification(item, clean)
            report["classified_count"] += 1
            status = str(clean.get("status") or "unknown")
            report["statuses"][status] = int(report["statuses"].get(status, 0)) + 1
            if clean.get("fallback_used"):
                report["fallback_count"] += 1
            viral_score = clean.get("ai_viral_score")
            if isinstance(viral_score, int):
                viral_scores.append(viral_score)
            candidate = item.get("candidate") if isinstance(item.get("candidate"), dict) else {}
            report["rows"].append(
                {
                    "index": index,
                    "candidate_rank": candidate.get("candidate_rank"),
                    "candidate_kind": candidate.get("candidate_kind"),
                    "selected_in_output": cache_key in selected_keys,
                    "score": self._ai_shadow_score(item, score_key),
                    "score_key": score_key,
                    "primary_category": moment.get("primary_category"),
                    "ai_viral_score": clean.get("ai_viral_score"),
                    "ai_viral_reason": clean.get("ai_viral_reason"),
                    "ai_dimensions": clean.get("ai_dimensions"),
                    "ai_adjustment": clean.get("ai_adjustment"),
                    "ai_rank_delta": clean.get("ai_rank_delta"),
                    "ai_moment_classification": clean,
                }
            )

        if viral_scores:
            report["ai_viral_potential"]["scored_count"] = len(viral_scores)
            report["ai_viral_potential"]["average_score"] = round(sum(viral_scores) / len(viral_scores), 2)
        report["status"] = "ok" if report["classified_count"] else "no_classifications"
        return report, cache

    def _analyze_multimodal_candidate_shortlist(
        self,
        evaluations: list[dict],
        selected: list[dict],
        video_path: Path,
        *,
        enabled: bool,
        score_key: str,
        video_duration: float,
        max_count: int = 8,
        game_context: dict | None = None,
    ) -> dict:
        """Ask a local vision model about a Deep-only candidate shortlist."""
        evaluations = [item for item in (evaluations or []) if isinstance(item, dict)]
        selected = [item for item in (selected or []) if isinstance(item, dict)]
        max_count = max(0, int(max_count or 0))
        started = time.monotonic()
        report = {
            "schema_version": 1,
            "enabled": bool(enabled),
            "status": "disabled" if not enabled else "not_started",
            "provider": "ollama",
            "model": "",
            "selection_impact": "capped_rank_adjustment" if enabled else "none",
            "prompt_scope": "deep_pre_final_vision_shortlist_frames_transcript_ranker_visual_metadata_creator_learning",
            "score_key": score_key,
            "candidate_count": len(evaluations),
            "accepted_count": sum(1 for item in evaluations if item.get("accepted")),
            "selected_count": len(selected),
            "max_shortlist_candidates": max_count,
            "shortlist_count": 0,
            "accepted_shortlist_count": 0,
            "near_miss_shortlist_count": 0,
            "analyzed_count": 0,
            "frame_count": 0,
            "statuses": {},
            "vision_status": {},
            "learning_context_enabled": False,
            "rows": [],
            "elapsed_seconds": 0.0,
        }
        if not enabled or not evaluations:
            if enabled and not evaluations:
                report["status"] = "no_candidates"
            return report

        vision_status = ollama_vision_status()
        report["vision_status"] = vision_status
        report["model"] = vision_status.get("model", "")
        if not vision_status.get("model_ready"):
            report["status"] = "vision_model_missing"
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            return report
        preflight = preflight_ollama_vision_model(str(report["model"] or ""))
        report["preflight"] = preflight
        if not preflight.get("ok"):
            report["status"] = f"vision_preflight_{preflight.get('status') or 'failed'}"
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            return report

        selected_shortlist: list[dict] = []
        seen_ids: set[int] = set()
        for item in selected:
            if len(selected_shortlist) >= max_count:
                break
            selected_shortlist.append(item)
            seen_ids.add(id(item))
        near_miss_reserve = min(3, max(1, math.ceil(max_count * 0.18))) if max_count >= 2 else 0
        accepted_cap = max(0, max_count - len(selected_shortlist) - near_miss_reserve)
        accepted_shortlist = [
            item for item in self._ai_shadow_shortlist(
            evaluations,
            max_count=accepted_cap,
            score_key=score_key,
            )
            if id(item) not in seen_ids
        ]
        seen_ids.update(id(item) for item in accepted_shortlist)
        near_miss_shortlist = self._multimodal_near_miss_shortlist(
            evaluations,
            max_count=max(0, max_count - len(selected_shortlist) - len(accepted_shortlist)),
            exclude_ids=seen_ids,
            score_key=score_key,
        )
        if len(selected_shortlist) + len(accepted_shortlist) + len(near_miss_shortlist) < max_count:
            used_ids = {id(item) for item in selected_shortlist + accepted_shortlist + near_miss_shortlist}
            accepted_backfill = [
                item
                for item in self._ai_shadow_shortlist(
                    evaluations,
                    max_count=max_count,
                    score_key=score_key,
                )
                if id(item) not in used_ids
            ]
            remaining_slots = max_count - len(selected_shortlist) - len(accepted_shortlist) - len(near_miss_shortlist)
            accepted_shortlist.extend(accepted_backfill[:remaining_slots])
        shortlist = selected_shortlist + accepted_shortlist + near_miss_shortlist
        report["shortlist_count"] = len(shortlist)
        report["selected_shortlist_count"] = len(selected_shortlist)
        report["accepted_shortlist_count"] = len(selected_shortlist) + len(accepted_shortlist)
        report["near_miss_shortlist_count"] = len(near_miss_shortlist)
        if not shortlist:
            report["status"] = "no_shortlist_candidates"
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            return report

        game_context = game_context if isinstance(game_context, dict) else self._game_context_for_source(video_path, allow_network=False)
        game_title = game_context.get("label") or self._infer_game_title_from_path(video_path)
        report["game_context"] = compact_game_context_for_prompt(game_context)
        learning_prompt_context = self._feedback_learning_prompt_context()
        report["learning_context_enabled"] = bool(learning_prompt_context.get("enabled"))
        selected_ids = {id(item) for item in selected}
        for index, item in enumerate(shortlist, 1):
            if self._cancel:
                break
            moment = item.get("moment") if isinstance(item.get("moment"), dict) else {}
            candidate = item.get("candidate") if isinstance(item.get("candidate"), dict) else {}
            item["game_context"] = game_context
            candidate["game_context"] = game_context
            moment["game_context"] = game_context
            frame_candidate = {
                **candidate,
                "start": moment.get("start", candidate.get("start")),
                "end": moment.get("end", candidate.get("end")),
                "duration": moment.get("duration", candidate.get("duration")),
                "peak_time": moment.get("peak_time", candidate.get("peak_time")),
                "visual_diagnostics": moment.get("visual_diagnostics") or candidate.get("visual_diagnostics"),
            }
            self._push(
                "candidates",
                82,
                f"Inspecting clip visuals {index}/{len(shortlist)}...",
            )
            analysis = analyze_candidate_frames_with_ollama(
                video_path,
                frame_candidate,
                transcript=moment.get("transcript") or item.get("transcript") or "",
                game_title=game_title,
                game_context=game_context,
                learning_context=learning_prompt_context,
                video_duration=video_duration,
                enabled=True,
                model=report["model"],
                max_frames=6,
            )
            item["multimodal_analysis"] = analysis
            candidate["multimodal_analysis"] = analysis
            moment["multimodal_analysis"] = analysis
            ranker = moment.get("ranker") if isinstance(moment.get("ranker"), dict) else {}
            ranker["multimodal_analysis"] = analysis
            moment["ranker"] = ranker
            report["analyzed_count"] += 1
            report["frame_count"] += int(analysis.get("frame_count") or 0)
            status = str(analysis.get("status") or "unknown")
            report["statuses"][status] = int(report["statuses"].get(status, 0)) + 1
            report["rows"].append(
                {
                    "index": index,
                    "candidate_rank": candidate.get("candidate_rank"),
                    "candidate_kind": candidate.get("candidate_kind"),
                    "selected_in_output_before_multimodal": id(item) in selected_ids,
                    "rescue_candidate": bool(item.get("multimodal_rescue_candidate")),
                    "rescue_reason": item.get("multimodal_rescue_reason", ""),
                    "original_reject_reason": item.get("original_reject_reason") or item.get("reject_reason", ""),
                    "quality_score": item.get("quality_score"),
                    "quality_floor": item.get("quality_floor"),
                    "score": self._ai_shadow_score(item, score_key),
                    "score_key": score_key,
                    "status": status,
                    "model": analysis.get("model"),
                    "frame_count": analysis.get("frame_count"),
                    "sample_times": analysis.get("sample_times"),
                    "primary_visual_label": analysis.get("primary_visual_label"),
                    "visible_summary": analysis.get("visible_summary"),
                    "visual_labels": analysis.get("visual_labels"),
                    "detected_events": analysis.get("detected_events"),
                    "confidence": analysis.get("confidence"),
                    "ranking_adjustment": analysis.get("ranking_adjustment"),
                    "reject_flags": analysis.get("reject_flags"),
                    "fallback_used": analysis.get("fallback_used"),
                    "initial_status": analysis.get("initial_status"),
                }
            )

        if self._cancel:
            report["status"] = "cancelled"
        else:
            ok_count = int(report["statuses"].get("ok", 0))
            if ok_count:
                report["status"] = "ok"
            elif report["analyzed_count"]:
                report["status"] = "no_usable_analysis"
            else:
                report["status"] = "no_analysis"
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return report

    @staticmethod
    def _ai_shadow_score(item: dict, score_key: str) -> float | None:
        if not isinstance(item, dict):
            return None
        for key in (score_key, "learned_quality_score", "quality_score"):
            try:
                value = item.get(key)
                if value is None:
                    continue
                score = float(value)
                if math.isfinite(score):
                    return round(score, 4)
            except (TypeError, ValueError):
                continue
        return None

    # ── Exposed: results ─────────────────────────────────────────────────

    @staticmethod
    def _hash_id(prefix: str, *parts, length: int = 16) -> str:
        payload = "\x1f".join(str(p or "") for p in parts)
        digest = hashlib.sha1(payload.encode("utf-8", errors="ignore")).hexdigest()[:length]
        return f"{prefix}_{digest}"

    def _source_id_for(self, path=None, source_stem: str = "", source_path: str = "") -> str:
        raw_path = str(source_path or path or "").strip()
        stem = source_stem or (Path(raw_path).stem if raw_path else "")
        return self._hash_id("src", raw_path.lower(), stem.lower(), length=14)

    def _clip_id_for(self, moment: dict, path=None) -> str:
        source_id = moment.get("source_id") or self._source_id_for(path, moment.get("source_stem", ""), moment.get("source_path", ""))
        start = moment.get("start", "")
        end = moment.get("end", "")
        peak = moment.get("peak_time", "")
        transcript_hash = hashlib.sha1(
            (moment.get("transcript", "") or "").encode("utf-8", errors="ignore")
        ).hexdigest()[:10]
        if start != "" or end != "":
            return self._hash_id("clip", source_id, start, end, peak, transcript_hash, length=18)

        p = Path(path) if path else None
        try:
            stat = p.stat() if p and p.exists() else None
            return self._hash_id(
                "clip",
                source_id,
                str(p).lower() if p else "",
                stat.st_size if stat else "",
                int(stat.st_mtime) if stat else "",
                length=18,
            )
        except Exception:
            return self._hash_id("clip", source_id, str(path or "").lower(), length=18)

    def _ensure_moment_identity(self, moment, path=None) -> dict:
        if not isinstance(moment, dict):
            moment = {}
        p = Path(path) if path else None
        if not moment.get("source_path") and p and p.exists():
            moment["source_path"] = str(p)
        if not moment.get("source_stem") and p:
            match = re.match(r'^(.+?)_viral\d+', p.name)
            moment["source_stem"] = match.group(1) if match else p.stem
        if not moment.get("source_id"):
            moment["source_id"] = self._source_id_for(
                path,
                moment.get("source_stem", ""),
                moment.get("source_path", ""),
            )
        if not moment.get("clip_id"):
            moment["clip_id"] = self._clip_id_for(moment, path)
        return moment

    def _find_clip_index_by_id(self, clip_id: str | None) -> int | None:
        if not clip_id:
            return None
        for i, moment in enumerate(self._moments):
            if isinstance(moment, dict) and moment.get("clip_id") == clip_id:
                return i
        return None

    def _find_clip_index_by_filename(self, filename: str | None) -> int | None:
        name = str(filename or "").strip()
        if not name:
            return None
        for i, path in enumerate(self._results):
            if Path(path).name == name:
                return i
        return None

    def _resolve_clip_index(self, item) -> int | None:
        if not isinstance(item, dict):
            return None
        idx = self._find_clip_index_by_id(item.get("clip_id"))
        if idx is not None:
            return idx
        idx = self._find_clip_index_by_filename(item.get("clip_filename"))
        if idx is not None:
            return idx
        if item.get("clip_id") or item.get("clip_filename"):
            return None
        raw_idx = item.get("index", item.get("clipIdx", None))
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            return None
        if 0 <= idx < len(self._results):
            return idx
        return None

    def _attach_identity_to_schedule(self, item: dict, idx: int) -> dict:
        item = dict(item)
        item["clipIdx"] = idx
        if idx < len(self._moments):
            moment = self._ensure_moment_identity(self._moments[idx], self._results[idx])
            self._moments[idx] = moment
            item.setdefault("clip_id", moment.get("clip_id"))
            item.setdefault("source_id", moment.get("source_id"))
            item.setdefault("source_stem", moment.get("source_stem"))
            if "creator_title_context" in item:
                context = sanitize_creator_title_context(item.get("creator_title_context"))
                if context:
                    item["creator_title_context"] = context
                    moment["creator_title_context"] = context
                else:
                    item["creator_title_context"] = ""
                    moment.pop("creator_title_context", None)
            else:
                context = sanitize_creator_title_context(moment.get("creator_title_context"))
                if context:
                    item["creator_title_context"] = context
        if idx < len(self._results):
            item.setdefault("clip_filename", self._results[idx].name)
        return item

    def _schedule_game_title(self, item: dict, idx: int) -> str:
        if item.get("game_title"):
            return str(item.get("game_title") or "")
        if idx < len(self._moments) and isinstance(self._moments[idx], dict):
            metadata = self._moments[idx].get("generated_metadata") or {}
            if metadata.get("game_title"):
                return str(metadata.get("game_title") or "")
        return self._game_title_for_clip(idx)

    def _generated_metadata_for_clip(self, idx: int) -> dict:
        if 0 <= idx < len(self._moments) and isinstance(self._moments[idx], dict):
            metadata = self._moments[idx].get("generated_metadata")
            if isinstance(metadata, dict):
                return metadata
        return {}

    def _default_schedule_title_for_clip(self, idx: int) -> str:
        if 0 <= idx < len(self._results):
            return self._results[idx].stem
        return f"Clip {idx + 1}"

    def _schedule_title_is_default(self, item: dict, idx: int) -> bool:
        title = str(item.get("title") or "").strip()
        if not title:
            return True
        default_title = self._default_schedule_title_for_clip(idx)
        filename = self._results[idx].name if 0 <= idx < len(self._results) else ""
        return title in {default_title, filename}

    def _ensure_schedule_description(self, item: dict, idx: int) -> dict:
        item = dict(item)
        metadata = self._generated_metadata_for_clip(idx)
        generated_title = str(metadata.get("title") or metadata.get("generated_title") or "").strip()
        if generated_title and self._schedule_title_is_default(item, idx):
            item["title"] = generated_title
        generated_tags = str(metadata.get("tags") or "").strip()
        if generated_tags and not str(item.get("tags") or "").strip():
            item["tags"] = generated_tags
        structured = any(
            key in item
            for key in (
                "description_custom_text",
                "description_auto_hashtags",
                "description_generated",
                "generated_description",
                "final_description",
            )
        )
        if item.get("description") and not structured:
            return item

        title = str(item.get("title") or generated_title or self._default_schedule_title_for_clip(idx))
        game_title = self._schedule_game_title(item, idx)
        clip_context = self._title_context_for_clip(idx)
        if "description_generated" in item or "generated_description" in item:
            generated_text = item.get("description_generated")
            if generated_text is None:
                generated_text = item.get("generated_description")
        else:
            generated_text = (
                metadata.get("generated_description")
                or metadata.get("description_generated")
                or metadata.get("description")
                or None
            )
        custom_text = item.get("description_custom_text")
        if custom_text is None and not structured:
            custom_text = self._description_profile()["custom_text"]
        auto_hashtags = item.get("description_auto_hashtags")
        desc_parts = self._compose_clip_description(
            title,
            game_title,
            clip_context=clip_context,
            custom_text=custom_text,
            auto_hashtags=auto_hashtags,
            generated_text=generated_text,
        )
        item["description"] = desc_parts["description"]
        item["final_description"] = desc_parts["final_description"]
        item["description_generated"] = desc_parts["generated_description"]
        item["generated_description"] = desc_parts["generated_description"]
        item["description_custom_text"] = desc_parts["description_custom_text"]
        item["description_auto_hashtags"] = desc_parts["description_auto_hashtags"]
        item["recommended_hashtags"] = desc_parts["recommended_hashtags"]
        item["game_title"] = game_title
        if not str(item.get("tags") or "").strip():
            item["tags"] = generated_tags or self._tags_for_game(game_title, clip_context=clip_context)
        return item

    def _normalize_scheduled_items(self, scheduled, legacy_index_map: dict | None = None) -> list[dict]:
        normalized = []
        for item in scheduled or []:
            if not isinstance(item, dict):
                continue
            idx = self._find_clip_index_by_id(item.get("clip_id"))
            if idx is None:
                idx = self._find_clip_index_by_filename(item.get("clip_filename"))
            if idx is None and not (item.get("clip_id") or item.get("clip_filename")):
                try:
                    old_idx = int(item.get("clipIdx", -1))
                except (TypeError, ValueError):
                    old_idx = -1
                if legacy_index_map is not None:
                    idx = legacy_index_map.get(old_idx)
                elif 0 <= old_idx < len(self._results):
                    idx = old_idx
            if idx is None or not (0 <= idx < len(self._results)):
                continue
            item = self._attach_identity_to_schedule(item, idx)
            normalized.append(self._ensure_schedule_description(item, idx))
        return normalized

    @staticmethod
    def _schedule_identity_key(item: dict) -> tuple[str, str] | None:
        if not isinstance(item, dict):
            return None
        clip_id = str(item.get("clip_id") or "").strip()
        if clip_id:
            return ("clip_id", clip_id)
        filename = str(item.get("clip_filename") or "").strip()
        if filename:
            return ("clip_filename", filename)
        try:
            return ("clipIdx", str(int(item.get("clipIdx"))))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _same_schedule_slot(existing: dict, incoming: dict) -> bool:
        for key in ("date", "time", "account_id", "channel_id"):
            if str(existing.get(key) or "") != str(incoming.get(key) or ""):
                return False
        return True

    @staticmethod
    def _reset_schedule_success_fields(item: dict) -> None:
        item["uploaded"] = False
        for key in SCHEDULE_SUCCESS_FIELDS - {"uploaded"}:
            item.pop(key, None)
        for key in SCHEDULE_BACKEND_STATUS_FIELDS:
            item.pop(key, None)

    def _merge_backend_schedule_fields(self, incoming: list[dict], existing: list[dict]) -> list[dict]:
        existing_by_key: dict[tuple[str, str], dict] = {}
        for item in existing or []:
            key = self._schedule_identity_key(item)
            if key:
                existing_by_key[key] = item

        merged = []
        for item in incoming:
            current = dict(item)
            previous = existing_by_key.get(self._schedule_identity_key(current))
            if previous:
                same_slot = self._same_schedule_slot(previous, current)
                if same_slot:
                    for key in SCHEDULE_SUCCESS_FIELDS:
                        if key in previous and previous.get(key) not in (None, ""):
                            current[key] = previous[key]
                    for key in SCHEDULE_BACKEND_STATUS_FIELDS:
                        if key in previous and key not in current:
                            current[key] = previous[key]
                else:
                    self._reset_schedule_success_fields(current)
            merged.append(current)
        return merged

    def _normalize_upload_history(self, history) -> list[dict]:
        normalized = []
        for row in history or []:
            if not isinstance(row, dict):
                continue
            clean = dict(row)
            clean["schema_version"] = _safe_int_value(clean.get("schema_version"), 1) or 1
            clean.setdefault("upload_id", str(uuid.uuid4()))
            clean.setdefault("status", "sent_to_youtube")
            normalized.append(clean)
        return normalized[-1000:]

    def _append_upload_history(self, record: dict) -> None:
        if not isinstance(record, dict):
            return
        with self._get_state_lock():
            self._upload_history = self._normalize_upload_history(getattr(self, "_upload_history", []))
            youtube_id = str(record.get("youtube_id") or "").strip()
            for idx, existing in enumerate(self._upload_history):
                if youtube_id and str(existing.get("youtube_id") or "").strip() == youtube_id:
                    merged = dict(existing)
                    merged.update({k: v for k, v in record.items() if v not in (None, "")})
                    self._upload_history[idx] = merged
                    return
            self._upload_history.append(record)

    def _upload_history_record(self, item: dict, clip_idx: int, meta: dict, upload_result=None, trigger: str = "manual", timestamp: str | None = None) -> dict:
        meta = meta or {}
        item = item or {}
        timestamp = timestamp or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        publish_at = None
        try:
            parsed = self._parse_publish_at(meta) or self._parse_publish_at(item)
            if parsed:
                publish_at = parsed.isoformat().replace("+00:00", "Z")
        except Exception:
            publish_at = None
        result = upload_result if isinstance(upload_result, dict) else {}
        privacy = str(meta.get("privacy") or item.get("privacy") or "private").lower()
        status = "youtube_scheduled" if trigger != "scheduler" and privacy == "public" and publish_at else "sent_to_youtube"
        return {
            "schema_version": 1,
            "upload_id": str(uuid.uuid4()),
            "clip_id": meta.get("clip_id") or item.get("clip_id"),
            "clip_filename": meta.get("clip_filename") or item.get("clip_filename"),
            "source_id": meta.get("source_id") or item.get("source_id"),
            "source_stem": meta.get("source_stem") or item.get("source_stem"),
            "clipIdx": clip_idx,
            "title": meta.get("title") or item.get("title"),
            "account_id": meta.get("account_id") or item.get("account_id"),
            "channel_id": meta.get("channel_id") or item.get("channel_id"),
            "privacy": privacy,
            "date": meta.get("date") or item.get("date"),
            "time": meta.get("time") or item.get("time"),
            "publish_at_utc": publish_at,
            "finished_at_utc": timestamp,
            "status": status,
            "trigger": trigger,
            "youtube_id": result.get("id"),
            "youtube_url": result.get("url"),
        }

    @staticmethod
    def _compact_truth_sources(identity: dict | None) -> list[str]:
        identity = identity if isinstance(identity, dict) else {}
        sources: list[str] = []
        candidate = identity.get("matched_candidate") if isinstance(identity.get("matched_candidate"), dict) else {}
        for source in candidate.get("sources") or []:
            text = str(source or "").strip()
            if text and text not in sources:
                sources.append(text)
        for evidence in identity.get("evidence") or []:
            if not isinstance(evidence, dict):
                continue
            if evidence.get("type") == "title_candidate":
                for source in evidence.get("sources") or []:
                    text = str(source or "").strip()
                    if text and text not in sources:
                        sources.append(text)
        return sources[:8]

    def _game_truth_source(
        self,
        identity: dict | None,
        source_record: dict | None,
        *,
        game_title_hint: str = "",
    ) -> tuple[str, str]:
        identity = identity if isinstance(identity, dict) else {}
        source_record = source_record if isinstance(source_record, dict) else {}
        sources = self._compact_truth_sources(identity)
        matched_via = str(identity.get("matched_via") or "").strip()
        youtube_context = source_record.get("youtube_context") if isinstance(source_record.get("youtube_context"), dict) else {}
        youtube_title = self._sanitize_game_title_hint(youtube_context.get("title"))
        hint = self._sanitize_game_title_hint(game_title_hint or source_record.get("game_title_hint"))
        if hint and (not youtube_title or hint.lower() != youtube_title.lower()):
            return "user_hint", "User hint"
        if youtube_context and (
            "explicit_title" in sources
            or "creator_context" in sources
            or (hint and youtube_title and hint.lower() == youtube_title.lower())
        ):
            return "youtube_metadata", "YouTube metadata"
        if any(source.startswith("source_") for source in sources):
            return "filename", "Filename"
        if matched_via in {"wikidata_search", "local_cache"} or identity.get("qid"):
            return "wikidata", "Wikidata"
        return "auto", "Auto"

    @staticmethod
    def _visual_truth_payload(moment: dict | None) -> dict:
        moment = moment if isinstance(moment, dict) else {}
        visual = moment.get("visual_diagnostics") if isinstance(moment.get("visual_diagnostics"), dict) else {}
        multimodal = moment.get("multimodal_analysis") if isinstance(moment.get("multimodal_analysis"), dict) else {}
        multimodal_status = str(multimodal.get("status") or "").strip()
        visual_status = str(visual.get("status") or "").strip()
        model = str(multimodal.get("model") or multimodal.get("vision_model") or "").strip()
        frame_count = _safe_int_value(
            multimodal.get("frame_count")
            or multimodal.get("sampled_frame_count")
            or visual.get("sampled_frame_count")
            or visual.get("frame_count"),
            0,
        )
        used = bool(
            multimodal_status == "ok"
            or visual_status == "ok"
            or frame_count > 0
            or multimodal.get("metadata_keywords")
            or multimodal.get("visual_labels")
        )
        source = ""
        if multimodal_status == "ok" or model:
            source = "ollama_vision"
        elif visual_status:
            source = "local_visual_analysis"
        return {
            "used": used,
            "source": source,
            "status": multimodal_status or visual_status or ("unused" if not used else "unknown"),
            "model": model,
            "frame_count": frame_count,
        }

    def _clip_truth_summary(self, moment: dict | None, path: Path | str | None = None) -> dict:
        moment = moment if isinstance(moment, dict) else {}
        source_record = self._source_record_for(moment.get("source_path") or path, moment.get("source_id"))
        source_identity = source_record.get("game_identity") if isinstance(source_record.get("game_identity"), dict) else {}
        source_context = source_record.get("game_context") if isinstance(source_record.get("game_context"), dict) else {}
        identity = moment.get("game_identity") if isinstance(moment.get("game_identity"), dict) else source_identity
        context = moment.get("game_context") if isinstance(moment.get("game_context"), dict) else source_context
        game_title_hint = self._sanitize_game_title_hint(
            moment.get("game_title_hint") or source_record.get("game_title_hint")
        )
        title = (
            str(moment.get("game_title") or "").strip()
            or str(identity.get("title") or "").strip()
            or str(context.get("label") or "").strip()
            or game_title_hint
        )
        confidence = self._game_identity_confidence(identity)
        game_source, game_source_label = self._game_truth_source(
            identity,
            source_record,
            game_title_hint=game_title_hint,
        )
        multi_signal = (
            moment.get("multi_signal_ai_scoring")
            if isinstance(moment.get("multi_signal_ai_scoring"), dict)
            else {}
        )
        nudge = (
            multi_signal.get("game_context_nudge")
            if isinstance(multi_signal.get("game_context_nudge"), dict)
            else {}
        )
        contributions = (
            multi_signal.get("contributions")
            if isinstance(multi_signal.get("contributions"), dict)
            else {}
        )
        adjustment = _safe_float_value(
            nudge.get("adjustment")
            if nudge.get("adjustment") is not None
            else contributions.get("game_context"),
            0.0,
        )
        affected_ranking = bool(abs(adjustment) > 0.0001)
        visual_truth = self._visual_truth_payload(moment)
        speech_policy = _clip_speech_policy_summary(moment)
        return {
            "schema_version": 1,
            "game_title": title[:140],
            "game_confidence": round(confidence, 4),
            "game_source": game_source,
            "game_source_label": game_source_label,
            "game_matched_via": str(identity.get("matched_via") or "")[:80],
            "game_qid": str(identity.get("qid") or context.get("qid") or "")[:40],
            "game_context_status": str(context.get("status") or identity.get("status") or "")[:40],
            "game_context_available": bool(context.get("qid") or identity.get("qid")),
            "game_context_affected_ranking": affected_ranking,
            "game_context_score_delta": round(adjustment, 4),
            "game_context_nudge": {
                key: nudge.get(key)
                for key in (
                    "status",
                    "reason",
                    "selection_impact",
                    "primary_category",
                    "context_families",
                    "cue_hits",
                    "max_adjustment",
                )
                if nudge.get(key) is not None
            },
            "visual_analysis_used": bool(visual_truth.get("used")),
            "visual_analysis_status": visual_truth.get("status"),
            "visual_analysis_source": visual_truth.get("source"),
            "vision_model": visual_truth.get("model"),
            "visual_frame_count": visual_truth.get("frame_count"),
            "speech_policy_status": speech_policy.get("status"),
            "speech_policy_warning": speech_policy.get("warning"),
            "selected_track_has_speech": speech_policy.get("selected_track_has_speech"),
            "selected_track_word_count": speech_policy.get("selected_track_word_count"),
            "metadata_transcript_source": speech_policy.get("metadata_transcript_source"),
        }

    def _clip_payload(self, idx: int, path: Path, include_url: bool = True) -> dict:
        moment = self._ensure_moment_identity(
            self._moments[idx] if idx < len(self._moments) else {},
            path,
        )
        if self._hydrate_generated_metadata_from_sidecar(idx, path, moment):
            self._metadata_hydration_changed = True
        if idx < len(self._moments):
            self._moments[idx] = moment
        generated_metadata = (
            moment.get("generated_metadata")
            if isinstance(moment.get("generated_metadata"), dict)
            else {}
        )
        clip = {
            "path": str(path),
            "filename": path.name,
            "size_mb": round(path.stat().st_size / (1024 * 1024), 1),
            "clip_id": moment.get("clip_id"),
            "source_id": moment.get("source_id"),
            "source_stem": moment.get("source_stem", ""),
            "primary_category": moment.get("primary_category"),
            "moment_categories": moment.get("moment_categories"),
            "visual_diagnostics": moment.get("visual_diagnostics"),
            "ai_moment_classification": moment.get("ai_moment_classification"),
            "commentary_guard": moment.get("commentary_guard"),
            "voice_profile": moment.get("voice_profile"),
            "creator_title_context": sanitize_creator_title_context(moment.get("creator_title_context")),
            "subtitle_style": moment.get("subtitle_style"),
            "captions_requested": moment.get("captions_requested"),
            "subtitle_enabled": moment.get("subtitle_enabled"),
            "subtitle_generated": moment.get("subtitle_generated"),
            "subtitles_burned": moment.get("subtitles_burned"),
            "subtitle_placement": moment.get("subtitle_placement"),
            "speech_policy": moment.get("speech_policy") if isinstance(moment.get("speech_policy"), dict) else _clip_speech_policy_summary(moment),
            "metadata_warning": moment.get("metadata_warning"),
            "metadata_needs_context": bool(moment.get("metadata_needs_context")),
            "generated_metadata": generated_metadata,
            "truth_summary": self._clip_truth_summary(moment, path),
        }
        if include_url:
            clip["url"] = self._clip_url_for_path(path) or f"http://127.0.0.1:{self._video_port}/{quote(path.name)}"
        return clip

    @staticmethod
    def _compact_moment_categories(categories) -> dict | None:
        """Return the small label payload needed by library/review screens."""
        if not isinstance(categories, dict):
            return None
        compact = {}
        primary = str(categories.get("primary") or "").strip()
        if primary:
            compact["primary"] = primary
        confidence = _safe_float_value(categories.get("confidence"), None)
        if confidence is not None:
            compact["confidence"] = round(confidence, 4)
        ai = categories.get("ai")
        if isinstance(ai, dict):
            ai_compact = {
                key: ai.get(key)
                for key in ("status", "provider", "primary_category", "confidence", "fallback_used")
                if ai.get(key) is not None
            }
            fine_labels = ai.get("fine_labels")
            if isinstance(fine_labels, list):
                ai_compact["fine_labels"] = [str(label) for label in fine_labels[:2] if label]
            if ai_compact:
                compact["ai"] = ai_compact
        return compact or None

    @staticmethod
    def _empty_personalization() -> dict:
        return {
            "schema_version": PERSONALIZATION_SCHEMA_VERSION,
            "events": [],
            "clips": {},
        }

    @staticmethod
    def _empty_processing_history() -> dict:
        return {
            "schema_version": PROCESSING_HISTORY_SCHEMA_VERSION,
            "runs": [],
        }

    @staticmethod
    def _utc_now_label() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _processing_history_stats(runs: list[dict]) -> dict:
        clean_runs = [row for row in runs if isinstance(row, dict)]
        by_depth: dict[str, dict] = {}
        total_elapsed = 0.0
        total_count = 0
        for row in clean_runs:
            elapsed = row.get("elapsed_seconds")
            try:
                elapsed = float(elapsed)
            except (TypeError, ValueError):
                continue
            if elapsed <= 0:
                continue
            total_elapsed += elapsed
            total_count += 1
            depth = _normalize_processing_depth(row.get("processing_depth"))
            bucket = by_depth.setdefault(
                depth,
                {
                    "run_count": 0,
                    "total_elapsed_seconds": 0.0,
                    "total_video_minutes": 0.0,
                    "last_elapsed_seconds": None,
                    "last_estimate_error_seconds": None,
                },
            )
            bucket["run_count"] += 1
            bucket["total_elapsed_seconds"] += elapsed
            try:
                video_duration = float(row.get("video_duration_seconds") or 0.0)
            except (TypeError, ValueError):
                video_duration = 0.0
            bucket["total_video_minutes"] += max(0.0, video_duration / 60.0)
            bucket["last_elapsed_seconds"] = round(elapsed, 3)
            if row.get("estimate_error_seconds") is not None:
                try:
                    bucket["last_estimate_error_seconds"] = round(float(row.get("estimate_error_seconds")), 3)
                except (TypeError, ValueError):
                    bucket["last_estimate_error_seconds"] = None
        for bucket in by_depth.values():
            count = max(1, int(bucket["run_count"]))
            video_minutes = float(bucket.get("total_video_minutes") or 0.0)
            bucket["average_elapsed_seconds"] = round(bucket["total_elapsed_seconds"] / count, 3)
            bucket["average_video_minutes"] = round(video_minutes / count, 3)
            bucket["seconds_per_video_minute"] = (
                round(bucket["total_elapsed_seconds"] / video_minutes, 4)
                if video_minutes > 0
                else None
            )
            bucket.pop("total_elapsed_seconds", None)
            bucket.pop("total_video_minutes", None)
        return {
            "run_count": total_count,
            "average_elapsed_seconds": round(total_elapsed / total_count, 3) if total_count else 0.0,
            "by_depth": by_depth,
            "last_run": clean_runs[-1] if clean_runs else None,
        }

    @staticmethod
    def _history_fingerprint_similarity(target: dict | None, actual: dict | None) -> float:
        target = target if isinstance(target, dict) else {}
        actual = actual if isinstance(actual, dict) else {}
        if not target or not actual:
            return 0.0
        target_mode = str(target.get("generation_mode") or "").strip()
        actual_mode = str(actual.get("generation_mode") or "").strip()
        if target_mode and actual_mode and target_mode != actual_mode:
            return 0.0
        score = 0.0
        total = 0.0
        for key in (
            "generation_mode",
            "candidate_whisper_model",
            "scene_mode",
            "subtitle_style",
        ):
            if target.get(key) is None:
                continue
            total += 1.0
            if str(target.get(key)) == str(actual.get(key)):
                score += 1.0
        for key in (
            "visual_analysis",
            "multimodal_analysis",
            "ai_moment_labels",
            "ai_moment_ranking",
            "multimodal_ranking",
            "multi_signal_ai_ranking",
            "moment_label_ranking",
            "voice_profile_ranking",
            "game_context",
        ):
            if key not in target:
                continue
            total += 0.8
            if bool(target.get(key)) == bool(actual.get(key)):
                score += 0.8
        for key in ("candidate_pool_cap", "candidate_multiplier"):
            if target.get(key) is None or actual.get(key) is None:
                continue
            try:
                left = float(target.get(key) or 0.0)
                right = float(actual.get(key) or 0.0)
            except (TypeError, ValueError):
                continue
            total += 0.8
            score += 0.8 * max(0.0, 1.0 - abs(left - right) / max(1.0, left, right))
        return round(score / total, 4) if total > 0 else 0.0

    @staticmethod
    def _history_generation_mode(row: dict | None) -> str:
        row = row if isinstance(row, dict) else {}
        fingerprint = row.get("settings_fingerprint") if isinstance(row.get("settings_fingerprint"), dict) else {}
        return _normalize_generation_mode(row.get("generation_mode") or fingerprint.get("generation_mode"))

    @classmethod
    def _history_runs_matching_fingerprint(cls, runs: list[dict], fingerprint: dict | None) -> list[dict]:
        target = fingerprint if isinstance(fingerprint, dict) else {}
        target_mode = (
            _normalize_generation_mode(target.get("generation_mode"))
            if target.get("generation_mode")
            else ""
        )
        if not target_mode:
            return [row for row in runs if isinstance(row, dict)]
        filtered = []
        for row in runs:
            if not isinstance(row, dict):
                continue
            actual_fingerprint = row.get("settings_fingerprint") if isinstance(row.get("settings_fingerprint"), dict) else {}
            actual_mode_raw = row.get("generation_mode") or actual_fingerprint.get("generation_mode")
            if not actual_mode_raw:
                continue
            if cls._history_generation_mode(row) == target_mode:
                filtered.append(row)
        return filtered

    @staticmethod
    def _median_float(values: list[float]) -> float | None:
        clean = sorted(float(value) for value in values if isinstance(value, (int, float)) and math.isfinite(float(value)))
        if not clean:
            return None
        mid = len(clean) // 2
        if len(clean) % 2:
            return clean[mid]
        return (clean[mid - 1] + clean[mid]) / 2.0

    @staticmethod
    def _stage_seconds_from_timing(stage_timings: dict | None) -> dict[str, float]:
        timings = stage_timings if isinstance(stage_timings, dict) else {}
        stage_seconds: dict[str, float] = {}
        for stage in ETA_PROGRESS_STAGES:
            total = 0.0
            for key in ETA_STAGE_TIMING_KEYS.get(stage, ()):
                total += max(0.0, _safe_float_value(timings.get(key), 0.0))
            stage_seconds[stage] = round(total, 3)

        # Older debug backfills may only have scene_detection, while live runs
        # record detect as the parent operation. Use scene_detection only as a
        # fallback so scene time is not double-counted.
        if stage_seconds.get("detect", 0.0) <= 0:
            stage_seconds["detect"] = round(
                max(0.0, _safe_float_value(timings.get("scene_detection"), 0.0)),
                3,
            )

        tracked = sum(stage_seconds.values())
        other = max(0.0, _safe_float_value(timings.get("other_untracked"), 0.0))
        if other > 0 and tracked > 0:
            # Spread untracked time across known stages so learned weights add up
            # closer to real wall time without inventing a new UI stage.
            for stage in ETA_PROGRESS_STAGES:
                share = stage_seconds[stage] / tracked if tracked else 0.0
                stage_seconds[stage] = round(stage_seconds[stage] + other * share, 3)
        elif other > 0:
            stage_seconds["candidates"] = round(stage_seconds.get("candidates", 0.0) + other, 3)
        return stage_seconds

    def _estimate_processing_stage_plan_from_history(
        self,
        depth: str,
        video_duration: float | None,
        *,
        settings_fingerprint: dict | None = None,
    ) -> dict | None:
        """Return learned per-stage ETA seconds for the current workload."""
        try:
            video_minutes = max(0.0, float(video_duration or 0.0) / 60.0)
        except (TypeError, ValueError):
            video_minutes = 0.0
        if video_minutes <= 0:
            return None

        lock = getattr(self, "_processing_history_lock", threading.RLock())
        history = getattr(self, "_processing_history", self._empty_processing_history())
        with lock:
            runs = list(history.get("runs", []))

        normalized_depth = _normalize_processing_depth(depth)
        eligible = []
        recent_runs = self._history_runs_matching_fingerprint(runs[-80:], settings_fingerprint)
        for idx, row in enumerate(recent_runs):
            if not isinstance(row, dict):
                continue
            if _normalize_processing_depth(row.get("processing_depth")) != normalized_depth:
                continue
            elapsed = _safe_float_value(row.get("elapsed_seconds"), 0.0)
            duration = _safe_float_value(row.get("video_duration_seconds"), 0.0)
            stage_seconds = self._stage_seconds_from_timing(row.get("stage_timings"))
            if elapsed <= 0 or duration <= 0 or sum(stage_seconds.values()) <= 0:
                continue
            similarity = self._history_fingerprint_similarity(
                settings_fingerprint,
                row.get("settings_fingerprint") if isinstance(row.get("settings_fingerprint"), dict) else {},
            )
            eligible.append(
                {
                    "stage_seconds_per_minute": {
                        stage: seconds / max(0.1, duration / 60.0)
                        for stage, seconds in stage_seconds.items()
                    },
                    "similarity": similarity,
                    "recency_weight": 0.65 + (idx / max(1, len(recent_runs))) * 0.35,
                }
            )

        similar = [row for row in eligible if row["similarity"] >= 0.68]
        source_rows = similar[-12:] if similar else eligible[-12:]
        if not source_rows:
            return None

        stage_plan = {}
        stage_rates = {}
        for stage in ETA_PROGRESS_STAGES:
            values = []
            weighted_total = 0.0
            weight_sum = 0.0
            for item in source_rows:
                rate = float(item["stage_seconds_per_minute"].get(stage, 0.0) or 0.0)
                if rate <= 0:
                    continue
                weight = (
                    max(0.1, float(item.get("similarity") or 0.0))
                    * float(item.get("recency_weight") or 1.0)
                )
                values.append(rate)
                weighted_total += rate * weight
                weight_sum += weight
            if weight_sum > 0:
                rate = weighted_total / weight_sum
            else:
                median_rate = self._median_float(values)
                rate = float(median_rate or 0.0)
            stage_rates[stage] = round(rate, 4)
            stage_plan[stage] = round(max(0.0, rate * video_minutes), 3)

        total = round(sum(stage_plan.values()), 3)
        if total <= 0:
            return None

        source = "stage_history_similar" if similar else "stage_history"
        if len(source_rows) == 1:
            source = "stage_history_nearest"
        confidence = "medium" if len(source_rows) >= 3 and similar else "low"
        if len(source_rows) >= 6 and similar:
            confidence = "high"
        return {
            "stages": stage_plan,
            "stageRates": stage_rates,
            "estimatedTotalSeconds": total,
            "source": source,
            "sampleCount": len(source_rows),
            "confidence": confidence,
        }

    def _estimate_processing_seconds_from_history(
        self,
        depth: str,
        video_duration: float | None,
        *,
        settings_fingerprint: dict | None = None,
    ) -> tuple[float | None, str]:
        try:
            video_minutes = max(0.0, float(video_duration or 0.0) / 60.0)
        except (TypeError, ValueError):
            video_minutes = 0.0
        if video_minutes <= 0:
            return None, "no_video_duration"
        lock = getattr(self, "_processing_history_lock", threading.RLock())
        history = getattr(self, "_processing_history", self._empty_processing_history())
        with lock:
            runs = list(history.get("runs", []))
        normalized_depth = _normalize_processing_depth(depth)
        eligible_runs = []
        history_runs = self._history_runs_matching_fingerprint(runs[-80:], settings_fingerprint)
        for idx, row in enumerate(history_runs):
            if not isinstance(row, dict):
                continue
            if _normalize_processing_depth(row.get("processing_depth")) != normalized_depth:
                continue
            elapsed = _safe_float_value(row.get("elapsed_seconds"), 0.0)
            duration = _safe_float_value(row.get("video_duration_seconds"), 0.0)
            if elapsed <= 0 or duration <= 0:
                continue
            similarity = self._history_fingerprint_similarity(
                settings_fingerprint,
                row.get("settings_fingerprint") if isinstance(row.get("settings_fingerprint"), dict) else {},
            )
            eligible_runs.append(
                {
                    "row": row,
                    "similarity": similarity,
                    "recency_weight": 0.65 + (idx / max(1, len(history_runs))) * 0.35,
                    "seconds_per_minute": elapsed / max(0.1, duration / 60.0),
                }
            )
        similar_runs = [row for row in eligible_runs if row["similarity"] >= 0.68]
        if similar_runs:
            weighted_total = 0.0
            weight_sum = 0.0
            for item in similar_runs[-12:]:
                weight = max(0.1, item["similarity"]) * float(item["recency_weight"])
                weighted_total += float(item["seconds_per_minute"]) * weight
                weight_sum += weight
            if weight_sum > 0:
                source = "local_history_similar" if len(similar_runs) >= 2 else "local_history_nearest"
                return round((weighted_total / weight_sum) * video_minutes, 2), source
        stats = self._processing_history_stats(history_runs)
        depth_stats = stats.get("by_depth", {}).get(normalized_depth, {})
        seconds_per_minute = depth_stats.get("seconds_per_video_minute")
        run_count = int(depth_stats.get("run_count") or 0)
        if not seconds_per_minute or run_count < 1:
            return None, "no_local_history"
        return round(float(seconds_per_minute) * video_minutes, 2), "local_history"

    def get_processing_history_summary(self):
        lock = getattr(self, "_processing_history_lock", threading.RLock())
        history = getattr(self, "_processing_history", self._empty_processing_history())
        with lock:
            runs = list(history.get("runs", []))
        stats = self._processing_history_stats(runs)
        try:
            size_bytes = PROCESSING_HISTORY_FILE.stat().st_size if PROCESSING_HISTORY_FILE.exists() else 0
        except Exception:
            size_bytes = 0
        return {
            "schema_version": PROCESSING_HISTORY_SCHEMA_VERSION,
            "path": str(PROCESSING_HISTORY_FILE),
            "exists": PROCESSING_HISTORY_FILE.exists(),
            "size_bytes": size_bytes,
            "local_only": True,
            **stats,
        }

    def _record_processing_history(self, row: dict) -> dict:
        """Append one local run timing row and return the refreshed summary."""
        if not isinstance(row, dict):
            row = {}
        elapsed_seconds = _safe_float_value(row.get("elapsed_seconds"), 0.0)
        estimated_raw = row.get("estimated_total_seconds")
        estimated_seconds = (
            _safe_float_value(estimated_raw, None)
            if estimated_raw is not None
            else None
        )
        raw_stage_timings = row.get("stage_timings") if isinstance(row.get("stage_timings"), dict) else {}
        clean_stage_timings = {}
        for key, value in raw_stage_timings.items():
            try:
                clean_stage_timings[str(key)] = round(float(value), 3)
            except (TypeError, ValueError):
                continue
        tracked_elapsed = sum(
            max(0.0, float(value))
            for value in clean_stage_timings.values()
            if isinstance(value, (int, float))
        )
        if elapsed_seconds > 0:
            untracked = max(0.0, elapsed_seconds - tracked_elapsed)
            if untracked >= 0.5:
                clean_stage_timings["other_untracked"] = round(untracked, 3)
        clean = {
            "schema_version": PROCESSING_HISTORY_SCHEMA_VERSION,
            "run_id": str(row.get("run_id") or ""),
            "started_at_utc": str(row.get("started_at_utc") or ""),
            "finished_at_utc": str(row.get("finished_at_utc") or self._utc_now_label()),
            "status": str(row.get("status") or "success"),
            "elapsed_seconds": round(elapsed_seconds, 3),
            "estimated_total_seconds": (
                round(estimated_seconds, 3)
                if estimated_seconds is not None
                else None
            ),
            "estimate_source": str(row.get("estimate_source") or "unknown"),
            "estimate_error_seconds": None,
            "video_duration_seconds": round(_safe_float_value(row.get("video_duration_seconds"), 0.0), 3),
            "generation_mode": _normalize_generation_mode(
                row.get("generation_mode")
                or (
                    row.get("settings_fingerprint", {}).get("generation_mode")
                    if isinstance(row.get("settings_fingerprint"), dict)
                    else None
                )
            ),
            "processing_depth": _normalize_processing_depth(row.get("processing_depth")),
            "detection_preference": normalize_detection_preference(row.get("detection_preference")),
            "candidate_multiplier": _safe_int_value(row.get("candidate_multiplier"), 0),
            "candidate_count": _safe_int_value(row.get("candidate_count"), 0),
            "selected_count": _safe_int_value(row.get("selected_count"), 0),
            "rendered_clip_count": _safe_int_value(row.get("rendered_clip_count"), 0),
            "settings_fingerprint": row.get("settings_fingerprint") if isinstance(row.get("settings_fingerprint"), dict) else {},
            "stage_timings": clean_stage_timings,
        }
        if clean["estimated_total_seconds"] is not None and clean["elapsed_seconds"] > 0:
            clean["estimate_error_seconds"] = round(clean["elapsed_seconds"] - clean["estimated_total_seconds"], 3)
            clean["estimate_error_ratio"] = round(
                clean["elapsed_seconds"] / max(clean["estimated_total_seconds"], 1.0),
                4,
            )
        with self._processing_history_lock:
            runs = self._processing_history.get("runs", [])
            if not isinstance(runs, list):
                runs = []
            runs.append(clean)
            self._processing_history = {
                "schema_version": PROCESSING_HISTORY_SCHEMA_VERSION,
                "runs": runs[-150:],
            }
            self._save_processing_history()
            summary = self._processing_history_stats(list(self._processing_history.get("runs", [])))
        return {
            "schema_version": PROCESSING_HISTORY_SCHEMA_VERSION,
            "path": str(PROCESSING_HISTORY_FILE),
            **summary,
        }

    def _voice_profile_status_payload(self) -> dict:
        with getattr(self, "_voice_profile_lock", threading.RLock()):
            profile = sanitize_voice_profile(getattr(self, "_voice_profile", empty_voice_profile()))
        try:
            size = VOICE_PROFILE_FILE.stat().st_size if VOICE_PROFILE_FILE.exists() else 0
        except Exception:
            size = 0
        status = voice_profile_status(
            profile,
            file_exists=VOICE_PROFILE_FILE.exists(),
            size_bytes=size,
        )
        ranking_enabled = _normalize_bool_setting(
            (getattr(self, "_user_settings", {}) or {}).get("voice_profile_ranking"),
            False,
        )
        can_score = bool(status.get("can_score") or voice_profile_ready(profile))
        ranking_active = bool(ranking_enabled and can_score)
        status["ranking_enabled"] = ranking_enabled
        status["ranking_active"] = ranking_active
        status["selection_impact"] = "capped_rank_adjustment" if ranking_active else "none"
        status["can_rank"] = can_score
        if ranking_active:
            status["influence_state"] = "influencing"
            status["status_label"] = "Influencing"
            status["next_action"] = "ready"
            status["blocking_reason"] = ""
            status["guidance"] = "Voice ranking can nudge close calls on runs that score candidate audio."
        elif not status.get("enabled"):
            status["influence_state"] = "off"
            status["blocking_reason"] = "disabled"
            status["guidance"] = "Enable the local profile before building samples or using voice ranking."
        elif not status.get("enrolled"):
            status["influence_state"] = "needs_samples"
            status["blocking_reason"] = "not_enrolled"
            status["guidance"] = "Build from current clips after generating clips with clear creator commentary."
        elif not can_score:
            status["influence_state"] = "needs_more_samples"
            status["blocking_reason"] = "needs_more_samples"
            status["guidance"] = (
                "Add more clear creator-commentary samples before voice ranking can help "
                f"({status.get('sample_count', 0)}/{MIN_VOICE_PROFILE_SAMPLES} samples, "
                f"{status.get('total_active_seconds', 0.0):.1f}/{MIN_VOICE_PROFILE_TOTAL_ACTIVE_SECONDS:.0f}s speech)."
            )
        elif ranking_enabled:
            status["influence_state"] = "ready_waiting_for_run"
            status["blocking_reason"] = "waiting_for_candidate_scores"
            status["guidance"] = "Voice ranking is on and will influence runs when candidate voice scores are available."
        else:
            status["influence_state"] = "ready_not_influencing"
            status["blocking_reason"] = "ranking_disabled"
            status["guidance"] = "Profile is ready. Turn on voice ranking to let it make a tiny capped nudge."
        status["ranking_cap"] = round(VOICE_PROFILE_SELECTION_MAX_ADJUSTMENT, 4)
        status["ranking_cap_label"] = f"+/-{VOICE_PROFILE_SELECTION_MAX_ADJUSTMENT:.3f}"
        status["path"] = str(VOICE_PROFILE_FILE)
        return status

    def get_voice_profile_status(self):
        """Return local creator voice-profile status for the settings UI."""
        return self._voice_profile_status_payload()

    def set_voice_profile_ranking_enabled(self, enabled):
        """Opt in/out of using the local voice profile as a capped ranking nudge."""
        ranking_enabled = _normalize_bool_setting(enabled, False)
        self._user_settings = dict(getattr(self, "_user_settings", {}) or {})
        self._user_settings["voice_profile_ranking"] = ranking_enabled
        self._save_state()
        return {"ok": True, "voice_profile": self._voice_profile_status_payload()}

    def set_voice_profile_enabled(self, enabled):
        """Opt in/out of voice-profile scoring without deleting the profile."""
        with self._voice_profile_lock:
            self._voice_profile = sanitize_voice_profile(self._voice_profile)
            self._voice_profile["enabled"] = bool(enabled)
            self._save_voice_profile()
        return {"ok": True, "voice_profile": self._voice_profile_status_payload()}

    def reset_voice_profile(self):
        """Delete the local voice-profile centroid after making a backup."""
        with self._voice_profile_lock:
            backup = self._backup_json_file(VOICE_PROFILE_FILE, "cleared")
            self._voice_profile = empty_voice_profile(enabled=False)
            self._save_voice_profile()
        return {
            "ok": True,
            "backup": str(backup) if backup else "",
            "voice_profile": self._voice_profile_status_payload(),
        }

    def enroll_voice_profile_from_current_clips(self):
        """Build/update the local voice profile from clips currently in Results."""
        self._prune_missing_results()
        results = list(getattr(self, "_results", []) or [])
        moments = list(getattr(self, "_moments", []) or [])
        entries: list[tuple[Path, dict]] = []
        for idx, raw_path in enumerate(results):
            path = self._safe_clip_path(raw_path)
            if not path or not path.exists():
                continue
            moment = moments[idx] if idx < len(moments) and isinstance(moments[idx], dict) else {}
            entries.append((path, moment))
        if not entries:
            return {
                "error": "Generate or load clips first, then build the voice profile.",
                "voice_profile": self._voice_profile_status_payload(),
            }

        enrolled = 0
        skipped: list[dict] = []
        eligible_seen = 0
        self._cleanup_voice_profile_temp_wavs()
        with self._voice_profile_lock:
            profile = sanitize_voice_profile(self._voice_profile)
            profile["enabled"] = True

        for idx, (path, moment) in enumerate(entries, 1):
            eligibility = self._voice_profile_enrollment_eligibility(moment)
            if not eligibility.get("eligible"):
                skipped.append({
                    "index": idx,
                    "reason": eligibility.get("reason", "not_creator_commentary"),
                })
                continue
            eligible_seen += 1
            if enrolled >= 8:
                skipped.append({"index": idx, "reason": "sample_limit_reached"})
                continue
            duration = self._probe_media_duration(path, default=0.0)
            if duration <= 0.5:
                skipped.append({"index": idx, "reason": "duration_unavailable"})
                continue
            sample_path = path
            sample_start = 0
            sample_end = max(1, min(45, int(math.ceil(duration))))
            sample_stream = None
            audio = moment.get("audio_source") if isinstance(moment.get("audio_source"), dict) else {}
            stream_count = _safe_int_value(audio.get("stream_count"), 0)
            selected_stream_raw = moment.get("speech_stream")
            if selected_stream_raw is None:
                selected_stream_raw = audio.get("selected_stream")
            selected_stream = _safe_int_value(selected_stream_raw, None)
            source_path = Path(str(moment.get("source_path") or "")) if moment.get("source_path") else None
            if stream_count > 1:
                if not source_path or not source_path.exists() or selected_stream is None:
                    skipped.append({"index": idx, "reason": "source_speech_stream_unavailable"})
                    continue
                source_start = _safe_float_value(
                    moment.get("render_start")
                    or moment.get("selected_start")
                    or moment.get("start"),
                    0.0,
                )
                source_end = _safe_float_value(
                    moment.get("render_end")
                    or moment.get("selected_end")
                    or moment.get("end"),
                    source_start + duration,
                )
                if source_end <= source_start + 0.5:
                    skipped.append({"index": idx, "reason": "source_time_range_unavailable"})
                    continue
                sample_path = source_path
                sample_start = max(0, int(math.floor(source_start)))
                sample_end = max(sample_start + 1, int(math.ceil(min(source_end, source_start + 45))))
                sample_stream = selected_stream
            wav = SUBTITLES_DIR / f"voice_profile_{uuid.uuid4().hex}.wav"
            try:
                if not extract_audio_clip(sample_path, sample_start, sample_end, wav, audio_stream=sample_stream):
                    skipped.append({"index": idx, "reason": "audio_extract_failed"})
                    continue
                features = extract_voice_features(wav)
                if not features.get("ok"):
                    skipped.append({"index": idx, "reason": features.get("reason", "feature_extract_failed")})
                    continue
                with self._voice_profile_lock:
                    profile = update_voice_profile(
                        profile,
                        features.get("features", []),
                        active_seconds=float(features.get("active_seconds") or 0.0),
                    )
                    self._voice_profile = profile
                enrolled += 1
            finally:
                try:
                    wav.unlink(missing_ok=True)
                except Exception:
                    pass

        with self._voice_profile_lock:
            self._voice_profile = profile
            self._save_voice_profile()

        skip_summary = self._voice_profile_skip_summary(skipped)
        if not enrolled:
            return {
                "error": "No usable creator-commentary voice samples found in the current clips.",
                "eligible_candidates": eligible_seen,
                **skip_summary,
                "voice_profile": self._voice_profile_status_payload(),
            }
        return {
            "ok": True,
            "enrolled_samples": enrolled,
            "eligible_candidates": eligible_seen,
            **skip_summary,
            "voice_profile": self._voice_profile_status_payload(),
        }

    @staticmethod
    def _voice_profile_skip_summary(skipped: list[dict]) -> dict:
        counts: dict[str, int] = {}
        examples: list[dict] = []
        for item in skipped:
            reason = str(item.get("reason") or "unknown")
            counts[reason] = counts.get(reason, 0) + 1
            if len(examples) < 8:
                example = {"reason": reason}
                index = _safe_int_value(item.get("index"), 0)
                if index:
                    example["index"] = index
                examples.append(example)
        return {
            "skipped_total": len(skipped),
            "skipped_by_reason": counts,
            "skipped": examples,
        }

    @staticmethod
    def _voice_profile_enrollment_eligibility(moment: dict | None) -> dict:
        """Decide whether a rendered clip is a safe source for local creator voice samples."""
        if not isinstance(moment, dict) or not moment:
            return {"eligible": False, "reason": "missing_analysis_metadata"}

        word_count = _safe_int_value(
            moment.get("subtitle_word_count")
            or moment.get("analysis_word_count")
            or moment.get("word_count"),
            0,
        )
        if not word_count:
            transcript = str(moment.get("transcript") or "")
            word_count = len(re.findall(r"[A-Za-z0-9']+", transcript))
        if word_count < VOICE_ENROLLMENT_MIN_WORDS:
            return {"eligible": False, "reason": "too_few_creator_words", "word_count": word_count}

        music = moment.get("music_lyrics_guard") if isinstance(moment.get("music_lyrics_guard"), dict) else {}
        lyric_likelihood = _safe_float_value(music.get("lyric_likelihood"), 0.0)
        creator_exception = _safe_float_value(music.get("creator_exception_score"), 0.0)
        music_penalty = _safe_float_value(music.get("selection_penalty"), 0.0)
        if (
            music.get("reject_candidate")
            or music_penalty > 0.0
            or (lyric_likelihood >= 0.45 and creator_exception < 0.62)
        ):
            return {"eligible": False, "reason": "likely_music_or_lyrics"}

        guard = moment.get("commentary_guard") if isinstance(moment.get("commentary_guard"), dict) else {}
        if not guard or str(guard.get("reason") or "") == "disabled":
            dual_track = ApiBridge._voice_profile_dual_track_eligibility(moment)
            if dual_track.get("eligible"):
                return dual_track
            return {"eligible": False, "reason": dual_track.get("reason", "missing_commentary_guard")}
        application = guard.get("application") if isinstance(guard.get("application"), dict) else {}
        raw_policy = guard.get("policy") or application.get("policy")
        policy = normalize_commentary_subtitle_policy(raw_policy)
        if not raw_policy or policy != "creator":
            return {"eligible": False, "reason": "not_creator_policy"}
        if (
            bool(guard.get("output_changed"))
            or bool(application.get("output_changed"))
            or bool(application.get("fallback_used"))
            or _safe_int_value(application.get("removed_word_count"), 0) > 0
        ):
            return {"eligible": False, "reason": "mixed_or_filtered_speech"}
        if (
            _safe_float_value(guard.get("selection_penalty"), 0.0) > 0.0
            or _safe_float_value(application.get("selection_penalty"), 0.0) > 0.0
        ):
            return {"eligible": False, "reason": "commentary_guard_penalty"}
        selection = guard.get("selection") if isinstance(guard.get("selection"), dict) else {}
        if _safe_float_value(selection.get("selection_penalty"), 0.0) > 0.0:
            return {"eligible": False, "reason": "commentary_guard_penalty"}

        summary = guard.get("summary") if isinstance(guard.get("summary"), dict) else {}
        if not summary:
            return {"eligible": False, "reason": "missing_commentary_guard"}
        primary = str(summary.get("primary_label") or "").strip()
        creator_ratio = _safe_float_value(summary.get("creator_word_ratio"), 0.0)
        game_ratio = _safe_float_value(summary.get("game_narration_word_ratio"), 0.0)
        confidence = _safe_float_value(summary.get("confidence"), 0.0)
        if primary == "game_narration" or game_ratio > VOICE_ENROLLMENT_MAX_GAME_RATIO:
            return {"eligible": False, "reason": "likely_game_narration"}
        if primary != "creator_commentary":
            return {"eligible": False, "reason": "not_creator_commentary"}
        if (
            confidence < VOICE_ENROLLMENT_MIN_CREATOR_CONFIDENCE
            or creator_ratio < VOICE_ENROLLMENT_MIN_CREATOR_RATIO
        ):
            return {"eligible": False, "reason": "low_creator_confidence"}

        category = str(moment.get("primary_category") or "").strip()
        if category == "low_value":
            return {"eligible": False, "reason": "low_value_speech"}

        return {
            "eligible": True,
            "reason": "creator_commentary",
            "creator_word_ratio": round(creator_ratio, 4),
        }

    @staticmethod
    def _voice_profile_dual_track_eligibility(moment: dict | None) -> dict:
        if not isinstance(moment, dict) or not moment:
            return {"eligible": False, "reason": "missing_analysis_metadata"}
        audio = moment.get("audio_source") if isinstance(moment.get("audio_source"), dict) else {}
        selection = (
            audio.get("stream_selection")
            if isinstance(audio.get("stream_selection"), dict)
            else moment.get("stream_selection")
            if isinstance(moment.get("stream_selection"), dict)
            else {}
        )
        stream_count = _safe_int_value(audio.get("stream_count"), 0)
        selected_stream = audio.get("selected_stream", selection.get("selected_stream"))
        selected_reason = str(audio.get("selected_reason") or selection.get("selected_reason") or "").strip()
        selected_confidence = _safe_float_value(audio.get("selected_confidence") or selection.get("confidence"), 0.0)
        policy = normalize_commentary_subtitle_policy(audio.get("subtitle_policy") or "creator")
        if stream_count <= 1:
            return {"eligible": False, "reason": "missing_commentary_guard"}
        if selected_stream is None:
            return {"eligible": False, "reason": "missing_selected_speech_stream"}
        if policy != "creator":
            return {"eligible": False, "reason": "not_creator_policy"}
        if selected_reason not in VOICE_ENROLLMENT_CREATOR_STREAM_REASONS:
            return {"eligible": False, "reason": "weak_creator_stream_signal"}
        if selected_confidence < VOICE_ENROLLMENT_DUAL_TRACK_MIN_CONFIDENCE:
            return {"eligible": False, "reason": "low_creator_stream_confidence"}

        categories = moment.get("moment_categories") if isinstance(moment.get("moment_categories"), dict) else {}
        signals = categories.get("signals") if isinstance(categories.get("signals"), dict) else {}
        speech_source = str(signals.get("speech_source") or "").strip()
        source_confidence = _safe_float_value(signals.get("speech_source_confidence"), 0.0)
        creator_speech = _safe_float_value(signals.get("creator_speech"), 0.0)
        game_speech = _safe_float_value(signals.get("game_speech"), 0.0)
        if speech_source != "creator_commentary" and creator_speech < VOICE_ENROLLMENT_MIN_CREATOR_RATIO:
            return {"eligible": False, "reason": "not_creator_commentary"}
        if source_confidence < VOICE_ENROLLMENT_MIN_CREATOR_CONFIDENCE:
            return {"eligible": False, "reason": "low_creator_confidence"}
        if game_speech > VOICE_ENROLLMENT_MAX_GAME_RATIO:
            return {"eligible": False, "reason": "likely_game_narration"}

        return {
            "eligible": True,
            "reason": "dual_track_creator_stream",
            "creator_word_ratio": round(max(creator_speech, source_confidence), 4),
            "selected_stream": selected_stream,
        }

    def _voice_profile_score_for_wav(self, wav_path: Path, profile_snapshot: dict | None = None) -> dict:
        profile = sanitize_voice_profile(profile_snapshot or getattr(self, "_voice_profile", empty_voice_profile()))
        if not profile.get("enabled") or not profile.get("enrolled"):
            return score_voice_profile(profile, None)
        try:
            wav_path = Path(wav_path)
            if not wav_path.exists():
                score = score_voice_profile(profile, None)
                score["reason"] = "wav_missing"
                score["active_seconds"] = 0.0
                return score
            if wav_path.stat().st_size <= 44:
                score = score_voice_profile(profile, None)
                score["reason"] = "wav_empty"
                score["active_seconds"] = 0.0
                return score
        except Exception:
            score = score_voice_profile(profile, None)
            score["reason"] = "wav_unreadable"
            score["active_seconds"] = 0.0
            return score
        features = extract_voice_features(wav_path)
        if not features.get("ok"):
            score = score_voice_profile(profile, None)
            score["reason"] = features.get("reason", "feature_extract_failed")
            score["active_seconds"] = features.get("active_seconds", 0.0)
            return score
        score = score_voice_profile(profile, features.get("features", []))
        score["active_seconds"] = features.get("active_seconds", 0.0)
        score["duration"] = features.get("duration", 0.0)
        return score

    def _voice_profile_inactive_score(self, profile_snapshot: dict | None = None) -> dict:
        profile = sanitize_voice_profile(profile_snapshot or getattr(self, "_voice_profile", empty_voice_profile()))
        score = score_voice_profile(profile, None)
        if profile.get("enabled") and profile.get("enrolled"):
            score["reason"] = "ranking_inactive"
        score["active_seconds"] = 0.0
        return score

    def _probe_media_duration(self, path: Path, default: float = 60.0) -> float:
        try:
            r = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "csv=p=0", str(path),
                ],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if r.returncode == 0:
                return float((r.stdout or "").strip())
        except Exception:
            pass
        return float(default or 0.0)

    def _feedback_identity_for(self, feedback: dict) -> tuple[int | None, dict]:
        idx = self._resolve_clip_index(feedback)
        if idx is not None:
            path = self._results[idx]
            moment = self._ensure_moment_identity(
                self._moments[idx] if idx < len(self._moments) else {},
                path,
            )
            if idx < len(self._moments):
                self._moments[idx] = moment
            return idx, {
                "clip_id": moment.get("clip_id"),
                "source_id": moment.get("source_id"),
                "source_stem": moment.get("source_stem", ""),
                "clip_filename": path.name,
            }

        clip_id = str(feedback.get("clip_id") or "").strip()
        if not clip_id:
            raise ValueError("Feedback needs a clip_id or valid clip index")
        return None, {
            "clip_id": clip_id,
            "source_id": str(feedback.get("source_id") or "").strip(),
            "source_stem": str(feedback.get("source_stem") or "").strip(),
            "clip_filename": str(feedback.get("clip_filename") or "").strip(),
        }

    @staticmethod
    def _clean_learning_terms(values) -> list[str]:
        if not isinstance(values, list):
            return []
        clean: list[str] = []
        seen: set[str] = set()
        for value in values:
            term = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
            term = re.sub(r"\s+", " ", term)
            if not term or term in seen:
                continue
            seen.add(term)
            clean.append(term[:80])
            if len(clean) >= 40:
                break
        return clean

    @staticmethod
    def _clean_feedback_reasons(values) -> dict[str, str]:
        if not isinstance(values, dict):
            return {}
        clean: dict[str, str] = {}
        for key, value in values.items():
            event_type = str(key or "").strip().lower()
            if event_type not in {"like", "dislike", "favorite"}:
                continue
            reason = str(value or "").strip()[:1000]
            if reason:
                clean[event_type] = reason
        return clean

    @staticmethod
    def _feedback_display_reason(
        reasons: dict[str, str],
        event_type: str,
        like: bool,
        dislike: bool,
        favorite: bool,
        fallback: str = "",
    ) -> str:
        active_flags = {"like": like, "dislike": dislike, "favorite": favorite}
        if active_flags.get(event_type) and reasons.get(event_type):
            return reasons[event_type]
        for key in ("like", "dislike", "favorite"):
            if active_flags.get(key) and reasons.get(key):
                return reasons[key]
        return str(fallback or "").strip()[:1000]

    @staticmethod
    def _feedback_active_flag(value) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() not in {"", "0", "false", "no", "off"}
        return bool(value)

    @classmethod
    def _learning_terms_from_feedback_snapshot(cls, snapshot: dict | None) -> list[str]:
        if not isinstance(snapshot, dict):
            return []
        terms = cls._clean_learning_terms(snapshot.get("learning_terms"))
        if terms:
            return terms
        nested = snapshot.get("learning_snapshot")
        if isinstance(nested, dict):
            terms = cls._clean_learning_terms(nested.get("learning_terms"))
            if terms:
                return terms
        return build_learning_terms(
            cls._feedback_learning_text(snapshot),
            categories=snapshot.get("moment_categories") if isinstance(snapshot.get("moment_categories"), dict) else {},
            primary_category=str(snapshot.get("primary_category") or ""),
        )

    @staticmethod
    def _feedback_learning_text(snapshot: dict | None) -> str:
        if not isinstance(snapshot, dict):
            return ""
        parts = [str(snapshot.get("transcript") or "")]
        ai = snapshot.get("ai_moment_classification")
        if isinstance(ai, dict):
            parts.append(str(ai.get("primary_category") or ""))
            for key in ("fine_labels", "supporting_labels"):
                values = ai.get(key)
                if isinstance(values, list):
                    parts.extend(str(value or "") for value in values[:8])
        visual = snapshot.get("visual_diagnostics")
        if isinstance(visual, dict):
            values = visual.get("labels")
            if isinstance(values, list):
                parts.extend(str(value or "") for value in values[:8])
        multimodal = snapshot.get("multimodal_analysis")
        if isinstance(multimodal, dict):
            parts.append(str(multimodal.get("primary_visual_label") or ""))
            for key in ("visual_labels", "detected_events", "title_hooks", "metadata_keywords"):
                values = multimodal.get(key)
                if isinstance(values, list):
                    parts.extend(str(value or "") for value in values[:8])
        return " ".join(part for part in parts if str(part or "").strip())

    def _feedback_clip_snapshot(self, clip_idx: int | None) -> dict:
        if clip_idx is None or clip_idx < 0 or clip_idx >= len(self._moments):
            return {}
        moment = self._moments[clip_idx]
        if not isinstance(moment, dict):
            return {}
        ranker = moment.get("ranker") if isinstance(moment.get("ranker"), dict) else {}
        transcript = str(moment.get("transcript") or "")[:4000]
        moment_categories = moment.get("moment_categories")
        if not isinstance(moment_categories, dict):
            moment_categories = {}
        primary_category = moment.get("primary_category")
        learning_terms = build_learning_terms(
            self._feedback_learning_text({
                "transcript": transcript,
                "ai_moment_classification": moment.get("ai_moment_classification"),
                "visual_diagnostics": moment.get("visual_diagnostics"),
                "multimodal_analysis": moment.get("multimodal_analysis"),
            }),
            categories=moment_categories,
            primary_category=str(primary_category or ""),
        )
        return {
            "start": moment.get("start"),
            "end": moment.get("end"),
            "duration": moment.get("duration"),
            "peak_time": moment.get("peak_time"),
            "quality_score": moment.get("quality_score"),
            "selection_quality_score": moment.get("selection_quality_score"),
            "quality_rank": moment.get("quality_rank"),
            "quality_floor": moment.get("quality_floor"),
            "detection_preference": moment.get("detection_preference"),
            "moment_categories": moment_categories,
            "primary_category": primary_category,
            "ai_moment_classification": moment.get("ai_moment_classification"),
            "visual_diagnostics": moment.get("visual_diagnostics"),
            "multimodal_analysis": moment.get("multimodal_analysis"),
            "commentary_guard": moment.get("commentary_guard"),
            "word_count": moment.get("word_count"),
            "analysis_word_count": moment.get("analysis_word_count"),
            "subtitle_word_count": moment.get("subtitle_word_count"),
            "speech_stream": moment.get("speech_stream"),
            "transcript": transcript,
            "learning_terms": learning_terms,
            "learning_terms_version": 1,
            "learning_terms_count": len(learning_terms),
            "learning_basis": {
                "has_transcript": bool(transcript),
                "has_ai_labels": isinstance(moment.get("ai_moment_classification"), dict),
                "has_multimodal_labels": isinstance(moment.get("multimodal_analysis"), dict),
                "primary_category": primary_category or "",
                "stores_media": False,
            },
            "ranker": {
                "hook_points": ranker.get("hook_points"),
                "weak_points": ranker.get("weak_points"),
                "aftermath_points": ranker.get("aftermath_points"),
                "first_word_start": ranker.get("first_word_start"),
                "last_word_end": ranker.get("last_word_end"),
            },
        }

    def get_personalization(self):
        """Return persisted feedback summaries and event log."""
        with self._personalization_lock:
            return json.loads(json.dumps(self._personalization))

    @staticmethod
    def _redact_personalization_export(personalization: dict) -> dict:
        """Return a shareable feedback export without user text or source IDs."""
        payload = json.loads(json.dumps(personalization or {}))

        def public_hash(value):
            text = str(value or "").strip()
            if not text:
                return ""
            return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

        def redact_identity_fields(container):
            if not isinstance(container, dict):
                return
            redact_learning_terms(container)
            for key in ("clip_id", "source_id"):
                value = container.pop(key, "")
                hashed = public_hash(value)
                if hashed:
                    container[f"{key}_hash"] = hashed
            if container.pop("reason", ""):
                container["reason_redacted"] = True
            reasons = container.pop("reasons", None)
            if isinstance(reasons, dict) and any(str(value or "").strip() for value in reasons.values()):
                container["reasons_redacted"] = True
                container["reasons_count"] = sum(1 for value in reasons.values() if str(value or "").strip())
            for key in (
                "event_id",
                "clip_filename",
                "source_stem",
                "source_path",
                "timestamp",
                "updated_at",
                "created_at",
                "last_feedback_at",
                "latest_timestamp",
            ):
                container.pop(key, None)

        def redact_learning_terms(container):
            if not isinstance(container, dict):
                return
            terms = container.pop("learning_terms", None)
            if isinstance(terms, list) and terms:
                container["learning_terms_redacted"] = True
                container["learning_terms_count"] = len(terms)
            nested = container.get("learning_snapshot")
            if isinstance(nested, dict):
                redact_learning_terms(nested)

        def redact_snapshot(container):
            if not isinstance(container, dict):
                return
            snapshot = container.get("clip_snapshot")
            if not isinstance(snapshot, dict):
                return
            redact_learning_terms(snapshot)
            transcript = str(snapshot.pop("transcript", "") or "")
            if transcript:
                snapshot["transcript_redacted"] = True
                snapshot["transcript_chars"] = len(transcript)
            if snapshot.pop("voice_profile", None) is not None:
                snapshot["voice_profile_redacted"] = True
            guard = snapshot.get("commentary_guard")
            if isinstance(guard, dict):
                for segment in guard.get("segments", []) if isinstance(guard.get("segments"), list) else []:
                    if not isinstance(segment, dict):
                        continue
                    text = str(segment.pop("text", "") or "")
                    segment.pop("text_preview", None)
                    if text:
                        segment["text_redacted"] = True
                        segment["text_chars"] = len(text)

        for event in payload.get("events", []) if isinstance(payload.get("events"), list) else []:
            redact_identity_fields(event)
            redact_snapshot(event)
        clips = payload.get("clips", {})
        if isinstance(clips, dict):
            redacted_clips = {}
            for key, entry in clips.items():
                if not isinstance(entry, dict):
                    continue
                redact_identity_fields(entry)
                latest = entry.get("latest")
                if isinstance(latest, dict):
                    redact_identity_fields(latest)
                redact_snapshot(entry)
                redacted_key = public_hash(key) or f"clip_{len(redacted_clips) + 1}"
                redacted_clips[redacted_key] = entry
            payload["clips"] = redacted_clips

        payload["export_redacted"] = True
        payload["export_redactions"] = [
            "clip_snapshot.transcript",
            "clip_snapshot.learning_terms",
            "clip_snapshot.commentary_guard.segments.text",
            "clip_snapshot.voice_profile",
            "learning_terms",
            "reason",
            "reasons",
            "clip_filename",
            "source_stem",
            "clip_id",
            "source_id",
            "timestamps",
        ]
        return payload

    def get_data_privacy_summary(self):
        """Return local data and personalization counts for the settings UI."""
        with self._personalization_lock:
            events = self._personalization.get("events", [])
            clips = self._personalization.get("clips", {})
            if not isinstance(events, list):
                events = []
            if not isinstance(clips, dict):
                clips = {}
            latest_values = []
            for entry in clips.values():
                if not isinstance(entry, dict):
                    continue
                latest = entry.get("latest", {})
                if isinstance(latest, dict):
                    latest_values.append(latest)
            like_count = sum(1 for latest in latest_values if latest.get("like"))
            dislike_count = sum(1 for latest in latest_values if latest.get("dislike"))
            favorite_count = sum(1 for latest in latest_values if latest.get("favorite"))
            feedback_times = [
                str(event.get("timestamp") or "")
                for event in events
                if isinstance(event, dict) and event.get("timestamp")
            ]
            for entry in clips.values():
                if not isinstance(entry, dict):
                    continue
                latest = entry.get("latest", {})
                if isinstance(latest, dict) and latest.get("timestamp"):
                    feedback_times.append(str(latest.get("timestamp")))
                if entry.get("updated_at"):
                    feedback_times.append(str(entry.get("updated_at")))
            latest_timestamp = max(feedback_times) if feedback_times else ""
            run_learning = getattr(self, "_run_learning", empty_run_learning())
            if not isinstance(run_learning, dict):
                run_learning = empty_run_learning()
            with getattr(self, "_run_learning_lock", threading.RLock()):
                run_learning_snapshot = json.loads(json.dumps(run_learning))
            learning_status = build_learning_status(
                self._personalization,
                run_learning=run_learning_snapshot,
            )
            learning_status["last_feedback_time"] = latest_timestamp
            learning_status["last_feedback_at"] = latest_timestamp
            try:
                personalization_size = PERSONALIZATION_FILE.stat().st_size if PERSONALIZATION_FILE.exists() else 0
            except Exception:
                personalization_size = 0
        try:
            state_size = STATE_FILE.stat().st_size if STATE_FILE.exists() else 0
        except Exception:
            state_size = 0
        settings = self.get_settings()
        depth = _normalize_processing_depth(settings.get("processing_depth"))
        depth_profile = _processing_depth_profile(
            depth,
            normalize_detection_preference(settings.get("detection_preference")),
            0,
        )
        visual_analysis_enabled = _normalize_bool_setting(settings.get("visual_diagnostics"), True)
        ai_moment_labels_enabled = _normalize_bool_setting(settings.get("ai_moment_classification"), False)
        moment_label_ranking_enabled = _normalize_bool_setting(settings.get("moment_category_ranking"), False)
        voice_profile_ranking_enabled = _normalize_bool_setting(settings.get("voice_profile_ranking"), False)
        vision_context_enabled = bool(depth_profile.get("multimodal_analysis"))
        voice_profile_summary = self._voice_profile_status_payload()
        feature_statuses = _depth_feature_statuses(
            depth,
            depth_profile,
            visual_requested=visual_analysis_enabled,
            ai_requested=ai_moment_labels_enabled,
            category_requested=moment_label_ranking_enabled,
            voice_requested=voice_profile_ranking_enabled,
            multimodal_requested=vision_context_enabled,
            voice_status=voice_profile_summary,
        )
        return {
            "personalization": {
                "path": str(PERSONALIZATION_FILE),
                "exists": PERSONALIZATION_FILE.exists(),
                "size_bytes": personalization_size,
                "event_count": len(events),
                "clip_count": len(clips),
                "like_count": like_count,
                "dislike_count": dislike_count,
                "favorite_count": favorite_count,
                "latest_timestamp": latest_timestamp,
                "last_feedback_at": latest_timestamp,
                "learning": learning_status,
                "schema_version": PERSONALIZATION_SCHEMA_VERSION,
            },
            "learning": learning_status,
            "state": {
                "path": str(STATE_FILE),
                "exists": STATE_FILE.exists(),
                "size_bytes": state_size,
                "schema_version": STATE_SCHEMA_VERSION,
            },
            "voice_profile": voice_profile_summary,
            "processing_history": self.get_processing_history_summary(),
            "run_learning": self.get_run_learning_summary(),
            "local_analysis": {
                "processing_depth": depth,
                "visual_analysis_enabled": visual_analysis_enabled,
                "ai_moment_labels_enabled": ai_moment_labels_enabled,
                "moment_label_ranking_enabled": moment_label_ranking_enabled,
                "voice_profile_ranking_enabled": voice_profile_ranking_enabled,
                "vision_context_enabled": vision_context_enabled,
                "depth_preset_controls": {
                    "visual_analysis": depth_profile.get("visual_diagnostics"),
                    "vision_context": depth_profile.get("multimodal_analysis"),
                    "ai_moment_labels": depth_profile.get("ai_moment_classification"),
                    "moment_label_ranking": depth_profile.get("moment_category_ranking"),
                    "voice_profile_ranking": depth_profile.get("voice_profile_ranking"),
                },
                "feature_statuses": feature_statuses,
                "selection_caps": {
                    "feedback_learning": round(LEARNED_SELECTION_MAX_ADJUSTMENT, 4),
                    "moment_label_ranking": round(MOMENT_CATEGORY_SELECTION_MAX_ADJUSTMENT, 4),
                    "vision_context": round(MULTIMODAL_SELECTION_MAX_ADJUSTMENT, 4),
                    "voice_profile_ranking": round(VOICE_PROFILE_SELECTION_MAX_ADJUSTMENT, 4),
                },
            },
            "local_only": True,
        }

    def open_personalization_file(self):
        """Open personalization.json in the system default app."""
        with self._personalization_lock:
            if not PERSONALIZATION_FILE.exists():
                self._save_personalization()
        try:
            os.startfile(str(PERSONALIZATION_FILE))
        except Exception as e:
            return {"error": str(e)}
        return {"ok": True}

    def export_personalization(self):
        """Export a redacted personalization summary to a user-selected JSON file."""
        import webview

        with self._personalization_lock:
            snapshot = self._redact_personalization_export(self._personalization)
        try:
            result = self._window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename="personalization_export.json",
                file_types=("JSON files (*.json)", "All files (*.*)"),
            )
            if not result:
                return {"cancelled": True}
            target = Path(result[0] if isinstance(result, (list, tuple)) else result)
            if target.suffix.lower() != ".json":
                target = target.with_suffix(".json")
            self._write_json_atomic(target, snapshot)
            return {"ok": True, "path": str(target)}
        except Exception as e:
            return {"error": str(e)}

    def clear_personalization(self):
        """Clear all local feedback events and summaries."""
        with self._personalization_lock:
            event_count = len(self._personalization.get("events", []))
            clip_count = len(self._personalization.get("clips", {}))
            backup = self._backup_json_file(PERSONALIZATION_FILE, "cleared")
            self._personalization = self._empty_personalization()
            self._save_personalization()
        return {
            "ok": True,
            "cleared": {"event_count": event_count, "clip_count": clip_count},
            "backup": str(backup) if backup else "",
            "personalization": self.get_personalization(),
        }

    def record_feedback(self, feedback):
        """Append a like/dislike/favorite feedback event for a clip."""
        if not isinstance(feedback, dict):
            return {"error": "Feedback payload must be an object"}

        event_type = str(feedback.get("event_type") or feedback.get("type") or "").strip().lower()
        if event_type not in {"like", "dislike", "favorite"}:
            return {"error": "event_type must be like, dislike, or favorite"}

        try:
            clip_idx, identity = self._feedback_identity_for(feedback)
        except Exception as e:
            return {"error": str(e)}

        active = self._feedback_active_flag(feedback.get("active", True))
        reason = str(feedback.get("reason") or "").strip()[:1000]
        timestamp = self._utc_now_label()
        clip_snapshot = self._feedback_clip_snapshot(clip_idx)

        with self._personalization_lock:
            data = self._personalization
            data.setdefault("schema_version", PERSONALIZATION_SCHEMA_VERSION)
            events = data.setdefault("events", [])
            clips = data.setdefault("clips", {})

            clip_id = identity["clip_id"]
            previous_entry = clips.get(clip_id, {})
            if not isinstance(previous_entry, dict):
                previous_entry = {}
            previous = previous_entry.get("latest", {})
            if not isinstance(previous, dict):
                previous = {}
            previous_snapshot = previous_entry.get("clip_snapshot")
            if not isinstance(previous_snapshot, dict):
                previous_snapshot = {}
            if not clip_snapshot and previous_snapshot:
                clip_snapshot = json.loads(json.dumps(previous_snapshot))
            learning_terms = self._learning_terms_from_feedback_snapshot(clip_snapshot)
            if not learning_terms:
                learning_terms = self._clean_learning_terms(previous_entry.get("learning_terms"))
            if not learning_terms:
                learning_terms = self._clean_learning_terms(feedback.get("learning_terms"))
            if learning_terms:
                if not clip_snapshot:
                    clip_snapshot = {}
                clip_snapshot["learning_terms"] = learning_terms
                clip_snapshot["learning_terms_version"] = 1
                clip_snapshot["learning_terms_count"] = len(learning_terms)
            like = bool(previous.get("like", False))
            dislike = bool(previous.get("dislike", False))
            favorite = bool(previous.get("favorite", False))
            reasons = self._clean_feedback_reasons(previous.get("reasons"))
            legacy_reason = str(previous.get("reason") or "").strip()[:1000]
            legacy_type = str(previous.get("event_type") or "").strip().lower()
            if legacy_reason and not reasons:
                if legacy_type not in {"like", "dislike", "favorite"}:
                    legacy_type = "like" if like else "dislike" if dislike else "favorite" if favorite else ""
                if legacy_type in {"like", "dislike", "favorite"}:
                    reasons[legacy_type] = legacy_reason
            elif legacy_reason and legacy_type in {"like", "dislike", "favorite"} and legacy_type not in reasons:
                reasons[legacy_type] = legacy_reason

            if event_type == "like":
                like = active
                if active:
                    dislike = False
                    reasons.pop("dislike", None)
            elif event_type == "dislike":
                dislike = active
                if active:
                    like = False
                    reasons.pop("like", None)
            elif event_type == "favorite":
                favorite = active
            if active and reason:
                reasons[event_type] = reason
            elif not active:
                reasons.pop(event_type, None)
            if not like:
                reasons.pop("like", None)
            if not dislike:
                reasons.pop("dislike", None)
            if not favorite:
                reasons.pop("favorite", None)
            display_reason = self._feedback_display_reason(
                reasons,
                event_type,
                like,
                dislike,
                favorite,
                fallback=legacy_reason,
            )

            event = {
                "event_id": self._hash_id("fb", timestamp, clip_id, event_type, len(events), reason, length=18),
                "event_type": event_type,
                "active": active,
                "clip_id": clip_id,
                "source_id": identity.get("source_id", ""),
                "source_stem": identity.get("source_stem", ""),
                "clip_filename": identity.get("clip_filename", ""),
                "like": like,
                "dislike": dislike,
                "favorite": favorite,
                "reason": reason,
                "timestamp": timestamp,
            }
            if reasons:
                event["reasons"] = dict(reasons)
            if learning_terms:
                event["learning_terms"] = learning_terms
            if clip_snapshot:
                event["clip_snapshot"] = clip_snapshot
            events.append(event)

            latest = {
                "like": like,
                "dislike": dislike,
                "favorite": favorite,
                "reason": display_reason,
                "reasons": dict(reasons),
                "timestamp": timestamp,
                "event_type": event_type,
            }
            entry = {
                "clip_id": clip_id,
                "source_id": identity.get("source_id", ""),
                "source_stem": identity.get("source_stem", ""),
                "clip_filename": identity.get("clip_filename", ""),
                "latest": latest,
                "event_count": int(previous_entry.get("event_count", 0)) + 1,
                "updated_at": timestamp,
            }
            if previous_entry.get("rendered_file_deleted"):
                entry["rendered_file_deleted"] = True
                entry["deleted_at"] = previous_entry.get("deleted_at", "")
                entry["deleted_filename"] = previous_entry.get("deleted_filename", "")
            if learning_terms:
                entry["learning_terms"] = learning_terms
            if clip_snapshot:
                entry["clip_snapshot"] = clip_snapshot
            clips[clip_id] = entry
            self._save_personalization()

        try:
            self._record_run_learning_event(
                build_feedback_event(
                    event_id=event.get("event_id", ""),
                    event_type=event_type,
                    active=active,
                    timestamp=timestamp,
                    identity=identity,
                    reason=reason,
                    clip_snapshot=clip_snapshot,
                )
            )
        except Exception as exc:
            print(f"[learning] Failed to record feedback outcome: {exc}")

        voice_profile_nudge = self._voice_profile_nudge_for_feedback(
            event_type,
            active,
            clip_snapshot,
        )
        return {"ok": True, "event": event, "clip": entry, "voice_profile_nudge": voice_profile_nudge}

    def _voice_profile_nudge_for_feedback(self, event_type: str, active: bool, clip_snapshot: dict | None) -> dict:
        """Suggest local voice-profile enrollment after good creator-commentary feedback."""
        event_type = str(event_type or "").strip().lower()
        if not active or event_type not in {"like", "favorite"}:
            return {"show": False, "reason": "feedback_not_positive"}
        lock = getattr(self, "_voice_profile_lock", threading.RLock())
        profile = getattr(self, "_voice_profile", empty_voice_profile())
        with lock:
            voice = voice_profile_status(
                profile,
                file_exists=VOICE_PROFILE_FILE.exists(),
                size_bytes=VOICE_PROFILE_FILE.stat().st_size if VOICE_PROFILE_FILE.exists() else 0,
            )
        if voice.get("enrolled"):
            return {"show": False, "reason": "voice_profile_already_enrolled"}
        eligibility = self._voice_profile_enrollment_eligibility(clip_snapshot or {})
        if not eligibility.get("eligible"):
            return {
                "show": False,
                "reason": eligibility.get("reason", "not_voice_profile_ready"),
                "eligibility": eligibility,
            }
        return {
            "show": True,
            "reason": "liked_creator_commentary_clip",
            "message": "This liked clip looks useful for your local Creator Voice Profile. Build it from Data & Privacy when you are ready.",
            "next_action": "open_voice_profile_settings",
            "eligibility": eligibility,
            "voice_profile": {
                "enabled": bool(voice.get("enabled")),
                "enrolled": bool(voice.get("enrolled")),
                "sample_count": int(voice.get("sample_count") or 0),
            },
        }

    def _mark_personalization_clips_deleted(self, clip_ids=None, filenames=None):
        clip_ids = {str(item or "").strip() for item in (clip_ids or []) if str(item or "").strip()}
        filenames = {str(item or "").strip() for item in (filenames or []) if str(item or "").strip()}
        if not clip_ids and not filenames:
            return
        timestamp = self._utc_now_label()
        changed = False
        personalization = getattr(self, "_personalization", None)
        if not isinstance(personalization, dict):
            return
        lock = getattr(self, "_personalization_lock", threading.RLock())
        learning_events: list[dict] = []
        with lock:
            clips = personalization.setdefault("clips", {})
            if not isinstance(clips, dict):
                return
            for clip_id, entry in clips.items():
                if not isinstance(entry, dict):
                    continue
                filename = str(entry.get("clip_filename") or "").strip()
                if str(clip_id) not in clip_ids and str(entry.get("clip_id") or "") not in clip_ids and filename not in filenames:
                    continue
                entry["rendered_file_deleted"] = True
                entry["deleted_at"] = timestamp
                if filename:
                    entry["deleted_filename"] = filename
                learning_events.append({
                    "clip_id": str(entry.get("clip_id") or clip_id or ""),
                    "source_id": str(entry.get("source_id") or ""),
                    "source_stem": str(entry.get("source_stem") or ""),
                    "clip_filename": filename,
                })
                changed = True
            if changed:
                self._save_personalization()
        if learning_events:
            for identity in learning_events:
                try:
                    self._record_run_learning_event(
                        build_clip_deleted_event(
                            event_id=self._hash_id(
                                "learn",
                                timestamp,
                                identity.get("clip_id"),
                                identity.get("clip_filename"),
                                "deleted",
                                length=18,
                            ),
                            timestamp=timestamp,
                            clip_id=identity.get("clip_id", ""),
                            source_id=identity.get("source_id", ""),
                            source_stem=identity.get("source_stem", ""),
                            clip_filename=identity.get("clip_filename", ""),
                            reason="rendered_file_deleted",
                        )
                    )
                except Exception as exc:
                    print(f"[learning] Failed to record deleted clip outcome: {exc}")

    def _prune_missing_results(self) -> int:
        """Remove deleted clip paths from state while keeping moments/schedule aligned."""
        if not self._results:
            return 0

        old_results = list(self._results)
        old_moments = list(self._moments)
        index_map = {}
        new_results = []
        new_moments = []
        removed_clip_ids: set[str] = set()
        removed_filenames: set[str] = set()

        for old_idx, path in enumerate(old_results):
            safe_path = self._safe_clip_path(path)
            if safe_path:
                index_map[old_idx] = len(new_results)
                new_results.append(safe_path)
                new_moments.append(
                    self._ensure_moment_identity(
                        old_moments[old_idx] if old_idx < len(old_moments) else {},
                        safe_path,
                    )
                )
            else:
                self._delete_clip_sidecar(path, reason="missing_video")
                if old_idx < len(old_moments) and isinstance(old_moments[old_idx], dict):
                    clip_id = str(old_moments[old_idx].get("clip_id") or "").strip()
                    if clip_id:
                        removed_clip_ids.add(clip_id)
                try:
                    removed_filenames.add(Path(path).name)
                except Exception:
                    pass

        removed = len(old_results) - len(new_results)
        if not removed:
            return 0

        self._results = new_results
        self._moments = new_moments
        self._scheduled = self._normalize_scheduled_items(self._scheduled, legacy_index_map=index_map)
        print(f"[refresh] Removed {removed} deleted clip reference(s) from state")
        self._mark_personalization_clips_deleted(removed_clip_ids, removed_filenames)
        self._save_state()
        return removed

    def get_results(self):
        self._prune_missing_results()
        self._prune_orphan_metadata_sidecars()
        clips = []
        self._metadata_hydration_changed = False
        for i, p in enumerate(self._results):
            clips.append(self._clip_payload(i, p, include_url=True))
        if getattr(self, "_metadata_hydration_changed", False):
            self._metadata_hydration_changed = False
            self._save_state()
        return {"clips": clips, "moments": self._moments}

    def _resolve_clip_context_index(self, clip_id=None, clip_index=None, filename=None) -> int:
        try:
            idx = int(clip_index)
            if 0 <= idx < len(self._results):
                return idx
        except Exception:
            pass
        target_id = str(clip_id or "").strip()
        if target_id:
            for idx, moment in enumerate(self._moments):
                if isinstance(moment, dict) and str(moment.get("clip_id") or "").strip() == target_id:
                    return idx
        target_name = Path(str(filename or "")).name if filename else ""
        if target_name:
            for idx, path in enumerate(self._results):
                try:
                    if Path(path).name == target_name:
                        return idx
                except Exception:
                    continue
            try:
                safe_path = self._safe_clip_path(self._clips_dir() / target_name)
            except Exception:
                safe_path = None
            if safe_path and safe_path.exists():
                self._results.append(safe_path)
                self._moments.append(self._ensure_moment_identity({}, safe_path))
                return len(self._results) - 1
        return -1

    def _set_clip_title_context(self, clip_index: int, text: str) -> dict:
        if clip_index < 0 or clip_index >= len(self._results):
            return {"error": "Clip not found"}
        context = sanitize_creator_title_context(text)
        try:
            path = Path(self._results[clip_index])
        except Exception:
            return {"error": "Clip file is missing"}
        if not path.exists():
            return {"error": "Clip file is missing"}
        original_moment = copy.deepcopy(self._moments[clip_index]) if clip_index < len(self._moments) and isinstance(self._moments[clip_index], dict) else {}
        moment = self._ensure_moment_identity(
            self._moments[clip_index] if clip_index < len(self._moments) else {},
            path,
        )
        previous = sanitize_creator_title_context(moment.get("creator_title_context"))
        if context:
            moment["creator_title_context"] = context
        else:
            moment.pop("creator_title_context", None)
        if previous != context:
            moment.pop("generated_metadata", None)
        self._moments[clip_index] = moment
        changed = moment != original_moment
        clip_id = str(moment.get("clip_id") or "").strip()
        updated_scheduled = 0
        for item in self._scheduled:
            same_clip = False
            if clip_id and str(item.get("clip_id") or "").strip() == clip_id:
                same_clip = True
            else:
                try:
                    same_clip = int(item.get("clipIdx")) == clip_index
                except Exception:
                    same_clip = False
            if not same_clip:
                continue
            old_context = sanitize_creator_title_context(item.get("creator_title_context"))
            if str(item.get("creator_title_context") or "") != context:
                changed = True
            item["creator_title_context"] = context
            if old_context != context:
                item["description_generated"] = ""
                item["generated_description"] = ""
                item["metadata_stale"] = True
                changed = True
            updated_scheduled += 1
        return {
            "ok": True,
            "changed": changed,
            "clip_index": clip_index,
            "clip_id": clip_id,
            "updated_scheduled": updated_scheduled,
            "creator_title_context": context,
        }

    def save_clip_title_context(self, clip_id=None, clip_index=None, filename=None, text=""):
        """Save optional AI notes for one clip only."""
        with self._get_state_lock():
            idx = self._resolve_clip_context_index(clip_id=clip_id, clip_index=clip_index, filename=filename)
            result = self._set_clip_title_context(idx, text)
            if result.get("ok") and result.get("changed"):
                self._save_state()
            return result

    def _set_clip_game_title(self, clip_index: int, text: str) -> dict:
        if clip_index < 0 or clip_index >= len(self._results):
            return {"error": "Clip not found"}
        game_title = self._sanitize_game_title_hint(text)
        try:
            path = Path(self._results[clip_index])
        except Exception:
            return {"error": "Clip file is missing"}
        if not path.exists():
            return {"error": "Clip file is missing"}

        original_moment = copy.deepcopy(self._moments[clip_index]) if clip_index < len(self._moments) and isinstance(self._moments[clip_index], dict) else {}
        moment = self._ensure_moment_identity(
            self._moments[clip_index] if clip_index < len(self._moments) else {},
            path,
        )
        previous = self._sanitize_game_title_hint(moment.get("game_title") or moment.get("game_title_hint"))
        if game_title:
            moment["game_title"] = game_title
            moment["game_title_hint"] = game_title
            truth = moment.get("truth_summary")
            if not isinstance(truth, dict):
                truth = {}
            truth["game_title"] = game_title
            truth["game_source_label"] = "Manual"
            moment["truth_summary"] = truth
            metadata = moment.get("generated_metadata")
            if isinstance(metadata, dict):
                metadata["game_title"] = game_title
        else:
            moment.pop("game_title", None)
            moment.pop("game_title_hint", None)
            truth = moment.get("truth_summary")
            if isinstance(truth, dict):
                truth["game_title"] = ""
                truth["game_source_label"] = "Unknown"
            metadata = moment.get("generated_metadata")
            if isinstance(metadata, dict):
                metadata["game_title"] = ""

        while len(self._moments) <= clip_index:
            self._moments.append({})
        self._moments[clip_index] = moment
        changed = moment != original_moment or previous != game_title
        clip_id = str(moment.get("clip_id") or "").strip()
        updated_scheduled = 0
        for item in self._scheduled:
            same_clip = False
            if clip_id and str(item.get("clip_id") or "").strip() == clip_id:
                same_clip = True
            else:
                try:
                    same_clip = int(item.get("clipIdx")) == clip_index
                except Exception:
                    same_clip = False
            if not same_clip:
                continue
            old_title = self._sanitize_game_title_hint(item.get("game_title"))
            item["game_title"] = game_title
            if old_title != game_title:
                item["metadata_stale"] = True
                changed = True
            updated_scheduled += 1

        return {
            "ok": True,
            "changed": changed,
            "clip_index": clip_index,
            "clip_id": clip_id,
            "updated_scheduled": updated_scheduled,
            "game_title": game_title,
        }

    def save_clip_game_title(self, clip_id=None, clip_index=None, filename=None, text=""):
        """Save a user-corrected game title for one clip."""
        with self._get_state_lock():
            idx = self._resolve_clip_context_index(clip_id=clip_id, clip_index=clip_index, filename=filename)
            result = self._set_clip_game_title(idx, text)
            if result.get("ok") and result.get("changed"):
                self._save_state()
            return result

    def open_output_folder(self):
        try:
            clips_dir = self._clips_dir()
            clips_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(str(clips_dir))
        except Exception as e:
            return {"error": str(e)}
        return {"ok": True}

    def select_output_folder(self):
        """Let the user choose the default generated-video output folder."""
        import webview

        try:
            result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception as e:
            return {"error": str(e)}
        if not result:
            return {"cancelled": True}
        raw = result[0] if isinstance(result, (list, tuple)) else result
        try:
            path = Path(raw).expanduser().resolve()
            path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {"error": f"Could not use that folder: {e}"}
        self._user_settings = dict(getattr(self, "_user_settings", {}) or {})
        self._user_settings["output_dir"] = str(path)
        self._sync_video_server_root()
        self._save_state()
        return {"ok": True, "path": str(path)}

    def reset_output_folder(self):
        """Return generated clips to the default LocalAppData clips folder."""
        self._user_settings = dict(getattr(self, "_user_settings", {}) or {})
        self._user_settings.pop("output_dir", None)
        self._sync_video_server_root()
        self._save_state()
        return {"ok": True, "path": str(self._clips_dir())}

    def select_file(self):
        import webview

        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("Video files (*.mp4;*.mkv;*.avi;*.mov;*.webm)", "All files (*.*)"),
        )
        if result and len(result) > 0:
            return {"path": result[0]}
        return {"path": None}

    def select_files_multiple(self):
        """Open file dialog allowing multiple file selection."""
        import webview

        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("Video files (*.mp4;*.mkv;*.avi;*.mov;*.webm)", "All files (*.*)"),
            allow_multiple=True,
        )
        if result and len(result) > 0:
            return {"paths": list(result)}
        return {"paths": []}

    # ── Exposed: video preview ───────────────────────────────────────────

    def get_video_url(self, clip_index):
        """Return a local HTTP URL for the clip so the HTML5 <video> can play it."""
        if 0 <= clip_index < len(self._results):
            p = self._safe_clip_path(self._results[clip_index])
            if p:
                return {"url": self._clip_url_for_path(p)}
        return {"url": None}

    def get_subtitle_preview_url(self):
        """Return the newest tracked clip URL for the settings subtitle preview."""
        self._prune_missing_results()
        for idx in range(len(self._results) - 1, -1, -1):
            p = self._safe_clip_path(self._results[idx])
            if not p:
                continue
            return {
                "url": self._clip_url_for_path(p),
                "filename": p.name,
                "index": idx,
            }
        return {"url": None}

    # ── Exposed: delete clip ────────────────────────────────────────────

    def delete_clip(self, clip_index):
        """Delete a clip by its index in the current results list."""
        if 0 <= clip_index < len(self._results):
            p = self._safe_clip_path(self._results[clip_index])
            if not p:
                self._prune_missing_results()
                return {"error": "Clip path is missing or outside the clips folder"}
            clip_id = None
            if clip_index < len(self._moments):
                clip_id = self._moments[clip_index].get("clip_id")
            try:
                deleted_name = p.name
                deleted_ok, delete_error = self._unlink_clip_file(p)
                if not deleted_ok:
                    return {"error": delete_error}
                sidecar_deleted = self._delete_clip_sidecar(p, reason="clip_deleted")
                self._results.pop(clip_index)
                # Remove matching moments entry
                if clip_index < len(self._moments):
                    self._moments.pop(clip_index)
                self._scheduled = [
                    s for s in self._scheduled
                    if s.get("clip_id") != clip_id and s.get("clipIdx") != clip_index
                ]
                self._scheduled = self._normalize_scheduled_items(self._scheduled)
                self._mark_personalization_clips_deleted({clip_id} if clip_id else set(), {deleted_name})
                self._save_state()
                return {"ok": True, "sidecar_deleted": sidecar_deleted}
            except Exception as e:
                return {"error": str(e)}
        return {"error": "Invalid clip index"}

    def delete_library_file(self, filename):
        """Delete a video file from the clips folder by filename."""
        result = self.delete_library_files([filename])
        if result.get("deleted") or result.get("missing_pruned"):
            return {"ok": True}
        failed = result.get("failed") or []
        if failed:
            return {"error": failed[0].get("error") or "Delete failed"}
        return {"error": result.get("error") or "File not found"}

    def delete_library_files(self, filenames):
        """Delete multiple video files from the clips folder by exact filename."""
        if not isinstance(filenames, list):
            return {"ok": False, "deleted": [], "failed": [{"filename": "", "error": "Expected a list of filenames"}]}

        video_exts = {'.mp4', '.mkv', '.avi', '.mov', '.webm'}
        targets: list[tuple[str, Path]] = []
        failed = []
        seen = set()
        missing_names: set[str] = set()
        clips_dir = self._clips_dir()

        for raw in filenames:
            name = str(raw or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            if Path(name).name != name:
                failed.append({"filename": name, "error": "Invalid filename"})
                continue
            target = self._safe_child_path(clips_dir, name)
            if not target or not target.exists() or not target.is_file():
                missing_names.add(name)
                failed.append({"filename": name, "error": "File not found"})
                continue
            if target.suffix.lower() not in video_exts:
                failed.append({"filename": name, "error": "Not a supported video file"})
                continue
            targets.append((name, target))

        deleted = []
        deleted_names = set()
        sidecars_deleted = []
        missing_pruned = []
        removed_ids = set()
        lock = getattr(self, "_state_lock", threading.RLock())
        with lock:
            for name, target in targets:
                try:
                    deleted_ok, delete_error = self._unlink_clip_file(target)
                    if not deleted_ok:
                        failed.append({"filename": name, "error": delete_error})
                        continue
                    if self._delete_clip_sidecar(target, reason="library_delete"):
                        sidecars_deleted.append(target.with_suffix(".txt").name)
                    deleted.append(name)
                    deleted_names.add(name)
                except Exception as exc:
                    failed.append({"filename": name, "error": str(exc)})

            if deleted_names:
                for i, path in enumerate(self._results):
                    if Path(path).name in deleted_names and i < len(self._moments):
                        clip_id = self._moments[i].get("clip_id")
                        if clip_id:
                            removed_ids.add(clip_id)

                keep = [i for i, path in enumerate(self._results) if Path(path).name not in deleted_names]
                old_moments = list(self._moments)
                self._results = [self._results[i] for i in keep]
                self._moments = [old_moments[i] for i in keep if i < len(old_moments)]
                self._scheduled = [
                    s for s in self._scheduled
                    if s.get("clip_id") not in removed_ids and s.get("clip_filename") not in deleted_names
                ]
                self._scheduled = self._normalize_scheduled_items(self._scheduled)
                self._mark_personalization_clips_deleted(removed_ids, deleted_names)
                self._save_state()

            if missing_names:
                before_state = set()
                for path in self._results:
                    try:
                        before_state.add(Path(path).name)
                    except Exception:
                        continue
                state_missing = before_state.intersection(missing_names)
                if state_missing:
                    self._prune_missing_results()
                    after_state = set()
                    for path in self._results:
                        try:
                            after_state.add(Path(path).name)
                        except Exception:
                            continue
                    missing_pruned = sorted(state_missing - after_state)
                    if missing_pruned:
                        failed = [item for item in failed if item.get("filename") not in set(missing_pruned)]

        return {
            "ok": bool(deleted or missing_pruned),
            "deleted": deleted,
            "sidecars_deleted": sidecars_deleted,
            "missing_pruned": missing_pruned,
            "failed": failed,
        }

    # ── Exposed: library (all videos) ────────────────────────────────────

    def list_all_clips(self):
        """List all video files in the clips directory."""
        self._prune_missing_results()
        self._prune_orphan_metadata_sidecars()
        clips = []
        total_size = 0
        _exts = {'.mp4', '.mkv', '.avi', '.mov', '.webm'}
        known = {
            p.resolve(): i
            for i, p in enumerate(self._results)
            if p.exists()
        }
        clips_dir = self._clips_dir()
        if clips_dir.exists():
            # Single stat() per file — cache the result
            entries = []
            for p in clips_dir.iterdir():
                safe_path = self._safe_path_under(clips_dir, p)
                if not safe_path or not safe_path.is_file():
                    continue
                if safe_path.suffix.lower() in _exts:
                    st = safe_path.stat()
                    entries.append((safe_path, st))
            entries.sort(key=lambda x: x[1].st_mtime, reverse=True)
            for p, st in entries:
                total_size += st.st_size
                clip = {
                    "filename": p.name,
                    "size_mb": round(st.st_size / (1024 * 1024), 1),
                    "modified": st.st_mtime,
                    "url": self._clip_url_for_path(p),
                }
                known_idx = known.get(p.resolve())
                if known_idx is not None:
                    moment = self._ensure_moment_identity(
                        self._moments[known_idx] if known_idx < len(self._moments) else {},
                        p,
                    )
                    if known_idx < len(self._moments):
                        self._moments[known_idx] = moment
                    compact_categories = self._compact_moment_categories(moment.get("moment_categories"))
                    clip.update({
                        "clip_id": moment.get("clip_id"),
                        "source_id": moment.get("source_id"),
                        "source_stem": moment.get("source_stem", ""),
                        "primary_category": moment.get("primary_category"),
                        "moment_categories": compact_categories,
                        "ai_moment_classification": moment.get("ai_moment_classification"),
                        "creator_title_context": sanitize_creator_title_context(moment.get("creator_title_context")),
                        "subtitle_style": moment.get("subtitle_style"),
                        "captions_requested": moment.get("captions_requested"),
                        "subtitle_enabled": moment.get("subtitle_enabled"),
                        "subtitle_generated": moment.get("subtitle_generated"),
                        "subtitles_burned": moment.get("subtitles_burned"),
                        "subtitle_placement": moment.get("subtitle_placement"),
                        "truth_summary": self._clip_truth_summary(moment, p),
                    })
                else:
                    moment = self._ensure_moment_identity({}, p)
                    compact_categories = self._compact_moment_categories(moment.get("moment_categories"))
                    clip.update({
                        "clip_id": moment.get("clip_id"),
                        "source_id": moment.get("source_id"),
                        "source_stem": moment.get("source_stem", ""),
                        "primary_category": moment.get("primary_category"),
                        "moment_categories": compact_categories,
                        "ai_moment_classification": moment.get("ai_moment_classification"),
                        "creator_title_context": sanitize_creator_title_context(moment.get("creator_title_context")),
                        "subtitle_style": moment.get("subtitle_style"),
                        "captions_requested": moment.get("captions_requested"),
                        "subtitle_enabled": moment.get("subtitle_enabled"),
                        "subtitle_generated": moment.get("subtitle_generated"),
                        "subtitles_burned": moment.get("subtitles_burned"),
                        "subtitle_placement": moment.get("subtitle_placement"),
                        "truth_summary": self._clip_truth_summary(moment, p),
                    })
                clips.append(clip)
        return {
            "clips": clips,
            "total_size_mb": round(total_size / (1024 * 1024), 1),
            "count": len(clips),
        }

    def import_folder_clips(self):
        """Scan the clips folder and add any videos not already tracked.

        This lets users drop videos into the clips/ folder and have them
        appear in the upload section alongside pipeline-generated clips.
        Returns the updated results list.
        """
        removed = self._prune_missing_results()
        self._prune_orphan_metadata_sidecars()
        _exts = {'.mp4', '.mkv', '.avi', '.mov', '.webm'}
        existing = {p.resolve() for p in self._results if p.exists()}
        added = 0

        clips_dir = self._clips_dir()
        if clips_dir.exists():
            safe_entries = []
            for p in clips_dir.iterdir():
                safe_path = self._safe_path_under(clips_dir, p)
                if safe_path and safe_path.is_file() and safe_path.suffix.lower() in _exts:
                    safe_entries.append(safe_path)
            for p in sorted(safe_entries, key=lambda x: x.stat().st_mtime):
                resolved = p.resolve()
                if resolved not in existing:
                    self._results.append(p)
                    self._moments.append(self._ensure_moment_identity({}, p))
                    existing.add(resolved)
                    added += 1

        if added or removed:
            self._save_state()
            if added:
                print(f"[+] Imported {added} clip(s) from clips folder")

        return self.get_results()

    # ── Exposed: schedule management ─────────────────────────────────────

    def save_scheduled(self, scheduled_list):
        """Replace the full scheduled list (called from JS on every change)."""
        with self._state_lock:
            incoming = self._normalize_scheduled_items(scheduled_list or [])
            self._scheduled = self._merge_backend_schedule_fields(incoming, self._scheduled)
            self._save_state()
        return {"ok": True}

    def get_all_scheduled(self):
        """Return the persisted scheduled list."""
        with self._state_lock:
            self._prune_missing_results()
            self._scheduled = self._normalize_scheduled_items(self._scheduled)
            missed = self._mark_overdue_schedules_missed()
            if missed:
                self._save_state()
            self._upload_history = self._normalize_upload_history(getattr(self, "_upload_history", []))
            return {"scheduled": self._scheduled, "upload_history": self._upload_history}

    # ── Exposed: upload ──────────────────────────────────────────────────

    @staticmethod
    def _parse_publish_at(meta: dict) -> datetime | None:
        """Return a timezone-aware UTC publish datetime from upload metadata."""
        if not isinstance(meta, dict):
            return None

        raw = meta.get("publish_at") or meta.get("scheduled_time")
        if raw:
            text = str(raw).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.astimezone()
            return dt.astimezone(timezone.utc).replace(microsecond=0)

        local = str(meta.get("scheduled_local") or "").strip()
        offset = meta.get("timezone_offset_minutes")
        if local and offset is not None:
            local_dt = datetime.fromisoformat(local)
            if local_dt.tzinfo is not None:
                return local_dt.astimezone(timezone.utc).replace(microsecond=0)
            return (local_dt + timedelta(minutes=int(offset))).replace(
                tzinfo=timezone.utc,
                microsecond=0,
            )

        if meta.get("date") and meta.get("time"):
            local_dt = datetime.fromisoformat(f"{meta['date']}T{meta['time']}")
            return local_dt.astimezone(timezone.utc).replace(microsecond=0)
        return None

    @classmethod
    def _ordered_upload_metadata(cls, clips_metadata) -> list[tuple[int, dict, datetime | None]]:
        if not isinstance(clips_metadata, list):
            raise ValueError("Upload metadata must be a list")
        parsed = []
        for original_index, meta in enumerate(clips_metadata):
            if not isinstance(meta, dict):
                raise ValueError("Each upload item must be an object")
            try:
                publish_at = cls._parse_publish_at(meta)
            except Exception as exc:
                raise ValueError(f"Invalid publish time for clip {original_index + 1}: {exc}") from exc
            parsed.append((original_index, meta, publish_at))
        return sorted(
            parsed,
            key=lambda item: (item[2] or datetime.max.replace(tzinfo=timezone.utc), item[0]),
        )

    def _validate_upload_metadata(self, clips_metadata) -> list[tuple[int, dict, datetime | None]]:
        ordered = self._ordered_upload_metadata(clips_metadata)
        now_utc = datetime.now(timezone.utc)
        for original_index, meta, publish_at in ordered:
            privacy = str(meta.get("privacy", "private") or "private").lower()
            if privacy == "public" and not publish_at:
                label = meta.get("title") or meta.get("clip_filename") or f"clip {original_index + 1}"
                raise ValueError(f"Scheduled publish time is required for public upload: {label}")
            if publish_at and privacy == "public" and publish_at <= now_utc + SCHEDULE_PUBLISH_BUFFER:
                label = meta.get("title") or meta.get("clip_filename") or f"clip {original_index + 1}"
                minutes = int(SCHEDULE_PUBLISH_BUFFER.total_seconds() // 60)
                raise ValueError(f"Scheduled publish time must be at least {minutes} minutes from now for {label}")
        return ordered

    def _get_upload_lock(self):
        lock = getattr(self, "_upload_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._upload_lock = lock
        return lock

    def _get_state_lock(self):
        lock = getattr(self, "_state_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._state_lock = lock
        return lock

    def _is_cancelled(self) -> bool:
        with self._get_state_lock():
            return bool(getattr(self, "_cancel", False))

    def start_upload(self, clips_metadata, channel_id=None):
        """Upload clips with per-clip metadata.

        clips_metadata: list of {index, clip_id, source_id, title, description, tags, privacy, publish_at}
        YouTube category is intentionally fixed to Gaming.
        channel_id: YouTube channel ID to upload to (from get_channels())
        """
        with self._get_state_lock():
            if getattr(self, "_processing", False):
                return {"error": "Processing in progress"}
        try:
            ordered = self._validate_upload_metadata(clips_metadata)
        except Exception as exc:
            return {"error": str(exc)}
        if not ordered:
            return {"error": "No clips to upload"}
        upload_lock = self._get_upload_lock()
        if not upload_lock.acquire(blocking=False):
            return {"error": "Upload already in progress"}
        with self._get_state_lock():
            if getattr(self, "_processing", False):
                try:
                    upload_lock.release()
                except RuntimeError:
                    pass
                return {"error": "Processing in progress"}
            self._processing = True
            self._cancel = False
        threading.Thread(
            target=self._run_upload,
            args=(clips_metadata, channel_id, upload_lock),
            daemon=True,
        ).start()
        return {"ok": True}

    def upload_single_clip(self, clip_index, meta, channel_id=None):
        """Upload one clip through the same schedule-aware state path."""
        if clip_index >= len(self._results):
            return {"error": "Invalid clip index"}
        video_path = self._safe_clip_path(self._results[clip_index])
        if not video_path:
            return {"error": "Clip file not found"}
        upload_lock = self._get_upload_lock()
        if not upload_lock.acquire(blocking=False):
            return {"error": "Upload already in progress"}
        with self._get_state_lock():
            if getattr(self, "_processing", False):
                try:
                    upload_lock.release()
                except RuntimeError:
                    pass
                return {"error": "Processing in progress"}
            self._processing = True
            self._cancel = False
        attempt_started = False
        try:
            normalized_meta = self._ensure_schedule_description(dict(meta or {}), clip_index)
            if "index" not in normalized_meta and "clipIdx" not in normalized_meta:
                normalized_meta["index"] = clip_index
            try:
                _original_index, normalized_meta, scheduled = self._validate_upload_metadata([normalized_meta])[0]
            except Exception as exc:
                return {"error": str(exc)}
            scheduled_active = self._scheduled_upload_active(clip_index, normalized_meta, channel_id)
            if scheduled_active:
                attempt_id = self._begin_scheduled_upload_attempt(
                    clip_index,
                    normalized_meta,
                    trigger="single",
                    channel_id=channel_id,
                )
                attempt_started = bool(attempt_id)
                if not attempt_started:
                    return {"error": "Scheduled item changed before upload"}
                self._js("window.onScheduleUpdated()")
            else:
                attempt_id = None
            result = upload_to_youtube(
                video_path,
                title=normalized_meta.get("title", f"Viral Clip #{clip_index + 1}"),
                description=normalized_meta.get("final_description") or normalized_meta.get("description", ""),
                tags=normalized_meta.get("tags", generate_tags()),
                privacy=normalized_meta.get("privacy", "private"),
                scheduled_time=scheduled,
                channel_id=normalized_meta.get("channel_id") or channel_id,
                account_id=normalized_meta.get("account_id"),
                cancel_check=lambda: self._is_cancelled() or (
                    bool(attempt_id)
                    and not self._scheduled_upload_active(clip_index, normalized_meta, channel_id, attempt_id=attempt_id)
                ),
            )
            if scheduled_active and self._mark_scheduled_uploaded(clip_index, normalized_meta, result, trigger="single", attempt_id=attempt_id):
                self._js("window.onScheduleUpdated()")
            else:
                self._append_upload_history(
                    self._upload_history_record(normalized_meta, clip_index, normalized_meta, result, trigger="single")
                )
                self._save_state()
            return {"ok": True}
        except Exception as e:
            if attempt_started:
                if self._is_cancelled():
                    self._clear_scheduled_upload_attempt(clip_index, normalized_meta, channel_id, attempt_id=attempt_id)
                else:
                    self._mark_scheduled_upload_failed_for_clip(clip_index, normalized_meta, e, attempt_id=attempt_id)
                self._js("window.onScheduleUpdated()")
            return {"error": str(e)}
        finally:
            with self._get_state_lock():
                self._processing = False
            try:
                upload_lock.release()
            except RuntimeError:
                pass

    # ── Exposed: background scheduler ────────────────────────────────────

    def start_scheduler(self):
        """Start the background upload scheduler thread."""
        with self._get_state_lock():
            if self._scheduler_running:
                return {"ok": True}
            self._scheduler_running = True
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        print("[+] Background upload scheduler started")
        return {"ok": True}

    # ── Exposed: state persistence ───────────────────────────────────────

    def load_persisted_state(self):
        """Return persisted results/moments/scheduled for frontend init."""
        self._prune_missing_results()
        with self._state_lock:
            if self._mark_overdue_schedules_missed():
                self._save_state()
        clips = []
        for i, p in enumerate(self._results):
            clips.append(self._clip_payload(i, p, include_url=False))
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "clips": clips,
            "moments": self._moments[:len(self._results)],
            "scheduled": self._scheduled,
            "upload_history": self._normalize_upload_history(getattr(self, "_upload_history", [])),
        }

    # ── Candidate-debug recovery ─────────────────────────────────────────

    def _latest_candidate_debug_path(self) -> Path | None:
        """Return the newest usable candidate debug report under SUBTITLES_DIR."""
        if not SUBTITLES_DIR.exists():
            return None
        candidates = sorted(
            SUBTITLES_DIR.glob("*_candidate_debug.json"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )
        for path in candidates:
            safe_path = self._safe_path_under(SUBTITLES_DIR, path)
            if not safe_path or not safe_path.is_file():
                continue
            try:
                payload = json.loads(safe_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if payload.get("debug_stage") != "candidate_pre_render":
                continue
            if int(payload.get("selected_count") or 0) <= 0:
                continue
            rows = payload.get("candidates")
            if isinstance(rows, list) and any(isinstance(row, dict) and row.get("selected") for row in rows):
                return safe_path
        return None

    def _latest_run_debug_path(self) -> Path | None:
        """Return the newest usable run debug report under SUBTITLES_DIR."""
        if not SUBTITLES_DIR.exists():
            return None
        candidates = sorted(
            SUBTITLES_DIR.glob("*_run_debug.json"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )
        for path in candidates:
            safe_path = self._safe_path_under(SUBTITLES_DIR, path)
            if not safe_path or not safe_path.is_file():
                continue
            try:
                payload = json.loads(safe_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            rows = payload.get("candidates")
            if isinstance(rows, list):
                return safe_path
        return None

    def get_montage_candidate_audit(
        self,
        debug_path=None,
        target_beats: int = 3,
        target_duration_seconds=None,
    ):
        """Build and persist a compact montage-readiness audit from run debug."""
        if debug_path:
            safe_path = self._safe_path_under(SUBTITLES_DIR, debug_path)
        else:
            safe_path = self._latest_run_debug_path()
        if not safe_path or not safe_path.is_file():
            return {
                "ok": False,
                "error": "No run debug report is available for montage audit.",
                "audit": None,
            }
        try:
            payload = json.loads(safe_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Could not read run debug report: {exc}",
                "audit": None,
                "debug_path": str(safe_path),
            }

        try:
            audit = build_candidate_audit(
                payload,
                target_beats=target_beats,
                target_duration_seconds=target_duration_seconds,
            )
            base_stem = safe_path.stem
            if base_stem.endswith("_run_debug"):
                base_stem = base_stem[: -len("_run_debug")]
            safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", base_stem).strip("._") or "latest"
            output_path = MONTAGES_DIR / f"{safe_stem}_montage_audit.json"
            write_candidate_audit(output_path, audit)
            return {
                "ok": True,
                "audit": audit,
                "path": str(output_path),
                "debug_path": str(safe_path),
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Could not build montage audit: {exc}",
                "audit": None,
                "debug_path": str(safe_path),
            }

    def draft_montage(
        self,
        debug_path=None,
        target_duration_seconds=60,
        story_shape="hook_escalate_payoff",
        memory_enabled=True,
        render_quality="draft",
    ):
        """Create a storyboard-only montage draft from the latest run debug."""
        audit_result = self.get_montage_candidate_audit(
            debug_path=debug_path,
            target_beats=3,
            target_duration_seconds=target_duration_seconds,
        )
        if not audit_result.get("ok"):
            return {
                "ok": False,
                "error": audit_result.get("error") or "Could not build montage audit.",
                "audit": audit_result.get("audit"),
                "storyboard": None,
            }
        audit = audit_result.get("audit") if isinstance(audit_result.get("audit"), dict) else {}
        try:
            storyboard = build_storyboard_from_audit(
                audit,
                target_duration_seconds=target_duration_seconds,
                story_shape=story_shape,
                memory_enabled=bool(memory_enabled),
                render_quality=render_quality,
            )
            if storyboard.get("status") == "no_storyboard":
                return {
                    "ok": False,
                    "error": "No usable beats are available for montage storyboard.",
                    "audit": audit,
                    "storyboard": storyboard,
                    "debug_path": audit_result.get("debug_path"),
                }
            source = storyboard.get("source") if isinstance(storyboard.get("source"), dict) else {}
            base_stem = str(source.get("source_stem") or "latest")
            safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", base_stem).strip("._") or "latest"
            output_path = MONTAGES_DIR / f"{safe_stem}_montage_storyboard.json"
            write_storyboard(output_path, storyboard)
            return {
                "ok": True,
                "audit": audit,
                "storyboard": storyboard,
                "path": str(output_path),
                "audit_path": audit_result.get("path"),
                "debug_path": audit_result.get("debug_path"),
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Could not draft montage storyboard: {exc}",
                "audit": audit,
                "storyboard": None,
                "debug_path": audit_result.get("debug_path"),
            }

    def _latest_montage_storyboard_path(self) -> Path | None:
        if not MONTAGES_DIR.exists():
            return None
        candidates = sorted(
            MONTAGES_DIR.glob("*_montage_storyboard.json"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )
        for path in candidates:
            safe_path = self._safe_path_under(MONTAGES_DIR, path)
            if safe_path and safe_path.is_file():
                return safe_path
        return None

    def get_montage_storyboard(self, storyboard_path=None):
        """Return a saved storyboard JSON from the local montage cache."""
        if storyboard_path:
            safe_path = self._safe_path_under(MONTAGES_DIR, storyboard_path)
        else:
            safe_path = self._latest_montage_storyboard_path()
        if not safe_path or not safe_path.is_file():
            return {
                "ok": False,
                "error": "No montage storyboard is available.",
                "storyboard": None,
            }
        try:
            storyboard = json.loads(safe_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Could not read montage storyboard: {exc}",
                "storyboard": None,
                "path": str(safe_path),
            }
        return {"ok": True, "storyboard": storyboard, "path": str(safe_path)}

    def _storyboard_source_matches_run_debug(self, storyboard: dict) -> bool:
        """Only render storyboards whose source can be traced to saved run debug."""
        return self._run_debug_for_storyboard_source(storyboard) is not None

    def _run_debug_for_storyboard_source(self, storyboard: dict) -> dict | None:
        """Return the saved run debug payload that owns a storyboard source."""
        if not isinstance(storyboard, dict):
            return None
        source = storyboard.get("source") if isinstance(storyboard.get("source"), dict) else {}
        run_id = str(source.get("run_id") or "").strip()
        video = str(source.get("video") or "").strip()
        if not video or not SUBTITLES_DIR.exists():
            return None
        for path in SUBTITLES_DIR.glob("*_run_debug.json"):
            safe_path = self._safe_path_under(SUBTITLES_DIR, path)
            if not safe_path or not safe_path.is_file():
                continue
            try:
                payload = json.loads(safe_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            payload_run_id = str(payload.get("run_id") or "").strip()
            payload_video = str(payload.get("video") or "").strip()
            if run_id and payload_run_id == run_id and payload_video == video:
                return payload
            if not run_id and payload_video == video:
                return payload
        return None

    def _montage_render_options_from_run_debug(self, payload: dict | None) -> dict:
        settings = payload.get("settings") if isinstance(payload, dict) and isinstance(payload.get("settings"), dict) else {}
        audio_source_debug = settings.get("audio_source") if isinstance(settings.get("audio_source"), dict) else {}
        speech_stream = audio_source_debug.get("selected_stream")
        try:
            speech_stream = int(speech_stream) if speech_stream is not None else None
        except (TypeError, ValueError):
            speech_stream = None
        style_value = settings.get("subtitle_style")
        style = _normalize_subtitle_style(style_value, SUBTITLE_STYLE) if style_value else _normalize_subtitle_style("none", SUBTITLE_STYLE)
        return {
            "processing_depth": _normalize_processing_depth(settings.get("processing_depth")),
            "subtitle_style": style,
            "subtitle_enabled": subtitles_are_enabled(style),
            "subtitle_placement": normalize_subtitle_placement(
                settings.get("subtitle_placement", SUBTITLE_PLACEMENT)
            ),
            "crop_vertical": bool(settings.get("crop_vertical")) if "crop_vertical" in settings else False,
            "whisper_model": settings.get("whisper_model", WHISPER_MODEL),
            "whisper_language": settings.get("whisper_language") or None,
            "speech_stream": speech_stream,
            "allow_stream_retry": bool(audio_source_debug.get("alternate_stream_retry", True)),
            "commentary_guard_policy": normalize_commentary_subtitle_policy(audio_source_debug.get("subtitle_policy")),
            "commentary_guard_enabled": bool(audio_source_debug.get("commentary_guard_enabled")),
            "audio_source_debug": audio_source_debug,
        }

    def _prepare_montage_storyboard_for_render(
        self,
        storyboard: dict,
        *,
        source_video: Path,
        output_stem: str,
        render_options: dict | None = None,
    ) -> tuple[dict, dict]:
        options = render_options if isinstance(render_options, dict) else {}
        prepared = copy.deepcopy(storyboard)
        source_video = Path(source_video)
        processing_depth = _normalize_processing_depth(options.get("processing_depth"))
        style = _normalize_subtitle_style(options.get("subtitle_style", "none"), "none")
        subtitle_enabled = bool(options.get("subtitle_enabled") and subtitles_are_enabled(style))
        subtitle_placement = normalize_subtitle_placement(
            options.get("subtitle_placement", SUBTITLE_PLACEMENT)
        )
        crop_vertical = bool(options.get("crop_vertical"))
        model = str(options.get("whisper_model") or WHISPER_MODEL)
        language = options.get("whisper_language") or None
        speech_stream = options.get("speech_stream")
        try:
            speech_stream = int(speech_stream) if speech_stream is not None else None
        except (TypeError, ValueError):
            speech_stream = None
        allow_stream_retry = bool(options.get("allow_stream_retry", False))
        commentary_guard_policy = normalize_commentary_subtitle_policy(
            options.get("commentary_guard_policy")
        )
        commentary_guard_enabled = bool(options.get("commentary_guard_enabled"))
        render_plan = prepared.get("render_plan") if isinstance(prepared.get("render_plan"), list) else []
        beats = prepared.get("beats") if isinstance(prepared.get("beats"), list) else []
        width, height = get_dimensions(source_video)
        summary = {
            "crop_vertical": crop_vertical,
            "subtitle_enabled": subtitle_enabled,
            "subtitle_style": style,
            "subtitle_segments": 0,
            "subtitle_word_count": 0,
            "crop_segments": 0,
            "segments_prepared": 0,
            "warnings": [],
        }
        crop_profile = _crop_tracking_profile(processing_depth)
        crop_sample_count = min(int(crop_profile["sample_count"]), 28)
        crop_min_sample_rate = min(float(crop_profile["min_sample_rate"]), 1.5)
        for index, item in enumerate(render_plan, 1):
            if not isinstance(item, dict):
                continue
            try:
                start = max(0.0, float(item.get("start") or 0.0))
                end = max(start, float(item.get("end") or start))
            except (TypeError, ValueError):
                continue
            item["source_video"] = str(source_video)
            item["start"] = round(start, 3)
            item["end"] = round(end, 3)
            crop_params = None
            crop_w, crop_h = width, height
            if crop_vertical:
                try:
                    crop_params = get_crop_params_dynamic(
                        source_video,
                        int(start),
                        max(int(end), int(start) + 1),
                        sample_count=crop_sample_count,
                        min_sample_rate=crop_min_sample_rate,
                    )
                except Exception as exc:
                    summary["warnings"].append(f"beat_{index}_crop_failed:{exc}")
                    crop_params = None
                if crop_params:
                    crop_w, crop_h = crop_params[0], crop_params[1]
                    item["crop_params"] = crop_params
                    summary["crop_segments"] += 1

            words = []
            ass_path = None
            final_stream = speech_stream
            if subtitle_enabled:
                wav = SUBTITLES_DIR / f"{output_stem}_montage_b{index}.wav"
                ass = SUBTITLES_DIR / f"{output_stem}_montage_b{index}.ass"
                for stale in (wav, ass):
                    try:
                        stale.unlink(missing_ok=True)
                    except Exception:
                        pass
                if extract_audio_clip(source_video, start, end, wav, audio_stream=final_stream):
                    words = transcribe_clip(wav, model_size=model, language=language)
                if allow_stream_retry and needs_stream_retry(
                    words,
                    end - start,
                    subtitle_policy=commentary_guard_policy,
                    commentary_guard=commentary_guard_enabled,
                ):
                    retry_words, retry_stream = self._try_alternate_audio_streams(
                        source_video,
                        start,
                        end,
                        wav,
                        model,
                        language,
                        index,
                        max(1, len(render_plan)),
                        final_stream,
                        return_stream=True,
                        progress_stage="silent",
                        subtitle_policy=commentary_guard_policy,
                    )
                    if retry_words:
                        words = retry_words
                        final_stream = retry_stream
                if words:
                    ass_path = generate_subtitles(
                        words,
                        ass,
                        video_width=crop_w or width or 1080,
                        video_height=crop_h or height or 1920,
                        style=style,
                        subtitle_placement=subtitle_placement,
                    )
                if ass_path:
                    item["subtitle_path"] = str(ass_path)
                    summary["subtitle_segments"] += 1
                item["subtitle_word_count"] = len(words)
                item["speech_stream"] = final_stream
                summary["subtitle_word_count"] += len(words)

            resolved_placement = resolve_subtitle_placement(
                crop_w or width or 1080,
                crop_h or height or 1920,
                subtitle_placement,
            )
            item["subtitle_enabled"] = subtitle_enabled
            item["subtitle_generated"] = bool(ass_path)
            item["subtitle_placement"] = resolved_placement
            item["crop_applied"] = bool(crop_params)
            if index <= len(beats) and isinstance(beats[index - 1], dict):
                beat = beats[index - 1]
                beat["subtitle_enabled"] = subtitle_enabled
                beat["subtitle_generated"] = bool(ass_path)
                beat["subtitle_word_count"] = len(words)
                beat["speech_stream"] = final_stream
                beat["crop_applied"] = bool(crop_params)
                beat["subtitle_placement"] = resolved_placement
            summary["segments_prepared"] += 1
        return prepared, summary

    def _render_montage_from_storyboard(self, storyboard_path=None, *, final: bool = False):
        """Render a storyboard montage and add it to Results."""
        storyboard_result = self.get_montage_storyboard(storyboard_path)
        if not storyboard_result.get("ok"):
            return storyboard_result
        storyboard = storyboard_result.get("storyboard")
        if not isinstance(storyboard, dict):
            return {"ok": False, "error": "Storyboard JSON is invalid.", "render": None}
        run_debug = self._run_debug_for_storyboard_source(storyboard)
        if not run_debug:
            return {
                "ok": False,
                "error": "Storyboard source does not match a saved run debug report.",
                "render": None,
                "storyboard": storyboard,
            }
        return self._render_montage_storyboard_payload(
            storyboard,
            storyboard_path=storyboard_result.get("path"),
            final=final,
            render_options=self._montage_render_options_from_run_debug(run_debug),
        )

    def _render_montage_storyboard_payload(
        self,
        storyboard: dict,
        *,
        storyboard_path=None,
        final: bool = False,
        render_options: dict | None = None,
    ):
        """Render a validated in-memory storyboard and add the montage to Results."""
        if not isinstance(storyboard, dict):
            return {"ok": False, "error": "Storyboard JSON is invalid.", "render": None}
        source = storyboard.get("source") if isinstance(storyboard.get("source"), dict) else {}
        source_video = Path(str(source.get("video") or ""))
        if not source_video.exists() or not source_video.is_file():
            return {
                "ok": False,
                "error": "Storyboard source video is missing.",
                "render": None,
                "storyboard": storyboard,
            }
        render_type = "final_hard_cut" if final else "draft_hard_cut"
        output_stem = str(source.get("source_stem") or source_video.stem or "montage")
        if final:
            output_stem = f"{output_stem}_final"
        output_path = self._unique_montage_output_path(output_stem)
        temp_dir = MONTAGES_DIR / ("_final_render_tmp" if final else "_draft_render_tmp")
        render_storyboard, render_prep = self._prepare_montage_storyboard_for_render(
            storyboard,
            source_video=source_video,
            output_stem=output_path.stem,
            render_options=render_options,
        )
        render = render_draft_montage(
            render_storyboard,
            output_path,
            temp_dir=temp_dir,
            preset="veryfast" if final else "ultrafast",
            crf="23" if final else "28",
            render_type=render_type,
        )
        debug_path = MONTAGES_DIR / f"{output_path.stem}_montage_render_debug.json"
        try:
            self._write_json_atomic(
                debug_path,
                {
                    "schema_version": 1,
                    "storyboard_id": storyboard.get("storyboard_id"),
                    "storyboard_path": str(storyboard_path or ""),
                    "render_mode": "final" if final else "draft",
                    "render_prep": render_prep,
                    "render": render,
                },
            )
        except Exception as exc:
            print(f"[montage] Failed to write montage render debug: {exc}")
        if not render.get("ok"):
            label = "Final" if final else "Draft"
            return {
                "ok": False,
                "error": render.get("error") or f"{label} montage render failed.",
                "render": render,
                "storyboard": render_storyboard,
                "debug_path": str(debug_path),
            }

        rendered_duration = round(sum(
            float(item.get("duration") or 0.0)
            for item in (render.get("segments") or [])
            if isinstance(item, dict)
        ), 3)
        planned_duration = rendered_duration or (render_storyboard.get("summary") or {}).get("planned_duration_seconds")
        burned_segments = sum(
            1
            for item in (render.get("segments") or [])
            if isinstance(item, dict) and item.get("subtitles_burned")
        )
        subtitle_segments = int(render_prep.get("subtitle_segments") or 0)
        subtitle_enabled = bool(render_prep.get("subtitle_enabled"))
        subtitle_status = "captions_disabled"
        if subtitle_enabled and subtitle_segments and burned_segments >= subtitle_segments:
            subtitle_status = "burned"
        elif subtitle_enabled and burned_segments:
            subtitle_status = "partially_burned"
        elif subtitle_enabled:
            subtitle_status = "not_burned_no_montage_words"
        moment = {
            "montage": True,
            "montage_storyboard_id": render_storyboard.get("storyboard_id"),
            "montage_render_type": render_type,
            "upload_ready": bool(final),
            "source_path": str(source_video),
            "source_stem": str(source.get("source_stem") or source_video.stem),
            "game_title": str(source.get("game_title") or ""),
            "start": 0,
            "end": planned_duration or 0,
            "duration": planned_duration or 0,
            "primary_category": "montage",
            "moment_categories": {
                "primary": "montage",
                "confidence": 1.0,
                "signals": {"storyboard_beats": len(render_storyboard.get("beats") or [])},
            },
            "subtitle_style": render_prep.get("subtitle_style"),
            "subtitle_enabled": subtitle_enabled,
            "subtitle_generated": bool(subtitle_segments),
            "subtitles_burned": bool(burned_segments),
            "captions_requested": subtitle_enabled,
            "subtitle_status": subtitle_status,
            "subtitle_word_count": int(render_prep.get("subtitle_word_count") or 0),
            "storyboard": {
                "storyboard_id": render_storyboard.get("storyboard_id"),
                "status": render_storyboard.get("status"),
                "beat_count": len(render_storyboard.get("beats") or []),
                "planned_duration_seconds": planned_duration,
                "subtitle_segments": subtitle_segments,
                "crop_segments": int(render_prep.get("crop_segments") or 0),
            },
        }
        moment = self._ensure_moment_identity(moment, output_path)
        self._results.append(output_path)
        self._moments.append(moment)
        metadata = {}
        if final:
            idx = len(self._results) - 1
            metadata = self._write_montage_metadata(idx, render_storyboard)
        self._save_state()
        clip = self._clip_payload(len(self._results) - 1, output_path, include_url=False)
        return {
            "ok": True,
            "render": render,
            "storyboard": render_storyboard,
            "path": str(output_path),
            "debug_path": str(debug_path),
            "clip": clip,
            "metadata": metadata,
        }

    def render_montage_draft(self, storyboard_path=None):
        """Render a storyboard as a draft hard-cut montage and add it to Results."""
        return self._render_montage_from_storyboard(storyboard_path, final=False)

    def render_montage_final(self, storyboard_path=None):
        """Render a storyboard as an upload-ready hard-cut montage with metadata."""
        return self._render_montage_from_storyboard(storyboard_path, final=True)

    def record_montage_feedback(self, feedback):
        """Record compact whole-montage or beat-level feedback in run learning."""
        if not isinstance(feedback, dict):
            return {"ok": False, "error": "Montage feedback payload must be an object."}
        feedback_type = str(feedback.get("feedback_type") or feedback.get("event_type") or feedback.get("type") or "").strip().lower()
        if feedback_type not in {"like", "dislike", "favorite"}:
            return {"ok": False, "error": "feedback_type must be like, dislike, or favorite."}

        storyboard_result = self.get_montage_storyboard(
            feedback.get("storyboard_path") or feedback.get("path") or None
        )
        if not storyboard_result.get("ok"):
            return {
                "ok": False,
                "error": storyboard_result.get("error") or "No montage storyboard is available.",
            }
        storyboard = storyboard_result.get("storyboard")
        if not isinstance(storyboard, dict):
            return {"ok": False, "error": "Storyboard JSON is invalid."}

        storyboard_id = str(storyboard.get("storyboard_id") or "").strip()
        requested_id = str(feedback.get("storyboard_id") or "").strip()
        if requested_id and requested_id != storyboard_id:
            return {"ok": False, "error": "Feedback storyboard_id does not match the storyboard file."}
        if not storyboard_id:
            return {"ok": False, "error": "Storyboard is missing storyboard_id."}

        beat_snapshot = None
        beat_id = str(feedback.get("beat_id") or "").strip()
        if beat_id:
            beats = storyboard.get("beats") if isinstance(storyboard.get("beats"), list) else []
            for beat in beats:
                if isinstance(beat, dict) and str(beat.get("beat_id") or "").strip() == beat_id:
                    beat_snapshot = beat
                    break
            if beat_snapshot is None:
                return {"ok": False, "error": "beat_id does not exist in the storyboard."}

        active = self._feedback_active_flag(feedback.get("active", True))
        reason = str(feedback.get("reason") or "").strip()[:1000]
        timestamp = self._utc_now_label()
        source = storyboard.get("source") if isinstance(storyboard.get("source"), dict) else {}
        source_ids = storyboard.get("source_ids") if isinstance(storyboard.get("source_ids"), list) else []
        event = build_montage_feedback_event(
            event_id=self._hash_id("mfb", timestamp, storyboard_id, beat_id, feedback_type, reason, length=18),
            feedback_type=feedback_type,
            active=active,
            timestamp=timestamp,
            storyboard_id=storyboard_id,
            source_id=str(source_ids[0]) if source_ids else "",
            source_stem=str(source.get("source_stem") or ""),
            reason=reason,
            storyboard_snapshot=storyboard,
            beat_snapshot=beat_snapshot,
        )
        try:
            self._record_run_learning_event(event)
        except Exception as exc:
            return {"ok": False, "error": f"Could not save montage feedback: {exc}"}

        return {
            "ok": True,
            "event": event,
            "storyboard_id": storyboard_id,
            "beat_id": beat_id,
            "run_learning": self.get_run_learning_summary(),
        }

    def _montage_storyboard_metadata_context(self, storyboard: dict) -> dict:
        """Build compact, beat-aware context for montage titles and descriptions."""
        beats = storyboard.get("beats") if isinstance(storyboard.get("beats"), list) else []
        lines: list[str] = []
        beat_context: list[dict] = []
        for index, beat in enumerate(beats, 1):
            if not isinstance(beat, dict):
                continue
            role = re.sub(r"[^a-z0-9_ -]", "", str(beat.get("role") or f"beat {index}").lower()).strip() or f"beat {index}"
            category = re.sub(r"[^a-z0-9_ -]", "", str(beat.get("category") or "gameplay").lower()).strip() or "gameplay"
            text = sanitize_creator_title_context(str(beat.get("hook_text") or ""), limit=220)
            line = f"{role}: {text}" if text else f"{role}: {category.replace('_', ' ')} moment"
            lines.append(line)
            beat_context.append(
                {
                    "role": role,
                    "category": category,
                    "text": text,
                    "source_start": beat.get("source_start"),
                    "source_end": beat.get("source_end"),
                    "context_only": bool(beat.get("context_only")),
                    "repetition_penalty": _safe_float_value(beat.get("repetition_penalty"), 0.0),
                }
            )
        transcript = "\n".join(lines).strip()
        if not transcript:
            transcript = "montage built from selected gameplay beats"
        creator_context = (
            "Montage storyboard beats: "
            + " | ".join(lines[:5])
        )
        return {
            "transcript": transcript,
            "beats": beat_context,
            "creator_context": sanitize_creator_title_context(creator_context, limit=420),
        }

    def _is_generic_montage_title(self, title: str, game_title: str = "") -> bool:
        clean = str(title or "").lower()
        game = str(game_title or "").lower()
        clean_no_tags = re.sub(r"#\w+", "", clean).strip()
        generic_bits = (
            "montage highlights",
            "gameplay montage",
            "short montage",
            "gaming moment",
            "gameplay highlights",
        )
        if any(bit in clean_no_tags for bit in generic_bits):
            return True
        if game and clean_no_tags in {game, f"{game} montage", f"{game} highlights"}:
            return True
        return not clean_no_tags

    def _fallback_montage_title(self, game_title: str, montage_context: dict) -> str:
        beats = montage_context.get("beats") if isinstance(montage_context.get("beats"), list) else []
        text_candidates = [
            str(beat.get("text") or "").strip()
            for beat in beats
            if isinstance(beat, dict) and str(beat.get("text") or "").strip()
        ]
        source = max(text_candidates, key=len) if text_candidates else str(montage_context.get("transcript") or "")
        source = re.sub(r"\b(oh my god|like|literally|actually|basically|i guess|you know)\b", " ", source, flags=re.IGNORECASE)
        source = re.sub(r"[^A-Za-z0-9' ]+", " ", source)
        words = [word for word in source.split() if len(word) > 1]
        title = " ".join(words[:7]).strip()
        if not title:
            title = f"{game_title or 'Gameplay'} Montage"
        elif len(title) < 18 and game_title:
            title = f"{title} in {game_title}"
        return format_short_title(title.title(), game_title)

    def _montage_quality_explanation(self, storyboard: dict) -> dict:
        beats = storyboard.get("beats") if isinstance(storyboard.get("beats"), list) else []
        categories: dict[str, int] = {}
        roles: list[str] = []
        context_only_count = 0
        repeated_count = 0
        for beat in beats:
            if not isinstance(beat, dict):
                continue
            category = str(beat.get("category") or "unknown")
            categories[category] = categories.get(category, 0) + 1
            role = str(beat.get("role") or "").strip()
            if role:
                roles.append(role)
            if beat.get("context_only"):
                context_only_count += 1
            if (_safe_float_value(beat.get("repetition_penalty"), 0.0) or 0.0) >= 0.10:
                repeated_count += 1
        summary = storyboard.get("summary") if isinstance(storyboard.get("summary"), dict) else {}
        warnings: list[str] = []
        if context_only_count:
            warnings.append(f"{context_only_count} context beat(s) were included to keep the story connected.")
        if repeated_count:
            warnings.append(f"{repeated_count} beat(s) contain repeated chatter and may need review.")
        if len(categories) <= 1 and len(beats) > 2:
            warnings.append("Most beats share one category, so pacing may feel less varied.")
        strengths = []
        if len(beats) >= 3:
            strengths.append("Has setup, escalation, and payoff structure.")
        if len(categories) > 1:
            strengths.append("Mixes more than one moment type.")
        return {
            "schema_version": 1,
            "beat_count": len(beats),
            "planned_duration_seconds": summary.get("planned_duration_seconds"),
            "roles": roles,
            "categories": categories,
            "strengths": strengths,
            "warnings": warnings,
        }

    def _write_montage_metadata(self, clip_index: int, storyboard: dict) -> dict:
        if clip_index < 0 or clip_index >= len(self._results):
            return {}
        source = storyboard.get("source") if isinstance(storyboard.get("source"), dict) else {}
        game_title = str(source.get("game_title") or "")
        beats = storyboard.get("beats") if isinstance(storyboard.get("beats"), list) else []
        montage_context = self._montage_storyboard_metadata_context(storyboard)
        transcript = montage_context["transcript"]
        quality_explanation = self._montage_quality_explanation(storyboard)
        clip_context = {
            "transcript": transcript,
            "primary_category": "montage",
            "moment_categories": {"primary": "montage", "confidence": 1.0},
            "quality_score": (storyboard.get("summary") or {}).get("beat_count"),
            "selection_quality_score": (storyboard.get("summary") or {}).get("planned_duration_seconds"),
            "creator_title_context": montage_context["creator_context"],
            "game_context": self._game_context_for_title(game_title, allow_network=False) if game_title else {},
            "feedback_learning_context": self._feedback_learning_prompt_context(),
            "montage_storyboard": montage_context["beats"],
            "montage_quality_explanation": quality_explanation,
        }
        title = generate_title(transcript, game_title=game_title, clip_context=clip_context)
        if self._is_generic_montage_title(title, game_title):
            title = self._fallback_montage_title(game_title, montage_context)
        generated_description = self._generated_description_for_clip(
            title,
            transcript,
            game_title,
            clip_context,
        )
        desc_parts = self._compose_clip_description(
            title,
            game_title,
            clip_context=clip_context,
            generated_text=generated_description,
        )
        tags = self._tags_for_game(game_title, transcript, clip_context=clip_context)
        metadata_file = self._write_metadata_sidecar(
            clip_index,
            title,
            game_title,
            desc_parts["description"],
            tags,
            clip_context,
        )
        self._store_generated_metadata(
            clip_index,
            title,
            desc_parts["description"],
            tags,
            game_title,
            metadata_file,
            clip_context,
            generated_description=desc_parts["generated_description"],
            custom_text=desc_parts["description_custom_text"],
            auto_hashtags=desc_parts["description_auto_hashtags"],
        )
        if 0 <= clip_index < len(self._moments) and isinstance(self._moments[clip_index], dict):
            self._moments[clip_index]["montage_quality_explanation"] = quality_explanation
            generated = self._moments[clip_index].get("generated_metadata")
            if isinstance(generated, dict):
                generated["montage_quality_explanation"] = quality_explanation
        return {
            "title": title,
            "game_title": game_title,
            "description": desc_parts["description"],
            "tags": tags,
            "metadata_file": metadata_file,
        }

    def _selected_items_from_candidate_debug(self, payload: dict) -> list[dict]:
        """Rebuild the selected-evaluation shape from a candidate debug report."""
        items: list[dict] = []
        for row in payload.get("candidates", []):
            if not isinstance(row, dict) or not row.get("selected"):
                continue
            moment = row.get("final") or row.get("selection") or {}
            candidate = row.get("candidate") or {}
            if not isinstance(moment, dict):
                moment = {}
            if not isinstance(candidate, dict):
                candidate = {}
            try:
                start = int(moment.get("start", row.get("start", candidate.get("start"))))
                end = int(moment.get("end", row.get("end", candidate.get("end"))))
            except (TypeError, ValueError):
                continue
            if end <= start:
                continue
            moment = json.loads(json.dumps(moment))
            candidate = json.loads(json.dumps(candidate))
            moment["start"] = start
            moment["end"] = end
            moment["duration"] = end - start
            moment.setdefault("ranker", row.get("ranker") if isinstance(row.get("ranker"), dict) else {})
            moment.setdefault("transcript", row.get("transcript", ""))
            candidate.setdefault("start", start)
            candidate.setdefault("end", end)
            candidate.setdefault("duration", end - start)
            item = {
                "candidate": candidate,
                "moment": moment,
                "words": [],
                "accepted": True,
                "word_count": row.get("word_count", 0),
                "quality_score": row.get("base_quality_score", row.get("quality_score")),
                "selection_rank_score": row.get("selection_rank_score"),
                "selection_score_source": row.get("selection_score_source", "quality_score"),
                "shadow_scoring": row.get("shadow_scoring") if isinstance(row.get("shadow_scoring"), dict) else {},
                "moment_category_scoring": row.get("moment_category_scoring") if isinstance(row.get("moment_category_scoring"), dict) else {},
                "ai_moment_scoring": row.get("ai_moment_scoring") if isinstance(row.get("ai_moment_scoring"), dict) else {},
                "multimodal_scoring": row.get("multimodal_scoring") if isinstance(row.get("multimodal_scoring"), dict) else {},
                "multi_signal_ai_scoring": row.get("multi_signal_ai_scoring") if isinstance(row.get("multi_signal_ai_scoring"), dict) else {},
                "voice_scoring": row.get("voice_scoring") if isinstance(row.get("voice_scoring"), dict) else {},
                "voice_profile": row.get("voice_profile") if isinstance(row.get("voice_profile"), dict) else None,
            }
            for key in (
                "primary_category",
                "moment_categories",
                "visual_diagnostics",
                "multimodal_analysis",
                "ai_moment_classification",
                "commentary_guard",
                "music_lyrics_guard",
                "music_lyrics_penalty",
                "learned_adjustment",
                "learned_quality_score",
                "ai_moment_quality_score",
                "ai_adjustment",
                "moment_category_quality_score",
                "moment_category_adjustment",
                "multimodal_quality_score",
                "multimodal_adjustment",
                "multi_signal_ai_quality_score",
                "multi_signal_ai_adjustment",
                "voice_profile_quality_score",
                "voice_adjustment",
            ):
                if row.get(key) is not None:
                    item[key] = row.get(key)
                    moment.setdefault(key, row.get(key))
            items.append(item)
        items.sort(key=lambda item: (int(item["moment"].get("start", 0)), int(item["moment"].get("end", 0))))
        return items

    @staticmethod
    def _merge_recovered_run_debug_payload(
        payload: dict,
        *,
        debug_path: Path,
        final_clip_debug: list[dict],
        run_warnings: list[str],
        stage_timings: dict,
        auto_metadata_count: int,
    ) -> dict:
        """Preserve candidate-debug rows while adding recovered render metadata."""
        recovered = dict(payload if isinstance(payload, dict) else {})
        candidates = recovered.get("candidates")
        if not isinstance(candidates, list):
            candidates = []
        selected_rows = [row for row in candidates if isinstance(row, dict) and row.get("selected")]
        selected_rows.sort(key=lambda row: (int(row.get("start") or 0), int(row.get("end") or 0)))

        for row, final_row in zip(selected_rows, final_clip_debug or []):
            row["final_render"] = final_row
            row["final_rendered"] = True
            if isinstance(final_row, dict):
                row["final_clip_path"] = final_row.get("path")
                if final_row.get("transcript") and not row.get("transcript"):
                    row["transcript"] = final_row.get("transcript")

        timing = recovered.get("timing") if isinstance(recovered.get("timing"), dict) else {}
        timing = dict(timing)
        timing["status"] = "recovered_from_candidate_debug"
        timing["rendered_clip_count"] = len(final_clip_debug or [])
        timing["auto_metadata_count"] = int(auto_metadata_count or 0)
        timing["stage_timings"] = dict(stage_timings or {})

        recovered["debug_stage"] = "run_post_render"
        recovered["final_render_metadata_included"] = True
        recovered["recovered_from_candidate_debug"] = True
        recovered["source_candidate_debug"] = str(debug_path)
        recovered["final_clips"] = final_clip_debug or []
        recovered["warnings"] = list(run_warnings or [])
        recovered["rendered_clip_count"] = len(final_clip_debug or [])
        recovered["auto_metadata_count"] = int(auto_metadata_count or 0)
        recovered["stage_timings"] = dict(stage_timings or {})
        recovered["timing"] = timing
        recovered["candidate_count"] = len(candidates)
        recovered["selected_count"] = len(selected_rows)
        recovered["candidates"] = candidates
        return recovered

    def _run_candidate_debug_recovery(self, debug_path: Path):
        """Render selected clips from a saved candidate debug report."""
        try:
            debug_path = self._safe_path_under(SUBTITLES_DIR, debug_path)
            if not debug_path or not debug_path.is_file():
                return self._error("Saved candidate analysis was not found")

            payload = json.loads(debug_path.read_text(encoding="utf-8"))
            selected = self._selected_items_from_candidate_debug(payload)
            if not selected:
                return self._error("Saved candidate analysis has no selected clips to render")

            video_path = Path(str(payload.get("video") or ""))
            if not video_path.exists() or not video_path.is_file():
                return self._error("Source video for the saved analysis is missing")

            settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
            audio_source_debug = settings.get("audio_source") if isinstance(settings.get("audio_source"), dict) else {}
            speech_stream = audio_source_debug.get("selected_stream")
            try:
                speech_stream = int(speech_stream) if speech_stream is not None else None
            except (TypeError, ValueError):
                speech_stream = None
            commentary_guard_policy = normalize_commentary_subtitle_policy(audio_source_debug.get("subtitle_policy"))
            allow_stream_retry = bool(audio_source_debug.get("alternate_stream_retry", True))
            manual_stream_locked = (
                str(audio_source_debug.get("mode") or "").strip().lower() == "stream"
                and speech_stream is not None
            )
            if manual_stream_locked:
                allow_stream_retry = False
            commentary_guard_enabled = bool(
                audio_source_debug.get(
                    "commentary_guard_enabled",
                    audio_source_debug.get("single_track_commentary_guard"),
                )
                and commentary_guard_policy == "creator"
            )

            source_id = self._source_id_for(video_path)
            source_stem = video_path.stem[:50]
            stem = debug_path.name.removesuffix("_candidate_debug.json")
            vid_duration = float(payload.get("video_duration") or 0)
            if vid_duration <= 0:
                try:
                    from subprocess_utils import run as _srun
                    _r = _srun(
                        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "csv=p=0", str(video_path)],
                        capture_output=True, text=True, timeout=10,
                    )
                    vid_duration = float(_r.stdout.strip())
                except Exception:
                    vid_duration = max(int(item["moment"].get("end", 0)) for item in selected)

            clip_duration = _normalize_clip_duration(settings.get("clip_duration", CLIP_DURATION))
            detection_preference = normalize_detection_preference(settings.get("detection_preference"))
            processing_depth = _normalize_processing_depth(settings.get("processing_depth"))
            quality_floor = float(settings.get("quality_floor", quality_floor_for_preference(detection_preference)))
            model = settings.get("whisper_model", WHISPER_MODEL)
            language = settings.get("whisper_language") or None
            style = _normalize_subtitle_style(settings.get("subtitle_style", SUBTITLE_STYLE))
            subtitle_enabled = subtitles_are_enabled(style)
            subtitle_placement = normalize_subtitle_placement(
                settings.get("subtitle_placement", SUBTITLE_PLACEMENT)
            )
            preset = settings.get("ffmpeg_preset", FFMPEG_PRESET)
            crf = str(settings.get("video_crf", VIDEO_CRF))
            crop_vertical = settings.get("crop_vertical", CROP_VERTICAL)
            with self._voice_profile_lock:
                voice_profile_snapshot = json.loads(json.dumps(self._voice_profile))
            recovery_voice_profile_status = voice_profile_status(
                voice_profile_snapshot,
                file_exists=VOICE_PROFILE_FILE.exists(),
                size_bytes=VOICE_PROFILE_FILE.stat().st_size if VOICE_PROFILE_FILE.exists() else 0,
            )
            recovery_voice_scoring_active = bool(
                _normalize_bool_setting(settings.get("voice_profile_ranking"), False)
                and recovery_voice_profile_status.get("can_score")
            )

            run_warnings = list(payload.get("warnings") or [])
            run_warnings.append("rendered_from_candidate_debug")
            source_game_identity = self._game_identity_for_source(video_path, allow_network=False)
            source_game_context = (
                source_game_identity.get("game_context")
                if isinstance(source_game_identity.get("game_context"), dict)
                else {}
            )
            source_game_title = (
                source_game_identity.get("title")
                or source_game_context.get("label")
                or self._infer_game_title_from_path(video_path)
            )
            if not source_game_context:
                source_game_context = self._game_context_for_title(source_game_title, allow_network=False)
            self._push("candidates", 100, f"Loaded {len(selected)} selected clips from saved analysis")

            moments = [item["moment"] for item in selected]
            for item, m in zip(selected, moments):
                m["source_id"] = source_id
                m["source_path"] = str(video_path)
                m["source_stem"] = source_stem
                m["game_title"] = source_game_context.get("label") or source_game_title
                m["game_identity"] = source_game_identity
                m["game_context"] = source_game_context
                m["clip_id"] = self._clip_id_for(m)
                moment_stream = speech_stream if manual_stream_locked else m.get("speech_stream", speech_stream)
                if manual_stream_locked:
                    m["speech_stream"] = moment_stream
                m["audio_source"] = {
                    "mode": audio_source_debug.get("mode", "auto"),
                    "selected_stream": moment_stream,
                    "selected_reason": audio_source_debug.get("selected_reason"),
                    "selected_confidence": audio_source_debug.get("selected_confidence"),
                    "runner_up_stream": audio_source_debug.get("runner_up_stream"),
                    "stream_count": audio_source_debug.get("stream_count"),
                    "render_audio": audio_source_debug.get("render_audio", "all_source_streams_mixed"),
                    "alternate_stream_retry": allow_stream_retry,
                    "subtitle_policy": commentary_guard_policy,
                    "commentary_guard_enabled": commentary_guard_enabled,
                    "single_track_commentary_guard": audio_source_debug.get(
                        "single_track_commentary_guard",
                        commentary_guard_enabled,
                    ),
                    "stream_selection": _audio_stream_selection_summary(
                        audio_source_debug,
                        selected_stream=moment_stream,
                    ),
                }
                m["stream_selection"] = m["audio_source"]["stream_selection"]
                m["subtitle_style"] = style
                m["captions_requested"] = bool(subtitle_enabled)
                m["subtitle_enabled"] = bool(subtitle_enabled)
                m["voice_profile"] = item.get("voice_profile") or m.get("voice_profile")
            self._js(f"window.onMomentsDetected({json.dumps(moments)})")

            done: list[Path] = []
            done_moments: list[dict] = []
            final_clip_debug: list[dict] = []
            total = len(selected)
            stage_timings: dict[str, float] = {}

            render_started = time.monotonic()
            for idx, item in enumerate(selected, 1):
                if self._cancel:
                    return self._cancelled()
                m = item["moment"]
                selection_primary_category, selection_moment_categories = _category_snapshot_from_selection(item, m)
                category_scoring = item.get("moment_category_scoring") or {}
                ranking_primary_category = category_scoring.get("primary_category") or selection_primary_category
                ranking_moment_categories = selection_moment_categories
                selected_start, selected_end = int(m["start"]), int(m["end"])
                start, end = selected_start, selected_end
                m["selection_primary_category"] = selection_primary_category
                if selection_moment_categories is not None:
                    m["selection_moment_categories"] = selection_moment_categories
                m["ranking_primary_category"] = ranking_primary_category
                if ranking_moment_categories is not None:
                    m["ranking_moment_categories"] = ranking_moment_categories
                if m.get("ai_moment_classification"):
                    m["selection_ai_moment_classification"] = copy.deepcopy(m["ai_moment_classification"])
                    m["ai_moment_classification_stage"] = "selection_pre_render"
                m["selected_start"] = selected_start
                m["selected_end"] = selected_end
                m["selected_duration"] = selected_end - selected_start
                m["render_start"] = start
                m["render_end"] = end
                m["render_duration"] = end - start
                out = self._unique_clip_output_path(stem, idx)
                wav = SUBTITLES_DIR / f"{stem}_c{idx}.wav"
                ass = SUBTITLES_DIR / f"{stem}_c{idx}.ass"
                for stale in (wav, ass):
                    try:
                        stale.unlink(missing_ok=True)
                    except Exception:
                        pass

                self._clip_push(idx, total, "audio", 40, f"Clip {idx}/{total}: Final transcription...")
                final_probe_end = min(end + 8, int(vid_duration))
                final_words = []
                final_stream = speech_stream if manual_stream_locked else m.get("speech_stream", speech_stream)
                final_retry_report = None
                if extract_audio_clip(video_path, start, final_probe_end, wav, audio_stream=final_stream):
                    final_words = transcribe_clip(wav, model_size=model, language=language)
                if allow_stream_retry and needs_stream_retry(
                    final_words,
                    final_probe_end - start,
                    subtitle_policy=commentary_guard_policy,
                    commentary_guard=commentary_guard_enabled,
                ):
                    retry_words, retry_stream = self._try_alternate_audio_streams(
                        video_path, start, final_probe_end, wav, model, language,
                        idx, total, final_stream, return_stream=True,
                        subtitle_policy=commentary_guard_policy,
                    )
                    final_retry_report = getattr(self, "_last_stream_retry", None)
                    if retry_words:
                        final_words = retry_words
                        final_stream = retry_stream
                final_voice_profile_score = (
                    self._voice_profile_score_for_wav(wav, voice_profile_snapshot)
                    if recovery_voice_scoring_active
                    else self._voice_profile_inactive_score(voice_profile_snapshot)
                )
                words = final_words
                if final_words:
                    final_stream_profile = _selected_audio_stream_profile(
                        audio_source_debug,
                        selected_stream=final_stream,
                        retry_report=final_retry_report,
                    )
                    final_candidate = {
                        **item["candidate"],
                        "start": start,
                        "end": end,
                        "duration": end - start,
                    }
                    final_eval = evaluate_candidate(
                        final_candidate,
                        final_words,
                        extraction_start=float(start),
                        extraction_end=float(final_probe_end),
                        video_duration=float(vid_duration),
                        target_duration=clip_duration,
                        selected_stream=final_stream,
                        quality_floor=quality_floor,
                        detection_preference=detection_preference,
                        commentary_guard=commentary_guard_enabled,
                        commentary_guard_policy=commentary_guard_policy,
                        voice_profile=final_voice_profile_score,
                        stream_profile=final_stream_profile,
                    )
                    refined_moment = final_eval.get("moment") or {}
                    try:
                        trim_start = int(refined_moment.get("start", start))
                        trim_end = int(refined_moment.get("end", end))
                    except (TypeError, ValueError):
                        trim_start, trim_end = start, end
                    filtered_words = final_eval.get("words")
                    words = (
                        _subtitle_words_for_render_start(filtered_words, trim_start, trim_start)
                        if isinstance(filtered_words, list)
                        else final_words
                    )
                    start, end = trim_start, trim_end
                    for key in (
                        "primary_category",
                        "moment_categories",
                        "commentary_guard",
                        "music_lyrics_guard",
                        "music_lyrics_penalty",
                        "visual_diagnostics",
                    ):
                        if refined_moment.get(key) is not None:
                            m[key] = refined_moment.get(key)
                    m["start"] = start
                    m["end"] = end
                    m["duration"] = end - start
                    m["render_start"] = start
                    m["render_end"] = end
                    m["render_duration"] = end - start
                    m["trim_adjusted_start"] = trim_start
                    m["trim_adjusted_end"] = trim_end
                    m["trim_adjusted_duration"] = max(0, trim_end - trim_start)
                    m["trim_adjusted_from_selected"] = (
                        trim_start != selected_start or trim_end != selected_end
                    )
                    m["subtitle_timing_offset"] = round(float(trim_start) - float(selected_start), 3)
                transcript = " ".join(str(w.get("text", "")).strip() for w in words if isinstance(w, dict)).strip()
                m["speech_stream"] = final_stream
                m["word_count"] = len(words)
                m["subtitle_word_count"] = len(words)
                m["transcript"] = transcript
                m["voice_profile"] = final_voice_profile_score
                if final_retry_report:
                    m["stream_retry"] = final_retry_report
                m["audio_source"] = {
                    "mode": audio_source_debug.get("mode", "auto"),
                    "selected_stream": final_stream,
                    "selected_reason": audio_source_debug.get("selected_reason"),
                    "selected_confidence": audio_source_debug.get("selected_confidence"),
                    "runner_up_stream": audio_source_debug.get("runner_up_stream"),
                    "stream_count": audio_source_debug.get("stream_count"),
                    "render_audio": audio_source_debug.get("render_audio", "all_source_streams_mixed"),
                    "alternate_stream_retry": allow_stream_retry,
                    "subtitle_policy": commentary_guard_policy,
                    "commentary_guard_enabled": commentary_guard_enabled,
                    "single_track_commentary_guard": audio_source_debug.get(
                        "single_track_commentary_guard",
                        commentary_guard_enabled,
                    ),
                    "stream_selection": _audio_stream_selection_summary(
                        audio_source_debug,
                        selected_stream=final_stream,
                        retry_report=final_retry_report,
                    ),
                }
                m["stream_selection"] = m["audio_source"]["stream_selection"]
                m["subtitle_enabled"] = bool(subtitle_enabled)
                m["captions_requested"] = bool(subtitle_enabled)
                m["speech_policy"] = _clip_speech_policy_summary(m)
                if _creator_caption_speech_missing(
                    m,
                    subtitle_enabled=bool(subtitle_enabled),
                    subtitle_policy=commentary_guard_policy,
                ):
                    m["final_render_rejected"] = True
                    m["final_render_reject_reason"] = "no_selected_commentary_transcript"
                    if m["speech_policy"].get("warning"):
                        m["metadata_warning"] = m["speech_policy"]["warning"]
                    m["metadata_needs_context"] = True
                    run_warnings.append(f"clip_{idx}_no_selected_commentary_transcript")
                    self._clip_push(
                        idx,
                        total,
                        "subtitle",
                        100,
                        f"Clip {idx}/{total}: Skipped, no commentary transcript",
                    )
                    for stale in (wav, ass, out):
                        try:
                            stale.unlink(missing_ok=True)
                        except Exception:
                            pass
                    continue

                crop_params = None
                crop_w, crop_h = get_dimensions(video_path)
                if crop_vertical:
                    self._clip_push(idx, total, "audio", 100, f"Clip {idx}/{total}: Tracking speakers...")
                    try:
                        crop_profile = _crop_tracking_profile(processing_depth)
                        crop_params = get_crop_params_dynamic(
                            video_path,
                            start,
                            end,
                            sample_count=int(crop_profile["sample_count"]),
                            min_sample_rate=float(crop_profile["min_sample_rate"]),
                        )
                    except Exception as e:
                        print(f"[!] Crop detection failed for recovered clip {idx}: {e}")
                        crop_params = None
                    if crop_params:
                        crop_w, crop_h = crop_params[0], crop_params[1]

                resolved_subtitle_placement = resolve_subtitle_placement(
                    crop_w, crop_h, subtitle_placement
                )
                if subtitle_enabled:
                    self._clip_push(idx, total, "subtitle", 0, f"Clip {idx}/{total}: Generating subtitles...")
                    ass_path = generate_subtitles(
                        words,
                        ass,
                        video_width=crop_w,
                        video_height=crop_h,
                        style=style,
                        subtitle_placement=subtitle_placement,
                    )
                else:
                    ass.unlink(missing_ok=True)
                    ass_path = None
                m["subtitle_generated"] = bool(ass_path)
                m["subtitle_placement"] = resolved_subtitle_placement
                m["processing_depth"] = processing_depth
                m["speech_policy"] = _clip_speech_policy_summary(m)
                if m["speech_policy"].get("warning"):
                    m["metadata_warning"] = m["speech_policy"]["warning"]
                m["metadata_needs_context"] = bool(m["speech_policy"].get("metadata_backfill_blocked"))
                self._clip_push(
                    idx, total, "subtitle", 100,
                    "Subtitles generated" if ass_path else ("Captions disabled" if not subtitle_enabled else "No subtitles generated"),
                )

                self._clip_push(idx, total, "render", 0, f"Clip {idx}/{total}: Rendering...")
                clip_result = extract_clip(
                    video_path,
                    start,
                    end,
                    out,
                    subtitle_path=ass_path if ass_path else None,
                    crop_params=crop_params,
                    preset=preset,
                    crf=crf,
                )
                if clip_result and clip_result.path:
                    m["subtitles_burned"] = bool(clip_result.subtitles_burned and ass_path)
                    m["source_id"] = source_id
                    m["source_path"] = str(video_path)
                    m["source_stem"] = source_stem
                    m["game_title"] = source_game_context.get("label") or source_game_title
                    m["game_identity"] = source_game_identity
                    m["game_context"] = source_game_context
                    m["clip_id"] = self._clip_id_for(m, clip_result.path)
                    if clip_result.warning:
                        m["render_warning"] = clip_result.warning
                        run_warnings.append(f"clip_{idx}_{clip_result.warning}")
                    m["speech_policy"] = _clip_speech_policy_summary(m)
                    if m["speech_policy"].get("warning"):
                        m["metadata_warning"] = m["speech_policy"]["warning"]
                    m["metadata_needs_context"] = bool(m["speech_policy"].get("metadata_backfill_blocked"))
                    m["truth_summary"] = self._clip_truth_summary(m, clip_result.path)
                    done.append(clip_result.path)
                    done_moments.append(m)
                    final_primary_category = m.get("primary_category") or item.get("primary_category")
                    final_moment_categories = m.get("moment_categories") or item.get("moment_categories")
                    final_clip_debug.append({
                        "index": idx,
                        "path": str(clip_result.path),
                        "clip_id": m.get("clip_id"),
                        "source_id": m.get("source_id"),
                        "source_path": m.get("source_path"),
                        "source_stem": m.get("source_stem"),
                        "subtitle_path": str(ass_path) if ass_path else None,
                        "start": start,
                        "end": end,
                        "duration": end - start,
                        "selected_start": m.get("selected_start", start),
                        "selected_end": m.get("selected_end", end),
                        "selected_duration": m.get("selected_duration", end - start),
                        "render_start": m.get("render_start", start),
                        "render_end": m.get("render_end", end),
                        "render_duration": m.get("render_duration", end - start),
                        "trim_adjusted_start": m.get("trim_adjusted_start"),
                        "trim_adjusted_end": m.get("trim_adjusted_end"),
                        "trim_adjusted_duration": m.get("trim_adjusted_duration"),
                        "trim_adjusted_from_selected": m.get("trim_adjusted_from_selected", False),
                        "subtitle_timing_offset": m.get("subtitle_timing_offset", 0.0),
                        "base_quality_score": item.get("quality_score"),
                        "selection_rank_score": item.get("selection_rank_score"),
                        "selection_score_source": item.get("selection_score_source", "quality_score"),
                        "shadow_scoring": item.get("shadow_scoring", {}),
                        "moment_category_scoring": item.get("moment_category_scoring") or m.get("moment_category_scoring"),
                        "moment_category_diversity_adjustment": (item.get("moment_category_scoring") or {}).get("category_diversity_adjustment"),
                        "voice_scoring": item.get("voice_scoring") or m.get("voice_scoring"),
                        "ai_moment_quality_score": (item.get("ai_moment_scoring") or {}).get("ai_moment_quality_score"),
                        "ai_ranking_enabled": (item.get("ai_moment_scoring") or {}).get("ranking_enabled"),
                        "ai_adjustment": (item.get("ai_moment_scoring") or {}).get("ai_adjustment"),
                        "ai_selection_delta": (item.get("ai_moment_scoring") or {}).get("selection_delta", ""),
                        "ai_rank_delta": (item.get("ai_moment_scoring") or {}).get("rank_delta"),
                        "ai_scoring_eligible": (item.get("ai_moment_scoring") or {}).get("ai_scoring_eligible"),
                        "ai_ineligible_reason": (item.get("ai_moment_scoring") or {}).get("ai_ineligible_reason"),
                        "ai_moment_scoring": item.get("ai_moment_scoring") or m.get("ai_moment_scoring"),
                        "multimodal_quality_score": (item.get("multimodal_scoring") or {}).get("multimodal_quality_score"),
                        "multimodal_ranking_enabled": (item.get("multimodal_scoring") or {}).get("ranking_enabled"),
                        "multimodal_adjustment": (item.get("multimodal_scoring") or {}).get("multimodal_adjustment"),
                        "multimodal_selection_delta": (item.get("multimodal_scoring") or {}).get("selection_delta", ""),
                        "multimodal_rank_delta": (item.get("multimodal_scoring") or {}).get("rank_delta"),
                        "multimodal_scoring_eligible": (item.get("multimodal_scoring") or {}).get("scoring_eligible"),
                        "multimodal_ineligible_reason": (item.get("multimodal_scoring") or {}).get("ineligible_reason"),
                        "multimodal_scoring": item.get("multimodal_scoring") or m.get("multimodal_scoring"),
                        "selection_primary_category": selection_primary_category,
                        "selection_moment_categories": selection_moment_categories,
                        "ranking_primary_category": ranking_primary_category,
                        "ranking_moment_categories": ranking_moment_categories,
                        "final_primary_category": final_primary_category,
                        "final_moment_categories": final_moment_categories,
                        "primary_category": final_primary_category,
                        "moment_categories": final_moment_categories,
                        "visual_diagnostics": m.get("visual_diagnostics") or item.get("visual_diagnostics"),
                        "multimodal_analysis": m.get("multimodal_analysis") or item.get("multimodal_analysis"),
                        "truth_summary": m.get("truth_summary"),
                        "selection_ai_moment_classification": m.get("selection_ai_moment_classification") or item.get("ai_moment_classification"),
                        "ai_moment_classification_stage": m.get("ai_moment_classification_stage"),
                        "ai_moment_classification": m.get("ai_moment_classification") or item.get("ai_moment_classification"),
                        "commentary_guard": m.get("commentary_guard") or item.get("commentary_guard"),
                        "music_lyrics_guard": m.get("music_lyrics_guard") or item.get("music_lyrics_guard"),
                        "music_lyrics_penalty": m.get("music_lyrics_penalty") if m.get("music_lyrics_penalty") is not None else item.get("music_lyrics_penalty"),
                        "word_count": m.get("word_count"),
                        "speech_stream": m.get("speech_stream"),
                        "audio_source": m.get("audio_source"),
                        "stream_selection": m.get("stream_selection"),
                        "stream_retry": m.get("stream_retry"),
                        "subtitle_style": m.get("subtitle_style"),
                        "captions_requested": m.get("captions_requested"),
                        "subtitle_enabled": m.get("subtitle_enabled"),
                        "subtitle_generated": m.get("subtitle_generated"),
                        "subtitles_burned": m.get("subtitles_burned"),
                        "subtitle_placement": m.get("subtitle_placement"),
                        "speech_policy": m.get("speech_policy"),
                        "metadata_warning": m.get("metadata_warning", ""),
                        "metadata_needs_context": m.get("metadata_needs_context", False),
                        "render_warning": m.get("render_warning", ""),
                        "transcript": m.get("transcript", ""),
                        "recovered_from_candidate_debug": True,
                    })
                    self._clip_push(idx, total, "render", 100, f"Clip {idx} complete!")
                else:
                    self._clip_push(idx, total, "render", 100, f"Clip {idx} failed")
                try:
                    wav.unlink(missing_ok=True)
                except Exception:
                    pass
            stage_timings["final_render"] = round(time.monotonic() - render_started, 3)

            run_debug_path = debug_path.with_name(debug_path.name.replace("_candidate_debug.json", "_run_debug.json"))
            first_new_clip_index = len(self._results)
            self._results.extend(done)
            self._moments.extend(done_moments)
            auto_metadata = self._generate_auto_metadata_for_results(
                first_new_clip_index,
                len(done),
                final_clip_debug,
                run_warnings,
            ) if done else []
            try:
                recovered = self._merge_recovered_run_debug_payload(
                    payload,
                    debug_path=debug_path,
                    final_clip_debug=final_clip_debug,
                    run_warnings=run_warnings,
                    stage_timings=stage_timings,
                    auto_metadata_count=len(auto_metadata),
                )
                self._write_json_atomic(run_debug_path, recovered)
                print(f"[rank] Recovered run debug saved: {run_debug_path}")
            except Exception as e:
                print(f"[rank] Failed to save recovered run debug: {e}")
                try:
                    recovered = self._merge_recovered_run_debug_payload(
                        payload,
                        debug_path=debug_path,
                        final_clip_debug=final_clip_debug,
                        run_warnings=run_warnings,
                        stage_timings=stage_timings,
                        auto_metadata_count=len(auto_metadata),
                    )
                    self._write_json_atomic(run_debug_path, recovered)
                except Exception as fallback_exc:
                    print(f"[rank] Failed to save recovered fallback debug: {fallback_exc}")
            self._save_state()
            self._js(f"window.onPipelineComplete(true, {len(done)}, {total}, null)")
        except CancelledError:
            return self._cancelled()
        except Exception as e:
            self._record_pipeline_error(e, {"phase": "candidate_debug_recovery", "debug_path": str(debug_path)})
            self._error(str(e))
        finally:
            self._processing = False

    # ── Pipeline orchestrator (background thread) ────────────────────────

    def _run_pipeline(self, url, settings):
        settings = dict(settings or {})
        pipeline_started_monotonic = time.monotonic()
        pipeline_started_at = self._utc_now_label()
        stage_timings: dict[str, float] = {}
        estimate_total_seconds = None
        estimate_source = "not_estimated"
        self._active_progress_context = self._progress_context_from_settings(settings)
        try:
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            run_warnings: list[str] = []
            generation_mode = _normalize_generation_mode(settings.get("generation_mode"))
            montage_settings = _normalize_montage_settings(settings.get("montage"))
            num_clips_raw = settings.get("num_clips", NUM_CLIPS)
            auto_clips = num_clips_raw == "auto"
            num_clips = NUM_CLIPS if auto_clips else int(num_clips_raw)
            processing_depth = _normalize_processing_depth(settings.get("processing_depth"))
            detection_preference = normalize_detection_preference(settings.get("detection_preference"))
            game_title_hint = self._sanitize_game_title_hint(settings.get("game_title_hint"))
            quality_floor = quality_floor_for_preference(detection_preference)
            print(
                f"[*] Pipeline settings: generation_mode={generation_mode}, "
                f"num_clips_raw={num_clips_raw!r}, "
                f"auto_clips={auto_clips}, num_clips={num_clips}, "
                f"processing_depth={processing_depth}, "
                f"detection_preference={detection_preference}, quality_floor={quality_floor:.2f}, "
                f"game_hint={'set' if game_title_hint else 'auto'}"
            )
            if generation_mode == "montage":
                montage_count = int(montage_settings.get("count") or 1)
                print(
                    "[montage] Montage intent received "
                    f"(template={montage_settings.get('template')}, "
                    f"target={montage_settings.get('target_duration')}s, "
                    f"count={montage_count}). "
                    "This run will build distinct storyboards from selected beats and render final hard-cut montages."
                )
            clip_duration = _normalize_clip_duration(settings.get("clip_duration", CLIP_DURATION))
            min_gap = _normalize_min_gap(settings.get("min_gap", MIN_GAP))
            style = _normalize_subtitle_style(settings.get("subtitle_style", SUBTITLE_STYLE))
            subtitle_enabled = subtitles_are_enabled(style)
            subtitle_placement = normalize_subtitle_placement(
                settings.get("subtitle_placement", SUBTITLE_PLACEMENT)
            )
            model = settings.get("whisper_model", WHISPER_MODEL)
            language = settings.get("whisper_language") or None
            preset = settings.get("ffmpeg_preset", FFMPEG_PRESET)
            crf = str(settings.get("video_crf", VIDEO_CRF))
            crop_vertical = settings.get("crop_vertical", CROP_VERTICAL)
            effect = settings.get("video_effect", "none")
            music_file = settings.get("music_file", None)
            music_volume = float(settings.get("music_volume", 0.12))
            music_start = float(settings.get("music_start", 0))
            music_end = float(settings.get("music_end", 0))
            audio_source = _normalize_audio_source_settings(settings)
            visual_diagnostics_enabled = _normalize_bool_setting(
                settings.get("visual_diagnostics"),
                True,
            )
            ai_moment_classification_enabled = _normalize_bool_setting(
                settings.get("ai_moment_classification"),
                False,
            )
            moment_category_ranking_enabled = _normalize_bool_setting(
                settings.get("moment_category_ranking"),
                False,
            )
            voice_profile_ranking_enabled = _normalize_bool_setting(
                settings.get("voice_profile_ranking"),
                False,
            )
            visual_diagnostics_requested = visual_diagnostics_enabled
            ai_moment_classification_requested = ai_moment_classification_enabled
            moment_category_ranking_requested = moment_category_ranking_enabled
            voice_profile_ranking_requested = voice_profile_ranking_enabled
            multimodal_analysis_requested = False

            # ── 1. Download ──────────────────────────────────────────
            if self._cancel:
                return self._cancelled()
            self._push("download", 0, "Downloading video...")

            download_started = time.monotonic()
            video_path = self._download_with_progress(url)
            stage_timings["download"] = round(time.monotonic() - download_started, 3)
            source_id = self._source_id_for(video_path)
            source_stem = video_path.stem[:50]
            game_context_started = time.monotonic()
            source_game_identity = self._game_identity_for_source(
                video_path,
                allow_network=True,
                explicit_title=game_title_hint,
            )
            source_game_context = (
                source_game_identity.get("game_context")
                if isinstance(source_game_identity.get("game_context"), dict)
                else {}
            )
            source_game_title = (
                source_game_identity.get("title")
                or source_game_context.get("label")
                or self._infer_game_title_from_path(video_path)
            )
            if not source_game_context:
                source_game_context = self._game_context_for_title(source_game_title, allow_network=True)
            self._remember_source_context(
                video_path,
                source_id=source_id,
                game_title_hint=game_title_hint,
                game_identity=source_game_identity,
                game_context=source_game_context,
                youtube_context=self._youtube_context_for_source(video_path),
                force=bool(game_title_hint),
            )
            stage_timings["game_context"] = round(time.monotonic() - game_context_started, 3)
            source_game_context_prompt = compact_game_context_for_prompt(source_game_context)
            if source_game_context.get("status") in {"ok", "cache_hit"}:
                print(
                    "[game-context] "
                    f"{source_game_context.get('label') or source_game_title or 'Unknown'} "
                    f"({source_game_context.get('qid') or 'no qid'}) via "
                    f"{source_game_identity.get('matched_via') or source_game_context.get('status')}"
                )
            elif source_game_title:
                run_warnings.append(f"game_context_{source_game_context.get('status', 'unknown')}")
                print(
                    "[game-context] "
                    f"{source_game_title}: {source_game_context.get('status', 'unknown')}"
                )
            self._active_progress_context = self._progress_context_from_settings(
                settings,
                source_name=video_path.name,
            )
            source_audio_streams = [_public_audio_stream(s) for s in get_audio_streams(video_path)]
            source_audio_ordinals = {int(s["ordinal"]) for s in source_audio_streams}
            requested_stream = audio_source.get("stream")
            try:
                requested_stream_ordinal = int(requested_stream)
            except (TypeError, ValueError):
                requested_stream_ordinal = None
            forced_speech_stream = None
            if audio_source["mode"] == "stream":
                if requested_stream_ordinal in source_audio_ordinals:
                    forced_speech_stream = int(requested_stream_ordinal)
                else:
                    run_warnings.append("audio_source_stream_unavailable")
                    print(
                        "[audio] Requested transcription stream "
                        f"0:a:{requested_stream} is unavailable; falling back to auto"
                    )
            allow_stream_retry = forced_speech_stream is None
            manual_stream_locked = forced_speech_stream is not None
            commentary_guard_policy = normalize_commentary_subtitle_policy(
                audio_source.get("subtitle_policy", "creator")
            )
            commentary_guard_enabled = bool(
                audio_source.get("commentary_guard")
                and commentary_guard_policy == "creator"
            )
            audio_source_debug = {
                "mode": audio_source["mode"],
                "requested_stream": requested_stream,
                "selected_stream": forced_speech_stream,
                "stream_count": len(source_audio_streams),
                "streams": source_audio_streams,
                "render_audio": "all_source_streams_mixed",
                "alternate_stream_retry": allow_stream_retry,
                "subtitle_policy": commentary_guard_policy,
                "commentary_guard_enabled": commentary_guard_enabled,
                "single_track_commentary_guard": bool(
                    audio_source.get("commentary_guard") and len(source_audio_streams) == 1
                ),
            }
            with self._voice_profile_lock:
                voice_profile_snapshot = json.loads(json.dumps(self._voice_profile))
            voice_profile_debug = voice_profile_status(
                voice_profile_snapshot,
                file_exists=VOICE_PROFILE_FILE.exists(),
                size_bytes=VOICE_PROFILE_FILE.stat().st_size if VOICE_PROFILE_FILE.exists() else 0,
            )
            voice_profile_debug["ranking_enabled"] = voice_profile_ranking_enabled
            voice_profile_debug["ranking_active"] = bool(
                voice_profile_ranking_enabled and voice_profile_debug.get("can_score")
            )
            voice_profile_debug["selection_impact"] = (
                "capped_rank_adjustment" if voice_profile_debug["ranking_active"] else "none"
            )
            voice_profile_debug["ranking_cap"] = round(VOICE_PROFILE_SELECTION_MAX_ADJUSTMENT, 4)
            voice_profile_debug["ranking_cap_label"] = f"+/-{VOICE_PROFILE_SELECTION_MAX_ADJUSTMENT:.3f}"

            self._push("download", 100, f"Downloaded: {video_path.name}")

            # ── Get video duration (needed for auto clip count + sentence snapping) ──
            try:
                from subprocess_utils import run as _srun
                _r = _srun(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", str(video_path)],
                    capture_output=True, text=True, timeout=10,
                )
                vid_duration = float(_r.stdout.strip())
            except Exception:
                vid_duration = 600  # default 10 min

            # ── Auto clip count ──────────────────────────────────────
            if auto_clips:
                vid_w, vid_h = get_dimensions(video_path)
                # Smart auto: scale clips based on video length
                #   < 5 min  → 2-3 clips
                #   5-15 min → 3-5 clips
                #   15-30 min → 5-8 clips
                #   30-60 min → 8-15 clips
                #   1-2 hrs  → 15-25 clips
                #   2+ hrs   → 25-40 clips
                # Formula: roughly 1 clip per 3-4 minutes, with a minimum of 2
                vid_mins = vid_duration / 60
                if vid_mins < 5:
                    num_clips = max(2, min(3, int(vid_mins / 1.5)))
                elif vid_mins < 15:
                    num_clips = max(3, int(vid_mins / 3))
                elif vid_mins < 30:
                    num_clips = max(5, int(vid_mins / 3.5))
                elif vid_mins < 60:
                    num_clips = max(8, int(vid_mins / 3.5))
                elif vid_mins < 120:
                    num_clips = max(15, min(30, int(vid_mins / 4)))
                else:
                    num_clips = max(25, min(50, int(vid_mins / 4)))
                # Also consider clip duration — shorter clips = can fit more
                if clip_duration < 20:
                    num_clips = int(num_clips * 1.3)
                elif clip_duration > 60:
                    num_clips = max(2, int(num_clips * 0.7))
                if detection_preference == "quality":
                    num_clips = max(2, int(num_clips * 0.72))
                elif detection_preference == "quantity":
                    num_clips = int(num_clips * 1.25) + 1
                num_clips = max(2, min(50, num_clips))
                pref_label = detection_preference.title()
                self._push("detect", 0, f"Auto/{pref_label}: {num_clips} clips for {int(vid_mins)}min video")
                print(f"[+] Auto clip count: {num_clips} ({pref_label}; video is {vid_duration:.0f}s / {vid_mins:.1f}min)")

            depth_profile = _processing_depth_profile(
                processing_depth,
                detection_preference,
                vid_duration,
            )
            candidate_multiplier = int(depth_profile["candidate_multiplier"])
            candidate_pool_cap = int(depth_profile.get("candidate_pool_cap") or 0)
            scene_mode = str(depth_profile["scene_mode"])
            candidate_model = _candidate_model_for_depth(processing_depth, model)
            estimate_fingerprint = {
                "generation_mode": generation_mode,
                "montage_template": montage_settings.get("template"),
                "montage_target_duration": montage_settings.get("target_duration"),
                "candidate_whisper_model": candidate_model,
                "scene_mode": scene_mode,
                "candidate_multiplier": candidate_multiplier,
                "candidate_pool_cap": candidate_pool_cap,
                "visual_analysis": bool(
                    visual_diagnostics_enabled
                    if depth_profile["visual_diagnostics"] is None
                    else depth_profile["visual_diagnostics"]
                ),
                "multimodal_analysis": bool(depth_profile.get("multimodal_analysis")),
                "ai_moment_labels": bool(
                    ai_moment_classification_enabled
                    if depth_profile["ai_moment_classification"] is None
                    else depth_profile["ai_moment_classification"]
                ),
                "moment_label_ranking": bool(
                    moment_category_ranking_enabled
                    if depth_profile["moment_category_ranking"] is None
                    else depth_profile["moment_category_ranking"]
                ),
                "voice_profile_ranking": bool(
                    voice_profile_ranking_enabled
                    if depth_profile["voice_profile_ranking"] is None
                    else depth_profile["voice_profile_ranking"]
                ),
                "game_context": bool(source_game_context_prompt.get("available")),
                "subtitle_style": style,
            }
            estimate_total_seconds, estimate_source = self._estimate_processing_seconds_from_history(
                processing_depth,
                vid_duration,
                settings_fingerprint=estimate_fingerprint,
            )
            eta_stage_plan = self._estimate_processing_stage_plan_from_history(
                processing_depth,
                vid_duration,
                settings_fingerprint=estimate_fingerprint,
            )
            if eta_stage_plan and eta_stage_plan.get("estimatedTotalSeconds"):
                estimate_total_seconds = float(eta_stage_plan["estimatedTotalSeconds"])
                estimate_source = str(eta_stage_plan.get("source") or estimate_source)
            if estimate_total_seconds:
                print(
                    "[timing] Local estimate for this run: "
                    f"{estimate_total_seconds:.0f}s ({estimate_source})"
                )
                progress_context = {
                    **(self._active_progress_context or {}),
                    "estimatedTotalSeconds": round(float(estimate_total_seconds), 3),
                    "estimateSource": estimate_source,
                    "estimateStartedAt": time.time(),
                }
                if eta_stage_plan:
                    progress_context.update(
                        {
                            "etaStagePlan": eta_stage_plan.get("stages") or {},
                            "etaStageRates": eta_stage_plan.get("stageRates") or {},
                            "etaStageSampleCount": int(eta_stage_plan.get("sampleCount") or 0),
                            "etaStageConfidence": str(eta_stage_plan.get("confidence") or "low"),
                        }
                    )
                self._active_progress_context = progress_context
            if depth_profile["visual_diagnostics"] is not None:
                visual_diagnostics_enabled = bool(depth_profile["visual_diagnostics"])
            if depth_profile["ai_moment_classification"] is not None:
                ai_moment_classification_enabled = bool(depth_profile["ai_moment_classification"])
            multimodal_analysis_enabled = bool(depth_profile.get("multimodal_analysis"))
            multimodal_analysis_requested = multimodal_analysis_enabled
            if depth_profile["moment_category_ranking"] is not None:
                moment_category_ranking_enabled = bool(depth_profile["moment_category_ranking"])
            if depth_profile["voice_profile_ranking"] is not None:
                voice_profile_ranking_enabled = bool(depth_profile["voice_profile_ranking"])
            voice_profile_debug["ranking_enabled"] = voice_profile_ranking_enabled
            voice_profile_debug["ranking_active"] = bool(
                voice_profile_ranking_enabled and voice_profile_debug.get("can_score")
            )
            voice_profile_debug["selection_impact"] = (
                "capped_rank_adjustment" if voice_profile_debug["ranking_active"] else "none"
            )
            local_analysis_feature_statuses = _depth_feature_statuses(
                processing_depth,
                depth_profile,
                visual_requested=visual_diagnostics_requested,
                ai_requested=ai_moment_classification_requested,
                category_requested=moment_category_ranking_requested,
                voice_requested=voice_profile_ranking_requested,
                multimodal_requested=multimodal_analysis_requested,
                voice_status=voice_profile_debug,
            )
            print(
                "[*] Processing depth: "
                f"{processing_depth} (candidates x{candidate_multiplier}, "
                f"cap={candidate_pool_cap}, scene={scene_mode}, "
                f"candidate_whisper={candidate_model})"
            )

            # ── 2. Detect viral moments ──────────────────────────────
            if self._cancel:
                return self._cancelled()
            self._push("detect", 0, "Analyzing video for viral moments...")

            def _scene_progress(detail: str):
                if self._cancel:
                    return
                self._push("detect", 18, "Analyzing video for viral moments...", detail=detail)

            detect_started = time.monotonic()
            candidates = find_viral_moments(
                video_path,
                num_clips=num_clips,
                clip_duration=clip_duration,
                min_gap=min_gap,
                candidate_multiplier=candidate_multiplier,
                scene_mode=scene_mode,
                max_candidates=candidate_pool_cap,
                progress_callback=_scene_progress,
            )
            stage_timings["detect"] = round(time.monotonic() - detect_started, 3)
            scene_detection = get_last_scene_detection_diagnostics()
            stage_timings["scene_detection"] = float(scene_detection.get("elapsed_seconds") or 0.0)
            scene_status = scene_detection.get("status", "unknown")
            if scene_status not in {"ok", "zero_changes", "sampled_ok", "sampled_zero_changes", "targeted_ok", "targeted_zero_changes", "skipped"}:
                run_warnings.append(f"scene_detection_{scene_status}")

            if not candidates:
                self._push("detect", 100, "No moments found")
                return self._error("No viral moments found. Try a longer video or fewer clips.")

            self._push("detect", 55, f"Found {len(candidates)} candidate moments")

            if self._cancel:
                return self._cancelled()
            if visual_diagnostics_enabled:
                self._push("detect", 57, "Sampling visual clues...")
                try:
                    candidate_visuals, visual_diagnostics_report = analyze_candidate_visuals(
                        video_path,
                        candidates,
                        video_duration=float(vid_duration),
                        max_candidates=int(depth_profile.get("visual_max_candidates") or 48),
                    )
                except Exception as e:
                    print(f"[visual] Candidate visual diagnostics failed: {e}")
                    candidate_visuals, visual_diagnostics_report = disabled_visual_diagnostics(
                        candidates,
                        status="analysis_failed",
                    )
                    visual_diagnostics_report["warnings"].append(str(e)[:180])
            else:
                candidate_visuals, visual_diagnostics_report = disabled_visual_diagnostics(candidates)
            for candidate, visual in zip(candidates, candidate_visuals):
                candidate["visual_diagnostics"] = visual
            visual_status = visual_diagnostics_report.get("status", "unknown")
            stage_timings["visual_analysis"] = float(visual_diagnostics_report.get("elapsed_seconds") or 0.0)
            if visual_status not in {"ok", "disabled", "no_candidates"}:
                run_warnings.append(f"visual_diagnostics_{visual_status}")
            print(
                f"[visual] Candidate diagnostics: {visual_status}, "
                f"{visual_diagnostics_report.get('sampled_candidate_count', 0)}/"
                f"{visual_diagnostics_report.get('candidate_count', len(candidates))} sampled"
            )

            # Pick the real commentary/speech track by sampling the selected
            # moments. OBS track labels are often misleading.
            self._push("detect", 60, "Inspecting audio tracks...")
            speech_stream = None
            stream_probe_samples = 2 if processing_depth == "fast" else (4 if processing_depth == "balanced" else 6)
            stream_probe_seconds = 12 if processing_depth == "fast" else (16 if processing_depth == "balanced" else 20)
            if forced_speech_stream is not None:
                speech_stream = forced_speech_stream
                manual_selected_title = next(
                    (
                        stream.get("title")
                        for stream in source_audio_streams
                        if int(stream.get("ordinal", -1)) == speech_stream
                    ),
                    None,
                )
                audio_source_debug["stream_selection"] = {
                    "schema_version": 1,
                    "status": "forced",
                    "mode": "manual_stream",
                    "selection_impact": "user_selected_stream",
                    "selected_stream": speech_stream,
                    "selected_title": manual_selected_title,
                    "selected_reason": "user_selected_stream",
                    "runner_up_stream": None,
                    "confidence": 1.0,
                    "stream_profiles": [],
                }
                self._push("detect", 62, f"Using selected transcription track 0:a:{speech_stream}")
                print(
                    "[audio] User-selected transcription stream locked: "
                    f"0:a:{speech_stream} ({manual_selected_title or 'unknown title'})"
                )
            else:
                try:
                    speech_stream = select_speech_stream(
                        video_path,
                        candidates,
                        candidate_model,
                        language,
                        SUBTITLES_DIR,
                        max_samples=stream_probe_samples,
                        sample_seconds=stream_probe_seconds,
                    )
                    audio_source_debug["stream_selection"] = get_last_speech_stream_selection()
                except Exception as e:
                    print(f"[audio] Speech stream scan failed: {e}")
                    speech_stream = pick_voice_stream_ordinal(video_path)
                    audio_source_debug["stream_selection"] = {
                        "schema_version": 1,
                        "status": "error",
                        "mode": "diagnostic_v2",
                        "selection_impact": "fallback_stream_selection",
                        "selected_stream": speech_stream,
                        "selected_reason": "stream_scan_error_fallback",
                        "error": str(e)[:240],
                        "runner_up_stream": None,
                        "confidence": 0.2 if speech_stream is not None else 0.0,
                        "stream_profiles": [],
                    }
            audio_source_debug["selected_stream"] = speech_stream
            stream_selection = audio_source_debug.get("stream_selection") or {}
            audio_source_debug["selected_reason"] = stream_selection.get("selected_reason")
            audio_source_debug["selected_confidence"] = stream_selection.get("confidence")
            audio_source_debug["runner_up_stream"] = stream_selection.get("runner_up_stream")

            # ── 3. Transcript-rank candidates before rendering ────────
            stem = source_stem
            evaluations: list[dict] = []
            probe_buffer = 14
            detected_candidate_total = len(candidates)
            analysis_candidates = _shortlist_candidates_for_transcription(
                candidates,
                depth=processing_depth,
                detection_preference=detection_preference,
                num_clips=num_clips,
                min_gap=min_gap,
            )
            candidate_total = len(analysis_candidates)
            if candidate_total < detected_candidate_total:
                run_warnings.append("candidate_transcription_shortlisted")
                print(
                    "[perf] Candidate transcription shortlist: "
                    f"{candidate_total}/{detected_candidate_total} candidates"
                )
            self._push("candidates", 0, f"Analyzing {candidate_total} candidate moments...")

            candidate_analysis_started = time.monotonic()
            candidate_rows: list[dict] = []
            extracted_wavs: list[Path] = []
            candidate_probe_durations: dict[str, float] = {}
            for idx, candidate in enumerate(analysis_candidates, 1):
                if self._cancel:
                    return self._cancelled()
                start = int(candidate["start"])
                candidate_end = int(candidate["end"])
                extended_end = min(
                    candidate_end + probe_buffer,
                    start + CANDIDATE_TRANSCRIPT_PROBE_MAX_SECONDS,
                    int(vid_duration),
                )
                wav = SUBTITLES_DIR / f"{stem}_probe_{idx}.wav"
                wav.unlink(missing_ok=True)

                pct = int((idx - 1) / max(candidate_total, 1) * 100)
                self._push("candidates", pct, f"Extracting candidate audio {idx}/{candidate_total}...")

                extracted = extract_audio_clip(video_path, start, extended_end, wav, audio_stream=speech_stream)
                row = {
                    "idx": idx,
                    "candidate": candidate,
                    "start": start,
                    "extended_end": extended_end,
                    "wav": wav,
                    "pct": pct,
                    "extracted": bool(extracted),
                    "probe_duration": max(0, extended_end - start),
                }
                candidate_rows.append(row)
                if extracted:
                    extracted_wavs.append(wav)
                    candidate_probe_durations[str(wav)] = float(row["probe_duration"])

            batch_words_by_path: dict[str, list] = {}
            if extracted_wavs:
                transcription_chunks = _candidate_transcription_chunks(
                    extracted_wavs,
                    candidate_probe_durations,
                )
                if len(transcription_chunks) > 1:
                    run_warnings.append("candidate_transcription_chunked")
                    print(
                        "[perf] Candidate transcription chunked: "
                        f"{len(extracted_wavs)} probes across {len(transcription_chunks)} batches"
                    )

                def _candidate_transcription_progress(chunk_index, total_chunks, chunk, chunk_seconds):
                    pct = 35 + int(((chunk_index - 1) / total_chunks) * 25)
                    self._push(
                        "candidates",
                        pct,
                        (
                            f"Transcribing candidate batch {chunk_index}/{total_chunks} "
                            f"({len(chunk)} clips, {int(round(chunk_seconds))}s audio)..."
                        ),
                    )

                try:
                    batch_words_by_path, _ = _transcribe_candidate_wav_chunks(
                        extracted_wavs,
                        candidate_probe_durations,
                        model_size=candidate_model,
                        language=language,
                        cancel_check=lambda: bool(self._cancel),
                        progress_callback=_candidate_transcription_progress,
                    )
                except CancelledError:
                    return self._cancelled()

            voice_scoring_active = bool(voice_profile_debug.get("ranking_active"))
            for row in candidate_rows:
                if self._cancel:
                    return self._cancelled()
                idx = int(row["idx"])
                candidate = row["candidate"]
                start = int(row["start"])
                extended_end = int(row["extended_end"])
                wav = row["wav"]
                pct = int(row["pct"])
                self._push("candidates", pct, f"Scoring candidate {idx}/{candidate_total} before rendering...")

                words = list(batch_words_by_path.get(str(wav), []))
                used_stream = speech_stream
                retry_report = None

                if allow_stream_retry and needs_stream_retry(
                    words,
                    extended_end - start,
                    subtitle_policy=commentary_guard_policy,
                    commentary_guard=commentary_guard_enabled,
                ):
                    words, alt_stream = self._try_alternate_audio_streams(
                        video_path, start, extended_end, wav, candidate_model, language,
                        idx, candidate_total, speech_stream, return_stream=True,
                        progress_stage="candidates", progress_percent=pct,
                        subtitle_policy=commentary_guard_policy,
                    )
                    retry_report = getattr(self, "_last_stream_retry", None)
                    if alt_stream is not None:
                        used_stream = alt_stream
                if voice_scoring_active:
                    voice_profile_score = self._voice_profile_score_for_wav(wav, voice_profile_snapshot)
                else:
                    voice_profile_score = self._voice_profile_inactive_score(voice_profile_snapshot)

                evaluation = evaluate_candidate(
                    candidate,
                    words,
                    extraction_start=float(start),
                    extraction_end=float(extended_end),
                    video_duration=float(vid_duration),
                    target_duration=clip_duration,
                    selected_stream=used_stream,
                    quality_floor=quality_floor,
                    detection_preference=detection_preference,
                    commentary_guard=commentary_guard_enabled,
                    commentary_guard_policy=commentary_guard_policy,
                    voice_profile=voice_profile_score,
                    stream_profile=_selected_audio_stream_profile(
                        audio_source_debug,
                        selected_stream=used_stream,
                        retry_report=retry_report,
                    ),
                )
                if retry_report:
                    evaluation["stream_retry"] = retry_report
                    evaluation["moment"]["stream_retry"] = retry_report
                evaluation["game_context"] = source_game_context
                if isinstance(evaluation.get("candidate"), dict):
                    evaluation["candidate"]["game_context"] = source_game_context
                evaluation["moment"]["game_context"] = source_game_context
                evaluation["moment"]["game_title"] = source_game_context.get("label") or source_game_title
                evaluation["moment"]["game_title_hint"] = game_title_hint
                evaluation["voice_profile"] = voice_profile_score
                evaluation["moment"]["voice_profile"] = voice_profile_score
                evaluations.append(evaluation)
                try:
                    wav.unlink(missing_ok=True)
                except Exception:
                    pass
            stage_timings["candidate_analysis"] = round(time.monotonic() - candidate_analysis_started, 3)

            with self._personalization_lock:
                personalization_snapshot = json.loads(json.dumps(self._personalization))
            with getattr(self, "_run_learning_lock", threading.RLock()):
                run_learning_snapshot = json.loads(json.dumps(getattr(self, "_run_learning", empty_run_learning())))
            apply_learned_scoring(
                evaluations,
                personalization_snapshot,
                run_learning=run_learning_snapshot,
                source_id=source_id,
                source_stem=source_stem,
            )
            learned_selected = select_best_candidates(
                evaluations,
                num_clips,
                min_gap=min_gap,
                score_key="learned_quality_score",
            )
            shadow_scoring = build_shadow_scoring_report(
                evaluations,
                learned_selected,
                personalization_snapshot,
                run_learning=run_learning_snapshot,
                source_id=source_id,
                source_stem=source_stem,
                max_count=num_clips,
                min_gap=min_gap,
            )
            category_scoring = apply_moment_category_scoring(
                evaluations,
                enabled=moment_category_ranking_enabled,
                score_key="learned_quality_score",
                max_count=num_clips,
                min_gap=min_gap,
            )
            category_score_key = (
                "moment_category_quality_score"
                if category_scoring.get("ranking_enabled") and category_scoring.get("has_category_scores")
                else "learned_quality_score"
            )
            category_selected = learned_selected
            if category_score_key == "moment_category_quality_score":
                category_selected = select_best_candidates(
                    evaluations,
                    num_clips,
                    min_gap=min_gap,
                    score_key=category_score_key,
                )
            moment_category_ranking = build_moment_category_ranking_report(
                evaluations,
                learned_selected,
                category_selected,
                enabled=moment_category_ranking_enabled,
                max_count=num_clips,
                min_gap=min_gap,
                score_key="learned_quality_score",
                category_score_key="moment_category_quality_score",
            )
            ai_shadow_enabled = bool(processing_depth == "deep" and ai_moment_classification_enabled)
            ai_shadow_max_count = min(
                16,
                max(8, int(num_clips or 0) * 2),
                len(evaluations),
            )
            ai_moment_classification_shadow, ai_shadow_cache = self._classify_ai_moment_shadow(
                evaluations,
                category_selected,
                video_path,
                enabled=ai_shadow_enabled,
                score_key=category_score_key,
                max_count=ai_shadow_max_count,
                max_ollama=min(8, ai_shadow_max_count),
                attach_to_evaluations=True,
                game_context=source_game_context,
            )
            ai_scoring = apply_ai_moment_scoring(
                evaluations,
                enabled=ai_shadow_enabled,
                score_key=category_score_key,
            )
            ai_score_key = (
                "ai_moment_quality_score"
                if ai_scoring.get("ranking_enabled") and ai_scoring.get("has_ai_scores")
                else category_score_key
            )
            ai_selected = category_selected
            if ai_score_key == "ai_moment_quality_score":
                ai_selected = select_best_candidates(
                    evaluations,
                    num_clips,
                    min_gap=min_gap,
                    score_key=ai_score_key,
                )
            ai_moment_ranking = build_ai_moment_ranking_report(
                evaluations,
                category_selected,
                ai_selected,
                enabled=ai_shadow_enabled,
                max_count=num_clips,
                min_gap=min_gap,
                score_key=category_score_key,
                ai_score_key="ai_moment_quality_score",
            )
            multimodal_max_count = int(depth_profile.get("multimodal_max_candidates") or 0)
            if processing_depth == "deep" and multimodal_analysis_enabled:
                selected_count_for_vision = len(ai_selected or [])
                if selected_count_for_vision:
                    multimodal_max_count = max(
                        multimodal_max_count,
                        min(
                            len(evaluations),
                            selected_count_for_vision + min(6, max(2, math.ceil(selected_count_for_vision * 0.18))),
                        ),
                    )
                    multimodal_max_count = min(multimodal_max_count, 48)
            multimodal_analysis_report = self._analyze_multimodal_candidate_shortlist(
                evaluations,
                ai_selected,
                video_path,
                enabled=bool(processing_depth == "deep" and multimodal_analysis_enabled),
                score_key=ai_score_key,
                video_duration=float(vid_duration),
                max_count=multimodal_max_count,
                game_context=source_game_context,
            )
            multimodal_scoring = apply_multimodal_scoring(
                evaluations,
                enabled=bool(processing_depth == "deep" and multimodal_analysis_enabled),
                score_key=ai_score_key,
            )
            multimodal_score_key = (
                "multimodal_quality_score"
                if multimodal_scoring.get("ranking_enabled") and multimodal_scoring.get("has_multimodal_scores")
                else ai_score_key
            )
            multimodal_selected = ai_selected
            if multimodal_score_key == "multimodal_quality_score":
                multimodal_selected = select_best_candidates(
                    evaluations,
                    num_clips,
                    min_gap=min_gap,
                    score_key=multimodal_score_key,
                )
            multimodal_ranking = build_multimodal_ranking_report(
                evaluations,
                ai_selected,
                multimodal_selected,
                enabled=bool(processing_depth == "deep" and multimodal_analysis_enabled),
                max_count=num_clips,
                min_gap=min_gap,
                score_key=ai_score_key,
                multimodal_score_key="multimodal_quality_score",
            )
            voice_scoring = apply_voice_profile_scoring(
                evaluations,
                voice_profile_debug,
                score_key=multimodal_score_key,
            )
            selection_score_key = (
                "voice_profile_quality_score"
                if voice_scoring.get("ranking_enabled") and voice_scoring.get("has_voice_profile_scores")
                else multimodal_score_key
            )
            selected = multimodal_selected
            if selection_score_key == "voice_profile_quality_score":
                selected = select_best_candidates(
                    evaluations,
                    num_clips,
                    min_gap=min_gap,
                    score_key=selection_score_key,
                )
            voice_profile_ranking = build_voice_profile_ranking_report(
                evaluations,
                multimodal_selected,
                selected,
                voice_profile_debug,
                max_count=num_clips,
                min_gap=min_gap,
                score_key=multimodal_score_key,
                voice_score_key="voice_profile_quality_score",
            )
            voice_profile_shadow = build_voice_profile_shadow_report(
                evaluations,
                multimodal_selected,
                max_count=num_clips,
                min_gap=min_gap,
                score_key=multimodal_score_key,
            )
            multi_signal_baseline_score_key = selection_score_key
            multi_signal_ai_scoring = apply_multi_signal_ai_scoring(
                evaluations,
                enabled=bool(processing_depth == "deep"),
                score_key=multi_signal_baseline_score_key,
            )
            multi_signal_ai_score_key = (
                "multi_signal_ai_quality_score"
                if multi_signal_ai_scoring.get("ranking_enabled")
                and multi_signal_ai_scoring.get("has_multi_signal_scores")
                else selection_score_key
            )
            multi_signal_baseline_selected = selected
            if multi_signal_ai_score_key == "multi_signal_ai_quality_score":
                selected = select_best_candidates(
                    evaluations,
                    num_clips,
                    min_gap=min_gap,
                    score_key=multi_signal_ai_score_key,
                )
                selection_score_key = multi_signal_ai_score_key
            multi_signal_ai_ranking = build_multi_signal_ai_ranking_report(
                evaluations,
                multi_signal_baseline_selected,
                selected,
                enabled=bool(processing_depth == "deep"),
                max_count=num_clips,
                min_gap=min_gap,
                baseline_score_key=multi_signal_baseline_score_key,
                multi_signal_score_key="multi_signal_ai_quality_score",
            )
            near_quality_fallback = {
                "schema_version": 1,
                "applied": False,
                "reason": "",
                "selected_count": 0,
                "added_count": 0,
                "target_count": num_clips,
                "selection_mode": "strict",
                "score_key": selection_score_key,
            }
            should_fill_partial = bool(
                selected
                and processing_depth == "deep"
                and detection_preference != "quality"
                and len(selected) < num_clips
            )
            if (not selected or should_fill_partial) and evaluations:
                fallback_reason = (
                    "deep_auto_best_available_fill"
                    if should_fill_partial
                    else "strict_quality_selected_zero"
                )
                selected_before_fallback = len(selected)
                fallback_selected = select_near_quality_fallback_candidates(
                    evaluations,
                    num_clips,
                    min_gap=min_gap,
                    score_key=selection_score_key,
                    existing_selected=selected,
                    allow_partial=should_fill_partial,
                    reason=fallback_reason,
                    subtitle_policy=commentary_guard_policy,
                )
                if fallback_selected:
                    selected = fallback_selected
                    added_count = max(0, len(selected) - selected_before_fallback)
                    near_quality_fallback.update(
                        {
                            "applied": True,
                            "reason": fallback_reason,
                            "selected_count": len(selected),
                            "added_count": added_count,
                            "selection_mode": "partial_fill" if should_fill_partial else "zero_clip_rescue",
                            "score_key": selection_score_key,
                        }
                    )
                    run_warnings.append(
                        "deep_best_available_fill_used"
                        if should_fill_partial
                        else "near_quality_fallback_used"
                    )
                    if should_fill_partial:
                        print(
                            "[rank] Deep Analysis under-filled target; "
                            f"added {added_count} best-available candidate(s) "
                            f"for {len(selected)}/{num_clips} clips."
                        )
                    else:
                        print(
                            "[rank] Strict quality selected zero clips; "
                            f"using {len(selected)} near-quality fallback candidate(s)."
                        )
            remaining_ai_ollama = min(8, len(selected))
            ai_moment_classification_report = self._classify_selected_moments(
                selected,
                video_path,
                enabled=ai_moment_classification_enabled,
                max_ollama=remaining_ai_ollama,
                classification_cache=ai_shadow_cache,
                game_context=source_game_context,
            )
            multimodal_status = multimodal_analysis_report.get("status", "unknown")
            stage_timings["multimodal_analysis"] = float(multimodal_analysis_report.get("elapsed_seconds") or 0.0)
            if multimodal_status not in {"ok", "disabled", "no_candidates", "no_shortlist_candidates"}:
                run_warnings.append(f"multimodal_analysis_{multimodal_status}")
            scene_feature_status = local_analysis_feature_statuses.get("scene_detection", {})
            visual_feature_status = local_analysis_feature_statuses.get("visual_analysis", {})
            multimodal_feature_status = local_analysis_feature_statuses.get("vision_context", {})
            ai_feature_status = local_analysis_feature_statuses.get("ai_moment_labels", {})
            category_feature_status = local_analysis_feature_statuses.get("moment_label_ranking", {})
            voice_feature_status = local_analysis_feature_statuses.get("voice_profile_ranking", {})
            if scene_feature_status.get("inactive_reason"):
                scene_detection["skip_reason"] = scene_feature_status.get("inactive_reason")
                scene_detection["reason"] = scene_feature_status.get("reason")
            if visual_feature_status.get("inactive_reason"):
                visual_diagnostics_report["disabled_reason"] = visual_feature_status.get("inactive_reason")
                visual_diagnostics_report["reason"] = visual_feature_status.get("reason")
            if multimodal_feature_status.get("inactive_reason"):
                multimodal_analysis_report["disabled_reason"] = multimodal_feature_status.get("inactive_reason")
                multimodal_analysis_report["reason"] = multimodal_feature_status.get("reason")
                multimodal_ranking["disabled_reason"] = multimodal_feature_status.get("inactive_reason")
                multimodal_ranking["reason"] = multimodal_feature_status.get("reason")
            if ai_feature_status.get("inactive_reason"):
                ai_moment_classification_report["disabled_reason"] = ai_feature_status.get("inactive_reason")
                ai_moment_classification_report["reason"] = ai_feature_status.get("reason")
                ai_moment_classification_shadow["disabled_reason"] = ai_feature_status.get("inactive_reason")
                ai_moment_classification_shadow["reason"] = ai_feature_status.get("reason")
                ai_moment_ranking["disabled_reason"] = ai_feature_status.get("inactive_reason")
                ai_moment_ranking["reason"] = ai_feature_status.get("reason")
            if category_feature_status.get("inactive_reason"):
                moment_category_ranking["disabled_reason"] = category_feature_status.get("inactive_reason")
                moment_category_ranking["reason"] = category_feature_status.get("reason")
            if voice_feature_status.get("inactive_reason"):
                voice_profile_ranking["disabled_reason"] = voice_feature_status.get("inactive_reason")
                voice_profile_ranking["reason"] = voice_feature_status.get("reason")
                voice_profile_shadow["disabled_reason"] = voice_feature_status.get("inactive_reason")
                voice_profile_shadow["reason"] = voice_feature_status.get("reason")
            debug_path = SUBTITLES_DIR / f"{stem}_candidate_debug.json"
            run_debug_path = SUBTITLES_DIR / f"{stem}_run_debug.json"
            debug_settings = {
                "generation_mode": generation_mode,
                "montage": montage_settings,
                "generation": {
                    "mode": generation_mode,
                    "montage": montage_settings,
                    "montage_renderer_ready": generation_mode == "montage",
                    "selection_impact": (
                        "storyboard_and_final_montage_render"
                        if generation_mode == "montage"
                        else "normal_clip_render"
                    ),
                },
                "num_clips": num_clips,
                "detection_preference": detection_preference,
                "quality_floor": quality_floor,
                "processing_depth": processing_depth,
                "processing_depth_profile": depth_profile,
                "local_analysis_feature_statuses": local_analysis_feature_statuses,
                "game_title_hint": game_title_hint,
                "game_identity": {
                    "enabled": True,
                    "status": source_game_identity.get("status"),
                    "title": source_game_identity.get("title"),
                    "qid": source_game_identity.get("qid"),
                    "confidence": source_game_identity.get("confidence"),
                    "matched_via": source_game_identity.get("matched_via"),
                    "evidence": source_game_identity.get("evidence", [])[:8],
                    "candidates": source_game_identity.get("candidates", [])[:8],
                    "selection_impact": source_game_identity.get("selection_impact", "game_context_lookup"),
                },
                "game_context": {
                    "enabled": True,
                    "status": source_game_context.get("status"),
                    "prompt_context": source_game_context_prompt,
                    "selection_impact": "ai_label_vision_heuristic_context_and_capped_deep_ranking_nudge",
                    "game_context_nudge_max_adjustment": round(GAME_CONTEXT_SELECTION_MAX_ADJUSTMENT, 4),
                },
                "source_context": self._source_record_for(video_path, source_id),
                "clip_duration": clip_duration,
                "min_gap": min_gap,
                "whisper_model": model,
                "candidate_whisper_model": candidate_model,
                "whisper_language": language,
                "subtitle_style": style,
                "subtitle_placement": subtitle_placement,
                "ffmpeg_preset": preset,
                "video_crf": crf,
                "crop_vertical": crop_vertical,
                "candidate_multiplier": candidate_multiplier,
                "candidate_pool_cap": candidate_pool_cap,
                "audio_source": audio_source_debug,
                "visual_diagnostics": {
                    "enabled": visual_diagnostics_enabled,
                    "max_candidates": visual_diagnostics_report.get("max_candidates"),
                    "status": visual_feature_status,
                },
                "multimodal_analysis": {
                    "enabled": multimodal_analysis_enabled,
                    "model": multimodal_analysis_report.get("model"),
                    "feature_status": multimodal_feature_status,
                    "selection_impact": multimodal_ranking.get("selection_impact", "none"),
                    "max_adjustment": round(MULTIMODAL_SELECTION_MAX_ADJUSTMENT, 4),
                    "max_shortlist_candidates": multimodal_analysis_report.get("max_shortlist_candidates"),
                },
                "moment_category_ranking": {
                    "enabled": moment_category_ranking_enabled,
                    "selection_impact": moment_category_ranking.get("selection_impact", "none"),
                    "max_adjustment": round(MOMENT_CATEGORY_SELECTION_MAX_ADJUSTMENT, 4),
                    "status": category_feature_status,
                },
                "ai_moment_classification": {
                    "enabled": ai_moment_classification_enabled,
                    "model": DEFAULT_MODEL,
                    "max_ollama_candidates": ai_moment_classification_report.get("max_ollama_candidates"),
                    "selection_impact": "none",
                    "status": ai_feature_status,
                },
                "ai_moment_classification_shadow": {
                    "enabled": ai_shadow_enabled,
                    "model": DEFAULT_MODEL,
                    "max_shortlist_candidates": ai_moment_classification_shadow.get("max_shortlist_candidates"),
                    "max_ollama_candidates": ai_moment_classification_shadow.get("max_ollama_candidates"),
                    "selection_impact": "none",
                },
                "ai_moment_ranking": {
                    "enabled": ai_shadow_enabled,
                    "selection_impact": ai_moment_ranking.get("selection_impact", "none"),
                    "max_adjustment": round(AI_MOMENT_SELECTION_MAX_ADJUSTMENT, 4),
                    "status": ai_feature_status,
                },
                "voice_profile": voice_profile_debug,
                "voice_profile_ranking": {
                    "enabled": voice_profile_ranking_enabled,
                    "status": voice_feature_status,
                },
                "multi_signal_ai_ranking": {
                    "enabled": bool(processing_depth == "deep"),
                    "selection_impact": multi_signal_ai_ranking.get("selection_impact", "none"),
                    "selection_score_source": selection_score_key,
                    "max_positive_adjustment": round(MULTI_SIGNAL_AI_MAX_POSITIVE_ADJUSTMENT, 4),
                    "max_negative_adjustment": round(MULTI_SIGNAL_AI_MAX_NEGATIVE_ADJUSTMENT, 4),
                    "game_context_nudge_max_adjustment": round(GAME_CONTEXT_SELECTION_MAX_ADJUSTMENT, 4),
                    "has_game_context_scores": bool(multi_signal_ai_ranking.get("has_game_context_scores")),
                    "game_context_scored_candidate_count": int(multi_signal_ai_ranking.get("game_context_scored_candidate_count") or 0),
                    "status": {
                        "ranking_enabled": bool(multi_signal_ai_ranking.get("ranking_enabled")),
                        "has_multi_signal_scores": bool(multi_signal_ai_ranking.get("has_multi_signal_scores")),
                    },
                },
                "near_quality_fallback": near_quality_fallback,
            }

            def _timing_payload(status: str, rendered_clip_count: int = 0) -> dict:
                elapsed = round(time.monotonic() - pipeline_started_monotonic, 3)
                payload = {
                    "schema_version": PROCESSING_HISTORY_SCHEMA_VERSION,
                    "run_id": run_id,
                    "started_at_utc": pipeline_started_at,
                    "finished_at_utc": self._utc_now_label(),
                    "status": status,
                    "elapsed_seconds": elapsed,
                    "estimated_total_seconds": estimate_total_seconds,
                    "estimate_source": estimate_source,
                    "estimate_error_seconds": (
                        round(elapsed - float(estimate_total_seconds), 3)
                        if estimate_total_seconds is not None
                        else None
                    ),
                    "estimate_error_ratio": (
                        round(elapsed / max(float(estimate_total_seconds), 1.0), 4)
                        if estimate_total_seconds is not None
                        else None
                    ),
                    "video_duration_seconds": round(float(vid_duration or 0.0), 3),
                    "processing_depth": processing_depth,
                    "detection_preference": detection_preference,
                    "candidate_multiplier": candidate_multiplier,
                    "candidate_pool_cap": candidate_pool_cap,
                    "candidate_count": len(candidates),
                    "selected_count": len(selected),
                    "rendered_clip_count": int(rendered_clip_count or 0),
                    "settings_fingerprint": {
                        "generation_mode": generation_mode,
                        "montage_template": montage_settings.get("template"),
                        "montage_target_duration": montage_settings.get("target_duration"),
                        "candidate_whisper_model": candidate_model,
                        "scene_mode": scene_mode,
                        "candidate_multiplier": candidate_multiplier,
                        "candidate_pool_cap": candidate_pool_cap,
                        "visual_analysis": bool(visual_diagnostics_enabled),
                        "multimodal_analysis": bool(multimodal_analysis_enabled),
                        "ai_moment_labels": bool(ai_moment_classification_enabled),
                        "ai_moment_ranking": bool(ai_moment_ranking.get("ranking_enabled")),
                        "multimodal_ranking": bool(multimodal_ranking.get("ranking_enabled")),
                        "multi_signal_ai_ranking": bool(multi_signal_ai_ranking.get("ranking_enabled")),
                        "moment_label_ranking": bool(moment_category_ranking_enabled),
                        "voice_profile_ranking": bool(voice_profile_ranking_enabled),
                        "game_identity_qid": source_game_identity.get("qid") or "",
                        "game_identity_confidence": source_game_identity.get("confidence") or 0.0,
                        "game_context": bool(source_game_context_prompt.get("available")),
                        "subtitle_style": style,
                    },
                    "stage_timings": dict(stage_timings),
                }
                return payload

            for row in selected:
                if not isinstance(row, dict):
                    continue
                row.setdefault("source_id", source_id)
                row.setdefault("source_path", str(video_path))
                row.setdefault("source_stem", source_stem)
                row.setdefault("game_title", source_game_context.get("label") or source_game_title)
                row.setdefault("game_identity", source_game_identity)
                row.setdefault("game_context", source_game_context)
                if game_title_hint and not row.get("game_title_hint"):
                    row["game_title_hint"] = game_title_hint
                row["truth_summary"] = self._clip_truth_summary(row, video_path)

            try:
                write_debug_report(
                    debug_path,
                    video_path,
                    candidates,
                    evaluations,
                    selected,
                    scene_detection=scene_detection,
                    settings=debug_settings,
                    video_duration=vid_duration,
                    warnings=run_warnings,
                    shadow_scoring=shadow_scoring,
                    voice_profile_shadow=voice_profile_shadow,
                    voice_profile_ranking=voice_profile_ranking,
                    moment_category_ranking=moment_category_ranking,
                    ai_moment_ranking=ai_moment_ranking,
                    multimodal_ranking=multimodal_ranking,
                    multi_signal_ai_ranking=multi_signal_ai_ranking,
                    visual_diagnostics=visual_diagnostics_report,
                    multimodal_analysis=multimodal_analysis_report,
                    ai_moment_classification=ai_moment_classification_report,
                    ai_moment_classification_shadow=ai_moment_classification_shadow,
                    timing=_timing_payload("candidate_pre_render"),
                    run_id=run_id,
                )
                print(f"[rank] Candidate debug saved: {debug_path}")
                self._log_shadow_scoring(shadow_scoring)
                self._log_voice_profile_ranking(voice_profile_ranking)
                self._log_voice_profile_shadow(voice_profile_shadow)
            except Exception as e:
                print(f"[rank] Failed to save candidate debug: {e}")

            if not selected:
                self._push("detect", 100, "No high-quality clips found")
                no_quality_timing = _timing_payload("no_quality_clips", rendered_clip_count=0)
                try:
                    no_quality_timing["history_summary_after_run"] = self._record_processing_history(no_quality_timing)
                except Exception as e:
                    print(f"[timing] Failed to record no-quality processing history: {e}")
                try:
                    self._record_run_learning_summary(
                        self._build_run_learning_summary(
                            timing=no_quality_timing,
                            video_path=video_path,
                            source_id=source_id,
                            source_stem=source_stem,
                            game_title=source_game_context.get("label") or source_game_title,
                            settings=debug_settings,
                            candidates=candidates,
                            evaluations=evaluations,
                            selected=[],
                            final_clips=[],
                            debug_path=run_debug_path,
                            status="no_quality_clips",
                        )
                    )
                except Exception as e:
                    print(f"[learning] Failed to record no-quality run summary: {e}")
                try:
                    write_debug_report(
                        run_debug_path,
                        video_path,
                        candidates,
                        evaluations,
                        [],
                        scene_detection=scene_detection,
                        settings=debug_settings,
                        video_duration=vid_duration,
                        final_clips=[],
                        warnings=run_warnings,
                        shadow_scoring=shadow_scoring,
                        voice_profile_shadow=voice_profile_shadow,
                        voice_profile_ranking=voice_profile_ranking,
                        moment_category_ranking=moment_category_ranking,
                        ai_moment_ranking=ai_moment_ranking,
                        multimodal_ranking=multimodal_ranking,
                        multi_signal_ai_ranking=multi_signal_ai_ranking,
                        visual_diagnostics=visual_diagnostics_report,
                        multimodal_analysis=multimodal_analysis_report,
                        ai_moment_classification=ai_moment_classification_report,
                        ai_moment_classification_shadow=ai_moment_classification_shadow,
                        timing=no_quality_timing,
                        run_id=run_id,
                        debug_stage="run_no_quality_clips",
                    )
                    print(f"[rank] No-quality run debug saved: {run_debug_path}")
                except Exception as e:
                    print(f"[rank] Failed to save no-quality run debug: {e}")
                details = {
                    "completion_state": "no_quality_clips",
                    "message": "No clips met the quality bar.",
                    "guidance": "Try Auto or Quantity, a longer source, a shorter minimum gap, or Deep Analysis.",
                    "candidate_count": len(candidates),
                    "accepted_candidate_count": sum(1 for item in evaluations if item.get("accepted")),
                    "selected_count": 0,
                    "quality_floor": quality_floor,
                    "detection_preference": detection_preference,
                    "processing_depth": processing_depth,
                    "debug_path": str(run_debug_path),
                }
                self._save_state()
                self._js(f"window.onPipelineComplete(true, 0, 0, null, {json.dumps(details)})")
                return

            moments = [item["moment"] for item in selected]
            for item, m in zip(selected, moments):
                m["source_id"] = source_id
                m["source_path"] = str(video_path)
                m["source_stem"] = source_stem
                m["game_title"] = source_game_context.get("label") or source_game_title
                m["game_title_hint"] = game_title_hint
                m["game_identity"] = source_game_identity
                m["game_context"] = source_game_context
                m["clip_id"] = self._clip_id_for(m)
                moment_stream = speech_stream if manual_stream_locked else m.get("speech_stream", speech_stream)
                if manual_stream_locked:
                    m["speech_stream"] = moment_stream
                m["audio_source"] = {
                    "mode": audio_source_debug.get("mode"),
                    "selected_stream": moment_stream,
                    "selected_reason": audio_source_debug.get("selected_reason"),
                    "selected_confidence": audio_source_debug.get("selected_confidence"),
                    "runner_up_stream": audio_source_debug.get("runner_up_stream"),
                    "stream_count": audio_source_debug.get("stream_count"),
                    "render_audio": audio_source_debug.get("render_audio"),
                    "alternate_stream_retry": audio_source_debug.get("alternate_stream_retry"),
                    "subtitle_policy": audio_source_debug.get("subtitle_policy"),
                    "commentary_guard_enabled": audio_source_debug.get("commentary_guard_enabled"),
                    "single_track_commentary_guard": audio_source_debug.get("single_track_commentary_guard"),
                    "stream_selection": _audio_stream_selection_summary(
                        audio_source_debug,
                        selected_stream=moment_stream,
                    ),
                }
                m["stream_selection"] = m["audio_source"]["stream_selection"]
                m["subtitle_style"] = style
                m["captions_requested"] = bool(subtitle_enabled)
                m["subtitle_enabled"] = bool(subtitle_enabled)
                m["voice_profile"] = item.get("voice_profile") or m.get("voice_profile")
            self._push("candidates", 100, f"Selected {len(moments)} good clips from {len(candidates)} candidates")
            self._js(f"window.onMomentsDetected({json.dumps(moments)})")

            if generation_mode == "montage":
                montage_started = time.monotonic()
                montage_target = int(montage_settings.get("target_duration") or 60)
                montage_requested_count = int(montage_settings.get("count") or 1)
                montage_template = _normalize_montage_template(montage_settings.get("template"))
                montage_story_shape = _montage_story_shape_for_template(montage_template)
                self._push("render", 5, "Building montage storyboard...")
                try:
                    candidate_payload = json.loads(debug_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    raise RuntimeError(f"Could not read candidate debug for montage render: {exc}") from exc

                audit = build_candidate_audit(
                    candidate_payload,
                    target_beats=3,
                    target_duration_seconds=montage_target,
                )
                safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._") or "latest"
                audit_path = MONTAGES_DIR / f"{safe_stem}_montage_audit.json"
                write_candidate_audit(audit_path, audit)

                render_options = {
                    "processing_depth": processing_depth,
                    "subtitle_style": style,
                    "subtitle_enabled": subtitle_enabled,
                    "subtitle_placement": subtitle_placement,
                    "crop_vertical": crop_vertical,
                    "whisper_model": model,
                    "whisper_language": language,
                    "speech_stream": speech_stream,
                    "allow_stream_retry": allow_stream_retry,
                    "commentary_guard_policy": commentary_guard_policy,
                    "commentary_guard_enabled": commentary_guard_enabled,
                    "audio_source_debug": audio_source_debug,
                }
                used_montage_beat_ids: set[str] = set()
                montage_results: list[dict] = []
                storyboard_records: list[dict] = []
                final_clip_debug: list[dict] = []
                last_storyboard = None
                last_storyboard_path = None
                for montage_index in range(1, montage_requested_count + 1):
                    self._push(
                        "render",
                        max(8, min(90, 8 + int((montage_index - 1) / max(1, montage_requested_count) * 72))),
                        f"Building montage {montage_index}/{montage_requested_count} storyboard...",
                    )
                    storyboard = build_storyboard_from_audit(
                        audit,
                        target_duration_seconds=montage_target,
                        story_shape=montage_story_shape,
                        memory_enabled=True,
                        render_quality="final",
                        excluded_beat_ids=used_montage_beat_ids,
                        storyboard_index=montage_index,
                    )
                    storyboard_path = MONTAGES_DIR / (
                        f"{safe_stem}_montage_storyboard.json"
                        if montage_requested_count == 1
                        else f"{safe_stem}_montage_storyboard{montage_index}.json"
                    )
                    write_storyboard(storyboard_path, storyboard)
                    last_storyboard = storyboard
                    last_storyboard_path = storyboard_path
                    beat_count = len(storyboard.get("beats") or [])
                    if storyboard.get("status") == "no_storyboard" or beat_count <= 0:
                        if montage_index == 1:
                            run_warnings.append("montage_no_usable_beats")
                        else:
                            run_warnings.append("montage_requested_more_than_available")
                        break

                    self._push(
                        "render",
                        max(20, min(95, 20 + int((montage_index - 1) / max(1, montage_requested_count) * 70))),
                        f"Rendering montage {montage_index}/{montage_requested_count} from {beat_count} beats...",
                    )
                    montage_result = self._render_montage_storyboard_payload(
                        storyboard,
                        storyboard_path=str(storyboard_path),
                        final=True,
                        render_options=render_options,
                    )
                    if not montage_result.get("ok"):
                        if montage_results:
                            run_warnings.append("montage_partial_render_failure")
                            print(f"[montage] Stopping after partial render failure: {montage_result.get('error')}")
                            break
                        raise RuntimeError(montage_result.get("error") or "Montage render failed.")

                    montage_results.append(montage_result)
                    for beat in storyboard.get("beats") or []:
                        if isinstance(beat, dict) and beat.get("beat_id"):
                            used_montage_beat_ids.add(str(beat.get("beat_id")))
                    clip_payload = montage_result.get("clip") if isinstance(montage_result.get("clip"), dict) else {}
                    render_payload = montage_result.get("render") if isinstance(montage_result.get("render"), dict) else {}
                    storyboard_records.append(
                        {
                            "index": montage_index,
                            "path": montage_result.get("path"),
                            "storyboard_path": str(storyboard_path),
                            "storyboard_id": storyboard.get("storyboard_id"),
                            "render_debug_path": montage_result.get("debug_path"),
                            "beat_count": beat_count,
                            "planned_duration_seconds": (storyboard.get("summary") or {}).get("planned_duration_seconds"),
                        }
                    )
                    final_clip_debug.append(
                        {
                            "index": montage_index,
                            "path": montage_result.get("path"),
                            "clip_id": clip_payload.get("clip_id"),
                            "source_id": source_id,
                            "source_path": str(video_path),
                            "source_stem": source_stem,
                            "game_title": source_game_context.get("label") or source_game_title,
                            "montage": True,
                            "montage_template": montage_template,
                            "montage_target_duration": montage_target,
                            "montage_requested_count": montage_requested_count,
                            "montage_story_shape": montage_story_shape,
                            "montage_storyboard_id": storyboard.get("storyboard_id"),
                            "montage_storyboard_path": str(storyboard_path),
                            "montage_audit_path": str(audit_path),
                            "montage_render_debug_path": montage_result.get("debug_path"),
                            "montage_render_type": render_payload.get("render_type"),
                            "beat_count": beat_count,
                            "planned_duration_seconds": (storyboard.get("summary") or {}).get("planned_duration_seconds"),
                            "size_bytes": render_payload.get("size_bytes"),
                            "metadata": montage_result.get("metadata"),
                        }
                    )

                stage_timings["montage_render"] = round(time.monotonic() - montage_started, 3)
                if len(montage_results) < montage_requested_count:
                    run_warnings.append(f"montage_requested_{montage_requested_count}_created_{len(montage_results)}")
                if not montage_results:
                    storyboard = last_storyboard or {}
                    storyboard_path = last_storyboard_path or (MONTAGES_DIR / f"{safe_stem}_montage_storyboard.json")
                    beat_count = len(storyboard.get("beats") or [])
                    run_warnings.append("montage_no_usable_beats")
                    final_timing = _timing_payload("no_montage_beats", rendered_clip_count=0)
                    final_timing["montage"] = {
                        "status": storyboard.get("status"),
                        "requested_count": montage_requested_count,
                        "created_count": 0,
                        "audit_path": str(audit_path),
                        "storyboard_path": str(storyboard_path),
                        "beat_count": beat_count,
                    }
                    try:
                        final_timing["history_summary_after_run"] = self._record_processing_history(final_timing)
                    except Exception as e:
                        print(f"[timing] Failed to record montage processing history: {e}")
                    try:
                        self._record_run_learning_summary(
                            self._build_run_learning_summary(
                                timing=final_timing,
                                video_path=video_path,
                                source_id=source_id,
                                source_stem=source_stem,
                                game_title=source_game_context.get("label") or source_game_title,
                                settings=debug_settings,
                                candidates=candidates,
                                evaluations=evaluations,
                                selected=selected,
                                final_clips=[],
                                debug_path=run_debug_path,
                                status="no_montage_beats",
                            )
                        )
                    except Exception as e:
                        print(f"[learning] Failed to record montage no-output summary: {e}")
                    write_debug_report(
                        run_debug_path,
                        video_path,
                        candidates,
                        evaluations,
                        selected,
                        scene_detection=scene_detection,
                        settings=debug_settings,
                        video_duration=vid_duration,
                        final_clips=[],
                        warnings=run_warnings,
                        shadow_scoring=shadow_scoring,
                        voice_profile_shadow=voice_profile_shadow,
                        voice_profile_ranking=voice_profile_ranking,
                        moment_category_ranking=moment_category_ranking,
                        ai_moment_ranking=ai_moment_ranking,
                        multimodal_ranking=multimodal_ranking,
                        multi_signal_ai_ranking=multi_signal_ai_ranking,
                        visual_diagnostics=visual_diagnostics_report,
                        multimodal_analysis=multimodal_analysis_report,
                        ai_moment_classification=ai_moment_classification_report,
                        ai_moment_classification_shadow=ai_moment_classification_shadow,
                        timing=final_timing,
                        run_id=run_id,
                        debug_stage="run_no_montage_beats",
                    )
                    details = {
                        "completion_state": "no_montage_beats",
                        "message": "No montage could be assembled from this run.",
                        "guidance": "Try Balanced or Deep Analysis, a longer source, or a broader montage template.",
                        "requested_count": montage_requested_count,
                        "created_count": 0,
                        "audit_path": str(audit_path),
                        "storyboard_path": str(storyboard_path),
                        "beat_count": beat_count,
                    }
                    self._save_state()
                    self._js(f"window.onPipelineComplete(true, 0, 1, null, {json.dumps(details)})")
                    return

                final_timing = _timing_payload("success", rendered_clip_count=len(montage_results))
                final_timing["auto_metadata_count"] = sum(1 for item in montage_results if item.get("metadata"))
                final_timing["montage"] = {
                    "status": "rendered",
                    "template": montage_template,
                    "target_duration": montage_target,
                    "requested_count": montage_requested_count,
                    "created_count": len(montage_results),
                    "story_shape": montage_story_shape,
                    "audit_path": str(audit_path),
                    "storyboards": storyboard_records,
                }
                try:
                    final_timing["history_summary_after_run"] = self._record_processing_history(final_timing)
                except Exception as e:
                    print(f"[timing] Failed to record montage processing history: {e}")
                try:
                    self._record_run_learning_summary(
                        self._build_run_learning_summary(
                            timing=final_timing,
                            video_path=video_path,
                            source_id=source_id,
                            source_stem=source_stem,
                            game_title=source_game_context.get("label") or source_game_title,
                            settings=debug_settings,
                            candidates=candidates,
                            evaluations=evaluations,
                            selected=selected,
                            final_clips=final_clip_debug,
                            debug_path=run_debug_path,
                            status="success",
                        )
                    )
                except Exception as e:
                    print(f"[learning] Failed to record montage run summary: {e}")
                write_debug_report(
                    run_debug_path,
                    video_path,
                    candidates,
                    evaluations,
                    selected,
                    scene_detection=scene_detection,
                    settings=debug_settings,
                    video_duration=vid_duration,
                    final_clips=final_clip_debug,
                    warnings=run_warnings,
                    shadow_scoring=shadow_scoring,
                    voice_profile_shadow=voice_profile_shadow,
                    voice_profile_ranking=voice_profile_ranking,
                    moment_category_ranking=moment_category_ranking,
                    ai_moment_ranking=ai_moment_ranking,
                    multimodal_ranking=multimodal_ranking,
                    multi_signal_ai_ranking=multi_signal_ai_ranking,
                    visual_diagnostics=visual_diagnostics_report,
                    multimodal_analysis=multimodal_analysis_report,
                    ai_moment_classification=ai_moment_classification_report,
                    ai_moment_classification_shadow=ai_moment_classification_shadow,
                    timing=final_timing,
                    run_id=run_id,
                )
                print(f"[montage] Final montages saved: {len(montage_results)}")
                self._push("render", 100, f"{len(montage_results)} montage{'s' if len(montage_results) != 1 else ''} created")
                self._save_state()
                first_result = montage_results[0]
                created_count = len(montage_results)
                details = {
                    "completion_state": "montage_created",
                    "message": (
                        f"Created {created_count} montage{'s' if created_count != 1 else ''}."
                        if created_count == montage_requested_count
                        else f"Created {created_count} of {montage_requested_count} requested montages from the available beats."
                    ),
                    "path": first_result.get("path"),
                    "paths": [item.get("path") for item in montage_results if item.get("path")],
                    "requested_count": montage_requested_count,
                    "created_count": created_count,
                    "beat_count": sum(int((record or {}).get("beat_count") or 0) for record in storyboard_records),
                    "storyboard_path": storyboard_records[0]["storyboard_path"] if storyboard_records else "",
                    "storyboards": storyboard_records,
                    "debug_path": str(run_debug_path),
                }
                self._js(f"window.onPipelineComplete(true, {created_count}, 1, null, {json.dumps(details)})")
                return

            # ── 4. Render accepted clips ──────────────────────────────
            done: list[Path] = []
            done_moments: list[dict] = []
            final_clip_debug: list[dict] = []
            total = len(selected)
            render_queue = list(selected)
            if processing_depth == "deep" and total > 0:
                selected_ids = {id(row) for row in selected}
                backup_selection = select_near_quality_fallback_candidates(
                    evaluations,
                    total + min(6, max(2, total // 4)),
                    min_gap=min_gap,
                    score_key=selection_score_key,
                    existing_selected=selected,
                    allow_partial=True,
                    reason="final_creator_speech_replacement_pool",
                    subtitle_policy=commentary_guard_policy,
                )
                backups = [row for row in backup_selection if id(row) not in selected_ids]
                if backups:
                    render_queue.extend(backups)
                    run_warnings.append("final_render_replacement_pool_available")
            render_started = time.monotonic()

            for idx, item in enumerate(render_queue, 1):
                if len(done) >= total:
                    break
                if self._cancel:
                    return self._cancelled()
                m = item["moment"]
                words = item["words"]
                selection_primary_category, selection_moment_categories = _category_snapshot_from_selection(item, m)
                category_scoring = item.get("moment_category_scoring") or {}
                ranking_primary_category = category_scoring.get("primary_category") or selection_primary_category
                ranking_moment_categories = selection_moment_categories
                selected_start, selected_end = int(m["start"]), int(m["end"])
                start, end = selected_start, selected_end
                m["selection_primary_category"] = selection_primary_category
                if selection_moment_categories is not None:
                    m["selection_moment_categories"] = selection_moment_categories
                m["ranking_primary_category"] = ranking_primary_category
                if ranking_moment_categories is not None:
                    m["ranking_moment_categories"] = ranking_moment_categories
                if m.get("ai_moment_classification"):
                    m["selection_ai_moment_classification"] = copy.deepcopy(m["ai_moment_classification"])
                    m["ai_moment_classification_stage"] = "selection_pre_render"
                m["selected_start"] = selected_start
                m["selected_end"] = selected_end
                m["selected_duration"] = selected_end - selected_start
                m["render_start"] = start
                m["render_end"] = end
                m["render_duration"] = end - start
                clip_num = len(done) + 1
                out = self._unique_clip_output_path(stem, clip_num)
                wav = SUBTITLES_DIR / f"{stem}_c{clip_num}.wav"
                ass = SUBTITLES_DIR / f"{stem}_c{clip_num}.ass"
                for stale in (wav, ass):
                    try:
                        stale.unlink(missing_ok=True)
                    except Exception:
                        pass

                self._clip_push(clip_num, total, "audio", 40, f"Clip {clip_num}/{total}: Final transcription...")
                final_probe_end = min(end + 8, int(vid_duration))
                final_words = []
                final_stream = speech_stream if manual_stream_locked else m.get("speech_stream", speech_stream)
                final_retry_report = None
                if extract_audio_clip(video_path, start, final_probe_end, wav, audio_stream=final_stream):
                    final_words = transcribe_clip(wav, model_size=model, language=language)
                if allow_stream_retry and needs_stream_retry(
                    final_words,
                    final_probe_end - start,
                    subtitle_policy=commentary_guard_policy,
                    commentary_guard=commentary_guard_enabled,
                ):
                    retry_words, retry_stream = self._try_alternate_audio_streams(
                        video_path, start, final_probe_end, wav, model, language,
                        clip_num, total, final_stream, return_stream=True,
                        subtitle_policy=commentary_guard_policy,
                    )
                    final_retry_report = getattr(self, "_last_stream_retry", None)
                    if retry_words:
                        final_words = retry_words
                        final_stream = retry_stream
                final_voice_profile_score = (
                    self._voice_profile_score_for_wav(wav, voice_profile_snapshot)
                    if voice_profile_debug.get("ranking_active")
                    else self._voice_profile_inactive_score(voice_profile_snapshot)
                )

                if not final_words:
                    words = []
                    m["word_count"] = 0
                    m["subtitle_word_count"] = 0
                    m["analysis_word_count"] = 0
                    m["transcript"] = ""
                    m["final_transcription_warning"] = "no_final_words"

                if final_words:
                    final_stream_profile = _selected_audio_stream_profile(
                        audio_source_debug,
                        selected_stream=final_stream,
                        retry_report=final_retry_report,
                    )
                    final_candidate = {
                        **item["candidate"],
                        "start": start,
                        "end": end,
                        "duration": end - start,
                    }
                    final_eval = evaluate_candidate(
                        final_candidate,
                        final_words,
                        extraction_start=float(start),
                        extraction_end=float(final_probe_end),
                        video_duration=float(vid_duration),
                        target_duration=clip_duration,
                        selected_stream=final_stream,
                        quality_floor=quality_floor,
                        detection_preference=detection_preference,
                        commentary_guard=commentary_guard_enabled,
                        commentary_guard_policy=commentary_guard_policy,
                        voice_profile=final_voice_profile_score,
                        stream_profile=final_stream_profile,
                    )
                    final_eval["voice_profile"] = final_voice_profile_score
                    final_eval["moment"]["voice_profile"] = final_voice_profile_score
                    if final_retry_report:
                        final_eval["stream_retry"] = final_retry_report
                        final_eval["moment"]["stream_retry"] = final_retry_report
                    final_words_for_subtitles = final_eval.get("words")
                    if final_words_for_subtitles:
                        refined_moment = final_eval.get("moment") or {}
                        try:
                            trim_start = int(refined_moment.get("start", start))
                            trim_end = int(refined_moment.get("end", end))
                        except (TypeError, ValueError):
                            trim_start, trim_end = start, end
                        start, end = trim_start, trim_end
                        keep_rank = m.get("quality_rank")
                        keep_selection_quality = m.get("selection_quality_score")
                        keep_selection_rank_score = m.get("selection_rank_score")
                        keep_selection_score_source = m.get("selection_score_source")
                        keep_learned_quality = m.get("learned_quality_score")
                        keep_learned_adjustment = m.get("learned_adjustment")
                        keep_category_quality = m.get("moment_category_quality_score")
                        keep_category_adjustment = m.get("moment_category_adjustment")
                        keep_category_scoring = m.get("moment_category_scoring")
                        keep_voice_quality = m.get("voice_profile_quality_score")
                        keep_voice_adjustment = m.get("voice_adjustment")
                        keep_voice_scoring = m.get("voice_scoring")
                        keep_ai_quality = m.get("ai_moment_quality_score")
                        keep_ai_adjustment = m.get("ai_adjustment")
                        keep_ai_scoring = m.get("ai_moment_scoring")
                        keep_ai_classification = m.get("ai_moment_classification")
                        keep_multimodal_analysis = copy.deepcopy(m.get("multimodal_analysis")) if isinstance(m.get("multimodal_analysis"), dict) else None
                        keep_multimodal_quality = m.get("multimodal_quality_score")
                        keep_multimodal_adjustment = m.get("multimodal_adjustment")
                        keep_multimodal_scoring = copy.deepcopy(m.get("multimodal_scoring")) if isinstance(m.get("multimodal_scoring"), dict) else None
                        keep_selection_ai_classification = m.get("selection_ai_moment_classification")
                        keep_ranking_primary_category = m.get("ranking_primary_category")
                        keep_ranking_moment_categories = copy.deepcopy(m.get("ranking_moment_categories")) if isinstance(m.get("ranking_moment_categories"), dict) else None
                        keep_selection_primary_category = m.get("selection_primary_category")
                        keep_selection_moment_categories = copy.deepcopy(m.get("selection_moment_categories")) if isinstance(m.get("selection_moment_categories"), dict) else None
                        m.update(refined_moment)
                        m["selected_start"] = selected_start
                        m["selected_end"] = selected_end
                        m["selected_duration"] = selected_end - selected_start
                        m["render_start"] = start
                        m["render_end"] = end
                        m["render_duration"] = end - start
                        m["trim_adjusted_start"] = trim_start
                        m["trim_adjusted_end"] = trim_end
                        m["trim_adjusted_duration"] = max(0, trim_end - trim_start)
                        m["trim_adjusted_from_selected"] = (
                            trim_start != selected_start or trim_end != selected_end
                        )
                        m["subtitle_timing_offset"] = round(float(trim_start) - float(selected_start), 3)
                        m["start"] = start
                        m["end"] = end
                        m["duration"] = end - start
                        if keep_ai_classification:
                            m["ai_moment_classification"] = copy.deepcopy(keep_ai_classification)
                            m["ai_moment_classification_stage"] = "selection_pre_render"
                        if keep_selection_ai_classification:
                            m["selection_ai_moment_classification"] = copy.deepcopy(keep_selection_ai_classification)
                        if keep_rank is not None:
                            m["quality_rank"] = keep_rank
                        if keep_selection_quality is not None:
                            m["selection_quality_score"] = keep_selection_quality
                        if keep_selection_rank_score is not None:
                            m["selection_rank_score"] = keep_selection_rank_score
                        if keep_selection_score_source is not None:
                            m["selection_score_source"] = keep_selection_score_source
                        if keep_learned_quality is not None:
                            m["learned_quality_score"] = keep_learned_quality
                        if keep_learned_adjustment is not None:
                            m["learned_adjustment"] = keep_learned_adjustment
                        if keep_category_quality is not None:
                            m["moment_category_quality_score"] = keep_category_quality
                        if keep_category_adjustment is not None:
                            m["moment_category_adjustment"] = keep_category_adjustment
                        if keep_category_scoring is not None:
                            m["moment_category_scoring"] = keep_category_scoring
                        if keep_voice_quality is not None:
                            m["voice_profile_quality_score"] = keep_voice_quality
                        if keep_voice_adjustment is not None:
                            m["voice_adjustment"] = keep_voice_adjustment
                        if keep_voice_scoring is not None:
                            m["voice_scoring"] = keep_voice_scoring
                        if keep_ai_quality is not None:
                            m["ai_moment_quality_score"] = keep_ai_quality
                        if keep_ai_adjustment is not None:
                            m["ai_adjustment"] = keep_ai_adjustment
                        if keep_ai_scoring is not None:
                            m["ai_moment_scoring"] = keep_ai_scoring
                        if keep_multimodal_analysis is not None:
                            m["multimodal_analysis"] = keep_multimodal_analysis
                        if keep_multimodal_quality is not None:
                            m["multimodal_quality_score"] = keep_multimodal_quality
                        if keep_multimodal_adjustment is not None:
                            m["multimodal_adjustment"] = keep_multimodal_adjustment
                        if keep_multimodal_scoring is not None:
                            m["multimodal_scoring"] = keep_multimodal_scoring
                        m["selection_primary_category"] = keep_selection_primary_category
                        if keep_selection_moment_categories is not None:
                            m["selection_moment_categories"] = keep_selection_moment_categories
                        m["ranking_primary_category"] = keep_ranking_primary_category
                        if keep_ranking_moment_categories is not None:
                            m["ranking_moment_categories"] = keep_ranking_moment_categories
                        words = _subtitle_words_for_render_start(
                            final_eval["words"],
                            trim_start,
                            start,
                        )
                    elif isinstance(final_words_for_subtitles, list):
                        refined_moment = final_eval.get("moment") or {}
                        for key in (
                            "commentary_guard",
                            "music_lyrics_guard",
                            "music_lyrics_penalty",
                            "speech_source",
                            "speech_source_penalty",
                        ):
                            if refined_moment.get(key) is not None:
                                m[key] = refined_moment.get(key)
                        words = []
                        m["word_count"] = 0
                        m["subtitle_word_count"] = 0
                        m["transcript"] = ""
                m["audio_source"] = {
                    "mode": audio_source_debug.get("mode"),
                    "selected_stream": final_stream,
                    "selected_reason": audio_source_debug.get("selected_reason"),
                    "selected_confidence": audio_source_debug.get("selected_confidence"),
                    "runner_up_stream": audio_source_debug.get("runner_up_stream"),
                    "stream_count": audio_source_debug.get("stream_count"),
                    "render_audio": audio_source_debug.get("render_audio"),
                    "alternate_stream_retry": audio_source_debug.get("alternate_stream_retry"),
                    "subtitle_policy": audio_source_debug.get("subtitle_policy"),
                    "commentary_guard_enabled": audio_source_debug.get("commentary_guard_enabled"),
                    "single_track_commentary_guard": audio_source_debug.get("single_track_commentary_guard"),
                    "stream_selection": _audio_stream_selection_summary(
                        audio_source_debug,
                        selected_stream=final_stream,
                        retry_report=final_retry_report,
                    ),
                }
                m["stream_selection"] = m["audio_source"]["stream_selection"]
                m["voice_profile"] = final_voice_profile_score
                if final_retry_report:
                    m["stream_retry"] = final_retry_report
                m["subtitle_style"] = style
                m["captions_requested"] = bool(subtitle_enabled)
                m["subtitle_enabled"] = bool(subtitle_enabled)
                m["speech_policy"] = _clip_speech_policy_summary(m)
                if _creator_caption_speech_missing(
                    m,
                    subtitle_enabled=bool(subtitle_enabled),
                    subtitle_policy=commentary_guard_policy,
                ):
                    m["final_render_rejected"] = True
                    m["final_render_reject_reason"] = "no_selected_commentary_transcript"
                    if m["speech_policy"].get("warning"):
                        m["metadata_warning"] = m["speech_policy"]["warning"]
                    m["metadata_needs_context"] = True
                    run_warnings.append(f"clip_{clip_num}_no_selected_commentary_transcript")
                    self._clip_push(
                        clip_num,
                        total,
                        "subtitle",
                        100,
                        f"Clip {clip_num}/{total}: Skipped, no commentary transcript",
                    )
                    for stale in (wav, ass, out):
                        try:
                            stale.unlink(missing_ok=True)
                        except Exception:
                            pass
                    continue

                self._clip_push(clip_num, total, "audio", 100, f"Clip {clip_num}/{total}: Preparing...")

                # ── 4a: compute crop params (uses adjusted start/end) ──
                crop_params = None
                crop_w, crop_h = get_dimensions(video_path)
                if crop_vertical:
                    if self._cancel:
                        return self._cancelled()
                    self._clip_push(clip_num, total, "audio", 100, f"Clip {clip_num}/{total}: Tracking speakers...")
                    try:
                        crop_profile = _crop_tracking_profile(processing_depth)
                        crop_params = get_crop_params_dynamic(
                            video_path,
                            start,
                            end,
                            sample_count=int(crop_profile["sample_count"]),
                            min_sample_rate=float(crop_profile["min_sample_rate"]),
                        )
                    except Exception as e:
                        print(f"[!] Crop detection failed for clip {clip_num}: {e}")
                        crop_params = None
                    if crop_params:
                        crop_w, crop_h = crop_params[0], crop_params[1]

                # ── 4b: subtitles (pass cropped dimensions) ──
                if self._cancel:
                    return self._cancelled()
                resolved_subtitle_placement = resolve_subtitle_placement(
                    crop_w, crop_h, subtitle_placement
                )
                if subtitle_enabled:
                    self._clip_push(clip_num, total, "subtitle", 0, f"Clip {clip_num}/{total}: Generating subtitles...")
                    ass_path = generate_subtitles(
                        words, ass,
                        video_width=crop_w,
                        video_height=crop_h,
                        style=style,
                        subtitle_placement=subtitle_placement,
                    )
                else:
                    ass.unlink(missing_ok=True)
                    ass_path = None
                m["subtitle_generated"] = bool(ass_path)
                m["subtitle_placement"] = resolved_subtitle_placement
                m["processing_depth"] = processing_depth
                m["speech_policy"] = _clip_speech_policy_summary(m)
                if m["speech_policy"].get("warning"):
                    m["metadata_warning"] = m["speech_policy"]["warning"]
                m["metadata_needs_context"] = bool(m["speech_policy"].get("metadata_backfill_blocked"))
                self._clip_push(
                    clip_num, total, "subtitle", 100,
                    "Subtitles generated" if ass_path else ("Captions disabled" if not subtitle_enabled else "No subtitles generated"),
                )

                # ── 4c: render clip with crop + burned subs ──
                if self._cancel:
                    return self._cancelled()
                self._clip_push(clip_num, total, "render", 0, f"Clip {clip_num}/{total}: Rendering...")
                clip_result = extract_clip(
                    video_path, start, end, out,
                    subtitle_path=ass_path if ass_path else None,
                    crop_params=crop_params,
                    preset=preset, crf=crf,
                )
                if clip_result and clip_result.path:
                    m["subtitles_burned"] = bool(clip_result.subtitles_burned and ass_path)
                    m["source_id"] = source_id
                    m["source_path"] = str(video_path)
                    m["source_stem"] = source_stem
                    m["game_title"] = source_game_context.get("label") or source_game_title
                    m["game_title_hint"] = game_title_hint
                    m["game_identity"] = source_game_identity
                    m["game_context"] = source_game_context
                    m["clip_id"] = self._clip_id_for(m, clip_result.path)
                    if clip_result.warning:
                        m["render_warning"] = clip_result.warning
                        run_warnings.append(f"clip_{clip_num}_{clip_result.warning}")
                    m["speech_policy"] = _clip_speech_policy_summary(m)
                    if m["speech_policy"].get("warning"):
                        m["metadata_warning"] = m["speech_policy"]["warning"]
                    m["metadata_needs_context"] = bool(m["speech_policy"].get("metadata_backfill_blocked"))

                    # Post-processing: apply video effect
                    if effect and effect != "none":
                        self._clip_push(clip_num, total, "render", 80,
                                        f"Clip {clip_num}/{total}: Applying {effect} effect...")
                        apply_video_effect(clip_result.path, effect, preset, crf)

                    # Post-processing: mix background music
                    if music_file:
                        music_path = self._safe_child_path(MUSIC_DIR, music_file)
                        if music_path and music_path.exists() and music_path.is_file():
                            self._clip_push(clip_num, total, "render", 90,
                                            f"Clip {clip_num}/{total}: Adding music...")
                            add_background_music(
                                clip_result.path, music_path, music_volume,
                                trim_start=music_start, trim_end=music_end,
                            )
                        else:
                            run_warnings.append(f"clip_{clip_num}_music_file_missing")

                    m["truth_summary"] = self._clip_truth_summary(m, clip_result.path)
                    done.append(clip_result.path)
                    done_moments.append(m)
                    final_primary_category = m.get("primary_category") or item.get("primary_category")
                    final_moment_categories = m.get("moment_categories") or item.get("moment_categories")
                    final_clip_debug.append(
                        {
                            "index": clip_num,
                            "path": str(clip_result.path),
                            "clip_id": m.get("clip_id"),
                            "source_id": m.get("source_id"),
                            "source_path": m.get("source_path"),
                            "source_stem": m.get("source_stem"),
                            "game_title": m.get("game_title"),
                            "game_identity": {
                                "status": (m.get("game_identity") or {}).get("status"),
                                "title": (m.get("game_identity") or {}).get("title"),
                                "qid": (m.get("game_identity") or {}).get("qid"),
                                "confidence": (m.get("game_identity") or {}).get("confidence"),
                                "matched_via": (m.get("game_identity") or {}).get("matched_via"),
                            } if isinstance(m.get("game_identity"), dict) else {},
                            "game_context": compact_game_context_for_prompt(
                                m.get("game_context") if isinstance(m.get("game_context"), dict) else {}
                            ),
                            "subtitle_path": str(ass_path) if ass_path else None,
                            "start": start,
                            "end": end,
                            "duration": end - start,
                            "selected_start": m.get("selected_start", start),
                            "selected_end": m.get("selected_end", end),
                            "selected_duration": m.get("selected_duration", end - start),
                            "render_start": m.get("render_start", start),
                            "render_end": m.get("render_end", end),
                            "render_duration": m.get("render_duration", end - start),
                            "trim_adjusted_start": m.get("trim_adjusted_start"),
                            "trim_adjusted_end": m.get("trim_adjusted_end"),
                            "trim_adjusted_duration": m.get("trim_adjusted_duration"),
                            "trim_adjusted_from_selected": m.get("trim_adjusted_from_selected", False),
                            "subtitle_timing_offset": m.get("subtitle_timing_offset", 0.0),
                            "base_quality_score": item.get("quality_score"),
                            "quality_score": m.get("quality_score"),
                            "selection_quality_score": m.get("selection_quality_score"),
                            "selection_rank_score": item.get("selection_rank_score"),
                            "selection_score_source": item.get("selection_score_source", "quality_score"),
                            "learned_score": item.get("shadow_scoring", {}).get("learned_quality_score"),
                            "learned_quality_score": item.get("shadow_scoring", {}).get("learned_quality_score"),
                            "learned_adjustment": item.get("shadow_scoring", {}).get("learned_adjustment"),
                            "moment_category_quality_score": item.get("moment_category_scoring", {}).get("moment_category_quality_score"),
                            "moment_category_ranking_enabled": item.get("moment_category_scoring", {}).get("ranking_enabled"),
                            "moment_category_adjustment": item.get("moment_category_scoring", {}).get("category_adjustment"),
                            "moment_category_diversity_adjustment": item.get("moment_category_scoring", {}).get("category_diversity_adjustment"),
                            "moment_category_selection_delta": item.get("moment_category_scoring", {}).get("selection_delta", ""),
                            "moment_category_rank_delta": item.get("moment_category_scoring", {}).get("rank_delta"),
                            "moment_category_scoring": item.get("moment_category_scoring") or m.get("moment_category_scoring"),
                            "selection_primary_category": selection_primary_category,
                            "selection_moment_categories": selection_moment_categories,
                            "ranking_primary_category": ranking_primary_category,
                            "ranking_moment_categories": ranking_moment_categories,
                            "final_primary_category": final_primary_category,
                            "final_moment_categories": final_moment_categories,
                            "primary_category": final_primary_category,
                            "moment_categories": final_moment_categories,
                            "voice_profile_quality_score": item.get("voice_scoring", {}).get("voice_profile_quality_score"),
                            "voice_ranking_enabled": item.get("voice_scoring", {}).get("ranking_enabled"),
                            "voice_ranking_adjustment": item.get("voice_scoring", {}).get("voice_adjustment"),
                            "voice_ranking_selection_delta": item.get("voice_scoring", {}).get("selection_delta", ""),
                            "voice_ranking_rank_delta": item.get("voice_scoring", {}).get("rank_delta"),
                            "voice_scoring": item.get("voice_scoring") or m.get("voice_scoring"),
                            "ai_moment_quality_score": item.get("ai_moment_scoring", {}).get("ai_moment_quality_score"),
                            "ai_ranking_enabled": item.get("ai_moment_scoring", {}).get("ranking_enabled"),
                            "ai_adjustment": item.get("ai_moment_scoring", {}).get("ai_adjustment"),
                            "ai_selection_delta": item.get("ai_moment_scoring", {}).get("selection_delta", ""),
                            "ai_rank_delta": item.get("ai_moment_scoring", {}).get("rank_delta"),
                            "ai_scoring_eligible": item.get("ai_moment_scoring", {}).get("ai_scoring_eligible"),
                            "ai_ineligible_reason": item.get("ai_moment_scoring", {}).get("ai_ineligible_reason"),
                            "ai_moment_scoring": item.get("ai_moment_scoring") or m.get("ai_moment_scoring"),
                            "multimodal_quality_score": item.get("multimodal_scoring", {}).get("multimodal_quality_score"),
                            "multimodal_ranking_enabled": item.get("multimodal_scoring", {}).get("ranking_enabled"),
                            "multimodal_adjustment": item.get("multimodal_scoring", {}).get("multimodal_adjustment"),
                            "multimodal_selection_delta": item.get("multimodal_scoring", {}).get("selection_delta", ""),
                            "multimodal_rank_delta": item.get("multimodal_scoring", {}).get("rank_delta"),
                            "multimodal_scoring_eligible": item.get("multimodal_scoring", {}).get("scoring_eligible"),
                            "multimodal_ineligible_reason": item.get("multimodal_scoring", {}).get("ineligible_reason"),
                            "multimodal_scoring": item.get("multimodal_scoring") or m.get("multimodal_scoring"),
                            "rank_delta": item.get("shadow_scoring", {}).get("rank_delta"),
                            "selection_delta": item.get("shadow_scoring", {}).get("selection_delta", ""),
                            "shadow_scoring": item.get("shadow_scoring", {}),
                            "quality_rank": m.get("quality_rank"),
                            "word_count": m.get("word_count"),
                            "analysis_word_count": m.get("analysis_word_count"),
                            "subtitle_word_count": m.get("subtitle_word_count"),
                            "speech_stream": m.get("speech_stream"),
                            "audio_source": m.get("audio_source"),
                            "stream_selection": m.get("stream_selection"),
                            "stream_retry": m.get("stream_retry"),
                            "visual_diagnostics": m.get("visual_diagnostics"),
                            "multimodal_analysis": m.get("multimodal_analysis"),
                            "truth_summary": m.get("truth_summary"),
                            "selection_ai_moment_classification": m.get("selection_ai_moment_classification") or item.get("ai_moment_classification"),
                            "ai_moment_classification_stage": m.get("ai_moment_classification_stage"),
                            "ai_moment_classification": m.get("ai_moment_classification"),
                            "commentary_guard": m.get("commentary_guard"),
                            "music_lyrics_guard": m.get("music_lyrics_guard"),
                            "music_lyrics_penalty": m.get("music_lyrics_penalty"),
                            "voice_profile": m.get("voice_profile"),
                            "subtitle_style": m.get("subtitle_style"),
                            "captions_requested": m.get("captions_requested"),
                            "subtitle_enabled": m.get("subtitle_enabled"),
                            "subtitle_generated": m.get("subtitle_generated"),
                            "subtitles_burned": m.get("subtitles_burned"),
                            "subtitle_placement": m.get("subtitle_placement"),
                            "speech_policy": m.get("speech_policy"),
                            "metadata_warning": m.get("metadata_warning", ""),
                            "metadata_needs_context": m.get("metadata_needs_context", False),
                            "render_warning": m.get("render_warning", ""),
                            "transcript": m.get("transcript", ""),
                        }
                    )
                    if not clip_result.subtitles_burned and clip_result.warning:
                        self._clip_push(clip_num, total, "render", 100,
                                        f"Clip {clip_num} done (WARNING: {clip_result.warning})")
                    else:
                        self._clip_push(clip_num, total, "render", 100, f"Clip {clip_num} complete!")
                elif clip_result and not clip_result.path:
                    self._clip_push(clip_num, total, "render", 100, f"Clip {clip_num} failed")
                else:
                    self._clip_push(clip_num, total, "render", 100, f"Clip {clip_num} failed")

                try:
                    wav.unlink(missing_ok=True)
                except Exception:
                    pass

            # Append results (batch mode: preserve previous video's clips)
            stage_timings["final_render"] = round(time.monotonic() - render_started, 3)
            first_new_clip_index = len(self._results)
            self._results.extend(done)
            self._moments.extend(done_moments)
            metadata_started = time.monotonic()
            auto_metadata = self._generate_auto_metadata_for_results(
                first_new_clip_index,
                len(done),
                final_clip_debug,
                run_warnings,
            ) if done else []
            stage_timings["auto_metadata"] = round(time.monotonic() - metadata_started, 3)
            final_timing = _timing_payload("success", rendered_clip_count=len(done))
            final_timing["auto_metadata_count"] = len(auto_metadata)
            try:
                final_timing["history_summary_after_run"] = self._record_processing_history(final_timing)
            except Exception as e:
                print(f"[timing] Failed to record processing history: {e}")
            try:
                self._record_run_learning_summary(
                    self._build_run_learning_summary(
                        timing=final_timing,
                        video_path=video_path,
                        source_id=source_id,
                        source_stem=source_stem,
                        game_title=source_game_context.get("label") or source_game_title,
                        settings=debug_settings,
                        candidates=candidates,
                        evaluations=evaluations,
                        selected=selected,
                        final_clips=final_clip_debug,
                        debug_path=run_debug_path,
                        status="success",
                    )
                )
            except Exception as e:
                print(f"[learning] Failed to record run summary: {e}")
            try:
                write_debug_report(
                    run_debug_path,
                    video_path,
                    candidates,
                    evaluations,
                    selected,
                    scene_detection=scene_detection,
                    settings=debug_settings,
                    video_duration=vid_duration,
                    final_clips=final_clip_debug,
                    warnings=run_warnings,
                    shadow_scoring=shadow_scoring,
                    voice_profile_shadow=voice_profile_shadow,
                    voice_profile_ranking=voice_profile_ranking,
                    moment_category_ranking=moment_category_ranking,
                    ai_moment_ranking=ai_moment_ranking,
                    multimodal_ranking=multimodal_ranking,
                    multi_signal_ai_ranking=multi_signal_ai_ranking,
                    visual_diagnostics=visual_diagnostics_report,
                    multimodal_analysis=multimodal_analysis_report,
                    ai_moment_classification=ai_moment_classification_report,
                    ai_moment_classification_shadow=ai_moment_classification_shadow,
                    timing=final_timing,
                    run_id=run_id,
                )
                print(f"[rank] Run debug saved: {run_debug_path}")
            except Exception as e:
                print(f"[rank] Failed to save final run debug: {e}")
            self._save_state()
            self._js(f"window.onPipelineComplete(true, {len(done)}, {total}, null)")

        except CancelledError:
            return self._cancelled()
        except Exception as e:
            self._record_pipeline_error(e, {"phase": "pipeline", "url": str(url or "")[:300]})
            self._error(str(e))
        finally:
            self._active_progress_context = None
            self._processing = False

    # ── Download with real progress ──────────────────────────────────────

    def _download_with_progress(self, url):
        """Download via yt-dlp with progress_hooks for live percent updates."""

        def hook(d):
            if self._cancel:
                raise CancelledError("Download cancelled")
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                if total > 0:
                    pct = int(downloaded / total * 100)
                    self._push("download", pct, f"Downloading... {pct}%")
            elif d["status"] == "finished":
                self._push("download", 95, "Merging formats...")

        DOWNLOADS_DIR.mkdir(exist_ok=True)

        # Prefer H.264 (avc1) — universally supported by ffmpeg.
        # restrictfilenames removes unicode chars that break Windows paths.
        fmt = (
            "bestvideo[vcodec^=avc1][height<=1080]+bestaudio[acodec^=mp4a]/"
            "bestvideo[vcodec^=avc1][height<=1080]+bestaudio/"
            "bestvideo[height<=1080]+bestaudio/"
            "best"
        )
        ydl_opts = {
            "format": fmt,
            "outtmpl": str(DOWNLOADS_DIR / "%(title)s.%(ext)s"),
            "merge_output_format": "mp4",
            "restrictfilenames": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [hook],
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            "file_access_retries": 3,
            "extractor_retries": 3,
        }

        # If it looks like a local file path, just use it directly
        if Path(url).exists():
            path = Path(url)
            if not hasattr(self, "_download_info_by_path"):
                self._download_info_by_path = {}
            try:
                self._download_info_by_path.pop(str(path.resolve()), None)
            except Exception:
                pass
            return path

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = resolve_downloaded_path(info, ydl)
            if not hasattr(self, "_download_info_by_path"):
                self._download_info_by_path = {}
            try:
                self._download_info_by_path[str(path.resolve())] = self._compact_download_info(info, url)
            except Exception:
                pass
            return path

    @staticmethod
    def _compact_download_info(info: dict | None, original_url: str = "") -> dict:
        info = info if isinstance(info, dict) else {}

        def text(value, limit):
            cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
            cleaned = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", cleaned)
            return cleaned[:limit]

        def list_text(values, limit, item_limit):
            result = []
            for value in values or []:
                cleaned = text(value, item_limit)
                if cleaned and cleaned not in result:
                    result.append(cleaned)
                if len(result) >= limit:
                    break
            return result

        return {
            "schema_version": 1,
            "source": "yt_dlp",
            "title": text(info.get("title"), 180),
            "uploader": text(info.get("uploader") or info.get("channel"), 140),
            "channel": text(info.get("channel"), 140),
            "webpage_url": text(info.get("webpage_url") or original_url, 300),
            "original_url": text(original_url, 300),
            "categories": list_text(info.get("categories"), 8, 80),
            "tags": list_text(info.get("tags"), 20, 60),
            "description": text(info.get("description"), 1000),
        }

    def _try_alternate_audio_streams(self, video_path, start, end, wav, model, language,
                                     clip_num, total, preferred_stream=None,
                                     return_stream=False, progress_stage="clip",
                                     progress_percent=None, subtitle_policy="creator"):
        """If the preferred mic stream is silent, try other audio tracks for speech."""
        streams = get_audio_streams(video_path)
        self._last_stream_retry = {
            "schema_version": 1,
            "attempted": False,
            "accepted": False,
            "preferred_stream": preferred_stream,
            "subtitle_policy": subtitle_policy,
            "max_alternate_streams": 2,
            "attempts": [],
        }
        if len(streams) < 2:
            return ([], None) if return_stream else []

        preferred = preferred_stream
        if preferred is None:
            preferred = pick_voice_stream_ordinal(video_path)
        self._last_stream_retry["preferred_stream"] = preferred
        alternate_attempts = 0
        for stream in streams:
            ordinal = int(stream["ordinal"])
            if preferred is not None and ordinal == preferred:
                continue
            if alternate_attempts >= self._last_stream_retry["max_alternate_streams"]:
                self._last_stream_retry["stopped_reason"] = "alternate_stream_retry_cap"
                break
            alternate_attempts += 1
            title = stream.get("title") or f"0:a:{ordinal}"
            self._last_stream_retry["attempted"] = True
            print(f"[audio] Preferred stream needs retry; trying 0:a:{ordinal} ({title})")
            if progress_stage == "candidates":
                pct = int(progress_percent) if progress_percent is not None else 0
                self._push("candidates", pct, f"Candidate {clip_num}/{total}: trying audio track {ordinal + 1}...")
            elif progress_stage != "silent":
                self._clip_push(
                    clip_num, total, "transcribe", 40,
                    f"Clip {clip_num}/{total}: Trying audio track {ordinal + 1}..."
                )
            try:
                Path(wav).unlink(missing_ok=True)
            except Exception:
                pass
            r = extract_audio_clip(video_path, start, end, wav, audio_stream=ordinal)
            if not r:
                continue
            words = transcribe_clip(wav, model_size=model, language=language)
            if words:
                profile = profile_words_for_stream(
                    ordinal,
                    title,
                    words,
                    wav_path=wav,
                    sampled_seconds=max(0.0, float(end) - float(start)),
                )
                acceptance = should_accept_alternate_stream(
                    profile,
                    subtitle_policy=subtitle_policy,
                )
                self._last_stream_retry["attempts"].append(acceptance)
                if not acceptance.get("accepted"):
                    print(
                        "[audio] Rejected alternate stream "
                        f"0:a:{ordinal} ({title}): {acceptance.get('reason')}"
                    )
                    continue
                self._last_stream_retry["accepted"] = True
                self._last_stream_retry["accepted_stream"] = ordinal
                self._last_stream_retry["accepted_reason"] = acceptance.get("reason")
                print(f"[audio] Using 0:a:{ordinal} ({title}) for subtitles")
                return (words, ordinal) if return_stream else words
            else:
                self._last_stream_retry["attempts"].append({
                    "schema_version": 1,
                    "accepted": False,
                    "reason": "no_transcribed_words",
                    "subtitle_policy": subtitle_policy,
                    "stream": ordinal,
                    "title": title,
                    "words": 0,
                })

        return ([], None) if return_stream else []

    # ── Upload orchestrator (background thread) ──────────────────────────

    @staticmethod
    def _schedule_upload_fingerprint_value(value):
        if isinstance(value, (list, tuple, set)):
            return [ApiBridge._schedule_upload_fingerprint_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): ApiBridge._schedule_upload_fingerprint_value(value[key])
                for key in sorted(value)
                if value[key] not in (None, "")
            }
        if value is None:
            return ""
        return str(value).strip()

    def _schedule_upload_slot_fingerprint(self, item: dict | None) -> str:
        item = item if isinstance(item, dict) else {}
        payload = {
            key: self._schedule_upload_fingerprint_value(item.get(key))
            for key in (
                "clip_id",
                "clip_filename",
                "clipIdx",
                "source_id",
                "account_id",
                "channel_id",
                "date",
                "time",
                "publish_at",
                "scheduled_local",
                "timezone_offset_minutes",
                "title",
                "privacy",
                "description",
                "final_description",
                "tags",
                "category_id",
            )
        }
        return hashlib.sha1(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8", errors="ignore")
        ).hexdigest()

    def _scheduled_upload_attempt_matches(self, item: dict, attempt_id: str | None = None) -> bool:
        attempt = str(attempt_id or "").strip()
        if not attempt:
            return True
        if str(item.get("upload_attempt_id") or "").strip() != attempt:
            return False
        stored_fingerprint = str(item.get("upload_attempt_fingerprint") or "").strip()
        if not stored_fingerprint:
            return False
        return stored_fingerprint == self._schedule_upload_slot_fingerprint(item)

    def _scheduled_upload_active(self, clip_idx, meta=None, channel_id=None, attempt_id: str | None = None) -> bool:
        with self._get_state_lock():
            for item in self._scheduled:
                if item.get("uploaded"):
                    continue
                if str(item.get("scheduler_status") or "").lower() == "upload_outcome_unknown":
                    continue
                if not self._scheduled_item_matches_clip(item, clip_idx, meta):
                    continue
                if channel_id and item.get("channel_id") and item.get("channel_id") != channel_id:
                    continue
                if meta and meta.get("channel_id") and item.get("channel_id") and item.get("channel_id") != meta.get("channel_id"):
                    continue
                if meta and meta.get("title") and item.get("title") and item.get("title") != meta.get("title"):
                    continue
                if meta and meta.get("account_id") and item.get("account_id") and item.get("account_id") != meta.get("account_id"):
                    continue
                if not self._scheduled_upload_attempt_matches(item, attempt_id):
                    continue
                return True
        return False

    def _scheduled_item_matches_clip(self, item, clip_idx, meta=None) -> bool:
        """Match schedule entries by stable identity before falling back to legacy indexes."""
        if not isinstance(item, dict):
            return False
        meta = meta or {}
        try:
            resolved_idx = int(clip_idx)
        except (TypeError, ValueError):
            resolved_idx = -1

        target_clip_id = str(meta.get("clip_id") or "").strip()
        target_filename = str(meta.get("clip_filename") or "").strip()
        moments = getattr(self, "_moments", [])
        results = getattr(self, "_results", [])
        if 0 <= resolved_idx < len(moments):
            moment = moments[resolved_idx]
            if isinstance(moment, dict) and not target_clip_id:
                target_clip_id = str(moment.get("clip_id") or "").strip()
        if 0 <= resolved_idx < len(results) and not target_filename:
            target_filename = Path(results[resolved_idx]).name

        item_clip_id = str(item.get("clip_id") or "").strip()
        item_filename = str(item.get("clip_filename") or "").strip()

        if target_clip_id or item_clip_id:
            return bool(target_clip_id and item_clip_id and target_clip_id == item_clip_id)
        if target_filename or item_filename:
            return bool(target_filename and item_filename and target_filename == item_filename)

        try:
            return int(item.get("clipIdx", -1)) == resolved_idx
        except (TypeError, ValueError):
            return False

    def _mark_scheduled_uploaded(self, clip_idx, meta, upload_result=None, trigger: str = "manual", attempt_id: str | None = None):
        """Mark the matching scheduled item as uploaded after YouTube accepts it."""
        with self._get_state_lock():
            meta = meta or {}
            timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            changed = False
            for item in self._scheduled:
                if not self._scheduled_item_matches_clip(item, clip_idx, meta):
                    continue
                if meta and meta.get("account_id") and item.get("account_id") and item.get("account_id") != meta.get("account_id"):
                    continue
                if meta and meta.get("channel_id") and item.get("channel_id") and item.get("channel_id") != meta.get("channel_id"):
                    continue
                if meta and meta.get("title") and item.get("title") and item.get("title") != meta.get("title"):
                    continue
                if not self._scheduled_upload_attempt_matches(item, attempt_id):
                    continue
                item["uploaded"] = True
                item["uploaded_at"] = timestamp
                item["upload_state"] = (
                    "youtube_scheduled"
                    if trigger != "scheduler" and str(item.get("privacy") or meta.get("privacy") or "").lower() == "public"
                    else "sent_to_youtube"
                )
                item["send_status"] = item["upload_state"]
                for key in SCHEDULE_BACKEND_STATUS_FIELDS:
                    item.pop(key, None)
                if isinstance(upload_result, dict):
                    if upload_result.get("id"):
                        item["youtube_id"] = upload_result["id"]
                    if upload_result.get("url"):
                        item["youtube_url"] = upload_result["url"]
                self._append_upload_history(
                    self._upload_history_record(item, clip_idx, meta, upload_result, trigger=trigger, timestamp=timestamp)
                )
                changed = True
            if changed:
                self._save_state()
            return changed

    def _begin_scheduled_upload_attempt(self, clip_idx, meta=None, trigger: str = "manual", channel_id=None) -> str | None:
        """Persist a durable in-progress marker before calling YouTube."""
        with self._get_state_lock():
            meta = meta or {}
            attempt_id = f"{trigger}-{uuid.uuid4().hex}"
            timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            changed = False
            for item in self._scheduled:
                if item.get("uploaded"):
                    continue
                if not self._scheduled_item_matches_clip(item, clip_idx, meta):
                    continue
                if channel_id and item.get("channel_id") and item.get("channel_id") != channel_id:
                    continue
                if meta.get("channel_id") and item.get("channel_id") and item.get("channel_id") != meta.get("channel_id"):
                    continue
                if meta.get("account_id") and item.get("account_id") and item.get("account_id") != meta.get("account_id"):
                    continue
                if meta.get("title") and item.get("title") and item.get("title") != meta.get("title"):
                    continue
                item["scheduler_status"] = "uploading"
                item["scheduler_note"] = "Sending to YouTube"
                item["upload_attempt_id"] = attempt_id
                item["upload_attempt_fingerprint"] = self._schedule_upload_slot_fingerprint(item)
                item["upload_attempt_started_at"] = timestamp
                item["upload_attempt_trigger"] = trigger
                item.pop("upload_unknown_at", None)
                changed = True
            if changed:
                self._save_state()
                return attempt_id
            return None

    def _clear_scheduled_upload_attempt(self, clip_idx, meta=None, channel_id=None, attempt_id: str | None = None) -> bool:
        """Clear an in-progress marker when an upload is cancelled before acceptance."""
        with self._get_state_lock():
            meta = meta or {}
            changed = False
            for item in self._scheduled:
                if item.get("uploaded"):
                    continue
                if not self._scheduled_item_matches_clip(item, clip_idx, meta):
                    continue
                if channel_id and item.get("channel_id") and item.get("channel_id") != channel_id:
                    continue
                if str(item.get("scheduler_status") or "").lower() == "uploading":
                    if not self._scheduled_upload_attempt_matches(item, attempt_id):
                        continue
                    for key in ("scheduler_status", "scheduler_note", "upload_attempt_id", "upload_attempt_fingerprint", "upload_attempt_started_at", "upload_attempt_trigger"):
                        item.pop(key, None)
                    changed = True
            if changed:
                self._save_state()
            return changed

    def _mark_scheduled_upload_failed_for_clip(self, clip_idx, meta, error, now=None, attempt_id: str | None = None) -> bool:
        """Mark matching scheduled rows as failed after a known upload error."""
        with self._get_state_lock():
            meta = meta or {}
            changed = False
            for item in self._scheduled:
                if item.get("uploaded"):
                    continue
                if not self._scheduled_item_matches_clip(item, clip_idx, meta):
                    continue
                if meta.get("channel_id") and item.get("channel_id") and item.get("channel_id") != meta.get("channel_id"):
                    continue
                if meta.get("account_id") and item.get("account_id") and item.get("account_id") != meta.get("account_id"):
                    continue
                if meta.get("title") and item.get("title") and item.get("title") != meta.get("title"):
                    continue
                if not self._scheduled_upload_attempt_matches(item, attempt_id):
                    continue
                self._mark_scheduled_upload_failed(item, error, now)
                changed = True
            if changed:
                self._save_state()
            return changed

    def _run_upload(self, clips_metadata, channel_id=None, upload_lock=None):
        try:
            ordered_metadata = self._validate_upload_metadata(clips_metadata)
            total = len(ordered_metadata)
            uploaded = 0
            skipped = 0

            for i, (_original_index, meta, scheduled) in enumerate(ordered_metadata):
                if self._is_cancelled():
                    return self._cancelled()
                pct = int((i / total) * 100)

                idx = self._resolve_clip_index(meta)
                if idx is None:
                    skipped += 1
                    continue
                video_path = self._safe_clip_path(self._results[idx])
                if not video_path:
                    skipped += 1
                    continue
                meta = self._ensure_schedule_description(dict(meta), idx)
                if not self._scheduled_upload_active(idx, meta, channel_id):
                    skipped += 1
                    print(f"[upload] Skipping Clip {idx + 1}; it is no longer scheduled")
                    continue

                self._push("upload", pct, f"Uploading clip {i + 1}/{total}...")
                attempt_id = self._begin_scheduled_upload_attempt(idx, meta, trigger="manual", channel_id=channel_id)
                if not attempt_id:
                    skipped += 1
                    print(f"[upload] Skipping Clip {idx + 1}; schedule slot changed before upload")
                    continue
                self._js("window.onScheduleUpdated()")

                clip_base_pct = int((i / total) * 100)
                clip_span_pct = 100 / total

                def _upload_progress(chunk_percent, clip_number=i + 1):
                    overall = min(99, int(clip_base_pct + (float(chunk_percent) / 100.0) * clip_span_pct))
                    self._push("upload", overall, f"Uploading clip {clip_number}/{total}... {int(chunk_percent)}%")

                try:
                    result = upload_to_youtube(
                        video_path,
                        title=meta.get("title", f"Viral Clip #{i + 1}"),
                        description=meta.get("final_description") or meta.get("description", ""),
                        tags=meta.get("tags", generate_tags()),
                        privacy=meta.get("privacy", "private"),
                        scheduled_time=scheduled,
                        channel_id=meta.get("channel_id") or channel_id,
                        account_id=meta.get("account_id"),
                        cancel_check=lambda: self._is_cancelled() or not self._scheduled_upload_active(idx, meta, channel_id, attempt_id=attempt_id),
                        on_progress=_upload_progress,
                    )
                except Exception as upload_error:
                    if self._is_cancelled():
                        self._clear_scheduled_upload_attempt(idx, meta, channel_id, attempt_id=attempt_id)
                        self._js("window.onScheduleUpdated()")
                        return self._cancelled()
                    self._mark_scheduled_upload_failed_for_clip(idx, meta, upload_error, attempt_id=attempt_id)
                    self._js("window.onScheduleUpdated()")
                    raise
                uploaded += 1
                if self._mark_scheduled_uploaded(idx, meta, result, attempt_id=attempt_id):
                    self._js("window.onScheduleUpdated()")

                # Auto-delete from disk after successful upload
                if self._delete_after_upload:
                    self._delete_uploaded_clip(idx, video_path)

            msg = f"Uploaded {uploaded} clip(s)"
            if skipped:
                msg += f"; skipped {skipped} removed/missing clip(s)"
            self._push("upload", 100, msg)
            success = skipped == 0 and uploaded == total
            error_msg = "null" if success else json.dumps(msg)
            self._js(f"window.onPipelineComplete({str(success).lower()}, {uploaded}, {total}, {error_msg})")

        except Exception as e:
            if self._is_cancelled():
                return self._cancelled()
            self._error(f"Upload failed: {e}")
        finally:
            with self._get_state_lock():
                self._processing = False
            if upload_lock:
                try:
                    upload_lock.release()
                except RuntimeError:
                    pass

    # ── Background upload scheduler ──────────────────────────────────────

    def _scheduler_loop(self):
        """Check every 30s for scheduled uploads whose time has arrived."""
        while True:
            with self._get_state_lock():
                if not self._scheduler_running:
                    break
                scheduled_snapshot = list(self._scheduled)
            now = datetime.now()
            changed = False

            for snapshot_item in scheduled_snapshot:
                with self._get_state_lock():
                    item = None
                    snapshot_key = self._schedule_identity_key(snapshot_item)
                    if snapshot_key:
                        item = next(
                            (
                                current
                                for current in self._scheduled
                                if self._schedule_identity_key(current) == snapshot_key
                            ),
                            None,
                        )
                    if item is None and any(current is snapshot_item for current in self._scheduled):
                        item = snapshot_item
                    if item is None:
                        continue
                    if item.get("uploaded"):
                        continue
                    if item.get("scheduler_status") in {"account_disconnected", "upload_outcome_unknown"}:
                        continue
                    if not self._scheduled_retry_due(item, now):
                        continue
                    try:
                        sched_dt = datetime.fromisoformat(f"{item['date']}T{item['time']}")
                    except (KeyError, ValueError):
                        continue

                    if now < sched_dt:
                        continue
                    retrying_failed_upload = str(item.get("scheduler_status") or "").lower() == "upload_failed"
                    if not retrying_failed_upload and self._scheduled_item_missed_upload_window(item, sched_dt, now):
                        if item.get("scheduler_status") != "missed":
                            item["scheduler_status"] = "missed"
                            item["missed_at"] = now.replace(microsecond=0).isoformat()
                            changed = True
                            print("[scheduler] Scheduled upload missed; waiting for manual reschedule/upload")
                        continue
                    clip_idx = self._resolve_clip_index(item)
                    video_path = self._safe_clip_path(self._results[clip_idx]) if clip_idx is not None and 0 <= clip_idx < len(self._results) else None
                    if not video_path:
                        self._scheduled.remove(item)
                        changed = True
                        print("[scheduler] Removed scheduled item for a missing clip")
                        continue
                    item["clipIdx"] = clip_idx
                    meta = self._ensure_schedule_description(dict(item), clip_idx)
                    item.update(meta)
                    title = meta.get("title", f"Viral Clip #{clip_idx + 1}")
                    tags = meta.get("tags", generate_tags())
                    if isinstance(tags, str):
                        tags = [t.strip() for t in tags.split(",") if t.strip()]

                upload_lock = self._get_upload_lock()
                with self._get_state_lock():
                    if self._processing or not upload_lock.acquire(blocking=False):
                        print("[scheduler] Upload already in progress; will retry scheduled item")
                        continue
                    self._processing = True
                    self._cancel = False

                print(f"[scheduler] Uploading Clip {clip_idx + 1}: {title}")
                status_message = json.dumps(f"Uploading: {title}")
                self._js(f"window.onSchedulerStatus({status_message})")
                attempt_id = None
                try:
                    attempt_id = self._begin_scheduled_upload_attempt(
                        clip_idx,
                        meta,
                        trigger="scheduler",
                        channel_id=meta.get("channel_id"),
                    )
                    if not attempt_id:
                        print("[scheduler] Scheduled item changed before upload; will retry later")
                        continue
                    self._js("window.onScheduleUpdated()")
                    result = upload_to_youtube(
                        video_path,
                        title=title,
                        description=meta.get("final_description") or meta.get("description", ""),
                        tags=tags,
                        privacy=meta.get("privacy", "private"),
                        channel_id=meta.get("channel_id"),
                        account_id=meta.get("account_id"),
                        cancel_check=lambda meta=meta, clip_idx=clip_idx: (
                            self._is_cancelled()
                            or not self._scheduled_upload_active(clip_idx, meta, meta.get("channel_id"), attempt_id=attempt_id)
                        ),
                    )
                    if self._mark_scheduled_uploaded(clip_idx, meta, result, trigger="scheduler", attempt_id=attempt_id):
                        changed = True
                        self._js("window.onScheduleUpdated()")
                    print(f"[scheduler] Uploaded: {title}")
                    self._js(f"window.onScheduledUploadDone({clip_idx}, true, null)")

                    # Auto-delete from disk after successful upload
                    with self._get_state_lock():
                        delete_after_upload = bool(self._delete_after_upload)
                    if delete_after_upload:
                        self._delete_uploaded_clip(clip_idx, video_path)

                except Exception as e:
                    print(f"[scheduler] Upload failed: {e}")
                    if self._is_cancelled():
                        self._clear_scheduled_upload_attempt(clip_idx, meta, meta.get("channel_id"), attempt_id=attempt_id)
                    elif self._mark_scheduled_upload_failed_for_clip(clip_idx, meta, e, now, attempt_id=attempt_id):
                        changed = True
                    self._js(f"window.onScheduledUploadDone({clip_idx}, false, `{self._esc(str(e))}`)")
                finally:
                    with self._get_state_lock():
                        self._processing = False
                    try:
                        upload_lock.release()
                    except RuntimeError:
                        pass

            if changed:
                self._save_state()
                self._js("window.onScheduleUpdated()")

            time.sleep(30)

    def _scheduled_retry_due(self, item, now=None) -> bool:
        retry_at = item.get("retry_after")
        if not retry_at:
            return True
        now = _local_naive_datetime(now or datetime.now())
        try:
            retry_dt = _local_naive_datetime(_parse_iso_datetime(retry_at))
            return now >= retry_dt
        except (TypeError, ValueError):
            return True

    def _mark_scheduled_upload_failed(self, item, error, now=None):
        with self._get_state_lock():
            now = _local_naive_datetime(now or datetime.now())
            attempts = int(item.get("failure_count") or 0) + 1
            delay_minutes = min(180, 5 * (2 ** max(0, attempts - 1)))
            item["failure_count"] = attempts
            item["scheduler_status"] = "upload_failed"
            item["last_error"] = str(error)[:500]
            item["last_failed_at"] = now.replace(microsecond=0).isoformat()
            item["retry_after"] = (now + timedelta(minutes=delay_minutes)).replace(microsecond=0).isoformat()
            for key in ("scheduler_note", "upload_attempt_id", "upload_attempt_fingerprint", "upload_attempt_started_at", "upload_attempt_trigger", "upload_unknown_at"):
                item.pop(key, None)

    def _scheduled_item_missed_upload_window(self, item, sched_dt, now=None) -> bool:
        """Return True when an overdue local watcher item should wait for user action."""
        if not isinstance(item, dict):
            return False
        now = _local_naive_datetime(now or datetime.now())
        sched_dt = _local_naive_datetime(sched_dt)
        return now > sched_dt + SCHEDULER_MISSED_GRACE

    def _mark_overdue_schedules_missed(self, now=None) -> bool:
        """Mark overdue local-watcher items before the scheduler tick runs."""
        with self._get_state_lock():
            now = _local_naive_datetime(now or datetime.now())
            changed = False
            for item in self._scheduled:
                if item.get("uploaded"):
                    continue
                if item.get("scheduler_status") in {"missed", "account_disconnected", "upload_failed", "upload_outcome_unknown"}:
                    continue
                try:
                    sched_dt = datetime.fromisoformat(f"{item['date']}T{item['time']}")
                except (KeyError, ValueError):
                    continue
                if self._scheduled_item_missed_upload_window(item, sched_dt, now):
                    item["scheduler_status"] = "missed"
                    item["missed_at"] = now.replace(microsecond=0).isoformat()
                    changed = True
            return changed

    def _delete_uploaded_clip(self, clip_idx, video_path):
        """Delete a clip file from disk after successful upload."""
        try:
            safe_path = self._safe_clip_path(video_path)
            if safe_path:
                deleted_name = safe_path.name
                clip_id = None
                if isinstance(clip_idx, int) and 0 <= clip_idx < len(self._moments):
                    moment = self._moments[clip_idx] if isinstance(self._moments[clip_idx], dict) else {}
                    clip_id = moment.get("clip_id")
                deleted_ok, delete_error = self._unlink_clip_file(safe_path)
                if not deleted_ok:
                    print(f"[cleanup] Failed to delete {safe_path.name}: {delete_error}")
                    return
                self._delete_clip_sidecar(safe_path, reason="uploaded_clip_deleted")
                self._mark_personalization_clips_deleted({clip_id} if clip_id else set(), {deleted_name})
                self._prune_missing_results()
                print(f"[cleanup] Deleted uploaded clip: {deleted_name}")
                payload = {"clipIdx": clip_idx, "clipId": clip_id or "", "filename": deleted_name}
                self._js(f"window.onClipDeleted({json.dumps(payload)})")
        except Exception as e:
            print(f"[cleanup] Failed to delete {video_path.name}: {e}")

    # ── State persistence ────────────────────────────────────────────────

    def _backup_json_file(self, path: Path, label: str) -> Path | None:
        if not path.exists():
            return None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = path.with_name(f"{path.stem}.{label}.{timestamp}{path.suffix}.bak")
        try:
            shutil.copy2(path, backup)
            return backup
        except Exception as e:
            print(f"[state] Failed to back up {path.name}: {e}")
            return None

    def _backup_state_file(self, label: str) -> Path | None:
        backup = self._backup_json_file(STATE_FILE, label)
        if backup:
            print(f"[state] Backed up state before migration: {backup}")
        return backup

    def _write_json_atomic(self, path: Path, data: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def _write_state_atomic(self, data: dict):
        self._write_json_atomic(STATE_FILE, data)

    def _save_personalization(self):
        """Persist feedback history to personalization.json."""
        with self._personalization_lock:
            data = {
                "schema_version": PERSONALIZATION_SCHEMA_VERSION,
                "events": self._personalization.get("events", []),
                "clips": self._personalization.get("clips", {}),
            }
            self._personalization = data
            try:
                self._write_json_atomic(PERSONALIZATION_FILE, data)
            except Exception as e:
                print(f"[!] Failed to save personalization: {e}")

    def _save_voice_profile(self):
        """Persist the local creator voice profile."""
        with self._voice_profile_lock:
            self._voice_profile = sanitize_voice_profile(self._voice_profile)
            try:
                self._write_json_atomic(VOICE_PROFILE_FILE, self._voice_profile)
            except Exception as e:
                print(f"[voice-profile] Failed to save voice profile: {e}")

    def _save_processing_history(self):
        """Persist local timing history used for future run estimates."""
        with self._processing_history_lock:
            runs = self._processing_history.get("runs", [])
            if not isinstance(runs, list):
                runs = []
            data = {
                "schema_version": PROCESSING_HISTORY_SCHEMA_VERSION,
                "runs": runs[-150:],
            }
            self._processing_history = data
            try:
                self._write_json_atomic(PROCESSING_HISTORY_FILE, data)
            except Exception as e:
                print(f"[timing] Failed to save processing history: {e}")

    def _save_run_learning(self):
        """Persist compact local outcome memory used by future clip/montage learning."""
        lock = getattr(self, "_run_learning_lock", threading.RLock())
        self._run_learning_lock = lock
        with lock:
            self._run_learning = sanitize_run_learning(getattr(self, "_run_learning", empty_run_learning()))
            try:
                self._write_json_atomic(RUN_LEARNING_FILE, self._run_learning)
            except Exception as e:
                print(f"[learning] Failed to save run learning: {e}")

    def _load_run_learning(self):
        """Load compact local outcome memory for future clip and montage learning."""
        if not RUN_LEARNING_FILE.exists():
            self._run_learning = empty_run_learning()
            self._save_run_learning()
            return
        lock = getattr(self, "_run_learning_lock", threading.RLock())
        self._run_learning_lock = lock
        with lock:
            try:
                data = json.loads(RUN_LEARNING_FILE.read_text(encoding="utf-8"))
                self._run_learning = sanitize_run_learning(data)
                if data.get("schema_version") != RUN_LEARNING_SCHEMA_VERSION:
                    self._save_run_learning()
                summary = run_learning_redacted_summary(self._run_learning)
                print(
                    "[learning] Restored run learning: "
                    f"{summary.get('run_count', 0)} run(s), "
                    f"{summary.get('event_count', 0)} event(s)"
                )
            except Exception as e:
                backup = self._backup_json_file(RUN_LEARNING_FILE, "corrupt")
                if backup:
                    print(f"[learning] Backed up corrupt run learning file: {backup}")
                print(f"[learning] Failed to load run learning: {e}")
                self._run_learning = empty_run_learning()

    def _record_run_learning_event(self, event: dict):
        if not isinstance(event, dict):
            return
        lock = getattr(self, "_run_learning_lock", threading.RLock())
        self._run_learning_lock = lock
        with lock:
            self._run_learning = append_run_learning_event(
                getattr(self, "_run_learning", empty_run_learning()),
                event,
            )
            self._save_run_learning()

    def _record_run_learning_summary(self, summary: dict):
        if not isinstance(summary, dict):
            return
        lock = getattr(self, "_run_learning_lock", threading.RLock())
        self._run_learning_lock = lock
        with lock:
            self._run_learning = append_run_summary(
                getattr(self, "_run_learning", empty_run_learning()),
                summary,
            )
            self._save_run_learning()

    def _build_run_learning_summary(
        self,
        *,
        timing: dict,
        video_path: Path,
        source_id: str,
        source_stem: str,
        game_title: str,
        settings: dict,
        candidates: list[dict],
        evaluations: list[dict],
        selected: list[dict],
        final_clips: list[dict] | None,
        debug_path: Path,
        status: str,
    ) -> dict:
        selected_snapshots: list[dict] = []
        selected_clip_ids: list[str] = []
        for row in selected or []:
            if not isinstance(row, dict):
                continue
            moment = row.get("moment") if isinstance(row.get("moment"), dict) else {}
            if not moment:
                moment = row.get("selection_moment") if isinstance(row.get("selection_moment"), dict) else {}
            if not isinstance(moment, dict):
                continue
            snapshot = dict(moment)
            snapshot.setdefault("source_id", source_id)
            snapshot.setdefault("source_stem", source_stem)
            snapshot.setdefault("source_path", str(video_path))
            snapshot.setdefault("game_title", game_title)
            snapshot = self._ensure_moment_identity(snapshot, video_path)
            selected_clip_ids.append(snapshot.get("clip_id", ""))
            selected_snapshots.append(compact_clip_snapshot(snapshot))

        feature_status = {}
        if isinstance(settings, dict):
            feature_status = settings.get("local_analysis_feature_statuses")
            if not isinstance(feature_status, dict):
                feature_status = {
                    key: value
                    for key, value in {
                        "visual_diagnostics": settings.get("visual_diagnostics"),
                        "multimodal_analysis": settings.get("multimodal_analysis"),
                        "moment_category_ranking": settings.get("moment_category_ranking"),
                        "ai_moment_ranking": settings.get("ai_moment_ranking"),
                        "voice_profile_ranking": settings.get("voice_profile_ranking"),
                    }.items()
                    if isinstance(value, dict)
                }

        return {
            "run_id": str((timing or {}).get("run_id") or ""),
            "status": status,
            "source_id": source_id,
            "source_stem": source_stem,
            "game_title": game_title,
            "debug_path": str(debug_path),
            "timing": timing or {},
            "settings": settings or {},
            "video_duration_seconds": (timing or {}).get("video_duration_seconds"),
            "candidate_count": len(candidates or []),
            "accepted_candidate_count": sum(1 for item in evaluations or [] if isinstance(item, dict) and item.get("accepted")),
            "selected_count": len(selected or []),
            "rendered_clip_count": len(final_clips or []),
            "selected_clip_ids": [item for item in selected_clip_ids if item],
            "selected": selected_snapshots,
            "final_clips": final_clips or [],
            "feature_status": feature_status,
        }

    def get_run_learning_summary(self):
        """Return a redacted local learning summary for diagnostics/settings UI."""
        lock = getattr(self, "_run_learning_lock", threading.RLock())
        self._run_learning_lock = lock
        with lock:
            data = sanitize_run_learning(getattr(self, "_run_learning", empty_run_learning()))
            try:
                size = RUN_LEARNING_FILE.stat().st_size if RUN_LEARNING_FILE.exists() else 0
            except Exception:
                size = 0
        return {
            "path": str(RUN_LEARNING_FILE),
            "exists": RUN_LEARNING_FILE.exists(),
            "size_bytes": size,
            "local_only": True,
            **run_learning_redacted_summary(data),
        }

    def _load_voice_profile(self):
        """Load the local creator voice profile."""
        if not VOICE_PROFILE_FILE.exists():
            self._voice_profile = empty_voice_profile()
            return
        with self._voice_profile_lock:
            try:
                data = json.loads(VOICE_PROFILE_FILE.read_text(encoding="utf-8"))
                self._voice_profile = sanitize_voice_profile(data)
                if data.get("schema_version") != self._voice_profile.get("schema_version"):
                    self._save_voice_profile()
                print(
                    "[voice-profile] Restored local profile: "
                    f"{self._voice_profile.get('sample_count', 0)} sample(s), "
                    f"enabled={bool(self._voice_profile.get('enabled'))}"
                )
            except Exception as e:
                backup = self._backup_json_file(VOICE_PROFILE_FILE, "corrupt")
                if backup:
                    print(f"[voice-profile] Backed up corrupt profile file: {backup}")
                print(f"[voice-profile] Failed to load voice profile: {e}")
                self._voice_profile = empty_voice_profile()

    def _load_processing_history(self):
        """Load local timing history for processing-time estimates."""
        if not PROCESSING_HISTORY_FILE.exists():
            self._processing_history = self._empty_processing_history()
            if self._backfill_processing_history_from_debug_reports():
                self._save_processing_history()
            return
        with self._processing_history_lock:
            try:
                data = json.loads(PROCESSING_HISTORY_FILE.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("root JSON value is not an object")
                runs = data.get("runs", [])
                if not isinstance(runs, list):
                    runs = []
                self._processing_history = {
                    "schema_version": PROCESSING_HISTORY_SCHEMA_VERSION,
                    "runs": runs[-150:],
                }
                if data.get("schema_version") != PROCESSING_HISTORY_SCHEMA_VERSION:
                    self._save_processing_history()
                print(f"[timing] Restored processing history: {len(runs)} run(s)")
            except Exception as e:
                backup = self._backup_json_file(PROCESSING_HISTORY_FILE, "corrupt")
                if backup:
                    print(f"[timing] Backed up corrupt processing history file: {backup}")
                print(f"[timing] Failed to load processing history: {e}")
                self._processing_history = self._empty_processing_history()

    def _backfill_processing_history_from_debug_reports(self) -> int:
        """Seed timing history from prior run debug files when no history exists."""
        try:
            debug_files = sorted(SUBTITLES_DIR.glob("*_run_debug.json"), key=lambda path: path.stat().st_mtime)
        except Exception:
            return 0
        rows: list[dict] = []
        for debug_path in debug_files[-25:]:
            try:
                data = json.loads(debug_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            try:
                row = self._processing_history_row_from_debug(data, debug_path)
            except Exception as exc:
                print(f"[timing] Skipped malformed run debug history row {debug_path.name}: {exc}")
                continue
            if row:
                rows.append(row)
        if not rows:
            return 0
        with self._processing_history_lock:
            existing = self._processing_history.get("runs", [])
            if not isinstance(existing, list):
                existing = []
            seen = {str(row.get("run_id") or "") for row in existing}
            for row in rows:
                if row.get("run_id") in seen:
                    continue
                existing.append(row)
                seen.add(str(row.get("run_id") or ""))
            self._processing_history = {
                "schema_version": PROCESSING_HISTORY_SCHEMA_VERSION,
                "runs": existing[-150:],
            }
        print(f"[timing] Backfilled processing history from {len(rows)} run debug file(s)")
        return len(rows)

    def _processing_history_row_from_debug(self, data: dict, debug_path: Path) -> dict | None:
        if not isinstance(data, dict):
            return None
        timing = data.get("timing") if isinstance(data.get("timing"), dict) else {}
        settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
        stage_timings = timing.get("stage_timings") if isinstance(timing.get("stage_timings"), dict) else {}
        if not stage_timings:
            stage_timings = data.get("stage_timings") if isinstance(data.get("stage_timings"), dict) else {}
        stage_timings = dict(stage_timings or {})
        scene = data.get("scene_detection") if isinstance(data.get("scene_detection"), dict) else {}
        visual = data.get("visual_diagnostics") if isinstance(data.get("visual_diagnostics"), dict) else {}
        if "scene_detection" not in stage_timings and scene.get("elapsed_seconds") is not None:
            stage_timings["scene_detection"] = scene.get("elapsed_seconds")
        if "visual_analysis" not in stage_timings and visual.get("elapsed_seconds") is not None:
            stage_timings["visual_analysis"] = visual.get("elapsed_seconds")

        elapsed = timing.get("elapsed_seconds")
        try:
            elapsed = float(elapsed)
        except (TypeError, ValueError):
            elapsed = 0.0
        if elapsed <= 0:
            total = 0.0
            for value in stage_timings.values():
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                if number > 0:
                    total += number
            elapsed = total
        if elapsed <= 0:
            return None

        try:
            finished = datetime.fromtimestamp(debug_path.stat().st_mtime, tz=timezone.utc)
            finished_at = finished.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        except Exception:
            finished_at = self._utc_now_label()

        estimated = timing.get("estimated_total_seconds")
        try:
            estimated = float(estimated) if estimated is not None else None
        except (TypeError, ValueError):
            estimated = None
        video_duration = data.get("video_duration") or timing.get("video_duration_seconds")
        try:
            video_duration = float(video_duration or 0.0)
        except (TypeError, ValueError):
            video_duration = 0.0
        clean_stage_timings = {}
        for key, value in stage_timings.items():
            try:
                clean_stage_timings[str(key)] = round(float(value), 3)
            except (TypeError, ValueError):
                continue
        final_clips = data.get("final_clips")
        rendered_clip_count = (
            len(final_clips)
            if isinstance(final_clips, list)
            else _safe_int_value(data.get("rendered_clip_count"), 0)
        )

        row = {
            "schema_version": PROCESSING_HISTORY_SCHEMA_VERSION,
            "run_id": str(data.get("run_id") or debug_path.stem),
            "started_at_utc": str(timing.get("started_at_utc") or ""),
            "finished_at_utc": finished_at,
            "status": str(timing.get("status") or "backfilled"),
            "elapsed_seconds": round(float(elapsed), 3),
            "estimated_total_seconds": round(estimated, 3) if estimated is not None else None,
            "estimate_source": str(timing.get("estimate_source") or "debug_backfill"),
            "estimate_error_seconds": None,
            "video_duration_seconds": round(video_duration, 3),
            "processing_depth": _normalize_processing_depth(settings.get("processing_depth")),
            "detection_preference": normalize_detection_preference(settings.get("detection_preference")),
            "candidate_multiplier": _safe_int_value(settings.get("candidate_multiplier"), 0),
            "candidate_count": _safe_int_value(data.get("candidate_count"), 0),
            "selected_count": _safe_int_value(data.get("selected_count"), 0),
            "rendered_clip_count": rendered_clip_count,
            "settings_fingerprint": {},
            "stage_timings": clean_stage_timings,
            "backfilled_from": debug_path.name,
        }
        if estimated is not None and row["elapsed_seconds"] > 0:
            row["estimate_error_seconds"] = round(row["elapsed_seconds"] - estimated, 3)
            row["estimate_error_ratio"] = round(row["elapsed_seconds"] / max(estimated, 1.0), 4)
        return row

    def _load_personalization(self):
        """Load feedback history from personalization.json."""
        if not PERSONALIZATION_FILE.exists():
            self._personalization = self._empty_personalization()
            self._save_personalization()
            return
        with self._personalization_lock:
            try:
                data = json.loads(PERSONALIZATION_FILE.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("root JSON value is not an object")
                events = data.get("events", [])
                clips = data.get("clips", {})
                if not isinstance(events, list):
                    events = []
                if not isinstance(clips, dict):
                    clips = {}
                self._personalization = {
                    "schema_version": PERSONALIZATION_SCHEMA_VERSION,
                    "events": events,
                    "clips": clips,
                }
                if data.get("schema_version") != PERSONALIZATION_SCHEMA_VERSION:
                    self._save_personalization()
                print(f"[+] Restored personalization: {len(events)} feedback event(s)")
            except Exception as e:
                backup = self._backup_json_file(PERSONALIZATION_FILE, "corrupt")
                if backup:
                    print(f"[personalization] Backed up corrupt feedback file: {backup}")
                print(f"[!] Failed to load personalization: {e}")
                self._personalization = self._empty_personalization()

    def _save_state(self):
        """Persist results, moments, schedule, and settings to JSON."""
        with self._get_state_lock():
            aligned_moments = []
            for i, path in enumerate(self._results):
                moment = self._moments[i] if i < len(self._moments) else {}
                aligned_moments.append(self._ensure_moment_identity(moment, path))
            self._moments = aligned_moments
            self._scheduled = self._normalize_scheduled_items(self._scheduled)
            self._upload_history = self._normalize_upload_history(getattr(self, "_upload_history", []))

            data = {
                "schema_version": STATE_SCHEMA_VERSION,
                "results": [str(p) for p in self._results],
                "moments": self._moments,
                "scheduled": self._scheduled,
                "upload_history": self._upload_history,
                "delete_after_upload": self._delete_after_upload,
                "user_settings": self._user_settings,
                "download_info_by_path": self._normalize_download_info_store(
                    getattr(self, "_download_info_by_path", {})
                ),
                "source_context": {
                    "schema_version": 1,
                    "sources": self._normalize_source_context_store(
                        getattr(self, "_source_context", {})
                    ),
                },
            }
            try:
                self._write_state_atomic(data)
            except Exception as e:
                print(f"[!] Failed to save state: {e}")

    def _reconcile_incomplete_upload_attempts(self, now=None) -> bool:
        """Turn stale persisted uploading markers into a user-reviewed state."""
        with self._get_state_lock():
            now = now or datetime.now(timezone.utc)
            timestamp = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            changed = False
            for item in self._scheduled:
                if item.get("uploaded"):
                    continue
                status = str(item.get("scheduler_status") or "").lower()
                if status != "uploading":
                    continue
                if not item.get("upload_attempt_id"):
                    continue
                item["scheduler_status"] = "upload_outcome_unknown"
                item["scheduler_note"] = "ViriaRevive closed before confirming this upload. Check YouTube Studio before retrying."
                item["upload_unknown_at"] = timestamp
                changed = True
            return changed

    def _load_state(self):
        """Load persisted state from previous session."""
        if not STATE_FILE.exists():
            return
        with self._state_lock:
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    print("[!] Failed to load state: root JSON value is not an object")
                    return

                try:
                    schema_version = int(data.get("schema_version") or 1)
                except (TypeError, ValueError):
                    schema_version = 1

                paths = list(data.get("results", []))
                all_moments = data.get("moments", [])
                if not isinstance(all_moments, list):
                    all_moments = []
                old_scheduled = data.get("scheduled", [])
                if not isinstance(old_scheduled, list):
                    old_scheduled = []
                old_upload_history = data.get("upload_history", [])
                if not isinstance(old_upload_history, list):
                    old_upload_history = []

                self._results = []
                self._moments = []
                index_map: dict[int, int] = {}
                identity_missing = schema_version < STATE_SCHEMA_VERSION
                for i, path in enumerate(paths):
                    safe_path = self._safe_clip_path(path)
                    if not safe_path:
                        continue
                    index_map[i] = len(self._results)
                    moment = all_moments[i] if i < len(all_moments) else {}
                    if not isinstance(moment, dict) or not moment.get("clip_id") or not moment.get("source_id"):
                        identity_missing = True
                    self._results.append(safe_path)
                    self._moments.append(self._ensure_moment_identity(moment, safe_path))

                schedule_missing_identity = any(
                    isinstance(item, dict) and not item.get("clip_id")
                    for item in old_scheduled
                )
                self._scheduled = self._normalize_scheduled_items(old_scheduled, legacy_index_map=index_map)
                stale_upload_attempts = self._reconcile_incomplete_upload_attempts()
                self._upload_history = self._normalize_upload_history(old_upload_history)
                self._delete_after_upload = bool(data.get("delete_after_upload", False))
                self._user_settings = data.get("user_settings", {}) if isinstance(data.get("user_settings", {}), dict) else {}
                self._download_info_by_path = self._normalize_download_info_store(
                    data.get("download_info_by_path", {})
                )
                self._source_context = self._normalize_source_context_store(
                    data.get("source_context", {})
                )

                removed_missing_files = len(self._results) != len(paths)
                removed_scheduled_items = len(self._scheduled) != len(old_scheduled)
                history_missing_schema = bool(old_upload_history) and any(
                    isinstance(item, dict) and not item.get("schema_version")
                    for item in old_upload_history
                )
                source_context_missing = not isinstance(data.get("source_context"), dict)
                needs_rewrite = (
                    schema_version != STATE_SCHEMA_VERSION
                    or identity_missing
                    or schedule_missing_identity
                    or removed_missing_files
                    or removed_scheduled_items
                    or stale_upload_attempts
                    or history_missing_schema
                    or source_context_missing
                )
                if schema_version < STATE_SCHEMA_VERSION:
                    self._backup_state_file(f"pre_v{STATE_SCHEMA_VERSION}")
                if needs_rewrite:
                    self._save_state()

                print(f"[+] Restored state: {len(self._results)} clips, {len(self._scheduled)} scheduled")
                if self._user_settings:
                    print(f"[+] Restored user settings: {list(self._user_settings.keys())}")
            except Exception as e:
                print(f"[!] Failed to load state: {e}")

    def _log_shadow_scoring(self, shadow_scoring: dict):
        if not shadow_scoring:
            return
        if not shadow_scoring.get("has_learning_signals"):
            print(
                f"[rank] Learned scoring checked {shadow_scoring.get('candidate_count', 0)} "
                "candidate(s); no feedback signals yet, base ranking used"
            )
            return

        changes = shadow_scoring.get("top_changes", [])
        changed_count = len(changes)
        learned_add = sum(1 for row in changes if row.get("selection_delta") == "added_by_learning")
        learned_drop = sum(1 for row in changes if row.get("selection_delta") == "dropped_by_learning")
        cap = shadow_scoring.get("learned_selection_max_adjustment", 0)
        changed = "changed selection" if shadow_scoring.get("output_changed") else "kept the same selection"
        print(
            f"[rank] Learned scoring blend: cap ±{cap}, {changed_count} reorder signal(s), "
            f"{learned_add} added, {learned_drop} dropped; {changed}"
        )
        for row in changes[:3]:
            start = row.get("start")
            end = row.get("end")
            delta = row.get("rank_delta")
            current = row.get("baseline_rank")
            shadow = row.get("shadow_rank")
            score = row.get("learned_quality_score", row.get("shadow_score"))
            print(
                f"[rank]   candidate {row.get('candidate_rank')} {start}-{end}s: "
                f"rank {current}->{shadow} (delta {delta}), learned_score={score}"
            )

    def _log_voice_profile_ranking(self, voice_ranking: dict):
        if not voice_ranking:
            return
        if not voice_ranking.get("ranking_enabled"):
            print("[voice-profile] Ranking blend is off; diagnostics only")
            return
        if not voice_ranking.get("has_voice_profile_scores"):
            print("[voice-profile] Ranking blend is on, but no scorable voice features were found")
            return
        changes = voice_ranking.get("top_changes", [])
        voice_add = sum(1 for row in changes if row.get("selection_delta") == "added_by_voice")
        voice_drop = sum(1 for row in changes if row.get("selection_delta") == "dropped_by_voice")
        cap = voice_ranking.get("voice_profile_selection_max_adjustment", 0)
        changed = "changed selection" if voice_ranking.get("output_changed") else "kept the same selection"
        print(
            f"[voice-profile] Ranking blend: cap ±{cap}, {len(changes)} reorder signal(s), "
            f"{voice_add} added, {voice_drop} dropped; {changed}"
        )
        for row in changes[:3]:
            print(
                f"[voice-profile]   candidate {row.get('candidate_rank')} {row.get('start')}-{row.get('end')}s: "
                f"confidence={row.get('voice_confidence')}, voice_score={row.get('voice_profile_quality_score')}, "
                f"rank {row.get('baseline_rank')}->{row.get('voice_rank')}"
            )

    def _log_voice_profile_shadow(self, voice_shadow: dict):
        if not voice_shadow or not voice_shadow.get("has_voice_profile_scores"):
            return
        changes = voice_shadow.get("top_changes", [])
        changed_count = len(changes)
        hypothetical = "would change selection" if voice_shadow.get("hypothetical_selection_changed") else "keeps selection"
        cap = voice_shadow.get("voice_profile_max_adjustment", 0)
        print(
            f"[voice-profile] Shadow scoring: cap ±{cap}, {changed_count} reorder signal(s); "
            f"{hypothetical}; no output changed"
        )
        for row in changes[:3]:
            print(
                f"[voice-profile]   candidate {row.get('candidate_rank')} {row.get('start')}-{row.get('end')}s: "
                f"confidence={row.get('voice_confidence')}, voice_score={row.get('voice_shadow_score')}, "
                f"rank {row.get('current_rank')}->{row.get('shadow_rank')}"
            )

    # ── Progress push helpers ────────────────────────────────────────────

    def _record_pipeline_error(self, error, context: dict | None = None):
        """Persist the last pipeline failure so console truncation does not hide it."""
        payload = {
            "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "error": str(error),
            "error_type": type(error).__name__,
            "context": context or {},
        }
        try:
            self._write_json_atomic(SUBTITLES_DIR / "last_pipeline_error.json", payload)
        except Exception:
            pass

    def _progress_context_from_settings(self, settings, source_name=None):
        raw = {}
        if isinstance(settings, dict) and isinstance(settings.get("progress_context"), dict):
            raw = settings.get("progress_context") or {}
        context: dict[str, object] = {}

        def _short_text(value, limit=180):
            text = str(value or "").strip()
            return text[:limit] if text else ""

        source = (
            _short_text(source_name)
            or _short_text(raw.get("sourceName") or raw.get("source_name"))
            or _short_text(raw.get("sourceLabel") or raw.get("source_label"))
        )
        if source:
            context["sourceName"] = source
        source_url = _short_text(raw.get("sourceUrl") or raw.get("source_url"), limit=300)
        if source_url:
            context["sourceUrl"] = source_url
        for source_key, dest_key in (
            ("batchIndex", "batchIndex"),
            ("batch_index", "batchIndex"),
            ("batchTotal", "batchTotal"),
            ("batch_total", "batchTotal"),
        ):
            if dest_key in context:
                continue
            try:
                value = int(raw.get(source_key))
            except Exception:
                continue
            if value > 0:
                context[dest_key] = value
        return context

    @staticmethod
    def _js_json_arg(value):
        if not value:
            return "null"
        return (
            json.dumps(value, ensure_ascii=True, separators=(",", ":"))
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
        )

    def _push(self, stage, pct, msg, detail=None, context=None):
        detail_arg = "null" if detail is None else f"`{self._esc(detail)}`"
        if context is None and stage != "upload":
            context = getattr(self, "_active_progress_context", None)
        context_arg = self._js_json_arg(context)
        self._js(
            f"window.onPipelineProgress('{stage}', {pct}, `{self._esc(msg)}`, {detail_arg}, {context_arg})"
        )

    def _clip_push(self, num, total, substep, pct, msg):
        self._js(
            f"window.onClipProgress({num}, {total}, '{substep}', {pct}, `{self._esc(msg)}`)"
        )

    def _error(self, msg):
        self._js(f"window.onPipelineComplete(false, 0, 0, `{self._esc(msg)}`)")
        with self._get_state_lock():
            self._processing = False

    def _cancelled(self):
        self._js("window.onPipelineCancelled()")
        with self._get_state_lock():
            self._processing = False

    def _js(self, code):
        """Execute JS in the frontend. Queues calls if window is hidden/minimized."""
        try:
            if self._window:
                self._window.evaluate_js(code)
                return
        except Exception:
            pass
        # Window is hidden or unavailable — queue for when it comes back.
        # Only keep the last progress update per type (avoid flooding the queue)
        # but ALWAYS keep completion/error/cancel callbacks.
        is_progress = "onPipelineProgress" in code or "onClipProgress" in code
        is_console = "onConsoleLog" in code
        if is_progress:
            # Replace previous progress of same type
            self._pending_js = [c for c in self._pending_js
                                if ("onPipelineProgress" not in c and "onClipProgress" not in c)]
        if is_console and len([c for c in self._pending_js if "onConsoleLog" in c]) > 200:
            # Trim old console logs to avoid memory bloat
            non_console = [c for c in self._pending_js if "onConsoleLog" not in c]
            console = [c for c in self._pending_js if "onConsoleLog" in c][-100:]
            self._pending_js = non_console + console
        self._pending_js.append(code)

    def flush_pending_js(self):
        """Called from frontend when window is restored — replay any queued JS calls."""
        pending = list(self._pending_js)
        self._pending_js.clear()
        for code in pending:
            try:
                if self._window:
                    self._window.evaluate_js(code)
            except Exception:
                pass
        return {"flushed": len(pending)}

    @staticmethod
    def _esc(s):
        return (
            str(s)
            .replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("$", "\\$")
            .replace("'", "\\'")
            .replace("\r", "\\r")
            .replace("\n", "\\n")
        )
