"""Pick the best source audio stream for subtitles.

OBS recordings can have misleading track names or mixed routing. Instead of
trusting labels alone, sample the selected moments and let Whisper tell us
which stream actually contains intelligible commentary.
"""

from __future__ import annotations

import math
import re
import wave
from array import array
from pathlib import Path

from audio_streams import get_audio_streams
from clipper import extract_audio_clip
from speech_source_classifier import (
    classify_speech_source,
    positive_boost_block_reason as speech_source_positive_boost_block_reason,
)
from transcriber import transcribe_clips


VOICE_HINTS = ("microphone", "mic", "voice", "commentary", "narration")
GAME_HINTS = ("game", "desktop", "system", "capture", "output")
CREATOR_PHRASES = (
    ("oh my god", 3.0),
    ("what the", 2.5),
    ("right behind", 3.0),
    ("i think", 1.6),
    ("i need", 1.8),
    ("i have to", 1.8),
    ("we need", 1.8),
    ("we have to", 1.8),
    ("look at", 1.6),
    ("this game", 1.6),
    ("chat", 1.6),
    ("brother", 1.4),
    ("run", 1.3),
    ("wait", 1.2),
    ("please", 1.2),
)
GAME_SYSTEM_PHRASES = (
    ("checkpoint reached", 3.2),
    ("checkpoint", 2.0),
    ("objective updated", 3.2),
    ("objective", 2.4),
    ("press", 2.5),
    ("loading", 2.0),
    ("mission", 2.2),
    ("chapter", 2.0),
    ("collect", 1.8),
    ("saving", 2.0),
    ("you must", 2.2),
    ("incoming transmission", 3.0),
)
NATURAL_DIALOGUE_MARKERS = (
    ("i", 0.45),
    ("im", 0.75),
    ("ive", 0.7),
    ("me", 0.45),
    ("my", 0.45),
    ("we", 0.45),
    ("were", 0.65),
    ("lets", 0.85),
    ("wait", 0.95),
    ("what", 0.75),
    ("why", 0.7),
    ("how", 0.45),
    ("no", 0.55),
    ("yeah", 0.45),
    ("okay", 0.55),
    ("alright", 0.55),
    ("come on", 0.9),
    ("i dont know", 1.2),
    ("what am i", 1.15),
    ("where do", 0.9),
    ("do we", 0.75),
    ("we need", 0.9),
    ("i need", 0.9),
    ("i think", 0.85),
    ("that was", 0.65),
    ("this is", 0.55),
)
SCRIPTED_GAME_MARKERS = (
    ("press", 0.9),
    ("checkpoint", 1.1),
    ("objective", 1.1),
    ("mission", 0.85),
    ("loading", 0.8),
    ("chapter", 0.8),
    ("episode", 0.65),
    ("enter", 0.55),
    ("skip", 0.75),
    ("collect", 0.65),
    ("saving", 0.75),
    ("you must", 1.0),
    ("you have been", 1.0),
)
MUSIC_LYRIC_TERMS = {
    "bitch", "bitches", "hoe", "shawty", "diamonds", "diamond", "necklace",
    "ring", "wedding", "cocaine", "codeine", "lean", "perc", "perks",
    "molly", "drugs", "wasted", "gta", "money", "racks", "flexing",
    "stunting", "condom", "basement", "patients", "demonic", "medusa",
}
MUSIC_CONTEXT_MARKERS = (
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
LIVE_CREATOR_EXCEPTION_MARKERS = (
    ("no im joking", 3.0),
    ("no i'm joking", 3.0),
    ("what the hell", 2.4),
    ("i saved myself", 3.0),
    ("i had to use", 2.0),
    ("i just used", 1.8),
    ("we havent used", 1.8),
    ("we haven't used", 1.8),
    ("this game", 1.8),
    ("rifle", 1.5),
    ("ammo", 1.5),
    ("battery", 1.7),
    ("batteries", 1.7),
    ("flashlight", 1.6),
    ("headlamp", 1.6),
)
SELECTION_SCHEMA_VERSION = 1
_LAST_SPEECH_STREAM_SELECTION: dict = {}
MIC_CREATOR_PREFERENCE_MAX_BONUS = 34.0


def select_speech_stream(
    video_path: Path,
    moments: list[dict],
    model_size: str,
    language: str | None,
    scratch_dir: Path,
    max_samples: int = 6,
    sample_seconds: int = 20,
) -> int | None:
    """Return the audio stream ordinal that looks best for subtitles."""
    global _LAST_SPEECH_STREAM_SELECTION
    streams = get_audio_streams(video_path)
    if not streams:
        _LAST_SPEECH_STREAM_SELECTION = _selection_report(
            status="no_audio_streams",
            streams=[],
            selected_stream=None,
            selected_reason="no_audio_streams",
        )
        return None
    if len(streams) == 1:
        selected = int(streams[0]["ordinal"])
        profile = score_stream_profile(
            selected,
            streams[0].get("title") or f"0:a:{selected}",
            "",
            sample_hits=0,
        )
        _LAST_SPEECH_STREAM_SELECTION = _selection_report(
            status="single_stream",
            streams=[profile],
            selected_stream=selected,
            selected_reason="single_audio_stream",
            confidence=1.0,
        )
        return selected

    samples = _candidate_sample_windows(
        moments,
        max_samples=max_samples,
        sample_seconds=sample_seconds,
    )
    if not samples:
        fallback = _fallback_stream_from_metadata(streams)
        _LAST_SPEECH_STREAM_SELECTION = _selection_report(
            status="fallback",
            streams=[
                score_stream_profile(
                    int(stream["ordinal"]),
                    stream.get("title") or f"0:a:{stream['ordinal']}",
                    "",
                    sample_hits=0,
                )
                for stream in streams
            ],
            selected_stream=fallback,
            selected_reason="no_candidate_samples",
            confidence=0.25 if fallback is not None else 0.0,
        )
        return fallback

    scratch_dir.mkdir(exist_ok=True)
    print("[audio] Inspecting audio streams for subtitle speech...")

    scored = []
    for stream in streams:
        ordinal = int(stream["ordinal"])
        title = stream.get("title") or f"0:a:{ordinal}"
        words_total = 0
        chars_total = 0
        sample_hits = 0
        sampled_seconds = 0.0
        transcript_parts: list[str] = []
        acoustic_profiles: list[dict] = []
        extracted_samples: list[tuple[Path, float]] = []

        for sample_idx, moment in enumerate(samples):
            start = moment["_sample_start"]
            end = moment["_sample_end"]
            sampled_seconds += max(0, end - start)

            wav = scratch_dir / f"_speech_stream_probe_a{ordinal}_{sample_idx}.wav"
            try:
                if not extract_audio_clip(video_path, start, end, wav, audio_stream=ordinal):
                    continue
                extracted_samples.append((wav, end - start))
            except Exception:
                try:
                    wav.unlink(missing_ok=True)
                except OSError:
                    pass

        probe_wavs = [wav for wav, _duration in extracted_samples]
        transcribed_samples = transcribe_clips(probe_wavs, model_size=model_size, language=language) if probe_wavs else []
        for (wav, duration), words in zip(extracted_samples, transcribed_samples):
            try:
                if words:
                    sample_hits += 1
                    words_total += len(words)
                    transcript = " ".join(str(w.get("text", "")).strip() for w in words).strip()
                    transcript_parts.append(transcript)
                    chars_total += sum(len(w.get("text", "")) for w in words)
                    acoustic_profiles.append(analyze_audio_bed(wav, words, duration=duration))
            finally:
                try:
                    wav.unlink(missing_ok=True)
                except OSError:
                    pass

        scored.append(
            score_stream_profile(
                ordinal,
                title,
                " ".join(transcript_parts),
                words_total=words_total,
                chars_total=chars_total,
                sample_hits=sample_hits,
                sampled_seconds=sampled_seconds,
                acoustic_profile=_merge_acoustic_profiles(acoustic_profiles),
            )
        )

    for row in scored:
        print(
            "[audio] Stream score "
            f"0:a:{row['ordinal']} ({row['title']}): "
            f"{row['words']} words across {row['hits']} samples, "
            f"score={row['selection_score']:.1f}, confidence={row['confidence']:.2f}"
        )

    selection = choose_stream_from_profiles(scored, fallback_stream=_fallback_stream_from_metadata(streams))
    _LAST_SPEECH_STREAM_SELECTION = selection
    selected = selection.get("selected_stream")
    if selected is not None and selection.get("selected_words", 0) > 0:
        print(
            "[audio] Subtitle speech stream selected: "
            f"0:a:{selected} ({selection.get('selected_title')}) "
            f"reason={selection.get('selected_reason')} confidence={selection.get('confidence')}"
        )
        return int(selected)

    print(f"[audio] No speech found during stream scan; fallback stream={selected}")
    return int(selected) if selected is not None else None


def get_last_speech_stream_selection() -> dict:
    """Return the last speech-stream selection report."""
    return dict(_LAST_SPEECH_STREAM_SELECTION)


def profile_words_for_stream(
    ordinal: int,
    title: str,
    words: list[dict],
    *,
    wav_path: Path | None = None,
    sampled_seconds: float = 0.0,
) -> dict:
    """Build a stream profile from one transcription result."""
    transcript = " ".join(str(w.get("text", "")).strip() for w in words if isinstance(w, dict)).strip()
    acoustic_profile = analyze_audio_bed(wav_path, words, duration=sampled_seconds) if wav_path else {}
    return score_stream_profile(
        ordinal,
        title,
        transcript,
        words_total=len(words or []),
        chars_total=sum(len(str(w.get("text", ""))) for w in words if isinstance(w, dict)),
        sample_hits=1 if words else 0,
        sampled_seconds=sampled_seconds,
        acoustic_profile=acoustic_profile,
    )


def analyze_audio_bed(wav_path: Path | None, words: list[dict], *, duration: float = 0.0) -> dict:
    """Estimate whether speech sits on a constant background/game audio bed.

    The transcription WAV is mono 16 kHz PCM. We compare RMS energy during
    word intervals against gaps. A high gap-to-speech ratio is a weak clue
    that the track is game/system audio or mixed audio rather than close mic.
    """
    if not wav_path:
        return {"status": "missing"}
    try:
        path = Path(wav_path)
        if not path.exists() or path.stat().st_size <= 44:
            return {"status": "missing"}
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            frame_rate = handle.getframerate()
            frame_count = handle.getnframes()
            frames = handle.readframes(frame_count)
        if channels != 1 or sample_width != 2 or frame_rate <= 0 or not frames:
            return {"status": "unsupported"}
        samples = array("h")
        samples.frombytes(frames)
        if not samples:
            return {"status": "empty"}
        total_sq = sum(float(sample) * float(sample) for sample in samples)
        total_count = len(samples)
        intervals = []
        for word in words or []:
            if not isinstance(word, dict):
                continue
            try:
                start = max(0.0, float(word.get("start", 0.0)))
                end = max(start, float(word.get("end", start)))
            except (TypeError, ValueError):
                continue
            start_idx = max(0, min(total_count, int(start * frame_rate)))
            end_idx = max(start_idx, min(total_count, int(end * frame_rate)))
            if end_idx > start_idx:
                intervals.append((start_idx, end_idx))
        intervals.sort()
        merged = []
        for start_idx, end_idx in intervals:
            if not merged or start_idx > merged[-1][1]:
                merged.append([start_idx, end_idx])
            else:
                merged[-1][1] = max(merged[-1][1], end_idx)
        speech_sq = 0.0
        speech_count = 0
        for start_idx, end_idx in merged:
            segment = samples[start_idx:end_idx]
            speech_count += len(segment)
            speech_sq += sum(float(sample) * float(sample) for sample in segment)
        gap_count = max(0, total_count - speech_count)
        gap_sq = max(0.0, total_sq - speech_sq)
        total_rms = math.sqrt(total_sq / max(1, total_count)) / 32768.0
        speech_rms = math.sqrt(speech_sq / max(1, speech_count)) / 32768.0 if speech_count else 0.0
        gap_rms = math.sqrt(gap_sq / max(1, gap_count)) / 32768.0 if gap_count else 0.0
        gap_to_speech = gap_rms / max(0.0001, speech_rms)
        gap_to_total = gap_rms / max(0.0001, total_rms)
        speech_coverage = speech_count / max(1, total_count)
        # A constant music/ambience bed tends to keep gap RMS high even when
        # no words are active. Keep this bounded and weak; it is only a clue.
        game_bed_score = _clamp((gap_to_speech - 0.28) / 0.82, 0.0, 1.0) * 0.7
        game_bed_score += _clamp((gap_to_total - 0.42) / 0.58, 0.0, 1.0) * 0.3
        return {
            "status": "ok",
            "duration": round(float(duration or total_count / frame_rate), 3),
            "total_rms": round(total_rms, 5),
            "speech_rms": round(speech_rms, 5),
            "gap_rms": round(gap_rms, 5),
            "gap_to_speech_ratio": round(gap_to_speech, 4),
            "gap_to_total_ratio": round(gap_to_total, 4),
            "speech_coverage": round(speech_coverage, 4),
            "game_bed_score": round(_clamp(game_bed_score, 0.0, 1.0), 4),
            "_total_sq": total_sq,
            "_speech_sq": speech_sq,
            "_gap_sq": gap_sq,
            "_total_count": total_count,
            "_speech_count": speech_count,
            "_gap_count": gap_count,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:120]}


def score_stream_profile(
    ordinal: int,
    title: str,
    transcript: str,
    *,
    words_total: int | None = None,
    chars_total: int | None = None,
    sample_hits: int = 0,
    sampled_seconds: float = 0.0,
    acoustic_profile: dict | None = None,
) -> dict:
    """Build a diagnostic score row for one audio stream."""
    title = str(title or f"0:a:{ordinal}")
    transcript = re.sub(r"\s+", " ", str(transcript or "")).strip()
    if words_total is None:
        words_total = len(transcript.split())
    if chars_total is None:
        chars_total = len(transcript)
    title_lower = title.lower()
    voice_hints = [hint for hint in VOICE_HINTS if hint in title_lower]
    game_hints = [hint for hint in GAME_HINTS if hint in title_lower]
    creator_score = _weighted_score(transcript, CREATOR_PHRASES)
    game_score = _weighted_score(transcript, GAME_SYSTEM_PHRASES)
    natural_score = _natural_dialogue_score(transcript)
    scripted_score = _scripted_game_dialogue_score(transcript)
    lyric_profile = _lyric_likelihood_profile(transcript)
    lyric_likelihood = float(lyric_profile.get("lyric_likelihood") or 0.0)
    creator_exception_score = float(lyric_profile.get("creator_exception_score") or 0.0)
    acoustic = _public_acoustic_profile(acoustic_profile)
    game_bed_score = float(acoustic.get("game_bed_score") or 0.0)
    phrase_total = creator_score + game_score
    density = float(words_total) / max(1.0, float(sampled_seconds or 0.0))
    creator_likeness = _creator_likeness_score(
        words_total=words_total,
        voice_hints=voice_hints,
        game_hints=game_hints,
        creator_score=creator_score,
        game_score=game_score,
        natural_score=natural_score,
        scripted_score=scripted_score,
        game_bed_score=game_bed_score,
        sample_hits=sample_hits,
    )
    if lyric_likelihood >= 0.45:
        creator_likeness = _clamp(
            creator_likeness - lyric_likelihood * 0.34 + creator_exception_score * 0.12,
            0.0,
            1.0,
        )
    selection_score = float(words_total)
    mic_creator_preference_bonus = 0.0
    if words_total:
        if voice_hints and lyric_likelihood < 0.55:
            selection_score += 12.0
            mic_creator_preference_bonus = _mic_creator_preference_bonus(
                words_total=words_total,
                creator_likeness=creator_likeness,
                natural_score=natural_score,
                creator_score=creator_score,
                game_score=game_score,
                scripted_score=scripted_score,
                game_bed_score=game_bed_score,
                lyric_likelihood=lyric_likelihood,
                creator_exception_score=creator_exception_score,
                sample_hits=sample_hits,
            )
            selection_score += mic_creator_preference_bonus
        elif voice_hints:
            selection_score += 2.0
        if game_hints:
            selection_score -= 8.0
        if lyric_likelihood >= 0.45 and creator_exception_score < 0.62:
            selection_score -= min(180.0, float(words_total or 0) * 0.78 * lyric_likelihood + 24.0)
    confidence = _profile_confidence(
        words_total=words_total,
        voice_hints=voice_hints,
        game_hints=game_hints,
        creator_score=creator_score,
        game_score=game_score,
        natural_score=natural_score,
        game_bed_score=game_bed_score,
        sample_hits=sample_hits,
    )
    profile = {
        "ordinal": int(ordinal),
        "title": title,
        "words": int(words_total or 0),
        "chars": int(chars_total or 0),
        "hits": int(sample_hits or 0),
        "sampled_seconds": round(float(sampled_seconds or 0.0), 3),
        "word_density": round(density, 4),
        "voice_title_hints": voice_hints,
        "game_title_hints": game_hints,
        "creator_phrase_score": round(float(creator_score), 4),
        "game_system_phrase_score": round(float(game_score), 4),
        "natural_dialogue_score": round(float(natural_score), 4),
        "scripted_game_score": round(float(scripted_score), 4),
        "creator_phrase_ratio": round(creator_score / phrase_total, 4) if phrase_total else 0.0,
        "game_system_phrase_ratio": round(game_score / phrase_total, 4) if phrase_total else 0.0,
        "acoustic_profile": acoustic,
        "acoustic_game_bed_score": round(float(game_bed_score), 4),
        "lyric_likelihood": round(float(lyric_likelihood), 4),
        "creator_exception_score": round(float(creator_exception_score), 4),
        "lyric_signals": lyric_profile.get("signals", {}),
        "creator_likeness_score": round(float(creator_likeness), 4),
        "mic_creator_preference_bonus": round(float(mic_creator_preference_bonus), 4),
        "selection_score": round(float(selection_score), 4),
        "diagnostic_score": 0.0,
        "confidence": round(float(confidence), 4),
        "transcript_preview": transcript[:220],
    }
    speech_source = classify_speech_source(
        transcript=transcript,
        stream_profile=profile,
        subtitle_policy="creator",
    )
    creator_probability = float(speech_source.get("creator_probability") or 0.0)
    game_probability = float(speech_source.get("game_or_npc_probability") or 0.0)
    music_probability = float(speech_source.get("music_or_lyrics_probability") or 0.0)
    source_margin = creator_probability - max(game_probability, music_probability)
    source_selection_adjustment = max(-18.0, min(14.0, source_margin * 18.0))
    if speech_source_positive_boost_block_reason(speech_source):
        source_selection_adjustment = min(source_selection_adjustment, -8.0)
    selection_score += source_selection_adjustment
    diagnostic_score = (
        selection_score
        + (natural_score * 1.2)
        + (creator_score * 0.55)
        - (scripted_score * 0.85)
        - (game_score * 0.45)
        - (game_bed_score * 7.0)
        - (lyric_likelihood * 28.0)
        + (creator_exception_score * 8.0)
    )
    profile.update(
        {
            "speech_source": speech_source,
            "speech_source_selection_adjustment": round(float(source_selection_adjustment), 4),
            "selection_score": round(float(selection_score), 4),
            "diagnostic_score": round(float(diagnostic_score), 4),
        }
    )
    return profile


def choose_stream_from_profiles(
    stream_profiles: list[dict],
    *,
    fallback_stream: int | None = None,
) -> dict:
    """Return a selection report from already-scored stream profiles."""
    profiles = [
        _normalize_profile(profile)
        for profile in stream_profiles
        if isinstance(profile, dict) and profile.get("ordinal") is not None
    ]
    profiles.sort(key=lambda row: row["selection_score"], reverse=True)
    if not profiles:
        return _selection_report(
            status="no_profiles",
            streams=[],
            selected_stream=fallback_stream,
            selected_reason="no_stream_profiles",
            confidence=0.0,
        )

    best = profiles[0]
    runner_up = profiles[1] if len(profiles) > 1 else None
    if best.get("words", 0) <= 0:
        selected = fallback_stream
        if selected is None:
            selected = best["ordinal"]
        selected_profile = next((row for row in profiles if row["ordinal"] == selected), best)
        return _selection_report(
            status="fallback",
            streams=profiles,
            selected_stream=selected,
            runner_up_stream=runner_up.get("ordinal") if runner_up else None,
            selected_reason="fallback_no_speech",
            confidence=0.2 if selected is not None else 0.0,
            selected_profile=selected_profile,
            runner_up_profile=runner_up,
        )

    reason = _selection_reason(best, runner_up)
    confidence = _selection_confidence(best, runner_up)
    return _selection_report(
        status="ok",
        streams=profiles,
        selected_stream=best["ordinal"],
        runner_up_stream=runner_up.get("ordinal") if runner_up else None,
        selected_reason=reason,
        confidence=confidence,
        selected_profile=best,
        runner_up_profile=runner_up,
    )


def should_accept_alternate_stream(
    profile: dict,
    *,
    subtitle_policy: str = "creator",
) -> dict:
    """Decide whether retry should switch subtitles to an alternate stream."""
    profile = _normalize_profile(profile)
    policy = str(subtitle_policy or "creator").strip().lower()
    if policy not in {"creator", "all", "game"}:
        policy = "creator"
    if int(profile.get("words", 0)) <= 0:
        return _retry_acceptance(False, "no_transcribed_words", profile, policy)
    if policy == "all":
        return _retry_acceptance(True, "all_speech_policy", profile, policy)
    if policy == "game":
        return _retry_acceptance(True, "game_speech_policy", profile, policy)

    creator_likeness = float(profile.get("creator_likeness_score") or 0.0)
    game_bed_score = float(profile.get("acoustic_game_bed_score") or 0.0)
    has_voice_hint = bool(profile.get("voice_title_hints"))
    has_game_hint = bool(profile.get("game_title_hints"))
    natural = float(profile.get("natural_dialogue_score") or 0.0)
    scripted = float(profile.get("scripted_game_score") or 0.0)
    lyric_likelihood = float(profile.get("lyric_likelihood") or 0.0)
    creator_exception = float(profile.get("creator_exception_score") or 0.0)
    speech_source = profile.get("speech_source") if isinstance(profile.get("speech_source"), dict) else {}
    speech_block = speech_source_positive_boost_block_reason(speech_source, policy=policy)
    creator_probability = float(speech_source.get("creator_probability") or 0.0)
    game_probability = float(speech_source.get("game_or_npc_probability") or 0.0)
    music_probability = float(speech_source.get("music_or_lyrics_probability") or 0.0)

    if lyric_likelihood >= 0.62 and creator_exception < 0.58:
        return _retry_acceptance(False, "music_lyrics_not_creator_commentary", profile, policy)
    if game_bed_score >= 0.72 and not has_voice_hint:
        return _retry_acceptance(False, "background_bed_suggests_game_audio", profile, policy)
    if speech_block == "speech_source_music_or_lyrics":
        return _retry_acceptance(False, "source_confidence_music_or_lyrics", profile, policy)
    if speech_block == "speech_source_game_or_npc":
        return _retry_acceptance(False, "source_confidence_game_or_npc", profile, policy)
    if speech_block == "speech_source_weak_creator_evidence" and not has_voice_hint:
        return _retry_acceptance(False, "source_confidence_weak_creator", profile, policy)
    if not has_voice_hint:
        if creator_probability < 0.58:
            return _retry_acceptance(False, "alternate_lacks_creator_confidence", profile, policy)
        if game_probability >= 0.26 and game_probability > creator_probability - 0.34:
            return _retry_acceptance(False, "alternate_has_game_speech_risk", profile, policy)
        if music_probability >= 0.34 and music_probability > creator_probability - 0.30:
            return _retry_acceptance(False, "alternate_has_music_speech_risk", profile, policy)
    if has_game_hint and not has_voice_hint and creator_likeness < 0.62:
        return _retry_acceptance(False, "game_track_not_creator_like", profile, policy)
    if scripted > natural + 1.5 and creator_likeness < 0.58:
        return _retry_acceptance(False, "scripted_or_system_dialogue", profile, policy)
    if creator_likeness >= 0.46:
        return _retry_acceptance(True, "creator_like_alternate", profile, policy)
    if has_voice_hint and natural >= 1.8 and creator_likeness >= 0.38:
        return _retry_acceptance(True, "mic_hint_and_natural_dialogue", profile, policy)
    return _retry_acceptance(False, "not_creator_like_enough", profile, policy)


def _selection_report(
    *,
    status: str,
    streams: list[dict],
    selected_stream: int | None,
    selected_reason: str,
    confidence: float = 0.0,
    runner_up_stream: int | None = None,
    selected_profile: dict | None = None,
    runner_up_profile: dict | None = None,
) -> dict:
    selected_profile = selected_profile or next(
        (row for row in streams if row.get("ordinal") == selected_stream),
        None,
    )
    runner_up_profile = runner_up_profile or next(
        (row for row in streams if row.get("ordinal") == runner_up_stream),
        None,
    )
    return {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "status": status,
        "mode": "diagnostic_v2",
        "selection_impact": "stream_selection_only",
        "selected_stream": selected_stream,
        "selected_title": selected_profile.get("title") if selected_profile else None,
        "selected_words": selected_profile.get("words", 0) if selected_profile else 0,
        "selected_reason": selected_reason,
        "runner_up_stream": runner_up_stream,
        "runner_up_title": runner_up_profile.get("title") if runner_up_profile else None,
        "runner_up_words": runner_up_profile.get("words", 0) if runner_up_profile else 0,
        "confidence": round(max(0.0, min(1.0, float(confidence or 0.0))), 4),
        "stream_profiles": streams,
    }


def _normalize_profile(profile: dict) -> dict:
    title = str(profile.get("title") or f"0:a:{profile.get('ordinal')}")
    normalized = score_stream_profile(
        int(profile.get("ordinal")),
        title,
        str(profile.get("transcript_preview") or ""),
        words_total=int(profile.get("words") or 0),
        chars_total=int(profile.get("chars") or 0),
        sample_hits=int(profile.get("hits") or 0),
        sampled_seconds=float(profile.get("sampled_seconds") or 0.0),
    )
    for key, value in profile.items():
        if key not in {"ordinal", "title", "transcript_preview"}:
            normalized[key] = value
    normalized["selection_score"] = float(normalized.get("selection_score") or 0.0)
    return normalized


def _retry_acceptance(accepted: bool, reason: str, profile: dict, policy: str) -> dict:
    return {
        "schema_version": 1,
        "accepted": bool(accepted),
        "reason": reason,
        "subtitle_policy": policy,
        "stream": profile.get("ordinal"),
        "title": profile.get("title"),
        "words": profile.get("words", 0),
        "creator_likeness_score": profile.get("creator_likeness_score", 0.0),
        "natural_dialogue_score": profile.get("natural_dialogue_score", 0.0),
        "scripted_game_score": profile.get("scripted_game_score", 0.0),
        "acoustic_game_bed_score": profile.get("acoustic_game_bed_score", 0.0),
        "lyric_likelihood": profile.get("lyric_likelihood", 0.0),
        "creator_exception_score": profile.get("creator_exception_score", 0.0),
        "speech_source": profile.get("speech_source"),
        "speech_source_selection_adjustment": profile.get("speech_source_selection_adjustment", 0.0),
        "voice_title_hints": profile.get("voice_title_hints", []),
        "game_title_hints": profile.get("game_title_hints", []),
    }


def _selection_reason(best: dict, runner_up: dict | None) -> str:
    if float(best.get("lyric_likelihood") or 0.0) >= 0.45 and float(best.get("creator_exception_score") or 0.0) < 0.62:
        return "lyric_like_but_best_available"
    if best.get("voice_title_hints") and best.get("words", 0) > 0:
        if runner_up and runner_up.get("words", 0) > best.get("words", 0):
            if float(best.get("mic_creator_preference_bonus") or 0.0) > 0:
                return "mic_creator_signal_over_more_words"
            return "mic_title_hint_over_more_words"
        return "mic_title_hint_and_speech"
    source = best.get("speech_source") if isinstance(best.get("speech_source"), dict) else {}
    if (
        source.get("creator_safe")
        and float(best.get("speech_source_selection_adjustment") or 0.0) > 0
        and not best.get("game_title_hints")
    ):
        if runner_up and runner_up.get("words", 0) > best.get("words", 0):
            return "creator_source_confidence_over_more_words"
        return "creator_source_confidence"
    if best.get("creator_phrase_score", 0.0) > best.get("game_system_phrase_score", 0.0):
        return "creator_phrase_signal"
    if runner_up and best.get("words", 0) > runner_up.get("words", 0):
        return "more_whisper_words"
    return "best_whisper_stream_score"


def _selection_confidence(best: dict, runner_up: dict | None) -> float:
    score = 0.38
    score += min(0.24, float(best.get("words", 0)) / 90.0)
    score += min(0.14, float(best.get("hits", 0)) * 0.035)
    if best.get("voice_title_hints"):
        score += 0.12
    if best.get("creator_phrase_score", 0.0) > best.get("game_system_phrase_score", 0.0):
        score += 0.08
    if best.get("game_title_hints") and not best.get("voice_title_hints"):
        score -= 0.10
    lyric_likelihood = float(best.get("lyric_likelihood") or 0.0)
    creator_exception = float(best.get("creator_exception_score") or 0.0)
    if lyric_likelihood >= 0.45 and creator_exception < 0.62:
        score -= min(0.24, lyric_likelihood * 0.22)
    if runner_up:
        margin = float(best.get("selection_score", 0.0)) - float(runner_up.get("selection_score", 0.0))
        score += max(0.0, min(0.16, margin / 80.0))
        if abs(margin) < 6:
            score -= 0.08
    return max(0.0, min(1.0, score))


def _profile_confidence(
    *,
    words_total: int,
    voice_hints: list[str],
    game_hints: list[str],
    creator_score: float,
    game_score: float,
    natural_score: float,
    game_bed_score: float,
    sample_hits: int,
) -> float:
    score = 0.18
    score += min(0.32, float(words_total or 0) / 100.0)
    score += min(0.14, float(sample_hits or 0) * 0.035)
    if voice_hints:
        score += 0.16
    if game_hints and not voice_hints:
        score -= 0.10
    if creator_score > game_score:
        score += 0.10
    elif game_score > creator_score:
        score -= 0.08
    score += min(0.10, float(natural_score or 0.0) / 45.0)
    score -= min(0.14, float(game_bed_score or 0.0) * 0.14)
    return max(0.0, min(1.0, score))


def _fallback_stream_from_metadata(streams: tuple[dict, ...]) -> int | None:
    if not streams:
        return None
    for stream in streams:
        title = str(stream.get("title") or "").lower()
        if any(keyword in title for keyword in VOICE_HINTS):
            return int(stream["ordinal"])
    return int(streams[0]["ordinal"])


def _creator_likeness_score(
    *,
    words_total: int,
    voice_hints: list[str],
    game_hints: list[str],
    creator_score: float,
    game_score: float,
    natural_score: float,
    scripted_score: float,
    game_bed_score: float,
    sample_hits: int,
) -> float:
    score = 0.18
    score += min(0.18, float(words_total or 0) / 160.0)
    score += min(0.10, float(sample_hits or 0) * 0.025)
    score += min(0.34, float(natural_score or 0.0) / 22.0)
    score += min(0.08, float(creator_score or 0.0) / 28.0)
    if voice_hints:
        score += 0.18
    if game_hints and not voice_hints:
        score -= 0.16
    score -= min(0.18, float(game_score or 0.0) / 24.0)
    score -= min(0.16, float(scripted_score or 0.0) / 22.0)
    score -= min(0.22, float(game_bed_score or 0.0) * 0.22)
    return _clamp(score, 0.0, 1.0)


def _mic_creator_preference_bonus(
    *,
    words_total: int,
    creator_likeness: float,
    natural_score: float,
    creator_score: float,
    game_score: float,
    scripted_score: float,
    game_bed_score: float,
    lyric_likelihood: float,
    creator_exception_score: float,
    sample_hits: int,
) -> float:
    if int(words_total or 0) < 12 and int(sample_hits or 0) < 2:
        return 0.0
    if lyric_likelihood >= 0.45 and creator_exception_score < 0.62:
        return 0.0
    if game_bed_score >= 0.72:
        return 0.0
    if creator_likeness < 0.42:
        return 0.0
    if natural_score < 1.4 and creator_score < 2.4:
        return 0.0
    if scripted_score > natural_score + 2.5 and creator_score < 2.4:
        return 0.0
    bonus = 6.0
    bonus += min(15.0, creator_likeness * 18.0)
    bonus += min(9.0, natural_score * 1.15)
    bonus += min(5.0, creator_score * 0.45)
    bonus += min(3.0, float(sample_hits or 0) * 0.75)
    bonus -= min(8.0, game_score * 0.65)
    bonus -= min(7.0, game_bed_score * 7.0)
    return round(_clamp(bonus, 0.0, MIC_CREATOR_PREFERENCE_MAX_BONUS), 4)


def _natural_dialogue_score(text: str) -> float:
    normal = _normal_text(text)
    if not normal:
        return 0.0
    tokens = normal.split()
    token_count = max(1, len(tokens))
    score = _weighted_score(normal, NATURAL_DIALOGUE_MARKERS)
    first_person = sum(1 for token in tokens if token in {"i", "im", "ive", "me", "my", "we", "were", "us", "our"})
    questions = sum(1 for token in tokens if token in {"what", "why", "where", "how", "who"})
    reactions = sum(1 for token in tokens if token in {"wait", "no", "yeah", "okay", "alright", "please", "run"})
    score += min(3.0, first_person * 0.45)
    score += min(2.0, questions * 0.5)
    score += min(2.0, reactions * 0.35)
    short_spoken_ratio = sum(1 for token in tokens if len(token) <= 4) / token_count
    if short_spoken_ratio >= 0.55:
        score += 0.9
    if token_count >= 5 and len(set(tokens)) / token_count < 0.72:
        score += 0.6
    return round(float(score), 4)


def _scripted_game_dialogue_score(text: str) -> float:
    normal = _normal_text(text)
    if not normal:
        return 0.0
    tokens = normal.split()
    score = _weighted_score(normal, SCRIPTED_GAME_MARKERS)
    if len(tokens) >= 10:
        second_person = sum(1 for token in tokens if token in {"you", "your"})
        imperative = sum(1 for token in tokens if token in {"press", "go", "find", "collect", "reach", "follow", "return"})
        score += min(2.5, second_person * 0.25 + imperative * 0.45)
    return round(float(score), 4)


def _lyric_likelihood_profile(text: str) -> dict:
    normal = _normal_text(text)
    tokens = normal.split()
    token_count = len(tokens)
    if not tokens:
        return {
            "lyric_likelihood": 0.0,
            "creator_exception_score": 0.0,
            "signals": {},
        }
    lyric_hits = sum(1 for token in tokens if token in MUSIC_LYRIC_TERMS)
    lyric_vocab_score = _clamp(lyric_hits / max(3.0, token_count / 12.0), 0.0, 1.0)
    repetition = _repetition_profile(tokens)
    repetition_score = _clamp(
        0.45 * repetition["top_token_ratio"] * 5.0
        + 0.35 * repetition["repeated_bigram_ratio"] * 8.0
        + 0.20 * repetition["repeated_trigram_ratio"] * 10.0,
        0.0,
        1.0,
    )
    music_context_score = _clamp(_weighted_score(normal, MUSIC_CONTEXT_MARKERS) / 5.0, 0.0, 1.0)
    natural_score = _natural_dialogue_score(normal)
    creator_score = _weighted_score(normal, CREATOR_PHRASES)
    live_context_score = _clamp(
        (_weighted_score(normal, LIVE_CREATOR_EXCEPTION_MARKERS) / 6.0)
        + creator_score / 10.0
        + natural_score / 28.0,
        0.0,
        1.0,
    )
    creator_exception_score = _clamp(
        0.62 * live_context_score
        + 0.20 * _clamp(creator_score / 8.0, 0.0, 1.0)
        + 0.18 * _clamp(natural_score / 16.0, 0.0, 1.0),
        0.0,
        1.0,
    )
    lyric_likelihood = _clamp(
        0.36 * lyric_vocab_score
        + 0.34 * repetition_score
        + 0.20 * music_context_score
        + 0.10 * _clamp(1.0 - creator_exception_score, 0.0, 1.0)
        - 0.26 * creator_exception_score,
        0.0,
        1.0,
    )
    return {
        "lyric_likelihood": round(float(lyric_likelihood), 4),
        "creator_exception_score": round(float(creator_exception_score), 4),
        "signals": {
            "lyric_term_hits": lyric_hits,
            "lyric_vocab_score": round(float(lyric_vocab_score), 4),
            "repetition_score": round(float(repetition_score), 4),
            "top_token_ratio": round(float(repetition["top_token_ratio"]), 4),
            "repeated_bigram_ratio": round(float(repetition["repeated_bigram_ratio"]), 4),
            "repeated_trigram_ratio": round(float(repetition["repeated_trigram_ratio"]), 4),
            "music_context_score": round(float(music_context_score), 4),
            "live_context_score": round(float(live_context_score), 4),
        },
    }


def _repetition_profile(tokens: list[str]) -> dict:
    tokens = [token for token in tokens if token]
    if not tokens:
        return {"top_token_ratio": 0.0, "repeated_bigram_ratio": 0.0, "repeated_trigram_ratio": 0.0}
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    top_token_ratio = max(counts.values()) / max(1, len(tokens))
    return {
        "top_token_ratio": top_token_ratio,
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


def _merge_acoustic_profiles(profiles: list[dict]) -> dict:
    ok_profiles = [profile for profile in profiles if isinstance(profile, dict) and profile.get("status") == "ok"]
    if not ok_profiles:
        return {"status": "unavailable" if profiles else "not_sampled"}
    total_sq = sum(float(profile.get("_total_sq", 0.0)) for profile in ok_profiles)
    speech_sq = sum(float(profile.get("_speech_sq", 0.0)) for profile in ok_profiles)
    gap_sq = sum(float(profile.get("_gap_sq", 0.0)) for profile in ok_profiles)
    total_count = sum(int(profile.get("_total_count", 0)) for profile in ok_profiles)
    speech_count = sum(int(profile.get("_speech_count", 0)) for profile in ok_profiles)
    gap_count = sum(int(profile.get("_gap_count", 0)) for profile in ok_profiles)
    total_rms = math.sqrt(total_sq / max(1, total_count)) / 32768.0
    speech_rms = math.sqrt(speech_sq / max(1, speech_count)) / 32768.0 if speech_count else 0.0
    gap_rms = math.sqrt(gap_sq / max(1, gap_count)) / 32768.0 if gap_count else 0.0
    gap_to_speech = gap_rms / max(0.0001, speech_rms)
    gap_to_total = gap_rms / max(0.0001, total_rms)
    speech_coverage = speech_count / max(1, total_count)
    game_bed_score = _clamp((gap_to_speech - 0.28) / 0.82, 0.0, 1.0) * 0.7
    game_bed_score += _clamp((gap_to_total - 0.42) / 0.58, 0.0, 1.0) * 0.3
    return {
        "status": "ok",
        "sample_count": len(ok_profiles),
        "duration": round(sum(float(profile.get("duration", 0.0)) for profile in ok_profiles), 3),
        "total_rms": round(total_rms, 5),
        "speech_rms": round(speech_rms, 5),
        "gap_rms": round(gap_rms, 5),
        "gap_to_speech_ratio": round(gap_to_speech, 4),
        "gap_to_total_ratio": round(gap_to_total, 4),
        "speech_coverage": round(speech_coverage, 4),
        "game_bed_score": round(_clamp(game_bed_score, 0.0, 1.0), 4),
    }


def _public_acoustic_profile(profile: dict | None) -> dict:
    if not isinstance(profile, dict):
        return {"status": "not_sampled", "game_bed_score": 0.0}
    public = {
        key: value for key, value in profile.items()
        if not str(key).startswith("_")
    }
    public.setdefault("status", "unknown")
    public.setdefault("game_bed_score", 0.0)
    return public


def _candidate_sample_windows(
    moments: list[dict],
    *,
    max_samples: int = 6,
    sample_seconds: int = 20,
) -> list[dict]:
    """Return safe candidate windows for stream sampling, sorted by score."""
    sample_seconds = max(1, int(_safe_float(sample_seconds, 20.0)))
    limit = max(0, int(_safe_float(max_samples, 0.0)))
    windows: list[dict] = []
    for moment in moments or []:
        if not isinstance(moment, dict):
            continue
        start = _safe_float(moment.get("start"), None)
        end = _safe_float(moment.get("end"), None)
        if start is None or end is None:
            continue
        start_i = max(0, int(start))
        end_i = int(min(end, start_i + sample_seconds))
        if end_i <= start_i:
            continue
        window = dict(moment)
        window["_sample_start"] = start_i
        window["_sample_end"] = end_i
        window["_sample_score"] = _safe_float(moment.get("score"), 0.0)
        windows.append(window)
    windows.sort(key=lambda row: row["_sample_score"], reverse=True)
    return windows[:limit]


def _safe_float(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _normal_text(text: str) -> str:
    text = str(text or "").lower().replace("'", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _weighted_score(text: str, weights: tuple[tuple[str, float], ...]) -> float:
    normal = _normal_text(text)
    if not normal:
        return 0.0
    padded = f" {normal} "
    score = 0.0
    for phrase, weight in weights:
        phrase_norm = _normal_text(phrase)
        if phrase_norm and f" {phrase_norm} " in padded:
            score += float(weight)
    return score
