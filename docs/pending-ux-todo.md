# Pending UX Todo

Captured on 2026-06-22 while testing the v2.3.0 release candidate. This file is now status-based: completed items are listed first, and remaining items should be tackled in small follow-up passes.

## Cleanup Sweep 2026-06-24

Completed:

- Removed stale dynamic YouTube category lookup. Upload category is fixed to Gaming in the UI/backend/uploader path.
- Removed legacy `.scheduled-list*` / `.sched-*` CSS after confirming the active Upload UI uses the calendar/timeline layout.
- Upload view-load no longer persists an empty schedule when clip scanning returns no visible clips.
- Missed local-watcher protection now applies to private/unlisted/public rows instead of public-only rows.
- Ollama setup controls now use normalized text/vision status helpers and keep the download/help action available when the install path is not discoverable.
- Candidate-debug recovery remains hidden from normal UI and now requires `VIRIAREVIVE_ENABLE_DEBUG_RECOVERY=1`.
- Visible metadata wording is standardized on **AI Notes** for per-clip title/description hints.
- Upload clip refresh now only shows the success toast after the scan/fallback completes.
- `multimodal_analysis.py` and `tests/test_multimodal_analysis.py` are staged for the next commit, and `viria.spec` explicitly includes `multimodal_analysis`.
- Rebuilt `dist/` and `release/` for v2.3.1, including the ZIP package, latest ZIP copy, installer EXE, and SHA256 files.
- FFmpeg/dependency license-source signoff completed for this build: release compliance/safety passed, generated notices include FFmpeg GPL/source links, AGPL dependency notes, package licenses, and native media library hashes.

Remaining:

- Consider returning explicit persistence errors from schedule/upload APIs instead of only printing `_save_state()` failures.
- Add hidden-selection warnings for Results/All Videos bulk delete when filters/search hide selected clips.
- Decide whether library preview should support delete or hide the shared delete affordance there.
- Improve calendar channel filtering summaries so filtered timelines and channel tabs count the same row set.
- Add keyboard semantics to clickable calendar/detail/clip surfaces that are currently `div` click targets.

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
  - Results/Preview moment labels now separate deterministic **Detected** labels from **Fallback** and **Ollama** context labels in counts and chip styling.
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
  - Full unittest discovery passed this run: 340 tests.
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

Resolved:

- The first active AI cap is `+/-0.015` for safer release testing.

## Scout Sweep: New Edge Cases To Add Before/Alongside AI Graduation

Status: added from read-only subagent sweeps on 2026-06-23.

UI/UX polish findings:

- Completed: separate Results/Preview moment label source handling so `Detected`, `Fallback`, and `Ollama` labels have distinct counts, tooltips, and styles; do not count detected-only labels as fallback classifier output.
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
- [ ] Before the release commit/tag, ensure untracked runtime modules and tests are staged intentionally, especially `multimodal_analysis.py` and `tests/test_multimodal_analysis.py`.
- [ ] Confirm public binary dependency/license posture before uploading broad-distribution installers. Current metadata includes `ultralytics==8.4.71` reporting `AGPL-3.0`, `pystray==0.19.5` reporting `LGPLv3`, and PyInstaller reporting GPL with its bundling exception.
- [ ] FFmpeg is bundled in `bin/` for the public build; confirm GPL/source-notice posture before uploading broad-distribution installers.
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

## All Videos Label Clarity And Bulk Delete

Status: completed for the first pass. Folder open-state persistence, All Videos multi-select delete, AI chip source clarity, deterministic-first label ordering, compact `moment_categories` in All Videos, and visual-only failure fine-label hardening are complete with focused tests.

Observed issues:

- Teal **AI** labels feel less accurate than the orange category labels in some runs.
- Some clips can show a fine label such as **Death scene** even when no death actually happened.
- Completed: in **All Videos**, deleting a clip no longer collapses the source folder that was open.
- Completed: **All Videos** supports multi-select deletion so users can remove several clips at once.

Label source findings:

- Orange `Detected` chips are deterministic base labels from `primary_category` / compact `moment_categories.primary`.
- Teal `Ollama` chips are local Ollama labels from `ai_moment_classification` when `status == "ok"` and `provider == "ollama"`.
- Purple `Fallback` chips are fallback/heuristic labels when Ollama is unavailable, skipped, or not ready.
- When Ollama/fallback primary differs from deterministic category primary, the deterministic **Detected** chip appears first and the Ollama/Fallback label is secondary context.
- All Videos now gets compact `moment_categories` for known clips, so it can make the same deterministic-vs-AI distinction as Results.

Label implementation map:

- `gui/app.js`: `aiMomentForClip()` chooses `Ollama`, `Fallback`, or `Detected`.
- `gui/app.js`: `momentLabelMarkup()` renders the visible chip row.
- `gui/app.js`: `_buildResultCard()` renders Results labels.
- `gui/app.js`: `_buildLibraryCard()` renders All Videos labels.
- `gui/style.css`: `.moment-chip.is-ai`, `.moment-chip.is-local`, `.moment-chip.is-category` define chip colors.
- `api_bridge.py`: `_clip_payload()` feeds Results with `primary_category`, `moment_categories`, and `ai_moment_classification`.
- `api_bridge.py`: `list_all_clips()` feeds All Videos with known clip metadata.
- `candidate_ranker.py`: `score_moment_categories()` computes deterministic category scores.
- `title_generator.py`: `classify_moment_ai()` and `_heuristic_moment_classification()` create Ollama/fallback labels.
- `visual_diagnostics.py`: `possible_failure_score` can contribute broad failure/death-looking evidence.

Recommended label fix:

- Completed: rename chip prefixes for clarity:
  - `AI` -> `Ollama`
  - `Local` -> `Fallback`
  - `Category` -> `Detected`
- Completed: if Ollama/fallback primary differs from deterministic category primary, show the deterministic **Detected** chip first and the Ollama/fallback label as secondary context.
- Completed: include compact `moment_categories` for known clips in `list_all_clips()` so All Videos can make the same deterministic-vs-AI distinction as Results.
- Completed: visual-only failure diagnostics now use `possible_failure` instead of `death_scene` unless transcript, category, or aftermath evidence confirms a failure/death moment.
- Keep watching real runs for false positive `death_or_failure` primary labels; this pass softens the fine label without removing broad failure-category evidence from the ranking/debug pipeline.

Folder collapse findings:

- All Videos folder open state is only the DOM class `.open`.
- After delete, `loadLibrary()` calls `renderLibraryGrid()`, which rebuilds the folder DOM from scratch.
- `renderLibraryGrid()` only auto-opens when there is one group, so multi-folder views come back collapsed after delete/refresh/filter changes.

Folder state implementation map:

- `gui/app.js`: `state` has `libraryClips`, `libraryView`, and `libraryMomentFilter`, but no folder open-state field.
- `gui/app.js`: `toggleFolder(headerEl)` toggles `.open` only.
- `gui/app.js`: `_groupLibraryByStem()` defines folder keys from clip filename stems.
- `gui/app.js`: `renderLibraryGrid()` rebuilds folders and applies `autoOpen = groups.length === 1`.
- `gui/style.css`: `.result-folder.open .result-folder-body` controls expanded display.

Recommended folder-state fix:

- Completed: added `state.libraryOpenFolders`, keyed by folder stem.
- Completed: updated `toggleFolder()` to record open/closed state when the folder is inside `#library-grid`.
- Completed: `renderLibraryGrid()` uses stored open state when present and falls back to `autoOpen` only when there is no user preference.
- Completed: state is preserved across delete, refresh, search, and moment-filter rerenders.

Multi-select delete implementation map:

- `gui/index.html`: All Videos toolbar is the best insertion point for bulk controls.
- `gui/app.js`: `_buildLibraryCard()` renders each All Videos card and single delete button.
- `gui/app.js`: `requestDeleteLibrary()`, `confirmDelete()`, and `loadLibrary()` are the current single-delete flow.
- `api_bridge.py`: `delete_library_file(filename)` deletes one safe child path under `CLIPS_DIR`.
- `api_bridge.py`: `_safe_child_path()`, `_safe_path_under()`, and `_safe_clip_path()` are the path-safety helpers.
- `gui/app.js`: `refreshScheduleFromBackend(false)` should run after bulk deletion so scheduled/upload views stay in sync.

Recommended multi-select delete fix:

- Completed: implemented multi-select in **All Videos**, keyed by exact `filename`.
- Completed: added `state.librarySelectedFilenames`.
- Completed: added card checkboxes with click propagation stopped so selection does not open preview.
- Completed: added toolbar controls: selected count, **Select visible**, **Clear**, and **Delete selected**.
- Completed: added backend `delete_library_files(filenames)` instead of looping single deletes in JavaScript.
- Completed: backend bulk delete validates every filename with clips-folder safe path helpers, deletes files under `CLIPS_DIR`, prunes `_results`, `_moments`, `_scheduled`, marks personalization entries as deleted, and saves once.
- Completed: returns `deleted` and `failed` lists so partial failures can be reported without corrupting state.

Test map:

- Completed: `tests/test_gui_static_guards.py` asserts library folder open state exists and `renderLibraryGrid()` consults it.
- Completed: `tests/test_gui_static_guards.py` asserts selection state, checkbox markup, selected count, select-visible/clear/delete-selected controls, and `delete_library_files(...)` call are wired.
- Completed: `tests/test_api_bridge_path_safety.py` bulk delete rejects path traversal such as `..\outside.mp4`.
- Completed: `tests/test_api_bridge_path_safety.py` bulk delete removes multiple files, prunes results/moments/scheduled entries, marks personalization deleted, and saves state once.
- Completed: `tests/test_api_bridge_path_safety.py` partial failures return `deleted` and `failed` without breaking remaining valid deletes.

## Results Folder State And Multi-Delete Follow-Up

Status: completed this run. The earlier folder-state and multi-select delete pass covered **All Videos**; this pass brought the same core behavior to **Results**.

Observed issue:

- Completed: deleting a clip from **Results**, changing the Results moment filter, refreshing/re-entering Results, or auto-delete updates now preserve the user's expanded/collapsed folder choice during the session.
- Completed: **Results** now supports multi-select delete, reusing the same safe bulk delete backend used by **All Videos**.

Code findings:

- `gui/app.js`: added `resultsOpenFolders`, `resultsSelectedFilenames`, and `resultsVisibleFilenames`.
- `gui/app.js`: `toggleFolder(headerEl)` now persists folder open state for both `#results-grid` and `#library-grid`.
- `gui/app.js`: `renderResultsGrid()` now reads stored Results folder preferences before falling back to `autoOpen = groups.length === 1`.
- `gui/app.js`: `requestDeleteSelectedResults()` and the `results-bulk` branch in `confirmDelete()` call `delete_library_files(filenames)` so path safety, schedule pruning, and deleted-file learning markers stay centralized.
- `gui/app.js`: `renderLibraryGrid()` is the reference implementation because it reads `state.libraryOpenFolders[group.stem]` and falls back to auto-open only when no user preference exists.

Test map:

- Completed: `tests/test_gui_static_guards.py` asserts Results folder state exists, `toggleFolder()` records it, `renderResultsGrid()` consults it, and Results bulk-delete controls/call path are wired.
- Existing path-safety coverage in `tests/test_api_bridge_path_safety.py` still covers the shared `delete_library_files(filenames)` backend path.

## Upload Layout Redesign Pass

Status: mostly implemented for the first redesign pass. The readiness strip, safer upload wording, compact schedule status classes, local upload history persistence, shared scheduler/manual success recording, stale schedule-save protection, top account/schedule row, right-side Upload Prep review panel, sticky action bar, derived upload summary, subtle calendar history markers, day-level history drill-down, per-clip AI Notes, and regression/static guards are now in place.

Observed current app:

- Full-width YouTube Accounts and Smart Schedule bands waste horizontal space at desktop widths.
- Your Clips is narrow while the calendar receives a lot of empty horizontal space.
- Upload prep, description, and action controls feel disconnected when they live in the bottom options strip.
- The current installed app can still show older upload layout/state behavior until the latest source fixes are rebuilt.

Best ideas to borrow from the generated mockup:

- Add a lightweight workflow health strip, not a separate-screen wizard. It should highlight/check off the area the user is currently working in and show readiness at a glance.
- Use this status model:
  - **1. Connect your accounts**: account exists, OAuth token is valid, selected channel is known. Show a green ready state when connected.
  - **2. Select videos and metadata**: all available clips live here; no separate **Add more clips** affordance is needed for normal use. This is where clip rows expose **AI Notes** and per-clip title/description editing.
  - **3. Schedule clips**: Smart Schedule is an action/mode that auto-places selected clips into good slots, while manual drag/drop and adding clips to individual days stays available.
  - **4. Review and upload**: final queue, visibility, description/hashtag behavior, delete-after-upload, and upload start.
- Readiness behavior should not treat every section as binary:
  - **Accounts** can be binary enough: `Connected` / `Needs account`.
  - **Videos and metadata** should reflect real blockers and optional polish: `No clips`, `Needs titles`, `Ready`, `AI context optional`, or `Context added`.
  - **Schedule** should stay simple: `Nothing scheduled`, `Scheduled`, or `Schedule needs attention` when an item is missed, failed, disconnected, missing a channel/account, or has an invalid time.
  - **Review and upload** should reflect final blockers: `Ready to send to YouTube`, `Missing visibility/channel`, `Upload running`, `Upload failed`, or `Uploaded`.
- Do not mark missing AI context as a warning. Use neutral styling for `AI context optional`, and a positive/quiet indicator for `Context added`.
- Clicking a readiness item should focus the related area on the same screen, not navigate away. Example: clicking `Needs titles` focuses the clip/source list; clicking `Nothing scheduled` focuses the calendar.
- Use green for ready, amber for attention, red only for blocking/error states, and neutral for optional improvements.
- Current scheduling behavior is overloaded and needs clearer UX:
  - Dropping/adding clips to the calendar persists them to the backend. If YouTube is connected and the app stays open, the local background scheduler can upload those clips at their local calendar time.
  - Pressing **Send Scheduled Clips to YouTube** uploads all pending clips to YouTube immediately. For public clips, YouTube receives a future `publishAt` time and publishes later; for private/unlisted clips, the clip uploads immediately at that privacy setting.
  - Redesign labels should separate these two meanings. Suggested wording: **Queue on Calendar** for local schedule placement, **Send Scheduled Clips to YouTube** for uploading now with YouTube publish times, and **Local Upload Watcher active** for the background scheduler.
  - The upload review panel should explain the chosen mode in one plain line, such as `Send now to YouTube; public posts publish at their calendar time.` or `Keep app open to auto-upload at calendar times.`
- Preferred beginner-safe upload wording:
  - Completed: renamed the main CTA to **Send Scheduled Clips to YouTube**.
  - Completed: renamed scheduler bar language to **Local Upload Watcher** / `Next send to YouTube`.
  - Completed: optional clip context is now surfaced as **AI Notes**.
  - Completed: timeline/readiness labels distinguish `Pending locally`, `Sent to YouTube`, `YouTube scheduled`, `Missed time`, `Upload failed`, and interrupted-upload **Check YouTube** states when known.
- Account for app close/reopen behavior:
  - If the app closes while local calendar items are pending, the local watcher cannot upload while closed.
  - On next app launch, pending schedule state should be restored and explained clearly: `Local Upload Watcher paused while ViriaRevive was closed.`
  - If a public item is now past its calendar time, show `Missed time` and offer `Reschedule` or `Send now`.
  - If a private/unlisted item would upload late, confirm the intended behavior before silently sending old queued items, or mark it `Needs attention` until the user chooses `Send now`.
- Calendar visual states should separate active queue from upload history:
  - Pending local calendar items get the most visible chip/card treatment.
  - Items currently being sent should show a small spinner/progress ring on the calendar chip and in the right-side review panel.
  - Items sent to YouTube should switch to a quieter `sent`/check style, not remain visually identical to pending schedule items.
  - Public clips with a YouTube `publishAt` should read `Sent to YouTube - publishes later`, not just `Uploaded`, because upload and public release are different.
  - Upload failures should show a clear error chip and keep the clip actionable with retry/reschedule/remove.
  - Completed: interrupted/uncertain upload attempts are treated as attention states in upload readiness, Upload Prep summary, day detail, timeline, and upload preflight.
- Add persistent upload memory/history separate from the active schedule:
  - Store compact `upload_history` records with date, clip/source identity, title, channel/account, privacy, YouTube id/url when available, local send time, intended publish time, and final status.
  - Calendar days with historical uploads should show a subtle history marker/count, visually different from pending scheduled clips so users do not think old uploads are still queued.
  - Completed: clicking a historical marker opens an `Upload history` section in the day-detail view, while the default calendar surface stays focused on current pending work.
  - When pressing **Send Scheduled Clips to YouTube**, successful items should move out of the active pending queue state and into history/`sent` state so the local watcher cannot duplicate-upload them.
- Completed implementation/code map from subagent sweep:
  - Completed: backend active schedule state remains `ApiBridge._scheduled`; `upload_history` is now persisted beside `scheduled` in `viria_state.json`.
  - Completed: `load_persisted_state()` and `get_all_scheduled()` return upload history for frontend use.
  - Completed: `_mark_scheduled_uploaded()` records sent/uploaded history for manual **Send Scheduled Clips to YouTube** flows because it has clip identity plus `youtube_id`/`youtube_url`.
  - Completed: `_scheduler_loop()` captures the `upload_to_youtube()` result and calls the same shared uploaded/history path.
  - `_scheduled_upload_active()` already treats uploaded items as inactive; keep that as duplicate prevention, but do not rely on it as the only persistent protection.
  - Completed: `save_scheduled()` merges/preserves backend-owned fields like `uploaded`, `uploaded_at`, `youtube_id`, `youtube_url`, `scheduler_status`, `failure_count`, `last_error`, `missed_at`, upload attempt ids/timestamps, and status fields so stale frontend saves do not resurrect sent or in-progress clips.
  - Completed: overdue local-watcher items become `Missed time` during schedule reads instead of waiting for a scheduler tick.
  - Completed initial status field support: `upload_state` / `send_status` can distinguish `sent_to_youtube` and `youtube_scheduled`, while preserving `uploaded` for migration/backward compatibility.
  - Completed: before manual or local-watcher YouTube sends, backend now writes a durable `upload_attempt_id` / `uploading` marker. Success clears the marker into sent/upload history; known failures clear it into `upload_failed`; reopen after an unfinished attempt marks the row `upload_outcome_unknown` so the UI says **Check YouTube** and does not retry blindly.
  - Completed: single Results delete now refreshes backend schedule state and rerenders upload surfaces, matching bulk delete behavior.
  - Pending schedule rows for deleted clip files are pruned by existing state cleanup, so upload history must not live only inside `scheduled`.
- Frontend edit map from subagent sweep:
  - Completed: added readiness strip DOM near the top of `#section-upload`, after the YouTube connection/setup area and before Smart Schedule.
  - Suggested ids/classes: `#upload-readiness-strip`, `.upload-readiness-step`, `.is-ready`, `.is-warning`, `.is-blocked`, plus a neutral class for optional AI notes/context.
  - Completed: readiness is computed in pure helpers near existing schedule helpers in `gui/app.js`, reusing `scheduledLocalDate()`, `isScheduleMissed()`, and `hasPendingSchedule()`.
  - Completed: readiness inputs include `state.results`, `state.scheduled`, `state.ytConnected`, channels/account state, pending/missed/sent counts, and `state.uploadHistory`.
  - Completed: readiness refreshes from upload load, schedule refresh, scheduler/upload callbacks, and YouTube connection/UI updates.
  - Completed: calendar chip, day detail, and timeline status labels use centralized label/class derivation.
  - Completed: fixed scheduler bar is reworded to **Local Upload Watcher** and `Next send to YouTube`.
  - Completed: manual calendar-day clip picking now uses the same Smart Schedule channel/account selection as drag/drop scheduling.
  - Completed: custom **Clips per day** changes immediately refresh the peak-slot legend and upload summary.
  - Calendar chips have tight space, so do not add long labels inside chips. Use icon/ring/check/error styling and put detailed text in tooltips/day detail.
  - Active UI uses `#schedule-timeline`; avoid legacy `.scheduled-list*` styles when implementing.
- Test map for this run:
  - Completed: `tests/test_upload_scheduling.py` covers upload history recording, scheduler success YouTube id/url/history, stale frontend save preservation, and overdue local-watcher missed marking.
  - Completed: `tests/test_upload_scheduling.py` covers durable upload-attempt markers, stale frontend-save preservation of attempt fields, success/failure cleanup, and crash/reopen conversion to `upload_outcome_unknown`.
  - Completed: `tests/test_gui_static_guards.py` asserts readiness strip DOM, helper/render functions, wording, shared status classes, and CSS states.
  - Release guards: if upload history becomes a separate file instead of part of `viria_state.json`, add it to private local-data release exclusions.
- Completed: make the top row a two-column layout: compact YouTube Accounts plus a schedule control area that explains auto-schedule and current channel/start settings.
- Completed: treat the middle of the screen as three work zones: **Your Clips/source queue**, **Scheduled Calendar**, and **Upload Prep/Review**.
- Completed: keep the Upload Prep area in the right-side review/prep position from the mockup instead of as a disconnected bottom strip.
- Completed: put an **Upload Summary** in that same panel: clips selected/scheduled, channel, visibility, start date, and estimated upload span.
- Completed: keep a sticky bottom action bar with concise summary plus **View Calendar Plan** and **Send Scheduled Clips to YouTube**.
- Completed: readiness items focus the matching account, clip, calendar, or review area on the same screen.
- Completed: calendar days can show subtle upload-history markers for previous successful sends that are not already visible as sent schedule chips.
- Completed: added a day-level upload-history detail section for historical markers.
- Completed: improved **Your Clips** with local search/filter, clearer source rows, and per-clip actions like **AI Notes**.
- Completed: per-clip **AI Notes** live on clip rows and in the metadata modal.

Avoid copying:

- Do not fake account/channel status, subscriber counts, or connection details; only display real connected data.
- Do not turn the upload page into locked wizard gating or separate screens. Users should still see clips, calendar, and upload prep together.
- Do not add every mockup field before backend truth exists.
- Avoid a huge card-heavy marketing feel. This screen should remain a dense operational dashboard.

Implementation map:

- `gui/index.html`: upload section layout; split `#upload-content` into top grid, work grid, and sticky summary/action bar.
- `gui/style.css`: add responsive upload grid classes, avoid inline styles, use desktop two/three-column layout and mobile stacking.
- `gui/app.js`: reuse `renderClipTray()`, `renderCalendar()`, `renderTimeline()`, `updateDescriptionOptionsStatus()`, `refreshScheduleFromBackend()`, and `startUpload()`.
- Add a derived upload summary renderer from existing `state.scheduled`, `state.channels`, selected visibility, and start date.
- Avoid new backend APIs for layout-only work, but the upload history/readiness pass does require backend state support via `upload_history`, merged schedule saves, and `load_persisted_state()` returning enough history/status data for the calendar.
- Tests: static guard for layout classes, summary uses real state, upload button still calls `startUpload()`, and Optional AI Notes remain clip-scoped.

## Game Knowledge For Metadata

Status: first Wikidata-backed Game Knowledge slice is implemented. ViriaRevive can resolve likely game identity from local title hints and YouTube metadata, fetch/cache compact game facts, then feed those facts into AI moment labels, Deep Analysis vision prompts, title/description prompts, sidecars, and run debug. Broader wiki/walkthrough-style sources still need a separate privacy, licensing, attribution, and UX pass.

### Wikidata One-Game DB Prototype

Status: sample built for schema discovery only. A one-query Alan Wake pull was written to ignored runtime data at `analysis_cache/game_context_sample.sqlite3`.

Implemented runtime slice:

- `game_context.py` stores a small SQLite cache under app data `game_context/game_context.sqlite3`.
- `game_identity.py` resolves likely game identity before fact lookup by combining explicit/user hints, source filename/folder hints, YouTube title/description/tag metadata, local cache hits, Wikidata search results, and QID-validated game context.
- Recent-game seeding supports pagination/offsets, de-dupes query results by QID, and skips already-cached games by default.
- The first provider is Wikidata only, using compact CC0 facts instead of raw wiki pages or walkthrough prose.
- Prompt context is capped and includes game identity fields such as release year, genre, developer, publisher, platform, series, fictional universe, characters, narrative locations, environment, and game mode.
- `api_bridge.py` resolves identity/context once per source during analysis and passes it through candidate evaluation, AI labels, multimodal vision analysis, selected moments, metadata sidecars, and run debug.
- `title_generator.py` and `multimodal_analysis.py` use game knowledge as background only and keep "do not invent" rules in the prompts.
- Release safety now blocks local game-context caches from packaged builds.

Findings:

- The sample game row was `Alan Wake (Q575505)`.
- The query returned 156 fact rows across 112 distinct Wikidata properties.
- A fixed wide table would get brittle quickly because games have many optional IDs, ratings, stores, review scores, awards, languages, platforms, characters, and external references.
- Use a hybrid schema instead:
  - `games`: `qid`, `label`, `description`, `sitelinks`, `source_url`, `license`, `license_url`, `fetched_at`.
  - `game_aliases`: `qid`, `alias`.
  - `game_facts`: `qid`, `property_id`, `property_label`, `value_kind`, `value_id`, `value_label`, `value_text`, `datatype`, `source_url`, `fetched_at`.
  - `game_fact_columns`: curated mapping from important properties to app-facing prompt fields.
- First prompt-worthy mapped fields:
  - `P577` -> `first_release_date`
  - `P178` -> `developers`
  - `P123` -> `publishers`
  - `P136` -> `genres`
  - `P400` -> `platforms`
  - `P179` -> `series`
  - `P674` -> `characters` (only use when supported by transcript/vision)
  - `P840` -> `narrative_locations`
  - `P31` -> `instance_of`
  - `P856` -> `official_website` (source/link only)
- Some Wikidata entity values can still return only QIDs in one broad query. Keep both `value_id` and `value_label`, and add a small label-enrichment fallback before prompt use.

### Creator-Provided Video Context

Status: completed for the first local-only slice. Per-clip Optional AI Notes and scheduled metadata overrides now save sanitized `creator_title_context`, feed the compact title prompt/sidecar pipeline, and avoid copying those notes into final descriptions.

Concept:

- Give the user a small way to tell AI title/description generation what the source video/session is.
- Example: `This is my blind Alan Wake run in the nursing home chapter, mostly exploring and getting jumped by Taken.`
- This is not upload description text and should not be copied verbatim into the final description by default.
- It should guide titles, generated summaries, and tags through the same compact title context pipeline that already uses transcript/detector/ranker data.
- Keep it optional and local. Do not add another Generate wizard step.

Recommended UI placement:

1. **Primary home: Upload > individual clip rows**
   - Completed: added an **AI Notes** control on each clip row.
   - Best match for mixed-source batches: the user can explain a specific clip without accidentally applying that hint to unrelated videos.
   - Completed: scope is clip-level, keyed by `clip_id`/filename and copied to matching scheduled rows.
2. **Companion home: scheduled Edit Clip modal**
   - Completed: added a compact **Optional AI Notes** field under the title area near the `AI Title` button.
   - This lets the user override the source/session hint for one clip.
   - Completed: regenerating a title from that modal saves the override before title generation.
3. **Avoid as primary: global Upload card**
   - A single textarea near **Generate AI Titles** is fast to build, but risky with mixed-source batches.
   - If added later, label it as "Apply to visible/source selection" rather than a silent global.
4. **Avoid for now: Generate wizard**
   - The wizard is already carrying detection/audio/style choices.
   - Creator title context is about metadata after clips exist, not detection setup.

Frontend implementation map:

- `gui/index.html`
  - Upload page controls: `#section-upload`, `#btn-gen-ai-titles`, folder/tray rows.
  - Scheduled metadata modal: `#meta-modal`, `#modal-meta-title`, `#modal-meta-desc`, `#modal-description-preview`.
  - Completed: added a compact per-clip **AI Notes** action in the clip tray.
  - Completed: added a per-clip **Optional AI Notes** textarea in `#meta-modal`.
- `gui/app.js`
  - Clip tray UI: `_createTrayClipEl()` and the per-clip `AI Notes` action.
  - Title entry points: `generateAITitlesManual()`, `generateAITitlesForFolder(stem, btn)`, `regenerateTitle(schedIdx)`.
  - Schedule helpers: `_scheduleClipIndices()`, `descriptionFieldsForClip()`, `applyGeneratedMetadataToSchedule()`, `openMetaModal()`, `saveMetaModal()`, `normalizeScheduledMetadata()`.
  - Completed: added state/payload handling for `creator_title_context`.
  - Completed: when context changes, scheduled generated description snippets are invalidated so previews do not keep stale AI summaries.
  - Completed: static guards ensure the field is preserved in scheduled metadata and passed during title regeneration.

Backend/data implementation map:

- `api_bridge.py`
  - Completed: normalized context is stored on persisted moments as `moment["creator_title_context"]`.
  - Completed: added a sanitizer/truncator for creator context.
  - Completed: added `save_clip_title_context(clip_id, clip_index, filename, text)` to update the matching moment, matching scheduled rows, and save state.
  - Completed: `_title_context_for_clip()` includes `creator_title_context`.
  - Completed: `_store_generated_metadata()` and the title context summary inherit sanitized creator context.
  - Completed: `_write_metadata_sidecar()` adds a compact `Creator Context:` line under `Analysis Context`.
  - Completed: `save_scheduled()` / `_normalize_scheduled_items()` preserve `creator_title_context` on scheduled items.
- `title_generator.py`
  - Completed: `summarize_clip_context()` includes sanitized creator context.
  - Completed: `_analysis_prompt_lines()` adds a `Creator-provided context` line.
  - Completed: `_prompt_safe_text()` redaction is reused and the limit is 420 chars.
  - Completed: `_build_ollama_prompt()` and `generate_titles_batch()` inherit it through existing `clip_contexts`.
  - Completed: descriptions do not dump creator notes verbatim.

Tests/docs:

- Completed: `tests/test_title_context.py`
  - Prompt includes sanitized creator context.
  - Secrets/local paths/prompt-like text are redacted.
  - Long context is truncated.
  - Generated description does not expose raw creator prompt wording by default.
- Completed: `tests/test_api_bridge_path_safety.py`
  - `_title_context_for_clip()` attaches compact creator context.
  - Sidecar writes sanitized context.
  - Generated metadata stores the title context used.
- Completed: `tests/test_upload_scheduling.py`
  - Scheduled `creator_title_context` survives normalization/save.
  - Per-clip override is passed when regenerating a scheduled title.
- Completed: `tests/test_gui_static_guards.py`
  - Source/folder AI context control exists.
  - Metadata modal AI context field exists.
  - Frontend preserves/passes `creator_title_context`.
- Completed: README updates:
  - Titles, Metadata, And Sidecars.
  - Upload workflow.
  - Data & Privacy/state notes: creator context is local, optional, persisted in `viria_state.json`, and sent only to local Ollama when local AI title generation uses Ollama.

Concept:

- This is a strong idea, but the safe version is not "scrape GameFAQs and feed the whole guide to Ollama." It should be a provider-backed game-context/RAG layer for titles, descriptions, tags, and AI moment labels.
- The app should use the same clip analysis it already has, then retrieve a few compact game/story facts from licensed/attributable online sources.
- First runtime slice now enriches metadata and reinforces the middle of analysis: AI moment labels, Deep Analysis vision prompts, heuristic context scoring, sidecars, and debug. Keep the influence compact and auditable before allowing broader wiki/story retrieval to affect ranking.
- The goal is to help Ollama know things like game title, enemy/location/mechanic/story-beat names, and broad story context without inventing details that are not visible in the clip.
- Do not require users to build or import local knowledge packs.

Research notes:

- GameFAQs/walkthrough content is not a safe default scraping source. GameFAQs contributors retain copyright in guides, and Fandom/GameSpot terms also place responsibility on users not to submit infringing material.
- Wikidata is useful for structured game metadata because its main structured data is CC0/public-domain equivalent, but it will not usually know exact walkthrough/story moments.
- Fandom wiki text is often CC BY-SA, but licenses vary by wiki, attribution/share-alike matters, non-text files are separate, and off-wiki/forum content should not be assumed licensed.
- IGDB can provide game metadata through its API, but it requires Twitch developer credentials and is better as a later optional provider, not a first-run requirement.
- MediaWiki APIs can expose site rights/license metadata through `meta=siteinfo`, so wiki providers should check license/rights info before using page extracts.
- RAG/document-grounding best practice is to chunk large documents and retrieve only relevant snippets to avoid token bloat and truncation.
- Retrieved web content is untrusted content. OWASP flags prompt injection from files/websites as a real risk, so retrieved snippets must be treated as data, not instructions.

Recommended product shape:

1. Start with an opt-in online **Game Knowledge** provider layer, not local imports.
   - Default can stay off until the user enables **Use online game context for metadata**.
   - The feature should clearly say when it helps titles/descriptions/tags versus candidate analysis/ranking.
   - No arbitrary URL scraping.
   - No GameFAQs ingestion unless we later have an explicit license, public API, or partner-approved path.
   - User should not need to curate files.
2. Use a source registry with provider tiers:
   - **Tier 0: Existing clip analysis** - transcript, OCR/visual labels, source filename/folder, game title, moment categories, AI labels, scene/audio signals.
   - **Tier 1: Structured metadata** - Wikidata first for CC0 facts, then optional IGDB once API credentials/config are handled.
   - **Tier 2: Licensed wiki context** - MediaWiki/Fandom/wiki.gg-style providers only when license/rights info is detectable and compatible with our attribution rules.
   - **Tier 3: Walkthrough/detail sources** - only if the source grants explicit usable rights through API/terms/partnership. Treat GameFAQs as blocked by default.
3. Add a compact source schema before any broad provider work:
   - `game_title`
   - `aliases`
   - `safe_tags`
   - `characters`
   - `enemies`
   - `locations`
   - `mechanics`
   - `story_beats`
   - `spoiler_level`
   - `source_name`
   - `source_url`
   - `provider`
   - `license`
   - `license_url`
   - `attribution`
   - `revision_id` / `retrieved_at` where available
4. Retrieval should return only compact matched facts:
   - matched game
   - matched aliases/entities
   - matched locations/mechanics/story beats
   - source/license summary
   - confidence/reason
   - capped excerpt only when license/terms allow it, never a full article or walkthrough block
5. Default spoiler behavior:
   - "Avoid spoilers beyond the clip" should be default-on.
   - Story beats should only be used when matched by transcript/OCR/visual context or a strong source-position clue.
   - If story position is uncertain, use broad game context only: genre, setting, mechanics, enemy names, location names detected on screen or in transcript.
6. UI home:
   - Put this in **Settings > Data & Privacy > Advanced Features** as a new **Game Knowledge** tab.
   - Add a lighter **Local AI** status line: `Game Knowledge: Off / Online context ready / Sources cached`.
   - Upload metadata and AI title flows should show whether Game Knowledge influenced generated metadata.
   - Do not add another Generate wizard step; expose status and clearing controls in Data & Privacy / Local AI instead.
7. Metadata behavior:
   - Titles should use context to get more specific, not longer.
   - Descriptions should stay viewer-facing and never expose prompt/debug phrasing.
   - Sidecar/debug files can record compact source/context metadata for inspection.
   - Metadata modals should show "Sources used" with source names/licenses/URLs.
   - Tags may include safe game/entity tags while staying within the existing YouTube tag budget.

Implementation map:

- New helper: `game_context.py`
  - Provider registry and result normalization.
  - Match by game title/aliases plus transcript/category/visual clues.
  - Fetch compact source context from approved providers.
  - Check provider license/rights metadata before using page extracts.
  - Cache compact facts, source metadata, and retrieval timestamps.
  - Return compact, prompt-safe context.
- `config.py`
  - Add local app-data paths for cache only, e.g. `GAME_CONTEXT_CACHE_FILE` and `GAME_CONTEXT_CACHE_DIR`.
  - Add provider config paths if needed, e.g. `GAME_CONTEXT_PROVIDERS_FILE`.
- `.gitignore` and `scripts/check_release_safety.py`
  - Exclude local provider credentials, cache files, raw fetched page extracts, and backups from commits/releases.
- `api_bridge.py`
  - `_title_context_for_clip()` should attach compact `game_context`.
  - `_classify_selected_moments()` and `_classify_ai_moment_shadow()` should pass context-enriched moments to AI labels.
  - `_write_metadata_sidecar()` should include a compact `Game Context` line.
  - `_store_generated_metadata()` already stores `title_context`, so it can inherit context once summarization is updated.
  - Add enable/disable, refresh, cache-status, clear-cache, and source-inspection APIs after the helper exists.
- `title_generator.py`
  - `summarize_clip_context()` should include sanitized `game_context`.
  - `_analysis_prompt_lines()` should add one compact `Local game context` line for title prompts.
  - `_build_moment_classification_prompt()` should include compact context while keeping "do not invent" rules.
  - `_description_context_line()` / `_context_sentence()` should use game context only when a matched fact is confident.
  - `generate_tags()` can add safe context tags while respecting tag caps.
- `candidate_ranker.py`
  - Debug summaries can expose compact matched facts, not raw fetched article text.
- `gui/index.html`
  - Data & Privacy advanced modal: add `Game Knowledge` tab.
  - Local AI card: add a compact status row and opt-in state.
  - Metadata modal: show "used local game knowledge" when true.
- `gui/app.js`
  - Render Game Knowledge status.
  - Add enable/disable, refresh, clear-cache, and source-inspection controls.
  - Keep raw fetched text out of normal UI and share-safe export.

Provider options to investigate in order:

1. **Wikidata provider first**
   - Pros: CC0 structured facts, no user API key for basic SPARQL/API usage, good for game title, aliases, genre, developer/publisher, series, platform, release date, official site, external identifiers.
   - Limits: usually weak for walkthrough/story beats.
   - Use for baseline game identity and tags.
2. **MediaWiki-compatible wiki provider**
   - Pros: can query license/rights info via siteinfo, can retrieve page extracts/revisions with source URLs, often has enemies/locations/mechanics/story pages.
   - Limits: licenses differ, attribution matters, page text can contain spoilers and untrusted prompt text.
   - Use only compact facts and source attribution; avoid copying prose into video descriptions.
3. **IGDB provider**
   - Pros: strong official-ish game metadata API, useful themes/genres/keywords/storyline/summary.
   - Limits: requires Twitch app credentials and provider setup.
   - Use after UX for provider credentials is clear.
4. **Search-result/browser lookup provider**
   - Pros: can find relevant official/wiki pages for a game without hardcoding every wiki.
   - Limits: search APIs have cost/terms; arbitrary scraping is risky.
   - Keep as later work, not v1.
5. **GameFAQs/walkthrough provider**
   - Blocked by default.
   - Only revisit if a source/API/license explicitly permits reuse or we obtain permission.

Security/privacy guardrails:

- No automatic scraping of GameFAQs or arbitrary walkthrough sites.
- Online game context must be opt-in and clearly marked.
- Store only compact cached facts and provenance by default; do not cache whole pages unless explicitly justified and licensed.
- If Ollama is used, compact snippets/facts go only to local Ollama prompts.
- Treat retrieved text as untrusted reference material, not instructions.
- Strip or redact local paths, tokens, secrets, URLs with credentials, and prompt-like instructions before including context in prompts.
- Cap fetched page size, snippet count, prompt length, request rate, and cache size.
- Add allowlisted provider domains and reject redirects to unexpected hosts.
- Respect provider terms, license metadata, robots/API requirements, and rate limits.
- Share-safe export must exclude raw fetched text, exact request URLs with params, provider credentials, source cache files, and raw source IDs where needed.
- Clearing Game Knowledge should remove caches, fetched snippets, indexes, summaries, and backups where practical.
- If CC BY-SA or similar source text materially shapes an output, preserve attribution in sidecar/debug and show sources in the metadata modal. Prefer facts/entity names over prose reuse.

Test map:

- `tests/test_game_context.py`
  - Provider result sanitization, alias matching, caps, source/license fields, spoiler filtering, malformed provider payloads, request errors, redirects, rate limiting, and prompt-injection text.
- `tests/test_title_context.py`
  - Title prompt includes compact context but not raw full source text.
  - Description/tags use matched context without mirroring prompt/debug language.
  - No invented enemy/location when context confidence is low.
- `tests/test_api_bridge_path_safety.py`
  - Cache clear/delete path safety and app-data containment.
  - `_title_context_for_clip()` attaches only compact context.
- `tests/test_data_privacy_summary.py`
  - Game Knowledge status shows enabled/cache/source counts without leaking raw fetched text, credentials, or exact request URLs in share-safe export.
- `tests/test_release_guards.py`
  - Release safety rejects packaged game-context caches, provider credentials, raw fetched extracts, and backups.
- README updates:
  - Titles, Metadata, And Sidecars.
  - Data & Privacy.
  - Debugging And State.
  - Optional Game Knowledge setup with opt-in network, provider, attribution, copyright, and privacy wording.

## Upload Reliability Sweep - 2026-06-25

Status: targeted reliability fixes in progress after the first real YouTube upload attempt failed on a Google resumable-upload 308 response.

Completed in this pass:

- `uploader.py`: call `request.next_chunk(num_retries=YOUTUBE_UPLOAD_CHUNK_RETRIES)` so transient chunk/network failures get the Google client library retry path.
- `uploader.py` / `viria.spec`: keep the patched `httplib2` transport visible in packaged builds and log loudly if the patched path is unavailable.
- `api_bridge.py`: reject non-video files from `_safe_clip_path()` even if a tampered state file points inside `CLIPS_DIR`.
- `api_bridge.py`: when a schedule slot changes, clear stale backend upload success/failure/missed fields instead of preserving old row state.
- `api_bridge.py`: retry-ready `upload_failed` rows no longer become `missed` just because the original local calendar time is outside the grace window.
- `api_bridge.py`: upload attempts now store a slot fingerprint and success/failure/clear paths require the same attempt id plus unchanged slot fingerprint, so an in-flight upload cannot mark a rescheduled slot as uploaded.
- `game_context.py`: cache/connect failures now return soft `query_error` payloads instead of risking an undefined cached QID.
- `gui/index.html` / `README.md`: Game Knowledge privacy wording now explicitly says Wikidata may be contacted for compact game facts and that raw clips are not uploaded for local learning.
- `gui/app.js`: clip tray scheduled badges/counts refresh after picker scheduling, metadata save, and schedule removal.
- `gui/app.js`: editing a row title/time/privacy clears stale missed/failed/uploading fields for that row.
- `gui/app.js`: upload summary now reports multi-channel queues instead of showing only the first channel.

Still open after this pass:

- Add friendlier permanent-error classes for YouTube quota, revoked OAuth, invalid privacy, invalid title/description/tags, and daily upload-limit responses instead of treating every `HttpError` as a transient retry.
- Decide whether `Clear All` should be renamed `Clear Pending` or should also archive/remove sent rows from the active calendar plan.
- Render disconnected/legacy scheduled channel ids in calendar filter tabs, or reset those filters to `All` with a clear note.
- Add direct retry/reschedule actions and visible `last_error` detail for failed calendar/timeline rows.
- Add keyboard/focus support for clickable upload calendar cells, tray folders, clip picker items, and day-detail rows.
