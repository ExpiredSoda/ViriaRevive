# Pending UX Todo

Captured on 2026-06-22 while testing the fresh v2.2.0 release candidate. This file is now status-based: completed items are listed first, and remaining items should be tackled in small follow-up passes.

## Completed This Run

- Detection wizard fixed-count behavior:
  - Fixed **Number of clips** now hides the Quality/Quantity dropdown and submits **Quality**.
  - **Auto** clip count still exposes the Quality/Quantity dropdown.
  - Tests cover the UI contract and submitted setting.
- Generate progress clarity:
  - Advancing into later stages now marks earlier stages completed during active runs.
  - Scene-sampling detail clears when backend progress no longer sends a scene detail string.
  - Static tests cover prior-stage checkmarks and detail clearing.
- Data & Privacy advanced modal:
  - Modal is wider/taller.
  - Tabs wrap instead of producing the awkward horizontal scrollbar.
  - Small-window behavior keeps usable overflow on the modal, not the tab strip.
- Subtitle Style settings:
  - **Refresh Preview** and **Reset** now sit in a stable left/right action row.
- Backend final render/debug reliability:
  - Final transcription can still suggest a trim, but rendering now preserves the selected candidate window.
  - Trim-relative subtitle words are shifted onto the preserved render timeline before subtitle generation.
  - Run debug `final_clips[]` now records `selected_*`, `render_*`, and `trim_adjusted_*` interval fields.
  - Run debug `final_clips[]` also records `subtitle_timing_offset` when a trim-relative transcript was shifted.
  - Final rows also carry compact category, visual, audio-stream, commentary guard, music/lyrics guard, subtitle, and transcript metadata.
  - Candidate and run debug rows now split `selection_*`, `ranking_*`, and `final_*` category fields so final transcript refinement cannot hide what influenced selection.
  - Selected AI moment labels stay in explicit pre-render AI metadata instead of being reinserted into final refined category objects.
  - Candidate-debug recovery carries richer selected-row metadata forward.
- Voice profile guard:
  - Missing or empty temporary WAVs are treated as non-scored voice samples instead of causing a runtime failure.
- Feedback persistence:
  - Feedback now stores compact local `learning_terms` with events and clip summaries.
  - Feedback now preserves separate like/dislike/favorite reasons so blank favorite toggles do not wipe useful like/dislike notes.
  - Learned scoring can use those terms when full transcript/moment snapshots are missing.
  - Existing feedback remains useful after rendered clip files are deleted.
  - Deletion paths mark matching feedback entries as `rendered_file_deleted` without removing learning data.
  - Redacted exports strip compact learning terms and per-action reasons while keeping only redaction/count markers.
- Local analysis graduation:
  - Balanced now enables deterministic moment-category ranking by Processing Depth using the existing `+/-0.020` cap.
  - Fast still keeps moment-category ranking off.
  - Confirmed black-frame candidates and stat/results/end-screen wording now feed low-value category evidence through the capped ranking path.
  - Tutorial/explainer, lore/story, and atmosphere labels now receive smaller positive deterministic signals instead of remaining display-only.
  - A tiny category-diversity helper can lift close underrepresented moment types without forcing weak clips over stronger candidates.
  - CLI now has optional deterministic moment-category ranking via `--moment-category-ranking`; CLI behavior remains off by default for one release.
- Voice profile clarity:
  - Voice status now separates **Off**, **Needs samples**, **Ready**, and **Influencing**.
  - Data & Privacy now explains why voice ranking is not influencing when ranking is saved but samples are missing.
  - Enrollment failures still refresh the current voice-profile status before showing the error.
  - Enrollment now requires clean creator-commentary metadata, enough speech, low game/music contamination, and no mixed/filtered transcript output before temporary audio extraction.
  - Enrollment scans all current Results entries for up to eight clean samples, deletes stale `voice_profile_*.wav` temp files, and returns compact skip counts/reasons without clip filenames.
  - Deep Analysis no longer forces voice-profile ranking on; voice ranking remains a separate opt-in setting.
- Commentary/game-audio guard selection impact:
  - Creator-policy single-track candidates that look like high-confidence game/NPC narration now receive a tiny capped quality penalty.
  - The guard does not hard-reject by itself and does not apply to explicit **Include all speech** or **Prefer game/NPC speech** policies.
  - Candidate debug rows now include `commentary_guard_selection` and `commentary_guard_selection_penalty`.
- Selection contract guard:
  - Added a synthetic test proving visual diagnostics and AI moment labels do not change selection when moment-category ranking is disabled.
  - The test also confirms malformed AI `selection_impact` / `output_changed` fields are sanitized back to no-impact metadata.
- AI moment-label shadow report:
  - Deep Analysis now writes a diagnostic-only `ai_moment_classification_shadow` report for a pre-final shortlist.
  - The shadow pass uses a non-mutating shortlist, never changes selected clips, and stores `selection_impact: "none"` / `output_changed: false`.
  - Selected AI labels can reuse shadow classifications so Ollama is not asked twice for the same selected candidate.
  - Completed first AI Viral Potential contract slice: selected labels and Deep shadow rows now include diagnostic-only `ai_viral_score`, `ai_viral_reason`, `ai_dimensions`, `ai_confidence`, zero `ai_adjustment`, and no `ai_rank_delta`.
  - Deep Analysis now has a separate guarded `ai_moment_ranking` pass with a `+/-0.015` cap after deterministic category ranking and before optional voice ranking.
  - AI ranking requires real Ollama output, high confidence, useful dimensions, no fallback label, and no music/commentary rejection.
  - Debug reports now show AI eligible/scored counts, score source, cap, added/dropped/kept clips, rank deltas, and ineligible reasons.
- Voice Profile nudge:
  - Liked or favorited creator-commentary clips can now return a local opt-in nudge to build the Creator Voice Profile.
  - The nudge does not auto-enroll, does not enable voice ranking, and does not store raw audio.
- README/tests:
  - README was updated for fixed-count Quality behavior, scene-detail clearing, final debug intervals, voice temp-WAV fail-soft behavior, per-action feedback reasons, and depth-enabled category ranking.
  - README was updated for default-off CLI moment-category ranking parity and the `--moment-category-ranking` opt-in.
  - README and Settings copy now describe Ollama as powering local AI titles, opt-in AI labels, and Deep AI ranking instead of calling it title-only.
  - Data & Privacy copy now says Deep Analysis can use high-confidence local AI labels as a tiny guarded ranking nudge instead of claiming AI labels never affect selection.
  - Focused tests passed for GUI static guards, API bridge path safety, Data & Privacy summary, and feedback reconciliation.
- Stream-selection weighting:
  - Completed a bounded mic/commentary creator-preference bonus so a mic-labeled track with strong creator-like dialogue does not lose too easily to an unlabeled/game track solely because the other track has more words.
  - The bonus is skipped for tiny evidence, lyric-like transcripts, game-bed-heavy tracks, weak/noisy mic transcripts, and scripted/game-dialogue-dominant samples.
  - Stream debug now exposes `mic_creator_preference_bonus` and can report `mic_creator_signal_over_more_words`.
- Release privacy guard:
  - `carryover_backups/` is ignored and treated as a private directory by the release safety scan.
- UI/UX clarity:
  - Results/Preview moment labels now separate deterministic **Category** labels from local fallback and Ollama AI labels in counts and chip styling.
  - Data & Privacy Local Analysis copy now says Fast keeps label ranking off while Balanced/Deep can enable deterministic label ranking by depth.
  - Data & Privacy Local Analysis now shows scene detection and voice ranking status, and labels Fast-suppressed heavy features as **Inactive in Fast** instead of implying a failure.
  - Run debug settings now include `local_analysis_feature_statuses` plus Fast disabled/skip reasons for scene, visual, AI labels, moment-label ranking, and voice ranking.
  - Deep Analysis wizard copy now says deeper targeted scene analysis instead of implying every long video gets a full scene scan.
  - Voice Profile enrollment copy now says **Build & Enable From Current Clips**, matching the backend behavior that enrollment enables the local profile.
- Ranking/debug hardening:
  - Learned scoring and learned debug reports now tolerate malformed quality values via safe float fallbacks.
  - Moment-category and voice-profile ranking reports now fall back to the real base score source when no usable category/voice scores existed.
  - AI moment-label sanitization now records unknown provider/model categories as explicit `unknown` diagnostic labels instead of silently mapping them to `low_value`.
- Empty quality outcome:
  - Runs where candidates were found but none met the quality bar now complete as a warning/empty state instead of a failed batch.
  - No-quality runs still write run debug and processing-history timing so local ETA learning does not lose the run.
  - The queue and completion banner now show **no clips** / **No Clips Created** instead of `0/1 Videos Done`.
- Additional guardrails:
  - Processing-history recording/backfill now tolerates malformed numeric fields and malformed `final_clips` values.
  - Corrupt voice-profile `feature_version` values fail closed to an unenrolled profile instead of raising.
  - Schedule timeline/status text is escaped before rendering.
  - Quiet captured subprocess output now returns empty strings/bytes instead of `None`.
  - Clip score rendering now coerces persisted scores through a finite-number guard before `toFixed()`.
  - yt-dlp GUI and CLI download paths now resolve the final merged media path before falling back to `prepare_filename()`.
  - Speech stream sampling now skips malformed candidate windows and falls back safely instead of halting audio-source inspection.
  - Subprocess timeout/cancel exceptions now preserve partial stdout/stderr so media failures keep useful diagnostic tails.
  - Crop debug screenshots are default-off and, when explicitly enabled, write to app analysis cache instead of beside user videos.
  - Full FFmpeg command echoing is default-off; render logs keep concise phase/status lines unless verbose command logging is explicitly enabled.
- Validation:
  - Full unittest discovery passed this run: 277 tests.
  - `node --check gui/app.js` passed.

## Still Pending Next

- Monitor real Fast/Balanced/Deep runs after the first active AI-label graduation, Voice Profile nudge, and category-diversity pass; tune caps only if run debug shows consistent wins.
- Improve stream-selection weighting when one track has more words but another looks more like the creator microphone. Completed first slice with a bounded `mic_creator_preference_bonus`; keep watching real runs for overly generous mic wins.
- Run the full test suite and release safety checks before the next version bump/build.

## External AI Clipping Research: AI Label Graduation

Status: first active Deep-only graduation completed.

Research summary:

- OpusClip exposes a user-facing Virality Score from `0-99`, sorts generated clips by that score by default, and says the score is based on hook, flow, value, and trend/platform interest. It also pairs selected clips with AI-generated titles/descriptions, transcription review, editing, export, and scheduling.
- OpusClip's broader product framing is not just "find a topic"; it packages clips with captions, reframing, B-roll, audio enhancement, and social publishing. Its B-roll page says AI analyzes content to add contextually relevant B-roll, and its product FAQ says its system relates content to social/marketing trends before choosing highlights.
- Vizard's API exposes generated clips sorted by `viralScore`, plus `viralReason`, `relatedTopic`, title, transcript, editor URL, and feedback fields like disliked/starred. This is a useful model for explainable ranking: score plus reason, not score alone.
- Quso/Vidyo positions Virality Score as a predictive score that gives exact tweak guidance and considers visuals, sound, timing, layout, length, tone, and platform fit. It also says gaming and music videos are not currently recommended, which matters for us: ViriaRevive should not copy generic talking-head assumptions too strongly for gameplay.

ViriaRevive direction:

- Do not make "AI label" a vague decorative badge. Graduate it as an explainable, capped **AI Viral Potential** layer for Deep Analysis first.
- Keep deterministic transcript/audio/visual/category ranking as the base. AI should add a small capped nudge only when it agrees with enough local evidence or has high confidence.
- Keep Fast lightweight. Keep Balanced deterministic for now. Use Deep as the first real AI influence path because Deep already pays the runtime cost and has the richest candidate data.
- Output a score and reason pair similar to the commercial tools, but local-first: `ai_viral_score`, `ai_viral_reason`, `ai_dimensions`, `ai_confidence`, `ai_adjustment`, and `ai_rank_delta`.
- Dimensions should map to what Opus-like tools expose, adapted to gameplay:
  - **Hook:** first seconds have panic, surprise, clear setup, funny line, threat, failure, or tutorial promise.
  - **Flow:** clip has setup -> escalation/explanation -> payoff, not just aftermath.
  - **Value:** entertaining, useful, scary, funny, explanatory, beautiful, or story/lore-relevant.
  - **Platform fit:** short, coherent, readable subtitles, no dead air, not mostly menu/stat screen.
  - **Game context:** fight/chase/failure/tutorial/atmosphere/lore evidence from transcript, audio energy, visual diagnostics, and moment categories.
- Because gaming is less supported by some generic tools, use feedback learning as the second gate: AI labels should learn from likes/dislikes/favorites before their cap is increased.

Recommended AI graduation run:

1. Completed first slice: add AI score/reason/dimension metadata to the selected-label and Deep shadow reports, using the existing `ai_moment_classification_shadow` cache and preserving `selection_impact: "none"`.
2. Sanitize the AI response schema so unknown categories become `unknown` / `invalid` diagnostic states instead of `low_value`.
3. Completed: add a tiny capped Deep-only score at `+/-0.015` after deterministic moment-category scoring and before optional voice-profile ranking.
4. Completed: require guardrails before applying the nudge:
   - Ollama/model readiness must be real.
   - AI schema must be valid.
   - AI confidence must be above a threshold.
   - The candidate must not already be rejected by transcript/music/commentary guards.
   - The AI reason must include at least one supported dimension, not only a generic category.
5. Completed: persist debug fields for whether AI added/dropped/kept clips, rank deltas, score source, cap, eligible/scored counts, and why a candidate was or was not eligible for the AI nudge.
6. Keep Results UI simple: show an optional "AI reason" tooltip or detail row, not another first-run setting.
7. Completed first synthetic coverage: cap enforcement, confidence/provider/fallback eligibility, and close-call reorder behavior. Keep malformed response/hard-reject expansion as a later test-hardening item if more AI label edge cases appear.

Implementation map:

- `title_generator.py`: `classify_moment_ai()` schema and prompt should return score dimensions/reasons, not just category labels.
- `api_bridge.py`: `_classify_ai_moment_shadow()`, `_classify_selected_moments()`, `_run_pipeline()` score order, cache reuse, and debug payload fields.
- `candidate_ranker.py`: add `apply_ai_moment_scoring()` and `build_ai_moment_ranking_report()` parallel to moment-category/voice reports; harden `compact_ai_moment_classification()`.
- `gui/app.js`: label tooltip/detail rendering in Results/Preview and "used this run" text in Data & Privacy Local Analysis.
- `tests/test_feedback_reconciliation.py` or a new focused AI-ranking test file: cap, invalid schema, no model, hard-reject guard, and rank-delta reporting.
- `README.md`: documented current AI Viral Potential metadata as local/Ollama-dependent, explainable, diagnostic on the label object, and capped/Deep-only when the separate ranking report is enabled.

Open question before implementation:

- Whether the first active cap should be `+/-0.015` for safer release testing or `+/-0.020` to match deterministic moment-category ranking.

## Scout Sweep: New Edge Cases To Add Before/Alongside AI Graduation

Status: added from read-only subagent sweeps on 2026-06-23.

UI/UX polish findings:

- Completed: separate Results/Preview moment label source handling so `Category`, local fallback, and Ollama AI labels have distinct counts, tooltips, and styles; do not count category-only labels as local classifier output.
  - Why: README distinguishes deterministic categories from local/AI classifier labels, but the UI can count every non-Ollama label as local.
  - Where: `gui/app.js` label/filter summary helpers around moment label source counts and chip classes; README moment-label wording.
- Completed: update Data & Privacy Local Analysis copy to say Fast keeps label ranking off, Balanced/Deep can enable deterministic label ranking by depth, and manual opt-in applies only where depth does not override.
  - Why: current copy can make Balanced's capped label nudge feel hidden.
  - Where: `gui/index.html` Local Analysis copy; `api_bridge.py` `_processing_depth_profile()`; README Processing Depth section.
- Completed: revise Deep Analysis wizard text from "full scene scan" to "deeper targeted scene analysis."
  - Why: long Deep runs use targeted scene scanning, not always exhaustive full-video scanning.
  - Where: `gui/index.html` Detection wizard Deep card; README Detection/Scene sections.
- Completed: add a distinct Generate completion state for "no clips met the quality bar."
  - Why: a normal quality outcome currently reads like a failed batch / `0/1 Videos Done`.
  - Where: `api_bridge.py` no-candidate/no-render paths and batch completion payload; `gui/app.js` final generate banner/status rendering.
  - Desired copy: show the backend suggestion and quick guidance such as try Auto/Quantity, longer duration, smaller gap, or Deep Analysis.
- Completed: clarify Voice Profile enrollment UX by renaming the action to **Build & Enable From Current Clips** and updating the click toast.
  - Why: enrollment currently enables the profile as a side effect.
  - Where: `gui/index.html` Voice Profile controls/copy; `gui/app.js` `enrollVoiceProfile()`; `api_bridge.py` `enroll_voice_profile_from_current_clips()`.

Backend/debug robustness findings:

- Completed: fix ranking debug source reporting so moment-category/voice reports only name their score key when ranking was enabled and at least one candidate had a usable score; otherwise report the actual fallback score source.
  - Why: debug can imply a ranking source was used even when selection fell back.
  - Where: `api_bridge.py` score-key assignment/report wiring; `candidate_ranker.py` moment-category and voice ranking reports.
- Completed: split final debug metadata into pre-render selection/ranking labels versus post-render refined labels.
  - Why: final transcription can refine labels after selection, making final rows confusing about what actually influenced selection.
  - Where: `api_bridge.py` final render loop where refined moments update selected rows and final debug rows are assembled.
- Completed: harden learned scoring against malformed candidate quality values by replacing direct `float(...)` conversions with `_safe_float` fallbacks and adding a synthetic malformed-score regression test.
  - Why: malformed candidate score values should not crash learned scoring/report construction.
  - Where: `candidate_ranker.py` learned scoring/report paths around candidate `quality_score` reads.
- Completed in debug sanitization: change AI moment-label sanitization so unknown primary categories become an explicit `unknown`/`invalid` diagnostic state instead of defaulting to `low_value`, while keeping `selection_impact: "none"`.
  - Why: provider/model/schema drift should not masquerade as an intentional low-value label.
  - Where: `candidate_ranker.py` `compact_ai_moment_classification()` and related AI label schema handling.

Continuous code-improvement scout findings:

- Completed: harden processing-history debug backfill so malformed numeric fields in one old `*_run_debug.json` are skipped or defaulted with safe parsing, without aborting the whole backfill.
  - Where: `api_bridge.py` processing history backfill and `_processing_history_row_from_debug()`.
- Completed: move voice-profile `feature_version` parsing through safe integer parsing so corrupt local profile files fail closed to an unenrolled profile instead of raising.
  - Where: `voice_profile.py` `sanitize_voice_profile()`.
- Completed: sanitize stream-selector candidate moments with safe score/start/end parsing and skip invalid windows instead of raising during audio stream inspection.
  - Where: `speech_stream_selector.py` stream candidate sorting/window extraction.
- Completed: gate crop debug-frame writing behind an explicit debug setting and save under app debug/cache output with a run-specific filename.
  - Where: `cropper.py` crop debug frame save paths.
- Completed: escape schedule `time`, `privacy`, and status text wherever schedule rows/chips are rendered via `innerHTML`, or render them with `textContent`.
  - Where: `gui/app.js` schedule row/day/timeline rendering.
- Completed: normalize empty captured subprocess output to `''`/`b''` based on text/binary mode so quiet failures do not cause secondary stdout/stderr errors.
  - Where: `subprocess_utils.py` capture assembly.
- Completed: coerce progress/result card scores through a finite-number helper before comparisons and `toFixed()`.
  - Where: `gui/app.js` progress/result card score rendering.
- Completed: preserve captured stdout/stderr on subprocess timeout/cancel paths so media timeout logs include useful diagnostic tails.
  - Where: `subprocess_utils.py` timeout handling.
- Completed: resolve yt-dlp's actual downloaded/merged filepath from `requested_downloads[].filepath` or `info["filepath"]`, with `prepare_filename()` only as a fallback, in both CLI and GUI download paths.
  - Where: `downloader.py` and `api_bridge.py` GUI download path.
- Completed: gate full ffmpeg command echoing behind a verbose/debug flag; keep concise phase/status lines by default and print command tails only on failure.
  - Where: `clipper.py` render command logging.

## Data & Privacy Advanced Window

Status: completed this run.

- Make the advanced Data & Privacy modal larger vertically and horizontally so the tab row has room.
- Remove or avoid the awkward horizontal scrollbar in the tab row.
- If overflow is still needed at very small window sizes, style the scrollbar consistently with the rest of the app.
- Keep the beginner-friendly card small, but let the advanced modal breathe.

Implementation map:

- `gui/index.html`: `#data-privacy-modal`, around the advanced modal markup.
- `gui/style.css`: `.data-privacy-modal-body`, `.data-privacy-tabs`, `.data-privacy-tab`, plus the small-screen override for the modal.
- `gui/app.js`: `setDataPrivacyTab()` and `openDataPrivacyModal()`.
- Useful selectors/search terms: `#data-privacy-modal`, `.data-privacy-tabs`, `[data-privacy-tab]`, `[data-privacy-panel]`.
- Likely approach: increase modal width/usable height, make tabs wrap or fit instead of horizontally scrolling, and only add app-styled scrollbar rules if overflow remains necessary on short/narrow viewports.

## Subtitle Style Settings

Status: completed this run.

- Move **Refresh Preview** and **Reset** into one horizontal action row.
- Put **Refresh Preview** on the left and **Reset** on the right.
- Recheck spacing around the preview snapshot and sliders after the button move.

Implementation map:

- `gui/index.html`: subtitle placement markup near `.subtitle-preview-actions`; both buttons are already in the same row.
- `gui/style.css`: `.subtitle-placement-panel`, `.subtitle-placement-preview`, `.subtitle-placement-controls`, `.subtitle-preview-actions`, `.subtitle-placement-controls .setting-row`.
- `gui/app.js`: `refreshSubtitlePreviewSnapshot()`, `resetSubtitlePreviewSnapshot()`, `updateSubtitlePlacementPreview()`, and subtitle branch in `updateSliderLabel()`.
- Useful selectors/search terms: `#subtitle-placement-preview`, `.subtitle-preview-actions`, `#set-subtitle-x`, `#set-subtitle-y`, `#set-subtitle-width`.
- Likely approach: set `.subtitle-preview-actions` to a non-wrapping row with `justify-content: space-between`, give buttons stable sizing if needed, then adjust bottom spacing before the slider rows.

## Feedback Modal Button Spacing

Status: previously completed.

- Implemented quick UI pass: the Like/Dislike/Favorite feedback modal now uses a feedback-specific action row so **Cancel** and **Save Feedback** have clearer spacing from the note field and better narrow-width behavior.

Implementation map:

- `gui/index.html`: `#feedback-modal`, `.feedback-modal-body`, and `.feedback-modal-actions`.
- `gui/style.css`: `.feedback-modal-body`, `.feedback-modal-body textarea.form-input`, `.feedback-modal-actions`, and the narrow viewport override for `.feedback-modal-actions`.
- `gui/app.js`: modal behavior lives in `recordClipFeedback()`, `recordPreviewFeedback()`, `recordLibraryFeedback()`, `openFeedbackModal()`, `closeFeedbackModal()`, and `submitFeedbackModal()`. No JS change was needed for this spacing pass.

## Detection Wizard

Status: completed this run.

- Rework **Output preference** based on the **Number of clips** setting.
- If the user chooses a fixed number of clips, show the effective preference as **Quality** instead of a dropdown.
- If **Number of clips** is set to **Auto**, show the dropdown so the user can choose **Quality** or **Quantity**.
- Keep the wording beginner-friendly and avoid adding another new setting.

Implementation map:

- `gui/index.html`: Detection wizard controls for `#wizard-detection-preference` and `#wizard-num-clips`.
- `gui/style.css`: `.wizard-detection-grid`, `.wizard-detection-row`, and any static-value styling added beside the existing select.
- `gui/app.js`: `normalizeDetectionPreference()`, `openStylePicker()`, `syncWizardEstimate()`, `setWizardClipCount()`, wizard listeners, and `confirmStyleAndGenerate()`.
- Backend reference only: `api_bridge.py` `_run_pipeline()` reads `num_clips`, `auto_clips`, and `detection_preference`; auto-count quality/quantity modifiers live later in that same pipeline.
- Useful selectors/search terms: `#wizard-detection-preference`, `#wizard-num-clips`, `#wizard-estimate`.
- Likely approach: add a small UI sync helper. When `#wizard-num-clips` is `auto`, show/enable the output-preference dropdown. When fixed, hide or replace the dropdown with static **Quality** and force the submitted `detection_preference` to `quality` in `confirmStyleAndGenerate()`. Update `syncWizardEstimate()` so fixed-count wording reflects the effective Quality mode.

## Current Test Notes

Status: superseded by completed Fast/Balanced/Deep review notes below.

- User is running a Fast test on a clip after the clean reinstall.
- Await follow-up notes from that run before coding these items.

## Graduate Shadow Features Into Real Ranking

Status: partially completed overall; this run completed AI label graduation, Voice Profile feedback nudge, and the first category-diversity pass. Remaining work is release validation plus real-run tuning.

- User direction: stop leaving mature systems as shadow-only once tests show they are useful. Keep the UI beginner-friendly by rolling these into existing **Fast / Balanced / Deep Analysis** behavior instead of adding more switches.
- Latest Balanced run reviewed: `%LOCALAPPDATA%\ViriaRevive\subtitles\2026-06-09 23-27-05-vertical_run_debug.json`.
- Balanced run result: success, 5 clips from 25 candidates, sampled scene detection completed, visual diagnostics ran on all 25 candidates, subtitles rendered cleanly.
- Current issue: several systems record excellent data but either do not affect clip selection yet or only affect it when hidden prerequisites are met.
- Completed this run: **Balanced** now forces deterministic moment-category ranking through `_processing_depth_profile()` while **Fast** keeps it off and **Deep Analysis** keeps the fuller local-analysis stack. The ranking path remains capped at `+/-0.020`.
- Completed this run: confirmed black-frame candidates and stat/results/end-screen wording now feed low-value category evidence through the same capped path, unless stronger creator payoff/failure/tutorial/story evidence wins.

Current influence inventory:

- **Already active:** scene/audio/variance candidate scoring, transcript scoring, speech/game audio stream selection, mixed final audio rendering, learned feedback scoring when feedback signals exist, and deterministic moment-category ranking in Balanced/Deep.
- **Active but had no influence in the latest Balanced run:** learned feedback scoring, because the run started before the new like/dislike feedback event existed.
- **Active through category ranking:** deterministic moment categories and visual diagnostics can now nudge close calls in Balanced/Deep; black-frame and stat/end-screen evidence down-rank low-value candidates only through the cap.
- **Active in Deep only:** AI/Ollama moment labels still save non-mutating label metadata, but Deep Analysis can now run a separate capped `ai_moment_ranking` pass on the shortlist when real high-confidence Ollama labels pass guardrails.
- **Conditional but inactive:** voice profile ranking. It is enabled in settings, but the local voice profile has no enrolled samples yet, so it cannot score candidates.
- **UI/runtime only:** processing-history ETA and browser preview thumbnails. These help the user experience but do not influence clip choice.
- **Active through guarded quality:** creator-policy commentary guard can now down-rank high-confidence game/NPC narration by a small `0.060` cap when filtering cannot safely recover enough creator words.

Recommended activation order:

1. Keep **Fast** lightweight.
2. Completed: **Balanced** now auto-enables deterministic moment-category ranking by depth and the Local Analysis tab can show **On by depth**.
3. Keep **Deep Analysis** as the “use everything reasonable” path, but verify it actually gets nonzero AI and voice impact once prerequisites are met.
4. Completed: visual diagnostics influence selection through capped category scores plus a confirmed black-frame low-value guard.
5. Completed first active step: Deep Analysis now writes a pre-final AI/Ollama shortlist report and can feed high-confidence real Ollama labels into a tiny capped ranking nudge.
6. Completed for the deterministic guard: creator-policy commentary/game-audio guard now down-ranks high-confidence game/NPC narration with a small capped quality penalty. Music/lyrics guarding was already active.
7. Completed: add clearer voice-profile enrollment status and a post-feedback nudge for eligible liked/favorite creator-commentary clips; once enrolled, keep its capped adjustment small and use it as a tie-breaker.
8. Show in debug/results whether learning/category/voice/AI actually changed a clip rank, so “used this run” is obvious.

Next safe scouts:

- AI labels: Deep-only pre-final shortlist is now debug metadata plus a guarded `+/-0.015` active nudge for real high-confidence Ollama labels. Next step is to inspect real run reports before changing the cap.
- Visual/local labels: visual diagnostics and deterministic categories already influence selection through capped category ranking. Tutorial/story/atmosphere now receive smaller positive signals plus a tiny diversity helper; keep watching feedback before increasing their weight.
- Selection-contract test: completed for visual diagnostics plus AI labels when category ranking is disabled.

Implementation map:

- `candidate_ranker.py`: `score_moment_categories()`, `apply_moment_category_scoring()`, `build_moment_category_ranking_report()`, `apply_learned_scoring()`, `apply_voice_profile_scoring()`, `classify_commentary_guard()`, `apply_commentary_subtitle_policy()`, `commentary_guard_selection_penalty()`.
- `api_bridge.py`: `_processing_depth_profile()`, `_run_pipeline()`, `_classify_selected_moments()`, `_voice_profile_score_for_wav()`, processing-history recording.
- `visual_diagnostics.py`: `analyze_candidate_visuals()` and visual metric fields already attached to candidates.
- `title_generator.py`: `classify_moment_ai()` for Ollama-backed moment labels.
- `speech_stream_selector.py`: `select_speech_stream()`, `score_stream_profile()`, `analyze_audio_bed()`.
- `gui/app.js`: result labels, Data & Privacy feature wording, and any “used this run” display.

Latest run notes:

- Balanced selected all five clips as `high_energy`; this run added a small diversity helper and broader tutorial/story/atmosphere scoring so close calls can diversify without forcing weak clips.
- Scene detection status was `sampled_ok`; FFmpeg stderr had harmless empty-window messages, but return code was `0`.
- Deep test now has a pre-render candidate report at `%LOCALAPPDATA%\ViriaRevive\subtitles\2026-06-16 22-56-14-vertical_candidate_debug.json`.
- Current Deep pre-render status: `candidate_count=35`, `selected_count=5`, scene detection `targeted_ok`, visual diagnostics `ok` on 35 candidates, moment-category ranking enabled with capped impact, AI/Ollama labels enabled and `ollama_ready=true`, voice ranking inactive because the voice profile is not enrolled.

README update map:

- `README.md` fork/features section: clarify that Processing Depth can override analysis toggles.
- `README.md` Detection and Ranking section: completed for Balanced/Deep depth-enabled category ranking and capped black-frame/stat-screen low-value guards.
- `README.md` Moment Classification section: distinguish deterministic `moment_categories` from post-selection `ai_moment_classification`.
- `README.md` CLI Mode section: completed for default-off `--moment-category-ranking` parity with GUI deterministic category scoring.
- `README.md` Local Analysis / Data & Privacy section: explain what is on by setting vs on by depth, and show the ranking caps.
- `README.md` Debug Reports section: document `selection_score_source`, `moment_category_scoring`, AI labels, voice ranking, rank deltas, and added/dropped/kept output.

## Feedback Persistence And Deleted Clip Learning

Status: completed this run for compact learning terms, deletion persistence, and per-action reason preservation.

- Current status: thumbs up, thumbs down, favorite, and feedback reasons all go through the same feedback API and are persisted in `personalization.json`.
- Current status: feedback stores separate active reasons for like, dislike, and favorite. Blank favorite toggles no longer erase an active like/dislike reason.
- Current status: learned scoring is already wired into selection. It reads the latest active per-clip feedback state, so removed likes stop counting and like/dislike flips do not leave stale old signals.
- Current weights: favorite is positive and stronger than like, like is positive, dislike is negative.
- Current status: deleting a clip removes the video file/results/moment/schedule entry, but does **not** delete `personalization.json`, so feedback survives if it was recorded before deletion.
- Caveat: if a clip is deleted before feedback is recorded, the app may no longer have the rich in-memory moment snapshot needed for strong learning.
- Desired behavior: disliked/favorited/liked learning should stick even after files are deleted, using compact metadata only. Do not store the full video file or thumbnails in the learning file.

Completed changes:

1. Added compact `learning_terms` to feedback events and clip summaries.
2. Preserved compact learning metadata when state pruning or explicit clip deletion removes rendered moments.
3. Taught learned scoring to use compact `learning_terms` when full transcript snapshots are absent.
4. Added tests proving liked/disliked `learning_terms` affect matching candidates, removed feedback no longer counts, and like -> dislike flips correctly.
5. Added tests proving feedback by `clip_id` can reuse stored terms after the clip is missing.
6. Added tests proving delete actions keep personalization and mark matching feedback entries as file-deleted.
7. Added redaction coverage so compact learning terms are removed from share-safe exports.
8. Preserved per-action/per-flag reasons so favorite toggles do not wipe useful dislike/like reason context.
9. Added tests for legacy reason migration, event replay with placeholder summaries, string false toggle parsing, and per-action reason redaction.

Remaining follow-up:

1. Consider a small UI/readout for file-deleted feedback entries if users need to audit why learning still exists after clips are gone.

Implementation map:

- `gui/app.js`: `recordClipFeedback()`, `recordPreviewFeedback()`, `recordLibraryFeedback()`, `openFeedbackModal()`, `submitFeedbackModal()`, and `feedbackButtonsMarkup()`.
- `api_bridge.py`: `_feedback_identity_for()`, `_feedback_clip_snapshot()`, `record_feedback()`, `_prune_missing_results()`, `delete_clip()`, `delete_library_file()`, `_save_personalization()`.
- `candidate_ranker.py`: `_current_feedback_signals()`, `_signals_from_clip_summaries()`, `_signals_from_event_replay()`, `_add_feedback_signal()`, `_score_shadow_candidate()`, `apply_learned_scoring()`.
- `tests/`: extend learned-scoring and feedback-deletion tests with no-video synthetic cases.

README update map:

- Completed: `README.md` Personalization section states deleting clip files does not delete feedback learning and documents per-action reasons.
- Completed: `README.md` Privacy section documents compact learning terms and that raw videos/thumbnails/audio are never stored in feedback.
- Completed: `README.md` Debug/State section documents learned scoring fallback and redacted export handling for compact learning terms and per-action reasons.

## Voice Profile Activation

Status: completed for the current run; readiness/status clarity, enrollment quality guards, and the post-feedback opt-in nudge are done.

- Current status: voice-profile scoring can influence candidate selection today, but only when all gates are true: profile enabled, profile enrolled, voice ranking enabled, candidates have voice scores, and the active depth/settings allow voice ranking.
- Completed this run: Data & Privacy now separates **Off**, **Needs samples**, **Ready**, and **Influencing**, plus backend fields for `readiness`, `can_score`, `can_rank`, `influence_state`, `blocking_reason`, and user-facing guidance.
- Current local status from test machine: `voice_profile.json` exists and is enabled, but `enrolled=false`, `sample_count=0`, and `centroid=[]`; therefore it cannot influence selection yet.
- Current Deep behavior: Deep Analysis can collect richer voice diagnostics, but voice-profile ranking still respects the explicit **Use Voice In Ranking** opt-in and has no effect until the profile is enrolled.
- Desired behavior: once the app can confirm a valid local profile with enough samples, voice profile should make a small capped influence in Balanced/Deep without feeling mysterious. The new feedback nudge helps users discover that path after liking or favoriting clean creator-commentary clips.

Recommended changes:

1. Completed: improve the Voice Profile tab status so it says plainly whether the profile is ready, missing samples, or influencing the current run.
2. Completed: add a post-feedback nudge to build/update the voice profile from liked/favorite creator-commentary clips, while keeping enrollment and ranking opt-in.
3. Completed: add a guard that only enrolls clips with creator-commentary confidence, enough active speech, low game/music contamination, no filtered/mixed transcript output, and current commentary-guard metadata.
4. Completed: once enrolled, Balanced and Deep both respect the existing voice-ranking setting instead of forcing voice ranking by depth.
5. Keep the current small cap (`+/-0.025`) until tests show it is stable.
6. Add debug output that says how many candidates were voice-scored and whether voice ranking added/dropped/kept clips.

Implementation map:

- `voice_profile.py`: `extract_voice_features()`, `update_voice_profile()`, `score_voice_profile()`, `voice_profile_status()`.
- `api_bridge.py`: `enroll_voice_profile_from_current_clips()`, `_voice_profile_status_payload()`, `set_voice_profile_enabled()`, `set_voice_profile_ranking_enabled()`, `_voice_profile_score_for_wav()`, `_run_pipeline()`.
- `candidate_ranker.py`: `apply_voice_profile_scoring()`, `build_voice_profile_shadow_report()`, `build_voice_profile_ranking_report()`.
- `gui/app.js`: `renderVoiceProfileStatus()`, `toggleVoiceProfileEnabled()`, `toggleVoiceProfileRanking()`, `enrollVoiceProfile()`, Data & Privacy status refresh.

README update map:

- Completed: `README.md` Voice Profile section explains guarded enrollment, local-only numeric centroid, no raw audio storage, current gates, temp-WAV cleanup, compact skip reasons, backup behavior, and capped influence.
- Completed: `README.md` Data & Privacy section explains enabled/enrolled/influencing states.
- `README.md` Debug Reports section: voice status/readiness fields are documented; deeper voice scoring counts/cap/rank delta remain covered by existing debug text.

## Latest Deep Run Findings Before Release

Status: partially completed; final render/debug mismatch, stat/end-screen low-value guard, Balanced category-ranking activation, first category diversity, AI label graduation, and Voice Profile nudge are completed. Remaining work is real-run validation and release checks.

- Source reviewed: `%LOCALAPPDATA%\ViriaRevive\subtitles\2026-06-16 22-56-14-vertical_run_debug.json`.
- Run: `20260622_195059`, Deep Analysis, source duration about `116.97min`, elapsed about `18m56s`.
- Outcome: success, `35` candidates, `5` selected/rendered clips.
- Speed note: this was much faster than the earlier long-video Deep experience. Targeted scene scanning plus the `small` candidate Whisper model made Deep feel practical on a roughly 2-hour file.
- Timing breakdown: detect `263.766s`, targeted scene detection `251.172s`, visual analysis `30.984s`, candidate analysis `438.219s`; remaining time was final render/transcription/bookkeeping.
- Scene detection: `targeted_ok`, `40` target windows, about `1716s` of targeted timeline scanned, return code `0`.
- Visual analysis: `ok`, all `35/35` candidates sampled, `105` frames read, no warnings.
- AI labels: enabled, Ollama ready, `5/5` selected clips classified; `4 ok`, `1 ollama_error` fallback.
- Learned feedback: active from one positive event; selected clips received nonzero learned adjustments around `+0.054` to `+0.060`; ranks shifted but selected set did not change.
- Moment-category ranking: active with capped `+/-0.020`; ranks shifted but selected set did not change.
- Voice ranking: inactive because profile is not enrolled.
- Audio source: auto selected `Track3_vertical`, reason `creator_phrase_signal`, confidence `1.0`; final render mixed all source audio streams.

Release-facing lessons:

1. Keep targeted Deep scene detection. This run supports the current targeted-window approach as the default Deep path for long videos.
2. Keep candidate Whisper on `small` for Deep candidate screening unless later tests show quality loss; final subtitles still use the selected model path.
3. Preserve learned scoring and moment-category scoring as real capped rank influences. They nudged ranks without destabilizing output.
4. Completed: final render duration now preserves the selected candidate window while shifting trim-relative subtitles onto the render timeline.
5. Completed: stat/results/end-screen wording now down-ranks low-value candidates through the capped moment-category path unless a stronger creator payoff/failure/tutorial/story signal wins.
6. Completed first slice: broader category diversity/accuracy now gives smaller positive signals to tutorial/explainer, lore/story, and atmosphere moments plus a tiny diversity helper for close calls.
7. Completed first slice: AI labels still save non-mutating label metadata, and Deep Analysis can now use a separate guarded `+/-0.015` ranking report when real high-confidence Ollama labels pass eligibility checks.
8. Completed: final `final_clips[]` debug rows now carry compact category, visual, audio-stream, subtitle, and transcript metadata.

## Pre-Release Must Fix / Polish List

Status: core trust/debug items completed this run; broader real-run tuning and release verification remain pending.

- [x] Detection wizard output preference:
  - Fixed clip count should effectively use **Quality**.
  - Only **Auto** clip count should expose **Quality / Quantity** choice.
  - Code map: `gui/index.html` `#wizard-detection-preference`, `#wizard-num-clips`; `gui/app.js` `syncWizardEstimate()`, `confirmStyleAndGenerate()`, wizard listeners; `api_bridge.py` `_run_pipeline()` fixed-count handling.

- [x] Progress stage checkmarks:
  - During active runs, completed prior stages should show checkmarks as soon as the pipeline advances.
  - Code map: `gui/app.js` `window.onPipelineProgress()`, `activateStage()`, `completeStage()`, `completeStageThrough()`.
  - Test map: `tests/test_gui_static_guards.py`.

- [x] Scene sampling detail line:
  - Clear `Sampling scenes: ... / ...` once scene sampling is complete, especially while Detect has moved to audio inspection/transcription.
  - Code map: `gui/app.js` `setProgressDetail()`, `window.onPipelineProgress()`; backend option in `api_bridge.py` `_run_pipeline()` after `find_viral_moments()`.

- [x] Data & Privacy advanced modal:
  - Make modal wider/taller, avoid the awkward horizontal tab scrollbar, and keep the scrollbar style consistent if overflow is unavoidable.
  - Code map: `gui/style.css` `.data-privacy-modal-body`, `.data-privacy-tabs`, `.data-privacy-tab`; `gui/index.html` `#data-privacy-modal`.

- [x] Final render duration/debug mismatch:
  - Add a guard/test so final clip duration, final transcript window, and selected candidate interval are either aligned or explicitly documented as trim-adjusted.
  - Code map: `api_bridge.py` final render loop, final clip metadata assembly; `candidate_ranker.py` selected candidate/debug report helpers.

- [x] Final debug metadata:
  - Add direct `primary_category`, `moment_categories`, `moment_category_scoring`, `music_lyrics_guard`, selected stream summary, original selected interval, and trim-adjusted interval to `final_clips[]`.
  - Code map: `api_bridge.py` final clip row assembly; `candidate_ranker.py` `write_debug_report()`.

- [x] README before release:
  - Update Processing Depth behavior, especially Fast/Balanced/Deep overrides.
  - Update Detection/Ranking to describe fixed-count Quality behavior once implemented.
  - Update Moment Classification to distinguish deterministic ranking categories from AI/Ollama selected-clip labels.
  - Update Personalization to describe compact deleted-clip learning terms without implying raw media is stored.
  - Update Voice Profile wording to explain enabled vs enrolled vs actively influencing.
  - Update Debug Reports with rank deltas, score source, and final metadata fields after the debug cleanup lands.

Release verification:

- [x] Run `venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_*.py"`.
- [x] Run `scripts\check_version_sync.py`.
- [ ] Before the release commit/tag, ensure untracked runtime modules and tests are staged intentionally, especially `voice_profile.py` and `visual_diagnostics.py`.
- [ ] Confirm public binary dependency/license posture before uploading broad-distribution installers. Current metadata includes `ultralytics==8.4.71` reporting `AGPL-3.0`, `pystray==0.19.5` reporting `LGPLv3`, and PyInstaller reporting GPL with its bundling exception.
- [ ] Decide whether this release requires system FFmpeg or should bundle `bin/ffmpeg.exe` and `bin/ffprobe.exe`; current `bin/` contains only `README.md`.
- [ ] Run `build.bat`.
- [ ] Run installer build only if shipping a setup EXE for this version.

## Generate Progress Checkmarks

Status: completed this run.

- Observed issue: **Detect** does not show a checkmark once the pipeline has clearly advanced into **Candidates** or **Render**, but it becomes checked at final completion.
- Latest user observation: at the end all stage checkmarks appear correctly; the issue is only during the active run.

Implementation map:

- `gui/index.html`: progress stage DOM at `#progress-area`, `.stage[data-stage="download|detect|candidates|clips|done"]`.
- `gui/style.css`: checkmark visibility is driven by `.stage.completed .stage-dot`; active-only stages keep the SVG visually inactive.
- `gui/app.js`: `window.onPipelineProgress()`, `window.onClipProgress()`, `activateStage()`, `completeStage()`, `completeStageThrough()`.
- `api_bridge.py`: `_push()`, `_clip_push()`, `_run_pipeline()`.
- Useful selectors/search terms: `.stage[data-stage="${name}"]`, `.stage-line`, `#progress-status`, `#progress-percent`, `#progress-fill`, `#progress-detail`.
- Probable cause: frontend only calls `completeStage('detect')` when it sees `stage === 'detect' && percent >= 100`, but the backend moves from `detect` around 60% directly into `candidates`. Final success calls `completeStageThrough('done')`, which explains why Detect checks only at the end.
- Likely approach: when activating a later stage, complete all prior stages. For example, `candidates` implies `download` and `detect` are completed, while `clips` implies `download`, `detect`, and `candidates` are completed. Alternative backend fix is to emit `_push("detect", 100, ...)` immediately before `_push("candidates", 0, ...)`.
- Add or update a static guard in `tests/test_gui_static_guards.py`.

## Scene Sampling Detail Line

- Observed issue: the detail line such as `Sampling scenes: 02:29:00 / 02:29:00` stays visible after scene sampling has finished, even while the console has moved on to audio track inspection and transcription.
- Desired behavior: show the scene timeline detail only while the scene scan is actively running or while the console would otherwise look still during scene analysis. Once scene sampling has passed, hide the detail line during audio inspection/transcription and later stages.
- Clarification from testing: the detail already disappears once the UI reaches **Candidates**. The problem is narrower: during the remaining **Detect** substages, it can sit at a mirrored/completed value like `02:29:00 / 02:29:00`, which reads as stale because scene sampling is already done.

Implementation map:

- `gui/index.html`: `#progress-detail` under the main progress bar.
- `gui/style.css`: `.progress-detail` and `.progress-detail.hidden`.
- `gui/app.js`: `setProgressDetail(detail)`, `window.onPipelineProgress()`, `window.onClipProgress()`, and any stage transition helpers around progress updates.
- `api_bridge.py`: `_scene_progress(detail)` inside `_run_pipeline()` pushes scene detail with `_push("detect", 18, ..., detail=detail)`.
- `detector.py`: `_notify_scene_progress()` formats `Sampling scenes: HH:MM:SS / HH:MM:SS` or `Detecting scenes: HH:MM:SS / HH:MM:SS`.
- Useful search terms: `progress-detail`, `setProgressDetail`, `_scene_progress`, `_notify_scene_progress`, `Sampling scenes`, `Detecting scenes`.
- Likely approach: clear detail once scene progress reaches the total duration, or clear it on the first later Detect progress update that is not scene-related. Avoid changing the already-good Candidates behavior.
- Backend-side option: send `detail: ""` immediately after `find_viral_moments()` returns and before audio stream inspection/transcription begins.
- Frontend-side option: if `onPipelineProgress()` receives a Detect update with no scene `detail`, clear `#progress-detail`; keep scene details visible only while the incoming text begins with `Sampling scenes:` or `Detecting scenes:` and has not reached a completed mirrored value.
- Test/guard idea: add a static GUI guard that non-scene Detect progress clears `#progress-detail`, or a backend test that after scene detection finishes the next Detect push has empty detail.

## Fast Run Feature Usage Notes

- Latest Fast run after clean reinstall:
  - Source: `D:\Recording Video Files\Alan Wake\Vertical\2026-06-09 00-42-13-vertical.mkv`.
  - Run debug: `%LOCALAPPDATA%\ViriaRevive\subtitles\2026-06-09 00-42-13-vertical_run_debug.json`.
  - Processing depth: `fast`.
  - Runtime: about `228s` for about `186min` of video.
  - Candidates: `15`; accepted candidates: `3`; rendered clips: `2`.
- Fast profile behaved as designed:
  - Scene detection skipped: `scene_detection.status = "skipped"`, `mode = "skip"`.
  - Visual diagnostics disabled.
  - AI moment labels disabled.
  - Moment-category ranking disabled.
  - Voice-profile ranking disabled.
  - Learned-scoring report present, but no feedback signals yet.
  - Music/lyrics guard ran and did not penalize candidates.
  - Stream selection ran.
- Interpretation: getting the same 2 clips in Fast is not surprising. Fast had only 3 accepted candidates above the current quality floor, so either the source segment is thin for short-form moments or Fast is intentionally too shallow to discover the better alternates.
- Completed: Data/debug screens now state when scene, visual, AI labels, moment-label ranking, and voice ranking are inactive because Fast is intentionally lightweight.

## Stream Selection Review

- Latest Fast run selected stream ordinal `1`, title `Track3_vertical`, reason `more_whisper_words`, confidence `0.705`.
- Runner-up was stream ordinal `0`, title `Microphone_vertical`.
- Oddity: `Microphone_vertical` has obvious voice title hints and likely higher creator-likeness, but `Track3_vertical` won because the selection score still leans heavily on word count (`129` vs `115` sampled words).
- The selected transcript still looked like creator speech, so this is not automatically wrong, but it is worth reviewing the stream-selection weighting.

Implementation map:

- `speech_stream_selector.py`: `score_stream_profile()` around the selection score weighting and stream profile fields.
- `speech_stream_selector.py`: `select_speech_stream()` / final selection path around where best and runner-up are chosen.
- Debug fields to compare: `voice_title_hints`, `creator_likeness_score`, `selected_words`, `selected_reason`, `natural_dialogue_score`, `lyric_likelihood`, `acoustic_game_bed_score`.
- Completed first slice: `speech_stream_selector.py` now adds a bounded `mic_creator_preference_bonus` only when a voice-labeled track has enough evidence and looks creator-like. The bonus is skipped for tiny samples, lyric-like text, strong game beds, and scripted/game-dialogue-dominant samples.

## Run Debug Final Clip Metadata

- Latest run debug candidate rows contain useful metadata such as `moment_categories`, `music_lyrics_guard`, `ranker`, and detailed selection scores.
- `final_clips` rows are thinner: they include rendered path, subtitle status, transcript, timing, and quality, but do not repeat moment categories, AI label objects, music/lyrics guard, selected stream, or primary category.
- This does not appear to break the app, but it weakens run-debug inspection because the rendered clip rows need cross-reference back to candidate rows.

Implementation map:

- `candidate_ranker.py`: `write_debug_report()` and final run-debug row composition.
- `api_bridge.py`: final clip metadata assembly before writing `*_run_debug.json`.
- Likely approach: keep `*_candidate_debug.json` as pre-render truth, but copy compact final-facing metadata into `final_clips[]`: `primary_category`, `moment_categories`, `music_lyrics_guard`, `audio_source.selected_stream`, `word_count`, `subtitle_generated`, `subtitles_burned`, and any AI/visual summaries that exist.
