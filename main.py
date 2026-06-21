#!/usr/bin/env python3
"""
ViriaRevive  –  Viral Clip Generator

  Downloads a YouTube video, finds the most engaging moments (no AI –
  pure audio-energy + scene-change analysis), adds TikTok-style
  word-by-word subtitles, and optionally schedules uploads to YouTube.

Usage:
  python main.py "https://youtube.com/watch?v=VIDEO_ID"
  python main.py "URL" --clips 3 --duration 45 --style bold
  python main.py "URL" --upload --schedule 12
"""

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import (
    CLIPS_DIR,
    CLIP_DURATION,
    CROP_VERTICAL,
    FFMPEG_PRESET,
    MIN_GAP,
    NUM_CLIPS,
    PERSONALIZATION_FILE,
    SUBTITLE_PLACEMENT,
    SUBTITLE_STYLE,
    SUBTITLES_DIR,
    VIDEO_CRF,
    WHISPER_LANGUAGE,
    WHISPER_MODEL,
)
from downloader import download_video
from detector import find_viral_moments, get_last_scene_detection_diagnostics
from transcriber import transcribe_clip
from subtitler import generate_subtitles, normalize_subtitle_placement, resolve_subtitle_placement
from clipper import extract_clip, extract_audio_clip
from cropper import get_crop_params, get_dimensions
from audio_streams import get_audio_streams, pick_voice_stream_ordinal
from candidate_ranker import (
    apply_learned_scoring,
    build_shadow_scoring_report,
    evaluate_candidate,
    needs_stream_retry,
    select_best_candidates,
    write_debug_report,
)
from speech_stream_selector import select_speech_stream
from uploader import upload_to_youtube, build_schedule


def _check_deps():
    missing = [name for name in ("ffmpeg", "ffprobe") if not shutil.which(name)]
    if missing:
        print(
            "[!] Missing dependency: "
            + ", ".join(missing)
            + " – install FFmpeg from https://ffmpeg.org/download.html "
              "and ensure both ffmpeg and ffprobe are in PATH"
        )
        sys.exit(1)


def _load_personalization_snapshot() -> dict:
    try:
        if PERSONALIZATION_FILE.exists():
            data = json.loads(PERSONALIZATION_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        print(f"[rank] Learned scoring could not load personalization: {exc}")
    return {}


def _print_shadow_summary(shadow_scoring: dict):
    if not shadow_scoring:
        return
    if not shadow_scoring.get("has_learning_signals"):
        print(
            f"[rank] Learned scoring checked {shadow_scoring.get('candidate_count', 0)} "
            "candidate(s); no feedback signals yet, base ranking used"
        )
        return
    changes = shadow_scoring.get("top_changes", [])
    learned_add = sum(1 for row in changes if row.get("selection_delta") == "added_by_learning")
    learned_drop = sum(1 for row in changes if row.get("selection_delta") == "dropped_by_learning")
    cap = shadow_scoring.get("learned_selection_max_adjustment", 0)
    changed = "changed selection" if shadow_scoring.get("output_changed") else "kept the same selection"
    print(
        f"[rank] Learned scoring blend: cap ±{cap}, {len(changes)} reorder signal(s), "
        f"{learned_add} added, {learned_drop} dropped; {changed}"
    )


def _video_duration(video_path: Path) -> float:
    from subprocess_utils import run as _run

    try:
        r = _run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return 600.0


def process(
    url: str,
    num_clips: int = NUM_CLIPS,
    clip_duration: int = CLIP_DURATION,
    style: str = SUBTITLE_STYLE,
    model: str = WHISPER_MODEL,
    language: str = WHISPER_LANGUAGE,
    upload: bool = False,
    schedule_hours: int = 24,
    crop: bool = CROP_VERTICAL,
    subtitle_placement: dict | None = None,
):
    _check_deps()
    subtitle_placement = normalize_subtitle_placement(subtitle_placement or SUBTITLE_PLACEMENT)

    # ── 1. Download ──────────────────────────────────────────────────────
    print("\n══ 1 · Downloading video ══")
    video_path = download_video(url)
    print(f"[+] {video_path}")

    vid_duration = _video_duration(video_path)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_warnings: list[str] = []

    # ── 2. Detect viral moments ──────────────────────────────────────────
    print("\n══ 2 · Finding viral moment candidates ══")
    candidates = find_viral_moments(
        video_path,
        num_clips=num_clips,
        clip_duration=clip_duration,
        min_gap=MIN_GAP,
        candidate_multiplier=5,
    )
    scene_detection = get_last_scene_detection_diagnostics()
    scene_status = scene_detection.get("status", "unknown")
    if scene_status not in {"ok", "zero_changes"}:
        run_warnings.append(f"scene_detection_{scene_status}")
    if not candidates:
        print("[!] Nothing found – try a longer video or lower --clips")
        return []

    speech_stream = select_speech_stream(
        video_path, candidates, model, language, SUBTITLES_DIR
    )

    # ── 3. Transcript-rank candidates ────────────────────────────────────
    print("\n══ 3 · Ranking candidates by transcript quality ══")
    stem = video_path.stem[:50]
    evaluations = []
    probe_buffer = 14

    for idx, candidate in enumerate(candidates, 1):
        print(f"\n── candidate {idx}/{len(candidates)} ──")
        start = int(candidate["start"])
        extended_end = min(int(candidate["end"]) + probe_buffer, int(vid_duration))
        wav = SUBTITLES_DIR / f"{stem}_probe_{idx}.wav"
        wav.unlink(missing_ok=True)

        words = []
        used_stream = speech_stream
        if extract_audio_clip(video_path, start, extended_end, wav, audio_stream=speech_stream):
            words = transcribe_clip(wav, model_size=model, language=language)
        if needs_stream_retry(words, extended_end - start):
            words, alt_stream = _try_alternate_audio_streams(
                video_path, start, extended_end, wav, model, language,
                speech_stream, return_stream=True,
            )
            if alt_stream is not None:
                used_stream = alt_stream

        evaluations.append(
            evaluate_candidate(
                candidate,
                words,
                extraction_start=float(start),
                extraction_end=float(extended_end),
                video_duration=float(vid_duration),
                target_duration=clip_duration,
                selected_stream=used_stream,
            )
        )
        wav.unlink(missing_ok=True)

    personalization_snapshot = _load_personalization_snapshot()
    apply_learned_scoring(
        evaluations,
        personalization_snapshot,
        source_stem=stem,
    )
    selected = select_best_candidates(
        evaluations,
        num_clips,
        min_gap=max(8, MIN_GAP),
        score_key="learned_quality_score",
    )
    shadow_scoring = build_shadow_scoring_report(
        evaluations,
        selected,
        personalization_snapshot,
        source_stem=stem,
        max_count=num_clips,
        min_gap=max(8, MIN_GAP),
    )
    debug_path = SUBTITLES_DIR / f"{stem}_candidate_debug.json"
    run_debug_path = SUBTITLES_DIR / f"{stem}_run_debug.json"
    debug_settings = {
        "num_clips": num_clips,
        "clip_duration": clip_duration,
        "min_gap": MIN_GAP,
        "whisper_model": model,
        "whisper_language": language,
        "subtitle_style": style,
        "subtitle_placement": subtitle_placement,
        "ffmpeg_preset": FFMPEG_PRESET,
        "video_crf": VIDEO_CRF,
        "crop_vertical": crop,
        "candidate_multiplier": 5,
    }
    write_debug_report(
        debug_path,
        video_path,
        candidates,
        evaluations,
        selected,
        scene_detection=scene_detection,
        settings=debug_settings,
        video_duration=vid_duration,
        warnings=run_warnings,
        shadow_scoring=shadow_scoring,
        run_id=run_id,
    )
    print(f"[+] Candidate debug saved: {debug_path}")
    _print_shadow_summary(shadow_scoring)

    if not selected:
        print("[!] No high-quality clips found.")
        return []

    # ── 4. Clip + subtitle each selected moment ──────────────────────────
    print(f"\n══ 4 · Creating {len(selected)} selected clips with subtitles ══")
    done: list[Path] = []
    final_clip_debug: list[dict] = []

    for idx, item in enumerate(selected, 1):
        print(f"\n── clip {idx}/{len(selected)} ──")
        m = item["moment"]
        words = item["words"]
        start, end = int(m["start"]), int(m["end"])
        wav = SUBTITLES_DIR / f"{stem}_c{idx}.wav"
        ass = SUBTITLES_DIR / f"{stem}_c{idx}.ass"
        out = CLIPS_DIR / f"{stem}_viral{idx}.mp4"
        for stale in (wav, ass, out):
            stale.unlink(missing_ok=True)

        final_probe_end = min(end + 8, int(vid_duration))
        final_words = []
        final_stream = m.get("speech_stream", speech_stream)
        if extract_audio_clip(video_path, start, final_probe_end, wav, audio_stream=final_stream):
            final_words = transcribe_clip(wav, model_size=model, language=language)
        if needs_stream_retry(final_words, final_probe_end - start):
            retry_words, retry_stream = _try_alternate_audio_streams(
                video_path, start, final_probe_end, wav, model, language,
                final_stream, return_stream=True,
            )
            if retry_words:
                final_words = retry_words
                final_stream = retry_stream
        if final_words:
            final_candidate = {
                **item["candidate"],
                "start": start,
                "end": end,
                "duration": end - start,
            }
            final_eval = evaluate_candidate(
                final_candidate,
                final_words,
                extraction_start=float(start),
                extraction_end=float(final_probe_end),
                video_duration=float(vid_duration),
                target_duration=clip_duration,
                selected_stream=final_stream,
            )
            if final_eval["words"]:
                keep_rank = m.get("quality_rank")
                keep_selection_quality = m.get("selection_quality_score")
                m.update(final_eval["moment"])
                if keep_rank is not None:
                    m["quality_rank"] = keep_rank
                if keep_selection_quality is not None:
                    m["selection_quality_score"] = keep_selection_quality
                words = final_eval["words"]
                start, end = int(m["start"]), int(m["end"])

        # 3a. compute crop params for 9:16
        crop_params = None
        vid_w, vid_h = get_dimensions(video_path)
        if crop:
            crop_params = get_crop_params(video_path, start, end)
            if crop_params:
                vid_w, vid_h = crop_params[0], crop_params[1]

        # 3d. build ASS subtitles (sized for cropped resolution)
        resolved_subtitle_placement = resolve_subtitle_placement(
            vid_w, vid_h, subtitle_placement
        )
        ass_path = generate_subtitles(
            words,
            ass,
            video_width=vid_w,
            video_height=vid_h,
            style=style,
            subtitle_placement=subtitle_placement,
        )
        m["subtitle_generated"] = bool(ass_path)
        m["subtitle_placement"] = resolved_subtitle_placement

        # 3e. extract clip + crop + burn subs (single ffmpeg pass)
        result = extract_clip(
            video_path, start, end, out,
            subtitle_path=ass_path if ass_path else None,
            crop_params=crop_params,
            preset=FFMPEG_PRESET,
            crf=VIDEO_CRF,
        )
        if result and result.path:
            m["subtitles_burned"] = bool(result.subtitles_burned and ass_path)
            if result.warning:
                m["render_warning"] = result.warning
                run_warnings.append(f"clip_{idx}_{result.warning}")
            done.append(result.path)
            final_clip_debug.append(
                {
                    "index": idx,
                    "path": str(result.path),
                    "subtitle_path": str(ass_path) if ass_path else None,
                    "start": start,
                    "end": end,
                    "duration": end - start,
                    "base_quality_score": item.get("quality_score"),
                    "quality_score": m.get("quality_score"),
                    "selection_quality_score": m.get("selection_quality_score"),
                    "selection_rank_score": item.get("selection_rank_score"),
                    "selection_score_source": item.get("selection_score_source", "quality_score"),
                    "learned_score": item.get("shadow_scoring", {}).get("learned_quality_score"),
                    "learned_quality_score": item.get("shadow_scoring", {}).get("learned_quality_score"),
                    "learned_adjustment": item.get("shadow_scoring", {}).get("learned_adjustment"),
                    "rank_delta": item.get("shadow_scoring", {}).get("rank_delta"),
                    "selection_delta": item.get("shadow_scoring", {}).get("selection_delta", ""),
                    "quality_rank": m.get("quality_rank"),
                    "word_count": m.get("word_count"),
                    "speech_stream": m.get("speech_stream"),
                    "subtitle_generated": m.get("subtitle_generated"),
                    "subtitles_burned": m.get("subtitles_burned"),
                    "subtitle_placement": m.get("subtitle_placement"),
                    "render_warning": m.get("render_warning", ""),
                    "transcript": m.get("transcript", ""),
                }
            )

        # cleanup temp wav
        wav.unlink(missing_ok=True)

    print(f"\n══ Done! {len(done)} clips ══")
    for p in done:
        print(f"  → {p}")
    write_debug_report(
        run_debug_path,
        video_path,
        candidates,
        evaluations,
        selected,
        scene_detection=scene_detection,
        settings=debug_settings,
        video_duration=vid_duration,
        final_clips=final_clip_debug,
        warnings=run_warnings,
        shadow_scoring=shadow_scoring,
        run_id=run_id,
    )
    print(f"[+] Run debug saved: {run_debug_path}")

    # ── 4. Upload / schedule ─────────────────────────────────────────────
    if upload and done:
        print("\n══ 4 · Uploading to YouTube ══")
        sched = build_schedule(
            done,
            start_time=datetime.now(timezone.utc) + timedelta(hours=1),
            interval_hours=schedule_hours,
        )
        for item in sched:
            idx = done.index(item["path"]) + 1
            upload_to_youtube(
                item["path"],
                title=f"{stem} – Viral Clip #{idx}",
                description=f"Viral clip from {stem}\n\n#shorts #viral",
                scheduled_time=item["scheduled_time"],
                privacy="public",
            )

    return done


def _try_alternate_audio_streams(video_path, start, end, wav, model, language,
                                 preferred_stream=None, return_stream=False):
    streams = get_audio_streams(video_path)
    if len(streams) < 2:
        return ([], None) if return_stream else []

    preferred = preferred_stream
    if preferred is None:
        preferred = pick_voice_stream_ordinal(video_path)
    for stream in streams:
        ordinal = int(stream["ordinal"])
        if preferred is not None and ordinal == preferred:
            continue
        title = stream.get("title") or f"0:a:{ordinal}"
        print(f"[audio] No words on preferred stream; trying 0:a:{ordinal} ({title})")
        if not extract_audio_clip(video_path, start, end, wav, audio_stream=ordinal):
            continue
        words = transcribe_clip(wav, model_size=model, language=language)
        if words:
            print(f"[audio] Using 0:a:{ordinal} ({title}) for subtitles")
            return (words, ordinal) if return_stream else words
    return ([], None) if return_stream else []


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    p = argparse.ArgumentParser(
        description="ViriaRevive – viral clip generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("url", help="YouTube video URL")
    p.add_argument("-n", "--clips",    type=int, default=NUM_CLIPS,    help=f"number of clips  (default {NUM_CLIPS})")
    p.add_argument("-d", "--duration", type=int, default=CLIP_DURATION, help=f"clip length in seconds  (default {CLIP_DURATION})")
    p.add_argument("-s", "--style",    choices=["tiktok", "karaoke", "glow", "clean", "bold", "minimal"], default=SUBTITLE_STYLE, help="subtitle style")
    p.add_argument("-m", "--model",    choices=["tiny", "base", "small", "medium", "large-v3"], default=WHISPER_MODEL, help="whisper model size")
    p.add_argument("-l", "--language", default=WHISPER_LANGUAGE, help="force language (en, es, fr …)")
    p.add_argument("-u", "--upload",   action="store_true", help="upload clips to YouTube")
    p.add_argument("--schedule",       type=int, default=24, help="hours between scheduled uploads")
    p.add_argument("--no-crop",        action="store_true", help="disable 9:16 vertical crop")
    p.add_argument("--subtitle-x",     type=int, default=SUBTITLE_PLACEMENT["x_pct"], help="caption box horizontal position percent")
    p.add_argument("--subtitle-y",     type=int, default=SUBTITLE_PLACEMENT["y_pct"], help="caption box vertical position percent")
    p.add_argument("--subtitle-width", type=int, default=SUBTITLE_PLACEMENT["width_pct"], help="caption box width percent")

    a = p.parse_args()
    process(
        url=a.url,
        num_clips=a.clips,
        clip_duration=a.duration,
        style=a.style,
        model=a.model,
        language=a.language,
        upload=a.upload,
        schedule_hours=a.schedule,
        crop=not a.no_crop,
        subtitle_placement={
            "x_pct": a.subtitle_x,
            "y_pct": a.subtitle_y,
            "width_pct": a.subtitle_width,
        },
    )


if __name__ == "__main__":
    main()
