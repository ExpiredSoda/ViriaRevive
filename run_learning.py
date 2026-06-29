"""Compact local run-learning memory for clip and montage decisions.

This module stores outcome signals only. It intentionally avoids raw media,
thumbnails, audio, and full transcripts so future ranking/montage logic can
learn from creator choices without expanding the app's privacy surface.
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any

from config import RUN_LEARNING_SCHEMA_VERSION


MAX_RUNS = 150
MAX_EVENTS = 1200
MAX_CLIP_OUTCOMES = 600
MAX_MONTAGE_OUTCOMES = 240
MAX_MONTAGE_BEAT_OUTCOMES = 80
MAX_TERMS = 40


def empty_run_learning() -> dict:
    return {
        "schema_version": RUN_LEARNING_SCHEMA_VERSION,
        "runs": [],
        "events": [],
        "clip_outcomes": {},
        "montage_outcomes": {},
    }


def sanitize_run_learning(data: Any) -> dict:
    if not isinstance(data, dict):
        return empty_run_learning()
    runs = data.get("runs", [])
    events = data.get("events", [])
    outcomes = data.get("clip_outcomes", {})
    montage_outcomes = data.get("montage_outcomes", {})
    if not isinstance(runs, list):
        runs = []
    if not isinstance(events, list):
        events = []
    if not isinstance(outcomes, dict):
        outcomes = {}
    if not isinstance(montage_outcomes, dict):
        montage_outcomes = {}

    clean_outcomes: dict[str, dict] = {}
    for clip_id, outcome in list(outcomes.items())[-MAX_CLIP_OUTCOMES:]:
        if not isinstance(outcome, dict):
            continue
        clean_id = clean_string(clip_id, 120)
        if not clean_id:
            continue
        clean_outcomes[clean_id] = compact_clip_outcome(outcome)

    clean_montage_outcomes: dict[str, dict] = {}
    for storyboard_id, outcome in list(montage_outcomes.items())[-MAX_MONTAGE_OUTCOMES:]:
        if not isinstance(outcome, dict):
            continue
        clean_id = clean_string(storyboard_id, 120)
        if not clean_id:
            continue
        clean_montage_outcomes[clean_id] = compact_montage_outcome(outcome)

    return {
        "schema_version": RUN_LEARNING_SCHEMA_VERSION,
        "runs": [compact_run_summary(row) for row in runs[-MAX_RUNS:] if isinstance(row, dict)][-MAX_RUNS:],
        "events": [compact_event(row) for row in events[-MAX_EVENTS:] if isinstance(row, dict)][-MAX_EVENTS:],
        "clip_outcomes": clean_outcomes,
        "montage_outcomes": clean_montage_outcomes,
    }


def append_run_summary(store: dict, summary: dict) -> dict:
    data = sanitize_run_learning(store)
    clean = compact_run_summary(summary)
    if not clean.get("run_id"):
        return data
    runs = [row for row in data.get("runs", []) if row.get("run_id") != clean["run_id"]]
    runs.append(clean)
    data["runs"] = runs[-MAX_RUNS:]
    return data


def append_event(store: dict, event: dict) -> dict:
    data = sanitize_run_learning(store)
    clean = compact_event(event)
    if not clean.get("event_type"):
        return data
    events = data.setdefault("events", [])
    events.append(clean)
    data["events"] = events[-MAX_EVENTS:]

    event_type = str(clean.get("event_type") or "")
    clip_id = clean.get("clip_id")
    storyboard_id = clean.get("storyboard_id")
    if clip_id:
        outcomes = data.setdefault("clip_outcomes", {})
        previous = outcomes.get(clip_id, {})
        outcomes[clip_id] = merge_clip_outcome(previous, clean)
        if len(outcomes) > MAX_CLIP_OUTCOMES:
            ordered = sorted(
                outcomes.items(),
                key=lambda item: str(item[1].get("updated_at") or item[1].get("last_event_at") or ""),
            )
            data["clip_outcomes"] = dict(ordered[-MAX_CLIP_OUTCOMES:])
    if storyboard_id and event_type.startswith("montage_feedback_"):
        montage_outcomes = data.setdefault("montage_outcomes", {})
        previous = montage_outcomes.get(storyboard_id, {})
        montage_outcomes[storyboard_id] = merge_montage_outcome(previous, clean)
        if len(montage_outcomes) > MAX_MONTAGE_OUTCOMES:
            ordered = sorted(
                montage_outcomes.items(),
                key=lambda item: str(item[1].get("updated_at") or item[1].get("last_event_at") or ""),
            )
            data["montage_outcomes"] = dict(ordered[-MAX_MONTAGE_OUTCOMES:])
    return data


def build_feedback_event(
    *,
    event_id: str,
    event_type: str,
    active: bool,
    timestamp: str,
    identity: dict,
    reason: str = "",
    clip_snapshot: dict | None = None,
) -> dict:
    snapshot = compact_clip_snapshot(clip_snapshot or {})
    return compact_event(
        {
            "event_id": event_id,
            "event_type": f"feedback_{clean_string(event_type, 30)}",
            "feedback_type": clean_string(event_type, 30),
            "active": bool(active),
            "timestamp": timestamp,
            "clip_id": identity.get("clip_id"),
            "source_id": identity.get("source_id"),
            "source_stem": identity.get("source_stem"),
            "clip_filename": identity.get("clip_filename"),
            "reason": reason,
            "learning_terms": snapshot.get("learning_terms", []),
            "clip_snapshot": snapshot,
        }
    )


def build_clip_deleted_event(
    *,
    event_id: str,
    timestamp: str,
    clip_id: str = "",
    source_id: str = "",
    source_stem: str = "",
    clip_filename: str = "",
    reason: str = "clip_deleted",
) -> dict:
    return compact_event(
        {
            "event_id": event_id,
            "event_type": "clip_deleted",
            "timestamp": timestamp,
            "clip_id": clip_id,
            "source_id": source_id,
            "source_stem": source_stem,
            "clip_filename": clip_filename,
            "reason": reason,
        }
    )


def build_metadata_event(
    *,
    event_id: str,
    timestamp: str,
    clip_id: str,
    source_id: str = "",
    source_stem: str = "",
    clip_filename: str = "",
    title: str = "",
    game_title: str = "",
    reason: str = "metadata_generated",
) -> dict:
    return compact_event(
        {
            "event_id": event_id,
            "event_type": "metadata_generated",
            "timestamp": timestamp,
            "clip_id": clip_id,
            "source_id": source_id,
            "source_stem": source_stem,
            "clip_filename": clip_filename,
            "reason": reason,
            "metadata": {
                "title": clean_string(title, 140),
                "game_title": clean_string(game_title, 140),
            },
        }
    )


def build_montage_feedback_event(
    *,
    event_id: str,
    feedback_type: str,
    active: bool,
    timestamp: str,
    storyboard_id: str,
    source_id: str = "",
    source_stem: str = "",
    reason: str = "",
    storyboard_snapshot: dict | None = None,
    beat_snapshot: dict | None = None,
) -> dict:
    """Build a compact event for whole-montage or per-beat feedback."""

    clean_feedback_type = clean_string(feedback_type, 30).lower()
    storyboard = compact_montage_snapshot(storyboard_snapshot or {})
    beat = compact_montage_beat_snapshot(beat_snapshot or {})
    learning_terms = clean_terms(
        (beat.get("learning_terms") or []) + (storyboard.get("learning_terms") or [])
    )
    event = {
        "event_id": event_id,
        "event_type": f"montage_feedback_{clean_feedback_type}",
        "feedback_type": clean_feedback_type,
        "active": bool(active),
        "timestamp": timestamp,
        "storyboard_id": storyboard_id,
        "source_id": source_id or storyboard.get("source_id", ""),
        "source_stem": source_stem or storyboard.get("source_stem", ""),
        "reason": reason,
        "learning_terms": learning_terms,
        "montage_snapshot": storyboard,
    }
    if beat:
        event.update(
            {
                "beat_id": beat.get("beat_id", ""),
                "beat_role": beat.get("role", ""),
                "beat_category": beat.get("category", ""),
                "clip_id": beat.get("clip_id", ""),
                "clip_filename": beat.get("clip_filename", ""),
                "beat_snapshot": beat,
            }
        )
    return compact_event(event)


def compact_run_summary(summary: dict) -> dict:
    if not isinstance(summary, dict):
        summary = {}
    timing = summary.get("timing") if isinstance(summary.get("timing"), dict) else {}
    settings = summary.get("settings") if isinstance(summary.get("settings"), dict) else {}
    timing_fingerprint = (
        timing.get("settings_fingerprint")
        if isinstance(timing.get("settings_fingerprint"), dict)
        else {}
    )
    selected = summary.get("selected") if isinstance(summary.get("selected"), list) else []
    final_clips = summary.get("final_clips") if isinstance(summary.get("final_clips"), list) else []
    return {
        "schema_version": RUN_LEARNING_SCHEMA_VERSION,
        "run_id": clean_string(summary.get("run_id") or timing.get("run_id"), 120),
        "status": clean_string(summary.get("status") or timing.get("status"), 60),
        "source_id": clean_string(summary.get("source_id"), 120),
        "source_stem": clean_string(summary.get("source_stem"), 180),
        "game_title": clean_string(summary.get("game_title"), 160),
        "debug_path": clean_string(summary.get("debug_path"), 300),
        "started_at_utc": clean_string(timing.get("started_at_utc") or summary.get("started_at_utc"), 40),
        "finished_at_utc": clean_string(timing.get("finished_at_utc") or summary.get("finished_at_utc"), 40),
        "video_duration_seconds": finite_number(
            summary.get("video_duration_seconds", timing.get("video_duration_seconds"))
        ),
        "elapsed_seconds": finite_number(summary.get("elapsed_seconds", timing.get("elapsed_seconds"))),
        "estimated_total_seconds": finite_number(
            summary.get("estimated_total_seconds", timing.get("estimated_total_seconds")),
            allow_none=True,
        ),
        "generation_mode": clean_string(
            settings.get("generation_mode")
            or summary.get("generation_mode")
            or timing_fingerprint.get("generation_mode"),
            40,
        ),
        "processing_depth": clean_string(settings.get("processing_depth") or summary.get("processing_depth"), 40),
        "detection_preference": clean_string(
            settings.get("detection_preference") or summary.get("detection_preference"), 40
        ),
        "candidate_count": safe_int(summary.get("candidate_count", timing.get("candidate_count"))),
        "accepted_candidate_count": safe_int(summary.get("accepted_candidate_count")),
        "selected_count": safe_int(summary.get("selected_count", timing.get("selected_count"))),
        "rendered_clip_count": safe_int(summary.get("rendered_clip_count", timing.get("rendered_clip_count"))),
        "selected_clip_ids": [clean_string(item, 120) for item in summary.get("selected_clip_ids", [])[:40]],
        "selected": [compact_clip_snapshot(row) for row in selected[:40] if isinstance(row, dict)],
        "final_clips": [compact_final_clip(row) for row in final_clips[:40] if isinstance(row, dict)],
        "feature_status": compact_feature_status(summary.get("feature_status")),
        "stores_raw_media": False,
    }


def compact_event(event: dict) -> dict:
    if not isinstance(event, dict):
        event = {}
    clean = {
        "schema_version": RUN_LEARNING_SCHEMA_VERSION,
        "event_id": clean_string(event.get("event_id"), 140),
        "event_type": clean_string(event.get("event_type"), 60),
        "feedback_type": clean_string(event.get("feedback_type"), 40),
        "active": bool(event.get("active", True)),
        "timestamp": clean_string(event.get("timestamp"), 40),
        "clip_id": clean_string(event.get("clip_id"), 120),
        "source_id": clean_string(event.get("source_id"), 120),
        "source_stem": clean_string(event.get("source_stem"), 180),
        "clip_filename": clean_string(event.get("clip_filename"), 220),
        "storyboard_id": clean_string(event.get("storyboard_id"), 120),
        "beat_id": clean_string(event.get("beat_id"), 120),
        "beat_role": clean_string(event.get("beat_role"), 60),
        "beat_category": clean_string(event.get("beat_category"), 80),
        "reason": clean_string(event.get("reason"), 500),
        "learning_terms": clean_terms(event.get("learning_terms")),
        "stores_raw_media": False,
    }
    snapshot = compact_clip_snapshot(event.get("clip_snapshot"))
    if snapshot:
        clean["clip_snapshot"] = snapshot
    montage_snapshot = compact_montage_snapshot(event.get("montage_snapshot"))
    if montage_snapshot:
        clean["montage_snapshot"] = montage_snapshot
    beat_snapshot = compact_montage_beat_snapshot(event.get("beat_snapshot"))
    if beat_snapshot:
        clean["beat_snapshot"] = beat_snapshot
    metadata = event.get("metadata")
    if isinstance(metadata, dict):
        clean["metadata"] = {
            "title": clean_string(metadata.get("title"), 140),
            "game_title": clean_string(metadata.get("game_title"), 140),
        }
    return {k: v for k, v in clean.items() if v not in ("", [], {})}


def compact_clip_snapshot(snapshot: dict | None) -> dict:
    if not isinstance(snapshot, dict):
        return {}
    categories = snapshot.get("moment_categories") if isinstance(snapshot.get("moment_categories"), dict) else {}
    ai = snapshot.get("ai_moment_classification") if isinstance(snapshot.get("ai_moment_classification"), dict) else {}
    visual = snapshot.get("visual_diagnostics") if isinstance(snapshot.get("visual_diagnostics"), dict) else {}
    multimodal = snapshot.get("multimodal_analysis") if isinstance(snapshot.get("multimodal_analysis"), dict) else {}
    ranker = snapshot.get("ranker") if isinstance(snapshot.get("ranker"), dict) else {}
    return {
        "start": finite_number(snapshot.get("start"), allow_none=True),
        "end": finite_number(snapshot.get("end"), allow_none=True),
        "duration": finite_number(snapshot.get("duration"), allow_none=True),
        "peak_time": finite_number(snapshot.get("peak_time"), allow_none=True),
        "quality_score": finite_number(snapshot.get("quality_score"), allow_none=True),
        "selection_quality_score": finite_number(snapshot.get("selection_quality_score"), allow_none=True),
        "quality_rank": safe_int(snapshot.get("quality_rank")),
        "quality_floor": finite_number(snapshot.get("quality_floor"), allow_none=True),
        "detection_preference": clean_string(snapshot.get("detection_preference"), 40),
        "primary_category": clean_string(snapshot.get("primary_category"), 80),
        "category_scores": compact_category_scores(categories),
        "ai_category": clean_string(ai.get("primary_category"), 80),
        "visual_labels": clean_terms(visual.get("labels"), limit=10),
        "vision_label": clean_string(multimodal.get("primary_visual_label"), 80),
        "word_count": safe_int(snapshot.get("word_count")),
        "analysis_word_count": safe_int(snapshot.get("analysis_word_count")),
        "subtitle_word_count": safe_int(snapshot.get("subtitle_word_count")),
        "speech_stream": safe_int(snapshot.get("speech_stream"), default=-1),
        "subtitle_generated": bool(snapshot.get("subtitle_generated")) if "subtitle_generated" in snapshot else None,
        "subtitles_burned": bool(snapshot.get("subtitles_burned")) if "subtitles_burned" in snapshot else None,
        "learning_terms": clean_terms(snapshot.get("learning_terms")),
        "hook_points": safe_int(ranker.get("hook_points")),
        "weak_points": safe_int(ranker.get("weak_points")),
        "aftermath_points": safe_int(ranker.get("aftermath_points")),
        "stores_raw_media": False,
    } | ({"has_transcript": bool(snapshot.get("transcript"))} if "transcript" in snapshot else {})


def compact_montage_snapshot(snapshot: dict | None) -> dict:
    if not isinstance(snapshot, dict):
        return {}
    source = snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {}
    settings = snapshot.get("settings") if isinstance(snapshot.get("settings"), dict) else {}
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    memory = snapshot.get("memory_snapshot") if isinstance(snapshot.get("memory_snapshot"), dict) else {}
    categories = summary.get("category_counts") if isinstance(summary.get("category_counts"), dict) else {}
    terms = montage_terms_from_snapshot(snapshot)
    return {
        "storyboard_id": clean_string(snapshot.get("storyboard_id"), 120),
        "status": clean_string(snapshot.get("status"), 60),
        "ready": bool(snapshot.get("ready")) if "ready" in snapshot else None,
        "created_at": clean_string(snapshot.get("created_at"), 40),
        "source_id": clean_string((snapshot.get("source_ids") or [""])[0] if isinstance(snapshot.get("source_ids"), list) else "", 120),
        "source_stem": clean_string(source.get("source_stem"), 180),
        "game_title": clean_string(source.get("game_title"), 160),
        "target_duration": finite_number(settings.get("target_duration"), allow_none=True),
        "story_shape": clean_string(settings.get("story_shape"), 80),
        "render_quality": clean_string(settings.get("render_quality"), 40),
        "beat_count": safe_int(summary.get("beat_count")),
        "planned_duration_seconds": finite_number(summary.get("planned_duration_seconds"), allow_none=True),
        "category_counts": compact_category_scores(categories),
        "memory_enabled": bool(settings.get("memory_enabled")) if "memory_enabled" in settings else None,
        "memory_snapshot": compact_montage_memory_snapshot(memory),
        "learning_terms": terms,
        "stores_raw_media": False,
        "stores_full_transcripts": False,
    }


def compact_montage_memory_snapshot(snapshot: dict | None) -> dict:
    if not isinstance(snapshot, dict):
        return {}
    return {
        "local_only": bool(snapshot.get("local_only", True)),
        "memory_enabled": bool(snapshot.get("memory_enabled")) if "memory_enabled" in snapshot else None,
        "feedback_signal_count": safe_int(snapshot.get("feedback_signal_count")),
        "voice_profile_used": bool(snapshot.get("voice_profile_used")),
        "game_context_used": bool(snapshot.get("game_context_used")),
        "visual_used": bool(snapshot.get("visual_used")),
        "multimodal_used": bool(snapshot.get("multimodal_used")),
        "ai_label_used": bool(snapshot.get("ai_label_used")),
        "stores_raw_media": False,
        "stores_full_transcripts": False,
    }


def compact_montage_beat_snapshot(snapshot: dict | None) -> dict:
    if not isinstance(snapshot, dict):
        return {}
    terms = montage_terms_from_beat(snapshot)
    clean = {
        "beat_id": clean_string(snapshot.get("beat_id"), 120),
        "role": clean_string(snapshot.get("role"), 60),
        "clip_id": clean_string(snapshot.get("clip_id"), 120),
        "source_id": clean_string(snapshot.get("source_id"), 120),
        "clip_filename": clean_string(snapshot.get("clip_filename"), 220),
        "start": finite_number(snapshot.get("start"), allow_none=True),
        "end": finite_number(snapshot.get("end"), allow_none=True),
        "duration": finite_number(snapshot.get("duration"), allow_none=True),
        "category": clean_string(snapshot.get("category"), 80),
        "label": clean_string(snapshot.get("label"), 100),
        "score": finite_number(snapshot.get("score"), allow_none=True),
        "evidence": clean_terms(snapshot.get("evidence"), limit=16),
        "learning_terms": terms,
        "has_hook_text": bool(snapshot.get("hook_text")) if "hook_text" in snapshot else None,
        "stores_raw_media": False,
        "stores_full_transcripts": False,
    }
    meaningful_keys = ("beat_id", "role", "clip_id", "source_id", "clip_filename", "category", "label")
    if not any(clean.get(key) for key in meaningful_keys):
        return {}
    return {k: v for k, v in clean.items() if v not in ("", [], {}, None)}


def montage_terms_from_snapshot(snapshot: dict | None) -> list[str]:
    if not isinstance(snapshot, dict):
        return []
    terms = []
    source = snapshot.get("source") if isinstance(snapshot.get("source"), dict) else {}
    settings = snapshot.get("settings") if isinstance(snapshot.get("settings"), dict) else {}
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    terms.extend(
        [
            source.get("game_title"),
            settings.get("story_shape"),
            settings.get("render_quality"),
            "montage",
        ]
    )
    categories = summary.get("category_counts") if isinstance(summary.get("category_counts"), dict) else {}
    terms.extend(categories.keys())
    beats = snapshot.get("beats") if isinstance(snapshot.get("beats"), list) else []
    for beat in beats[:MAX_MONTAGE_BEAT_OUTCOMES]:
        if isinstance(beat, dict):
            terms.extend(montage_terms_from_beat(beat))
    return clean_terms(terms)


def montage_terms_from_beat(snapshot: dict | None) -> list[str]:
    if not isinstance(snapshot, dict):
        return []
    terms = [
        "montage beat",
        snapshot.get("role"),
        snapshot.get("category"),
        snapshot.get("label"),
    ]
    evidence = snapshot.get("evidence")
    if isinstance(evidence, list):
        terms.extend(evidence)
    return clean_terms(terms)


def compact_final_clip(row: dict) -> dict:
    if not isinstance(row, dict):
        return {}
    return {
        "clip_id": clean_string(row.get("clip_id"), 120),
        "source_id": clean_string(row.get("source_id"), 120),
        "filename": clean_string(row.get("filename") or row.get("clip_filename"), 220),
        "start": finite_number(row.get("start", row.get("selected_start")), allow_none=True),
        "end": finite_number(row.get("end", row.get("selected_end")), allow_none=True),
        "duration": finite_number(row.get("duration"), allow_none=True),
        "subtitle_generated": bool(row.get("subtitle_generated")) if "subtitle_generated" in row else None,
        "subtitles_burned": bool(row.get("subtitles_burned")) if "subtitles_burned" in row else None,
        "primary_category": clean_string(row.get("primary_category") or row.get("final_primary_category"), 80),
        "title": clean_string((row.get("generated_metadata") or {}).get("title"), 140)
        if isinstance(row.get("generated_metadata"), dict)
        else "",
    }


def compact_clip_outcome(outcome: dict) -> dict:
    if not isinstance(outcome, dict):
        return {}
    return {
        "clip_id": clean_string(outcome.get("clip_id"), 120),
        "source_id": clean_string(outcome.get("source_id"), 120),
        "source_stem": clean_string(outcome.get("source_stem"), 180),
        "clip_filename": clean_string(outcome.get("clip_filename"), 220),
        "like": bool(outcome.get("like", False)),
        "dislike": bool(outcome.get("dislike", False)),
        "favorite": bool(outcome.get("favorite", False)),
        "deleted": bool(outcome.get("deleted", False)),
        "metadata_generated": bool(outcome.get("metadata_generated", False)),
        "last_event_type": clean_string(outcome.get("last_event_type"), 60),
        "last_feedback_type": clean_string(outcome.get("last_feedback_type"), 40),
        "last_event_at": clean_string(outcome.get("last_event_at"), 40),
        "updated_at": clean_string(outcome.get("updated_at"), 40),
        "reason": clean_string(outcome.get("reason"), 500),
        "learning_terms": clean_terms(outcome.get("learning_terms")),
        "clip_snapshot": compact_clip_snapshot(outcome.get("clip_snapshot")),
        "stores_raw_media": False,
    }


def compact_montage_outcome(outcome: dict) -> dict:
    if not isinstance(outcome, dict):
        return {}
    beat_outcomes = outcome.get("beat_outcomes") if isinstance(outcome.get("beat_outcomes"), dict) else {}
    clean_beats: dict[str, dict] = {}
    for beat_id, beat in list(beat_outcomes.items())[-MAX_MONTAGE_BEAT_OUTCOMES:]:
        if not isinstance(beat, dict):
            continue
        clean_id = clean_string(beat_id, 120)
        if not clean_id:
            continue
        clean_beats[clean_id] = compact_montage_beat_outcome(beat)
    return {
        "storyboard_id": clean_string(outcome.get("storyboard_id"), 120),
        "source_id": clean_string(outcome.get("source_id"), 120),
        "source_stem": clean_string(outcome.get("source_stem"), 180),
        "game_title": clean_string(outcome.get("game_title"), 160),
        "like": bool(outcome.get("like", False)),
        "dislike": bool(outcome.get("dislike", False)),
        "favorite": bool(outcome.get("favorite", False)),
        "last_event_type": clean_string(outcome.get("last_event_type"), 60),
        "last_feedback_type": clean_string(outcome.get("last_feedback_type"), 40),
        "last_event_at": clean_string(outcome.get("last_event_at"), 40),
        "updated_at": clean_string(outcome.get("updated_at"), 40),
        "reason": clean_string(outcome.get("reason"), 500),
        "event_count": safe_int(outcome.get("event_count")),
        "beat_outcomes": clean_beats,
        "learning_terms": clean_terms(outcome.get("learning_terms")),
        "montage_snapshot": compact_montage_snapshot(outcome.get("montage_snapshot")),
        "stores_raw_media": False,
        "stores_full_transcripts": False,
    }


def compact_montage_beat_outcome(outcome: dict) -> dict:
    if not isinstance(outcome, dict):
        return {}
    return {
        "beat_id": clean_string(outcome.get("beat_id"), 120),
        "clip_id": clean_string(outcome.get("clip_id"), 120),
        "source_id": clean_string(outcome.get("source_id"), 120),
        "clip_filename": clean_string(outcome.get("clip_filename"), 220),
        "role": clean_string(outcome.get("role"), 60),
        "category": clean_string(outcome.get("category"), 80),
        "label": clean_string(outcome.get("label"), 100),
        "score": finite_number(outcome.get("score"), allow_none=True),
        "like": bool(outcome.get("like", False)),
        "dislike": bool(outcome.get("dislike", False)),
        "favorite": bool(outcome.get("favorite", False)),
        "last_event_type": clean_string(outcome.get("last_event_type"), 60),
        "last_feedback_type": clean_string(outcome.get("last_feedback_type"), 40),
        "last_event_at": clean_string(outcome.get("last_event_at"), 40),
        "updated_at": clean_string(outcome.get("updated_at"), 40),
        "reason": clean_string(outcome.get("reason"), 500),
        "learning_terms": clean_terms(outcome.get("learning_terms")),
        "beat_snapshot": compact_montage_beat_snapshot(outcome.get("beat_snapshot")),
        "stores_raw_media": False,
    }


def merge_clip_outcome(previous: dict, event: dict) -> dict:
    outcome = compact_clip_outcome(previous)
    event_type = clean_string(event.get("event_type"), 60)
    feedback_type = clean_string(event.get("feedback_type"), 40)
    active = bool(event.get("active", True))
    outcome.update(
        {
            "clip_id": clean_string(event.get("clip_id") or outcome.get("clip_id"), 120),
            "source_id": clean_string(event.get("source_id") or outcome.get("source_id"), 120),
            "source_stem": clean_string(event.get("source_stem") or outcome.get("source_stem"), 180),
            "clip_filename": clean_string(event.get("clip_filename") or outcome.get("clip_filename"), 220),
            "last_event_type": event_type,
            "last_feedback_type": feedback_type or outcome.get("last_feedback_type", ""),
            "last_event_at": clean_string(event.get("timestamp") or outcome.get("last_event_at"), 40),
            "updated_at": clean_string(event.get("timestamp") or outcome.get("updated_at"), 40),
            "reason": clean_string(event.get("reason") or outcome.get("reason"), 500),
            "stores_raw_media": False,
        }
    )
    if event_type == "feedback_like":
        outcome["like"] = active
        if active:
            outcome["dislike"] = False
    elif event_type == "feedback_dislike":
        outcome["dislike"] = active
        if active:
            outcome["like"] = False
    elif event_type == "feedback_favorite":
        outcome["favorite"] = active
    elif event_type == "clip_deleted":
        outcome["deleted"] = True
    elif event_type == "metadata_generated":
        outcome["metadata_generated"] = True
    terms = clean_terms(event.get("learning_terms") or outcome.get("learning_terms"))
    if terms:
        outcome["learning_terms"] = terms
    snapshot = compact_clip_snapshot(event.get("clip_snapshot"))
    if snapshot:
        outcome["clip_snapshot"] = snapshot
    return outcome


def merge_montage_outcome(previous: dict, event: dict) -> dict:
    outcome = compact_montage_outcome(previous)
    event_type = clean_string(event.get("event_type"), 60)
    feedback_type = clean_string(event.get("feedback_type"), 40)
    active = bool(event.get("active", True))
    montage_snapshot = compact_montage_snapshot(event.get("montage_snapshot"))
    beat_snapshot = compact_montage_beat_snapshot(event.get("beat_snapshot"))
    outcome.update(
        {
            "storyboard_id": clean_string(event.get("storyboard_id") or outcome.get("storyboard_id"), 120),
            "source_id": clean_string(event.get("source_id") or outcome.get("source_id"), 120),
            "source_stem": clean_string(event.get("source_stem") or outcome.get("source_stem"), 180),
            "game_title": clean_string(
                montage_snapshot.get("game_title") or outcome.get("game_title"),
                160,
            ),
            "last_event_type": event_type,
            "last_feedback_type": feedback_type or outcome.get("last_feedback_type", ""),
            "last_event_at": clean_string(event.get("timestamp") or outcome.get("last_event_at"), 40),
            "updated_at": clean_string(event.get("timestamp") or outcome.get("updated_at"), 40),
            "reason": clean_string(event.get("reason") or outcome.get("reason"), 500),
            "event_count": safe_int(outcome.get("event_count")) + 1,
            "stores_raw_media": False,
            "stores_full_transcripts": False,
        }
    )
    if not beat_snapshot:
        _apply_feedback_flags(outcome, feedback_type, active)
    else:
        beat_id = beat_snapshot.get("beat_id") or clean_string(event.get("beat_id"), 120)
        if beat_id:
            beats = outcome.setdefault("beat_outcomes", {})
            previous_beat = compact_montage_beat_outcome(beats.get(beat_id, {}))
            previous_beat.update(
                {
                    "beat_id": beat_id,
                    "clip_id": beat_snapshot.get("clip_id", ""),
                    "source_id": beat_snapshot.get("source_id", ""),
                    "clip_filename": beat_snapshot.get("clip_filename", ""),
                    "role": beat_snapshot.get("role", ""),
                    "category": beat_snapshot.get("category", ""),
                    "label": beat_snapshot.get("label", ""),
                    "score": beat_snapshot.get("score"),
                    "last_event_type": event_type,
                    "last_feedback_type": feedback_type,
                    "last_event_at": clean_string(event.get("timestamp"), 40),
                    "updated_at": clean_string(event.get("timestamp"), 40),
                    "reason": clean_string(event.get("reason"), 500),
                    "beat_snapshot": beat_snapshot,
                    "stores_raw_media": False,
                }
            )
            _apply_feedback_flags(previous_beat, feedback_type, active)
            terms = clean_terms(event.get("learning_terms") or beat_snapshot.get("learning_terms"))
            if terms:
                previous_beat["learning_terms"] = terms
            beats[beat_id] = compact_montage_beat_outcome(previous_beat)
            if len(beats) > MAX_MONTAGE_BEAT_OUTCOMES:
                ordered = sorted(
                    beats.items(),
                    key=lambda item: str(item[1].get("updated_at") or item[1].get("last_event_at") or ""),
                )
                outcome["beat_outcomes"] = dict(ordered[-MAX_MONTAGE_BEAT_OUTCOMES:])
    terms = clean_terms(event.get("learning_terms") or outcome.get("learning_terms"))
    if terms:
        outcome["learning_terms"] = terms
    if montage_snapshot:
        outcome["montage_snapshot"] = montage_snapshot
    return compact_montage_outcome(outcome)


def _apply_feedback_flags(outcome: dict, feedback_type: str, active: bool) -> None:
    if feedback_type == "like":
        outcome["like"] = active
        if active:
            outcome["dislike"] = False
    elif feedback_type == "dislike":
        outcome["dislike"] = active
        if active:
            outcome["like"] = False
    elif feedback_type == "favorite":
        outcome["favorite"] = active


def compact_category_scores(categories: dict) -> dict:
    if not isinstance(categories, dict):
        return {}
    scores = categories.get("scores") if isinstance(categories.get("scores"), dict) else categories
    clean: dict[str, float] = {}
    for key, value in scores.items():
        label = clean_string(key, 80)
        number = finite_number(value, allow_none=True)
        if label and number is not None:
            clean[label] = round(number, 4)
        if len(clean) >= 12:
            break
    return clean


def compact_feature_status(status: Any) -> dict:
    if not isinstance(status, dict):
        return {}
    clean: dict[str, dict] = {}
    for key, value in status.items():
        if not isinstance(value, dict):
            continue
        clean[clean_string(key, 80)] = {
            "enabled": bool(value.get("enabled")) if "enabled" in value else None,
            "active": bool(value.get("active")) if "active" in value else None,
            "reason": clean_string(value.get("reason") or value.get("inactive_reason"), 160),
            "selection_impact": clean_string(value.get("selection_impact"), 80),
        }
        if len(clean) >= 20:
            break
    return clean


def clean_terms(values, *, limit: int = MAX_TERMS) -> list[str]:
    if not isinstance(values, list):
        return []
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
        term = re.sub(r"\s+", " ", term)
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term[:80])
        if len(terms) >= limit:
            break
    return terms


def clean_string(value: Any, limit: int) -> str:
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[: max(0, int(limit or 0))]


def finite_number(value: Any, default: float | None = 0.0, *, allow_none: bool = False) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None if allow_none else default
    if not math.isfinite(number):
        return None if allow_none else default
    return round(number, 4)


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def redacted_summary(store: dict) -> dict:
    data = sanitize_run_learning(copy.deepcopy(store))
    events = data.get("events", [])
    outcomes = data.get("clip_outcomes", {})
    montage_outcomes = data.get("montage_outcomes", {})
    montage_events = [
        event for event in events if str(event.get("event_type") or "").startswith("montage_feedback_")
    ]
    return {
        "schema_version": RUN_LEARNING_SCHEMA_VERSION,
        "run_count": len(data.get("runs", [])),
        "event_count": len(events),
        "clip_outcome_count": len(outcomes),
        "montage_outcome_count": len(montage_outcomes),
        "feedback_event_count": sum(
            1 for event in events if str(event.get("event_type") or "").startswith("feedback_")
        ),
        "montage_feedback_event_count": len(montage_events),
        "montage_beat_outcome_count": sum(
            len(item.get("beat_outcomes", {}))
            for item in montage_outcomes.values()
            if isinstance(item, dict) and isinstance(item.get("beat_outcomes"), dict)
        ),
        "deleted_clip_count": sum(1 for item in outcomes.values() if isinstance(item, dict) and item.get("deleted")),
        "metadata_event_count": sum(1 for event in events if event.get("event_type") == "metadata_generated"),
        "stores_raw_media": False,
    }
