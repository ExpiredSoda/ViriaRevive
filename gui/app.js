/* ── ViriaRevive Frontend v2 ──────────────────────────────────────────── */

const state = {
    section: 'generate',
    processing: false,
    settings: {},
    results: [],
    moments: [],
    feedbackByClipId: {},
    personalization: { schema_version: 1, events: [], clips: {} },
    overallPercent: 0,
    ytConnected: false,
    channels: [],
    categories: [],
    selectedChannel: null,
    // Calendar
    calYear: new Date().getFullYear(),
    calMonth: new Date().getMonth(),
    scheduled: [],          // [{clipIdx, clip_id, source_id, date, time, title, description, tags, category_id, privacy, uploaded}]
    editingScheduleIdx: -1,
    pickerDate: null,
    _schedPreset: 'allpeaks',
    calChannelFilter: 'all',  // 'all' or a channel ID
    // Library
    libraryClips: [],
    libraryView: 'grid',
    // Preview
    previewClipIdx: -1,
    // Delete
    pendingDeleteIdx: -1,
    pendingDeleteFilename: null,
    pendingDeleteSource: null, // 'results' | 'library' | 'preview'
    // Batch queue
    batchQueue: [],       // [{url, status: 'pending'|'active'|'done'|'error', label}]
    batchIndex: -1,       // current index being processed (-1 = not running)
    batchSettings: null,  // settings snapshot for the batch run
};

const DEFAULT_CATEGORY_ID = '20'; // Gaming
const DEFAULT_UPLOAD_TAGS = 'shorts, gaming, gameplay, gaming shorts, youtube shorts, viral shorts, stream highlights, streamer moments, live stream clips, funny gaming moments, scary gaming moments, horror gaming, scary game, creepy game, survival horror, horror shorts, jump scare, chase scene, panic moment, gaming reaction, lets play, playthrough, vertical gaming, game clips';
const SCHEDULE_BUFFER_MINUTES = 10;
let _lastOllamaStatus = null;

function scheduledLocalDate(item) {
    const date = String(item?.date || '').trim();
    const time = String(item?.time || '').trim() || '12:00';
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^\d{2}:\d{2}$/.test(time)) return null;
    const d = new Date(`${date}T${time}:00`);
    return Number.isNaN(d.getTime()) ? null : d;
}

function scheduledPublishFields(item) {
    const d = scheduledLocalDate(item);
    if (!d) {
        return { scheduled_local: '', publish_at: '', timezone_offset_minutes: null };
    }
    return {
        scheduled_local: `${item.date}T${item.time}:00`,
        publish_at: d.toISOString(),
        timezone_offset_minutes: d.getTimezoneOffset(),
    };
}

function isScheduleMissed(item, now = new Date()) {
    if (!item || item.uploaded) return false;
    const d = scheduledLocalDate(item);
    return !!d && d.getTime() < now.getTime();
}

function _isTodayDateStr(dateStr, now = new Date()) {
    return dateStr === _toDateStr(now);
}

function _isFutureScheduleSlot(dateStr, time, now = new Date()) {
    if (!_isTodayDateStr(dateStr, now)) return true;
    const slot = scheduledLocalDate({ date: dateStr, time });
    if (!slot) return false;
    return slot.getTime() > now.getTime() + SCHEDULE_BUFFER_MINUTES * 60 * 1000;
}

function hasPendingSchedule() {
    return state.scheduled.some(s => !s.uploaded);
}

function ensureSchedulerForPending() {
    if (!state.ytConnected || !hasPendingSchedule()) return;
    try { pywebview.api.start_scheduler(); } catch (_) {}
}

function descriptionProfile() {
    const profile = state.settings.description_profile || {};
    return {
        auto_hashtags: profile.auto_hashtags !== false,
        custom_text: String(profile.custom_text || ''),
    };
}

function setDescriptionProfile(profile) {
    state.settings.description_profile = {
        auto_hashtags: profile.auto_hashtags !== false,
        custom_text: String(profile.custom_text || ''),
    };
    refreshDescriptionOptionsStatus();
}

function descriptionGameHashtag(gameTitle = '') {
    const cleaned = String(gameTitle || '').replace(/[^A-Za-z0-9]+/g, ' ').trim();
    if (!cleaned) return '#Gaming';
    return '#' + cleaned.split(/\s+/).map(part => part.charAt(0).toUpperCase() + part.slice(1)).join('').slice(0, 48);
}

function recommendedDescriptionHashtags(gameTitle = '') {
    const tags = ['#shorts', descriptionGameHashtag(gameTitle), '#gaming'];
    const unique = [];
    const seen = new Set();
    tags.forEach(tag => {
        const key = tag.toLowerCase();
        if (seen.has(key)) return;
        unique.push(tag);
        seen.add(key);
    });
    return unique;
}

function generatedDescriptionForClip(clip, idx, title = '') {
    const moment = state.moments[idx] || {};
    const meta = moment.generated_metadata || {};
    if (meta.generated_description) return meta.generated_description;
    if (meta.description) return String(meta.description).split(/\n\s*#shorts\b/i)[0].trim();
    const cleanTitle = (title || clip?.filename || '').replace(/\.[^.]+$/, '').trim();
    return cleanTitle || 'Gameplay clip';
}

function gameTitleForClip(idx) {
    const moment = state.moments[idx] || {};
    const meta = moment.generated_metadata || {};
    return meta.game_title || moment.game_title || '';
}

function composeDescriptionPreview({ generated = '', custom = '', autoHashtags = true, gameTitle = '' } = {}) {
    const parts = [];
    if (String(generated || '').trim()) parts.push(String(generated).trim());
    if (String(custom || '').trim()) parts.push(String(custom).trim());
    if (autoHashtags) parts.push(recommendedDescriptionHashtags(gameTitle).join(' '));
    return parts.join('\n\n');
}

function updateScheduledDescriptionPreview(item) {
    if (!item) return item;
    const auto = item.description_auto_hashtags !== undefined
        ? item.description_auto_hashtags !== false
        : descriptionProfile().auto_hashtags;
    const final = composeDescriptionPreview({
        generated: item.description_generated || item.generated_description || item.title || '',
        custom: item.description_custom_text || '',
        autoHashtags: auto,
        gameTitle: item.game_title || '',
    });
    item.description_auto_hashtags = auto;
    item.description = final;
    item.final_description = final;
    return item;
}

function applyGeneratedMetadataToSchedule(item, meta) {
    if (!item || !meta) return item;
    const generated = meta.generated_description || meta.description_generated || meta.description || '';
    if (generated) {
        item.description_generated = generated;
        item.generated_description = generated;
    }
    if (meta.game_title) item.game_title = meta.game_title;
    if (meta.tags) item.tags = meta.tags;
    if (item.description_custom_text === undefined) item.description_custom_text = descriptionProfile().custom_text;
    if (item.description_auto_hashtags === undefined) item.description_auto_hashtags = descriptionProfile().auto_hashtags;
    updateScheduledDescriptionPreview(item);
    return item;
}

function descriptionFieldsForClip(clip, idx, title = '') {
    const profile = descriptionProfile();
    const gameTitle = gameTitleForClip(idx);
    const generated = generatedDescriptionForClip(clip, idx, title);
    const final = composeDescriptionPreview({
        generated,
        custom: profile.custom_text,
        autoHashtags: profile.auto_hashtags,
        gameTitle,
    });
    return {
        game_title: gameTitle,
        description_generated: generated,
        generated_description: generated,
        description_custom_text: profile.custom_text,
        description_auto_hashtags: profile.auto_hashtags,
        description: final,
        final_description: final,
        recommended_hashtags: recommendedDescriptionHashtags(gameTitle),
    };
}

function defaultUploadDescription(title = '') {
    return composeDescriptionPreview({
        generated: title,
        custom: descriptionProfile().custom_text,
        autoHashtags: descriptionProfile().auto_hashtags,
    });
}

function clipIndexById(clipId) {
    if (!clipId) return -1;
    return state.results.findIndex(c => c && c.clip_id === clipId);
}

function resolveScheduledClipIndex(item) {
    if (!item) return -1;
    const byId = clipIndexById(item.clip_id);
    if (byId >= 0) return byId;
    if (item.clip_filename) {
        const byName = state.results.findIndex(c => c && c.filename === item.clip_filename);
        if (byName >= 0) return byName;
    }
    if (item.clip_id || item.clip_filename) return -1;
    const idx = Number(item.clipIdx ?? item.index);
    if (Number.isInteger(idx) && idx >= 0 && idx < state.results.length) return idx;
    return -1;
}

function clipIdentityFields(clip, clipIdx) {
    return {
        clipIdx,
        clip_id: clip?.clip_id || null,
        source_id: clip?.source_id || null,
        source_stem: clip?.source_stem || null,
        clip_filename: clip?.filename || null,
    };
}

function channelById(channelId) {
    return state.channels.find(ch => ch.id === channelId) || null;
}

function channelIdentityFields(channelId) {
    const ch = channelById(channelId);
    return {
        channel_id: ch?.id || channelId || null,
        account_id: ch?.account_id || null,
        channel_title: ch?.title || '',
        account_title: ch?.account_title || '',
    };
}

function normalizeScheduledMetadata(items) {
    const normalized = [];
    (items || []).forEach(item => {
        if (!item) return;
        const clipIdx = resolveScheduledClipIndex(item);
        if (clipIdx < 0) return;
        const hasStructuredDescription = [
            'description_generated',
            'generated_description',
            'description_custom_text',
            'description_auto_hashtags',
            'final_description',
        ].some(key => Object.prototype.hasOwnProperty.call(item, key));
        Object.assign(item, clipIdentityFields(state.results[clipIdx], clipIdx));
        item.category_id = DEFAULT_CATEGORY_ID;
        if (!String(item.tags || '').trim()) item.tags = DEFAULT_UPLOAD_TAGS;
        if (!hasStructuredDescription && item.description) {
            item.final_description = item.description;
            if (item.channel_id && !item.account_id) Object.assign(item, channelIdentityFields(item.channel_id));
            normalized.push(item);
            return;
        }
        if (!item.description_generated && !item.generated_description) {
            item.description_generated = generatedDescriptionForClip(state.results[clipIdx], clipIdx, item.title);
            item.generated_description = item.description_generated;
        }
        if (item.description_custom_text === undefined) item.description_custom_text = '';
        if (item.description_auto_hashtags === undefined) item.description_auto_hashtags = descriptionProfile().auto_hashtags;
        if (!item.game_title) item.game_title = gameTitleForClip(clipIdx);
        if (item.channel_id && !item.account_id) Object.assign(item, channelIdentityFields(item.channel_id));
        updateScheduledDescriptionPreview(item);
        normalized.push(item);
    });
    return normalized;
}

function visibleClipList(clips) {
    return (clips || []).filter(c => c && c.filename && (c.url !== '' || (c.size_mb || 0) > 0));
}

async function loadPersonalization() {
    if (!window.pywebview || !pywebview.api || !pywebview.api.get_personalization) return;
    try {
        const data = await pywebview.api.get_personalization();
        state.personalization = data || { schema_version: 1, events: [], clips: {} };
        state.feedbackByClipId = state.personalization.clips || {};
        renderAllFeedbackStates();
        renderPreviewFeedbackState();
        refreshDataPrivacyCard(false);
    } catch (_) {}
}

function _feedbackForClip(clip) {
    return (clip && clip.clip_id && state.feedbackByClipId[clip.clip_id]) || {};
}

function _feedbackLatest(clip) {
    return _feedbackForClip(clip).latest || {};
}

function _feedbackActive(clip, eventType) {
    const latest = _feedbackLatest(clip);
    if (eventType === 'like') return !!latest.like;
    if (eventType === 'dislike') return !!latest.dislike;
    if (eventType === 'favorite') return !!latest.favorite;
    return false;
}

function renderFeedbackState(container, clip) {
    if (!container || !clip || !clip.clip_id) return;
    container.querySelectorAll('.feedback-btn').forEach(btn => {
        const eventType = btn.dataset.feedback;
        const active = _feedbackActive(clip, eventType);
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
}

function renderCardFeedbackState(card, clip) {
    renderFeedbackState(card, clip);
}

function renderAllFeedbackStates() {
    document.querySelectorAll('.result-card').forEach(card => {
        const idx = Number(card.dataset.clipIdx);
        if (Number.isInteger(idx) && idx >= 0) renderCardFeedbackState(card, state.results[idx]);
    });
}

function renderPreviewFeedbackState() {
    const panel = document.getElementById('preview-feedback');
    if (!panel) return;
    const clip = state.previewClipIdx >= 0 ? state.results[state.previewClipIdx] : null;
    if (!clip || !clip.clip_id) {
        panel.classList.add('hidden');
        return;
    }
    panel.classList.remove('hidden');
    renderFeedbackState(panel, clip);
    const latest = _feedbackLatest(clip);
    const status = document.getElementById('preview-feedback-status');
    if (status) {
        const flags = [];
        if (latest.like) flags.push('Liked');
        if (latest.dislike) flags.push('Disliked');
        if (latest.favorite) flags.push('Favorite');
        const reason = latest.reason ? ` - ${latest.reason}` : '';
        status.textContent = flags.length ? `${flags.join(' / ')}${reason}` : 'No feedback yet';
    }
}

function recordPreviewFeedback(eventType) {
    if (state.previewClipIdx < 0) return;
    recordClipFeedback(state.previewClipIdx, eventType);
}

async function recordClipFeedback(clipIdx, eventType) {
    const clip = state.results[clipIdx];
    if (!clip || !clip.clip_id) return toast('Clip identity is not ready yet', 'warning');
    const active = !_feedbackActive(clip, eventType);
    const label = active ? eventType : `remove ${eventType}`;
    let reason = window.prompt(`Reason for ${label}?`, '');
    if (reason === null) reason = '';

    try {
        const payload = {
            ...clipIdentityFields(clip, clipIdx),
            index: clipIdx,
            event_type: eventType,
            active,
            reason,
        };
        const r = await pywebview.api.record_feedback(payload);
        if (r.error) return toast(r.error, 'error');
        state.feedbackByClipId[clip.clip_id] = r.clip;
        renderAllFeedbackStates();
        renderPreviewFeedbackState();
        refreshDataPrivacyCard(false);
        toast(active ? `Marked ${eventType}` : `Removed ${eventType}`, 'success');
    } catch (e) {
        toast('Could not save feedback', 'error');
    }
}

async function refreshDataPrivacyCard(showToast = true) {
    if (!window.pywebview || !pywebview.api || !pywebview.api.get_data_privacy_summary) return;
    try {
        const r = await pywebview.api.get_data_privacy_summary();
        const p = r.personalization || {};
        const learning = r.learning || p.learning || {};
        const eventEl = document.getElementById('privacy-event-count');
        const clipEl = document.getElementById('privacy-clip-count');
        const sizeEl = document.getElementById('privacy-data-size');
        const learningEnabledEl = document.getElementById('learning-enabled');
        const activeSignalsEl = document.getElementById('learning-active-signals');
        const learnedCapEl = document.getElementById('learning-cap');
        const lastFeedbackEl = document.getElementById('learning-last-feedback');
        if (eventEl) eventEl.textContent = formatNumber(p.event_count || 0);
        if (clipEl) clipEl.textContent = formatNumber(p.clip_count || 0);
        if (sizeEl) sizeEl.textContent = formatBytes(p.size_bytes || 0);
        if (learningEnabledEl) {
            const enabled = Boolean(learning.enabled);
            learningEnabledEl.textContent = enabled ? 'Enabled' : 'Idle';
            learningEnabledEl.classList.toggle('is-active', enabled);
            learningEnabledEl.classList.toggle('is-idle', !enabled);
        }
        if (activeSignalsEl) activeSignalsEl.textContent = formatNumber(learning.active_feedback_signals || 0);
        if (learnedCapEl) learnedCapEl.textContent = learning.learned_cap_label || formatLearningCap(learning.learned_cap);
        if (lastFeedbackEl) {
            lastFeedbackEl.textContent = formatLearningTimestamp(learning.last_feedback_time || p.latest_timestamp);
            if (learning.last_feedback_time || p.latest_timestamp) lastFeedbackEl.title = learning.last_feedback_time || p.latest_timestamp;
            else lastFeedbackEl.removeAttribute('title');
        }
        if (showToast) toast('Data summary refreshed', 'success');
    } catch (_) {
        if (showToast) toast('Could not refresh data summary', 'error');
    }
}

async function openDataFolder() {
    try {
        const r = await pywebview.api.open_data_folder();
        if (r && r.error) toast(r.error, 'error');
    } catch (_) {
        toast('Could not open data folder', 'error');
    }
}

async function openYouTubeOAuthConsole() {
    try {
        const r = await pywebview.api.open_youtube_oauth_console();
        if (r && r.error) return toast(`Open this page: ${r.url}`, 'warning');
        toast('Opened Google Cloud credentials', 'info');
    } catch (_) {
        toast('Could not open Google Cloud credentials', 'error');
    }
}

async function openFfmpegDownload() {
    try {
        const r = await pywebview.api.open_ffmpeg_download();
        if (r && r.error) return toast(`Open this page: ${r.url}`, 'warning');
        toast('Opened FFmpeg download page', 'info');
    } catch (_) {
        toast('Could not open FFmpeg download page', 'error');
    }
}

async function openAppBinFolder() {
    try {
        const r = await pywebview.api.open_app_bin_folder();
        if (r && r.error) toast(r.error, 'error');
    } catch (_) {
        toast('Could not open app bin folder', 'error');
    }
}

async function openPersonalizationFile() {
    try {
        const r = await pywebview.api.open_personalization_file();
        if (r && r.error) toast(r.error, 'error');
    } catch (e) {
        toast('Could not open feedback file', 'error');
    }
}

async function exportPersonalizationFile() {
    try {
        const r = await pywebview.api.export_personalization();
        if (r.cancelled) return;
        if (r.error) return toast(r.error, 'error');
        toast('Share-safe feedback copy exported', 'success');
    } catch (e) {
        toast('Could not export feedback file', 'error');
    }
}

async function clearFeedbackData() {
    if (!confirm('Clear all local clip feedback? This removes like, dislike, favorite, reason history, and learned selection influence.')) return;
    try {
        const r = await pywebview.api.clear_personalization();
        if (r.error) return toast(r.error, 'error');
        state.feedbackByClipId = {};
        state.personalization = r.personalization || { schema_version: 1, events: [], clips: {} };
        renderAllFeedbackStates();
        renderPreviewFeedbackState();
        await refreshDataPrivacyCard(false);
        toast(r.backup ? 'Feedback cleared and backup created' : 'Feedback data cleared', 'success');
    } catch (e) {
        toast('Could not clear feedback data', 'error');
    }
}

function refreshDescriptionOptionsStatus() {
    const profile = descriptionProfile();
    const toggle = document.getElementById('desc-auto-hashtags-toggle');
    const status = document.getElementById('description-options-status');
    if (toggle) toggle.checked = profile.auto_hashtags;
    if (status) {
        const parts = [];
        parts.push(profile.auto_hashtags ? 'Recommended hashtags on' : 'Auto hashtags off');
        if (profile.custom_text.trim()) parts.push('custom text added');
        status.textContent = parts.join(' · ');
    }
}

function openDescriptionDefaultsModal() {
    const profile = descriptionProfile();
    const auto = document.getElementById('desc-default-auto-hashtags');
    const custom = document.getElementById('desc-default-custom-text');
    if (auto) auto.checked = profile.auto_hashtags;
    if (custom) custom.value = profile.custom_text;
    showModal('description-defaults-modal');
}

async function toggleDescriptionAutoHashtags(enabled) {
    const profile = descriptionProfile();
    setDescriptionProfile({ ...profile, auto_hashtags: enabled });
    try { await pywebview.api.save_settings(state.settings); } catch (_) {}
    state.scheduled.forEach(item => {
        item.description_auto_hashtags = enabled;
        updateScheduledDescriptionPreview(item);
    });
    persistSchedule();
    renderTimeline();
    refreshDescriptionOptionsStatus();
}

async function saveDescriptionDefaults(applyToScheduled = false) {
    const profile = {
        auto_hashtags: document.getElementById('desc-default-auto-hashtags')?.checked !== false,
        custom_text: document.getElementById('desc-default-custom-text')?.value || '',
    };
    setDescriptionProfile(profile);
    try { await pywebview.api.save_settings(state.settings); } catch (_) {}
    if (applyToScheduled) {
        state.scheduled.forEach(item => {
            item.description_auto_hashtags = profile.auto_hashtags;
            item.description_custom_text = profile.custom_text;
            updateScheduledDescriptionPreview(item);
        });
        persistSchedule();
        renderTimeline();
        renderCalendar();
        toast('Description text applied to scheduled clips', 'success');
    } else {
        toast('Description defaults saved', 'success');
    }
    closeModal('description-defaults-modal');
    refreshDescriptionOptionsStatus();
}

function hideUploadProgressAfter(ms = 1800) {
    const uploadCard = document.getElementById('upload-progress-card');
    window.clearTimeout(window._uploadProgressHideTimer);
    window._uploadProgressHideTimer = window.setTimeout(() => {
        if (!uploadCard) return;
        uploadCard.classList.add('hidden');
        document.getElementById('upload-status').textContent = 'Uploading...';
        document.getElementById('upload-percent').textContent = '0%';
        document.getElementById('upload-fill').style.width = '0%';
        const cancelBtn = document.getElementById('btn-cancel-upload');
        if (cancelBtn) cancelBtn.disabled = false;
    }, ms);
}

function updateOllamaActionButtons(status) {
    const locationBtn = document.getElementById('btn-ollama-download');
    const installBtn = document.getElementById('btn-ollama-install');
    const modelBtn = document.getElementById('btn-ollama-model');
    const installed = !!(status && (status.installed || status.running || status.install_path));
    const running = !!(status && status.running);
    const modelReady = !!(status && status.model_ready);
    const model = status?.model || 'qwen2.5:3b';

    if (locationBtn) {
        locationBtn.disabled = false;
        locationBtn.textContent = installed ? 'Open Ollama Folder' : 'Install Ollama';
        locationBtn.title = installed
            ? 'Open the local Ollama install folder'
            : 'Open the official Ollama download page';
    }

    if (installBtn) {
        installBtn.disabled = false;
        if (running && modelReady) {
            installBtn.textContent = 'Ollama Installed';
            installBtn.title = 'Ollama is installed and ready';
            installBtn.disabled = true;
        } else if (installed) {
            installBtn.textContent = 'Repair via PowerShell';
            installBtn.title = 'Run Ollama’s official installer again if the local service needs repair';
        } else {
            installBtn.textContent = 'Install via PowerShell';
            installBtn.title = 'Run Ollama’s official Windows installer in a visible PowerShell window';
        }
    }

    if (modelBtn) {
        modelBtn.disabled = !running;
        modelBtn.textContent = modelReady ? 'Title Model Ready' : running ? 'Download Title Model' : 'Start Ollama First';
        modelBtn.title = modelReady
            ? `${model} is installed; click to re-check`
            : running
                ? `Download ${model} for AI titles`
                : 'Start or install Ollama before downloading the title model';
    }
}

async function refreshOllamaStatus() {
    const pill = document.getElementById('ollama-status');
    if (!pill || !window.pywebview || !pywebview.api) return;
    const label = pill.querySelector('.ollama-label');
    const settingsDot = document.getElementById('settings-ollama-dot');
    const settingsState = document.getElementById('settings-ollama-state');
    const settingsDetail = document.getElementById('settings-ollama-detail');
    pill.classList.remove('ready', 'partial', 'offline', 'error');
    pill.classList.add('checking');
    if (label) label.textContent = 'Ollama...';
    if (settingsState) settingsState.textContent = 'Checking';
    if (settingsDetail) settingsDetail.textContent = 'Ollama status';
    if (settingsDot) settingsDot.className = 'ollama-settings-dot checking';
    try {
        const status = await pywebview.api.get_ollama_status();
        _lastOllamaStatus = status;
        updateOllamaActionButtons(status);
        pill.classList.remove('checking');
        if (status.using_ollama) {
            pill.classList.add('ready');
            if (label) label.textContent = 'Ollama on';
            const version = status.version ? ` ${status.version}` : '';
            pill.title = `Ollama${version} is running and ${status.model} is ready for AI titles`;
            if (settingsState) settingsState.textContent = 'Ready';
            if (settingsDetail) settingsDetail.textContent = `${status.model}${version ? ` • ${version}` : ''}`;
            if (settingsDot) settingsDot.className = 'ollama-settings-dot ready';
        } else if (status.running) {
            pill.classList.add('partial');
            if (label) label.textContent = 'Model missing';
            const version = status.version ? ` ${status.version}` : '';
            pill.title = `Ollama${version} is running, but ${status.model} is not installed yet`;
            if (settingsState) settingsState.textContent = 'Model Missing';
            if (settingsDetail) settingsDetail.textContent = `${status.model}${version ? ` • ${version}` : ''}`;
            if (settingsDot) settingsDot.className = 'ollama-settings-dot partial';
        } else {
            pill.classList.add('offline');
            if (label) label.textContent = 'Ollama off';
            pill.title = 'Ollama is not running; title generation will use the fallback';
            if (settingsState) settingsState.textContent = 'Not Running';
            if (settingsDetail) settingsDetail.textContent = `${status.model || 'qwen2.5:3b'} fallback active`;
            if (settingsDot) settingsDot.className = 'ollama-settings-dot offline';
        }
    } catch (e) {
        _lastOllamaStatus = null;
        updateOllamaActionButtons(null);
        pill.classList.remove('checking');
        pill.classList.add('error');
        if (label) label.textContent = 'Ollama ?';
        pill.title = 'Could not check Ollama status';
        if (settingsState) settingsState.textContent = 'Unknown';
        if (settingsDetail) settingsDetail.textContent = 'Could not check Ollama';
        if (settingsDot) settingsDot.className = 'ollama-settings-dot error';
    }
}

async function handleOllamaLocationAction() {
    if (_lastOllamaStatus && (_lastOllamaStatus.installed || _lastOllamaStatus.running || _lastOllamaStatus.install_path)) {
        return openOllamaFolder();
    }
    return openOllamaDownload();
}

async function openOllamaFolder() {
    try {
        const r = await pywebview.api.open_ollama_folder();
        if (r && r.error) return toast(r.error, 'error');
        toast('Opened Ollama folder', 'info');
    } catch (_) {
        toast('Could not open Ollama folder', 'error');
    }
}

async function openOllamaDownload() {
    try {
        const r = await pywebview.api.open_ollama_download();
        if (r && r.error) return toast(`Open this page: ${r.url}`, 'warning');
        toast('Opened official Ollama download page', 'info');
    } catch (_) {
        toast('Could not open Ollama download page', 'error');
    }
}

async function installOllamaWithPowerShell() {
    if (_lastOllamaStatus && _lastOllamaStatus.running && _lastOllamaStatus.model_ready) {
        return toast('Ollama is already installed and ready', 'success');
    }
    const command = 'irm https://ollama.com/install.ps1 | iex';
    if (!confirm(`Run the official Ollama Windows installer in PowerShell?\n\n${command}\n\nThis downloads and runs code from ollama.com in a new PowerShell window.`)) {
        return;
    }
    try {
        const prepared = await pywebview.api.prepare_ollama_install();
        if (prepared && prepared.error) return toast(prepared.error, 'error');
        const r = await pywebview.api.install_ollama_with_powershell(prepared.token);
        if (r && r.error) return toast(r.error, 'error');
        toast('Opened Ollama installer in PowerShell', 'info');
    } catch (_) {
        toast('Could not start Ollama installer', 'error');
    }
}

async function downloadOllamaModel() {
    try {
        if (_lastOllamaStatus && _lastOllamaStatus.model_ready) {
            toast(`${_lastOllamaStatus.model} is already ready for AI titles`, 'success');
            return refreshOllamaStatus();
        }
        if (_lastOllamaStatus && !_lastOllamaStatus.running) {
            toast('Start Ollama before downloading the title model', 'warning');
            return refreshOllamaStatus();
        }
        toast('Downloading title model with Ollama...', 'info');
        const r = await pywebview.api.ensure_ollama_model();
        if (r.ready) {
            toast(`${r.model} is ready for AI titles`, 'success');
        } else {
            toast('Ollama is not running yet', 'warning');
        }
        refreshOllamaStatus();
    } catch (_) {
        toast('Could not download Ollama title model', 'error');
    }
}

/* ── Thumbnail generator (queued + lazy) ─────────────────────────────── */

const _thumbCache = {};   // url → dataURL cache
const _thumbQueue = [];   // pending thumbnail tasks
let _thumbActive = 0;
const _THUMB_CONCURRENCY = 2;  // max simultaneous video decodes

function generateThumbnail(videoUrl, targetEl, seekTime = 1.0) {
    if (!targetEl) return;
    // Check cache first — instant
    if (_thumbCache[videoUrl]) {
        _applyThumb(targetEl, _thumbCache[videoUrl]);
        return;
    }
    // Queue instead of firing immediately
    _thumbQueue.push({ url: videoUrl, el: targetEl, seek: seekTime });
    _processThumbQueue();
}

function _processThumbQueue() {
    while (_thumbActive < _THUMB_CONCURRENCY && _thumbQueue.length) {
        const task = _thumbQueue.shift();
        // Skip if element is no longer in DOM (tab switched, etc.)
        if (!task.el.isConnected) continue;
        // Skip if already cached (queued duplicate)
        if (_thumbCache[task.url]) { _applyThumb(task.el, _thumbCache[task.url]); continue; }
        _thumbActive++;
        _decodeThumbnail(task.url, task.el, task.seek);
    }
}

function _decodeThumbnail(videoUrl, targetEl, seekTime) {
    const vid = document.createElement('video');
    vid.crossOrigin = 'anonymous';
    vid.muted = true;
    vid.preload = 'metadata';
    vid.playsInline = true;

    const cleanup = () => { vid.src = ''; vid.load(); _thumbActive--; _processThumbQueue(); };

    vid.addEventListener('loadeddata', () => {
        vid.currentTime = Math.min(seekTime, vid.duration * 0.5 || seekTime);
    });

    vid.addEventListener('seeked', () => {
        try {
            const canvas = document.createElement('canvas');
            // Use smaller size for thumbnails — saves memory
            const scale = Math.min(1, 320 / (vid.videoWidth || 320));
            canvas.width = Math.round((vid.videoWidth || 320) * scale);
            canvas.height = Math.round((vid.videoHeight || 180) * scale);
            const ctx = canvas.getContext('2d');
            ctx.drawImage(vid, 0, 0, canvas.width, canvas.height);
            const dataUrl = canvas.toDataURL('image/jpeg', 0.6);
            _thumbCache[videoUrl] = dataUrl;
            if (targetEl.isConnected) _applyThumb(targetEl, dataUrl);
        } catch (e) { /* CORS or other error */ }
        cleanup();
    });

    vid.addEventListener('error', cleanup);
    // Timeout safety — don't block queue forever
    setTimeout(() => { if (_thumbActive > 0 && vid.readyState < 2) cleanup(); }, 8000);

    vid.src = videoUrl;
}

function _applyThumb(el, dataUrl) {
    if (!el) return;
    el.style.backgroundImage = `url(${dataUrl})`;
    el.style.backgroundSize = 'cover';
    el.style.backgroundPosition = 'center';
    const placeholder = el.querySelector('.thumb-placeholder');
    if (placeholder) placeholder.style.opacity = '0';
}

/* ── Lazy loading via IntersectionObserver ────────────────────────────── */

const _lazyObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            const el = entry.target;
            const url = el.dataset.lazyThumbUrl;
            if (url) {
                generateThumbnail(url, el);
                el.removeAttribute('data-lazy-thumb-url');
            }
            _lazyObserver.unobserve(el);
        }
    });
}, { rootMargin: '200px' });  // start loading 200px before visible

function lazyThumb(el, url) {
    if (_thumbCache[url]) {
        _applyThumb(el, _thumbCache[url]);
    } else {
        el.dataset.lazyThumbUrl = url;
        _lazyObserver.observe(el);
    }
}

/* ── Utility: throttle & debounce ────────────────────────────────────── */

function _throttle(fn, ms) {
    let last = 0, timer = null;
    return function (...args) {
        const now = Date.now();
        const remaining = ms - (now - last);
        clearTimeout(timer);
        if (remaining <= 0) { last = now; fn.apply(this, args); }
        else { timer = setTimeout(() => { last = Date.now(); fn.apply(this, args); }, remaining); }
    };
}

function _debounce(fn, ms) {
    let timer;
    return function (...args) { clearTimeout(timer); timer = setTimeout(() => fn.apply(this, args), ms); };
}

function applyAppMetadata(meta) {
    if (!meta) return;
    const name = meta.name || 'ViriaRevive';
    const version = meta.version_display || (meta.version ? `v${meta.version}` : '');
    const fullName = version ? `${name} ${version}` : name;
    const versionEl = document.getElementById('app-version');
    if (versionEl && version) {
        versionEl.textContent = version;
        versionEl.title = fullName;
    }
    const aboutTitle = document.getElementById('about-app-title');
    if (aboutTitle) aboutTitle.textContent = fullName;
    document.title = fullName;
}

/* ── Init ──────────────────────────────────────────────────────────────── */

window.addEventListener('pywebviewready', async () => {
    try {
        try {
            applyAppMetadata(await pywebview.api.get_app_metadata());
        } catch (_) {}

        const deps = await pywebview.api.check_dependencies();
        if (!deps.ffmpeg || !deps.ffprobe) showModal('ffmpeg-modal');
        refreshOllamaStatus();

        // Backend (viria_state.json) is the source of truth for settings.
        // localStorage is a fallback for first-run only.
        const backendSettings = await pywebview.api.get_settings();
        const local = loadLocal('settings', {});
        // Use backend settings, fall back to localStorage for any missing keys
        state.settings = { ...local, ...backendSettings };
        populateSettings(state.settings);
        refreshDescriptionOptionsStatus();

        // Load persisted state from previous session
        const persisted = await pywebview.api.load_persisted_state();
        if (persisted.clips && persisted.clips.length) {
            state.results = visibleClipList(persisted.clips);
            state.moments = persisted.moments || [];
        }
        if (persisted.scheduled && persisted.scheduled.length) {
            state.scheduled = normalizeScheduledMetadata(persisted.scheduled);
            persistSchedule();
        }
        await loadPersonalization();

        const yt = await pywebview.api.youtube_status();
        if (yt.connected) {
            state.ytConnected = true;
            await loadChannelsAndCategories();
            if (state.scheduled.length) {
                state.scheduled = normalizeScheduledMetadata(state.scheduled);
                persistSchedule();
            }
            updateYtUI(true);
        }

        // Render peak times legend on init
        _renderPeakTimesLegend();

        // Start the local background scheduler only when it can actually upload.
        if (yt.connected && hasPendingSchedule()) {
            await pywebview.api.start_scheduler();
            document.getElementById('scheduler-bar').classList.remove('hidden');
        }
    } catch (e) {
        console.error('Init error:', e);
    }
});

setInterval(() => {
    if (window.pywebview && pywebview.api) refreshOllamaStatus();
}, 60000);

// When window is restored from minimized/hidden, flush any queued JS calls
document.addEventListener('visibilitychange', async () => {
    if (!document.hidden && window.pywebview && pywebview.api) {
        try { await pywebview.api.flush_pending_js(); } catch (_) {}
    }
});
window.addEventListener('focus', async () => {
    if (window.pywebview && pywebview.api) {
        try { await pywebview.api.flush_pending_js(); } catch (_) {}
    }
});

// Ctrl+Enter to start processing from textarea
document.getElementById('url-input')?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        startProcessing();
    }
});

// Auto-grow textarea as user types
document.getElementById('url-input')?.addEventListener('input', e => {
    const el = e.target;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 140) + 'px';
});

// Auto-detect paste of multiple URLs and add to queue
document.getElementById('url-input')?.addEventListener('paste', e => {
    setTimeout(() => {
        const val = document.getElementById('url-input').value;
        const lines = val.split('\n').map(l => l.trim()).filter(l => l);
        if (lines.length > 1) {
            lines.forEach(url => addToBatchQueue(url));
            document.getElementById('url-input').value = '';
        }
    }, 50);
});

document.getElementById('set-auto-clips')?.addEventListener('change', e => {
    const slider = document.getElementById('set-num-clips');
    const label = document.getElementById('val-num-clips');
    if (slider) slider.disabled = e.target.checked;
    if (label) label.textContent = e.target.checked ? 'Auto' : slider.value;
    // Persist immediately so the setting survives app restart
    gatherSettings();
});

// Auto-save all settings when any setting input changes
document.querySelectorAll('#section-settings input, #section-settings select').forEach(el => {
    el.addEventListener('change', () => { try { gatherSettings(); } catch (_) {} });
});

document.querySelectorAll('.style-option').forEach(opt => {
    opt.addEventListener('click', () => {
        document.querySelectorAll('.style-option').forEach(o => o.classList.remove('active'));
        opt.classList.add('active');
        opt.querySelector('input[type="radio"]').checked = true;
        updateSubtitlePlacementPreview();
        try { gatherSettings(); } catch (_) {}
    });
});

document.querySelectorAll('.style-pick-card').forEach(card => {
    card.addEventListener('click', () => {
        document.querySelectorAll('.style-pick-card').forEach(c => c.classList.remove('active'));
        card.classList.add('active');
        card.querySelector('input[type="radio"]').checked = true;
    });
});

document.querySelectorAll('#preview-feedback .feedback-btn').forEach(btn => {
    btn.addEventListener('click', event => {
        event.stopPropagation();
        recordPreviewFeedback(btn.dataset.feedback);
    });
});

/* ── Navigation ────────────────────────────────────────────────────────── */

function navigateTo(section) {
    state.section = section;
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.getElementById(`section-${section}`)?.classList.add('active');
    document.querySelector(`.nav-item[data-section="${section}"]`)?.classList.add('active');
    if (section === 'results') loadResults();
    if (section === 'upload') loadUploadSection();
    if (section === 'library') loadLibrary();
    if (section === 'settings') refreshDataPrivacyCard(false);
}

/* ── Generate ──────────────────────────────────────────────────────────── */

async function startProcessing() {
    if (state.processing) return;

    const urlInput = document.getElementById('url-input').value.trim();

    // Build queue from: existing batch queue items + url input
    if (!state.batchQueue.length && !urlInput) {
        return toast('Enter a YouTube URL, paste multiple links, or browse files', 'warning');
    }

    // If no batch queue yet, parse the url input (could be multiple lines)
    if (!state.batchQueue.length && urlInput) {
        const urls = urlInput.split('\n').map(u => u.trim()).filter(u => u);
        urls.forEach(u => addToBatchQueue(u));
        document.getElementById('url-input').value = '';
    } else if (urlInput && !state.batchQueue.some(q => q.url === urlInput)) {
        // URL typed while queue exists — add it
        const urls = urlInput.split('\n').map(u => u.trim()).filter(u => u);
        urls.forEach(u => addToBatchQueue(u));
        document.getElementById('url-input').value = '';
    }

    if (!state.batchQueue.length) return toast('Nothing to process', 'warning');

    // Show style picker modal before starting
    openStylePicker();
}

function openStylePicker() {
    const currentStyle = document.querySelector('input[name="subtitle-style"]:checked')?.value || 'tiktok';
    document.querySelectorAll('.style-pick-card').forEach(card => {
        const isActive = card.dataset.style === currentStyle;
        card.classList.toggle('active', isActive);
        card.querySelector('input[type="radio"]').checked = isActive;
    });
    wizardNext(1);
    loadEffectsGrid();
    loadMusicList();

    // Restore previous wizard settings
    const saved = loadLocal('wizard', {});
    if (saved.effect) {
        document.querySelectorAll('.effect-card').forEach(c => {
            c.classList.toggle('active', c.dataset.effect === saved.effect);
        });
    }
    if (saved.musicEnabled) {
        document.getElementById('wizard-music-enabled').checked = true;
        document.getElementById('music-options').classList.remove('hidden');
    }
    if (saved.musicVolume) {
        const vol = document.getElementById('wizard-music-volume');
        if (vol) { vol.value = saved.musicVolume; document.getElementById('val-music-vol').textContent = saved.musicVolume + '%'; }
    }

    showModal('style-picker-modal');
}

/* ── Wizard Navigation ────────────────────────────────────────────────── */

function wizardNext(step) {
    // Hide all wizard pages
    document.querySelectorAll('.wizard-page').forEach(p => p.classList.remove('active'));
    document.getElementById(`wizard-step-${step}`)?.classList.add('active');

    // Update step indicators
    document.querySelectorAll('.wizard-step').forEach(s => {
        const sNum = parseInt(s.dataset.step);
        s.classList.toggle('active', sNum === step);
        s.classList.toggle('completed', sNum < step);
    });
    // Update step lines
    const lines = document.querySelectorAll('.wizard-step-line');
    lines.forEach((l, i) => l.classList.toggle('completed', i < step - 1));
}

async function loadEffectsGrid() {
    const grid = document.getElementById('effects-grid');
    if (grid.children.length > 0) return; // already loaded

    try {
        const r = await pywebview.api.get_effects();
        const effects = r.effects || [];
        grid.innerHTML = '';
        const saved = loadLocal('wizard', {});
        effects.forEach(fx => {
            const card = document.createElement('div');
            card.className = 'effect-card' + (fx.id === (saved.effect || 'none') ? ' active' : '');
            card.dataset.effect = fx.id;
            card.innerHTML = `<span class="effect-card-name">${escHtml(fx.label)}</span><span class="effect-card-desc">${escHtml(fx.desc)}</span>`;
            card.onclick = () => {
                document.querySelectorAll('.effect-card').forEach(c => c.classList.remove('active'));
                card.classList.add('active');
            };
            grid.appendChild(card);
        });
    } catch (e) {
        grid.innerHTML = '<div class="music-empty">Could not load effects</div>';
    }
}

async function loadMusicList() {
    const list = document.getElementById('music-track-list');
    try {
        const r = await pywebview.api.list_music();
        const tracks = r.tracks || [];
        list.innerHTML = '';

        if (!tracks.length) {
            list.innerHTML = '<div class="music-empty">No music files found.<br>Add .mp3/.wav files to the music/ folder.</div>';
            return;
        }

        const saved = loadLocal('wizard', {});
        tracks.forEach(track => {
            const item = document.createElement('div');
            item.className = 'music-track' + (saved.musicFile === track.filename ? ' active' : '');
            item.dataset.filename = track.filename;
            item.innerHTML = `
                <svg class="music-track-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
                <span class="music-track-name">${escHtml(track.filename)}</span>
                <span class="music-track-size">${track.size_mb} MB</span>`;
            item.onclick = () => {
                document.querySelectorAll('.music-track').forEach(t => t.classList.remove('active'));
                item.classList.add('active');
                loadWaveform(track.filename);
            };
            list.appendChild(item);
        });

        // Auto-load waveform for saved/active track
        if (saved.musicFile) {
            const activeTrack = tracks.find(t => t.filename === saved.musicFile);
            if (activeTrack) loadWaveform(activeTrack.filename);
        }
    } catch (e) {
        list.innerHTML = '<div class="music-empty">Could not load music</div>';
    }
}

// Music toggle
document.getElementById('wizard-music-enabled')?.addEventListener('change', e => {
    document.getElementById('music-options').classList.toggle('hidden', !e.target.checked);
    if (e.target.checked) loadMusicList();
});

async function openMusicFolder() {
    try { await pywebview.api.open_music_folder(); } catch (_) {}
    // Refresh the list after a short delay
    setTimeout(() => loadMusicList(), 1000);
}

/* ── Waveform Trimmer ────────────────────────────────────────────────── */

const trimmerState = {
    peaks: [],
    duration: 0,
    startPct: 0,    // 0.0 - 1.0
    endPct: 1,      // 0.0 - 1.0
    dragging: null,  // 'left' | 'right' | 'region' | null
    dragStartX: 0,
    dragStartPcts: [0, 1],
    filename: null,
    audioUrl: null,
};

async function loadWaveform(filename) {
    const trimmer = document.getElementById('music-trimmer');
    const wrap = document.getElementById('trimmer-canvas-wrap');

    trimmerState.filename = filename;
    trimmer.classList.remove('hidden');
    document.getElementById('trimmer-track-name').textContent = filename;
    wrap.innerHTML = '<div class="trimmer-loading">Loading waveform...</div>';

    try {
        const r = await pywebview.api.get_music_waveform(filename);
        if (r.error || !r.peaks || !r.peaks.length) {
            wrap.innerHTML = '<div class="trimmer-loading">Could not load waveform</div>';
            return;
        }

        trimmerState.peaks = r.peaks;
        trimmerState.duration = r.duration;

        // Restore saved trim or default to full
        const saved = loadLocal('wizard', {});
        if (saved.musicFile === filename && saved.musicTrimStart != null) {
            trimmerState.startPct = saved.musicTrimStart / r.duration;
            trimmerState.endPct = saved.musicTrimEnd / r.duration;
        } else {
            trimmerState.startPct = 0;
            trimmerState.endPct = 1;
        }

        // Rebuild canvas + overlay elements
        wrap.innerHTML = `
            <canvas id="trimmer-canvas" height="64"></canvas>
            <div class="trimmer-selection" id="trimmer-selection">
                <div class="trimmer-handle trimmer-handle-left" id="trimmer-handle-left"></div>
                <div class="trimmer-handle trimmer-handle-right" id="trimmer-handle-right"></div>
            </div>
            <div class="trimmer-playhead" id="trimmer-playhead"></div>`;

        document.getElementById('trimmer-duration').textContent = fmtTime(r.duration);
        drawWaveform();
        updateTrimmerSelection();
        initTrimmerDrag();

        // Set up audio for preview
        try {
            const urlResult = await pywebview.api.get_music_url(filename);
            if (urlResult.url) {
                trimmerState.audioUrl = urlResult.url;
                const audio = document.getElementById('trimmer-audio');
                if (audio) audio.src = urlResult.url;
            }
        } catch (_) {}

    } catch (e) {
        wrap.innerHTML = '<div class="trimmer-loading">Failed to load waveform</div>';
    }
}

function drawWaveform() {
    const canvas = document.getElementById('trimmer-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.parentElement.getBoundingClientRect();

    canvas.width = rect.width * dpr;
    canvas.height = 64 * dpr;
    canvas.style.width = rect.width + 'px';
    canvas.style.height = '64px';
    ctx.scale(dpr, dpr);

    const w = rect.width;
    const h = 64;
    const peaks = trimmerState.peaks;
    if (!peaks.length) return;

    const barWidth = Math.max(1, (w / peaks.length) - 1);
    const gap = 1;

    ctx.clearRect(0, 0, w, h);

    peaks.forEach((peak, i) => {
        const x = (i / peaks.length) * w;
        const barH = Math.max(2, peak * (h * 0.85));
        const y = (h - barH) / 2;

        const pct = i / peaks.length;
        const inSelection = pct >= trimmerState.startPct && pct <= trimmerState.endPct;

        if (inSelection) {
            ctx.fillStyle = 'rgba(0, 206, 201, 0.7)';
        } else {
            ctx.fillStyle = 'rgba(255, 255, 255, 0.15)';
        }

        ctx.fillRect(x, y, Math.max(1, barWidth), barH);
    });
}

function updateTrimmerSelection() {
    const sel = document.getElementById('trimmer-selection');
    if (!sel) return;
    const wrap = document.getElementById('trimmer-canvas-wrap');
    const wrapW = wrap.getBoundingClientRect().width;

    const left = trimmerState.startPct * wrapW;
    const right = trimmerState.endPct * wrapW;

    sel.style.left = left + 'px';
    sel.style.width = Math.max(0, right - left) + 'px';

    // Update time labels
    const startSec = trimmerState.startPct * trimmerState.duration;
    const endSec = trimmerState.endPct * trimmerState.duration;
    document.getElementById('trimmer-start-time').textContent = fmtTime(startSec);
    document.getElementById('trimmer-end-time').textContent = fmtTime(endSec);
    document.getElementById('trimmer-sel-duration').textContent = `Selected: ${fmtTime(endSec - startSec)}`;

    drawWaveform();
}

function initTrimmerDrag() {
    const wrap = document.getElementById('trimmer-canvas-wrap');
    const leftH = document.getElementById('trimmer-handle-left');
    const rightH = document.getElementById('trimmer-handle-right');
    if (!wrap || !leftH || !rightH) return;

    const getXPct = (e) => {
        const rect = wrap.getBoundingClientRect();
        const clientX = e.touches ? e.touches[0].clientX : e.clientX;
        return Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    };

    leftH.addEventListener('mousedown', (e) => {
        e.stopPropagation();
        trimmerState.dragging = 'left';
        trimmerState.dragStartX = getXPct(e);
    });

    rightH.addEventListener('mousedown', (e) => {
        e.stopPropagation();
        trimmerState.dragging = 'right';
        trimmerState.dragStartX = getXPct(e);
    });

    // Click on waveform to set region start point
    wrap.addEventListener('mousedown', (e) => {
        if (trimmerState.dragging) return;
        const pct = getXPct(e);
        // If clicking inside selection, drag the whole region
        if (pct > trimmerState.startPct + 0.02 && pct < trimmerState.endPct - 0.02) {
            trimmerState.dragging = 'region';
            trimmerState.dragStartX = pct;
            trimmerState.dragStartPcts = [trimmerState.startPct, trimmerState.endPct];
        } else {
            // Click to set new start point, drag to select
            trimmerState.startPct = pct;
            trimmerState.endPct = pct;
            trimmerState.dragging = 'right';
            updateTrimmerSelection();
        }
    });

    // Throttled mousemove — cap at ~60fps to avoid layout thrashing
    const _trimmerMove = _throttle((e) => {
        if (!trimmerState.dragging) return;
        const pct = getXPct(e);

        if (trimmerState.dragging === 'left') {
            trimmerState.startPct = Math.min(pct, trimmerState.endPct - 0.01);
        } else if (trimmerState.dragging === 'right') {
            trimmerState.endPct = Math.max(pct, trimmerState.startPct + 0.01);
        } else if (trimmerState.dragging === 'region') {
            const delta = pct - trimmerState.dragStartX;
            const width = trimmerState.dragStartPcts[1] - trimmerState.dragStartPcts[0];
            let newStart = trimmerState.dragStartPcts[0] + delta;
            let newEnd = trimmerState.dragStartPcts[1] + delta;
            if (newStart < 0) { newStart = 0; newEnd = width; }
            if (newEnd > 1) { newEnd = 1; newStart = 1 - width; }
            trimmerState.startPct = newStart;
            trimmerState.endPct = newEnd;
        }

        trimmerState.startPct = Math.max(0, trimmerState.startPct);
        trimmerState.endPct = Math.min(1, trimmerState.endPct);
        updateTrimmerSelection();
    }, 16);

    document.addEventListener('mousemove', _trimmerMove);

    document.addEventListener('mouseup', () => {
        if (trimmerState.dragging) {
            trimmerState.dragging = null;
            // Ensure minimum selection
            if (trimmerState.endPct - trimmerState.startPct < 0.01) {
                trimmerState.endPct = Math.min(1, trimmerState.startPct + 0.05);
                updateTrimmerSelection();
            }
        }
    });
}

function trimmerReset() {
    trimmerState.startPct = 0;
    trimmerState.endPct = 1;
    updateTrimmerSelection();
}

function trimmerSelectAll() {
    trimmerState.startPct = 0;
    trimmerState.endPct = 1;
    updateTrimmerSelection();
}

function trimmerPlayPreview() {
    const audio = document.getElementById('trimmer-audio');
    if (!audio || !trimmerState.audioUrl) {
        toast('Audio preview not available', 'warning');
        return;
    }

    const startSec = trimmerState.startPct * trimmerState.duration;
    const endSec = trimmerState.endPct * trimmerState.duration;
    const playhead = document.getElementById('trimmer-playhead');
    const btn = document.getElementById('btn-trimmer-play');

    // If already playing, stop
    if (!audio.paused) {
        audio.pause();
        if (playhead) playhead.style.display = 'none';
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> Preview';
        return;
    }

    audio.currentTime = startSec;
    audio.play();
    btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg> Stop';

    if (playhead) playhead.style.display = 'block';

    const updatePlayhead = () => {
        if (audio.paused || audio.currentTime >= endSec) {
            audio.pause();
            if (playhead) playhead.style.display = 'none';
            btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> Preview';
            return;
        }
        const pct = audio.currentTime / trimmerState.duration;
        if (playhead) {
            const wrap = document.getElementById('trimmer-canvas-wrap');
            playhead.style.left = (pct * wrap.getBoundingClientRect().width) + 'px';
        }
        requestAnimationFrame(updatePlayhead);
    };
    requestAnimationFrame(updatePlayhead);
}

function getMusicTrimValues() {
    if (!trimmerState.filename || !trimmerState.duration) return null;
    return {
        start: Math.round(trimmerState.startPct * trimmerState.duration * 100) / 100,
        end: Math.round(trimmerState.endPct * trimmerState.duration * 100) / 100,
    };
}

function confirmStyleAndGenerate() {
    const pickedStyle = document.querySelector('input[name="picker-style"]:checked')?.value || 'tiktok';

    // Sync subtitle style back to settings
    document.querySelectorAll('.style-option').forEach(opt => {
        opt.classList.toggle('active', opt.dataset.style === pickedStyle);
        const radio = opt.querySelector('input[type="radio"]');
        if (radio) radio.checked = opt.dataset.style === pickedStyle;
    });
    updateSubtitlePlacementPreview();

    // Save wizard choices for next time
    const selectedEffect = document.querySelector('.effect-card.active')?.dataset.effect || 'none';
    const musicEnabled = document.getElementById('wizard-music-enabled')?.checked || false;
    const selectedTrack = document.querySelector('.music-track.active')?.dataset.filename || null;
    const musicVolume = parseInt(document.getElementById('wizard-music-volume')?.value || '12');

    const trimValues = getMusicTrimValues();
    saveLocal('wizard', {
        effect: selectedEffect,
        musicEnabled: musicEnabled,
        musicFile: selectedTrack,
        musicVolume: musicVolume,
        musicTrimStart: trimValues ? trimValues.start : null,
        musicTrimEnd: trimValues ? trimValues.end : null,
    });

    closeModal('style-picker-modal');

    // Snapshot settings for the entire batch
    const settings = gatherSettings();
    settings.video_effect = selectedEffect;
    settings.music_file = musicEnabled && selectedTrack ? selectedTrack : null;
    settings.music_volume = musicVolume / 100;
    settings.music_start = trimValues ? trimValues.start : 0;
    settings.music_end = trimValues ? trimValues.end : 0;
    state.batchSettings = settings;

    state.processing = true;
    state.batchIndex = -1;

    document.getElementById('generate-idle').classList.add('hidden');
    document.getElementById('progress-area').classList.remove('hidden');
    document.getElementById('completion-banner').classList.add('hidden');
    document.getElementById('btn-cancel').classList.remove('hidden');
    document.getElementById('clip-cards').innerHTML = '';

    // Start processing the first item in the queue
    processNextInQueue();
}

async function cancelProcessing() {
    try { await pywebview.api.cancel_processing(); } catch (_) {}
    // Cancel stops the current item; clear the rest of the queue
    state.batchQueue.forEach(q => { if (q.status === 'pending') q.status = 'cancelled'; });
    state.batchIndex = -1;
    renderBatchQueue();
}

/* ── Batch Queue ──────────────────────────────────────────────────────── */

function addUrlsFromInput() {
    const textarea = document.getElementById('url-input');
    const val = textarea.value.trim();
    if (!val) return;
    const lines = val.split('\n').map(l => l.trim()).filter(l => l);
    lines.forEach(url => addToBatchQueue(url));
    textarea.value = '';
    textarea.style.height = 'auto'; // reset height after clearing
}

function addToBatchQueue(url) {
    if (!url) return;
    // Avoid duplicates
    if (state.batchQueue.some(q => q.url === url)) return;
    const label = url.length > 60 ? url.slice(0, 57) + '...' : url;
    state.batchQueue.push({ url, label, status: 'pending' });
    renderBatchQueue();
}

function removeBatchItem(idx) {
    if (state.batchQueue[idx]?.status === 'active') return; // can't remove active
    state.batchQueue.splice(idx, 1);
    renderBatchQueue();
    if (!state.batchQueue.length) {
        document.getElementById('batch-queue').classList.add('hidden');
    }
}

function clearBatchQueue() {
    if (state.processing) return toast('Cannot clear queue while processing', 'warning');
    state.batchQueue = [];
    state.batchIndex = -1;
    renderBatchQueue();
    document.getElementById('batch-queue').classList.add('hidden');
}

function renderBatchQueue() {
    const container = document.getElementById('batch-queue');
    const list = document.getElementById('batch-queue-list');
    const label = document.getElementById('batch-queue-label');
    if (!list) return;

    if (!state.batchQueue.length) {
        container.classList.add('hidden');
        return;
    }
    container.classList.remove('hidden');

    const pending = state.batchQueue.filter(q => q.status === 'pending').length;
    const done = state.batchQueue.filter(q => q.status === 'done').length;
    label.innerHTML = `Queue: <strong>${state.batchQueue.length}</strong> items (${done} done, ${pending} pending)`;

    list.innerHTML = '';
    state.batchQueue.forEach((q, i) => {
        const li = document.createElement('li');
        li.className = `batch-queue-item ${q.status}`;
        li.innerHTML = `
            <span class="batch-queue-item-label" title="${escHtml(q.url)}">${escHtml(q.label)}</span>
            <span class="batch-queue-item-status ${q.status}">${q.status}</span>
            ${q.status === 'pending' ? `<button class="batch-queue-item-remove" onclick="removeBatchItem(${i})">&times;</button>` : ''}`;
        list.appendChild(li);
    });
}

async function processNextInQueue() {
    // Find the next pending item
    state.batchIndex++;
    while (state.batchIndex < state.batchQueue.length && state.batchQueue[state.batchIndex].status !== 'pending') {
        state.batchIndex++;
    }

    if (state.batchIndex >= state.batchQueue.length) {
        // All done
        _onBatchComplete();
        return;
    }

    const item = state.batchQueue[state.batchIndex];
    item.status = 'active';
    renderBatchQueue();

    // Update progress UI
    const queueLabel = state.batchQueue.length > 1
        ? ` (${state.batchIndex + 1}/${state.batchQueue.length})`
        : '';
    resetStages();
    setProgress(0, `Starting${queueLabel}...`);
    document.getElementById('clip-cards').innerHTML = '';

    try {
        let r = await pywebview.api.start_processing(item.url, state.batchSettings);
        // Retry once if "Already processing" (race with previous pipeline's finally block)
        if (r.error && r.error.includes('Already processing')) {
            await new Promise(ok => setTimeout(ok, 1500));
            r = await pywebview.api.start_processing(item.url, state.batchSettings);
        }
        if (r.error) {
            item.status = 'error';
            toast(`Failed: ${item.label} — ${r.error}`, 'error');
            renderBatchQueue();
            // Continue to next
            processNextInQueue();
        }
        // Otherwise, onPipelineComplete will call processNextInQueue
    } catch (e) {
        item.status = 'error';
        toast(`Failed: ${item.label}`, 'error');
        renderBatchQueue();
        processNextInQueue();
    }
}

function _onBatchComplete() {
    state.processing = false;
    state.batchIndex = -1;
    document.getElementById('btn-cancel').classList.add('hidden');

    const done = state.batchQueue.filter(q => q.status === 'done').length;
    const errors = state.batchQueue.filter(q => q.status === 'error').length;
    const total = state.batchQueue.length;

    document.getElementById('completion-title').textContent =
        errors ? `${done}/${total} Videos Done` : 'All Done!';
    document.getElementById('completion-message').textContent =
        `Processed ${done} video${done !== 1 ? 's' : ''}${errors ? `, ${errors} failed` : ''}.`;
    document.getElementById('completion-banner').classList.remove('hidden');

    toast(`Batch complete: ${done} done${errors ? `, ${errors} failed` : ''}`, done ? 'success' : 'error');

    // Refresh results to include all clips from all processed videos
    pywebview.api.get_results().then(r => {
        state.results = visibleClipList(r.clips);
        state.moments = r.moments || state.moments;
    }).catch(() => {});
}

async function browseFilesMulti() {
    try {
        const r = await pywebview.api.select_files_multiple();
        if (r && r.paths && r.paths.length) {
            r.paths.forEach(p => addToBatchQueue(p));
            // Clear single URL input since we're using queue
            document.getElementById('url-input').value = '';
        }
    } catch (_) {}
}

function resetGenerate() {
    state.processing = false;
    document.getElementById('generate-idle').classList.remove('hidden');
    document.getElementById('progress-area').classList.add('hidden');
    document.getElementById('btn-cancel').classList.add('hidden');
}

async function browseFile() {
    try {
        const r = await pywebview.api.select_file();
        if (r && r.path) {
            if (state.batchQueue.length) {
                // If queue already has items, add to queue instead
                addToBatchQueue(r.path);
            } else {
                document.getElementById('url-input').value = r.path;
            }
        }
    } catch (_) {}
}

/* ── Console Panel ────────────────────────────────────────────────────── */

function toggleConsole() {
    const panel = document.getElementById('console-panel');
    panel.classList.toggle('hidden');
    if (!panel.classList.contains('hidden')) {
        const log = document.getElementById('console-log');
        log.scrollTop = log.scrollHeight;
    }
}

function clearConsole() {
    document.getElementById('console-log').innerHTML = '';
}

function toggleGlobalConsole() {
    const panel = document.getElementById('global-console');
    panel.classList.toggle('hidden');
    if (!panel.classList.contains('hidden')) {
        const log = document.getElementById('global-console-log');
        log.scrollTop = log.scrollHeight;
    }
}

function clearGlobalConsole() {
    document.getElementById('global-console-log').innerHTML = '';
}

function _appendLogLine(log, text) {
    const line = document.createElement('div');
    line.className = 'log-line';

    // Color-code by prefix
    if (text.includes('[+]') || text.includes('complete') || text.includes('success'))
        line.classList.add('log-success');
    else if (text.includes('[!]') || text.includes('fail') || text.includes('error'))
        line.classList.add('log-error');
    else if (text.includes('[*]') || text.includes('Loading') || text.includes('Starting'))
        line.classList.add('log-info');
    else if (text.includes('WARNING') || text.includes('[warn]'))
        line.classList.add('log-warn');

    const time = document.createElement('span');
    time.className = 'log-time';
    const now = new Date();
    time.textContent = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;

    line.appendChild(time);
    line.appendChild(document.createTextNode(text));
    log.appendChild(line);

    // Auto-scroll + trim old lines
    if (log.children.length > 500) log.removeChild(log.firstChild);
    log.scrollTop = log.scrollHeight;
}

window.onConsoleLog = function (text) {
    // Write to both the in-progress console and the global console
    const log = document.getElementById('console-log');
    if (log) _appendLogLine(log, text);
    const glog = document.getElementById('global-console-log');
    if (glog) _appendLogLine(glog, text);
};

/* ── Progress Callbacks ───────────────────────────────────────────────── */

window.onPipelineProgress = function (stage, percent, message) {
    if (stage === 'upload') {
        const pct = Math.min(100, Math.max(0, Math.round(percent)));
        const uploadCard = document.getElementById('upload-progress-card');
        if (uploadCard) uploadCard.classList.remove('hidden');
        document.getElementById('upload-status').textContent = message || 'Uploading...';
        document.getElementById('upload-percent').textContent = `${pct}%`;
        document.getElementById('upload-fill').style.width = `${pct}%`;
        return;
    }
    const ranges = { download: [0, 15], detect: [15, 30], clips: [30, 95], upload: [0, 100] };
    const r = ranges[stage] || [0, 100];
    setProgress(r[0] + (percent / 100) * (r[1] - r[0]), message);
    activateStage(stage);
    if (stage === 'download' && percent >= 100) completeStage('download');
    if (stage === 'detect' && percent >= 100) completeStage('detect');
};

window.onClipProgress = function (clipNum, totalClips, substep, percent, message) {
    const sw = { audio: [0, 0.10], transcribe: [0.10, 0.40], subtitle: [0.40, 0.60], render: [0.60, 1.0] }[substep] || [0, 1];
    const clipFrac = sw[0] + (percent / 100) * (sw[1] - sw[0]);
    const perClip = 65 / totalClips;
    setProgress(30 + (clipNum - 1) * perClip + clipFrac * perClip, message);
    activateStage('clips');
    updateClipCard(clipNum, totalClips, substep, percent, message);
};

window.onMomentsDetected = function (moments) {
    state.moments = moments;
    const grid = document.getElementById('clip-cards');
    grid.innerHTML = '';
    moments.forEach((m, i) => grid.appendChild(createClipCard(i + 1, moments.length, m)));
};

async function refreshScheduleFromBackend(render = false) {
    try {
        const r = await pywebview.api.get_all_scheduled();
        if (r.scheduled) state.scheduled = normalizeScheduledMetadata(r.scheduled);
    } catch (_) {
        state.scheduled = normalizeScheduledMetadata(state.scheduled);
    }
    if (render) {
        renderCalendar();
        renderTimeline();
        renderClipTray();
    }
}

window.onPipelineComplete = async function (success, doneCount, totalCount, errorMsg) {
    const uploadCard = document.getElementById('upload-progress-card');
    const uploadVisible = uploadCard && !uploadCard.classList.contains('hidden');
    if (uploadVisible && document.getElementById('btn-upload')?.disabled) {
        await refreshScheduleFromBackend(false);
        const partial = !success && doneCount > 0;
        const pct = success ? 100 : partial && totalCount ? Math.round((doneCount / totalCount) * 100) : 0;
        document.getElementById('upload-fill').style.width = `${pct}%`;
        document.getElementById('upload-percent').textContent = `${pct}%`;
        document.getElementById('upload-status').textContent = success
            ? `Upload finished (${doneCount}/${totalCount})`
            : (errorMsg || 'Upload failed');
        document.getElementById('btn-upload').disabled = false;
        const cancelBtn = document.getElementById('btn-cancel-upload');
        if (cancelBtn) cancelBtn.disabled = true;
        toast(
            success ? `Uploaded ${doneCount} clip${doneCount !== 1 ? 's' : ''}` : (errorMsg || 'Upload failed'),
            success ? 'success' : partial ? 'warning' : 'error'
        );
        await loadUploadSection();
        if (success || partial) hideUploadProgressAfter();
        return;
    }

    // Mark current batch item
    if (state.batchIndex >= 0 && state.batchIndex < state.batchQueue.length) {
        state.batchQueue[state.batchIndex].status = success ? 'done' : 'error';
        renderBatchQueue();
    }

    if (success) {
        setProgress(100, `${doneCount} clips created`);
        completeStage('clips'); completeStage('done');
        toast(`${doneCount} clips created successfully`, 'success');
        addNotification(
            'Clips Ready',
            `${doneCount} viral clip${doneCount > 1 ? 's' : ''} generated and ready to upload`,
            'success'
        );

        // Accumulate results (don't overwrite — append from all batch items)
        pywebview.api.get_results().then(r => {
            state.results = visibleClipList(r.clips);
            state.moments = r.moments || state.moments;
        }).catch(() => {});
    } else {
        toast(errorMsg || 'Processing failed', 'error');
        addNotification('Processing Failed', errorMsg || 'An error occurred during clip generation', 'error');
    }

    // Check if there are more items in the queue
    const hasMore = state.batchQueue.some((q, i) => i > state.batchIndex && q.status === 'pending');
    if (hasMore) {
        // Short delay before starting next to let UI update
        setTimeout(() => processNextInQueue(), 500);
    } else {
        // All done (or single video)
        _onBatchComplete();
    }
};

window.onPipelineCancelled = async function () {
    const uploadCard = document.getElementById('upload-progress-card');
    const uploadVisible = uploadCard && !uploadCard.classList.contains('hidden');
    if (uploadVisible && document.getElementById('btn-upload')?.disabled) {
        await refreshScheduleFromBackend(false);
        document.getElementById('upload-status').textContent = 'Upload stopped';
        document.getElementById('upload-percent').textContent = '0%';
        document.getElementById('upload-fill').style.width = '0%';
        document.getElementById('btn-upload').disabled = false;
        const cancelBtn = document.getElementById('btn-cancel-upload');
        if (cancelBtn) cancelBtn.disabled = false;
        toast('Upload stopped', 'warning');
        await loadUploadSection();
        hideUploadProgressAfter(1200);
        return;
    }

    state.processing = false;
    if (state.batchIndex >= 0 && state.batchIndex < state.batchQueue.length) {
        state.batchQueue[state.batchIndex].status = 'error';
    }
    state.batchIndex = -1;
    renderBatchQueue();
    toast('Processing cancelled', 'warning');
    resetGenerate();
};

/* ── Scheduler Callbacks ──────────────────────────────────────────────── */

window.onSchedulerStatus = function (msg) {
    const bar = document.getElementById('scheduler-bar');
    bar.classList.remove('hidden');
    document.getElementById('scheduler-status-text').textContent = msg;
    // Add uploading notification if it looks like an active upload
    if (msg.toLowerCase().includes('uploading')) {
        addNotification('Uploading', msg, 'uploading');
    }
};

// Update scheduler bar — cache next upload and only recalc when needed
let _cachedNextUpload = null;
let _nextUploadCacheTime = 0;

setInterval(() => {
    const bar = document.getElementById('scheduler-bar');
    if (!bar || bar.classList.contains('hidden')) return;

    const now = Date.now();
    // Recalculate next upload only every 60s (it rarely changes)
    if (!_cachedNextUpload || now - _nextUploadCacheTime > 60000) {
        _cachedNextUpload = null;
        let earliest = Infinity;
        for (const s of state.scheduled) {
            if (s.uploaded) continue;
            const dt = new Date(`${s.date}T${s.time}`).getTime();
            if (dt < earliest) { earliest = dt; _cachedNextUpload = s; }
        }
        _nextUploadCacheTime = now;
    }

    if (!_cachedNextUpload) return;
    const diffMs = new Date(`${_cachedNextUpload.date}T${_cachedNextUpload.time}`).getTime() - now;
    if (diffMs > 0) {
        const hrs = Math.floor(diffMs / 3600000);
        const mins = Math.floor((diffMs % 3600000) / 60000);
        document.getElementById('scheduler-status-text').textContent =
            `Next upload: Clip ${_cachedNextUpload.clipIdx + 1} in ${hrs}h ${mins}m`;
    }
}, 30000);

window.onScheduledUploadDone = function (clipIdx, success, error) {
    const clipName = state.results[clipIdx]?.filename || `Clip ${clipIdx + 1}`;
    if (success) {
        toast(`Clip ${clipIdx + 1} uploaded by scheduler`, 'success');
        addNotification(
            'Upload Complete',
            `${clipName} was uploaded to YouTube successfully`,
            'success'
        );
        state.scheduled.forEach(s => {
            if (s.clipIdx === clipIdx && !s.uploaded) s.uploaded = true;
        });
        renderCalendar();
        renderTimeline();
    } else {
        toast(`Scheduler upload failed: ${error}`, 'error');
        addNotification(
            'Upload Failed',
            `${clipName}: ${error}`,
            'error'
        );
    }
};

window.onScheduleUpdated = function () {
    refreshScheduleFromBackend(true);
};

/* ── Progress Helpers ──────────────────────────────────────────────────── */

function setProgress(pct, msg) {
    pct = Math.min(100, Math.max(0, pct));
    document.getElementById('progress-fill').style.width = pct + '%';
    document.getElementById('progress-percent').textContent = Math.round(pct) + '%';
    if (msg) document.getElementById('progress-status').textContent = msg;
}

function resetStages() {
    document.querySelectorAll('.stage').forEach(s => s.classList.remove('active', 'completed'));
    document.querySelectorAll('.stage-line').forEach(l => l.classList.remove('active', 'completed'));
}
function activateStage(name) {
    const el = document.querySelector(`.stage[data-stage="${name}"]`);
    if (el && !el.classList.contains('completed')) el.classList.add('active');
}
function completeStage(name) {
    const el = document.querySelector(`.stage[data-stage="${name}"]`);
    if (el) { el.classList.remove('active'); el.classList.add('completed'); }
    const stages = ['download', 'detect', 'clips', 'done'];
    const idx = stages.indexOf(name);
    if (idx > 0) { const lines = document.querySelectorAll('.stage-line'); if (lines[idx - 1]) lines[idx - 1].classList.add('completed'); }
}

/* ── Clip Progress Cards ───────────────────────────────────────────────── */

function createClipCard(num, total, moment) {
    const card = document.createElement('div');
    card.className = 'clip-progress-card';
    card.id = `clip-card-${num}`;
    card.style.animationDelay = `${(num - 1) * 0.06}s`;
    const score = moment.score || 0;
    const sc = score >= 0.7 ? 'high' : score >= 0.4 ? 'mid' : 'low';
    card.innerHTML = `
        <div class="clip-card-header">
            <span class="clip-num">Clip ${num}</span>
            <span class="clip-time">${fmtTime(moment.start)} - ${fmtTime(moment.end)}</span>
            <span class="clip-score ${sc}">${score.toFixed(2)}</span>
        </div>
        <div class="clip-substep">Waiting...</div>
        <div class="clip-bar"><div class="clip-bar-fill" style="width:0%"></div></div>`;
    return card;
}

function updateClipCard(num, total, substep, percent, message) {
    let card = document.getElementById(`clip-card-${num}`);
    if (!card) { const grid = document.getElementById('clip-cards'); card = createClipCard(num, total, state.moments[num-1] || {start:0,end:0,score:0}); grid.appendChild(card); }
    const labels = { audio: 'Extracting audio', transcribe: 'Transcribing', subtitle: 'Generating subtitles', render: 'Rendering clip' };
    card.querySelector('.clip-substep').textContent = (percent >= 100 && substep === 'render') ? 'Complete' : (labels[substep] || substep) + '...';
    card.querySelector('.clip-bar-fill').style.width = (['audio','transcribe','subtitle','render'].indexOf(substep) * 25 + (percent/100) * 25) + '%';
    card.classList.remove('processing', 'done');
    if (percent >= 100 && substep === 'render') { card.classList.add('done'); card.querySelector('.clip-bar-fill').style.width = '100%'; }
    else card.classList.add('processing');
}

/* ── Results ───────────────────────────────────────────────────────────── */

function _groupResultsByStem(clips) {
    const groups = {};
    clips.forEach((clip, i) => {
        // Use source_stem from moments (persists through renames),
        // then try filename pattern, then fall back to 'Other'
        let stem = clip.source_stem;
        if (!stem) {
            const m = state.moments[i];
            if (m && m.source_stem) stem = m.source_stem;
        }
        if (!stem) {
            const match = clip.filename.match(/^(.+?)_viral\d+/i);
            stem = match ? match[1] : clip.filename.replace(/\.[^.]+$/, '');
        }
        if (!groups[stem]) groups[stem] = { stem, clips: [] };
        groups[stem].clips.push({ ...clip, _idx: i });
    });
    return Object.values(groups);
}

function _buildResultCard(clip, i) {
    const m = state.moments[i] || {};
    const score = m.score || 0;
    const sc = score >= 0.7 ? 'high' : score >= 0.4 ? 'mid' : 'low';
    const card = document.createElement('div');
    card.className = 'result-card';
    card.dataset.clipIdx = i;
    card.innerHTML = `
        <div class="result-card-thumb" data-clip-idx="${i}" onclick="previewClip(${i})">
            <div class="thumb-placeholder">
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            </div>
            <div class="result-card-overlay">
                <button class="play-btn" type="button" aria-label="Preview clip ${i + 1}">
                    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                </button>
            </div>
            <button class="result-card-delete" onclick="event.stopPropagation(); requestDeleteResult(${i})" title="Delete" aria-label="Delete clip ${i + 1}">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            </button>
        </div>
        <div class="result-card-info">
            <div class="result-card-top">
                <span class="result-clip-num">Clip ${i+1}</span>
                <span class="clip-score ${sc}">${score.toFixed(2)}</span>
            </div>
            <div class="result-filename">${escHtml(clip.filename)}</div>
            <div class="result-meta">
                ${m.start !== undefined ? `<span>${fmtTime(m.start)} - ${fmtTime(m.end)}</span>` : ''}
                <span>${clip.size_mb} MB</span>
            </div>
            <div class="result-feedback" aria-label="Clip feedback">
                <button type="button" class="feedback-btn" data-feedback="like" title="Like this clip and nudge future picks" aria-label="Like this clip and nudge future picks">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 10v11"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2h0a3.13 3.13 0 0 1 3 3.88Z"/></svg>
                </button>
                <button type="button" class="feedback-btn" data-feedback="dislike" title="Dislike this clip and reduce similar future picks" aria-label="Dislike this clip and reduce similar future picks">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 14V3"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22h0a3.13 3.13 0 0 1-3-3.88Z"/></svg>
                </button>
                <button type="button" class="feedback-btn" data-feedback="favorite" title="Favorite this clip and boost similar future picks" aria-label="Favorite this clip and boost similar future picks">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
                </button>
            </div>
        </div>`;
    card.querySelectorAll('.feedback-btn').forEach(btn => {
        btn.addEventListener('click', event => {
            event.stopPropagation();
            recordClipFeedback(i, btn.dataset.feedback);
        });
    });
    renderCardFeedbackState(card, clip);
    return card;
}

function toggleFolder(headerEl) {
    const folder = headerEl.closest('.result-folder');
    folder.classList.toggle('open');
}

async function loadResults() {
    try {
        const r = await pywebview.api.get_results();
        state.results = visibleClipList(r.clips);
        state.moments = r.moments || state.moments;
        await loadPersonalization();
    } catch (_) {}
    const grid = document.getElementById('results-grid');
    const empty = document.getElementById('results-empty');
    const countEl = document.getElementById('results-count');
    if (countEl) countEl.textContent = state.results.length ? state.results.length + ' clip' + (state.results.length !== 1 ? 's' : '') : '';
    if (!state.results.length) { grid.innerHTML = ''; grid.appendChild(empty); empty.style.display = ''; return; }

    // Batch all video URLs in one pass before building DOM
    const urlPromises = state.results.map((_, i) =>
        pywebview.api.get_video_url(i).catch(() => null)
    );

    const groups = _groupResultsByStem(state.results);
    const frag = document.createDocumentFragment();

    // If only 1 group, render it open; otherwise start collapsed
    const autoOpen = groups.length === 1;

    groups.forEach(group => {
        const totalMB = group.clips.reduce((sum, c) => sum + (parseFloat(c.size_mb) || 0), 0).toFixed(1);
        const folder = document.createElement('div');
        folder.className = 'result-folder' + (autoOpen ? ' open' : '');
        folder.dataset.stem = group.stem;

        const header = document.createElement('div');
        header.className = 'result-folder-header';
        header.onclick = () => toggleFolder(header);
        header.innerHTML = `
            <span class="folder-toggle">&#9654;</span>
            <span class="folder-name">${escHtml(group.stem)}</span>
            <span class="folder-count">${group.clips.length} clip${group.clips.length > 1 ? 's' : ''}</span>
            <span class="folder-size">${totalMB} MB</span>
            <button class="folder-schedule-all" title="Schedule all clips from this source">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                Schedule All
            </button>`;
        const schedBtn = header.querySelector('.folder-schedule-all');
        const stemName = group.stem;
        schedBtn.addEventListener('click', (e) => { e.stopPropagation(); scheduleFolder(stemName); });
        folder.appendChild(header);

        const body = document.createElement('div');
        body.className = 'result-folder-body';
        group.clips.forEach(c => {
            body.appendChild(_buildResultCard(c, c._idx));
        });
        folder.appendChild(body);
        frag.appendChild(folder);
    });

    grid.innerHTML = '';
    grid.appendChild(frag);

    // Lazy-load thumbnails — only decode when visible, max 2 at a time
    const urls = await Promise.all(urlPromises);
    state.results.forEach((_, i) => {
        const r = urls[i];
        if (r && r.url) {
            const thumbEl = document.querySelector(`.result-card-thumb[data-clip-idx="${i}"]`);
            if (thumbEl) lazyThumb(thumbEl, r.url);
        }
    });
}
async function openFolder() { try { await pywebview.api.open_output_folder(); } catch (_) {} }

/* ── Video Preview ─────────────────────────────────────────────────────── */

async function previewClip(idx) {
    try {
        const r = await pywebview.api.get_video_url(idx);
        if (r.url) {
            state.previewClipIdx = idx;
            const video = document.getElementById('preview-video');
            video.src = r.url;
            document.getElementById('preview-modal-title').textContent = `Clip ${idx + 1}`;
            renderPreviewFeedbackState();
            showModal('preview-modal');
            video.play().catch(() => {});
        } else {
            toast('Video file not found', 'error');
        }
    } catch (e) {
        toast('Preview failed: ' + e, 'error');
    }
}

function closePreview() {
    const video = document.getElementById('preview-video');
    video.pause();
    video.src = '';
    state.previewClipIdx = -1;
    renderPreviewFeedbackState();
    closeModal('preview-modal');
}

function deleteFromPreview() {
    if (state.previewClipIdx >= 0) {
        requestDeleteResult(state.previewClipIdx);
    }
}

/* ── Delete Clips ──────────────────────────────────────────────────────── */

function requestDeleteResult(idx) {
    const clip = state.results[idx];
    if (!clip) return;
    state.pendingDeleteIdx = idx;
    state.pendingDeleteFilename = clip.filename;
    state.pendingDeleteSource = 'results';
    document.getElementById('confirm-delete-msg').textContent = `Delete "${clip.filename}"? This cannot be undone.`;
    showModal('confirm-delete-modal');
}

function requestDeleteLibrary(filename) {
    state.pendingDeleteIdx = -1;
    state.pendingDeleteFilename = filename;
    state.pendingDeleteSource = 'library';
    document.getElementById('confirm-delete-msg').textContent = `Delete "${filename}"? This cannot be undone.`;
    showModal('confirm-delete-modal');
}

async function confirmDelete() {
    closeModal('confirm-delete-modal');

    if (state.pendingDeleteSource === 'results' && state.pendingDeleteIdx >= 0) {
        try {
            const r = await pywebview.api.delete_clip(state.pendingDeleteIdx);
            if (r.ok) {
                toast('Clip deleted', 'success');
                // Close preview if we deleted the previewed clip
                if (state.previewClipIdx === state.pendingDeleteIdx) {
                    closePreview();
                }
                loadResults();
            } else {
                toast(r.error || 'Delete failed', 'error');
            }
        } catch (e) { toast('Delete failed: ' + e, 'error'); }
    } else if (state.pendingDeleteSource === 'library' && state.pendingDeleteFilename) {
        try {
            const r = await pywebview.api.delete_library_file(state.pendingDeleteFilename);
            if (r.ok) {
                toast('Video deleted', 'success');
                loadLibrary();
            } else {
                toast(r.error || 'Delete failed', 'error');
            }
        } catch (e) { toast('Delete failed: ' + e, 'error'); }
    }

    state.pendingDeleteIdx = -1;
    state.pendingDeleteFilename = null;
    state.pendingDeleteSource = null;
}

/* ── Library (All Videos) ──────────────────────────────────────────────── */

async function loadLibrary() {
    try {
        const r = await pywebview.api.list_all_clips();
        state.libraryClips = r.clips || [];

        // Update stats
        document.getElementById('lib-stat-count').textContent = r.count || 0;
        document.getElementById('lib-stat-size').textContent = (r.total_size_mb || 0) + ' MB';
        const libCountEl = document.getElementById('library-count');
        if (libCountEl) libCountEl.textContent = state.libraryClips.length ? state.libraryClips.length + ' video' + (state.libraryClips.length !== 1 ? 's' : '') : '';

        if (state.libraryClips.length > 0) {
            const latest = state.libraryClips[0]; // sorted by newest first
            const d = new Date(latest.modified * 1000);
            document.getElementById('lib-stat-recent').textContent = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
        } else {
            document.getElementById('lib-stat-recent').textContent = '-';
        }

        renderLibraryGrid();
    } catch (e) {
        console.error('Load library error:', e);
    }
}

function refreshLibrary() {
    loadLibrary();
    toast('Library refreshed', 'success');
}

function _groupLibraryByStem(clips) {
    const groups = {};
    clips.forEach((clip, i) => {
        const match = clip.filename.match(/^(.+?)_viral\d+/i);
        const stem = match ? match[1] : 'Other';
        if (!groups[stem]) groups[stem] = { stem, clips: [] };
        groups[stem].clips.push({ ...clip, _libIdx: i });
    });
    return Object.values(groups);
}

function _buildLibraryCard(clip) {
    const item = document.createElement('div');
    item.className = 'library-item';
    const d = new Date(clip.modified * 1000);
    const dateStr = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    item.innerHTML = `
        <div class="library-item-thumb" data-lib-url="${escHtml(clip.url)}">
            <div class="thumb-placeholder">
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            </div>
            <div class="library-item-overlay">
                <button class="play-btn" style="width:40px;height:40px;">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                </button>
            </div>
            <button class="library-item-delete" title="Delete">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            </button>
        </div>
        <div class="library-item-info">
            <div class="library-item-name" title="${escHtml(clip.filename)}">${escHtml(clip.filename)}</div>
            <div class="library-item-meta">
                <span>${clip.size_mb} MB</span>
                <span>${dateStr}</span>
            </div>
        </div>`;
    // Lazy-load thumbnail
    const thumbEl = item.querySelector('.library-item-thumb');
    if (thumbEl) {
        thumbEl.addEventListener('click', () => previewLibraryClip(clip.filename, clip.url));
    }
    const deleteBtn = item.querySelector('.library-item-delete');
    if (deleteBtn) {
        deleteBtn.addEventListener('click', (event) => {
            event.stopPropagation();
            requestDeleteLibrary(clip.filename);
        });
    }
    if (clip.url && thumbEl) lazyThumb(thumbEl, clip.url);
    return item;
}

function renderLibraryGrid() {
    const grid = document.getElementById('library-grid');
    const empty = document.getElementById('library-empty');
    const searchTerm = (document.getElementById('library-search-input')?.value || '').toLowerCase();

    const filtered = searchTerm
        ? state.libraryClips.filter(c => c.filename.toLowerCase().includes(searchTerm))
        : state.libraryClips;

    if (!filtered.length) {
        grid.innerHTML = '';
        grid.appendChild(empty);
        empty.style.display = '';
        return;
    }

    const groups = _groupLibraryByStem(filtered);
    const frag = document.createDocumentFragment();
    const autoOpen = groups.length === 1;

    groups.forEach(group => {
        const totalMB = group.clips.reduce((sum, c) => sum + (parseFloat(c.size_mb) || 0), 0).toFixed(1);
        const folder = document.createElement('div');
        folder.className = 'result-folder' + (autoOpen ? ' open' : '');
        folder.dataset.stem = group.stem;

        const header = document.createElement('div');
        header.className = 'result-folder-header';
        header.onclick = () => toggleFolder(header);
        header.innerHTML = `
            <span class="folder-toggle">&#9654;</span>
            <span class="folder-name">${escHtml(group.stem)}</span>
            <span class="folder-count">${group.clips.length} clip${group.clips.length > 1 ? 's' : ''}</span>
            <span class="folder-size">${totalMB} MB</span>`;
        folder.appendChild(header);

        const body = document.createElement('div');
        body.className = 'result-folder-body library-folder-body';
        group.clips.forEach(c => {
            body.appendChild(_buildLibraryCard(c));
        });
        folder.appendChild(body);
        frag.appendChild(folder);
    });

    grid.innerHTML = '';
    grid.appendChild(frag);
}

const filterLibrary = _debounce(() => {
    renderLibraryGrid();
}, 200);

function setLibraryView(view) {
    state.libraryView = view;
    const grid = document.getElementById('library-grid');
    grid.classList.toggle('list-view', view === 'list');
    document.getElementById('lib-view-grid').classList.toggle('active', view === 'grid');
    document.getElementById('lib-view-list').classList.toggle('active', view === 'list');
}

function previewLibraryClip(filename, url) {
    state.previewClipIdx = -1; // not from results
    renderPreviewFeedbackState();
    const video = document.getElementById('preview-video');
    video.src = url;
    document.getElementById('preview-modal-title').textContent = filename;
    // Hide delete button in preview for library (use library's own delete)
    document.getElementById('preview-delete-btn').style.display = 'none';
    showModal('preview-modal');
    video.play().catch(() => {});
}

/* ── YouTube Connection ───────────────────────────────────────────────── */

async function connectYouTube() {
    const btn = document.getElementById('btn-yt-connect');
    const origHTML = btn.innerHTML;
    btn.textContent = 'Connecting...'; btn.disabled = true;
    try {
        const r = await pywebview.api.connect_youtube();
        if (r.ok) {
            state.ytConnected = true;
            await loadChannelsAndCategories();
            updateYtUI(true);
            if (hasPendingSchedule()) {
                try { await pywebview.api.start_scheduler(); } catch (_) {}
            }
            hideYouTubeSetupCard();
            const name = r.account ? r.account.title : 'YouTube';
            toast(`Connected: ${name}`, 'success');
            addNotification('YouTube Connected', `Account "${name}" linked successfully`, 'success');
        } else {
            showYouTubeSetupCard(r.error || 'Could not connect to YouTube');
            toast(r.error || 'Connection failed', 'error');
            addNotification('Connection Failed', r.error || 'Could not connect to YouTube', 'error');
        }
    } catch (e) {
        showYouTubeSetupCard(String(e));
        toast('Connection failed: ' + e, 'error');
        addNotification('Connection Failed', String(e), 'error');
    }
    btn.innerHTML = origHTML; btn.disabled = false;
}

function showYouTubeSetupCard(message = '') {
    const card = document.getElementById('yt-setup-card');
    const msg = document.getElementById('yt-setup-message');
    if (!card) return;
    if (msg && message) {
        msg.textContent = message.includes('client_secrets.json')
            ? 'Add a Desktop app OAuth JSON named client_secrets.json to the app data folder. Keep the OAuth app in Testing and add yourself as a test user before clicking Add Account.'
            : message;
    }
    card.classList.remove('hidden');
}

function hideYouTubeSetupCard() {
    document.getElementById('yt-setup-card')?.classList.add('hidden');
}

async function disconnectAccount(accountId) {
    try {
        await pywebview.api.disconnect_youtube(accountId);
        // Refresh channels
        await loadChannelsAndCategories();
        const hasAccounts = state.channels.length > 0;
        state.ytConnected = hasAccounts;
        updateYtUI(hasAccounts);
        toast('Account removed', 'success');
    } catch (_) {}
}

function updateYtUI(connected) {
    const statusText = document.getElementById('yt-status-text');
    const channelArea = document.getElementById('yt-channel-area');
    if (connected) {
        const accountCount = new Set(state.channels.map(c => c.account_id)).size;
        statusText.textContent = `${accountCount} account${accountCount !== 1 ? 's' : ''} · ${state.channels.length} channel${state.channels.length !== 1 ? 's' : ''}`;
        statusText.classList.add('connected');
    } else {
        statusText.textContent = 'No accounts connected';
        statusText.classList.remove('connected');
    }
    // Always show Add Account button (can add more accounts)
    channelArea.classList.toggle('hidden', !connected);
}

async function loadChannelsAndCategories() {
    try {
        const [chRes, catRes] = await Promise.all([pywebview.api.get_channels(), pywebview.api.get_categories()]);
        state.channels = chRes.channels || [];
        state.categories = catRes.categories || [];
        if (!state.categories.length) state.categories = [{ id: DEFAULT_CATEGORY_ID, title: 'Gaming' }];
        const list = document.getElementById('yt-channel-list');
        list.innerHTML = '';

        // Group channels by account
        const accountGroups = {};
        state.channels.forEach(ch => {
            const key = ch.account_id || ch.id;
            if (!accountGroups[key]) accountGroups[key] = { title: ch.account_title || ch.title, channels: [] };
            accountGroups[key].channels.push(ch);
        });

        const accountKeys = Object.keys(accountGroups);
        const showAccountHeaders = accountKeys.length > 1;

        accountKeys.forEach(acctId => {
            const group = accountGroups[acctId];

            if (showAccountHeaders) {
                const header = document.createElement('div');
                header.className = 'yt-account-header';
                const name = document.createElement('span');
                name.className = 'yt-account-name';
                name.textContent = group.title;
                const remove = document.createElement('button');
                remove.className = 'yt-account-remove';
                remove.type = 'button';
                remove.title = 'Remove account';
                remove.setAttribute('aria-label', `Remove account ${group.accountTitle || acctId}`);
                remove.textContent = '×';
                remove.addEventListener('click', event => {
                    event.stopPropagation();
                    disconnectAccount(acctId);
                });
                header.append(name, remove);
                list.appendChild(header);
            }

            group.channels.forEach(ch => {
                const isSelected = state.selectedChannel === ch.id || (!state.selectedChannel && state.channels[0]?.id === ch.id);
                const card = document.createElement('div');
                card.className = 'yt-channel-card' + (isSelected ? ' selected' : '');
                card.dataset.channelId = ch.id;
                card.tabIndex = 0;
                card.setAttribute('role', 'button');
                card.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
                card.onclick = () => selectChannel(ch.id);
                card.addEventListener('keydown', event => {
                    if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        selectChannel(ch.id);
                    }
                });
                const img = document.createElement('img');
                img.className = 'yt-channel-thumb';
                img.alt = '';
                img.src = safeMediaUrl(ch.thumbnail);
                const info = document.createElement('div');
                info.className = 'yt-channel-info';
                const title = document.createElement('span');
                title.className = 'yt-channel-title';
                title.textContent = ch.title || 'YouTube channel';
                const subs = document.createElement('span');
                subs.className = 'yt-channel-subs';
                subs.textContent = `${formatNumber(ch.subscribers)} subscribers`;
                info.append(title, subs);
                card.append(img, info);
                if (!showAccountHeaders) {
                    const remove = document.createElement('button');
                    remove.className = 'yt-account-remove yt-channel-remove-inline';
                    remove.type = 'button';
                    remove.title = 'Remove account';
                    remove.setAttribute('aria-label', `Remove account ${ch.account_title || ch.title || ch.id}`);
                    remove.textContent = '×';
                    remove.addEventListener('click', event => {
                        event.stopPropagation();
                        disconnectAccount(ch.account_id || ch.id);
                    });
                    card.appendChild(remove);
                }
                list.appendChild(card);
            });
        });

        if (state.channels.length && !state.selectedChannel) state.selectedChannel = state.channels[0].id;
        updateModalCategoryDropdown();
        _populateScheduleChannelDropdown();
        if (state.scheduled.length) {
            state.scheduled = normalizeScheduledMetadata(state.scheduled);
        }
    } catch (e) { console.error('Load channels/cats error:', e); }
}

function selectChannel(id) {
    state.selectedChannel = id;
    document.querySelectorAll('.yt-channel-card').forEach(c => {
        const selected = c.dataset.channelId === id;
        c.classList.toggle('selected', selected);
        c.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
}

function updateModalCategoryDropdown() {
    const sel = document.getElementById('modal-meta-category');
    if (!sel) return;
    sel.innerHTML = '';
    if (!state.categories.length) state.categories = [{ id: DEFAULT_CATEGORY_ID, title: 'Gaming' }];
    state.categories.forEach(cat => {
        const opt = document.createElement('option');
        opt.value = cat.id; opt.textContent = cat.title;
        if (cat.id === DEFAULT_CATEGORY_ID) opt.selected = true;
        sel.appendChild(opt);
    });
    sel.value = DEFAULT_CATEGORY_ID;
}

/* ── Upload / Calendar Section ────────────────────────────────────────── */

async function loadUploadSection() {
    const empty = document.getElementById('upload-empty');
    const content = document.getElementById('upload-content');

    // Import any clips dropped into the clips/ folder + refresh results
    try {
        const r = await pywebview.api.import_folder_clips();
        state.results = visibleClipList(r.clips || []);
        state.moments = r.moments || [];
    } catch (_) {
        // Fallback: just refresh from backend
        try {
            const r = await pywebview.api.get_results();
            state.results = visibleClipList(r.clips || []);
            state.moments = r.moments || [];
        } catch (_) {}
    }

    await refreshScheduleFromBackend(false);

    if (!state.results.length) {
        state.scheduled = [];
        persistSchedule();
        empty.style.display = '';
        content.classList.add('hidden');
        renderTimeline();
        renderCalendar();
        renderClipTray();
        return;
    }
    empty.style.display = 'none';
    content.classList.remove('hidden');

    // Sync auto-delete toggle
    try {
        const d = await pywebview.api.get_delete_after_upload();
        const cb = document.getElementById('auto-delete-toggle');
        if (cb) cb.checked = !!d.enabled;
    } catch (_) {}

    renderClipTray();
    renderTimeline();
    renderCalendar();
}

/* ── Clip Tray (draggable) ────────────────────────────────────────────── */

function _groupClipsByStem(clips) {
    const groups = {};
    clips.forEach((clip, i) => {
        // Use source_stem from backend (persisted even after rename),
        // check moments as fallback, then try filename pattern
        let stem = clip.source_stem;
        if (!stem) {
            const m = state.moments[i];
            if (m && m.source_stem) stem = m.source_stem;
        }
        if (!stem) {
            const match = clip.filename.match(/^(.+?)_viral\d+/i);
            stem = match ? match[1] : clip.filename.replace(/\.[^.]+$/, '');
        }
        if (!groups[stem]) groups[stem] = { stem, clips: [] };
        groups[stem].clips.push({ ...clip, _idx: i });
    });
    return Object.values(groups);
}

function renderClipTray() {
    const list = document.getElementById('clip-tray-list');
    if (!list) return;
    list.innerHTML = '';

    const groups = _groupClipsByStem(state.results);

    if (!groups.length) return;

    // Always show folders — even with 1 group, the folder gives
    // a "Schedule All" button and keeps the UI consistent
    groups.forEach((group, gi) => {
        const folder = document.createElement('div');
        // First folder starts open, rest collapsed
        folder.className = 'tray-folder' + (gi === 0 ? ' open' : '');

        const totalMB = group.clips.reduce((sum, c) => sum + parseFloat(c.size_mb || 0), 0).toFixed(1);
        const scheduledCount = group.clips.filter(c =>
            state.scheduled.some(s => s.clipIdx === c._idx && !s.uploaded)
        ).length;

        const header = document.createElement('div');
        header.className = 'tray-folder-header';
        // Build channel options for per-folder dropdown
        const chOptions = state.channels.map(ch =>
            `<option value="${ch.id}"${ch.id === (state.selectedChannel || '') ? ' selected' : ''}>${escHtml(ch.title)}</option>`
        ).join('');
        const chDropdownHtml = state.channels.length
            ? `<select class="tray-folder-channel" title="Target channel for this folder" onclick="event.stopPropagation()">${chOptions}</select>`
            : '';

        header.innerHTML = `
            <span class="tray-folder-toggle">&#9654;</span>
            <span class="tray-folder-name" title="${escHtml(group.stem)}">${escHtml(group.stem)}</span>
            <span class="tray-folder-count">${group.clips.length} clips</span>
            ${scheduledCount ? `<span class="tray-folder-scheduled">${scheduledCount} scheduled</span>` : ''}
            <div class="tray-folder-actions" onclick="event.stopPropagation()">
                ${chDropdownHtml}
                <button class="tray-folder-sched-btn" title="Schedule all clips from this folder to selected channel">Schedule</button>
                <button class="tray-folder-ai-btn" title="Generate AI titles only for clips in this folder">AI Titles</button>
            </div>`;
        const folderStem = group.stem; // capture in closure — no encode/decode needed
        header.addEventListener('click', (e) => {
            if (e.target.closest('.tray-folder-actions')) return;
            folder.classList.toggle('open');
        });
        const schedBtn = header.querySelector('.tray-folder-sched-btn');
        if (schedBtn) {
            schedBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                const chSelect = header.querySelector('.tray-folder-channel');
                const channelId = chSelect ? chSelect.value : null;
                scheduleFolderWithChannel(folderStem, channelId);
            });
        }
        const aiBtn = header.querySelector('.tray-folder-ai-btn');
        if (aiBtn) {
            aiBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                generateAITitlesForFolder(folderStem, aiBtn);
            });
        }

        const body = document.createElement('div');
        body.className = 'tray-folder-body';
        group.clips.forEach(clip => {
            body.appendChild(_createTrayClipEl(clip, clip._idx));
        });

        folder.appendChild(header);
        folder.appendChild(body);
        list.appendChild(folder);
    });
}

function _createTrayClipEl(clip, idx) {
    const el = document.createElement('div');
    el.className = 'tray-clip';
    el.draggable = true;
    el.dataset.clipIdx = idx;
    const isScheduled = state.scheduled.some(s => s.clipIdx === idx && !s.uploaded);
    if (isScheduled) el.classList.add('scheduled');
    el.innerHTML = `<span class="tray-clip-num">C${idx+1}</span><span class="tray-clip-name">${escHtml(clip.filename)}</span><span class="tray-clip-size">${escHtml(clip.size_mb)} MB</span>`;
    el.addEventListener('dragstart', e => {
        e.dataTransfer.setData('text/plain', String(idx));
        e.dataTransfer.effectAllowed = 'copy';
        el.classList.add('dragging');
    });
    el.addEventListener('dragend', () => el.classList.remove('dragging'));
    return el;
}

/* ── Smart Presets ────────────────────────────────────────────────────── */

function setSmartPreset(preset) {
    document.querySelectorAll('.smart-preset').forEach(b => b.classList.toggle('active', b.dataset.preset === preset));
    state._schedPreset = preset;
    const customEl = document.getElementById('smart-custom-interval');
    if (customEl) customEl.classList.toggle('hidden', preset !== 'custom');
    _renderPeakTimesLegend();
}

function _renderPeakTimesLegend() {
    const container = document.getElementById('peak-times-slots');
    if (!container) return;
    const count = _getClipsPerDay();
    const slots = _getPeakTimesForDay(count);
    const tiers = ['gold', 'gold', 'silver', 'silver', 'bronze', 'bronze', 'bronze', 'bronze', 'bronze', 'bronze'];
    container.innerHTML = slots.map((t, i) => {
        const [h, m] = t.split(':');
        const hr = parseInt(h);
        const ampm = hr >= 12 ? 'PM' : 'AM';
        const h12 = hr > 12 ? hr - 12 : hr === 0 ? 12 : hr;
        return `<span class="peak-slot ${tiers[i] || 'bronze'}">${h12}:${m} ${ampm}</span>`;
    }).join('');
}

/**
 * Proven YouTube peak upload times (best engagement windows).
 * Ranked by priority — first slots get highest views on average.
 * Source: aggregate creator analytics data (US/EU audiences).
 */
const PEAK_TIMES = [
    '09:00',  // Morning commute / coffee scroll
    '12:00',  // Lunch break
    '15:00',  // Afternoon engagement peak
    '17:00',  // After work / school
    '19:00',  // Evening prime time
    '20:30',  // Late evening second wave
    '07:00',  // Early risers
    '22:00',  // Night owls
    '10:30',  // Mid-morning
    '14:00',  // Early afternoon
];

function _getClipsPerDay() {
    const preset = state._schedPreset || 'allpeaks';
    switch (preset) {
        case 'allpeaks': return PEAK_TIMES.length;
        case '1perday': return 1;
        case '2perday': return 2;
        case '3perday': return 3;
        case '5perday': return 5;
        case 'custom': return parseInt(document.getElementById('smart-sched-custom-perday')?.value) || 1;
        default: return 1;
    }
}

function _getPeakTimesForDay(count) {
    // Return the top N peak times for a day, sorted chronologically
    const slots = PEAK_TIMES.slice(0, Math.min(count, PEAK_TIMES.length));
    return slots.sort();
}

function _availableScheduleSlotsForDate(dateStr, count, now = new Date()) {
    const usedTimes = new Set(state.scheduled
        .filter(s => s.date === dateStr && !s.uploaded)
        .map(s => s.time));
    return _getPeakTimesForDay(count)
        .filter(t => !usedTimes.has(t))
        .filter(t => _isFutureScheduleSlot(dateStr, t, now));
}

function _nextPeakTimeForDate(dateStr) {
    // Find the next available peak time for a given date (not already taken)
    const available = _availableScheduleSlotsForDate(dateStr, PEAK_TIMES.length);
    if (available.length) return available[0];
    if (_isTodayDateStr(dateStr)) {
        const future = new Date(Date.now() + SCHEDULE_BUFFER_MINUTES * 60 * 1000);
        return `${String(future.getHours()).padStart(2, '0')}:${String(future.getMinutes()).padStart(2, '0')}`;
    }
    // All peak slots taken — use next hour after last used
    const usedTimes = state.scheduled
        .filter(s => s.date === dateStr && !s.uploaded)
        .map(s => s.time);
    if (usedTimes.length) {
        const last = usedTimes.sort().pop();
        const [h, m] = last.split(':').map(Number);
        const nextH = Math.min(h + 1, 23);
        return `${String(nextH).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
    }
    return '12:00';
}

function _resolveSchedulableDateTime(dateStr, requestedTime = null) {
    let cursor = new Date(`${dateStr}T12:00:00`);
    if (Number.isNaN(cursor.getTime())) cursor = new Date();
    for (let guard = 0; guard < 370; guard++) {
        const currentDateStr = _toDateStr(cursor);
        const usedTimes = new Set(state.scheduled
            .filter(s => s.date === currentDateStr && !s.uploaded)
            .map(s => s.time));
        if (requestedTime && !usedTimes.has(requestedTime) && _isFutureScheduleSlot(currentDateStr, requestedTime)) {
            return { date: currentDateStr, time: requestedTime };
        }
        const available = _availableScheduleSlotsForDate(currentDateStr, PEAK_TIMES.length);
        if (available.length) return { date: currentDateStr, time: available[0] };
        requestedTime = null;
        cursor.setDate(cursor.getDate() + 1);
    }
    return { date: _toDateStr(new Date()), time: '12:00' };
}

/* ── Calendar ─────────────────────────────────────────────────────────── */

function calNavMonth(delta) {
    state.calMonth += delta;
    if (state.calMonth > 11) { state.calMonth = 0; state.calYear++; }
    if (state.calMonth < 0) { state.calMonth = 11; state.calYear--; }
    renderCalendar();
}

function calGoToday() {
    const now = new Date();
    state.calYear = now.getFullYear();
    state.calMonth = now.getMonth();
    renderCalendar();
}

function renderCalendar() {
    const months = ['January','February','March','April','May','June','July','August','September','October','November','December'];
    document.getElementById('cal-month-label').textContent = `${months[state.calMonth]} ${state.calYear}`;

    // Update channel filter tabs
    _renderCalChannelTabs();

    const container = document.getElementById('cal-days');
    const firstDay = new Date(state.calYear, state.calMonth, 1).getDay();
    const daysInMonth = new Date(state.calYear, state.calMonth + 1, 0).getDate();
    const today = new Date();
    const todayStr = _toDateStr(today);
    const filter = state.calChannelFilter;

    // Pre-index scheduled items by date, applying channel filter
    const schedByDate = {};
    state.scheduled.forEach((s, idx) => {
        if (filter !== 'all' && s.channel_id && s.channel_id !== filter) return;
        if (!schedByDate[s.date]) schedByDate[s.date] = [];
        schedByDate[s.date].push({ ...s, _origIdx: idx });
    });

    const frag = document.createDocumentFragment();
    const MAX_CHIPS = 3; // Collapse if more than this

    for (let i = 0; i < firstDay; i++) {
        const blank = document.createElement('div');
        blank.className = 'cal-day blank';
        frag.appendChild(blank);
    }

    for (let d = 1; d <= daysInMonth; d++) {
        const cell = document.createElement('div');
        const dateStr = `${state.calYear}-${String(state.calMonth + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        const isPast = dateStr < todayStr;
        cell.className = 'cal-day' + (dateStr === todayStr ? ' today' : '') + (isPast ? ' past' : '');
        cell.dataset.date = dateStr;

        const num = document.createElement('span');
        num.className = 'cal-day-num';
        num.textContent = d;
        cell.appendChild(num);

        // Render chips — collapse when many clips on same day
        const dayItems = schedByDate[dateStr];
        if (dayItems) {
            const showAll = dayItems.length <= MAX_CHIPS;
            const visible = showAll ? dayItems : dayItems.slice(0, 2);

            visible.forEach(s => {
                const chip = document.createElement('div');
                const isMissed = isScheduleMissed(s);
                chip.className = 'cal-chip' + (s.uploaded ? ' uploaded' : isMissed ? ' missed' : '');
                chip.innerHTML = `<span>C${s.clipIdx + 1}</span><span class="cal-chip-time">${s.time || ''}</span>`;
                chip.title = `${s.title || 'Clip ' + (s.clipIdx + 1)} — ${s.time}${s.uploaded ? ' (uploaded)' : isMissed ? ' (missed)' : ''}`;
                chip.onclick = (e) => { e.stopPropagation(); openMetaModal(s._origIdx); };
                cell.appendChild(chip);
            });

            if (!showAll) {
                const more = document.createElement('div');
                more.className = 'cal-day-count';
                more.textContent = `+${dayItems.length - 2} more`;
                more.title = dayItems.map(s => s.title || `Clip ${s.clipIdx + 1}`).join(', ');
                more.onclick = (e) => { e.stopPropagation(); openDayDetailView(dateStr, dayItems); };
                cell.appendChild(more);
            }
        }

        cell.addEventListener('click', () => {
            const items = schedByDate[dateStr];
            if (items && items.length > 0) {
                openDayDetailView(dateStr, items);
            } else {
                openClipPicker(dateStr);
            }
        });
        cell.addEventListener('dragover', e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; cell.classList.add('drag-over'); });
        cell.addEventListener('dragleave', () => cell.classList.remove('drag-over'));
        cell.addEventListener('drop', e => {
            e.preventDefault();
            cell.classList.remove('drag-over');
            const clipIdx = parseInt(e.dataTransfer.getData('text/plain'));
            if (isNaN(clipIdx)) return;
            dropClipOnDate(clipIdx, dateStr);
        });

        frag.appendChild(cell);
    }

    container.innerHTML = '';
    container.appendChild(frag);

    _checkMissedUploads();
}

function _renderCalChannelTabs() {
    const tabs = document.getElementById('cal-channel-tabs');
    // Collect unique channels from scheduled items
    const channelIds = new Set();
    state.scheduled.forEach(s => { if (s.channel_id) channelIds.add(s.channel_id); });

    if (channelIds.size < 2 && state.channels.length < 2) {
        tabs.classList.add('hidden');
        return;
    }
    tabs.classList.remove('hidden');
    tabs.innerHTML = '';

    // "All" tab
    const allTab = document.createElement('button');
    allTab.className = 'cal-ch-tab' + (state.calChannelFilter === 'all' ? ' active' : '');
    allTab.dataset.channel = 'all';
    allTab.textContent = 'All Channels';
    allTab.onclick = () => filterCalendarByChannel('all');
    tabs.appendChild(allTab);

    // Per-channel tabs
    const chMap = {};
    state.channels.forEach(c => { chMap[c.id] = c; });
    // Include channels from scheduled items even if not in state.channels
    channelIds.forEach(id => { if (!chMap[id]) chMap[id] = { id, title: id, thumbnail: '' }; });

    state.channels.forEach(ch => {
        const tab = document.createElement('button');
        tab.className = 'cal-ch-tab' + (state.calChannelFilter === ch.id ? ' active' : '');
        tab.dataset.channel = ch.id;
        if (ch.thumbnail) {
            const img = document.createElement('img');
            img.className = 'cal-ch-thumb';
            img.src = safeMediaUrl(ch.thumbnail);
            img.alt = '';
            tab.appendChild(img);
        }
        tab.appendChild(document.createTextNode(ch.title || ch.id));
        tab.onclick = () => filterCalendarByChannel(ch.id);
        tabs.appendChild(tab);
    });
}

function filterCalendarByChannel(channelId) {
    state.calChannelFilter = channelId;
    renderCalendar();
    renderTimeline();
}

function openDayDetailView(dateStr, dayItems) {
    state.pickerDate = dateStr;
    const d = new Date(dateStr + 'T12:00:00');
    const fmtDate = d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
    document.getElementById('day-detail-title').textContent = `${fmtDate} — ${dayItems.length} clip${dayItems.length > 1 ? 's' : ''}`;

    const list = document.getElementById('day-detail-list');
    list.innerHTML = '';
    const sorted = [...dayItems].sort((a, b) => (a.time || '').localeCompare(b.time || ''));

    // Add status summary line
    let sPending = 0, sUploaded = 0, sMissed = 0;
    sorted.forEach(s => {
        if (s.uploaded) sUploaded++;
        else if (isScheduleMissed(s)) sMissed++;
        else sPending++;
    });
    const summaryParts = [];
    if (sPending > 0) summaryParts.push(`<span class="summary-pending">${sPending} pending</span>`);
    if (sUploaded > 0) summaryParts.push(`<span class="summary-uploaded">${sUploaded} uploaded</span>`);
    if (sMissed > 0) summaryParts.push(`<span class="summary-missed">${sMissed} missed</span>`);
    const existingSummary = document.getElementById('day-detail-summary');
    if (existingSummary) existingSummary.remove();
    if (summaryParts.length) {
        const summaryEl = document.createElement('div');
        summaryEl.id = 'day-detail-summary';
        summaryEl.className = 'day-detail-summary';
        summaryEl.innerHTML = summaryParts.join('<span style="color:var(--text-3)">·</span>');
        list.parentNode.insertBefore(summaryEl, list);
    }

    sorted.forEach(s => {
        const isMissed = isScheduleMissed(s);
        const statusClass = s.uploaded ? 'uploaded' : isMissed ? 'missed' : 'pending';
        const statusLabel = s.uploaded ? 'Uploaded' : isMissed ? 'Missed' : 'Pending';

        const item = document.createElement('div');
        item.className = `day-detail-item ${statusClass}`;
        item.innerHTML = `
            <div class="day-detail-thumb" data-detail-clip="${s.clipIdx}">
                <div class="thumb-placeholder">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                </div>
            </div>
            <div class="day-detail-info">
                <div class="day-detail-item-title">${escHtml(s.title || 'Untitled')}</div>
                <div class="day-detail-meta">
                    <span class="day-detail-time">${s.time || '—'}</span>
                    <span class="day-detail-status ${statusClass}">${statusLabel}</span>
                    <span class="day-detail-privacy">${s.privacy || 'public'}</span>
                </div>
            </div>
            <div class="day-detail-actions-row">
                <button class="btn-sm btn-secondary" onclick="event.stopPropagation(); closeModal('day-detail-modal'); openMetaModal(${s._origIdx})" title="Edit">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                </button>
                <button class="btn-sm btn-danger-subtle" onclick="event.stopPropagation(); removeDayDetailItem(${s._origIdx})" title="Remove">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
            </div>`;
        item.onclick = () => { closeModal('day-detail-modal'); openMetaModal(s._origIdx); };
        list.appendChild(item);

        // Lazy-load thumbnail
        const thumbEl = item.querySelector('.day-detail-thumb');
        if (thumbEl) {
            pywebview.api.get_video_url(s.clipIdx).then(r => {
                if (r && r.url) lazyThumb(thumbEl, r.url);
            }).catch(() => {});
        }
    });

    showModal('day-detail-modal');
}

function removeDayDetailItem(idx) {
    state.scheduled.splice(idx, 1);
    persistSchedule();
    renderTimeline();
    renderCalendar();
    closeModal('day-detail-modal');
    toast('Clip removed from schedule', 'success');
}

function closeDayDetailAndAddClip() {
    const dateStr = state.pickerDate;
    closeModal('day-detail-modal');
    if (dateStr) openClipPicker(dateStr);
}

function _checkMissedUploads() {
    const missed = state.scheduled.filter(s => isScheduleMissed(s));
    const banner = document.getElementById('missed-uploads-banner');
    if (missed.length > 0) {
        document.getElementById('missed-uploads-text').textContent =
            `${missed.length} scheduled upload${missed.length > 1 ? 's were' : ' was'} missed (app was offline)`;
        banner.classList.remove('hidden');
    } else {
        banner.classList.add('hidden');
    }
}

/* ── Auto-Schedule ────────────────────────────────────────────────────── */

function _getScheduleChannelId() {
    const sel = document.getElementById('smart-sched-channel');
    const val = sel ? sel.value : '';
    if (val) return val;
    // Fallback to currently selected channel
    return state.selectedChannel || null;
}

function _populateScheduleChannelDropdown() {
    const sel = document.getElementById('smart-sched-channel');
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = '';
    if (!state.channels.length) {
        sel.innerHTML = '<option value="">No channels connected</option>';
        return;
    }
    state.channels.forEach(ch => {
        const opt = document.createElement('option');
        opt.value = ch.id;
        opt.textContent = ch.title;
        sel.appendChild(opt);
    });
    // Restore previous selection or default to selectedChannel
    if (current && [...sel.options].some(o => o.value === current)) {
        sel.value = current;
    } else if (state.selectedChannel) {
        sel.value = state.selectedChannel;
    }
}

function autoScheduleClips() {
    if (!state.results.length) return toast('No clips available. Generate clips first.', 'warning');
    const channelId = _getScheduleChannelId();
    if (!channelId) return toast('Please select a channel to schedule to', 'warning');
    const indices = state.results.map((_, i) => i);
    _scheduleClipIndices(indices, { clearExisting: true, channelId });
}

function _findClipIndicesForStem(stem) {
    const indices = [];
    state.results.forEach((clip, i) => {
        // Check source_stem first (survives renames), then moments, then filename
        let clipStem = clip.source_stem;
        if (!clipStem) {
            const m = state.moments[i];
            if (m && m.source_stem) clipStem = m.source_stem;
        }
        if (!clipStem) {
            const match = clip.filename.match(/^(.+?)_viral\d+/i);
            clipStem = match ? match[1] : clip.filename.replace(/\.[^.]+$/, '');
        }
        if (clipStem === stem) indices.push(i);
    });
    return indices;
}

function scheduleFolder(stem) {
    const channelId = _getScheduleChannelId();
    if (!channelId) return toast('Please select a channel to schedule to', 'warning');
    const indices = _findClipIndicesForStem(stem);
    if (!indices.length) return toast('No clips found in this folder', 'warning');
    _scheduleClipIndices(indices, { clearExisting: false, channelId });
}

function scheduleFolderWithChannel(stem, channelId) {
    // Use provided channelId (from per-folder dropdown), or auto-detect
    if (!channelId) {
        if (state.channels.length === 1) {
            channelId = state.channels[0].id;
        } else {
            channelId = _getScheduleChannelId();
        }
    }
    if (!channelId) return toast('Please select a channel to schedule to', 'warning');
    const indices = _findClipIndicesForStem(stem);
    if (!indices.length) return toast('No clips found in this folder', 'warning');
    _scheduleClipIndices(indices, { clearExisting: false, channelId });
}

function _scheduleClipIndices(clipIndices, opts = {}) {
    const { clearExisting = true, channelId = null } = opts;

    const resolvedChannel = channelId || _getScheduleChannelId();
    const perDay = _getClipsPerDay();
    const privacy = document.getElementById('smart-sched-privacy').value || 'public';
    const startFrom = document.getElementById('smart-sched-start').value || 'tomorrow';
    const peakSlots = _getPeakTimesForDay(perDay);

    // Start date — if appending, find the next available day
    const startDate = new Date();
    if (startFrom === 'tomorrow') {
        startDate.setDate(startDate.getDate() + 1);
    }

    if (clearExisting) {
        // Remove any non-uploaded scheduled items (replace with new schedule)
        state.scheduled = state.scheduled.filter(s => s.uploaded);
    } else {
        // When appending (e.g. folder schedule), find the next free slot after existing scheduled items
        const existingDates = state.scheduled.filter(s => !s.uploaded).map(s => s.date).sort();
        if (existingDates.length) {
            const lastDate = existingDates[existingDates.length - 1];
            const usedOnLast = state.scheduled.filter(s => s.date === lastDate && !s.uploaded).length;
            if (usedOnLast >= perDay) {
                // Last day is full, start on the next day
                const d = new Date(lastDate + 'T12:00:00');
                d.setDate(d.getDate() + 1);
                startDate.setTime(d.getTime());
            } else {
                // Continue filling the last day
                startDate.setTime(new Date(lastDate + 'T12:00:00').getTime());
            }
        }
    }

    // Distribute clips across future peak time slots. If Today has no future slots left,
    // automatically roll into tomorrow instead of creating missed uploads.
    const cursorDate = new Date(startDate);
    const scheduledDates = new Set();
    let firstScheduledDate = null;

    clipIndices.forEach(i => {
        const clip = state.results[i];
        if (!clip) return;

        let dateStr = _toDateStr(cursorDate);
        let availableSlots = _availableScheduleSlotsForDate(dateStr, perDay);
        while (!availableSlots.length) {
            cursorDate.setDate(cursorDate.getDate() + 1);
            dateStr = _toDateStr(cursorDate);
            availableSlots = _availableScheduleSlotsForDate(dateStr, perDay);
        }
        const time = availableSlots[0];
        if (!firstScheduledDate) firstScheduledDate = new Date(cursorDate);
        scheduledDates.add(dateStr);

        const title = clip.filename.replace(/\.mp4$/i, '');
        state.scheduled.push({
            ...clipIdentityFields(clip, i),
            ...descriptionFieldsForClip(clip, i, title),
            ...channelIdentityFields(resolvedChannel),
            date: dateStr,
            time: time,
            title,
            tags: DEFAULT_UPLOAD_TAGS,
            category_id: DEFAULT_CATEGORY_ID,
            privacy: privacy,
            uploaded: false,
        });
    });

    persistSchedule();

    // Navigate calendar to the first scheduled date
    const navDate = firstScheduledDate || startDate;
    state.calYear = navDate.getFullYear();
    state.calMonth = navDate.getMonth();

    renderTimeline();
    renderCalendar();
    renderClipTray();

    const totalDays = Math.max(1, scheduledDates.size);
    const timesStr = peakSlots.map(t => {
        const [h, m] = t.split(':');
        const hr = parseInt(h);
        return `${hr > 12 ? hr - 12 : hr}:${m} ${hr >= 12 ? 'PM' : 'AM'}`;
    }).join(', ');

    toast(`Scheduled ${clipIndices.length} clips across ${totalDays} day${totalDays > 1 ? 's' : ''} at peak times (${timesStr})`, 'success');
    addNotification(
        'Schedule Created',
        `${clipIndices.length} clips scheduled across ${totalDays} day${totalDays > 1 ? 's' : ''} at peak upload times`,
        'info'
    );

    // Titles are generated manually via "Generate AI Titles" button
}

async function generateAITitles() {
    try {
        toast('Generating AI titles...', 'info');
        const r = await pywebview.api.generate_titles();
        if (r.error || !r.titles || !r.titles.length) {
            if (r.error) toast(r.error, 'warning');
            return;
        }
        let updated = 0;
        const metaByIndex = {};
        (r.metadata || []).forEach(m => { metaByIndex[m.index] = m; });
        state.scheduled.forEach(s => {
            if (s.uploaded) return;
            const idx = s.clipIdx;
            if (idx >= 0 && idx < r.titles.length && r.titles[idx]) {
                s.title = r.titles[idx];
                if (metaByIndex[idx]) {
                    applyGeneratedMetadataToSchedule(s, metaByIndex[idx]);
                }
                updated++;
            }
        });
        if (updated) {
            persistSchedule();
            renderTimeline();
            renderCalendar();
            if (r.llm) {
                toast(`AI generated ${updated} title${updated > 1 ? 's' : ''}`, 'success');
            } else {
                toast(`Generated ${updated} title${updated > 1 ? 's' : ''} (install Ollama for better AI titles)`, 'warning');
            }
        }
    } catch (e) {
        console.error('AI title generation error:', e);
    }
}

// Title generation progress callback from backend (runs in background thread)
window.onTitleProgress = function (done, total, title) {
    const btn = document.getElementById('btn-gen-ai-titles');
    if (btn) btn.textContent = `Generating... ${done}/${total}`;
};

// Title generation completion callback from backend
window.onTitlesDone = function (r) {
    const btn = document.getElementById('btn-gen-ai-titles');
    if (btn) { btn.disabled = false; btn.textContent = 'Generate AI Titles'; }

    if (r.error) {
        toast(r.error, 'warning');
        return;
    }

    // Update scheduled items with new titles and filenames
    let schedUpdated = 0;
    if (r.titles) {
        r.titles.forEach(t => {
            if (!t.title) return;
            state.scheduled.forEach(s => {
                if (s.clipIdx === t.index && !s.uploaded) {
                    s.title = t.title;
                    applyGeneratedMetadataToSchedule(s, t);
                    schedUpdated++;
                }
            });
            if (t.filename && t.index < state.results.length) {
                state.results[t.index].filename = t.filename;
            }
        });
    }

    if (schedUpdated) {
        persistSchedule();
        renderTimeline();
        renderCalendar();
    }

    // Refresh results from backend to get updated filenames + source_stems
    pywebview.api.get_results().then(fresh => {
        if (fresh.clips && fresh.clips.length) {
            state.results = visibleClipList(fresh.clips);
            state.moments = fresh.moments || state.moments;
        }
        renderClipTray();
    }).catch(() => renderClipTray());

    const msg = r.llm
        ? `AI generated ${r.renamed} title${r.renamed !== 1 ? 's' : ''} and renamed files`
        : `Generated ${r.renamed} title${r.renamed !== 1 ? 's' : ''} (install Ollama for better titles)`;
    toast(msg, r.renamed ? 'success' : 'warning');
};

async function generateAITitlesManual() {
    const btn = document.getElementById('btn-gen-ai-titles');
    if (btn) { btn.disabled = true; btn.textContent = 'Generating... 0/?'; }

    try {
        toast('Transcribing clips & generating AI titles...', 'info');
        await pywebview.api.generate_and_rename_all();
        // Results come via window.onTitlesDone callback
    } catch (e) {
        console.error('AI title gen error:', e);
        toast('Title generation failed — check console', 'error');
        if (btn) { btn.disabled = false; btn.textContent = 'Generate AI Titles'; }
    }
}

async function generateAITitlesForFolder(stem, btn) {
    const indices = _findClipIndicesForStem(stem);
    if (!indices.length) return toast('No clips found in this folder', 'warning');

    if (btn) { btn.disabled = true; btn.textContent = '...'; }
    toast(`Generating AI titles for "${stem}" (${indices.length} clips)...`, 'info');

    // Set up a folder-specific completion callback
    const origCallback = window.onTitlesDone;
    window.onTitlesDone = function (r) {
        // Restore original callback
        window.onTitlesDone = origCallback;
        if (btn) { btn.disabled = false; btn.textContent = 'AI Titles'; }

        if (r.error) {
            toast(r.error, 'warning');
            return;
        }

        let schedUpdated = 0;
        if (r.titles) {
            r.titles.forEach(t => {
                if (!t.title) return;
                state.scheduled.forEach(s => {
                    if (s.clipIdx === t.index && !s.uploaded) {
                        s.title = t.title;
                        applyGeneratedMetadataToSchedule(s, t);
                        schedUpdated++;
                    }
                });
                if (t.filename && t.index < state.results.length) {
                    state.results[t.index].filename = t.filename;
                }
            });
        }

        if (schedUpdated) {
            persistSchedule();
            renderTimeline();
            renderCalendar();
        }

        pywebview.api.get_results().then(fresh => {
            if (fresh.clips && fresh.clips.length) {
                state.results = visibleClipList(fresh.clips);
                state.moments = fresh.moments || state.moments;
            }
            renderClipTray();
        }).catch(() => renderClipTray());

        const count = r.renamed || 0;
        const msg = r.llm
            ? `AI generated ${count} title${count !== 1 ? 's' : ''} for "${stem}"`
            : `Generated ${count} title${count !== 1 ? 's' : ''} for "${stem}" (install Ollama for better titles)`;
        toast(msg, count ? 'success' : 'warning');
    };

    try {
        await pywebview.api.generate_and_rename_indices(indices);
    } catch (e) {
        console.error('AI title gen error:', e);
        toast('Title generation failed — check console', 'error');
        window.onTitlesDone = origCallback;
        if (btn) { btn.disabled = false; btn.textContent = 'AI Titles'; }
    }
}

async function regenerateTitle(schedIdx) {
    const s = state.scheduled[schedIdx];
    if (!s) return;
    try {
        const r = await pywebview.api.generate_title_for_clip(s.clipIdx);
        if (r.title) {
            s.title = r.title;
            applyGeneratedMetadataToSchedule(s, r);
            persistSchedule();
            renderTimeline();
            renderCalendar();
            // Update meta modal if open
            const titleInput = document.getElementById('modal-meta-title');
            if (titleInput) titleInput.value = r.title;
            const descInput = document.getElementById('modal-meta-desc');
            if (descInput) descInput.value = s.description_custom_text || '';
            updateMetaDescriptionPreview();
            const tagsInput = document.getElementById('modal-meta-tags');
            if (tagsInput && r.tags) tagsInput.value = r.tags;
            toast('Title regenerated', 'success');
        } else {
            toast(r.error || 'No transcript available', 'warning');
        }
    } catch (_) { toast('Title generation failed', 'error'); }
}

function _toDateStr(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function _fmtDateShort(d) {
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function _fmtDateFull(dateStr, timeStr) {
    const d = new Date(dateStr + 'T' + timeStr);
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
}

/* ── Schedule Timeline ────────────────────────────────────────────────── */

function renderTimeline() {
    const panel = document.getElementById('schedule-timeline');
    const list = document.getElementById('timeline-list');

    if (!state.scheduled.length) { panel.classList.add('hidden'); return; }
    panel.classList.remove('hidden');

    const now = new Date();

    // Single-pass: count + build sorted array, with channel filter
    const filter = state.calChannelFilter;
    let uploadedCount = 0, missedCount = 0;
    const sorted = state.scheduled.map((s, i) => {
        if (s.uploaded) uploadedCount++;
        else if (isScheduleMissed(s, now)) missedCount++;
        return { ...s, _idx: i };
    }).filter(s => filter === 'all' || !s.channel_id || s.channel_id === filter)
      .sort((a, b) => (`${a.date}T${a.time}` > `${b.date}T${b.time}` ? 1 : -1));

    const pendingCount = state.scheduled.length - uploadedCount - missedCount;

    const summaryEl = document.getElementById('smart-sched-summary');
    if (summaryEl) {
        const parts = [];
        if (pendingCount > 0) parts.push(`${pendingCount} pending`);
        if (uploadedCount > 0) parts.push(`${uploadedCount} done`);
        if (missedCount > 0) parts.push(`${missedCount} missed`);
        summaryEl.textContent = parts.join(' · ');
    }

    const frag = document.createDocumentFragment();
    sorted.forEach(s => {
        const isMissed = isScheduleMissed(s, now);
        const statusClass = s.uploaded ? 'uploaded' : isMissed ? 'missed' : 'pending';
        const statusLabel = s.uploaded ? 'Uploaded' : isMissed ? 'Missed' : s.privacy;
        const dateFmt = _fmtDateFull(s.date, s.time);

        const item = document.createElement('div');
        item.className = `timeline-item ${statusClass}`;
        item.onclick = () => openMetaModal(s._idx);
        const chName = s.channel_id ? (state.channels.find(c => c.id === s.channel_id)?.title || '') : '';
        item.innerHTML = `
            <span class="timeline-dot"></span>
            <span class="timeline-clip-num">Clip ${s.clipIdx + 1}</span>
            <div class="timeline-info">
                <span class="timeline-title">${escHtml(s.title)}</span>
                <div class="timeline-date">
                    <span class="timeline-date-val">${dateFmt}</span>
                    <span class="timeline-time-val">${s.time}</span>
                    ${chName ? `<span class="timeline-ch-name">${escHtml(chName)}</span>` : ''}
                </div>
            </div>
            <span class="timeline-status ${statusClass}">${statusLabel}</span>
            <button class="timeline-edit" onclick="event.stopPropagation(); removeScheduleAt(${s._idx})" title="Remove">&times;</button>`;
        frag.appendChild(item);
    });

    list.innerHTML = '';
    list.appendChild(frag);

    document.getElementById('scheduler-bar').classList.toggle('hidden', pendingCount === 0 && missedCount === 0);
    _checkMissedUploads();
}

function removeScheduleAt(idx) {
    state.scheduled.splice(idx, 1);
    persistSchedule();
    renderTimeline();
    renderCalendar();
    renderClipTray();
}

function clearSchedule() {
    const pending = state.scheduled.filter(s => !s.uploaded);
    if (!pending.length) return toast('No pending uploads to clear', 'warning');
    state.scheduled = state.scheduled.filter(s => s.uploaded);
    persistSchedule();
    renderTimeline();
    renderCalendar();
    toast('Schedule cleared', 'success');
}



/* ── Missed upload actions ────────────────────────────────────────────── */

function rescheduleOverdue() {
    const now = new Date();
    const perDay = _getClipsPerDay();
    const peakSlots = _getPeakTimesForDay(perDay);

    let nextDate = new Date();
    nextDate.setDate(nextDate.getDate() + 1);

    let rescheduled = 0;
    let slotIdx = 0;

    state.scheduled.forEach(s => {
        if (isScheduleMissed(s, now)) {
            s.date = _toDateStr(nextDate);
            s.time = peakSlots[slotIdx];
            delete s.scheduler_status;
            delete s.missed_at;
            rescheduled++;
            slotIdx++;
            if (slotIdx >= peakSlots.length) {
                slotIdx = 0;
                nextDate.setDate(nextDate.getDate() + 1);
            }
        }
    });

    persistSchedule();
    renderTimeline();
    renderCalendar();
    toast(`Rescheduled ${rescheduled} missed upload${rescheduled > 1 ? 's' : ''} at peak times`, 'success');
}

function uploadOverdueNow() {
    if (!state.ytConnected) {
        return toast('Connect a YouTube account before uploading missed clips', 'warning');
    }
    const now = new Date();
    const todayStr = _toDateStr(now);
    const nowTime = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;

    let count = 0;
    state.scheduled.forEach(s => {
        if (isScheduleMissed(s, now)) {
            s.date = todayStr;
            s.time = nowTime;
            delete s.scheduler_status;
            delete s.missed_at;
            count++;
        }
    });

    persistSchedule();
    renderTimeline();
    renderCalendar();
    ensureSchedulerForPending();

    if (count > 0) {
        toast(`${count} clip${count > 1 ? 's' : ''} queued for immediate upload`, 'success');
    }
}

function dismissMissedBanner() {
    document.getElementById('missed-uploads-banner').classList.add('hidden');
}

function dropClipOnDate(clipIdx, dateStr) {
    const clip = state.results[clipIdx];
    if (!clip) return;

    const title = clip.filename.replace(/\.mp4$/i, '');
    const channelId = _getScheduleChannelId() || state.selectedChannel || null;
    const scheduledAt = _resolveSchedulableDateTime(dateStr);
    state.scheduled.push({
        ...clipIdentityFields(clip, clipIdx),
        ...descriptionFieldsForClip(clip, clipIdx, title),
        ...channelIdentityFields(channelId),
        date: scheduledAt.date,
        time: scheduledAt.time,
        title,
        tags: DEFAULT_UPLOAD_TAGS,
        category_id: DEFAULT_CATEGORY_ID,
        privacy: document.getElementById('smart-sched-privacy').value || 'public',
        uploaded: false,
    });

    persistSchedule();
    renderTimeline();
    renderCalendar();
    renderClipTray();
    openMetaModal(state.scheduled.length - 1);
}

/* ── Clip Picker (click on calendar day) ─────────────────────────────── */

function openClipPicker(dateStr) {
    if (!state.results.length) return toast('No clips available. Generate clips first.', 'warning');
    const scheduledAt = _resolveSchedulableDateTime(dateStr);
    state.pickerDate = scheduledAt.date;

    document.getElementById('clip-picker-title').textContent = `Schedule on ${scheduledAt.date}`;
    document.getElementById('picker-time').value = scheduledAt.time;

    const list = document.getElementById('clip-picker-list');
    list.innerHTML = '';
    state.results.forEach((clip, i) => {
        const item = document.createElement('div');
        item.className = 'clip-picker-item';
        item.innerHTML = `<span class="tray-clip-num">Clip ${i+1}</span><span class="tray-clip-name">${escHtml(clip.filename)}</span>`;
        item.onclick = () => pickClipForDate(i);
        list.appendChild(item);
    });

    showModal('clip-picker-modal');
}

function pickClipForDate(clipIdx) {
    const dateStr = state.pickerDate;
    const time = document.getElementById('picker-time').value || '12:00';
    closeModal('clip-picker-modal');

    const clip = state.results[clipIdx];
    if (!clip) return;

    const title = clip.filename.replace(/\.mp4$/i, '');
    const channelId = state.selectedChannel || null;
    const scheduledAt = _resolveSchedulableDateTime(dateStr, time);
    state.scheduled.push({
        ...clipIdentityFields(clip, clipIdx),
        ...descriptionFieldsForClip(clip, clipIdx, title),
        ...channelIdentityFields(channelId),
        date: scheduledAt.date,
        time: scheduledAt.time,
        title,
        tags: DEFAULT_UPLOAD_TAGS,
        category_id: DEFAULT_CATEGORY_ID,
        privacy: document.getElementById('smart-sched-privacy').value || 'public',
        uploaded: false,
    });

    persistSchedule();
    renderTimeline();
    renderCalendar();
    openMetaModal(state.scheduled.length - 1);
}

/* ── Persist schedule to Python backend ──────────────────────────────── */

function persistSchedule() {
    _cachedNextUpload = null; _nextUploadCacheTime = 0; // invalidate scheduler cache
    try {
        pywebview.api.save_scheduled(state.scheduled);
    } catch (_) {}
    ensureSchedulerForPending();
}

/* ── Meta Modal (edit scheduled item) ─────────────────────────────────── */

function openMetaModal(schedIdx) {
    const item = state.scheduled[schedIdx];
    if (!item) return;
    state.editingScheduleIdx = schedIdx;

    document.getElementById('meta-modal-title').textContent = `Clip ${item.clipIdx + 1} — ${item.date}`;
    document.getElementById('modal-meta-title').value = item.title;
    document.getElementById('modal-meta-desc').value = item.description_custom_text || '';
    document.getElementById('modal-meta-tags').value = item.tags;
    document.getElementById('modal-meta-privacy').value = item.privacy;
    document.getElementById('modal-meta-time').value = item.time;

    updateModalCategoryDropdown();
    item.category_id = DEFAULT_CATEGORY_ID;
    document.getElementById('modal-meta-category').value = DEFAULT_CATEGORY_ID;

    showModal('meta-modal');
    updateMetaDescriptionPreview();
}

function updateMetaDescriptionPreview() {
    const idx = state.editingScheduleIdx;
    const preview = document.getElementById('modal-description-preview');
    if (!preview || idx < 0 || !state.scheduled[idx]) return;
    const item = { ...state.scheduled[idx] };
    item.title = document.getElementById('modal-meta-title')?.value || item.title || 'Untitled';
    item.description_custom_text = document.getElementById('modal-meta-desc')?.value || '';
    item.description_generated = item.description_generated || item.generated_description || item.title;
    updateScheduledDescriptionPreview(item);
    preview.textContent = item.final_description || item.description || '';
}

function saveMetaModal() {
    const idx = state.editingScheduleIdx;
    if (idx < 0 || !state.scheduled[idx]) return;

    const item = state.scheduled[idx];
    item.title = document.getElementById('modal-meta-title').value || 'Untitled';
    item.description_custom_text = document.getElementById('modal-meta-desc').value || '';
    item.description_generated = item.description_generated || item.generated_description || item.title;
    item.generated_description = item.description_generated;
    item.tags = document.getElementById('modal-meta-tags').value;
    item.category_id = DEFAULT_CATEGORY_ID;
    item.privacy = document.getElementById('modal-meta-privacy').value;
    item.time = document.getElementById('modal-meta-time').value;
    updateScheduledDescriptionPreview(item);

    closeModal('meta-modal');
    persistSchedule();
    renderTimeline();
    renderCalendar();
}

function closeMetaModal() { closeModal('meta-modal'); }

function removeScheduledItem() {
    const idx = state.editingScheduleIdx;
    if (idx >= 0) { state.scheduled.splice(idx, 1); state.editingScheduleIdx = -1; }
    closeModal('meta-modal');
    persistSchedule();
    renderTimeline();
    renderCalendar();
}

/* ── Upload ───────────────────────────────────────────────────────────── */

async function toggleAutoDelete(enabled) {
    try { await pywebview.api.set_delete_after_upload(enabled); } catch (_) {}
}

async function refreshUploadClips() {
    toast('Scanning clips folder...', 'info');
    await loadUploadSection();
    toast('Clips refreshed', 'success');
}

async function cancelUpload() {
    try {
        await pywebview.api.cancel_upload();
        await pywebview.api.cancel_processing();
    } catch (_) {}
    document.getElementById('upload-status').textContent = 'Stopping upload...';
    document.getElementById('btn-cancel-upload').disabled = true;
    toast('Stopping upload...', 'warning');
}

// Called from Python when a clip is auto-deleted after upload
window.onClipDeleted = async function(clipIdx, filename) {
    toast(`Deleted "${filename}" from disk`, 'info');
    const idx = state.results.findIndex((clip, i) => i === Number(clipIdx) || clip.filename === filename);
    if (idx >= 0) {
        state.results.splice(idx, 1);
        if (idx < state.moments.length) state.moments.splice(idx, 1);
        await refreshScheduleFromBackend(false);
        renderClipTray();
        renderTimeline();
        renderCalendar();
    }
    if (document.getElementById('section-results')?.classList.contains('active')) {
        await loadResults();
    } else if (document.getElementById('section-upload')?.classList.contains('active')) {
        await loadUploadSection();
    } else if (document.getElementById('section-library')?.classList.contains('active')) {
        await loadLibrary();
    }
};

async function startUpload() {
    if (!state.scheduled.length) return toast('Click "Schedule All Clips" to create a schedule first', 'warning');

    state.scheduled = normalizeScheduledMetadata(state.scheduled);
    const pending = state.scheduled
        .filter(s => !s.uploaded)
        .map(s => ({ ...s, _scheduledDate: scheduledLocalDate(s) }))
        .sort((a, b) => {
            const ad = a._scheduledDate ? a._scheduledDate.getTime() : Number.MAX_SAFE_INTEGER;
            const bd = b._scheduledDate ? b._scheduledDate.getTime() : Number.MAX_SAFE_INTEGER;
            return ad - bd;
        });

    if (pending.some(s => !s._scheduledDate)) {
        return toast('One or more scheduled clips has an invalid date or time', 'error');
    }

    const clipsMetadata = pending.map(s => ({
        index: s.clipIdx,
        clip_id: s.clip_id,
        source_id: s.source_id,
        source_stem: s.source_stem,
        clip_filename: s.clip_filename,
        title: s.title,
        description: s.final_description || s.description,
        final_description: s.final_description || s.description,
        description_generated: s.description_generated || s.generated_description || '',
        generated_description: s.generated_description || s.description_generated || '',
        description_custom_text: s.description_custom_text || '',
        description_auto_hashtags: s.description_auto_hashtags !== false,
        game_title: s.game_title || '',
        tags: (s.tags || '').split(',').map(t => t.trim()).filter(Boolean),
        category_id: DEFAULT_CATEGORY_ID,
        privacy: s.privacy || 'private',
        channel_id: s.channel_id,
        account_id: s.account_id,
        ...scheduledPublishFields(s),
    }));

    if (!clipsMetadata.length) return toast('All clips already uploaded', 'warning');
    if (!clipsMetadata.every(meta => meta.account_id)) return toast('Please select a YouTube channel first', 'warning');
    if (!clipsMetadata.every(meta => meta.publish_at)) return toast('Scheduled clips need a valid publish time', 'error');
    const now = new Date();
    if (pending.some(s => s.privacy === 'public' && s._scheduledDate <= now)) {
        return toast('One or more public uploads is scheduled in the past. Reschedule missed uploads first.', 'error');
    }

    try {
        await pywebview.api.save_scheduled(state.scheduled);
    } catch (e) {
        return toast('Could not save the schedule before upload', 'error');
    }

    document.getElementById('upload-progress-card').classList.remove('hidden');
    window.clearTimeout(window._uploadProgressHideTimer);
    document.getElementById('btn-upload').disabled = true;
    const cancelBtn = document.getElementById('btn-cancel-upload');
    if (cancelBtn) cancelBtn.disabled = false;

    const pendingCount = clipsMetadata.length;
    addNotification(
        'Upload Started',
        `Uploading ${pendingCount} clip${pendingCount > 1 ? 's' : ''} to YouTube...`,
        'uploading'
    );

    try {
        const r = await pywebview.api.start_upload(clipsMetadata, null, null, null);
        if (r.error) {
            toast(r.error, 'error');
            addNotification('Upload Error', r.error, 'error');
            document.getElementById('btn-upload').disabled = false;
            const cancelBtn = document.getElementById('btn-cancel-upload');
            if (cancelBtn) cancelBtn.disabled = true;
        }
    } catch (e) {
        toast('Upload failed: ' + e, 'error');
        addNotification('Upload Failed', String(e), 'error');
        document.getElementById('btn-upload').disabled = false;
        const cancelBtn = document.getElementById('btn-cancel-upload');
        if (cancelBtn) cancelBtn.disabled = true;
    }
}

async function showYouTubeSetup() {
    try {
        const r = await pywebview.api.get_app_paths();
        const pathEl = document.getElementById('youtube-client-secret-path');
        if (pathEl && r.client_secrets_file) pathEl.textContent = r.client_secrets_file;
    } catch (_) {}
    showModal('youtube-modal');
}

/* ── Settings ──────────────────────────────────────────────────────────── */

function populateSettings(s) {
    // Restore auto-clips checkbox state
    const autoClipsEl = document.getElementById('set-auto-clips');
    const isAuto = s.num_clips === 'auto';
    if (autoClipsEl) {
        autoClipsEl.checked = isAuto;
    }
    const clipSlider = document.getElementById('set-num-clips');
    const clipLabel = document.getElementById('val-num-clips');
    if (isAuto) {
        if (clipSlider) clipSlider.disabled = true;
        if (clipLabel) clipLabel.textContent = 'Auto';
    } else {
        if (clipSlider) clipSlider.disabled = false;
        setSlider('set-num-clips', s.num_clips);
    }
    setSlider('set-clip-duration', s.clip_duration);
    setSlider('set-min-gap', s.min_gap);
    setSlider('set-crf', s.video_crf);
    setSelect('set-model', s.whisper_model);
    setSelect('set-preset', s.ffmpeg_preset);
    setVal('set-language', s.whisper_language || '');
    const crop = document.getElementById('set-crop-vertical');
    if (crop) crop.checked = s.crop_vertical !== false;
    const subtitlePlacement = s.subtitle_placement || {};
    setSlider('set-subtitle-x', subtitlePlacement.x_pct ?? 50);
    setSlider('set-subtitle-y', subtitlePlacement.y_pct ?? 82);
    setSlider('set-subtitle-width', subtitlePlacement.width_pct ?? 86);
    const style = s.subtitle_style || 'tiktok';
    document.querySelectorAll('.style-option').forEach(opt => {
        opt.classList.toggle('active', opt.dataset.style === style);
        opt.querySelector('input').checked = opt.dataset.style === style;
    });
    updateSubtitlePlacementPreview();
}

function gatherSettings() {
    const autoClips = document.getElementById('set-auto-clips')?.checked;
    const s = {
        num_clips: autoClips ? 'auto' : parseInt(getVal('set-num-clips')),
        clip_duration: parseInt(getVal('set-clip-duration')),
        min_gap: parseInt(getVal('set-min-gap')),
        whisper_model: getVal('set-model'),
        whisper_language: getVal('set-language') || null,
        subtitle_style: document.querySelector('input[name="subtitle-style"]:checked')?.value || 'tiktok',
        subtitle_placement: {
            x_pct: parseInt(getVal('set-subtitle-x') || '50'),
            y_pct: parseInt(getVal('set-subtitle-y') || '82'),
            width_pct: parseInt(getVal('set-subtitle-width') || '86'),
        },
        ffmpeg_preset: getVal('set-preset'),
        video_crf: getVal('set-crf'),
        crop_vertical: document.getElementById('set-crop-vertical')?.checked ?? true,
        description_profile: descriptionProfile(),
    };
    state.settings = { ...state.settings, ...s };
    saveLocal('settings', s);
    // Also persist to Python backend (survives localStorage clears)
    try { pywebview.api.save_settings(s); } catch (_) {}
    return s;
}

function resetSettings() {
    localStorage.removeItem('viria_settings');
    Promise.resolve()
        .then(() => pywebview.api.save_settings({}))
        .then(() => pywebview.api.get_settings())
        .then(s => {
            state.settings = s;
            populateSettings(s);
            refreshDescriptionOptionsStatus();
            toast('Settings reset', 'success');
        })
        .catch(() => toast('Could not reset settings', 'error'));
}

function updateSubtitlePlacementPreview() {
    const box = document.getElementById('subtitle-placement-box');
    if (!box) return;
    const panel = document.querySelector('.subtitle-placement-panel');
    const x = Math.max(10, Math.min(90, parseInt(getVal('set-subtitle-x') || '50')));
    const y = Math.max(12, Math.min(92, parseInt(getVal('set-subtitle-y') || '82')));
    const width = Math.max(45, Math.min(96, parseInt(getVal('set-subtitle-width') || '86')));
    const safe = 2;
    const maxLeft = Math.max(safe, 100 - safe - width);
    const leftEdge = Math.max(safe, Math.min(maxLeft, x - (width / 2)));
    const adjustedX = leftEdge + (width / 2);
    const style = document.querySelector('input[name="subtitle-style"]:checked')?.value || 'tiktok';
    const captionsOff = style === 'none';

    if (panel) {
        panel.classList.toggle('captions-disabled', captionsOff);
        panel.querySelectorAll('input[type="range"]').forEach(input => { input.disabled = captionsOff; });
    }

    box.style.left = adjustedX + '%';
    box.style.top = y + '%';
    box.style.width = width + '%';
    box.className = `subtitle-placement-box subtitle-placement-${style}`;
    box.textContent = captionsOff ? 'NO CAPTIONS' : 'TRANSCRIPT TEXT';
}

function updateSliderLabel(el) {
    const lbl = document.getElementById('val-' + el.id.replace('set-', ''));
    if (!lbl) return;
    if (el.id === 'set-clip-duration') {
        const v = parseInt(el.value);
        if (v >= 60) {
            const m = Math.floor(v / 60);
            const s = v % 60;
            lbl.textContent = s > 0 ? `${m}m ${s}s` : `${m}m`;
        } else {
            lbl.textContent = v + 's';
        }
    } else if (el.id === 'set-min-gap') {
        lbl.textContent = el.value + 's';
    } else if (el.id === 'set-subtitle-x' || el.id === 'set-subtitle-y' || el.id === 'set-subtitle-width') {
        lbl.textContent = el.value + '%';
        updateSubtitlePlacementPreview();
    } else {
        lbl.textContent = el.value;
    }
}

/* ── Helpers ───────────────────────────────────────────────────────────── */

function fmtTime(s) { s = Math.round(s); return Math.floor(s/60) + ':' + String(s%60).padStart(2,'0'); }
function formatNumber(n) { n = parseInt(n)||0; if (n >= 1e6) return (n/1e6).toFixed(1)+'M'; if (n >= 1e3) return (n/1e3).toFixed(1)+'K'; return String(n); }
function formatBytes(n) { n = Number(n)||0; if (n >= 1048576) return (n/1048576).toFixed(1)+' MB'; if (n >= 1024) return (n/1024).toFixed(1)+' KB'; return n + ' B'; }
function formatLearningCap(n) { n = Number(n)||0; return '+/-' + n.toFixed(2); }
function formatLearningTimestamp(value) {
    if (!value) return 'Never';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}
function escHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function safeMediaUrl(url) {
    const raw = String(url || '').trim();
    if (!raw) return '';
    try {
        const parsed = new URL(raw, window.location.href);
        if (['http:', 'https:', 'data:', 'blob:'].includes(parsed.protocol)) return raw;
    } catch (_) {}
    return '';
}
function setSlider(id, val) { const el = document.getElementById(id); if (el) { el.value = val; updateSliderLabel(el); } }
function setSelect(id, val) { const el = document.getElementById(id); if (el) el.value = val; }
function setVal(id, val) { const el = document.getElementById(id); if (el) el.value = val; }
function getVal(id) { return document.getElementById(id)?.value ?? ''; }
function saveLocal(k, d) { try { localStorage.setItem('viria_'+k, JSON.stringify(d)); } catch (_) {} }
function loadLocal(k, fb) { try { const d = localStorage.getItem('viria_'+k); return d ? JSON.parse(d) : fb; } catch (_) { return fb; } }

/* ── Toast / Modal ─────────────────────────────────────────────────────── */

function toast(msg, type = 'info') {
    const c = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast ${type}`; el.textContent = msg;
    c.appendChild(el);
    setTimeout(() => { el.classList.add('removing'); setTimeout(() => el.remove(), 300); }, 4000);
}

/* ── Notification Center ──────────────────────────────────────────────── */

const _notifications = [];
let _notifUnreadCount = 0;

function addNotification(title, desc, type = 'info', { progress = -1, id = null } = {}) {
    const notif = {
        id: id || ('notif_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6)),
        title,
        desc,
        type,       // 'success' | 'error' | 'info' | 'uploading'
        time: new Date(),
        unread: true,
        progress,   // -1 = no progress bar, 0-100 = progress
    };
    _notifications.unshift(notif);
    // Keep max 50 notifications
    if (_notifications.length > 50) _notifications.pop();
    _notifUnreadCount++;
    _updateNotifBadge();
    _renderNotifList();
    return notif.id;
}

function updateNotification(id, updates) {
    const notif = _notifications.find(n => n.id === id);
    if (!notif) return;
    if (updates.title !== undefined) notif.title = updates.title;
    if (updates.desc !== undefined) notif.desc = updates.desc;
    if (updates.type !== undefined) notif.type = updates.type;
    if (updates.progress !== undefined) notif.progress = updates.progress;
    _renderNotifList();
}

function _updateNotifBadge() {
    const btn = document.getElementById('notif-btn');
    if (!btn) return;
    const oldBadge = btn.querySelector('.notif-badge');
    if (oldBadge) oldBadge.remove();

    if (_notifUnreadCount > 0) {
        btn.classList.add('has-unread');
        const badge = document.createElement('span');
        badge.className = 'notif-badge';
        badge.textContent = _notifUnreadCount > 9 ? '9+' : _notifUnreadCount;
        btn.appendChild(badge);
    } else {
        btn.classList.remove('has-unread');
    }
}

function _formatNotifTime(date) {
    const now = new Date();
    const diffMs = now - date;
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return 'Just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function _renderNotifList() {
    const list = document.getElementById('notif-list');
    const empty = document.getElementById('notif-empty');
    if (!list) return;

    if (!_notifications.length) {
        list.innerHTML = '';
        list.appendChild(empty);
        empty.style.display = '';
        return;
    }
    empty.style.display = 'none';

    // Build items — reuse existing DOM where possible
    const frag = document.createDocumentFragment();
    _notifications.forEach((n, i) => {
        const item = document.createElement('div');
        item.className = 'notif-item' + (n.unread ? ' unread' : '');
        item.style.animationDelay = `${Math.min(i * 0.04, 0.3)}s`;

        const iconSvg = {
            success: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>',
            error: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
            uploading: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>',
            info: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
        };

        let progressHtml = '';
        if (n.progress >= 0 && n.progress < 100) {
            progressHtml = `<div class="notif-progress"><div class="notif-progress-fill" style="width:${n.progress}%"></div></div>`;
        }

        item.innerHTML = `
            <div class="notif-icon ${n.type}">${iconSvg[n.type] || iconSvg.info}</div>
            <div class="notif-content">
                <div class="notif-title">${escHtml(n.title)}</div>
                <div class="notif-desc">${escHtml(n.desc)}</div>
                ${progressHtml}
                <div class="notif-time">${_formatNotifTime(n.time)}</div>
            </div>`;
        frag.appendChild(item);
    });

    list.innerHTML = '';
    list.appendChild(frag);
}

function toggleNotifPanel() {
    const panel = document.getElementById('notif-panel');
    const overlay = document.getElementById('notif-overlay');
    const isOpen = panel.classList.contains('open');

    if (isOpen) {
        closeNotifPanel();
    } else {
        panel.classList.add('open');
        overlay.classList.add('open');
        // Mark all as read
        _notifications.forEach(n => n.unread = false);
        _notifUnreadCount = 0;
        _updateNotifBadge();
        _renderNotifList();
    }
}

function closeNotifPanel() {
    document.getElementById('notif-panel')?.classList.remove('open');
    document.getElementById('notif-overlay')?.classList.remove('open');
}

function clearAllNotifications() {
    _notifications.length = 0;
    _notifUnreadCount = 0;
    _updateNotifBadge();
    _renderNotifList();
}

function showModal(id) {
    document.getElementById(id)?.classList.remove('hidden');
    // Show preview delete button for results preview (not library)
    if (id === 'preview-modal' && state.previewClipIdx >= 0) {
        document.getElementById('preview-delete-btn').style.display = '';
    }
}
function closeModal(id) { document.getElementById(id)?.classList.add('hidden'); }
