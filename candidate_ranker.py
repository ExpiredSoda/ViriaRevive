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
DETECTION_PREFERENCES = {"auto", "quality", "quantity"}
QUALITY_FLOORS = {
    "auto": MIN_QUALITY_SCORE,
    "quality": 0.60,
    "quantity": 0.44,
}
MIN_WORDS = 6
MIN_PEAK_TAIL = 8
MAX_EXTENSION = 10
SHADOW_SCORING_SCHEMA_VERSION = 2
SHADOW_MAX_ADJUSTMENT = 0.18
LEARNED_SELECTION_MAX_ADJUSTMENT = 0.06
SHADOW_MAX_TERMS_PER_EVENT = 40
VOICE_PROFILE_SHADOW_SCHEMA_VERSION = 1
VOICE_PROFILE_SHADOW_MAX_ADJUSTMENT = 0.035
VOICE_PROFILE_SELECTION_SCHEMA_VERSION = 1
VOICE_PROFILE_SELECTION_MAX_ADJUSTMENT = 0.025
MIN_VOICE_RANKING_SCORED_CANDIDATES = 2
MIN_VOICE_RANKING_SCORED_RATIO = 0.30
MOMENT_CATEGORY_SELECTION_SCHEMA_VERSION = 1
MOMENT_CATEGORY_SELECTION_MAX_ADJUSTMENT = 0.020
MOMENT_CATEGORY_DIVERSITY_MAX_ADJUSTMENT = 0.006
AI_MOMENT_SELECTION_SCHEMA_VERSION = 1
AI_MOMENT_SELECTION_MAX_ADJUSTMENT = 0.015
COMMENTARY_GUARD_SCHEMA_VERSION = 1
COMMENTARY_SEGMENT_MAX_WORDS = 14
COMMENTARY_SEGMENT_GAP = 0.75
COMMENTARY_SUBTITLE_POLICIES = {"creator", "all", "game"}
COMMENTARY_GUARD_SELECTION_MAX_PENALTY = 0.06
MUSIC_LYRICS_GUARD_SCHEMA_VERSION = 1
MUSIC_LYRICS_SELECTION_MAX_PENALTY = 0.30
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

CREATOR_COMMENTARY_PHRASES = (
    ("oh my god", 3.0),
    ("what the", 2.5),
    ("right behind", 3.0),
    ("i think", 2.0),
    ("i dont", 2.0),
    ("i don't", 2.0),
    ("i can", 1.5),
    ("i cant", 2.0),
    ("i can't", 2.0),
    ("i need", 2.0),
    ("i have to", 2.0),
    ("we need", 2.0),
    ("we have to", 2.0),
    ("look at", 2.0),
    ("this game", 2.0),
    ("gameplay", 1.8),
    ("brother", 1.8),
    ("chat", 1.6),
    ("run", 1.5),
    ("hide", 1.5),
    ("wait", 1.4),
    ("please", 1.2),
    ("whoa", 1.6),
)

GAME_NARRATION_PHRASES = (
    ("press", 2.5),
    ("objective", 3.0),
    ("mission", 2.5),
    ("quest", 2.4),
    ("checkpoint", 2.0),
    ("loading", 2.0),
    ("chapter", 2.0),
    ("previously", 2.0),
    ("collect", 1.8),
    ("find the", 1.8),
    ("go to the", 1.8),
    ("you must", 2.4),
    ("you need to", 1.6),
    ("the door", 1.7),
    ("the key", 1.7),
    ("manuscript", 2.0),
    ("narrator", 2.0),
    ("warning", 2.0),
    ("incoming transmission", 3.0),
)

MUSIC_LYRIC_TERMS = {
    "bitch", "bitches", "hoe", "shawty", "diamonds", "diamond", "necklace",
    "ring", "wedding", "cocaine", "codeine", "lean", "perc", "perks",
    "molly", "drugs", "wasted", "gta", "money", "racks", "flexing",
    "stunting", "condom", "basement", "patients", "demonic", "medusa",
}

MUSIC_CONTEXT_PHRASES = (
    ("listen to", 1.8),
    ("play this song", 2.4),
    ("one song", 1.8),
    ("this song", 1.6),
    ("music", 1.4),
    ("juice wrld", 3.0),
    ("lyrics", 2.0),
    ("sing", 1.4),
    ("singing", 1.4),
    ("chorus", 2.0),
)

LIVE_CREATOR_EXCEPTION_PHRASES = (
    ("no im joking", 3.0),
    ("no i'm joking", 3.0),
    ("what the hell", 2.4),
    ("i saved myself", 3.0),
    ("i had to use", 2.0),
    ("i just used", 1.8),
    ("we havent used", 1.8),
    ("we haven't used", 1.8),
    ("this game", 1.8),
    ("gameplay", 1.6),
    ("rifle", 1.5),
    ("ammo", 1.5),
    ("battery", 1.7),
    ("batteries", 1.7),
    ("flashlight", 1.6),
    ("headlamp", 1.6),
    ("dodged", 1.8),
    ("dodge", 1.4),
)

CATEGORY_PHRASES = {
    "high_energy": (
        ("oh my god", 4.0),
        ("what the", 3.0),
        ("run", 2.0),
        ("hide", 2.0),
        ("right behind", 4.0),
        ("please", 1.5),
        ("whoa", 2.0),
        ("scary", 2.0),
        ("jump scare", 3.0),
    ),
    "death_or_failure": (
        ("we died", 4.0),
        ("did we die", 4.0),
        ("i died", 4.0),
        ("got me", 3.0),
        ("he got me", 4.0),
        ("killed me", 4.0),
        ("caught me", 3.5),
        ("failed", 2.5),
        ("restart", 2.0),
        ("try again", 2.0),
    ),
    "tutorial_or_explainer": (
        ("how to", 4.0),
        ("heres how", 4.0),
        ("here is how", 4.0),
        ("difference between", 2.5),
        ("what the difference", 2.5),
        ("you have to", 3.0),
        ("you need to", 3.0),
        ("what you do", 3.0),
        ("go here", 2.5),
        ("first", 1.5),
        ("then", 1.5),
        ("strategy", 3.0),
        ("the trick", 3.0),
        ("mechanic", 2.5),
        ("tutorial", 4.0),
    ),
    "commentary_or_review": (
        ("this game", 2.5),
        ("gameplay", 2.5),
        ("beautiful", 3.0),
        ("rough", 2.5),
        ("looks", 1.5),
        ("feels", 1.5),
        ("i like", 2.0),
        ("i love", 2.0),
        ("i hate", 2.0),
        ("design", 2.0),
        ("graphics", 2.5),
    ),
    "lore_or_story": (
        ("lore", 4.0),
        ("story", 3.0),
        ("manuscript", 3.0),
        ("character", 2.0),
        ("chapter", 2.0),
        ("written", 1.8),
        ("narrator", 2.0),
        ("backstory", 3.0),
        ("the dark presence", 4.0),
    ),
    "atmosphere_or_visual": (
        ("beautiful", 3.0),
        ("creepy", 3.0),
        ("scary", 2.5),
        ("dark", 1.8),
        ("atmosphere", 4.0),
        ("lighting", 2.5),
        ("visual", 2.0),
        ("scene", 1.6),
        ("look at this", 2.5),
    ),
    "low_value": (
        ("where am i", 4.0),
        ("all the way back", 4.0),
        ("going forward or back", 4.0),
        ("just going to wait", 4.0),
        ("im just going to wait", 4.0),
        ("we are going to end it", 4.0),
        ("go ahead and end it", 4.0),
        ("thank you for tuning", 4.0),
        ("future post watchers", 4.0),
        ("stats screen", 3.5),
        ("statistics", 3.0),
        ("scoreboard", 3.0),
        ("results screen", 3.5),
        ("end screen", 3.0),
        ("mission complete", 3.0),
        ("chapter complete", 3.0),
        ("completion stats", 3.0),
        ("inventory screen", 2.5),
        ("credits", 2.5),
        ("menu", 2.0),
        ("loading", 2.0),
    ),
}

CATEGORY_KEYS = tuple(CATEGORY_PHRASES.keys())


def transcript_text(words: list[dict]) -> str:
    return " ".join(w.get("text", "").strip() for w in words if w.get("text", "").strip())


def normalize_detection_preference(value: str | None) -> str:
    preference = str(value or "auto").strip().lower()
    return preference if preference in DETECTION_PREFERENCES else "auto"


def quality_floor_for_preference(value: str | None) -> float:
    return QUALITY_FLOORS[normalize_detection_preference(value)]


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


def classify_commentary_guard(words: list[dict], *, enabled: bool = True) -> dict:
    """Classify transcript segments in shadow mode without changing output."""
    words = clean_words(words)
    base = {
        "schema_version": COMMENTARY_GUARD_SCHEMA_VERSION,
        "mode": "shadow",
        "enabled": bool(enabled),
        "output_changed": False,
        "selection_impact": "none",
        "subtitle_impact": "none",
        "segments": [],
        "summary": {
            "segment_count": 0,
            "creator_commentary_segments": 0,
            "game_narration_segments": 0,
            "unclear_segments": 0,
            "creator_word_ratio": 0.0,
            "game_narration_word_ratio": 0.0,
            "primary_label": "none",
            "confidence": 0.0,
        },
    }
    if not enabled:
        base["reason"] = "disabled"
        return base
    if not words:
        base["reason"] = "no_words"
        return base

    segments = _commentary_guard_segments(words)
    classified = [_classify_commentary_segment(segment) for segment in segments]
    total_words = max(1, sum(int(row["word_count"]) for row in classified))
    creator_words = sum(int(row["word_count"]) for row in classified if row["label"] == "creator_commentary")
    game_words = sum(int(row["word_count"]) for row in classified if row["label"] == "game_narration")
    unclear_words = total_words - creator_words - game_words
    creator_count = sum(1 for row in classified if row["label"] == "creator_commentary")
    game_count = sum(1 for row in classified if row["label"] == "game_narration")
    unclear_count = len(classified) - creator_count - game_count
    creator_ratio = creator_words / total_words
    game_ratio = game_words / total_words
    if creator_ratio >= game_ratio and creator_ratio >= 0.35:
        primary = "creator_commentary"
        confidence = creator_ratio
    elif game_ratio > creator_ratio and game_ratio >= 0.35:
        primary = "game_narration"
        confidence = game_ratio
    else:
        primary = "unclear"
        confidence = max(creator_ratio, game_ratio, unclear_words / total_words)

    base.update(
        {
            "reason": "single_track_shadow_guard",
            "segments": classified,
            "summary": {
                "segment_count": len(classified),
                "creator_commentary_segments": creator_count,
                "game_narration_segments": game_count,
                "unclear_segments": unclear_count,
                "creator_word_ratio": round(float(creator_ratio), 4),
                "game_narration_word_ratio": round(float(game_ratio), 4),
                "primary_label": primary,
                "confidence": round(float(confidence), 4),
            },
        }
    )
    return base


def normalize_commentary_subtitle_policy(policy: str | None) -> str:
    policy = str(policy or "creator").strip().lower()
    return policy if policy in COMMENTARY_SUBTITLE_POLICIES else "creator"


def apply_commentary_subtitle_policy(
    words: list[dict],
    guard: dict,
    *,
    policy: str | None = "creator",
) -> tuple[list[dict], dict]:
    """Lightly choose which classified transcript segments feed subtitles."""
    words = clean_words(words)
    policy = normalize_commentary_subtitle_policy(policy)
    application = {
        "schema_version": COMMENTARY_GUARD_SCHEMA_VERSION,
        "policy": policy,
        "applied": False,
        "output_changed": False,
        "fallback_used": False,
        "reason": "not_applicable",
        "original_word_count": len(words),
        "filtered_word_count": len(words),
        "removed_word_count": 0,
        "kept_labels": [],
        "removed_labels": [],
        "selection_impact": "none",
        "subtitle_impact": "none",
    }
    if not words:
        application["reason"] = "no_words"
        return words, application
    if not isinstance(guard, dict) or not guard.get("enabled"):
        application["reason"] = "guard_disabled"
        return words, application
    if policy == "all":
        application["reason"] = "all_speech_policy"
        application["kept_labels"] = ["creator_commentary", "game_narration", "unclear"]
        return words, application

    segments = guard.get("segments") if isinstance(guard.get("segments"), list) else []
    if not segments:
        application["reason"] = "no_segments"
        return words, application

    if policy == "game":
        keep_labels = {"game_narration", "unclear"}
        remove_labels = {"creator_commentary"}
    else:
        keep_labels = {"creator_commentary", "unclear"}
        remove_labels = {"game_narration"}

    filtered = _filter_words_by_commentary_labels(words, segments, keep_labels)
    kept_labels = sorted({str(row.get("label", "unclear")) for row in segments if str(row.get("label", "unclear")) in keep_labels})
    removed_labels = sorted({str(row.get("label", "unclear")) for row in segments if str(row.get("label", "unclear")) in remove_labels})
    removed_word_count = max(0, len(words) - len(filtered))
    application.update(
        {
            "kept_labels": kept_labels,
            "removed_labels": removed_labels,
            "filtered_word_count": len(filtered),
            "removed_word_count": removed_word_count,
        }
    )

    if not removed_word_count:
        application["reason"] = "no_matching_segments_removed"
        return words, application
    if len(filtered) < MIN_WORDS:
        application.update(
            {
                "fallback_used": True,
                "reason": "filtered_transcript_too_sparse",
                "filtered_word_count": len(words),
                "removed_word_count": 0,
            }
        )
        return words, application

    application.update(
        {
            "applied": True,
            "output_changed": True,
            "reason": f"{policy}_subtitle_filter_applied",
            "selection_impact": "none",
            "subtitle_impact": "filtered_words",
        }
    )
    return filtered, application


def classify_music_lyrics_guard(words: list[dict], *, policy: str | None = "creator") -> dict:
    """Detect transcript text that looks like music lyrics rather than creator commentary."""
    words = clean_words(words)
    policy = normalize_commentary_subtitle_policy(policy)
    text = transcript_text(words)
    normal = _normal_text(text)
    tokens = normal.split()
    token_count = len(tokens)
    duration = 0.0
    if words:
        duration = max(0.0, float(words[-1]["end"]) - float(words[0]["start"]))

    if not tokens:
        return {
            "schema_version": MUSIC_LYRICS_GUARD_SCHEMA_VERSION,
            "policy": policy,
            "status": "no_words",
            "lyric_likelihood": 0.0,
            "creator_exception_score": 0.0,
            "selection_penalty": 0.0,
            "selection_impact": "none",
            "reject_candidate": False,
            "reason": "no_words",
            "signals": {},
        }

    lyric_hits = sum(1 for token in tokens if token in MUSIC_LYRIC_TERMS)
    lyric_vocab_score = _score01(lyric_hits / max(3.0, token_count / 12.0))
    repetition = _repetition_profile(tokens)
    repetition_score = _score01(
        0.45 * repetition["top_token_ratio"] * 5.0
        + 0.35 * repetition["repeated_bigram_ratio"] * 8.0
        + 0.20 * repetition["repeated_trigram_ratio"] * 10.0
    )
    music_context_score = _score01(_weighted_score(normal, MUSIC_CONTEXT_PHRASES) / 5.0)
    source = _speech_source_evidence(normal)
    live_context_score = _score01(
        (_weighted_score(normal, LIVE_CREATOR_EXCEPTION_PHRASES) / 6.0)
        + (source["creator_score"] / 8.0)
        + (_weighted_score(normal, HOOK_WEIGHTS) / 12.0)
    )
    word_density = token_count / max(1.0, duration)
    dense_transcript_score = _score01((word_density - 1.4) / 1.4)
    creator_exception_score = _score01(
        0.45 * live_context_score
        + 0.28 * source["creator_signal"]
        + 0.17 * _score01(_weighted_score(normal, LIVE_CREATOR_EXCEPTION_PHRASES) / 4.5)
        + 0.10 * _score01(_weighted_score(normal, CATEGORY_PHRASES["commentary_or_review"]) / 5.0)
    )
    lyric_likelihood = _score01(
        0.32 * lyric_vocab_score
        + 0.30 * repetition_score
        + 0.18 * music_context_score
        + 0.12 * dense_transcript_score
        + 0.08 * _score01(1.0 - min(1.0, source["game_signal"]))
        - 0.28 * creator_exception_score
    )

    selection_penalty = 0.0
    reject_candidate = False
    reason = "not_music_lyrics"
    if policy == "creator" and lyric_likelihood >= 0.52 and creator_exception_score < 0.62:
        selection_penalty = min(
            MUSIC_LYRICS_SELECTION_MAX_PENALTY,
            0.12 + (lyric_likelihood - 0.52) * 0.42 + max(0.0, 0.42 - creator_exception_score) * 0.16,
        )
        reason = "lyrics_without_enough_creator_context"
        reject_candidate = lyric_likelihood >= 0.68 and creator_exception_score < 0.45
    elif policy == "creator" and lyric_likelihood >= 0.45 and creator_exception_score < 0.45:
        selection_penalty = min(MUSIC_LYRICS_SELECTION_MAX_PENALTY * 0.55, (lyric_likelihood - 0.40) * 0.28)
        reason = "possible_music_lyrics"

    return {
        "schema_version": MUSIC_LYRICS_GUARD_SCHEMA_VERSION,
        "policy": policy,
        "status": "ok",
        "lyric_likelihood": round(float(lyric_likelihood), 4),
        "creator_exception_score": round(float(creator_exception_score), 4),
        "selection_penalty": round(float(selection_penalty), 4),
        "selection_impact": "quality_penalty" if selection_penalty else "none",
        "reject_candidate": bool(reject_candidate),
        "reason": reason,
        "signals": {
            "lyric_term_hits": lyric_hits,
            "lyric_vocab_score": round(float(lyric_vocab_score), 4),
            "repetition_score": round(float(repetition_score), 4),
            "top_token_ratio": round(float(repetition["top_token_ratio"]), 4),
            "repeated_bigram_ratio": round(float(repetition["repeated_bigram_ratio"]), 4),
            "repeated_trigram_ratio": round(float(repetition["repeated_trigram_ratio"]), 4),
            "music_context_score": round(float(music_context_score), 4),
            "live_context_score": round(float(live_context_score), 4),
            "creator_signal": round(float(source["creator_signal"]), 4),
            "game_signal": round(float(source["game_signal"]), 4),
            "word_density": round(float(word_density), 4),
            "dense_transcript_score": round(float(dense_transcript_score), 4),
        },
    }


def commentary_guard_selection_penalty(guard: dict, *, policy: str | None = "creator") -> dict:
    """Return a capped quality penalty for likely game/NPC speech under creator-only policy."""
    policy = normalize_commentary_subtitle_policy(policy)
    base = {
        "schema_version": COMMENTARY_GUARD_SCHEMA_VERSION,
        "policy": policy,
        "enabled": bool(isinstance(guard, dict) and guard.get("enabled")),
        "selection_impact": "none",
        "selection_penalty": 0.0,
        "reason": "not_applicable",
        "signals": {},
    }
    if policy != "creator":
        base["reason"] = "non_creator_policy"
        return base
    if not isinstance(guard, dict) or not guard.get("enabled"):
        base["reason"] = "guard_disabled"
        return base

    summary = guard.get("summary") if isinstance(guard.get("summary"), dict) else {}
    application = guard.get("application") if isinstance(guard.get("application"), dict) else {}
    primary = str(summary.get("primary_label") or "none")
    confidence = _score01(summary.get("confidence", 0.0))
    game_ratio = _score01(summary.get("game_narration_word_ratio", 0.0))
    creator_ratio = _score01(summary.get("creator_word_ratio", 0.0))
    fallback_used = bool(application.get("fallback_used"))
    output_changed = bool(application.get("output_changed"))

    base["signals"] = {
        "primary_label": primary,
        "confidence": round(float(confidence), 4),
        "game_narration_word_ratio": round(float(game_ratio), 4),
        "creator_word_ratio": round(float(creator_ratio), 4),
        "subtitle_filter_output_changed": output_changed,
        "subtitle_filter_fallback_used": fallback_used,
    }
    if primary != "game_narration":
        base["reason"] = "not_game_narration_primary"
        return base
    if output_changed and not fallback_used:
        base["reason"] = "creator_filter_recovered"
        return base
    if confidence < 0.55 or game_ratio < 0.55:
        base["reason"] = "low_confidence_game_narration"
        return base

    raw_penalty = 0.025 + max(0.0, game_ratio - 0.55) * 0.08 + max(0.0, confidence - 0.55) * 0.05
    raw_penalty -= min(0.018, creator_ratio * 0.04)
    penalty = max(0.0, min(COMMENTARY_GUARD_SELECTION_MAX_PENALTY, raw_penalty))
    if penalty < 0.01:
        base["reason"] = "penalty_below_floor"
        return base

    base.update(
        {
            "selection_impact": "quality_penalty",
            "selection_penalty": round(float(penalty), 4),
            "reason": "high_confidence_game_narration_under_creator_policy",
        }
    )
    return base


def _filter_words_by_commentary_labels(
    words: list[dict],
    segments: list[dict],
    keep_labels: set[str],
) -> list[dict]:
    filtered: list[dict] = []
    for word in words:
        label = _commentary_label_for_word(word, segments)
        if label in keep_labels:
            filtered.append(word)
    return filtered


def _commentary_label_for_word(word: dict, segments: list[dict]) -> str:
    try:
        start = float(word.get("start", 0.0))
        end = float(word.get("end", start))
    except (TypeError, ValueError):
        return "unclear"
    midpoint = (start + end) / 2.0
    for segment in segments:
        try:
            seg_start = float(segment.get("start", 0.0))
            seg_end = float(segment.get("end", seg_start))
        except (TypeError, ValueError):
            continue
        if seg_start - 0.05 <= midpoint <= seg_end + 0.05:
            return str(segment.get("label") or "unclear")
    return "unclear"


def _compact_commentary_guard(guard: dict) -> dict:
    """Return a state-safe commentary guard summary without segment text."""
    if not isinstance(guard, dict):
        return {}
    return {
        "schema_version": guard.get("schema_version", COMMENTARY_GUARD_SCHEMA_VERSION),
        "mode": guard.get("mode", "shadow"),
        "enabled": bool(guard.get("enabled", False)),
        "reason": guard.get("reason", ""),
        "policy": guard.get("policy"),
        "output_changed": bool(guard.get("output_changed", False)),
        "selection_impact": guard.get("selection_impact", "none"),
        "subtitle_impact": guard.get("subtitle_impact", "none"),
        "selection_penalty": guard.get("selection_penalty", 0.0),
        "selection_reason": guard.get("selection_reason", ""),
        "selection": copy.deepcopy(guard.get("selection", {})),
        "summary": copy.deepcopy(guard.get("summary", {})),
        "application": copy.deepcopy(guard.get("application", {})),
    }


def _commentary_guard_segments(words: list[dict]) -> list[list[dict]]:
    segments: list[list[dict]] = []
    current: list[dict] = []
    for word in words:
        if current:
            gap = float(word["start"]) - float(current[-1]["end"])
            previous = str(current[-1].get("text", "")).rstrip()
            if (
                gap >= COMMENTARY_SEGMENT_GAP
                or previous.endswith((".", "!", "?"))
                or len(current) >= COMMENTARY_SEGMENT_MAX_WORDS
            ):
                segments.append(current)
                current = []
        current.append(word)
    if current:
        segments.append(current)
    return segments


def _speech_source_evidence(normal: str) -> dict:
    """Score whether transcript language looks creator-spoken or game/system-spoken."""
    normal = str(normal or "")
    creator_score = _weighted_score(normal, CREATOR_COMMENTARY_PHRASES)
    game_score = _weighted_score(normal, GAME_NARRATION_PHRASES)
    tokens = set(normal.split())
    first_person_hits = len(tokens.intersection({"i", "im", "i'm", "me", "my", "we", "were", "we're", "our"}))
    second_person_hits = len(tokens.intersection({"you", "your"}))
    reactive_hits = len(tokens.intersection({"wait", "run", "hide", "please", "whoa", "no", "brother", "chat"}))
    formal_hits = len(tokens.intersection({"objective", "mission", "chapter", "checkpoint", "warning", "collect"}))

    creator_score += min(3.0, first_person_hits * 0.9 + reactive_hits * 0.7)
    game_score += min(2.5, formal_hits * 0.9)
    if second_person_hits and first_person_hits == 0 and reactive_hits == 0:
        game_score += min(1.4, second_person_hits * 0.45)
    if first_person_hits and formal_hits:
        creator_score += 0.4
        game_score += 0.4

    total = max(0.01, creator_score + game_score)
    creator_norm = creator_score / total if total else 0.0
    game_norm = game_score / total if total else 0.0
    margin = abs(creator_score - game_score)
    if creator_score > game_score and creator_score >= 1.6 and margin >= 0.7:
        label = "creator_commentary"
        confidence = creator_norm
    elif game_score > creator_score and game_score >= 1.8 and margin >= 0.7:
        label = "game_narration"
        confidence = game_norm
    else:
        label = "unclear"
        confidence = min(0.35, max(creator_score, game_score) / 3.0)

    signals = []
    if first_person_hits:
        signals.append("first_person")
    if reactive_hits:
        signals.append("reactive_language")
    if formal_hits:
        signals.append("formal_game_language")
    if second_person_hits and not first_person_hits:
        signals.append("second_person_instruction")
    if creator_score:
        signals.append("creator_phrase")
    if game_score:
        signals.append("game_phrase")

    return {
        "label": label,
        "confidence": round(float(confidence), 4),
        "creator_score": round(float(creator_score), 4),
        "game_score": round(float(game_score), 4),
        "creator_signal": _score01(creator_score / 6.0),
        "game_signal": _score01(game_score / 6.0),
        "creator_norm": round(float(creator_norm), 4),
        "game_norm": round(float(game_norm), 4),
        "first_person_hits": first_person_hits,
        "second_person_hits": second_person_hits,
        "reactive_hits": reactive_hits,
        "formal_hits": formal_hits,
        "signals": signals[:8],
    }


def _repetition_profile(tokens: list[str]) -> dict:
    tokens = [token for token in tokens if token]
    if not tokens:
        return {"top_token_ratio": 0.0, "repeated_bigram_ratio": 0.0, "repeated_trigram_ratio": 0.0}
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    return {
        "top_token_ratio": max(counts.values()) / max(1, len(tokens)),
        "repeated_bigram_ratio": _repeated_ngram_ratio(tokens, 2),
        "repeated_trigram_ratio": _repeated_ngram_ratio(tokens, 3),
    }


def _repeated_ngram_ratio(tokens: list[str], size: int) -> float:
    if len(tokens) < size * 2:
        return 0.0
    counts: dict[tuple[str, ...], int] = {}
    for idx in range(0, len(tokens) - size + 1):
        gram = tuple(tokens[idx : idx + size])
        counts[gram] = counts.get(gram, 0) + 1
    repeated = sum(count for count in counts.values() if count > 1)
    return repeated / max(1, len(tokens) - size + 1)


def _classify_commentary_segment(segment: list[dict]) -> dict:
    text = transcript_text(segment)
    normal = _normal_text(text)
    word_count = len(segment)
    source = _speech_source_evidence(normal)

    return {
        "start": round(float(segment[0]["start"]), 3),
        "end": round(float(segment[-1]["end"]), 3),
        "text": text,
        "word_count": word_count,
        "label": source["label"],
        "confidence": source["confidence"],
        "scores": {
            "creator_commentary": source["creator_score"],
            "game_narration": source["game_score"],
        },
        "signals": source["signals"],
    }


def evaluate_candidate(
    candidate: dict,
    words: list[dict],
    extraction_start: float,
    extraction_end: float,
    video_duration: float,
    target_duration: int,
    selected_stream: int | None,
    quality_floor: float | None = None,
    detection_preference: str = "auto",
    commentary_guard: bool = False,
    commentary_guard_policy: str = "creator",
) -> dict:
    words = clean_words(words)
    text = transcript_text(words)
    normal = _normal_text(text)
    hook_points = _weighted_score(normal, HOOK_WEIGHTS)
    weak_points = _weighted_score(normal, WEAK_WEIGHTS)
    aftermath_points = _weighted_score(normal, AFTERMATH_WEIGHTS)
    visual_diagnostics = candidate.get("visual_diagnostics") if isinstance(candidate.get("visual_diagnostics"), dict) else {}
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
    quality_floor = MIN_QUALITY_SCORE if quality_floor is None else float(quality_floor)
    detection_preference = normalize_detection_preference(detection_preference)
    moment_categories = score_moment_categories(
        text,
        candidate,
        hook_points=hook_points,
        weak_points=weak_points,
        aftermath_points=aftermath_points,
        word_count=word_count,
        duration=duration,
        visual_diagnostics=visual_diagnostics,
    )

    render_start, render_end, render_words = trim_candidate_with_transcript(
        candidate, words, extraction_start, extraction_end, video_duration, target_duration
    )
    commentary_guard_result = classify_commentary_guard(
        render_words,
        enabled=bool(commentary_guard),
    )
    subtitle_words, commentary_guard_application = apply_commentary_subtitle_policy(
        render_words,
        commentary_guard_result,
        policy=commentary_guard_policy,
    )
    commentary_guard_result["policy"] = normalize_commentary_subtitle_policy(commentary_guard_policy)
    commentary_guard_result["application"] = commentary_guard_application
    commentary_guard_result["output_changed"] = bool(commentary_guard_application.get("output_changed"))
    commentary_guard_result["selection_impact"] = commentary_guard_application.get("selection_impact", "none")
    commentary_guard_result["subtitle_impact"] = commentary_guard_application.get("subtitle_impact", "none")
    if commentary_guard_application.get("output_changed"):
        commentary_guard_result["mode"] = "light_filter"
    commentary_selection_guard = commentary_guard_selection_penalty(
        commentary_guard_result,
        policy=commentary_guard_policy,
    )
    commentary_guard_penalty = float(commentary_selection_guard.get("selection_penalty") or 0.0)
    if commentary_guard_penalty:
        quality = max(0.0, min(1.0, quality - commentary_guard_penalty))
        commentary_selection_guard["quality_before_penalty"] = round(float(quality + commentary_guard_penalty), 4)
        commentary_selection_guard["quality_after_penalty"] = round(float(quality), 4)
        commentary_guard_result["selection_impact"] = "quality_penalty"
    commentary_guard_result["selection_penalty"] = round(float(commentary_guard_penalty), 4)
    commentary_guard_result["selection_reason"] = commentary_selection_guard.get("reason", "")
    commentary_guard_result["selection"] = commentary_selection_guard
    commentary_guard_summary = _compact_commentary_guard(commentary_guard_result)
    music_lyrics_guard = classify_music_lyrics_guard(
        render_words,
        policy=commentary_guard_policy,
    )
    music_lyrics_penalty = float(music_lyrics_guard.get("selection_penalty") or 0.0)
    if music_lyrics_penalty:
        quality = max(0.0, min(1.0, quality - music_lyrics_penalty))
        music_lyrics_guard["quality_before_penalty"] = round(float(quality + music_lyrics_penalty), 4)
        music_lyrics_guard["quality_after_penalty"] = round(float(quality), 4)

    reject_reason = ""
    if word_count < MIN_WORDS:
        reject_reason = "too_few_words"
    elif music_lyrics_guard.get("reject_candidate"):
        reject_reason = "music_lyrics_not_creator_commentary"
    elif quality < quality_floor:
        reject_reason = "low_transcript_quality"
    elif not render_words:
        reject_reason = "empty_after_trim"

    moment = {
        **candidate,
        "start": int(render_start),
        "end": int(render_end),
        "duration": int(render_end - render_start),
        "quality_score": float(round(quality, 4)),
        "quality_floor": float(round(quality_floor, 4)),
        "detection_preference": detection_preference,
        "transcript": transcript_text(subtitle_words),
        "word_count": len(subtitle_words),
        "analysis_word_count": len(render_words),
        "subtitle_word_count": len(subtitle_words),
        "speech_stream": selected_stream,
        "subtitle_generated": False,
        "subtitles_burned": False,
        "transcript_source": "pipeline",
        "moment_categories": moment_categories,
        "primary_category": moment_categories.get("primary"),
        "visual_diagnostics": visual_diagnostics,
        "commentary_guard": commentary_guard_summary,
        "music_lyrics_guard": music_lyrics_guard,
        "music_lyrics_penalty": round(float(music_lyrics_penalty), 4),
        "ranker": {
            "hook_points": hook_points,
            "weak_points": weak_points,
            "aftermath_points": aftermath_points,
            "commentary_guard_selection_penalty": round(float(commentary_guard_penalty), 4),
            "music_lyrics_penalty": round(float(music_lyrics_penalty), 4),
            "first_word_start": first_word_start,
            "last_word_end": last_word_end,
            "reject_reason": reject_reason,
            "quality_floor": float(round(quality_floor, 4)),
            "detection_preference": detection_preference,
            "moment_categories": moment_categories,
            "primary_category": moment_categories.get("primary"),
            "visual_diagnostics": visual_diagnostics,
            "commentary_guard": commentary_guard_summary,
            "music_lyrics_guard": music_lyrics_guard,
        },
    }

    return {
        "accepted": reject_reason == "",
        "reject_reason": reject_reason,
        "quality_score": quality,
        "quality_floor": quality_floor,
        "detection_preference": detection_preference,
        "candidate": candidate,
        "moment": moment,
        "moment_categories": moment_categories,
        "visual_diagnostics": visual_diagnostics,
        "commentary_guard": commentary_guard_result,
        "commentary_guard_selection": commentary_selection_guard,
        "commentary_guard_selection_penalty": commentary_guard_penalty,
        "music_lyrics_guard": music_lyrics_guard,
        "music_lyrics_penalty": music_lyrics_penalty,
        "words": subtitle_words,
        "analysis_words": render_words,
        "transcript": text,
        "word_count": word_count,
        "subtitle_word_count": len(subtitle_words),
        "selected_stream": selected_stream,
    }


def score_moment_categories(
    text: str,
    candidate: dict | None = None,
    *,
    hook_points: float = 0.0,
    weak_points: float = 0.0,
    aftermath_points: float = 0.0,
    word_count: int = 0,
    duration: float = 1.0,
    visual_diagnostics: dict | None = None,
) -> dict:
    """Return diagnostic moment-category scores without changing ranking yet."""
    candidate = candidate or {}
    visual_diagnostics = (
        visual_diagnostics
        if isinstance(visual_diagnostics, dict)
        else candidate.get("visual_diagnostics") if isinstance(candidate.get("visual_diagnostics"), dict) else {}
    )
    normal = _normal_text(text)
    detector_scores = candidate.get("detector_scores") if isinstance(candidate.get("detector_scores"), dict) else {}
    audio = _score01(detector_scores.get("audio", candidate.get("audio_score", 0.0)))
    variance = _score01(detector_scores.get("variance", candidate.get("variance_score", 0.0)))
    scene = _score01(detector_scores.get("scene", candidate.get("scene_score", 0.0)))
    visual_energy = _score01(visual_diagnostics.get("visual_energy", 0.0))
    visual_failure = _score01(visual_diagnostics.get("possible_failure_score", 0.0))
    visual_scenic = _score01(visual_diagnostics.get("scenic_score", 0.0))
    visual_ui = _score01(visual_diagnostics.get("ui_density", 0.0))
    visual_dark = _score01(visual_diagnostics.get("dark_scene_score", 0.0))
    visual_red = _score01(visual_diagnostics.get("red_flash_score", 0.0))
    visual_black = _score01(visual_diagnostics.get("black_frame_ratio", 0.0))
    density = _score01(float(word_count or 0) / max(8.0, float(duration or 1.0) * 1.15))

    phrase_scores = {
        key: _category_phrase_score(normal, phrases)
        for key, phrases in CATEGORY_PHRASES.items()
    }
    source = _speech_source_evidence(normal)
    creator_signal = _score01(source.get("creator_signal", 0.0))
    game_signal = _score01(source.get("game_signal", 0.0))
    hook_signal = _score01(float(hook_points or 0.0) / 10.0)
    weak_signal = _score01(float(weak_points or 0.0) / 8.0)
    aftermath_signal = _score01(float(aftermath_points or 0.0) / 8.0)

    tutorial_phrase = phrase_scores["tutorial_or_explainer"]
    commentary_phrase = phrase_scores["commentary_or_review"]
    lore_phrase = phrase_scores["lore_or_story"]
    high_energy_phrase = phrase_scores["high_energy"]
    high_energy_hook = hook_signal
    technical_explainer = _score01(
        _weighted_score(
            normal,
            (
                ("what the difference", 2.5),
                ("what is the difference", 2.5),
                ("difference between", 2.5),
                ("how this works", 2.0),
                ("mechanic", 1.5),
            ),
        )
        / 5.0
    )
    evidence_notes: list[str] = []
    if technical_explainer >= 0.25:
        high_energy_phrase *= 0.25
        high_energy_hook *= 0.35
        evidence_notes.append("technical_explainer_tempered_high_energy")
    action_visual = max(visual_energy, visual_red * 0.45)
    failure_visual = max(visual_failure, visual_red)
    action_evidence = max(
        high_energy_phrase,
        high_energy_hook,
        action_visual,
    )
    ambient_energy_weight = 1.0 if action_evidence >= 0.25 else 0.35
    high_energy_multiplier = 1.0
    if game_signal > creator_signal + 0.15 and action_visual < 0.25 and high_energy_phrase < 0.45:
        high_energy_multiplier *= 0.55
        evidence_notes.append("game_source_tempered_high_energy")
    if technical_explainer >= 0.25 and action_visual < 0.25:
        high_energy_multiplier *= 0.60
    context_score = max(tutorial_phrase, commentary_phrase, lore_phrase)
    confirmed_blank_visual = visual_black >= 0.67 and visual_energy < 0.20 and visual_scenic < 0.20
    if confirmed_blank_visual:
        evidence_notes.append("confirmed_black_frames_tempered_category")
        high_energy_multiplier *= 0.45
    if context_score >= max(high_energy_phrase, high_energy_hook) + 0.10 and action_visual < 0.25:
        high_energy_multiplier *= 0.55
        evidence_notes.append("context_tempered_high_energy")
    if commentary_phrase >= 0.35 and action_evidence < 0.25:
        high_energy_multiplier *= 0.75
        evidence_notes.append("commentary_context_tempered_high_energy")

    tutorial_multiplier = 1.0
    if game_signal > creator_signal + 0.20 and creator_signal < 0.25:
        tutorial_multiplier = 0.78
        evidence_notes.append("game_source_tempered_tutorial")
    commentary_multiplier = 1.0
    if game_signal > creator_signal + 0.20 and commentary_phrase < 0.40:
        commentary_multiplier = 0.65
        evidence_notes.append("game_source_tempered_commentary")
    scores = {
        "high_energy": _score01(
            high_energy_multiplier
            * (
                0.36 * high_energy_hook
                + 0.22 * high_energy_phrase
                + ambient_energy_weight * (0.12 * audio + 0.10 * variance)
                + 0.14 * action_visual
                + 0.06 * creator_signal
            )
        ),
        "death_or_failure": _score01(
            0.38 * phrase_scores["death_or_failure"]
            + 0.22 * aftermath_signal
            + 0.16 * hook_signal
            + 0.10 * audio
            + 0.18 * failure_visual
        ),
        "tutorial_or_explainer": _score01(
            tutorial_multiplier
            * (0.68 * tutorial_phrase + 0.14 * density + 0.10 * creator_signal + 0.08 * visual_ui)
        )
        if tutorial_phrase > 0 else 0.0,
        "commentary_or_review": _score01(
            commentary_multiplier
            * (0.62 * commentary_phrase + 0.16 * density + 0.14 * creator_signal + 0.08 * (1.0 - aftermath_signal))
        )
        if commentary_phrase > 0 or creator_signal >= 0.25 else 0.0,
        "lore_or_story": _score01(0.70 * lore_phrase + 0.12 * density + 0.12 * game_signal + 0.06 * (1.0 - audio))
        if lore_phrase > 0 or game_signal >= 0.25 else 0.0,
        "atmosphere_or_visual": _score01(
            0.40 * phrase_scores["atmosphere_or_visual"]
            + 0.20 * scene
            + 0.12 * variance
            + 0.14 * visual_scenic
            + 0.06 * visual_dark
            + 0.08 * (1.0 - density)
        ),
        "low_value": _score01(
            0.48 * phrase_scores["low_value"]
            + 0.30 * weak_signal
            + 0.14 * (1.0 - density)
            + 0.08 * aftermath_signal
            + (0.24 if confirmed_blank_visual else 0.0)
        ),
    }
    if confirmed_blank_visual:
        scores["atmosphere_or_visual"] = round(scores["atmosphere_or_visual"] * 0.35, 4)
    if hook_signal >= 0.45:
        scores["low_value"] = round(scores["low_value"] * 0.7, 4)
    if visual_failure >= 0.45 or visual_red >= 0.45:
        scores["low_value"] = round(scores["low_value"] * 0.65, 4)

    positive_scores = {k: v for k, v in scores.items() if k != "low_value"}
    primary, primary_score = max(positive_scores.items(), key=lambda item: item[1])
    if scores["low_value"] >= primary_score + 0.08 or (
        primary_score < 0.20 and scores["low_value"] >= 0.10
    ):
        primary = "low_value"
        primary_score = scores["low_value"]

    return {
        "schema_version": 1,
        "primary": primary,
        "confidence": round(float(primary_score), 4),
        "scores": {key: round(float(scores[key]), 4) for key in CATEGORY_KEYS},
        "signals": {
            "audio_energy": round(audio, 4),
            "variance": round(variance, 4),
            "scene_change": round(scene, 4),
            "speech_density": round(density, 4),
            "hook_signal": round(hook_signal, 4),
            "weak_signal": round(weak_signal, 4),
            "aftermath_signal": round(aftermath_signal, 4),
            "visual_energy": round(visual_energy, 4),
            "visual_failure": round(visual_failure, 4),
            "visual_scenic": round(visual_scenic, 4),
            "visual_ui_density": round(visual_ui, 4),
            "visual_dark_scene": round(visual_dark, 4),
            "visual_red_flash": round(visual_red, 4),
            "visual_black_frames": round(visual_black, 4),
            "visual_status": str(visual_diagnostics.get("status") or "missing"),
            "speech_source": source.get("label", "unclear"),
            "speech_source_confidence": source.get("confidence", 0.0),
            "creator_speech": round(creator_signal, 4),
            "game_speech": round(game_signal, 4),
            "technical_explainer": round(technical_explainer, 4),
            "ambient_energy_weight": round(ambient_energy_weight, 4),
        },
        "evidence_notes": evidence_notes[:8],
    }


def attach_ai_moment_classification(evaluation: dict, classification: dict | None) -> dict:
    """Attach optional AI moment labels without changing ranking or output choice."""
    if not isinstance(evaluation, dict):
        return {}
    clean = _compact_ai_moment_classification(classification)
    if not clean:
        return {}

    moment = evaluation.get("moment")
    if not isinstance(moment, dict):
        moment = {}
        evaluation["moment"] = moment

    categories = copy.deepcopy(moment.get("moment_categories")) if isinstance(moment.get("moment_categories"), dict) else {}
    if not categories and isinstance(evaluation.get("moment_categories"), dict):
        categories = copy.deepcopy(evaluation["moment_categories"])
    if "primary" in categories:
        categories.setdefault("heuristic_primary", categories.get("primary"))
    categories["ai"] = clean

    moment["moment_categories"] = categories
    moment["ai_moment_classification"] = clean
    ranker = moment.get("ranker") if isinstance(moment.get("ranker"), dict) else {}
    ranker["moment_categories"] = categories
    ranker["ai_moment_classification"] = clean
    moment["ranker"] = ranker

    evaluation["moment_categories"] = categories
    evaluation["ai_moment_classification"] = clean
    if isinstance(evaluation.get("selection_moment"), dict):
        selection_moment = evaluation["selection_moment"]
        selection_categories = copy.deepcopy(selection_moment.get("moment_categories")) if isinstance(selection_moment.get("moment_categories"), dict) else copy.deepcopy(categories)
        selection_categories["ai"] = clean
        if "primary" in selection_categories:
            selection_categories.setdefault("heuristic_primary", selection_categories.get("primary"))
        selection_moment["moment_categories"] = selection_categories
        selection_moment["ai_moment_classification"] = clean
        selection_ranker = selection_moment.get("ranker") if isinstance(selection_moment.get("ranker"), dict) else {}
        selection_ranker["moment_categories"] = selection_categories
        selection_ranker["ai_moment_classification"] = clean
        selection_moment["ranker"] = selection_ranker
    return clean


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
            _safe_float(e.get(score_key, e.get("quality_score", 0.0)), 0.0) or 0.0,
            _safe_float(e.get("quality_score", 0.0), 0.0) or 0.0,
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
        base_quality = _safe_float(evaluation.get("quality_score", 0.0), 0.0) or 0.0
        rank_score = _safe_float(evaluation.get(score_key, base_quality), base_quality) or base_quality
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
        voice = evaluation.get("voice_scoring") or {}
        if "voice_profile_quality_score" in evaluation:
            voice_score = _safe_float(evaluation["voice_profile_quality_score"], rank_score)
            evaluation["moment"]["voice_profile_quality_score"] = round(
                rank_score if voice_score is None else voice_score,
                4,
            )
        if "voice_adjustment" in voice:
            evaluation["moment"]["voice_adjustment"] = voice.get("voice_adjustment")
        if voice:
            evaluation["moment"]["voice_scoring"] = copy.deepcopy(voice)
        category_scoring = evaluation.get("moment_category_scoring") or {}
        if "moment_category_quality_score" in evaluation:
            category_score = _safe_float(evaluation["moment_category_quality_score"], rank_score)
            evaluation["moment"]["moment_category_quality_score"] = round(
                rank_score if category_score is None else category_score,
                4,
            )
        if "category_adjustment" in category_scoring:
            evaluation["moment"]["moment_category_adjustment"] = category_scoring.get("category_adjustment")
        if category_scoring:
            evaluation["moment"]["moment_category_scoring"] = copy.deepcopy(category_scoring)
        ai_scoring = evaluation.get("ai_moment_scoring") or {}
        if "ai_moment_quality_score" in evaluation:
            ai_score = _safe_float(evaluation["ai_moment_quality_score"], rank_score)
            evaluation["moment"]["ai_moment_quality_score"] = round(
                rank_score if ai_score is None else ai_score,
                4,
            )
        if "ai_adjustment" in ai_scoring:
            evaluation["moment"]["ai_adjustment"] = ai_scoring.get("ai_adjustment")
        if ai_scoring:
            evaluation["moment"]["ai_moment_scoring"] = copy.deepcopy(ai_scoring)
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
        key=lambda e: _safe_float(e.get("quality_score"), 0.0) or 0.0,
        reverse=True,
    )
    baseline_rank_by_id = {id(e): idx for idx, e in enumerate(baseline_order, 1)}
    learned_enabled = profile["signal_count"] > 0 and max_adjustment > 0

    for evaluation in evaluations:
        shadow = _score_shadow_candidate(evaluation, profile, source_id, source_stem)
        base_score = _safe_float(evaluation.get("quality_score"), 0.0) or 0.0
        raw_adjustment = _safe_float(shadow.get("adjustment"), 0.0) or 0.0
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


def apply_voice_profile_scoring(
    evaluations: list[dict],
    voice_profile_status: dict | None,
    *,
    score_key: str = "learned_quality_score",
    max_adjustment: float = VOICE_PROFILE_SELECTION_MAX_ADJUSTMENT,
) -> dict:
    """Blend an explicit opt-in creator-voice signal into candidate scores."""
    status = voice_profile_status if isinstance(voice_profile_status, dict) else {}
    max_adjustment = max(0.0, _safe_float(max_adjustment, 0.0) or 0.0)
    status_ready = bool(
        status.get("ranking_enabled")
        and status.get("enabled")
        and status.get("can_score", status.get("enrolled"))
        and max_adjustment > 0
    )
    accepted_count = sum(1 for evaluation in evaluations if evaluation.get("accepted"))
    scored_count = 0
    for evaluation in evaluations:
        voice = evaluation.get("voice_profile") if isinstance(evaluation.get("voice_profile"), dict) else {}
        confidence = _safe_float(voice.get("confidence"), None)
        reason = str(voice.get("reason") or "no_voice_profile")
        if evaluation.get("accepted") and confidence is not None and reason == "scored":
            scored_count += 1
    scored_ratio = (scored_count / accepted_count) if accepted_count else 0.0
    coverage_ready = bool(
        scored_count >= MIN_VOICE_RANKING_SCORED_CANDIDATES
        and scored_ratio >= MIN_VOICE_RANKING_SCORED_RATIO
    )
    ranking_enabled = bool(status_ready and coverage_ready)
    if not status_ready:
        disabled_reason = str(status.get("blocking_reason") or status.get("reason") or "voice_profile_not_ready")
    elif not coverage_ready:
        disabled_reason = "insufficient_scored_candidates"
    else:
        disabled_reason = ""

    for evaluation in evaluations:
        base_score = _safe_float(evaluation.get(score_key, evaluation.get("quality_score", 0.0)), 0.0) or 0.0
        voice = evaluation.get("voice_profile") if isinstance(evaluation.get("voice_profile"), dict) else {}
        confidence = _safe_float(voice.get("confidence"), None)
        reason = str(voice.get("reason") or "no_voice_profile")
        raw_adjustment = 0.0
        if confidence is not None and reason == "scored":
            raw_adjustment = (confidence - 0.5) * 2.0 * max_adjustment
        voice_adjustment = 0.0
        if ranking_enabled and evaluation.get("accepted"):
            voice_adjustment = max(-max_adjustment, min(max_adjustment, raw_adjustment))
        voice_score = max(0.0, min(1.0, base_score + voice_adjustment))
        scoring = {
            "schema_version": VOICE_PROFILE_SELECTION_SCHEMA_VERSION,
            "mode": "voice_profile_blend",
            "ranking_enabled": ranking_enabled,
            "selection_impact": "capped_rank_adjustment" if ranking_enabled else "none",
            "disabled_reason": disabled_reason,
            "score_source": score_key,
            "base_score": round(base_score, 4),
            "voice_confidence": round(confidence, 4) if confidence is not None else None,
            "voice_reason": reason,
            "voice_selection_max_adjustment": round(max_adjustment, 4),
            "min_scored_candidate_count": MIN_VOICE_RANKING_SCORED_CANDIDATES,
            "min_scored_candidate_ratio": MIN_VOICE_RANKING_SCORED_RATIO,
            "scored_candidate_count": scored_count,
            "accepted_candidate_count": accepted_count,
            "scored_candidate_ratio": round(scored_ratio, 4),
            "voice_adjustment": round(voice_adjustment, 4),
            "voice_profile_quality_score": round(voice_score, 4),
            "learned_quality_score": round(_safe_float(evaluation.get("learned_quality_score"), base_score) or base_score, 4),
            "selected_by_baseline": False,
            "selected_by_voice": False,
            "baseline_rank": None,
            "voice_rank": None,
            "rank_delta": None,
            "selection_delta": "",
        }
        evaluation["voice_profile_quality_score"] = voice_score
        evaluation["voice_scoring"] = scoring

    return {
        "schema_version": VOICE_PROFILE_SELECTION_SCHEMA_VERSION,
        "mode": "voice_profile_blend",
        "ranking_enabled": ranking_enabled,
        "selection_impact": "capped_rank_adjustment" if ranking_enabled else "none",
        "disabled_reason": disabled_reason,
        "score_source": score_key,
        "voice_profile_selection_max_adjustment": round(max_adjustment, 4),
        "has_voice_profile_scores": coverage_ready,
        "scored_candidate_count": scored_count,
        "accepted_candidate_count": accepted_count,
        "scored_candidate_ratio": round(scored_ratio, 4),
        "min_scored_candidate_count": MIN_VOICE_RANKING_SCORED_CANDIDATES,
        "min_scored_candidate_ratio": MIN_VOICE_RANKING_SCORED_RATIO,
    }


def apply_moment_category_scoring(
    evaluations: list[dict],
    *,
    enabled: bool = False,
    score_key: str = "learned_quality_score",
    max_adjustment: float = MOMENT_CATEGORY_SELECTION_MAX_ADJUSTMENT,
    max_count: int = 0,
    min_gap: int = 12,
    diversity_max_adjustment: float = MOMENT_CATEGORY_DIVERSITY_MAX_ADJUSTMENT,
) -> dict:
    """Blend deterministic moment categories into ranking when settings or depth enable it."""
    safe_max = max(0.0, _safe_float(max_adjustment, 0.0) or 0.0)
    diversity_cap = max(0.0, _safe_float(diversity_max_adjustment, 0.0) or 0.0)
    ranking_enabled = bool(enabled and safe_max > 0)
    scored_count = 0
    diversity_count = 0
    baseline_category_counts: dict[str, int] = {}
    accepted = [e for e in evaluations if e.get("accepted")]
    target_count = max(0, int(max_count or 0))
    if ranking_enabled and diversity_cap > 0 and target_count > 1:
        baseline_order = sorted(
            accepted,
            key=lambda e: (
                _safe_float(e.get(score_key, e.get("quality_score", 0.0)), 0.0) or 0.0,
                _safe_float(e.get("quality_score", 0.0), 0.0) or 0.0,
            ),
            reverse=True,
        )
        for row in _select_for_report(baseline_order, target_count, min_gap):
            categories = _categories_for_scoring(row)
            primary = str(categories.get("primary") or "")
            if primary:
                baseline_category_counts[primary] = baseline_category_counts.get(primary, 0) + 1

    for evaluation in evaluations:
        base_score = _safe_float(evaluation.get(score_key, evaluation.get("quality_score", 0.0)), 0.0) or 0.0
        categories = _categories_for_scoring(evaluation)
        signal = _moment_category_signal(categories)
        has_signal = bool(categories and abs(signal) > 0.0001)
        raw_adjustment = signal * safe_max
        diversity_adjustment = 0.0
        primary = str(categories.get("primary") or "") if isinstance(categories, dict) else ""
        if ranking_enabled and evaluation.get("accepted") and diversity_cap > 0 and target_count > 1:
            diversity_adjustment = _moment_category_diversity_adjustment(
                categories,
                baseline_category_counts,
                max_adjustment=diversity_cap,
            )
        category_adjustment = 0.0
        if ranking_enabled and evaluation.get("accepted") and has_signal:
            category_adjustment = max(-safe_max, min(safe_max, raw_adjustment + diversity_adjustment))
            scored_count += 1
        elif ranking_enabled and evaluation.get("accepted") and diversity_adjustment:
            category_adjustment = max(-safe_max, min(safe_max, diversity_adjustment))
            scored_count += 1
        if category_adjustment and diversity_adjustment:
            diversity_count += 1
        category_score = max(0.0, min(1.0, base_score + category_adjustment))
        scoring = {
            "schema_version": MOMENT_CATEGORY_SELECTION_SCHEMA_VERSION,
            "mode": "moment_category_blend",
            "ranking_enabled": ranking_enabled,
            "selection_impact": "capped_rank_adjustment" if ranking_enabled else "none",
            "score_source": score_key,
            "base_score": round(base_score, 4),
            "primary_category": primary,
            "category_confidence": categories.get("confidence") if isinstance(categories, dict) else None,
            "category_signal": round(signal, 4),
            "moment_category_selection_max_adjustment": round(safe_max, 4),
            "category_diversity_adjustment": round(diversity_adjustment, 4),
            "category_diversity_cap": round(diversity_cap, 4),
            "baseline_category_count": baseline_category_counts.get(primary, 0),
            "category_adjustment": round(category_adjustment, 4),
            "moment_category_quality_score": round(category_score, 4),
            "learned_quality_score": round(_safe_float(evaluation.get("learned_quality_score"), base_score) or base_score, 4),
            "selected_by_baseline": False,
            "selected_by_category": False,
            "baseline_rank": None,
            "category_rank": None,
            "rank_delta": None,
            "selection_delta": "",
        }
        evaluation["moment_categories"] = copy.deepcopy(categories)
        if isinstance(evaluation.get("moment"), dict):
            evaluation["moment"]["moment_categories"] = copy.deepcopy(categories)
            evaluation["moment"]["primary_category"] = categories.get("primary") if isinstance(categories, dict) else ""
        evaluation["moment_category_quality_score"] = category_score
        evaluation["moment_category_scoring"] = scoring

    return {
        "schema_version": MOMENT_CATEGORY_SELECTION_SCHEMA_VERSION,
        "mode": "moment_category_blend",
        "ranking_enabled": ranking_enabled,
        "selection_impact": "capped_rank_adjustment" if ranking_enabled else "none",
        "score_source": score_key,
        "moment_category_selection_max_adjustment": round(safe_max, 4),
        "moment_category_diversity_max_adjustment": round(diversity_cap, 4),
        "has_category_scores": scored_count > 0,
        "scored_candidate_count": scored_count,
        "diversity_candidate_count": diversity_count,
        "baseline_category_counts": baseline_category_counts,
    }


def apply_ai_moment_scoring(
    evaluations: list[dict],
    *,
    enabled: bool = False,
    score_key: str = "moment_category_quality_score",
    max_adjustment: float = AI_MOMENT_SELECTION_MAX_ADJUSTMENT,
    confidence_floor: float = 0.70,
) -> dict:
    """Blend high-confidence local/Ollama AI labels into Deep Analysis ranking."""
    safe_max = max(0.0, _safe_float(max_adjustment, 0.0) or 0.0)
    confidence_floor = max(0.0, min(1.0, _safe_float(confidence_floor, 0.70) or 0.70))
    ranking_enabled = bool(enabled and safe_max > 0)
    eligible_count = 0
    scored_count = 0

    for evaluation in evaluations:
        base_score = _safe_float(evaluation.get(score_key, evaluation.get("quality_score", 0.0)), 0.0) or 0.0
        ai = _ai_classification_for_scoring(evaluation)
        eligibility = _ai_moment_scoring_eligibility(ai, evaluation, confidence_floor=confidence_floor)
        raw_adjustment = 0.0
        if eligibility["eligible"]:
            raw_adjustment = _ai_moment_signal(ai) * safe_max
            eligible_count += 1
        ai_adjustment = 0.0
        if ranking_enabled and evaluation.get("accepted") and eligibility["eligible"]:
            ai_adjustment = max(-safe_max, min(safe_max, raw_adjustment))
            if abs(ai_adjustment) > 0.0001:
                scored_count += 1
        ai_score = max(0.0, min(1.0, base_score + ai_adjustment))
        scoring = {
            "schema_version": AI_MOMENT_SELECTION_SCHEMA_VERSION,
            "mode": "ai_moment_blend",
            "ranking_enabled": ranking_enabled,
            "selection_impact": "capped_rank_adjustment" if ranking_enabled else "none",
            "score_source": score_key,
            "base_score": round(base_score, 4),
            "ai_score": ai.get("ai_viral_score"),
            "ai_confidence": ai.get("ai_confidence"),
            "ai_primary_category": ai.get("primary_category"),
            "ai_provider": ai.get("provider"),
            "ai_status": ai.get("status"),
            "ai_scoring_eligible": bool(eligibility["eligible"]),
            "ai_ineligible_reason": eligibility["reason"],
            "ai_selection_max_adjustment": round(safe_max, 4),
            "ai_adjustment": round(ai_adjustment, 4),
            "ai_moment_quality_score": round(ai_score, 4),
            "selected_by_baseline": False,
            "selected_by_ai": False,
            "baseline_rank": None,
            "ai_rank": None,
            "rank_delta": None,
            "selection_delta": "",
        }
        evaluation["ai_moment_quality_score"] = ai_score
        evaluation["ai_moment_scoring"] = scoring
        if isinstance(evaluation.get("moment"), dict):
            evaluation["moment"]["ai_moment_scoring"] = copy.deepcopy(scoring)
            evaluation["moment"]["ai_moment_quality_score"] = round(ai_score, 4)

    return {
        "schema_version": AI_MOMENT_SELECTION_SCHEMA_VERSION,
        "mode": "ai_moment_blend",
        "ranking_enabled": ranking_enabled,
        "selection_impact": "capped_rank_adjustment" if ranking_enabled else "none",
        "score_source": score_key,
        "ai_moment_selection_max_adjustment": round(safe_max, 4),
        "confidence_floor": round(confidence_floor, 4),
        "has_ai_scores": scored_count > 0,
        "eligible_candidate_count": eligible_count,
        "scored_candidate_count": scored_count,
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
        key=lambda e: _safe_float(e.get("quality_score"), 0.0) or 0.0,
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
            _safe_float(
                e.get("shadow_scoring", {}).get(
                    "learned_quality_score",
                    e.get("learned_quality_score", e.get("quality_score", 0.0)),
                ),
                0.0,
            ) or 0.0,
            _safe_float(e.get("quality_score"), 0.0) or 0.0,
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
                "quality_score": round(_safe_float(evaluation.get("quality_score"), 0.0) or 0.0, 4),
                "shadow_score": shadow.get("shadow_score"),
                "learned_quality_score": shadow.get("learned_quality_score"),
                "learned_adjustment": shadow.get("learned_adjustment"),
                "baseline_rank": shadow.get("baseline_rank"),
                "shadow_rank": shadow.get("learned_rank"),
                "rank_delta": rank_delta,
                "selection_delta": selection_delta,
                "moment_categories": moment.get("moment_categories"),
                "primary_category": moment.get("primary_category"),
                "signals": shadow.get("signals", {}),
                "transcript_preview": _preview_text(moment.get("transcript", "")),
            }
        )
    top_changes.sort(
        key=lambda row: (
            row.get("selection_delta") in {"added_by_learning", "dropped_by_learning"},
            abs(int(row.get("rank_delta") or 0)),
            _safe_float(row.get("learned_quality_score") or row.get("shadow_score"), 0.0) or 0.0,
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


def build_voice_profile_shadow_report(
    evaluations: list[dict],
    selected: list[dict],
    *,
    max_count: int = 0,
    min_gap: int = 12,
    max_adjustment: float = VOICE_PROFILE_SHADOW_MAX_ADJUSTMENT,
    score_key: str = "learned_quality_score",
) -> dict:
    """Show how creator voice confidence would reorder candidates without changing output."""
    accepted = [e for e in evaluations if e.get("accepted")]
    target_count = max(0, int(max_count or len(selected) or len(accepted)))
    max_adjustment = max(0.0, _safe_float(max_adjustment, 0.0) or 0.0)

    current_order = sorted(
        accepted,
        key=lambda e: (
            _safe_float(e.get(score_key, e.get("quality_score", 0.0)), 0.0) or 0.0,
            _safe_float(e.get("quality_score", 0.0), 0.0) or 0.0,
        ),
        reverse=True,
    )
    current_rank_by_id = {id(e): idx for idx, e in enumerate(current_order, 1)}
    current_selected = selected or _select_for_report(current_order, target_count, min_gap)
    current_selected_ids = {id(e) for e in current_selected}
    selected_ids = {id(e) for e in selected}
    scored_count = 0

    for evaluation in evaluations:
        base_score = _safe_float(evaluation.get(score_key, evaluation.get("quality_score", 0.0)), 0.0) or 0.0
        voice = evaluation.get("voice_profile") if isinstance(evaluation.get("voice_profile"), dict) else {}
        confidence = _safe_float(voice.get("confidence"), None)
        reason = str(voice.get("reason") or "no_voice_profile")
        adjustment = 0.0
        if confidence is not None and reason == "scored" and max_adjustment > 0:
            adjustment = max(-max_adjustment, min(max_adjustment, (confidence - 0.5) * 2.0 * max_adjustment))
            scored_count += 1
        shadow_score = max(0.0, min(1.0, base_score + adjustment))
        evaluation["voice_profile_shadow"] = {
            "schema_version": VOICE_PROFILE_SHADOW_SCHEMA_VERSION,
            "mode": "voice_profile_shadow",
            "diagnostic_only": True,
            "selection_impact": "none",
            "output_changed": False,
            "score_source": score_key,
            "base_score": round(base_score, 4),
            "voice_confidence": round(confidence, 4) if confidence is not None else None,
            "voice_reason": reason,
            "max_adjustment": round(max_adjustment, 4),
            "voice_adjustment": round(adjustment, 4),
            "voice_shadow_score": round(shadow_score, 4),
            "current_rank": current_rank_by_id.get(id(evaluation)),
            "shadow_rank": None,
            "rank_delta": None,
            "selected_by_current": id(evaluation) in current_selected_ids,
            "selected_by_actual_run": id(evaluation) in selected_ids,
            "would_select": False,
            "selection_delta": "",
        }

    shadow_order = sorted(
        accepted,
        key=lambda e: (
            _safe_float(e.get("voice_profile_shadow", {}).get("voice_shadow_score", e.get(score_key, e.get("quality_score", 0.0))), 0.0) or 0.0,
            _safe_float(e.get(score_key, e.get("quality_score", 0.0)), 0.0) or 0.0,
            _safe_float(e.get("quality_score", 0.0), 0.0) or 0.0,
        ),
        reverse=True,
    )
    for idx, evaluation in enumerate(shadow_order, 1):
        shadow = evaluation["voice_profile_shadow"]
        shadow["shadow_rank"] = idx
        current_rank = shadow.get("current_rank")
        if current_rank is not None:
            shadow["rank_delta"] = int(current_rank) - idx

    voice_selected = _select_for_report(shadow_order, target_count, min_gap)
    voice_selected_ids = {id(e) for e in voice_selected}
    for evaluation in evaluations:
        shadow = evaluation.get("voice_profile_shadow", {})
        baseline = id(evaluation) in current_selected_ids
        would = id(evaluation) in voice_selected_ids
        shadow["would_select"] = would
        if baseline and would:
            shadow["selection_delta"] = "kept"
        elif baseline and not would:
            shadow["selection_delta"] = "would_drop_by_voice"
        elif not baseline and would:
            shadow["selection_delta"] = "would_add_by_voice"
        elif shadow.get("rank_delta"):
            shadow["selection_delta"] = "rank_changed"

    selection_delta_counts: dict[str, int] = {}
    top_changes = []
    for evaluation in accepted:
        shadow = evaluation.get("voice_profile_shadow", {})
        selection_delta = shadow.get("selection_delta", "")
        if selection_delta:
            selection_delta_counts[selection_delta] = selection_delta_counts.get(selection_delta, 0) + 1
        rank_delta = shadow.get("rank_delta")
        if not rank_delta and selection_delta not in {"would_add_by_voice", "would_drop_by_voice"}:
            continue
        moment = evaluation.get("selection_moment") or evaluation.get("moment", {})
        top_changes.append(
            {
                "candidate_rank": evaluation.get("candidate", {}).get("candidate_rank"),
                "candidate_kind": evaluation.get("candidate", {}).get("candidate_kind", ""),
                "start": moment.get("start"),
                "end": moment.get("end"),
                "base_score": shadow.get("base_score"),
                "voice_confidence": shadow.get("voice_confidence"),
                "voice_adjustment": shadow.get("voice_adjustment"),
                "voice_shadow_score": shadow.get("voice_shadow_score"),
                "current_rank": shadow.get("current_rank"),
                "shadow_rank": shadow.get("shadow_rank"),
                "rank_delta": rank_delta,
                "selection_delta": selection_delta,
                "voice_reason": shadow.get("voice_reason"),
                "transcript_preview": _preview_text(moment.get("transcript", "")),
            }
        )
    top_changes.sort(
        key=lambda row: (
            row.get("selection_delta") in {"would_add_by_voice", "would_drop_by_voice"},
            abs(int(row.get("rank_delta") or 0)),
            float(row.get("voice_shadow_score") or 0.0),
        ),
        reverse=True,
    )
    hypothetical_changed = current_selected_ids != voice_selected_ids

    return {
        "schema_version": VOICE_PROFILE_SHADOW_SCHEMA_VERSION,
        "mode": "voice_profile_shadow",
        "diagnostic_only": True,
        "output_changed": False,
        "selection_impact": "none",
        "hypothetical_selection_changed": hypothetical_changed,
        "score_source": score_key,
        "voice_profile_max_adjustment": round(max_adjustment, 4),
        "hypothetical_score_key": "voice_shadow_score",
        "has_voice_profile_scores": scored_count > 0,
        "scored_candidate_count": scored_count,
        "candidate_count": len(evaluations),
        "accepted_count": len(accepted),
        "current_selected_count": len(current_selected),
        "shadow_selected_count": len(voice_selected),
        "selection_delta_counts": selection_delta_counts,
        "current_selected": [_shadow_selection_summary(e) for e in current_selected],
        "shadow_selected": [_shadow_selection_summary(e) for e in voice_selected],
        "top_changes": top_changes[:10],
    }


def build_voice_profile_ranking_report(
    evaluations: list[dict],
    baseline_selected: list[dict],
    selected: list[dict],
    voice_profile_status: dict | None,
    *,
    max_count: int = 0,
    min_gap: int = 12,
    score_key: str = "learned_quality_score",
    voice_score_key: str = "voice_profile_quality_score",
    max_adjustment: float = VOICE_PROFILE_SELECTION_MAX_ADJUSTMENT,
) -> dict:
    """Report actual opt-in voice-profile ranking impact."""
    status = voice_profile_status if isinstance(voice_profile_status, dict) else {}
    safe_max_adjustment = max(0.0, _safe_float(max_adjustment, 0.0) or 0.0)
    if not all("voice_scoring" in evaluation for evaluation in evaluations):
        prepared = apply_voice_profile_scoring(
            evaluations,
            status,
            score_key=score_key,
            max_adjustment=safe_max_adjustment,
        )
    else:
        accepted_count = sum(1 for e in evaluations if e.get("accepted"))
        voice_rows = [e.get("voice_scoring") or {} for e in evaluations]
        ranking_enabled = any(bool(row.get("ranking_enabled")) for row in voice_rows)
        scored_rows = [
            row
            for evaluation, row in zip(evaluations, voice_rows)
            if evaluation.get("accepted")
            and row.get("voice_reason") == "scored"
            and row.get("voice_confidence") is not None
        ]
        scored_ratio = (len(scored_rows) / accepted_count) if accepted_count else 0.0
        coverage_ready = bool(
            len(scored_rows) >= MIN_VOICE_RANKING_SCORED_CANDIDATES
            and scored_ratio >= MIN_VOICE_RANKING_SCORED_RATIO
        )
        prepared = {
            "ranking_enabled": ranking_enabled,
            "selection_impact": "capped_rank_adjustment" if ranking_enabled else "none",
            "voice_profile_selection_max_adjustment": round(safe_max_adjustment, 4),
            "has_voice_profile_scores": coverage_ready,
            "scored_candidate_count": len(scored_rows),
            "accepted_candidate_count": accepted_count,
            "scored_candidate_ratio": round(scored_ratio, 4),
            "min_scored_candidate_count": MIN_VOICE_RANKING_SCORED_CANDIDATES,
            "min_scored_candidate_ratio": MIN_VOICE_RANKING_SCORED_RATIO,
            "disabled_reason": next((row.get("disabled_reason") for row in voice_rows if row.get("disabled_reason")), ""),
        }

    accepted = [e for e in evaluations if e.get("accepted")]
    target_count = max(0, int(max_count or len(selected) or len(baseline_selected) or len(accepted)))

    baseline_order = sorted(
        accepted,
        key=lambda e: (
            _safe_float(e.get(score_key, e.get("quality_score", 0.0)), 0.0) or 0.0,
            _safe_float(e.get("quality_score", 0.0), 0.0) or 0.0,
        ),
        reverse=True,
    )
    voice_order = sorted(
        accepted,
        key=lambda e: (
            _safe_float(e.get(voice_score_key, e.get(score_key, e.get("quality_score", 0.0))), 0.0) or 0.0,
            _safe_float(e.get(score_key, e.get("quality_score", 0.0)), 0.0) or 0.0,
            _safe_float(e.get("quality_score", 0.0), 0.0) or 0.0,
        ),
        reverse=True,
    )
    baseline_rank_by_id = {id(e): idx for idx, e in enumerate(baseline_order, 1)}
    voice_rank_by_id = {id(e): idx for idx, e in enumerate(voice_order, 1)}
    baseline_selected = baseline_selected or _select_for_report(baseline_order, target_count, min_gap)
    selected = selected or baseline_selected
    baseline_selected_ids = {id(e) for e in baseline_selected}
    selected_ids = {id(e) for e in selected}

    selection_delta_counts: dict[str, int] = {}
    top_changes = []
    for evaluation in evaluations:
        scoring = evaluation.get("voice_scoring") or {}
        baseline_rank = baseline_rank_by_id.get(id(evaluation))
        voice_rank = voice_rank_by_id.get(id(evaluation))
        rank_delta = None
        if baseline_rank is not None and voice_rank is not None:
            rank_delta = int(baseline_rank) - int(voice_rank)
        baseline = id(evaluation) in baseline_selected_ids
        chosen = id(evaluation) in selected_ids
        if baseline and chosen:
            selection_delta = "kept"
        elif baseline and not chosen:
            selection_delta = "dropped_by_voice"
        elif not baseline and chosen:
            selection_delta = "added_by_voice"
        elif rank_delta:
            selection_delta = "rank_changed"
        else:
            selection_delta = ""
        scoring.update(
            {
                "baseline_rank": baseline_rank,
                "voice_rank": voice_rank,
                "rank_delta": rank_delta,
                "selected_by_baseline": baseline,
                "selected_by_voice": chosen,
                "selection_delta": selection_delta,
            }
        )
        evaluation["voice_scoring"] = scoring
        if selection_delta:
            selection_delta_counts[selection_delta] = selection_delta_counts.get(selection_delta, 0) + 1
        if evaluation.get("accepted") and (rank_delta or selection_delta in {"added_by_voice", "dropped_by_voice"}):
            moment = evaluation.get("selection_moment") or evaluation.get("moment", {})
            top_changes.append(
                {
                    "candidate_rank": evaluation.get("candidate", {}).get("candidate_rank"),
                    "candidate_kind": evaluation.get("candidate", {}).get("candidate_kind", ""),
                    "start": moment.get("start"),
                    "end": moment.get("end"),
                    "base_score": scoring.get("base_score"),
                    "voice_confidence": scoring.get("voice_confidence"),
                    "voice_adjustment": scoring.get("voice_adjustment"),
                    "voice_profile_quality_score": scoring.get("voice_profile_quality_score"),
                    "baseline_rank": baseline_rank,
                    "voice_rank": voice_rank,
                    "rank_delta": rank_delta,
                    "selection_delta": selection_delta,
                    "voice_reason": scoring.get("voice_reason"),
                    "transcript_preview": _preview_text(moment.get("transcript", "")),
                }
            )

    top_changes.sort(
        key=lambda row: (
            row.get("selection_delta") in {"added_by_voice", "dropped_by_voice"},
            abs(int(row.get("rank_delta") or 0)),
            float(row.get("voice_profile_quality_score") or 0.0),
        ),
        reverse=True,
    )
    output_changed = baseline_selected_ids != selected_ids
    usable_voice_score_source = bool(prepared.get("ranking_enabled") and prepared.get("has_voice_profile_scores"))

    return {
        "schema_version": VOICE_PROFILE_SELECTION_SCHEMA_VERSION,
        "mode": "voice_profile_blend",
        "ranking_enabled": bool(prepared.get("ranking_enabled")),
        "selection_impact": "capped_rank_adjustment" if prepared.get("ranking_enabled") else "none",
        "output_changed": output_changed,
        "selection_score_source": voice_score_key if usable_voice_score_source else score_key,
        "base_score_source": score_key,
        "voice_profile_selection_max_adjustment": prepared.get("voice_profile_selection_max_adjustment", round(safe_max_adjustment, 4)),
        "has_voice_profile_scores": bool(prepared.get("has_voice_profile_scores")),
        "scored_candidate_count": int(prepared.get("scored_candidate_count") or 0),
        "accepted_candidate_count": int(prepared.get("accepted_candidate_count") or len(accepted)),
        "scored_candidate_ratio": prepared.get("scored_candidate_ratio", 0.0),
        "min_scored_candidate_count": prepared.get("min_scored_candidate_count", MIN_VOICE_RANKING_SCORED_CANDIDATES),
        "min_scored_candidate_ratio": prepared.get("min_scored_candidate_ratio", MIN_VOICE_RANKING_SCORED_RATIO),
        "disabled_reason": prepared.get("disabled_reason", ""),
        "candidate_count": len(evaluations),
        "accepted_count": len(accepted),
        "baseline_selected_count": len(baseline_selected),
        "selected_count": len(selected),
        "selection_delta_counts": selection_delta_counts,
        "status": {
            "enabled": bool(status.get("enabled")),
            "enrolled": bool(status.get("enrolled")),
            "ranking_enabled": bool(status.get("ranking_enabled")),
            "ranking_active": bool(status.get("ranking_active")),
            "sample_count": int(status.get("sample_count") or 0),
        },
        "baseline_selected": [_voice_selection_summary(e) for e in baseline_selected],
        "selected": [_voice_selection_summary(e) for e in selected],
        "top_changes": top_changes[:10],
    }


def build_ai_moment_ranking_report(
    evaluations: list[dict],
    baseline_selected: list[dict],
    selected: list[dict],
    *,
    enabled: bool = False,
    max_count: int = 0,
    min_gap: int = 12,
    score_key: str = "moment_category_quality_score",
    ai_score_key: str = "ai_moment_quality_score",
    max_adjustment: float = AI_MOMENT_SELECTION_MAX_ADJUSTMENT,
) -> dict:
    """Report actual Deep-only AI moment-ranking impact."""
    safe_max = max(0.0, _safe_float(max_adjustment, 0.0) or 0.0)
    if not all("ai_moment_scoring" in evaluation for evaluation in evaluations):
        prepared = apply_ai_moment_scoring(
            evaluations,
            enabled=enabled,
            score_key=score_key,
            max_adjustment=safe_max,
        )
    else:
        rows = [e.get("ai_moment_scoring") or {} for e in evaluations]
        ranking_enabled = any(bool(row.get("ranking_enabled")) for row in rows)
        scored_rows = [row for row in rows if abs(_safe_float(row.get("ai_adjustment"), 0.0) or 0.0) > 0]
        prepared = {
            "ranking_enabled": ranking_enabled,
            "selection_impact": "capped_rank_adjustment" if ranking_enabled else "none",
            "ai_moment_selection_max_adjustment": round(safe_max, 4),
            "has_ai_scores": bool(scored_rows),
            "eligible_candidate_count": sum(1 for row in rows if row.get("ai_scoring_eligible")),
            "scored_candidate_count": len(scored_rows),
        }

    accepted = [e for e in evaluations if e.get("accepted")]
    target_count = max(0, int(max_count or len(selected) or len(baseline_selected) or len(accepted)))
    baseline_order = sorted(
        accepted,
        key=lambda e: (
            _safe_float(e.get(score_key, e.get("quality_score", 0.0)), 0.0) or 0.0,
            _safe_float(e.get("quality_score", 0.0), 0.0) or 0.0,
        ),
        reverse=True,
    )
    ai_order = sorted(
        accepted,
        key=lambda e: (
            _safe_float(e.get(ai_score_key, e.get(score_key, e.get("quality_score", 0.0))), 0.0) or 0.0,
            _safe_float(e.get(score_key, e.get("quality_score", 0.0)), 0.0) or 0.0,
            _safe_float(e.get("quality_score", 0.0), 0.0) or 0.0,
        ),
        reverse=True,
    )
    baseline_rank_by_id = {id(e): idx for idx, e in enumerate(baseline_order, 1)}
    ai_rank_by_id = {id(e): idx for idx, e in enumerate(ai_order, 1)}
    baseline_selected = baseline_selected or _select_for_report(baseline_order, target_count, min_gap)
    selected = selected or baseline_selected
    baseline_selected_ids = {id(e) for e in baseline_selected}
    selected_ids = {id(e) for e in selected}

    selection_delta_counts: dict[str, int] = {}
    top_changes = []
    for evaluation in evaluations:
        scoring = evaluation.get("ai_moment_scoring") or {}
        baseline_rank = baseline_rank_by_id.get(id(evaluation))
        ai_rank = ai_rank_by_id.get(id(evaluation))
        rank_delta = None
        if baseline_rank is not None and ai_rank is not None:
            rank_delta = int(baseline_rank) - int(ai_rank)
        baseline = id(evaluation) in baseline_selected_ids
        chosen = id(evaluation) in selected_ids
        if baseline and chosen:
            selection_delta = "kept"
        elif baseline and not chosen:
            selection_delta = "dropped_by_ai"
        elif not baseline and chosen:
            selection_delta = "added_by_ai"
        elif rank_delta:
            selection_delta = "rank_changed"
        else:
            selection_delta = ""
        scoring.update(
            {
                "baseline_rank": baseline_rank,
                "ai_rank": ai_rank,
                "rank_delta": rank_delta,
                "selected_by_baseline": baseline,
                "selected_by_ai": chosen,
                "selection_delta": selection_delta,
            }
        )
        evaluation["ai_moment_scoring"] = scoring
        if selection_delta:
            selection_delta_counts[selection_delta] = selection_delta_counts.get(selection_delta, 0) + 1
        if evaluation.get("accepted") and (rank_delta or selection_delta in {"added_by_ai", "dropped_by_ai"}):
            moment = evaluation.get("selection_moment") or evaluation.get("moment", {})
            top_changes.append(
                {
                    "candidate_rank": evaluation.get("candidate", {}).get("candidate_rank"),
                    "candidate_kind": evaluation.get("candidate", {}).get("candidate_kind", ""),
                    "start": moment.get("start"),
                    "end": moment.get("end"),
                    "base_score": scoring.get("base_score"),
                    "ai_score": scoring.get("ai_score"),
                    "ai_confidence": scoring.get("ai_confidence"),
                    "ai_adjustment": scoring.get("ai_adjustment"),
                    "ai_moment_quality_score": scoring.get("ai_moment_quality_score"),
                    "ai_primary_category": scoring.get("ai_primary_category"),
                    "ai_ineligible_reason": scoring.get("ai_ineligible_reason"),
                    "baseline_rank": baseline_rank,
                    "ai_rank": ai_rank,
                    "rank_delta": rank_delta,
                    "selection_delta": selection_delta,
                    "transcript_preview": _preview_text(moment.get("transcript", "")),
                }
            )

    top_changes.sort(
        key=lambda row: (
            row.get("selection_delta") in {"added_by_ai", "dropped_by_ai"},
            abs(int(row.get("rank_delta") or 0)),
            float(row.get("ai_moment_quality_score") or 0.0),
        ),
        reverse=True,
    )
    output_changed = baseline_selected_ids != selected_ids
    usable_ai_score_source = bool(prepared.get("ranking_enabled") and prepared.get("has_ai_scores"))

    return {
        "schema_version": AI_MOMENT_SELECTION_SCHEMA_VERSION,
        "mode": "ai_moment_blend",
        "ranking_enabled": bool(prepared.get("ranking_enabled")),
        "selection_impact": "capped_rank_adjustment" if prepared.get("ranking_enabled") else "none",
        "output_changed": output_changed,
        "selection_score_source": ai_score_key if usable_ai_score_source else score_key,
        "base_score_source": score_key,
        "ai_moment_selection_max_adjustment": prepared.get(
            "ai_moment_selection_max_adjustment",
            round(safe_max, 4),
        ),
        "confidence_floor": prepared.get("confidence_floor"),
        "has_ai_scores": bool(prepared.get("has_ai_scores")),
        "eligible_candidate_count": int(prepared.get("eligible_candidate_count") or 0),
        "scored_candidate_count": int(prepared.get("scored_candidate_count") or 0),
        "candidate_count": len(evaluations),
        "accepted_count": len(accepted),
        "baseline_selected_count": len(baseline_selected),
        "selected_count": len(selected),
        "selection_delta_counts": selection_delta_counts,
        "baseline_selected": [_ai_moment_selection_summary(e) for e in baseline_selected],
        "selected": [_ai_moment_selection_summary(e) for e in selected],
        "top_changes": top_changes[:10],
    }


def build_moment_category_ranking_report(
    evaluations: list[dict],
    baseline_selected: list[dict],
    selected: list[dict],
    *,
    enabled: bool = False,
    max_count: int = 0,
    min_gap: int = 12,
    score_key: str = "learned_quality_score",
    category_score_key: str = "moment_category_quality_score",
    max_adjustment: float = MOMENT_CATEGORY_SELECTION_MAX_ADJUSTMENT,
) -> dict:
    """Report actual opt-in deterministic moment-category ranking impact."""
    safe_max = max(0.0, _safe_float(max_adjustment, 0.0) or 0.0)
    if not all("moment_category_scoring" in evaluation for evaluation in evaluations):
        prepared = apply_moment_category_scoring(
            evaluations,
            enabled=enabled,
            score_key=score_key,
            max_adjustment=safe_max,
        )
    else:
        rows = [e.get("moment_category_scoring") or {} for e in evaluations]
        ranking_enabled = any(bool(row.get("ranking_enabled")) for row in rows)
        scored_rows = [row for row in rows if abs(_safe_float(row.get("category_adjustment"), 0.0) or 0.0) > 0]
        prepared = {
            "ranking_enabled": ranking_enabled,
            "selection_impact": "capped_rank_adjustment" if ranking_enabled else "none",
            "moment_category_selection_max_adjustment": round(safe_max, 4),
            "moment_category_diversity_max_adjustment": round(MOMENT_CATEGORY_DIVERSITY_MAX_ADJUSTMENT, 4),
            "has_category_scores": bool(scored_rows),
            "scored_candidate_count": len(scored_rows),
            "baseline_category_counts": {
                str(row.get("primary_category") or ""): int(row.get("baseline_category_count") or 0)
                for row in rows
                if row.get("primary_category")
            },
            "diversity_candidate_count": sum(
                1
                for row in rows
                if abs(_safe_float(row.get("category_diversity_adjustment"), 0.0) or 0.0) > 0
            ),
        }

    accepted = [e for e in evaluations if e.get("accepted")]
    target_count = max(0, int(max_count or len(selected) or len(baseline_selected) or len(accepted)))
    baseline_order = sorted(
        accepted,
        key=lambda e: (
            _safe_float(e.get(score_key, e.get("quality_score", 0.0)), 0.0) or 0.0,
            _safe_float(e.get("quality_score", 0.0), 0.0) or 0.0,
        ),
        reverse=True,
    )
    category_order = sorted(
        accepted,
        key=lambda e: (
            _safe_float(e.get(category_score_key, e.get(score_key, e.get("quality_score", 0.0))), 0.0) or 0.0,
            _safe_float(e.get(score_key, e.get("quality_score", 0.0)), 0.0) or 0.0,
            _safe_float(e.get("quality_score", 0.0), 0.0) or 0.0,
        ),
        reverse=True,
    )
    baseline_rank_by_id = {id(e): idx for idx, e in enumerate(baseline_order, 1)}
    category_rank_by_id = {id(e): idx for idx, e in enumerate(category_order, 1)}
    baseline_selected = baseline_selected or _select_for_report(baseline_order, target_count, min_gap)
    selected = selected or baseline_selected
    baseline_selected_ids = {id(e) for e in baseline_selected}
    selected_ids = {id(e) for e in selected}

    selection_delta_counts: dict[str, int] = {}
    top_changes = []
    for evaluation in evaluations:
        scoring = evaluation.get("moment_category_scoring") or {}
        baseline_rank = baseline_rank_by_id.get(id(evaluation))
        category_rank = category_rank_by_id.get(id(evaluation))
        rank_delta = None
        if baseline_rank is not None and category_rank is not None:
            rank_delta = int(baseline_rank) - int(category_rank)
        baseline = id(evaluation) in baseline_selected_ids
        chosen = id(evaluation) in selected_ids
        if baseline and chosen:
            selection_delta = "kept"
        elif baseline and not chosen:
            selection_delta = "dropped_by_category"
        elif not baseline and chosen:
            selection_delta = "added_by_category"
        elif rank_delta:
            selection_delta = "rank_changed"
        else:
            selection_delta = ""
        scoring.update(
            {
                "baseline_rank": baseline_rank,
                "category_rank": category_rank,
                "rank_delta": rank_delta,
                "selected_by_baseline": baseline,
                "selected_by_category": chosen,
                "selection_delta": selection_delta,
            }
        )
        evaluation["moment_category_scoring"] = scoring
        if selection_delta:
            selection_delta_counts[selection_delta] = selection_delta_counts.get(selection_delta, 0) + 1
        if evaluation.get("accepted") and (rank_delta or selection_delta in {"added_by_category", "dropped_by_category"}):
            moment = evaluation.get("selection_moment") or evaluation.get("moment", {})
            top_changes.append(
                {
                    "candidate_rank": evaluation.get("candidate", {}).get("candidate_rank"),
                    "candidate_kind": evaluation.get("candidate", {}).get("candidate_kind", ""),
                    "start": moment.get("start"),
                    "end": moment.get("end"),
                    "base_score": scoring.get("base_score"),
                    "category_adjustment": scoring.get("category_adjustment"),
                    "category_diversity_adjustment": scoring.get("category_diversity_adjustment"),
                    "moment_category_quality_score": scoring.get("moment_category_quality_score"),
                    "primary_category": scoring.get("primary_category"),
                    "category_confidence": scoring.get("category_confidence"),
                    "baseline_rank": baseline_rank,
                    "category_rank": category_rank,
                    "rank_delta": rank_delta,
                    "selection_delta": selection_delta,
                    "transcript_preview": _preview_text(moment.get("transcript", "")),
                }
            )

    top_changes.sort(
        key=lambda row: (
            row.get("selection_delta") in {"added_by_category", "dropped_by_category"},
            abs(int(row.get("rank_delta") or 0)),
            float(row.get("moment_category_quality_score") or 0.0),
        ),
        reverse=True,
    )
    output_changed = baseline_selected_ids != selected_ids
    usable_category_score_source = bool(prepared.get("ranking_enabled") and prepared.get("has_category_scores"))

    return {
        "schema_version": MOMENT_CATEGORY_SELECTION_SCHEMA_VERSION,
        "mode": "moment_category_blend",
        "ranking_enabled": bool(prepared.get("ranking_enabled")),
        "selection_impact": "capped_rank_adjustment" if prepared.get("ranking_enabled") else "none",
        "output_changed": output_changed,
        "selection_score_source": category_score_key if usable_category_score_source else score_key,
        "base_score_source": score_key,
        "moment_category_selection_max_adjustment": prepared.get(
            "moment_category_selection_max_adjustment",
            round(safe_max, 4),
        ),
        "moment_category_diversity_max_adjustment": prepared.get(
            "moment_category_diversity_max_adjustment",
            round(MOMENT_CATEGORY_DIVERSITY_MAX_ADJUSTMENT, 4),
        ),
        "has_category_scores": bool(prepared.get("has_category_scores")),
        "scored_candidate_count": int(prepared.get("scored_candidate_count") or 0),
        "diversity_candidate_count": int(prepared.get("diversity_candidate_count") or 0),
        "baseline_category_counts": prepared.get("baseline_category_counts") or {},
        "candidate_count": len(evaluations),
        "accepted_count": len(accepted),
        "baseline_selected_count": len(baseline_selected),
        "selected_count": len(selected),
        "selection_delta_counts": selection_delta_counts,
        "baseline_selected": [_moment_category_selection_summary(e) for e in baseline_selected],
        "selected": [_moment_category_selection_summary(e) for e in selected],
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
    voice_profile_shadow: dict | None = None,
    voice_profile_ranking: dict | None = None,
    moment_category_ranking: dict | None = None,
    ai_moment_ranking: dict | None = None,
    visual_diagnostics: dict | None = None,
    ai_moment_classification: dict | None = None,
    ai_moment_classification_shadow: dict | None = None,
    timing: dict | None = None,
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
        voice_shadow = evaluation.get("voice_profile_shadow", {})
        voice_scoring = evaluation.get("voice_scoring", {})
        category_scoring = evaluation.get("moment_category_scoring", {})
        ai_scoring = evaluation.get("ai_moment_scoring", {})
        music_lyrics_guard = (
            evaluation.get("music_lyrics_guard")
            or selection_moment.get("music_lyrics_guard")
            or {}
        )
        ai_classification = (
            evaluation.get("ai_moment_classification")
            or selection_moment.get("ai_moment_classification")
            or candidate.get("ai_moment_classification")
            or {}
        )
        candidate_visual = (
            evaluation.get("visual_diagnostics")
            or selection_moment.get("visual_diagnostics")
            or candidate.get("visual_diagnostics")
            or {}
        )
        base_quality = round(_safe_float(evaluation.get("quality_score"), 0.0) or 0.0, 4)
        selection_quality = round(
            _safe_float(evaluation.get("selection_quality_score"), base_quality) or base_quality,
            4,
        )
        learned_score = shadow.get("learned_quality_score", evaluation.get("learned_quality_score"))
        selection_moment_categories = selection_moment.get("moment_categories", evaluation.get("moment_categories"))
        selection_primary_category = selection_moment.get("primary_category")
        ranking_moment_categories = selection_moment_categories
        ranking_primary_category = category_scoring.get("primary_category") or selection_primary_category
        final_moment_categories = final_moment.get("moment_categories") if isinstance(final_moment, dict) else None
        final_primary_category = final_moment.get("primary_category") if isinstance(final_moment, dict) else None
        rows.append(
            {
                "selected": id(evaluation) in selected_ids,
                "accepted": evaluation.get("accepted", False),
                "reject_reason": evaluation.get("reject_reason") or selection_moment["ranker"].get("reject_reason", ""),
                "start": selection_moment["start"],
                "end": selection_moment["end"],
                "base_quality_score": base_quality,
                "quality_score": base_quality,
                "quality_floor": evaluation.get("quality_floor"),
                "detection_preference": evaluation.get("detection_preference"),
                "selection_quality_score": selection_quality,
                "selection_rank_score": evaluation.get("selection_rank_score"),
                "selection_score_source": evaluation.get("selection_score_source", "quality_score"),
                "learned_adjustment": shadow.get("learned_adjustment"),
                "learned_score": learned_score,
                "learned_quality_score": learned_score,
                "moment_category_quality_score": category_scoring.get("moment_category_quality_score"),
                "moment_category_ranking_enabled": category_scoring.get("ranking_enabled"),
                "moment_category_adjustment": category_scoring.get("category_adjustment"),
                "moment_category_diversity_adjustment": category_scoring.get("category_diversity_adjustment"),
                "moment_category_selection_delta": category_scoring.get("selection_delta", ""),
                "moment_category_rank_delta": category_scoring.get("rank_delta"),
                "voice_profile_quality_score": voice_scoring.get("voice_profile_quality_score"),
                "voice_ranking_enabled": voice_scoring.get("ranking_enabled"),
                "voice_ranking_adjustment": voice_scoring.get("voice_adjustment"),
                "voice_ranking_selection_delta": voice_scoring.get("selection_delta", ""),
                "voice_ranking_rank_delta": voice_scoring.get("rank_delta"),
                "ai_moment_quality_score": ai_scoring.get("ai_moment_quality_score"),
                "ai_ranking_enabled": ai_scoring.get("ranking_enabled"),
                "ai_adjustment": ai_scoring.get("ai_adjustment"),
                "ai_selection_delta": ai_scoring.get("selection_delta", ""),
                "ai_rank_delta": ai_scoring.get("rank_delta"),
                "ai_scoring_eligible": ai_scoring.get("ai_scoring_eligible"),
                "ai_ineligible_reason": ai_scoring.get("ai_ineligible_reason"),
                "rank_delta": shadow.get("rank_delta"),
                "selection_delta": shadow.get("selection_delta", ""),
                "voice_adjustment": voice_shadow.get("voice_adjustment"),
                "voice_confidence": voice_shadow.get("voice_confidence"),
                "voice_reason": voice_shadow.get("voice_reason"),
                "voice_score_source": voice_shadow.get("score_source"),
                "voice_max_adjustment": voice_shadow.get("max_adjustment"),
                "voice_shadow_score": voice_shadow.get("voice_shadow_score"),
                "voice_current_rank": voice_shadow.get("current_rank"),
                "voice_shadow_rank": voice_shadow.get("shadow_rank"),
                "voice_rank_delta": voice_shadow.get("rank_delta"),
                "voice_selection_delta": voice_shadow.get("selection_delta", ""),
                "voice_selected_by_current": voice_shadow.get("selected_by_current"),
                "voice_would_select": voice_shadow.get("would_select"),
                "selection_moment_categories": selection_moment_categories,
                "selection_primary_category": selection_primary_category,
                "ranking_primary_category": ranking_primary_category,
                "ranking_moment_categories": ranking_moment_categories,
                "final_moment_categories": final_moment_categories,
                "final_primary_category": final_primary_category,
                "moment_categories": selection_moment_categories,
                "primary_category": selection_primary_category,
                "ai_moment_classification": ai_classification,
                "visual_diagnostics": candidate_visual,
                "commentary_guard": evaluation.get("commentary_guard") or selection_moment.get("commentary_guard"),
                "commentary_guard_selection": evaluation.get("commentary_guard_selection")
                or (selection_moment.get("commentary_guard") or {}).get("selection"),
                "commentary_guard_selection_penalty": evaluation.get(
                    "commentary_guard_selection_penalty",
                    (selection_moment.get("commentary_guard") or {}).get("selection_penalty"),
                ),
                "music_lyrics_guard": music_lyrics_guard,
                "music_lyrics_penalty": evaluation.get(
                    "music_lyrics_penalty",
                    selection_moment.get("music_lyrics_penalty"),
                ),
                "stream_retry": evaluation.get("stream_retry") or selection_moment.get("stream_retry"),
                "voice_profile": evaluation.get("voice_profile") or selection_moment.get("voice_profile"),
                "selection": _moment_summary(selection_moment, selection_quality),
                "final": _moment_summary(final_moment) if final_moment else None,
                "word_count": evaluation.get("word_count", 0),
                "transcript": selection_moment.get("transcript", ""),
                "candidate": candidate,
                "ranker": selection_moment.get("ranker", {}),
                "shadow_scoring": shadow,
                "moment_category_scoring": category_scoring,
                "ai_moment_scoring": ai_scoring,
                "voice_scoring": voice_scoring,
                "voice_profile_shadow": voice_shadow,
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
        "voice_profile_shadow": voice_profile_shadow or {},
        "voice_profile_ranking": voice_profile_ranking or {},
        "moment_category_ranking": moment_category_ranking or {},
        "ai_moment_ranking": ai_moment_ranking or {},
        "visual_diagnostics": visual_diagnostics or {},
        "ai_moment_classification": ai_moment_classification or {},
        "ai_moment_classification_shadow": ai_moment_classification_shadow or {},
        "timing": timing or {},
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
    for entry in clips.values():
        if not isinstance(entry, dict):
            continue
        latest = entry.get("latest")
        if isinstance(latest, dict) and any(key in latest for key in ("like", "dislike", "favorite")):
            return True
    return False


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
            "clip_snapshot": entry.get("clip_snapshot") if isinstance(entry.get("clip_snapshot"), dict) else {},
            "learning_terms": _learning_terms_from_feedback_container(entry),
        }
        if latest.get("like"):
            signals.append({**base, "event_type": "like", "reason": _feedback_reason_for(latest, "like")})
        if latest.get("dislike"):
            signals.append({**base, "event_type": "dislike", "reason": _feedback_reason_for(latest, "dislike")})
        if latest.get("favorite"):
            signals.append({**base, "event_type": "favorite", "reason": _feedback_reason_for(latest, "favorite")})
    return signals


def _feedback_reason_for(latest: dict, event_type: str) -> str:
    if not isinstance(latest, dict):
        return ""
    event_type = str(event_type or "").strip().lower()
    reasons = latest.get("reasons")
    if isinstance(reasons, dict):
        reason = str(reasons.get(event_type) or "").strip()
        if reason:
            return reason
        if reasons:
            return ""
    latest_type = str(latest.get("event_type") or "").strip().lower()
    if latest_type and latest_type != event_type:
        return ""
    return str(latest.get("reason") or "").strip()


def _feedback_reasons_from_container(container: dict) -> dict[str, str]:
    if not isinstance(container, dict):
        return {}
    values = container.get("reasons")
    if not isinstance(values, dict):
        return {}
    clean: dict[str, str] = {}
    for key, value in values.items():
        event_type = str(key or "").strip().lower()
        reason = str(value or "").strip()
        if event_type in {"like", "dislike", "favorite"} and reason:
            clean[event_type] = reason
    return clean


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
                "reasons": {},
                "clip_snapshot": {},
                "learning_terms": [],
                "like": False,
                "dislike": False,
                "favorite": False,
            },
        )
        current["source_id"] = event.get("source_id", current.get("source_id", ""))
        current["source_stem"] = event.get("source_stem", current.get("source_stem", ""))
        event_type = str(event.get("event_type") or "").lower()
        event_reason = str(event.get("reason") or "").strip()
        current_reasons = current.setdefault("reasons", {})
        if not isinstance(current_reasons, dict):
            current_reasons = {}
            current["reasons"] = current_reasons
        for key, value in _feedback_reasons_from_container(event).items():
            current_reasons[key] = value
        if event_type in {"like", "dislike", "favorite"}:
            active = bool(event.get("active", True))
            if active and event_reason:
                current_reasons[event_type] = event_reason
            elif not active:
                current_reasons.pop(event_type, None)
        current["reason"] = event_reason or current.get("reason", "")
        if isinstance(event.get("clip_snapshot"), dict):
            current["clip_snapshot"] = event["clip_snapshot"]
        event_terms = _learning_terms_from_feedback_container(event)
        if event_terms:
            current["learning_terms"] = event_terms
        if {"like", "dislike", "favorite"}.issubset(event.keys()):
            current["like"] = bool(event.get("like"))
            current["dislike"] = bool(event.get("dislike"))
            current["favorite"] = bool(event.get("favorite"))
            if not current["like"]:
                current_reasons.pop("like", None)
            if not current["dislike"]:
                current_reasons.pop("dislike", None)
            if not current["favorite"]:
                current_reasons.pop("favorite", None)
            continue

        active = bool(event.get("active", True))
        if event_type == "like":
            current["like"] = active
            if active:
                current["dislike"] = False
                current_reasons.pop("dislike", None)
        elif event_type == "dislike":
            current["dislike"] = active
            if active:
                current["like"] = False
                current_reasons.pop("like", None)
        elif event_type == "favorite":
            current["favorite"] = active
        if not current["like"]:
            current_reasons.pop("like", None)
        if not current["dislike"]:
            current_reasons.pop("dislike", None)
        if not current["favorite"]:
            current_reasons.pop("favorite", None)

    return _signals_from_clip_summaries(
        {
            clip_id: {
                "clip_id": row.get("clip_id", clip_id),
                "source_id": row.get("source_id", ""),
                "source_stem": row.get("source_stem", ""),
                "clip_snapshot": row.get("clip_snapshot", {}),
                "learning_terms": row.get("learning_terms", []),
                "latest": {
                    "like": row.get("like", False),
                    "dislike": row.get("dislike", False),
                    "favorite": row.get("favorite", False),
                    "reason": row.get("reason", ""),
                    "reasons": row.get("reasons", {}),
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
        text_parts.append(_category_signal_text(snapshot.get("moment_categories")))
        text_parts.append(str(snapshot.get("primary_category") or ""))
    learning_terms = _learning_terms_from_feedback_container(signal)
    if learning_terms:
        text_parts.append(" ".join(learning_terms))
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


def _learning_terms_from_feedback_container(container: dict) -> list[str]:
    if not isinstance(container, dict):
        return []
    candidates = [
        container.get("learning_terms"),
        (container.get("learning_snapshot") or {}).get("learning_terms")
        if isinstance(container.get("learning_snapshot"), dict)
        else None,
    ]
    snapshot = container.get("clip_snapshot")
    if isinstance(snapshot, dict):
        candidates.extend(
            [
                snapshot.get("learning_terms"),
                (snapshot.get("learning_snapshot") or {}).get("learning_terms")
                if isinstance(snapshot.get("learning_snapshot"), dict)
                else None,
            ]
        )
    terms: list[str] = []
    seen: set[str] = set()
    for values in candidates:
        if not isinstance(values, list):
            continue
        for value in values:
            term = _normal_text(str(value or ""))
            if term and term not in seen:
                seen.add(term)
                terms.append(term)
            if len(terms) >= SHADOW_MAX_TERMS_PER_EVENT:
                return terms
    return terms


def _score_shadow_candidate(evaluation: dict, profile: dict, source_id: str, source_stem: str) -> dict:
    base_score = _safe_float(evaluation.get("quality_score"), 0.0) or 0.0
    moment = evaluation.get("selection_moment") or evaluation.get("moment") or {}
    transcript = " ".join(
        part for part in [
            str(evaluation.get("transcript") or ""),
            str(moment.get("transcript") or ""),
            _category_signal_text(moment.get("moment_categories")),
            str(moment.get("primary_category") or ""),
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
    category_phrases = tuple(
        item
        for phrases in CATEGORY_PHRASES.values()
        for item in phrases
    )
    for phrase, _ in HOOK_WEIGHTS + WEAK_WEIGHTS + AFTERMATH_WEIGHTS + category_phrases:
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
        "quality_score": round(_safe_float(evaluation.get("quality_score"), 0.0) or 0.0, 4),
        "shadow_score": shadow.get("shadow_score"),
        "learned_score": shadow.get("learned_quality_score"),
        "learned_quality_score": shadow.get("learned_quality_score"),
        "learned_adjustment": shadow.get("learned_adjustment"),
        "baseline_rank": shadow.get("baseline_rank"),
        "shadow_rank": shadow.get("learned_rank", shadow.get("shadow_rank")),
        "rank_delta": shadow.get("rank_delta"),
        "selection_delta": shadow.get("selection_delta", ""),
        "moment_categories": moment.get("moment_categories"),
        "primary_category": moment.get("primary_category"),
        "transcript_preview": _preview_text(moment.get("transcript", "")),
    }


def _voice_selection_summary(evaluation: dict) -> dict:
    moment = evaluation.get("selection_moment") or evaluation.get("moment", {})
    voice = evaluation.get("voice_scoring", {})
    return {
        "candidate_rank": evaluation.get("candidate", {}).get("candidate_rank"),
        "candidate_kind": evaluation.get("candidate", {}).get("candidate_kind", ""),
        "start": moment.get("start"),
        "end": moment.get("end"),
        "base_score": voice.get("base_score"),
        "voice_confidence": voice.get("voice_confidence"),
        "voice_adjustment": voice.get("voice_adjustment"),
        "voice_profile_quality_score": voice.get("voice_profile_quality_score"),
        "baseline_rank": voice.get("baseline_rank"),
        "voice_rank": voice.get("voice_rank"),
        "rank_delta": voice.get("rank_delta"),
        "selection_delta": voice.get("selection_delta", ""),
        "primary_category": moment.get("primary_category"),
        "transcript_preview": _preview_text(moment.get("transcript", "")),
    }


def _moment_category_selection_summary(evaluation: dict) -> dict:
    moment = evaluation.get("selection_moment") or evaluation.get("moment", {})
    scoring = evaluation.get("moment_category_scoring", {})
    return {
        "candidate_rank": evaluation.get("candidate", {}).get("candidate_rank"),
        "candidate_kind": evaluation.get("candidate", {}).get("candidate_kind", ""),
        "start": moment.get("start"),
        "end": moment.get("end"),
        "base_score": scoring.get("base_score"),
        "category_adjustment": scoring.get("category_adjustment"),
        "category_diversity_adjustment": scoring.get("category_diversity_adjustment"),
        "moment_category_quality_score": scoring.get("moment_category_quality_score"),
        "baseline_rank": scoring.get("baseline_rank"),
        "category_rank": scoring.get("category_rank"),
        "rank_delta": scoring.get("rank_delta"),
        "selection_delta": scoring.get("selection_delta", ""),
        "primary_category": scoring.get("primary_category") or moment.get("primary_category"),
        "category_confidence": scoring.get("category_confidence"),
        "transcript_preview": _preview_text(moment.get("transcript", "")),
    }


def _ai_moment_selection_summary(evaluation: dict) -> dict:
    moment = evaluation.get("selection_moment") or evaluation.get("moment", {})
    scoring = evaluation.get("ai_moment_scoring", {})
    return {
        "candidate_rank": evaluation.get("candidate", {}).get("candidate_rank"),
        "candidate_kind": evaluation.get("candidate", {}).get("candidate_kind", ""),
        "start": moment.get("start"),
        "end": moment.get("end"),
        "base_score": scoring.get("base_score"),
        "ai_score": scoring.get("ai_score"),
        "ai_confidence": scoring.get("ai_confidence"),
        "ai_adjustment": scoring.get("ai_adjustment"),
        "ai_moment_quality_score": scoring.get("ai_moment_quality_score"),
        "baseline_rank": scoring.get("baseline_rank"),
        "ai_rank": scoring.get("ai_rank"),
        "rank_delta": scoring.get("rank_delta"),
        "selection_delta": scoring.get("selection_delta", ""),
        "primary_category": scoring.get("ai_primary_category") or moment.get("primary_category"),
        "ai_ineligible_reason": scoring.get("ai_ineligible_reason"),
        "transcript_preview": _preview_text(moment.get("transcript", "")),
    }


def _preview_text(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _score01(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _category_phrase_score(normal_text: str, phrases: tuple[tuple[str, float], ...]) -> float:
    if not normal_text:
        return 0.0
    return _score01(_weighted_score(normal_text, phrases) / 8.0)


def _category_signal_text(categories) -> str:
    if not isinstance(categories, dict):
        return ""
    labels: list[str] = []
    primary = str(categories.get("primary") or "").strip()
    if primary:
        labels.append(primary.replace("_", " "))
    scores = categories.get("scores") if isinstance(categories.get("scores"), dict) else {}
    for key, value in scores.items():
        try:
            score = float(value)
        except (TypeError, ValueError):
            continue
        if score >= 0.45:
            labels.append(str(key).replace("_", " "))
    return " ".join(labels)


def build_learning_terms(
    text: str = "",
    *,
    categories: dict | None = None,
    primary_category: str | None = "",
    reason: str | None = "",
    limit: int = SHADOW_MAX_TERMS_PER_EVENT,
) -> list[str]:
    """Build compact local feedback terms without storing media files."""
    parts = [
        str(reason or ""),
        str(text or ""),
        _category_signal_text(categories),
        str(primary_category or ""),
    ]
    return _extract_shadow_terms(" ".join(part for part in parts if part), limit=limit)


def _categories_for_scoring(evaluation: dict) -> dict:
    if isinstance(evaluation.get("moment_categories"), dict):
        return copy.deepcopy(evaluation["moment_categories"])
    moment = evaluation.get("moment") if isinstance(evaluation.get("moment"), dict) else {}
    if isinstance(moment.get("moment_categories"), dict):
        return copy.deepcopy(moment["moment_categories"])
    ranker = moment.get("ranker") if isinstance(moment.get("ranker"), dict) else {}
    if isinstance(ranker.get("moment_categories"), dict):
        return copy.deepcopy(ranker["moment_categories"])
    return {}


def _moment_category_signal(categories: dict) -> float:
    if not isinstance(categories, dict):
        return 0.0
    confidence = _safe_float(categories.get("confidence"), 0.0) or 0.0
    if confidence < 0.30:
        return 0.0
    primary = str(categories.get("primary") or "").strip()
    if primary in {"high_energy", "death_or_failure"}:
        return 1.0
    if primary == "tutorial_or_explainer":
        return 0.72
    if primary == "lore_or_story":
        return 0.60
    if primary == "atmosphere_or_visual":
        return 0.45
    if primary == "low_value":
        return -1.0
    return 0.0


def _moment_category_diversity_adjustment(
    categories: dict,
    baseline_category_counts: dict[str, int],
    *,
    max_adjustment: float,
) -> float:
    if not isinstance(categories, dict) or max_adjustment <= 0:
        return 0.0
    primary = str(categories.get("primary") or "").strip()
    if primary in {"", "low_value", "unknown"}:
        return 0.0
    confidence = _safe_float(categories.get("confidence"), 0.0) or 0.0
    if confidence < 0.45:
        return 0.0
    existing = int(baseline_category_counts.get(primary, 0) or 0)
    if existing <= 0 and primary in {"tutorial_or_explainer", "lore_or_story", "atmosphere_or_visual", "death_or_failure"}:
        return max_adjustment
    if existing >= 2 and primary == "high_energy":
        return -max_adjustment * 0.5
    return 0.0


def _ai_classification_for_scoring(evaluation: dict) -> dict:
    if isinstance(evaluation.get("ai_moment_classification"), dict):
        return copy.deepcopy(evaluation["ai_moment_classification"])
    moment = evaluation.get("moment") if isinstance(evaluation.get("moment"), dict) else {}
    if isinstance(moment.get("ai_moment_classification"), dict):
        return copy.deepcopy(moment["ai_moment_classification"])
    categories = moment.get("moment_categories") if isinstance(moment.get("moment_categories"), dict) else {}
    if isinstance(categories.get("ai"), dict):
        return copy.deepcopy(categories["ai"])
    return {}


def _ai_moment_scoring_eligibility(ai: dict, evaluation: dict, *, confidence_floor: float) -> dict:
    if not isinstance(ai, dict) or not ai:
        return {"eligible": False, "reason": "missing_ai_classification"}
    if not evaluation.get("accepted"):
        return {"eligible": False, "reason": "candidate_not_accepted"}
    if str(ai.get("status") or "") != "ok":
        return {"eligible": False, "reason": "ai_status_not_ok"}
    if str(ai.get("provider") or "") != "ollama":
        return {"eligible": False, "reason": "not_ollama"}
    if ai.get("fallback_used"):
        return {"eligible": False, "reason": "fallback_label"}
    if ai.get("invalid_primary_category"):
        return {"eligible": False, "reason": "invalid_primary_category"}
    confidence = _safe_float(ai.get("ai_confidence"), _safe_float(ai.get("confidence"), None))
    if confidence is None or confidence < confidence_floor:
        return {"eligible": False, "reason": "low_ai_confidence"}
    dimensions = ai.get("ai_dimensions") if isinstance(ai.get("ai_dimensions"), dict) else {}
    if not any((_safe_float(dimensions.get(key), 0.0) or 0.0) >= 0.45 for key in ("hook", "flow", "value", "platform_fit", "game_context")):
        return {"eligible": False, "reason": "weak_ai_dimensions"}
    primary = str(ai.get("primary_category") or "").strip()
    if primary in {"unknown", ""}:
        return {"eligible": False, "reason": "unknown_primary_category"}
    music_guard = evaluation.get("music_lyrics_guard") if isinstance(evaluation.get("music_lyrics_guard"), dict) else {}
    if music_guard.get("reject_candidate"):
        return {"eligible": False, "reason": "music_guard_rejected"}
    return {"eligible": True, "reason": "eligible"}


def _ai_moment_signal(ai: dict) -> float:
    score = _safe_float(ai.get("ai_viral_score"), 50.0)
    if score is None:
        score = 50.0
    confidence = _safe_float(ai.get("ai_confidence"), _safe_float(ai.get("confidence"), 0.0)) or 0.0
    primary = str(ai.get("primary_category") or "").strip()
    dimensions = ai.get("ai_dimensions") if isinstance(ai.get("ai_dimensions"), dict) else {}
    dimension_values = [
        _safe_float(dimensions.get(key), 0.0) or 0.0
        for key in ("hook", "flow", "value", "platform_fit", "game_context")
    ]
    dimension_mean = sum(dimension_values) / max(1, len(dimension_values))
    score_signal = max(-1.0, min(1.0, (score - 50.0) / 49.0))
    dimension_signal = max(-1.0, min(1.0, (dimension_mean - 0.50) * 2.0))
    category_bonus = 0.0
    if primary in {"high_energy", "death_or_failure"}:
        category_bonus = 0.15
    elif primary in {"tutorial_or_explainer", "lore_or_story", "atmosphere_or_visual"}:
        category_bonus = 0.08
    elif primary == "low_value":
        category_bonus = -0.35
    signal = (score_signal * 0.68 + dimension_signal * 0.22 + category_bonus) * confidence
    if primary == "low_value":
        signal = min(signal, -0.25 * confidence)
    return max(-1.0, min(1.0, signal))


def _compact_ai_moment_classification(classification) -> dict:
    if not isinstance(classification, dict):
        return {}
    allowed_categories = set(CATEGORY_KEYS)
    primary = str(classification.get("primary_category") or "").strip()
    invalid_primary_category = False
    if primary not in allowed_categories:
        primary = "unknown"
        invalid_primary_category = True
    fine_labels = classification.get("fine_labels")
    if not isinstance(fine_labels, list):
        fine_labels = []
    clean_labels = []
    seen = set()
    for label in fine_labels:
        value = str(label or "").strip().lower().replace("-", "_").replace(" ", "_")
        if not value or value in seen:
            continue
        clean_labels.append(value[:48])
        seen.add(value)
        if len(clean_labels) >= 5:
            break
    confidence = _safe_float(classification.get("confidence"), None)
    ai_confidence = _safe_float(classification.get("ai_confidence"), confidence)
    ai_viral_score = _safe_float(classification.get("ai_viral_score"), None)
    if ai_viral_score is None:
        ai_viral_score = 0.0
    ai_dimensions = classification.get("ai_dimensions")
    if not isinstance(ai_dimensions, dict):
        ai_dimensions = {}
    cleaned_dimensions = {}
    for key in ("hook", "flow", "value", "platform_fit", "game_context"):
        value = _safe_float(ai_dimensions.get(key), 0.0) or 0.0
        cleaned_dimensions[key] = round(max(0.0, min(1.0, value)), 4)
    ai_viral_reason = re.sub(
        r"\s+",
        " ",
        str(classification.get("ai_viral_reason") or classification.get("reason") or ""),
    ).strip()
    reason = re.sub(r"\s+", " ", str(classification.get("reason") or "")).strip()
    return {
        "schema_version": int(_safe_float(classification.get("schema_version"), 1) or 1),
        "enabled": bool(classification.get("enabled", True)),
        "status": str(classification.get("status") or "unknown")[:48],
        "provider": str(classification.get("provider") or "unknown")[:48],
        "model": str(classification.get("model") or "")[:96],
        "primary_category": primary,
        "fine_labels": clean_labels,
        "confidence": round(max(0.0, min(1.0, confidence)), 4) if confidence is not None else None,
        "reason": reason[:180],
        "fallback_used": bool(classification.get("fallback_used")),
        "fallback_primary_category": classification.get("fallback_primary_category"),
        "invalid_primary_category": invalid_primary_category,
        "ai_viral_score": int(max(0, min(99, round(ai_viral_score)))),
        "ai_viral_reason": ai_viral_reason[:180],
        "ai_dimensions": cleaned_dimensions,
        "ai_confidence": round(max(0.0, min(1.0, ai_confidence)), 4) if ai_confidence is not None else None,
        "ai_adjustment": 0.0,
        "ai_rank_delta": None,
        "ai_scoring_eligible": bool(classification.get("ai_scoring_eligible", False)),
        "selection_impact": "none",
        "output_changed": False,
    }


def compact_ai_moment_classification(classification) -> dict:
    """Sanitize AI moment metadata for diagnostic reports without mutating candidates."""
    return _compact_ai_moment_classification(classification)


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
        "quality_floor": moment.get("quality_floor"),
        "detection_preference": moment.get("detection_preference"),
        "selection_quality_score": moment.get("selection_quality_score"),
        "selection_rank_score": moment.get("selection_rank_score"),
        "quality_rank": moment.get("quality_rank"),
        "learned_adjustment": moment.get("learned_adjustment"),
        "learned_score": moment.get("learned_quality_score"),
        "learned_quality_score": moment.get("learned_quality_score"),
        "voice_adjustment": moment.get("voice_adjustment"),
        "voice_profile_quality_score": moment.get("voice_profile_quality_score"),
        "voice_scoring": moment.get("voice_scoring"),
        "selection_score_source": moment.get("selection_score_source"),
        "word_count": moment.get("word_count"),
        "analysis_word_count": moment.get("analysis_word_count"),
        "subtitle_word_count": moment.get("subtitle_word_count"),
        "speech_stream": moment.get("speech_stream"),
        "subtitle_generated": moment.get("subtitle_generated"),
        "subtitles_burned": moment.get("subtitles_burned"),
        "subtitle_placement": moment.get("subtitle_placement"),
        "moment_categories": moment.get("moment_categories"),
        "primary_category": moment.get("primary_category"),
        "ai_moment_classification": moment.get("ai_moment_classification"),
        "visual_diagnostics": moment.get("visual_diagnostics"),
        "commentary_guard": moment.get("commentary_guard"),
        "commentary_guard_selection": (moment.get("commentary_guard") or {}).get("selection"),
        "commentary_guard_selection_penalty": (moment.get("commentary_guard") or {}).get("selection_penalty"),
        "music_lyrics_guard": moment.get("music_lyrics_guard"),
        "music_lyrics_penalty": moment.get("music_lyrics_penalty"),
        "voice_profile": moment.get("voice_profile"),
        "transcript": moment.get("transcript", ""),
        "ranker": moment.get("ranker", {}),
    }


def _normal_text(text: str) -> str:
    text = text.lower().replace("'", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _safe_float(value, default=None):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return parsed


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
