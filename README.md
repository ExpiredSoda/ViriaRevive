<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/Windows-first-0078D6?style=for-the-badge&logo=windows&logoColor=white" alt="Windows first">
  <img src="https://img.shields.io/badge/FFmpeg-bundled_or_PATH-007808?style=for-the-badge&logo=ffmpeg&logoColor=white" alt="FFmpeg bundled or PATH">
  <img src="https://img.shields.io/badge/Ollama-optional-FF6B6B?style=for-the-badge" alt="Ollama optional">
  <img src="https://img.shields.io/badge/license-MIT-blue?style=for-the-badge" alt="MIT License">
</p>

<h1 align="center">
  ViriaRevive
  <br>
  <sub><sup>Turn long gameplay recordings into short-form clips.</sup></sub>
</h1>

<p align="center">
  ViriaRevive finds promising moments in long gameplay videos, renders vertical clips,
  adds optional subtitles, helps write titles and descriptions, and can schedule posts
  to YouTube.
</p>

<p align="center">
  <strong>Current version: v2.3.1</strong>
</p>

<p align="center">
  <img src="docs/preview.png" alt="ViriaRevive desktop app preview" width="850">
</p>

---

## Why Use It

Gameplay recordings are long. Good Shorts are usually buried in the middle.

ViriaRevive is built for creators who want a local desktop workflow for finding
those moments without dragging a three-hour recording through a timeline by hand.
It looks for speech, reactions, gameplay intensity, scene changes, failures,
explainer moments, atmosphere, and creator feedback over time.

It is especially useful for:

- Streamers turning long sessions into Shorts.
- YouTubers reviewing local OBS recordings.
- Creators with separate mic/game audio tracks.
- Anyone who wants clips, subtitles, titles, descriptions, and upload scheduling
  in one place.

---

## What It Does

### Find Better Moments

- Choose **Fast**, **Balanced**, or **Deep Analysis** depending on how much time
  you want the app to spend looking.
- Uses audio peaks, scene signals, transcript quality, category labels, and
  local feedback to rank clips.
- Can prefer fewer stronger clips instead of padding a batch with weak moments.
- Learns from local like, dislike, favorite, and reason feedback.

### Handle Gameplay Audio

- Keeps the final clip audio mix intact.
- Can choose a separate mic/commentary track for subtitles and title context.
- Can guard against game/NPC speech or music lyrics being mistaken for creator
  commentary.
- Works with normal single-track videos too, including YouTube downloads where
  voice and game audio are already mixed together.

### Render Shorts

- Creates vertical clips for short-form platforms.
- Keeps already-vertical footage from being forced through an unnecessary crop.
- Offers subtitle styles, subtitle placement controls, and a **None** option for
  clips with no words on screen.
- Supports optional visual effects and local background music.

### Review, Rate, And Improve

- Results and All Videos show playable clip cards, thumbnails, labels, and local
  feedback controls.
- Feedback stays on your machine and can nudge future detection.
- Optional creator voice profile stores numeric local features only, not raw
  audio, and can be used as a small opt-in ranking nudge.

### Titles, Descriptions, And Uploads

- Generates titles, descriptions, tags, and sidecar `.txt` files for manual
  posting.
- Uses optional local Ollama for richer AI titles and moment labels.
- Supports creator-provided AI notes so titles can understand the game/session
  context better.
- Can connect YouTube accounts with your own Google OAuth credentials.
- Includes a calendar scheduler, upload readiness checks, and local upload
  history.

---

## Local-First Privacy

ViriaRevive is designed to keep creator data local.

- Clips, debug reports, feedback, voice profile data, settings, and YouTube
  tokens are stored on your PC.
- Clip detection, transcript ranking, local labels, and feedback learning do not
  upload your raw media to a cloud service.
- Ollama is optional and runs locally when installed.
- YouTube upload is optional and only uses Google APIs after you connect your own
  account.

Installed builds store private runtime data here:

```text
%LOCALAPPDATA%\ViriaRevive
```

Source checkouts keep local runtime folders beside the code for development.
Never commit clips, tokens, OAuth files, state files, feedback exports, or debug
reports.

---

## Install

### Recommended For Most Users

Download a release package from this repository's GitHub Releases page:

1. **Installer EXE** - run `ViriaReviveSetup-v2.3.1.exe`, then launch
   ViriaRevive from the Start Menu.
2. **ZIP app** - extract `ViriaRevive-v2.3.1-Windows-x64.zip`, then run
   `ViriaRevive.exe`.

FFmpeg support depends on the release package:

- Some builds may include reviewed `ffmpeg.exe` and `ffprobe.exe` in the app's
  local `bin/` folder.
- Source checkouts and unbundled builds can use FFmpeg from your system `PATH`.

If video probing or rendering fails, install FFmpeg from
[ffmpeg.org](https://ffmpeg.org/download.html), then make sure `ffmpeg` and
`ffprobe` are available from a normal Command Prompt.

### Optional Local AI

Ollama is optional. Without it, ViriaRevive still uses local heuristic title and
label fallbacks.

In the app, open **Settings > Local AI**:

1. **Install Ollama** opens the official Ollama download page.
2. **Install via PowerShell** asks first, then runs Ollama's official Windows
   installer command in a visible PowerShell window.
3. **Download AI Model** pulls the local model used for AI titles and labels.
4. The footer status only says Ollama is ready when the local Ollama API and
   selected model are actually detected.

### Optional YouTube Upload

YouTube upload uses your own Google OAuth desktop-app credentials.

In the app, open **Upload > Setup guide** for the exact app data folder and
current instructions. The short version:

1. Open [Google Cloud Console credentials](https://console.cloud.google.com/apis/credentials).
2. Create or select a project.
3. Enable **YouTube Data API v3**.
4. Configure the OAuth consent screen. For personal use, keep it in **Testing**
   and add your own Google account as a test user.
5. Create an **OAuth 2.0 Client ID** for a **Desktop app**.
6. Download the JSON file and save it as `client_secrets.json` in the app data
   folder shown by ViriaRevive.
7. Click **Add Account** in ViriaRevive and finish the browser sign-in flow.

Do not commit `client_secrets.json`, `tokens/`, or generated OAuth files.

---

## Basic Workflow

1. **Generate** - paste YouTube URLs or choose local video files.
2. **Configure** - pick style, detection depth, audio source, effects, and music.
3. **Analyze** - ViriaRevive finds and ranks candidate moments.
4. **Review** - watch clips, favorite the good ones, and dislike the misses.
5. **Prepare** - generate titles, descriptions, tags, and metadata sidecars.
6. **Schedule** - drag clips onto the upload calendar.
7. **Publish** - upload through YouTube scheduling or post clips manually.

---

## Source Install

Use this if you want to develop, inspect, or modify the app.

```bat
git clone https://github.com/ExpiredSoda/ViriaRevive.git
cd ViriaRevive

python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Launch from source:

```bat
python app.py
```

Launch without a console window:

```bat
pythonw app.pyw
```

CLI mode is available for simpler runs:

```bat
python main.py "https://youtube.com/watch?v=VIDEO_ID"
python main.py "URL" --clips 5 --duration 30 --style bold
```

The desktop app is the main experience. CLI mode is intentionally leaner.

---

## Testing

The test suite is mostly synthetic and does not require real videos for the core
regression checks.

```bat
python -m unittest discover -s tests -p "test_*.py"
```

Useful targeted checks:

```bat
python -m unittest discover -s tests -p "test_upload_scheduling.py"
python -m unittest discover -s tests -p "test_transcriber_batch.py"
python -m unittest discover -s tests -p "test_release_guards.py"
python -m unittest discover -s tests -p "test_external_status_truth.py"
```

---

## Building Releases

Release builds are Windows-first and are designed to keep private runtime data
out of packages.

```bat
build.bat
```

`build.bat` creates a PyInstaller one-folder app, writes build provenance, checks
release safety/compliance, and creates ZIP artifacts such as:

```text
release\ViriaRevive-v2.3.1-Windows-x64.zip
release\ViriaRevive-Windows-x64.zip
```

Optional installer:

```bat
build_installer.bat
```

The installer requires Inno Setup. It installs per-user under
`%LOCALAPPDATA%\Programs\ViriaRevive` and keeps user clips, tokens, OAuth files,
state, feedback, and debug output in `%LOCALAPPDATA%\ViriaRevive`.

### Bundled FFmpeg Releases

If a public release bundles FFmpeg:

- Include `ffmpeg.exe`, `ffprobe.exe`, and `bin/FFMPEG_BUILD.json`.
- Use an immutable download/source URL where possible.
- Include applicable FFmpeg GPL/LGPL license/source notices.
- Do not distribute builds configured with `--enable-nonfree`.
- Keep FFmpeg as separate executables in `bin/`.

The release compliance script checks FFmpeg provenance, hashes, important package
notices, and required release files.

---

## Project Structure

```text
ViriaRevive/
├── gui/                       # Desktop UI
├── installer/                 # Inno Setup installer script
├── scripts/                   # Build, version, safety, and compliance checks
├── tests/                     # Synthetic regression tests
├── app.py                     # GUI launcher with console
├── app.pyw                    # GUI launcher without console
├── main.py                    # CLI entry point
├── api_bridge.py              # Python <-> JavaScript bridge
├── detector.py                # Candidate detection
├── transcriber.py             # Faster-Whisper integration
├── clipper.py                 # FFmpeg clip rendering
├── title_generator.py         # Titles, descriptions, tags, AI labels
├── uploader.py                # YouTube OAuth and uploads
├── version.py                 # Central app version metadata
├── build.bat                  # Windows ZIP build
├── build_installer.bat        # Optional installer build
├── THIRD_PARTY_NOTICES.md     # Source-level third-party summary
└── requirements.txt
```

Generated folders such as `clips/`, `downloads/`, `subtitles/`, `music/`,
`tokens/`, app state files, feedback files, voice profiles, and debug reports are
ignored by Git.

---

## Fork And Attribution

This repository is a heavily modified fork of the original
[ViriaRevive project by VladPolus](https://github.com/VladPolus/ViriaRevive).

This fork keeps the original open-source foundation and adds a creator-focused
workflow around multi-track gameplay audio, transcript-aware ranking, local
learning, upload preparation, packaging, and public-release safety.

If you contribute changes that are useful to the original project, open a pull
request upstream. If your work is specific to this fork's creator workflow, open
an issue or pull request here.

---

## Contributing

Contributions are welcome.

```bat
git checkout -b feature/amazing-feature
git add .
git commit -m "Add amazing feature"
git push origin feature/amazing-feature
```

Then open a pull request on GitHub.

Before opening one:

- Run the tests.
- Do not stage private runtime data.
- Do not commit OAuth credentials, YouTube tokens, clips, debug reports,
  feedback exports, or local state files.

---

## License

ViriaRevive is licensed under the MIT License. See [LICENSE](LICENSE).

Release packages include and/or use third-party components under additional
licenses. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), and for packaged
builds inspect the generated `BUILD-MANIFEST.json` and `licenses/` folder.

This fork is intended to remain open source. Some optional features rely on
third-party components with their own obligations, including FFmpeg and
Ultralytics YOLO. Public release owners should review those notices before
publishing binaries.
