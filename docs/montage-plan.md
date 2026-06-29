# ViriaRevive Gameplay Montage Plan

Research checked: 2026-06-26

This plan tracks the gameplay-focused montage system in this fork. The goal is
not to turn ViriaRevive into a full manual editor; it is to let the same local
analysis used for single clips build multi-beat gaming shorts with a clearer
hook, escalation, and payoff.

The current foundation can analyze gameplay candidates, build local storyboard
beats, render hard-cut vertical montages, write metadata sidecars, and record
compact montage feedback. The remaining work is quality polish: better beat
continuity, stronger pacing, clearer review UI, and more useful learning from
approved/rejected montage sequences.

## Research Snapshot

Current AI clipping tools generally converge on the same product shape:

- **Generic short-form clippers**: OpusClip, Klap, quso.ai/Vidyo, Vizard, and
  Descript focus on ingesting a long video or URL, finding ranked highlights,
  reframing them vertically, adding captions, and exporting or scheduling social
  posts. Public docs emphasize virality scores, transcript editing, captions,
  auto-reframe, branding/templates, and social publishing.
- **Gaming-first clippers**: Powder and Medal focus on stream/gameplay sources,
  bookmarks, event detection, full-session recording, multi-track audio, game
  libraries, and quick editing/sharing.
- **Open-source building blocks**: PySceneDetect, FFmpeg, Whisper/faster-whisper,
  OpenCV, and Auto-Editor-style silence/audio analysis cover lower-level pieces:
  scene cuts, media rendering, speech timing, visual stats, and silence removal.
  They do not provide a full creator-aware montage editor by themselves.

The gap this fork can own: current tools mostly select highlights and package
them for short-form platforms. They generally do not build a local, inspectable
**creator memory storyboard** for video-game footage that combines feedback
history, creator voice confidence, game context, visual diagnostics, and prior
review decisions to sequence a montage around the creator's actual style.

## Current Focus: Learning And Montage, Not Manual Editing

ViriaRevive should not become a full timeline editor in the near term. The next
product advantage is a better local learning loop plus montage generation.

The app should learn from every useful outcome:

- Which candidates were found, accepted, selected, rendered, or rejected.
- Which clips the user liked, disliked, favorited, deleted, kept, uploaded, or
  rerolled metadata for.
- Which audio stream/subtitle source ended up correct.
- Which moment categories, game context, vision labels, and creator-voice
  signals were present when the user approved or rejected a clip.
- Which processing settings produced no clips, too few clips, or strong clips.

This should feed two future systems:

- **Single-clip ranking:** a bounded preference nudge that can compare liked
  clips against disliked/deleted clips from similar runs.
- **Montage storyboards:** a memory-aware sequence planner that understands the
  creator's preferred hooks, setup length, pacing, reaction value, cutscene
  tolerance, and payoff style.

## Repo Fit

The plan should reuse these existing files before adding new dependencies:

- `detector.py`: source moment candidates from audio/scene/variance signals.
- `candidate_ranker.py`: transcript scoring, categories, feedback learning,
  voice-profile scoring, AI label scoring, debug report writing.
- `api_bridge.py`: GUI pipeline orchestration, app state, personalization,
  source context, voice profile persistence, debug reports, upload scheduling.
- `main.py`: CLI pipeline path for parity checks.
- `clipper.py`, `cropper.py`, `subtitler.py`: rendering, vertical framing, and
  subtitles.
- `voice_profile.py`: local numeric creator voice profile; no raw audio stored.
- `visual_diagnostics.py`: cheap frame statistics for visual evidence.
- `multimodal_analysis.py`: optional local Ollama vision analysis.
- `game_identity.py`, `game_context.py`: local game matching plus compact
  Wikidata-backed facts.
- `title_generator.py`: local/Ollama metadata and label prompt infrastructure.
- `gui/index.html`, `gui/app.js`, `gui/style.css`: Generate, Results,
  All Videos, Upload, Settings, feedback, AI Notes, and Data & Privacy UI.
- `config.py`: current runtime artifact roots: `CLIPS_DIR`, `SUBTITLES_DIR`,
  `ANALYSIS_CACHE_DIR`, `PERSONALIZATION_FILE`, `VOICE_PROFILE_FILE`,
  `GAME_CONTEXT_DB_FILE`, `RUN_LEARNING_FILE`.
- `run_learning.py`: compact local run/outcome memory. Stores run summaries,
  feedback/delete/metadata events, and clip-outcome summaries without raw media,
  thumbnails, raw audio, or full transcripts.
- `tests/`: existing guards for API path safety, feedback learning, data
  privacy, visual diagnostics, game context, multimodal analysis, voice profile,
  GUI wiring, and release/dependency safety.

## Differentiator: Creator Memory Storyboard

Instead of "top N viral clips stitched together," ViriaRevive should create a
storyboard that explains why each beat belongs in the montage.

Creator memory inputs:

- Local feedback: like/dislike/favorite, reason chips, notes, compact
  `learning_terms`, deleted-file learning preservation from `personalization.json`.
- Run learning: `run_learning.json` summaries for candidate counts, selected
  clips, no-quality runs, metadata rerolls, deleted clips, and compact outcome
  history.
- Local voice profile: creator-speech confidence and ranking readiness from
  `voice_profile.json`, used only as a small opt-in signal.
- Game context: resolved source/game identity and compact facts from
  `game_context.sqlite3`.
- Visual diagnostics: motion, darkness, red flashes, UI density, possible
  failure screens, scenic frames, and optional local Ollama visual labels.
- Clip context: transcript windows, moment categories, selected stream summary,
  subtitle policy, AI Notes, title metadata, and run debug intervals.

Storyboard output:

- Beat roles such as `hook`, `setup`, `escalation`, `payoff`, `reaction`,
  `callback`, `tutorial_step`, `atmosphere`, and `outro`.
- Evidence chips for each beat: feedback match, creator voice confidence, game
  context, visual energy, category, transcript hook, and rejection guards.
- Pacing plan: target montage length, per-beat duration, transition type,
  subtitle density, audio handling, and optional title-card/callout text.
- Memory summary: compact, redacted "this creator tends to keep/dislike" signals
  so users can understand the cut without exposing raw transcripts publicly.

This should remain local-first and explainable. No raw video, thumbnails, or
audio should be copied into memory artifacts.

## Proposed Data Artifacts

Add new artifacts only when implementation begins:

- `analysis_cache/montages/<storyboard_id>.json`
  - Canonical storyboard and render plan.
  - Stores compact source identities, candidate ids, beat roles, scores,
    evidence, and settings.
- `subtitles/<source_stem>_montage_debug.json`
  - Debug mirror similar to existing `*_run_debug.json`, including selected and
    rejected montage beats, rank deltas, feature statuses, and render timings.
- `clips/<source_stem>_montage<N>.mp4`
  - Final rendered montage output.
- `clips/<source_stem>_montage<N>.txt`
  - Existing-style upload sidecar with generated title, description, tags, and
    storyboard summary.
- `personalization.json`
  - Add compact montage feedback only after the user rates a montage. Store
    beat ids, action, reason chips, and learning terms; do not store raw media.
- `run_learning.json`
  - New compact memory file for run summaries and clip outcome events.
  - Stores selected/rendered counts, feature status, compact category/vision
    evidence, feedback events, delete events, and metadata-generation events.
  - Does not store raw video, thumbnails, raw audio, or full transcripts.

Suggested `montage_storyboard` schema fields:

```json
{
  "schema_version": 1,
  "storyboard_id": "montage_...",
  "created_at": "2026-06-26T00:00:00Z",
  "source_ids": [],
  "settings": {
    "target_duration": 60,
    "story_shape": "hook_escalate_payoff",
    "memory_enabled": true,
    "render_quality": "draft"
  },
  "memory_snapshot": {
    "local_only": true,
    "stores_raw_media": false,
    "feedback_signal_count": 0,
    "voice_profile_used": false,
    "game_context_used": false
  },
  "beats": [
    {
      "beat_id": "beat_1",
      "role": "hook",
      "clip_id": "",
      "source_id": "",
      "start": 0.0,
      "end": 8.0,
      "evidence": [],
      "transition_after": "hard_cut",
      "subtitle_policy": "creator",
      "score": 0.0
    }
  ],
  "rejected": [],
  "render_plan": []
}
```

## Backend Pipeline

Stage the backend as additive functions, not a rewrite.

1. **Collect candidates**
   - Source from current in-memory `_moments`/`_results` in `api_bridge.py`.
   - Fallback to the newest `*_run_debug.json` from `SUBTITLES_DIR` when the app
     has candidate debug but no live result state.
   - Include known clips from All Videos only when matching compact metadata is
     available; otherwise show "needs analysis first."

2. **Build memory context**
   - Add a small helper near candidate ranking, likely in a new
     `montage_storyboard.py`, that consumes sanitized snapshots from
     `run_learning.py`, `candidate_ranker.py`, `voice_profile.py`,
     `game_context.py`, and `visual_diagnostics.py`.
   - Keep influence caps separate from current single-clip ranking caps. Memory
     should reorder or assign beat roles, not silently overpower quality guards.
   - Use pairwise learning where possible: liked/favorited clips from a source
     should be compared with disliked/deleted clips from the same or similar
     source before assigning montage roles.

3. **Sequence beats**
   - Start deterministic: choose one strong hook, one or more escalation beats,
     one payoff/reaction, and optional callback/outro.
   - Penalize duplicated timestamps, repeated same-category clips, menu/static
     visuals, black frames, music-lyrics guard failures, and weak transcript-only
     aftermath.
   - Prefer variety when scores are close: visual action + creator reaction +
     game-context-aware explainer can beat five similar panic clips.

4. **Draft render plan**
   - Reuse `clipper.py` and FFmpeg utilities for segment extraction.
   - Concatenate segments with conservative hard cuts first. Add crossfades,
     speed ramps, beat-sync, or music later only behind explicit settings.
   - Reuse `subtitler.py` for word-level captions where transcript timing is
     reliable; otherwise mark subtitle gaps in debug.

5. **Optional Deep critique**
   - In Deep mode only, reuse `multimodal_analysis.py`/Ollama patterns to ask a
     local model for storyboard critique: missing setup, duplicate beat, weak
     ending, confusing game context, or unsafe title claim.
   - Treat this as a capped suggestion layer. The deterministic storyboard must
     still work without Ollama.

6. **Persist and render**
   - Write storyboard JSON before rendering so a failed render can be resumed.
   - Write montage debug after render with final file paths, durations, subtitle
     status, and feature influence summaries.
   - Add montage outputs to Results/All Videos using the same path-safety rules
     as normal clips.

## UI Plan

Keep the first UI practical and review-first.

1. **Results: Draft Montage**
   - Add a folder-level and selected-clips action once implementation starts:
     `Draft Montage`.
   - Controls: target duration, source scope, story shape, use memory, use local
     vision, use creator voice, include tutorial/explainer beats, output quality.
   - Show an estimate using existing processing-history patterns.

2. **Montage Review Panel**
   - A compact storyboard timeline with beat cards.
   - Each card shows role, clip thumbnail/player, interval, evidence chips,
     transcript preview, visual/game/voice/feedback indicators, and a replace
     action.
   - Allow reorder, remove, replace from alternates, and regenerate storyboard.

3. **Render States**
   - `Analyze only`: creates storyboard, no video render.
   - `Draft render`: low-effort render for review.
   - `Final render`: full quality subtitles/crop/metadata.
   - `Add to Upload`: sends rendered montage to the existing Upload prep flow.

4. **Settings and Privacy**
   - Data & Privacy should explain montage memory with the same clarity as
     voice profile and feedback learning.
   - Settings should keep creator memory opt-in for selection influence, with
     storyboard generation still possible in diagnostics-only mode.

## Staged Runs

Use staged runs so long footage does not become an all-or-nothing wait.

- **Run A: Learning foundation**
  - Add compact `run_learning.json` persistence.
  - Record run summaries, feedback events, delete outcomes, and metadata rerolls.
  - Keep the artifact local-first and media-free.
- **Run B: Pairwise preference scoring**
  - Compare approved clips against disliked/deleted clips from similar sources.
  - Add a small capped preference nudge after base quality scoring.
  - Report score deltas in run debug before increasing caps.
  - First slice completed: existing learned scoring now builds pairwise
    positive/negative terms when a source has both approved and rejected
    examples. Compact `run_learning.json` outcomes can backfill these signals
    when personalization summaries are missing. The total learned influence
    remains capped by the existing feedback-learning cap.
- **Run 0: Candidate audit**
  - Reads existing run debug/results and reports whether enough usable clips
    exist for montage.
  - Completed first slice: `montage_storyboard.py` builds a compact
    montage-readiness audit from `*_run_debug.json`, filters weak/music/black
    candidates, reports usable beat counts/category variety/feature usage, and
    can persist `analysis_cache/montages/<source>_montage_audit.json`.
- **Run 1: Storyboard-only**
  - Produces `montage_storyboard.json`; no rendering.
  - Good first implementation target and easy to test.
  - Completed first slice: `build_storyboard_from_audit(...)` turns a ready or
    thin audit into a deterministic hook/escalation/payoff storyboard with
    compact evidence chips and a hard-cut render plan placeholder. `draft_montage`
    persists `<source>_montage_storyboard.json` without touching video files.
- **Run 2: Draft render**
  - Renders a quick montage from approved beats; minimal transitions.
  - Captions can be omitted or simplified if word timings are incomplete.
  - Completed first slice: `montage_renderer.py` renders storyboard beats into
    normalized temporary segments and concatenates them with hard cuts. The
    `render_montage_draft` API writes a draft MP4 into `clips/`, adds it to
    Results state, and saves render debug while refusing storyboard sources that
    cannot be traced back to saved run debug.
  - Follow-up completed: segment rendering can now reuse the normal clip
    extraction path when a storyboard beat carries crop params or subtitle
    paths, and failed partial segment files are cleaned up.
- **Run 3: Final render**
  - Full subtitle styling, crop checks, metadata sidecar, and upload-ready file.
  - Backend foundation completed: `render_montage_final` reuses the guarded
    storyboard source check, renders a higher-quality hard-cut montage, writes
    metadata sidecar text, marks the Result as upload-ready, and records subtitle
    status from actual rendered segments.
  - Generate-flow wiring completed: when the wizard is in Montage mode, the main
    pipeline now stops before normal clip rendering, builds a montage audit from
    the just-analyzed candidates, writes storyboard files, renders 1-5 final
    hard-cut montages when enough distinct beats exist, adds each montage to
    Results, and stores montage render metadata in run debug.
  - Follow-up completed: live Generate montage passes the current crop,
    subtitle style/placement, Whisper model, and selected commentary stream into
    the montage render prep step. Storyboard selection now caps beat duration to
    the requested target and lets template intent, such as funny/punchline
    moments, influence beat choice without storing full transcripts.
  - Follow-up completed: montage planning now prefers coherent local beat
    clusters around a strong anchor instead of stitching unrelated top-scoring
    moments from across the whole source. Final concat re-encodes the stitched
    segments to reset timestamps, avoiding files whose audio/container duration
    outlives the visible video.
  - Follow-up completed: montage planning can now rescue nearby low-confidence
    context beats, repair topic jumps, keep payoff lines on screen longer, and
    avoid reusing the same beat across additional requested montages. If fewer
    usable sequences exist than requested, the app reports the smaller count
    instead of padding with duplicates.
  - Follow-up completed: final montage metadata now uses the actual storyboard
    roles, beat text, local game context, creator learning context, and compact
    quality explanations instead of a generic montage-title seed. The planner
    also applies a narrow repeated-chatter penalty so low-context loops are less
    likely to win over cleaner story beats.
  - Follow-up completed: Ollama vision preflight now uses a valid tiny image
    payload and records the Ollama HTTP error body when a local vision model
    rejects a request, making vision failures actionable in debug.
- **Run 4: Learn from montage feedback**
  - User rates the whole montage and individual beats.
  - Store compact learning terms and beat roles back into personalization.
  - Completed backend first slice: `run_learning.py` now supports
    `montage_feedback_like`, `montage_feedback_dislike`, and
    `montage_feedback_favorite` events with per-storyboard and per-beat
    outcomes. `api_bridge.record_montage_feedback(...)` validates saved
    storyboard files, accepts optional `beat_id`, and stores only compact role,
    category, reason, and learning-term data. UI controls for rating rendered
    montage storyboards are still a follow-up.
  - Completed memory-consumption slice: montage outcomes now feed the central
    learned-scoring profile as reduced-weight signals, and Ollama title,
    description, vision, and moment-label prompts receive the same compact
    `run_learning.json` context. This follows a local RAG/memory approach rather
    than retraining Ollama model weights during normal app use.
- **Run 5: Batch montage**
  - Multiple sources or sessions, after single-source reliability is proven.

## Tests

Add tests before widening UI exposure.

- `tests/test_montage_storyboard.py`
  - Deterministic beat sequencing from synthetic candidates.
  - Influence caps for feedback, voice, game, and visual diagnostics.
  - Rejection of weak/menu/black-frame/music-guard candidates.
  - Storyboard schema sanitization and redaction.
- `tests/test_run_learning.py`
  - Run summaries preserve selected/outcome counts but not raw transcript text.
  - Feedback events merge like/dislike/favorite flips correctly.
  - Deleted clips and metadata rerolls update compact clip outcomes.
  - Corrupt or malformed learning JSON sanitizes to a safe schema.
- `tests/test_api_bridge_path_safety.py`
  - Montage artifact paths stay under `ANALYSIS_CACHE_DIR`, `SUBTITLES_DIR`, and
    `CLIPS_DIR`.
  - Resume from storyboard cannot render arbitrary paths.
- `tests/test_data_privacy_summary.py`
  - Montage memory reports `local_only`, `stores_raw_media: false`, counts, and
    opt-in influence state.
- `tests/test_gui_static_guards.py`
  - Montage controls are wired, escaped, keyboard reachable, and not exposed as
    a hidden debug-only recovery path.
- `tests/test_release_guards.py` / `tests/test_release_safety.py`
  - No new unreviewed runtime directories or private montage artifacts are
    included in releases.
- Small media integration fixture
  - Synthetic two-to-four segment video with known audio/transcript/scene
    intervals to prove storyboard -> render -> debug round trip.

## Licensing And Dependency Posture

Start with no new runtime dependency.

- Reuse existing FFmpeg/ffprobe posture in `THIRD_PARTY_NOTICES.md`. Keep FFmpeg
  as separate executables, preserve source/license obligations, and avoid
  nonfree builds.
- Reuse `scenedetect-headless`, `faster-whisper`, OpenCV, and optional Ollama
  model downloads already present in `requirements.txt` and app settings.
- Do not add new AGPL exposure beyond the existing Ultralytics YOLO posture.
  If montage needs object detection beyond current crop support, prefer existing
  diagnostics or a separately reviewed permissive option.
- Treat WhisperX/pyannote-style diarization as future research only; it may add
  model-access and license complexity that is not needed for the first montage
  storyboard.
- Auto-Editor is useful prior art for silence/loudness-driven cuts, but the
  first implementation should reuse ViriaRevive's existing audio signals instead
  of shelling out to another editor.
- Do not bundle model weights, sound effects, music, memes, templates, or B-roll
  packs without explicit provenance and license metadata.
- Keep online game context limited to the current compact Wikidata/CC0 path
  unless a future docs/licensing pass approves additional sources.

## Implementation Milestones

1. **Docs and schema**
   - This document.
   - Add a proposed schema doc or test fixture when implementation starts.

2. **Backend storyboard MVP**
   - New `montage_storyboard.py`.
   - Build storyboard from existing `*_run_debug.json` and current `_moments`.
   - Persist `analysis_cache/montages/<storyboard_id>.json`.

3. **API surface**
   - Add `draft_montage(...)`, `get_montage_storyboard(...)`,
     `render_montage(...)`, and `record_montage_feedback(...)` to `api_bridge.py`.
   - Keep path validation and cancellation behavior consistent with current
     generate/upload flows.

4. **Draft renderer**
   - Reuse FFmpeg segment concat first.
   - Save montage debug and sidecar metadata.

5. **Review UI**
   - Results selected/folder action, storyboard panel, beat replace/reorder,
     render button, feedback controls.

6. **Deep local AI polish**
   - Optional storyboard critique using existing Ollama status/model detection.
   - Debug report must show whether this changed anything.

7. **Upload integration**
   - Treat montage files like normal clips in Upload, with AI Notes and metadata
     rerolls available.

## Open Questions

- Should montage memory influence be opt-in globally, per-run, or both?
- Should the first montage target only one source at a time, or allow selected
  clips across a folder if they share a source/game identity?
- Should final montage feedback train only montage sequencing, or also normal
  single-clip ranking?
- What is the minimum review UI for v1: storyboard cards only, or cards plus a
  playable draft preview?

## Sources

- OpusClip: https://www.opus.pro/
- Klap AI Clip Generator: https://klap.app/tools/ai-clip-generator
- quso.ai/Vidyo AI Clips Generator: https://quso.ai/products/ai-clips-generator
- Vizard API output fields: https://docs.vizard.ai/docs/retrieve-video-clips
- Powder gaming clipping: https://www.powder.gg/
- Medal features and game event detection: https://medal.tv/features
- PySceneDetect: https://www.scenedetect.com/
- OpenAI Whisper: https://github.com/openai/whisper
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- Auto-Editor: https://github.com/WyattBlue/auto-editor
- FFmpeg legal notes: https://www.ffmpeg.org/legal.html
- OpenCV license: https://opencv.org/license/
- Ultralytics license: https://www.ultralytics.com/license
