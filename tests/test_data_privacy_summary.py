import sys
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api_bridge import ApiBridge  # noqa: E402
from candidate_ranker import LEARNED_SELECTION_MAX_ADJUSTMENT  # noqa: E402


class DataPrivacySummaryTests(unittest.TestCase):
    def _bridge_with_personalization(self, personalization):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._personalization_lock = threading.RLock()
        bridge._personalization = personalization
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
                        "timestamp": "2026-06-21T12:00:00Z",
                        "clip_snapshot": {"transcript": "private words", "word_count": 2},
                    }
                ],
                "clips": {
                    "clip_1": {
                        "clip_id": "clip_1",
                        "source_id": "source_1",
                        "source_stem": "Private Source",
                        "clip_filename": "private_clip.mp4",
                        "updated_at": "2026-06-21T12:00:00Z",
                        "latest": {"reason": "still private", "timestamp": "2026-06-21T12:00:00Z"},
                        "clip_snapshot": {"transcript": "also private", "quality_score": 0.8},
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
        self.assertTrue(event_snapshot["transcript_redacted"])
        self.assertTrue(clip_snapshot["transcript_redacted"])
        self.assertEqual(event_snapshot["word_count"], 2)
        self.assertEqual(clip_snapshot["quality_score"], 0.8)
        self.assertNotIn("clip_1", payload["clips"])
        self.assertNotIn("clip_id", payload["events"][0])
        self.assertNotIn("source_id", payload["events"][0])
        self.assertNotIn("source_stem", payload["events"][0])
        self.assertNotIn("clip_filename", payload["events"][0])
        self.assertNotIn("reason", payload["events"][0])
        self.assertNotIn("timestamp", payload["events"][0])
        self.assertTrue(payload["events"][0]["clip_id_hash"].startswith("sha256:"))
        self.assertTrue(payload["events"][0]["reason_redacted"])
        self.assertNotIn("reason", clip_entry["latest"])
        self.assertNotIn("timestamp", clip_entry["latest"])


if __name__ == "__main__":
    unittest.main()
