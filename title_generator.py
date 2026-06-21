"""Lightweight title generator using a local Ollama model.

Falls back to a simple extraction heuristic if Ollama is unavailable,
so this never blocks the pipeline.
"""

import json
import re
import urllib.request
import urllib.error

# Default model — 3b is the sweet spot for creative titles (~2GB RAM)
DEFAULT_MODEL = "qwen2.5:3b"
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_DOWNLOAD_URL = "https://ollama.com/download/windows"
OLLAMA_WINDOWS_DOCS_URL = "https://docs.ollama.com/windows"
OLLAMA_INSTALL_SCRIPT_URL = "https://ollama.com/install.ps1"
TIMEOUT = 30  # seconds per title request
YOUTUBE_TAG_LIMIT = 500
YOUTUBE_TAG_TARGET = YOUTUBE_TAG_LIMIT - 100
DEFAULT_VIDEO_CATEGORY_ID = "20"  # Gaming
TITLE_CONTEXT_SCHEMA_VERSION = 1


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
        # Long timeout — small models like qwen2.5:0.5b are ~400MB
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
    parts = [base_title]
    if context_line:
        parts.append(context_line)
    return "\n\n".join(part for part in parts if part)


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
    generated = (generated_text or generated_description_body(title, game_title, clip_context)).strip()
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
    tags = []
    if game:
        tags.extend([game, f"{game} gameplay", f"{game} shorts", f"{game} clips"])
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
        "streamer moments",
        "live stream clips",
        "funny gaming moments",
        "scary gaming moments",
        "horror gaming",
        "scary game",
        "creepy game",
        "survival horror",
        "horror shorts",
        "jump scare",
        "chase scene",
        "panic moment",
        "gaming reaction",
        "lets play",
        "playthrough",
        "vertical gaming",
        "game clips",
    ])
    if any(word in transcript_text for word in ("run", "behind", "hide", "chase")):
        tags.extend(["chase gameplay", "running scared", "close call"])
    if any(word in transcript_text for word in ("boss", "fight", "kill", "hit")):
        tags.extend(["boss fight", "combat gameplay", "intense fight"])
    if any(word in transcript_text for word in ("scary", "oh my god", "what", "please")):
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
    text = transcript or clip_context.get("transcript") or ""
    normal = _normal_text(text)
    matched = _matching_signal_phrases(normal)
    moment_type = _infer_moment_type(normal, ranker, matched)

    resolved_game = (game_title or clip_context.get("game_title") or "").strip()
    summary = {
        "schema_version": TITLE_CONTEXT_SCHEMA_VERSION,
        "game_title": resolved_game,
        "moment_type": moment_type,
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
        "quality_rank": clip_context.get("quality_rank"),
        "word_count": clip_context.get("word_count"),
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
        "Prefer the strongest spoken hook/payoff over generic clipbait.\n\n"
        "RULES:\n"
        "- Max 70 characters before hashtags\n"
        "- No quotes, no hashtags, no emojis\n"
        "- Make it specific to the game moment, enemy, objective, or reaction\n"
        "- Do not invent enemy names, locations, weapons, or mechanics not present in the transcript or analysis\n"
        "- Avoid generic titles like 'This Changes Everything' unless the transcript truly supports it\n"
        "- Use curiosity, danger, panic, funny failure, or a clear payoff\n"
        "- Good examples: 'Alan Wake Had Me Running For My Life', "
        "'This Chase Got Way Too Close', 'This Boss Fight Got Personal'\n\n"
        f"Clip analysis from detector/ranker:\n{analysis_block}\n\n"
        f'Transcript: "{transcript[:900]}"\n\n'
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
            title = _clean_base_title(data.get("response", ""))
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
    lines = [
        f"- Moment type: {context.get('moment_type') or 'general gameplay'}",
    ]
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
    ranker_parts = []
    for key in ("hook_points", "weak_points", "aftermath_points", "first_word_start"):
        if ranker.get(key) is not None:
            ranker_parts.append(f"{key}={ranker[key]}")
    if ranker.get("reject_reason"):
        ranker_parts.append(f"reject_reason={ranker['reject_reason']}")
    if ranker_parts:
        lines.append(f"- Ranker signals: {', '.join(ranker_parts)}")
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
    if moment_type == "chase/panic":
        if hook:
            return f"A tense {game} chase/panic moment built around \"{hook}\"."
        return f"A tense {game} chase/panic moment pulled from the strongest spoken hook."
    if moment_type == "combat/fight":
        return f"An intense {game} fight moment picked from the clip's strongest action beat."
    if moment_type == "funny failure":
        return f"A funny {game} failure moment selected from the clip's clearest payoff."
    if moment_type == "exploration/setup":
        return f"A {game} gameplay moment selected from the clearest spoken setup."
    return ""


def _infer_moment_type(normal: str, ranker: dict, matched_phrases: list[str]) -> str:
    phrase_set = set(matched_phrases)
    hook_points = _float_or_zero(ranker.get("hook_points"))
    weak_points = _float_or_zero(ranker.get("weak_points"))
    aftermath_points = _float_or_zero(ranker.get("aftermath_points"))
    for rule in MOMENT_SIGNAL_RULES:
        if any(phrase in phrase_set for phrase in rule["phrases"]):
            return rule["type"]
    if hook_points >= 4 and any(word in normal for word in ("run", "behind", "hide", "scary", "please")):
        return "chase/panic"
    if any(word in normal for word in ("boss", "fight", "kill", "hit", "shoot")):
        return "combat/fight"
    if aftermath_points > weak_points and aftermath_points >= 2:
        return "funny failure"
    if weak_points >= 2:
        return "exploration/setup"
    return "general gameplay"


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
        "install_script_url": OLLAMA_INSTALL_SCRIPT_URL,
    }
