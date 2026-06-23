import math
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api_bridge import ApiBridge  # noqa: E402
from voice_profile import (  # noqa: E402
    MIN_VOICE_PROFILE_SAMPLES,
    MIN_VOICE_PROFILE_TOTAL_ACTIVE_SECONDS,
    VOICE_PROFILE_FEATURE_COUNT,
    empty_voice_profile,
    extract_voice_features,
    sanitize_voice_profile,
    score_voice_profile,
    update_voice_profile,
    voice_profile_status,
)


def _write_tone(path: Path, hz: float = 220.0, seconds: float = 2.2, rate: int = 16000):
    total = int(seconds * rate)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        frames = bytearray()
        for i in range(total):
            sample = int(12000 * math.sin(2 * math.pi * hz * (i / rate)))
            frames.extend(sample.to_bytes(2, "little", signed=True))
        wav.writeframes(bytes(frames))


class VoiceProfileTests(unittest.TestCase):
    def test_extract_update_and_score_voice_profile_are_bounded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wav_path = Path(temp_dir) / "voice.wav"
            _write_tone(wav_path)

            features = extract_voice_features(wav_path)
            self.assertTrue(features["ok"])
            self.assertEqual(len(features["features"]), VOICE_PROFILE_FEATURE_COUNT)
            self.assertGreater(features["active_seconds"], 1.5)

            profile = update_voice_profile(empty_voice_profile(enabled=True), features["features"], active_seconds=features["active_seconds"])
            self.assertTrue(profile["enabled"])
            self.assertTrue(profile["enrolled"])
            self.assertEqual(profile["sample_count"], 1)
            self.assertNotIn("audio", profile)
            status = voice_profile_status(profile, file_exists=True, size_bytes=123)
            self.assertEqual(status["readiness"], "collecting_samples")
            self.assertFalse(status["can_score"])
            self.assertFalse(status["can_rank"])

            score = score_voice_profile(profile, features["features"])
            self.assertEqual(score["reason"], "needs_more_samples")
            self.assertIsNone(score["confidence"])
            self.assertEqual(score["selection_impact"], "none")
            self.assertTrue(score["diagnostic_only"])

            for _ in range(MIN_VOICE_PROFILE_SAMPLES - 1):
                profile = update_voice_profile(profile, features["features"], active_seconds=MIN_VOICE_PROFILE_TOTAL_ACTIVE_SECONDS)
            status = voice_profile_status(profile, file_exists=True, size_bytes=123)
            self.assertEqual(status["readiness"], "ready")
            self.assertTrue(status["can_score"])
            score = score_voice_profile(profile, features["features"])
            self.assertEqual(score["reason"], "scored")
            self.assertGreaterEqual(score["confidence"], 0.0)
            self.assertLessEqual(score["confidence"], 1.0)

    def test_disabled_and_malformed_profiles_are_safe(self):
        disabled = score_voice_profile(empty_voice_profile(enabled=False), [0.1] * VOICE_PROFILE_FEATURE_COUNT)
        self.assertEqual(disabled["reason"], "disabled")
        self.assertIsNone(disabled["confidence"])

        disabled_profile = update_voice_profile(
            empty_voice_profile(enabled=True),
            [0.2] * VOICE_PROFILE_FEATURE_COUNT,
            active_seconds=2.0,
        )
        disabled_profile["enabled"] = False
        disabled_status = voice_profile_status(disabled_profile, file_exists=True, size_bytes=123)
        self.assertEqual(disabled_status["reason"], "disabled")
        self.assertEqual(disabled_status["readiness"], "off")
        self.assertEqual(disabled_status["status_label"], "Off")
        self.assertFalse(disabled_status["can_score"])

        malformed = sanitize_voice_profile({"enabled": True, "sample_count": 5, "centroid": [0.1]})
        self.assertTrue(malformed["enabled"])
        self.assertFalse(malformed["enrolled"])

        corrupt_feature_version = sanitize_voice_profile({
            "enabled": True,
            "feature_version": "not-a-number",
            "sample_count": 5,
            "total_active_seconds": 9,
            "centroid": [0.1] * VOICE_PROFILE_FEATURE_COUNT,
        })
        self.assertTrue(corrupt_feature_version["enabled"])
        self.assertFalse(corrupt_feature_version["enrolled"])

        status = voice_profile_status(malformed, file_exists=True, size_bytes=123)
        self.assertTrue(status["local_only"])
        self.assertFalse(status["stores_raw_audio"])
        self.assertEqual(status["selection_impact"], "none")
        self.assertEqual(status["readiness"], "needs_samples")
        self.assertEqual(status["status_label"], "Needs samples")
        self.assertEqual(status["next_action"], "build_from_current_clips")
        self.assertFalse(status["can_score"])

    def test_bridge_voice_profile_toggle_persists_local_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_path = Path(temp_dir) / "voice_profile.json"
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._voice_profile_lock = __import__("threading").RLock()
            bridge._voice_profile = empty_voice_profile()

            with patch("api_bridge.VOICE_PROFILE_FILE", profile_path):
                result = bridge.set_voice_profile_enabled(True)
                self.assertTrue(result["voice_profile"]["enabled"])
                self.assertTrue(profile_path.exists())

                status = bridge.get_voice_profile_status()
                self.assertTrue(status["file_exists"])
                self.assertFalse(status["stores_raw_audio"])

                reset = bridge.reset_voice_profile()
                self.assertFalse(reset["voice_profile"]["enabled"])
                self.assertFalse(reset["voice_profile"]["enrolled"])


if __name__ == "__main__":
    unittest.main()
