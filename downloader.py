import yt_dlp
from pathlib import Path
from config import DOWNLOADS_DIR

# Prefer H.264 (avc1) which every ffmpeg supports.
# Fallback chain avoids AV1/VP9 codec issues on Windows.
_FORMAT = (
    "bestvideo[vcodec^=avc1][height<=1080]+bestaudio[acodec^=mp4a]/"
    "bestvideo[vcodec^=avc1][height<=1080]+bestaudio/"
    "bestvideo[height<=1080]+bestaudio/"
    "best"
)


def resolve_downloaded_path(info: dict, ydl: yt_dlp.YoutubeDL) -> Path:
    """Return yt-dlp's final media file path after merges/post-processing."""
    if isinstance(info, dict):
        for download in info.get("requested_downloads") or []:
            if not isinstance(download, dict):
                continue
            filepath = download.get("filepath")
            if filepath:
                return Path(filepath)
        for key in ("filepath", "_filename"):
            filepath = info.get(key)
            if filepath:
                return Path(filepath)

    prepared = Path(ydl.prepare_filename(info))
    if prepared.exists():
        return prepared
    merged_mp4 = prepared.with_suffix(".mp4")
    if merged_mp4.exists():
        return merged_mp4
    return prepared


def download_video(url: str, output_dir: Path = DOWNLOADS_DIR) -> Path:
    """Download a YouTube video and return the file path."""
    output_dir.mkdir(exist_ok=True)

    ydl_opts = {
        "format": _FORMAT,
        "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
        "merge_output_format": "mp4",
        "restrictfilenames": True,   # ASCII-safe names (no unicode quotes etc.)
        "quiet": False,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "file_access_retries": 3,
        "extractor_retries": 3,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return resolve_downloaded_path(info, ydl)
