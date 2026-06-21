import re
import platform
from pathlib import Path

# ── Font file mapping (for drawtext fallback) ────────────────────────────────

_FONT_FILES = {
    "Arial": "arial.ttf",
    "Arial Black": "ariblk.ttf",
    "Impact": "impact.ttf",
    "Verdana": "verdana.ttf",
}

# ── Style presets ────────────────────────────────────────────────────────────

STYLES = {
    "tiktok": {
        "font": "Arial Black",
        "size": 68,
        "primary": "&H00FFFFFF",
        "highlight": "&H0000D5FF",       # golden yellow  (BGR)
        "outline": "&H00000000",
        "back": "&H96000000",
        "bold": -1,
        "border": 4,
        "shadow": 2,
        "label": "TikTok",
        "desc": "Classic bold white with yellow pop",
    },
    "karaoke": {
        "font": "Arial Black",
        "size": 66,
        "primary": "&H0000FFFF",         # yellow (after karaoke fill)
        "highlight": "&H0000FFFF",       # same — used in karaoke mode
        "secondary": "&H00FFFFFF",       # white (before karaoke fill)
        "outline": "&H00000000",
        "back": "&H96000000",
        "bold": -1,
        "border": 4,
        "shadow": 2,
        "mode": "karaoke",               # signals karaoke rendering
        "label": "Karaoke",
        "desc": "Smooth left-to-right fill, no flicker",
    },
    "glow": {
        "font": "Arial Black",
        "size": 66,
        "primary": "&H00FFFFFF",
        "highlight": "&H00FF88FF",       # magenta/pink  (BGR)
        "outline": "&H00FF44CC",         # purple glow outline
        "back": "&H00000000",
        "bold": -1,
        "border": 6,
        "shadow": 0,
        "border_style": 1,               # outline + drop shadow
        "label": "Neon Glow",
        "desc": "Vibrant neon glow with pink highlight",
    },
    "clean": {
        "font": "Arial",
        "size": 60,
        "primary": "&H00FFFFFF",
        "highlight": "&H000088FF",       # orange (BGR)
        "outline": "&H00000000",
        "back": "&H96000000",
        "bold": -1,
        "border": 3,
        "shadow": 1,
        "label": "Clean",
        "desc": "Sleek white with orange accent",
    },
    "bold": {
        "font": "Impact",
        "size": 78,
        "primary": "&H00FFFFFF",
        "highlight": "&H000055FF",       # red (BGR)
        "outline": "&H00000000",
        "back": "&H96000000",
        "bold": -1,
        "border": 5,
        "shadow": 3,
        "label": "Bold",
        "desc": "Impactful red highlight, heavy shadows",
    },
    "minimal": {
        "font": "Verdana",
        "size": 52,
        "primary": "&H00FFFFFF",
        "highlight": "&H00FFFFFF",       # no color change — scale only
        "outline": "&H50000000",
        "back": "&H00000000",
        "bold": 0,
        "border": 2,
        "shadow": 0,
        "label": "Minimal",
        "desc": "Subtle white text, thin outline",
    },
}

NO_SUBTITLE_STYLE = "none"

DEFAULT_SUBTITLE_PLACEMENT = {
    "x_pct": 50,
    "y_pct": 82,
    "width_pct": 86,
}


def normalize_subtitle_placement(placement: dict | None = None) -> dict:
    """Clamp caption-box placement percentages into a usable video safe area."""
    data = placement if isinstance(placement, dict) else {}
    return {
        "x_pct": _clamp_pct(data.get("x_pct"), DEFAULT_SUBTITLE_PLACEMENT["x_pct"], 10, 90),
        "y_pct": _clamp_pct(data.get("y_pct"), DEFAULT_SUBTITLE_PLACEMENT["y_pct"], 12, 92),
        "width_pct": _clamp_pct(data.get("width_pct"), DEFAULT_SUBTITLE_PLACEMENT["width_pct"], 45, 96),
    }


def resolve_subtitle_placement(
    video_width: int,
    video_height: int,
    placement: dict | None = None,
) -> dict:
    """Return requested percentages plus resolved ASS pixel coordinates/margins."""
    normalized = normalize_subtitle_placement(placement)
    layout = _subtitle_layout(video_width, video_height, normalized)
    return {
        **normalized,
        "x_px": layout["x"],
        "y_px": layout["y"],
        "box_width_px": layout["box_width"],
        "margin_l": layout["margin_l"],
        "margin_r": layout["margin_r"],
        "margin_v": layout["margin_v"],
        "alignment": 5,
    }


def generate_subtitles(
    words: list,
    output_path: Path,
    video_width: int = 1920,
    video_height: int = 1080,
    style: str = "tiktok",
    subtitle_placement: dict | None = None,
) -> Path | None:
    """Generate ASS subtitles with word-by-word highlighting.

    Flicker-free: uses a base phrase layer + gapless highlight overlay.
    Automatically adjusts font size and phrase length for vertical video.
    """
    if not subtitles_are_enabled(style):
        output_path.unlink(missing_ok=True)
        print("[*] Subtitles disabled by style")
        return None

    if not words:
        output_path.unlink(missing_ok=True)
        print("[!] No words for subtitles")
        return None

    s = dict(STYLES.get(style, STYLES["tiktok"]))  # copy
    placement = resolve_subtitle_placement(video_width, video_height, subtitle_placement)

    # ── adapt for vertical video ─────────────────────────────────────────
    is_vertical = video_width < 900
    if is_vertical:
        s["size"] = round(s["size"] * 0.75)          # 68→51, 60→45, 78→59
        s["border"] = max(2, s["border"] - 1)
    max_words = _max_words_for_layout(is_vertical, placement["width_pct"])

    # Sanitize word timestamps — fix overlaps from Whisper
    words = _sanitize_word_times(words)
    if not words:
        output_path.unlink(missing_ok=True)
        print("[!] No usable words for subtitles")
        return None

    phrases = _group_phrases(words, max_words=max_words)
    if not phrases:
        output_path.unlink(missing_ok=True)
        print("[!] No subtitle phrases generated")
        return None

    use_karaoke = s.get("mode") == "karaoke"

    lines = [
        _ass_header(video_width, video_height, s, placement),
        "\n[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    position = _ass_position_override(placement)
    base_bold = 1 if s.get("bold", 0) != 0 else 0

    for phrase in phrases:
        pw = phrase["words"]
        phrase_start = pw[0]["start"]
        phrase_end = pw[-1]["end"]

        if use_karaoke:
            # ── Karaoke mode: single line with \kf tags ──────────────
            parts = []
            for i, w in enumerate(pw):
                # Duration in centiseconds
                dur_cs = int((w["end"] - w["start"]) * 100)
                dur_cs = max(10, dur_cs)  # minimum 0.1s
                parts.append(f"{{\\kf{dur_cs}}}{w['text'].upper()}")
            text = " ".join(parts)
            start = _ass_time(phrase_start)
            end = _ass_time(phrase_end)
            lines.append(
                f"Dialogue: 0,{start},{end},Default,,0,0,0,,{position}{text}"
            )
        else:
            # ── Standard mode: gapless highlight lines (no base layer) ─
            # Each line shows the full phrase with one word color-highlighted.
            # Timing is gapless: word[i] ends when word[i+1] starts.
            for i, word in enumerate(pw):
                parts = []
                for j, w in enumerate(pw):
                    if j == i:
                        # Highlight: color only (no scale — scale causes
                        # width mismatch that shows as double text)
                        parts.append(
                            f"{{\\c{s['highlight']}\\b1}}"
                            f"{w['text'].upper()}"
                            f"{{\\c{s['primary']}\\b{base_bold}}}"
                        )
                    else:
                        parts.append(w["text"].upper())

                text = " ".join(parts)

                # Gapless timing: extend to next word's start (or phrase end)
                w_start = word["start"]
                if i < len(pw) - 1:
                    w_end = pw[i + 1]["start"]
                else:
                    w_end = phrase_end

                # Ensure minimum duration
                if w_end <= w_start:
                    w_end = w_start + 0.1

                lines.append(
                    f"Dialogue: 0,{_ass_time(w_start)},{_ass_time(w_end)},"
                    f"Default,,0,0,0,,{position}{text}"
                )

    output_path.write_text("\n".join(lines), encoding="utf-8")
    mode_label = "karaoke" if use_karaoke else "highlight"
    print(
        f"[+] Subtitles saved: {output_path.name}  "
        f"({len(phrases)} phrases, {len(words)} words, {mode_label}, "
        f"x={placement['x_pct']}%, y={placement['y_pct']}%, width={placement['width_pct']}%)"
    )
    return output_path


def subtitles_are_enabled(style: str | None) -> bool:
    return str(style or "").strip().lower() != NO_SUBTITLE_STYLE


def get_available_styles() -> list[dict]:
    """Return style metadata for the UI style picker."""
    result = [{
        "id": NO_SUBTITLE_STYLE,
        "label": "None",
        "desc": "No words burned into the clip",
    }]
    for key, s in STYLES.items():
        result.append({
            "id": key,
            "label": s.get("label", key.title()),
            "desc": s.get("desc", ""),
        })
    return result


def generate_drawtext_vf(
    words: list,
    video_width: int = 540,
    video_height: int = 960,
    style: str = "tiktok",
    subtitle_placement: dict | None = None,
) -> str:
    """Generate a drawtext filter chain for subtitles (ffmpeg drawtext fallback).

    Used when the libass-based subtitle filter is broken (old ffmpeg builds).
    Returns a comma-separated chain of drawtext filters with time-based visibility.

    Each phrase is shown/hidden using: y=if(between(t,start,end), visible_y, -100)
    This works even on old ffmpeg that doesn't support timeline/enable.
    """
    if not subtitles_are_enabled(style):
        return ""

    if not words:
        return ""

    s = STYLES.get(style, STYLES["tiktok"])
    placement = resolve_subtitle_placement(video_width, video_height, subtitle_placement)

    is_vertical = video_width < 900
    font_size = round(s["size"] * 0.75) if is_vertical else s["size"]
    max_words = _max_words_for_layout(is_vertical, placement["width_pct"])

    # Resolve font file path
    font_name = s.get("font", "Arial")
    font_file = _FONT_FILES.get(font_name, "arial.ttf")
    if platform.system() == "Windows":
        fontfile_escaped = f"C\\:/Windows/Fonts/{font_file}"
    else:
        fontfile_escaped = f"/usr/share/fonts/truetype/{font_file}"

    visible_x = f"min(max(10\\,{placement['x_px']}-tw/2)\\,w-tw-10)"
    visible_y = f"min(max(10\\,{placement['y_px']}-th/2)\\,h-th-10)"
    phrases = _group_phrases(words, max_words=max_words)

    filters = []
    for phrase in phrases:
        text = " ".join(w["text"].upper() for w in phrase["words"])
        start = phrase["start"]
        end = phrase["end"]

        # Escape for ffmpeg drawtext: colons, backslashes, single quotes
        text = text.replace("\\", "\\\\")
        text = text.replace(":", "\\:")
        text = text.replace("'", "\u2019")  # replace apostrophe with unicode right single quote

        # Time-based y: visible during phrase, off-screen otherwise
        y_expr = f"if(between(t\\,{start:.2f}\\,{end:.2f})\\,{visible_y}\\,-100)"

        filt = (
            f"drawtext=text='{text}'"
            f":fontfile='{fontfile_escaped}'"
            f":fontsize={font_size}"
            f":fontcolor=white"
            f":x={visible_x}"
            f":y={y_expr}"
            f":shadowcolor=black:shadowx=3:shadowy=3"
        )
        filters.append(filt)

    print(f"[+] Generated drawtext filter chain: {len(filters)} phrases")
    return ",".join(filters)


# ── helpers ──────────────────────────────────────────────────────────────────


def _clean_word_text(text: str) -> str:
    """Strip punctuation and symbols from a subtitle word.

    Keeps letters, digits, and apostrophes (for words like don't, it's).
    Removes: ? ! . , ; : " ( ) [ ] { } * # @ & % ^ ~ / \\ etc.
    """
    # Keep apostrophes/right-single-quotes inside words (e.g. don't)
    # Remove all other non-alphanumeric characters
    text = re.sub(r"[^\w'\u2019]", "", text, flags=re.UNICODE)
    # Strip leading/trailing apostrophes (not mid-word ones)
    text = text.strip("'\u2019")
    return text


def _sanitize_word_times(words: list) -> list:
    """Fix common Whisper timing issues and clean text.

    - Strips punctuation/symbols from word text
    - Removes empty words after cleaning
    - Fixes overlaps, zero-duration, backwards timing
    """
    if not words:
        return words

    cleaned = []
    for w in words:
        cw = dict(w)
        # Clean text: remove punctuation and symbols
        cw["text"] = _clean_word_text(cw["text"])
        # Skip words that become empty after cleaning
        if not cw["text"]:
            continue
        # Ensure minimum word duration of 100ms
        if cw["end"] <= cw["start"]:
            cw["end"] = cw["start"] + 0.1
        if cw["end"] - cw["start"] < 0.05:
            cw["end"] = cw["start"] + 0.1
        cleaned.append(cw)

    # Fix overlaps: each word must start >= previous word's end
    for i in range(1, len(cleaned)):
        if cleaned[i]["start"] < cleaned[i - 1]["end"]:
            # Overlap — split the difference
            mid = (cleaned[i - 1]["end"] + cleaned[i]["start"]) / 2
            cleaned[i - 1]["end"] = mid
            cleaned[i]["start"] = mid
        # Ensure start < end still holds after fix
        if cleaned[i]["end"] <= cleaned[i]["start"]:
            cleaned[i]["end"] = cleaned[i]["start"] + 0.1

    return cleaned


def _group_phrases(
    words: list, max_words: int = 4, max_dur: float = 2.5, max_gap: float = 0.8
) -> list:
    if not words:
        return []
    phrases, cur = [], [words[0]]
    for w in words[1:]:
        prev = cur[-1]
        if len(cur) >= max_words or w["start"] - prev["end"] > max_gap or w["end"] - cur[0]["start"] > max_dur:
            phrases.append({"words": cur, "start": cur[0]["start"], "end": cur[-1]["end"]})
            cur = [w]
        else:
            cur.append(w)
    if cur:
        phrases.append({"words": cur, "start": cur[0]["start"], "end": cur[-1]["end"]})
    return phrases


def _clamp_pct(value, default: int, low: int, high: int) -> int:
    try:
        pct = int(round(float(value)))
    except (TypeError, ValueError):
        pct = default
    return max(low, min(high, pct))


def _max_words_for_layout(is_vertical: bool, width_pct: int) -> int:
    if is_vertical:
        return 2 if width_pct < 58 else 3
    return 3 if width_pct < 58 else 4


def _subtitle_layout(w: int, h: int, placement: dict) -> dict:
    safe_pad = max(8, round(min(w, h) * 0.02))
    desired_x = round(w * placement["x_pct"] / 100)
    y = round(h * placement["y_pct"] / 100)
    box_w = round(w * placement["width_pct"] / 100)
    box_w = max(1, min(box_w, max(1, w - (safe_pad * 2))))

    left = round(desired_x - (box_w / 2))
    left = max(safe_pad, min(max(safe_pad, w - safe_pad - box_w), left))
    right = max(safe_pad, w - left - box_w)
    x = round(left + (box_w / 2))
    y = max(safe_pad, min(max(safe_pad, h - safe_pad), y))

    return {
        "x": x,
        "y": y,
        "box_width": box_w,
        "margin_l": left,
        "margin_r": right,
        "margin_v": 0,
    }


def _ass_position_override(layout: dict) -> str:
    return f"{{\\an5\\pos({layout['x_px']},{layout['y_px']})}}"


def _ass_header(w: int, h: int, s: dict, layout: dict) -> str:
    secondary = s.get("secondary", s["highlight"])
    return (
        f"[Script Info]\n"
        f"Title: ViriaRevive Subtitles\n"
        f"ScriptType: v4.00+\n"
        f"WrapStyle: 0\n"
        f"ScaledBorderAndShadow: yes\n"
        f"YCbCr Matrix: TV.709\n"
        f"PlayResX: {w}\n"
        f"PlayResY: {h}\n"
        f"\n"
        f"[V4+ Styles]\n"
        f"Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        f"OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        f"ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        f"Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{s['font']},{s['size']},{s['primary']},{secondary},"
        f"{s['outline']},{s['back']},{s['bold']},0,0,0,100,100,0,0,"
        f"{s.get('border_style', 1)},"
        f"{s['border']},{s['shadow']},5,"
        f"{layout['margin_l']},{layout['margin_r']},{layout['margin_v']},1"
    )


def _ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
