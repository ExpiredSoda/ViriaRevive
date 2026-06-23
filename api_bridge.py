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
import socket
import subprocess
import threading
import time
import uuid
import webbrowser
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import yt_dlp

from config import (
    BASE_DIR,
    APP_DATA_DIR,
    BIN_DIR,
    CLIPS_DIR,
    CLIP_DURATION,
    CLIENT_SECRETS_FILE,
    CROP_VERTICAL,
    DOWNLOADS_DIR,
    FFMPEG_PRESET,
    MIN_GAP,
    MUSIC_DIR,
    NUM_CLIPS,
    PERSONALIZATION_FILE,
    PERSONALIZATION_SCHEMA_VERSION,
    PROCESSING_HISTORY_FILE,
    PROCESSING_HISTORY_SCHEMA_VERSION,
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
from detector import find_viral_moments, get_last_scene_detection_diagnostics
from transcriber import transcribe_clip, find_sentence_boundary
from subtitler import (
    generate_subtitles,
    get_available_styles,
    normalize_subtitle_placement,
    resolve_subtitle_placement,
    subtitles_are_enabled,
)
from clipper import (
    extract_clip, extract_audio_clip, ClipResult,
    add_background_music, apply_video_effect, get_effects_list,
)
from cropper import get_crop_params, get_crop_params_dynamic, get_dimensions
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
    apply_voice_profile_scoring,
    build_learning_terms,
    build_learning_status,
    build_ai_moment_ranking_report,
    build_moment_category_ranking_report,
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
    LEARNED_SELECTION_MAX_ADJUSTMENT,
    AI_MOMENT_SELECTION_MAX_ADJUSTMENT,
    MOMENT_CATEGORY_SELECTION_MAX_ADJUSTMENT,
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
    DEFAULT_VIDEO_CATEGORY_ID,
    classify_moment_ai,
    compose_description,
    generated_description_body,
    generate_title,
    generate_tags,
    generate_titles_batch,
    list_ollama_models,
    ensure_model,
    is_ollama_model_ready,
    ollama_status,
    summarize_clip_context,
    recommended_hashtags,
    OLLAMA_DOWNLOAD_URL,
    OLLAMA_WINDOWS_DOCS_URL,
    OLLAMA_INSTALL_SCRIPT_URL,
)
from uploader import (
    upload_to_youtube,
    build_schedule,
    get_youtube_service,
    is_connected,
    disconnect,
    list_channels,
    list_categories,
    add_account,
    list_accounts,
)
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


YOUTUBE_CREDENTIALS_URL = "https://console.cloud.google.com/apis/credentials"
FFMPEG_DOWNLOAD_URL = "https://ffmpeg.org/download.html"
SCHEDULER_MISSED_GRACE = timedelta(minutes=10)
PROCESSING_DEPTHS = {"fast", "balanced", "deep"}
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


def _normalize_bool_setting(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
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
            "moment_category_ranking": False,
            "ai_moment_classification": False,
            "voice_profile_ranking": False,
            "visual_max_candidates": 24,
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
            "moment_category_ranking": True,
            "ai_moment_classification": True,
            "voice_profile_ranking": None,
            "visual_max_candidates": 64,
        }
    multiplier = 6 if preference == "quality" else 5
    return {
        "depth": "balanced",
        "candidate_multiplier": multiplier,
        "scene_mode": "sampled" if duration >= 1200 else "full",
        "candidate_pool_cap": 56,
        "visual_diagnostics": None,
        "moment_category_ranking": True,
        "ai_moment_classification": None,
        "voice_profile_ranking": None,
        "visual_max_candidates": 48,
    }


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


def _audio_stream_selection_summary(audio_source_debug: dict | None, selected_stream=None) -> dict:
    audio_source_debug = audio_source_debug if isinstance(audio_source_debug, dict) else {}
    selection = audio_source_debug.get("stream_selection")
    selection = selection if isinstance(selection, dict) else {}
    resolved_stream = selected_stream
    if resolved_stream is None:
        resolved_stream = audio_source_debug.get("selected_stream")
    summary = {
        "schema_version": selection.get("schema_version", 1),
        "status": selection.get("status") or ("manual" if audio_source_debug.get("mode") == "stream" else "unknown"),
        "mode": selection.get("mode") or audio_source_debug.get("mode") or "auto",
        "selected_stream": resolved_stream,
        "selected_title": selection.get("selected_title"),
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
import io as _io


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

class _SilentHandler(http.server.SimpleHTTPRequestHandler):
    """Serves files from a directory with range support and no logging."""

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range, Content-Type")
        self.send_header("Access-Control-Expose-Headers", "Accept-Ranges, Content-Length, Content-Range")
        self.send_header("Cache-Control", "public, max-age=3600")
        super().end_headers()

    def do_OPTIONS(self):
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
        self._video_port = _start_video_server(CLIPS_DIR)
        self._music_port = _start_video_server(MUSIC_DIR)
        self._scheduler_running = False
        self._delete_after_upload = False   # auto-delete clips after YouTube upload
        self._user_settings: dict = {}      # user settings persisted to disk
        self._pending_js: list[str] = []    # JS calls queued while window was hidden
        self._state_lock = threading.RLock()
        self._upload_lock = threading.Lock()
        self._ollama_install_token: tuple[str, float] | None = None
        self._personalization_lock = threading.RLock()
        self._personalization: dict = self._empty_personalization()
        self._voice_profile_lock = threading.RLock()
        self._voice_profile: dict = empty_voice_profile()
        self._processing_history_lock = threading.RLock()
        self._processing_history: dict = self._empty_processing_history()

        # Install log interceptor so print() output goes to the GUI console
        global _log_bridge
        _log_bridge = self
        _install_log_tee()

        # Load persisted state from previous session
        self._load_state()
        self._load_personalization()
        self._load_voice_profile()
        self._load_processing_history()
        self._cleanup_voice_profile_temp_wavs()

    # ── Exposed: config / deps ───────────────────────────────────────────

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
            "num_clips": NUM_CLIPS,
            "processing_depth": "balanced",
            "detection_preference": "auto",
            "clip_duration": CLIP_DURATION,
            "min_gap": MIN_GAP,
            "whisper_model": WHISPER_MODEL,
            "whisper_language": WHISPER_LANGUAGE or "",
            "subtitle_style": SUBTITLE_STYLE,
            "subtitle_placement": dict(SUBTITLE_PLACEMENT),
            "ffmpeg_preset": FFMPEG_PRESET,
            "video_crf": VIDEO_CRF,
            "crop_vertical": CROP_VERTICAL,
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
            "clips_dir": str(CLIPS_DIR),
            "music_dir": str(MUSIC_DIR),
        }

    def save_settings(self, settings):
        """Persist user settings to disk so they survive restarts."""
        cleaned = dict(settings or {})
        if "detection_preference" in cleaned:
            cleaned["detection_preference"] = normalize_detection_preference(
                cleaned.get("detection_preference")
            )
        if "processing_depth" in cleaned:
            cleaned["processing_depth"] = _normalize_processing_depth(
                cleaned.get("processing_depth")
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

        recommended = pick_voice_stream_ordinal(path)
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

    def _title_context_for_clip(self, clip_index: int) -> dict:
        if clip_index < 0 or clip_index >= len(self._moments):
            return {}
        moment = self._moments[clip_index]
        if not isinstance(moment, dict):
            return {}
        ranker = moment.get("ranker") if isinstance(moment.get("ranker"), dict) else {}
        context = {
            "schema_version": 1,
            "clip_id": moment.get("clip_id"),
            "source_id": moment.get("source_id"),
            "source_path": moment.get("source_path"),
            "source_stem": moment.get("source_stem"),
            "game_title": self._game_title_for_clip(clip_index),
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
            "ranker": {
                "hook_points": ranker.get("hook_points"),
                "weak_points": ranker.get("weak_points"),
                "aftermath_points": ranker.get("aftermath_points"),
                "first_word_start": ranker.get("first_word_start"),
                "last_word_end": ranker.get("last_word_end"),
                "reject_reason": ranker.get("reject_reason"),
            },
        }
        if 0 <= clip_index < len(self._results):
            context["clip_filename"] = self._results[clip_index].name
        return context

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
        sidecar = clip_path.with_suffix(".txt")
        try:
            sidecar.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return str(sidecar)
        except Exception as exc:
            print(f"[metadata] Failed to write sidecar for {clip_path.name}: {exc}")
            return ""

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
        moment["generated_metadata"] = {
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
            "title_context": summarize_clip_context(
                (clip_context or {}).get("transcript", ""),
                game_title,
                clip_context,
            ),
        }

    def generate_titles(self):
        """Generate titles for all clips using LLM (or heuristic fallback).

        If transcripts are missing (e.g. clips from a previous session where
        moments were lost), auto-transcribe the clip audio first.
        """
        from title_generator import DEFAULT_MODEL

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
        title_contexts = [self._title_context_for_clip(i) for i in range(num_clips)]
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
            desc_parts = self._compose_clip_description(title, game_title, clip_context=clip_context)
            description = desc_parts["description"]
            tags = self._tags_for_game(
                game_title,
                transcripts[i] if i < len(transcripts) else "",
                clip_context=clip_context,
            )
            metadata_file = self._write_metadata_sidecar(i, title, game_title, description, tags, clip_context)
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
                "title": title,
                "game_title": game_title,
                "description": description,
                "final_description": description,
                "generated_description": desc_parts["generated_description"],
                "description_custom_text": desc_parts["description_custom_text"],
                "description_auto_hashtags": desc_parts["description_auto_hashtags"],
                "recommended_hashtags": desc_parts["recommended_hashtags"],
                "tags": tags,
                "title_context": summarize_clip_context(transcripts[i], game_title, clip_context),
                "metadata_file": metadata_file,
            })
        self._save_state()
        return {"titles": titles, "metadata": metadata, "llm": llm_available}

    def generate_title_for_clip(self, clip_index):
        """Generate a title for a single clip."""
        # Ensure moments list matches results length
        while len(self._moments) < len(self._results):
            self._moments.append({})

        if clip_index < 0 or clip_index >= len(self._moments):
            return {"title": "", "error": "Invalid clip index"}

        transcript = self._moments[clip_index].get("transcript", "")

        # If no transcript, try to transcribe from the clip file
        if not transcript and clip_index < len(self._results):
            self._backfill_transcript_single(clip_index)
            transcript = self._moments[clip_index].get("transcript", "")

        if not transcript:
            return {"title": "", "error": "No transcript for this clip"}
        clip_context = self._title_context_for_clip(clip_index)
        game_title = clip_context.get("game_title") or self._game_title_for_clip(clip_index)
        title = generate_title(transcript, game_title=game_title, clip_context=clip_context)
        desc_parts = self._compose_clip_description(title, game_title, clip_context=clip_context)
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
        self._save_state()
        return {
            "title": title,
            "description": description,
            "final_description": description,
            "generated_description": desc_parts["generated_description"],
            "description_custom_text": desc_parts["description_custom_text"],
            "description_auto_hashtags": desc_parts["description_auto_hashtags"],
            "tags": tags,
            "game_title": game_title,
            "hashtags": desc_parts["recommended_hashtags"],
            "title_context": summarize_clip_context(transcript, game_title, clip_context),
            "metadata_file": metadata_file,
        }

    def rename_clip(self, clip_index, new_title):
        """Rename a clip file on disk to match a new title.

        Returns the new filename, or error.
        """
        if clip_index < 0 or clip_index >= len(self._results):
            return {"error": "Invalid clip index"}
        old_path = self._results[clip_index]
        if not old_path.exists():
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

        # Avoid collisions
        if new_path.exists() and new_path != old_path:
            counter = 2
            while new_path.exists():
                new_name = f"{safe} ({counter}){ext}"
                new_path = old_path.parent / new_name
                counter += 1

        try:
            old_path.rename(new_path)
            self._results[clip_index] = new_path
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
            from title_generator import DEFAULT_MODEL

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
                title_contexts[i] = self._title_context_for_clip(i)
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
                r = self.rename_clip(i, title)
                ok = "filename" in r
                if ok:
                    renamed += 1
                game_title = game_titles[i] if i < len(game_titles) else ""
                clip_context = title_contexts[i] if i < len(title_contexts) else {}
                desc_parts = self._compose_clip_description(title, game_title, clip_context=clip_context)
                description = desc_parts["description"]
                tags = self._tags_for_game(
                    game_title,
                    transcripts[i] if i < len(transcripts) else "",
                    clip_context=clip_context,
                )
                metadata_file = self._write_metadata_sidecar(i, title, game_title, description, tags, clip_context)
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

    def _backfill_transcripts(self):
        """Transcribe clips that are missing transcripts (e.g. from previous sessions)."""
        print("[title-gen] Backfilling missing transcripts from clip audio...")
        for i, p in enumerate(self._results):
            if i < len(self._moments) and self._moments[i].get("transcript"):
                continue  # already has transcript
            self._backfill_transcript_single(i)
        self._save_state()  # persist backfilled transcripts

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

    def get_ollama_status(self):
        """Return whether Ollama is running and ready for title generation."""
        status = ollama_status()
        install_path = shutil.which("ollama.exe") or shutil.which("ollama")
        if install_path:
            status["install_path"] = str(Path(install_path))
        status["installed"] = bool(status.get("running") or install_path)
        return status

    def open_ollama_folder(self):
        """Open the local Ollama install folder when it can be found."""
        install_path = shutil.which("ollama.exe") or shutil.which("ollama")
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

    def prepare_ollama_install(self):
        """Create a short-lived token after the UI asks for install confirmation."""
        token = uuid.uuid4().hex
        self._ollama_install_token = (token, time.time())
        return {
            "ok": True,
            "token": token,
            "command": "irm https://ollama.com/install.ps1 | iex",
            "url": OLLAMA_DOWNLOAD_URL,
        }

    def install_ollama_with_powershell(self, token=None):
        """Start Ollama's official Windows install script after UI confirmation."""
        expected, created_at = self._ollama_install_token or ("", 0)
        self._ollama_install_token = None
        if not token or token != expected or time.time() - created_at > 120:
            return {"error": "Ollama install confirmation expired. Try again from the app UI."}
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"irm {OLLAMA_INSTALL_SCRIPT_URL} | iex",
        ]
        try:
            subprocess.Popen(command, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        except Exception as e:
            return {"error": str(e), "url": OLLAMA_DOWNLOAD_URL}
        return {
            "ok": True,
            "command": "irm https://ollama.com/install.ps1 | iex",
            "url": OLLAMA_DOWNLOAD_URL,
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
        from title_generator import DEFAULT_MODEL
        model = model or DEFAULT_MODEL
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
            for item in self._scheduled:
                item_account = item.get("account_id")
                item_account_id = str(item_account) if item_account else ""
                if item_account_id in removed_ids or (account_id is None and not item_account_id):
                    item["scheduler_status"] = "account_disconnected"
                    item["scheduler_note"] = "Reconnect YouTube or choose another account before upload"
                    changed = True
            if changed:
                self._save_state()
                self._js("window.onScheduleUpdated()")
        return {"ok": True}

    def youtube_status(self):
        return {"connected": is_connected(), "accounts": list_accounts()}

    def get_channels(self):
        try:
            return {"channels": list_channels()}
        except Exception as e:
            return {"error": str(e), "channels": []}

    def get_categories(self):
        try:
            return {"categories": list_categories()}
        except Exception as e:
            return {
                "error": str(e),
                "categories": [{"id": DEFAULT_VIDEO_CATEGORY_ID, "title": "Gaming"}],
            }

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
        """Return an existing clip path only when it is inside CLIPS_DIR."""
        resolved = self._safe_path_under(CLIPS_DIR, path)
        if resolved and resolved.exists() and resolved.is_file():
            return resolved
        return None

    def _unique_clip_output_path(self, stem: str, clip_num: int) -> Path:
        """Return a clip output path without deleting an existing rendered clip."""
        clean_stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", str(stem or "clip")).strip(" ._") or "clip"
        safe_num = max(1, int(clip_num or 1))
        base = CLIPS_DIR / f"{clean_stem}_viral{safe_num}.mp4"
        if not base.exists():
            return base
        for suffix in range(2, 1000):
            candidate = CLIPS_DIR / f"{clean_stem}_viral{safe_num}_{suffix}.mp4"
            if not candidate.exists():
                return candidate
        return CLIPS_DIR / f"{clean_stem}_viral{safe_num}_{uuid.uuid4().hex[:8]}.mp4"

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
            "prompt_scope": "selected_clips_compact_transcript_ranker_visual_metadata",
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
        game_title = self._infer_game_title_from_path(video_path)
        viral_scores: list[int] = []

        for item in selected:
            moment = item.get("moment") if isinstance(item.get("moment"), dict) else {}
            transcript = moment.get("transcript") or item.get("transcript") or ""
            cache_key = self._ai_moment_cache_key(item)
            cached = classification_cache.get(cache_key) if cache_key else None
            if isinstance(cached, dict):
                classification = dict(cached)
                report["reused_shadow_count"] += 1
            else:
                use_ollama = bool(ollama_ready and report["ollama_attempted_count"] < max_ollama)
                if use_ollama:
                    report["ollama_attempted_count"] += 1
                classification = classify_moment_ai(
                    transcript,
                    game_title=game_title,
                    clip_context=moment,
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

    def _ai_shadow_shortlist(
        self,
        evaluations: list[dict],
        *,
        max_count: int,
        score_key: str,
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
        ordered = sorted(
            accepted,
            key=lambda item: (
                _score(item, score_key, _score(item, "learned_quality_score", _score(item, "quality_score", 0.0))),
                _score(item, "learned_quality_score", _score(item, "quality_score", 0.0)),
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
            "prompt_scope": "deep_pre_final_shadow_shortlist_compact_transcript_ranker_visual_metadata",
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
        game_title = self._infer_game_title_from_path(video_path)
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
                clip_context=moment,
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

    def _ensure_schedule_description(self, item: dict, idx: int) -> dict:
        item = dict(item)
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

        title = str(item.get("title") or f"Clip {idx + 1}")
        game_title = self._schedule_game_title(item, idx)
        clip_context = self._title_context_for_clip(idx)
        generated_text = (
            item.get("description_generated")
            or item.get("generated_description")
            or (self._moments[idx].get("generated_metadata", {}).get("generated_description")
                if idx < len(self._moments) and isinstance(self._moments[idx], dict)
                else None)
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

    def _clip_payload(self, idx: int, path: Path, include_url: bool = True) -> dict:
        moment = self._ensure_moment_identity(
            self._moments[idx] if idx < len(self._moments) else {},
            path,
        )
        if idx < len(self._moments):
            self._moments[idx] = moment
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
            "subtitle_style": moment.get("subtitle_style"),
            "captions_requested": moment.get("captions_requested"),
            "subtitle_enabled": moment.get("subtitle_enabled"),
            "subtitle_generated": moment.get("subtitle_generated"),
            "subtitles_burned": moment.get("subtitles_burned"),
            "subtitle_placement": moment.get("subtitle_placement"),
        }
        if include_url:
            clip["url"] = f"http://127.0.0.1:{self._video_port}/{quote(path.name)}"
        return clip

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

    def _estimate_processing_seconds_from_history(self, depth: str, video_duration: float | None) -> tuple[float | None, str]:
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
        stats = self._processing_history_stats(runs)
        depth_stats = stats.get("by_depth", {}).get(_normalize_processing_depth(depth), {})
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
            "processing_depth": _normalize_processing_depth(row.get("processing_depth")),
            "detection_preference": normalize_detection_preference(row.get("detection_preference")),
            "candidate_multiplier": _safe_int_value(row.get("candidate_multiplier"), 0),
            "candidate_count": _safe_int_value(row.get("candidate_count"), 0),
            "selected_count": _safe_int_value(row.get("selected_count"), 0),
            "rendered_clip_count": _safe_int_value(row.get("rendered_clip_count"), 0),
            "settings_fingerprint": row.get("settings_fingerprint") if isinstance(row.get("settings_fingerprint"), dict) else {},
            "stage_timings": row.get("stage_timings") if isinstance(row.get("stage_timings"), dict) else {},
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
            str(snapshot.get("transcript") or ""),
            categories=snapshot.get("moment_categories") if isinstance(snapshot.get("moment_categories"), dict) else {},
            primary_category=str(snapshot.get("primary_category") or ""),
        )

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
            transcript,
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
            learning_status = build_learning_status(self._personalization)
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
        voice_profile_summary = self._voice_profile_status_payload()
        feature_statuses = _depth_feature_statuses(
            depth,
            depth_profile,
            visual_requested=visual_analysis_enabled,
            ai_requested=ai_moment_labels_enabled,
            category_requested=moment_label_ranking_enabled,
            voice_requested=voice_profile_ranking_enabled,
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
            "local_analysis": {
                "processing_depth": depth,
                "visual_analysis_enabled": visual_analysis_enabled,
                "ai_moment_labels_enabled": ai_moment_labels_enabled,
                "moment_label_ranking_enabled": moment_label_ranking_enabled,
                "voice_profile_ranking_enabled": voice_profile_ranking_enabled,
                "depth_preset_controls": {
                    "visual_analysis": depth_profile.get("visual_diagnostics"),
                    "ai_moment_labels": depth_profile.get("ai_moment_classification"),
                    "moment_label_ranking": depth_profile.get("moment_category_ranking"),
                    "voice_profile_ranking": depth_profile.get("voice_profile_ranking"),
                },
                "feature_statuses": feature_statuses,
                "selection_caps": {
                    "feedback_learning": round(LEARNED_SELECTION_MAX_ADJUSTMENT, 4),
                    "moment_label_ranking": round(MOMENT_CATEGORY_SELECTION_MAX_ADJUSTMENT, 4),
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
                changed = True
            if changed:
                self._save_personalization()

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
        clips = []
        for i, p in enumerate(self._results):
            clips.append(self._clip_payload(i, p, include_url=True))
        return {"clips": clips, "moments": self._moments}

    def open_output_folder(self):
        try:
            os.startfile(str(CLIPS_DIR))
        except Exception:
            pass
        return {"ok": True}

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
                rel = p.resolve().relative_to(CLIPS_DIR.resolve()).as_posix()
                return {"url": f"http://127.0.0.1:{self._video_port}/{quote(rel)}"}
        return {"url": None}

    def get_subtitle_preview_url(self):
        """Return the newest tracked clip URL for the settings subtitle preview."""
        self._prune_missing_results()
        for idx in range(len(self._results) - 1, -1, -1):
            p = self._safe_clip_path(self._results[idx])
            if not p:
                continue
            rel = p.resolve().relative_to(CLIPS_DIR.resolve()).as_posix()
            return {
                "url": f"http://127.0.0.1:{self._video_port}/{quote(rel)}",
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
                p.unlink()
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
                return {"ok": True}
            except Exception as e:
                return {"error": str(e)}
        return {"error": "Invalid clip index"}

    def delete_library_file(self, filename):
        """Delete a video file from the clips folder by filename."""
        target = self._safe_child_path(CLIPS_DIR, filename)
        if target and target.exists() and target.is_file():
            try:
                removed_ids = {
                    self._moments[i].get("clip_id")
                    for i, p in enumerate(self._results)
                    if p.name == target.name and i < len(self._moments)
                }
                target.unlink()
                # Also remove from results if it was there
                keep = [i for i, p in enumerate(self._results) if p.name != target.name]
                self._results = [self._results[i] for i in keep]
                self._moments = [self._moments[i] for i in keep if i < len(self._moments)]
                self._scheduled = [
                    s for s in self._scheduled
                    if s.get("clip_id") not in removed_ids and s.get("clip_filename") != target.name
                ]
                self._scheduled = self._normalize_scheduled_items(self._scheduled)
                self._mark_personalization_clips_deleted(removed_ids, {target.name})
                self._save_state()
                return {"ok": True}
            except Exception as e:
                return {"error": str(e)}
        return {"error": "File not found"}

    # ── Exposed: library (all videos) ────────────────────────────────────

    def list_all_clips(self):
        """List all video files in the clips directory."""
        self._prune_missing_results()
        clips = []
        total_size = 0
        _exts = {'.mp4', '.mkv', '.avi', '.mov', '.webm'}
        known = {
            p.resolve(): i
            for i, p in enumerate(self._results)
            if p.exists()
        }
        if CLIPS_DIR.exists():
            # Single stat() per file — cache the result
            entries = []
            for p in CLIPS_DIR.iterdir():
                safe_path = self._safe_path_under(CLIPS_DIR, p)
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
                    "url": f"http://127.0.0.1:{self._video_port}/{quote(p.name)}",
                }
                known_idx = known.get(p.resolve())
                if known_idx is not None:
                    moment = self._ensure_moment_identity(
                        self._moments[known_idx] if known_idx < len(self._moments) else {},
                        p,
                    )
                    if known_idx < len(self._moments):
                        self._moments[known_idx] = moment
                    clip.update({
                        "clip_id": moment.get("clip_id"),
                        "source_id": moment.get("source_id"),
                        "source_stem": moment.get("source_stem", ""),
                        "primary_category": moment.get("primary_category"),
                        "ai_moment_classification": moment.get("ai_moment_classification"),
                        "subtitle_style": moment.get("subtitle_style"),
                        "captions_requested": moment.get("captions_requested"),
                        "subtitle_enabled": moment.get("subtitle_enabled"),
                        "subtitle_generated": moment.get("subtitle_generated"),
                        "subtitles_burned": moment.get("subtitles_burned"),
                        "subtitle_placement": moment.get("subtitle_placement"),
                    })
                else:
                    moment = self._ensure_moment_identity({}, p)
                    clip.update({
                        "clip_id": moment.get("clip_id"),
                        "source_id": moment.get("source_id"),
                        "source_stem": moment.get("source_stem", ""),
                        "primary_category": moment.get("primary_category"),
                        "ai_moment_classification": moment.get("ai_moment_classification"),
                        "subtitle_style": moment.get("subtitle_style"),
                        "captions_requested": moment.get("captions_requested"),
                        "subtitle_enabled": moment.get("subtitle_enabled"),
                        "subtitle_generated": moment.get("subtitle_generated"),
                        "subtitles_burned": moment.get("subtitles_burned"),
                        "subtitle_placement": moment.get("subtitle_placement"),
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
        _exts = {'.mp4', '.mkv', '.avi', '.mov', '.webm'}
        existing = {p.resolve() for p in self._results if p.exists()}
        added = 0

        if CLIPS_DIR.exists():
            safe_entries = []
            for p in CLIPS_DIR.iterdir():
                safe_path = self._safe_path_under(CLIPS_DIR, p)
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
            self._scheduled = self._normalize_scheduled_items(scheduled_list or [])
            self._save_state()
        return {"ok": True}

    def get_all_scheduled(self):
        """Return the persisted scheduled list."""
        with self._state_lock:
            self._prune_missing_results()
            self._scheduled = self._normalize_scheduled_items(self._scheduled)
            return {"scheduled": self._scheduled}

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
            if publish_at and privacy == "public" and publish_at <= now_utc:
                label = meta.get("title") or meta.get("clip_filename") or f"clip {original_index + 1}"
                raise ValueError(f"Scheduled publish time is in the past for {label}")
        return ordered

    def _get_upload_lock(self):
        lock = getattr(self, "_upload_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._upload_lock = lock
        return lock

    def start_upload(self, clips_metadata, schedule_start, interval_hours, channel_id=None):
        """Upload clips with per-clip metadata.

        clips_metadata: list of {index, clip_id, source_id, title, description, tags, category_id, privacy, publish_at}
        channel_id: YouTube channel ID to upload to (from get_channels())
        """
        if self._processing:
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
        self._processing = True
        self._cancel = False
        threading.Thread(
            target=self._run_upload,
            args=(clips_metadata, schedule_start, interval_hours, channel_id, upload_lock),
            daemon=True,
        ).start()
        return {"ok": True}

    def upload_single_clip(self, clip_index, meta, channel_id=None):
        """Upload a single clip immediately (used by background scheduler)."""
        if clip_index >= len(self._results):
            return {"error": "Invalid clip index"}
        video_path = self._safe_clip_path(self._results[clip_index])
        if not video_path:
            return {"error": "Clip file not found"}
        try:
            normalized_meta = self._ensure_schedule_description(dict(meta or {}), clip_index)
            upload_to_youtube(
                video_path,
                title=normalized_meta.get("title", f"Viral Clip #{clip_index + 1}"),
                description=normalized_meta.get("final_description") or normalized_meta.get("description", ""),
                tags=normalized_meta.get("tags", generate_tags()),
                category_id=DEFAULT_VIDEO_CATEGORY_ID,
                privacy=normalized_meta.get("privacy", "private"),
                channel_id=normalized_meta.get("channel_id") or channel_id,
                account_id=normalized_meta.get("account_id"),
                cancel_check=lambda: self._cancel,
            )
            return {"ok": True}
        except Exception as e:
            return {"error": str(e)}

    # ── Exposed: background scheduler ────────────────────────────────────

    def start_scheduler(self):
        """Start the background upload scheduler thread."""
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
        clips = []
        for i, p in enumerate(self._results):
            clips.append(self._clip_payload(i, p, include_url=False))
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "clips": clips,
            "moments": self._moments[:len(self._results)],
            "scheduled": self._scheduled,
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
            candidate.setdefault("start", start)
            candidate.setdefault("end", end)
            candidate.setdefault("duration", end - start)
            item = {
                "candidate": candidate,
                "moment": moment,
                "words": [],
                "quality_score": row.get("base_quality_score", row.get("quality_score")),
                "selection_rank_score": row.get("selection_rank_score"),
                "selection_score_source": row.get("selection_score_source", "quality_score"),
                "shadow_scoring": row.get("shadow_scoring") if isinstance(row.get("shadow_scoring"), dict) else {},
                "moment_category_scoring": row.get("moment_category_scoring") if isinstance(row.get("moment_category_scoring"), dict) else {},
                "voice_scoring": row.get("voice_scoring") if isinstance(row.get("voice_scoring"), dict) else {},
                "voice_profile": row.get("voice_profile") if isinstance(row.get("voice_profile"), dict) else None,
            }
            for key in (
                "primary_category",
                "moment_categories",
                "visual_diagnostics",
                "ai_moment_classification",
                "commentary_guard",
                "music_lyrics_guard",
                "music_lyrics_penalty",
                "learned_adjustment",
                "learned_quality_score",
                "moment_category_quality_score",
                "moment_category_adjustment",
                "voice_profile_quality_score",
                "voice_adjustment",
            ):
                if row.get(key) is not None:
                    item[key] = row.get(key)
                    moment.setdefault(key, row.get(key))
            items.append(item)
        items.sort(key=lambda item: (int(item["moment"].get("start", 0)), int(item["moment"].get("end", 0))))
        return items

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
            allow_stream_retry = bool(audio_source_debug.get("alternate_stream_retry", True))
            commentary_guard_enabled = bool(audio_source_debug.get("single_track_commentary_guard"))
            commentary_guard_policy = normalize_commentary_subtitle_policy(audio_source_debug.get("subtitle_policy"))

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

            clip_duration = int(settings.get("clip_duration", CLIP_DURATION))
            detection_preference = normalize_detection_preference(settings.get("detection_preference"))
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

            run_warnings = list(payload.get("warnings") or [])
            run_warnings.append("rendered_from_candidate_debug")
            self._push("candidates", 100, f"Loaded {len(selected)} selected clips from saved analysis")

            moments = [item["moment"] for item in selected]
            for item, m in zip(selected, moments):
                m["source_id"] = source_id
                m["source_path"] = str(video_path)
                m["source_stem"] = source_stem
                m["game_title"] = self._infer_game_title_from_path(video_path)
                m["clip_id"] = self._clip_id_for(m)
                m["audio_source"] = {
                    "mode": audio_source_debug.get("mode", "auto"),
                    "selected_stream": m.get("speech_stream", speech_stream),
                    "selected_reason": audio_source_debug.get("selected_reason"),
                    "selected_confidence": audio_source_debug.get("selected_confidence"),
                    "runner_up_stream": audio_source_debug.get("runner_up_stream"),
                    "stream_count": audio_source_debug.get("stream_count"),
                    "render_audio": audio_source_debug.get("render_audio", "all_source_streams_mixed"),
                    "alternate_stream_retry": allow_stream_retry,
                    "subtitle_policy": commentary_guard_policy,
                    "single_track_commentary_guard": commentary_guard_enabled,
                    "stream_selection": _audio_stream_selection_summary(
                        audio_source_debug,
                        selected_stream=m.get("speech_stream", speech_stream),
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
                final_stream = m.get("speech_stream", speech_stream)
                final_retry_report = None
                if extract_audio_clip(video_path, start, final_probe_end, wav, audio_stream=final_stream):
                    final_words = transcribe_clip(wav, model_size=model, language=language)
                if allow_stream_retry and needs_stream_retry(final_words, final_probe_end - start):
                    retry_words, retry_stream = self._try_alternate_audio_streams(
                        video_path, start, final_probe_end, wav, model, language,
                        idx, total, final_stream, return_stream=True,
                        subtitle_policy=commentary_guard_policy,
                    )
                    final_retry_report = getattr(self, "_last_stream_retry", None)
                    if retry_words:
                        final_words = retry_words
                        final_stream = retry_stream
                final_voice_profile_score = self._voice_profile_score_for_wav(wav, voice_profile_snapshot)
                words = final_words
                if final_words:
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
                    )
                    refined_moment = final_eval.get("moment") or {}
                    try:
                        trim_start = int(refined_moment.get("start", start))
                        trim_end = int(refined_moment.get("end", end))
                    except (TypeError, ValueError):
                        trim_start, trim_end = start, end
                    words = (
                        _subtitle_words_for_render_start(final_eval.get("words") or [], trim_start, selected_start)
                        if final_eval.get("words")
                        else final_words
                    )
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
                m["transcript"] = transcript or m.get("transcript", "")
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
                    "single_track_commentary_guard": commentary_guard_enabled,
                    "stream_selection": _audio_stream_selection_summary(
                        audio_source_debug,
                        selected_stream=final_stream,
                    ),
                }
                m["stream_selection"] = m["audio_source"]["stream_selection"]

                crop_params = None
                crop_w, crop_h = get_dimensions(video_path)
                if crop_vertical:
                    self._clip_push(idx, total, "audio", 100, f"Clip {idx}/{total}: Tracking speakers...")
                    try:
                        crop_params = get_crop_params_dynamic(video_path, start, end)
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
                    m["game_title"] = self._infer_game_title_from_path(video_path)
                    m["clip_id"] = self._clip_id_for(m, clip_result.path)
                    if clip_result.warning:
                        m["render_warning"] = clip_result.warning
                        run_warnings.append(f"clip_{idx}_{clip_result.warning}")
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
                        "selection_primary_category": selection_primary_category,
                        "selection_moment_categories": selection_moment_categories,
                        "ranking_primary_category": ranking_primary_category,
                        "ranking_moment_categories": ranking_moment_categories,
                        "final_primary_category": final_primary_category,
                        "final_moment_categories": final_moment_categories,
                        "primary_category": final_primary_category,
                        "moment_categories": final_moment_categories,
                        "visual_diagnostics": m.get("visual_diagnostics") or item.get("visual_diagnostics"),
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

            self._results.extend(done)
            self._moments.extend(done_moments)
            recovered = dict(payload)
            recovered["debug_stage"] = "run_post_render"
            recovered["final_render_metadata_included"] = True
            recovered["recovered_from_candidate_debug"] = True
            recovered["source_candidate_debug"] = str(debug_path)
            recovered["final_clips"] = final_clip_debug
            recovered["warnings"] = run_warnings
            recovered["rendered_clip_count"] = len(done)
            recovered["stage_timings"] = dict(stage_timings)
            run_debug_path = debug_path.with_name(debug_path.name.replace("_candidate_debug.json", "_run_debug.json"))
            try:
                self._write_json_atomic(run_debug_path, recovered)
                print(f"[rank] Recovered run debug saved: {run_debug_path}")
            except Exception as e:
                print(f"[rank] Failed to save recovered run debug: {e}")
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
        pipeline_started_monotonic = time.monotonic()
        pipeline_started_at = self._utc_now_label()
        stage_timings: dict[str, float] = {}
        estimate_total_seconds = None
        estimate_source = "not_estimated"
        self._active_progress_context = self._progress_context_from_settings(settings)
        try:
            num_clips_raw = settings.get("num_clips", NUM_CLIPS)
            auto_clips = num_clips_raw == "auto"
            num_clips = NUM_CLIPS if auto_clips else int(num_clips_raw)
            processing_depth = _normalize_processing_depth(settings.get("processing_depth"))
            detection_preference = normalize_detection_preference(settings.get("detection_preference"))
            quality_floor = quality_floor_for_preference(detection_preference)
            print(
                f"[*] Pipeline settings: num_clips_raw={num_clips_raw!r}, "
                f"auto_clips={auto_clips}, num_clips={num_clips}, "
                f"processing_depth={processing_depth}, "
                f"detection_preference={detection_preference}, quality_floor={quality_floor:.2f}"
            )
            clip_duration = int(settings.get("clip_duration", CLIP_DURATION))
            min_gap = int(settings.get("min_gap", MIN_GAP))
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
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            run_warnings: list[str] = []

            # ── 1. Download ──────────────────────────────────────────
            if self._cancel:
                return self._cancelled()
            self._push("download", 0, "Downloading video...")

            download_started = time.monotonic()
            video_path = self._download_with_progress(url)
            stage_timings["download"] = round(time.monotonic() - download_started, 3)
            source_id = self._source_id_for(video_path)
            source_stem = video_path.stem[:50]
            self._active_progress_context = self._progress_context_from_settings(
                settings,
                source_name=video_path.name,
            )
            source_audio_streams = [_public_audio_stream(s) for s in get_audio_streams(video_path)]
            source_audio_ordinals = {int(s["ordinal"]) for s in source_audio_streams}
            requested_stream = audio_source.get("stream")
            forced_speech_stream = None
            if audio_source["mode"] == "stream":
                if requested_stream in source_audio_ordinals:
                    forced_speech_stream = int(requested_stream)
                else:
                    run_warnings.append("audio_source_stream_unavailable")
                    print(
                        "[audio] Requested transcription stream "
                        f"0:a:{requested_stream} is unavailable; falling back to auto"
                    )
            allow_stream_retry = forced_speech_stream is None
            audio_source_debug = {
                "mode": audio_source["mode"],
                "requested_stream": requested_stream,
                "selected_stream": forced_speech_stream,
                "stream_count": len(source_audio_streams),
                "streams": source_audio_streams,
                "render_audio": "all_source_streams_mixed",
                "alternate_stream_retry": allow_stream_retry,
                "subtitle_policy": audio_source.get("subtitle_policy", "creator"),
                "single_track_commentary_guard": bool(
                    audio_source.get("commentary_guard") and len(source_audio_streams) == 1
                ),
            }
            commentary_guard_enabled = bool(audio_source_debug["single_track_commentary_guard"])
            commentary_guard_policy = audio_source_debug["subtitle_policy"]
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
            estimate_total_seconds, estimate_source = self._estimate_processing_seconds_from_history(
                processing_depth,
                vid_duration,
            )
            if estimate_total_seconds:
                print(
                    "[timing] Local estimate for this run: "
                    f"{estimate_total_seconds:.0f}s ({estimate_source})"
                )
                self._active_progress_context = {
                    **(self._active_progress_context or {}),
                    "estimatedTotalSeconds": round(float(estimate_total_seconds), 3),
                    "estimateSource": estimate_source,
                    "estimateStartedAt": time.time(),
                }
            if depth_profile["visual_diagnostics"] is not None:
                visual_diagnostics_enabled = bool(depth_profile["visual_diagnostics"])
            if depth_profile["ai_moment_classification"] is not None:
                ai_moment_classification_enabled = bool(depth_profile["ai_moment_classification"])
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
            if forced_speech_stream is not None:
                speech_stream = forced_speech_stream
                audio_source_debug["stream_selection"] = {
                    "schema_version": 1,
                    "status": "forced",
                    "mode": "manual_stream",
                    "selection_impact": "user_selected_stream",
                    "selected_stream": speech_stream,
                    "selected_title": next(
                        (
                            stream.get("title")
                            for stream in source_audio_streams
                            if int(stream.get("ordinal", -1)) == speech_stream
                        ),
                        None,
                    ),
                    "selected_reason": "user_selected_stream",
                    "runner_up_stream": None,
                    "confidence": 1.0,
                    "stream_profiles": [],
                }
                self._push("detect", 62, f"Using selected transcription track 0:a:{speech_stream}")
                print(f"[audio] User-selected transcription stream: 0:a:{speech_stream}")
            else:
                try:
                    speech_stream = select_speech_stream(
                        video_path, candidates, candidate_model, language, SUBTITLES_DIR
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
            candidate_total = len(candidates)
            self._push("candidates", 0, f"Analyzing {candidate_total} candidate moments...")

            candidate_analysis_started = time.monotonic()
            for idx, candidate in enumerate(candidates, 1):
                if self._cancel:
                    return self._cancelled()
                start = int(candidate["start"])
                extended_end = min(int(candidate["end"]) + probe_buffer, int(vid_duration))
                wav = SUBTITLES_DIR / f"{stem}_probe_{idx}.wav"
                wav.unlink(missing_ok=True)

                pct = int((idx - 1) / max(candidate_total, 1) * 100)
                self._push("candidates", pct, f"Analyzing candidate {idx}/{candidate_total} before rendering...")

                words = []
                used_stream = speech_stream
                retry_report = None
                if extract_audio_clip(video_path, start, extended_end, wav, audio_stream=speech_stream):
                    words = transcribe_clip(wav, model_size=candidate_model, language=language)

                if allow_stream_retry and needs_stream_retry(words, extended_end - start):
                    words, alt_stream = self._try_alternate_audio_streams(
                        video_path, start, extended_end, wav, candidate_model, language,
                        idx, candidate_total, speech_stream, return_stream=True,
                        progress_stage="candidates", progress_percent=pct,
                        subtitle_policy=commentary_guard_policy,
                    )
                    retry_report = getattr(self, "_last_stream_retry", None)
                    if alt_stream is not None:
                        used_stream = alt_stream
                voice_profile_score = self._voice_profile_score_for_wav(wav, voice_profile_snapshot)

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
                )
                if retry_report:
                    evaluation["stream_retry"] = retry_report
                    evaluation["moment"]["stream_retry"] = retry_report
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
            apply_learned_scoring(
                evaluations,
                personalization_snapshot,
                source_id=source_id,
                source_stem=source_stem,
            )
            learned_selected = select_best_candidates(
                evaluations,
                num_clips,
                min_gap=max(8, min_gap),
                score_key="learned_quality_score",
            )
            shadow_scoring = build_shadow_scoring_report(
                evaluations,
                learned_selected,
                personalization_snapshot,
                source_id=source_id,
                source_stem=source_stem,
                max_count=num_clips,
                min_gap=max(8, min_gap),
            )
            category_scoring = apply_moment_category_scoring(
                evaluations,
                enabled=moment_category_ranking_enabled,
                score_key="learned_quality_score",
                max_count=num_clips,
                min_gap=max(8, min_gap),
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
                    min_gap=max(8, min_gap),
                    score_key=category_score_key,
                )
            moment_category_ranking = build_moment_category_ranking_report(
                evaluations,
                learned_selected,
                category_selected,
                enabled=moment_category_ranking_enabled,
                max_count=num_clips,
                min_gap=max(8, min_gap),
                score_key="learned_quality_score",
                category_score_key="moment_category_quality_score",
            )
            ai_shadow_enabled = bool(processing_depth == "deep" and ai_moment_classification_enabled)
            ai_shadow_max_count = min(
                16,
                max(8, int(num_clips or 0) * 2),
                sum(1 for item in evaluations if item.get("accepted")),
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
                    min_gap=max(8, min_gap),
                    score_key=ai_score_key,
                )
            ai_moment_ranking = build_ai_moment_ranking_report(
                evaluations,
                category_selected,
                ai_selected,
                enabled=ai_shadow_enabled,
                max_count=num_clips,
                min_gap=max(8, min_gap),
                score_key=category_score_key,
                ai_score_key="ai_moment_quality_score",
            )
            voice_scoring = apply_voice_profile_scoring(
                evaluations,
                voice_profile_debug,
                score_key=ai_score_key,
            )
            selection_score_key = (
                "voice_profile_quality_score"
                if voice_scoring.get("ranking_enabled") and voice_scoring.get("has_voice_profile_scores")
                else ai_score_key
            )
            selected = ai_selected
            if selection_score_key == "voice_profile_quality_score":
                selected = select_best_candidates(
                    evaluations,
                    num_clips,
                    min_gap=max(8, min_gap),
                    score_key=selection_score_key,
                )
            voice_profile_ranking = build_voice_profile_ranking_report(
                evaluations,
                ai_selected,
                selected,
                voice_profile_debug,
                max_count=num_clips,
                min_gap=max(8, min_gap),
                score_key=ai_score_key,
                voice_score_key="voice_profile_quality_score",
            )
            voice_profile_shadow = build_voice_profile_shadow_report(
                evaluations,
                ai_selected,
                max_count=num_clips,
                min_gap=max(8, min_gap),
                score_key=ai_score_key,
            )
            remaining_ai_ollama = max(
                0,
                min(8, len(selected)) - int(ai_moment_classification_shadow.get("ollama_attempted_count") or 0),
            )
            ai_moment_classification_report = self._classify_selected_moments(
                selected,
                video_path,
                enabled=ai_moment_classification_enabled,
                max_ollama=remaining_ai_ollama,
                classification_cache=ai_shadow_cache,
            )
            scene_feature_status = local_analysis_feature_statuses.get("scene_detection", {})
            visual_feature_status = local_analysis_feature_statuses.get("visual_analysis", {})
            ai_feature_status = local_analysis_feature_statuses.get("ai_moment_labels", {})
            category_feature_status = local_analysis_feature_statuses.get("moment_label_ranking", {})
            voice_feature_status = local_analysis_feature_statuses.get("voice_profile_ranking", {})
            if scene_feature_status.get("inactive_reason"):
                scene_detection["skip_reason"] = scene_feature_status.get("inactive_reason")
                scene_detection["reason"] = scene_feature_status.get("reason")
            if visual_feature_status.get("inactive_reason"):
                visual_diagnostics_report["disabled_reason"] = visual_feature_status.get("inactive_reason")
                visual_diagnostics_report["reason"] = visual_feature_status.get("reason")
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
                "num_clips": num_clips,
                "detection_preference": detection_preference,
                "quality_floor": quality_floor,
                "processing_depth": processing_depth,
                "processing_depth_profile": depth_profile,
                "local_analysis_feature_statuses": local_analysis_feature_statuses,
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
                        "candidate_whisper_model": candidate_model,
                        "scene_mode": scene_mode,
                        "candidate_pool_cap": candidate_pool_cap,
                        "visual_analysis": bool(visual_diagnostics_enabled),
                        "ai_moment_labels": bool(ai_moment_classification_enabled),
                        "ai_moment_ranking": bool(ai_moment_ranking.get("ranking_enabled")),
                        "moment_label_ranking": bool(moment_category_ranking_enabled),
                        "voice_profile_ranking": bool(voice_profile_ranking_enabled),
                        "subtitle_style": style,
                    },
                    "stage_timings": dict(stage_timings),
                }
                return payload

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
                    visual_diagnostics=visual_diagnostics_report,
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
                        visual_diagnostics=visual_diagnostics_report,
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
                m["game_title"] = self._infer_game_title_from_path(video_path)
                m["clip_id"] = self._clip_id_for(m)
                m["audio_source"] = {
                    "mode": audio_source_debug.get("mode"),
                    "selected_stream": m.get("speech_stream", speech_stream),
                    "selected_reason": audio_source_debug.get("selected_reason"),
                    "selected_confidence": audio_source_debug.get("selected_confidence"),
                    "runner_up_stream": audio_source_debug.get("runner_up_stream"),
                    "stream_count": audio_source_debug.get("stream_count"),
                    "render_audio": audio_source_debug.get("render_audio"),
                    "alternate_stream_retry": audio_source_debug.get("alternate_stream_retry"),
                    "subtitle_policy": audio_source_debug.get("subtitle_policy"),
                    "single_track_commentary_guard": audio_source_debug.get("single_track_commentary_guard"),
                    "stream_selection": _audio_stream_selection_summary(
                        audio_source_debug,
                        selected_stream=m.get("speech_stream", speech_stream),
                    ),
                }
                m["stream_selection"] = m["audio_source"]["stream_selection"]
                m["subtitle_style"] = style
                m["captions_requested"] = bool(subtitle_enabled)
                m["subtitle_enabled"] = bool(subtitle_enabled)
                m["voice_profile"] = item.get("voice_profile") or m.get("voice_profile")
            self._push("candidates", 100, f"Selected {len(moments)} good clips from {len(candidates)} candidates")
            self._js(f"window.onMomentsDetected({json.dumps(moments)})")

            # ── 4. Render accepted clips ──────────────────────────────
            done: list[Path] = []
            done_moments: list[dict] = []
            final_clip_debug: list[dict] = []
            total = len(selected)

            for idx, item in enumerate(selected, 1):
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
                clip_num = idx
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
                final_stream = m.get("speech_stream", speech_stream)
                final_retry_report = None
                if extract_audio_clip(video_path, start, final_probe_end, wav, audio_stream=final_stream):
                    final_words = transcribe_clip(wav, model_size=model, language=language)
                if allow_stream_retry and needs_stream_retry(final_words, final_probe_end - start):
                    retry_words, retry_stream = self._try_alternate_audio_streams(
                        video_path, start, final_probe_end, wav, model, language,
                        clip_num, total, final_stream, return_stream=True,
                        subtitle_policy=commentary_guard_policy,
                    )
                    final_retry_report = getattr(self, "_last_stream_retry", None)
                    if retry_words:
                        final_words = retry_words
                        final_stream = retry_stream
                final_voice_profile_score = self._voice_profile_score_for_wav(wav, voice_profile_snapshot)

                if final_words:
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
                    )
                    final_eval["voice_profile"] = final_voice_profile_score
                    final_eval["moment"]["voice_profile"] = final_voice_profile_score
                    if final_retry_report:
                        final_eval["stream_retry"] = final_retry_report
                        final_eval["moment"]["stream_retry"] = final_retry_report
                    if final_eval["words"]:
                        refined_moment = final_eval.get("moment") or {}
                        try:
                            trim_start = int(refined_moment.get("start", start))
                            trim_end = int(refined_moment.get("end", end))
                        except (TypeError, ValueError):
                            trim_start, trim_end = start, end
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
                        m["start"] = selected_start
                        m["end"] = selected_end
                        m["duration"] = selected_end - selected_start
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
                        m["selection_primary_category"] = keep_selection_primary_category
                        if keep_selection_moment_categories is not None:
                            m["selection_moment_categories"] = keep_selection_moment_categories
                        m["ranking_primary_category"] = keep_ranking_primary_category
                        if keep_ranking_moment_categories is not None:
                            m["ranking_moment_categories"] = keep_ranking_moment_categories
                        words = _subtitle_words_for_render_start(
                            final_eval["words"],
                            trim_start,
                            selected_start,
                        )
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
                    "single_track_commentary_guard": audio_source_debug.get("single_track_commentary_guard"),
                    "stream_selection": _audio_stream_selection_summary(
                        audio_source_debug,
                        selected_stream=final_stream,
                    ),
                }
                m["stream_selection"] = m["audio_source"]["stream_selection"]
                m["voice_profile"] = final_voice_profile_score
                if final_retry_report:
                    m["stream_retry"] = final_retry_report
                m["subtitle_style"] = style
                m["captions_requested"] = bool(subtitle_enabled)
                m["subtitle_enabled"] = bool(subtitle_enabled)

                self._clip_push(clip_num, total, "audio", 100, f"Clip {clip_num}/{total}: Preparing...")

                # ── 4a: compute crop params (uses adjusted start/end) ──
                crop_params = None
                crop_w, crop_h = get_dimensions(video_path)
                if crop_vertical:
                    if self._cancel:
                        return self._cancelled()
                    self._clip_push(clip_num, total, "audio", 100, f"Clip {clip_num}/{total}: Tracking speakers...")
                    try:
                        crop_params = get_crop_params_dynamic(video_path, start, end)
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
                    m["game_title"] = self._infer_game_title_from_path(video_path)
                    m["clip_id"] = self._clip_id_for(m, clip_result.path)
                    if clip_result.warning:
                        m["render_warning"] = clip_result.warning
                        run_warnings.append(f"clip_{clip_num}_{clip_result.warning}")

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
            self._results.extend(done)
            self._moments.extend(done_moments)
            final_timing = _timing_payload("success", rendered_clip_count=len(done))
            try:
                final_timing["history_summary_after_run"] = self._record_processing_history(final_timing)
            except Exception as e:
                print(f"[timing] Failed to record processing history: {e}")
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
                    visual_diagnostics=visual_diagnostics_report,
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
            return Path(url)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return resolve_downloaded_path(info, ydl)

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
            print(f"[audio] No words on preferred stream; trying 0:a:{ordinal} ({title})")
            if progress_stage == "candidates":
                pct = int(progress_percent) if progress_percent is not None else 0
                self._push("candidates", pct, f"Candidate {clip_num}/{total}: trying audio track {ordinal + 1}...")
            else:
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

    def _scheduled_upload_active(self, clip_idx, meta=None, channel_id=None) -> bool:
        for item in self._scheduled:
            if item.get("uploaded"):
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

    def _mark_scheduled_uploaded(self, clip_idx, meta, upload_result=None):
        """Mark the matching scheduled item as uploaded after YouTube accepts it."""
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
            item["uploaded"] = True
            item["uploaded_at"] = timestamp
            if isinstance(upload_result, dict):
                if upload_result.get("id"):
                    item["youtube_id"] = upload_result["id"]
                if upload_result.get("url"):
                    item["youtube_url"] = upload_result["url"]
            changed = True
        if changed:
            self._save_state()
        return changed

    def _run_upload(self, clips_metadata, schedule_start_iso, interval_hours, channel_id=None, upload_lock=None):
        try:
            ordered_metadata = self._validate_upload_metadata(clips_metadata)
            total = len(ordered_metadata)
            uploaded = 0
            skipped = 0

            for i, (_original_index, meta, scheduled) in enumerate(ordered_metadata):
                if self._cancel:
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

                clip_base_pct = int((i / total) * 100)
                clip_span_pct = 100 / total

                def _upload_progress(chunk_percent, clip_number=i + 1):
                    overall = min(99, int(clip_base_pct + (float(chunk_percent) / 100.0) * clip_span_pct))
                    self._push("upload", overall, f"Uploading clip {clip_number}/{total}... {int(chunk_percent)}%")

                result = upload_to_youtube(
                    video_path,
                    title=meta.get("title", f"Viral Clip #{i + 1}"),
                    description=meta.get("final_description") or meta.get("description", ""),
                    tags=meta.get("tags", generate_tags()),
                    category_id=DEFAULT_VIDEO_CATEGORY_ID,
                    privacy=meta.get("privacy", "private"),
                    scheduled_time=scheduled,
                    channel_id=meta.get("channel_id") or channel_id,
                    account_id=meta.get("account_id"),
                    cancel_check=lambda: self._cancel or not self._scheduled_upload_active(idx, meta, channel_id),
                    on_progress=_upload_progress,
                )
                uploaded += 1
                if self._mark_scheduled_uploaded(idx, meta, result):
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
            if self._cancel:
                return self._cancelled()
            self._error(f"Upload failed: {e}")
        finally:
            self._processing = False
            if upload_lock:
                try:
                    upload_lock.release()
                except RuntimeError:
                    pass

    # ── Background upload scheduler ──────────────────────────────────────

    def _scheduler_loop(self):
        """Check every 30s for scheduled uploads whose time has arrived."""
        while self._scheduler_running:
            now = datetime.now()
            changed = False

            for item in list(self._scheduled):
                if item.get("uploaded"):
                    continue
                if item.get("scheduler_status") == "account_disconnected":
                    continue
                if not self._scheduled_retry_due(item, now):
                    continue
                try:
                    sched_dt = datetime.fromisoformat(f"{item['date']}T{item['time']}")
                except (KeyError, ValueError):
                    continue

                if now >= sched_dt:
                    if self._scheduled_item_missed_upload_window(item, sched_dt, now):
                        if item.get("scheduler_status") != "missed":
                            item["scheduler_status"] = "missed"
                            item["missed_at"] = now.replace(microsecond=0).isoformat()
                            changed = True
                            print("[scheduler] Public scheduled upload missed; waiting for manual reschedule/upload")
                        continue
                    clip_idx = self._resolve_clip_index(item)
                    video_path = self._safe_clip_path(self._results[clip_idx]) if clip_idx is not None and 0 <= clip_idx < len(self._results) else None
                    if not video_path:
                        self._scheduled.remove(item)
                        changed = True
                        print("[scheduler] Removed scheduled item for a missing clip")
                        continue
                    item["clipIdx"] = clip_idx
                    upload_lock = self._get_upload_lock()
                    if self._processing or not upload_lock.acquire(blocking=False):
                        print("[scheduler] Upload already in progress; will retry scheduled item")
                        continue
                    self._processing = True
                    self._cancel = False
                    title = item.get("title", f"Viral Clip #{clip_idx + 1}")
                    print(f"[scheduler] Uploading Clip {clip_idx + 1}: {title}")
                    status_message = json.dumps(f"Uploading: {title}")
                    self._js(f"window.onSchedulerStatus({status_message})")
                    try:
                        tags = item.get("tags", generate_tags())
                        if isinstance(tags, str):
                            tags = [t.strip() for t in tags.split(",") if t.strip()]
                        upload_to_youtube(
                            video_path,
                            title=title,
                            description=item.get("final_description") or item.get("description", ""),
                            tags=tags,
                            category_id=DEFAULT_VIDEO_CATEGORY_ID,
                            privacy=item.get("privacy", "private"),
                            channel_id=item.get("channel_id"),
                            account_id=item.get("account_id"),
                            cancel_check=lambda item=item: self._cancel or item not in self._scheduled,
                        )
                        item["uploaded"] = True
                        changed = True
                        print(f"[scheduler] Uploaded: {title}")
                        self._js(f"window.onScheduledUploadDone({clip_idx}, true, null)")

                        # Auto-delete from disk after successful upload
                        if self._delete_after_upload:
                            self._delete_uploaded_clip(clip_idx, video_path)

                    except Exception as e:
                        print(f"[scheduler] Upload failed: {e}")
                        self._mark_scheduled_upload_failed(item, e, now)
                        changed = True
                        self._js(f"window.onScheduledUploadDone({clip_idx}, false, `{self._esc(str(e))}`)")
                    finally:
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
        now = now or datetime.now()
        try:
            return now >= datetime.fromisoformat(str(retry_at))
        except ValueError:
            return True

    def _mark_scheduled_upload_failed(self, item, error, now=None):
        now = now or datetime.now()
        attempts = int(item.get("failure_count") or 0) + 1
        delay_minutes = min(180, 5 * (2 ** max(0, attempts - 1)))
        item["failure_count"] = attempts
        item["scheduler_status"] = "upload_failed"
        item["last_error"] = str(error)[:500]
        item["last_failed_at"] = now.replace(microsecond=0).isoformat()
        item["retry_after"] = (now + timedelta(minutes=delay_minutes)).replace(microsecond=0).isoformat()

    def _scheduled_item_missed_upload_window(self, item, sched_dt, now=None) -> bool:
        """Return True when an overdue public item should wait for user action."""
        if not isinstance(item, dict):
            return False
        privacy = str(item.get("privacy", "private") or "private").lower()
        if privacy != "public":
            return False
        now = now or datetime.now()
        return now > sched_dt + SCHEDULER_MISSED_GRACE

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
                safe_path.unlink()
                self._mark_personalization_clips_deleted({clip_id} if clip_id else set(), {deleted_name})
                self._prune_missing_results()
                print(f"[cleanup] Deleted uploaded clip: {deleted_name}")
                self._js(f"window.onClipDeleted({clip_idx}, `{self._esc(deleted_name)}`)")
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
        path.parent.mkdir(exist_ok=True)
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
        with self._state_lock:
            aligned_moments = []
            for i, path in enumerate(self._results):
                moment = self._moments[i] if i < len(self._moments) else {}
                aligned_moments.append(self._ensure_moment_identity(moment, path))
            self._moments = aligned_moments
            self._scheduled = self._normalize_scheduled_items(self._scheduled)

            data = {
                "schema_version": STATE_SCHEMA_VERSION,
                "results": [str(p) for p in self._results],
                "moments": self._moments,
                "scheduled": self._scheduled,
                "delete_after_upload": self._delete_after_upload,
                "user_settings": self._user_settings,
            }
            try:
                self._write_state_atomic(data)
            except Exception as e:
                print(f"[!] Failed to save state: {e}")

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
                self._delete_after_upload = bool(data.get("delete_after_upload", False))
                self._user_settings = data.get("user_settings", {}) if isinstance(data.get("user_settings", {}), dict) else {}

                removed_missing_files = len(self._results) != len(paths)
                removed_scheduled_items = len(self._scheduled) != len(old_scheduled)
                needs_rewrite = (
                    schema_version != STATE_SCHEMA_VERSION
                    or identity_missing
                    or schedule_missing_identity
                    or removed_missing_files
                    or removed_scheduled_items
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
        self._processing = False

    def _cancelled(self):
        self._js("window.onPipelineCancelled()")
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
