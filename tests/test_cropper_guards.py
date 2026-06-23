import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cropper  # noqa: E402


class CropperGuardTests(unittest.TestCase):
    def test_detect_all_persons_returns_tuple_when_detectors_unavailable(self):
        with patch("cropper._get_yolo_model", return_value=None), \
             patch("cropper._create_yunet_detector", return_value=None), \
             patch("cropper._load_cascades", return_value=[]):
            result = cropper._detect_all_persons(Path("missing.mp4"), 0, 10, 1920, 1080, 4)

        self.assertEqual(result, ([], 1.0, 1.0))

    def test_cropper_read_frame_at_records_timeout(self):
        with patch("cropper._run", side_effect=subprocess.TimeoutExpired(["ffmpeg"], 1)):
            ok, frame = cropper._read_frame_at(object(), Path("missing.mp4"), 0, timeout=0.001)

        self.assertFalse(ok)
        self.assertIsNone(frame)
        self.assertEqual(getattr(cropper._read_frame_at, "last_status", ""), "timeout")

    def test_crop_debug_frame_is_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source video.mp4"
            with patch.object(cropper, "CROP_DEBUG_FRAMES", False), \
                 patch.dict("os.environ", {"VIRIA_CROP_DEBUG_FRAMES": ""}, clear=False):
                result = cropper._save_debug_frame(
                    object(),
                    [(10, 10, 100, 0.9, 30)],
                    1920,
                    1080,
                    1.0,
                    1.0,
                    source,
                )

            self.assertIsNone(result)
            self.assertFalse((Path(temp_dir) / "crop_debug.jpg").exists())

    def test_crop_debug_frame_uses_app_cache_when_enabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            written = []

            def fake_imwrite(path, _debug_frame):
                written.append(Path(path))
                Path(path).write_bytes(b"debug")
                return True

            fake_cv2 = SimpleNamespace(
                FONT_HERSHEY_SIMPLEX=0,
                rectangle=lambda *args, **kwargs: None,
                circle=lambda *args, **kwargs: None,
                putText=lambda *args, **kwargs: None,
                imwrite=fake_imwrite,
            )
            frame = cropper.np.zeros((80, 80, 3), dtype=cropper.np.uint8)
            source = Path("D:/Recordings/Example Source.mp4")

            with patch.object(cropper, "CROP_DEBUG_FRAMES", True), \
                 patch.object(cropper, "ANALYSIS_CACHE_DIR", Path(temp_dir)), \
                 patch.dict(sys.modules, {"cv2": fake_cv2}):
                result = cropper._save_debug_frame(
                    frame,
                    [(35, 20, 100, 0.9, 40)],
                    80,
                    80,
                    1.0,
                    1.0,
                    source,
                )

            self.assertEqual(result, written[0])
            self.assertEqual(result.parent, Path(temp_dir) / "crop_debug")
            self.assertNotEqual(result.name, "crop_debug.jpg")
            self.assertTrue(result.exists())


if __name__ == "__main__":
    unittest.main()
