import os
import shutil
import sys
from pathlib import Path

# App files live next to the source checkout in dev, or next to the exe in
# frozen builds. User data moves to LOCALAPPDATA for frozen builds so installers
# never write tokens, clips, or state beside protected program files.
if getattr(sys, 'frozen', False):
    APP_DIR = Path(sys.executable).parent
    INTERNAL_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
    _local_appdata = os.environ.get("LOCALAPPDATA")
    APP_DATA_DIR = Path(_local_appdata) / "ViriaRevive" if _local_appdata else APP_DIR / "data"
else:
    APP_DIR = Path(__file__).parent
    INTERNAL_DIR = APP_DIR
    APP_DATA_DIR = APP_DIR

# Backward-compatible alias for code that needs the project/app root.
BASE_DIR = APP_DIR
BIN_DIR = APP_DIR / "bin"
INTERNAL_BIN_DIR = INTERNAL_DIR / "bin"
for _bin_dir in (BIN_DIR, INTERNAL_BIN_DIR):
    if _bin_dir.exists():
        os.environ["PATH"] = str(_bin_dir) + os.pathsep + os.environ.get("PATH", "")

DOWNLOADS_DIR = APP_DATA_DIR / "downloads"
CLIPS_DIR = APP_DATA_DIR / "clips"
SUBTITLES_DIR = APP_DATA_DIR / "subtitles"
STATE_FILE = APP_DATA_DIR / "viria_state.json"
STATE_SCHEMA_VERSION = 2
PERSONALIZATION_FILE = APP_DATA_DIR / "personalization.json"
PERSONALIZATION_SCHEMA_VERSION = 1
VOICE_PROFILE_FILE = APP_DATA_DIR / "voice_profile.json"
VOICE_PROFILE_SCHEMA_VERSION = 1
PROCESSING_HISTORY_FILE = APP_DATA_DIR / "processing_history.json"
PROCESSING_HISTORY_SCHEMA_VERSION = 1
ANALYSIS_CACHE_DIR = APP_DATA_DIR / "analysis_cache"

MUSIC_DIR = APP_DATA_DIR / "music"
TOKENS_DIR = APP_DATA_DIR / "tokens"

for d in [APP_DATA_DIR, DOWNLOADS_DIR, CLIPS_DIR, SUBTITLES_DIR, ANALYSIS_CACHE_DIR, MUSIC_DIR, TOKENS_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def _copy_legacy_file_if_missing(source: Path, target: Path):
    if not source.exists() or target.exists() or not source.is_file():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        print(f"[migration] Copied legacy data file: {source.name}")
    except OSError as exc:
        print(f"[migration] Could not copy legacy data file {source.name}: {exc}")


def _copy_legacy_dir_files_if_missing(source_dir: Path, target_dir: Path, patterns: tuple[str, ...]):
    if not source_dir.exists() or not source_dir.is_dir():
        return
    target_dir.mkdir(parents=True, exist_ok=True)
    for pattern in patterns:
        for source in source_dir.glob(pattern):
            _copy_legacy_file_if_missing(source, target_dir / source.name)


def _migrate_legacy_runtime_data():
    """Copy small pre-installer runtime files into the LocalAppData location."""
    if not getattr(sys, "frozen", False):
        return
    try:
        if APP_DATA_DIR.resolve() == APP_DIR.resolve():
            return
    except OSError:
        return

    for name in (
        "viria_state.json",
        "personalization.json",
        "voice_profile.json",
        "processing_history.json",
        "client_secrets.json",
        "token.json",
    ):
        _copy_legacy_file_if_missing(APP_DIR / name, APP_DATA_DIR / name)

    _copy_legacy_dir_files_if_missing(APP_DIR / "tokens", TOKENS_DIR, ("*.json",))
    _copy_legacy_dir_files_if_missing(APP_DIR / "music", MUSIC_DIR, ("*.mp3", "*.wav", "*.aac", "*.m4a", "*.flac", "*.ogg"))


_migrate_legacy_runtime_data()

# Clip detection
NUM_CLIPS = 5
CLIP_DURATION = 30
MIN_GAP = 15

# Whisper
WHISPER_MODEL = "base"
WHISPER_LANGUAGE = None

# Subtitle style
SUBTITLE_STYLE = "tiktok"
SUBTITLE_PLACEMENT = {
    "x_pct": 50,
    "y_pct": 82,
    "width_pct": 86,
}

# Cropping
CROP_VERTICAL = True          # auto-crop to 9:16 for Shorts
CROP_DEBUG_FRAMES = False     # save crop debug screenshots only when explicitly enabled

# FFmpeg encoding
FFMPEG_PRESET = "ultrafast"
VIDEO_CRF = "23"
FFMPEG_VERBOSE_COMMANDS = False

# YouTube
CLIENT_SECRETS_FILE = APP_DATA_DIR / "client_secrets.json"
TOKEN_FILE = APP_DATA_DIR / "token.json"
DEFAULT_TAGS = ["shorts", "viral", "clips"]
