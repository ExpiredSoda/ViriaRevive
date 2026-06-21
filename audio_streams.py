"""Helpers for multi-track source audio.

OBS MKV recordings often store microphone and game/desktop audio as separate
streams. These helpers let the pipeline use the right stream for transcription
while still mixing all source audio into detection and rendered clips.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from subprocess_utils import run as _run


VOICE_KEYWORDS = ("microphone", "mic", "voice", "commentary", "narration")


@lru_cache(maxsize=64)
def get_audio_streams(video_path: str | Path) -> tuple[dict, ...]:
    """Return audio streams with both ffmpeg ordinal and file stream index."""
    path = str(Path(video_path))
    try:
        r = _run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a",
                "-show_entries",
                "stream=index,codec_name,channels,channel_layout:stream_tags=title,language",
                "-of", "json",
                path,
            ],
            capture_output=True, text=True, timeout=10, errors="replace",
        )
        data = json.loads(r.stdout or "{}")
    except Exception as e:
        print(f"[audio] Could not inspect audio streams: {e}")
        return ()

    streams = []
    for ordinal, stream in enumerate(data.get("streams", [])):
        tags = stream.get("tags") or {}
        streams.append({
            "ordinal": ordinal,
            "index": stream.get("index"),
            "codec": stream.get("codec_name"),
            "channels": stream.get("channels"),
            "layout": stream.get("channel_layout"),
            "title": tags.get("title", ""),
            "language": tags.get("language", ""),
        })
    return tuple(streams)


def pick_voice_stream_ordinal(video_path: str | Path) -> int | None:
    """Pick the likely microphone/commentary stream. Fallback is first audio."""
    streams = get_audio_streams(video_path)
    if not streams:
        return None

    for stream in streams:
        title = str(stream.get("title", "")).lower()
        if any(keyword in title for keyword in VOICE_KEYWORDS):
            print(
                "[audio] Transcription stream: "
                f"0:a:{stream['ordinal']} ({stream.get('title') or 'untitled'})"
            )
            return int(stream["ordinal"])

    print("[audio] No named mic stream found; using first audio stream for transcription")
    return int(streams[0]["ordinal"])


def describe_audio_streams(video_path: str | Path) -> str:
    streams = get_audio_streams(video_path)
    if not streams:
        return "no audio streams"
    return ", ".join(
        f"0:a:{s['ordinal']}={s.get('title') or 'untitled'}"
        for s in streams
    )


def build_audio_mix_filter(video_path: str | Path, mono: bool = False) -> tuple[str, str] | None:
    """Build a filter_complex graph that mixes all source audio streams."""
    streams = get_audio_streams(video_path)
    if len(streams) < 2:
        return None

    layout = "mono" if mono else "stereo"
    parts = []
    labels = []
    for stream in streams:
        ordinal = int(stream["ordinal"])
        label = f"a{ordinal}"
        parts.append(
            f"[0:a:{ordinal}]"
            f"aresample=16000,"
            f"aformat=sample_fmts=s16:channel_layouts={layout}"
            f"[{label}]"
        )
        labels.append(f"[{label}]")

    out_label = "aout"
    parts.append(
        f"{''.join(labels)}"
        f"amix=inputs={len(labels)}:duration=longest:normalize=1"
        f"[{out_label}]"
    )
    return ";".join(parts), out_label


def audio_output_args(video_path: str | Path, bitrate: str = "192k",
                      copy_single: bool = False) -> list[str]:
    """Return ffmpeg output args that keep video plus the intended audio mix."""
    streams = get_audio_streams(video_path)
    if not streams:
        return ["-map", "0:v:0", "-an"]

    if len(streams) == 1:
        args = ["-map", "0:v:0", "-map", "0:a:0"]
        if copy_single:
            return [*args, "-c:a", "copy"]
        return [*args, "-c:a", "aac", "-strict", "-2", "-b:a", bitrate]

    mix = build_audio_mix_filter(video_path, mono=False)
    if not mix:
        return ["-map", "0:v:0", "-map", "0:a:0", "-c:a", "aac", "-strict", "-2", "-b:a", bitrate]

    filter_graph, out_label = mix
    print(f"[audio] Mixing source audio streams: {describe_audio_streams(video_path)}")
    return [
        "-filter_complex", filter_graph,
        "-map", "0:v:0", "-map", f"[{out_label}]",
        "-c:a", "aac", "-strict", "-2", "-b:a", bitrate,
    ]
