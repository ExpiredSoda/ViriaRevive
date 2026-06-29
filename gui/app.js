/* ── ViriaRevive Frontend v2 ──────────────────────────────────────────── */

const state = {
    section: 'generate',
    processing: false,
    settings: {},
    results: [],
    moments: [],
    feedbackByClipId: {},
    personalization: { schema_version: 1, events: [], clips: {} },
    voiceProfile: { enabled: false, enrolled: false, sample_count: 0 },
    overallPercent: 0,
    ytConnected: false,
    channels: [],
    categories: [],
    selectedChannel: null,
    // Calendar
    calYear: new Date().getFullYear(),
    calMonth: new Date().getMonth(),
    scheduled: [],          // [{clipIdx, clip_id, source_id, date, time, title, description, tags, category_id, privacy, uploaded}]
    uploadHistory: [],
    editingScheduleIdx: -1,
    pickerDate: null,
    _schedPreset: 'allpeaks',
    calChannelFilter: 'all',  // 'all' or a channel ID
    // Library
    libraryClips: [],
    libraryView: 'grid',
    resultsMomentFilter: 'all',
    resultsOpenFolders: {},
    resultsSelectedFilenames: new Set(),
    resultsVisibleFilenames: [],
    clipTraySearch: '',
    libraryMomentFilter: 'all',
    libraryOpenFolders: {},
    librarySelectedFilenames: new Set(),
    libraryVisibleFilenames: [],
    // Preview
    previewClipIdx: -1,
    previewLibraryClip: null,
    // Delete
    pendingDeleteIdx: -1,
    pendingDeleteFilename: null,
    pendingDeleteFilenames: [],
    pendingDeleteSource: null, // 'results' | 'results-bulk' | 'library' | 'library-bulk' | 'preview'
    pendingFeedback: null,
    aiContextEditing: null,
    gameTitleEditing: null,
    // Batch queue
    batchQueue: [],       // [{url, status: 'pending'|'active'|'done'|'error', label, audioSource, subtitleStyle, generationMode}]
    batchIndex: -1,       // current index being processed (-1 = not running)
    batchSettings: null,  // settings snapshot for the batch run
    batchProgressContext: null,
    generationMode: 'clips',
    wizardAudioProbe: null,
    wizardSavedAudioSource: null,
    wizardProcessingDepth: 'balanced',
    wizardMontageTemplate: 'panic',
    progressStartedAt: 0,
    progressStage: null,
    progressStagePercent: 0,
    dependencies: { ffmpeg: null, ffprobe: null, checked: false, error: '' },
};

const DEFAULT_CATEGORY_ID = '20'; // Gaming
const DEFAULT_UPLOAD_TAGS = 'shorts, gaming, gameplay, gaming shorts, youtube shorts, viral shorts, stream highlights, streamer moments, live stream clips, funny gaming moments, scary gaming moments, horror gaming, scary game, creepy game, survival horror, horror shorts, jump scare, chase scene, panic moment, gaming reaction, lets play, playthrough, vertical gaming, game clips';
const SCHEDULE_BUFFER_MINUTES = 10;
const SCHEDULE_BACKEND_STATUS_KEYS = [
    'scheduler_status',
    'scheduler_note',
    'failure_count',
    'last_error',
    'last_failed_at',
    'retry_after',
    'missed_at',
    'upload_attempt_id',
    'upload_attempt_started_at',
    'upload_attempt_trigger',
    'upload_unknown_at',
];
const DETECTION_PREFERENCES = new Set(['auto', 'quality', 'quantity']);
const PROCESSING_DEPTHS = new Set(['fast', 'balanced', 'deep']);
const GENERATION_MODES = new Set(['clips', 'montage']);
const MONTAGE_TEMPLATES = new Set(['panic', 'funny', 'failure', 'combat', 'story', 'tutorial', 'atmosphere', 'custom']);
const PIPELINE_STAGE_ORDER = ['download', 'detect', 'candidates', 'clips'];
const DEFAULT_PROGRESS_RANGES = {
    download: [0, 12],
    detect: [12, 24],
    candidates: [24, 68],
    clips: [68, 98],
    upload: [0, 100],
};
const FEEDBACK_REASON_PRESETS = {
    like: ['Strong hook', 'Funny moment', 'Good pacing', 'Useful explanation', 'Good subtitles'],
    dislike: ['Weak moment', 'Wrong audio', 'Bad timing', 'Too slow', 'Bad subtitles'],
    favorite: ['Post this', 'Best moment', 'Creator voice', 'Strong payoff', 'Replayable'],
};
let _lastOllamaStatus = null;
let _ollamaModelDownloadActive = false;
let _ollamaVisionModelDownloadActive = false;
let _subtitlePreviewUrl = '';
let _settingsPersistTimer = null;

function persistSettingsAsync(settings, { quiet = false } = {}) {
    if (!window.pywebview || !pywebview.api || !pywebview.api.save_settings) return;
    clearTimeout(_settingsPersistTimer);
    const payload = { ...(settings || {}) };
    delete payload.game_title_hint;
    _settingsPersistTimer = setTimeout(async () => {
        try {
            const result = await pywebview.api.save_settings(payload);
            if (result && result.error && !quiet) toast('Settings could not be saved', 'error');
        } catch (e) {
            if (!quiet) toast('Settings could not be saved', 'error');
            console.error('Save settings failed:', e);
        }
    }, 250);
}

function scheduledLocalDate(item) {
    const date = String(item?.date || '').trim();
    const time = String(item?.time || '').trim() || '12:00';
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^\d{2}:\d{2}$/.test(time)) return null;
    const d = new Date(`${date}T${time}:00`);
    return Number.isNaN(d.getTime()) ? null : d;
}

function normalizeDetectionPreference(value) {
    const pref = String(value || 'auto').trim().toLowerCase();
    return DETECTION_PREFERENCES.has(pref) ? pref : 'auto';
}

function normalizeProcessingDepth(value) {
    const depth = String(value || 'balanced').trim().toLowerCase().replace('_', '-');
    if (depth === 'normal' || depth === 'default') return 'balanced';
    if (depth === 'deep-analysis' || depth === 'deep analysis') return 'deep';
    return PROCESSING_DEPTHS.has(depth) ? depth : 'balanced';
}

function normalizeGameTitleHint(value) {
    return String(value || '').replace(/[\u0000-\u001f\u007f]/g, '').replace(/\s+/g, ' ').trim().slice(0, 120);
}

function normalizeGenerationMode(value) {
    const mode = String(value || 'clips').trim().toLowerCase().replace('_', '-');
    return GENERATION_MODES.has(mode) ? mode : 'clips';
}

function normalizeMontageTemplate(value) {
    const template = String(value || 'panic').trim().toLowerCase().replace(/[\s_]+/g, '-');
    return MONTAGE_TEMPLATES.has(template) ? template : 'panic';
}

function normalizeMontagePrompt(value) {
    return String(value || '').replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 500);
}

function normalizeMontageDuration(value) {
    const seconds = parseInt(value, 10);
    return [30, 45, 60, 90].includes(seconds) ? seconds : 60;
}

function normalizeMontageCount(value) {
    const count = parseInt(value, 10);
    return Number.isFinite(count) ? Math.max(1, Math.min(5, count)) : 1;
}

function montageSettingsFromWizard() {
    return {
        template: normalizeMontageTemplate(state.wizardMontageTemplate),
        target_duration: normalizeMontageDuration(getVal('wizard-montage-duration')),
        count: normalizeMontageCount(getVal('wizard-montage-count')),
        prompt: normalizeMontagePrompt(getVal('wizard-montage-prompt')),
    };
}

function pruneCompletedBatchItemsForNewRun() {
    if (state.processing) return;
    const before = state.batchQueue.length;
    state.batchQueue = state.batchQueue.filter(q => q.status === 'pending');
    if (state.batchQueue.length !== before) renderBatchQueue();
}

function currentBatchSourceCount() {
    return state.batchQueue.filter(q => q.status === 'pending' || q.status === 'active' || q.status === 'done').length;
}

function sourceDisplayLabel(source) {
    const text = String(source || '').trim();
    if (!text) return 'Source';
    if (/^https?:\/\//i.test(text)) {
        try {
            const url = new URL(text);
            const host = url.hostname.replace(/^www\./i, '');
            const path = url.pathname && url.pathname !== '/' ? url.pathname.replace(/\/$/, '') : '';
            return `${host}${path}`.slice(0, 120);
        } catch (_) {
            return text.length > 120 ? text.slice(0, 117) + '...' : text;
        }
    }
    const slash = Math.max(text.lastIndexOf('\\'), text.lastIndexOf('/'));
    const name = slash >= 0 ? text.slice(slash + 1) : text;
    return name || text;
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

function clearScheduleBackendStatus(item) {
    if (!item || typeof item !== 'object') return;
    SCHEDULE_BACKEND_STATUS_KEYS.forEach(key => { delete item[key]; });
}

function isScheduleMissed(item, now = new Date()) {
    if (!item || item.uploaded) return false;
    const d = scheduledLocalDate(item);
    return !!d && now.getTime() > d.getTime() + SCHEDULE_BUFFER_MINUTES * 60 * 1000;
}

function missingFfmpegParts(deps = state.dependencies) {
    const missing = [];
    if (!deps || deps.ffmpeg !== true) missing.push('ffmpeg');
    if (!deps || deps.ffprobe !== true) missing.push('ffprobe');
    return missing;
}

function renderFfmpegStatus() {
    const deps = state.dependencies || {};
    const card = document.getElementById('ffmpeg-status-card');
    const stateEl = document.getElementById('ffmpeg-status-state');
    const detailEl = document.getElementById('ffmpeg-status-detail');
    const modalStatus = document.getElementById('ffmpeg-modal-status');
    const missing = missingFfmpegParts(deps);
    const ready = deps.checked && missing.length === 0;

    if (card) {
        card.classList.toggle('hidden', ready);
        card.classList.toggle('ready', ready);
        card.classList.toggle('missing', !ready);
    }
    if (stateEl) stateEl.textContent = ready ? 'FFmpeg ready' : 'FFmpeg setup needed';
    if (detailEl) {
        detailEl.textContent = ready
            ? 'ffmpeg and ffprobe were found.'
            : deps.error
                ? 'Could not check FFmpeg. Recheck after installing both ffmpeg.exe and ffprobe.exe.'
                : `Missing ${missing.join(' and ')}. Install FFmpeg or place both executables in the app bin folder.`;
    }
    if (modalStatus) {
        modalStatus.textContent = ready
            ? 'Recheck passed. You can close this dialog.'
            : `Still missing ${missing.join(' and ')}.`;
    }
}

async function refreshFfmpegDependencies({ quiet = false, showModalOnMissing = false } = {}) {
    const recheckBtns = document.querySelectorAll('[data-ffmpeg-recheck]');
    recheckBtns.forEach(btn => { btn.disabled = true; btn.dataset.previousText = btn.textContent; btn.textContent = 'Checking...'; });
    try {
        const deps = await pywebview.api.check_dependencies();
        state.dependencies = {
            ffmpeg: deps.ffmpeg === true,
            ffprobe: deps.ffprobe === true,
            checked: true,
            error: '',
        };
    } catch (e) {
        state.dependencies = { ffmpeg: false, ffprobe: false, checked: true, error: String(e || 'check failed') };
    } finally {
        recheckBtns.forEach(btn => { btn.disabled = false; btn.textContent = btn.dataset.previousText || 'Recheck'; delete btn.dataset.previousText; });
    }

    renderFfmpegStatus();
    const missing = missingFfmpegParts();
    if (missing.length) {
        if (!quiet) toast(`FFmpeg setup incomplete: missing ${missing.join(' and ')}`, 'warning');
        if (showModalOnMissing) showModal('ffmpeg-modal');
        return false;
    }
    if (!quiet) toast('FFmpeg setup looks good', 'success');
    return true;
}

async function ensureFfmpegReady(action = 'continue') {
    if (!state.dependencies.checked) {
        await refreshFfmpegDependencies({ quiet: true, showModalOnMissing: false });
    }
    const missing = missingFfmpegParts();
    if (!missing.length) return true;
    renderFfmpegStatus();
    showModal('ffmpeg-modal');
    const card = document.getElementById('ffmpeg-status-card');
    if (card) {
        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        card.classList.remove('upload-focus-pulse');
        void card.offsetWidth;
        card.classList.add('upload-focus-pulse');
        window.setTimeout(() => card.classList.remove('upload-focus-pulse'), 1200);
    }
    toast(`Install FFmpeg before you ${action}`, 'error');
    return false;
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

function scheduleItemStatus(item, now = new Date()) {
    if (!item) return { key: 'invalid', className: 'invalid', label: 'Invalid' };
    const schedulerStatus = String(item.scheduler_status || '').toLowerCase();
    const uploadState = String(item.upload_state || item.send_status || '').toLowerCase();
    if (schedulerStatus === 'upload_failed') {
        return uploadRetryReady(item, now)
            ? { key: 'retry_ready', className: 'failed', label: 'Retry ready' }
            : { key: 'failed', className: 'failed', label: retryAfterLabel(item) };
    }
    if (schedulerStatus === 'upload_outcome_unknown') return { key: 'unknown', className: 'failed', label: 'Check YouTube' };
    if (schedulerStatus === 'account_disconnected') return { key: 'disconnected', className: 'disconnected', label: 'Needs account' };
    if (schedulerStatus === 'missed' || isScheduleMissed(item, now)) return { key: 'missed', className: 'missed', label: 'Missed time' };
    if (schedulerStatus === 'uploading' || uploadState === 'sending') return { key: 'sending', className: 'sending', label: 'Sending' };
    if (item.uploaded || uploadState === 'sent_to_youtube' || uploadState === 'youtube_scheduled') {
        if (uploadState === 'youtube_scheduled' || (String(item.privacy || '').toLowerCase() === 'public' && item.publish_at_utc)) {
            return { key: 'youtube_scheduled', className: 'youtube-scheduled', label: 'YouTube scheduled' };
        }
        return { key: 'sent', className: 'sent', label: 'Sent' };
    }
    if (!scheduledLocalDate(item)) return { key: 'invalid', className: 'invalid', label: 'Needs time' };
    return { key: 'pending', className: 'pending', label: 'Pending locally' };
}

function uploadRetryReady(item, now = new Date()) {
    const raw = String(item?.retry_after || '').trim();
    if (!raw) return true;
    const retryAt = new Date(raw);
    return Number.isNaN(retryAt.getTime()) || retryAt <= now;
}

function retryAfterLabel(item) {
    const raw = String(item?.retry_after || '').trim();
    const retryAt = raw ? new Date(raw) : null;
    if (!retryAt || Number.isNaN(retryAt.getTime())) return 'Upload failed';
    return `Retry ${retryAt.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`;
}

function isPublicScheduleTooSoon(item, now = new Date()) {
    if (!item || item.uploaded) return false;
    if (String(item.privacy || 'private').toLowerCase() !== 'public') return false;
    const d = scheduledLocalDate(item);
    if (!d) return false;
    return d.getTime() <= now.getTime() + SCHEDULE_BUFFER_MINUTES * 60 * 1000;
}

function scheduleBlocksUpload(item, now = new Date()) {
    const status = scheduleItemStatus(item, now);
    if (['missed', 'failed', 'unknown', 'disconnected', 'invalid', 'sending'].includes(status.key)) return status.key;
    if (isPublicScheduleTooSoon(item, now)) return 'too_soon';
    if (!item.account_id || !item.channel_id) return 'missing_channel';
    return '';
}

function uploadReadinessState() {
    const now = new Date();
    const pendingItems = state.scheduled.filter(s => !s.uploaded);
    const allStatuses = state.scheduled.map(s => scheduleItemStatus(s, now));
    const activeStatuses = pendingItems.map(s => scheduleItemStatus(s, now));
    const sending = activeStatuses.some(s => s.key === 'sending');
    const hasAttention = pendingItems.some(s => scheduleBlocksUpload(s, now));
    const missingAccount = pendingItems.some(s => !s.account_id || !s.channel_id) || !state.ytConnected;
    const missingTitle = pendingItems.some(s => !String(s.title || '').trim());
    const staleMetadata = pendingItems.some(s => s.metadata_stale === true);
    const sentCount = allStatuses.filter(s => ['sent', 'youtube_scheduled'].includes(s.key)).length;

    const account = state.ytConnected
        ? { label: 'Accounts', value: 'Connected', state: 'ready', icon: '1', focus: 'accounts' }
        : { label: 'Accounts', value: 'Needs account', state: 'blocked', icon: '1', focus: 'accounts' };

    let clips;
    if (!state.results.length) clips = { label: 'Videos', value: 'No clips', state: 'neutral', icon: '2', focus: 'clips' };
    else if (missingTitle) clips = { label: 'Videos', value: 'Needs titles', state: 'warning', icon: '2', focus: 'clips' };
    else if (staleMetadata) clips = { label: 'Videos', value: 'Needs reroll', state: 'warning', icon: '2', focus: 'clips' };
    else clips = { label: 'Videos', value: 'Ready', state: 'ready', icon: '2', focus: 'clips' };

    let schedule;
    if (!pendingItems.length) {
        schedule = { label: 'Calendar', value: sentCount ? `${sentCount} sent` : 'Nothing scheduled', state: sentCount ? 'ready' : 'neutral', icon: '3', focus: 'schedule' };
    } else if (sending) {
        schedule = { label: 'Calendar', value: 'Sending now', state: 'warning', icon: '3', focus: 'schedule' };
    } else if (hasAttention) {
        schedule = { label: 'Calendar', value: 'Needs attention', state: 'warning', icon: '3', focus: 'schedule' };
    } else {
        schedule = { label: 'Calendar', value: `${pendingItems.length} scheduled`, state: 'ready', icon: '3', focus: 'schedule' };
    }

    let review;
    if (!pendingItems.length) review = { label: 'Send', value: sentCount ? 'Sent to YouTube' : 'Nothing to send', state: sentCount ? 'ready' : 'neutral', icon: '4', focus: 'review' };
    else if (sending) review = { label: 'Send', value: 'Upload running', state: 'blocked', icon: '4', focus: 'review' };
    else if (missingAccount) review = { label: 'Send', value: 'Needs YouTube channel', state: 'blocked', icon: '4', focus: 'review' };
    else if (hasAttention) review = { label: 'Send', value: 'Review calendar', state: 'warning', icon: '4', focus: 'review' };
    else review = { label: 'Send', value: 'Ready to send', state: 'ready', icon: '4', focus: 'review' };

    return [account, clips, schedule, review];
}

function renderUploadReadinessStrip() {
    const strip = document.getElementById('upload-readiness-strip');
    if (!strip) return;
    strip.innerHTML = uploadReadinessState().map(item => `
        <button type="button" class="upload-readiness-step is-${item.state}" onclick="focusUploadReadiness('${escHtml(item.focus || '')}')">
            <span class="upload-readiness-icon">${escHtml(item.icon)}</span>
            <span class="upload-readiness-text">
                <span class="upload-readiness-label">${escHtml(item.label)}</span>
                <span class="upload-readiness-value">${escHtml(item.value)}</span>
            </span>
        </button>
    `).join('');
}

function focusUploadReadiness(target) {
    const ids = {
        accounts: 'yt-connect-card',
        clips: 'clip-tray-card',
        schedule: 'calendar-area-card',
        review: 'upload-review-panel',
    };
    const el = document.getElementById(ids[target] || target);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.remove('upload-focus-pulse');
    void el.offsetWidth;
    el.classList.add('upload-focus-pulse');
    window.setTimeout(() => el.classList.remove('upload-focus-pulse'), 1200);
}

function uploadHistoryDateKey(row) {
    const direct = String(row?.date || '').trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(direct)) return direct;
    const raw = row?.publish_at_utc || row?.finished_at_utc || row?.uploaded_at || row?.sent_at || '';
    if (!raw) return '';
    const d = new Date(raw);
    return Number.isNaN(d.getTime()) ? '' : _toDateStr(d);
}

function uploadHistoryByDate(channelFilter = 'all') {
    const grouped = {};
    (state.uploadHistory || []).forEach(row => {
        if (!row || typeof row !== 'object') return;
        if (channelFilter !== 'all' && row.channel_id && row.channel_id !== channelFilter) return;
        const dateKey = uploadHistoryDateKey(row);
        if (!dateKey) return;
        if (!grouped[dateKey]) grouped[dateKey] = [];
        grouped[dateKey].push(row);
    });
    return grouped;
}

function _formatScheduleDateLabel(dateStr, timeStr = '') {
    const d = new Date(`${dateStr}T${timeStr || '12:00'}:00`);
    if (Number.isNaN(d.getTime())) return 'Not scheduled';
    const base = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    return timeStr ? `${base}, ${timeStr}` : base;
}

function uploadSummaryState() {
    const now = new Date();
    const rows = (state.scheduled || []).map((s, idx) => ({
        ...s,
        _idx: idx,
        _date: scheduledLocalDate(s),
        _status: scheduleItemStatus(s, now),
    }));
    const queued = rows.filter(s => !s.uploaded);
    const sent = rows.filter(s => ['sent', 'youtube_scheduled'].includes(s._status.key));
    const attention = queued.filter(s => scheduleBlocksUpload(s, now));
    const sortedQueued = [...queued].sort((a, b) => {
        const at = a._date ? a._date.getTime() : Number.MAX_SAFE_INTEGER;
        const bt = b._date ? b._date.getTime() : Number.MAX_SAFE_INTEGER;
        return at - bt;
    });
    const first = sortedQueued[0] || null;
    const last = sortedQueued[sortedQueued.length - 1] || null;
    const selectedChannelId = first ? (first.channel_id || '') : (document.getElementById('smart-sched-channel')?.value || state.selectedChannel || '');
    const channel = selectedChannelId ? channelById(selectedChannelId) : null;
    const queuedChannelIds = [...new Set(queued.map(s => String(s.channel_id || '').trim()).filter(Boolean))];
    const channelLabel = queuedChannelIds.length > 1
        ? `${queuedChannelIds.length} channels`
        : (channel?.title || (selectedChannelId ? selectedChannelId : 'None selected'));
    const privacy = String(first?.privacy || document.getElementById('smart-sched-privacy')?.value || 'public').toLowerCase();
    const missingAccount = queued.some(s => !s.account_id || !s.channel_id) || (!!queued.length && !state.ytConnected);

    const historyCount = (state.uploadHistory || []).length;
    const startLabel = first ? _formatScheduleDateLabel(first.date, first.time) : 'Not scheduled';
    let spanLabel = 'No queued clips';
    if (first && last) {
        spanLabel = first.date === last.date
            ? _formatScheduleDateLabel(first.date)
            : `${_formatScheduleDateLabel(first.date)} - ${_formatScheduleDateLabel(last.date)}`;
    } else if (historyCount) {
        spanLabel = `${historyCount} sent in history`;
    }

    let reviewClass = 'neutral';
    let reviewLabel = 'Not ready';
    if (!queued.length && sent.length) {
        reviewClass = 'ready';
        reviewLabel = 'Sent';
    } else if (!queued.length) {
        reviewLabel = 'No queue';
    } else if (missingAccount) {
        reviewClass = 'blocked';
        reviewLabel = 'Needs account';
    } else if (attention.length) {
        reviewClass = 'warning';
        reviewLabel = attention.some(s => s._status.key === 'sending') ? 'Sending' : 'Needs review';
    } else {
        reviewClass = 'ready';
        reviewLabel = 'Ready';
    }

    const publicQueued = queued.some(s => String(s.privacy || privacy).toLowerCase() === 'public');
    const modeNote = !queued.length
        ? 'Schedule clips on the calendar before sending them to YouTube.'
        : publicQueued
            ? 'Send now to YouTube; public clips publish at their calendar time.'
            : 'Send now to YouTube with the selected private or unlisted visibility.';
    const actionTitle = queued.length
        ? `${queued.length} clip${queued.length !== 1 ? 's' : ''} queued`
        : sent.length
            ? `${sent.length} sent to YouTube`
            : 'No clips queued';
    const actionDetail = queued.length
        ? `${queuedChannelIds.length > 1 ? 'Multiple channels' : (channel?.title || 'No channel selected')} · ${privacy} · ${startLabel}`
        : historyCount
            ? `${historyCount} previous upload${historyCount !== 1 ? 's' : ''} stored locally`
            : 'Schedule clips before sending them to YouTube.';

    return {
        queuedCount: queued.length,
        sentCount: sent.length,
        attentionCount: attention.length,
        clipsLabel: queued.length
            ? `${queued.length} queued`
            : sent.length
                ? `${sent.length} sent`
                : `${state.results.length} available`,
        channelLabel,
        visibilityLabel: privacy.charAt(0).toUpperCase() + privacy.slice(1),
        startLabel,
        spanLabel,
        reviewClass,
        reviewLabel,
        modeNote,
        actionTitle,
        actionDetail,
    };
}

function renderUploadSummary() {
    const summary = uploadSummaryState();
    const setText = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    };
    setText('upload-summary-clips', summary.clipsLabel);
    setText('upload-summary-channel', summary.channelLabel);
    setText('upload-summary-visibility', summary.visibilityLabel);
    setText('upload-summary-start', summary.startLabel);
    setText('upload-summary-span', summary.spanLabel);
    setText('upload-mode-note', summary.modeNote);
    setText('upload-action-title', summary.actionTitle);
    setText('upload-action-detail', summary.actionDetail);
    const status = document.getElementById('upload-review-status');
    if (status) {
        status.textContent = summary.reviewLabel;
        status.className = `upload-review-status ${summary.reviewClass}`;
    }
    const pending = state.scheduled.filter(s => !s.uploaded);
    const pendingStatuses = pending.map(s => scheduleItemStatus(s));
    const attention = pending.some(s => scheduleBlocksUpload(s));
    const sending = pendingStatuses.some(s => s.key === 'sending');
    const tooSoon = pending.some(s => isPublicScheduleTooSoon(s));
    const missingAccount = pending.some(s => !s.account_id || !s.channel_id);
    const previewBtn = document.getElementById('btn-preview-queue');
    if (previewBtn) {
        previewBtn.disabled = !pending.length;
        previewBtn.title = pending.length ? 'Jump to the calendar plan' : 'Schedule clips before viewing the calendar plan';
    }
    const uploadBtn = document.getElementById('btn-upload');
    if (uploadBtn) {
        const disabled = !pending.length || !state.ytConnected || missingAccount || attention;
        uploadBtn.disabled = disabled;
        uploadBtn.title = !pending.length
            ? 'Schedule clips before sending them to YouTube'
            : !state.ytConnected
                ? 'Connect YouTube before uploading'
                : missingAccount
                    ? 'Choose a YouTube channel for every scheduled clip'
                    : sending
                        ? 'An upload is already running'
                        : tooSoon
                            ? `Public uploads need a publish time at least ${SCHEDULE_BUFFER_MINUTES} minutes from now`
                            : attention
                                ? 'Resolve schedule items that need attention before uploading'
                        : "Send pending clips to YouTube now with each clip's calendar publish time";
    }
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
    const clip = state.results[idx] || {};
    const moment = state.moments[idx] || {};
    return gameTitleForEntity(clip, moment);
}

function creatorTitleContextForClip(idx) {
    const clip = state.results[idx] || {};
    const moment = state.moments[idx] || {};
    return creatorTitleContextForEntity(clip, moment);
}

function gameTitleForEntity(clip = {}, moment = {}) {
    const meta = moment.generated_metadata || clip.generated_metadata || {};
    const truth = moment.truth_summary || clip.truth_summary || {};
    return String(
        clip.game_title
        || meta.game_title
        || moment.game_title
        || truth.game_title
        || ''
    ).trim();
}

function creatorTitleContextForEntity(clip = {}, moment = {}) {
    const meta = moment.generated_metadata || clip.generated_metadata || {};
    return String(
        clip.creator_title_context
        || moment.creator_title_context
        || meta.creator_title_context
        || ''
    ).trim();
}

function generatedMetadataForClip(idx) {
    const moment = state.moments[idx] || {};
    return (moment.generated_metadata && typeof moment.generated_metadata === 'object')
        ? moment.generated_metadata
        : {};
}

function fallbackTitleForClip(clip) {
    return String(clip?.filename || 'Clip').replace(/\.mp4$/i, '').trim() || 'Clip';
}

function isFallbackClipTitle(title, clip) {
    const value = String(title || '').trim();
    if (!value) return true;
    return value === fallbackTitleForClip(clip) || value === String(clip?.filename || '').trim();
}

function uploadTitleForClip(clip, idx) {
    const meta = generatedMetadataForClip(idx);
    return String(meta.title || meta.generated_title || '').trim() || fallbackTitleForClip(clip);
}

function uploadTagsForClip(idx) {
    const meta = generatedMetadataForClip(idx);
    return String(meta.tags || '').trim() || DEFAULT_UPLOAD_TAGS;
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

function scheduleClipFilename(item) {
    if (!item) return '';
    const direct = String(item.clip_filename || item.filename || '').trim();
    if (direct) return direct;
    const idx = Number(item.clipIdx);
    const clip = Number.isInteger(idx) && idx >= 0 ? state.results[idx] : null;
    return String(clip?.filename || '').trim();
}

function metadataMatchesScheduleItem(item, meta) {
    if (!item || !meta) return false;
    const metaClipId = String(meta.clip_id || '').trim();
    const itemClipId = String(item.clip_id || '').trim();
    if (metaClipId || itemClipId) return Boolean(metaClipId && itemClipId && metaClipId === itemClipId);
    const metaFilename = String(meta.clip_filename || meta.filename || '').trim();
    const itemFilename = scheduleClipFilename(item);
    if (metaFilename || itemFilename) return Boolean(metaFilename && itemFilename && metaFilename === itemFilename);
    const metaIndex = Number(meta.index ?? meta.clip_index);
    const itemIndex = Number(item.clipIdx);
    if (Number.isInteger(metaIndex) || Number.isInteger(itemIndex)) {
        return Number.isInteger(metaIndex) && Number.isInteger(itemIndex) && metaIndex === itemIndex;
    }
    return true;
}

function applyGeneratedMetadataToSchedule(item, meta) {
    if (!item || !meta) return item;
    if (!metadataMatchesScheduleItem(item, meta)) {
        item.metadata_stale = true;
        item.metadata_identity_mismatch = true;
        return item;
    }
    delete item.metadata_identity_mismatch;
    if (!String(item.title || '').trim() && meta.title) item.title = meta.title;
    const generated = meta.generated_description || meta.description_generated || meta.description || '';
    if (generated) {
        item.description_generated = generated;
        item.generated_description = generated;
    }
    if (meta.game_title) item.game_title = meta.game_title;
    if (meta.creator_title_context !== undefined) item.creator_title_context = String(meta.creator_title_context || '').trim();
    if (meta.tags) item.tags = meta.tags;
    if (meta.title || generated || meta.tags) item.metadata_stale = false;
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
        creator_title_context: creatorTitleContextForClip(idx),
        description_generated: generated,
        generated_description: generated,
        description_custom_text: profile.custom_text,
        description_auto_hashtags: profile.auto_hashtags,
        description: final,
        final_description: final,
        recommended_hashtags: recommendedDescriptionHashtags(gameTitle),
    };
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

function normalizeScheduledMetadata(items, options = {}) {
    const preserveUnresolved = !!options.preserveUnresolved;
    const normalized = [];
    (items || []).forEach(item => {
        if (!item) return;
        const clipIdx = resolveScheduledClipIndex(item);
        if (clipIdx < 0) {
            if (preserveUnresolved) normalized.push({ ...item });
            return;
        }
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
        const hasGeneratedDescription = Object.prototype.hasOwnProperty.call(item, 'description_generated')
            || Object.prototype.hasOwnProperty.call(item, 'generated_description');
        if (isFallbackClipTitle(item.title, state.results[clipIdx])) item.title = uploadTitleForClip(state.results[clipIdx], clipIdx);
        if (!hasGeneratedDescription) {
            item.description_generated = generatedDescriptionForClip(state.results[clipIdx], clipIdx, item.title);
            item.generated_description = item.description_generated;
        }
        if (item.description_custom_text === undefined) item.description_custom_text = '';
        if (item.description_auto_hashtags === undefined) item.description_auto_hashtags = descriptionProfile().auto_hashtags;
        if (!item.game_title) item.game_title = gameTitleForClip(clipIdx);
        if (item.creator_title_context === undefined) item.creator_title_context = creatorTitleContextForClip(clipIdx);
        if (!String(item.tags || '').trim() || String(item.tags || '').trim() === DEFAULT_UPLOAD_TAGS) item.tags = uploadTagsForClip(clipIdx);
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

function _feedbackReasonFor(latest, eventType) {
    const reasons = latest && typeof latest.reasons === 'object' && latest.reasons ? latest.reasons : null;
    if (reasons) {
        const reason = String(reasons[eventType] || '').trim();
        if (reason) return reason;
        if (Object.keys(reasons).length) return '';
    }
    const latestType = String(latest?.event_type || '').trim().toLowerCase();
    if (latestType && latestType !== eventType) return '';
    return String(latest?.reason || '').trim();
}

function feedbackStatusLabel(latest = {}) {
    const items = [
        ['like', 'Liked'],
        ['dislike', 'Disliked'],
        ['favorite', 'Favorite'],
    ].filter(([eventType]) => latest[eventType]);
    if (!items.length) return 'No feedback yet';
    return items.map(([eventType, label]) => {
        const reason = _feedbackReasonFor(latest, eventType);
        return reason ? `${label} - ${reason}` : label;
    }).join(' / ');
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

function prettyMomentLabel(value) {
    const map = {
        high_energy: 'High energy',
        death_or_failure: 'Death / fail',
        tutorial_or_explainer: 'Explainer',
        commentary_or_review: 'Commentary',
        lore_or_story: 'Lore / story',
        atmosphere_or_visual: 'Atmosphere',
        low_value: 'Low value',
        chase_panic: 'Chase panic',
        combat_action: 'Combat',
        funny_failure: 'Funny fail',
        death_scene: 'Death scene',
        possible_failure: 'Possible failure',
        tutorial_tip: 'Tutorial tip',
        lore_story: 'Lore',
        scenic_atmosphere: 'Scenic',
        creator_reaction: 'Reaction',
        game_narration: 'Game narration',
        navigation_setup: 'Setup',
    };
    const key = String(value || '').trim().toLowerCase().replace(/[-\s]+/g, '_');
    if (!key) return '';
    return map[key] || key.split('_').filter(Boolean).map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

function aiMomentForClip(clip = {}, moment = {}) {
    const categories = clip.moment_categories || moment.moment_categories || {};
    const ai = clip.ai_moment_classification || moment.ai_moment_classification || categories.ai || {};
    const detectedPrimary = clip.primary_category || moment.primary_category || categories.primary || '';
    const aiPrimary = ai.primary_category || '';
    const hasAiLabel = Boolean(ai.status && aiPrimary);
    const primary = hasAiLabel ? aiPrimary : detectedPrimary;
    if (!primary && !detectedPrimary) return null;
    const fineLabels = Array.isArray(ai.fine_labels) ? ai.fine_labels.filter(Boolean).slice(0, 2) : [];
    const primaryLabel = prettyMomentLabel(primary || detectedPrimary);
    const titleParts = [
        `Category: ${primaryLabel}`,
        ai.reason || '',
    ].filter(Boolean);
    return {
        primary: primary || detectedPrimary,
        primaryLabel,
        fineLabels: fineLabels.map(prettyMomentLabel).filter(Boolean),
        sourceType: 'category',
        title: titleParts.join(' - '),
    };
}

function momentFilterKeyForClip(clip = {}, moment = {}) {
    const info = aiMomentForClip(clip, moment);
    return info?.primary || 'unlabeled';
}

function clipMatchesMomentFilter(clip = {}, moment = {}, filter = 'all') {
    const key = String(filter || 'all');
    if (key === 'all') return true;
    return momentFilterKeyForClip(clip, moment) === key;
}

function normalizeAvailableMomentFilter(clips, momentForClip, activeFilter) {
    const filter = String(activeFilter || 'all');
    if (filter === 'all') return 'all';
    const hasMatch = (clips || []).some((clip, index) =>
        clipMatchesMomentFilter(clip, momentForClip ? momentForClip(clip, index) : {}, filter)
    );
    return hasMatch ? filter : 'all';
}

function renderMomentFilterBar(containerId, clips, momentForClip, activeFilter, onSelect) {
    const el = document.getElementById(containerId);
    if (!el) return;
    const rows = (clips || []).map((clip, index) => ({
        clip,
        moment: momentForClip ? (momentForClip(clip, index) || {}) : {},
    }));
    const counts = new Map();
    let labeled = 0;
    rows.forEach(({ clip, moment }) => {
        const key = momentFilterKeyForClip(clip, moment);
        counts.set(key, (counts.get(key) || 0) + 1);
        const info = aiMomentForClip(clip, moment);
        if (info && key !== 'unlabeled') {
            labeled += 1;
        }
    });
    const active = String(activeFilter || 'all');
    if (!rows.length || (!labeled && active === 'all')) {
        el.classList.add('hidden');
        el.innerHTML = '';
        return;
    }
    const categoryOrder = [
        'high_energy',
        'death_or_failure',
        'tutorial_or_explainer',
        'commentary_or_review',
        'lore_or_story',
        'atmosphere_or_visual',
        'low_value',
        'unlabeled',
    ];
    const keys = Array.from(counts.keys()).sort((a, b) => {
        const ai = categoryOrder.indexOf(a);
        const bi = categoryOrder.indexOf(b);
        if (ai !== -1 || bi !== -1) return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
        return prettyMomentLabel(a).localeCompare(prettyMomentLabel(b));
    });
    const buttons = [
        { key: 'all', label: 'All', count: rows.length },
        ...keys.map(key => ({
            key,
            label: key === 'unlabeled' ? 'Unlabeled' : prettyMomentLabel(key),
            count: counts.get(key) || 0,
        })),
    ];
    el.classList.remove('hidden');
    el.innerHTML = `
        <div class="moment-filter-buttons">
            ${buttons.map(btn => `
                <button type="button" class="moment-filter-btn${active === btn.key ? ' active' : ''}" data-filter="${escHtml(btn.key)}" aria-pressed="${active === btn.key ? 'true' : 'false'}">
                    <span>${escHtml(btn.label)}</span>
                    <strong>${btn.count}</strong>
                </button>
            `).join('')}
        </div>
        <span class="moment-filter-summary">${labeled ? `${labeled} categorized` : 'No categories yet'}</span>
    `;
    el.querySelectorAll('.moment-filter-btn').forEach(btn => {
        btn.addEventListener('click', () => onSelect(String(btn.dataset.filter || 'all')));
    });
}

function momentLabelMarkup(clip = {}, moment = {}) {
    const info = aiMomentForClip(clip, moment);
    if (!info) return '';
    const displayLabel = info.fineLabels[0] || info.primaryLabel;
    const title = info.fineLabels[0] && info.primaryLabel !== info.fineLabels[0]
        ? `${info.primaryLabel} - ${info.fineLabels[0]}`
        : info.title;
    return `
        <div class="moment-label-row" title="${escHtml(title)}">
            <span class="moment-chip is-category">${escHtml(displayLabel)}</span>
        </div>`;
}

function clipTruthMarkup(clip = {}, moment = {}, options = {}) {
    const gameTitle = gameTitleForEntity(clip, moment);
    const gameText = `Game: ${gameTitle || 'Unknown'}`;
    const truth = moment.truth_summary || clip.truth_summary || {};
    const speechPolicy = moment.speech_policy || clip.speech_policy || {};
    const speechStatus = String(speechPolicy.status || truth.speech_policy_status || '').trim();
    const speechWarning = String(speechPolicy.warning || truth.speech_policy_warning || '').trim();
    const needsCommentary = speechStatus === 'no_selected_commentary_speech'
        || moment.metadata_needs_context === true
        || clip.metadata_needs_context === true;
    const editable = options.editable === true;
    const editText = gameTitle ? 'Edit game' : 'Set game';
    const editButton = editable
        ? `<button type="button" class="truth-chip truth-chip-btn is-edit-game">${editText}</button>`
        : '';
    const speechChip = needsCommentary
        ? `<span class="truth-chip is-warning" title="${escHtml(speechWarning || 'No commentary transcript was found on the selected track.')}">No commentary transcript</span>`
        : '';
    return `
        <div class="truth-summary-row" title="${escHtml(gameText)}">
            <span class="truth-chip is-game">${escHtml(gameText)}</span>
            ${speechChip}
            ${editButton}
        </div>`;
}

function renderPreviewMomentLabel() {
    const el = document.getElementById('preview-moment-label');
    if (!el) return;
    const clip = state.previewClipIdx >= 0 ? state.results[state.previewClipIdx] : state.previewLibraryClip;
    const moment = state.previewClipIdx >= 0 ? (state.moments[state.previewClipIdx] || {}) : (clip || {});
    const html = `${momentLabelMarkup(clip || {}, moment || {})}${clipTruthMarkup(clip || {}, moment || {})}`;
    if (!html) {
        el.classList.add('hidden');
        el.innerHTML = '';
        return;
    }
    el.classList.remove('hidden');
    el.innerHTML = html;
}

function renderAllFeedbackStates() {
    document.querySelectorAll('.result-card').forEach(card => {
        const idx = Number(card.dataset.clipIdx);
        if (Number.isInteger(idx) && idx >= 0) renderCardFeedbackState(card, state.results[idx]);
    });
    document.querySelectorAll('.library-item').forEach(item => {
        const clipId = item.dataset.clipId || '';
        const clip = state.libraryClips.find(c => c && c.clip_id === clipId);
        if (clip) renderCardFeedbackState(item, clip);
    });
}

function renderPreviewFeedbackState() {
    const panel = document.getElementById('preview-feedback');
    renderPreviewMomentLabel();
    if (!panel) return;
    const clip = state.previewClipIdx >= 0 ? state.results[state.previewClipIdx] : state.previewLibraryClip;
    if (!clip || !clip.clip_id) {
        panel.classList.add('hidden');
        return;
    }
    panel.classList.remove('hidden');
    renderFeedbackState(panel, clip);
    const latest = _feedbackLatest(clip);
    const status = document.getElementById('preview-feedback-status');
    if (status) {
        status.textContent = feedbackStatusLabel(latest);
    }
}

function renderVoiceProfileStatus(voice = {}) {
    state.voiceProfile = voice || { enabled: false, enrolled: false, sample_count: 0 };
    const pill = document.getElementById('voice-profile-enabled');
    const sampleEl = document.getElementById('voice-profile-samples');
    const sizeEl = document.getElementById('voice-profile-size');
    const updatedEl = document.getElementById('voice-profile-updated');
    const rankingEl = document.getElementById('voice-profile-ranking');
    const guidanceEl = document.getElementById('voice-profile-guidance');
    const toggleBtn = document.getElementById('voice-profile-toggle-btn');
    const rankingBtn = document.getElementById('voice-profile-ranking-toggle-btn');
    const enabled = Boolean(state.voiceProfile.enabled);
    const enrolled = Boolean(state.voiceProfile.enrolled);
    const rankingEnabled = Boolean(state.voiceProfile.ranking_enabled);
    const rankingActive = Boolean(state.voiceProfile.ranking_active);
    const influenceState = String(state.voiceProfile.influence_state || '').trim();
    if (pill) {
        pill.textContent = state.voiceProfile.status_label || (enabled && enrolled ? 'Ready' : (enabled ? 'Needs samples' : 'Off'));
        pill.classList.toggle('is-active', enrolled || rankingActive);
        pill.classList.toggle('is-idle', !(enrolled || rankingActive));
    }
    if (sampleEl) sampleEl.textContent = formatNumber(state.voiceProfile.sample_count || 0);
    if (sizeEl) sizeEl.textContent = formatBytes(state.voiceProfile.size_bytes || 0);
    if (updatedEl) {
        updatedEl.textContent = formatLearningTimestamp(state.voiceProfile.updated_at || '');
        if (state.voiceProfile.updated_at) updatedEl.title = state.voiceProfile.updated_at;
        else updatedEl.removeAttribute('title');
    }
    if (rankingEl) {
        let rankingLabel = 'Off';
        if (rankingActive) rankingLabel = 'Active';
        else if ((influenceState === 'needs_samples' || influenceState === 'needs_more_samples') && rankingEnabled) rankingLabel = 'Needs samples';
        else if (influenceState === 'ready_waiting_for_run' || rankingEnabled) rankingLabel = 'On';
        rankingEl.textContent = rankingLabel;
        rankingEl.title = state.voiceProfile.guidance || (rankingEnabled
            ? `Max nudge ${state.voiceProfile.ranking_cap_label || '+/-0.025'}`
            : 'Voice ranking is off');
    }
    if (guidanceEl) guidanceEl.textContent = state.voiceProfile.guidance || 'Enable the profile, then build it from clips with clear creator commentary.';
    if (toggleBtn) toggleBtn.textContent = enabled ? 'Disable Voice Profile' : 'Enable Voice Profile';
    if (rankingBtn) rankingBtn.textContent = rankingEnabled ? 'Stop Voice Ranking' : 'Use Voice In Ranking';
}

function formatDurationShort(seconds) {
    const value = Number(seconds);
    if (!Number.isFinite(value)) return 'Never';
    if (value <= 0) return '0s';
    if (value < 60) return `${Math.round(value)}s`;
    const mins = Math.round(value / 60);
    if (mins < 90) return `${mins}m`;
    const hours = Math.floor(mins / 60);
    const rem = mins % 60;
    return rem ? `${hours}h ${rem}m` : `${hours}h`;
}

function analysisStatusLabel(enabled, depthOverride, status = {}) {
    if (status && status.label) return status.label;
    if (depthOverride === true) return 'On by depth';
    if (depthOverride === false) return 'Inactive in Fast';
    return enabled ? 'On' : 'Off';
}

function renderLocalAnalysisStatus(analysis = {}, history = {}) {
    const depth = normalizeProcessingDepth(analysis.processing_depth || state.settings?.processing_depth || 'balanced');
    const depthEl = document.getElementById('analysis-depth');
    const depthLabel = depth === 'deep' ? 'Deep' : depth.charAt(0).toUpperCase() + depth.slice(1);
    if (depthEl) {
        depthEl.textContent = depthLabel;
        depthEl.classList.toggle('is-active', depth !== 'fast');
        depthEl.classList.toggle('is-idle', depth === 'fast');
    }
    const controls = analysis.depth_preset_controls || {};
    const featureStatuses = analysis.feature_statuses || {};
    const rows = [
        ['analysis-scene', featureStatuses.scene_detection?.effective, featureStatuses.scene_detection?.depth_override, 'Scene detection is controlled by Processing Depth.', featureStatuses.scene_detection],
        ['analysis-visual', analysis.visual_analysis_enabled, controls.visual_analysis, 'Visual frame analysis enriches local moment labels.', featureStatuses.visual_analysis],
        ['analysis-ai-labels', analysis.ai_moment_labels_enabled, controls.ai_moment_labels, 'AI labels explain moments and can improve title/metadata context.', featureStatuses.ai_moment_labels],
        ['analysis-ranking', analysis.moment_label_ranking_enabled, controls.moment_label_ranking, 'Moment-label ranking can nudge close-call candidate selection.', featureStatuses.moment_label_ranking],
        ['analysis-voice', analysis.voice_profile_ranking_enabled, controls.voice_profile_ranking, 'Voice ranking is opt-in and needs an enrolled local voice profile plus candidate voice scores.', featureStatuses.voice_profile_ranking],
    ];
    rows.forEach(([id, enabled, override, title, status]) => {
        const el = document.getElementById(id);
        if (!el) return;
        const inactive = status?.inactive_reason || override === false;
        el.textContent = analysisStatusLabel(Boolean(enabled), override, status);
        el.title = status?.reason || title;
        el.classList.toggle('is-idle', Boolean(inactive) || el.textContent === 'Off');
        el.classList.toggle('is-active', !inactive && !['Off', 'Inactive in Fast', 'Needs samples'].includes(el.textContent));
    });
    const capEl = document.getElementById('analysis-ranking-cap');
    const caps = analysis.selection_caps || {};
    if (capEl) capEl.textContent = formatLearningCap(caps.moment_label_ranking ?? 0.02);

    const runsEl = document.getElementById('processing-history-runs');
    const lastEl = document.getElementById('processing-history-last');
    const errorEl = document.getElementById('processing-history-error');
    const last = history.last_run || null;
    if (runsEl) runsEl.textContent = formatNumber(history.run_count || 0);
    if (lastEl) {
        lastEl.textContent = last ? formatDurationShort(last.elapsed_seconds) : 'Never';
        if (last?.finished_at_utc) lastEl.title = last.finished_at_utc;
        else lastEl.removeAttribute('title');
    }
    if (errorEl) {
        if (last && last.estimate_error_seconds !== null && last.estimate_error_seconds !== undefined) {
            const error = Number(last.estimate_error_seconds || 0);
            errorEl.textContent = `${error >= 0 ? '+' : ''}${formatDurationShort(Math.abs(error))}`;
            errorEl.title = error >= 0 ? 'Actual run took longer than estimated' : 'Actual run was faster than estimated';
        } else {
            errorEl.textContent = history.run_count ? 'Learning' : 'No data';
            errorEl.removeAttribute('title');
        }
    }
}

function recordPreviewFeedback(eventType) {
    if (state.previewClipIdx >= 0) {
        recordClipFeedback(state.previewClipIdx, eventType);
        return;
    }
    if (state.previewLibraryClip) recordLibraryFeedback(state.previewLibraryClip, eventType);
}

async function recordClipFeedback(clipIdx, eventType) {
    const clip = state.results[clipIdx];
    return recordFeedbackForClip(clip, clipIdx, eventType);
}

async function recordLibraryFeedback(clip, eventType) {
    return recordFeedbackForClip(clip, -1, eventType);
}

async function recordFeedbackForClip(clip, clipIdx, eventType) {
    if (!clip || !clip.clip_id) return toast('Clip identity is not ready yet', 'warning');
    const active = !_feedbackActive(clip, eventType);
    const payload = {
        ...clipIdentityFields(clip, clipIdx),
        index: clipIdx,
        event_type: eventType,
        active,
        reason: '',
    };
    if (active) {
        openFeedbackModal(payload, eventType);
        return;
    }
    return submitFeedbackPayload(payload, eventType, active);
}

async function submitFeedbackPayload(payload, eventType, active) {
    try {
        const r = await pywebview.api.record_feedback(payload);
        if (r.error) return toast(r.error, 'error');
        if (payload.clip_id) state.feedbackByClipId[payload.clip_id] = r.clip;
        renderAllFeedbackStates();
        renderPreviewFeedbackState();
        refreshDataPrivacyCard(false);
        toast(active ? `Marked ${eventType}` : `Removed ${eventType}`, 'success');
        maybeShowVoiceProfileNudge(r.voice_profile_nudge);
    } catch (e) {
        toast('Could not save feedback', 'error');
    }
}

function maybeShowVoiceProfileNudge(nudge) {
    if (!nudge || !nudge.show) return;
    toast(nudge.message || 'This clip can help build your local Creator Voice Profile.', 'info');
}

function feedbackEventLabel(eventType) {
    const labels = {
        like: 'Like',
        dislike: 'Dislike',
        favorite: 'Favorite',
    };
    return labels[eventType] || 'Feedback';
}

function openFeedbackModal(payload, eventType) {
    state.pendingFeedback = { payload, eventType };
    const title = document.getElementById('feedback-modal-title');
    const copy = document.getElementById('feedback-modal-copy');
    const note = document.getElementById('feedback-reason-note');
    const chips = document.getElementById('feedback-reason-chips');
    const label = feedbackEventLabel(eventType);
    if (title) title.textContent = `${label} Feedback`;
    if (copy) copy.textContent = eventType === 'dislike'
        ? 'Pick what was off so future clips can avoid the same pattern.'
        : 'Pick what worked so future clips can learn from this moment.';
    if (note) note.value = '';
    if (chips) {
        chips.innerHTML = '';
        (FEEDBACK_REASON_PRESETS[eventType] || FEEDBACK_REASON_PRESETS.like).forEach(reason => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'feedback-reason-chip';
            btn.textContent = reason;
            btn.addEventListener('click', () => btn.classList.toggle('active'));
            chips.appendChild(btn);
        });
    }
    showModal('feedback-modal');
    setTimeout(() => chips?.querySelector('.feedback-reason-chip')?.focus(), 30);
}

function closeFeedbackModal() {
    state.pendingFeedback = null;
    closeModal('feedback-modal');
}

async function submitFeedbackModal() {
    const pending = state.pendingFeedback;
    if (!pending || !pending.payload) {
        closeModal('feedback-modal');
        return;
    }
    const selected = Array.from(document.querySelectorAll('#feedback-reason-chips .feedback-reason-chip.active'))
        .map(btn => btn.textContent.trim())
        .filter(Boolean);
    const note = String(document.getElementById('feedback-reason-note')?.value || '').trim();
    pending.payload.reason = [...selected, note].filter(Boolean).join('; ');
    state.pendingFeedback = null;
    closeModal('feedback-modal');
    await submitFeedbackPayload(pending.payload, pending.eventType, true);
}

function setDataPrivacyTab(tabName = 'overview') {
    const selected = String(tabName || 'overview');
    document.querySelectorAll('[data-privacy-tab]').forEach(btn => {
        const active = btn.dataset.privacyTab === selected;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    document.querySelectorAll('[data-privacy-panel]').forEach(panel => {
        panel.classList.toggle('active', panel.dataset.privacyPanel === selected);
    });
}

async function openDataPrivacyModal() {
    setDataPrivacyTab('overview');
    await refreshDataPrivacyCard(false);
    showModal('data-privacy-modal');
}

async function refreshDataPrivacyCard(showToast = true) {
    if (!window.pywebview || !pywebview.api || !pywebview.api.get_data_privacy_summary) return;
    try {
        const r = await pywebview.api.get_data_privacy_summary();
        const p = r.personalization || {};
        const learning = r.learning || p.learning || {};
        const voice = r.voice_profile || {};
        const analysis = r.local_analysis || {};
        const processingHistory = r.processing_history || {};
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
        renderVoiceProfileStatus(voice);
        renderLocalAnalysisStatus(analysis, processingHistory);
        if (showToast) toast('Data summary refreshed', 'success');
    } catch (_) {
        if (showToast) toast('Could not refresh data summary', 'error');
    }
}

async function toggleVoiceProfileEnabled() {
    if (!window.pywebview || !pywebview.api || !pywebview.api.set_voice_profile_enabled) return;
    const nextEnabled = !Boolean(state.voiceProfile?.enabled);
    try {
        const r = await pywebview.api.set_voice_profile_enabled(nextEnabled);
        if (r.error) return toast(r.error, 'error');
        renderVoiceProfileStatus(r.voice_profile || {});
        toast(nextEnabled ? 'Voice profile enabled' : 'Voice profile disabled', 'success');
    } catch (_) {
        toast('Could not update voice profile', 'error');
    }
}

async function toggleVoiceProfileRanking() {
    if (!window.pywebview || !pywebview.api || !pywebview.api.set_voice_profile_ranking_enabled) return;
    const nextEnabled = !Boolean(state.voiceProfile?.ranking_enabled);
    try {
        const r = await pywebview.api.set_voice_profile_ranking_enabled(nextEnabled);
        if (r.error) return toast(r.error, 'error');
        state.settings = { ...(state.settings || {}), voice_profile_ranking: nextEnabled };
        const voice = r.voice_profile || {};
        renderVoiceProfileStatus(voice);
        if (nextEnabled && !voice.enrolled) {
            toast('Voice ranking saved. Build the profile before it can score eligible runs.', 'info');
        } else {
            toast(nextEnabled ? 'Voice ranking enabled' : 'Voice ranking disabled', 'success');
        }
    } catch (_) {
        toast('Could not update voice ranking', 'error');
    }
}

async function enrollVoiceProfile() {
    if (!window.pywebview || !pywebview.api || !pywebview.api.enroll_voice_profile_from_current_clips) return;
    try {
        toast('Building and enabling local voice profile...', 'info');
        const r = await pywebview.api.enroll_voice_profile_from_current_clips();
        renderVoiceProfileStatus(r.voice_profile || {});
        if (r.error) return toast(r.error, 'error');
        const count = r.enrolled_samples || 0;
        toast(`Voice profile updated from ${count} clip${count === 1 ? '' : 's'}`, 'success');
    } catch (_) {
        toast('Could not build voice profile', 'error');
    }
}

async function resetVoiceProfile() {
    if (!confirm('Reset the local creator voice profile? This removes the numeric profile and future voice-confidence scoring until you build it again.')) return;
    try {
        const r = await pywebview.api.reset_voice_profile();
        if (r.error) return toast(r.error, 'error');
        renderVoiceProfileStatus(r.voice_profile || {});
        toast(r.backup ? 'Voice profile reset and backup created' : 'Voice profile reset', 'success');
    } catch (_) {
        toast('Could not reset voice profile', 'error');
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

function setOutputFolderDisplay(path) {
    const input = document.getElementById('set-output-dir');
    if (!input) return;
    const value = String(path || '').trim();
    input.value = value || 'Default clips folder';
    input.title = value || 'Uses the app default clips folder';
}

async function selectOutputFolder() {
    try {
        const r = await pywebview.api.select_output_folder();
        if (!r || r.cancelled) return;
        if (r.error) return toast(r.error, 'error');
        state.settings = { ...(state.settings || {}), output_dir: r.path || '' };
        setOutputFolderDisplay(r.path || '');
        saveLocal('settings', state.settings);
        toast('Output folder updated', 'success');
        refreshUploadClips?.();
    } catch (_) {
        toast('Could not choose output folder', 'error');
    }
}

async function resetOutputFolder() {
    try {
        const r = await pywebview.api.reset_output_folder();
        if (r && r.error) return toast(r.error, 'error');
        state.settings = { ...(state.settings || {}) };
        delete state.settings.output_dir;
        setOutputFolderDisplay('');
        saveLocal('settings', state.settings);
        toast('Output folder reset', 'success');
        refreshUploadClips?.();
    } catch (_) {
        toast('Could not reset output folder', 'error');
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
    const modelBtn = document.getElementById('btn-ollama-model');
    const visionModelBtn = document.getElementById('btn-ollama-vision-model');
    const text = normalizedOllamaTextStatus(status);
    const vision = normalizedOllamaVisionStatus(status);
    const running = text.running;
    const modelReady = text.ready;
    const model = text.model;
    const visionModel = vision.preferredModel;
    const visionReady = vision.ready;

    if (locationBtn) {
        locationBtn.classList.remove('hidden');
        if (status?.install_path) {
            locationBtn.disabled = false;
            locationBtn.textContent = 'Open Ollama Folder';
            locationBtn.title = 'Open the local Ollama install folder';
        } else if (running) {
            locationBtn.disabled = false;
            locationBtn.textContent = 'Open Ollama Download';
            locationBtn.title = 'Ollama Running; install folder was not found on PATH';
        } else {
            locationBtn.disabled = false;
            locationBtn.textContent = 'Install Ollama';
            locationBtn.title = 'Open the official Ollama download page';
        }
    }

    if (modelBtn) {
        if (_ollamaModelDownloadActive) {
            modelBtn.classList.remove('hidden');
            modelBtn.disabled = true;
            modelBtn.textContent = 'Downloading Model...';
            modelBtn.title = `Downloading ${model} with Ollama; keep Ollama running`;
        } else if (!running) {
            modelBtn.classList.add('hidden');
            modelBtn.disabled = true;
        } else {
            modelBtn.classList.remove('hidden');
            modelBtn.disabled = !running;
            modelBtn.textContent = modelReady ? 'Text Model Ready' : `Download ${model}`;
            modelBtn.title = modelReady
                ? `${model} is installed; click to re-check`
                : `Download ${model} for AI titles, AI moment labels, and Deep Analysis AI ranking`;
        }
    }

    if (visionModelBtn) {
        if (_ollamaVisionModelDownloadActive) {
            visionModelBtn.classList.remove('hidden');
            visionModelBtn.disabled = true;
            visionModelBtn.textContent = 'Downloading Vision...';
            visionModelBtn.title = `Downloading ${visionModel} with Ollama; keep Ollama running`;
        } else if (!running) {
            visionModelBtn.classList.add('hidden');
            visionModelBtn.disabled = true;
        } else {
            visionModelBtn.classList.remove('hidden');
            visionModelBtn.disabled = !running;
            visionModelBtn.textContent = visionReady ? 'Vision Model Ready' : `Download ${visionModel}`;
            visionModelBtn.title = visionReady
                ? `${vision.model || visionModel} is installed for Deep Analysis frame inspection`
                : `Download ${visionModel} for Deep Analysis frame inspection`;
        }
    }
}

function normalizedOllamaTextStatus(status = _lastOllamaStatus) {
    const textModel = status?.text_model || {};
    return {
        running: !!(status && status.running),
        ready: !!(textModel.model_ready || status?.model_ready),
        model: textModel.model || status?.model || 'qwen3.5:4b',
    };
}

function normalizedOllamaVisionStatus(status = _lastOllamaStatus) {
    const vision = status?.vision || {};
    const preferredModel = vision.preferred_model || vision.model || 'qwen3-vl:latest';
    return {
        running: !!(status && status.running),
        ready: !!vision.model_ready,
        model: vision.model || preferredModel,
        preferredModel,
    };
}

async function refreshOllamaStatus() {
    const pill = document.getElementById('ollama-status');
    if (!pill || !window.pywebview || !pywebview.api) return;
    const label = pill.querySelector('.ollama-label');
    const settingsDot = document.getElementById('settings-ollama-dot');
    const settingsState = document.getElementById('settings-ollama-state');
    const settingsDetail = document.getElementById('settings-ollama-detail');
    const textModelEl = document.getElementById('settings-ollama-text-model');
    const visionModelEl = document.getElementById('settings-ollama-vision-model');
    pill.classList.remove('ready', 'partial', 'offline', 'error');
    pill.classList.add('checking');
    if (label) label.textContent = 'Ollama...';
    if (settingsState) settingsState.textContent = 'Checking';
    if (settingsDetail) settingsDetail.textContent = 'Ollama status';
    if (textModelEl) textModelEl.textContent = 'Checking';
    if (visionModelEl) visionModelEl.textContent = 'Checking';
    if (settingsDot) settingsDot.className = 'ollama-settings-dot checking';
    try {
        const status = await pywebview.api.get_ollama_status();
        _lastOllamaStatus = status;
        updateOllamaActionButtons(status);
        pill.classList.remove('checking');
        const textModel = status.text_model || {};
        const vision = status.vision || {};
        const model = textModel.model || status.model || 'qwen3.5:4b';
        const textReady = !!(textModel.model_ready || status.model_ready);
        const visionReady = !!vision.model_ready;
        const visionName = vision.model || vision.preferred_model || 'qwen3-vl:latest';
        const version = status.version ? ` ${status.version}` : '';
        if (textModelEl) textModelEl.textContent = `${model}: ${textReady ? 'ready' : 'missing'}`;
        if (visionModelEl) visionModelEl.textContent = `${visionName}: ${visionReady ? 'ready' : 'missing'}`;
        if (textReady && visionReady) {
            pill.classList.add('ready');
            if (label) label.textContent = 'AI full';
            pill.title = `Ollama${version}: text ${model} ready; vision ${vision.model || visionName} ready`;
            if (settingsState) settingsState.textContent = 'Ready';
            if (settingsDetail) settingsDetail.textContent = `Text + vision ready${version ? ` • ${version}` : ''}`;
            if (settingsDot) settingsDot.className = 'ollama-settings-dot ready';
        } else if (status.running && textReady) {
            pill.classList.add('partial');
            if (label) label.textContent = 'Text AI';
            pill.title = `Ollama${version}: text ${model} ready; vision ${visionName} missing`;
            if (settingsState) settingsState.textContent = 'Text Ready';
            if (settingsDetail) settingsDetail.textContent = `Vision model missing for Deep Analysis${version ? ` • ${version}` : ''}`;
            if (settingsDot) settingsDot.className = 'ollama-settings-dot partial';
        } else if (status.running && visionReady) {
            pill.classList.add('partial');
            if (label) label.textContent = 'Vision AI';
            pill.title = `Ollama${version}: vision ${vision.model || visionName} ready; text ${model} missing`;
            if (settingsState) settingsState.textContent = 'Vision Ready';
            if (settingsDetail) settingsDetail.textContent = `Text model missing for titles, descriptions, and labels${version ? ` • ${version}` : ''}`;
            if (settingsDot) settingsDot.className = 'ollama-settings-dot partial';
        } else if (status.running) {
            pill.classList.add('partial');
            if (label) label.textContent = 'Models missing';
            pill.title = `Ollama${version} is running, but ${model} is not installed yet`;
            if (settingsState) settingsState.textContent = 'Models Missing';
            if (settingsDetail) settingsDetail.textContent = `${model}${version ? ` • ${version}` : ''}`;
            if (settingsDot) settingsDot.className = 'ollama-settings-dot partial';
        } else {
            pill.classList.add('offline');
            if (label) label.textContent = 'AI off';
            pill.title = 'Ollama is not running; local AI labels and titles will use fallback paths';
            if (settingsState) settingsState.textContent = 'Not Running';
            if (settingsDetail) settingsDetail.textContent = 'Ollama not running; local fallback active';
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
        if (textModelEl) textModelEl.textContent = 'Unknown';
        if (visionModelEl) visionModelEl.textContent = 'Unknown';
        if (settingsDot) settingsDot.className = 'ollama-settings-dot error';
    }
}

async function handleOllamaLocationAction() {
    if (_lastOllamaStatus && _lastOllamaStatus.install_path) {
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

async function downloadOllamaModel() {
    if (_ollamaModelDownloadActive) return toast('Ollama model download is already running', 'info');
    try {
        const text = normalizedOllamaTextStatus();
        if (text.ready) {
            toast(`${text.model} is already ready for local AI features`, 'success');
            return refreshOllamaStatus();
        }
        if (_lastOllamaStatus && !text.running) {
            toast('Start Ollama before downloading the local AI model', 'warning');
            return refreshOllamaStatus();
        }
        const model = text.model;
        if (!confirm(`Download ${model} with Ollama?\n\nThis can take several minutes and uses your network connection. Keep Ollama running until the app says the model is ready.`)) {
            return;
        }
        _ollamaModelDownloadActive = true;
        updateOllamaActionButtons(_lastOllamaStatus);
        const settingsDetail = document.getElementById('settings-ollama-detail');
        if (settingsDetail) settingsDetail.textContent = `Downloading ${model}...`;
        toast(`Downloading ${model} with Ollama...`, 'info');
        const r = await pywebview.api.ensure_ollama_model();
        if (r && r.error) return toast(r.error, 'error');
        if (r.ready) {
            toast(`${r.model} is ready for local AI features`, 'success');
        } else {
            toast('Ollama is not running yet', 'warning');
        }
        await refreshOllamaStatus();
    } catch (_) {
        toast('Could not download Ollama AI model', 'error');
    } finally {
        _ollamaModelDownloadActive = false;
        updateOllamaActionButtons(_lastOllamaStatus);
    }
}

async function downloadOllamaVisionModel() {
    if (_ollamaVisionModelDownloadActive) return toast('Visual model download is already running', 'info');
    try {
        const vision = normalizedOllamaVisionStatus();
        if (vision.ready) {
            toast(`${vision.model} is already ready for Deep Analysis`, 'success');
            return refreshOllamaStatus();
        }
        if (_lastOllamaStatus && !vision.running) {
            toast('Start Ollama before downloading the local vision model', 'warning');
            return refreshOllamaStatus();
        }
        const model = vision.preferredModel;
        if (!confirm(`Download ${model} with Ollama?\n\nThis is used for Deep Analysis frame inspection and may be several GB.`)) {
            return;
        }
        _ollamaVisionModelDownloadActive = true;
        updateOllamaActionButtons(_lastOllamaStatus);
        toast(`Downloading ${model} with Ollama...`, 'info');
        const r = await pywebview.api.ensure_ollama_vision_model();
        if (r && r.error) return toast(r.error, 'error');
        if (r.ready) {
            toast(`${r.model} is ready for Deep Analysis vision`, 'success');
        } else {
            toast('Ollama is not running yet', 'warning');
        }
        await refreshOllamaStatus();
    } catch (_) {
        toast('Could not download visual model', 'error');
    } finally {
        _ollamaVisionModelDownloadActive = false;
        updateOllamaActionButtons(_lastOllamaStatus);
    }
}

/* ── Thumbnail generator (queued + lazy) ─────────────────────────────── */

const _thumbCache = {};   // url → dataURL cache
const _thumbQueue = [];   // pending thumbnail tasks
const _thumbFailures = new Set();
const _activeThumbVideos = new Set();
let _thumbActive = 0;
const _THUMB_CONCURRENCY = 2;  // max simultaneous video decodes

function generateThumbnail(videoUrl, targetEl, seekTime = 1.0) {
    if (!targetEl) return;
    // Check cache first — instant
    if (_thumbCache[videoUrl]) {
        _applyThumb(targetEl, _thumbCache[videoUrl]);
        return;
    }
    if (_thumbFailures.has(videoUrl)) {
        targetEl.classList.add('thumb-failed');
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
    const activeThumb = { video: vid, url: videoUrl, cleanup: null };
    let cleaned = false;
    let seekPoints = [];
    let seekIndex = 0;
    let bestFrame = null;
    let seekTimer = null;

    const clearSeekTimer = () => {
        if (seekTimer) {
            clearTimeout(seekTimer);
            seekTimer = null;
        }
    };

    const cleanup = () => {
        if (cleaned) return;
        cleaned = true;
        clearSeekTimer();
        vid.src = '';
        vid.load();
        _activeThumbVideos.delete(activeThumb);
        _thumbActive = Math.max(0, _thumbActive - 1);
        _processThumbQueue();
    };
    activeThumb.cleanup = cleanup;
    _activeThumbVideos.add(activeThumb);

    const captureFrame = () => {
        try {
            const canvas = document.createElement('canvas');
            // Use smaller size for thumbnails — saves memory
            const scale = Math.min(1, 320 / (vid.videoWidth || 320));
            canvas.width = Math.round((vid.videoWidth || 320) * scale);
            canvas.height = Math.round((vid.videoHeight || 180) * scale);
            const ctx = canvas.getContext('2d');
            ctx.drawImage(vid, 0, 0, canvas.width, canvas.height);
            const dataUrl = canvas.toDataURL('image/jpeg', 0.6);
            const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
            let total = 0;
            let totalSq = 0;
            let samples = 0;
            const stride = Math.max(4, Math.floor((canvas.width * canvas.height) / 1800)) * 4;
            for (let i = 0; i < data.length; i += stride) {
                const luma = 0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2];
                total += luma;
                totalSq += luma * luma;
                samples++;
            }
            const mean = samples ? total / samples : 0;
            const variance = samples ? Math.max(0, totalSq / samples - mean * mean) : 0;
            const contrast = Math.sqrt(variance);
            return {
                dataUrl,
                score: mean + contrast * 0.55,
                black: mean < 18 && contrast < 18,
            };
        } catch (e) {
            return null;
        }
    };

    const applyFrame = (frame) => {
        _thumbCache[videoUrl] = frame.dataUrl;
        if (targetEl.isConnected) _applyThumb(targetEl, frame.dataUrl);
        cleanup();
    };

    const seekNext = () => {
        if (cleaned) return;
        if (seekIndex >= seekPoints.length) {
            if (bestFrame) applyFrame(bestFrame);
            else {
                _thumbFailures.add(videoUrl);
                if (targetEl.isConnected) targetEl.classList.add('thumb-failed');
                cleanup();
            }
            return;
        }
        const next = seekPoints[seekIndex++];
        try {
            vid.currentTime = next;
            clearSeekTimer();
            seekTimer = setTimeout(() => {
                if (!cleaned) seekNext();
            }, 2500);
        } catch (_) {
            seekNext();
        }
    };

    vid.addEventListener('loadedmetadata', () => {
        const duration = Number.isFinite(vid.duration) ? Math.max(0, vid.duration) : 0;
        const rawPoints = [
            seekTime,
            duration * 0.12,
            duration * 0.28,
            duration * 0.5,
            duration * 0.72,
            Math.max(0, duration - 1.5),
        ];
        seekPoints = rawPoints
            .filter(value => Number.isFinite(value) && value >= 0)
            .map(value => Math.min(Math.max(0, value), Math.max(0, duration - 0.05)))
            .filter((value, index, arr) => arr.findIndex(other => Math.abs(other - value) < 0.25) === index)
            .slice(0, 6);
        if (!seekPoints.length) seekPoints = [0];
        seekNext();
    });

    vid.addEventListener('seeked', () => {
        clearSeekTimer();
        const frame = captureFrame();
        if (frame) {
            if (!bestFrame || frame.score > bestFrame.score) bestFrame = frame;
            if (!frame.black || seekIndex >= seekPoints.length) {
                applyFrame(frame.black && bestFrame ? bestFrame : frame);
                return;
            }
        }
        seekNext();
    });

    vid.addEventListener('error', () => {
        clearSeekTimer();
        _thumbFailures.add(videoUrl);
        if (targetEl.isConnected) targetEl.classList.add('thumb-failed');
        cleanup();
    });
    // Timeout safety — don't block queue forever
    setTimeout(() => {
        if (!cleaned && vid.readyState < 2) {
            _thumbFailures.add(videoUrl);
            if (targetEl.isConnected) targetEl.classList.add('thumb-failed');
            cleanup();
        }
    }, 8000);

    vid.src = videoUrl;
}

function _applyThumb(el, dataUrl) {
    if (!el) return;
    el.style.backgroundImage = `url(${dataUrl})`;
    el.style.backgroundSize = 'cover';
    el.style.backgroundPosition = 'center';
    el.classList.remove('thumb-failed');
    el.classList.add('thumb-ready');
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

        await refreshFfmpegDependencies({ quiet: true, showModalOnMissing: true });
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
        if (Array.isArray(persisted.scheduled)) {
            state.scheduled = normalizeScheduledMetadata(persisted.scheduled);
            if (state.scheduled.length) persistSchedule();
            else clearStaleScheduleUi();
        }
        state.uploadHistory = Array.isArray(persisted.upload_history) ? persisted.upload_history : [];
        refreshSubtitlePreviewSnapshot(false);
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
        } else {
            state.ytConnected = false;
            updateYtUI(false);
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

document.querySelectorAll('input[name="wizard-audio-mode"]').forEach(input => {
    input.addEventListener('change', updateWizardAudioModeState);
});
document.getElementById('wizard-audio-stream-select')?.addEventListener('change', updateWizardAudioModeState);
document.getElementById('wizard-mixed-audio-subtitle-policy')?.addEventListener('change', () => {
    state.wizardSavedAudioSource = wizardAudioSourceSettings();
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
    if (!(await ensureFfmpegReady('generate clips'))) return;

    const urlInput = document.getElementById('url-input').value.trim();
    pruneCompletedBatchItemsForNewRun();

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
    const saved = loadLocal('wizard', {});
    state.generationMode = normalizeGenerationMode(saved.generationMode || state.settings.generation_mode || state.generationMode);
    updateGenerationModeUi();
    syncWizardModeUi();
    const savedDepth = normalizeProcessingDepth(saved.processingDepth || state.settings.processing_depth);
    selectProcessingDepth(savedDepth);
    setSelect(
        'wizard-detection-preference',
        normalizeDetectionPreference(getVal('set-detection-preference') || state.settings.detection_preference)
    );
    const autoClips = document.getElementById('set-auto-clips')?.checked || state.settings.num_clips === 'auto';
    setWizardClipCount(autoClips ? 'auto' : String(getVal('set-num-clips') || state.settings.num_clips || 'auto'));
    const clipDuration = clampClipDuration(getVal('set-clip-duration') || state.settings.clip_duration || 30);
    const minGap = getVal('set-min-gap') || state.settings.min_gap || 15;
    setVal('wizard-clip-duration', clipDuration);
    setVal('wizard-min-gap', minGap);
    setVal('wizard-game-title-hint', '');
    const savedMontage = saved.montage || state.settings.montage || {};
    selectMontageTemplate(savedMontage.template || state.wizardMontageTemplate || 'panic');
    setSelect('wizard-montage-duration', normalizeMontageDuration(savedMontage.target_duration || 60));
    setSelect('wizard-montage-count', normalizeMontageCount(savedMontage.count || 1));
    setVal('wizard-montage-prompt', savedMontage.prompt || '');
    const clipDurationLabel = document.getElementById('val-wizard-clip-duration');
    const minGapLabel = document.getElementById('val-wizard-min-gap');
    if (clipDurationLabel) clipDurationLabel.textContent = `${clipDuration}s`;
    if (minGapLabel) minGapLabel.textContent = `${minGap}s`;
    syncWizardEstimate();
    wizardNext(1);
    loadEffectsGrid();
    loadMusicList();

    // Restore previous wizard settings
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
    restoreWizardAudioSettings(saved.audioSource || state.settings.audio_source || {});
    loadWizardAudioSources();

    showModal('style-picker-modal');
}

/* ── Wizard Navigation ────────────────────────────────────────────────── */

function selectGenerationMode(mode) {
    state.generationMode = normalizeGenerationMode(mode);
    const saved = loadLocal('wizard', {});
    saveLocal('wizard', { ...saved, generationMode: state.generationMode });
    updateGenerationModeUi();
    syncWizardModeUi();
    syncWizardEstimate();
}

function updateGenerationModeUi() {
    const mode = normalizeGenerationMode(state.generationMode);
    state.generationMode = mode;
    document.querySelectorAll('.generation-mode-card').forEach(card => {
        const active = card.dataset.mode === mode;
        card.classList.toggle('active', active);
        card.setAttribute('aria-checked', active ? 'true' : 'false');
    });
    const generateLabel = document.getElementById('generate-button-label');
    if (generateLabel) generateLabel.textContent = mode === 'montage' ? 'Create Montage' : 'Generate Clips';
}

function syncWizardModeUi() {
    const mode = normalizeGenerationMode(state.generationMode);
    const montage = mode === 'montage';
    document.getElementById('wizard-clip-plan-panel')?.classList.toggle('hidden', montage);
    document.getElementById('wizard-montage-plan-panel')?.classList.toggle('hidden', !montage);
    setNodeText('wizard-step-2-label', montage ? 'Montage' : 'Detection');
    setNodeText('wizard-step-2-title', montage ? 'Montage Plan' : 'Detection');
    setNodeText(
        'wizard-step-2-hint',
        montage
            ? 'Choose the kind of stitched story ViriaRevive should build from the source.'
            : 'Choose how deeply ViriaRevive should inspect the video before rendering final clips.'
    );
    setNodeText('wizard-step-1-next-label', montage ? 'Next: Montage' : 'Next: Detection');
    setNodeText('wizard-generate-label', montage ? 'Create Montage' : 'Generate Clips');
}

function selectMontageTemplate(template) {
    const normalized = normalizeMontageTemplate(template);
    state.wizardMontageTemplate = normalized;
    document.querySelectorAll('.montage-template-card').forEach(card => {
        card.classList.toggle('active', card.dataset.template === normalized);
    });
    syncWizardEstimate();
}

function selectProcessingDepth(depth) {
    const normalized = normalizeProcessingDepth(depth);
    state.wizardProcessingDepth = normalized;
    document.querySelectorAll('.depth-preset').forEach(card => {
        card.classList.toggle('active', card.dataset.depth === normalized);
    });
    syncWizardEstimate();
}

function syncWizardEstimate() {
    syncWizardModeUi();
    const mode = normalizeGenerationMode(state.generationMode);
    if (mode === 'montage') {
        const montageEl = document.getElementById('wizard-montage-estimate');
        if (!montageEl) return;
        const target = normalizeMontageDuration(getVal('wizard-montage-duration'));
        const count = normalizeMontageCount(getVal('wizard-montage-count'));
        const template = normalizeMontageTemplate(state.wizardMontageTemplate).replace(/-/g, ' ');
        montageEl.textContent = `Montage target: ${count} ${count === 1 ? 'montage' : 'montages'} at about ${target}s each. ViriaRevive will gather distinct ${template} beats and may return fewer if the source does not have enough strong story threads.`;
        return;
    }
    const el = document.getElementById('wizard-estimate');
    if (!el) return;
    syncWizardDetectionPreferenceMode();
    const depth = normalizeProcessingDepth(state.wizardProcessingDepth);
    const duration = clampClipDuration(getVal('wizard-clip-duration') || '30');
    const clips = getVal('wizard-num-clips') || 'auto';
    const preference = effectiveWizardDetectionPreference();
    const label = clips === 'auto'
        ? `auto clip count, ${preference === 'quantity' ? 'quantity' : preference === 'quality' ? 'quality' : 'auto'} preference`
        : `${clips} clips, quality selection`;
    const base = {
        fast: 'Fast skips or samples heavier checks so long recordings finish sooner.',
        balanced: 'Balanced is the default: good ranking with sampled scene checks on long videos.',
        deep: 'Deep Analysis inspects more moments and may take a while on 2-3 hour recordings.',
    }[depth] || '';
    el.textContent = `${base} Current target: ${label}, about ${duration}s each.`;
}

function effectiveWizardDetectionPreference() {
    const clips = getVal('wizard-num-clips') || 'auto';
    if (clips !== 'auto') return 'quality';
    return normalizeDetectionPreference(getVal('wizard-detection-preference'));
}

function syncWizardDetectionPreferenceMode() {
    const clips = getVal('wizard-num-clips') || 'auto';
    const fixed = clips !== 'auto';
    const select = document.getElementById('wizard-detection-preference');
    const fixedValue = document.getElementById('wizard-detection-preference-fixed');
    if (select) {
        select.classList.toggle('hidden', fixed);
        select.disabled = fixed;
        if (fixed) select.value = 'quality';
    }
    if (fixedValue) {
        fixedValue.classList.toggle('hidden', !fixed);
        fixedValue.textContent = 'Quality';
    }
}

function setWizardClipCount(value) {
    const select = document.getElementById('wizard-num-clips');
    if (!select) return;
    const normalized = String(value || 'auto');
    if (![...select.options].some(opt => opt.value === normalized)) {
        const opt = document.createElement('option');
        opt.value = normalized;
        opt.textContent = normalized === 'auto' ? 'Auto' : normalized;
        select.appendChild(opt);
    }
    select.value = normalized;
    syncWizardDetectionPreferenceMode();
}

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

function firstQueuedSourceForAudioProbe() {
    const active = state.batchQueue.find(q => q.status === 'pending' || q.status === 'active') || state.batchQueue[0];
    return active?.url || document.getElementById('url-input')?.value.trim().split('\n').find(Boolean) || '';
}

function restoreWizardAudioSettings(audioSource = {}) {
    state.wizardSavedAudioSource = audioSource || {};
    const mode = audioSource.mode === 'stream' ? 'stream' : 'auto';
    const stream = audioSource.stream ?? '';
    const guard = audioSource.commentary_guard !== false;
    const policy = ['creator', 'all', 'game'].includes(audioSource.subtitle_policy)
        ? audioSource.subtitle_policy
        : 'creator';
    document.querySelectorAll('input[name="wizard-audio-mode"]').forEach(input => {
        input.checked = input.value === mode;
    });
    const select = document.getElementById('wizard-audio-stream-select');
    if (select && stream !== '') select.value = String(stream);
    const guardEl = document.getElementById('wizard-mixed-audio-guard');
    if (guardEl) guardEl.checked = guard;
    const policyEl = document.getElementById('wizard-mixed-audio-subtitle-policy');
    if (policyEl) policyEl.value = policy;
    updateWizardAudioModeState();
}

function updateWizardAudioModeState() {
    const mode = document.querySelector('input[name="wizard-audio-mode"]:checked')?.value || 'auto';
    const select = document.getElementById('wizard-audio-stream-select');
    const streamOption = document.getElementById('wizard-audio-stream-option');
    const hasStreams = !!select && select.options.length > 0 && !!select.value;
    if (select) select.disabled = mode !== 'stream' || !hasStreams;
    if (streamOption) streamOption.classList.toggle('disabled', !hasStreams);
    if (mode === 'stream' && !hasStreams) {
        document.querySelector('input[name="wizard-audio-mode"][value="auto"]').checked = true;
        if (select) select.disabled = true;
    }
}

function renderWizardAudioSources(probe) {
    state.wizardAudioProbe = probe || null;
    const status = document.getElementById('wizard-audio-source-status');
    const select = document.getElementById('wizard-audio-stream-select');
    const streamOption = document.getElementById('wizard-audio-stream-option');
    if (!status || !select) return;

    const streams = probe?.streams || [];
    select.innerHTML = '';
    const multiSourceBatch = currentBatchSourceCount() > 1;
    if (streams.length) {
        streams.forEach(stream => {
            const opt = document.createElement('option');
            opt.value = String(stream.ordinal);
            const role = stream.likely_role && stream.likely_role !== 'unknown' ? ` - ${stream.likely_role}` : '';
            const details = [stream.codec, stream.layout || (stream.channels ? `${stream.channels}ch` : '')]
                .filter(Boolean).join(', ');
            const title = stream.title || `Track ${stream.ordinal + 1}`;
            const roleLabel = stream.likely_role && stream.likely_role !== 'unknown'
                ? stream.likely_role
                : 'audio';
            const shortTitle = title.length > 26 ? `${title.slice(0, 23)}...` : title;
            opt.textContent = `Track ${stream.ordinal + 1} - ${roleLabel} - ${shortTitle}`;
            opt.title = `Track ${stream.ordinal + 1}: ${title}${role}${details ? ` (${details})` : ''}`;
            select.appendChild(opt);
        });
        const recommended = probe.recommended_stream;
        const savedStream = state.wizardSavedAudioSource?.stream;
        if (savedStream !== null && savedStream !== undefined && [...select.options].some(opt => opt.value === String(savedStream))) {
            select.value = String(savedStream);
        } else if (recommended !== null && recommended !== undefined) {
            select.value = String(recommended);
        }
    } else {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = probe?.mode === 'deferred'
            ? 'Tracks will appear after download'
            : 'No separate tracks detected';
        select.appendChild(opt);
    }

    const mode = probe?.mode || 'empty';
    if (multiSourceBatch) {
        status.textContent = 'Multiple sources are queued. Auto will inspect each video separately during processing.';
    } else if (mode === 'multi') {
        status.textContent = `${probe.message}. Auto will sample tracks, or choose the mic/commentary track here.`;
    } else if (mode === 'single') {
        status.textContent = 'One mixed audio track found. Auto will transcribe it and use your mixed-track subtitle preference below.';
    } else if (mode === 'deferred') {
        status.textContent = 'Online videos are checked after download. Auto is recommended unless you know the track layout will match your local recordings.';
    } else {
        status.textContent = probe?.message || 'Audio source details are not available yet.';
        if (mode === 'error' && probe?.diagnostics?.status === 'timeout') {
            status.textContent = 'Audio inspection timed out. Auto can still try again during processing.';
        }
    }

    const canSelectStream = streams.length > 1 && !multiSourceBatch;
    if (streamOption) streamOption.classList.toggle('disabled', !canSelectStream);
    if (!canSelectStream) {
        document.querySelector('input[name="wizard-audio-mode"][value="auto"]').checked = true;
    }
    updateWizardAudioModeState();
}

async function loadWizardAudioSources() {
    const status = document.getElementById('wizard-audio-source-status');
    if (status) status.textContent = 'Checking audio sources...';
    const source = firstQueuedSourceForAudioProbe();
    if (!window.pywebview || !pywebview.api || !pywebview.api.probe_audio_sources) {
        renderWizardAudioSources({ mode: 'deferred', streams: [], message: 'Audio tracks will be checked during generation' });
        return;
    }
    try {
        const probe = await pywebview.api.probe_audio_sources(source);
        renderWizardAudioSources(probe);
    } catch (_) {
        renderWizardAudioSources({ mode: 'error', streams: [], message: 'Could not inspect audio sources' });
    }
}

function wizardAudioSourceSettings() {
    const policyValue = document.getElementById('wizard-mixed-audio-subtitle-policy')?.value || 'creator';
    const subtitlePolicy = ['creator', 'all', 'game'].includes(policyValue) ? policyValue : 'creator';
    if (currentBatchSourceCount() > 1) {
        return {
            mode: 'auto',
            stream: null,
            commentary_guard: document.getElementById('wizard-mixed-audio-guard')?.checked !== false,
            subtitle_policy: subtitlePolicy,
        };
    }
    const mode = document.querySelector('input[name="wizard-audio-mode"]:checked')?.value || 'auto';
    const select = document.getElementById('wizard-audio-stream-select');
    const streamValue = select && select.value !== '' ? parseInt(select.value, 10) : null;
    const stream = Number.isInteger(streamValue) ? streamValue : null;
    const finalMode = mode === 'stream' && stream !== null ? 'stream' : 'auto';
    return {
        mode: finalMode,
        stream: finalMode === 'stream' ? stream : null,
        commentary_guard: document.getElementById('wizard-mixed-audio-guard')?.checked !== false,
        subtitle_policy: subtitlePolicy,
    };
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
document.getElementById('wizard-num-clips')?.addEventListener('change', syncWizardEstimate);
document.getElementById('wizard-detection-preference')?.addEventListener('change', syncWizardEstimate);

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
    const pickedGenerationMode = normalizeGenerationMode(state.generationMode);
    const pickedStyle = document.querySelector('input[name="picker-style"]:checked')?.value || 'tiktok';
    const pickedProcessingDepth = normalizeProcessingDepth(state.wizardProcessingDepth);
    const pickedNumClips = getVal('wizard-num-clips') || 'auto';
    const pickedDetectionPreference = pickedNumClips === 'auto'
        ? normalizeDetectionPreference(getVal('wizard-detection-preference'))
        : 'quality';
    const pickedGameTitleHint = normalizeGameTitleHint(getVal('wizard-game-title-hint'));
    const pickedClipDuration = clampClipDuration(getVal('wizard-clip-duration') || '30');
    const pickedMinGap = parseInt(getVal('wizard-min-gap') || '15', 10);
    const pickedMontage = montageSettingsFromWizard();

    // Sync subtitle style back to settings
    document.querySelectorAll('.style-option').forEach(opt => {
        opt.classList.toggle('active', opt.dataset.style === pickedStyle);
        const radio = opt.querySelector('input[type="radio"]');
        if (radio) radio.checked = opt.dataset.style === pickedStyle;
    });
    const autoClipsEl = document.getElementById('set-auto-clips');
    if (autoClipsEl) autoClipsEl.checked = pickedNumClips === 'auto';
    if (pickedNumClips !== 'auto') setSlider('set-num-clips', pickedNumClips);
    setSlider('set-clip-duration', pickedClipDuration);
    setSlider('set-min-gap', pickedMinGap);
    setSelect('set-detection-preference', pickedDetectionPreference);
    updateSubtitlePlacementPreview();

    // Save wizard choices for next time
    const selectedEffect = document.querySelector('.effect-card.active')?.dataset.effect || 'none';
    const musicEnabled = document.getElementById('wizard-music-enabled')?.checked || false;
    const selectedTrack = document.querySelector('.music-track.active')?.dataset.filename || null;
    const musicVolume = parseInt(document.getElementById('wizard-music-volume')?.value || '12');
    const audioSource = wizardAudioSourceSettings();

    const trimValues = getMusicTrimValues();
    saveLocal('wizard', {
        effect: selectedEffect,
        musicEnabled: musicEnabled,
        musicFile: selectedTrack,
        musicVolume: musicVolume,
        musicTrimStart: trimValues ? trimValues.start : null,
        musicTrimEnd: trimValues ? trimValues.end : null,
        audioSource: audioSource,
        processingDepth: pickedProcessingDepth,
        generationMode: pickedGenerationMode,
        montage: pickedMontage,
    });

    closeModal('style-picker-modal');
    // Snapshot settings for the entire batch
    const settings = gatherSettings();
    settings.generation_mode = pickedGenerationMode;
    settings.processing_depth = pickedProcessingDepth;
    settings.detection_preference = pickedDetectionPreference;
    settings.game_title_hint = pickedGameTitleHint;
    settings.num_clips = pickedNumClips === 'auto' ? 'auto' : parseInt(pickedNumClips, 10);
    settings.clip_duration = pickedClipDuration;
    settings.min_gap = pickedMinGap;
    settings.video_effect = selectedEffect;
    settings.music_file = musicEnabled && selectedTrack ? selectedTrack : null;
    settings.music_volume = musicVolume / 100;
    settings.music_start = trimValues ? trimValues.start : 0;
    settings.music_end = trimValues ? trimValues.end : 0;
    settings.montage = pickedMontage;
    settings.audio_source = {
        mode: 'auto',
        stream: null,
        commentary_guard: audioSource.commentary_guard,
        subtitle_policy: audioSource.subtitle_policy || 'creator',
    };
    state.batchSettings = settings;
    const perItemAudioSource = currentBatchSourceCount() === 1
        ? audioSource
        : {
            mode: 'auto',
            stream: null,
            commentary_guard: audioSource.commentary_guard,
            subtitle_policy: audioSource.subtitle_policy || 'creator',
    };
    state.batchQueue.forEach(item => {
        const hasAudioOverride = item.audioSourceOverride === true || item.audio_source_override === true;
        item.audioSource = hasAudioOverride && item.audioSource
            ? { ...item.audioSource }
            : { ...(perItemAudioSource || {}) };
        item.subtitleStyle = settings.subtitle_style || 'tiktok';
        item.generationMode = settings.generation_mode || 'clips';
        item.montage = { ...(settings.montage || {}) };
        item.settings = {
            ...(item.settings || {}),
            audio_source: item.audioSource,
            subtitle_style: item.subtitleStyle,
            generation_mode: item.generationMode,
            montage: item.montage,
            game_title_hint: settings.game_title_hint || '',
        };
    });

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
    const cancelBtn = document.getElementById('btn-cancel');
    if (cancelBtn) {
        cancelBtn.disabled = true;
        cancelBtn.textContent = 'Cancelling...';
    }
    setProgress(state.progress?.percent || 0, 'Cancelling current run...', true);
    try { await pywebview.api.cancel_processing(); } catch (_) {}
    // Cancel stops the current item; clear the rest of the queue
    state.batchQueue.forEach(q => { if (q.status === 'pending') q.status = 'cancelled'; });
    renderBatchQueue();
    renderBatchProgress();
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
    const display = sourceDisplayLabel(url);
    const label = display.length > 60 ? display.slice(0, 57) + '...' : display;
    state.batchQueue.push({ url, label, status: 'pending', subtitleStyle: null });
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
    const empty = state.batchQueue.filter(q => q.status === 'empty').length;
    const emptyText = empty ? `, ${empty} no clips` : '';
    label.innerHTML = `Queue: <strong>${state.batchQueue.length}</strong> items (${done} done${emptyText}, ${pending} pending)`;

    list.innerHTML = '';
    state.batchQueue.forEach((q, i) => {
        const li = document.createElement('li');
        li.className = `batch-queue-item ${q.status}`;
        const statusText = q.status === 'empty' ? 'no clips' : q.status;
        li.innerHTML = `
            <span class="batch-queue-item-label" title="${escHtml(q.url)}">${escHtml(q.label)}</span>
            <span class="batch-queue-item-status ${q.status}">${escHtml(statusText)}</span>
            ${q.status === 'pending' ? `<button class="batch-queue-item-remove" onclick="removeBatchItem(${i})">&times;</button>` : ''}`;
        list.appendChild(li);
    });
}

function batchStatusCounts() {
    const done = state.batchQueue.filter(q => q.status === 'done').length;
    const empty = state.batchQueue.filter(q => q.status === 'empty').length;
    const errors = state.batchQueue.filter(q => q.status === 'error').length;
    const cancelled = state.batchQueue.filter(q => q.status === 'cancelled').length;
    const active = state.batchQueue.filter(q => q.status === 'active').length;
    const pending = state.batchQueue.filter(q => q.status === 'pending').length;
    return { done, empty, errors, cancelled, active, pending, total: state.batchQueue.length };
}

function batchItemLabel(item) {
    if (!item) return 'Source';
    return item.resolvedLabel || item.label || sourceDisplayLabel(item.url);
}

function batchProgressItemsWindow(focusIndex = null) {
    const total = state.batchQueue.length;
    if (total <= 7) return state.batchQueue.map((item, index) => ({ item, index }));
    const current = Math.max(0, Math.min(total - 1, Number.isInteger(focusIndex) ? focusIndex : state.batchIndex));
    let start = Math.max(0, current - 2);
    let end = Math.min(total, start + 5);
    start = Math.max(0, end - 5);
    const items = [];
    if (start > 0) items.push({ omitted: start });
    for (let index = start; index < end; index++) {
        items.push({ item: state.batchQueue[index], index });
    }
    if (end < total) items.push({ omitted: total - end });
    return items;
}

function renderBatchProgress(context = {}) {
    const card = document.getElementById('batch-progress-card');
    if (!card) return;
    const counts = batchStatusCounts();
    if (!counts.total) {
        card.classList.add('hidden');
        return;
    }

    const activeIndex = state.batchIndex >= 0 ? state.batchIndex : Math.min(counts.total - 1, Math.max(0, counts.done + counts.empty + counts.errors - 1));
    const item = state.batchQueue[activeIndex] || state.batchQueue[0];
    const sourceName = context?.sourceName || context?.source_name || context?.sourceLabel || context?.source_label;
    if (sourceName && item) item.resolvedLabel = sourceDisplayLabel(sourceName);

    const complete = Boolean(context?.complete);
    const index = Number.isFinite(Number(context?.batchIndex || context?.batch_index))
        ? Number(context?.batchIndex || context?.batch_index)
        : Math.max(1, activeIndex + 1);
    const total = Number.isFinite(Number(context?.batchTotal || context?.batch_total))
        ? Number(context?.batchTotal || context?.batch_total)
        : counts.total;
    const completed = counts.done + counts.empty + counts.errors;
    const sourcePct = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;

    const kicker = document.getElementById('batch-progress-kicker');
    const source = document.getElementById('batch-progress-source');
    const countEl = document.getElementById('batch-progress-counts');
    const fill = document.getElementById('batch-progress-fill');
    const queue = document.getElementById('batch-progress-queue');

    if (kicker) kicker.textContent = complete ? 'Batch complete' : `Processing ${index}/${total}`;
    if (source) {
        const label = complete
            ? `${counts.done} done${counts.empty ? `, ${counts.empty} no clips` : ''}${counts.errors ? `, ${counts.errors} failed` : ''}`
            : batchItemLabel(item);
        source.textContent = label;
        source.title = item?.url || label;
    }
    if (countEl) {
        countEl.textContent = `Done ${counts.done} · No clips ${counts.empty} · Failed ${counts.errors} · Remaining ${counts.pending}`;
    }
    if (fill) fill.style.width = `${complete ? 100 : sourcePct}%`;
    if (queue) {
        queue.innerHTML = '';
        batchProgressItemsWindow(activeIndex).forEach(entry => {
            const chip = document.createElement('span');
            if (entry.omitted) {
                chip.className = 'batch-progress-chip omitted';
                chip.textContent = `+${entry.omitted} more`;
                queue.appendChild(chip);
                return;
            }
            const q = entry.item || {};
            const status = q.status || 'pending';
            chip.className = `batch-progress-chip ${status}`;
            chip.title = q.url || batchItemLabel(q);
            chip.textContent = `${entry.index + 1}. ${batchItemLabel(q)}`;
            queue.appendChild(chip);
        });
    }
    card.classList.remove('hidden');
}

function applyPipelineProgressContext(context) {
    if (!context || typeof context !== 'object') {
        renderBatchProgress();
        return;
    }
    state.batchProgressContext = { ...(state.batchProgressContext || {}), ...context };
    const item = state.batchIndex >= 0 ? state.batchQueue[state.batchIndex] : null;
    const beforeLabel = item?.resolvedLabel || '';
    renderBatchProgress(state.batchProgressContext);
    if (item && beforeLabel !== (item.resolvedLabel || '')) renderBatchQueue();
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
    state.batchProgressContext = {
        sourceName: batchItemLabel(item),
        batchIndex: state.batchIndex + 1,
        batchTotal: state.batchQueue.length,
    };
    renderBatchProgress(state.batchProgressContext);

    // Update progress UI
    const queueLabel = currentBatchSourceCount() > 1
        ? ` (${state.batchIndex + 1}/${state.batchQueue.length})`
        : '';
    resetStages();
    state.progressStartedAt = Date.now();
    setProgress(0, `Starting${queueLabel}...`, true);
    document.getElementById('clip-cards').innerHTML = '';

    try {
        const itemSettings = {
            ...(state.batchSettings || {}),
            ...(item.settings || {}),
            audio_source: item.audioSource || state.batchSettings?.audio_source || {
                mode: 'auto',
                stream: null,
                commentary_guard: true,
                subtitle_policy: 'creator',
            },
            subtitle_style: item.subtitleStyle || item.settings?.subtitle_style || state.batchSettings?.subtitle_style || 'tiktok',
        };
        itemSettings.progress_context = {
            sourceName: batchItemLabel(item),
            sourceUrl: item.url,
            batchIndex: state.batchIndex + 1,
            batchTotal: state.batchQueue.length,
        };
        let r = await pywebview.api.start_processing(item.url, itemSettings);
        // Retry once if "Already processing" (race with previous pipeline's finally block)
        if (r.error && r.error.includes('Already processing')) {
            await new Promise(ok => setTimeout(ok, 1500));
            r = await pywebview.api.start_processing(item.url, itemSettings);
        }
        if (r.error) {
            item.status = 'error';
            toast(`Failed: ${item.label} — ${r.error}`, 'error');
            renderBatchQueue();
            renderBatchProgress();
            // Continue to next
            processNextInQueue();
        }
        // Otherwise, onPipelineComplete will call processNextInQueue
    } catch (e) {
        item.status = 'error';
        toast(`Failed: ${item.label}`, 'error');
        renderBatchQueue();
        renderBatchProgress();
        processNextInQueue();
    }
}

function _onBatchComplete() {
    state.processing = false;
    state.batchIndex = -1;
    state.batchProgressContext = null;
    document.getElementById('btn-cancel')?.classList.add('hidden');

    const done = state.batchQueue.filter(q => q.status === 'done').length;
    const empty = state.batchQueue.filter(q => q.status === 'empty').length;
    const errors = state.batchQueue.filter(q => q.status === 'error').length;
    const total = state.batchQueue.length;
    const completed = done + empty;
    renderBatchProgress({ complete: true });

    const title = document.getElementById('completion-title');
    const message = document.getElementById('completion-message');
    const banner = document.getElementById('completion-banner');
    if (title) {
        title.textContent = errors
            ? `${completed}/${total} Videos Done`
            : done ? 'All Done!' : 'No Clips Created';
    }
    if (message) {
        const parts = [`Processed ${completed} video${completed !== 1 ? 's' : ''}`];
        if (empty) parts.push(`${empty} with no clips`);
        if (errors) parts.push(`${errors} failed`);
        message.textContent = `${parts.join(', ')}.`;
    }
    banner?.classList.remove('hidden');

    safeToast(
        `Batch complete: ${done} done${empty ? `, ${empty} no clips` : ''}${errors ? `, ${errors} failed` : ''}`,
        errors ? (completed ? 'warning' : 'error') : (done ? 'success' : 'warning')
    );

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
    state.overallPercent = 0;
    state.progressStartedAt = 0;
    state.batchProgressContext = null;
    document.getElementById('generate-idle').classList.remove('hidden');
    document.getElementById('progress-area').classList.add('hidden');
    const cancelBtn = document.getElementById('btn-cancel');
    if (cancelBtn) {
        cancelBtn.classList.add('hidden');
        cancelBtn.disabled = false;
        cancelBtn.textContent = 'Cancel';
    }
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

window.onPipelineProgress = function (stage, percent, message, detail, context = null) {
    if (stage === 'upload') {
        const pct = Math.min(100, Math.max(0, Math.round(percent)));
        const uploadCard = document.getElementById('upload-progress-card');
        if (uploadCard) uploadCard.classList.remove('hidden');
        document.getElementById('upload-status').textContent = message || 'Uploading...';
        document.getElementById('upload-percent').textContent = `${pct}%`;
        document.getElementById('upload-fill').style.width = `${pct}%`;
        return;
    }
    if (!state.processing) return;
    applyPipelineProgressContext(context);
    const stagePercent = Math.min(100, Math.max(0, Number(percent) || 0));
    state.progressStage = stage;
    state.progressStagePercent = stagePercent;
    const r = progressRangeForStage(stage);
    setProgress(r[0] + (stagePercent / 100) * (r[1] - r[0]), message);
    if (detail) {
        setProgressDetail(detail);
    } else {
        setProgressDetail('');
    }
    activateStage(stage);
    if (stage === 'download' && percent >= 100) completeStage('download');
    if (stage === 'detect' && percent >= 100) completeStage('detect');
    if (stage === 'candidates' && percent >= 100) completeStage('candidates');
};

window.onClipProgress = function (clipNum, totalClips, substep, percent, message) {
    if (!state.processing) return;
    const sw = { audio: [0, 0.10], transcribe: [0.10, 0.40], subtitle: [0.40, 0.60], render: [0.60, 1.0] }[substep] || [0, 1];
    const clipFrac = sw[0] + (percent / 100) * (sw[1] - sw[0]);
    const total = Math.max(1, totalClips || 1);
    const clipStagePercent = Math.min(100, Math.max(0, ((clipNum - 1) + clipFrac) / total * 100));
    state.progressStage = 'clips';
    state.progressStagePercent = clipStagePercent;
    const r = progressRangeForStage('clips');
    setProgress(r[0] + (clipStagePercent / 100) * (r[1] - r[0]), message);
    completeStage('candidates');
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
        if (Array.isArray(r.scheduled)) {
            state.scheduled = normalizeScheduledMetadata(r.scheduled, { preserveUnresolved: !state.results.length });
            _cachedNextUpload = null;
            _nextUploadCacheTime = 0;
        }
        if (Array.isArray(r.upload_history)) state.uploadHistory = r.upload_history;
    } catch (_) {
        state.scheduled = normalizeScheduledMetadata(state.scheduled, { preserveUnresolved: !state.results.length });
    }
    if (!state.scheduled.length) clearStaleScheduleUi();
    if (render) {
        renderCalendar();
        renderTimeline();
        renderClipTray();
    }
    renderUploadReadinessStrip();
    renderUploadSummary();
}

window.onPipelineComplete = async function (success, doneCount, totalCount, errorMsg, details = null) {
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

    let hasMore = false;
    const completionDetails = details && typeof details === 'object' ? details : {};
    const noQualityClips = success && completionDetails.completion_state === 'no_quality_clips';
    const montageCreated = success && completionDetails.completion_state === 'montage_created';
    const noMontageBeats = success && completionDetails.completion_state === 'no_montage_beats';
    try {
        // Mark current batch item
        if (state.batchIndex >= 0 && state.batchIndex < state.batchQueue.length) {
            state.batchQueue[state.batchIndex].status = success
                ? ((noQualityClips || noMontageBeats) ? 'empty' : 'done')
                : 'error';
            if (noQualityClips) {
                state.batchQueue[state.batchIndex].message = completionDetails.message || 'No clips met the quality bar.';
            } else if (noMontageBeats) {
                state.batchQueue[state.batchIndex].message = completionDetails.message || 'No montage could be assembled.';
            }
            renderBatchQueue();
            renderBatchProgress();
        }

        hasMore = state.batchQueue.some((q, i) => i > state.batchIndex && q.status === 'pending');

        if (success) {
            setProgress(
                100,
                noQualityClips || noMontageBeats
                    ? (completionDetails.message || 'No clips met the quality bar')
                    : (montageCreated ? 'Montage created' : `${doneCount} clips created`)
            );
            completeStageThrough('done');
            if (noQualityClips || noMontageBeats) {
                const fallbackGuidance = noMontageBeats
                    ? 'Try a longer source, a broader montage template, or Deep Analysis.'
                    : 'Try Auto or Quantity, a longer source, a shorter minimum gap, or Deep Analysis.';
                const guidance = completionDetails.guidance || fallbackGuidance;
                safeToast(completionDetails.message || (noMontageBeats ? 'No montage could be assembled' : 'No clips met the quality bar'), 'warning');
                safeAddNotification(noMontageBeats ? 'No Montage Created' : 'No Clips Created', guidance, 'info');
            } else if (montageCreated) {
                const beatCount = Number(completionDetails.beat_count || 0);
                const createdCount = Number(completionDetails.created_count || doneCount || 1);
                const requestedCount = Number(completionDetails.requested_count || createdCount);
                safeToast(
                    createdCount === 1 ? 'Montage created successfully' : `${createdCount} montages created successfully`,
                    'success'
                );
                safeAddNotification(
                    createdCount === 1 ? 'Montage Ready' : 'Montages Ready',
                    `${createdCount} of ${requestedCount} requested montage${requestedCount !== 1 ? 's' : ''}${beatCount ? ` from ${beatCount} total beat${beatCount !== 1 ? 's' : ''}` : ''} ready to review`,
                    'success'
                );
            } else {
                safeToast(`${doneCount} clips created successfully`, 'success');
                safeAddNotification(
                    'Clips Ready',
                    `${doneCount} viral clip${doneCount > 1 ? 's' : ''} generated and ready to upload`,
                    'success'
                );
            }

            // Accumulate results (don't overwrite — append from all batch items)
            pywebview.api.get_results().then(r => {
                state.results = visibleClipList(r.clips);
                state.moments = r.moments || state.moments;
                if (!noQualityClips && !noMontageBeats) refreshSubtitlePreviewSnapshot(true);
            }).catch(() => {});
        } else {
            safeToast(errorMsg || 'Processing failed', 'error');
            safeAddNotification('Processing Failed', errorMsg || 'An error occurred during clip generation', 'error');
        }
    } catch (e) {
        console.warn('Pipeline completion UI update failed; finishing queue cleanup anyway', e);
    } finally {
        if (hasMore) {
            // Short delay before starting next to let UI update
            setTimeout(() => processNextInQueue(), 500);
        } else {
            // All done (or single video)
            _onBatchComplete();
        }
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
    state.batchProgressContext = null;
    renderBatchQueue();
    renderBatchProgress({ complete: true });
    toast('Processing cancelled', 'warning');
    resetGenerate();
    const cancelBtn = document.getElementById('btn-cancel');
    if (cancelBtn) {
        cancelBtn.disabled = false;
        cancelBtn.textContent = 'Cancel';
    }
};

/* ── Scheduler Callbacks ──────────────────────────────────────────────── */

window.onSchedulerStatus = async function (msg) {
    await refreshScheduleFromBackend(false);
    if (!hasPendingSchedule()) {
        clearStaleScheduleUi();
        return;
    }

    const bar = document.getElementById('scheduler-bar');
    bar.classList.remove('hidden');
    const watcherMsg = String(msg || '').includes('Local Upload Watcher')
        ? String(msg || '')
        : `Local Upload Watcher: ${msg || 'active'}`;
    document.getElementById('scheduler-status-text').textContent = watcherMsg;
    // Add uploading notification if it looks like an active upload
    if (String(msg || '').toLowerCase().includes('uploading')) {
        removeNotificationsByType('uploading');
        addNotification('Uploading', watcherMsg, 'uploading');
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

    if (!_cachedNextUpload) {
        clearStaleScheduleUi();
        return;
    }
    const diffMs = new Date(`${_cachedNextUpload.date}T${_cachedNextUpload.time}`).getTime() - now;
    if (diffMs > 0) {
        const hrs = Math.floor(diffMs / 3600000);
        const mins = Math.floor((diffMs % 3600000) / 60000);
        document.getElementById('scheduler-status-text').textContent =
            `Local Upload Watcher: next send to YouTube is Clip ${_cachedNextUpload.clipIdx + 1} in ${hrs}h ${mins}m`;
    } else {
        document.getElementById('scheduler-status-text').textContent =
            'Local Upload Watcher: calendar needs attention';
    }
}, 30000);

window.onScheduledUploadDone = async function (clipIdx, success, error) {
    removeNotificationsByType('uploading');
    const clipName = state.results[clipIdx]?.filename || `Clip ${clipIdx + 1}`;
    if (success) {
        toast(`Clip ${clipIdx + 1} sent by Local Upload Watcher`, 'success');
        addNotification(
            'Upload Complete',
            `${clipName} was uploaded to YouTube successfully`,
            'success'
        );
        await refreshScheduleFromBackend(true);
    } else {
        toast(`Scheduler upload failed: ${error}`, 'error');
        addNotification(
            'Upload Failed',
            `${clipName}: ${error}`,
            'error'
        );
        await refreshScheduleFromBackend(true);
    }
};

window.onScheduleUpdated = function () {
    refreshScheduleFromBackend(true);
};

/* ── Progress Helpers ──────────────────────────────────────────────────── */

function learnedEtaStagePlan() {
    const context = state.batchProgressContext || {};
    const rawPlan = context.etaStagePlan || context.eta_stage_plan || {};
    if (!rawPlan || typeof rawPlan !== 'object') return null;
    const stages = {};
    let total = 0;
    PIPELINE_STAGE_ORDER.forEach(stage => {
        const seconds = Number(rawPlan[stage]);
        stages[stage] = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
        total += stages[stage];
    });
    if (total <= 0) return null;
    return {
        stages,
        total,
        confidence: String(context.etaStageConfidence || context.eta_stage_confidence || '').trim(),
        sampleCount: Number(context.etaStageSampleCount ?? context.eta_stage_sample_count ?? 0),
    };
}

function learnedProgressRanges() {
    const plan = learnedEtaStagePlan();
    if (!plan) return null;
    const ranges = {};
    let cursor = 0;
    PIPELINE_STAGE_ORDER.forEach((stage, index) => {
        const width = (plan.stages[stage] / plan.total) * 100;
        const end = index === PIPELINE_STAGE_ORDER.length - 1 ? 100 : cursor + width;
        ranges[stage] = [cursor, end];
        cursor = end;
    });
    ranges.upload = DEFAULT_PROGRESS_RANGES.upload;
    return ranges;
}

function progressRangeForStage(stage) {
    const learned = learnedProgressRanges();
    if (learned && learned[stage]) return learned[stage];
    return DEFAULT_PROGRESS_RANGES[stage] || [0, 100];
}

function setProgress(pct, msg, allowDecrease = false) {
    pct = Math.min(100, Math.max(0, pct));
    if (!allowDecrease) pct = Math.max(pct, state.overallPercent || 0);
    if (pct > 0 && !state.progressStartedAt) state.progressStartedAt = Date.now();
    state.overallPercent = pct;
    document.getElementById('progress-fill').style.width = pct + '%';
    document.getElementById('progress-percent').textContent = Math.round(pct) + '%';
    if (msg) document.getElementById('progress-status').textContent = msg;
    const etaEl = document.getElementById('progress-eta');
    if (etaEl) {
        if (pct >= 100) {
            etaEl.textContent = 'Complete.';
        } else {
            const backendEta = learnedStageEtaRemaining() || backendHistoryEtaRemaining();
            if (backendEta) {
                etaEl.textContent = `Rough ETA: ${fmtEta(backendEta.remaining)} remaining (${backendEta.source})`;
            } else if (pct >= 3 && state.progressStartedAt) {
                const elapsed = Math.max(0, (Date.now() - state.progressStartedAt) / 1000);
                if (elapsed >= 8) {
                    const remaining = (elapsed / Math.max(pct, 1)) * (100 - pct);
                    etaEl.textContent = `Rough ETA: ${fmtEta(remaining)} remaining`;
                } else {
                    etaEl.textContent = 'Estimating from current progress...';
                }
            } else {
                etaEl.textContent = 'Estimating from current progress...';
            }
        }
    }
}

function setProgressDetail(detail) {
    const el = document.getElementById('progress-detail');
    if (!el) return;
    const text = String(detail || '').trim();
    if (!text) {
        el.textContent = '';
        el.classList.add('hidden');
        return;
    }
    el.textContent = text;
    el.classList.remove('hidden');
}

function resetStages() {
    document.querySelectorAll('.stage').forEach(s => s.classList.remove('active', 'completed'));
    document.querySelectorAll('.stage-line').forEach(l => l.classList.remove('active', 'completed'));
    const etaEl = document.getElementById('progress-eta');
    if (etaEl) etaEl.textContent = 'ETA will appear after progress starts.';
    state.progressStage = null;
    state.progressStagePercent = 0;
    setProgressDetail('');
}
function activateStage(name) {
    const stages = ['download', 'detect', 'candidates', 'clips', 'done'];
    const idx = stages.indexOf(name);
    if (idx > 0) {
        stages.slice(0, idx).forEach(stageName => completeStage(stageName));
    }
    const el = document.querySelector(`.stage[data-stage="${name}"]`);
    if (el && !el.classList.contains('completed')) el.classList.add('active');
}
function completeStage(name) {
    const el = document.querySelector(`.stage[data-stage="${name}"]`);
    if (el) { el.classList.remove('active'); el.classList.add('completed'); }
    const stages = ['download', 'detect', 'candidates', 'clips', 'done'];
    const idx = stages.indexOf(name);
    if (idx > 0) { const lines = document.querySelectorAll('.stage-line'); if (lines[idx - 1]) lines[idx - 1].classList.add('completed'); }
}

function completeStageThrough(name) {
    const stages = ['download', 'detect', 'candidates', 'clips', 'done'];
    const idx = stages.indexOf(name);
    if (idx < 0) return;
    stages.slice(0, idx + 1).forEach(stageName => completeStage(stageName));
}

function fmtEta(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return 'estimating';
    if (seconds < 60) return '<1 min';
    const mins = Math.round(seconds / 60);
    if (mins < 90) return `${mins} min`;
    const hours = Math.floor(mins / 60);
    const rem = mins % 60;
    return rem ? `${hours}h ${rem}m` : `${hours}h`;
}

function learnedStageEtaRemaining() {
    const plan = learnedEtaStagePlan();
    if (!plan || !state.progressStage) return null;
    const stage = String(state.progressStage || '').trim();
    const currentIndex = PIPELINE_STAGE_ORDER.indexOf(stage);
    if (currentIndex < 0) return null;
    const percent = Math.min(100, Math.max(0, Number(state.progressStagePercent) || 0)) / 100;
    let remaining = plan.stages[stage] * (1 - percent);
    for (let index = currentIndex + 1; index < PIPELINE_STAGE_ORDER.length; index += 1) {
        remaining += plan.stages[PIPELINE_STAGE_ORDER[index]] || 0;
    }
    if (!Number.isFinite(remaining) || remaining <= 0) return null;
    const suffix = plan.confidence === 'low' ? 'learning' : 'learned stages';
    const sampleText = plan.sampleCount > 0 ? `, ${plan.sampleCount} run${plan.sampleCount === 1 ? '' : 's'}` : '';
    return { remaining, source: `${suffix}${sampleText}` };
}

function backendHistoryEtaRemaining() {
    const context = state.batchProgressContext || {};
    const total = Number(context.estimatedTotalSeconds ?? context.estimated_total_seconds);
    if (!Number.isFinite(total) || total <= 0 || !state.progressStartedAt) return null;
    const elapsed = Math.max(0, (Date.now() - state.progressStartedAt) / 1000);
    const remaining = Math.max(0, total - elapsed);
    const rawSource = String(context.estimateSource || context.estimate_source || '').trim();
    let source = 'local history';
    if (rawSource === 'duration_baseline') source = 'baseline';
    else if (rawSource && rawSource !== 'not_estimated') source = rawSource.replace(/_/g, ' ');
    return { remaining, source };
}

function finiteNumber(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
}

function clipSelectionTier(moment = {}) {
    const rawTier = String(moment?.selection_tier || '').toLowerCase();
    if (rawTier === 'extra_pick' || rawTier === 'near_quality_pick') return 'Extra pick';
    const fallback = moment && moment.near_quality_fallback;
    if (fallback && fallback.applied) return 'Extra pick';
    const depth = normalizeProcessingDepth(moment?.processing_depth || state.batchSettings?.processing_depth || state.settings?.processing_depth || 'balanced');
    if (depth === 'fast') return 'Quick pick';
    return 'Recommended';
}

/* ── Clip Progress Cards ───────────────────────────────────────────────── */

function createClipCard(num, total, moment) {
    const card = document.createElement('div');
    card.className = 'clip-progress-card';
    card.id = `clip-card-${num}`;
    card.style.animationDelay = `${(num - 1) * 0.06}s`;
    const score = finiteNumber(moment.score, 0);
    const sc = score >= 0.7 ? 'high' : score >= 0.4 ? 'mid' : 'low';
    const tier = clipSelectionTier(moment);
    card.innerHTML = `
        <div class="clip-card-header">
            <span class="clip-num">Clip ${num}</span>
            <span class="clip-time">${fmtTime(moment.start)} - ${fmtTime(moment.end)}</span>
            <span class="clip-tier">${tier}</span>
            <span class="clip-score ${sc}" title="Rank score">${score.toFixed(2)}</span>
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
    const steps = ['audio','transcribe','subtitle','render'];
    const stepIndex = Math.max(0, steps.indexOf(substep));
    card.querySelector('.clip-bar-fill').style.width = (stepIndex * 25 + (percent/100) * 25) + '%';
    card.classList.remove('processing', 'done');
    if (percent >= 100 && substep === 'render') { card.classList.add('done'); card.querySelector('.clip-bar-fill').style.width = '100%'; }
    else card.classList.add('processing');
}

/* ── Results ───────────────────────────────────────────────────────────── */

function _groupResultsByStem(clips) {
    const groups = {};
    clips.forEach((clip, i) => {
        const originalIdx = Number.isInteger(clip._idx) ? clip._idx : i;
        // Use source_stem from moments (persists through renames),
        // then try filename pattern, then fall back to 'Other'
        let stem = clip.source_stem;
        if (!stem) {
            const m = state.moments[originalIdx];
            if (m && m.source_stem) stem = m.source_stem;
        }
        if (!stem) {
            const match = clip.filename.match(/^(.+?)_viral\d+/i);
            stem = match ? match[1] : clip.filename.replace(/\.[^.]+$/, '');
        }
        if (!groups[stem]) groups[stem] = { stem, clips: [] };
        groups[stem].clips.push({ ...clip, _idx: originalIdx });
    });
    return Object.values(groups);
}

function feedbackButtonsMarkup() {
    return `
        <button type="button" class="feedback-btn" data-feedback="like" title="Like this clip and nudge future picks" aria-label="Like this clip and nudge future picks">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 10v11"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2h0a3.13 3.13 0 0 1 3 3.88Z"/></svg>
        </button>
        <button type="button" class="feedback-btn" data-feedback="dislike" title="Dislike this clip and reduce similar future picks" aria-label="Dislike this clip and reduce similar future picks">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 14V3"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22h0a3.13 3.13 0 0 1-3-3.88Z"/></svg>
        </button>
        <button type="button" class="feedback-btn" data-feedback="favorite" title="Favorite this clip and boost similar future picks" aria-label="Favorite this clip and boost similar future picks">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
        </button>`;
}

function _buildResultCard(clip, i) {
    const m = state.moments[i] || {};
    const score = finiteNumber(m.score, 0);
    const sc = score >= 0.7 ? 'high' : score >= 0.4 ? 'mid' : 'low';
    const tier = clipSelectionTier(m);
    const momentLabel = momentLabelMarkup(clip, m);
    const truthLabel = clipTruthMarkup(clip, m, { clipIndex: i, editable: true });
    const filename = resultFilenameKey(clip);
    const isSelected = state.resultsSelectedFilenames.has(filename);
    const hasContext = !!creatorTitleContextForClip(i);
    const card = document.createElement('div');
    card.className = 'result-card' + (isSelected ? ' selected' : '');
    card.dataset.clipIdx = i;
    card.dataset.filename = filename;
    card.innerHTML = `
        <div class="result-card-thumb" data-clip-idx="${i}" onclick="previewClip(${i})">
            <div class="thumb-placeholder">
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            </div>
            <label class="library-select-check result-select-check" title="Select clip" aria-label="Select ${escHtml(clip.filename)}">
                <input class="result-select-input" type="checkbox" ${isSelected ? 'checked' : ''}>
                <span></span>
            </label>
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
                <span class="clip-tier">${tier}</span>
                <span class="clip-score ${sc}" title="Rank score">${score.toFixed(2)}</span>
            </div>
            <div class="result-filename">${escHtml(clip.filename)}</div>
            ${momentLabel}
            ${truthLabel}
            <div class="result-meta">
                ${m.start !== undefined ? `<span>${fmtTime(m.start)} - ${fmtTime(m.end)}</span>` : ''}
                <span>${clip.size_mb} MB</span>
            </div>
            <div class="result-card-actions">
                <button type="button" class="result-ai-notes-btn${hasContext ? ' has-context' : ''}">AI Notes</button>
            </div>
            <div class="result-feedback" aria-label="Clip feedback">
                ${feedbackButtonsMarkup()}
            </div>
        </div>`;
    const gameEditBtn = card.querySelector('.is-edit-game');
    if (gameEditBtn) {
        gameEditBtn.addEventListener('click', event => {
            event.stopPropagation();
            openClipGameTitleModal(i);
        });
    }
    const aiNotesBtn = card.querySelector('.result-ai-notes-btn');
    if (aiNotesBtn) {
        aiNotesBtn.addEventListener('click', event => {
            event.stopPropagation();
            openClipTitleContextModal(i);
        });
    }
    card.querySelectorAll('.feedback-btn').forEach(btn => {
        btn.addEventListener('click', event => {
            event.stopPropagation();
            recordClipFeedback(i, btn.dataset.feedback);
        });
    });
    const selectInput = card.querySelector('.result-select-input');
    const selectLabel = card.querySelector('.result-select-check');
    if (selectLabel) selectLabel.addEventListener('click', event => event.stopPropagation());
    if (selectInput) {
        selectInput.addEventListener('click', event => event.stopPropagation());
        selectInput.addEventListener('change', event => {
            event.stopPropagation();
            setResultsSelected(clip.filename, selectInput.checked);
        });
    }
    renderCardFeedbackState(card, clip);
    return card;
}

function toggleFolder(headerEl) {
    const folder = headerEl.closest('.result-folder');
    const isOpen = folder.classList.toggle('open');
    const resultsGrid = document.getElementById('results-grid');
    if (resultsGrid && resultsGrid.contains(folder) && folder.dataset.stem) {
        state.resultsOpenFolders[folder.dataset.stem] = isOpen;
    }
    const libraryGrid = document.getElementById('library-grid');
    if (libraryGrid && libraryGrid.contains(folder) && folder.dataset.stem) {
        state.libraryOpenFolders[folder.dataset.stem] = isOpen;
    }
}

function resultFilenameKey(clipOrFilename) {
    if (typeof clipOrFilename === 'string') return clipOrFilename;
    return String(clipOrFilename?.filename || '').trim();
}

function libraryFilenameKey(clipOrFilename) {
    if (typeof clipOrFilename === 'string') return clipOrFilename;
    return String(clipOrFilename?.filename || '').trim();
}

function pruneResultsSelection() {
    const valid = new Set(state.results.map(clip => resultFilenameKey(clip)).filter(Boolean));
    [...state.resultsSelectedFilenames].forEach(filename => {
        if (!valid.has(filename)) state.resultsSelectedFilenames.delete(filename);
    });
}

function renderResultsSelectionState() {
    const selectedCount = state.resultsSelectedFilenames.size;
    document.querySelectorAll('.result-card').forEach(card => {
        const filename = card.dataset.filename || '';
        const selected = state.resultsSelectedFilenames.has(filename);
        card.classList.toggle('selected', selected);
        const checkbox = card.querySelector('.result-select-input');
        if (checkbox) checkbox.checked = selected;
    });
    const selectedEl = document.getElementById('results-selected-count');
    if (selectedEl) selectedEl.textContent = `${selectedCount} selected`;
    const hasVisible = (state.resultsVisibleFilenames || []).length > 0;
    const selectBtn = document.getElementById('results-select-visible');
    if (selectBtn) selectBtn.disabled = !hasVisible;
    const clearBtn = document.getElementById('results-clear-selected');
    if (clearBtn) clearBtn.disabled = selectedCount === 0;
    const deleteBtn = document.getElementById('results-delete-selected');
    if (deleteBtn) deleteBtn.disabled = selectedCount === 0;
}

function setResultsSelected(filename, selected) {
    const key = resultFilenameKey(filename);
    if (!key) return;
    if (selected) state.resultsSelectedFilenames.add(key);
    else state.resultsSelectedFilenames.delete(key);
    renderResultsSelectionState();
}

function selectVisibleResults() {
    (state.resultsVisibleFilenames || []).forEach(filename => {
        if (filename) state.resultsSelectedFilenames.add(filename);
    });
    renderResultsSelectionState();
}

function clearResultsSelection() {
    state.resultsSelectedFilenames.clear();
    renderResultsSelectionState();
}

function requestDeleteSelectedResults() {
    const filenames = [...state.resultsSelectedFilenames].filter(Boolean);
    if (!filenames.length) return toast('Select clips to delete first', 'warning');
    state.pendingDeleteIdx = -1;
    state.pendingDeleteFilename = null;
    state.pendingDeleteFilenames = filenames;
    state.pendingDeleteSource = 'results-bulk';
    document.getElementById('confirm-delete-msg').textContent =
        `Delete ${filenames.length} selected clip${filenames.length !== 1 ? 's' : ''}? This cannot be undone.`;
    showModal('confirm-delete-modal');
}

function pruneLibrarySelection() {
    const valid = new Set(state.libraryClips.map(clip => libraryFilenameKey(clip)).filter(Boolean));
    [...state.librarySelectedFilenames].forEach(filename => {
        if (!valid.has(filename)) state.librarySelectedFilenames.delete(filename);
    });
}

function renderLibrarySelectionState() {
    const selectedCount = state.librarySelectedFilenames.size;
    document.querySelectorAll('.library-item').forEach(item => {
        const filename = item.dataset.filename || '';
        const selected = state.librarySelectedFilenames.has(filename);
        item.classList.toggle('selected', selected);
        const checkbox = item.querySelector('.library-select-input');
        if (checkbox) checkbox.checked = selected;
    });
    const selectedEl = document.getElementById('library-selected-count');
    if (selectedEl) selectedEl.textContent = `${selectedCount} selected`;
    const hasVisible = (state.libraryVisibleFilenames || []).length > 0;
    const selectBtn = document.getElementById('library-select-visible');
    if (selectBtn) selectBtn.disabled = !hasVisible;
    const clearBtn = document.getElementById('library-clear-selected');
    if (clearBtn) clearBtn.disabled = selectedCount === 0;
    const deleteBtn = document.getElementById('library-delete-selected');
    if (deleteBtn) deleteBtn.disabled = selectedCount === 0;
}

function setLibrarySelected(filename, selected) {
    const key = libraryFilenameKey(filename);
    if (!key) return;
    if (selected) state.librarySelectedFilenames.add(key);
    else state.librarySelectedFilenames.delete(key);
    renderLibrarySelectionState();
}

function selectVisibleLibrary() {
    (state.libraryVisibleFilenames || []).forEach(filename => {
        if (filename) state.librarySelectedFilenames.add(filename);
    });
    renderLibrarySelectionState();
}

function clearLibrarySelection() {
    state.librarySelectedFilenames.clear();
    renderLibrarySelectionState();
}

function requestDeleteSelectedLibrary() {
    const filenames = [...state.librarySelectedFilenames].filter(Boolean);
    if (!filenames.length) return toast('Select videos to delete first', 'warning');
    state.pendingDeleteIdx = -1;
    state.pendingDeleteFilename = null;
    state.pendingDeleteFilenames = filenames;
    state.pendingDeleteSource = 'library-bulk';
    document.getElementById('confirm-delete-msg').textContent =
        `Delete ${filenames.length} selected video${filenames.length !== 1 ? 's' : ''}? This cannot be undone.`;
    showModal('confirm-delete-modal');
}

function deletedOrPrunedNames(result) {
    const deleted = Array.isArray(result?.deleted) ? result.deleted : [];
    const pruned = Array.isArray(result?.missing_pruned) ? result.missing_pruned : [];
    return [...new Set([...deleted, ...pruned])];
}

function deleteFailureMessage(result) {
    const failed = Array.isArray(result?.failed) ? result.failed : [];
    return (failed[0] && failed[0].error) || result?.error || 'Delete failed';
}

function deleteSuccessText(kind, count, staleOnly = false) {
    if (staleOnly) return `${kind} was already gone; refreshed`;
    return `Deleted ${count} ${kind}${count !== 1 ? 's' : ''}`;
}

function _decodeMediaUrl(url) {
    try {
        return decodeURIComponent(String(url || ''));
    } catch (_) {
        return String(url || '');
    }
}

function _mediaUrlMatchesDeleteNames(url, filenames) {
    const names = (filenames || []).map(name => String(name || '').toLowerCase()).filter(Boolean);
    if (!names.length) return true;
    const raw = String(url || '').toLowerCase();
    const decoded = _decodeMediaUrl(url).toLowerCase();
    return names.some(name => raw.includes(encodeURIComponent(name).toLowerCase()) || decoded.includes(name));
}

function _releaseVideoElement(video) {
    if (!video) return false;
    try {
        const src = video.currentSrc || video.src || '';
        video.pause();
        if ('srcObject' in video && video.srcObject) video.srcObject = null;
        video.removeAttribute('src');
        video.load();
        return Boolean(src);
    } catch (_) {
        return false;
    }
}

async function releaseLocalVideoHandlesBeforeDelete(filenames = []) {
    let released = false;
    const targetNames = (filenames || []).map(name => String(name || '')).filter(Boolean);
    document.querySelectorAll('video').forEach(video => {
        const src = video.currentSrc || video.src || '';
        if (src && _mediaUrlMatchesDeleteNames(src, targetNames)) {
            released = _releaseVideoElement(video) || released;
        }
    });
    for (let i = _thumbQueue.length - 1; i >= 0; i--) {
        if (_mediaUrlMatchesDeleteNames(_thumbQueue[i]?.url, targetNames)) {
            _thumbQueue.splice(i, 1);
        }
    }
    Array.from(_activeThumbVideos).forEach(entry => {
        if (_mediaUrlMatchesDeleteNames(entry?.url, targetNames)) {
            if (typeof entry.cleanup === 'function') {
                entry.cleanup();
                released = true;
            }
            else released = _releaseVideoElement(entry.video) || released;
        }
    });
    if (released) {
        await new Promise(resolve => setTimeout(resolve, 180));
    }
}

function ensureEmptyState(id, title, subtitle, icon = 'folder') {
    let empty = document.getElementById(id);
    if (empty instanceof Node) return empty;
    empty = document.createElement('div');
    empty.className = 'empty-state';
    empty.id = id;
    const iconMarkup = icon === 'video'
        ? '<rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/>'
        : '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>';
    empty.innerHTML = `
        <div class="empty-state-icon">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">${iconMarkup}</svg>
        </div>
        <p>${escHtml(title)}</p>
        <span>${escHtml(subtitle)}</span>`;
    return empty;
}

async function loadResults() {
    try {
        const r = await pywebview.api.get_results();
        state.results = visibleClipList(r.clips);
        state.moments = r.moments || state.moments;
        pruneResultsSelection();
        refreshSubtitlePreviewSnapshot(false);
        await loadPersonalization();
    } catch (_) {}
    renderResultsGrid();
}

function renderResultsGrid() {
    const grid = document.getElementById('results-grid');
    const empty = ensureEmptyState('results-empty', 'No clips yet', 'Generate some viral clips first', 'video');
    const countEl = document.getElementById('results-count');
    if (countEl) countEl.textContent = state.results.length ? state.results.length + ' clip' + (state.results.length !== 1 ? 's' : '') : '';
    if (!state.results.length) {
        renderMomentFilterBar('results-moment-filters', [], () => ({}), 'all', setResultsMomentFilter);
        grid.innerHTML = '';
        grid.appendChild(empty);
        empty.style.display = '';
        state.resultsVisibleFilenames = [];
        renderResultsSelectionState();
        return;
    }
    state.resultsMomentFilter = normalizeAvailableMomentFilter(
        state.results,
        (_, i) => state.moments[i] || {},
        state.resultsMomentFilter,
    );
    renderMomentFilterBar(
        'results-moment-filters',
        state.results,
        (_, i) => state.moments[i] || {},
        state.resultsMomentFilter,
        setResultsMomentFilter,
    );
    const visibleResults = state.results
        .map((clip, index) => ({ ...clip, _idx: index }))
        .filter(clip => clipMatchesMomentFilter(clip, state.moments[clip._idx] || {}, state.resultsMomentFilter));
    state.resultsVisibleFilenames = visibleResults.map(resultFilenameKey).filter(Boolean);
    if (countEl && visibleResults.length !== state.results.length) {
        countEl.textContent = `${visibleResults.length}/${state.results.length} clips`;
    }

    const groups = _groupResultsByStem(visibleResults);
    const frag = document.createDocumentFragment();

    // If only 1 group, render it open; otherwise start collapsed
    const autoOpen = groups.length === 1;

    groups.forEach(group => {
        const totalMB = group.clips.reduce((sum, c) => sum + (parseFloat(c.size_mb) || 0), 0).toFixed(1);
        const folder = document.createElement('div');
        const hasOpenPref = Object.prototype.hasOwnProperty.call(state.resultsOpenFolders, group.stem);
        const isOpen = hasOpenPref ? state.resultsOpenFolders[group.stem] === true : autoOpen;
        folder.className = 'result-folder' + (isOpen ? ' open' : '');
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
    renderResultsSelectionState();

    // Lazy-load thumbnails — only decode filtered visible cards.
    visibleResults.forEach(clip => {
        if (clip && clip.url) {
            const thumbEl = document.querySelector(`.result-card-thumb[data-clip-idx="${clip._idx}"]`);
            if (thumbEl) lazyThumb(thumbEl, clip.url);
        }
    });
}

function setResultsMomentFilter(filter) {
    state.resultsMomentFilter = String(filter || 'all');
    renderResultsGrid();
}
async function openFolder() { try { await pywebview.api.open_output_folder(); } catch (_) {} }

/* ── Video Preview ─────────────────────────────────────────────────────── */

async function previewClip(idx) {
    try {
        const r = await pywebview.api.get_video_url(idx);
        if (r.url) {
            state.previewClipIdx = idx;
            state.previewLibraryClip = null;
            const video = document.getElementById('preview-video');
            video.src = r.url;
            document.getElementById('preview-modal-title').textContent = `Clip ${idx + 1}`;
            document.getElementById('preview-delete-btn').style.display = '';
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
    video.load();
    state.previewClipIdx = -1;
    state.previewLibraryClip = null;
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
    state.pendingDeleteFilenames = [];
    state.pendingDeleteSource = 'results';
    document.getElementById('confirm-delete-msg').textContent = `Delete "${clip.filename}"? This cannot be undone.`;
    showModal('confirm-delete-modal');
}

function requestDeleteLibrary(filename) {
    state.pendingDeleteIdx = -1;
    state.pendingDeleteFilename = filename;
    state.pendingDeleteFilenames = [];
    state.pendingDeleteSource = 'library';
    document.getElementById('confirm-delete-msg').textContent = `Delete "${filename}"? This cannot be undone.`;
    showModal('confirm-delete-modal');
}

async function confirmDelete() {
    closeModal('confirm-delete-modal');

    if (state.pendingDeleteSource === 'results' && state.pendingDeleteIdx >= 0) {
        try {
            await releaseLocalVideoHandlesBeforeDelete([state.pendingDeleteFilename]);
            const r = await pywebview.api.delete_library_files([state.pendingDeleteFilename]);
            console.debug('[delete] result clip', r);
            const removed = deletedOrPrunedNames(r);
            if (removed.length) {
                const physicallyDeleted = Array.isArray(r.deleted) && r.deleted.length > 0;
                toast(deleteSuccessText('clip', removed.length, !physicallyDeleted), 'success');
                // Close preview if we deleted the previewed clip
                if (state.previewClipIdx === state.pendingDeleteIdx) {
                    closePreview();
                }
                await refreshScheduleFromBackend(false);
                await loadResults();
                renderTimeline();
                renderCalendar();
                renderClipTray();
            } else {
                toast(deleteFailureMessage(r), 'error');
            }
        } catch (e) { toast('Delete failed: ' + e, 'error'); }
    } else if (state.pendingDeleteSource === 'results-bulk' && state.pendingDeleteFilenames.length) {
        const filenames = [...state.pendingDeleteFilenames];
        try {
            await releaseLocalVideoHandlesBeforeDelete(filenames);
            const r = await pywebview.api.delete_library_files(filenames);
            console.debug('[delete] result bulk clips', r);
            const deleted = deletedOrPrunedNames(r);
            const failed = Array.isArray(r.failed) ? r.failed : [];
            if (deleted.length) {
                deleted.forEach(name => state.resultsSelectedFilenames.delete(name));
                const previewName = state.previewClipIdx >= 0 ? state.results[state.previewClipIdx]?.filename : null;
                if (previewName && deleted.includes(previewName)) {
                    closePreview();
                }
                await refreshScheduleFromBackend(false);
                await loadResults();
                toast(
                    failed.length
                        ? `Deleted ${deleted.length}; ${failed.length} could not be deleted`
                        : `Deleted ${deleted.length} clip${deleted.length !== 1 ? 's' : ''}`,
                    failed.length ? 'warning' : 'success'
                );
            } else {
                toast(deleteFailureMessage(r), 'error');
            }
        } catch (e) { toast('Delete failed: ' + e, 'error'); }
    } else if (state.pendingDeleteSource === 'library' && state.pendingDeleteFilename) {
        try {
            await releaseLocalVideoHandlesBeforeDelete([state.pendingDeleteFilename]);
            const r = await pywebview.api.delete_library_files([state.pendingDeleteFilename]);
            console.debug('[delete] library video', r);
            const removed = deletedOrPrunedNames(r);
            if (removed.length) {
                const physicallyDeleted = Array.isArray(r.deleted) && r.deleted.length > 0;
                toast(deleteSuccessText('video', removed.length, !physicallyDeleted), 'success');
                if (state.previewLibraryClip?.filename === state.pendingDeleteFilename) {
                    closePreview();
                }
                state.librarySelectedFilenames.delete(state.pendingDeleteFilename);
                await refreshScheduleFromBackend(false);
                await loadLibrary();
            } else {
                toast(deleteFailureMessage(r), 'error');
            }
        } catch (e) { toast('Delete failed: ' + e, 'error'); }
    } else if (state.pendingDeleteSource === 'library-bulk' && state.pendingDeleteFilenames.length) {
        const filenames = [...state.pendingDeleteFilenames];
        try {
            await releaseLocalVideoHandlesBeforeDelete(filenames);
            const r = await pywebview.api.delete_library_files(filenames);
            console.debug('[delete] library bulk videos', r);
            const deleted = deletedOrPrunedNames(r);
            const failed = Array.isArray(r.failed) ? r.failed : [];
            if (deleted.length) {
                deleted.forEach(name => state.librarySelectedFilenames.delete(name));
                if (state.previewLibraryClip && deleted.includes(state.previewLibraryClip.filename)) {
                    closePreview();
                }
                await refreshScheduleFromBackend(false);
                await loadLibrary();
                toast(
                    failed.length
                        ? `Deleted ${deleted.length}; ${failed.length} could not be deleted`
                        : `Deleted ${deleted.length} video${deleted.length !== 1 ? 's' : ''}`,
                    failed.length ? 'warning' : 'success'
                );
            } else {
                toast(deleteFailureMessage(r), 'error');
            }
        } catch (e) { toast('Delete failed: ' + e, 'error'); }
    }

    state.pendingDeleteIdx = -1;
    state.pendingDeleteFilename = null;
    state.pendingDeleteFilenames = [];
    state.pendingDeleteSource = null;
}

/* ── Library (All Videos) ──────────────────────────────────────────────── */

async function loadLibrary() {
    try {
        const r = await pywebview.api.list_all_clips();
        state.libraryClips = r.clips || [];
        pruneLibrarySelection();

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
        return true;
    } catch (e) {
        console.error('Load library error:', e);
        return false;
    }
}

async function refreshLibrary() {
    const ok = await loadLibrary();
    toast(ok ? 'Library refreshed' : 'Could not refresh library', ok ? 'success' : 'error');
}

function _groupLibraryByStem(clips) {
    const groups = {};
    clips.forEach((clip, i) => {
        let stem = clip.source_stem;
        if (!stem) {
            const match = clip.filename.match(/^(.+?)_viral\d+/i);
            stem = match ? match[1] : 'Other';
        }
        if (!groups[stem]) groups[stem] = { stem, clips: [] };
        groups[stem].clips.push({ ...clip, _libIdx: i });
    });
    return Object.values(groups);
}

function _buildLibraryCard(clip) {
    const item = document.createElement('div');
    item.className = 'library-item';
    item.dataset.clipId = clip.clip_id || '';
    item.dataset.filename = clip.filename || '';
    const d = new Date(clip.modified * 1000);
    const dateStr = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    const momentLabel = momentLabelMarkup(clip, clip);
    const truthLabel = clipTruthMarkup(clip, clip, { editable: true });
    const isSelected = state.librarySelectedFilenames.has(clip.filename);
    const hasContext = !!creatorTitleContextForEntity(clip, clip);
    item.classList.toggle('selected', isSelected);
    item.innerHTML = `
        <div class="library-item-thumb" data-lib-url="${escHtml(clip.url)}">
            <div class="thumb-placeholder">
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            </div>
            <label class="library-select-check" title="Select video" aria-label="Select ${escHtml(clip.filename)}">
                <input class="library-select-input" type="checkbox" ${isSelected ? 'checked' : ''}>
                <span></span>
            </label>
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
            ${momentLabel}
            ${truthLabel}
            <div class="library-item-meta">
                <span>${clip.size_mb} MB</span>
                <span>${dateStr}</span>
            </div>
            <div class="result-card-actions library-card-actions">
                <button type="button" class="result-ai-notes-btn${hasContext ? ' has-context' : ''}">AI Notes</button>
            </div>
            <div class="result-feedback library-feedback" aria-label="Clip feedback">
                ${feedbackButtonsMarkup()}
            </div>
        </div>`;
    // Lazy-load thumbnail
    const thumbEl = item.querySelector('.library-item-thumb');
    if (thumbEl) {
        thumbEl.addEventListener('click', () => previewLibraryClip(clip.filename, clip.url, clip));
    }
    const selectInput = item.querySelector('.library-select-input');
    const selectLabel = item.querySelector('.library-select-check');
    if (selectLabel) selectLabel.addEventListener('click', event => event.stopPropagation());
    if (selectInput) {
        selectInput.addEventListener('click', event => event.stopPropagation());
        selectInput.addEventListener('change', event => {
            event.stopPropagation();
            setLibrarySelected(clip.filename, selectInput.checked);
        });
    }
    const gameEditBtn = item.querySelector('.is-edit-game');
    if (gameEditBtn) {
        gameEditBtn.addEventListener('click', event => {
            event.stopPropagation();
            openLibraryGameTitleModal(clip);
        });
    }
    const aiNotesBtn = item.querySelector('.result-ai-notes-btn');
    if (aiNotesBtn) {
        aiNotesBtn.addEventListener('click', event => {
            event.stopPropagation();
            openLibraryTitleContextModal(clip);
        });
    }
    item.querySelectorAll('.feedback-btn').forEach(btn => {
        btn.addEventListener('click', event => {
            event.stopPropagation();
            recordLibraryFeedback(clip, btn.dataset.feedback);
        });
    });
    const deleteBtn = item.querySelector('.library-item-delete');
    if (deleteBtn) {
        deleteBtn.addEventListener('click', (event) => {
            event.stopPropagation();
            requestDeleteLibrary(clip.filename);
        });
    }
    if (clip.url && thumbEl) lazyThumb(thumbEl, clip.url);
    renderCardFeedbackState(item, clip);
    return item;
}

function renderLibraryGrid() {
    const grid = document.getElementById('library-grid');
    const empty = ensureEmptyState('library-empty', 'No videos in library', 'Generated clips will appear here', 'folder');
    const searchTerm = (document.getElementById('library-search-input')?.value || '').toLowerCase();

    const searchFiltered = searchTerm
        ? state.libraryClips.filter(c => c.filename.toLowerCase().includes(searchTerm))
        : state.libraryClips;
    state.libraryMomentFilter = normalizeAvailableMomentFilter(
        searchFiltered,
        clip => clip,
        state.libraryMomentFilter,
    );
    renderMomentFilterBar(
        'library-moment-filters',
        searchFiltered,
        clip => clip,
        state.libraryMomentFilter,
        setLibraryMomentFilter,
    );
    const filtered = searchFiltered.filter(clip =>
        clipMatchesMomentFilter(clip, clip, state.libraryMomentFilter)
    );
    const libCountEl = document.getElementById('library-count');
    if (libCountEl) {
        libCountEl.textContent = filtered.length !== state.libraryClips.length
            ? `${filtered.length}/${state.libraryClips.length} videos`
            : (state.libraryClips.length ? state.libraryClips.length + ' video' + (state.libraryClips.length !== 1 ? 's' : '') : '');
    }

    if (!filtered.length) {
        grid.innerHTML = '';
        grid.appendChild(empty);
        empty.style.display = '';
        state.libraryVisibleFilenames = [];
        renderLibrarySelectionState();
        return;
    }
    state.libraryVisibleFilenames = filtered.map(libraryFilenameKey).filter(Boolean);

    const groups = _groupLibraryByStem(filtered);
    const frag = document.createDocumentFragment();
    const autoOpen = groups.length === 1;

    groups.forEach(group => {
        const totalMB = group.clips.reduce((sum, c) => sum + (parseFloat(c.size_mb) || 0), 0).toFixed(1);
        const folder = document.createElement('div');
        const hasOpenPref = Object.prototype.hasOwnProperty.call(state.libraryOpenFolders, group.stem);
        const isOpen = hasOpenPref ? state.libraryOpenFolders[group.stem] === true : autoOpen;
        folder.className = 'result-folder' + (isOpen ? ' open' : '');
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
    renderLibrarySelectionState();
}

const filterLibrary = _debounce(() => {
    renderLibraryGrid();
}, 200);
window.filterLibrary = filterLibrary;

function setLibraryMomentFilter(filter) {
    state.libraryMomentFilter = String(filter || 'all');
    renderLibraryGrid();
}

function setLibraryView(view) {
    state.libraryView = view;
    const grid = document.getElementById('library-grid');
    grid.classList.toggle('list-view', view === 'list');
    document.getElementById('lib-view-grid').classList.toggle('active', view === 'grid');
    document.getElementById('lib-view-list').classList.toggle('active', view === 'list');
}

function previewLibraryClip(filename, url, clip = null) {
    state.previewClipIdx = -1; // not from results
    state.previewLibraryClip = clip || { filename, url };
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
        hideYouTubeSetupCard();
    } else {
        statusText.textContent = 'No accounts connected';
        statusText.classList.remove('connected');
        showYouTubeSetupCard();
    }
    // Always show Add Account button (can add more accounts)
    channelArea.classList.toggle('hidden', !connected);
    renderUploadReadinessStrip();
    renderUploadSummary();
}

async function loadChannelsAndCategories() {
    try {
        const chRes = await pywebview.api.get_channels();
        state.channels = chRes.channels || [];
        state.categories = [{ id: DEFAULT_CATEGORY_ID, title: 'Gaming' }];
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

        const validChannelIds = new Set(state.channels.map(ch => ch.id));
        if (state.selectedChannel && !validChannelIds.has(state.selectedChannel)) {
            state.selectedChannel = state.channels[0]?.id || null;
        } else if (state.channels.length && !state.selectedChannel) {
            state.selectedChannel = state.channels[0].id;
        }
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
    renderUploadSummary();
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
    let loadedClips = false;

    // Import any clips dropped into the clips/ folder + refresh results
    try {
        const r = await pywebview.api.import_folder_clips();
        state.results = visibleClipList(r.clips || []);
        state.moments = r.moments || [];
        loadedClips = true;
    } catch (_) {
        // Fallback: just refresh from backend
        try {
            const r = await pywebview.api.get_results();
            state.results = visibleClipList(r.clips || []);
            state.moments = r.moments || [];
            loadedClips = true;
        } catch (e) {
            console.error('Upload section refresh failed:', e);
            toast('Could not refresh clips for upload', 'error');
        }
    }

    await refreshScheduleFromBackend(false);

    if (!state.results.length && !state.scheduled.length) {
        empty.style.display = '';
        content.classList.add('hidden');
        renderTimeline();
        renderCalendar();
        renderClipTray();
        renderUploadReadinessStrip();
        renderUploadSummary();
        return loadedClips;
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
    renderUploadReadinessStrip();
    renderUploadSummary();
    return loadedClips;
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
    const summary = document.getElementById('clip-tray-summary');
    const searchTerm = (document.getElementById('clip-tray-search')?.value || '').trim().toLowerCase();
    state.clipTraySearch = searchTerm;

    const allGroups = _groupClipsByStem(state.results);
    const groups = !searchTerm
        ? allGroups
        : allGroups.map(group => {
            const sourceMatches = group.stem.toLowerCase().includes(searchTerm);
            const clips = sourceMatches
                ? group.clips
                : group.clips.filter(clip => String(clip.filename || '').toLowerCase().includes(searchTerm));
            return { ...group, clips };
        }).filter(group => group.clips.length);

    const visibleCount = groups.reduce((sum, group) => sum + group.clips.length, 0);
    if (summary) {
        summary.textContent = state.results.length
            ? (visibleCount === state.results.length ? `${state.results.length} clips` : `${visibleCount}/${state.results.length} clips`)
            : '';
    }

    if (!groups.length) {
        if (state.results.length && searchTerm) {
            list.innerHTML = '<div class="clip-tray-empty">No matching clips</div>';
        }
        return;
    }

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
            <div class="tray-folder-main">
                <span class="tray-folder-toggle">&#9654;</span>
                <span class="tray-folder-name" title="${escHtml(group.stem)}">${escHtml(group.stem)}</span>
                <span class="tray-folder-count">${group.clips.length} ${group.clips.length === 1 ? 'clip' : 'clips'}</span>
                ${scheduledCount ? `<span class="tray-folder-scheduled">${scheduledCount} scheduled</span>` : ''}
            </div>
            <div class="tray-folder-actions" onclick="event.stopPropagation()">
                ${chDropdownHtml}
                <button class="tray-folder-sched-btn" title="Schedule all clips from this folder to selected channel">Schedule</button>
                <button class="tray-folder-ai-btn" title="Generate or reroll AI metadata only for clips in this folder">AI Metadata</button>
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

const filterClipTray = _debounce(() => {
    renderClipTray();
}, 150);
window.filterClipTray = filterClipTray;

function _createTrayClipEl(clip, idx) {
    const el = document.createElement('div');
    el.className = 'tray-clip';
    el.draggable = true;
    el.dataset.clipIdx = idx;
    const isScheduled = state.scheduled.some(s => s.clipIdx === idx && !s.uploaded);
    if (isScheduled) el.classList.add('scheduled');
    const hasContext = !!creatorTitleContextForClip(idx);
    el.innerHTML = `
        <div class="tray-clip-main">
            <span class="tray-clip-num">C${idx+1}</span>
            <span class="tray-clip-name">${escHtml(clip.filename)}</span>
            <span class="tray-clip-size">${escHtml(clip.size_mb)} MB</span>
        </div>
        <button class="tray-clip-context-btn${hasContext ? ' has-context' : ''}" title="Add AI notes for this clip only">AI Notes</button>`;
    el.addEventListener('dragstart', e => {
        e.dataTransfer.setData('text/plain', String(idx));
        e.dataTransfer.effectAllowed = 'copy';
        el.classList.add('dragging');
    });
    el.addEventListener('dragend', () => el.classList.remove('dragging'));
    const contextBtn = el.querySelector('.tray-clip-context-btn');
    if (contextBtn) {
        contextBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            openClipTitleContextModal(idx);
        });
    }
    return el;
}

function openClipTitleContextModal(clipIdx) {
    const idx = Number(clipIdx);
    const clip = state.results[idx];
    if (!clip) return;
    state.aiContextEditing = { clipIdx: idx };
    const title = document.getElementById('source-context-title');
    const field = document.getElementById('source-context-text');
    if (title) title.textContent = `AI Notes - C${idx + 1}`;
    if (field) {
        field.value = creatorTitleContextForClip(idx);
        field.focus();
    }
    showModal('source-context-modal');
}

function openLibraryTitleContextModal(clip) {
    if (!clip) return;
    state.aiContextEditing = { libraryClip: { ...clip } };
    const title = document.getElementById('source-context-title');
    const field = document.getElementById('source-context-text');
    if (title) title.textContent = `AI Notes - ${clip.filename || 'Clip'}`;
    if (field) {
        field.value = creatorTitleContextForEntity(clip, clip);
        field.focus();
    }
    showModal('source-context-modal');
}

function openClipGameTitleModal(clipIdx) {
    const idx = Number(clipIdx);
    const clip = state.results[idx];
    if (!clip) return;
    state.gameTitleEditing = { clipIdx: idx };
    const title = document.getElementById('game-title-modal-title');
    const field = document.getElementById('game-title-text');
    if (title) title.textContent = `Game - C${idx + 1}`;
    if (field) {
        field.value = gameTitleForClip(idx);
        field.focus();
    }
    showModal('game-title-modal');
}

function openLibraryGameTitleModal(clip) {
    if (!clip) return;
    state.gameTitleEditing = { libraryClip: { ...clip } };
    const title = document.getElementById('game-title-modal-title');
    const field = document.getElementById('game-title-text');
    if (title) title.textContent = `Game - ${clip.filename || 'Clip'}`;
    if (field) {
        field.value = gameTitleForEntity(clip, clip);
        field.focus();
    }
    showModal('game-title-modal');
}

function _resolveModalEditTarget(edit, resultsOnly = false) {
    const idx = Number(edit?.clipIdx);
    if (Number.isInteger(idx) && idx >= 0 && state.results[idx]) {
        return { clip: state.results[idx], clipIdx: idx, isLibrary: false };
    }
    if (!resultsOnly && edit?.libraryClip) {
        return { clip: edit.libraryClip, clipIdx: null, isLibrary: true };
    }
    return null;
}

function _updateClipContextStateFromSave(target, result, fields = {}) {
    const targetIdx = Number.isInteger(target?.clipIdx)
        ? target.clipIdx
        : (Number.isInteger(Number(result?.clip_index)) ? Number(result.clip_index) : -1);
    if (targetIdx >= 0 && state.results[targetIdx]) {
        Object.assign(state.results[targetIdx], fields);
        if (state.moments[targetIdx]) Object.assign(state.moments[targetIdx], fields);
    }
    const filename = String(target?.clip?.filename || result?.filename || '').trim();
    const clipId = String(result?.clip_id || target?.clip?.clip_id || '').trim();
    state.libraryClips.forEach(item => {
        const sameClip = (clipId && item.clip_id === clipId) || (filename && item.filename === filename);
        if (sameClip) Object.assign(item, fields);
    });
    return targetIdx;
}

async function saveClipGameTitleModal() {
    const edit = state.gameTitleEditing || {};
    const target = _resolveModalEditTarget(edit);
    if (!target) return closeModal('game-title-modal');
    const text = document.getElementById('game-title-text')?.value || '';
    try {
        const clip = target.clip || {};
        const r = await pywebview.api.save_clip_game_title(
            clip.clip_id || '',
            Number.isInteger(target.clipIdx) ? target.clipIdx : null,
            clip.filename || '',
            text
        );
        if (r && r.error) {
            toast(r.error, 'error');
            return;
        }
        const gameTitle = String(r?.game_title || '').trim();
        const targetIdx = _updateClipContextStateFromSave(target, r, { game_title: gameTitle });
        if (targetIdx >= 0 && state.moments[targetIdx]) {
            const moment = state.moments[targetIdx];
            moment.game_title = gameTitle;
            moment.game_title_hint = gameTitle;
            if (!moment.truth_summary || typeof moment.truth_summary !== 'object') moment.truth_summary = {};
            moment.truth_summary.game_title = gameTitle;
            if (!moment.generated_metadata || typeof moment.generated_metadata !== 'object') moment.generated_metadata = {};
            moment.generated_metadata.game_title = gameTitle;
        }
        let scheduledChanged = false;
        state.scheduled.forEach(item => {
            const sameClip = (item.clip_id && r?.clip_id && item.clip_id === r.clip_id) || (targetIdx >= 0 && Number(item.clipIdx) === targetIdx);
            if (!sameClip) return;
            if (String(item.game_title || '') !== gameTitle) {
                item.game_title = gameTitle;
                item.metadata_stale = true;
                updateScheduledDescriptionPreview(item);
                scheduledChanged = true;
            }
        });
        if (scheduledChanged) persistSchedule();
        closeModal('game-title-modal');
        renderResultsGrid();
        renderPreviewMomentLabel();
        renderClipTray();
        renderTimeline();
        renderCalendar();
        if (target.isLibrary && document.getElementById('section-library')?.classList.contains('active')) {
            await loadLibrary();
        }
        toast(gameTitle ? 'Game saved' : 'Game cleared', 'success');
    } catch (e) {
        console.error('Save clip game failed:', e);
        toast('Could not save game', 'error');
    }
}

async function saveAiContextModal() {
    const edit = state.aiContextEditing || {};
    const text = document.getElementById('source-context-text')?.value || '';
    const target = _resolveModalEditTarget(edit);
    if (!target) return closeModal('source-context-modal');
    try {
        const clip = target.clip || {};
        const r = await pywebview.api.save_clip_title_context(
            clip.clip_id || '',
            Number.isInteger(target.clipIdx) ? target.clipIdx : null,
            clip.filename || '',
            text
        );
        if (r && r.error) {
            toast(r.error, 'error');
            return;
        }
        const context = String(r?.creator_title_context || '').trim();
        const targetIdx = _updateClipContextStateFromSave(target, r, { creator_title_context: context });
        if (targetIdx >= 0 && state.moments[targetIdx]) {
            state.moments[targetIdx].creator_title_context = context;
            if (state.moments[targetIdx].generated_metadata) delete state.moments[targetIdx].generated_metadata;
        }
        let scheduledChanged = false;
        state.scheduled.forEach(item => {
            const sameClip = (item.clip_id && r?.clip_id && item.clip_id === r.clip_id) || (targetIdx >= 0 && Number(item.clipIdx) === targetIdx);
            if (!sameClip) return;
            const previous = String(item.creator_title_context || '').trim();
            item.creator_title_context = context;
            if (previous !== context) {
                item.description_generated = '';
                item.generated_description = '';
                item.metadata_stale = true;
                updateScheduledDescriptionPreview(item);
                scheduledChanged = true;
            }
        });
        if (scheduledChanged) persistSchedule();
        closeModal('source-context-modal');
        renderResultsGrid();
        renderPreviewMomentLabel();
        renderClipTray();
        renderTimeline();
        renderCalendar();
        if (target.isLibrary && document.getElementById('section-library')?.classList.contains('active')) {
            await loadLibrary();
        }
        toast(context ? 'Clip AI notes saved' : 'Clip AI notes cleared', 'success');
    } catch (e) {
        console.error('Save clip AI notes failed:', e);
        toast('Could not save clip AI notes', 'error');
    }
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
    const historyByDate = uploadHistoryByDate(filter);

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
                const status = scheduleItemStatus(s);
                chip.className = `cal-chip ${status.className}`;
                chip.innerHTML = `<span>C${s.clipIdx + 1}</span><span class="cal-chip-time">${escHtml(s.time || '')}</span>`;
                chip.title = `${s.title || 'Clip ' + (s.clipIdx + 1)} — ${s.time} (${status.label})`;
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

        const representedSent = new Set();
        (dayItems || []).forEach(s => {
            const status = scheduleItemStatus(s);
            if (!['sent', 'youtube_scheduled'].includes(status.key)) return;
            if (s.youtube_id) representedSent.add(`yt:${s.youtube_id}`);
            if (s.clip_id) representedSent.add(`clip:${s.clip_id}`);
        });
        const historyItems = (historyByDate[dateStr] || []).filter(row => {
            if (row.youtube_id && representedSent.has(`yt:${row.youtube_id}`)) return false;
            if (row.clip_id && representedSent.has(`clip:${row.clip_id}`)) return false;
            return true;
        });
        if (historyItems.length) {
            const marker = document.createElement('div');
            marker.className = 'cal-history-marker';
            marker.textContent = `${historyItems.length} sent`;
            marker.title = `${historyItems.length} previous upload${historyItems.length !== 1 ? 's' : ''} recorded for this day`;
            marker.onclick = (e) => { e.stopPropagation(); openDayDetailView(dateStr, dayItems || [], historyItems); };
            cell.appendChild(marker);
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
    renderUploadSummary();
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

function historyRowStatus(row = {}) {
    const status = String(row.status || row.upload_state || row.send_status || 'sent_to_youtube').toLowerCase();
    if (status === 'youtube_scheduled') return { className: 'youtube-scheduled', label: 'Sent - publishes later' };
    if (status === 'upload_failed' || status === 'failed') return { className: 'failed', label: 'Upload failed' };
    return { className: 'sent', label: 'Sent to YouTube' };
}

function historyRowTimeLabel(row = {}) {
    const local = String(row.date || '').trim() && String(row.time || '').trim()
        ? `${row.date}T${row.time}:00`
        : '';
    const raw = row.publish_at_utc || row.finished_at_utc || row.uploaded_at || local;
    if (!raw) return String(row.time || '—');
    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) return String(row.time || '—');
    return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

function openDayDetailView(dateStr, dayItems, historyItems = []) {
    state.pickerDate = dateStr;
    const d = new Date(dateStr + 'T12:00:00');
    const fmtDate = d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
    const sorted = [...(dayItems || [])].sort((a, b) => (a.time || '').localeCompare(b.time || ''));
    const historyRows = Array.isArray(historyItems) ? [...historyItems] : [];
    historyRows.sort((a, b) => historyRowTimeLabel(a).localeCompare(historyRowTimeLabel(b)));
    const scheduledCount = sorted.length;
    const historyCount = historyRows.length;
    const countText = scheduledCount && historyCount
        ? `${scheduledCount} scheduled, ${historyCount} history`
        : historyCount
            ? `${historyCount} historical upload${historyCount === 1 ? '' : 's'}`
            : `${scheduledCount} clip${scheduledCount === 1 ? '' : 's'}`;
    document.getElementById('day-detail-title').textContent = `${fmtDate} — ${countText}`;

    const list = document.getElementById('day-detail-list');
    list.innerHTML = '';

    // Add status summary line
    let sPending = 0, sSent = 0, sAttention = 0;
    sorted.forEach(s => {
        const status = scheduleItemStatus(s);
        if (['sent', 'youtube_scheduled'].includes(status.key)) sSent++;
        else if (['missed', 'failed', 'unknown', 'disconnected', 'invalid'].includes(status.key)) sAttention++;
        else sPending++;
    });
    const summaryParts = [];
    if (sPending > 0) summaryParts.push(`<span class="summary-pending">${sPending} pending</span>`);
    if (sSent > 0) summaryParts.push(`<span class="summary-uploaded">${sSent} sent</span>`);
    if (historyCount > 0) summaryParts.push(`<span class="summary-uploaded">${historyCount} history</span>`);
    if (sAttention > 0) summaryParts.push(`<span class="summary-missed">${sAttention} needs attention</span>`);
    const existingSummary = document.getElementById('day-detail-summary');
    if (existingSummary) existingSummary.remove();
    if (summaryParts.length) {
        const summaryEl = document.createElement('div');
        summaryEl.id = 'day-detail-summary';
        summaryEl.className = 'day-detail-summary';
        summaryEl.innerHTML = summaryParts.join('<span style="color:var(--text-3)">·</span>');
        list.parentNode.insertBefore(summaryEl, list);
    }

    if (sorted.length) {
        const activeTitle = document.createElement('div');
        activeTitle.className = 'day-detail-section-title';
        activeTitle.textContent = 'Current schedule';
        list.appendChild(activeTitle);
    }

    sorted.forEach(s => {
        const status = scheduleItemStatus(s);
        const statusClass = status.className;
        const statusLabel = status.label;

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
                    <span class="day-detail-time">${escHtml(s.time || '—')}</span>
                    <span class="day-detail-status ${statusClass}">${escHtml(statusLabel)}</span>
                    <span class="day-detail-privacy">${escHtml(s.privacy || 'public')}</span>
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

    if (historyRows.length) {
        const historyTitle = document.createElement('div');
        historyTitle.className = 'day-detail-section-title';
        historyTitle.textContent = 'Upload history';
        list.appendChild(historyTitle);
    }

    historyRows.forEach(row => {
        const status = historyRowStatus(row);
        const item = document.createElement('div');
        item.className = `day-detail-item history ${status.className}`;
        const title = row.title || row.clip_filename || 'Uploaded clip';
        const channel = channelById(row.channel_id)?.title || row.channel_id || 'YouTube';
        const youtubeId = row.youtube_id ? ` · ${row.youtube_id}` : '';
        item.innerHTML = `
            <div class="day-detail-history-icon">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6 9 17l-5-5"/></svg>
            </div>
            <div class="day-detail-info">
                <div class="day-detail-item-title">${escHtml(title)}</div>
                <div class="day-detail-meta">
                    <span class="day-detail-time">${escHtml(historyRowTimeLabel(row))}</span>
                    <span class="day-detail-status ${escHtml(status.className)}">${escHtml(status.label)}</span>
                    <span class="day-detail-privacy">${escHtml(row.privacy || 'public')}</span>
                </div>
                <div class="day-detail-history-note">${escHtml(channel)}${escHtml(youtubeId)}</div>
            </div>`;
        list.appendChild(item);
    });

    showModal('day-detail-modal');
}

function removeDayDetailItem(idx) {
    state.scheduled.splice(idx, 1);
    persistSchedule();
    renderTimeline();
    renderCalendar();
    renderClipTray();
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
            `${missed.length} calendar send time${missed.length > 1 ? 's were' : ' was'} missed (app was offline)`;
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
    return channelById(state.selectedChannel) ? state.selectedChannel : null;
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
    } else if (state.selectedChannel && [...sel.options].some(o => o.value === state.selectedChannel)) {
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
    _scheduleClipIndices(indices, { clearExisting: false, channelId, focusCalendar: true });
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
    const { clearExisting = true, channelId = null, focusCalendar = false } = opts;

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

        const title = uploadTitleForClip(clip, i);
        state.scheduled.push({
            ...clipIdentityFields(clip, i),
            ...descriptionFieldsForClip(clip, i, title),
            ...channelIdentityFields(resolvedChannel),
            date: dateStr,
            time: time,
            title,
            tags: uploadTagsForClip(i),
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
    if (focusCalendar) {
        navigateTo('upload');
        window.setTimeout(() => focusUploadReadiness('schedule'), 100);
    }

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

    // AI metadata is generated manually via the upload page button.
}

// Title generation progress callback from backend (runs in background thread)
window.onTitleProgress = function (done, total, title) {
    const btn = document.getElementById('btn-gen-ai-titles');
    if (btn) btn.textContent = `Generating... ${done}/${total}`;
};

// Title generation completion callback from backend
window.onTitlesDone = function (r) {
    const btn = document.getElementById('btn-gen-ai-titles');
    if (btn) { btn.disabled = false; btn.textContent = 'Generate / Reroll AI Metadata'; }

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
                    if (!metadataMatchesScheduleItem(s, t)) {
                        s.metadata_stale = true;
                        s.metadata_identity_mismatch = true;
                        return;
                    }
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
        ? `AI generated metadata for ${r.renamed} clip${r.renamed !== 1 ? 's' : ''} and renamed files`
        : `Generated metadata for ${r.renamed} clip${r.renamed !== 1 ? 's' : ''} (install Ollama for better metadata)`;
    toast(msg, r.renamed ? 'success' : 'warning');
};

async function generateAITitlesManual() {
    const btn = document.getElementById('btn-gen-ai-titles');
    if (btn) { btn.disabled = true; btn.textContent = 'Generating... 0/?'; }

    try {
        toast('Transcribing clips and generating AI metadata...', 'info');
        await pywebview.api.generate_and_rename_all();
        // Results come via window.onTitlesDone callback
    } catch (e) {
        console.error('AI metadata generation error:', e);
        toast('AI metadata generation failed — check console', 'error');
        if (btn) { btn.disabled = false; btn.textContent = 'Generate / Reroll AI Metadata'; }
    }
}

async function generateAITitlesForFolder(stem, btn) {
    const indices = _findClipIndicesForStem(stem);
    if (!indices.length) return toast('No clips found in this folder', 'warning');

    if (btn) { btn.disabled = true; btn.textContent = '...'; }
    toast(`Generating AI metadata for "${stem}" (${indices.length} clips)...`, 'info');

    // Set up a folder-specific completion callback
    const origCallback = window.onTitlesDone;
    window.onTitlesDone = function (r) {
        // Restore original callback
        window.onTitlesDone = origCallback;
        if (btn) { btn.disabled = false; btn.textContent = 'AI Metadata'; }

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
            ? `AI generated metadata for ${count} clip${count !== 1 ? 's' : ''} in "${stem}"`
            : `Generated metadata for ${count} clip${count !== 1 ? 's' : ''} in "${stem}" (install Ollama for better metadata)`;
        toast(msg, count ? 'success' : 'warning');
    };

    try {
        await pywebview.api.generate_and_rename_indices(indices);
    } catch (e) {
        console.error('AI metadata generation error:', e);
        toast('AI metadata generation failed — check console', 'error');
        window.onTitlesDone = origCallback;
        if (btn) { btn.disabled = false; btn.textContent = 'AI Metadata'; }
    }
}

async function regenerateTitle(schedIdx) {
    const s = state.scheduled[schedIdx];
    if (!s) return;
    try {
        const contextInput = document.getElementById('modal-meta-creator-context');
        if (contextInput) {
            const previousContext = String(s.creator_title_context || '').trim();
            s.creator_title_context = String(contextInput.value || '').trim();
            if (previousContext !== s.creator_title_context) {
                s.description_generated = '';
                s.generated_description = '';
                await pywebview.api.save_scheduled(state.scheduled);
            }
        }
        const r = await pywebview.api.generate_title_for_clip(s.clipIdx, true, s.creator_title_context);
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
            const creatorContextInput = document.getElementById('modal-meta-creator-context');
            if (creatorContextInput) creatorContextInput.value = s.creator_title_context || '';
            updateMetaDescriptionPreview();
            const tagsInput = document.getElementById('modal-meta-tags');
            if (tagsInput && r.tags) tagsInput.value = r.tags;
            toast('AI metadata rerolled', 'success');
        } else {
            toast(r.error || 'No transcript available', 'warning');
        }
    } catch (_) { toast('AI metadata reroll failed', 'error'); }
}

function _toDateStr(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function _fmtDateFull(dateStr, timeStr) {
    const d = new Date(dateStr + 'T' + timeStr);
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
}

/* ── Schedule Timeline ────────────────────────────────────────────────── */

function renderTimeline() {
    const panel = document.getElementById('schedule-timeline');
    const list = document.getElementById('timeline-list');

    if (!state.scheduled.length) {
        panel.classList.add('hidden');
        if (list) list.innerHTML = '';
        clearStaleScheduleUi();
        renderUploadReadinessStrip();
        renderUploadSummary();
        return;
    }
    panel.classList.remove('hidden');

    const now = new Date();

    // Single-pass: count + build sorted array, with channel filter
    const filter = state.calChannelFilter;
    let sentCount = 0, attentionCount = 0;
    const sorted = state.scheduled.map((s, i) => {
        const status = scheduleItemStatus(s, now);
        if (['sent', 'youtube_scheduled'].includes(status.key)) sentCount++;
        else if (['missed', 'failed', 'unknown', 'disconnected', 'invalid'].includes(status.key)) attentionCount++;
        return { ...s, _idx: i };
    }).filter(s => filter === 'all' || !s.channel_id || s.channel_id === filter)
      .sort((a, b) => (`${a.date}T${a.time}` > `${b.date}T${b.time}` ? 1 : -1));

    const pendingCount = state.scheduled.length - sentCount - attentionCount;

    const summaryEl = document.getElementById('smart-sched-summary');
    if (summaryEl) {
        const parts = [];
        if (pendingCount > 0) parts.push(`${pendingCount} pending`);
        if (sentCount > 0) parts.push(`${sentCount} sent`);
        if (attentionCount > 0) parts.push(`${attentionCount} needs attention`);
        summaryEl.textContent = parts.join(' · ');
    }

    const frag = document.createDocumentFragment();
    sorted.forEach(s => {
        const status = scheduleItemStatus(s, now);
        const statusClass = status.className;
        const statusLabel = status.label;
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
                    <span class="timeline-date-val">${escHtml(dateFmt)}</span>
                    <span class="timeline-time-val">${escHtml(s.time || '')}</span>
                    ${chName ? `<span class="timeline-ch-name">${escHtml(chName)}</span>` : ''}
                </div>
            </div>
            <span class="timeline-status ${statusClass}">${escHtml(statusLabel)}</span>
            <button class="timeline-edit" onclick="event.stopPropagation(); removeScheduleAt(${s._idx})" title="Remove">&times;</button>`;
        frag.appendChild(item);
    });

    list.innerHTML = '';
    list.appendChild(frag);

    document.getElementById('scheduler-bar').classList.toggle('hidden', pendingCount === 0 && attentionCount === 0);
    renderUploadReadinessStrip();
    renderUploadSummary();
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
    renderClipTray();
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

function clearStaleScheduleUi() {
    const summaryEl = document.getElementById('smart-sched-summary');
    if (summaryEl) summaryEl.textContent = '';

    const schedulerBar = document.getElementById('scheduler-bar');
    if (schedulerBar) schedulerBar.classList.add('hidden');
    const schedulerText = document.getElementById('scheduler-status-text');
    if (schedulerText) schedulerText.textContent = 'Local Upload Watcher active';

    const missedBanner = document.getElementById('missed-uploads-banner');
    if (missedBanner) missedBanner.classList.add('hidden');

    _cachedNextUpload = null;
    _nextUploadCacheTime = 0;
    removeNotificationsByType('uploading');
    renderUploadSummary();
}

function dismissMissedBanner() {
    document.getElementById('missed-uploads-banner').classList.add('hidden');
}

function dropClipOnDate(clipIdx, dateStr) {
    const clip = state.results[clipIdx];
    if (!clip) return;

    const title = uploadTitleForClip(clip, clipIdx);
    const channelId = _getScheduleChannelId() || state.selectedChannel || null;
    if (!channelId) return toast('Choose a YouTube channel before scheduling this clip', 'warning');
    const scheduledAt = _resolveSchedulableDateTime(dateStr);
    state.scheduled.push({
        ...clipIdentityFields(clip, clipIdx),
        ...descriptionFieldsForClip(clip, clipIdx, title),
        ...channelIdentityFields(channelId),
        date: scheduledAt.date,
        time: scheduledAt.time,
        title,
        tags: uploadTagsForClip(clipIdx),
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

    const title = uploadTitleForClip(clip, clipIdx);
    const channelId = _getScheduleChannelId() || state.selectedChannel || null;
    if (!channelId) return toast('Choose a YouTube channel before scheduling this clip', 'warning');
    const scheduledAt = _resolveSchedulableDateTime(dateStr, time);
    state.scheduled.push({
        ...clipIdentityFields(clip, clipIdx),
        ...descriptionFieldsForClip(clip, clipIdx, title),
        ...channelIdentityFields(channelId),
        date: scheduledAt.date,
        time: scheduledAt.time,
        title,
        tags: uploadTagsForClip(clipIdx),
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
    document.getElementById('modal-meta-creator-context').value = item.creator_title_context || creatorTitleContextForClip(item.clipIdx) || '';
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
    item.creator_title_context = (document.getElementById('modal-meta-creator-context')?.value || '').trim();
    item.description_custom_text = document.getElementById('modal-meta-desc')?.value || '';
    item.description_generated = item.description_generated || item.generated_description || item.title;
    updateScheduledDescriptionPreview(item);
    preview.textContent = item.final_description || item.description || '';
}

function saveMetaModal() {
    const idx = state.editingScheduleIdx;
    if (idx < 0 || !state.scheduled[idx]) return;

    const item = state.scheduled[idx];
    const previousTitle = String(item.title || '');
    const previousTime = String(item.time || '');
    const previousPrivacy = String(item.privacy || '');
    item.title = document.getElementById('modal-meta-title').value || 'Untitled';
    const previousContext = String(item.creator_title_context || '').trim();
    item.creator_title_context = (document.getElementById('modal-meta-creator-context')?.value || '').trim();
    item.description_custom_text = document.getElementById('modal-meta-desc').value || '';
    item.description_generated = previousContext !== item.creator_title_context
        ? ''
        : (item.description_generated || item.generated_description || item.title);
    item.generated_description = item.description_generated;
    if (previousContext !== item.creator_title_context) item.metadata_stale = true;
    item.tags = document.getElementById('modal-meta-tags').value;
    item.category_id = DEFAULT_CATEGORY_ID;
    item.privacy = document.getElementById('modal-meta-privacy').value;
    item.time = document.getElementById('modal-meta-time').value;
    if (
        previousTitle !== String(item.title || '') ||
        previousTime !== String(item.time || '') ||
        previousPrivacy !== String(item.privacy || '')
    ) {
        clearScheduleBackendStatus(item);
    }
    updateScheduledDescriptionPreview(item);

    closeModal('meta-modal');
    persistSchedule();
    renderTimeline();
    renderCalendar();
    renderClipTray();
}

function closeMetaModal() { closeModal('meta-modal'); }

function removeScheduledItem() {
    const idx = state.editingScheduleIdx;
    if (idx >= 0) { state.scheduled.splice(idx, 1); state.editingScheduleIdx = -1; }
    closeModal('meta-modal');
    persistSchedule();
    renderTimeline();
    renderCalendar();
    renderClipTray();
}

/* ── Upload ───────────────────────────────────────────────────────────── */

async function toggleAutoDelete(enabled) {
    try { await pywebview.api.set_delete_after_upload(enabled); } catch (_) {}
}

async function refreshUploadClips() {
    toast('Scanning clips folder...', 'info');
    const ok = await loadUploadSection();
    if (ok) toast('Clips refreshed', 'success');
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
window.onClipDeleted = async function(payload, legacyFilename) {
    const deleted = (payload && typeof payload === 'object')
        ? payload
        : { clipIdx: payload, filename: legacyFilename };
    const clipId = String(deleted.clipId || deleted.clip_id || '').trim();
    const filename = String(deleted.filename || '').trim();
    toast(`Deleted "${filename || 'clip'}" from disk`, 'info');
    let idx = state.results.findIndex(clip =>
        (clipId && String(clip.clip_id || '').trim() === clipId)
        || (filename && String(clip.filename || '').trim() === filename)
    );
    if (idx < 0 && !clipId && !filename && Number.isInteger(Number(deleted.clipIdx))) {
        idx = Number(deleted.clipIdx);
    }
    if (idx >= 0 && idx < state.results.length) {
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
    await refreshScheduleFromBackend(true);
    if (!state.scheduled.length) return toast('Add clips to the calendar first', 'warning');

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
    if (pending.some(s => scheduleItemStatus(s).key === 'sending')) {
        return toast('An upload is already running. Wait for it to finish before sending again.', 'warning');
    }
    if (pending.some(s => scheduleItemStatus(s).key === 'unknown')) {
        return toast('A clip may already have been sent. Check YouTube Studio, then remove or reschedule it before sending again.', 'error');
    }
    if (pending.some(s => scheduleItemStatus(s).key === 'failed')) {
        return toast('One or more failed uploads is waiting for its retry time. Try again in a few minutes.', 'warning');
    }
    if (pending.some(s => ['missed', 'disconnected', 'invalid'].includes(scheduleItemStatus(s).key))) {
        return toast('Resolve schedule items that need attention before uploading.', 'error');
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
        creator_title_context: s.creator_title_context || '',
        tags: (s.tags || '').split(',').map(t => t.trim()).filter(Boolean),
        privacy: s.privacy || 'private',
        channel_id: s.channel_id,
        account_id: s.account_id,
        ...scheduledPublishFields(s),
    }));

    if (!clipsMetadata.length) return toast('All clips already uploaded', 'warning');
    if (!clipsMetadata.every(meta => meta.account_id)) return toast('Please select a YouTube channel first', 'warning');
    if (!clipsMetadata.every(meta => meta.publish_at)) return toast('Scheduled clips need a valid publish time', 'error');
    const now = new Date();
    const minPublicPublishDate = new Date(now.getTime() + SCHEDULE_BUFFER_MINUTES * 60 * 1000);
    if (
        pending.some(s => s.privacy === 'public' && s._scheduledDate <= now) ||
        pending.some(s => s.privacy === 'public' && s._scheduledDate <= minPublicPublishDate)
    ) {
        return toast(`Public uploads need a publish time at least ${SCHEDULE_BUFFER_MINUTES} minutes from now. Reschedule missed uploads first.`, 'error');
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
        const r = await pywebview.api.start_upload(clipsMetadata, null);
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
    setSlider('set-clip-duration', clampClipDuration(s.clip_duration));
    setSlider('set-min-gap', s.min_gap);
    setSlider('set-crf', s.video_crf);
    state.generationMode = normalizeGenerationMode(s.generation_mode);
    updateGenerationModeUi();
    syncWizardModeUi();
    state.wizardProcessingDepth = normalizeProcessingDepth(s.processing_depth);
    const montage = s.montage || {};
    state.wizardMontageTemplate = normalizeMontageTemplate(montage.template || state.wizardMontageTemplate);
    setSelect('set-detection-preference', normalizeDetectionPreference(s.detection_preference));
    const visualDiagnostics = document.getElementById('set-visual-diagnostics');
    if (visualDiagnostics) visualDiagnostics.checked = s.visual_diagnostics !== false;
    const aiMomentClassification = document.getElementById('set-ai-moment-classification');
    if (aiMomentClassification) aiMomentClassification.checked = s.ai_moment_classification === true;
    const momentCategoryRanking = document.getElementById('set-moment-category-ranking');
    if (momentCategoryRanking) momentCategoryRanking.checked = s.moment_category_ranking === true;
    setSelect('set-model', s.whisper_model);
    setSelect('set-preset', s.ffmpeg_preset);
    setVal('set-language', s.whisper_language || '');
    const crop = document.getElementById('set-crop-vertical');
    if (crop) crop.checked = s.crop_vertical !== false;
    setOutputFolderDisplay(s.output_dir || '');
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
        generation_mode: normalizeGenerationMode(state.generationMode),
        num_clips: autoClips ? 'auto' : parseInt(getVal('set-num-clips')),
        processing_depth: normalizeProcessingDepth(state.wizardProcessingDepth || state.settings?.processing_depth),
        detection_preference: normalizeDetectionPreference(getVal('set-detection-preference')),
        montage: montageSettingsFromWizard(),
        clip_duration: clampClipDuration(getVal('set-clip-duration')),
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
        output_dir: state.settings?.output_dir || '',
        description_profile: descriptionProfile(),
        visual_diagnostics: document.getElementById('set-visual-diagnostics')?.checked ?? true,
        ai_moment_classification: document.getElementById('set-ai-moment-classification')?.checked ?? false,
        moment_category_ranking: document.getElementById('set-moment-category-ranking')?.checked ?? false,
        voice_profile_ranking: Boolean(state.settings?.voice_profile_ranking),
    };
    state.settings = { ...state.settings, ...s };
    saveLocal('settings', s);
    persistSettingsAsync(s);
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

async function refreshSubtitlePreviewSnapshot(force = false) {
    const preview = document.getElementById('subtitle-placement-preview');
    const empty = document.getElementById('subtitle-placement-snapshot-empty');
    if (!preview || !window.pywebview || !pywebview.api) return;
    try {
        const r = await pywebview.api.get_subtitle_preview_url();
        const url = r && r.url ? String(r.url) : '';
        if (!url) {
            resetSubtitlePreviewSnapshot(false);
            return;
        }
        if (!force && _subtitlePreviewUrl === url && preview.classList.contains('has-snapshot')) {
            return;
        }
        _subtitlePreviewUrl = url;
        preview.classList.remove('snapshot-empty');
        preview.classList.add('has-snapshot');
        if (empty) empty.textContent = r.filename ? `Preview: ${r.filename}` : '';
        generateThumbnail(url, preview, 1.5);
        const largePreview = document.getElementById('subtitle-placement-preview-large');
        if (largePreview && !largePreview.closest('.modal')?.classList.contains('hidden')) {
            largePreview.classList.remove('snapshot-empty');
            largePreview.classList.add('has-snapshot');
            const largeEmpty = document.getElementById('subtitle-placement-large-empty');
            if (largeEmpty) largeEmpty.textContent = r.filename ? `Preview: ${r.filename}` : '';
            generateThumbnail(url, largePreview, 1.5);
        }
    } catch (_) {
        if (!preview.classList.contains('has-snapshot')) resetSubtitlePreviewSnapshot(false);
    }
}

function resetSubtitlePreviewSnapshot(showToast = true) {
    const preview = document.getElementById('subtitle-placement-preview');
    const largePreview = document.getElementById('subtitle-placement-preview-large');
    const empty = document.getElementById('subtitle-placement-snapshot-empty');
    const largeEmpty = document.getElementById('subtitle-placement-large-empty');
    _subtitlePreviewUrl = '';
    if (preview) {
        preview.classList.remove('has-snapshot');
        preview.classList.add('snapshot-empty');
        preview.style.backgroundImage = '';
        preview.style.backgroundSize = '';
        preview.style.backgroundPosition = '';
    }
    if (largePreview) {
        largePreview.classList.remove('has-snapshot');
        largePreview.classList.add('snapshot-empty');
        largePreview.style.backgroundImage = '';
        largePreview.style.backgroundSize = '';
        largePreview.style.backgroundPosition = '';
    }
    if (empty) empty.textContent = 'Preview uses your latest clip when available';
    if (largeEmpty) largeEmpty.textContent = 'Preview uses your latest clip when available';
    if (showToast) toast('Subtitle preview reset', 'info');
}

function updateSubtitlePlacementPreview() {
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

    ['subtitle-placement-box', 'subtitle-placement-box-large'].forEach(id => {
        const box = document.getElementById(id);
        if (!box) return;
        box.style.left = adjustedX + '%';
        box.style.top = y + '%';
        box.style.width = width + '%';
        box.className = `subtitle-placement-box subtitle-placement-${style}`;
        box.textContent = captionsOff ? 'NO CAPTIONS' : 'TRANSCRIPT TEXT';
    });
}

async function openSubtitlePreviewModal() {
    const largePreview = document.getElementById('subtitle-placement-preview-large');
    const largeEmpty = document.getElementById('subtitle-placement-large-empty');
    if (!largePreview) return;
    updateSubtitlePlacementPreview();
    if (!_subtitlePreviewUrl) await refreshSubtitlePreviewSnapshot(false);
    if (_subtitlePreviewUrl) {
        largePreview.classList.remove('snapshot-empty');
        largePreview.classList.add('has-snapshot');
        if (largeEmpty) largeEmpty.textContent = '';
        generateThumbnail(_subtitlePreviewUrl, largePreview, 1.5);
    } else {
        largePreview.classList.remove('has-snapshot');
        largePreview.classList.add('snapshot-empty');
        largePreview.style.backgroundImage = '';
        if (largeEmpty) largeEmpty.textContent = 'Generate a clip first, then open this preview again.';
    }
    showModal('subtitle-preview-modal');
}

function updateSliderLabel(el) {
    const lbl = document.getElementById('val-' + el.id.replace('set-', ''));
    if (!lbl) return;
    if (el.id === 'set-clip-duration') {
        const v = clampClipDuration(el.value);
        if (String(el.value) !== String(v)) el.value = v;
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

function clampClipDuration(value) {
    const n = parseInt(value, 10);
    if (!Number.isFinite(n)) return 30;
    return Math.max(10, Math.min(180, n));
}

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
function setNodeText(id, value) { const el = document.getElementById(id); if (el) el.textContent = value; }
function saveLocal(k, d) { try { localStorage.setItem('viria_'+k, JSON.stringify(d)); } catch (_) {} }
function loadLocal(k, fb) { try { const d = localStorage.getItem('viria_'+k); return d ? JSON.parse(d) : fb; } catch (_) { return fb; } }

/* ── Toast / Modal ─────────────────────────────────────────────────────── */

function toast(msg, type = 'info') {
    const c = document.getElementById('toast-container');
    if (!c) {
        console.log(`[toast:${type}] ${msg}`);
        return;
    }
    const el = document.createElement('div');
    el.className = `toast ${type}`; el.textContent = msg;
    c.appendChild(el);
    setTimeout(() => { el.classList.add('removing'); setTimeout(() => el.remove(), 300); }, 4000);
}

function safeToast(msg, type = 'info') {
    try {
        toast(msg, type);
    } catch (e) {
        console.warn('Toast failed', e);
    }
}

function safeAddNotification(title, desc, type = 'info', options = {}) {
    try {
        return addNotification(title, desc, type, options);
    } catch (e) {
        console.warn('Notification failed', e);
        return null;
    }
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

function removeNotificationsByType(type) {
    let removedAny = false;
    let removedUnread = 0;
    for (let i = _notifications.length - 1; i >= 0; i--) {
        if (_notifications[i].type !== type) continue;
        removedAny = true;
        if (_notifications[i].unread) removedUnread++;
        _notifications.splice(i, 1);
    }
    if (!removedAny) return;
    _notifUnreadCount = Math.max(0, _notifUnreadCount - removedUnread);
    _updateNotifBadge();
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
        if (empty) {
            list.appendChild(empty);
            empty.style.display = '';
        }
        return;
    }
    if (empty) empty.style.display = 'none';

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
