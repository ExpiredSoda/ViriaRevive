import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from subtitler import (  # noqa: E402
    generate_drawtext_vf,
    generate_subtitles,
    get_available_styles,
    normalize_subtitle_placement,
    resolve_subtitle_placement,
)


WORDS = [
    {"text": "right", "start": 0.0, "end": 0.3},
    {"text": "behind", "start": 0.3, "end": 0.7},
    {"text": "me", "start": 0.7, "end": 1.0},
]


class SubtitlePlacementTests(unittest.TestCase):
    def test_normalize_subtitle_placement_clamps_to_safe_percentages(self):
        placement = normalize_subtitle_placement(
            {"x_pct": 999, "y_pct": -10, "width_pct": "bad"}
        )

        self.assertEqual(placement["x_pct"], 90)
        self.assertEqual(placement["y_pct"], 12)
        self.assertEqual(placement["width_pct"], 86)

    def test_generate_subtitles_uses_resolved_ass_position(self):
        placement = {"x_pct": 50, "y_pct": 62, "width_pct": 70}

        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "clip.ass"
            result = generate_subtitles(
                WORDS,
                out,
                video_width=540,
                video_height=960,
                style="tiktok",
                subtitle_placement=placement,
            )

            self.assertEqual(result, out)
            text = out.read_text(encoding="utf-8")

        resolved = resolve_subtitle_placement(540, 960, placement)
        self.assertIn(
            f"\\an5\\pos({resolved['x_px']},{resolved['y_px']})",
            text,
        )
        self.assertIn(
            f",5,{resolved['margin_l']},{resolved['margin_r']},{resolved['margin_v']},1",
            text,
        )
        self.assertNotIn("\\r", text)

    def test_none_style_skips_caption_files_and_drawtext(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "clip.ass"
            out.write_text("stale", encoding="utf-8")

            result = generate_subtitles(WORDS, out, style="none")

            self.assertIsNone(result)
            self.assertFalse(out.exists())

        self.assertEqual(generate_drawtext_vf(WORDS, style="none"), "")

    def test_none_style_is_listed_first_for_ui(self):
        styles = get_available_styles()

        self.assertEqual(styles[0]["id"], "none")
        self.assertEqual(styles[0]["label"], "None")

    def test_drawtext_fallback_uses_same_position_math(self):
        placement = {"x_pct": 40, "y_pct": 30, "width_pct": 60}
        resolved = resolve_subtitle_placement(540, 960, placement)

        vf = generate_drawtext_vf(
            WORDS,
            video_width=540,
            video_height=960,
            style="clean",
            subtitle_placement=placement,
        )

        self.assertIn(f"{resolved['x_px']}-tw/2", vf)
        self.assertIn(f"{resolved['y_px']}-th/2", vf)


if __name__ == "__main__":
    unittest.main()
