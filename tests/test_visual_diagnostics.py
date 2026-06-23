import math
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from visual_diagnostics import _read_frame_at, disabled_visual_diagnostics, score_visual_frames  # noqa: E402


class VisualDiagnosticsTests(unittest.TestCase):
    def _assert_json_safe_numbers(self, payload):
        for key, value in payload.items():
            if isinstance(value, float):
                self.assertTrue(math.isfinite(value), key)
                self.assertGreaterEqual(value, 0.0, key)
                self.assertLessEqual(value, 1.0, key)

    def test_empty_frames_are_explicitly_unavailable(self):
        result = score_visual_frames([])

        self.assertEqual(result["status"], "no_frames")
        self.assertEqual(result["sample_count"], 0)
        self.assertEqual(result["visual_energy"], 0.0)

    def test_black_frames_mark_dark_without_motion(self):
        black = np.zeros((90, 160, 3), dtype=np.uint8)

        result = score_visual_frames([black, black.copy()], sample_times=[1.0, 2.0])

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sample_count"], 2)
        self.assertGreater(result["dark_scene_score"], 0.5)
        self.assertGreater(result["black_frame_ratio"], 0.9)
        self.assertLess(result["visual_energy"], 0.25)
        self._assert_json_safe_numbers(result)

    def test_changed_frames_raise_motion_and_visual_energy(self):
        first = np.full((90, 160, 3), 35, dtype=np.uint8)
        second = np.full((90, 160, 3), 220, dtype=np.uint8)

        static = score_visual_frames([first, first.copy()])
        changed = score_visual_frames([first, second])

        self.assertGreater(changed["motion"], static["motion"])
        self.assertGreater(changed["visual_energy"], static["visual_energy"])
        self._assert_json_safe_numbers(changed)

    def test_red_flash_contributes_to_possible_failure(self):
        red = np.zeros((90, 160, 3), dtype=np.uint8)
        red[:, :, 2] = 220

        result = score_visual_frames([red, red.copy()])

        self.assertGreater(result["red_flash_score"], 0.5)
        self.assertGreater(result["possible_failure_score"], 0.25)
        self.assertIn("red_flash", result["labels"])

    def test_disabled_payload_is_stable(self):
        rows, report = disabled_visual_diagnostics([{"start": 0}, {"start": 30}], status="disabled")

        self.assertEqual(report["status"], "disabled")
        self.assertEqual(report["candidate_count"], 2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], "disabled")

    def test_read_frame_at_reports_timeout(self):
        with patch("visual_diagnostics._run", side_effect=subprocess.TimeoutExpired(["ffmpeg"], 1)):
            ok, frame, status = _read_frame_at(object(), Path("missing.mp4"), 0, timeout=0.001)

        self.assertFalse(ok)
        self.assertIsNone(frame)
        self.assertEqual(status, "timeout")


if __name__ == "__main__":
    unittest.main()
