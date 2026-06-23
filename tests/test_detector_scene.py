import sys
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import detector  # noqa: E402


class SceneDetectionRoutingTests(unittest.TestCase):
    def test_scene_progress_label_uses_elapsed_video_time(self):
        updates = []

        detector._notify_scene_progress(updates.append, 4320, 9780, "full")
        detector._notify_scene_progress(updates.append, 540, 3600, "sampled")

        self.assertEqual(updates[0], "Detecting scenes: 01:12:00 / 02:43:00")
        self.assertEqual(updates[1], "Sampling scenes: 00:09:00 / 01:00:00")

    def test_pyscenedetect_is_preferred_before_ffmpeg(self):
        density = np.ones(6)
        diagnostics = {
            "status": "ok",
            "engine": "pyscenedetect",
            "timestamp_count": 2,
        }

        with patch.object(detector, "_PYSCENEDETECT_ENABLED", True), patch(
            "detector._try_pyscenedetect_scene_change_density",
            return_value=(density, diagnostics, True),
        ), patch("detector.shutil.which", return_value=None):
            result_density, result_diagnostics = detector._scene_change_density(
                Path("missing_source.mp4"),
                length=5,
                video_duration=60,
                mode="full",
            )

        self.assertTrue(np.array_equal(result_density, density))
        self.assertEqual(result_diagnostics["engine"], "pyscenedetect")
        self.assertEqual(result_diagnostics["status"], "ok")

    def test_pyscenedetect_full_long_video_uses_chunks(self):
        windows = detector._pyscenedetect_scan_windows("full", 7200)

        self.assertGreater(len(windows), 1)
        self.assertEqual(windows[0], (0.0, 600.0))
        self.assertEqual(windows[-1], (6600.0, 600.0))

    def test_pyscenedetect_sampled_video_uses_windows_not_full_scan(self):
        windows = detector._pyscenedetect_scan_windows("sampled", 7200)

        self.assertEqual(len(windows), 10)
        self.assertEqual(windows[0][0], 0.0)
        self.assertLess(windows[0][1], 100.0)
        self.assertGreater(windows[-1][0], 0.0)

    def test_targeted_scene_windows_are_bounded_and_cover_anchors(self):
        scores = np.zeros(9000)
        scores[1200] = 1.0
        scores[4800] = 0.9
        scores[8400] = 0.8

        windows = detector._targeted_scene_windows(
            scores,
            target_count=72,
            clip_duration=30,
            min_gap=15,
            video_duration=9000,
        )

        self.assertLessEqual(len(windows), 56)
        self.assertTrue(any(start <= 1200 <= start + span for start, span in windows))
        self.assertTrue(any(start == 0.0 for start, _ in windows))
        self.assertTrue(all(span <= 60 for _, span in windows))

    def test_candidate_target_count_respects_cap(self):
        self.assertEqual(
            detector._candidate_target_count(37, 8, max_candidates=72, scene_mode="targeted"),
            72,
        )
        self.assertEqual(
            detector._candidate_target_count(90, 8, max_candidates=72, scene_mode="targeted"),
            90,
        )

    def test_ffmpeg_sampled_fallback_still_runs_when_pyscenedetect_fails(self):
        fallback_density = np.zeros(6)
        fallback_diagnostics = {
            "status": "sampled_zero_changes",
            "engine": "ffmpeg",
            "pyscenedetect_attempt": {"status": "pyscenedetect_error"},
        }

        with patch(
            "detector._try_pyscenedetect_scene_change_density",
            return_value=(
                np.zeros(6),
                {"status": "pyscenedetect_error", "fallback_reason": "boom"},
                False,
            ),
        ), patch("detector.shutil.which", return_value="ffmpeg"), patch(
            "detector._sampled_scene_change_density",
            return_value=(fallback_density, fallback_diagnostics),
        ):
            result_density, result_diagnostics = detector._scene_change_density(
                Path("missing_source.mp4"),
                length=5,
                video_duration=7200,
                mode="sampled",
            )

        self.assertTrue(np.array_equal(result_density, fallback_density))
        self.assertEqual(result_diagnostics["engine"], "ffmpeg")
        self.assertEqual(result_diagnostics["status"], "sampled_zero_changes")

    def test_long_videos_are_not_routed_to_in_process_pyscenedetect(self):
        source = Path("missing_source.mp4")
        density = np.zeros(10)
        diagnostics = {
            "status": "unknown",
            "mode": "full",
            "elapsed_seconds": 0.0,
            "pyscenedetect_attempt": {},
        }

        with patch.object(detector, "_PYSCENEDETECT_ENABLED", True):
            result_density, result_diagnostics, success = detector._try_pyscenedetect_scene_change_density(
                source,
                density,
                length=10,
                video_duration=7200,
                mode="full",
                base_diagnostics=diagnostics,
            )

        self.assertFalse(success)
        self.assertTrue(np.array_equal(result_density, density))
        self.assertEqual(result_diagnostics["status"], "pyscenedetect_skipped_timeout_guard")

    def test_pyscenedetect_is_disabled_by_default_for_runtime_safety(self):
        density = np.zeros(10)
        result_density, result_diagnostics, success = detector._try_pyscenedetect_scene_change_density(
            Path("missing_source.mp4"),
            density,
            length=10,
            video_duration=60,
            mode="full",
            base_diagnostics={"mode": "full"},
        )

        self.assertFalse(success)
        self.assertTrue(np.array_equal(result_density, density))
        self.assertEqual(result_diagnostics["status"], "pyscenedetect_disabled_timeout_safety")

    def test_targeted_ffmpeg_fallback_uses_each_window_span(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        diagnostics = {"mode": "targeted", "status": "unknown"}
        with patch("detector._run", side_effect=fake_run):
            density, result_diagnostics = detector._sampled_scene_change_density(
                "ffmpeg",
                Path("source.mp4"),
                np.zeros(200),
                length=199,
                video_duration=200,
                diagnostics=diagnostics,
                target_windows=[(10, 12), (100, 44)],
            )

        t_values = [cmd[cmd.index("-t") + 1] for cmd, _ in calls]
        timeouts = [kwargs["timeout"] for _, kwargs in calls]
        self.assertEqual(t_values, ["12.0", "44.0"])
        self.assertEqual(timeouts, [60, 88])
        self.assertEqual(result_diagnostics["status"], "targeted_zero_changes")
        self.assertTrue(np.array_equal(density, np.zeros(200)))

    def test_analysis_audio_timeout_is_scaled_and_capped(self):
        self.assertEqual(detector._analysis_audio_timeout_seconds(0), 300)
        self.assertEqual(detector._analysis_audio_timeout_seconds(1200), 600)
        self.assertEqual(detector._analysis_audio_timeout_seconds(10000), 3600)

    def test_scene_cache_key_includes_algorithm_version(self):
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.stat") as stat:
                stat.return_value.st_size = 123
                stat.return_value.st_mtime_ns = 456

                key_a = detector._scene_cache_key(Path("source.mp4"), 100, 100.0, "targeted", [(1, 2)])
                with patch.object(detector, "_SCENE_CACHE_ALGORITHM_VERSION", detector._SCENE_CACHE_ALGORITHM_VERSION + 1):
                    key_b = detector._scene_cache_key(Path("source.mp4"), 100, 100.0, "targeted", [(1, 2)])

        self.assertNotEqual(key_a, key_b)


if __name__ == "__main__":
    unittest.main()
