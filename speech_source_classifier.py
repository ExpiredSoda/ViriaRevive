"""Local creator-vs-game speech source scoring.

This module is intentionally model-shaped, even though the first version uses
signals ViriaRevive already computes. Future VAD, diarization, speaker
embedding, or audio-event models can feed this same probability report instead
of adding more one-off rules around the pipeline.
"""

from __future__ import annotations

import math
import re


SPEECH_SOURCE_SCHEMA_VERSION = 1
SPEECH_SOURCE_SELECTION_MAX_PENALTY = 0.065


def classify_speech_source(
    *,
    words: list[dict] | None = None,
    transcript: str | None = None,
    stream_profile: dict | None = None,
    commentary_guard: dict | None = None,
    music_lyrics_guard: dict | None = None,
    voice_profile: dict | None = None,
    visual_context: dict | None = None,
    subtitle_policy: str | None = "creator",
) -> dict:
    """Return a structured source-probability report for transcribed speech."""
    text = _normal_text(transcript if transcript is not None else _words_text(words or []))
    tokens = text.split()
    word_count = len(tokens) if tokens else len(words or [])
    policy = _normalize_policy(subtitle_policy)

    scores = {
        "creator": 0.30,
        "game": 0.24,
        "music": 0.05,
        "unknown": 0.28,
    }
    evidence: list[dict] = []

    if word_count <= 0:
        scores["unknown"] += 1.0
        _add_evidence(evidence, "no_transcribed_words", "unknown", 1.0, 1.0)
    else:
        text_features = _text_source_features(tokens, text)
        _blend_signal(scores, evidence, "first_person_context", "creator", text_features["first_person_score"], 0.75)
        _blend_signal(scores, evidence, "conversational_context", "creator", text_features["conversational_score"], 0.55)
        _blend_signal(scores, evidence, "creator_meta_context", "creator", text_features["creator_meta_score"], 0.85)
        _blend_signal(scores, evidence, "formal_or_instructional_context", "game", text_features["formal_score"], 0.65)
        _blend_signal(scores, evidence, "scripted_dialogue_context", "game", text_features["scripted_dialogue_score"], 1.25)
        _blend_signal(scores, evidence, "repetition_or_lyric_shape", "music", text_features["music_shape_score"], 0.55)
        if text_features["sparse_context_score"]:
            _blend_signal(scores, evidence, "sparse_source_context", "unknown", text_features["sparse_context_score"], 0.30)
    if word_count <= 0:
        text_features = {
            "creator_meta_score": 0.0,
            "scripted_dialogue_score": 0.0,
        }

    visual_context = visual_context if isinstance(visual_context, dict) else {}
    visual_dialogue_score = _visual_dialogue_scene_score(visual_context)
    if visual_dialogue_score:
        creator_meta = _score01(text_features.get("creator_meta_score"))
        _blend_signal(
            scores,
            evidence,
            "visual_dialogue_scene_context",
            "game",
            visual_dialogue_score * max(0.25, 1.0 - creator_meta * 0.75),
            0.85,
        )

    stream_profile = stream_profile if isinstance(stream_profile, dict) else {}
    if stream_profile:
        creator_likeness = _score01(stream_profile.get("creator_likeness_score"))
        game_bed = _score01(stream_profile.get("acoustic_game_bed_score"))
        bed_drag = _score01((game_bed - 0.45) / 0.35)
        natural = _score01(_safe_float(stream_profile.get("natural_dialogue_score"), 0.0) / 5.0)
        scripted = _score01(_safe_float(stream_profile.get("scripted_game_score"), 0.0) / 4.0)
        lyric = _score01(stream_profile.get("lyric_likelihood"))
        creator_exception = _score01(stream_profile.get("creator_exception_score"))
        effective_creator_likeness = creator_likeness * (1.0 - 0.70 * bed_drag)
        selected_reason = str(
            stream_profile.get("selected_reason")
            or stream_profile.get("selection_reason")
            or ""
        ).lower()
        selected_confidence = _score01(
            stream_profile.get(
                "selected_confidence",
                stream_profile.get("confidence", 0.0),
            )
        )
        if "creator" in selected_reason and selected_confidence >= 0.55:
            _blend_signal(
                scores,
                evidence,
                "stream_creator_selection",
                "creator",
                selected_confidence,
                0.95,
            )
        if effective_creator_likeness:
            _blend_signal(scores, evidence, "stream_creator_likeness", "creator", effective_creator_likeness, 1.10)
        if creator_likeness < 0.34:
            _blend_signal(scores, evidence, "low_stream_creator_likeness", "unknown", 0.34 - creator_likeness, 0.70)
        _blend_signal(scores, evidence, "acoustic_background_bed", "game", game_bed, 0.90)
        if stream_profile.get("voice_title_hints"):
            _blend_signal(scores, evidence, "voice_track_hint", "creator", 1.0 - bed_drag, 0.30)
        if stream_profile.get("game_title_hints"):
            _blend_signal(scores, evidence, "game_track_hint", "game", 1.0, 0.36)
        if natural > scripted:
            _blend_signal(scores, evidence, "stream_natural_dialogue", "creator", natural - scripted, 0.40)
        elif scripted > natural:
            _blend_signal(scores, evidence, "stream_scripted_dialogue", "game", scripted - natural, 0.45)
        _blend_signal(scores, evidence, "stream_music_likelihood", "music", lyric, 1.05)
        _blend_signal(scores, evidence, "stream_creator_exception", "creator", creator_exception, 0.30)

    commentary_guard = commentary_guard if isinstance(commentary_guard, dict) else {}
    summary = commentary_guard.get("summary") if isinstance(commentary_guard.get("summary"), dict) else {}
    if summary:
        creator_ratio = _score01(summary.get("creator_word_ratio"))
        game_ratio = _score01(summary.get("game_narration_word_ratio"))
        confidence = _score01(summary.get("confidence"))
        _blend_signal(scores, evidence, "commentary_guard_creator_ratio", "creator", creator_ratio * confidence, 1.10)
        _blend_signal(scores, evidence, "commentary_guard_game_ratio", "game", game_ratio * confidence, 1.15)
        primary = str(summary.get("primary_label") or "")
        if primary == "unclear" and creator_ratio < 0.25 and game_ratio < 0.25:
            _blend_signal(scores, evidence, "commentary_guard_unclear", "unknown", confidence, 0.35)

    music_lyrics_guard = music_lyrics_guard if isinstance(music_lyrics_guard, dict) else {}
    if music_lyrics_guard:
        lyric_likelihood = _score01(music_lyrics_guard.get("lyric_likelihood"))
        creator_exception = _score01(music_lyrics_guard.get("creator_exception_score"))
        _blend_signal(scores, evidence, "music_lyrics_likelihood", "music", lyric_likelihood, 1.35)
        _blend_signal(scores, evidence, "music_creator_exception", "creator", creator_exception, 0.45)

    voice_profile = voice_profile if isinstance(voice_profile, dict) else {}
    if voice_profile and voice_profile.get("reason") == "scored":
        confidence = _score01(voice_profile.get("confidence"))
        if confidence >= 0.50:
            _blend_signal(scores, evidence, "voice_profile_match", "creator", (confidence - 0.50) * 2.0, 1.20)
        elif int(voice_profile.get("sample_count") or 0) >= 3:
            _blend_signal(scores, evidence, "voice_profile_mismatch", "unknown", (0.50 - confidence) * 1.5, 0.40)

    probabilities = _normalize_scores(scores)
    primary_source = max(probabilities, key=probabilities.get)
    ordered = sorted(probabilities.values(), reverse=True)
    margin = ordered[0] - ordered[1] if len(ordered) > 1 else ordered[0]
    confidence = _score01((ordered[0] * 0.62) + (margin * 0.72))
    scripted_dialogue_risk = _score01(
        max(
            text_features.get("scripted_dialogue_score", 0.0),
            visual_dialogue_score * max(0.35, 1.0 - _score01(text_features.get("creator_meta_score"))),
        )
    )
    creator_meta_score = _score01(text_features.get("creator_meta_score"))
    risk_flags: list[str] = []
    if (
        policy == "creator"
        and scripted_dialogue_risk >= 0.52
        and creator_meta_score < 0.38
        and (
            probabilities["game"] >= probabilities["creator"] - 0.24
            or visual_dialogue_score >= 0.48
            or scripted_dialogue_risk >= 0.70
        )
    ):
        risk_flags.append("scripted_dialogue_without_creator_meta")
    creator_safe = (
        probabilities["creator"] >= 0.44
        and probabilities["creator"] >= probabilities["game"] + 0.06
        and probabilities["music"] < 0.48
        and not risk_flags
    )

    return {
        "schema_version": SPEECH_SOURCE_SCHEMA_VERSION,
        "mode": "local_signal_blend",
        "policy": policy,
        "primary_source": primary_source,
        "confidence": round(float(confidence), 4),
        "creator_probability": round(float(probabilities["creator"]), 4),
        "game_or_npc_probability": round(float(probabilities["game"]), 4),
        "music_or_lyrics_probability": round(float(probabilities["music"]), 4),
        "unknown_probability": round(float(probabilities["unknown"]), 4),
        "creator_safe": bool(creator_safe),
        "scripted_dialogue_risk": round(float(scripted_dialogue_risk), 4),
        "creator_meta_score": round(float(creator_meta_score), 4),
        "visual_dialogue_scene_score": round(float(visual_dialogue_score), 4),
        "risk_flags": risk_flags,
        "word_count": int(word_count),
        "selection_impact": "none",
        "selection_penalty": 0.0,
        "retry_recommended": should_retry_for_creator_policy(
            {
                "policy": policy,
                "primary_source": primary_source,
                "confidence": confidence,
                "creator_probability": probabilities["creator"],
                "game_or_npc_probability": probabilities["game"],
                "music_or_lyrics_probability": probabilities["music"],
                "unknown_probability": probabilities["unknown"],
                "creator_safe": creator_safe,
            }
        ),
        "evidence": evidence[:12],
    }


def should_retry_for_creator_policy(source: dict | None) -> bool:
    source = source if isinstance(source, dict) else {}
    if _normalize_policy(source.get("policy")) != "creator":
        return False
    creator = _score01(source.get("creator_probability"))
    game = _score01(source.get("game_or_npc_probability"))
    music = _score01(source.get("music_or_lyrics_probability"))
    confidence = _score01(source.get("confidence"))
    primary = str(source.get("primary_source") or "")
    if bool(source.get("creator_safe")):
        return False
    if "scripted_dialogue_without_creator_meta" in (source.get("risk_flags") or []):
        return True
    if music >= 0.56 and music > creator + 0.10 and confidence >= 0.42:
        return True
    if game >= 0.48 and game > creator + 0.10 and confidence >= 0.42:
        return True
    if primary in {"game", "music"} and creator < 0.36 and confidence >= 0.44:
        return True
    if primary == "unknown" and creator < 0.26 and confidence >= 0.52:
        return True
    return False


def speech_source_selection_penalty(source: dict | None, *, policy: str | None = "creator") -> dict:
    source = source if isinstance(source, dict) else {}
    policy = _normalize_policy(policy or source.get("policy"))
    base = {
        "schema_version": SPEECH_SOURCE_SCHEMA_VERSION,
        "policy": policy,
        "selection_impact": "none",
        "selection_penalty": 0.0,
        "reason": "not_applicable",
    }
    if policy != "creator":
        base["reason"] = "non_creator_policy"
        return base
    if not source:
        base["reason"] = "missing_source_report"
        return base
    if bool(source.get("creator_safe")):
        base["reason"] = "creator_safe"
        return base

    creator = _score01(source.get("creator_probability"))
    game = _score01(source.get("game_or_npc_probability"))
    music = _score01(source.get("music_or_lyrics_probability"))
    unknown = _score01(source.get("unknown_probability"))
    confidence = _score01(source.get("confidence"))
    primary = str(source.get("primary_source") or "")
    scripted_risk = _score01(source.get("scripted_dialogue_risk"))
    creator_meta = _score01(source.get("creator_meta_score"))
    penalty = 0.0
    reason = "low_risk"
    if scripted_risk >= 0.52 and creator_meta < 0.38:
        penalty = 0.020 + (scripted_risk - 0.52) * 0.085 + max(0.0, 0.38 - creator_meta) * 0.035
        reason = "scripted_dialogue_without_creator_meta"
    elif music > creator + 0.10 and music >= 0.45:
        penalty = 0.025 + (music - creator) * 0.060 + confidence * 0.018
        reason = "music_or_lyrics_more_likely_than_creator"
    elif game > creator + 0.10 and game >= 0.42:
        penalty = 0.020 + (game - creator) * 0.055 + confidence * 0.016
        reason = "game_or_npc_more_likely_than_creator"
    elif primary == "unknown" and creator < 0.30 and unknown >= 0.38:
        penalty = 0.012 + (0.30 - creator) * 0.040
        reason = "not_enough_creator_source_evidence"

    penalty = max(0.0, min(SPEECH_SOURCE_SELECTION_MAX_PENALTY, penalty))
    if penalty < 0.008:
        base["reason"] = reason
        return base
    base.update(
        {
            "selection_impact": "quality_penalty",
            "selection_penalty": round(float(penalty), 4),
            "reason": reason,
        }
    )
    return base


def positive_boost_block_reason(source: dict | None, *, policy: str | None = "creator") -> str:
    source = source if isinstance(source, dict) else {}
    if _normalize_policy(policy or source.get("policy")) != "creator" or not source:
        return ""
    creator = _score01(source.get("creator_probability"))
    game = _score01(source.get("game_or_npc_probability"))
    music = _score01(source.get("music_or_lyrics_probability"))
    confidence = _score01(source.get("confidence"))
    if bool(source.get("creator_safe")):
        return ""
    if (
        "scripted_dialogue_without_creator_meta" in (source.get("risk_flags") or [])
        or (
            _score01(source.get("scripted_dialogue_risk")) >= 0.55
            and _score01(source.get("creator_meta_score")) < 0.38
        )
    ):
        return "speech_source_scripted_dialogue"
    if game >= 0.48 and game > creator + 0.10 and confidence >= 0.42:
        return "speech_source_game_or_npc"
    if music >= 0.52 and music > creator + 0.10 and confidence >= 0.42:
        return "speech_source_music_or_lyrics"
    if creator < 0.26 and confidence >= 0.52:
        return "speech_source_weak_creator_evidence"
    return ""


def with_selection_penalty(source: dict, penalty_report: dict, *, applied_penalty: float | None = None) -> dict:
    source = dict(source or {})
    penalty = _safe_float(
        applied_penalty if applied_penalty is not None else penalty_report.get("selection_penalty"),
        0.0,
    )
    source["selection_impact"] = "quality_penalty" if penalty > 0 else "none"
    source["selection_penalty"] = round(float(max(0.0, penalty)), 4)
    source["selection_reason"] = penalty_report.get("reason", "")
    source["selection"] = dict(penalty_report or {})
    return source


def _text_source_features(tokens: list[str], text: str) -> dict:
    token_set = set(tokens)
    first_person = len(token_set.intersection({"i", "im", "i'm", "ive", "i've", "me", "my", "we", "we're", "were", "our"}))
    conversational = len(token_set.intersection({"wait", "why", "what", "how", "okay", "alright", "yeah", "no", "please", "chat"}))
    formal = len(token_set.intersection({"objective", "mission", "checkpoint", "chapter", "press", "loading", "saving", "collect"}))
    second_person = len(token_set.intersection({"you", "your"}))
    creator_meta_raw = _weighted_phrase_score(text, CREATOR_META_PHRASES)
    creator_meta_raw += min(2.0, len(token_set.intersection(CREATOR_META_TOKENS)) * 0.45)
    scripted_raw = _weighted_phrase_score(text, SCRIPTED_DIALOGUE_PHRASES)
    scripted_raw += min(2.2, len(token_set.intersection(SCRIPTED_DIALOGUE_TOKENS)) * 0.38)
    if first_person and second_person and creator_meta_raw < 0.75:
        scripted_raw += 0.65
    if first_person >= 2 and creator_meta_raw < 0.60 and conversational <= 2:
        scripted_raw += 0.45
    top_ratio = _top_token_ratio(tokens)
    repeated_bigram = _repeated_ngram_ratio(tokens, 2)
    first_person_score = _score01(first_person / 3.0)
    conversational_score = _score01((conversational + max(0, first_person - 1) * 0.5) / 5.0)
    formal_score = _score01((formal + (0.35 if second_person and not first_person else 0.0)) / 3.0)
    creator_meta_score = _score01(creator_meta_raw / 3.0)
    scripted_dialogue_score = _score01(scripted_raw / 4.0)
    music_shape_score = _score01((top_ratio - 0.18) * 2.8 + repeated_bigram * 2.0)
    sparse_context_score = 0.0
    if first_person == 0 and formal == 0 and conversational <= 1 and len(tokens) >= 6:
        sparse_context_score = 0.45
    return {
        "first_person_score": first_person_score,
        "conversational_score": conversational_score,
        "creator_meta_score": creator_meta_score,
        "formal_score": formal_score,
        "scripted_dialogue_score": scripted_dialogue_score,
        "music_shape_score": music_shape_score,
        "sparse_context_score": sparse_context_score,
    }


CREATOR_META_PHRASES = (
    ("this game", 1.4),
    ("the game", 1.0),
    ("gameplay", 1.3),
    ("look smooth", 1.4),
    ("looks smooth", 1.4),
    ("doesnt look", 1.1),
    ("doesn't look", 1.1),
    ("does not look", 1.1),
    ("feels like", 1.1),
    ("feels weird", 1.2),
    ("compare that", 1.3),
    ("compared to", 1.2),
    ("i dont know what it is", 1.3),
    ("i don't know what it is", 1.3),
    ("look at this", 1.0),
    ("chat", 1.0),
)

CREATOR_META_TOKENS = {
    "game", "gameplay", "controls", "movement", "graphics", "mechanic",
    "mechanics", "camera", "smooth", "stiff", "weird", "chat", "stream",
    "recording", "clip", "fps", "aiming", "controller",
}

SCRIPTED_DIALOGUE_PHRASES = (
    ("do it yourself", 1.4),
    ("done right", 1.0),
    ("bad luck", 0.9),
    ("good luck", 0.8),
    ("live in", 0.7),
    ("fix yourself", 1.3),
    ("some dinner", 1.1),
    ("what do you need", 1.0),
    ("leave this place", 1.4),
    ("from up here", 1.1),
    ("in position", 1.3),
    ("starting the", 0.9),
    ("this way", 0.7),
    ("come on this way", 1.3),
    ("new quest", 1.3),
    ("quest item", 1.3),
    ("incoming transmission", 1.4),
    ("you are required", 1.4),
    ("you must", 1.2),
    ("objective updated", 1.4),
)

SCRIPTED_DIALOGUE_TOKENS = {
    "credits", "credit", "quest", "mission", "objective", "checkpoint",
    "cantina", "bounty", "syndicate", "cartel", "imperial", "rebel",
    "shipment", "cargo", "fireworks", "palace", "coordinates", "transmission",
    "contract", "smuggler", "speeder", "blaster", "hideout", "attic",
}


def _visual_dialogue_scene_score(visual_context: dict) -> float:
    if not isinstance(visual_context, dict) or not visual_context:
        return 0.0
    score = 0.0
    primary = str(visual_context.get("primary_visual_label") or "").strip().lower()
    if primary == "lore_or_story":
        score += 0.46
    elif primary == "commentary_or_review":
        score += 0.18
    labels = {str(label or "").strip().lower() for label in (visual_context.get("visual_labels") or [])}
    if "dialogue_scene" in labels:
        score += 0.55
    text_bits = [
        str(visual_context.get("visible_summary") or ""),
        " ".join(str(item or "") for item in (visual_context.get("detected_events") or [])),
        " ".join(str(item or "") for item in (visual_context.get("metadata_keywords") or [])),
    ]
    normal = _normal_text(" ".join(text_bits))
    score += min(
        0.45,
        _weighted_phrase_score(
            normal,
            (
                ("in game dialogue", 0.26),
                ("characters are talking", 0.26),
                ("character is talking", 0.22),
                ("dialogue scene", 0.28),
                ("cutscene", 0.28),
                ("story beat", 0.24),
                ("quest", 0.18),
                ("conversation", 0.20),
                ("npc", 0.26),
            ),
        ),
    )
    return _score01(score)


def _blend_signal(scores: dict[str, float], evidence: list[dict], name: str, target: str, value: float, weight: float) -> None:
    value = _score01(value)
    weight = max(0.0, float(weight or 0.0))
    if value <= 0 or weight <= 0:
        return
    amount = value * weight
    scores[target] = scores.get(target, 0.0) + amount
    _add_evidence(evidence, name, target, value, amount)


def _add_evidence(evidence: list[dict], name: str, target: str, value: float, amount: float) -> None:
    evidence.append(
        {
            "name": name,
            "target": target,
            "value": round(float(value), 4),
            "weight": round(float(amount), 4),
        }
    )


def _weighted_phrase_score(text: str, phrases: tuple[tuple[str, float], ...]) -> float:
    normal = str(text or "")
    return sum(float(weight) for phrase, weight in phrases if phrase and phrase in normal)


def _normalize_scores(scores: dict[str, float]) -> dict[str, float]:
    safe = {key: max(0.0, float(value or 0.0)) for key, value in scores.items()}
    total = sum(safe.values())
    if total <= 0 or not math.isfinite(total):
        return {"creator": 0.0, "game": 0.0, "music": 0.0, "unknown": 1.0}
    return {key: value / total for key, value in safe.items()}


def _words_text(words: list[dict]) -> str:
    return " ".join(str(word.get("text", "")).strip() for word in words or [] if isinstance(word, dict)).strip()


def _normal_text(text: str | None) -> str:
    text = str(text or "").lower()
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _top_token_ratio(tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    return max(counts.values()) / max(1, len(tokens))


def _repeated_ngram_ratio(tokens: list[str], size: int) -> float:
    if len(tokens) < size * 2:
        return 0.0
    counts: dict[tuple[str, ...], int] = {}
    for idx in range(0, len(tokens) - size + 1):
        gram = tuple(tokens[idx : idx + size])
        counts[gram] = counts.get(gram, 0) + 1
    repeated = sum(count for count in counts.values() if count > 1)
    return repeated / max(1, len(tokens) - size + 1)


def _normalize_policy(policy: str | None) -> str:
    policy = str(policy or "creator").strip().lower()
    return policy if policy in {"creator", "all", "game"} else "creator"


def _score01(value) -> float:
    return max(0.0, min(1.0, _safe_float(value, 0.0)))


def _safe_float(value, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default
