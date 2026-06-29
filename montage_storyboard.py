"""Montage candidate audit helpers.

This module is intentionally storyboard-only for now. It reads compact run
debug data and reports whether there are enough usable beats for a future
montage render without storing raw media or full transcripts.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MONTAGE_AUDIT_SCHEMA_VERSION = 1
MONTAGE_STORYBOARD_SCHEMA_VERSION = 1
DEFAULT_TARGET_BEATS = 3
DEFAULT_TARGET_DURATION_SECONDS = 60.0
DEFAULT_MIN_BEAT_SECONDS = 4.0
DEFAULT_MIN_SCORE = 0.52
DEFAULT_ACCEPTED_MIN_SCORE = 0.60
MAX_AUDIT_BEATS = 64
MAX_STORYBOARD_BEATS = 8
MAX_TEXT_CHARS = 140
DEFAULT_MAX_BEAT_SECONDS = 14.0
SLOW_CONTEXT_MAX_BEAT_SECONDS = 18.0
FUNNY_CONTEXT_MAX_BEAT_SECONDS = 20.0
FUNNY_PAYOFF_MAX_BEAT_SECONDS = 30.0
CONTEXT_CONTINUATION_MIN_SCORE = 0.45
CONTEXT_CONTINUATION_MAX_SCORE = 0.58
COHERENT_STORY_SHAPES = {
    "setup_escalate_punchline",
    "hook_escalate_payoff",
    "failure_recovery_payoff",
    "combat_escalation",
    "story_reveal",
    "tutorial_story",
}
FUNNY_PHRASES = {
    "oh my god",
    "what the hell",
    "what is happening",
    "are you kidding",
    "are they stupid",
    "that's funny",
    "that's hilarious",
    "idiot",
    "idiots",
    "bribe",
    "convenient",
    "no way",
    "wait what",
    "yikes",
}
CONTEXT_CONTINUATION_REJECT_REASONS = {
    "low_transcript_quality",
    "accepted_score_below_floor",
}
TOPIC_FAMILY_TERMS = {
    "entry": {
        "bribe",
        "credits",
        "check a list",
        "get in",
        "sneak in",
        "club",
        "bouncer",
        "door",
        "not worth",
        "scam",
        "scammed",
        "tricked",
    },
    "minigame": {
        "game inside",
        "inside of the game",
        "arcade",
        "minigame",
        "mini game",
        "assigned to make the game",
    },
    "combat": {
        "kill",
        "killed",
        "shoot",
        "ammo",
        "takedown",
        "weapon",
        "fight",
        "enemy",
    },
    "stealth": {
        "sneak",
        "caught",
        "hide",
        "lay low",
        "escape route",
        "get past",
    },
    "tutorial": {
        "how you",
        "you can",
        "need to",
        "use",
        "tutorial",
        "actions",
    },
}
TOPIC_RELATED = {
    "entry": {"stealth", "story"},
    "stealth": {"entry", "combat"},
    "combat": {"stealth", "failure"},
    "tutorial": {"minigame"},
}


def build_candidate_audit(
    run_debug: dict,
    *,
    target_beats: int = DEFAULT_TARGET_BEATS,
    target_duration_seconds: float | None = None,
    min_score: float = DEFAULT_MIN_SCORE,
    accepted_min_score: float = DEFAULT_ACCEPTED_MIN_SCORE,
) -> dict:
    """Build a compact readiness audit from a run debug payload."""

    payload = run_debug if isinstance(run_debug, dict) else {}
    rows = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    final_clips = payload.get("final_clips") if isinstance(payload.get("final_clips"), list) else []
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    target_beats = max(1, int(_safe_float(target_beats, DEFAULT_TARGET_BEATS) or DEFAULT_TARGET_BEATS))
    target_duration = _safe_float(target_duration_seconds, None)

    beat_rows: list[dict] = []
    rejected_summary: dict[str, int] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        beat = _compact_beat(row, index=index, min_score=min_score, accepted_min_score=accepted_min_score)
        if beat["usable"]:
            beat_rows.append(beat)
        else:
            reason = beat.get("reject_reason") or beat.get("audit_reject_reason") or "not_usable"
            rejected_summary[reason] = rejected_summary.get(reason, 0) + 1

    beat_rows.sort(key=_beat_sort_key)
    selected_beats = beat_rows[:MAX_AUDIT_BEATS]
    usable_duration = round(sum(max(0.0, _safe_float(item.get("duration"), 0.0) or 0.0) for item in selected_beats), 3)
    category_counts = _category_counts(selected_beats)
    feature_status = _feature_status(payload, selected_beats)
    quality_summary = _quality_summary(selected_beats)
    status, reasons = _audit_status(
        selected_beats,
        target_beats=target_beats,
        target_duration_seconds=target_duration,
        usable_duration_seconds=usable_duration,
        feature_status=feature_status,
    )

    audit = {
        "schema_version": MONTAGE_AUDIT_SCHEMA_VERSION,
        "audit_id": _audit_id(payload, target_beats, target_duration),
        "status": status,
        "ready": status == "ready",
        "reasons": reasons,
        "source": {
            "run_id": str(payload.get("run_id") or ""),
            "debug_stage": str(payload.get("debug_stage") or ""),
            "video": str(payload.get("video") or ""),
            "source_stem": _source_stem(payload),
            "game_title": _game_title(payload),
        },
        "counts": {
            "candidate_count": int(_safe_float(payload.get("candidate_count"), len(rows)) or 0),
            "debug_candidate_rows": len(rows),
            "selected_count": int(_safe_float(payload.get("selected_count"), 0) or 0),
            "final_clip_count": len(final_clips),
            "usable_beat_count": len(selected_beats),
            "target_beat_count": target_beats,
        },
        "duration": {
            "video_seconds": _round_or_none(payload.get("video_duration")),
            "usable_beats_seconds": usable_duration,
            "target_seconds": _round_or_none(target_duration),
        },
        "quality": quality_summary,
        "categories": category_counts,
        "feature_status": feature_status,
        "rejected_summary": dict(sorted(rejected_summary.items())),
        "recommendations": _recommendations(status, reasons, selected_beats, feature_status, settings),
        "beats": selected_beats,
        "stores_raw_media": False,
        "stores_full_transcripts": False,
    }
    return audit


def write_candidate_audit(output_path: Path, audit: dict) -> Path:
    """Persist an audit atomically enough for local debug/report use."""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_name(f"{output_path.name}.tmp")
    try:
        tmp.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
        tmp.replace(output_path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
    return output_path


def build_storyboard_from_audit(
    audit: dict,
    *,
    target_duration_seconds: float | None = DEFAULT_TARGET_DURATION_SECONDS,
    story_shape: str = "hook_escalate_payoff",
    memory_enabled: bool = True,
    render_quality: str = "draft",
    created_at: str | None = None,
    excluded_beat_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    storyboard_index: int = 1,
) -> dict:
    """Build a deterministic storyboard from a montage-readiness audit."""

    clean_audit = audit if isinstance(audit, dict) else {}
    source = clean_audit.get("source") if isinstance(clean_audit.get("source"), dict) else {}
    excluded = {str(value) for value in (excluded_beat_ids or []) if str(value or "").strip()}
    beats = [
        item
        for item in (clean_audit.get("beats") if isinstance(clean_audit.get("beats"), list) else [])
        if isinstance(item, dict) and item.get("usable") and str(item.get("beat_id") or "") not in excluded
    ]
    target_duration = _safe_float(target_duration_seconds, DEFAULT_TARGET_DURATION_SECONDS)
    if not target_duration or target_duration <= 0:
        target_duration = DEFAULT_TARGET_DURATION_SECONDS
    target_beats = max(1, int((clean_audit.get("counts") or {}).get("target_beat_count") or DEFAULT_TARGET_BEATS))
    selected = _select_storyboard_beats(
        beats,
        target_beats=target_beats,
        target_duration=target_duration,
        story_shape=story_shape,
    )
    role_map = _assign_roles(selected, story_shape=story_shape)
    storyboard_beats = []
    render_plan = []
    for index, beat in enumerate(selected):
        role = role_map.get(beat.get("beat_id"), "beat")
        transition = "none" if index == len(selected) - 1 else "hard_cut"
        storyboard_beat = {
            "beat_id": str(beat.get("beat_id") or f"beat_{index + 1}"),
            "role": role,
            "clip_id": str(beat.get("clip_id") or ""),
            "source_id": str(beat.get("source_id") or ""),
            "clip_filename": str(beat.get("clip_filename") or ""),
            "start": _round_or_none(beat.get("start")) or 0.0,
            "end": _round_or_none(beat.get("end")) or 0.0,
            "duration": _round_or_none(beat.get("duration")) or 0.0,
            "source_start": _round_or_none(beat.get("source_start")) or _round_or_none(beat.get("start")) or 0.0,
            "source_end": _round_or_none(beat.get("source_end")) or _round_or_none(beat.get("end")) or 0.0,
            "source_duration": _round_or_none(beat.get("source_duration")) or _round_or_none(beat.get("duration")) or 0.0,
            "trimmed_for_montage": bool(beat.get("trimmed_for_montage")),
            "trim_reason": str(beat.get("trim_reason") or ""),
            "context_only": bool(beat.get("context_only")),
            "category": str(beat.get("category") or "unknown"),
            "label": _safe_text(beat.get("label"), 80),
            "hook_text": _safe_text(beat.get("hook_text"), MAX_TEXT_CHARS),
            "payoff_language": bool(beat.get("payoff_language")),
            "repetition_penalty": _round_or_none(beat.get("repetition_penalty")) or 0.0,
            "evidence": _beat_evidence(beat),
            "transition_after": transition,
            "subtitle_policy": "creator",
            "score": round(_safe_float(beat.get("score"), 0.0) or 0.0, 4),
        }
        storyboard_beats.append(storyboard_beat)
        render_plan.append(
            {
                "beat_id": storyboard_beat["beat_id"],
                "source_video": str(source.get("video") or ""),
                "clip_filename": storyboard_beat["clip_filename"],
                "start": storyboard_beat["start"],
                "end": storyboard_beat["end"],
                "source_start": storyboard_beat["source_start"],
                "source_end": storyboard_beat["source_end"],
                "transition_after": transition,
                "render_action": "segment_copy_later",
            }
        )

    storyboard_status, storyboard_reasons = _storyboard_status(selected, clean_audit)
    storyboard = {
        "schema_version": MONTAGE_STORYBOARD_SCHEMA_VERSION,
        "storyboard_id": _storyboard_id(clean_audit, selected, story_shape, target_duration, storyboard_index=storyboard_index),
        "created_at": created_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": storyboard_status,
        "ready": storyboard_status in {"ready", "thin_draft"},
        "reasons": storyboard_reasons,
        "source_ids": _source_ids(source, selected),
        "source": {
            "run_id": str(source.get("run_id") or ""),
            "video": str(source.get("video") or ""),
            "source_stem": str(source.get("source_stem") or ""),
            "game_title": str(source.get("game_title") or ""),
        },
        "settings": {
            "target_duration": round(target_duration, 3),
            "story_shape": _normalize_story_shape(story_shape),
            "memory_enabled": bool(memory_enabled),
            "render_quality": _normalize_render_quality(render_quality),
            "storyboard_index": max(1, int(_safe_float(storyboard_index, 1) or 1)),
            "excluded_beat_count": len(excluded),
        },
        "memory_snapshot": _memory_snapshot(clean_audit, memory_enabled=memory_enabled),
        "summary": {
            "beat_count": len(storyboard_beats),
            "planned_duration_seconds": round(sum(item["duration"] for item in storyboard_beats), 3),
            "category_counts": _category_counts(storyboard_beats),
        },
        "beats": storyboard_beats,
        "rejected": _compact_storyboard_rejections(clean_audit),
        "render_plan": render_plan,
        "audit": {
            "audit_id": str(clean_audit.get("audit_id") or ""),
            "status": str(clean_audit.get("status") or ""),
            "usable_beat_count": int((clean_audit.get("counts") or {}).get("usable_beat_count") or 0),
            "target_beat_count": target_beats,
        },
        "stores_raw_media": False,
        "stores_full_transcripts": False,
    }
    return storyboard


def write_storyboard(output_path: Path, storyboard: dict) -> Path:
    """Persist a storyboard JSON artifact."""

    return write_candidate_audit(output_path, storyboard)


def _compact_beat(row: dict, *, index: int, min_score: float, accepted_min_score: float) -> dict:
    start = max(0.0, _safe_float(row.get("start"), 0.0) or 0.0)
    end = max(start, _safe_float(row.get("end"), start) or start)
    duration = max(0.0, end - start)
    score = _score_for_row(row)
    selected = bool(row.get("selected"))
    accepted = bool(row.get("accepted"))
    category = _primary_category(row)
    reject_reason = str(row.get("reject_reason") or "").strip()
    low_value = category == "low_value"
    music_guard = _has_music_guard(row)
    black_frame_ratio = _black_frame_ratio(row)
    transcript = _row_transcript(row)
    first_word_start = _safe_float(_first_nested_value(row, "first_word_start"), None)
    last_word_end = _safe_float(_first_nested_value(row, "last_word_end"), None)
    word_count = int(_safe_float(row.get("word_count"), 0) or 0)

    usable = True
    audit_reject_reason = ""
    context_only = False
    if duration < DEFAULT_MIN_BEAT_SECONDS:
        usable = False
        audit_reject_reason = "too_short"
    elif low_value:
        usable = False
        audit_reject_reason = "low_value_category"
    elif music_guard:
        usable = False
        audit_reject_reason = "music_or_lyrics_guard"
    elif black_frame_ratio >= 0.82:
        usable = False
        audit_reject_reason = "mostly_black_frames"
    elif selected:
        usable = score >= min_score
        audit_reject_reason = "" if usable else "selected_score_below_floor"
    elif accepted:
        usable = score >= accepted_min_score
        audit_reject_reason = "" if usable else "accepted_score_below_floor"
    else:
        usable = False
        audit_reject_reason = reject_reason or "not_selected_or_accepted"

    if not usable:
        reason = (reject_reason or audit_reject_reason).strip().lower()
        context_score = min(score, CONTEXT_CONTINUATION_MAX_SCORE)
        has_some_signal = word_count >= 20 or bool(transcript.strip()) or bool(row.get("visual_diagnostics"))
        if (
            reason in CONTEXT_CONTINUATION_REJECT_REASONS
            and not low_value
            and not music_guard
            and black_frame_ratio < 0.82
            and duration >= DEFAULT_MIN_BEAT_SECONDS
            and score >= CONTEXT_CONTINUATION_MIN_SCORE
            and has_some_signal
        ):
            usable = True
            context_only = True
            audit_reject_reason = "context_continuation"
            score = context_score

    return {
        "beat_id": _beat_id(row, index),
        "source_index": index,
        "usable": usable,
        "audit_reject_reason": audit_reject_reason,
        "selected": selected,
        "accepted": accepted,
        "context_only": context_only,
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(duration, 3),
        "score": round(score, 4),
        "clip_id": _first_nested_text(row, "clip_id"),
        "source_id": _first_nested_text(row, "source_id"),
        "clip_filename": _first_nested_text(row, "clip_filename", "filename"),
        "category": category,
        "label": _safe_text(_label_for_row(row), 80),
        "hook_text": _safe_text(transcript, MAX_TEXT_CHARS),
        "payoff_language": _text_has_payoff_language(" ".join((transcript, _label_for_row(row), category))),
        "repetition_penalty": _repeated_filler_penalty(transcript),
        "has_transcript": bool(transcript.strip()),
        "word_count": word_count,
        "first_word_start": _round_or_none(first_word_start),
        "last_word_end": _round_or_none(last_word_end),
        "reject_reason": reject_reason,
        "rank_delta": _round_or_none(row.get("rank_delta")),
        "learning_delta": _round_or_none(row.get("learned_adjustment")),
        "voice_delta": _round_or_none(row.get("voice_ranking_adjustment") or row.get("voice_adjustment")),
        "visual_used": bool(row.get("visual_diagnostics")),
        "multimodal_used": bool(row.get("multimodal_analysis")),
        "ai_label_used": bool(row.get("ai_moment_classification")),
        "game_context_used": bool(row.get("game_context_nudge") or row.get("game_context_adjustment")),
    }


def _select_storyboard_beats(
    beats: list[dict],
    *,
    target_beats: int,
    target_duration: float,
    story_shape: str,
) -> list[dict]:
    if not beats:
        return []
    shape = _normalize_story_shape(story_shape)
    if shape in COHERENT_STORY_SHAPES:
        max_by_duration = target_beats
    else:
        max_by_duration = max(target_beats, min(MAX_STORYBOARD_BEATS, int(round(target_duration / 12.0)) or target_beats))
    max_count = max(1, min(MAX_STORYBOARD_BEATS, max_by_duration, len(beats)))
    ordered = sorted(
        beats,
        key=lambda item: (
            -_storyboard_pick_score(item, story_shape=shape),
            _safe_float(item.get("start"), 0.0) or 0.0,
        ),
    )
    coherent = _select_coherent_story_beats(
        beats,
        ordered,
        target_beats=target_beats,
        max_count=max_count,
        story_shape=shape,
    )
    if coherent:
        return _trim_selected_beats_to_duration(
            coherent,
            target_duration=target_duration,
            story_shape=shape,
        )
    selected: list[dict] = []
    used_categories: set[str] = set()
    used_starts: list[float] = []
    for beat in ordered:
        start = _safe_float(beat.get("start"), 0.0) or 0.0
        if any(abs(start - prior) < 2.0 for prior in used_starts):
            continue
        category = str(beat.get("category") or "unknown")
        if _should_skip_for_story_shape(beat, shape, selected_count=len(selected), target_beats=target_beats):
            continue
        if (
            category in used_categories
            and len(used_categories) < min(target_beats, 4)
            and len(selected) < target_beats
        ):
            continue
        selected.append(beat)
        used_categories.add(category)
        used_starts.append(start)
        if len(selected) >= max_count:
            break
    if len(selected) < min(target_beats, len(beats)):
        selected_ids = {id(item) for item in selected}
        for beat in ordered:
            if id(beat) in selected_ids:
                continue
            start = _safe_float(beat.get("start"), 0.0) or 0.0
            if any(abs(start - prior) < 2.0 for prior in used_starts):
                continue
            if _should_skip_for_story_shape(
                beat,
                shape,
                selected_count=len(selected),
                target_beats=target_beats,
                strict=False,
            ):
                continue
            selected.append(beat)
            selected_ids.add(id(beat))
            used_starts.append(start)
            if len(selected) >= min(target_beats, max_count, len(beats)):
                break
    sequenced = _sequence_storyboard_beats(selected, story_shape=shape)
    return _trim_selected_beats_to_duration(sequenced, target_duration=target_duration, story_shape=shape)


def _select_coherent_story_beats(
    beats: list[dict],
    ordered: list[dict],
    *,
    target_beats: int,
    max_count: int,
    story_shape: str,
) -> list[dict]:
    if story_shape not in COHERENT_STORY_SHAPES or not beats or not ordered:
        return []
    window = _story_cohesion_window(story_shape)
    minimum_count = min(max(2, target_beats), len(beats), max_count)
    best: list[dict] = []
    best_score = -999.0
    for anchor in ordered[: min(12, len(ordered))]:
        anchor_mid = _beat_midpoint(anchor)
        pool = [
            beat for beat in beats
            if abs(_beat_midpoint(beat) - anchor_mid) <= window
        ]
        if len(pool) < minimum_count:
            continue
        pool_ordered = sorted(
            pool,
            key=lambda item: (
                -_storyboard_pick_score(item, story_shape=story_shape)
                - _topic_compatibility_score(anchor, item)
                - _temporal_continuation_bonus(anchor, item),
                abs(_beat_midpoint(item) - anchor_mid),
            ),
        )
        chosen: list[dict] = []
        starts: list[float] = []
        for beat in pool_ordered:
            start = _safe_float(beat.get("start"), 0.0) or 0.0
            if any(abs(start - prior) < 2.0 for prior in starts):
                continue
            if _should_skip_for_story_shape(
                beat,
                story_shape,
                selected_count=len(chosen),
                target_beats=target_beats,
            ):
                continue
            chosen.append(beat)
            starts.append(start)
            if len(chosen) >= max_count:
                break
        if len(chosen) < minimum_count:
            continue
        chronological = sorted(chosen, key=lambda item: _safe_float(item.get("start"), 0.0) or 0.0)
        chronological = _repair_disconnected_story_cluster(
            chronological,
            beats,
            story_shape=story_shape,
            max_count=max_count,
        )
        span = max(_safe_float(item.get("end"), 0.0) or 0.0 for item in chronological) - min(
            _safe_float(item.get("start"), 0.0) or 0.0 for item in chronological
        )
        avg_score = sum(_storyboard_pick_score(item, story_shape=story_shape) for item in chronological) / len(chronological)
        transcript_ratio = sum(1 for item in chronological if str(item.get("hook_text") or "").strip()) / len(chronological)
        topic_score = _cluster_topic_score(chronological)
        story_arc_score = _cluster_story_arc_score(chronological, story_shape=story_shape)
        context_bonus = sum(1 for item in chronological if item.get("context_only")) / len(chronological) * 0.025
        cluster_score = (
            avg_score
            + transcript_ratio * 0.04
            + len(chronological) * 0.01
            + topic_score
            + story_arc_score
            + context_bonus
            - min(1.0, span / max(window, 1.0)) * 0.18
        )
        if cluster_score > best_score:
            best = chronological
            best_score = cluster_score
    return best


def _story_cohesion_window(story_shape: str) -> float:
    if story_shape == "setup_escalate_punchline":
        return 240.0
    if story_shape in {"story_reveal", "tutorial_story", "atmosphere_build"}:
        return 480.0
    return 300.0


def _beat_midpoint(beat: dict) -> float:
    start = _safe_float(beat.get("start"), 0.0) or 0.0
    end = _safe_float(beat.get("end"), start) or start
    return start + max(0.0, end - start) / 2.0


def _beat_topic_families(beat: dict) -> set[str]:
    text = _beat_text(beat)
    families: set[str] = set()
    for family, terms in TOPIC_FAMILY_TERMS.items():
        if any(term in text for term in terms):
            families.add(family)
    category = str(beat.get("category") or "").lower()
    if category in {"lore_or_story", "cinematic_dialogue"}:
        families.add("story")
    if category == "death_or_failure":
        families.add("failure")
    return families


def _topic_compatibility_score(anchor: dict, beat: dict) -> float:
    if beat is anchor:
        return 0.0
    anchor_families = _beat_topic_families(anchor)
    beat_families = _beat_topic_families(beat)
    if not anchor_families or not beat_families:
        return 0.0
    if anchor_families & beat_families:
        return 0.055
    for family in anchor_families:
        if TOPIC_RELATED.get(family, set()) & beat_families:
            return 0.03
    if "minigame" in (anchor_families | beat_families) and (anchor_families | beat_families) - {"minigame", "tutorial"}:
        return -0.42
    if {"entry", "combat"} <= (anchor_families | beat_families):
        return -0.18
    return -0.025


def _temporal_continuation_bonus(anchor: dict, beat: dict) -> float:
    if beat is anchor:
        return 0.0
    anchor_end = _safe_float(anchor.get("end"), _beat_midpoint(anchor)) or _beat_midpoint(anchor)
    beat_start = _safe_float(beat.get("start"), _beat_midpoint(beat)) or _beat_midpoint(beat)
    gap = beat_start - anchor_end
    if 0 <= gap <= 90:
        return 0.28 if beat.get("context_only") else 0.045
    if 90 < gap <= 180:
        return 0.025
    return 0.0


def _cluster_topic_score(beats: list[dict]) -> float:
    if len(beats) < 2:
        return 0.0
    pairs = 0
    score = 0.0
    for index, left in enumerate(beats):
        for right in beats[index + 1:]:
            score += _topic_compatibility_score(left, right)
            pairs += 1
    return max(-0.35, min(0.16, score / max(1, pairs)))


def _cluster_story_arc_score(beats: list[dict], *, story_shape: str) -> float:
    if story_shape != "setup_escalate_punchline":
        return 0.0
    text = " ".join(_beat_text(beat) for beat in beats)
    score = 0.0
    if any(term in text for term in ("bribe", "credits", "scam", "scammed", "tricked", "not worth", "worth it")):
        score += 0.16
    if any(term in text for term in ("sneak", "get in", "door", "bouncer", "check a list")) and any(
        term in text for term in ("bribe", "credits", "not worth", "scam", "scammed")
    ):
        score += 0.08
    if any(term in text for term in ("pay", "paid", "cost", "money")) and any(
        term in text for term in ("not worth", "wrong", "scam", "tricked")
    ):
        score += 0.05
    return min(score, 0.24)


def _repair_disconnected_story_cluster(
    selected: list[dict],
    candidates: list[dict],
    *,
    story_shape: str,
    max_count: int,
) -> list[dict]:
    if story_shape != "setup_escalate_punchline" or len(selected) < 3:
        return selected
    selected_families = [_beat_topic_families(beat) for beat in selected]
    has_minigame = any("minigame" in families for families in selected_families)
    selected_text = " ".join(_beat_text(beat) for beat in selected)
    has_entry_arc = any(term in selected_text for term in ("bribe", "credits", "check a list", "sneak in", "get in"))
    if not has_minigame or not has_entry_arc:
        return selected

    selected_ids = {str(beat.get("beat_id") or "") for beat in selected}
    arc_end = max(
        _safe_float(beat.get("end"), 0.0) or 0.0
        for beat in selected
        if "minigame" not in _beat_topic_families(beat)
    )
    alternates = []
    for beat in candidates:
        beat_id = str(beat.get("beat_id") or "")
        if beat_id in selected_ids:
            continue
        if "minigame" in _beat_topic_families(beat):
            continue
        start = _safe_float(beat.get("start"), 0.0) or 0.0
        if start < arc_end or start - arc_end > 180:
            continue
        alternates.append(beat)
    if not alternates:
        return selected

    replacement = max(
        alternates,
        key=lambda beat: (
            0.16 if beat.get("context_only") else 0.0,
            -abs((_safe_float(beat.get("start"), 0.0) or 0.0) - arc_end),
            _storyboard_pick_score(beat, story_shape=story_shape),
        ),
    )
    repaired: list[dict] = []
    replaced = False
    for beat, families in zip(selected, selected_families):
        if not replaced and "minigame" in families:
            repaired.append(replacement)
            replaced = True
        else:
            repaired.append(beat)
    repaired = sorted(repaired[:max_count], key=lambda item: _safe_float(item.get("start"), 0.0) or 0.0)
    return repaired


def _storyboard_pick_score(beat: dict, *, story_shape: str = "hook_escalate_payoff") -> float:
    score = _safe_float(beat.get("score"), 0.0) or 0.0
    category = str(beat.get("category") or "").lower()
    if category in {"high_energy", "death_or_failure"}:
        score += 0.06
    elif category in {"lore_or_story", "cinematic_dialogue", "tutorial_or_explainer"}:
        score += 0.035
    elif category in {"atmosphere_or_visual"}:
        score += 0.025
    if beat.get("selected"):
        score += 0.025
    if beat.get("visual_used") or beat.get("multimodal_used"):
        score += 0.015
    if abs(_safe_float(beat.get("learning_delta"), 0.0) or 0.0) > 0:
        score += 0.012
    if beat.get("context_only"):
        score -= 0.04
    score -= _safe_float(beat.get("repetition_penalty"), 0.0) or 0.0
    score += _story_shape_bonus(beat, _normalize_story_shape(story_shape))
    return score


def _story_shape_bonus(beat: dict, story_shape: str) -> float:
    category = str(beat.get("category") or "").lower()
    text = _beat_text(beat)
    has_transcript = bool(str(beat.get("hook_text") or "").strip())
    word_count = int(_safe_float(beat.get("word_count"), 0) or 0)
    if story_shape == "setup_escalate_punchline":
        bonus = 0.0
        if any(phrase in text for phrase in FUNNY_PHRASES):
            bonus += 0.12
        if category in {"commentary_or_review", "high_energy"}:
            bonus += 0.045
        if word_count >= 8:
            bonus += 0.035
        if not has_transcript:
            bonus -= 0.12
        if category == "atmosphere_or_visual" and not has_transcript:
            bonus -= 0.06
        return bonus
    if story_shape == "tutorial_story":
        return 0.09 if category in {"tutorial_or_explainer", "commentary_or_review"} else 0.0
    if story_shape == "story_reveal":
        return 0.075 if category in {"lore_or_story", "cinematic_dialogue", "commentary_or_review"} else 0.0
    if story_shape == "atmosphere_build":
        return 0.07 if category in {"atmosphere_or_visual", "lore_or_story"} else 0.0
    if story_shape == "failure_recovery_payoff":
        return 0.08 if category in {"death_or_failure", "high_energy"} else 0.0
    if story_shape == "combat_escalation":
        return 0.07 if category in {"high_energy", "death_or_failure"} else 0.0
    return 0.0


def _should_skip_for_story_shape(
    beat: dict,
    story_shape: str,
    *,
    selected_count: int,
    target_beats: int,
    strict: bool = True,
) -> bool:
    if story_shape != "setup_escalate_punchline":
        return False
    if selected_count >= max(1, target_beats):
        return False
    has_transcript = bool(str(beat.get("hook_text") or "").strip())
    category = str(beat.get("category") or "").lower()
    if (
        (_safe_float(beat.get("repetition_penalty"), 0.0) or 0.0) >= 0.14
        and not _has_payoff_language(beat)
    ):
        return True
    if has_transcript:
        return False
    if strict and category == "atmosphere_or_visual":
        return True
    return False


def _sequence_storyboard_beats(beats: list[dict], *, story_shape: str) -> list[dict]:
    if not beats:
        return []
    shape = _normalize_story_shape(story_shape)
    if shape != "setup_escalate_punchline":
        return sorted(beats, key=lambda item: _safe_float(item.get("start"), 0.0) or 0.0)

    ordered = sorted(beats, key=lambda item: -_storyboard_pick_score(item, story_shape=shape))
    payoff = ordered[0]
    payoff_start = _safe_float(payoff.get("start"), 0.0) or 0.0
    earlier = [
        item
        for item in beats
        if item is not payoff and (_safe_float(item.get("start"), 0.0) or 0.0) < payoff_start
    ]
    if earlier:
        setup = max(earlier, key=lambda item: _storyboard_pick_score(item, story_shape=shape))
        rest = [item for item in ordered if item is not payoff and item is not setup]
        return [setup, *rest[: max(0, len(beats) - 2)], payoff]
    return ordered


def _trim_selected_beats_to_duration(
    beats: list[dict],
    *,
    target_duration: float,
    story_shape: str,
) -> list[dict]:
    if not beats:
        return []
    target_duration = max(DEFAULT_MIN_BEAT_SECONDS, _safe_float(target_duration, DEFAULT_TARGET_DURATION_SECONDS) or DEFAULT_TARGET_DURATION_SECONDS)
    per_beat_budget = target_duration / max(1, len(beats))
    slow_shape = story_shape in {"tutorial_story", "story_reveal", "atmosphere_build"}
    if story_shape == "setup_escalate_punchline":
        max_beat_seconds = FUNNY_CONTEXT_MAX_BEAT_SECONDS
    else:
        max_beat_seconds = SLOW_CONTEXT_MAX_BEAT_SECONDS if slow_shape else DEFAULT_MAX_BEAT_SECONDS
    beat_budget = max(DEFAULT_MIN_BEAT_SECONDS, min(max_beat_seconds, per_beat_budget))
    remaining = target_duration
    trimmed: list[dict] = []
    for index, beat in enumerate(beats):
        source_start = _safe_float(beat.get("start"), 0.0) or 0.0
        source_end = _safe_float(beat.get("end"), source_start) or source_start
        source_duration = max(0.0, source_end - source_start)
        remaining_slots = max(1, len(beats) - index)
        desired_duration = _desired_beat_duration(
            index,
            len(beats),
            story_shape=story_shape,
            default_duration=beat_budget,
        )
        if _has_payoff_language(beat):
            desired_duration = max(desired_duration, min(FUNNY_PAYOFF_MAX_BEAT_SECONDS, 24.0))
        allowed = min(source_duration, desired_duration, remaining - DEFAULT_MIN_BEAT_SECONDS * (remaining_slots - 1))
        if allowed < DEFAULT_MIN_BEAT_SECONDS and source_duration >= DEFAULT_MIN_BEAT_SECONDS:
            allowed = min(source_duration, DEFAULT_MIN_BEAT_SECONDS)
        if allowed <= 0:
            continue
        trim_start = _trim_anchor_start(
            beat,
            source_start,
            source_end,
            allowed,
            story_shape=story_shape,
            beat_index=index,
            beat_count=len(beats),
        )
        trim_end = min(source_end, trim_start + allowed)
        if trim_end - trim_start < min(DEFAULT_MIN_BEAT_SECONDS, source_duration):
            trim_start = max(source_start, source_end - allowed)
            trim_end = min(source_end, trim_start + allowed)
        copy_beat = dict(beat)
        copy_beat["source_start"] = round(source_start, 3)
        copy_beat["source_end"] = round(source_end, 3)
        copy_beat["source_duration"] = round(source_duration, 3)
        copy_beat["start"] = round(trim_start, 3)
        copy_beat["end"] = round(trim_end, 3)
        copy_beat["duration"] = round(max(0.0, trim_end - trim_start), 3)
        copy_beat["trimmed_for_montage"] = bool(abs(trim_start - source_start) > 0.001 or abs(trim_end - source_end) > 0.001)
        copy_beat["trim_reason"] = _trim_reason(beat, story_shape=story_shape, beat_index=index, beat_count=len(beats))
        trimmed.append(copy_beat)
        remaining -= max(0.0, trim_end - trim_start)
        if remaining <= 0.25:
            break
    return trimmed


def _desired_beat_duration(
    index: int,
    beat_count: int,
    *,
    story_shape: str,
    default_duration: float,
) -> float:
    if beat_count >= 3 and story_shape == "setup_escalate_punchline":
        if index == 0:
            return min(default_duration, 14.0)
        if index == beat_count - 1:
            return max(default_duration, FUNNY_PAYOFF_MAX_BEAT_SECONDS)
        return min(max(default_duration, 16.0), 18.0)
    if beat_count >= 3 and story_shape in {"hook_escalate_payoff", "story_reveal"} and index == beat_count - 1:
        return max(default_duration, 24.0)
    return default_duration


def _trim_anchor_start(
    beat: dict,
    source_start: float,
    source_end: float,
    duration: float,
    *,
    story_shape: str,
    beat_index: int,
    beat_count: int,
) -> float:
    source_duration = max(0.0, source_end - source_start)
    first_word = _safe_float(beat.get("first_word_start"), None)
    is_payoff = beat_count >= 2 and beat_index == beat_count - 1
    if is_payoff and _has_payoff_language(beat) and source_duration > duration:
        latest = max(source_start, source_end - duration)
        if first_word is not None and 0 <= first_word <= max(source_duration, 0.0):
            desired = min(source_start + first_word - 1.5, latest)
        else:
            desired = latest
    elif first_word is not None and 0 <= first_word <= max(source_duration, 0.0):
        desired = source_start + first_word - 1.5
    else:
        desired = source_start
    desired = max(source_start, min(desired, source_end - duration))
    return desired


def _trim_reason(beat: dict, *, story_shape: str, beat_index: int, beat_count: int) -> str:
    if beat_count >= 2 and beat_index == beat_count - 1 and _has_payoff_language(beat):
        return "payoff_context"
    if story_shape == "setup_escalate_punchline":
        return "funny_story_pacing"
    return "balanced_story_pacing"


def _has_payoff_language(beat: dict) -> bool:
    if beat.get("payoff_language"):
        return True
    return _text_has_payoff_language(_beat_text(beat))


def _text_has_payoff_language(text: str) -> bool:
    text = str(text or "").lower()
    return any(
        phrase in text
        for phrase in (
            "not worth",
            "worth it",
            "scam",
            "scammed",
            "tricked",
            "actually",
            "payoff",
            "no way",
            "wait what",
            "that's hilarious",
        )
    )


def _repeated_filler_penalty(text: str) -> float:
    """Return a small penalty for chatter loops that rarely carry montage story."""
    normal = str(text or "").lower()
    tokens = re.findall(r"[a-z0-9']+", normal)
    if len(tokens) < 8:
        return 0.0
    unique_ratio = len(set(tokens)) / max(1, len(tokens))
    repeated_bigrams: dict[tuple[str, str], int] = {}
    for left, right in zip(tokens, tokens[1:]):
        if left in {"the", "a", "an", "to", "of", "and", "or"} and right in {"the", "a", "an", "to", "of", "and", "or"}:
            continue
        key = (left, right)
        repeated_bigrams[key] = repeated_bigrams.get(key, 0) + 1
    max_bigram = max(repeated_bigrams.values() or [0])
    same_token_runs = 0
    current_run = 1
    for index in range(1, len(tokens)):
        if tokens[index] == tokens[index - 1]:
            current_run += 1
            same_token_runs = max(same_token_runs, current_run)
        else:
            current_run = 1

    penalty = 0.0
    if unique_ratio <= 0.42:
        penalty += 0.06
    if max_bigram >= 3:
        penalty += 0.10
    if max_bigram >= 5:
        penalty += 0.04
    if same_token_runs >= 3:
        penalty += 0.06
    if _text_has_payoff_language(normal):
        penalty *= 0.45
    return round(min(0.20, penalty), 4)


def _assign_roles(beats: list[dict], *, story_shape: str) -> dict[str, str]:
    if not beats:
        return {}
    shape = _normalize_story_shape(story_shape)
    roles: dict[str, str] = {}
    if len(beats) == 1:
        roles[str(beats[0].get("beat_id"))] = "hook"
        return roles
    if len(beats) == 2:
        if shape == "setup_escalate_punchline":
            roles[str(beats[0].get("beat_id"))] = "setup"
            roles[str(beats[1].get("beat_id"))] = "punchline"
        else:
            roles[str(beats[0].get("beat_id"))] = "hook"
            roles[str(beats[1].get("beat_id"))] = "payoff"
        return roles
    if shape == "setup_escalate_punchline":
        roles[str(beats[0].get("beat_id"))] = "setup"
        roles[str(beats[-1].get("beat_id"))] = "punchline"
        for beat in beats[1:-1]:
            roles[str(beat.get("beat_id"))] = "escalation"
        return roles
    roles[str(beats[0].get("beat_id"))] = "hook"
    roles[str(beats[-1].get("beat_id"))] = "payoff"
    middle_role = "explain" if shape == "tutorial_story" else "escalation"
    for beat in beats[1:-1]:
        roles[str(beat.get("beat_id"))] = middle_role
    return roles


def _storyboard_status(beats: list[dict], audit: dict) -> tuple[str, list[str]]:
    if not beats:
        return "no_storyboard", ["no usable beats available for storyboard"]
    target_beats = max(1, int((audit.get("counts") or {}).get("target_beat_count") or DEFAULT_TARGET_BEATS))
    if len(beats) < min(target_beats, int((audit.get("counts") or {}).get("usable_beat_count") or target_beats)):
        return "thin_draft", [f"storyboard selected {len(beats)} beat(s), target is {target_beats}"]
    audit_status = str(audit.get("status") or "")
    if audit_status == "ready":
        return "ready", ["storyboard drafted from ready audit"]
    return "thin_draft", ["storyboard drafted with limited usable beats"]


def _beat_evidence(beat: dict) -> list[str]:
    evidence = [f"category:{beat.get('category') or 'unknown'}", f"score:{round(_safe_float(beat.get('score'), 0.0) or 0.0, 2)}"]
    if beat.get("visual_used"):
        evidence.append("visual")
    if beat.get("multimodal_used"):
        evidence.append("multimodal")
    if beat.get("ai_label_used"):
        evidence.append("ai_label")
    if beat.get("game_context_used"):
        evidence.append("game_context")
    if abs(_safe_float(beat.get("learning_delta"), 0.0) or 0.0) > 0:
        evidence.append("local_learning")
    if abs(_safe_float(beat.get("voice_delta"), 0.0) or 0.0) > 0:
        evidence.append("voice_profile")
    return evidence


def _memory_snapshot(audit: dict, *, memory_enabled: bool) -> dict:
    status = audit.get("feature_status") if isinstance(audit.get("feature_status"), dict) else {}
    return {
        "local_only": True,
        "stores_raw_media": False,
        "stores_full_transcripts": False,
        "memory_enabled": bool(memory_enabled),
        "feedback_signal_count": 1 if status.get("learning_used") else 0,
        "voice_profile_used": bool(status.get("voice_profile_used")),
        "game_context_used": bool(status.get("game_context_used")),
        "visual_used": bool(status.get("visual_used")),
        "multimodal_used": bool(status.get("multimodal_used")),
        "ai_label_used": bool(status.get("ai_label_used")),
    }


def _compact_storyboard_rejections(audit: dict) -> list[dict]:
    summary = audit.get("rejected_summary") if isinstance(audit.get("rejected_summary"), dict) else {}
    return [
        {"reason": str(reason), "count": int(_safe_float(count, 0) or 0)}
        for reason, count in sorted(summary.items())
        if int(_safe_float(count, 0) or 0) > 0
    ]


def _source_ids(source: dict, beats: list[dict]) -> list[str]:
    values = [str(source.get("run_id") or "")]
    values.extend(str(beat.get("source_id") or "") for beat in beats)
    return [value for value in _dedupe(values) if value]


def _storyboard_id(
    audit: dict,
    beats: list[dict],
    story_shape: str,
    target_duration: float,
    *,
    storyboard_index: int = 1,
) -> str:
    raw = "|".join(
        [
            str(audit.get("audit_id") or ""),
            _normalize_story_shape(story_shape),
            str(round(target_duration, 3)),
            str(max(1, int(_safe_float(storyboard_index, 1) or 1))),
            ",".join(str(beat.get("beat_id") or "") for beat in beats),
        ]
    )
    return "montage_" + hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _normalize_story_shape(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", str(value or "hook_escalate_payoff").lower()).strip("_")
    return cleaned or "hook_escalate_payoff"


def _normalize_render_quality(value: str) -> str:
    cleaned = str(value or "draft").lower().strip()
    return cleaned if cleaned in {"draft", "final"} else "draft"


def _audit_status(
    beats: list[dict],
    *,
    target_beats: int,
    target_duration_seconds: float | None,
    usable_duration_seconds: float,
    feature_status: dict,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not beats:
        return "no_usable_beats", ["no usable selected or accepted candidates"]
    if len(beats) < target_beats:
        reasons.append(f"only {len(beats)} usable beat(s), target is {target_beats}")
    if target_duration_seconds and usable_duration_seconds < max(8.0, target_duration_seconds * 0.55):
        reasons.append("usable beats are short for the requested montage length")
    if not feature_status.get("has_category_variety") and len(beats) >= target_beats:
        reasons.append("usable beats have limited category variety")
    if reasons:
        return "thin", reasons
    return "ready", ["enough usable beats for storyboard planning"]


def _recommendations(status: str, reasons: list[str], beats: list[dict], feature_status: dict, settings: dict) -> list[str]:
    recs: list[str] = []
    if status == "ready":
        recs.append("Proceed to storyboard-only planning before rendering.")
    if not beats:
        recs.append("Run Balanced or Deep Analysis on the source before montage planning.")
    elif status == "thin":
        recs.append("Use the current beats for a short montage draft or generate more candidates.")
    if not feature_status.get("visual_used"):
        recs.append("Visual analysis did not contribute to the usable beat set.")
    if not feature_status.get("learning_used"):
        recs.append("No local learning deltas were present in usable beats.")
    depth = str((settings.get("generation") or {}).get("mode") or settings.get("processing_depth") or "").lower()
    if depth == "clip":
        recs.append("Montage intent was not selected for this run.")
    for reason in reasons:
        if "limited category variety" in reason:
            recs.append("Storyboard should avoid repeating the same beat type unless the user requests a highlight reel.")
            break
    return _dedupe(recs)


def _feature_status(payload: dict, beats: list[dict]) -> dict:
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    categories = {str(item.get("category") or "unknown") for item in beats}
    learning_used = any(abs(_safe_float(item.get("learning_delta"), 0.0) or 0.0) > 0 for item in beats)
    voice_used = any(abs(_safe_float(item.get("voice_delta"), 0.0) or 0.0) > 0 for item in beats)
    return {
        "visual_used": any(bool(item.get("visual_used")) for item in beats),
        "multimodal_used": any(bool(item.get("multimodal_used")) for item in beats),
        "ai_label_used": any(bool(item.get("ai_label_used")) for item in beats),
        "game_context_used": any(bool(item.get("game_context_used")) for item in beats),
        "learning_used": learning_used,
        "voice_profile_used": voice_used,
        "has_category_variety": len(categories - {"unknown", ""}) >= 2,
        "generation_mode": str((settings.get("generation") or {}).get("mode") or ""),
        "processing_depth": str(settings.get("processing_depth") or ""),
    }


def _quality_summary(beats: list[dict]) -> dict:
    scores = sorted(_safe_float(item.get("score"), 0.0) or 0.0 for item in beats)
    if not scores:
        return {"min": None, "max": None, "avg": None}
    return {
        "min": round(scores[0], 4),
        "max": round(scores[-1], 4),
        "avg": round(sum(scores) / len(scores), 4),
    }


def _category_counts(beats: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for item in beats:
        category = str(item.get("category") or "unknown")
        counts[category] = counts.get(category, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _beat_sort_key(beat: dict) -> tuple[int, float, float]:
    return (
        0 if beat.get("selected") else 1,
        -(_safe_float(beat.get("score"), 0.0) or 0.0),
        _safe_float(beat.get("start"), 0.0) or 0.0,
    )


def _score_for_row(row: dict) -> float:
    for key in (
        "multi_signal_ai_quality_score",
        "multimodal_quality_score",
        "ai_moment_quality_score",
        "moment_category_quality_score",
        "voice_profile_quality_score",
        "selection_quality_score",
        "learned_quality_score",
        "quality_score",
        "base_quality_score",
    ):
        value = _safe_float(row.get(key), None)
        if value is not None:
            return max(0.0, min(1.0, value))
    return 0.0


def _primary_category(row: dict) -> str:
    for key in ("final_primary_category", "ranking_primary_category", "selection_primary_category", "primary_category"):
        value = str(row.get(key) or "").strip()
        if value:
            return value[:80]
    categories = row.get("moment_categories")
    if isinstance(categories, dict):
        value = str(categories.get("primary") or "").strip()
        if value:
            return value[:80]
    return "unknown"


def _row_transcript(row: dict) -> str:
    return _first_nested_text(row, "transcript", "final_transcript", "caption_text")


def _first_nested_value(row: dict, key: str) -> Any:
    sources = [
        row,
        row.get("ranker") if isinstance(row.get("ranker"), dict) else {},
        row.get("selection") if isinstance(row.get("selection"), dict) else {},
        row.get("final") if isinstance(row.get("final"), dict) else {},
        row.get("candidate") if isinstance(row.get("candidate"), dict) else {},
    ]
    for source in sources:
        if not isinstance(source, dict):
            continue
        value = source.get(key)
        if value is not None:
            return value
    return None


def _first_nested_text(row: dict, *keys: str) -> str:
    sources = [
        row,
        row.get("selection") if isinstance(row.get("selection"), dict) else {},
        row.get("final") if isinstance(row.get("final"), dict) else {},
        row.get("candidate") if isinstance(row.get("candidate"), dict) else {},
    ]
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = str(source.get(key) or "").strip()
            if value:
                return _safe_text(value, 180)
    return ""


def _beat_text(beat: dict) -> str:
    return " ".join(
        str(value or "")
        for value in (
            beat.get("hook_text"),
            beat.get("label"),
            beat.get("category"),
        )
    ).lower()


def _label_for_row(row: dict) -> str:
    ai = row.get("ai_moment_classification") if isinstance(row.get("ai_moment_classification"), dict) else {}
    for key in ("label", "moment_label", "title", "primary_category"):
        value = str(ai.get(key) or "").strip()
        if value:
            return value
    return _primary_category(row).replace("_", " ").title()


def _has_music_guard(row: dict) -> bool:
    guard = row.get("music_lyrics_guard") if isinstance(row.get("music_lyrics_guard"), dict) else {}
    status = str(guard.get("status") or guard.get("decision") or "").lower()
    if status in {"blocked", "reject", "rejected", "lyrics_detected"}:
        return True
    penalty = _safe_float(row.get("music_lyrics_penalty") or guard.get("penalty"), 0.0) or 0.0
    return penalty <= -0.18


def _black_frame_ratio(row: dict) -> float:
    visual = row.get("visual_diagnostics") if isinstance(row.get("visual_diagnostics"), dict) else {}
    return _safe_float(visual.get("black_frame_ratio"), 0.0) or 0.0


def _audit_id(payload: dict, target_beats: int, target_duration: float | None) -> str:
    raw = "|".join(
        [
            str(payload.get("run_id") or ""),
            str(payload.get("video") or ""),
            str(payload.get("debug_stage") or ""),
            str(target_beats),
            str(target_duration or ""),
        ]
    )
    return "montage_audit_" + hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _beat_id(row: dict, index: int) -> str:
    raw = "|".join(
        [
            str(index),
            str(row.get("start") or ""),
            str(row.get("end") or ""),
            _row_transcript(row)[:80],
        ]
    )
    return "beat_" + hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:14]


def _source_stem(payload: dict) -> str:
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    source_context = settings.get("source_context") if isinstance(settings.get("source_context"), dict) else {}
    value = source_context.get("source_stem") or Path(str(payload.get("video") or "")).stem
    return _safe_text(value, 140)


def _game_title(payload: dict) -> str:
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    identity = settings.get("game_identity") if isinstance(settings.get("game_identity"), dict) else {}
    context = settings.get("game_context") if isinstance(settings.get("game_context"), dict) else {}
    prompt = context.get("prompt_context") if isinstance(context.get("prompt_context"), dict) else {}
    return _safe_text(identity.get("title") or prompt.get("title") or "", 140)


def _safe_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if result != result or result in (float("inf"), float("-inf")):
        return default
    return result


def _round_or_none(value: Any) -> float | None:
    number = _safe_float(value, None)
    if number is None:
        return None
    return round(number, 4)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
