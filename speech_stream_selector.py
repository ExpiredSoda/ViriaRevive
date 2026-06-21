"""Pick the best source audio stream for subtitles.

OBS recordings can have misleading track names or mixed routing. Instead of
trusting labels alone, sample the selected moments and let Whisper tell us
which stream actually contains intelligible commentary.
"""

from __future__ import annotations

from pathlib import Path

from audio_streams import get_audio_streams, pick_voice_stream_ordinal
from clipper import extract_audio_clip
from transcriber import transcribe_clip


VOICE_HINTS = ("microphone", "mic", "voice", "commentary", "narration")
GAME_HINTS = ("game", "desktop", "system", "capture")


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
    streams = get_audio_streams(video_path)
    if not streams:
        return None
    if len(streams) == 1:
        return int(streams[0]["ordinal"])

    samples = sorted(moments, key=lambda m: float(m.get("score", 0)), reverse=True)
    samples = samples[:max_samples]
    if not samples:
        return pick_voice_stream_ordinal(video_path)

    scratch_dir.mkdir(exist_ok=True)
    print("[audio] Inspecting audio streams for subtitle speech...")

    scored = []
    for stream in streams:
        ordinal = int(stream["ordinal"])
        title = stream.get("title") or f"0:a:{ordinal}"
        words_total = 0
        chars_total = 0
        sample_hits = 0

        for sample_idx, moment in enumerate(samples):
            start = int(moment["start"])
            end = int(min(moment["end"], start + sample_seconds))
            if end <= start:
                continue

            wav = scratch_dir / f"_speech_stream_probe_a{ordinal}_{sample_idx}.wav"
            try:
                if not extract_audio_clip(video_path, start, end, wav, audio_stream=ordinal):
                    continue
                words = transcribe_clip(wav, model_size=model_size, language=language)
                if words:
                    sample_hits += 1
                    words_total += len(words)
                    chars_total += sum(len(w.get("text", "")) for w in words)
            finally:
                try:
                    wav.unlink(missing_ok=True)
                except OSError:
                    pass

        score = float(words_total)
        title_lower = str(title).lower()
        if words_total:
            if any(hint in title_lower for hint in VOICE_HINTS):
                score += 12.0
            if any(hint in title_lower for hint in GAME_HINTS):
                score -= 8.0

        scored.append({
            "ordinal": ordinal,
            "title": title,
            "words": words_total,
            "chars": chars_total,
            "hits": sample_hits,
            "score": score,
        })

    for row in scored:
        print(
            "[audio] Stream score "
            f"0:a:{row['ordinal']} ({row['title']}): "
            f"{row['words']} words across {row['hits']} samples, score={row['score']:.1f}"
        )

    best = max(scored, key=lambda row: row["score"], default=None)
    if best and best["words"] > 0:
        print(f"[audio] Subtitle speech stream selected: 0:a:{best['ordinal']} ({best['title']})")
        return int(best["ordinal"])

    fallback = pick_voice_stream_ordinal(video_path)
    print(f"[audio] No speech found during stream scan; fallback stream={fallback}")
    return fallback
