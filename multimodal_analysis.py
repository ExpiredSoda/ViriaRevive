"""Local Ollama vision analysis for gameplay clip candidates.

This module keeps multimodal work optional and bounded. It only runs when a
compatible local Ollama vision model is already installed, samples a few frames,
and returns compact JSON that can enrich metadata or make a tiny Deep Analysis
ranking adjustment.
"""

from __future__ import annotations

import base64
import json
import math
import re
import time
import urllib.request
from pathlib import Path

from title_generator import OLLAMA_URL, list_ollama_models
from game_context import compact_game_context_for_prompt
from visual_diagnostics import _candidate_sample_times, _read_frame_at
from speech_source_classifier import positive_boost_block_reason as speech_source_positive_boost_block_reason


MULTIMODAL_ANALYSIS_SCHEMA_VERSION = 1
MULTIMODAL_SELECTION_SCHEMA_VERSION = 1
MULTIMODAL_SELECTION_MAX_ADJUSTMENT = 0.020
VISION_ANALYSIS_TIMEOUT = 35
VISION_FRAME_WIDTH = 512
VISION_JPEG_QUALITY = 82
DEFAULT_VISION_MODEL = "qwen3-vl:latest"
VISION_MODEL_CANDIDATES = (
    DEFAULT_VISION_MODEL,
    "qwen3-vl:8b",
    "qwen3-vl:4b",
    "qwen3-vl:2b",
    "qwen2.5vl:3b",
    "qwen2.5vl:7b",
    "qwen2.5vl:latest",
    "llama3.2-vision:11b",
    "llama3.2-vision:latest",
    "llava:7b",
    "llava:13b",
    "llava:latest",
    "bakllava:latest",
    "minicpm-v:latest",
)
VISION_MODEL_NAME_HINTS = (
    "qwen3-vl",
    "qwen2.5vl",
    "qwen2.5-vl",
    "llama3.2-vision",
    "llava",
    "bakllava",
    "minicpm-v",
)
VISUAL_LABELS = {
    "visible_enemy_or_threat",
    "combat",
    "chase_or_panic",
    "death_or_failure",
    "tutorial_ui",
    "menu_or_pause",
    "black_screen",
    "low_action",
    "scenic",
    "vehicle",
    "dialogue_scene",
    "inventory_or_map",
    "facecam_visible",
    "creator_overlay",
}
PRIMARY_VISUAL_LABELS = {
    "high_energy",
    "death_or_failure",
    "tutorial_or_explainer",
    "lore_or_story",
    "atmosphere_or_visual",
    "commentary_or_review",
    "low_value",
    "unclear",
}
REJECT_FLAGS = {
    "black_screen",
    "menu_or_pause",
    "no_gameplay_action",
    "only_static_overlay",
    "unclear_frames",
}


def select_ollama_vision_model(models: list[str] | None = None) -> str:
    """Pick the best installed Ollama model that likely accepts images."""
    installed = [str(model or "").strip() for model in (models if models is not None else list_ollama_models())]
    installed = [model for model in installed if model]
    if not installed:
        return ""
    by_lower = {model.lower(): model for model in installed}
    for candidate in VISION_MODEL_CANDIDATES:
        lowered = candidate.lower()
        if lowered in by_lower:
            return by_lower[lowered]
        latest = lowered[:-7] if lowered.endswith(":latest") else lowered
        for model_lower, model in by_lower.items():
            if model_lower == latest or model_lower.startswith(f"{latest}:"):
                return model
    for hint in VISION_MODEL_NAME_HINTS:
        hint = hint.lower()
        for model_lower, model in by_lower.items():
            if hint in model_lower:
                return model
    return ""


def ollama_vision_status(models: list[str] | None = None) -> dict:
    try:
        installed = list_ollama_models() if models is None else list(models)
    except Exception:
        installed = []
    model = select_ollama_vision_model(installed)
    return {
        "schema_version": MULTIMODAL_ANALYSIS_SCHEMA_VERSION,
        "running": bool(installed),
        "model_ready": bool(model),
        "model": model,
        "models": installed,
        "supported_model_hints": list(VISION_MODEL_NAME_HINTS),
    }


def analyze_candidate_frames_with_ollama(
    video_path: str | Path,
    candidate: dict,
    *,
    transcript: str = "",
    game_title: str = "",
    game_context: dict | None = None,
    learning_context: dict | None = None,
    video_duration: float = 0.0,
    enabled: bool = True,
    model: str | None = None,
    max_frames: int = 3,
    timeout: int = VISION_ANALYSIS_TIMEOUT,
) -> dict:
    """Analyze sampled candidate frames with a local Ollama vision model."""
    started = time.monotonic()
    if not enabled:
        return _empty_analysis("disabled", elapsed=time.monotonic() - started)
    selected_model = str(model or "").strip() or select_ollama_vision_model()
    if not selected_model:
        return _empty_analysis("vision_model_missing", elapsed=time.monotonic() - started)

    frames = _extract_frame_images(video_path, candidate, video_duration, max_frames=max_frames)
    if not frames:
        return _empty_analysis(
            "no_frames",
            model=selected_model,
            elapsed=time.monotonic() - started,
        )

    prompt = _build_vision_prompt(
        candidate,
        transcript,
        game_title,
        game_context=game_context,
        learning_context=learning_context,
    )
    try:
        response = _ask_ollama_vision_json(
            prompt,
            [row["image"] for row in frames],
            selected_model,
            timeout=timeout,
        )
    except TimeoutError as exc:
        return _empty_analysis(
            "timeout",
            model=selected_model,
            frames=frames,
            error=str(exc)[:180],
            elapsed=time.monotonic() - started,
        )
    except Exception as exc:
        return _empty_analysis(
            "ollama_error",
            model=selected_model,
            frames=frames,
            error=str(exc)[:180],
            elapsed=time.monotonic() - started,
        )

    clean = sanitize_vision_analysis(
        response,
        model=selected_model,
        frames=frames,
        elapsed=time.monotonic() - started,
    )
    if clean["status"] == "ok":
        print(
            "[vision] "
            f"{selected_model}: {clean.get('primary_visual_label')} "
            f"conf={clean.get('confidence')} adj={clean.get('ranking_adjustment')}"
        )
    return clean


def apply_multimodal_scoring(
    evaluations: list[dict],
    *,
    enabled: bool = False,
    score_key: str = "ai_moment_quality_score",
    max_adjustment: float = MULTIMODAL_SELECTION_MAX_ADJUSTMENT,
    confidence_floor: float = 0.55,
) -> dict:
    """Blend local vision analysis into Deep Analysis candidate ranking."""
    safe_max = max(0.0, _safe_float(max_adjustment, 0.0) or 0.0)
    confidence_floor = max(0.0, min(1.0, _safe_float(confidence_floor, 0.55) or 0.55))
    ranking_enabled = bool(enabled and safe_max > 0)
    eligible_count = 0
    scored_count = 0
    rescued_count = 0
    statuses: dict[str, int] = {}

    for evaluation in evaluations or []:
        base_score = _safe_float(evaluation.get(score_key, evaluation.get("quality_score", 0.0)), 0.0) or 0.0
        analysis = _vision_for_evaluation(evaluation)
        status = str(analysis.get("status") or "missing")
        statuses[status] = statuses.get(status, 0) + 1
        eligibility = _vision_scoring_eligibility(analysis, evaluation, confidence_floor=confidence_floor)
        if eligibility["eligible"]:
            eligible_count += 1
        raw = _safe_float(analysis.get("ranking_adjustment"), 0.0) or 0.0
        raw = max(-safe_max, min(safe_max, raw))
        positive_block_reason = _vision_positive_block_reason(evaluation, analysis)
        if positive_block_reason and raw > 0:
            raw = 0.0
        adjustment = 0.0
        rescue_applied = False
        rescue_reason = ""
        if (
            ranking_enabled
            and not evaluation.get("accepted")
            and eligibility["eligible"]
            and _is_visual_rescue_candidate(evaluation, analysis, confidence_floor=confidence_floor)
        ):
            _apply_visual_rescue(evaluation)
            rescue_applied = True
            rescued_count += 1
            rescue_reason = "near_miss_visual_rescue"
        if ranking_enabled and evaluation.get("accepted") and eligibility["eligible"]:
            adjustment = raw
            if abs(adjustment) > 0.0001:
                scored_count += 1
        score = max(0.0, min(1.0, base_score + adjustment))
        scoring = {
            "schema_version": MULTIMODAL_SELECTION_SCHEMA_VERSION,
            "mode": "ollama_vision_blend",
            "ranking_enabled": ranking_enabled,
            "selection_impact": "capped_rank_adjustment" if ranking_enabled else "none",
            "score_source": score_key,
            "base_score": round(base_score, 4),
            "status": status,
            "model": analysis.get("model"),
            "primary_visual_label": analysis.get("primary_visual_label"),
            "confidence": analysis.get("confidence"),
            "scoring_eligible": bool(eligibility["eligible"]),
            "ineligible_reason": eligibility["reason"],
            "multimodal_selection_max_adjustment": round(safe_max, 4),
            "multimodal_adjustment": round(adjustment, 4),
            "multimodal_quality_score": round(score, 4),
            "visual_rescue_candidate": bool(evaluation.get("multimodal_rescue_candidate")),
            "visual_rescue_applied": rescue_applied,
            "visual_rescue_reason": rescue_reason,
            "positive_block_reason": positive_block_reason,
            "original_reject_reason": evaluation.get("original_reject_reason", ""),
            "selected_by_baseline": False,
            "selected_by_multimodal": False,
            "baseline_rank": None,
            "multimodal_rank": None,
            "rank_delta": None,
            "selection_delta": "",
        }
        evaluation["multimodal_quality_score"] = score
        evaluation["multimodal_scoring"] = scoring
        moment = evaluation.get("moment") if isinstance(evaluation.get("moment"), dict) else {}
        if isinstance(moment, dict):
            moment["multimodal_quality_score"] = round(score, 4)
            moment["multimodal_scoring"] = dict(scoring)

    return {
        "schema_version": MULTIMODAL_SELECTION_SCHEMA_VERSION,
        "mode": "ollama_vision_blend",
        "ranking_enabled": ranking_enabled,
        "selection_impact": "capped_rank_adjustment" if ranking_enabled else "none",
        "score_source": score_key,
        "multimodal_selection_max_adjustment": round(safe_max, 4),
        "confidence_floor": round(confidence_floor, 4),
        "has_multimodal_scores": scored_count > 0,
        "eligible_candidate_count": eligible_count,
        "scored_candidate_count": scored_count,
        "rescued_candidate_count": rescued_count,
        "statuses": statuses,
    }


def build_multimodal_ranking_report(
    evaluations: list[dict],
    baseline_selected: list[dict],
    selected: list[dict],
    *,
    enabled: bool = False,
    max_count: int = 0,
    min_gap: int = 12,
    score_key: str = "ai_moment_quality_score",
    multimodal_score_key: str = "multimodal_quality_score",
    max_adjustment: float = MULTIMODAL_SELECTION_MAX_ADJUSTMENT,
) -> dict:
    """Report actual local vision ranking impact."""
    prepared = (
        {"ranking_enabled": False, "has_multimodal_scores": False}
        if all("multimodal_scoring" in evaluation for evaluation in evaluations or [])
        else apply_multimodal_scoring(
            evaluations,
            enabled=enabled,
            score_key=score_key,
            max_adjustment=max_adjustment,
        )
    )
    if prepared.get("ranking_enabled") is False and all("multimodal_scoring" in evaluation for evaluation in evaluations or []):
        rows = [e.get("multimodal_scoring") or {} for e in evaluations]
        prepared = {
            "ranking_enabled": any(bool(row.get("ranking_enabled")) for row in rows),
            "has_multimodal_scores": any(abs(_safe_float(row.get("multimodal_adjustment"), 0.0) or 0.0) > 0 for row in rows),
            "eligible_candidate_count": sum(1 for row in rows if row.get("scoring_eligible")),
            "scored_candidate_count": sum(1 for row in rows if abs(_safe_float(row.get("multimodal_adjustment"), 0.0) or 0.0) > 0),
            "rescued_candidate_count": sum(1 for row in rows if row.get("visual_rescue_applied")),
            "multimodal_selection_max_adjustment": round(max_adjustment, 4),
        }

    accepted = [e for e in (evaluations or []) if e.get("accepted")]
    target_count = max(0, int(max_count or len(selected) or len(baseline_selected) or len(accepted)))
    baseline_order = sorted(
        accepted,
        key=lambda e: (
            _safe_float(e.get(score_key, e.get("quality_score", 0.0)), 0.0) or 0.0,
            _safe_float(e.get("quality_score", 0.0), 0.0) or 0.0,
        ),
        reverse=True,
    )
    multimodal_order = sorted(
        accepted,
        key=lambda e: (
            _safe_float(e.get(multimodal_score_key, e.get(score_key, e.get("quality_score", 0.0))), 0.0) or 0.0,
            _safe_float(e.get(score_key, e.get("quality_score", 0.0)), 0.0) or 0.0,
            _safe_float(e.get("quality_score", 0.0), 0.0) or 0.0,
        ),
        reverse=True,
    )
    baseline_rank_by_id = {id(e): idx for idx, e in enumerate(baseline_order, 1)}
    multimodal_rank_by_id = {id(e): idx for idx, e in enumerate(multimodal_order, 1)}
    baseline_selected = baseline_selected or _select_for_report(baseline_order, target_count, min_gap)
    selected = selected or baseline_selected
    baseline_ids = {id(e) for e in baseline_selected}
    selected_ids = {id(e) for e in selected}

    selection_delta_counts: dict[str, int] = {}
    top_changes = []
    for evaluation in evaluations or []:
        scoring = evaluation.get("multimodal_scoring") or {}
        baseline_rank = baseline_rank_by_id.get(id(evaluation))
        vision_rank = multimodal_rank_by_id.get(id(evaluation))
        rank_delta = None
        if baseline_rank is not None and vision_rank is not None:
            rank_delta = int(baseline_rank) - int(vision_rank)
        baseline = id(evaluation) in baseline_ids
        chosen = id(evaluation) in selected_ids
        if baseline and chosen:
            selection_delta = "kept"
        elif baseline and not chosen:
            selection_delta = "dropped_by_multimodal"
        elif not baseline and chosen:
            selection_delta = "added_by_multimodal"
        elif rank_delta:
            selection_delta = "rank_changed"
        else:
            selection_delta = ""
        scoring.update(
            {
                "baseline_rank": baseline_rank,
                "multimodal_rank": vision_rank,
                "rank_delta": rank_delta,
                "selected_by_baseline": baseline,
                "selected_by_multimodal": chosen,
                "selection_delta": selection_delta,
            }
        )
        evaluation["multimodal_scoring"] = scoring
        if isinstance(evaluation.get("moment"), dict):
            evaluation["moment"]["multimodal_scoring"] = dict(scoring)
        if selection_delta:
            selection_delta_counts[selection_delta] = selection_delta_counts.get(selection_delta, 0) + 1
        if evaluation.get("accepted") and (rank_delta or selection_delta in {"added_by_multimodal", "dropped_by_multimodal"}):
            moment = evaluation.get("selection_moment") or evaluation.get("moment", {})
            analysis = _vision_for_evaluation(evaluation)
            top_changes.append(
                {
                    "candidate_rank": evaluation.get("candidate", {}).get("candidate_rank"),
                    "candidate_kind": evaluation.get("candidate", {}).get("candidate_kind", ""),
                    "start": moment.get("start"),
                    "end": moment.get("end"),
                    "base_score": scoring.get("base_score"),
                    "multimodal_quality_score": scoring.get("multimodal_quality_score"),
                    "multimodal_adjustment": scoring.get("multimodal_adjustment"),
                    "primary_visual_label": analysis.get("primary_visual_label"),
                    "confidence": analysis.get("confidence"),
                    "baseline_rank": baseline_rank,
                    "multimodal_rank": vision_rank,
                    "rank_delta": rank_delta,
                    "selection_delta": selection_delta,
                    "visible_summary": analysis.get("visible_summary"),
                }
            )
    top_changes.sort(
        key=lambda row: (
            row.get("selection_delta") in {"added_by_multimodal", "dropped_by_multimodal"},
            abs(int(row.get("rank_delta") or 0)),
            abs(float(row.get("multimodal_adjustment") or 0.0)),
        ),
        reverse=True,
    )
    output_changed = baseline_ids != selected_ids
    usable_score_source = bool(prepared.get("ranking_enabled") and prepared.get("has_multimodal_scores"))
    return {
        "schema_version": MULTIMODAL_SELECTION_SCHEMA_VERSION,
        "mode": "ollama_vision_blend",
        "ranking_enabled": bool(prepared.get("ranking_enabled")),
        "selection_impact": "capped_rank_adjustment" if prepared.get("ranking_enabled") else "none",
        "output_changed": output_changed,
        "selection_score_source": multimodal_score_key if usable_score_source else score_key,
        "base_score_source": score_key,
        "multimodal_selection_max_adjustment": prepared.get("multimodal_selection_max_adjustment", round(max_adjustment, 4)),
        "confidence_floor": prepared.get("confidence_floor"),
        "has_multimodal_scores": bool(prepared.get("has_multimodal_scores")),
        "eligible_candidate_count": int(prepared.get("eligible_candidate_count") or 0),
        "scored_candidate_count": int(prepared.get("scored_candidate_count") or 0),
        "rescued_candidate_count": int(prepared.get("rescued_candidate_count") or 0),
        "candidate_count": len(evaluations or []),
        "accepted_count": len(accepted),
        "baseline_selected_count": len(baseline_selected),
        "selected_count": len(selected),
        "selection_delta_counts": selection_delta_counts,
        "baseline_selected": [_selection_summary(e, score_key) for e in baseline_selected],
        "selected": [_selection_summary(e, multimodal_score_key) for e in selected],
        "top_changes": top_changes[:10],
    }


def _extract_frame_images(
    video_path: str | Path,
    candidate: dict,
    video_duration: float,
    *,
    max_frames: int,
) -> list[dict]:
    try:
        import cv2
    except Exception:
        return []
    path = Path(video_path)
    frames = []
    for sample_time in _candidate_sample_times(candidate, float(video_duration or 0.0))[: max(1, int(max_frames or 1))]:
        ok, frame, status = _read_frame_at(cv2, path, sample_time, timeout=5.0)
        if not ok or frame is None:
            continue
        image = _encode_frame(cv2, frame)
        if not image:
            continue
        frames.append({"time": round(float(sample_time), 3), "image": image, "read_status": status})
    return frames


def _encode_frame(cv2, frame) -> str:
    height, width = frame.shape[:2]
    if width > VISION_FRAME_WIDTH:
        scale = VISION_FRAME_WIDTH / float(width)
        frame = cv2.resize(frame, (VISION_FRAME_WIDTH, max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), VISION_JPEG_QUALITY])
    if not ok:
        return ""
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def _build_vision_prompt(
    candidate: dict,
    transcript: str,
    game_title: str,
    *,
    game_context: dict | None = None,
    learning_context: dict | None = None,
) -> str:
    learning = learning_context if isinstance(learning_context, dict) else {}
    game_knowledge = compact_game_context_for_prompt(game_context)
    payload = {
        "game_title": _prompt_safe_text(game_title, limit=120),
        "game_knowledge": game_knowledge,
        "creator_learning": {
            "enabled": bool(learning.get("enabled")),
            "positive_terms": [
                _prompt_safe_text(term, limit=48)
                for term in (learning.get("positive_terms") or [])[:8]
                if str(term or "").strip()
            ],
            "negative_terms": [
                _prompt_safe_text(term, limit=48)
                for term in (learning.get("negative_terms") or [])[:8]
                if str(term or "").strip()
            ],
        },
        "candidate": {
            "start": candidate.get("start"),
            "end": candidate.get("end"),
            "duration": candidate.get("duration"),
            "peak_time": candidate.get("peak_time"),
            "candidate_rank": candidate.get("candidate_rank"),
            "candidate_kind": candidate.get("candidate_kind"),
            "audio_score": candidate.get("audio_score"),
            "scene_score": candidate.get("scene_score"),
            "variance_score": candidate.get("variance_score"),
            "visual_diagnostics": candidate.get("visual_diagnostics"),
        },
        "transcript_preview": _prompt_safe_text(transcript, limit=900),
    }
    return (
        "You inspect sampled frames from a gameplay clip for a local Shorts clipping app.\n"
        "Use the images plus transcript preview. Use game knowledge as background only. "
        "Do not invent game names, enemy names, places, or events.\n"
        "Return exactly one JSON object with this schema:\n"
        "{\"primary_visual_label\":\"allowed label\",\"visible_summary\":\"short grounded visual summary\","
        "\"detected_events\":[\"short event\"],\"visual_labels\":[\"label\"],\"title_hooks\":[\"short hook\"],"
        "\"metadata_keywords\":[\"keyword\"],\"confidence\":0.0,\"ranking_adjustment\":0.0,"
        "\"reject_flags\":[\"flag\"]}\n"
        f"Allowed primary_visual_label values: {', '.join(sorted(PRIMARY_VISUAL_LABELS))}\n"
        f"Allowed visual_labels values: {', '.join(sorted(VISUAL_LABELS))}\n"
        f"Allowed reject_flags values: {', '.join(sorted(REJECT_FLAGS))}\n"
        "ranking_adjustment must be between -0.02 and 0.02. Boost visible threat, combat, chase, clear tutorial UI with creator explanation, or strong atmosphere. "
        "Penalize black screens, pause/menu/inventory-only frames, static overlays, or no visible gameplay action.\n\n"
        f"Clip metadata JSON:\n{json.dumps(payload, ensure_ascii=True, sort_keys=True)}"
    )


def _ask_ollama_vision_json(prompt: str, images: list[str], model: str, *, timeout: int) -> dict | None:
    body = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "images": images,
            "stream": False,
            "format": "json",
            "think": False,
            "keep_alive": "10m",
            "options": {"temperature": 0.0, "num_predict": 700},
        }
    ).encode()
    req = urllib.request.Request(
        OLLAMA_URL,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except TimeoutError:
        raise
    response = _ollama_response_text(data)
    parsed = _extract_json_object(response)
    return parsed if isinstance(parsed, dict) else None


def sanitize_vision_analysis(
    response,
    *,
    model: str = "",
    frames: list[dict] | None = None,
    elapsed: float = 0.0,
) -> dict:
    if not isinstance(response, dict):
        return _empty_analysis("invalid_response", model=model, frames=frames, elapsed=elapsed)
    primary = _clean_key(response.get("primary_visual_label") or response.get("primary") or response.get("category"))
    aliases = {
        "combat_action": "high_energy",
        "chase_panic": "high_energy",
        "scenic": "atmosphere_or_visual",
        "scenic_atmosphere": "atmosphere_or_visual",
        "menu_navigation": "low_value",
        "gameplay": "unclear",
    }
    primary = aliases.get(primary, primary)
    if primary not in PRIMARY_VISUAL_LABELS:
        primary = "unclear"
    confidence = _clamp01(response.get("confidence"))
    reject_flags = _clean_list(response.get("reject_flags"), REJECT_FLAGS, limit=5)
    adjustment = _safe_float(response.get("ranking_adjustment"), 0.0) or 0.0
    adjustment = max(-MULTIMODAL_SELECTION_MAX_ADJUSTMENT, min(MULTIMODAL_SELECTION_MAX_ADJUSTMENT, adjustment))
    if reject_flags and adjustment > 0:
        adjustment = 0.0
    if primary == "low_value":
        adjustment = min(adjustment, -0.008 * max(confidence, 0.5))
    if confidence < 0.35:
        adjustment = 0.0
    return {
        "schema_version": MULTIMODAL_ANALYSIS_SCHEMA_VERSION,
        "enabled": True,
        "status": "ok",
        "provider": "ollama",
        "model": model,
        "frame_count": len(frames or []),
        "sample_times": [row.get("time") for row in (frames or [])],
        "primary_visual_label": primary,
        "visible_summary": _clean_text(response.get("visible_summary"), limit=220),
        "detected_events": _clean_freeform_list(response.get("detected_events"), limit=5, item_limit=70),
        "visual_labels": _clean_list(response.get("visual_labels"), VISUAL_LABELS, limit=8),
        "title_hooks": _clean_freeform_list(response.get("title_hooks"), limit=4, item_limit=60),
        "metadata_keywords": _clean_freeform_list(response.get("metadata_keywords"), limit=8, item_limit=36),
        "confidence": round(confidence, 4),
        "ranking_adjustment": round(adjustment, 4),
        "reject_flags": reject_flags,
        "elapsed_seconds": round(float(elapsed or 0.0), 3),
    }


def _empty_analysis(
    status: str,
    *,
    model: str = "",
    frames: list[dict] | None = None,
    error: str = "",
    elapsed: float = 0.0,
) -> dict:
    return {
        "schema_version": MULTIMODAL_ANALYSIS_SCHEMA_VERSION,
        "enabled": status != "disabled",
        "status": status,
        "provider": "ollama" if model else "",
        "model": model,
        "frame_count": len(frames or []),
        "sample_times": [row.get("time") for row in (frames or [])],
        "primary_visual_label": "",
        "visible_summary": "",
        "detected_events": [],
        "visual_labels": [],
        "title_hooks": [],
        "metadata_keywords": [],
        "confidence": 0.0,
        "ranking_adjustment": 0.0,
        "reject_flags": [],
        "error": error,
        "elapsed_seconds": round(float(elapsed or 0.0), 3),
    }


def _vision_for_evaluation(evaluation: dict) -> dict:
    if isinstance(evaluation.get("multimodal_analysis"), dict):
        return dict(evaluation["multimodal_analysis"])
    moment = evaluation.get("moment") if isinstance(evaluation.get("moment"), dict) else {}
    if isinstance(moment.get("multimodal_analysis"), dict):
        return dict(moment["multimodal_analysis"])
    candidate = evaluation.get("candidate") if isinstance(evaluation.get("candidate"), dict) else {}
    if isinstance(candidate.get("multimodal_analysis"), dict):
        return dict(candidate["multimodal_analysis"])
    return {}


def _vision_scoring_eligibility(analysis: dict, evaluation: dict, *, confidence_floor: float) -> dict:
    if not isinstance(analysis, dict) or not analysis:
        return {"eligible": False, "reason": "missing_multimodal_analysis"}
    if str(analysis.get("status") or "") != "ok":
        return {"eligible": False, "reason": "vision_status_not_ok"}
    if str(analysis.get("provider") or "") != "ollama":
        return {"eligible": False, "reason": "not_ollama"}
    confidence = _safe_float(analysis.get("confidence"), 0.0) or 0.0
    if confidence < confidence_floor:
        return {"eligible": False, "reason": "low_vision_confidence"}
    if analysis.get("reject_flags") and abs(_safe_float(analysis.get("ranking_adjustment"), 0.0) or 0.0) < 0.0001:
        return {"eligible": False, "reason": "reject_flags_without_adjustment"}
    music_guard = evaluation.get("music_lyrics_guard") if isinstance(evaluation.get("music_lyrics_guard"), dict) else {}
    if music_guard.get("reject_candidate"):
        return {"eligible": False, "reason": "music_guard_rejected"}
    if not evaluation.get("accepted") and not evaluation.get("multimodal_rescue_candidate"):
        return {"eligible": False, "reason": "candidate_not_accepted"}
    return {"eligible": True, "reason": "eligible"}


def _is_visual_rescue_candidate(evaluation: dict, analysis: dict, *, confidence_floor: float) -> bool:
    if not evaluation.get("multimodal_rescue_candidate"):
        return False
    if _vision_positive_block_reason(evaluation, analysis):
        return False
    if str(evaluation.get("reject_reason") or "") != "low_transcript_quality":
        return False
    if analysis.get("reject_flags"):
        return False
    primary = str(analysis.get("primary_visual_label") or "")
    if primary in {"low_value", "unclear", ""}:
        return False
    confidence = _safe_float(analysis.get("confidence"), 0.0) or 0.0
    if confidence < max(0.68, confidence_floor):
        return False
    adjustment = _safe_float(analysis.get("ranking_adjustment"), 0.0) or 0.0
    if adjustment <= 0:
        return False
    quality = _safe_float(evaluation.get("quality_score"), 0.0) or 0.0
    floor = _safe_float(evaluation.get("quality_floor"), 0.60) or 0.60
    rescue_floor = _safe_float(evaluation.get("multimodal_rescue_relative_floor"), 0.0) or 0.0
    if rescue_floor <= 0:
        rescue_floor = max(0.32, min(0.45, floor - 0.18))
    if quality < rescue_floor:
        return False
    return True


def _vision_positive_block_reason(evaluation: dict, analysis: dict) -> str:
    source = _speech_source_for_evaluation(evaluation)
    source_block = speech_source_positive_boost_block_reason(source)
    if source_block:
        return source_block
    guard = _commentary_guard_for_evaluation(evaluation)
    if not guard:
        return ""
    summary = guard.get("summary") if isinstance(guard.get("summary"), dict) else {}
    if not summary:
        return ""
    selection = guard.get("selection") if isinstance(guard.get("selection"), dict) else {}
    policy = str(guard.get("policy") or selection.get("policy") or "creator").strip().lower()
    if policy == "creator":
        primary = str(summary.get("primary_label") or "")
        if primary in {"", "none"}:
            return ""
        confidence = _safe_float(summary.get("confidence"), 0.0) or 0.0
        game_ratio = _safe_float(summary.get("game_narration_word_ratio"), 0.0) or 0.0
        creator_ratio = _safe_float(summary.get("creator_word_ratio"), 0.0) or 0.0
        penalty = _safe_float(guard.get("selection_penalty", selection.get("selection_penalty", 0.0)), 0.0) or 0.0
        if primary == "game_narration" and confidence >= 0.52 and game_ratio >= 0.45:
            return "commentary_guard_game_narration"
        if primary != "creator_commentary" and creator_ratio < 0.25:
            return "commentary_guard_weak_creator_evidence"
        visual_primary = str(analysis.get("primary_visual_label") or "")
        labels = {str(label or "") for label in (analysis.get("visual_labels") or [])}
        if (
            (visual_primary in {"lore_or_story", "commentary_or_review"} or "dialogue_scene" in labels)
            and creator_ratio < 0.45
            and penalty >= 0.015
        ):
            return "weak_creator_speech_for_dialogue_vision"
    return ""


def _commentary_guard_for_evaluation(evaluation: dict) -> dict:
    if isinstance(evaluation.get("commentary_guard"), dict):
        return evaluation["commentary_guard"]
    moment = evaluation.get("moment") if isinstance(evaluation.get("moment"), dict) else {}
    if isinstance(moment.get("commentary_guard"), dict):
        return moment["commentary_guard"]
    return {}


def _speech_source_for_evaluation(evaluation: dict) -> dict:
    if isinstance(evaluation.get("speech_source"), dict):
        return evaluation["speech_source"]
    moment = evaluation.get("moment") if isinstance(evaluation.get("moment"), dict) else {}
    if isinstance(moment.get("speech_source"), dict):
        return moment["speech_source"]
    return {}


def _apply_visual_rescue(evaluation: dict) -> None:
    original_reason = str(evaluation.get("reject_reason") or evaluation.get("original_reject_reason") or "")
    evaluation["original_reject_reason"] = original_reason
    evaluation["reject_reason"] = ""
    evaluation["accepted"] = True
    evaluation["multimodal_rescue_applied"] = True
    moment = evaluation.get("moment") if isinstance(evaluation.get("moment"), dict) else {}
    if isinstance(moment, dict):
        moment["multimodal_rescue_applied"] = True
        moment["original_reject_reason"] = original_reason
        ranker = moment.get("ranker") if isinstance(moment.get("ranker"), dict) else {}
        ranker["original_reject_reason"] = original_reason
        ranker["reject_reason"] = ""
        ranker["multimodal_rescue_applied"] = True
        ranker["multimodal_rescue_reason"] = "near_miss_visual_rescue"
        moment["ranker"] = ranker


def _selection_summary(evaluation: dict, score_key: str) -> dict:
    moment = evaluation.get("moment") if isinstance(evaluation.get("moment"), dict) else {}
    analysis = _vision_for_evaluation(evaluation)
    return {
        "candidate_rank": evaluation.get("candidate", {}).get("candidate_rank"),
        "candidate_kind": evaluation.get("candidate", {}).get("candidate_kind", ""),
        "start": moment.get("start"),
        "end": moment.get("end"),
        "quality_score": round(_safe_float(evaluation.get("quality_score"), 0.0) or 0.0, 4),
        "score": round(_safe_float(evaluation.get(score_key, evaluation.get("quality_score", 0.0)), 0.0) or 0.0, 4),
        "primary_visual_label": analysis.get("primary_visual_label"),
        "confidence": analysis.get("confidence"),
    }


def _select_for_report(ordered_evaluations: list[dict], max_count: int, min_gap: int) -> list[dict]:
    selected: list[dict] = []
    for evaluation in ordered_evaluations:
        if len(selected) >= max_count:
            break
        moment = evaluation.get("moment") if isinstance(evaluation.get("moment"), dict) else {}
        if any(_overlaps(moment, other.get("moment", {}), min_gap) for other in selected):
            continue
        selected.append(evaluation)
    return selected


def _overlaps(moment: dict, other: dict, min_gap: int) -> bool:
    start = _safe_float(moment.get("start"), 0.0) or 0.0
    end = _safe_float(moment.get("end"), start) or start
    other_start = _safe_float(other.get("start"), 0.0) or 0.0
    other_end = _safe_float(other.get("end"), other_start) or other_start
    return not (end + min_gap <= other_start or start >= other_end + min_gap)


def _extract_json_object(text: str):
    if not text:
        return None
    text = text.strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _ollama_response_text(data) -> str:
    if not isinstance(data, dict):
        return ""
    response = data.get("response")
    if response not in (None, ""):
        return str(response)
    message = data.get("message")
    if isinstance(message, dict) and message.get("content") not in (None, ""):
        return str(message.get("content"))
    thinking = data.get("thinking")
    if thinking not in (None, ""):
        return str(thinking)
    return ""


def _clean_key(value) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _clean_text(value, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[: max(0, int(limit))]


def _clean_list(values, allowed: set[str], *, limit: int) -> list[str]:
    source = values if isinstance(values, list) else []
    cleaned = []
    seen = set()
    for item in source:
        value = _clean_key(item)
        if value not in allowed or value in seen:
            continue
        cleaned.append(value)
        seen.add(value)
        if len(cleaned) >= limit:
            break
    return cleaned


def _clean_freeform_list(values, *, limit: int, item_limit: int) -> list[str]:
    source = values if isinstance(values, list) else []
    cleaned = []
    seen = set()
    for item in source:
        value = _clean_text(item, limit=item_limit)
        if not value or value.lower() in seen:
            continue
        cleaned.append(value)
        seen.add(value.lower())
        if len(cleaned) >= limit:
            break
    return cleaned


def _prompt_safe_text(text: str | None, *, limit: int = 900) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(
        r"(?i)\b(refresh_token|access_token|client_secret|api[_-]?key|gemini_api_key)\s*[:=]\s*[\w.\-~+/=]+",
        r"\1=[redacted]",
        value,
    )
    value = re.sub(r"\b[A-Za-z]:\\[^\s\"']+", "[local-path]", value)
    return value[: max(0, int(limit))]


def _clamp01(value) -> float:
    number = _safe_float(value, 0.0) or 0.0
    return max(0.0, min(1.0, number))


def _safe_float(value, default=None):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return parsed
