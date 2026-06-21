"""Transcript-aware candidate ranking and trimming for gameplay clips."""

from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path


HOOK_WEIGHTS = (
    ("right behind", 6.0),
    ("oh my god", 4.0),
    ("what the", 3.0),
    ("what just happened", 4.0),
    ("dont tell me", 4.0),
    ("don't tell me", 4.0),
    ("too easy", 3.0),
    ("so close", 3.0),
    ("run", 2.0),
    ("hide", 2.0),
    ("kill", 2.0),
    ("hit", 2.0),
    ("scary", 2.0),
    ("scarier", 2.0),
    ("please", 1.5),
    ("wait", 1.5),
    ("whoa", 2.0),
)

WEAK_WEIGHTS = (
    ("where am i", 3.0),
    ("all the way back", 4.0),
    ("new strat", 4.0),
    ("parrying", 2.0),
    ("i can't do anything", 3.0),
    ("i cant do anything", 3.0),
    ("going forward or back", 4.0),
)

AFTERMATH_WEIGHTS = (
    ("did we die", 4.0),
    ("we died", 4.0),
    ("restart", 3.0),
    ("what just happened", 2.0),
)

MIN_QUALITY_SCORE = 0.50
MIN_WORDS = 6
MIN_PEAK_TAIL = 8
MAX_EXTENSION = 10
SHADOW_SCORING_SCHEMA_VERSION = 2
SHADOW_MAX_ADJUSTMENT = 0.18
LEARNED_SELECTION_MAX_ADJUSTMENT = 0.06
SHADOW_MAX_TERMS_PER_EVENT = 40
SHADOW_STOP_TERMS = {
    "about", "after", "again", "also", "and", "are", "back", "because",
    "been", "before", "being", "but", "can", "could", "did", "does",
    "doing", "dont", "from", "get", "got", "had", "has", "have", "here",
    "him", "his", "how", "into", "its", "just", "like", "look", "make",
    "more", "much", "not", "now", "off", "one", "only", "out", "over",
    "really", "right", "see", "she", "should", "some", "that", "the",
    "their", "them", "then", "there", "they", "this", "too", "very",
    "was", "way", "were", "what", "when", "where", "who", "why", "with",
    "would", "you", "your",
}


def transcript_text(words: list[dict]) -> str:
    return " ".join(w.get("text", "").strip() for w in words if w.get("text", "").strip())


def clean_words(words: list[dict]) -> list[dict]:
    cleaned = []
    for word in words or []:
        text = str(word.get("text", "")).strip()
        if not text:
            continue
        try:
            start = float(word["start"])
            end = float(word["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            end = start + 0.08
        cleaned.append({"text": text, "start": start, "end": end})
    return cleaned


def needs_stream_retry(words: list[dict], duration: float) -> bool:
    words = clean_words(words)
    if len(words) < MIN_WORDS:
        return True
    first_start = words[0]["start"]
    text = _normal_text(transcript_text(words))
    if first_start > min(12.0, max(5.0, duration * 0.45)) and _weighted_score(text, HOOK_WEIGHTS) < 4:
        return True
    if _weighted_score(text, WEAK_WEIGHTS) >= 4 and _weighted_score(text, HOOK_WEIGHTS) < 4:
        return True
    return False


def evaluate_candidate(
    candidate: dict,
    words: list[dict],
    extraction_start: float,
    extraction_end: float,
    video_duration: float,
    target_duration: int,
    selected_stream: int | None,
) -> dict:
    words = clean_words(words)
    text = transcript_text(words)
    normal = _normal_text(text)
    hook_points = _weighted_score(normal, HOOK_WEIGHTS)
    weak_points = _weighted_score(normal, WEAK_WEIGHTS)
    aftermath_points = _weighted_score(normal, AFTERMATH_WEIGHTS)
    duration = max(1.0, float(candidate["end"]) - float(candidate["start"]))
    word_count = len(words)
    first_word_start = words[0]["start"] if words else None
    last_word_end = words[-1]["end"] if words else None

    detector_score = min(float(candidate.get("score", 0.0)) / 0.75, 1.0)
    density_score = min(word_count / max(12.0, duration * 1.25), 1.0)
    hook_score = min(hook_points / 10.0, 1.0)
    weak_penalty = min(weak_points * 0.08, 0.42)
    aftermath_penalty = min(aftermath_points * 0.08, 0.38)
    late_penalty = 0.0
    if first_word_start is None:
        late_penalty = 0.35
    elif first_word_start > 12 and hook_points < 5:
        late_penalty = 0.12

    quality = (
        0.25 * detector_score
        + 0.28 * density_score
        + 0.52 * hook_score
        - weak_penalty
        - late_penalty
    )
    if candidate.get("candidate_kind") == "primary" and aftermath_points:
        quality -= aftermath_penalty
    else:
        quality -= min(aftermath_penalty, 0.12)
    quality = max(0.0, min(1.0, quality))

    render_start, render_end, render_words = trim_candidate_with_transcript(
        candidate, words, extraction_start, extraction_end, video_duration, target_duration
    )

    reject_reason = ""
    if word_count < MIN_WORDS:
        reject_reason = "too_few_words"
    elif quality < MIN_QUALITY_SCORE:
        reject_reason = "low_transcript_quality"
    elif not render_words:
        reject_reason = "empty_after_trim"

    moment = {
        **candidate,
        "start": int(render_start),
        "end": int(render_end),
        "duration": int(render_end - render_start),
        "quality_score": float(round(quality, 4)),
        "transcript": transcript_text(render_words),
        "word_count": len(render_words),
        "speech_stream": selected_stream,
        "subtitle_generated": False,
        "subtitles_burned": False,
        "transcript_source": "pipeline",
        "ranker": {
            "hook_points": hook_points,
            "weak_points": weak_points,
            "aftermath_points": aftermath_points,
            "first_word_start": first_word_start,
            "last_word_end": last_word_end,
            "reject_reason": reject_reason,
        },
    }

    return {
        "accepted": reject_reason == "",
        "reject_reason": reject_reason,
        "quality_score": quality,
        "candidate": candidate,
        "moment": moment,
        "words": render_words,
        "transcript": text,
        "word_count": word_count,
        "selected_stream": selected_stream,
    }


def trim_candidate_with_transcript(
    candidate: dict,
    words: list[dict],
    extraction_start: float,
    extraction_end: float,
    video_duration: float,
    target_duration: int,
) -> tuple[int, int, list[dict]]:
    cand_start = float(candidate["start"])
    cand_end = float(candidate["end"])
    peak = float(candidate.get("peak_time", cand_start + target_duration / 2))
    max_end = min(float(video_duration), max(float(extraction_end), cand_end))

    if not words:
        start = int(max(0, math.floor(cand_start)))
        end = int(min(video_duration, math.ceil(cand_end)))
        return start, end, []

    hook_start_rel = _best_hook_start(words)
    first_speech_abs = extraction_start + words[0]["start"]
    if candidate.get("candidate_kind") == "pre_event":
        desired_start = first_speech_abs - 1.5
    elif hook_start_rel is not None:
        desired_start = extraction_start + hook_start_rel - 2.0
    else:
        desired_start = first_speech_abs - 1.5
    desired_start = max(cand_start, desired_start)

    latest_start = max(cand_start, peak - 2.0)
    desired_start = min(desired_start, latest_start)
    render_start = int(max(0, math.floor(desired_start)))

    setup_min_end = render_start + 10
    min_end = max(setup_min_end, peak + MIN_PEAK_TAIL)
    if _terminal_payoff_before(words, extraction_start, setup_min_end, min_end):
        min_end = setup_min_end
    natural_end = _natural_end_after(words, extraction_start, min_end, max_end)
    if natural_end is None:
        last_abs = extraction_start + words[-1]["end"] + 0.35
        natural_end = max(min_end, last_abs)

    hard_end = min(float(video_duration), render_start + target_duration + MAX_EXTENSION)
    render_end = int(min(video_duration, math.ceil(min(natural_end, hard_end))))
    if render_end <= render_start:
        render_end = int(min(video_duration, render_start + max(1, target_duration)))

    render_words = []
    word_cutoff = min(float(render_end), float(natural_end) + 0.05)
    for word in words:
        abs_start = extraction_start + word["start"]
        abs_end = extraction_start + word["end"]
        if abs_end < render_start or abs_start > word_cutoff:
            continue
        render_words.append(
            {
                "text": word["text"],
                "start": max(0.0, abs_start - render_start),
                "end": max(0.08, abs_end - render_start),
            }
        )

    return render_start, render_end, render_words


def select_best_candidates(
    evaluations: list[dict],
    max_count: int,
    min_gap: int = 12,
    score_key: str = "quality_score",
) -> list[dict]:
    viable = [e for e in evaluations if e.get("accepted")]
    viable.sort(
        key=lambda e: (
            float(e.get(score_key, e.get("quality_score", 0.0))),
            float(e.get("quality_score", 0.0)),
        ),
        reverse=True,
    )

    selected = []
    for evaluation in viable:
        moment = evaluation["moment"]
        if _overlaps_selected(moment, selected, min_gap):
            evaluation["reject_reason"] = "overlaps_better_candidate"
            moment["ranker"]["reject_reason"] = evaluation["reject_reason"]
            continue
        selected.append(evaluation)
        if len(selected) >= max_count:
            break

    for idx, evaluation in enumerate(selected, 1):
        base_quality = float(evaluation.get("quality_score", 0.0))
        rank_score = float(evaluation.get(score_key, base_quality))
        evaluation["selection_quality_score"] = round(base_quality, 4)
        evaluation["selection_rank_score"] = round(rank_score, 4)
        evaluation["selection_score_source"] = score_key
        evaluation["moment"]["quality_rank"] = idx
        evaluation["moment"]["selection_quality_score"] = evaluation["selection_quality_score"]
        evaluation["moment"]["selection_rank_score"] = evaluation["selection_rank_score"]
        evaluation["moment"]["selection_score_source"] = score_key
        if "learned_quality_score" in evaluation:
            evaluation["moment"]["learned_quality_score"] = round(float(evaluation["learned_quality_score"]), 4)
        shadow = evaluation.get("shadow_scoring") or {}
        if "learned_adjustment" in shadow:
            evaluation["moment"]["learned_adjustment"] = shadow.get("learned_adjustment")
        evaluation["selection_moment"] = copy.deepcopy(evaluation["moment"])
    selected.sort(key=lambda e: e["moment"]["start"])
    return selected


def apply_learned_scoring(
    evaluations: list[dict],
    personalization: dict | None,
    *,
    source_id: str = "",
    source_stem: str = "",
    max_adjustment: float = LEARNED_SELECTION_MAX_ADJUSTMENT,
) -> dict:
    profile = _build_shadow_profile(personalization or {})
    accepted = [e for e in evaluations if e.get("accepted")]
    baseline_order = sorted(
        accepted,
        key=lambda e: float(e.get("quality_score", 0.0)),
        reverse=True,
    )
    baseline_rank_by_id = {id(e): idx for idx, e in enumerate(baseline_order, 1)}
    learned_enabled = profile["signal_count"] > 0 and max_adjustment > 0

    for evaluation in evaluations:
        shadow = _score_shadow_candidate(evaluation, profile, source_id, source_stem)
        base_score = float(evaluation.get("quality_score", 0.0))
        raw_adjustment = float(shadow.get("adjustment", 0.0))
        learned_adjustment = 0.0
        if learned_enabled:
            learned_adjustment = max(-max_adjustment, min(max_adjustment, raw_adjustment))
        learned_score = max(0.0, min(1.0, base_score + learned_adjustment))
        shadow.update(
            {
                "baseline_rank": baseline_rank_by_id.get(id(evaluation)),
                "learned_selection_enabled": learned_enabled,
                "learned_selection_cap": round(float(max_adjustment), 4),
                "learned_adjustment": round(learned_adjustment, 4),
                "learned_quality_score": round(learned_score, 4),
                "selected_by_current": False,
                "would_select": False,
                "shadow_rank": None,
                "learned_rank": None,
                "rank_delta": None,
                "selection_delta": "",
            }
        )
        evaluation["learned_quality_score"] = learned_score
        evaluation["shadow_scoring"] = shadow

    return {
        "profile": profile,
        "learned_enabled": learned_enabled,
        "max_adjustment": max_adjustment,
    }


def build_learning_status(
    personalization: dict | None,
    *,
    max_adjustment: float = LEARNED_SELECTION_MAX_ADJUSTMENT,
) -> dict:
    """Return the same local-learning signal summary used by candidate scoring."""
    profile = _build_shadow_profile(personalization or {})
    cap = max(0.0, float(max_adjustment or 0.0))
    scoring_signal_count = int(profile.get("signal_count") or 0)
    return {
        "enabled": scoring_signal_count > 0 and cap > 0,
        "active_feedback_signals": int(profile.get("active_event_count") or 0),
        "scoring_signal_count": scoring_signal_count,
        "positive_feedback_signals": int(profile.get("positive_feedback_count") or 0),
        "negative_feedback_signals": int(profile.get("negative_feedback_count") or 0),
        "favorite_signals": int(profile.get("favorite_count") or 0),
        "learned_cap": round(cap, 4),
        "learned_cap_label": f"+/-{cap:.2f}",
    }


def build_shadow_scoring_report(
    evaluations: list[dict],
    selected: list[dict],
    personalization: dict | None,
    *,
    source_id: str = "",
    source_stem: str = "",
    max_count: int = 0,
    min_gap: int = 12,
    max_adjustment: float = LEARNED_SELECTION_MAX_ADJUSTMENT,
) -> dict:
    """Build local-learning diagnostics for the selected candidate set."""
    if not all("shadow_scoring" in evaluation for evaluation in evaluations):
        prepared = apply_learned_scoring(
            evaluations,
            personalization,
            source_id=source_id,
            source_stem=source_stem,
            max_adjustment=max_adjustment,
        )
        profile = prepared["profile"]
    else:
        profile = _build_shadow_profile(personalization or {})

    accepted = [e for e in evaluations if e.get("accepted")]
    target_count = max(0, int(max_count or len(selected) or len(accepted)))
    selected_ids = {id(e) for e in selected}

    baseline_order = sorted(
        accepted,
        key=lambda e: float(e.get("quality_score", 0.0)),
        reverse=True,
    )
    baseline_rank_by_id = {id(e): idx for idx, e in enumerate(baseline_order, 1)}
    baseline_selected = _select_for_report(baseline_order, target_count, min_gap)
    baseline_selected_ids = {id(e) for e in baseline_selected}

    for evaluation in evaluations:
        shadow = evaluation.get("shadow_scoring") or _score_shadow_candidate(evaluation, profile, source_id, source_stem)
        shadow["baseline_rank"] = baseline_rank_by_id.get(id(evaluation))
        shadow["selected_by_current"] = id(evaluation) in selected_ids
        shadow["selected_by_baseline"] = id(evaluation) in baseline_selected_ids
        shadow["would_select"] = id(evaluation) in selected_ids
        shadow["shadow_rank"] = None
        shadow["learned_rank"] = None
        shadow["rank_delta"] = None
        shadow["selection_delta"] = ""
        evaluation["shadow_scoring"] = shadow

    shadow_order = sorted(
        accepted,
        key=lambda e: (
            float(e.get("shadow_scoring", {}).get("learned_quality_score", e.get("learned_quality_score", e.get("quality_score", 0.0)))),
            float(e.get("quality_score", 0.0)),
        ),
        reverse=True,
    )
    for idx, evaluation in enumerate(shadow_order, 1):
        shadow = evaluation["shadow_scoring"]
        shadow["shadow_rank"] = idx
        shadow["learned_rank"] = idx
        baseline_rank = shadow.get("baseline_rank")
        if baseline_rank is not None:
            shadow["rank_delta"] = int(baseline_rank) - idx

    shadow_selected = _select_for_report(shadow_order, target_count, min_gap)
    shadow_selected_ids = {id(e) for e in shadow_selected}

    for evaluation in evaluations:
        shadow = evaluation.get("shadow_scoring", {})
        baseline = id(evaluation) in baseline_selected_ids
        learned = id(evaluation) in selected_ids
        shadow["selected_by_current"] = learned
        shadow["selected_by_learned"] = learned
        shadow["would_select"] = id(evaluation) in shadow_selected_ids
        if baseline and learned:
            shadow["selection_delta"] = "kept"
        elif baseline and not learned:
            shadow["selection_delta"] = "dropped_by_learning"
        elif not baseline and learned:
            shadow["selection_delta"] = "added_by_learning"
        elif shadow.get("rank_delta"):
            shadow["selection_delta"] = "rank_changed"

    selection_delta_counts: dict[str, int] = {}
    for evaluation in evaluations:
        selection_delta = evaluation.get("shadow_scoring", {}).get("selection_delta", "")
        if selection_delta:
            selection_delta_counts[selection_delta] = selection_delta_counts.get(selection_delta, 0) + 1

    top_changes = []
    for evaluation in accepted:
        shadow = evaluation.get("shadow_scoring", {})
        rank_delta = shadow.get("rank_delta")
        selection_delta = shadow.get("selection_delta", "")
        if not rank_delta and selection_delta not in {"added_by_learning", "dropped_by_learning"}:
            continue
        moment = evaluation.get("selection_moment") or evaluation.get("moment", {})
        top_changes.append(
            {
                "candidate_rank": evaluation.get("candidate", {}).get("candidate_rank"),
                "candidate_kind": evaluation.get("candidate", {}).get("candidate_kind", ""),
                "start": moment.get("start"),
                "end": moment.get("end"),
                "quality_score": round(float(evaluation.get("quality_score", 0.0)), 4),
                "shadow_score": shadow.get("shadow_score"),
                "learned_quality_score": shadow.get("learned_quality_score"),
                "learned_adjustment": shadow.get("learned_adjustment"),
                "baseline_rank": shadow.get("baseline_rank"),
                "shadow_rank": shadow.get("learned_rank"),
                "rank_delta": rank_delta,
                "selection_delta": selection_delta,
                "signals": shadow.get("signals", {}),
                "transcript_preview": _preview_text(moment.get("transcript", "")),
            }
        )
    top_changes.sort(
        key=lambda row: (
            row.get("selection_delta") in {"added_by_learning", "dropped_by_learning"},
            abs(int(row.get("rank_delta") or 0)),
            float(row.get("learned_quality_score") or row.get("shadow_score") or 0.0),
        ),
        reverse=True,
    )
    output_changed = baseline_selected_ids != selected_ids

    return {
        "schema_version": SHADOW_SCORING_SCHEMA_VERSION,
        "mode": "learned_blend",
        "output_changed": output_changed,
        "selection_score_source": "learned_quality_score",
        "learned_selection_max_adjustment": round(float(max_adjustment), 4),
        "has_learning_signals": profile["signal_count"] > 0,
        "candidate_count": len(evaluations),
        "accepted_count": len(accepted),
        "baseline_selected_count": len(baseline_selected),
        "learned_selected_count": len(selected),
        "current_selected_count": len(selected),
        "shadow_selected_count": len(shadow_selected),
        "selection_delta_counts": selection_delta_counts,
        "profile": _shadow_profile_summary(profile),
        "baseline_selected": [_shadow_selection_summary(e) for e in baseline_selected],
        "learned_selected": [_shadow_selection_summary(e) for e in selected],
        "current_selected": [_shadow_selection_summary(e) for e in selected],
        "shadow_selected": [_shadow_selection_summary(e) for e in shadow_selected],
        "top_changes": top_changes[:10],
    }


def write_debug_report(
    output_path: Path,
    video_path: Path,
    candidates: list[dict],
    evaluations: list[dict],
    selected: list[dict],
    *,
    scene_detection: dict | None = None,
    settings: dict | None = None,
    video_duration: float | None = None,
    final_clips: list[dict] | None = None,
    warnings: list[str] | None = None,
    shadow_scoring: dict | None = None,
    run_id: str | None = None,
    debug_stage: str | None = None,
) -> None:
    rows = []
    selected_ids = {id(e) for e in selected}
    debug_stage = debug_stage or ("run_post_render" if final_clips is not None else "candidate_pre_render")
    for evaluation in evaluations:
        selection_moment = evaluation.get("selection_moment") or evaluation["moment"]
        final_moment = evaluation["moment"] if id(evaluation) in selected_ids else None
        candidate = evaluation["candidate"]
        shadow = evaluation.get("shadow_scoring", {})
        base_quality = round(float(evaluation.get("quality_score", 0.0)), 4)
        selection_quality = round(
            float(evaluation.get("selection_quality_score", base_quality)),
            4,
        )
        learned_score = shadow.get("learned_quality_score", evaluation.get("learned_quality_score"))
        rows.append(
            {
                "selected": id(evaluation) in selected_ids,
                "accepted": evaluation.get("accepted", False),
                "reject_reason": evaluation.get("reject_reason") or selection_moment["ranker"].get("reject_reason", ""),
                "start": selection_moment["start"],
                "end": selection_moment["end"],
                "base_quality_score": base_quality,
                "quality_score": base_quality,
                "selection_quality_score": selection_quality,
                "selection_rank_score": evaluation.get("selection_rank_score"),
                "selection_score_source": evaluation.get("selection_score_source", "quality_score"),
                "learned_adjustment": shadow.get("learned_adjustment"),
                "learned_score": learned_score,
                "learned_quality_score": learned_score,
                "rank_delta": shadow.get("rank_delta"),
                "selection_delta": shadow.get("selection_delta", ""),
                "selection": _moment_summary(selection_moment, selection_quality),
                "final": _moment_summary(final_moment) if final_moment else None,
                "word_count": evaluation.get("word_count", 0),
                "transcript": selection_moment.get("transcript", ""),
                "candidate": candidate,
                "ranker": selection_moment.get("ranker", {}),
                "shadow_scoring": shadow,
            }
        )

    payload = {
        "run_id": run_id,
        "debug_stage": debug_stage,
        "final_render_metadata_included": final_clips is not None,
        "video": str(video_path),
        "video_duration": video_duration,
        "settings": settings or {},
        "scene_detection": scene_detection or {},
        "warnings": warnings or [],
        "shadow_scoring": shadow_scoring or {},
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "final_clips": final_clips or [],
        "candidates": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _select_for_report(ordered_evaluations: list[dict], max_count: int, min_gap: int) -> list[dict]:
    selected: list[dict] = []
    if max_count <= 0:
        return selected
    for evaluation in ordered_evaluations:
        if not evaluation.get("accepted"):
            continue
        if _overlaps_selected(evaluation["moment"], selected, min_gap):
            continue
        selected.append(evaluation)
        if len(selected) >= max_count:
            break
    return selected


def _build_shadow_profile(personalization: dict) -> dict:
    events = personalization.get("events", [])
    clips = personalization.get("clips", {})
    if not isinstance(events, list):
        events = []
    if not isinstance(clips, dict):
        clips = {}

    profile = {
        "event_count": len(events),
        "clip_count": len(clips),
        "active_event_count": 0,
        "positive_feedback_count": 0,
        "negative_feedback_count": 0,
        "favorite_count": 0,
        "positive_terms": {},
        "negative_terms": {},
        "positive_sources": {},
        "negative_sources": {},
        "signal_count": 0,
    }

    for signal in _current_feedback_signals(events, clips):
        _add_feedback_signal(profile, signal)

    profile["signal_count"] = (
        len(profile["positive_terms"])
        + len(profile["negative_terms"])
        + len(profile["positive_sources"])
        + len(profile["negative_sources"])
    )
    return profile


def _current_feedback_signals(events: list[dict], clips: dict) -> list[dict]:
    signals = _signals_from_clip_summaries(clips)
    if _has_clip_summaries(clips):
        return signals
    return _signals_from_event_replay(events)


def _has_clip_summaries(clips: dict) -> bool:
    return any(isinstance(entry, dict) for entry in clips.values())


def _signals_from_clip_summaries(clips: dict) -> list[dict]:
    signals = []
    for clip_id, entry in clips.items():
        if not isinstance(entry, dict):
            continue
        latest = entry.get("latest", {})
        if not isinstance(latest, dict):
            latest = {}
        base = {
            "clip_id": entry.get("clip_id") or clip_id,
            "source_id": entry.get("source_id", ""),
            "source_stem": entry.get("source_stem", ""),
            "reason": latest.get("reason", ""),
            "clip_snapshot": entry.get("clip_snapshot") if isinstance(entry.get("clip_snapshot"), dict) else {},
        }
        if latest.get("like"):
            signals.append({**base, "event_type": "like"})
        if latest.get("dislike"):
            signals.append({**base, "event_type": "dislike"})
        if latest.get("favorite"):
            signals.append({**base, "event_type": "favorite"})
    return signals


def _signals_from_event_replay(events: list[dict]) -> list[dict]:
    state: dict[str, dict] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        clip_id = str(event.get("clip_id") or "").strip()
        if not clip_id:
            continue
        current = state.setdefault(
            clip_id,
            {
                "clip_id": clip_id,
                "source_id": event.get("source_id", ""),
                "source_stem": event.get("source_stem", ""),
                "reason": "",
                "clip_snapshot": {},
                "like": False,
                "dislike": False,
                "favorite": False,
            },
        )
        current["source_id"] = event.get("source_id", current.get("source_id", ""))
        current["source_stem"] = event.get("source_stem", current.get("source_stem", ""))
        current["reason"] = event.get("reason", current.get("reason", ""))
        if isinstance(event.get("clip_snapshot"), dict):
            current["clip_snapshot"] = event["clip_snapshot"]
        if {"like", "dislike", "favorite"}.issubset(event.keys()):
            current["like"] = bool(event.get("like"))
            current["dislike"] = bool(event.get("dislike"))
            current["favorite"] = bool(event.get("favorite"))
            continue

        event_type = str(event.get("event_type") or "").lower()
        active = bool(event.get("active", True))
        if event_type == "like":
            current["like"] = active
            if active:
                current["dislike"] = False
        elif event_type == "dislike":
            current["dislike"] = active
            if active:
                current["like"] = False
        elif event_type == "favorite":
            current["favorite"] = active

    return _signals_from_clip_summaries(
        {
            clip_id: {
                "clip_id": row.get("clip_id", clip_id),
                "source_id": row.get("source_id", ""),
                "source_stem": row.get("source_stem", ""),
                "clip_snapshot": row.get("clip_snapshot", {}),
                "latest": {
                    "like": row.get("like", False),
                    "dislike": row.get("dislike", False),
                    "favorite": row.get("favorite", False),
                    "reason": row.get("reason", ""),
                },
            }
            for clip_id, row in state.items()
        }
    )


def _add_feedback_signal(profile: dict, signal: dict):
    event_type = str(signal.get("event_type") or "").lower()
    weight = 0.0
    if event_type == "favorite":
        weight = 1.35
        profile["favorite_count"] += 1
    elif event_type == "like":
        weight = 1.0
    elif event_type == "dislike":
        weight = -1.15
    if weight == 0:
        return

    profile["active_event_count"] += 1
    if weight > 0:
        profile["positive_feedback_count"] += 1
    else:
        profile["negative_feedback_count"] += 1

    text_parts = [str(signal.get("reason") or "")]
    snapshot = signal.get("clip_snapshot")
    if isinstance(snapshot, dict):
        text_parts.append(str(snapshot.get("transcript") or ""))
    text = " ".join(part for part in text_parts if part)
    for term in _extract_shadow_terms(text):
        if weight > 0:
            _bump(profile["positive_terms"], term, abs(weight))
        else:
            _bump(profile["negative_terms"], term, abs(weight))

    source_key = str(signal.get("source_id") or signal.get("source_stem") or "").strip()
    if source_key:
        if weight > 0:
            _bump(profile["positive_sources"], source_key, abs(weight))
        else:
            _bump(profile["negative_sources"], source_key, abs(weight))


def _score_shadow_candidate(evaluation: dict, profile: dict, source_id: str, source_stem: str) -> dict:
    base_score = float(evaluation.get("quality_score", 0.0))
    moment = evaluation.get("selection_moment") or evaluation.get("moment") or {}
    transcript = " ".join(
        part for part in [
            str(evaluation.get("transcript") or ""),
            str(moment.get("transcript") or ""),
        ] if part
    )
    normal = _normal_text(transcript)
    candidate_terms = set(_extract_shadow_terms(transcript, limit=100))

    positive_matches = _match_shadow_terms(profile["positive_terms"], normal, candidate_terms)
    negative_matches = _match_shadow_terms(profile["negative_terms"], normal, candidate_terms)
    positive_points = sum(item["weight"] for item in positive_matches)
    negative_points = sum(item["weight"] for item in negative_matches)

    positive_adjustment = min(positive_points * 0.018, 0.14)
    negative_adjustment = min(negative_points * 0.020, 0.14)
    source_adjustment = _shadow_source_adjustment(profile, source_id, source_stem)
    adjustment = max(
        -SHADOW_MAX_ADJUSTMENT,
        min(SHADOW_MAX_ADJUSTMENT, positive_adjustment - negative_adjustment + source_adjustment),
    )
    shadow_score = max(0.0, min(1.0, base_score + adjustment))

    return {
        "base_score": round(base_score, 4),
        "shadow_score": round(shadow_score, 4),
        "adjustment": round(adjustment, 4),
        "signals": {
            "positive_matches": positive_matches[:8],
            "negative_matches": negative_matches[:8],
            "source_adjustment": round(source_adjustment, 4),
            "positive_points": round(positive_points, 3),
            "negative_points": round(negative_points, 3),
        },
    }


def _shadow_source_adjustment(profile: dict, source_id: str, source_stem: str) -> float:
    # Source-only signals are intentionally tiny because every candidate from
    # the same video would receive the same nudge and should not dominate text.
    keys = [str(source_id or "").strip(), str(source_stem or "").strip()]
    pos = 0.0
    neg = 0.0
    for key in keys:
        if not key:
            continue
        pos += float(profile["positive_sources"].get(key, 0.0))
        neg += float(profile["negative_sources"].get(key, 0.0))
    return max(-0.02, min(0.02, (pos - neg) * 0.004))


def _match_shadow_terms(term_weights: dict, normal_text: str, candidate_terms: set[str]) -> list[dict]:
    if not term_weights or not normal_text:
        return []
    padded = f" {normal_text} "
    matches = []
    for term, weight in term_weights.items():
        if " " in term:
            matched = f" {term} " in padded
        else:
            matched = term in candidate_terms
        if matched:
            matches.append({"term": term, "weight": round(float(weight), 3)})
    matches.sort(key=lambda item: item["weight"], reverse=True)
    return matches


def _extract_shadow_terms(text: str, limit: int = SHADOW_MAX_TERMS_PER_EVENT) -> list[str]:
    normal = _normal_text(text)
    if not normal:
        return []
    tokens = [
        token for token in normal.split()
        if len(token) >= 3 and token not in SHADOW_STOP_TERMS and not token.isdigit()
    ]
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str):
        if term and term not in seen:
            seen.add(term)
            terms.append(term)

    padded = f" {normal} "
    for phrase, _ in HOOK_WEIGHTS + WEAK_WEIGHTS + AFTERMATH_WEIGHTS:
        phrase_norm = _normal_text(phrase)
        if phrase_norm and f" {phrase_norm} " in padded:
            add(phrase_norm)

    for token in tokens:
        add(token)
    for idx in range(0, max(0, len(tokens) - 1)):
        add(f"{tokens[idx]} {tokens[idx + 1]}")

    return terms[:limit]


def _shadow_profile_summary(profile: dict) -> dict:
    return {
        "event_count": profile["event_count"],
        "clip_count": profile["clip_count"],
        "active_event_count": profile["active_event_count"],
        "positive_feedback_count": profile["positive_feedback_count"],
        "negative_feedback_count": profile["negative_feedback_count"],
        "favorite_count": profile["favorite_count"],
        "signal_count": profile["signal_count"],
        "positive_terms": _top_shadow_terms(profile["positive_terms"]),
        "negative_terms": _top_shadow_terms(profile["negative_terms"]),
    }


def _top_shadow_terms(term_weights: dict, limit: int = 12) -> list[dict]:
    rows = [
        {"term": term, "weight": round(float(weight), 3)}
        for term, weight in term_weights.items()
    ]
    rows.sort(key=lambda item: item["weight"], reverse=True)
    return rows[:limit]


def _shadow_selection_summary(evaluation: dict) -> dict:
    moment = evaluation.get("selection_moment") or evaluation.get("moment", {})
    shadow = evaluation.get("shadow_scoring", {})
    return {
        "candidate_rank": evaluation.get("candidate", {}).get("candidate_rank"),
        "candidate_kind": evaluation.get("candidate", {}).get("candidate_kind", ""),
        "start": moment.get("start"),
        "end": moment.get("end"),
        "quality_score": round(float(evaluation.get("quality_score", 0.0)), 4),
        "shadow_score": shadow.get("shadow_score"),
        "learned_score": shadow.get("learned_quality_score"),
        "learned_quality_score": shadow.get("learned_quality_score"),
        "learned_adjustment": shadow.get("learned_adjustment"),
        "baseline_rank": shadow.get("baseline_rank"),
        "shadow_rank": shadow.get("learned_rank", shadow.get("shadow_rank")),
        "rank_delta": shadow.get("rank_delta"),
        "selection_delta": shadow.get("selection_delta", ""),
        "transcript_preview": _preview_text(moment.get("transcript", "")),
    }


def _preview_text(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _bump(mapping: dict, key: str, amount: float):
    if not key:
        return
    mapping[key] = float(mapping.get(key, 0.0)) + float(amount)


def _moment_summary(moment: dict | None, quality_score: float | None = None) -> dict | None:
    if not moment:
        return None
    score = quality_score
    if score is None:
        score = moment.get("quality_score")
    return {
        "start": moment.get("start"),
        "end": moment.get("end"),
        "duration": moment.get("duration"),
        "quality_score": score,
        "selection_quality_score": moment.get("selection_quality_score"),
        "selection_rank_score": moment.get("selection_rank_score"),
        "quality_rank": moment.get("quality_rank"),
        "learned_adjustment": moment.get("learned_adjustment"),
        "learned_score": moment.get("learned_quality_score"),
        "learned_quality_score": moment.get("learned_quality_score"),
        "selection_score_source": moment.get("selection_score_source"),
        "word_count": moment.get("word_count"),
        "speech_stream": moment.get("speech_stream"),
        "subtitle_generated": moment.get("subtitle_generated"),
        "subtitles_burned": moment.get("subtitles_burned"),
        "subtitle_placement": moment.get("subtitle_placement"),
        "transcript": moment.get("transcript", ""),
        "ranker": moment.get("ranker", {}),
    }


def _normal_text(text: str) -> str:
    text = text.lower().replace("'", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _weighted_score(normal_text: str, weights: tuple[tuple[str, float], ...]) -> float:
    if not normal_text:
        return 0.0
    score = 0.0
    padded = f" {normal_text} "
    for phrase, weight in weights:
        phrase_norm = _normal_text(phrase)
        if phrase_norm and f" {phrase_norm} " in padded:
            score += weight
    return score


def _best_hook_start(words: list[dict]) -> float | None:
    tokens = [_normal_text(w["text"]) for w in words]
    best_idx = None
    best_score = 0.0
    for phrase, weight in HOOK_WEIGHTS:
        phrase_tokens = _normal_text(phrase).split()
        if not phrase_tokens:
            continue
        for idx in range(0, len(tokens) - len(phrase_tokens) + 1):
            if tokens[idx : idx + len(phrase_tokens)] == phrase_tokens and weight > best_score:
                best_score = weight
                best_idx = idx
    if best_idx is None:
        return None
    return float(words[best_idx]["start"])


def _natural_end_after(
    words: list[dict],
    extraction_start: float,
    min_abs_end: float,
    max_abs_end: float,
) -> float | None:
    best = None
    for idx, word in enumerate(words):
        word_abs_end = extraction_start + word["end"]
        if word_abs_end < min_abs_end:
            continue
        if word_abs_end > max_abs_end:
            break
        text = word["text"].rstrip()
        next_start = None
        next_text = ""
        if idx + 1 < len(words):
            next_start = extraction_start + words[idx + 1]["start"]
            next_text = _normal_text(words[idx + 1]["text"])
        gap = (next_start - word_abs_end) if next_start is not None else 1.0
        continues = next_text in {"and", "but", "so", "because", "then", "actually", "that", "it"}
        if _normal_text(text) in {"brother", "scarier", "scary"}:
            best = word_abs_end + 0.05
            if not continues:
                break
        if (text.endswith((".", "!", "?")) and not continues) or (gap >= 0.55 and not continues):
            best = word_abs_end + 0.25
            if gap >= 0.55 and not continues:
                break
    return best


def _terminal_payoff_before(
    words: list[dict],
    extraction_start: float,
    min_abs_end: float,
    max_abs_end: float,
) -> bool:
    for word in words:
        word_abs_end = extraction_start + word["end"]
        if word_abs_end < min_abs_end:
            continue
        if word_abs_end > max_abs_end:
            return False
        if _normal_text(word["text"]) in {"brother", "scarier", "scary"}:
            return True
    return False


def _overlaps_selected(moment: dict, selected: list[dict], min_gap: int) -> bool:
    start = int(moment["start"])
    end = int(moment["end"])
    for existing in selected:
        other = existing["moment"]
        if start < int(other["end"]) + min_gap and end > int(other["start"]) - min_gap:
            return True
    return False
