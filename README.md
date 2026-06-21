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
  <strong>Current version: v2.1.0</strong>
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
- Multi-track audio support: detection/rendering can mix all audio streams while subtitles use the best speech stream.
- Alternate speech-stream retries when the first transcription is too weak or starts too late.
- Scene-detection diagnostics with explicit statuses such as `ok`, `zero_changes`, `timeout`, `ffmpeg_missing`, and `ffmpeg_error`.
- Run and candidate debug JSON reports for inspecting why clips were selected or rejected.
- Stable state schema v2 with `clip_id`, `source_id`, atomic writes, and migration backups.
- Local feedback adds a small capped learned score into candidate selection.
- YouTube upload metadata defaults for Gaming, larger tag sets, and required Shorts/game hashtags.
- Customizable upload descriptions with generated context, optional creator text, and recommended Shorts/game hashtags.
- Ollama status indicator, opt-in setup controls, and game-aware title generation using `qwen2.5:3b` when available.
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
- Release safety scanning blocks known local runtime data, OAuth/token files, `.env*`, personalization/state backups, debug reports, private folders, and obvious private JSON markers.
- `.gitignore` excludes OAuth files, token folders, `.env*`, generated state, debug reports, local media, models, private backups, and release artifacts.
- App-local FFmpeg support packages only allowlisted `bin/` files: `README.md`, `ffmpeg.exe`, and `ffprobe.exe`.
- YouTube setup remains bring-your-own OAuth credentials, but the app now opens Google credentials and the exact app data folder from the UI.
- Connected YouTube accounts and Ollama status are based on real local token/service checks; OAuth client JSON files are not treated as connected accounts.
- `build_installer.bat` requires Inno Setup and `ISCC.exe`; `build.bat` alone creates the ZIP app.

Remaining public-release work:

- Add a full dependency vulnerability/SBOM audit before broad public distribution.
- Add code signing before broad public distribution so Windows users see a more trustworthy install path.
- Consider a verified public Google OAuth client later, if the project is distributed beyond personal/dev use.
- Add DPAPI/keyring protection for YouTube token files.

---

## What It Does

ViriaRevive is a local desktop app for finding short-form moments in longer videos. It downloads or accepts local video files, detects candidate moments using audio and scene signals, transcribes likely speech, reranks candidates by transcript quality, trims around hooks/payoffs, renders clips with optional subtitles, and helps schedule or upload them to YouTube.

Processing runs locally. YouTube upload is optional and uses your own Google OAuth credentials. Ollama is optional and runs locally for better title generation.

---

## Features

### Detection And Ranking

- Audio-energy and scene-change candidate detection.
- Transcript-aware second-stage reranking.
- Capped learned-score blending from local like, dislike, and favorite feedback.
- Hook boosting for panic/chase/reaction phrases.
- Weak aftermath/navigation penalties so fewer low-quality filler clips are returned.
- Asymmetric gameplay windows that favor lead-up before the peak.
- Pre-event rescue candidates for death/restart peaks.
- Fewer-but-better output policy when only a small number of clips are strong.

### Multi-Track Audio

- Detects source audio streams with `ffprobe`.
- Mixes multiple audio streams for analysis and rendered clip audio.
- Selects the speech stream by Whisper sampling instead of trusting OBS labels alone.
- Retries alternate streams when subtitles have too few words or poor timing.
- Keeps mic speech scoring separate from gameplay audio intensity.

### Subtitles And Rendering

- Faster-Whisper transcription.
- ASS subtitle burn-in with word-level timing.
- Subtitle styles: TikTok, Karaoke, Neon Glow, Clean, Bold, Minimal, and None for clips with no words on screen.
- Caption box placement controls for horizontal position, vertical position, and width on vertical clips.
- FFmpeg subtitle filter fallback between `subtitles` and `ass`.
- Windows font handling for libass subtitle rendering.
- YOLO/OpenCV person-aware crop for horizontal footage.
- Already-vertical footage can pass through without forced recrop.
- Optional video effects and background music from the local `music/` folder.

### Titles, Metadata, And Sidecars

- Ollama title generation with `qwen2.5:3b` when available.
- Heuristic title fallback when Ollama is unavailable.
- Title prompts reuse saved clip-generation analysis: transcript, game title, detector scores, candidate kind/rank, selected timing, ranker hook/weak/aftermath points, learned score, and subtitle word count.
- Descriptions, tags, and sidecar files use the same compact title context so metadata reflects the actual selected moment instead of only the raw transcript.
- Titles are formatted with `#shorts` plus a game hashtag.
- Upload category is forced to YouTube Gaming (`20`).
- Tag generation stays roughly 100 characters under YouTube's tag limit.
- Default descriptions are neutral and compact by default, with clip context plus `#shorts`, the game hashtag, and `#gaming`.
- The Upload page has compact description controls for recommended hashtags and default custom text.
- Each scheduled clip metadata modal keeps generated description text read-only: you can edit title, tags, visibility/time, and custom description text, then preview the final composed description so AI metadata refreshes do not overwrite creator copy.
- Generated metadata is stored on each persisted moment under `generated_metadata` and also written to `.txt` sidecars.
- `.txt` metadata sidecars can be generated beside clips for manual YouTube/TikTok posting.

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
- Per-run debug JSON in `subtitles/`.
- Candidate debug JSON with pre-render scores, reject reasons, transcripts, selected streams, and scene diagnostics.
- Run debug JSON with final render metadata and rendered clip rows.
- Learned scoring diagnostics that show base score, capped adjustment, learned score, and selection/rank changes.
- Persistent state in `viria_state.json` with schema migration and atomic saves.
- Persisted clip paths are constrained to the clips folder before preview, delete, scheduler, upload, and auto-delete actions.
- Personalization feedback in `personalization.json` with like, dislike, favorite, reason, and timestamp events.
- Result-card and preview-modal controls for marking clips as liked, disliked, or favorite.
- Beginner-friendly Data & Privacy settings card for reviewing local learning status, opening raw local feedback, exporting a share-safe copy, and clearing local feedback data.
- Ollama status pill in the app footer plus an AI Titles settings card for opt-in Ollama/model setup. The status only reports ready when the local Ollama API responds like Ollama and the selected model is installed.

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
| AI Titles | [Ollama](https://ollama.com/) |
| YouTube API | Google API v3 with OAuth 2.0 |

---

## Getting Started

### Recommended User Install

After a release is published, use one of these instead of cloning the repository:

1. **Installer EXE** - download `ViriaReviveSetup-v2.1.0.exe`, run it, then launch ViriaRevive from the Start Menu.
2. **ZIP app** - download `ViriaRevive-v2.1.0-Windows-x64.zip` or the stable latest copy `ViriaRevive-Windows-x64.zip`, extract it, then double-click `ViriaRevive.exe`.

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
4. **Ollama** *(optional)* - Needed only for local AI title generation.
5. **Google OAuth credentials** *(optional)* - Needed only for YouTube upload.

First use may download local model assets depending on the enabled features. Faster-Whisper can download speech models, YOLO/OpenCV crop detection can load YOLO assets, and Ollama pulls `qwen2.5:3b` only when you click **Download Title Model**.

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

Ollama is optional and local. The app does not install Ollama or download models automatically during title generation.

In the app, open **Settings > AI Titles**:

1. **Install Ollama** opens the official Ollama Windows download page in your browser.
2. **Install via PowerShell** asks for confirmation, then opens a new PowerShell window running Ollama's official Windows installer command: `irm https://ollama.com/install.ps1 | iex`. Once Ollama is detected, the setup buttons change to maintenance/status actions such as **Open Ollama Folder** and **Ollama Installed**.
3. **Download Title Model** pulls the current title model, `qwen2.5:3b`, only after you click it. Once the model exists, the button changes to **Title Model Ready** and only re-checks status.
4. **Refresh** checks whether Ollama is running and whether the title model is ready.

The app checks Ollama locally at `http://127.0.0.1:11434`. The status is considered real only when `/api/tags` returns an Ollama-style model list; a random service on that port does not count. If Ollama or the model is unavailable, title generation falls back to heuristic titles.

### YouTube Upload Setup

To enable YouTube uploads:

1. In ViriaRevive, open **Upload > Setup guide** or **Settings > Data & Privacy > Open Data Folder**.
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

For source checkouts, put `client_secrets.json` in the project root. For installed builds, open **Upload > Setup guide** or **Settings > Data & Privacy > Open Data Folder** and put `client_secrets.json` there. Connected account tokens are stored in the app data `tokens/` folder. Token filenames are constrained to safe YouTube account IDs, and legacy `token.json` migration keeps the original file unless migration and the new token save both succeed.

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
2. **Configure** - Pick clip count, duration, gap, crop, subtitle style/placement, Whisper model, effects, and music.
3. **Analyze** - Let the app detect, transcribe, rerank, trim, and render clips.
4. **Review** - Inspect clips in Results or Preview, then mark clips as liked, disliked, or favorite with an optional reason.
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

# Generate and schedule public YouTube publishing
python main.py "URL" --upload --schedule 24
```

CLI mode is intentionally leaner than the desktop workflow. It supports URL processing, detection, transcription, ranking, rendering, subtitle placement options, and optional public scheduled upload when `--upload` is used. The GUI-only features include local file picker flows, batch queue management, Ollama title/metadata sidecars, custom reusable descriptions, rich per-clip upload metadata, calendar editing, and feedback controls.

---

## Local Files And Generated Data

These paths are local runtime data and should not be committed. In source checkouts they live beside the code. In installed builds they live under `%LOCALAPPDATA%\ViriaRevive`.

- `downloads/` - downloaded source videos.
- `clips/` - rendered clips and metadata sidecars.
- `subtitles/` - temporary audio, ASS subtitle files, candidate debug JSON, and run debug JSON.
- `music/` - optional local background music files.
- `tokens/` - YouTube OAuth tokens and optional OAuth secret copy.
- `client_secrets.json` - Google OAuth client secret.
- `viria_state.json` - app state.
- `viria_state.*.bak` - state migration backups.
- `personalization.json` - local feedback events, latest per-clip summaries, and learned-ranking signals.
- `personalization.*.bak` - corrupt personalization backups.

The current `.gitignore` excludes these paths plus common secret files such as `.env` and `.env.*`. Release builds are also checked by `scripts/check_release_safety.py` before ZIP/installer packaging. That scanner blocks known private runtime data, common OAuth/token filenames, private folders, debug reports, and obvious secret-bearing JSON markers while allowing the harmless `client_secrets.example.json` template.

---

## State Persistence

The desktop app stores local UI state in `viria_state.json` in the app data folder. This file tracks generated clip paths, clip metadata, upload schedules, delete-after-upload preference, user settings, and the current `schema_version`.

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
- `reason` - optional note entered from the Results UI.
- `timestamp` - UTC timestamp generated by the backend.
- `clip_snapshot` - compact transcript/ranker context used by learned candidate scoring on future runs. Redacted feedback exports remove transcript text from these snapshots and strip or hash share-sensitive fields such as feedback reasons, clip/source IDs, filenames, source names, and exact timestamps.

The file also keeps a `clips` summary keyed by `clip_id` so the UI can quickly restore current like/dislike/favorite state. Learned scoring treats each clip's `latest` summary as the source of truth, so removed likes stop counting and like/dislike flips do not leave stale positive or negative signals behind. Historical `events` remain as an audit trail, and are replayed only as a fallback for legacy/event-only personalization files.

Feedback controls are available on each Results card and inside the clip Preview modal. Toggling one of these controls updates every visible copy of the same clip.

The Settings tab includes a beginner-friendly Data & Privacy card with a local summary of feedback clicks, clips you rated, feedback file size, and learning status. The learning readout explains how many active ratings are being used now, the small maximum learning nudge, and the last time you rated a clip. From that card you can open the app data folder, open raw `personalization.json`, export a share-safe copy, refresh the summary, or clear learning feedback. The share-safe export removes transcript text, user-entered reasons, filenames, source names, exact timestamps, and raw clip/source IDs while keeping hashed references for aggregate debugging. Clearing feedback also removes future learned-ranking influence, and writes a backup named like `personalization.cleared.YYYYMMDD_HHMMSS.json.bak` before creating a fresh empty file.

Subtitle Style includes a **None** option in both Settings and the pre-generation wizard. None skips subtitle file generation and renders clips without burned-in words while still allowing transcript analysis to help clip ranking and titles.

---

## Debug Reports

When detection runs, the app can write debug files into `subtitles/`:

- `*_candidate_debug.json` - pre-render candidate selection state only: all candidates, scores, transcripts, selected stream, reject reasons, scene diagnostics, and learned-scoring diagnostics.
- `*_run_debug.json` - post-render run state: the same candidate diagnostics plus `final_clips`, subtitles status, render warnings, rendered paths, and final render metadata.

Both debug files include a `debug_stage` field: `candidate_pre_render` for candidate debug and `run_post_render` for run debug. Candidate debug is not overwritten after rendering. Rendered clip rows also record resolved `subtitle_placement` values, including requested percentages, ASS pixel coordinates, margins, and alignment.

Both debug files include a `shadow_scoring` section. Learned scoring reads the current active feedback state from `personalization.json`, computes a diagnostic `shadow_score`, then blends only a small capped `learned_adjustment` into `learned_quality_score` for candidate selection. Candidate rows flatten the most useful fields at `candidates[].base_quality_score`, `candidates[].learned_adjustment`, `candidates[].learned_score`, `candidates[].learned_quality_score`, `candidates[].rank_delta`, and `candidates[].selection_delta`. The full nested detail remains at `candidates[].shadow_scoring`, and report-level summaries are available in `shadow_scoring.top_changes[]` and `shadow_scoring.selection_delta_counts`. The cap keeps transcript/audio quality dominant while allowing feedback to nudge close calls.

Scene diagnostics are explicit:

- `ok` - scene detection ran and found changes.
- `zero_changes` - scene detection ran but found no scene cuts.
- `timeout` - FFmpeg scene scan took too long.
- `ffmpeg_missing` - FFmpeg was unavailable.
- `ffmpeg_error` - FFmpeg returned an error.

Audio/transcript ranking remains the fallback when scene detection is unavailable.

---

## Testing

Synthetic no-video regression tests cover learned feedback reconciliation, capped score blending, removed feedback, like-to-dislike flips, Data & Privacy learning status and share-safe redacted exports, subtitle placement output, debug report stage/learning fields, analysis-aware title context, composed descriptions, Ollama status truth checks, YouTube account-token filtering, per-clip UTC publish scheduling including DST offsets, upload cancellation state, GUI schedule identity guards, release hash/safety guardrails, visible installer subprocess launches, and clip-path delete safety:

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
release\ViriaRevive-v2.1.0-Windows-x64.zip
release\ViriaRevive-Windows-x64.zip
release\ViriaRevive-v2.1.0-Windows-x64.zip.sha256
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
├── title_generator.py          # Ollama/heuristic titles, descriptions, tags
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

Generated local folders and files such as `clips/`, `downloads/`, `subtitles/`, `music/`, `tokens/`, `viria_state.json`, and `personalization.json` are intentionally ignored by Git.

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

Do not commit `client_secrets.json`, `tokens/`, `downloads/`, `clips/`, `subtitles/`, `music/`, `viria_state.json`, `personalization.json`, exported personalization files, or migration/corrupt/clear backups.

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
