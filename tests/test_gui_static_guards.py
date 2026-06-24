import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GuiStaticGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_js = (ROOT / "gui" / "app.js").read_text(encoding="utf-8")

    def test_schedule_identity_does_not_fall_back_to_index_when_identity_exists(self):
        self.assertIn("if (item.clip_id || item.clip_filename) return -1;", self.app_js)

    def test_frontend_javascript_syntax_is_valid_when_node_is_available(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not available for frontend syntax check")
        result = subprocess.run(
            [node, "--check", str(ROOT / "gui" / "app.js")],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_candidate_debug_recovery_is_not_user_facing(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn("Render Last Analysis", html)
        self.assertNotIn("btn-resume-analysis", html)
        self.assertNotIn("recoverLastAnalysis", self.app_js)

    def test_upload_completion_refreshes_schedule_from_backend(self):
        self.assertIn("await refreshScheduleFromBackend(false);", self.app_js)
        self.assertIn("await refreshScheduleFromBackend(true);", self.app_js)
        self.assertIn("clearStaleScheduleUi();", self.app_js)
        self.assertIn("removeNotificationsByType('uploading');", self.app_js)
        self.assertIn("window.onPipelineComplete = async function", self.app_js)
        self.assertIn("window.onPipelineCancelled = async function", self.app_js)

    def test_upload_readiness_strip_is_wired(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "gui" / "style.css").read_text(encoding="utf-8")

        self.assertIn('id="upload-readiness-strip"', html)
        self.assertIn("focusUploadReadiness", self.app_js)
        self.assertIn('onclick="focusUploadReadiness', self.app_js)
        self.assertIn("function scheduleItemStatus", self.app_js)
        self.assertIn("schedulerStatus === 'upload_outcome_unknown'", self.app_js)
        self.assertIn("label: 'Check YouTube'", self.app_js)
        self.assertIn("function uploadReadinessState", self.app_js)
        self.assertIn("function renderUploadReadinessStrip", self.app_js)
        self.assertIn("renderUploadReadinessStrip();", self.app_js)
        self.assertIn(".upload-readiness-step.is-ready", css)
        self.assertIn(".upload-readiness-step.is-warning", css)
        self.assertIn(".upload-readiness-step.is-blocked", css)

    def test_upload_layout_has_review_panel_summary_and_sticky_action_bar(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "gui" / "style.css").read_text(encoding="utf-8")

        self.assertIn("upload-top-grid", html)
        self.assertIn("upload-layout", html)
        self.assertIn("upload-main", html)
        self.assertIn('id="upload-review-panel"', html)
        self.assertIn("upload-prep-panel", html)
        self.assertIn("upload-action-bar", html)
        self.assertIn('id="upload-summary-clips"', html)
        self.assertIn('id="upload-summary-channel"', html)
        self.assertIn('id="upload-summary-visibility"', html)
        self.assertIn('id="upload-summary-start"', html)
        self.assertIn('id="upload-summary-span"', html)
        self.assertIn('id="upload-action-title"', html)
        self.assertIn('id="btn-upload"', html)
        self.assertIn("function uploadSummaryState", self.app_js)
        self.assertIn("function renderUploadSummary", self.app_js)
        self.assertIn("renderUploadSummary();", self.app_js)
        self.assertIn(".upload-prep-panel", css)
        self.assertIn(".upload-summary-row", css)
        self.assertIn(".upload-action-bar", css)
        self.assertIn(".upload-action-controls .btn-upload-go", css)

    def test_upload_calendar_uses_history_markers_without_fake_status(self):
        css = (ROOT / "gui" / "style.css").read_text(encoding="utf-8")

        self.assertIn("function uploadHistoryDateKey", self.app_js)
        self.assertIn("function uploadHistoryByDate", self.app_js)
        self.assertIn("const historyByDate = uploadHistoryByDate(filter);", self.app_js)
        self.assertIn("const representedSent = new Set();", self.app_js)
        self.assertIn("cal-history-marker", self.app_js)
        self.assertIn("marker.onclick = (e) => { e.stopPropagation(); openDayDetailView(dateStr, dayItems || [], historyItems); };", self.app_js)
        self.assertIn("function historyRowStatus", self.app_js)
        self.assertIn("function historyRowTimeLabel", self.app_js)
        self.assertIn("Upload history", self.app_js)
        self.assertIn(".cal-history-marker", css)
        self.assertIn(".day-detail-section-title", css)
        self.assertIn(".day-detail-history-icon", css)
        self.assertIn(".day-detail-history-note", css)
        self.assertIn("state.uploadHistory", self.app_js)

    def test_upload_wording_separates_local_queue_from_youtube_send(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")

        self.assertIn("Send Scheduled Clips to YouTube", html)
        self.assertIn("Local Upload Watcher active", html)
        self.assertIn("Pending locally", html)
        self.assertNotIn("Upload Scheduled Posts to YouTube", html)
        self.assertIn("next send to YouTube", self.app_js)

    def test_schedule_status_classes_are_shared_across_calendar_timeline_and_detail(self):
        css = (ROOT / "gui" / "style.css").read_text(encoding="utf-8")

        self.assertIn("scheduleItemStatus(s", self.app_js)
        self.assertIn("cal-chip ${status.className}", self.app_js)
        self.assertIn("timeline-status ${statusClass}", self.app_js)
        self.assertIn("day-detail-status ${statusClass}", self.app_js)
        self.assertIn(".cal-chip.sending", css)
        self.assertIn(".timeline-status.failed", css)
        self.assertIn(".day-detail-status.youtube-scheduled", css)
        self.assertIn("scheduleItemStatus(s).key === 'unknown'", self.app_js)
        self.assertIn("Check YouTube Studio", self.app_js)
        self.assertIn("['missed', 'failed', 'unknown', 'disconnected', 'invalid']", self.app_js)

    def test_empty_schedule_clears_stale_upload_ui(self):
        self.assertIn("if (!state.scheduled.length) {", self.app_js)
        self.assertIn("if (summaryEl) summaryEl.textContent = '';", self.app_js)
        self.assertIn("if (schedulerBar) schedulerBar.classList.add('hidden');", self.app_js)
        self.assertIn("_cachedNextUpload = null;", self.app_js)
        self.assertIn("function removeNotificationsByType(type)", self.app_js)

    def test_tags_are_not_overwritten_when_present(self):
        self.assertIn("if (!String(item.tags || '').trim()) item.tags = DEFAULT_UPLOAD_TAGS;", self.app_js)

    def test_creator_title_context_is_wired_for_upload_metadata(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "gui" / "style.css").read_text(encoding="utf-8")

        self.assertIn("AI Context", html)
        self.assertIn('id="source-context-modal"', html)
        self.assertIn('id="source-context-text"', html)
        self.assertIn('id="modal-meta-creator-context"', html)
        self.assertIn("function creatorTitleContextForClip", self.app_js)
        self.assertIn("function openSourceTitleContextModal", self.app_js)
        self.assertIn("function saveSourceTitleContextModal", self.app_js)
        self.assertIn("pywebview.api.save_source_title_context", self.app_js)
        self.assertIn("creator_title_context: creatorTitleContextForClip(idx)", self.app_js)
        self.assertIn("creator_title_context: s.creator_title_context || ''", self.app_js)
        self.assertIn("const hasGeneratedDescription = Object.prototype.hasOwnProperty.call(item, 'description_generated')", self.app_js)
        self.assertIn("await pywebview.api.save_scheduled(state.scheduled);", self.app_js)
        self.assertIn(".tray-folder-context-btn", css)
        self.assertIn(".tray-folder-context-btn.has-context", css)
        self.assertIn(".source-context-copy", css)

    def test_upload_clip_tray_has_local_search_filter(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "gui" / "style.css").read_text(encoding="utf-8")

        self.assertIn('id="clip-tray-search"', html)
        self.assertIn('oninput="filterClipTray()"', html)
        self.assertIn('id="clip-tray-summary"', html)
        self.assertIn("state.clipTraySearch = searchTerm;", self.app_js)
        self.assertIn("sourceMatches", self.app_js)
        self.assertIn("clip.filename", self.app_js)
        self.assertIn("const filterClipTray = _debounce", self.app_js)
        self.assertIn("window.filterClipTray = filterClipTray;", self.app_js)
        self.assertIn(".clip-tray-tools", css)
        self.assertIn(".clip-tray-empty", css)

    def test_today_scheduling_skips_past_peak_slots(self):
        self.assertIn("const SCHEDULE_BUFFER_MINUTES = 10;", self.app_js)
        self.assertIn("function _availableScheduleSlotsForDate", self.app_js)
        self.assertIn("function _resolveSchedulableDateTime", self.app_js)
        self.assertIn("while (!availableSlots.length)", self.app_js)

    def test_upload_preflight_rejects_past_public_schedule(self):
        self.assertIn("Reschedule missed uploads first", self.app_js)
        self.assertIn("s.privacy === 'public' && s._scheduledDate <= now", self.app_js)

    def test_channel_identity_does_not_guess_account_id_from_channel_id(self):
        self.assertIn("account_id: ch?.account_id || null", self.app_js)
        self.assertIn("state.scheduled = normalizeScheduledMetadata(state.scheduled);", self.app_js)

    def test_missed_actions_clear_backend_missed_state(self):
        self.assertIn("delete s.scheduler_status;", self.app_js)
        self.assertIn("delete s.missed_at;", self.app_js)
        self.assertIn("ensureSchedulerForPending();", self.app_js)

    def test_upload_ui_loose_ends_are_guarded(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")

        self.assertIn("const selectedChannelId = first ? (first.channel_id || '')", self.app_js)
        self.assertIn("const channelId = _getScheduleChannelId() || state.selectedChannel || null;", self.app_js)
        self.assertIn('id="smart-sched-custom-perday"', html)
        self.assertIn('oninput="_renderPeakTimesLegend(); renderUploadSummary()"', html)
        self.assertIn("await refreshScheduleFromBackend(false);\n                await loadResults();", self.app_js)

    def test_schedule_rendering_escapes_persisted_text_fields(self):
        self.assertIn('class="cal-chip-time">${escHtml(s.time || \'\')}</span>', self.app_js)
        self.assertIn('class="day-detail-time">${escHtml(s.time || \'—\')}</span>', self.app_js)
        self.assertIn('class="day-detail-status ${statusClass}">${escHtml(statusLabel)}</span>', self.app_js)
        self.assertIn('class="day-detail-privacy">${escHtml(s.privacy || \'public\')}</span>', self.app_js)
        self.assertIn('class="timeline-date-val">${escHtml(dateFmt)}</span>', self.app_js)
        self.assertIn('class="timeline-time-val">${escHtml(s.time || \'\')}</span>', self.app_js)
        self.assertIn('class="timeline-status ${statusClass}">${escHtml(statusLabel)}</span>', self.app_js)

    def test_ollama_setup_buttons_are_status_aware(self):
        self.assertIn("id=\"btn-ollama-download\"", (ROOT / "gui" / "index.html").read_text(encoding="utf-8"))
        self.assertIn("id=\"btn-ollama-install\"", (ROOT / "gui" / "index.html").read_text(encoding="utf-8"))
        self.assertIn("id=\"btn-ollama-model\"", (ROOT / "gui" / "index.html").read_text(encoding="utf-8"))
        self.assertIn("function updateOllamaActionButtons", self.app_js)
        self.assertIn("Open Ollama Folder", self.app_js)
        self.assertIn("Ollama Installed", self.app_js)
        self.assertIn("AI Model Ready", self.app_js)
        self.assertIn("Download AI Model", (ROOT / "gui" / "index.html").read_text(encoding="utf-8"))

    def test_subtitle_none_option_is_available_in_settings_and_wizard(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")

        self.assertIn('name="subtitle-style" value="none"', html)
        self.assertIn('name="picker-style" value="none"', html)
        self.assertIn("Captions disabled", (ROOT / "api_bridge.py").read_text(encoding="utf-8"))
        self.assertIn("captions-disabled", self.app_js)

    def test_detection_preference_and_subtitle_snapshot_preview_are_wired(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="set-detection-preference"', html)
        self.assertIn('id="wizard-detection-preference"', html)
        self.assertIn('id="wizard-detection-preference-fixed"', html)
        self.assertIn('id="wizard-depth-presets"', html)
        self.assertIn('data-depth="fast"', html)
        self.assertIn('data-depth="balanced"', html)
        self.assertIn('data-depth="deep"', html)
        self.assertIn('id="wizard-num-clips"', html)
        self.assertIn('id="wizard-clip-duration"', html)
        self.assertIn('id="wizard-min-gap"', html)
        self.assertIn("normalizeProcessingDepth", self.app_js)
        self.assertIn("function effectiveWizardDetectionPreference", self.app_js)
        self.assertIn("function syncWizardDetectionPreferenceMode", self.app_js)
        self.assertIn("pickedNumClips === 'auto'", self.app_js)
        self.assertIn("settings.processing_depth = pickedProcessingDepth;", self.app_js)
        self.assertIn("settings.detection_preference = pickedDetectionPreference;", self.app_js)
        self.assertIn('value="quality"', html)
        self.assertIn('value="quantity"', html)
        self.assertIn('id="subtitle-placement-snapshot-empty"', html)
        self.assertIn('id="subtitle-preview-modal"', html)
        self.assertIn("subtitle-placement-box-large", html)
        self.assertIn("openSubtitlePreviewModal", self.app_js)
        self.assertIn("refreshSubtitlePreviewSnapshot", self.app_js)
        self.assertIn("get_subtitle_preview_url", (ROOT / "api_bridge.py").read_text(encoding="utf-8"))

    def test_all_videos_feedback_controls_share_result_feedback_pipeline(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "gui" / "style.css").read_text(encoding="utf-8")

        self.assertIn("function feedbackButtonsMarkup", self.app_js)
        self.assertIn("class=\"result-feedback library-feedback\"", self.app_js)
        self.assertIn("recordLibraryFeedback(clip, btn.dataset.feedback)", self.app_js)
        self.assertIn("state.previewLibraryClip", self.app_js)
        self.assertIn("renderCardFeedbackState(item, clip)", self.app_js)
        self.assertIn(".library-item-overlay .play-btn", css)
        self.assertIn('id="feedback-modal"', html)
        self.assertIn("FEEDBACK_REASON_PRESETS", self.app_js)
        self.assertIn("openFeedbackModal(payload, eventType);", self.app_js)
        self.assertIn("submitFeedbackPayload", self.app_js)
        self.assertIn("function _feedbackReasonFor", self.app_js)
        self.assertIn("function feedbackStatusLabel", self.app_js)
        self.assertIn("latest.reasons", self.app_js)
        self.assertIn("feedbackStatusLabel(latest)", self.app_js)
        self.assertNotIn("window.prompt(`Reason for", self.app_js)

    def test_feedback_save_can_surface_voice_profile_nudge(self):
        self.assertIn("function maybeShowVoiceProfileNudge", self.app_js)
        self.assertIn("maybeShowVoiceProfileNudge(r.voice_profile_nudge)", self.app_js)
        self.assertIn("This clip can help build your local Creator Voice Profile.", self.app_js)

    def test_library_search_handler_is_window_visible(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")

        self.assertIn('oninput="filterLibrary()"', html)
        self.assertIn("const filterLibrary = _debounce", self.app_js)
        self.assertIn("window.filterLibrary = filterLibrary;", self.app_js)

    def test_all_videos_preserves_folder_state_and_supports_bulk_delete(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "gui" / "style.css").read_text(encoding="utf-8")
        api = (ROOT / "api_bridge.py").read_text(encoding="utf-8")

        self.assertIn("libraryOpenFolders", self.app_js)
        self.assertIn("state.libraryOpenFolders[folder.dataset.stem] = isOpen;", self.app_js)
        self.assertIn("Object.prototype.hasOwnProperty.call(state.libraryOpenFolders", self.app_js)
        self.assertIn("clip.source_stem", self.app_js)
        self.assertIn("librarySelectedFilenames", self.app_js)
        self.assertIn("libraryVisibleFilenames", self.app_js)
        self.assertIn("function requestDeleteSelectedLibrary", self.app_js)
        self.assertIn("delete_library_files(filenames)", self.app_js)
        self.assertIn('id="library-select-visible"', html)
        self.assertIn('id="library-clear-selected"', html)
        self.assertIn('id="library-delete-selected"', html)
        self.assertIn("library-select-input", self.app_js)
        self.assertIn(".library-item.selected", css)
        self.assertIn(".library-select-check", css)
        self.assertIn("def delete_library_files", api)

    def test_results_preserves_folder_state_and_supports_bulk_delete(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "gui" / "style.css").read_text(encoding="utf-8")

        self.assertIn("resultsOpenFolders", self.app_js)
        self.assertIn("state.resultsOpenFolders[folder.dataset.stem] = isOpen;", self.app_js)
        self.assertIn("Object.prototype.hasOwnProperty.call(state.resultsOpenFolders", self.app_js)
        self.assertIn("resultsSelectedFilenames", self.app_js)
        self.assertIn("resultsVisibleFilenames", self.app_js)
        self.assertIn("function requestDeleteSelectedResults", self.app_js)
        self.assertIn("state.pendingDeleteSource = 'results-bulk';", self.app_js)
        self.assertIn("delete_library_files(filenames)", self.app_js)
        self.assertIn('id="results-select-visible"', html)
        self.assertIn('id="results-clear-selected"', html)
        self.assertIn('id="results-delete-selected"', html)
        self.assertIn("result-select-input", self.app_js)
        self.assertIn(".result-card.selected", css)
        self.assertIn(".result-select-check", css)

    def test_settings_and_refresh_failures_are_user_visible(self):
        self.assertIn("function persistSettingsAsync", self.app_js)
        self.assertIn("await pywebview.api.save_settings(payload)", self.app_js)
        self.assertIn("toast('Settings could not be saved', 'error')", self.app_js)
        self.assertIn("async function refreshLibrary", self.app_js)
        self.assertIn("const ok = await loadLibrary();", self.app_js)
        self.assertIn("Could not refresh library", self.app_js)
        self.assertIn("Could not refresh clips for upload", self.app_js)

    def test_thumbnail_generation_samples_past_black_frames(self):
        self.assertIn("function _decodeThumbnail", self.app_js)
        self.assertIn("duration * 0.28", self.app_js)
        self.assertIn("duration * 0.72", self.app_js)
        self.assertIn("frame.black", self.app_js)
        self.assertIn("bestFrame", self.app_js)
        self.assertIn("mean < 18 && contrast < 18", self.app_js)
        self.assertIn("let seekTimer = null;", self.app_js)
        self.assertIn("clearSeekTimer();", self.app_js)
        self.assertIn("seekTimer = setTimeout(() =>", self.app_js)

    def test_moment_labels_are_visible_without_new_ranking_logic(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "gui" / "style.css").read_text(encoding="utf-8")

        self.assertIn('id="preview-moment-label"', html)
        self.assertIn("function momentLabelMarkup", self.app_js)
        self.assertIn("function aiMomentForClip", self.app_js)
        self.assertIn("function renderPreviewMomentLabel", self.app_js)
        self.assertIn("source: 'Detected'", self.app_js)
        self.assertIn("source = isOllama ? 'Ollama' : 'Fallback'", self.app_js)
        self.assertIn("const chips = [];", self.app_js)
        self.assertIn("if (detectedChip && (!aiChip || disagrees)) chips.push(detectedChip);", self.app_js)
        self.assertIn("renderPreviewMomentLabel();", self.app_js)
        self.assertIn("const momentLabel = momentLabelMarkup(clip, m);", self.app_js)
        self.assertIn("momentLabelMarkup(clip, m)", self.app_js)
        self.assertIn("const momentLabel = momentLabelMarkup(clip, clip);", self.app_js)
        self.assertIn("momentLabelMarkup(clip, clip)", self.app_js)
        self.assertIn(".moment-chip.is-ai", css)
        self.assertIn(".moment-chip.is-local", css)
        self.assertIn(".moment-chip.is-category", css)

    def test_moment_label_filters_are_local_review_controls(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "gui" / "style.css").read_text(encoding="utf-8")

        self.assertIn('id="results-moment-filters"', html)
        self.assertIn('id="library-moment-filters"', html)
        self.assertIn("resultsMomentFilter: 'all'", self.app_js)
        self.assertIn("libraryMomentFilter: 'all'", self.app_js)
        self.assertIn("function renderMomentFilterBar", self.app_js)
        self.assertIn("function clipMatchesMomentFilter", self.app_js)
        self.assertIn("function normalizeAvailableMomentFilter", self.app_js)
        self.assertIn("let categoryCount = 0;", self.app_js)
        self.assertIn("sourceParts.push(`${aiCount} Ollama`)", self.app_js)
        self.assertIn("sourceParts.push(`${localCount} fallback`)", self.app_js)
        self.assertIn("sourceParts.push(`${categoryCount} detected`)", self.app_js)
        self.assertIn("(!labeled && active === 'all')", self.app_js)
        self.assertIn("key === 'unlabeled' ? 'Unlabeled'", self.app_js)
        self.assertIn("renderMomentFilterBar(\n        'results-moment-filters'", self.app_js)
        self.assertIn("renderMomentFilterBar(\n        'library-moment-filters'", self.app_js)
        self.assertIn("function renderResultsGrid", self.app_js)
        self.assertIn("function setResultsMomentFilter", self.app_js)
        self.assertIn("state.resultsMomentFilter = String(filter || 'all');\n    renderResultsGrid();", self.app_js)
        self.assertNotIn("state.resultsMomentFilter = String(filter || 'all');\n    loadResults();", self.app_js)
        self.assertIn("state.resultsMomentFilter = normalizeAvailableMomentFilter", self.app_js)
        self.assertIn("state.libraryMomentFilter = normalizeAvailableMomentFilter", self.app_js)
        self.assertIn(".map((clip, index) => ({ ...clip, _idx: index }))", self.app_js)
        self.assertIn(".filter(clip => clipMatchesMomentFilter", self.app_js)
        self.assertIn("function setLibraryMomentFilter", self.app_js)
        self.assertIn(".moment-filter-btn.active", css)
        self.assertIn(".moment-filter-summary", css)

    def test_moment_labels_hide_when_metadata_is_absent(self):
        self.assertIn("if (!primary && !detectedPrimary) return null;", self.app_js)
        self.assertIn("if (!info) return '';", self.app_js)
        self.assertIn("el.classList.add('hidden');", self.app_js)
        self.assertIn("el.innerHTML = '';", self.app_js)

    def test_ollama_status_only_shows_ready_when_using_ollama(self):
        using_idx = self.app_js.index("if (status.using_ollama)")
        on_idx = self.app_js.index("label.textContent = 'Ollama on'", using_idx)
        running_idx = self.app_js.index("} else if (status.running)", using_idx)
        missing_idx = self.app_js.index("label.textContent = 'Model missing'", running_idx)

        self.assertLess(using_idx, on_idx)
        self.assertLess(on_idx, running_idx)
        self.assertLess(running_idx, missing_idx)
        self.assertIn("const isOllama = ai.status === 'ok' && ai.provider === 'ollama';", self.app_js)
        self.assertIn("const source = isOllama ? 'Ollama' : 'Fallback';", self.app_js)
        self.assertIn("source: 'Detected'", self.app_js)

    def test_progress_callbacks_are_monotonic_for_active_processing(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")
        self.assertIn("if (!state.processing) return;", self.app_js)
        self.assertIn("setProgress(0, `Starting${queueLabel}...`, true);", self.app_js)
        self.assertIn("window.onPipelineProgress = function (stage, percent, message, detail, context = null)", self.app_js)
        self.assertIn('id="progress-detail"', html)
        self.assertIn("function setProgressDetail(detail)", self.app_js)
        self.assertIn("setProgressDetail(detail);", self.app_js)
        self.assertIn("setProgressDetail('');", self.app_js)
        self.assertIn("stages.slice(0, idx).forEach(stageName => completeStage(stageName));", self.app_js)
        self.assertIn("function setProgress(pct, msg, allowDecrease = false)", self.app_js)
        self.assertIn("function backendHistoryEtaRemaining()", self.app_js)
        self.assertIn("estimatedTotalSeconds", self.app_js)
        self.assertIn('"estimatedTotalSeconds"', (ROOT / "api_bridge.py").read_text(encoding="utf-8"))

    def test_batch_progress_header_tracks_current_source(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "gui" / "style.css").read_text(encoding="utf-8")
        api = (ROOT / "api_bridge.py").read_text(encoding="utf-8")

        self.assertIn('id="batch-progress-card"', html)
        self.assertIn('id="batch-progress-source"', html)
        self.assertIn('id="batch-progress-counts"', html)
        self.assertIn('id="batch-progress-queue"', html)
        self.assertIn(".batch-progress-card", css)
        self.assertIn(".batch-progress-chip.active", css)
        self.assertIn("function sourceDisplayLabel", self.app_js)
        self.assertIn("function renderBatchProgress", self.app_js)
        self.assertIn("function applyPipelineProgressContext", self.app_js)
        self.assertIn("itemSettings.progress_context", self.app_js)
        self.assertIn("sourceName: batchItemLabel(item)", self.app_js)
        self.assertIn("self._progress_context_from_settings(settings", api)
        self.assertIn("window.onPipelineProgress('{stage}', {pct}, `{self._esc(msg)}`, {detail_arg}, {context_arg})", api)

    def test_no_quality_completion_is_a_warning_not_failure_state(self):
        css = (ROOT / "gui" / "style.css").read_text(encoding="utf-8")

        self.assertIn("window.onPipelineComplete = async function (success, doneCount, totalCount, errorMsg, details = null)", self.app_js)
        self.assertIn("completionDetails.completion_state === 'no_quality_clips'", self.app_js)
        self.assertIn("? (noQualityClips ? 'empty' : 'done')", self.app_js)
        self.assertIn("No Clips Created", self.app_js)
        self.assertIn("No clips met the quality bar", self.app_js)
        self.assertIn("if (!noQualityClips) refreshSubtitlePreviewSnapshot(true);", self.app_js)
        self.assertIn(".batch-queue-item.empty", css)
        self.assertIn(".batch-queue-item-status.empty", css)

    def test_clip_score_rendering_uses_finite_number_guard(self):
        self.assertIn("function finiteNumber(value, fallback = 0)", self.app_js)
        self.assertIn("const score = finiteNumber(moment.score, 0);", self.app_js)
        self.assertIn("const score = finiteNumber(m.score, 0);", self.app_js)
        self.assertIn("pct = Math.max(pct, state.overallPercent || 0);", self.app_js)
        self.assertIn("Math.max(1, totalClips || 1)", self.app_js)
        self.assertIn("const stepIndex = Math.max(0, steps.indexOf(substep));", self.app_js)
        self.assertIn("candidates: [24, 68]", self.app_js)
        self.assertIn("completeStage('candidates')", self.app_js)
        self.assertIn("Rough ETA:", self.app_js)
        self.assertIn("backendHistoryEtaRemaining()", self.app_js)

    def test_final_debug_preserves_active_ai_and_category_ranking_fields(self):
        api = (ROOT / "api_bridge.py").read_text(encoding="utf-8")
        self.assertIn('"ai_moment_quality_score": item.get("ai_moment_scoring", {}).get("ai_moment_quality_score")', api)
        self.assertIn('"ai_moment_scoring": item.get("ai_moment_scoring") or m.get("ai_moment_scoring")', api)
        self.assertIn('"moment_category_diversity_adjustment": item.get("moment_category_scoring", {}).get("category_diversity_adjustment")', api)
        self.assertIn('"source_path": m.get("source_path")', api)

    def test_pipeline_completion_cleanup_survives_ui_notification_failures(self):
        self.assertIn("Pipeline completion UI update failed; finishing queue cleanup anyway", self.app_js)
        self.assertIn("safeToast(`${doneCount} clips created successfully`, 'success');", self.app_js)
        self.assertIn("safeAddNotification(", self.app_js)
        self.assertIn("'Clips Ready'", self.app_js)
        self.assertIn("} finally {\n        if (hasMore)", self.app_js)
        self.assertIn("_onBatchComplete();", self.app_js)
        self.assertIn("document.getElementById('btn-cancel')?.classList.add('hidden');", self.app_js)
        self.assertIn("function safeToast", self.app_js)
        self.assertIn("function safeAddNotification", self.app_js)

    def test_backend_progress_stages_are_covered_by_frontend(self):
        api = (ROOT / "api_bridge.py").read_text(encoding="utf-8")
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")

        self.assertIn('data-stage="download"', html)
        self.assertIn('data-stage="detect"', html)
        self.assertIn('data-stage="candidates"', html)
        self.assertIn('data-stage="clips"', html)
        self.assertIn('data-stage="done"', html)
        self.assertIn('self._push("candidates"', api)
        self.assertIn("candidates: [24, 68]", self.app_js)
        self.assertIn("const stages = ['download', 'detect', 'candidates', 'clips', 'done'];", self.app_js)
        self.assertIn("completeStageThrough('done');", self.app_js)
        self.assertIn('id="progress-eta"', html)
        self.assertIn('id="progress-detail"', html)
        self.assertIn('id="batch-progress-card"', html)

    def test_data_privacy_modal_uses_wrapped_tabs_instead_of_horizontal_scroll(self):
        css = (ROOT / "gui" / "style.css").read_text(encoding="utf-8")

        self.assertIn("width: min(96vw, 980px)", css)
        self.assertIn("max-height: calc(100vh - 32px)", css)
        self.assertIn("flex-wrap: wrap", css)
        self.assertIn("overflow: visible", css)
        self.assertIn(".wizard-static-value", css)

    def test_audio_sources_wizard_step_is_wired(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")

        self.assertIn('data-step="2"><span class="wizard-step-num">2</span><span>Detection</span>', html)
        self.assertIn('data-step="3"><span class="wizard-step-num">3</span><span>Audio</span>', html)
        self.assertIn('id="wizard-step-3"', html)
        self.assertIn('id="wizard-audio-source-status"', html)
        self.assertIn('name="wizard-audio-mode" value="auto"', html)
        self.assertIn('name="wizard-audio-mode" value="stream"', html)
        self.assertIn('id="wizard-audio-stream-select"', html)
        self.assertIn('id="wizard-mixed-audio-guard"', html)
        self.assertIn('id="wizard-mixed-audio-subtitle-policy"', html)
        self.assertIn('<option value="creator" selected>Prefer my commentary</option>', html)
        self.assertIn('<option value="all">Include all speech</option>', html)
        self.assertIn('<option value="game">Prefer game/NPC speech</option>', html)
        self.assertIn("loadWizardAudioSources", self.app_js)
        self.assertIn("pywebview.api.probe_audio_sources(source)", self.app_js)
        self.assertIn("wizardAudioSourceSettings", self.app_js)
        self.assertIn("subtitle_policy: subtitlePolicy", self.app_js)
        self.assertIn("wizard-mixed-audio-subtitle-policy", self.app_js)

    def test_audio_source_choice_is_per_queue_item(self):
        self.assertIn("item.audioSource", self.app_js)
        self.assertIn("state.batchQueue.forEach(item => {", self.app_js)
        self.assertIn("item.audioSource =", self.app_js)
        self.assertIn("audio_source: item.audioSource || state.batchSettings?.audio_source", self.app_js)
        self.assertIn("if (currentBatchSourceCount() > 1)", self.app_js)
        self.assertIn("subtitle_policy: audioSource.subtitle_policy || 'creator'", self.app_js)
        self.assertIn("item.subtitleStyle", self.app_js)
        self.assertIn("item.subtitleStyle = settings.subtitle_style || 'tiktok';", self.app_js)
        self.assertIn("subtitle_style: item.subtitleStyle || item.settings?.subtitle_style || state.batchSettings?.subtitle_style || 'tiktok'", self.app_js)
        self.assertNotIn("subtitleOverride", self.app_js)
        self.assertNotIn("updateBatchSubtitleOverride", self.app_js)
        self.assertNotIn("batch-queue-caption-select", (ROOT / "gui" / "style.css").read_text(encoding="utf-8"))
        self.assertNotIn("batch-queue-caption", self.app_js)

    def test_completed_batch_history_is_pruned_before_new_processing(self):
        self.assertIn("function pruneCompletedBatchItemsForNewRun", self.app_js)
        self.assertIn("state.batchQueue = state.batchQueue.filter(q => q.status === 'pending');", self.app_js)
        self.assertIn("pruneCompletedBatchItemsForNewRun();", self.app_js)
        self.assertIn("function currentBatchSourceCount", self.app_js)
        self.assertIn("const multiSourceBatch = currentBatchSourceCount() > 1;", self.app_js)

    def test_voice_profile_controls_are_local_status_driven(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="voice-profile-enabled"', html)
        self.assertIn('id="voice-profile-toggle-btn"', html)
        self.assertIn('id="voice-profile-ranking"', html)
        self.assertIn('id="voice-profile-ranking-toggle-btn"', html)
        self.assertIn("No raw audio is saved", html)
        self.assertIn("Build &amp; Enable From Current Clips", html)
        self.assertIn("Building and enabling local voice profile", self.app_js)
        self.assertIn("function renderVoiceProfileStatus", self.app_js)
        self.assertIn('id="voice-profile-guidance"', html)
        self.assertIn("state.voiceProfile.influence_state", self.app_js)
        self.assertIn("state.voiceProfile.guidance", self.app_js)
        self.assertIn("Needs samples", self.app_js)
        self.assertIn("influenceState === 'needs_more_samples'", self.app_js)
        self.assertIn("Voice ranking saved. Build the profile before it can score eligible runs.", self.app_js)
        self.assertIn("renderVoiceProfileStatus(r.voice_profile || {});\n        if (r.error) return toast(r.error, 'error');", self.app_js)
        self.assertIn("pywebview.api.set_voice_profile_enabled", self.app_js)
        self.assertIn("pywebview.api.set_voice_profile_ranking_enabled", self.app_js)
        self.assertIn("pywebview.api.enroll_voice_profile_from_current_clips", self.app_js)
        self.assertIn("pywebview.api.reset_voice_profile", self.app_js)
        self.assertIn("voice_profile_ranking: Boolean", self.app_js)
        self.assertIn("r.voice_profile || {}", self.app_js)

    def test_data_privacy_details_are_in_advanced_tabbed_modal(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "gui" / "style.css").read_text(encoding="utf-8")

        self.assertIn("Advanced Features", html)
        self.assertIn('id="data-privacy-modal"', html)
        self.assertIn('data-privacy-tab="overview"', html)
        self.assertIn('data-privacy-tab="learning"', html)
        self.assertIn('data-privacy-tab="analysis"', html)
        self.assertIn('data-privacy-tab="voice"', html)
        self.assertIn('data-privacy-tab="files"', html)
        self.assertIn('id="analysis-visual"', html)
        self.assertIn('id="analysis-ai-labels"', html)
        self.assertIn('id="analysis-ranking"', html)
        self.assertIn('id="analysis-scene"', html)
        self.assertIn('id="analysis-voice"', html)
        self.assertIn("inactive in Fast", html)
        self.assertIn("Moment-label ranking", html)
        self.assertIn("separate opt-in", html)
        self.assertIn("candidate voice scores are available", html)
        self.assertIn('id="processing-history-runs"', html)
        self.assertIn("function renderLocalAnalysisStatus", self.app_js)
        self.assertIn("feature_statuses", self.app_js)
        self.assertIn("Inactive in Fast", self.app_js)
        self.assertIn("Deep Analysis can use high-confidence local labels as a tiny guarded ranking nudge", html)
        self.assertIn("function openDataPrivacyModal", self.app_js)
        self.assertIn("function setDataPrivacyTab", self.app_js)
        self.assertIn(".data-privacy-modal-body", css)
        self.assertIn(".data-privacy-tab.active", css)

    def test_visual_diagnostics_setting_is_sent_with_generation_settings(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="set-visual-diagnostics"', html)
        self.assertIn("Visual frame analysis", html)
        self.assertNotIn("Visual frame diagnostics", html)
        self.assertIn("visual_diagnostics: document.getElementById('set-visual-diagnostics')?.checked ?? true", self.app_js)
        self.assertIn('id="set-ai-moment-classification"', html)
        self.assertIn("AI moment labels", html)
        self.assertIn("ai_moment_classification: document.getElementById('set-ai-moment-classification')?.checked ?? false", self.app_js)
        self.assertIn('id="set-moment-category-ranking"', html)
        self.assertIn("Use moment labels in ranking", html)
        self.assertIn("moment_category_ranking: document.getElementById('set-moment-category-ranking')?.checked ?? false", self.app_js)

    def test_deep_analysis_wizard_describes_targeted_scene_analysis(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")

        self.assertIn("deeper targeted scene analysis", html)
        self.assertNotIn("full scene scan", html)


if __name__ == "__main__":
    unittest.main()
