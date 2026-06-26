#!/usr/bin/env python3
"""
ViriaRevive  –  Viral Clip Generator

  Downloads a YouTube video, finds engaging moments with transcript-aware
  ranking, optional learning/category scoring, word-by-word subtitles, and
  optionally schedules uploads to YouTube with generated metadata.

Usage:
  python main.py "https://youtube.com/watch?v=VIDEO_ID"
  python main.py "URL" --clips 3 --duration 45 --style bold
  python main.py "URL" --upload --schedule 12
"""

import argparse
import json
import multiprocessing
import re
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
    apply_moment_category_scoring,
    build_moment_category_ranking_report,
    build_shadow_scoring_report,
    evaluate_candidate,
    needs_stream_retry,
    select_best_candidates,
    write_debug_report,
)
from speech_stream_selector import (
    get_last_speech_stream_selection,
    profile_words_for_stream,
    select_speech_stream,
    should_accept_alternate_stream,
)
from title_generator import (
    generate_description,
    generate_tags,
    generate_title,
)
from uploader import upload_to_youtube, build_schedule

MIN_CLIP_DURATION_SECONDS = 10
MAX_CLIP_DURATION_SECONDS = 180


def _normalize_clip_duration(value, default: int = CLIP_DURATION) -> int:
    try:
        duration = int(value)
    except (TypeError, ValueError):
        duration = int(default or CLIP_DURATION)
    if duration <= 0:
        duration = int(default or CLIP_DURATION)
    return max(MIN_CLIP_DURATION_SECONDS, min(MAX_CLIP_DURATION_SECONDS, duration))


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


def _print_category_summary(moment_category_ranking: dict):
    if not moment_category_ranking:
        return
    if not moment_category_ranking.get("ranking_enabled"):
        return
    if not moment_category_ranking.get("has_category_scores"):
        print("[rank] Moment-label ranking enabled; no usable category signals found")
        return
    changes = moment_category_ranking.get("top_changes", [])
    added = sum(1 for row in changes if row.get("selection_delta") == "added_by_category")
    dropped = sum(1 for row in changes if row.get("selection_delta") == "dropped_by_category")
    cap = moment_category_ranking.get("moment_category_selection_max_adjustment", 0)
    changed = "changed selection" if moment_category_ranking.get("output_changed") else "kept the same selection"
    print(
        f"[rank] Moment-label blend: cap ±{cap}, {len(changes)} reorder signal(s), "
        f"{added} added, {dropped} dropped; {changed}"
    )


def _infer_cli_game_title_from_path(path) -> str:
    generic = {
        "vertical", "horizontal", "clips", "downloads", "recording video files",
        "videos", "video files", "captures", "recordings", "obs", "output",
    }
    try:
        p = Path(path)
    except Exception:
        return ""
    for part in (p.parent.name, p.parent.parent.name if p.parent else ""):
        cleaned = str(part or "").strip()
        if not cleaned or cleaned.lower() in generic:
            continue
        if re.match(r"^\d{4}-\d{2}-\d{2}", cleaned):
            continue
        return cleaned
    return ""


def _cli_title_context(
    video_path: Path,
    clip_path: Path,
    item: dict,
    idx: int,
    stream_selection: dict,
    source_audio_streams: list[dict],
) -> dict:
    moment = item.get("moment") if isinstance(item.get("moment"), dict) else {}
    ranker = moment.get("ranker") if isinstance(moment.get("ranker"), dict) else {}
    multi_signal_ai = (
        moment.get("multi_signal_ai_scoring")
        if isinstance(moment.get("multi_signal_ai_scoring"), dict)
        else {}
    )
    return {
        "schema_version": 1,
        "clip_id": moment.get("clip_id"),
        "source_id": moment.get("source_id"),
        "source_path": str(video_path),
        "source_stem": video_path.stem[:50],
        "game_title": moment.get("game_title") or _infer_cli_game_title_from_path(video_path),
        "clip_filename": Path(clip_path).name,
        "clip_index": idx,
        "start": moment.get("start"),
        "end": moment.get("end"),
        "duration": moment.get("duration"),
        "peak_time": moment.get("peak_time"),
        "candidate_rank": moment.get("candidate_rank"),
        "candidate_kind": moment.get("candidate_kind"),
        "detector_score": moment.get("score"),
        "detector_scores": moment.get("detector_scores") if isinstance(moment.get("detector_scores"), dict) else {},
        "scene_detection_status": moment.get("scene_detection_status"),
        "scene_score": moment.get("scene_score"),
        "variance_score": moment.get("variance_score"),
        "quality_score": moment.get("quality_score"),
        "selection_quality_score": moment.get("selection_quality_score"),
        "selection_rank_score": item.get("selection_rank_score") or moment.get("selection_rank_score"),
        "selection_score_source": item.get("selection_score_source") or moment.get("selection_score_source"),
        "quality_rank": moment.get("quality_rank"),
        "learned_quality_score": item.get("shadow_scoring", {}).get("learned_quality_score") or moment.get("learned_quality_score"),
        "learned_adjustment": item.get("shadow_scoring", {}).get("learned_adjustment") or moment.get("learned_adjustment"),
        "moment_categories": moment.get("moment_categories") or item.get("moment_categories"),
        "primary_category": moment.get("primary_category") or item.get("primary_category"),
        "ai_moment_classification": moment.get("ai_moment_classification"),
        "visual_diagnostics": moment.get("visual_diagnostics"),
        "multimodal_analysis": moment.get("multimodal_analysis"),
        "multi_signal_ai_quality_score": moment.get("multi_signal_ai_quality_score"),
        "multi_signal_ai_adjustment": moment.get("multi_signal_ai_adjustment"),
        "multi_signal_ai_scoring": multi_signal_ai,
        "commentary_guard": moment.get("commentary_guard"),
        "voice_profile": moment.get("voice_profile"),
        "word_count": moment.get("word_count"),
        "analysis_word_count": moment.get("analysis_word_count"),
        "subtitle_word_count": moment.get("subtitle_word_count"),
        "speech_stream": moment.get("speech_stream"),
        "audio_source": moment.get("audio_source"),
        "stream_selection": moment.get("stream_selection") or stream_selection,
        "source_audio_streams": source_audio_streams,
        "subtitle_generated": moment.get("subtitle_generated"),
        "subtitles_burned": moment.get("subtitles_burned"),
        "subtitle_placement": moment.get("subtitle_placement"),
        "transcript_source": moment.get("transcript_source"),
        "transcript_backfilled": moment.get("transcript_backfilled"),
        "transcript": str(moment.get("transcript") or item.get("transcript") or "")[:4000],
        "ranker": {
            "hook_points": ranker.get("hook_points"),
            "weak_points": ranker.get("weak_points"),
            "aftermath_points": ranker.get("aftermath_points"),
            "first_word_start": ranker.get("first_word_start"),
            "last_word_end": ranker.get("last_word_end"),
            "reject_reason": ranker.get("reject_reason"),
        },
    }


def _apply_cli_moment_category_ranking(
    evaluations: list[dict],
    learned_selected: list[dict],
    *,
    enabled: bool = False,
    num_clips: int = NUM_CLIPS,
    min_gap: int = MIN_GAP,
) -> tuple[list[dict], dict, str]:
    effective_gap = max(8, int(min_gap or 0))
    category_summary = apply_moment_category_scoring(
        evaluations,
        enabled=bool(enabled),
        score_key="learned_quality_score",
    )
    selection_score_key = "learned_quality_score"
    selected = learned_selected
    category_selected = learned_selected
    if category_summary.get("ranking_enabled") and category_summary.get("has_category_scores"):
        selection_score_key = "moment_category_quality_score"
        category_selected = select_best_candidates(
            evaluations,
            num_clips,
            min_gap=effective_gap,
            score_key=selection_score_key,
        )
        selected = category_selected

    moment_category_ranking = build_moment_category_ranking_report(
        evaluations,
        learned_selected,
        category_selected,
        enabled=category_summary.get("ranking_enabled", False),
        max_count=num_clips,
        min_gap=effective_gap,
    )
    return selected, moment_category_ranking, selection_score_key


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
    moment_category_ranking: bool = False,
):
    _check_deps()
    requested_clip_duration = clip_duration
    clip_duration = _normalize_clip_duration(clip_duration)
    if clip_duration != requested_clip_duration:
        print(
            "[i] Clip duration adjusted to "
            f"{clip_duration}s to stay within the 3-minute Shorts limit"
        )
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
    stream_selection = get_last_speech_stream_selection()
    source_audio_streams = [
        {
            "ordinal": int(stream.get("ordinal", 0)),
            "index": stream.get("index"),
            "title": stream.get("title") or f"Track {int(stream.get('ordinal', 0)) + 1}",
            "codec": stream.get("codec") or "",
            "channels": stream.get("channels"),
            "layout": stream.get("layout") or "",
            "language": stream.get("language") or "",
        }
        for stream in get_audio_streams(video_path)
    ]

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
    learned_selected = select_best_candidates(
        evaluations,
        num_clips,
        min_gap=max(8, MIN_GAP),
        score_key="learned_quality_score",
    )
    selected, moment_category_report, selection_score_key = _apply_cli_moment_category_ranking(
        evaluations,
        learned_selected,
        enabled=moment_category_ranking,
        num_clips=num_clips,
        min_gap=MIN_GAP,
    )
    shadow_scoring = build_shadow_scoring_report(
        evaluations,
        learned_selected,
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
        "moment_category_ranking": bool(moment_category_ranking),
        "selection_score_source": selection_score_key,
        "audio_source": {
            "mode": "auto",
            "selected_stream": speech_stream,
            "selected_reason": stream_selection.get("selected_reason"),
            "selected_confidence": stream_selection.get("confidence"),
            "runner_up_stream": stream_selection.get("runner_up_stream"),
            "stream_count": len(source_audio_streams),
            "streams": source_audio_streams,
            "render_audio": "all_source_streams_mixed",
            "alternate_stream_retry": True,
            "stream_selection": stream_selection,
        },
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
        moment_category_ranking=moment_category_report,
        run_id=run_id,
    )
    print(f"[+] Candidate debug saved: {debug_path}")
    _print_shadow_summary(shadow_scoring)
    _print_category_summary(moment_category_report)

    if not selected:
        print("[!] No high-quality clips found.")
        return []

    # ── 4. Clip + subtitle each selected moment ──────────────────────────
    print(f"\n══ 4 · Creating {len(selected)} selected clips with subtitles ══")
    done: list[Path] = []
    done_items: list[dict] = []
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
        m["audio_source"] = {
            "mode": "auto",
            "selected_stream": final_stream,
            "selected_reason": stream_selection.get("selected_reason"),
            "selected_confidence": stream_selection.get("confidence"),
            "runner_up_stream": stream_selection.get("runner_up_stream"),
            "stream_count": len(source_audio_streams),
            "render_audio": "all_source_streams_mixed",
            "alternate_stream_retry": True,
            "stream_selection": {
                "schema_version": stream_selection.get("schema_version", 1),
                "status": stream_selection.get("status"),
                "mode": stream_selection.get("mode", "diagnostic_v2"),
                "selected_stream": final_stream,
                "selected_title": stream_selection.get("selected_title"),
                "selected_reason": stream_selection.get("selected_reason"),
                "runner_up_stream": stream_selection.get("runner_up_stream"),
                "runner_up_title": stream_selection.get("runner_up_title"),
                "confidence": stream_selection.get("confidence"),
            },
        }
        m["stream_selection"] = m["audio_source"]["stream_selection"]

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
            done_items.append(item)
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
                    "moment_category_quality_score": item.get("moment_category_scoring", {}).get("moment_category_quality_score"),
                    "moment_category_ranking_enabled": item.get("moment_category_scoring", {}).get("ranking_enabled"),
                    "moment_category_adjustment": item.get("moment_category_scoring", {}).get("category_adjustment"),
                    "moment_category_selection_delta": item.get("moment_category_scoring", {}).get("selection_delta", ""),
                    "moment_category_rank_delta": item.get("moment_category_scoring", {}).get("rank_delta"),
                    "moment_category_scoring": item.get("moment_category_scoring") or m.get("moment_category_scoring"),
                    "primary_category": m.get("primary_category") or item.get("primary_category"),
                    "moment_categories": m.get("moment_categories") or item.get("moment_categories"),
                    "rank_delta": item.get("shadow_scoring", {}).get("rank_delta"),
                    "selection_delta": item.get("shadow_scoring", {}).get("selection_delta", ""),
                    "quality_rank": m.get("quality_rank"),
                    "word_count": m.get("word_count"),
                    "speech_stream": m.get("speech_stream"),
                    "audio_source": m.get("audio_source"),
                    "stream_selection": m.get("stream_selection"),
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
        moment_category_ranking=moment_category_report,
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
        for idx, (item, selected_item) in enumerate(zip(sched, done_items), 1):
            clip_path = item["path"]
            clip_context = _cli_title_context(
                video_path,
                clip_path,
                selected_item,
                idx,
                stream_selection,
                source_audio_streams,
            )
            transcript = str(clip_context.get("transcript") or "")
            game_title = str(clip_context.get("game_title") or "")
            title = generate_title(
                transcript,
                game_title=game_title,
                clip_context=clip_context,
            ) or f"{stem} - Clip #{idx}"
            description = generate_description(
                title,
                game_title=game_title,
                clip_context=clip_context,
            )
            tags = generate_tags(game_title, transcript, clip_context=clip_context)
            upload_to_youtube(
                clip_path,
                title=title,
                description=description,
                tags=tags,
                scheduled_time=item["scheduled_time"],
                privacy="public",
            )

    return done


def _try_alternate_audio_streams(video_path, start, end, wav, model, language,
                                 preferred_stream=None, return_stream=False,
                                 subtitle_policy="creator"):
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
            profile = profile_words_for_stream(
                ordinal,
                title,
                words,
                wav_path=wav,
                sampled_seconds=max(0.0, float(end) - float(start)),
            )
            acceptance = should_accept_alternate_stream(
                profile,
                subtitle_policy=subtitle_policy,
            )
            if not acceptance.get("accepted"):
                print(
                    "[audio] Rejected alternate stream "
                    f"0:a:{ordinal} ({title}): {acceptance.get('reason')}"
                )
                continue
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
    p.add_argument(
        "-d",
        "--duration",
        type=int,
        default=CLIP_DURATION,
        help=f"clip length in seconds, {MIN_CLIP_DURATION_SECONDS}-{MAX_CLIP_DURATION_SECONDS} for Shorts  (default {CLIP_DURATION})",
    )
    p.add_argument("-s", "--style",    choices=["tiktok", "karaoke", "glow", "clean", "bold", "minimal"], default=SUBTITLE_STYLE, help="subtitle style")
    p.add_argument("-m", "--model",    choices=["tiny", "base", "small", "medium", "large-v3"], default=WHISPER_MODEL, help="whisper model size")
    p.add_argument("-l", "--language", default=WHISPER_LANGUAGE, help="force language (en, es, fr …)")
    p.add_argument("-u", "--upload",   action="store_true", help="upload clips to YouTube")
    p.add_argument("--schedule",       type=int, default=24, help="hours between scheduled uploads")
    p.add_argument("--no-crop",        action="store_true", help="disable 9:16 vertical crop")
    p.add_argument("--subtitle-x",     type=int, default=SUBTITLE_PLACEMENT["x_pct"], help="caption box horizontal position percent")
    p.add_argument("--subtitle-y",     type=int, default=SUBTITLE_PLACEMENT["y_pct"], help="caption box vertical position percent")
    p.add_argument("--subtitle-width", type=int, default=SUBTITLE_PLACEMENT["width_pct"], help="caption box width percent")
    p.add_argument("--moment-category-ranking", action="store_true", help="opt in to capped deterministic moment-label ranking")

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
        moment_category_ranking=a.moment_category_ranking,
    )


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
