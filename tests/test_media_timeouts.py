import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import clipper  # noqa: E402
import subprocess_utils  # noqa: E402


class MediaTimeoutTests(unittest.TestCase):
    def test_ffmpeg_timeout_returns_failed_completed_process(self):
        cmd = ["ffmpeg", "-version"]

        timeout = subprocess.TimeoutExpired(
            cmd,
            12,
            output="partial stdout",
            stderr="ffmpeg diagnostic tail",
        )
        with patch("clipper._run", side_effect=timeout):
            result = clipper._run_ffmpeg(
                cmd,
                phase="Test phase",
                timeout=12,
                capture_output=True,
                text=True,
                errors="replace",
            )

        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.stdout, "partial stdout")
        self.assertIn("ffmpeg diagnostic tail", result.stderr)
        self.assertIn("timed out", result.stderr)

    def test_ffmpeg_timeout_scales_by_duration(self):
        self.assertEqual(clipper._ffmpeg_timeout_seconds(0), 120)
        self.assertEqual(clipper._ffmpeg_timeout_seconds(20, multiplier=10), 200)
        self.assertEqual(clipper._ffmpeg_timeout_seconds(10000), 1800)

    def test_ffmpeg_command_logging_is_concise_by_default(self):
        cmd = ["ffmpeg", "-i", "D:/private/source file.mp4", "A:/out.mp4"]
        with patch.object(clipper, "FFMPEG_VERBOSE_COMMANDS", False), \
             patch.dict("os.environ", {"VIRIA_FFMPEG_VERBOSE_COMMANDS": ""}, clear=False):
            out = StringIO()
            with redirect_stdout(out):
                clipper._log_ffmpeg_command("Crop render", cmd)

        text = out.getvalue()
        self.assertIn("Crop render", text)
        self.assertNotIn("D:/private/source file.mp4", text)
        self.assertNotIn("ffmpeg -i", text)

    def test_ffmpeg_command_logging_can_be_enabled_explicitly(self):
        cmd = ["ffmpeg", "-i", "input.mp4", "out.mp4"]
        with patch.object(clipper, "FFMPEG_VERBOSE_COMMANDS", False), \
             patch.dict("os.environ", {"VIRIA_FFMPEG_VERBOSE_COMMANDS": "1"}, clear=False):
            out = StringIO()
            with redirect_stdout(out):
                clipper._log_ffmpeg_command("Crop render", cmd)

        self.assertIn("ffmpeg -i input.mp4 out.mp4", out.getvalue())

    def test_rename_safe_replaces_existing_output_without_unlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "new.mp4"
            dst = root / "clip.mp4"
            src.write_bytes(b"new")
            dst.write_bytes(b"old")

            with patch.object(Path, "unlink", side_effect=AssertionError("should not pre-delete output")):
                clipper._rename_safe(src, dst)

            self.assertEqual(dst.read_bytes(), b"new")
            self.assertFalse(src.exists())

    def test_rename_safe_failure_preserves_existing_output_and_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "new.mp4"
            dst = root / "clip.mp4"
            src.write_bytes(b"new")
            dst.write_bytes(b"old")

            with patch("clipper.os.replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    clipper._rename_safe(src, dst)

            self.assertEqual(dst.read_bytes(), b"old")
            self.assertEqual(src.read_bytes(), b"new")

    def test_subprocess_timeout_preserves_partial_output(self):
        subprocess_utils.reset_cancel()
        script = (
            "import sys, time; "
            "print('partial stdout', flush=True); "
            "print('partial stderr', file=sys.stderr, flush=True); "
            "time.sleep(2)"
        )

        with self.assertRaises(subprocess.TimeoutExpired) as ctx:
            subprocess_utils.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=0.1,
            )

        self.assertIn("partial stdout", ctx.exception.output)
        self.assertIn("partial stderr", ctx.exception.stderr)

    def test_subprocess_cancel_preserves_partial_output(self):
        subprocess_utils.reset_cancel()
        script = (
            "import sys, time; "
            "print('cancel stdout', flush=True); "
            "print('cancel stderr', file=sys.stderr, flush=True); "
            "time.sleep(2)"
        )
        timer = threading.Timer(0.1, subprocess_utils.request_cancel)
        timer.start()
        try:
            with self.assertRaises(subprocess_utils.CancelledError) as ctx:
                subprocess_utils.run(
                    [sys.executable, "-c", script],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
        finally:
            timer.cancel()
            subprocess_utils.reset_cancel()

        self.assertIn("cancel stdout", ctx.exception.stdout)
        self.assertIn("cancel stderr", ctx.exception.stderr)


if __name__ == "__main__":
    unittest.main()
