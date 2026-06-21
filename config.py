import os
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

MUSIC_DIR = APP_DATA_DIR / "music"
TOKENS_DIR = APP_DATA_DIR / "tokens"

for d in [APP_DATA_DIR, DOWNLOADS_DIR, CLIPS_DIR, SUBTITLES_DIR, MUSIC_DIR, TOKENS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

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

# FFmpeg encoding
FFMPEG_PRESET = "ultrafast"
VIDEO_CRF = "23"

# YouTube
CLIENT_SECRETS_FILE = APP_DATA_DIR / "client_secrets.json"
TOKEN_FILE = APP_DATA_DIR / "token.json"
DEFAULT_TAGS = ["shorts", "viral", "clips"]
