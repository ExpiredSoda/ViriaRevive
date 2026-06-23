<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/FFmpeg-required-007808?style=for-the-badge&logo=ffmpeg&logoColor=white" alt="FFmpeg">
  <img src="https://img.shields.io/badge/Ollama-optional-FF6B6B?style=for-the-badge" alt="Ollama">
  <img src="https://img.shields.io/badge/license-MIT-blue?style=for-the-badge" alt="License">
  <img src="https://img.shields.io/badge/platform-Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white" alt="Windows">
</p>

<h1 align="center">
  <br>
  ViriaRevive
  <br>
  <sub><sup>AI-assisted gameplay Shorts clipper and scheduler</sup></sub>
</h1>

<p align="center">
  Turn long gameplay videos or YouTube URLs into short-form clips with local detection,
  transcript-aware reranking, optional subtitles, AI titles, metadata sidecars, and optional YouTube scheduling.
</p>

<p align="center">
  <strong>Current version: v2.2.0</strong>
</p>

<p align="center">
  <img src="docs/preview.png" alt="ViriaRevive Preview" width="850">
</p>

---

## Fork Notes

This repository is a workflow-focused fork of the original ViriaRevive project:
[VladPolus/ViriaRevive](https://github.com/VladPolus/ViriaRevive).

The upstream README describes the original baseline app. This fork keeps the same desktop GUI and CLI direction, but the local codebase now includes several changes that are important for gameplay recordings, especially OBS MKVs with separate microphone and game audio tracks.

Major differences from upstream:

- Transcript-aware clip reranking that prefers strong spoken hooks over raw loudness alone.
- Local moment classification with compact `primary_category` labels, based on transcript, detector/ranker stats, optional visual frame analysis, and an opt-in local Ollama AI label pass.
- A 5-step Generate wizard splits **Detection** from **Subtitle Style**, so clip-finding settings are not mixed with caption styling.
- Processing Depth presets: **Fast**, **Balanced**, and **Deep Analysis**, with fixed clip counts treated as a Quality pass and Auto clip count exposing the Quality/Quantity preference.
- Multi-track audio support with a Generate wizard Audio Sources step: detection/rendering can keep the full audio mix while transcription can use Auto or a selected mic/game track.
- Batch queue rows inherit the wizard's subtitle style, including **None** for no captions, so caption behavior stays in one predictable setup flow.
- Single-track commentary guard for mixed one-track audio, with creator/all/game subtitle policy choices for one-track YouTube downloads and mixed OBS exports.
- Alternate speech-stream retries when the first transcription is too weak or starts too late.
- Bounded FFmpeg scene diagnostics with sampled/targeted scan windows and explicit statuses such as `ok`, `zero_changes`, `sampled_ok`, `targeted_ok`, `timeout`, `ffmpeg_missing`, and `ffmpeg_error`; long videos are skipped, sampled, or targeted internally based on **Processing Depth**, and PySceneDetect is only used when runtime guards allow it.
- Progress messaging separates analyzing candidate moments from rendering final clips, with local-history-backed expectation/ETA-style text during longer work and a scene-scrub detail line such as `Detecting scenes: 01:12:00 / 02:43:00` during long scene scans.
- Multi-source batches show a dedicated source progress header with the current file/link, source counts, and nearby queue items so the per-video progress bar can restart without hiding which source is active.
- If no candidate meets the current quality bar, the run completes as a clear **No Clips Created** warning instead of a failed batch, while still saving run debug and timing history.
- Run and candidate debug JSON reports for inspecting why clips were selected or rejected, plus a tested backend-only recovery helper for developer/debug use after a crash.
- Stable state schema v2 with `clip_id`, `source_id`, atomic writes, and migration backups.
- Local feedback adds a small capped learned score into candidate selection.
- Balanced and Deep Analysis can apply a small capped `+/-0.020` moment-category nudge for close calls using deterministic high-energy, death/failure, tutorial/explainer, lore/story, atmosphere, and low-value labels; Fast keeps it off. The same pass includes a tiny diversity helper so one close category does not crowd out every other useful moment type.
- Deterministic category labels use transcript meaning, creator-vs-game speech cues, acoustic intensity, and visual frame analysis together, avoiding technical/explainer or game-narration phrases being treated as high-energy panic just because the source audio is loud.
- Optional local Creator Voice Profile is off by default, stores only numeric acoustic features, requires explicit local enrollment from multiple eligible samples with enough active speech, reports capped debug-only voice shadow scoring, nudges you after liked/favorite creator-commentary clips are eligible for enrollment, and can use a separate opt-in capped local-only ranking nudge once enrolled.
- Results, Preview, and All Videos share stable local clip previews, feedback state, optional saved moment-label chips, and thumbnail sampling that skips early black frames when a better frame is available.
- YouTube upload metadata defaults for Gaming, larger tag sets, and required Shorts/game hashtags.
- Customizable upload descriptions with generated context, optional creator text, and recommended Shorts/game hashtags.
- Ollama status indicator, opt-in setup controls, game-aware title generation, and optional AI moment labels using `qwen2.5:3b` when available. Deep Analysis can use high-confidence local Ollama labels as a guarded `+/-0.015` close-call ranking nudge.
- Better schedule cleanup when clips are deleted, moved, or refreshed.
- Central version metadata in `version.py`, wired through the GUI, app window, PyInstaller, Inno Setup, and release naming.
- Public-release setup helpers for YouTube OAuth, app data, FFmpeg downloads, and app-local `bin/` folder setup.

Current fork state:

- This checkout tracks [ExpiredSoda/ViriaRevive](https://github.com/ExpiredSoda/ViriaRevive) as `origin` and keeps [VladPolus/ViriaRevive](https://github.com/VladPolus/ViriaRevive) as read-only `upstream`.
- Runtime data is separated from installed program files in packaged builds; source checkouts still keep local runtime folders beside the code for development.
- ZIP and installer packaging scripts exist, and both run version sync, dependency pin, and release safety checks before packaging.
- `build.bat` produces versioned ZIP artifacts and SHA256 sidecars while keeping a stable latest ZIP name.

Current release hardening:

- One central `APP_VERSION` source drives the GUI, Python bridge, app window title, PyInstaller metadata, Inno Setup metadata, and release package naming.
- Build scripts run from their own folder, fail on required tooling errors, scan staged output, and clear the current version/latest ZIP artifacts before rebuilding.
- Release safety scanning blocks known local runtime data, OAuth/token files, `.env*`, personalization/voice-profile/state backups, debug reports, private folders, and obvious private JSON markers.
- `.gitignore` excludes OAuth files, token folders, `.env*`, generated state, debug reports, local media, models, private backups, and release artifacts.
- App-local FFmpeg support packages only allowlisted `bin/` files: `README.md`, `ffmpeg.exe`, and `ffprobe.exe`.
- YouTube setup remains bring-your-own OAuth credentials, but the app now opens Google credentials and the exact app data folder from the UI.
- Connected YouTube accounts and Ollama status are based on real local token/service checks; OAuth client JSON files are not treated as connected accounts.
- `build_installer.bat` requires Inno Setup and `ISCC.exe`; `build.bat` alone creates the ZIP app.

Remaining public-release work:

- Add a full dependency vulnerability/SBOM audit before broad public distribution.
- Settle the public binary dependency/license posture before broad distribution, especially for bundled native tools or model/runtime dependencies.
- Decide whether public binaries require system FFmpeg or ship reviewed app-local FFmpeg binaries with matching license/source notices.
- Add code signing before broad public distribution so Windows users see a more trustworthy install path.
- Consider a verified public Google OAuth client later, if the project is distributed beyond personal/dev use.
- Add DPAPI/keyring protection for YouTube token files.

---

## What It Does

ViriaRevive is a local desktop app for finding short-form moments in longer videos. It downloads or accepts local video files, detects candidate moments using audio and scene signals, transcribes likely speech, reranks candidates by transcript quality, trims around hooks/payoffs, renders clips with optional subtitles, and helps schedule or upload them to YouTube.

Processing runs locally. YouTube upload is optional and uses your own Google OAuth credentials. Ollama is optional and runs locally for better title generation and opt-in AI moment labels. Classification/debug labels use local transcript, detector/ranker, and visual-diagnostic summaries; raw media is not sent to cloud services by the classification path.

---

## Features

### Detection And Ranking

- Audio-energy and scene-change candidate detection.
- Transcript-aware second-stage reranking.
- Compact local moment categories such as `high_energy`, `death_or_failure`, `tutorial_or_explainer`, `commentary_or_review`, `lore_or_story`, `atmosphere_or_visual`, and `low_value`.
- Processing Depth presets for **Fast**, **Balanced**, and **Deep Analysis** runs.
- Long-video scene detection can use sampled/skipped scene analysis on faster presets; **Deep Analysis** uses audio/variance peaks plus timeline anchors to target likely scene windows before candidate reranking.
- Capped learned-score blending from local like, dislike, and favorite feedback.
- Hook boosting for panic/chase/reaction phrases.
- Weak aftermath/navigation penalties so fewer low-quality filler clips are returned.
- Asymmetric gameplay windows that favor lead-up before the peak.
- Pre-event rescue candidates for death/restart peaks.
- Fewer-but-better output policy when only a small number of clips are strong.
- Clean no-quality completions when the app finds candidates but rejects them all under the current quality bar; this is treated as a normal quality outcome, not a crash.

### Multi-Track Audio

- Detects source audio streams with `ffprobe`.
- The Generate wizard has five steps: **Style** -> **Detection** -> **Audio** -> **Effects** -> **Music**.
- The **Audio Sources** step can auto-detect commentary or use a selected track for transcription on a single local multi-track source.
- Local files are probed before generation; online videos are checked after download, so **Auto detect commentary** is the safest default for URLs and multi-file batches.
- Audio probe messages distinguish a true **No audio tracks found** result from probe timeouts, FFmpeg errors, or files that could not be inspected.
- Mixes multiple audio streams for analysis and rendered clip audio.
- Selects the speech stream by Whisper sampling instead of trusting OBS labels alone, with a saved scoring profile that shows mic/game title hints, natural-dialogue score, creator/game phrase signals, acoustic game-bed score, lyric-likelihood checks, mic creator-preference bonus, runner-up stream, reason, and confidence.
- Lets OBS-style recordings use a mic/commentary track for subtitles while the rendered clip still keeps the full source audio mix.
- If only one mixed track exists, Auto transcribes that single track and can use the commentary guard to lightly choose subtitle/title-facing words. The default keeps creator commentary, with opt-ins to include all speech or prefer game/NPC narration.
- Retries alternate streams when subtitles have too few words or poor timing, but the default creator-only policy now accepts an alternate only when it looks like natural human commentary. If the alternate looks like game/system audio, scripted narration, music lyrics without enough creator context, or speech over a strong background bed, the app keeps the original stream instead of burning game subtitles. The **Include all speech** and **Prefer game/NPC speech** policies remain explicit opt-ins.
- Keeps mic speech scoring separate from gameplay audio intensity.

### Subtitles And Rendering

- Faster-Whisper transcription.
- ASS subtitle burn-in with word-level timing.
- Subtitle styles: TikTok, Karaoke, Neon Glow, Clean, Bold, Minimal, and None for clips with no words on screen.
- Caption box placement controls for horizontal position, vertical position, and width on vertical clips.
- Settings show a real subtitle preview snapshot so style and placement choices can be checked before rendering.
- FFmpeg subtitle filter fallback between `subtitles` and `ass`.
- Windows font handling for libass subtitle rendering.
- YOLO/OpenCV person-aware crop for horizontal footage.
- Already-vertical footage can pass through without forced recrop.
- Optional video effects and background music from the local `music/` folder.
- Progress text separates candidate analysis from final clip rendering, so a long "analyzing candidates" phase does not look like clips should already be finished.
- Long Deep Analysis scene scans can show a plain video-time readout under the estimate line, for example `Detecting scenes: 01:12:00 / 02:43:00`, instead of adding another percentage. Deep candidate screening can use a lighter Whisper model for speed while final selected clips still use the chosen subtitle/transcription model.

### Titles, Metadata, And Sidecars

- Ollama title generation with `qwen2.5:3b` when available.
- Heuristic title fallback when Ollama is unavailable.
- Title prompts reuse saved clip-generation analysis: transcript, game title, detector scores, candidate kind/rank, selected timing, ranker hook/weak/aftermath points, learned score, and subtitle word count. On guarded mixed one-track clips, the title-facing transcript follows the same conservative subtitle policy and fallback as burned-in captions.
- Descriptions, tags, and sidecar files use the same compact title context so metadata reflects the actual selected moment instead of only the raw transcript.
- Titles are formatted with `#shorts` plus a game hashtag.
- Upload category is forced to YouTube Gaming (`20`).
- Tag generation stays roughly 100 characters under YouTube's tag limit.
- Default descriptions are neutral and compact by default, with clip context plus `#shorts`, the game hashtag, and `#gaming`.
- The Upload page has compact description controls for recommended hashtags and default custom text.
- Each scheduled clip metadata modal keeps generated description text read-only: you can edit title, tags, visibility/time, and custom description text, then preview the final composed description so AI metadata refreshes do not overwrite creator copy.
- Generated metadata is stored on each persisted moment under `generated_metadata` and also written to `.txt` sidecars.
- `.txt` metadata sidecars can be generated beside clips for manual YouTube/TikTok posting.

### Moment Classification

- Candidate and run debug data can include `moment_categories` plus a compact `primary_category` label for quick inspection.
- Classification uses local transcript text, creator-vs-game speech-source cues, detector/ranker stats, audio/scene scores, and `visual_diagnostics` when frame sampling is enabled.
- Generic action words such as `run`, `hide`, or `please` stay lower-confidence unless backed by creator/reactive speech, visual action/failure evidence, or stronger hook context.
- The deterministic classifier works without Ollama, internet, or cloud services.
- The **AI moment labels** setting can add selected-clips-only classification metadata, plus a Deep Analysis shortlist in debug reports. It tries local Ollama when the service/model are ready and otherwise uses the deterministic local fallback. Saved `ai_moment_classification` records status, provider, confidence, fine labels, fallback state, and diagnostic **AI Viral Potential** fields such as `ai_viral_score`, `ai_viral_reason`, and `ai_dimensions`; the label object itself keeps `selection_impact: "none"`. In Deep Analysis only, a separate guarded `ai_moment_ranking` pass can use real high-confidence Ollama labels as a capped close-call nudge.
- Results, Preview, and All Videos can show saved labels when metadata exists. The UI source text is truth-driven: `AI` means a successful local Ollama classification, `Local` means local fallback/classifier metadata, and `Category` means only deterministic `moment_categories` are present.
- Labels are debug/context hints, not a separate moderation or upload category system, and they do not replace transcript/audio quality scoring. Review chips and any label filters only change what the review view shows; they do not recompute ranking, selection, or saved metadata.
- Visual diagnostics store compact numeric frame stats and labels only; they do not persist sampled frame images.

### YouTube Scheduling

- Google OAuth desktop-app flow.
- Multiple connected OAuth accounts.
- Channel listing is limited to uploadable channels backed by a connected local OAuth token.
- Calendar scheduling with drag/drop, smart peak-time scheduling, missed-upload handling, and per-clip metadata editing.
- YouTube scheduled uploads send per-clip UTC `publish_at` times, sorted by each clip's actual calendar time instead of reconstructing from one start time and an interval.
- Public scheduled posts are uploaded private first, then YouTube publishes them at the selected calendar time. Private/unlisted choices upload with that visibility instead of pretending to be scheduled public posts.
- The local background scheduler is separate from YouTube scheduled publishing: while the app is open and a YouTube account is connected, it can upload local schedule items when their calendar time arrives. Public items that are already beyond the scheduler grace window are marked missed and wait for explicit Reschedule or Upload Now action instead of publishing immediately on launch. The main upload button sends public pending posts to YouTube with future publish times, while private/unlisted pending posts upload immediately with that visibility.
- Upload cancellation between chunks.
- Optional delete-after-upload cleanup.
- Schedule state resolves clips by `clip_id` first, with legacy index fallback.

### Debugging And State

- Built-in console log viewer.
- Concise render logs by default; full FFmpeg commands are only echoed when verbose command logging is explicitly enabled for debugging.
- Batch progress is split into source-level status and current-video phase status, so multiple files or pasted links remain readable while each video runs through Download, Detect, Candidates, and Render.
- Per-run debug JSON in `subtitles/`.
- Candidate debug JSON with pre-render scores, reject reasons, transcripts, selected streams, scene diagnostics, and lightweight visual diagnostics.
- Run debug JSON with final render metadata and rendered clip rows.
- Diagnostic moment category scores for candidate quality, hook, action, context, aftermath, and lightweight sampled-frame visual signals.
- Single-track commentary guard diagnostics with segment labels, summary ratios, subtitle policy, fallback status, and a tiny creator-policy quality penalty for high-confidence game/NPC narration.
- Learned scoring diagnostics that show base score, capped adjustment, learned score, and selection/rank changes.
- Persistent state in `viria_state.json` with schema migration and atomic saves.
- Persisted clip paths are constrained to the clips folder before preview, delete, scheduler, upload, and auto-delete actions.
- Personalization feedback in `personalization.json` with like, dislike, favorite, per-action reasons, and timestamped events.
- Optional Creator Voice Profile in `voice_profile.json` with local-only numeric acoustic features, status controls, diagnostic confidence output, a capped debug-only shadow score, and a separate default-off voice-ranking opt-in for close calls.
- Result-card, preview-modal, and All Videos controls for marking clips as liked, disliked, or favorite, with quick reason chips plus an optional note.
- Stable local thumbnail/video preview handling for Results, Preview, and All Videos so refreshes reuse the same resolved clip media instead of briefly showing blank or stale previews, with consistent play controls on library cards.
- Saved moment labels can appear on Results cards, in the Preview modal, and in All Videos when present; deterministic categories may have already nudged close-call selection, while high-confidence local Ollama labels can also make a small Deep-only ranking nudge when all guardrails pass.
- Generation progress distinguishes candidate analysis from final clip rendering, with expectation/ETA-style text so status and percentages stay aligned during longer batches.
- No-quality runs still save timing/debug metadata and show **No Clips Created** with next-step guidance rather than a failed `0/N` batch message.
- Crop debug screenshots are disabled by default; if explicitly enabled for support/debug work, they are written under the app analysis cache instead of beside the user's source video files.
- Saved candidate debug reports can be used to render selected candidates after a crash without running full detection and transcription again.
- Beginner-friendly Data & Privacy settings card with an **Advanced Features** window for local learning status, voice profile controls, raw feedback inspection, share-safe exports, and clearing local feedback data.
- Ollama status pill in the app footer plus a Local AI settings card for opt-in Ollama/model setup. The status only reports ready when the local Ollama API responds like Ollama and the selected model is installed.

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | Python 3.11+ |
| Frontend | HTML5 / CSS3 / Vanilla JS |
| Desktop Shell | [pywebview](https://pywebview.flowrl.com/) |
| Video Processing | [FFmpeg](https://ffmpeg.org/) |
| Video Download | [yt-dlp](https://github.com/yt-dlp/yt-dlp) |
| Speech-To-Text | [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) |
| Person Detection | [YOLOv8](https://github.com/ultralytics/ultralytics) + OpenCV |
| Local AI titles and labels | [Ollama](https://ollama.com/) |
| YouTube API | Google API v3 with OAuth 2.0 |

---

## Getting Started

### Recommended User Install

After a release is published, use one of these instead of cloning the repository:

1. **Installer EXE** - download `ViriaReviveSetup-v2.2.0.exe`, run it, then launch ViriaRevive from the Start Menu.
2. **ZIP app** - download `ViriaRevive-v2.2.0-Windows-x64.zip` or the stable latest copy `ViriaRevive-Windows-x64.zip`, extract it, then double-click `ViriaRevive.exe`.

Current release packages do not bundle FFmpeg unless reviewed `ffmpeg.exe` and `ffprobe.exe` binaries are intentionally placed in `bin/` before building. Most users should install FFmpeg separately and make sure `ffmpeg` and `ffprobe` are available from the Windows `PATH`.

Installed builds store clips, OAuth tokens, state, personalization, and debug files in:

```text
%LOCALAPPDATA%\ViriaRevive
```

That keeps private user data out of the program folder and out of release packages. From source, the same runtime folders remain in the project checkout for easier development.

### Prerequisites For Source Installs

1. **Python 3.11+** - Release builds should use Python 3.12; `build.bat` prefers Python 3.12 for a new venv, then falls back to Python 3.11 or the available `py -3`/`python`.
2. **FFmpeg + ffprobe** - Must be available in your system `PATH`, or placed in the app's local `bin/` folder.
   - Windows: download from [ffmpeg.org](https://ffmpeg.org/download.html), extract it, and add the FFmpeg `bin` folder to `PATH`.
3. **Git** - Needed to clone and fork the repository.
4. **Ollama** *(optional)* - Needed for local AI titles, opt-in AI moment labels, and the guarded Deep Analysis AI ranking nudge.
5. **Google OAuth credentials** *(optional)* - Needed only for YouTube upload.

First use may download local model assets depending on the enabled features. Faster-Whisper can download speech models, YOLO/OpenCV crop detection can load YOLO assets, and Ollama pulls `qwen2.5:3b` only when you click **Download AI Model**.

### Source Installation

```bash
# Clone your fork, or clone upstream while testing locally
git clone https://github.com/<your-github-username>/ViriaRevive.git
cd ViriaRevive

# Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Ollama Setup

Ollama is optional and local. The app does not install Ollama or download models automatically during title generation, AI moment labeling, or Deep AI ranking.

In the app, open **Settings > Local AI**:

1. **Install Ollama** opens the official Ollama Windows download page in your browser.
2. **Install via PowerShell** asks for confirmation, then opens a new PowerShell window running Ollama's official Windows installer command: `irm https://ollama.com/install.ps1 | iex`. Once Ollama is detected, the setup buttons change to maintenance/status actions such as **Open Ollama Folder** and **Ollama Installed**.
3. **Download AI Model** pulls the current local AI model, `qwen2.5:3b`, only after you click it. Once the model exists, the button changes to **AI Model Ready** and only re-checks status.
4. **Refresh** checks whether Ollama is running and whether the local AI model is ready.

The app checks Ollama locally at `http://127.0.0.1:11434`. The status is considered real only when `/api/tags` returns an Ollama-style model list; a random service on that port does not count. If Ollama or the model is unavailable, title generation falls back to heuristic titles and AI moment labeling falls back to local deterministic metadata.

### YouTube Upload Setup

To enable YouTube uploads:

1. In ViriaRevive, open **Upload > Setup guide** or **Settings > Data & Privacy > Advanced Features > Open Data Folder**.
2. Open [Google Cloud Console credentials](https://console.cloud.google.com/apis/credentials).
3. Create or select a project.
4. Enable **YouTube Data API v3**.
5. Configure the **OAuth consent screen**. For a personal/local setup, keep the app in **Testing** and add your own Google account as a test user.
6. Go to **Credentials** > **Create Credentials** > **OAuth 2.0 Client ID**.
7. Choose **Desktop app**.
8. Download the JSON file.
9. Save it as `client_secrets.json` in the app data folder shown by the setup guide.

The app's **Add Account** button then opens Google's browser-based consent flow and saves account tokens locally. For source checkouts, `client_secrets.json` can also live in the project root. The app still accepts `tokens/client_secrets.json`, but that file is treated as an OAuth client secret and is not listed as a connected account.

Google treats installed desktop apps as unable to keep client secrets private, so public builds should either keep this bring-your-own-OAuth-client setup or ship a verified public OAuth client later. OAuth upload access uses sensitive YouTube scopes, so a broad public release should expect Google's OAuth verification process before strangers can connect without warning screens. For personal use, adding yourself as a test user is the closest low-friction workaround until the app has a verified public OAuth client.

Manual Google Cloud steps, if not using the in-app buttons:

1. Open [Google Cloud Console](https://console.cloud.google.com/).
2. Create or select a project.
3. Enable **YouTube Data API v3**.
4. Configure the **OAuth consent screen**, keep the app in **Testing**, and add yourself as a test user.
5. Go to **Credentials** > **Create Credentials** > **OAuth 2.0 Client ID**.
6. Choose **Desktop app**.
7. Download the JSON file.
8. Save it as `client_secrets.json`.

For source checkouts, put `client_secrets.json` in the project root. For installed builds, open **Upload > Setup guide** or **Settings > Data & Privacy > Advanced Features > Open Data Folder** and put `client_secrets.json` there. Connected account tokens are stored in the app data `tokens/` folder. Token filenames are constrained to safe YouTube account IDs, and legacy `token.json` migration keeps the original file unless migration and the new token save both succeed.

Do not commit `client_secrets.json`, `tokens/`, or generated OAuth/token files. YouTube OAuth tokens are stored as local JSON files so the app can upload without asking you to sign in every time. Treat the app data folder as private, and disconnect accounts or clear the `tokens/` folder before sharing a machine, ZIP, screenshot, or support bundle.

Public releases should not bundle private `client_secrets.json` files or personal refresh tokens.

### Launch

```bash
# With console output
python app.py

# Without console window
pythonw app.pyw
```

### Windows Startup And Tray

- `app.pyw` launches without a console window and is the preferred launcher for daily desktop use.
- The tray icon can show the app again or quit it while the window is minimized.
- `setup_startup.bat` creates a Startup-folder shortcut that runs `ViriaRevive_Startup.vbs`, which launches the app minimized on Windows sign-in. The installer removes that Startup shortcut during uninstall if it exists.
- `ViriaRevive.vbs` is a no-console launcher for the app in normal windowed mode.

---

## Usage

### Desktop Workflow

1. **Generate** - Paste one or more YouTube URLs, or choose local video files.
2. **Configure** - Pick clip count, duration, gap, crop, Processing Depth, subtitle style/placement, audio source, Whisper model, effects, and music.
3. **Analyze** - Let the app detect, transcribe, rerank, trim, and render clips.
4. **Review** - Inspect clips in Results, Preview, or All Videos, then mark clips as liked, disliked, or favorite with an optional reason.
5. **Title** - Generate AI titles/metadata with Ollama or the heuristic fallback.
6. **Describe** - Keep recommended hashtags on, add reusable creator text, or customize the text per scheduled clip.
7. **Schedule** - Assign clips to channels with the calendar.
8. **Upload** - Use **Upload Scheduled Posts to YouTube**, or post manually using the rendered clips and sidecar metadata.

### CLI Mode

```bash
# Basic usage
python main.py "https://youtube.com/watch?v=VIDEO_ID"

# With options
python main.py "URL" --clips 5 --duration 30 --style bold

# Move transcript captions higher and narrow the caption box
python main.py "URL" --subtitle-y 64 --subtitle-width 72

# Opt in to capped deterministic moment-category ranking
python main.py "URL" --moment-category-ranking

# Generate and schedule public YouTube publishing
python main.py "URL" --upload --schedule 24
```

CLI mode is intentionally leaner than the desktop workflow. It supports URL processing, detection, transcription, learned-feedback ranking, optional deterministic moment-category ranking with `--moment-category-ranking`, rendering, subtitle placement options, candidate/run debug JSON, and optional public scheduled upload when `--upload` is used. Moment-category ranking remains off by default in CLI for one release, preserving existing CLI output unless the opt-in flag is passed. The richer desktop pipeline is where batch/local-file flows, Processing Depth presets, visual frame analysis, depth-enabled deterministic category ranking, AI moment labels, voice-profile ranking, Ollama title/metadata sidecars, custom reusable descriptions, rich per-clip upload metadata, calendar editing, and feedback controls are wired together.

---

### Generate Wizard

The Generate wizard runs **Style** -> **Detection** -> **Audio** -> **Effects** -> **Music**. The separate **Detection** step keeps clip-finding choices away from subtitle styling.

**Processing Depth** controls how much time ViriaRevive spends looking for moments:

- **Fast** - quickest pass for shorter tests and rough drafts.
- **Balanced** - the normal default for most videos.
- **Deep Analysis** - slower, more thorough detection for long videos or runs where scene changes matter.

ViriaRevive intentionally keeps this beginner-friendly: there is no separate scene-scan switch. Internally, Fast may skip expensive long-video scene scans, Balanced samples long recordings, and Deep Analysis targets scene scanning around strong audio/variance peaks plus timeline anchor windows. The public-runtime default is bounded FFmpeg scanning; PySceneDetect is only used when available and allowed by the detector's runtime-safety guards. Deep Analysis is the preset to use when you want a broader look than Balanced and are comfortable waiting longer, without blindly decoding every frame of a multi-hour recording first. While long scene scans are running, the progress card can show video time scanned, such as `Detecting scenes: 01:12:00 / 02:43:00`, so users can tell the detector is still moving; that detail clears once backend progress moves on to audio inspection, candidate analysis, or rendering.

The Detection step keeps **Output preference** simple. When **Number of clips** is a fixed number, ViriaRevive uses the Quality path because the user has already chosen the target count. When **Number of clips** is **Auto**, the Quality/Quantity preference appears so the app can decide whether to return fewer stronger clips or fill more of the available output.

The **Audio Sources** step chooses where ViriaRevive listens for words. This affects transcription, subtitles, transcript-aware ranking, and title context. Changing the transcription source does not mute the final clips: rendered clips keep the full source audio mix.

If audio inspection cannot finish, the wizard should say whether the probe timed out, FFmpeg returned an error, or the file could not be inspected. **No audio tracks found** means the probe completed and did not find usable audio.

For OBS or other multi-track recordings:

1. Choose one local file, then start generation.
2. On **Audio Sources**, wait for the track list. Tracks are shown as `Track 1`, `Track 2`, and so on, with labels such as `commentary` or `game` when the file names make that clear.
3. Leave **Auto detect commentary** on for most recordings. ViriaRevive samples the tracks and picks the one with usable speech. Debug reports save the stream scoring profile so you can see the selected stream, runner-up stream, reason, confidence, mic/game title hints, and creator/game phrase signals.
4. Use **Use selected track** when you know exactly where the words are. Pick the mic/commentary track for creator subtitles, or the game/desktop track only if the speech you want is there.

For single-track videos, including one-track YouTube downloads and recordings where mic and game are already mixed together, leave **Auto detect commentary** on and keep **Use the single-track speech guard when mic and game audio are mixed** enabled. ViriaRevive transcribes the one mixed track, keeps that same mixed audio in the rendered clip, and groups transcript words into short `commentary_guard` segments labeled `creator_commentary`, `game_narration`, or `unclear`.

The mixed-track subtitle policy defaults to **Prefer my commentary** (`creator`). This lightly removes likely game/NPC speech from subtitle and title-facing transcript words when there are enough creator words left. The same wizard control can opt in to **Include all speech** (`all`) or **Prefer game/NPC speech** (`game`). The game/NPC option keeps likely game speech plus unclear words and removes likely creator commentary when safe.

The subtitle guard is conservative. It is not voice separation, does not mute rendered audio, and only filters the subtitle/title-facing moment transcript and generated subtitle words. If filtering would make subtitles too sparse, ViriaRevive falls back to the original mixed transcript for that clip. Under the default creator policy, high-confidence game/NPC narration that cannot be safely filtered can receive only a tiny capped quality penalty; the explicit **Include all speech** and **Prefer game/NPC speech** policies do not receive that creator-only penalty. A separate music/lyrics guard can also affect candidate quality under the default creator policy: likely song-lyric transcripts without enough creator context receive a capped quality penalty, and very lyric-heavy song-only candidates can be rejected as `music_lyrics_not_creator_commentary`. This keeps background music and game narration from being treated as creator speech while still allowing creator jokes or reactions that happen over a song.

When multiple files are queued at once, the wizard uses Auto for each item because track numbers can mean different things from one source file to the next. This prevents a manual stream choice from one recording from leaking into another file. Normal queue rows inherit the wizard subtitle style so caption behavior stays in one place; per-source caption overrides are treated as an advanced mixed-batch safeguard rather than the default flow.

---

## Local Files And Generated Data

These paths are local runtime data and should not be committed. In source checkouts they live beside the code. In installed builds they live under `%LOCALAPPDATA%\ViriaRevive`.

- `downloads/` - downloaded source videos.
- `clips/` - rendered clips and metadata sidecars.
- `subtitles/` - temporary audio, ASS subtitle files, candidate debug JSON, and run debug JSON.
- `analysis_cache/` - local-only cached scene-analysis timestamps keyed to source file fingerprints and scan settings.
- `music/` - optional local background music files.
- `tokens/` - YouTube OAuth tokens and optional OAuth secret copy.
- `client_secrets.json` - Google OAuth client secret.
- `viria_state.json` - app state.
- `viria_state.*.bak` - state migration backups.
- `personalization.json` - local feedback events, latest per-clip summaries, and learned-ranking signals.
- `personalization.*.bak` - corrupt personalization backups.
- `processing_history.json` - local-only run timing history used for better future estimate text.
- `processing_history.*.bak` - corrupt timing-history backups.
- `voice_profile.json` - optional local creator voice profile; stores a small numeric centroid, not raw audio.
- `voice_profile.*.bak` - corrupt or cleared voice profile backups.

The current `.gitignore` excludes these paths plus common secret files such as `.env` and `.env.*`. Release builds are also checked by `scripts/check_release_safety.py` before ZIP/installer packaging. That scanner blocks known private runtime data, common OAuth/token filenames, private folders, debug reports, and obvious secret-bearing JSON markers while allowing the harmless `client_secrets.example.json` template.

---

## State Persistence

The desktop app stores local UI state in `viria_state.json` in the app data folder. This file tracks generated clip paths, clip metadata, upload schedules, delete-after-upload preference, user settings, and the current `schema_version`.

Generated moments can include an `audio_source` summary with the transcription mode, `selected_stream`, stream-selection reason/confidence, retry behavior, `stream_count`, render-audio mode, `subtitle_policy`, and `single_track_commentary_guard`. Backend settings also understand an `audio_source` object with `mode`, `stream`, `commentary_guard`, and `subtitle_policy`: `auto` lets ViriaRevive choose the speech track, `stream` stores a selected audio stream ordinal for a compatible single-source run, and `subtitle_policy` is normalized to `creator`, `all`, or `game`. Manual `stream` choices do not use alternate-stream fallback during normal generation; Auto mode is where retry can switch tracks.

Generated moments can include compact local frame analysis from a few small sampled frames around each candidate. The saved debug key is still named `visual_diagnostics` for compatibility, but the feature itself is treated as local analysis: brightness, darkness, motion, edge density, red-flash, UI/text-like density, possible failure-screen score, scenic score, black-frame ratio, and labels such as `high_motion`, `dark_scene`, `red_flash`, `ui_overlay`, `possible_failure_screen`, or `scenic_frame`. These signals enrich classification/category debug data and title context. They influence selection only through the capped moment-label ranking path: Fast keeps that off, Balanced and Deep Analysis turn it on by Processing Depth, and the manual setting can still enable it for compatible custom runs.

The Clip Detection settings card includes a **Visual frame analysis** toggle. It is on by default for richer category/debug context and can be turned off to skip frame sampling on slow sources. Processing Depth presets can temporarily override the toggle: Fast may skip heavier analysis, while Deep Analysis can enable the richer passes for difficult sources.

Generated moments can include `moment_categories` and `primary_category` for compact local classification. The current labels are deterministic hints derived from transcript phrases, ranker signals, detector stats, and optional visual frame analysis, so they continue to work when Ollama is not installed or not running.

The Clip Detection card also includes **Use moment labels in ranking**. Processing Depth can override the saved toggle: Fast disables the nudge, Balanced and Deep Analysis enable it, and the Data & Privacy window reports this as **On by depth**. When enabled, deterministic categories can add or subtract at most `0.020` after learned feedback scoring and before optional AI/voice-profile ranking. High-energy and death/failure moments receive the strongest positive nudge, tutorial/explainer, lore/story, and atmosphere moments can receive smaller positive nudges, and low-value moments can receive a negative nudge. A very small diversity helper can lift close underrepresented categories so one repeated label does not crowd out every other useful moment type. Confirmed black frames and stat/results/end-screen language count as low-value evidence unless a stronger creator payoff, failure, tutorial, or story signal wins.

The Clip Detection settings card also includes **AI moment labels**, which is off by default. When enabled, ViriaRevive checks the real local Ollama service and `qwen2.5:3b` model, then classifies selected clips with a compact JSON prompt containing transcript preview, detector/ranker scores, and local frame-analysis signals. Deep Analysis also writes an `ai_moment_classification_shadow` report for a pre-final shortlist, then can run a separate `ai_moment_ranking` pass that adds at most `+/-0.015` after deterministic moment-category scoring and before optional voice-profile ranking. The nudge is skipped unless Ollama/model readiness is real, the label status is `ok`, provider is `ollama`, the result is not a fallback, confidence is high enough, useful score dimensions exist, and the candidate was not rejected by music/commentary guards. Ollama calls are capped per run; any unavailable, timed-out, malformed, or over-cap result falls back to the deterministic local labeler for metadata only. The saved `ai_moment_classification` object remains optional local metadata for display, titles, and debug context; it records `selection_impact: "none"` plus `output_changed: false`. Results, Preview, and All Videos show labels only when saved metadata exists, with the source label based on the saved status/provider rather than the toggle state alone. Review label filtering, where present, is only a hide/show aid for inspection and does not change selected clips, ranking, or persisted metadata.

Generated moments may also include a compact `voice_profile` diagnostic when the optional Creator Voice Profile is enabled and enrolled from multiple local samples with enough active speech. This records confidence, distance, reason, sample count, feature version, `diagnostic_only: true`, and `selection_impact: "none"`; it does not store profile centroids or raw audio inside moments. Debug reports can also include `voice_profile_shadow`, a hypothetical voice-confidence reorder using a small capped adjustment of `+/-0.035`. If the separate **Use Voice In Ranking** opt-in is enabled, candidate selection can also use `voice_profile_quality_score`, which applies a smaller capped local-only `+/-0.025` adjustment on top of the learned/category/AI score. The status payload separates the gates clearly: enabled means the local feature is allowed, enrolled means usable samples exist, and influencing means ranking is allowed for eligible runs; the run debug report still shows whether candidate voice scores were actually available and changed ranking. When you like or favorite a clip that already looks like clean creator commentary, including eligible dual-track mic/commentary selections, the backend can return a local opt-in nudge to build the profile; it never auto-enrolls or enables voice ranking from feedback alone.

When `single_track_commentary_guard` is true, generated moments can also carry `commentary_guard` diagnostics. That data can include `policy`, `output_changed`, `selection_impact`, `subtitle_impact`, a summary with `primary_label`, creator/game narration word ratios, confidence, an `application` block with fallback and word-count details, and a small `selection` block when creator-policy game/NPC narration receives a capped quality penalty. Persisted moments omit raw segment text, but debug reports keep per-segment labels for inspection.

State schema v2 adds stable `clip_id` and `source_id` values:

- `clip_id` identifies an individual rendered clip even if file indexes shift.
- `source_id` identifies the original source video or local recording.

Legacy state files are migrated on launch. Before rewriting an older schema, the app creates a timestamped backup such as `viria_state.pre_v2.YYYYMMDD_HHMMSS.json.bak`.

State writes are atomic: the app writes a temporary JSON file, flushes it to disk, and replaces `viria_state.json` with `os.replace`. This reduces the chance of corrupted state if the app exits during a save.

---

## Personalization Feedback

Clip feedback is stored separately from app state in `personalization.json`. Each click appends an event with:

- `event_type` - `like`, `dislike`, or `favorite`.
- `active` - whether the feedback was turned on or off.
- `clip_id` and `source_id` - durable identities for the clip and source video.
- `reason` - optional local reason text for the event/action, composed from quick reason chips and/or a note.
- `timestamp` - UTC timestamp generated by the backend.
- `clip_snapshot` - compact transcript/ranker context used by learned candidate scoring on future runs. This is metadata, not a media snapshot: it does not store rendered video, thumbnails, or raw audio.
- `learning_terms` - compact local terms derived from transcript/category context so feedback can keep teaching future runs even after the rendered clip file is deleted.

The file also keeps a `clips` summary keyed by `clip_id` so the UI can quickly restore current like/dislike/favorite state, including the active reason for each feedback action. Learned scoring treats each clip's `latest` summary as the source of truth and uses the matching action's stored reason for like/dislike/favorite signals, so removed likes stop counting, favorite toggles do not erase like/dislike reasoning, and like/dislike flips do not leave stale positive or negative signals behind. Historical `events` remain as an audit trail, and are replayed only as a fallback for legacy/event-only personalization files. Deleting a rendered clip removes the video file and app state entry, but existing feedback and compact learning metadata remain local until you clear feedback from Data & Privacy.

Feedback controls are available on each Results card, inside the clip Preview modal, and in All Videos. Toggling one of these controls updates every visible copy of the same clip. When feedback is turned on, the app opens its own **Feedback** window with preset reasons and an optional note instead of using a browser prompt; the backend stores that text with the action being changed, so a favorite note does not overwrite the current like or dislike reason.

The Settings tab keeps Data & Privacy simple: the card explains that local feedback, learning signals, optional voice profile data, OAuth files, generated clips, and timing history stay on the PC, then opens an **Advanced Features** window for details. That window uses tabs for **Overview**, **Learning**, **Local Analysis**, **Voice Profile**, and **Files & Sharing**. The learning readout explains how many active ratings are being used now, the small maximum learning nudge, and the last time you rated a clip. The Local Analysis tab shows whether scene detection, visual frame analysis, AI moment labels, moment-label ranking, and voice ranking are active, sampled, off, or intentionally inactive in Fast. From the advanced window you can open the app data folder, open raw `personalization.json`, export a share-safe copy, refresh the summary, or clear learning feedback. The share-safe export removes transcript text, compact learning terms, user-entered per-action reasons, filenames, source names, exact timestamps, and raw clip/source IDs while keeping hashed references for aggregate debugging. Clearing feedback also removes future learned-ranking influence, and writes a backup named like `personalization.cleared.YYYYMMDD_HHMMSS.json.bak` before creating a fresh empty file.

The **Voice Profile** tab includes an optional **Creator Voice Profile**. It is off by default until you enable it or build it from current clips. Building the profile first checks each current clip's compact analysis metadata and skips likely game/NPC narration, likely song lyrics/music pauses, filtered single-track speech, too-little speech, low-value speech, and older clips without commentary-guard metadata before extracting any temporary audio. Clean selected mic/commentary streams from dual-track recordings can be eligible even when rendered clips keep the full source mix. Enrollment still requires multiple local samples with enough active speech; one clip or sparse speech keeps the profile in **Needs samples**. Eligible clips are briefly converted to WAV, reduced to a small numeric acoustic centroid, and the temporary WAV is deleted; only `voice_profile.json` is saved. Raw audio, rendered videos, and transcript text are not stored in the profile. The tab now calls out whether the profile is **Off**, **Needs samples**, **Ready**, or **Influencing**. Voice ranking can be saved before samples exist, but it will stay non-influencing until the profile is enrolled. Future runs can report bounded creator-voice confidence per candidate/final clip in debug data. Voice ranking is a separate default-off opt-in, including in Deep Analysis. When enabled and enrolled, it can add or subtract at most `0.025` locally from the learned/category/AI candidate score, so it nudges close calls without replacing transcript/audio quality. Liked or favorited clips that already look like clean creator commentary can trigger a small local prompt to build the profile, but feedback never auto-enrolls or stores audio by itself. Missing, stale, or empty temporary WAVs are treated as non-scored samples instead of runtime failures. Enrollment failures include compact skip counts and ordinal examples such as `likely_game_narration` or `likely_music_or_lyrics` without storing transcripts or returning clip filenames. Resetting the profile writes a local backup before clearing it, so remove `voice_profile.*.bak` files too if you need every old local profile copy gone. Run-level debug still includes `voice_profile_shadow`, which applies a hypothetical capped `+/-0.035` voice adjustment for comparison.

Subtitle Style includes a **None** option in both Settings and the pre-generation wizard. None skips subtitle file generation and renders clips without burned-in words while still allowing transcript analysis to help clip ranking and titles. Batch queue rows inherit the wizard's subtitle choice, so captions are controlled from the same setup flow instead of a separate queue dropdown. Persisted clip metadata distinguishes intentional caption-off runs with `captions_requested`/`subtitle_enabled` from subtitle generation failures. Subtitle settings also include a real preview snapshot so style, position, and width changes can be checked against an actual video frame before rendering; clicking the small preview opens a larger settings preview.

Processing Depth in the pre-generation wizard can be set to **Fast**, **Balanced**, or **Deep Analysis**. Fast favors speed, Balanced is the everyday default, and Deep Analysis spends more time on detection for difficult or long sources. Balanced and Deep Analysis also enable the capped deterministic moment-label ranking path, while Fast keeps it off. Scene detection is rolled into those presets: bounded FFmpeg scans are the default runtime path, long Deep runs use targeted scan windows based on audio/variance peaks plus timeline anchors, and PySceneDetect attempts run only when the detector's runtime guards allow them. Expensive scene timestamps can be reused from `analysis_cache/` when the same source file and scan settings are unchanged.

ViriaRevive keeps a local-only `processing_history.json` timing log in the app data folder. Each successful or no-quality run records the chosen depth, video duration, candidate/render counts, stage timings, estimate source, total elapsed time, and estimate error. If the timing log is missing but older `*_run_debug.json` files still exist, the backend can seed a small history from those debug reports before saving the next timing file, using safe defaults for malformed old debug values. The next runs can use that local history for better visible ETA/expectation text, and the Data & Privacy advanced window summarizes the latest runtime and estimate miss without uploading anything.

---

## Debug Reports

When detection runs, the app can write debug files into `subtitles/`:

- `*_candidate_debug.json` - pre-render candidate selection state only: all candidates, scores, transcripts, selected stream, reject reasons, scene diagnostics, learned-scoring diagnostics, commentary guard diagnostics, and music/lyrics guard diagnostics when enabled.
- `*_run_debug.json` - post-render run state: the same candidate diagnostics plus `final_clips`, subtitles status, render warnings, rendered paths, final render metadata, and final `commentary_guard` / `music_lyrics_guard` rows when present.

Both debug files include a `debug_stage` field: `candidate_pre_render` for candidate debug, `run_post_render` for rendered run debug, and `run_no_quality_clips` when no clips pass the quality bar. Candidate debug is not overwritten after rendering, so pre-render ranking data such as AI ranking fields and category-diversity counts remain inspectable. Candidate rows include diagnostic moment category scores so quality, hook, action, context, and aftermath signals can be inspected without rerunning detection. Rendered clip rows also record resolved `subtitle_placement` values, including requested percentages, ASS pixel coordinates, margins, and alignment. Final render rows include `selected_start` / `selected_end`, `render_start` / `render_end`, and any `trim_adjusted_*` suggestion from final transcription, so a selected interval, actual rendered interval, and transcript-trim hint can be compared directly. If final transcription uses a trim-relative word list while the selected window is preserved, `subtitle_timing_offset` records the shift applied before subtitle generation. Final rows also split `selection_*`, `ranking_*`, and `final_*` category fields so you can see what influenced selection separately from labels refined after final transcription. Selected AI moment labels stay in explicit pre-render AI metadata and are not inserted into final refined category objects. Final rows also carry compact category, visual, audio-stream, commentary guard, music/lyrics guard, subtitle, and transcript metadata for the rendered file. The run debug report also includes a `timing` object with stage timings, estimated total time, actual elapsed time, estimate error, and the refreshed local processing-history summary after a successful run.

If the app crashes after candidate analysis but before final clips finish rendering, the saved `*_candidate_debug.json` can still be used by the backend recovery helper during development/debugging. The normal user-facing Generate screen no longer exposes a **Render Last Analysis** button, because this recovery path can replay stale analysis choices and is better treated as a support/debug tool.

Both debug files include a top-level `visual_diagnostics` report and per-candidate `visual_diagnostics` rows when frame sampling is enabled. The report records status, candidate count, sampled count, frames read, elapsed time, and warnings. Candidate rows store only compact numeric stats and labels, not full frame images. Crop debug screenshots are a separate default-off support aid and, when explicitly enabled, save under `analysis_cache/crop_debug/` with unique filenames. If frame sampling is unavailable, disabled, or fails, the report uses explicit statuses such as `disabled`, `ffmpeg_missing`, `opencv_missing`, `video_open_failed`, `no_frames`, or `analysis_failed`; transcript/audio ranking continues normally.

When **AI moment labels** is enabled, both debug files include a top-level `ai_moment_classification` report and per-selected-row `ai_moment_classification` objects. The report records whether the feature was enabled, whether Ollama/model readiness was real, how many selected clips were classified, how many Ollama calls were attempted, fallback counts, and status counts such as `ok`, `model_not_ready`, `invalid_response`, `ollama_error`, or `ollama_skipped_limit`. Candidate rows keep the AI label nested under `moment_categories.ai` while preserving the original deterministic `moment_categories.primary`. Deep Analysis can also include `ai_moment_classification_shadow`, a diagnostic-only pre-final shortlist report with `selection_impact: "none"` and `output_changed: false`; it records shortlist rows, selected-output overlap, score source, Ollama/fallback counts, sanitized AI labels, and an `ai_viral_potential` summary. The saved AI label objects can carry AI Viral Potential metadata such as `ai_viral_score`, `ai_viral_reason`, `ai_dimensions`, `ai_confidence`, zero/default `ai_adjustment`, and no `ai_rank_delta` while remaining non-mutating label metadata. The separate `ai_moment_ranking` section is the active guarded ranking report: it records the `+/-0.015` cap, eligible/scored counts, selection score source, added/dropped/kept clips, rank deltas, learned/category baseline score, AI adjustment, final AI score, and ineligible reasons such as fallback label, low confidence, missing dimensions, or music rejection.

Both debug files also include `settings.audio_source`, with the requested mode/stream, `selected_stream`, `selected_reason`, `selected_confidence`, `runner_up_stream`, `stream_count`, detected source streams, `render_audio: "all_source_streams_mixed"`, `alternate_stream_retry`, `subtitle_policy`, and `single_track_commentary_guard`. Auto stream selection also saves `stream_selection`, a compact scoring profile with per-stream Whisper word counts, sample hits, word density, mic/game title hints, natural-dialogue score, creator/game phrase scores, acoustic game-bed score, lyric likelihood, creator-exception score, creator-likeness score, `mic_creator_preference_bonus`, the selected stream, runner-up stream, reason, and confidence. The mic creator-preference bonus is a bounded score helper for a track that is named like a microphone and also looks creator-like; lyric-like, game-bed-heavy, or weak/noisy mic transcripts do not receive it. Candidate/final rows may include `stream_retry` when the preferred stream was too weak; retry attempts record whether an alternate was accepted or rejected and why, such as `creator_like_alternate`, `background_bed_suggests_game_audio`, `music_lyrics_not_creator_commentary`, or `not_creator_like_enough`. Persisted moments include a smaller `audio_source` summary with the selected stream, reason, confidence, runner-up stream, stream count, render-audio mode, retry behavior, mixed-track subtitle policy, and single-track guard. If a saved stream is not available for a source, debug warnings include `audio_source_stream_unavailable` and the run falls back to Auto.

When the one-track subtitle guard is enabled, candidate rows and run/final rows include `commentary_guard`. It reports segment labels, scores, confidence, and summary counts/ratios for likely creator commentary vs game/NPC narration. It also records the applied subtitle policy, `application.reason`, `fallback_used`, original/filtered/removed word counts, and kept/removed labels. When a safe filter is applied, `mode` becomes `light_filter`, `output_changed` is true, and `subtitle_impact` is `filtered_words`; the subtitle filter itself does not change selection. Under the default creator policy only, a mostly game/NPC narration candidate that could not be safely filtered can record `selection_impact: "quality_penalty"`, `commentary_guard_selection_penalty`, and a `selection` reason. That penalty is capped at `+/-0.060`, does not hard-reject by itself, and is skipped for explicit `all` or `game` policies.

Candidate rows can also include `music_lyrics_guard` and `music_lyrics_penalty`. This is the active song-lyric safety layer used by creator-policy runs. It records `lyric_likelihood`, `creator_exception_score`, `selection_penalty`, `selection_impact`, `reject_candidate`, reason, compact lyric/repetition/music-context signals, and before/after quality when a penalty applies. The guard demotes or rejects likely song-only transcripts, but creator-context phrases can reduce or remove the penalty so funny commentary over music can still survive.

When the optional Creator Voice Profile is enabled and enrolled from multiple local samples with enough active speech, candidate rows, selected moments, and final render rows can include `voice_profile`. This reports `confidence`, `distance`, `reason`, `sample_count`, `diagnostic_only: true`, and `selection_impact: "none"`. The run settings also include a voice-profile status summary with enable/enrollment state, readiness/influence fields, blocking reason/guidance text, and `stores_raw_audio: false`. Enrollment uses the same local commentary/music guards before creating temporary WAV samples, scans all current Results entries for up to eight clean samples, allows clean selected mic/commentary streams from dual-track recordings, and returns compact skip counts/reasons for rejected current clips. The centroid stays only in local `voice_profile.json`.

When **Use Voice In Ranking** is enabled in Data & Privacy, both debug files include a `voice_profile_ranking` section. It reports `mode: "voice_profile_blend"`, `selection_impact: "capped_rank_adjustment"`, the `+/-0.025` cap, whether output selection changed, and added/dropped/kept counts. Candidate rows flatten the active ranking fields at `voice_profile_quality_score`, `voice_ranking_enabled`, `voice_ranking_adjustment`, `voice_ranking_rank_delta`, and `voice_ranking_selection_delta`. The active score is applied locally after feedback learning, deterministic category ranking, and optional Deep AI ranking, so voice remains the final explicit opt-in close-call nudge.

When moment-label ranking is enabled by setting or Processing Depth, both debug files include a `moment_category_ranking` section. It reports `mode: "moment_category_blend"`, `selection_impact: "capped_rank_adjustment"`, the `+/-0.020` cap, output-change status, added/dropped/kept counts, diversity candidate count, and baseline category counts. Candidate rows flatten `moment_category_quality_score`, `moment_category_adjustment`, `category_diversity_adjustment`, `category_diversity_cap`, `baseline_category_count`, `moment_category_rank_delta`, and `moment_category_selection_delta`. This score is applied after learned feedback and before optional Deep AI and voice-profile ranking.

Both debug files can also include a `voice_profile_shadow` section. It uses the current learned score as its base, adds at most `+/-0.035` based on local creator-voice confidence, then reports hypothetical fields such as `voice_adjustment`, `voice_shadow_score`, `voice_rank_delta`, `voice_selection_delta`, `hypothetical_selection_changed`, and `selection_delta_counts`. Candidate rows flatten the most useful voice-shadow fields, including `voice_confidence`, `voice_reason`, `voice_score_source`, `voice_current_rank`, `voice_shadow_rank`, `voice_rank_delta`, `voice_selection_delta`, `voice_selected_by_current`, and `voice_would_select`. The report-level section explains whether any candidate would have been added, dropped, kept, or only rank-shifted. `output_changed` remains `false`, `diagnostic_only` remains `true`, and `selection_impact` remains `none` because this is only a diagnostic preview for future learning work.

Both debug files include a `shadow_scoring` section. Learned scoring reads the current active feedback state from `personalization.json`, computes a diagnostic `shadow_score`, then blends only a small capped `learned_adjustment` into `learned_quality_score` for candidate selection. Candidate rows flatten the most useful fields at `candidates[].base_quality_score`, `candidates[].learned_adjustment`, `candidates[].learned_score`, `candidates[].learned_quality_score`, `candidates[].rank_delta`, and `candidates[].selection_delta`. The full nested detail remains at `candidates[].shadow_scoring`, and report-level summaries are available in `shadow_scoring.top_changes[]` and `shadow_scoring.selection_delta_counts`. When full transcript/moment context is unavailable, learned scoring can fall back to compact `learning_terms` saved with feedback. The cap keeps transcript/audio quality dominant while allowing feedback to nudge close calls.

Scene diagnostics are explicit and include an `engine` field such as `ffmpeg`, `none`, or `pyscenedetect` when PySceneDetect actually ran:

- `ok` - scene detection ran and found changes.
- `zero_changes` - scene detection ran but found no scene cuts.
- `sampled_ok` - sampled scene detection ran and found changes.
- `sampled_zero_changes` - sampled scene detection ran but found no scene cuts.
- `targeted_ok` - targeted Deep scene detection ran on likely scene windows and found changes.
- `targeted_zero_changes` - targeted Deep scene detection ran but found no scene cuts.
- `skipped` - the current Processing Depth intentionally skipped scene scanning.
- `timeout` - FFmpeg scene scan took too long.
- `ffmpeg_missing` - FFmpeg was unavailable.
- `ffmpeg_error` - FFmpeg returned an error.

For long videos, Fast and Balanced depth may report sampled or skipped scene work instead of scanning every possible cut. Deep Analysis reports targeted scene work: the bounded FFmpeg path uses audio/variance peaks and timeline anchor windows to inspect likely high-value regions without blindly decoding the full recording first. Debug reports include `target_window_count`, `target_window_seconds`, `cache_hit`, and `candidate_pool_cap` so Deep speed/quality tradeoffs can be inspected. Audio/transcript ranking remains the fallback when scene detection is unavailable, times out, or is intentionally shortened.

---

## Testing

Synthetic no-video regression tests cover learned feedback reconciliation, compact deleted-clip learning terms, per-action feedback reasons, legacy reason migration, capped score blending, Processing Depth presets, Fast-depth inactive local-analysis statuses, depth-enabled/opt-in moment-category ranking, category diversity nudging, CLI default-off moment-category ranking, visual/AI metadata no-impact guards when ranking is disabled, Deep-only AI shadow shortlist debug reports, guarded Deep AI ranking cap/eligibility, AI Viral Potential debug contracts, visual black-frame and stat/end-screen low-value guards, bounded FFmpeg sampled/targeted scene routing, candidate-analysis vs final-render progress text, active progress-stage checkmarks, scene-detail clearing, ETA/expectation messaging, processing-history backfill from run debug files, backend candidate-debug recovery rendering, final selected/render/trim interval debug fields, trim-relative subtitle timing shifts, long-video sampled/skipped/targeted scene detection, audio probe timeout/error/no-audio messaging, stream-selection diagnostics, creator-like mic preference scoring, natural-dialogue scoring, acoustic game-bed retry rejection, creator-policy game/NPC narration downranking, lyric-like stream penalties, mic-vs-game scoring, active music/lyrics candidate guarding, missing/empty temp WAV voice-score guards, hidden queue Captions override behavior, voice-profile shadow scoring, voice-profile feedback nudges, opt-in voice-profile ranking, lightweight visual diagnostics, opt-in AI moment labeling/fallback/debug metadata, removed feedback, like-to-dislike flips, Data & Privacy learning/voice-profile status and share-safe redacted exports, subtitle placement output, per-source caption override wiring, mixed-source batch queue safeguards, mixed-track subtitle policy and fallback behavior, duplicate-stem clip output paths, debug report stage/learning fields, analysis-aware title context, composed descriptions, Ollama status truth checks, YouTube account-token filtering, per-clip UTC publish scheduling including DST offsets, upload cancellation state, GUI schedule identity guards, release hash/safety guardrails, visible installer subprocess launches, and clip-path delete safety:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

---

## Building Releases

Release builds are Windows-first and intentionally keep private data out of the package.

```bat
build.bat
```

`build.bat` creates or updates `venv`, checks that direct dependencies are pinned, installs runtime and build dependencies, runs PyInstaller with `viria.spec`, scans the PyInstaller output folder for private data, and creates:

```text
release\ViriaRevive-v2.2.0-Windows-x64.zip
release\ViriaRevive-Windows-x64.zip
release\ViriaRevive-v2.2.0-Windows-x64.zip.sha256
release\ViriaRevive-Windows-x64.zip.sha256
```

Optional app-local FFmpeg:

- Place `ffmpeg.exe` and `ffprobe.exe` in `bin/` before building.
- The app prepends `bin/` to `PATH` at runtime.
- Release packaging allowlists only `bin/README.md`, `bin/ffmpeg.exe`, and `bin/ffprobe.exe`.
- Review the license/distribution terms for the FFmpeg build before shipping binaries.

Optional installer EXE:

```bat
build_installer.bat
```

This requires Inno Setup locally. `build_installer.bat` runs `build.bat` first so the installer packages a fresh PyInstaller output, checks common current-user and machine install locations before falling back to `ISCC.exe` on `PATH`, clears stale setup artifacts for the current version before compiling, and verifies the built EXE version before compiling. The installer uses a per-user install location under `%LOCALAPPDATA%\Programs\ViriaRevive` and does not require admin privileges. User clips, tokens, OAuth files, state, personalization, and debug output remain in `%LOCALAPPDATA%\ViriaRevive`.

Never package from a hand-made ZIP of the live checkout. Use `build.bat` so `scripts/check_version_sync.py` and `scripts/check_release_safety.py` can block version drift and accidental inclusion of `tokens/`, clips, subtitles, state files, debug JSON, `.env`, or OAuth secrets.

### Version Bump Checklist

1. Update `APP_VERSION` in `version.py`; `APP_VERSION_QUAD` is derived automatically unless that derivation changes.
2. Review `scripts/check_version_sync.py` for any version-specific guardrails.
3. Run `venv\Scripts\python.exe -B scripts\check_version_sync.py`.
4. Run the no-video tests with `venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_*.py"`.
5. Run `build.bat`, then `build_installer.bat` if shipping the setup EXE.
6. Check release hashes and create the Git tag only after the version commit is final.

---

## Project Structure

```text
ViriaRevive/
├── bin/
│   └── README.md               # Optional local ffmpeg/ffprobe drop-in folder
├── gui/
│   ├── index.html              # Main UI layout
│   ├── app.js                  # Frontend logic, scheduling, state
│   └── style.css               # Desktop app styling
├── installer/
│   └── ViriaRevive.iss         # Optional Inno Setup installer script
├── scripts/
│   ├── check_release_safety.py # Blocks private data from release output
│   ├── check_version_sync.py   # Fails release builds on version drift
│   └── write_release_hashes.py # Writes release artifact SHA256 sidecars
├── app.py                      # GUI launcher with console
├── app.pyw                     # GUI launcher without console
├── ViriaRevive.vbs             # No-console launcher helper
├── ViriaRevive_Startup.vbs     # Startup-folder launcher helper
├── setup_startup.bat           # Creates Windows Startup shortcut
├── main.py                     # CLI entry point
├── api_bridge.py               # Python <-> JavaScript bridge
├── audio_streams.py            # Multi-track audio inspection/mixing helpers
├── candidate_ranker.py         # Transcript-aware candidate scoring/trimming
├── detector.py                 # Audio/scene candidate detection
├── clipper.py                  # FFmpeg clip extraction, subtitles, audio mix
├── cropper.py                  # YOLO/OpenCV crop detection
├── speech_stream_selector.py   # Whisper-based speech stream selection
├── transcriber.py              # Faster-Whisper integration
├── subtitler.py                # ASS subtitle generation
├── title_generator.py          # Ollama/heuristic titles, descriptions, tags, and AI moment labels
├── uploader.py                 # YouTube OAuth, accounts, channels, uploads
├── downloader.py               # yt-dlp wrapper
├── config.py                   # Paths and default settings
├── version.py                  # Central app version/build metadata
├── tray.py                     # Windows system tray integration
├── windows_subprocess.py       # Hidden child console handling on Windows
├── subprocess_utils.py         # Cancellable subprocess wrapper
├── client_secrets.example.json # Example OAuth client secret shape
├── viria.spec                  # PyInstaller one-folder release spec
├── build.bat                   # Windows ZIP build
├── build_installer.bat         # Optional installer build
├── requirements-build.txt      # Build-only dependencies
├── scripts/                    # Release safety, version, dependency, and hash checks
├── tests/                      # Synthetic no-video regression tests
└── requirements.txt
```

Generated local folders and files such as `clips/`, `downloads/`, `subtitles/`, `music/`, `tokens/`, `viria_state.json`, `personalization.json`, and `voice_profile.json` are intentionally ignored by Git.

---

## Preparing A Fork

If this checkout still points at upstream and you want your own fork:

```bash
git remote rename origin upstream
git remote add origin https://github.com/<your-github-username>/ViriaRevive.git
git push -u origin main
```

Before pushing, confirm that secrets and generated data are not staged:

```bash
git status --short
```

Do not commit `client_secrets.json`, `tokens/`, `downloads/`, `clips/`, `subtitles/`, `music/`, `viria_state.json`, `personalization.json`, `voice_profile.json`, exported personalization files, or migration/corrupt/clear backups.

---

## Contributing

Contributions are welcome. A pull request is a proposed set of changes from your fork back to another repository, usually upstream. It lets the maintainer review the diff, discuss it, run checks, and decide whether to merge it.

Standard flow:

```bash
git checkout -b feature/amazing-feature
git add .
git commit -m "Add amazing feature"
git push origin feature/amazing-feature
```

Then open a pull request on GitHub from your branch. Before opening one, run the tests and make sure no private runtime data is staged.

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

This fork is based on the original ViriaRevive repository by VladPolus.
