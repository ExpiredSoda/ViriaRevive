import json
import sys
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import api_bridge  # noqa: E402
from api_bridge import ApiBridge  # noqa: E402
from candidate_ranker import LEARNED_SELECTION_MAX_ADJUSTMENT, VOICE_PROFILE_SELECTION_MAX_ADJUSTMENT  # noqa: E402
from voice_profile import (  # noqa: E402
    MIN_VOICE_PROFILE_SAMPLES,
    MIN_VOICE_PROFILE_TOTAL_ACTIVE_SECONDS,
    VOICE_PROFILE_FEATURE_COUNT,
    empty_voice_profile,
    update_voice_profile,
)


class DataPrivacySummaryTests(unittest.TestCase):
    def _bridge_with_personalization(self, personalization):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._personalization_lock = threading.RLock()
        bridge._personalization = personalization
        bridge._voice_profile_lock = threading.RLock()
        bridge._voice_profile = empty_voice_profile()
        bridge._processing_history_lock = threading.RLock()
        bridge._processing_history = {"schema_version": 1, "runs": []}
        bridge._user_settings = {}
        return bridge

    def test_learning_status_is_exposed_in_data_privacy_summary(self):
        bridge = self._bridge_with_personalization(
            {
                "schema_version": 1,
                "events": [
                    {
                        "event_type": "like",
                        "active": True,
                        "clip_id": "clip_1",
                        "timestamp": "2026-06-21T12:00:00Z",
                    }
                ],
                "clips": {
                    "clip_1": {
                        "clip_id": "clip_1",
                        "latest": {
                            "like": True,
                            "dislike": False,
                            "favorite": False,
                            "timestamp": "2026-06-21T12:05:00Z",
                        },
                        "updated_at": "2026-06-21T12:05:00Z",
                        "clip_snapshot": {"transcript": "right behind me run please"},
                    }
                },
            }
        )

        summary = bridge.get_data_privacy_summary()
        learning = summary["learning"]

        self.assertEqual(summary["personalization"]["event_count"], 1)
        self.assertEqual(summary["personalization"]["clip_count"], 1)
        self.assertEqual(summary["personalization"]["last_feedback_at"], "2026-06-21T12:05:00Z")
        self.assertEqual(summary["personalization"]["learning"], learning)
        self.assertTrue(learning["enabled"])
        self.assertEqual(learning["active_feedback_signals"], 1)
        self.assertEqual(learning["learned_cap"], LEARNED_SELECTION_MAX_ADJUSTMENT)
        self.assertEqual(learning["last_feedback_at"], "2026-06-21T12:05:00Z")
        self.assertIn("voice_profile", summary)
        self.assertFalse(summary["voice_profile"]["stores_raw_audio"])
        self.assertEqual(summary["voice_profile"]["selection_impact"], "none")
        self.assertIn("processing_history", summary)
        self.assertIn("local_analysis", summary)

    def test_local_analysis_and_processing_history_are_exposed(self):
        bridge = self._bridge_with_personalization({"schema_version": 1, "events": [], "clips": {}})
        bridge._user_settings = {
            "processing_depth": "balanced",
            "visual_diagnostics": True,
            "ai_moment_classification": True,
            "moment_category_ranking": True,
        }
        bridge._processing_history = {
            "schema_version": 1,
            "runs": [
                {
                    "run_id": "run_1",
                    "processing_depth": "balanced",
                    "elapsed_seconds": 120.0,
                    "estimated_total_seconds": 100.0,
                    "estimate_error_seconds": 20.0,
                    "video_duration_seconds": 600.0,
                    "finished_at_utc": "2026-06-22T18:00:00Z",
                }
            ],
        }

        summary = bridge.get_data_privacy_summary()
        analysis = summary["local_analysis"]
        history = summary["processing_history"]

        self.assertTrue(analysis["visual_analysis_enabled"])
        self.assertTrue(analysis["ai_moment_labels_enabled"])
        self.assertTrue(analysis["moment_label_ranking_enabled"])
        self.assertEqual(analysis["selection_caps"]["moment_label_ranking"], 0.02)
        self.assertEqual(history["run_count"], 1)
        self.assertEqual(history["last_run"]["estimate_error_seconds"], 20.0)

    def test_balanced_depth_reports_moment_label_ranking_depth_override(self):
        bridge = self._bridge_with_personalization({"schema_version": 1, "events": [], "clips": {}})
        bridge._user_settings = {
            "processing_depth": "balanced",
            "visual_diagnostics": True,
            "ai_moment_classification": False,
            "moment_category_ranking": False,
            "voice_profile_ranking": False,
        }

        analysis = bridge.get_data_privacy_summary()["local_analysis"]

        self.assertFalse(analysis["moment_label_ranking_enabled"])
        self.assertTrue(analysis["depth_preset_controls"]["moment_label_ranking"])
        self.assertIsNone(analysis["depth_preset_controls"]["ai_moment_labels"])
        self.assertIsNone(analysis["depth_preset_controls"]["voice_profile_ranking"])

    def test_fast_depth_reports_heavy_features_as_intentionally_inactive(self):
        bridge = self._bridge_with_personalization({"schema_version": 1, "events": [], "clips": {}})
        profile = update_voice_profile(
            empty_voice_profile(enabled=True),
            [0.2] * VOICE_PROFILE_FEATURE_COUNT,
            active_seconds=4.0,
        )
        bridge._voice_profile = profile
        bridge._user_settings = {
            "processing_depth": "fast",
            "visual_diagnostics": True,
            "ai_moment_classification": True,
            "moment_category_ranking": True,
            "voice_profile_ranking": True,
        }

        analysis = bridge.get_data_privacy_summary()["local_analysis"]
        statuses = analysis["feature_statuses"]

        self.assertEqual(analysis["processing_depth"], "fast")
        self.assertTrue(analysis["visual_analysis_enabled"])
        self.assertTrue(analysis["ai_moment_labels_enabled"])
        self.assertTrue(analysis["moment_label_ranking_enabled"])
        self.assertFalse(analysis["depth_preset_controls"]["visual_analysis"])
        self.assertFalse(analysis["depth_preset_controls"]["ai_moment_labels"])
        self.assertFalse(analysis["depth_preset_controls"]["moment_label_ranking"])
        self.assertFalse(analysis["depth_preset_controls"]["voice_profile_ranking"])
        self.assertEqual(statuses["scene_detection"]["label"], "Inactive in Fast")
        self.assertEqual(statuses["scene_detection"]["inactive_reason"], "fast_depth")
        for key in ("visual_analysis", "ai_moment_labels", "moment_label_ranking", "voice_profile_ranking"):
            self.assertTrue(statuses[key]["requested"])
            self.assertFalse(statuses[key]["effective"])
            self.assertEqual(statuses[key]["label"], "Inactive in Fast")
            self.assertEqual(statuses[key]["inactive_reason"], "fast_depth")

    def test_processing_history_backfills_from_run_debug_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subtitles = root / "subtitles"
            subtitles.mkdir()
            history_file = root / "processing_history.json"
            (subtitles / "example_run_debug.json").write_text(
                json.dumps(
                    {
                        "run_id": "run_from_debug",
                        "video_duration": 600.0,
                        "candidate_count": 12,
                        "selected_count": 3,
                        "settings": {
                            "processing_depth": "balanced",
                            "detection_preference": "auto",
                            "candidate_multiplier": 5,
                        },
                        "scene_detection": {"elapsed_seconds": 20.0},
                        "visual_diagnostics": {"elapsed_seconds": 5.0},
                        "final_clips": [{}, {}, {}],
                    }
                ),
                encoding="utf-8",
            )
            bridge = self._bridge_with_personalization({"schema_version": 1, "events": [], "clips": {}})

            with patch.object(api_bridge, "PROCESSING_HISTORY_FILE", history_file), \
                 patch.object(api_bridge, "SUBTITLES_DIR", subtitles):
                bridge._load_processing_history()

        runs = bridge._processing_history["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["run_id"], "run_from_debug")
        self.assertEqual(runs[0]["elapsed_seconds"], 25.0)
        self.assertEqual(runs[0]["processing_depth"], "balanced")
        self.assertEqual(runs[0]["rendered_clip_count"], 3)

    def test_processing_history_backfill_skips_malformed_debug_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subtitles = root / "subtitles"
            subtitles.mkdir()
            history_file = root / "processing_history.json"
            (subtitles / "bad_run_debug.json").write_text(
                json.dumps(
                    {
                        "run_id": "bad_row",
                        "timing": {"elapsed_seconds": 10},
                        "settings": {"candidate_multiplier": {"not": "numeric"}},
                        "candidate_count": {"bad": "value"},
                        "final_clips": {"not": "a-list"},
                        "rendered_clip_count": "7",
                    }
                ),
                encoding="utf-8",
            )
            (subtitles / "good_run_debug.json").write_text(
                json.dumps(
                    {
                        "run_id": "good_row",
                        "timing": {"elapsed_seconds": 20},
                        "settings": {"candidate_multiplier": "5"},
                        "candidate_count": "12",
                        "selected_count": "2",
                        "final_clips": [{}],
                    }
                ),
                encoding="utf-8",
            )
            bridge = self._bridge_with_personalization({"schema_version": 1, "events": [], "clips": {}})

            with patch.object(api_bridge, "PROCESSING_HISTORY_FILE", history_file), \
                 patch.object(api_bridge, "SUBTITLES_DIR", subtitles):
                bridge._load_processing_history()

        runs = bridge._processing_history["runs"]
        self.assertEqual([row["run_id"] for row in runs], ["bad_row", "good_row"])
        self.assertEqual(runs[0]["candidate_multiplier"], 0)
        self.assertEqual(runs[0]["candidate_count"], 0)
        self.assertEqual(runs[0]["rendered_clip_count"], 7)
        self.assertEqual(runs[1]["candidate_multiplier"], 5)
        self.assertEqual(runs[1]["candidate_count"], 12)

    def test_voice_profile_status_is_exposed_in_data_privacy_summary(self):
        bridge = self._bridge_with_personalization({"schema_version": 1, "events": [], "clips": {}})
        bridge._voice_profile = update_voice_profile(
            empty_voice_profile(enabled=True),
            [0.2] * 8,
            active_seconds=3.5,
        )

        summary = bridge.get_data_privacy_summary()
        voice = summary["voice_profile"]

        self.assertTrue(voice["enabled"])
        self.assertTrue(voice["enrolled"])
        self.assertEqual(voice["sample_count"], 1)
        self.assertEqual(voice["total_active_seconds"], 3.5)
        self.assertFalse(voice["stores_raw_audio"])
        self.assertFalse(voice["ranking_enabled"])
        self.assertFalse(voice["can_score"])
        self.assertFalse(voice["can_rank"])
        self.assertEqual(voice["influence_state"], "needs_more_samples")
        self.assertEqual(voice["blocking_reason"], "needs_more_samples")
        self.assertIn("Add more clear creator-commentary samples", voice["guidance"])
        self.assertEqual(voice["selection_impact"], "none")

    def test_voice_profile_enabled_without_samples_is_not_influencing(self):
        bridge = self._bridge_with_personalization({"schema_version": 1, "events": [], "clips": {}})
        bridge._voice_profile = empty_voice_profile(enabled=True)
        bridge._user_settings = {"voice_profile_ranking": True}

        voice = bridge.get_data_privacy_summary()["voice_profile"]

        self.assertTrue(voice["enabled"])
        self.assertFalse(voice["enrolled"])
        self.assertTrue(voice["ranking_enabled"])
        self.assertFalse(voice["ranking_active"])
        self.assertEqual(voice["readiness"], "needs_samples")
        self.assertEqual(voice["status_label"], "Needs samples")
        self.assertEqual(voice["influence_state"], "needs_samples")
        self.assertEqual(voice["blocking_reason"], "not_enrolled")
        self.assertEqual(voice["selection_impact"], "none")
        self.assertIn("Build from current clips", voice["guidance"])

    def test_voice_profile_ranking_status_is_explicitly_opt_in(self):
        bridge = self._bridge_with_personalization({"schema_version": 1, "events": [], "clips": {}})
        profile = empty_voice_profile(enabled=True)
        for _ in range(MIN_VOICE_PROFILE_SAMPLES):
            profile = update_voice_profile(
                profile,
                [0.2] * 8,
                active_seconds=MIN_VOICE_PROFILE_TOTAL_ACTIVE_SECONDS / MIN_VOICE_PROFILE_SAMPLES,
            )
        bridge._voice_profile = profile
        bridge._user_settings = {"voice_profile_ranking": True}

        voice = bridge.get_data_privacy_summary()["voice_profile"]

        self.assertTrue(voice["ranking_enabled"])
        self.assertTrue(voice["ranking_active"])
        self.assertEqual(voice["selection_impact"], "capped_rank_adjustment")
        self.assertEqual(voice["ranking_cap"], VOICE_PROFILE_SELECTION_MAX_ADJUSTMENT)
        self.assertEqual(voice["influence_state"], "influencing")
        self.assertEqual(voice["status_label"], "Influencing")
        self.assertFalse(voice["stores_raw_audio"])

    def test_positive_feedback_can_nudge_voice_profile_build_without_auto_enrolling(self):
        bridge = self._bridge_with_personalization({"schema_version": 1, "events": [], "clips": {}})
        bridge._save_personalization = lambda: None
        with tempfile.TemporaryDirectory() as temp_dir:
            clip_path = Path(temp_dir) / "clip.mp4"
            clip_path.write_bytes(b"fake")
            bridge._results = [clip_path]
            bridge._moments = [
                {
                    "start": 0,
                    "end": 30,
                    "duration": 30,
                    "transcript": "wait what are we doing i think he is right behind me please run",
                    "analysis_word_count": 14,
                    "primary_category": "high_energy",
                    "moment_categories": {"primary": "high_energy", "confidence": 0.9},
                    "commentary_guard": {
                        "policy": "creator",
                        "summary": {
                            "primary_label": "creator_commentary",
                            "creator_word_ratio": 0.9,
                            "game_narration_word_ratio": 0.0,
                            "confidence": 0.9,
                        },
                        "application": {
                            "policy": "creator",
                            "output_changed": False,
                            "fallback_used": False,
                            "removed_word_count": 0,
                        },
                    },
                }
            ]

            with patch.object(api_bridge, "VOICE_PROFILE_FILE", Path(temp_dir) / "voice_profile.json"):
                result = bridge.record_feedback({"index": 0, "event_type": "like", "active": True})

        self.assertTrue(result["ok"])
        self.assertTrue(result["voice_profile_nudge"]["show"])
        self.assertEqual(result["voice_profile_nudge"]["next_action"], "open_voice_profile_settings")
        self.assertFalse(bridge._voice_profile["enrolled"])

    def test_redacted_export_omits_transcript_snapshots(self):
        payload = ApiBridge._redact_personalization_export(
            {
                "schema_version": 1,
                "events": [
                    {
                        "event_type": "like",
                        "clip_id": "clip_1",
                        "source_id": "source_1",
                        "source_stem": "Private Source",
                        "clip_filename": "private_clip.mp4",
                        "reason": "my private note",
                        "reasons": {"like": "my private like note"},
                        "timestamp": "2026-06-21T12:00:00Z",
                        "learning_terms": ["private words", "right behind"],
                        "clip_snapshot": {
                            "transcript": "private words",
                            "word_count": 2,
                            "learning_terms": ["private words", "right behind"],
                            "voice_profile": {"centroid": [0.1] * 8, "sample_count": 3},
                            "commentary_guard": {
                                "segments": [
                                    {"text": "private guard words", "label": "creator_commentary"}
                                ]
                            },
                        },
                    }
                ],
                "clips": {
                    "clip_1": {
                        "clip_id": "clip_1",
                        "source_id": "source_1",
                        "source_stem": "Private Source",
                        "clip_filename": "private_clip.mp4",
                        "updated_at": "2026-06-21T12:00:00Z",
                        "latest": {
                            "reason": "still private",
                            "reasons": {"like": "still private like", "favorite": "still private fav"},
                            "timestamp": "2026-06-21T12:00:00Z",
                        },
                        "learning_terms": ["also private", "game narration"],
                        "clip_snapshot": {
                            "transcript": "also private",
                            "quality_score": 0.8,
                            "learning_terms": ["also private", "game narration"],
                            "voice_profile": {"confidence": 0.8, "centroid": [0.2] * 8},
                            "commentary_guard": {
                                "segments": [
                                    {"text": "also private guard words", "text_preview": "also private", "label": "game_narration"}
                                ]
                            },
                        },
                    }
                },
            }
        )

        self.assertTrue(payload["export_redacted"])
        event_snapshot = payload["events"][0]["clip_snapshot"]
        clip_entry = next(iter(payload["clips"].values()))
        clip_snapshot = clip_entry["clip_snapshot"]
        self.assertNotIn("transcript", event_snapshot)
        self.assertNotIn("transcript", clip_snapshot)
        self.assertNotIn("learning_terms", payload["events"][0])
        self.assertNotIn("learning_terms", event_snapshot)
        self.assertNotIn("learning_terms", clip_entry)
        self.assertNotIn("learning_terms", clip_snapshot)
        self.assertTrue(payload["events"][0]["learning_terms_redacted"])
        self.assertEqual(payload["events"][0]["learning_terms_count"], 2)
        self.assertTrue(event_snapshot["learning_terms_redacted"])
        self.assertEqual(event_snapshot["learning_terms_count"], 2)
        self.assertTrue(clip_entry["learning_terms_redacted"])
        self.assertEqual(clip_entry["learning_terms_count"], 2)
        self.assertTrue(clip_snapshot["learning_terms_redacted"])
        self.assertEqual(clip_snapshot["learning_terms_count"], 2)
        self.assertNotIn("voice_profile", event_snapshot)
        self.assertNotIn("voice_profile", clip_snapshot)
        self.assertTrue(event_snapshot["transcript_redacted"])
        self.assertTrue(clip_snapshot["transcript_redacted"])
        self.assertTrue(event_snapshot["voice_profile_redacted"])
        self.assertTrue(clip_snapshot["voice_profile_redacted"])
        event_segment = event_snapshot["commentary_guard"]["segments"][0]
        clip_segment = clip_snapshot["commentary_guard"]["segments"][0]
        self.assertNotIn("text", event_segment)
        self.assertNotIn("text", clip_segment)
        self.assertNotIn("text_preview", clip_segment)
        self.assertTrue(event_segment["text_redacted"])
        self.assertTrue(clip_segment["text_redacted"])
        self.assertEqual(event_snapshot["word_count"], 2)
        self.assertEqual(clip_snapshot["quality_score"], 0.8)
        self.assertNotIn("clip_1", payload["clips"])
        self.assertNotIn("clip_id", payload["events"][0])
        self.assertNotIn("source_id", payload["events"][0])
        self.assertNotIn("source_stem", payload["events"][0])
        self.assertNotIn("clip_filename", payload["events"][0])
        self.assertNotIn("reason", payload["events"][0])
        self.assertNotIn("reasons", payload["events"][0])
        self.assertNotIn("timestamp", payload["events"][0])
        self.assertTrue(payload["events"][0]["clip_id_hash"].startswith("sha256:"))
        self.assertTrue(payload["events"][0]["reason_redacted"])
        self.assertTrue(payload["events"][0]["reasons_redacted"])
        self.assertEqual(payload["events"][0]["reasons_count"], 1)
        self.assertNotIn("reason", clip_entry["latest"])
        self.assertNotIn("reasons", clip_entry["latest"])
        self.assertTrue(clip_entry["latest"]["reasons_redacted"])
        self.assertEqual(clip_entry["latest"]["reasons_count"], 2)
        self.assertNotIn("timestamp", clip_entry["latest"])


if __name__ == "__main__":
    unittest.main()
