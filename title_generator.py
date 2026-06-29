"""Lightweight title generator using a local Ollama model.

Falls back to a simple extraction heuristic if Ollama is unavailable,
so this never blocks the pipeline.
"""

import json
import re
import urllib.request
import urllib.error

from game_context import compact_game_context_for_prompt

# Default model for local AI titles, descriptions, and moment labels.
DEFAULT_MODEL = "qwen3.5:4b"
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_DOWNLOAD_URL = "https://ollama.com/download/windows"
OLLAMA_WINDOWS_DOCS_URL = "https://docs.ollama.com/windows"
TIMEOUT = 90  # seconds per title request; cold local models can take 30s+ to load
YOUTUBE_TAG_LIMIT = 500
YOUTUBE_TAG_TARGET = YOUTUBE_TAG_LIMIT - 100
DEFAULT_VIDEO_CATEGORY_ID = "20"  # Gaming
TITLE_CONTEXT_SCHEMA_VERSION = 1
AI_MOMENT_CLASSIFICATION_SCHEMA_VERSION = 2
AI_CLASSIFICATION_TIMEOUT = 12

AI_MOMENT_CATEGORIES = (
    "high_energy",
    "death_or_failure",
    "tutorial_or_explainer",
    "commentary_or_review",
    "lore_or_story",
    "cinematic_dialogue",
    "atmosphere_or_visual",
    "low_value",
)

AI_FINE_LABELS = (
    "chase_panic",
    "combat_action",
    "funny_failure",
    "death_scene",
    "possible_failure",
    "tutorial_tip",
    "lore_story",
    "cinematic_dialogue",
    "npc_dialogue",
    "scenic_atmosphere",
    "creator_reaction",
    "game_narration",
    "navigation_setup",
)

AI_VIRAL_DIMENSIONS = (
    "hook",
    "flow",
    "value",
    "platform_fit",
    "game_context",
)


MOMENT_SIGNAL_RULES = (
    {
        "type": "chase/panic",
        "phrases": (
            "right behind me", "behind me", "run", "hide", "chase", "please",
            "oh my god", "wait", "what", "scary",
        ),
        "tags": ("chase gameplay", "panic moment", "running scared", "close call"),
    },
    {
        "type": "combat/fight",
        "phrases": (
            "kill", "hit", "boss", "fight", "weapon", "shot", "shoot", "attack",
        ),
        "tags": ("combat gameplay", "intense fight", "boss fight"),
    },
    {
        "type": "funny failure",
        "phrases": (
            "we died", "i died", "that was stupid", "what am i doing",
            "this game is too easy", "new strat",
        ),
        "tags": ("funny gaming moments", "gaming fail", "streamer moment"),
    },
    {
        "type": "exploration/setup",
        "phrases": (
            "where am i", "what is this", "look at this", "all the way back",
            "restart",
        ),
        "tags": ("lets play", "gameplay moment", "playthrough"),
    },
    {
        "type": "cinematic/dialogue",
        "phrases": (
            "cutscene", "dialogue", "conversation", "objective updated",
            "checkpoint reached", "mission", "chapter", "quest",
        ),
        "tags": ("cinematic gameplay", "story moment", "game dialogue"),
    },
)


def _ollama_version(timeout: int = 3) -> str:
    """Return the local Ollama version if the service exposes one."""
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/version")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return ""
            data = json.loads(resp.read())
            version = str(data.get("version", "")).strip()
            if re.match(r"^\d+\.\d+(?:\.\d+)?", version):
                return version
    except Exception:
        return ""
    return ""


def _ollama_tags(timeout: int = 3) -> dict | None:
    """Return Ollama tags only when the local service looks like Ollama."""
    if not _ollama_version(timeout=timeout):
        return None
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read())
            if isinstance(data, dict) and isinstance(data.get("models"), list):
                return data
    except Exception:
        return None
    return None


def _ollama_available() -> bool:
    """Quick check if Ollama is running."""
    return _ollama_tags() is not None


def _model_exists(model: str = DEFAULT_MODEL) -> bool:
    """Check if a specific model is already downloaded in Ollama."""
    try:
        data = _ollama_tags()
        if not data:
            return False
        names = [m.get("name", "") for m in data.get("models", []) if isinstance(m, dict)]
        return model in names or f"{model}:latest" in names
    except Exception:
        return False


def _pull_model(model: str = DEFAULT_MODEL) -> bool:
    """Pull (download) a model via Ollama. Blocks until complete."""
    print(f"[title-gen] Model '{model}' not found — pulling from Ollama...")
    body = json.dumps({"name": model, "stream": False}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/pull",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        # Long timeout because first-time model pulls can be several GB.
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
            status = data.get("status", "")
            if "success" in status.lower():
                print(f"[title-gen] Model '{model}' pulled successfully")
                return True
            print(f"[title-gen] Pull status: {status}")
            return _model_exists(model)
    except Exception as e:
        print(f"[title-gen] Failed to pull model '{model}': {e}")
        return False


def ensure_model(model: str = DEFAULT_MODEL) -> bool:
    """Ensure the model is available — download it if needed. Returns True if ready."""
    if not _ollama_available():
        return False
    if _model_exists(model):
        return True
    return _pull_model(model)


def is_ollama_model_ready(model: str = DEFAULT_MODEL) -> bool:
    """Return True only when Ollama is running and the model is already installed."""
    return bool(_ollama_available() and _model_exists(model))


def game_hashtag(game_title: str | None) -> str:
    """Return a compact game hashtag, e.g. 'Alan Wake' -> '#AlanWake'."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", game_title or "").strip()
    if not cleaned:
        return "#Gaming"
    tag = "".join(part[:1].upper() + part[1:] for part in cleaned.split())
    return f"#{tag[:48]}" if tag else "#Gaming"


def recommended_hashtags(game_title: str | None = None) -> list[str]:
    """Return default description hashtags without duplicates."""
    unique = []
    seen = set()
    for tag in ("#shorts", game_hashtag(game_title), "#gaming"):
        key = tag.lower()
        if key in seen:
            continue
        unique.append(tag)
        seen.add(key)
    return unique


def format_short_title(base_title: str, game_title: str | None = None) -> str:
    """Append required Shorts/game hashtags while staying under YouTube's limit."""
    base = _clean_base_title(base_title)
    suffix = f"#shorts {game_hashtag(game_title)}"
    max_base = max(10, 100 - len(suffix) - 1)
    if len(base) > max_base:
        words = base.split()
        trimmed = ""
        for word in words:
            candidate = f"{trimmed} {word}".strip() if trimmed else word
            if len(candidate) > max_base:
                break
            trimmed = candidate
        base = trimmed or base[:max_base].rstrip()
    return f"{base} {suffix}".strip()


def generate_description(
    title: str,
    game_title: str | None = None,
    clip_context: dict | None = None,
    custom_text: str | None = None,
    auto_hashtags: bool = True,
) -> str:
    """Default upload/caption description for YouTube/TikTok sidecars."""
    return compose_description(
        title,
        game_title=game_title,
        clip_context=clip_context,
        custom_text=custom_text,
        auto_hashtags=auto_hashtags,
    )


def generated_description_body(
    title: str,
    game_title: str | None = None,
    clip_context: dict | None = None,
) -> str:
    """Generated description without custom text or hashtag footer."""
    base_title = _clean_base_title(title)
    context_line = _description_context_line(title, game_title, clip_context)
    if context_line and not _text_echoes_title(context_line, base_title):
        return context_line
    return base_title


def generate_ai_description_body(
    title: str,
    transcript: str | None = None,
    game_title: str | None = None,
    clip_context: dict | None = None,
    model: str = DEFAULT_MODEL,
) -> str:
    """Generate the short auto-written description body using local Ollama."""
    if not title or not is_ollama_model_ready(model):
        return ""
    prompt = _build_description_prompt(title, transcript or "", game_title, clip_context)
    try:
        parsed = ask_ollama_json(
            prompt,
            model=model,
            timeout=TIMEOUT,
            num_predict=120,
            temperature=0.45,
        )
    except Exception as exc:
        print(f"[description-gen] Ollama error: {exc}")
        return ""
    if not isinstance(parsed, dict):
        return ""
    return _clean_generated_description(parsed.get("description"), title=title)


def compose_description(
    title: str,
    game_title: str | None = None,
    clip_context: dict | None = None,
    custom_text: str | None = None,
    auto_hashtags: bool = True,
    generated_text: str | None = None,
) -> str:
    """Compose final upload text from generated description plus user text."""
    parts = []
    if generated_text:
        generated = _clean_generated_description(generated_text, title=title)
    else:
        generated = generated_description_body(title, game_title, clip_context)
    generated = generated.strip()
    custom = (custom_text or "").strip()
    if generated:
        parts.append(generated)
    if custom:
        parts.append(custom)
    if auto_hashtags:
        parts.append(" ".join(recommended_hashtags(game_title)))
    return "\n\n".join(parts)


def generate_tags(
    game_title: str | None = None,
    transcript: str | None = None,
    target_chars: int = YOUTUBE_TAG_TARGET,
    clip_context: dict | None = None,
) -> str:
    """Return comma-separated YouTube tags, staying roughly 100 chars below limit."""
    game = (game_title or "").strip()
    transcript_text = (transcript or "").lower()
    context = summarize_clip_context(transcript, game_title, clip_context)
    game_knowledge = context.get("game_knowledge") if isinstance(context.get("game_knowledge"), dict) else {}
    knowledge_text = " ".join(
        str(value or "")
        for key in ("label", "description", "genres", "series", "fictional_universes")
        for value in (
            game_knowledge.get(key)
            if isinstance(game_knowledge.get(key), list)
            else [game_knowledge.get(key)]
        )
        if value
    ).lower()
    primary_category = str(context.get("ai_primary_category") or context.get("primary_category") or "").strip()
    fine_labels = {
        str(label or "").strip().lower().replace("-", "_").replace(" ", "_")
        for label in (context.get("ai_fine_labels") or [])
    }
    moment_type = str(context.get("moment_type") or "")
    speech_source = context.get("speech_source") if isinstance(context.get("speech_source"), dict) else {}
    game_speech_like = (
        "game_narration" in fine_labels
        or "npc_dialogue" in fine_labels
        or str(speech_source.get("primary_source") or "").lower() in {"game", "game_or_npc", "npc"}
    )
    is_cinematic_dialogue = (
        primary_category == "cinematic_dialogue"
        or moment_type == "cinematic/dialogue"
        or game_speech_like
    )
    horror_context = any(
        term in " ".join([game.lower(), knowledge_text, transcript_text])
        for term in ("horror", "scary", "creepy", "survival horror", "jump scare", "jumpscare")
    )
    tags = []
    if game:
        tags.extend([game, f"{game} gameplay", f"{game} shorts", f"{game} clips"])
    multimodal = context.get("multimodal_analysis") if isinstance(context.get("multimodal_analysis"), dict) else {}
    for group_key in ("metadata_keywords", "visual_labels", "detected_events", "title_hooks"):
        values = multimodal.get(group_key)
        if not isinstance(values, list):
            continue
        for value in values[:6]:
            cleaned_value = re.sub(r"\s+", " ", str(value or "")).strip(" ,")
            if 2 <= len(cleaned_value) <= 36:
                tags.append(cleaned_value)
    for rule in MOMENT_SIGNAL_RULES:
        if context.get("moment_type") == rule["type"]:
            tags.extend(rule["tags"])
            break
    for phrase in context.get("hook_phrases", [])[:3]:
        if len(phrase) <= 32:
            tags.append(phrase)
    tags.extend([
        "shorts",
        "gaming",
        "gameplay",
        "gaming shorts",
        "youtube shorts",
        "viral shorts",
        "stream highlights",
        "lets play",
        "playthrough",
        "vertical gaming",
        "game clips",
    ])
    if is_cinematic_dialogue:
        tags.extend(["cinematic gameplay", "story moment", "game dialogue", "cutscene moment"])
    else:
        tags.extend(["streamer moments", "live stream clips", "gaming reaction", "funny gaming moments"])
    if horror_context:
        tags.extend([
            "scary gaming moments",
            "horror gaming",
            "scary game",
            "creepy game",
            "survival horror",
            "horror shorts",
            "jump scare",
            "chase scene",
            "panic moment",
        ])
    if any(word in transcript_text for word in ("run", "behind", "hide", "chase")):
        tags.extend(["chase gameplay", "running scared", "close call"])
    if not is_cinematic_dialogue and any(word in transcript_text for word in ("boss", "fight", "kill", "hit")):
        tags.extend(["boss fight", "combat gameplay", "intense fight"])
    if horror_context and any(word in transcript_text for word in ("scary", "oh my god", "what", "please")):
        tags.extend(["scary reaction", "panic reaction", "creepy moments"])

    unique = []
    seen = set()
    for tag in tags:
        cleaned = re.sub(r"\s+", " ", tag).strip(" ,")
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        candidate = ", ".join(unique + [cleaned])
        if len(candidate) > target_chars:
            continue
        unique.append(cleaned)
        seen.add(key)
    return ", ".join(unique)


def _clean_base_title(title: str) -> str:
    title = (title or "").split("\n")[0].strip().strip('"').strip("'")
    title = re.sub(r"#\w+", "", title).strip()
    title = re.sub(r"\s+", " ", title)
    for prefix in ["Title:", "title:", "Here's", "Here is"]:
        if title.startswith(prefix):
            title = title[len(prefix):].strip().strip('"').strip("'").strip()
    return title or "Gaming Moment"


def sanitize_creator_title_context(text: str | None, *, limit: int = 420) -> str:
    """Normalize short creator-provided title guidance without storing secrets."""
    value = _prompt_safe_text(text or "", limit=limit)
    return re.sub(r"\s+", " ", value).strip()


def summarize_clip_context(
    transcript: str | None = None,
    game_title: str | None = None,
    clip_context: dict | None = None,
) -> dict:
    """Summarize detector/ranker context for titles without rerunning analysis."""
    clip_context = clip_context if isinstance(clip_context, dict) else {}
    ranker = clip_context.get("ranker") if isinstance(clip_context.get("ranker"), dict) else {}
    detector_scores = (
        clip_context.get("detector_scores")
        if isinstance(clip_context.get("detector_scores"), dict)
        else {}
    )
    moment_categories = (
        clip_context.get("moment_categories")
        if isinstance(clip_context.get("moment_categories"), dict)
        else {}
    )
    visual = (
        clip_context.get("visual_diagnostics")
        if isinstance(clip_context.get("visual_diagnostics"), dict)
        else {}
    )
    ai_classification = (
        clip_context.get("ai_moment_classification")
        if isinstance(clip_context.get("ai_moment_classification"), dict)
        else {}
    )
    multimodal = (
        clip_context.get("multimodal_analysis")
        if isinstance(clip_context.get("multimodal_analysis"), dict)
        else {}
    )
    multi_signal_ai = (
        clip_context.get("multi_signal_ai_scoring")
        if isinstance(clip_context.get("multi_signal_ai_scoring"), dict)
        else {}
    )
    speech_source = (
        clip_context.get("speech_source")
        if isinstance(clip_context.get("speech_source"), dict)
        else {}
    )
    commentary_guard = (
        clip_context.get("commentary_guard")
        if isinstance(clip_context.get("commentary_guard"), dict)
        else {}
    )
    commentary_summary = (
        commentary_guard.get("summary")
        if isinstance(commentary_guard.get("summary"), dict)
        else {}
    )
    game_knowledge = compact_game_context_for_prompt(clip_context.get("game_context"))
    text = transcript or clip_context.get("transcript") or ""
    normal = _normal_text(text)
    matched = _matching_signal_phrases(normal)
    moment_type = _infer_moment_type(normal, ranker, matched, clip_context)

    resolved_game = (game_title or clip_context.get("game_title") or "").strip()
    creator_context = sanitize_creator_title_context(clip_context.get("creator_title_context"))
    raw_learning = (
        clip_context.get("feedback_learning_context")
        if isinstance(clip_context.get("feedback_learning_context"), dict)
        else {}
    )
    learning_context = {
        "enabled": bool(raw_learning.get("enabled")),
        "positive_feedback_count": int(raw_learning.get("positive_feedback_count") or 0),
        "negative_feedback_count": int(raw_learning.get("negative_feedback_count") or 0),
        "favorite_count": int(raw_learning.get("favorite_count") or 0),
        "run_learning_signal_count": int(raw_learning.get("run_learning_signal_count") or 0),
        "montage_learning_signal_count": int(raw_learning.get("montage_learning_signal_count") or 0),
        "positive_terms": [
            _prompt_safe_text(term, limit=48)
            for term in (raw_learning.get("positive_terms") or [])[:8]
            if str(term or "").strip()
        ],
        "negative_terms": [
            _prompt_safe_text(term, limit=48)
            for term in (raw_learning.get("negative_terms") or [])[:8]
            if str(term or "").strip()
        ],
        "guidance": _prompt_safe_text(raw_learning.get("guidance"), limit=240),
    }
    raw_speech_policy = (
        clip_context.get("speech_policy")
        if isinstance(clip_context.get("speech_policy"), dict)
        else {}
    )
    speech_policy = {
        "subtitle_policy": _prompt_safe_text(raw_speech_policy.get("subtitle_policy"), limit=24),
        "status": _prompt_safe_text(raw_speech_policy.get("status"), limit=48),
        "warning": _prompt_safe_text(raw_speech_policy.get("warning"), limit=180),
        "metadata_transcript_source": _prompt_safe_text(raw_speech_policy.get("metadata_transcript_source"), limit=48),
        "selected_track_has_speech": bool(raw_speech_policy.get("selected_track_has_speech")),
        "selected_track_word_count": _int_or_zero(raw_speech_policy.get("selected_track_word_count")),
        "analysis_word_count": _int_or_zero(raw_speech_policy.get("analysis_word_count")),
        "selected_stream": raw_speech_policy.get("selected_stream"),
        "selected_title": _prompt_safe_text(raw_speech_policy.get("selected_title"), limit=80),
        "selected_reason": _prompt_safe_text(raw_speech_policy.get("selected_reason"), limit=80),
        "render_audio": _prompt_safe_text(raw_speech_policy.get("render_audio"), limit=80),
        "mixed_speech_without_selected_track": bool(raw_speech_policy.get("mixed_speech_without_selected_track")),
        "metadata_backfill_blocked": bool(raw_speech_policy.get("metadata_backfill_blocked")),
    }
    summary = {
        "schema_version": TITLE_CONTEXT_SCHEMA_VERSION,
        "game_title": resolved_game,
        "game_knowledge": game_knowledge,
        "creator_title_context": creator_context,
        "feedback_learning_context": learning_context,
        "speech_policy": speech_policy,
        "metadata_warning": _prompt_safe_text(
            clip_context.get("metadata_warning") or speech_policy.get("warning"),
            limit=180,
        ),
        "metadata_needs_context": bool(
            clip_context.get("metadata_needs_context")
            or speech_policy.get("metadata_backfill_blocked")
        ),
        "moment_type": moment_type,
        "primary_category": clip_context.get("primary_category") or moment_categories.get("primary"),
        "ai_primary_category": ai_classification.get("primary_category"),
        "ai_fine_labels": ai_classification.get("fine_labels", []) if isinstance(ai_classification.get("fine_labels"), list) else [],
        "hook_phrases": matched[:6],
        "quality_score": _round_or_none(clip_context.get("quality_score")),
        "detector_score": _round_or_none(clip_context.get("detector_score")),
        "audio_score": _round_or_none(detector_scores.get("audio") or clip_context.get("audio_score")),
        "scene_score": _round_or_none(detector_scores.get("scene") or clip_context.get("scene_score")),
        "variance_score": _round_or_none(detector_scores.get("variance") or clip_context.get("variance_score")),
        "scene_detection_status": clip_context.get("scene_detection_status"),
        "candidate_rank": clip_context.get("candidate_rank"),
        "candidate_kind": clip_context.get("candidate_kind"),
        "selection_quality_score": _round_or_none(clip_context.get("selection_quality_score")),
        "learned_quality_score": _round_or_none(clip_context.get("learned_quality_score")),
        "multi_signal_ai_quality_score": _round_or_none(
            clip_context.get("multi_signal_ai_quality_score")
            or multi_signal_ai.get("multi_signal_ai_quality_score")
        ),
        "multi_signal_ai_adjustment": _round_or_none(
            clip_context.get("multi_signal_ai_adjustment")
            or multi_signal_ai.get("multi_signal_adjustment")
        ),
        "quality_rank": clip_context.get("quality_rank"),
        "word_count": clip_context.get("word_count"),
        "visual": {
            "status": visual.get("status"),
            "labels": visual.get("labels", [])[:6] if isinstance(visual.get("labels"), list) else [],
            "visual_energy": _round_or_none(visual.get("visual_energy")),
            "possible_failure_score": _round_or_none(visual.get("possible_failure_score")),
            "scenic_score": _round_or_none(visual.get("scenic_score")),
            "ui_density": _round_or_none(visual.get("ui_density")),
        },
        "ai_moment_classification": {
            "status": ai_classification.get("status"),
            "provider": ai_classification.get("provider"),
            "primary_category": ai_classification.get("primary_category"),
            "fine_labels": ai_classification.get("fine_labels", [])[:5]
            if isinstance(ai_classification.get("fine_labels"), list) else [],
            "confidence": _round_or_none(ai_classification.get("confidence")),
            "fallback_used": bool(ai_classification.get("fallback_used")),
        },
        "speech_source": {
            "primary_source": speech_source.get("primary_source"),
            "creator_probability": _round_or_none(speech_source.get("creator_probability")),
            "game_or_npc_probability": _round_or_none(speech_source.get("game_or_npc_probability")),
            "music_or_lyrics_probability": _round_or_none(speech_source.get("music_or_lyrics_probability")),
            "creator_safe": bool(speech_source.get("creator_safe")),
            "commentary_guard_label": commentary_summary.get("primary_label"),
        },
        "multimodal_analysis": {
            "status": multimodal.get("status"),
            "provider": multimodal.get("provider"),
            "model": multimodal.get("model"),
            "primary_visual_label": multimodal.get("primary_visual_label"),
            "visible_summary": multimodal.get("visible_summary"),
            "visual_labels": multimodal.get("visual_labels", [])[:6]
            if isinstance(multimodal.get("visual_labels"), list) else [],
            "detected_events": multimodal.get("detected_events", [])[:4]
            if isinstance(multimodal.get("detected_events"), list) else [],
            "title_hooks": multimodal.get("title_hooks", [])[:4]
            if isinstance(multimodal.get("title_hooks"), list) else [],
            "metadata_keywords": multimodal.get("metadata_keywords", [])[:8]
            if isinstance(multimodal.get("metadata_keywords"), list) else [],
            "confidence": _round_or_none(multimodal.get("confidence")),
            "ranking_adjustment": _round_or_none(multimodal.get("ranking_adjustment")),
            "reject_flags": multimodal.get("reject_flags", [])[:5]
            if isinstance(multimodal.get("reject_flags"), list) else [],
        },
        "multi_signal_ai": {
            "ranking_enabled": bool(multi_signal_ai.get("ranking_enabled")),
            "selection_delta": multi_signal_ai.get("selection_delta", ""),
            "rank_delta": multi_signal_ai.get("rank_delta"),
            "signals": multi_signal_ai.get("signals") if isinstance(multi_signal_ai.get("signals"), dict) else {},
            "contributions": multi_signal_ai.get("contributions") if isinstance(multi_signal_ai.get("contributions"), dict) else {},
        },
        "timing": {
            "start": clip_context.get("start"),
            "end": clip_context.get("end"),
            "duration": clip_context.get("duration"),
            "peak_time": clip_context.get("peak_time"),
        },
        "ranker": {
            "hook_points": _round_or_none(ranker.get("hook_points")),
            "weak_points": _round_or_none(ranker.get("weak_points")),
            "aftermath_points": _round_or_none(ranker.get("aftermath_points")),
            "first_word_start": _round_or_none(ranker.get("first_word_start")),
            "last_word_end": _round_or_none(ranker.get("last_word_end")),
            "reject_reason": ranker.get("reject_reason"),
        },
    }
    summary["context_sentence"] = _context_sentence(summary, resolved_game)
    return summary


def _build_ollama_prompt(
    transcript: str,
    game_title: str | None = None,
    clip_context: dict | None = None,
) -> str:
    context = summarize_clip_context(transcript, game_title, clip_context)
    game_line = (
        f'The game is "{context["game_title"]}". Mention the game context naturally when it helps.\n'
        if context["game_title"] else
        "The game title is unknown, so avoid inventing a specific game name.\n"
    )
    analysis_lines = _analysis_prompt_lines(context)
    analysis_block = "\n".join(analysis_lines)
    return (
        "You are a gaming YouTube Shorts title expert. "
        "Given a gameplay transcript and the same detector/ranker analysis used to select the clip, "
        "create ONE title that makes people want to watch.\n"
        f"{game_line}\n"
        "Use the analysis as grounding, but do not expose scores in the title.\n"
        "Use creator-provided context only as guidance; do not quote it verbatim.\n"
        "Prefer the strongest spoken hook/payoff over generic clipbait.\n\n"
        "RULES:\n"
        "- Max 70 characters before hashtags\n"
        "- No quotes, no hashtags, no emojis\n"
        "- Make it specific to the game moment, enemy, objective, or reaction\n"
        "- Do not invent enemy names, locations, weapons, or mechanics not present in the transcript or analysis\n"
        "- Treat the transcript as the selected creator-commentary transcript, not as proof of game/NPC dialogue\n"
        "- If the speech policy says there is no selected creator speech, do not invent a creator reaction\n"
        "- Do not claim something is a fan favorite, guide, tutorial, review, or secret unless the transcript or creator context supports it\n"
        "- Avoid generic titles like 'This Changes Everything' unless the transcript truly supports it\n"
        "- Use curiosity, danger, panic, funny failure, or a clear payoff\n"
        "- Good examples: 'Alan Wake Had Me Running For My Life', "
        "'This Chase Got Way Too Close', 'This Boss Fight Got Personal'\n\n"
        f"Clip analysis from detector/ranker:\n{analysis_block}\n\n"
        f'Transcript: "{_prompt_safe_text(transcript, limit=900)}"\n\n'
        "Reply with ONLY the title. Nothing else."
    )


def _ask_ollama(
    transcript: str,
    model: str = DEFAULT_MODEL,
    game_title: str | None = None,
    clip_context: dict | None = None,
) -> str | None:
    """Ask Ollama for a catchy short YouTube Shorts title."""
    prompt = _build_ollama_prompt(transcript, game_title=game_title, clip_context=clip_context)
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": 0.7, "num_predict": 40},
    }).encode()

    req = urllib.request.Request(
        OLLAMA_URL,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read())
            title = _clean_base_title(_ollama_response_text(data))
            if title and len(title) >= 3:
                # Truncate at word boundary to keep titles clean for Shorts
                if len(title) > 70:
                    words = title.split()
                    title = ""
                    for w in words:
                        candidate = f"{title} {w}".strip() if title else w
                        if len(candidate) > 70:
                            break
                        title = candidate
                return title
    except Exception as e:
        print(f"[title-gen] Ollama error: {e}")
    return None


def _warm_ollama_model(model: str = DEFAULT_MODEL) -> bool:
    """Load the local model once before batch work fans out."""
    body = json.dumps({
        "model": model,
        "prompt": "Reply with OK.",
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": 0.0, "num_predict": 2},
    }).encode()
    req = urllib.request.Request(
        OLLAMA_URL,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return getattr(resp, "status", 200) == 200
    except Exception as e:
        print(f"[title-gen] Ollama warm-up failed: {e}")
        return False


def ask_ollama_json(
    prompt: str,
    model: str = DEFAULT_MODEL,
    *,
    timeout: int = AI_CLASSIFICATION_TIMEOUT,
    num_predict: int = 160,
    temperature: float = 0.2,
) -> dict | None:
    """Ask local Ollama for a JSON object and parse it safely."""
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {
            "temperature": float(temperature),
            "num_predict": int(num_predict),
        },
    }).encode()
    req = urllib.request.Request(
        OLLAMA_URL,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if getattr(resp, "status", 200) != 200:
            return None
        data = json.loads(resp.read())
    response = _ollama_response_text(data)
    parsed = _extract_json_object(response)
    return parsed if isinstance(parsed, dict) else None


def classify_moment_ai(
    transcript: str | None,
    game_title: str | None = None,
    clip_context: dict | None = None,
    *,
    enabled: bool = True,
    model: str = DEFAULT_MODEL,
    ollama_ready: bool | None = None,
    timeout: int = AI_CLASSIFICATION_TIMEOUT,
) -> dict:
    """Classify a gameplay moment using local Ollama with a deterministic fallback."""
    clip_context = clip_context if isinstance(clip_context, dict) else {}
    fallback = _heuristic_moment_classification(transcript, game_title, clip_context)
    fallback["enabled"] = bool(enabled)
    fallback["model"] = model

    if not enabled:
        fallback["status"] = "disabled"
        fallback["fallback_used"] = True
        return fallback

    ready = is_ollama_model_ready(model) if ollama_ready is None else bool(ollama_ready)
    if not ready:
        fallback["status"] = "model_not_ready"
        fallback["fallback_used"] = True
        return fallback

    prompt = _build_moment_classification_prompt(transcript, game_title, clip_context)
    try:
        response = ask_ollama_json(
            prompt,
            model=model,
            timeout=timeout,
            num_predict=240,
            temperature=0.2,
        )
    except Exception as exc:
        fallback["status"] = "ollama_error"
        fallback["fallback_used"] = True
        fallback["error"] = str(exc)[:160]
        return fallback

    sanitized = _sanitize_ai_classification(response, fallback, model)
    if sanitized is None:
        fallback["status"] = "invalid_response"
        fallback["fallback_used"] = True
        return fallback
    return sanitized


def _build_moment_classification_prompt(
    transcript: str | None,
    game_title: str | None = None,
    clip_context: dict | None = None,
) -> str:
    clip_context = clip_context if isinstance(clip_context, dict) else {}
    context = summarize_clip_context(transcript, game_title, clip_context)
    categories = clip_context.get("moment_categories") if isinstance(clip_context.get("moment_categories"), dict) else {}
    category_scores = categories.get("scores") if isinstance(categories.get("scores"), dict) else {}
    visual = context.get("visual") if isinstance(context.get("visual"), dict) else {}
    ranker = context.get("ranker") if isinstance(context.get("ranker"), dict) else {}
    detector = {
        "audio": context.get("audio_score"),
        "variance": context.get("variance_score"),
        "scene": context.get("scene_score"),
    }
    learning = context.get("feedback_learning_context") if isinstance(context.get("feedback_learning_context"), dict) else {}
    classification_learning = {
        key: learning.get(key)
        for key in (
            "enabled",
            "positive_feedback_count",
            "negative_feedback_count",
            "favorite_count",
            "run_learning_signal_count",
            "montage_learning_signal_count",
            "positive_terms",
            "negative_terms",
        )
        if key in learning
    }
    payload = {
        "game_title": context.get("game_title") or "",
        "game_knowledge": context.get("game_knowledge"),
        "heuristic_primary": context.get("primary_category") or categories.get("primary") or "general_gameplay",
        "creator_learning": classification_learning,
        "heuristic_scores": {
            key: _round_or_none(category_scores.get(key))
            for key in AI_MOMENT_CATEGORIES
            if category_scores.get(key) is not None
        },
        "detector_scores": detector,
        "ranker": {
            "hook_points": ranker.get("hook_points"),
            "weak_points": ranker.get("weak_points"),
            "aftermath_points": ranker.get("aftermath_points"),
        },
        "visual": visual,
        "word_count": context.get("word_count"),
        "transcript_preview": _prompt_safe_text(
            transcript or clip_context.get("transcript") or "",
            limit=700,
        ),
    }
    allowed = ", ".join(AI_MOMENT_CATEGORIES)
    fine = ", ".join(AI_FINE_LABELS)
    return (
        "You classify gameplay clips for a local Shorts clipping app.\n"
        "Use only the compact transcript preview and numeric detector metadata below.\n"
        "Use game knowledge as background only. Do not invent names, locations, mechanics, enemies, or events.\n"
        "Return exactly one JSON object with this schema:\n"
        "{\"primary_category\":\"one allowed category\",\"fine_labels\":[\"compact_label\"],"
        "\"confidence\":0.0,\"reason\":\"short grounded reason\","
        "\"ai_viral_score\":0,\"ai_viral_reason\":\"short score reason\","
        "\"ai_dimensions\":{\"hook\":0.0,\"flow\":0.0,\"value\":0.0,"
        "\"platform_fit\":0.0,\"game_context\":0.0}}\n"
        f"Allowed primary_category values: {allowed}\n"
        f"Allowed fine_labels values: {fine}\n"
        "Score 0-99 for short-form potential, grounded in hook, flow, value, platform fit, and game context.\n"
        "If uncertain, use the heuristic primary. Avoid low_value unless the moment is truly weak, menu, navigation, or filler.\n\n"
        f"Clip metadata JSON:\n{json.dumps(payload, ensure_ascii=True, sort_keys=True)}\n"
    )


def _clamp01(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:
        return 0.0
    return max(0.0, min(1.0, number))


def _viral_dimensions_from_context(
    transcript: str | None,
    clip_context: dict | None,
    primary: str,
    fine_labels: list[str],
    confidence: float | None,
) -> tuple[dict, int, str]:
    clip_context = clip_context if isinstance(clip_context, dict) else {}
    context = summarize_clip_context(transcript, None, clip_context)
    ranker = clip_context.get("ranker") if isinstance(clip_context.get("ranker"), dict) else {}
    categories = clip_context.get("moment_categories") if isinstance(clip_context.get("moment_categories"), dict) else {}
    scores = categories.get("scores") if isinstance(categories.get("scores"), dict) else {}
    visual = clip_context.get("visual_diagnostics") if isinstance(clip_context.get("visual_diagnostics"), dict) else {}
    normal = _normal_text(transcript or clip_context.get("transcript") or "")
    word_count = _float_or_zero(clip_context.get("word_count") or context.get("word_count"))
    hook_hits = len(_matching_signal_phrases(normal))
    hook = _clamp01(
        0.18
        + 0.18 * hook_hits
        + 0.10 * _float_or_zero(ranker.get("hook_points"))
        + (0.18 if primary in {"high_energy", "death_or_failure"} else 0.0)
        + (0.10 if {"chase_panic", "combat_action", "funny_failure"} & set(fine_labels) else 0.0)
    )
    first_word_start = _float_or_zero(ranker.get("first_word_start"))
    late_start_penalty = 0.18 if first_word_start and first_word_start > 8.0 else 0.0
    flow = _clamp01(
        0.50
        + (0.10 if 8 <= word_count <= 90 else -0.12)
        + (0.08 if _float_or_zero(ranker.get("last_word_end")) >= 6.0 else 0.0)
        - late_start_penalty
        - (0.18 if primary == "low_value" else 0.0)
    )
    value = _clamp01(
        0.35
        + (0.22 if primary in {"high_energy", "death_or_failure", "tutorial_or_explainer", "lore_or_story", "cinematic_dialogue", "atmosphere_or_visual"} else 0.0)
        + (0.12 if primary == "tutorial_or_explainer" else 0.0)
        + (0.08 if primary == "lore_or_story" else 0.0)
        + (0.06 if primary == "cinematic_dialogue" else 0.0)
        + 0.12 * _float_or_zero(scores.get(primary))
        - (0.25 if primary == "low_value" else 0.0)
    )
    platform_fit = _clamp01(
        0.62
        + (0.10 if 6 <= word_count <= 80 else -0.12)
        - 0.22 * _float_or_zero(visual.get("black_frame_ratio"))
        - 0.12 * _float_or_zero(visual.get("ui_density"))
        - (0.18 if primary == "low_value" else 0.0)
    )
    game_context = _clamp01(
        0.38
        + 0.16 * _float_or_zero(context.get("audio_score"))
        + 0.14 * _float_or_zero(context.get("variance_score"))
        + 0.14 * _float_or_zero(visual.get("visual_energy"))
        + 0.16 * _float_or_zero(visual.get("possible_failure_score"))
        + 0.12 * _float_or_zero(visual.get("scenic_score"))
        + (0.08 if primary in {"high_energy", "death_or_failure", "cinematic_dialogue", "atmosphere_or_visual"} else 0.0)
    )
    game_knowledge = context.get("game_knowledge") if isinstance(context.get("game_knowledge"), dict) else {}
    if game_knowledge.get("available"):
        filled_fields = sum(
            1
            for key in (
                "genres",
                "series",
                "fictional_universes",
                "characters",
                "narrative_locations",
                "game_modes",
                "release_year",
            )
            if game_knowledge.get(key)
        )
        richness = min(1.0, filled_fields / 5.0)
        game_context = _clamp01(
            game_context
            + 0.08 * richness
            + (
                0.04 * richness
                if primary in {"lore_or_story", "cinematic_dialogue", "tutorial_or_explainer", "atmosphere_or_visual"}
                else 0.0
            )
        )
    dimensions = {
        "hook": round(hook, 4),
        "flow": round(flow, 4),
        "value": round(value, 4),
        "platform_fit": round(platform_fit, 4),
        "game_context": round(game_context, 4),
    }
    score = round(
        99
        * (
            0.25 * hook
            + 0.20 * flow
            + 0.24 * value
            + 0.16 * platform_fit
            + 0.15 * game_context
        )
        * (0.82 + 0.18 * _clamp01(confidence if confidence is not None else 0.45))
    )
    score = int(max(0, min(99, score)))
    strongest = max(dimensions.items(), key=lambda item: item[1])[0].replace("_", " ")
    reason = f"Diagnostic score led by {strongest}; category={primary or 'unknown'}."
    return dimensions, score, reason


def _sanitize_ai_dimensions(dimensions, fallback: dict) -> dict:
    fallback = fallback if isinstance(fallback, dict) else {}
    source = dimensions if isinstance(dimensions, dict) else {}
    cleaned = {}
    for key in AI_VIRAL_DIMENSIONS:
        raw = source.get(key, fallback.get(key, 0.0))
        cleaned[key] = round(_clamp01(raw), 4)
    return cleaned


def _sanitize_ai_viral_fields(response: dict, fallback: dict) -> dict:
    response = response if isinstance(response, dict) else {}
    fallback = fallback if isinstance(fallback, dict) else {}
    fallback_dimensions = fallback.get("ai_dimensions") if isinstance(fallback.get("ai_dimensions"), dict) else {}
    dimensions = _sanitize_ai_dimensions(response.get("ai_dimensions"), fallback_dimensions)
    score = _round_or_none(response.get("ai_viral_score"))
    if score is None:
        score = _round_or_none(fallback.get("ai_viral_score"))
    if score is None:
        score = 0
    score = int(max(0, min(99, round(float(score)))))
    reason = re.sub(
        r"\s+",
        " ",
        str(response.get("ai_viral_reason") or fallback.get("ai_viral_reason") or fallback.get("reason") or ""),
    ).strip()
    if len(reason) > 180:
        reason = reason[:177].rstrip() + "..."
    confidence = _round_or_none(response.get("ai_confidence"))
    if confidence is None:
        confidence = _round_or_none(response.get("confidence"))
    if confidence is None:
        confidence = _round_or_none(fallback.get("ai_confidence"))
    return {
        "ai_viral_score": score,
        "ai_viral_reason": reason,
        "ai_dimensions": dimensions,
        "ai_confidence": round(_clamp01(confidence if confidence is not None else 0.0), 4),
        "ai_adjustment": 0.0,
        "ai_rank_delta": None,
        "ai_scoring_eligible": False,
    }


def _heuristic_moment_classification(
    transcript: str | None,
    game_title: str | None,
    clip_context: dict | None,
) -> dict:
    clip_context = clip_context if isinstance(clip_context, dict) else {}
    context = summarize_clip_context(transcript, game_title, clip_context)
    categories = clip_context.get("moment_categories") if isinstance(clip_context.get("moment_categories"), dict) else {}
    visual = clip_context.get("visual_diagnostics") if isinstance(clip_context.get("visual_diagnostics"), dict) else {}
    primary = str(clip_context.get("primary_category") or categories.get("primary") or "").strip()
    confidence = _round_or_none(categories.get("confidence"))
    moment_type = context.get("moment_type") or "general gameplay"
    fine_labels = []
    ranker = clip_context.get("ranker") if isinstance(clip_context.get("ranker"), dict) else {}
    category_scores = categories.get("scores") if isinstance(categories.get("scores"), dict) else {}
    normal = _normal_text(transcript or clip_context.get("transcript") or "")
    visual_failure = _float_or_zero(visual.get("possible_failure_score"))
    confirmed_failure = (
        primary == "death_or_failure"
        or moment_type in {"death/failure", "funny failure"}
        or _float_or_zero(category_scores.get("death_or_failure")) >= 0.50
        or _float_or_zero(ranker.get("aftermath_points")) >= 2.0
        or bool(re.search(r"\b(died|dead|death|killed|game over|failed|failure|we died|i died)\b", normal))
    )
    if visual_failure >= 0.45:
        primary = "death_or_failure"
        fine_labels.append("death_scene" if confirmed_failure else "possible_failure")
        confidence = max(confidence or 0.0, 0.62 if confirmed_failure else 0.52)
    if visual.get("scenic_score", 0) and _float_or_zero(visual.get("scenic_score")) >= 0.55 and primary not in {"death_or_failure", "high_energy"}:
        primary = "atmosphere_or_visual"
        fine_labels.append("scenic_atmosphere")
        confidence = max(confidence or 0.0, 0.58)
    if not primary or primary not in AI_MOMENT_CATEGORIES:
        if moment_type == "chase/panic":
            primary = "high_energy"
        elif moment_type == "combat/fight":
            primary = "high_energy"
        elif moment_type == "funny failure":
            primary = "death_or_failure"
        elif moment_type == "cinematic/dialogue":
            primary = "cinematic_dialogue"
        elif moment_type == "exploration/setup":
            primary = "low_value"
        else:
            primary = "low_value" if not str(transcript or "").strip() else "commentary_or_review"
    if moment_type == "chase/panic":
        fine_labels.append("chase_panic")
    elif moment_type == "combat/fight":
        fine_labels.append("combat_action")
    elif moment_type == "funny failure":
        fine_labels.append("funny_failure")
    elif primary == "tutorial_or_explainer":
        fine_labels.append("tutorial_tip")
    elif primary == "lore_or_story":
        fine_labels.append("lore_story")
    elif primary == "cinematic_dialogue":
        fine_labels.append("cinematic_dialogue")
        speech_source = context.get("speech_source") if isinstance(context.get("speech_source"), dict) else {}
        if str(speech_source.get("primary_source") or "").lower() in {"game", "game_or_npc", "npc"}:
            fine_labels.append("npc_dialogue")
    elif primary == "commentary_or_review":
        fine_labels.append("creator_reaction")
    fine_labels = _sanitize_fine_labels(fine_labels)
    confidence_value = round(max(0.0, min(1.0, confidence if confidence is not None else 0.45)), 4)
    dimensions, viral_score, viral_reason = _viral_dimensions_from_context(
        transcript,
        clip_context,
        primary,
        fine_labels,
        confidence_value,
    )
    return {
        "schema_version": AI_MOMENT_CLASSIFICATION_SCHEMA_VERSION,
        "enabled": True,
        "status": "heuristic",
        "provider": "heuristic",
        "model": "",
        "primary_category": primary,
        "fine_labels": fine_labels,
        "confidence": confidence_value,
        "reason": _classification_reason(primary, fine_labels, visual),
        "fallback_used": True,
        "ai_viral_score": viral_score,
        "ai_viral_reason": viral_reason,
        "ai_dimensions": dimensions,
        "ai_confidence": confidence_value,
        "ai_adjustment": 0.0,
        "ai_rank_delta": None,
        "ai_scoring_eligible": False,
        "selection_impact": "none",
        "output_changed": False,
    }


def _sanitize_ai_classification(response, fallback: dict, model: str) -> dict | None:
    if not isinstance(response, dict):
        return None
    primary = str(
        response.get("primary_category")
        or response.get("primary")
        or response.get("category")
        or ""
    ).strip().lower()
    primary = primary.replace("-", "_").replace(" ", "_")
    aliases = {
        "tutorial_explainer": "tutorial_or_explainer",
        "commentary_review": "commentary_or_review",
        "lore_story": "lore_or_story",
        "atmosphere_scenic": "atmosphere_or_visual",
        "cinematic": "cinematic_dialogue",
        "cinematic_scene": "cinematic_dialogue",
        "dialogue_scene": "cinematic_dialogue",
        "npc_dialogue": "cinematic_dialogue",
        "death_failure": "death_or_failure",
        "funny_failure": "death_or_failure",
        "general_gameplay": fallback.get("primary_category", "low_value"),
    }
    primary = aliases.get(primary, primary)
    if primary not in AI_MOMENT_CATEGORIES:
        return None
    confidence = _round_or_none(response.get("confidence"))
    if confidence is None:
        confidence = fallback.get("confidence", 0.45)
    confidence = max(0.0, min(1.0, float(confidence)))
    fine_labels = response.get("fine_labels")
    if not isinstance(fine_labels, list):
        fine_labels = [response.get("fine_label")] if response.get("fine_label") else []
    fine_labels = _sanitize_fine_labels(fine_labels)
    reason = re.sub(r"\s+", " ", str(response.get("reason") or "")).strip()
    if len(reason) > 180:
        reason = reason[:177].rstrip() + "..."
    viral_fields = _sanitize_ai_viral_fields(response, fallback)
    return {
        "schema_version": AI_MOMENT_CLASSIFICATION_SCHEMA_VERSION,
        "enabled": True,
        "status": "ok",
        "provider": "ollama",
        "model": model,
        "primary_category": primary,
        "fine_labels": fine_labels,
        "confidence": round(confidence, 4),
        "reason": reason or _classification_reason(primary, fine_labels, {}),
        "fallback_used": False,
        "fallback_primary_category": fallback.get("primary_category"),
        **viral_fields,
        "selection_impact": "none",
        "output_changed": False,
    }


def _sanitize_fine_labels(labels) -> list[str]:
    cleaned = []
    seen = set()
    for label in labels or []:
        value = str(label or "").strip().lower().replace("-", "_").replace(" ", "_")
        if value not in AI_FINE_LABELS or value in seen:
            continue
        cleaned.append(value)
        seen.add(value)
        if len(cleaned) >= 5:
            break
    return cleaned


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
        parsed = json.loads(text[start:end + 1])
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
    return ""


def _classification_reason(primary: str, fine_labels: list[str], visual: dict) -> str:
    if primary == "death_or_failure":
        return "Failure/death cues from transcript, ranker, or visual diagnostics."
    if primary == "high_energy":
        return "High-energy action, panic, combat, or strong hook cues."
    if primary == "tutorial_or_explainer":
        return "Instructional or explanation-style language."
    if primary == "lore_or_story":
        return "Story, lore, or narrative-context language."
    if primary == "cinematic_dialogue":
        return "In-game dialogue or cinematic story context carries the moment."
    if primary == "atmosphere_or_visual":
        return "Atmospheric or scenic visual/context cues."
    if primary == "commentary_or_review":
        return "Creator commentary or opinion-focused moment."
    if primary == "low_value":
        return "Weak, filler, navigation, menu, or low-payoff moment."
    return "Compact deterministic fallback classification."


def _prompt_safe_text(text: str | None, *, limit: int = 900) -> str:
    """Keep local LLM prompts grounded without leaking obvious local secrets."""
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(
        r"(?i)\b(refresh_token|access_token|client_secret|api[_-]?key|gemini_api_key)\s*[:=]\s*[\w.\-~+/=]+",
        r"\1=[redacted]",
        value,
    )
    value = re.sub(r"\b[A-Za-z]:\\[^\s\"']+", "[local-path]", value)
    value = re.sub(r"/(?:Users|home|mnt|media|Volumes)/[^\s\"']+", "[local-path]", value)
    return value[: max(0, int(limit))]


def _heuristic_title(
    transcript: str,
    game_title: str | None = None,
    clip_context: dict | None = None,
) -> str:
    """Fallback: generate a clickbait-style title from transcript keywords."""
    import random
    if not transcript:
        return ""

    context = summarize_clip_context(transcript, game_title, clip_context)
    game = game_title or context.get("game_title") or "Game"
    moment_type = context.get("moment_type")
    hook = context.get("hook_phrases", [""])[0] if context.get("hook_phrases") else ""
    if moment_type == "chase/panic":
        if "right behind" in hook:
            return f"He Was Right Behind Me In {game}"
        return random.choice([
            f"{game} Had Me Running For My Life",
            f"This {game} Chase Got Way Too Close",
            f"{game} Turned Into Pure Panic",
        ])
    if moment_type == "combat/fight":
        return random.choice([
            f"This {game} Fight Got Personal",
            f"{game} Almost Ended Me Here",
            f"I Was Not Ready For This {game} Fight",
        ])
    if moment_type == "funny failure":
        return random.choice([
            f"This {game} Moment Went Sideways",
            f"{game} Went Completely Wrong",
            f"This Was A Bad Idea In {game}",
        ])
    if moment_type == "cinematic/dialogue":
        return random.choice([
            f"{game} Drops A Story Moment",
            f"This {game} Scene Got Interesting",
            f"{game} Paused For A Story Beat",
        ])

    words = transcript.lower().split()

    # Extract a short key phrase (2-4 words) from the middle of the transcript
    # Middle tends to have the core topic, not filler intro/outro
    mid = len(words) // 2
    start = max(0, mid - 2)
    key_phrase = " ".join(words[start:start + 3]).strip(".,!?;:'\"")

    # Clickbait templates — {topic} gets replaced with the key phrase
    templates = [
        "{game} Got Way Too Intense",
        "This {game} Moment Went Sideways",
        "{game} Almost Ended Me Here",
        "I Was Not Ready For This {game} Fight",
        "{topic} Went Completely Wrong",
        "This Was A Bad Idea In {game}",
        "{game} Turned Into Pure Panic",
        "Wait For This {game} Moment",
    ]

    topic = key_phrase.title()
    title = random.choice(templates).format(topic=topic, game=game)

    # If title is too long, use shorter templates
    if len(title) > 55:
        short_templates = [
            "{topic} Goes Wrong",
            "{game} Goes Wrong",
            "Wait For {topic}",
            "{topic} Was Insane",
            "This {game} Fight Though",
        ]
        title = random.choice(short_templates).format(topic=topic, game=game)

    # Final safety: truncate at word boundary
    if len(title) > 60:
        parts = title.split()
        title = ""
        for w in parts:
            candidate = f"{title} {w}".strip() if title else w
            if len(candidate) > 55:
                break
            title = candidate

    return title


def generate_title(
    transcript: str,
    model: str = DEFAULT_MODEL,
    game_title: str | None = None,
    clip_context: dict | None = None,
) -> str:
    """Generate a title for a clip. Uses Ollama if available, else heuristic."""
    if not transcript:
        print("[title-gen] Skipped — empty transcript")
        return ""

    # Try Ollama first only when the model is already installed. Model downloads
    # are intentionally opt-in through the Settings UI.
    if is_ollama_model_ready(model):
        result = _ask_ollama(transcript, model, game_title=game_title, clip_context=clip_context)
        if result:
            formatted = format_short_title(result, game_title)
            print(f"[title-gen] LLM: {formatted}")
            return formatted
        print(f"[title-gen] LLM returned empty/short, trying heuristic...")

    # Fallback to heuristic
    result = _heuristic_title(transcript, game_title=game_title, clip_context=clip_context)
    if result:
        result = format_short_title(result, game_title)
        print(f"[title-gen] Heuristic: {result}")
    else:
        print(f"[title-gen] Both LLM and heuristic failed for transcript: {transcript[:60]}...")
    return result


def generate_titles_batch(
    transcripts: list[str],
    model: str = DEFAULT_MODEL,
    on_progress=None,
    game_titles: list[str] | None = None,
    clip_contexts: list[dict] | None = None,
) -> list[str]:
    """Generate titles for multiple clips. Uses concurrent requests for speed.

    on_progress(done, total, title) is called after each title is generated.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total = len(transcripts)
    if not total:
        return []

    # Check model availability ONCE, not per-title. Do not auto-pull here; model
    # downloads are intentionally opt-in through the Settings UI.
    model_ready = is_ollama_model_ready(model)
    if model_ready:
        _warm_ollama_model(model)

    results = [""] * total
    done_count = 0

    def _gen_one(idx_transcript):
        idx, transcript = idx_transcript
        if not transcript:
            return idx, ""
        game_title = game_titles[idx] if game_titles and idx < len(game_titles) else None
        clip_context = clip_contexts[idx] if clip_contexts and idx < len(clip_contexts) else None
        if model_ready:
            title = _ask_ollama(transcript, model, game_title=game_title, clip_context=clip_context)
            if title:
                return idx, format_short_title(title, game_title)
        return idx, format_short_title(_heuristic_title(transcript, game_title, clip_context), game_title)

    # Run up to 3 concurrent Ollama requests (Ollama handles queuing internally)
    workers = min(3, total) if model_ready else 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_gen_one, (i, t)): i for i, t in enumerate(transcripts)}
        for future in as_completed(futures):
            try:
                idx, title = future.result()
                results[idx] = title
                done_count += 1
                if on_progress:
                    on_progress(done_count, total, title)
                print(f"[title-gen] {done_count}/{total}: {title or '(empty)'}")
            except Exception as e:
                done_count += 1
                print(f"[title-gen] Error: {e}")

    return results


def _analysis_prompt_lines(context: dict) -> list[str]:
    ranker = context.get("ranker") or {}
    timing = context.get("timing") or {}
    visual = context.get("visual") if isinstance(context.get("visual"), dict) else {}
    ai_classification = (
        context.get("ai_moment_classification")
        if isinstance(context.get("ai_moment_classification"), dict)
        else {}
    )
    multimodal = (
        context.get("multimodal_analysis")
        if isinstance(context.get("multimodal_analysis"), dict)
        else {}
    )
    lines = [
        f"- Moment type: {context.get('moment_type') or 'general gameplay'}",
    ]
    game_knowledge = context.get("game_knowledge") if isinstance(context.get("game_knowledge"), dict) else {}
    if game_knowledge.get("available"):
        fact_parts = []
        if game_knowledge.get("label"):
            fact_parts.append(f"title={game_knowledge.get('label')}")
        if game_knowledge.get("release_year"):
            fact_parts.append(f"year={game_knowledge.get('release_year')}")
        for key, label in (
            ("series", "series"),
            ("fictional_universes", "universe"),
            ("genres", "genre"),
            ("developers", "developer"),
            ("characters", "characters"),
            ("narrative_locations", "setting"),
            ("game_modes", "mode"),
        ):
            values = game_knowledge.get(key)
            if isinstance(values, list) and values:
                fact_parts.append(f"{label}={', '.join(values[:4])}")
        if fact_parts:
            lines.append(
                "- Game knowledge: "
                + "; ".join(fact_parts[:8])
                + ". Use as background only; do not name characters, places, or mechanics unless transcript or vision supports them."
            )
    category_parts = []
    if context.get("primary_category"):
        category_parts.append(f"heuristic={context['primary_category']}")
    if context.get("ai_primary_category"):
        category_parts.append(f"ai={context['ai_primary_category']}")
    if category_parts:
        lines.append(f"- Moment category labels: {', '.join(category_parts)}")
    if context.get("ai_fine_labels"):
        lines.append(f"- AI fine labels: {', '.join(context['ai_fine_labels'][:5])}")
    if context.get("hook_phrases"):
        lines.append(f"- Spoken hooks/payoffs: {', '.join(context['hook_phrases'][:5])}")
    score_parts = []
    if context.get("detector_score") is not None:
        score_parts.append(f"detector={context['detector_score']}")
    if context.get("audio_score") is not None:
        score_parts.append(f"audio={context['audio_score']}")
    if context.get("scene_score") is not None:
        score_parts.append(f"scene={context['scene_score']}")
    if context.get("variance_score") is not None:
        score_parts.append(f"variance={context['variance_score']}")
    if context.get("quality_score") is not None:
        score_parts.append(f"quality={context['quality_score']}")
    if context.get("selection_quality_score") is not None:
        score_parts.append(f"selection_quality={context['selection_quality_score']}")
    if context.get("learned_quality_score") is not None:
        score_parts.append(f"learned={context['learned_quality_score']}")
    if context.get("multi_signal_ai_quality_score") is not None:
        score_parts.append(f"multi_signal={context['multi_signal_ai_quality_score']}")
    if context.get("multi_signal_ai_adjustment") is not None:
        score_parts.append(f"multi_signal_adjustment={context['multi_signal_ai_adjustment']}")
    if score_parts:
        lines.append(f"- Clip scores: {', '.join(score_parts)}")
    candidate_parts = []
    if context.get("candidate_rank") is not None:
        candidate_parts.append(f"candidate_rank={context['candidate_rank']}")
    if context.get("candidate_kind"):
        candidate_parts.append(f"candidate_kind={context['candidate_kind']}")
    if candidate_parts:
        lines.append(f"- Detector candidate: {', '.join(candidate_parts)}")
    if context.get("scene_detection_status"):
        lines.append(f"- Scene detection status: {context['scene_detection_status']}")
    if context.get("creator_title_context"):
        lines.append(f"- Creator-provided context: {context['creator_title_context']}")
    speech_policy = context.get("speech_policy") if isinstance(context.get("speech_policy"), dict) else {}
    if speech_policy:
        speech_parts = []
        if speech_policy.get("subtitle_policy"):
            speech_parts.append(f"subtitle_policy={speech_policy.get('subtitle_policy')}")
        if speech_policy.get("metadata_transcript_source"):
            speech_parts.append(f"metadata_transcript_source={speech_policy.get('metadata_transcript_source')}")
        speech_parts.append(f"selected_track_has_speech={bool(speech_policy.get('selected_track_has_speech'))}")
        speech_parts.append(f"selected_track_words={speech_policy.get('selected_track_word_count', 0)}")
        if speech_policy.get("analysis_word_count"):
            speech_parts.append(f"analysis_words={speech_policy.get('analysis_word_count')}")
        if speech_policy.get("selected_title"):
            speech_parts.append(f"selected_track={speech_policy.get('selected_title')}")
        if speech_policy.get("warning"):
            speech_parts.append(f"warning={speech_policy.get('warning')}")
        lines.append(f"- Speech policy: {'; '.join(speech_parts)}")
    if context.get("metadata_warning"):
        lines.append(f"- Metadata warning: {context.get('metadata_warning')}")
    learning = context.get("feedback_learning_context") if isinstance(context.get("feedback_learning_context"), dict) else {}
    if learning.get("enabled"):
        parts = []
        if learning.get("positive_terms"):
            parts.append(f"likes={', '.join(learning.get('positive_terms')[:5])}")
        if learning.get("negative_terms"):
            parts.append(f"dislikes={', '.join(learning.get('negative_terms')[:5])}")
        counts = (
            f"positive={learning.get('positive_feedback_count', 0)}, "
            f"negative={learning.get('negative_feedback_count', 0)}, "
            f"favorites={learning.get('favorite_count', 0)}, "
            f"run_memory={learning.get('run_learning_signal_count', 0)}, "
            f"montage_memory={learning.get('montage_learning_signal_count', 0)}"
        )
        parts.append(counts)
        if learning.get("guidance"):
            parts.append(f"guidance={learning.get('guidance')}")
        lines.append(f"- Creator feedback learning: {'; '.join(parts)}")
    ranker_parts = []
    for key in ("hook_points", "weak_points", "aftermath_points", "first_word_start"):
        if ranker.get(key) is not None:
            ranker_parts.append(f"{key}={ranker[key]}")
    if ranker.get("reject_reason"):
        ranker_parts.append(f"reject_reason={ranker['reject_reason']}")
    if ranker_parts:
        lines.append(f"- Ranker signals: {', '.join(ranker_parts)}")
    visual_parts = []
    if visual.get("labels"):
        visual_parts.append(f"labels={', '.join(visual['labels'][:5])}")
    for key in ("visual_energy", "possible_failure_score", "scenic_score", "ui_density"):
        if visual.get(key) is not None:
            visual_parts.append(f"{key}={visual[key]}")
    if visual_parts:
        lines.append(f"- Visual diagnostics: {', '.join(visual_parts)}")
    if ai_classification.get("status"):
        ai_parts = [
            f"status={ai_classification.get('status')}",
            f"provider={ai_classification.get('provider')}",
            f"primary={ai_classification.get('primary_category')}",
        ]
        if ai_classification.get("confidence") is not None:
            ai_parts.append(f"confidence={ai_classification.get('confidence')}")
        lines.append(f"- AI moment label: {', '.join(part for part in ai_parts if part)}")
    if multimodal.get("status"):
        vision_parts = [
            f"status={multimodal.get('status')}",
            f"provider={multimodal.get('provider')}",
            f"primary_visual={multimodal.get('primary_visual_label')}",
        ]
        if multimodal.get("visible_summary"):
            vision_parts.append(f"summary={multimodal.get('visible_summary')}")
        if multimodal.get("visual_labels"):
            vision_parts.append(f"labels={', '.join(multimodal.get('visual_labels')[:5])}")
        if multimodal.get("detected_events"):
            vision_parts.append(f"events={', '.join(multimodal.get('detected_events')[:3])}")
        if multimodal.get("title_hooks"):
            vision_parts.append(f"title_hooks={', '.join(multimodal.get('title_hooks')[:3])}")
        if multimodal.get("confidence") is not None:
            vision_parts.append(f"confidence={multimodal.get('confidence')}")
        lines.append(f"- Vision model analysis: {'; '.join(part for part in vision_parts if part)}")
    multi_signal_ai = context.get("multi_signal_ai") if isinstance(context.get("multi_signal_ai"), dict) else {}
    if multi_signal_ai.get("ranking_enabled") or multi_signal_ai.get("selection_delta"):
        parts = []
        if multi_signal_ai.get("selection_delta"):
            parts.append(f"selection_delta={multi_signal_ai.get('selection_delta')}")
        if multi_signal_ai.get("rank_delta") is not None:
            parts.append(f"rank_delta={multi_signal_ai.get('rank_delta')}")
        signals = multi_signal_ai.get("signals") if isinstance(multi_signal_ai.get("signals"), dict) else {}
        strongest = [
            f"{key}={value}"
            for key, value in sorted(signals.items(), key=lambda kv: abs(float(kv[1] or 0.0)), reverse=True)[:4]
            if value is not None
        ]
        if strongest:
            parts.append(f"signals={', '.join(strongest)}")
        if parts:
            lines.append(f"- Combined AI selection signals: {'; '.join(parts)}")
    time_parts = []
    for key in ("start", "end", "duration", "peak_time"):
        if timing.get(key) is not None:
            time_parts.append(f"{key}={timing[key]}")
    if time_parts:
        lines.append(f"- Clip timing: {', '.join(time_parts)}")
    if context.get("word_count") is not None:
        lines.append(f"- Final subtitle word count: {context['word_count']}")
    if context.get("context_sentence"):
        lines.append(f"- Metadata context: {context['context_sentence']}")
    return lines


def _build_description_prompt(
    title: str,
    transcript: str,
    game_title: str | None = None,
    clip_context: dict | None = None,
) -> str:
    context = summarize_clip_context(transcript, game_title, clip_context)
    analysis_lines = [
        line for line in _analysis_prompt_lines(context)
        if not line.startswith("- Metadata context:")
    ]
    analysis_block = "\n".join(analysis_lines) or "- No structured analysis available."
    game_line = f"Game: {game_title or context.get('game_title') or 'Unknown game'}"
    return (
        "Write the generated part of a YouTube Shorts description for one gameplay clip.\n"
        f"{game_line}\n"
        f"Title: {_prompt_safe_text(_clean_base_title(title), limit=120)}\n\n"
        "Use the transcript, detector/ranker summary, game knowledge, creator feedback hints, and vision summary if present.\n"
        "Treat the transcript as the selected creator-commentary transcript. Do not describe game/NPC dialogue as creator commentary.\n"
        "If speech policy says no selected creator speech, write only from verified game/visual/context facts and keep it neutral.\n"
        "Do not mention AI, analysis, detector, ranker, scores, metadata, captions, subtitles, or that the clip was selected.\n"
        "Do not include hashtags, links, calls to action, channel branding, or the custom footer.\n"
        "Do not quote the prompt or explain your reasoning.\n"
        "Make it sound like a creator wrote it, not like software labeling a clip.\n"
        "Keep it specific, natural, and short: one or two sentences, 120-240 characters total.\n"
        "Return ONLY valid JSON in this shape: {\"description\":\"...\"}\n\n"
        f"Clip analysis:\n{analysis_block}\n\n"
        f'Transcript preview: "{_prompt_safe_text(transcript, limit=900)}"'
    )


def _clean_generated_description(value, *, title: str | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" \"'\n\t")
    text = re.sub(r"^(description|caption)\s*:\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"#\w+", "", text).strip()
    if not text:
        return ""
    base_title = _clean_base_title(title or "")
    if base_title and _text_echoes_title(text, base_title):
        title_pattern = re.escape(base_title)
        remainder = re.sub(
            rf"^{title_pattern}\s*[\-:–—.]?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        if remainder and not _text_echoes_title(remainder, base_title):
            text = remainder
        else:
            return ""
    lower = text.lower()
    blocked = (
        "detector",
        "ranker",
        "analysis",
        "metadata",
        "selected from",
        "selected for",
        "clip was selected",
        "story or lore moment",
        "high-energy",
        "chase/panic",
        "moment built around",
        "moment with narrative context",
        "action, panic, or reaction cues",
        "grounded by",
        "this moment is",
        "speech policy",
        "selected creator speech",
        "selected creator-commentary",
        "return only",
        "valid json",
        "youtube shorts description",
    )
    if any(term in lower for term in blocked):
        return ""
    if len(text) > 260:
        text = text[:260].rstrip()
        sentence_end = max(text.rfind("."), text.rfind("!"), text.rfind("?"))
        if sentence_end >= 120:
            text = text[: sentence_end + 1]
        else:
            words = text.split()
            text = ""
            for word in words:
                candidate = f"{text} {word}".strip() if text else word
                if len(candidate) > 240:
                    break
                text = candidate
    return text.strip()


def _text_echoes_title(text: str | None, title: str | None) -> bool:
    clean_text = _normal_text(_clean_base_title(str(text or "")))
    clean_title = _normal_text(_clean_base_title(str(title or "")))
    if not clean_text or not clean_title or clean_title == "gaming moment":
        return False
    if clean_text == clean_title or clean_text.startswith(clean_title + " "):
        return True
    text_words = set(clean_text.split())
    title_words = [word for word in clean_title.split() if len(word) > 2]
    if len(title_words) < 4:
        return False
    overlap = sum(1 for word in title_words if word in text_words)
    return overlap / max(1, len(title_words)) >= 0.88 and len(clean_text.split()) <= len(title_words) + 4


def _description_context_line(
    title: str,
    game_title: str | None = None,
    clip_context: dict | None = None,
) -> str:
    context = summarize_clip_context(None, game_title, clip_context)
    sentence = context.get("context_sentence", "")
    if not sentence:
        return ""
    return sentence


def _context_sentence(context: dict, game_title: str | None) -> str:
    game = game_title or "gameplay"
    moment_type = context.get("moment_type")
    hook = context.get("hook_phrases", [""])[0] if context.get("hook_phrases") else ""
    multimodal = context.get("multimodal_analysis") if isinstance(context.get("multimodal_analysis"), dict) else {}
    game_knowledge = context.get("game_knowledge") if isinstance(context.get("game_knowledge"), dict) else {}
    if game_knowledge.get("label"):
        game = game_knowledge.get("label") or game
    if (
        multimodal.get("status") == "ok"
        and multimodal.get("visible_summary")
        and _float_or_zero(multimodal.get("confidence")) >= 0.62
    ):
        summary = str(multimodal.get("visible_summary") or "").strip()
        return f"{game} gets tense here: {summary[:180].rstrip('.') }."
    if moment_type == "chase/panic":
        if hook:
            if len(hook.split()) >= 2:
                return f"{game} turns into a close-call chase once \"{hook}\" hits."
            return f"{game} turns tense fast as the escape starts going wrong."
        return f"{game} turns tense fast as the chase pressure takes over."
    if moment_type == "combat/fight":
        return f"{game} drops straight into a fight with the pressure turned up."
    if moment_type == "high-energy gameplay":
        return f"{game} turns loud and chaotic here, with the reaction carrying the clip."
    if moment_type == "death/failure":
        return f"{game} gets rough here, with the danger landing right at the payoff."
    if moment_type == "funny failure":
        return f"{game} goes sideways in the kind of way that only gets funnier after it happens."
    if moment_type == "exploration/setup":
        return f"{game} slows down for a setup beat before the next problem shows itself."
    if moment_type == "tutorial/explainer":
        return f"A quick {game} explanation with the useful part kept front and center."
    if moment_type == "commentary/review":
        return f"{game} gets a little creator commentary here, with the reaction doing the heavy lifting."
    if moment_type == "lore/story":
        return f"{game}'s story takes a strange turn here, right in the middle of the darkness."
    if moment_type == "cinematic/dialogue":
        return f"{game} pauses on an in-game dialogue beat, with the story carrying the moment."
    if moment_type == "atmosphere/visual":
        return f"{game} leans into the mood here, letting the atmosphere do the work."
    return ""


def _infer_moment_type(
    normal: str,
    ranker: dict,
    matched_phrases: list[str],
    clip_context: dict | None = None,
) -> str:
    clip_context = clip_context if isinstance(clip_context, dict) else {}
    categories = (
        clip_context.get("moment_categories")
        if isinstance(clip_context.get("moment_categories"), dict)
        else {}
    )
    ai_classification = (
        clip_context.get("ai_moment_classification")
        if isinstance(clip_context.get("ai_moment_classification"), dict)
        else {}
    )
    phrase_set = set(matched_phrases)
    hook_points = _float_or_zero(ranker.get("hook_points"))
    weak_points = _float_or_zero(ranker.get("weak_points"))
    aftermath_points = _float_or_zero(ranker.get("aftermath_points"))
    for rule in MOMENT_SIGNAL_RULES:
        if any(phrase in phrase_set for phrase in rule["phrases"]):
            return rule["type"]
    ai_primary = str(ai_classification.get("primary_category") or "").strip()
    if ai_primary and _float_or_zero(ai_classification.get("confidence")) >= 0.55:
        mapped = _moment_type_from_category(ai_primary)
        if mapped != "general gameplay":
            return mapped
    primary = str(clip_context.get("primary_category") or categories.get("primary") or "").strip()
    mapped = _moment_type_from_category(primary)
    if mapped != "general gameplay":
        return mapped
    if hook_points >= 4 and any(word in normal for word in ("run", "behind", "hide", "scary", "please")):
        return "chase/panic"
    if any(word in normal for word in ("boss", "fight", "kill", "hit", "shoot")):
        return "combat/fight"
    if aftermath_points > weak_points and aftermath_points >= 2:
        return "funny failure"
    if weak_points >= 2:
        return "exploration/setup"
    return "general gameplay"


def _moment_type_from_category(category: str) -> str:
    return {
        "high_energy": "high-energy gameplay",
        "death_or_failure": "death/failure",
        "tutorial_or_explainer": "tutorial/explainer",
        "commentary_or_review": "commentary/review",
        "lore_or_story": "lore/story",
        "cinematic_dialogue": "cinematic/dialogue",
        "atmosphere_or_visual": "atmosphere/visual",
        "low_value": "exploration/setup",
    }.get(str(category or "").strip(), "general gameplay")


def _matching_signal_phrases(normal: str) -> list[str]:
    if not normal:
        return []
    padded = f" {normal} "
    matched = []
    seen = set()
    for rule in MOMENT_SIGNAL_RULES:
        for phrase in rule["phrases"]:
            phrase_norm = _normal_text(phrase)
            if phrase_norm and f" {phrase_norm} " in padded and phrase_norm not in seen:
                matched.append(phrase_norm)
                seen.add(phrase_norm)
    return matched


def _normal_text(text: str | None) -> str:
    text = (text or "").lower().replace("'", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _round_or_none(value):
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _int_or_zero(value) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _float_or_zero(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def list_ollama_models() -> list[str]:
    """Return available Ollama models, or empty list if unavailable."""
    try:
        data = _ollama_tags()
        if not data:
            return []
        return [m.get("name", "") for m in data.get("models", []) if isinstance(m, dict) and m.get("name")]
    except Exception:
        return []


def ollama_version() -> str:
    """Return the local Ollama version if the service exposes it."""
    return _ollama_version()


def ollama_status(model: str = DEFAULT_MODEL) -> dict:
    """Return Ollama status without pulling/downloading anything."""
    running = _ollama_available()
    models = list_ollama_models() if running else []
    model_ready = model in models or f"{model}:latest" in models
    return {
        "running": running,
        "model": model,
        "model_ready": model_ready,
        "models": models,
        "using_ollama": bool(running and model_ready),
        "version": ollama_version() if running else "",
        "download_url": OLLAMA_DOWNLOAD_URL,
        "windows_docs_url": OLLAMA_WINDOWS_DOCS_URL,
    }
