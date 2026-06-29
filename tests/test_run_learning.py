import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_learning import (  # noqa: E402
    append_event,
    append_run_summary,
    build_clip_deleted_event,
    build_feedback_event,
    build_metadata_event,
    build_montage_feedback_event,
    empty_run_learning,
    redacted_summary,
    sanitize_run_learning,
)


class RunLearningTests(unittest.TestCase):
    def test_run_summary_keeps_compact_counts_not_raw_transcript(self):
        store = empty_run_learning()
        store = append_run_summary(
            store,
            {
                "run_id": "run_1",
                "status": "success",
                "source_id": "src_1",
                "source_stem": "Alan Wake",
                "game_title": "Alan Wake",
                "timing": {"elapsed_seconds": 30, "video_duration_seconds": 120},
                "settings": {
                    "generation_mode": "montage",
                    "processing_depth": "deep",
                    "detection_preference": "auto",
                },
                "candidate_count": 12,
                "accepted_candidate_count": 7,
                "selected_count": 2,
                "rendered_clip_count": 2,
                "selected_clip_ids": ["clip_1"],
                "selected": [
                    {
                        "start": 5,
                        "end": 35,
                        "quality_score": 0.82,
                        "primary_category": "panic",
                        "transcript": "this raw sentence should not be stored",
                        "learning_terms": ["panic chase"],
                    }
                ],
            },
        )

        text = str(store)
        self.assertIn("clip_1", text)
        self.assertIn("panic chase", text)
        self.assertNotIn("this raw sentence should not be stored", text)
        self.assertEqual(store["runs"][0]["candidate_count"], 12)
        self.assertEqual(store["runs"][0]["rendered_clip_count"], 2)
        self.assertEqual(store["runs"][0]["generation_mode"], "montage")
        self.assertFalse(store["runs"][0]["stores_raw_media"])

    def test_feedback_flip_updates_clip_outcome(self):
        store = empty_run_learning()
        identity = {
            "clip_id": "clip_1",
            "source_id": "src_1",
            "source_stem": "source",
            "clip_filename": "clip.mp4",
        }
        store = append_event(
            store,
            build_feedback_event(
                event_id="e1",
                event_type="like",
                active=True,
                timestamp="2026-06-26T00:00:00Z",
                identity=identity,
                reason="good hook",
                clip_snapshot={"learning_terms": ["good hook"], "transcript": "do not store"},
            ),
        )
        store = append_event(
            store,
            build_feedback_event(
                event_id="e2",
                event_type="dislike",
                active=True,
                timestamp="2026-06-26T00:01:00Z",
                identity=identity,
                reason="bad label",
                clip_snapshot={"learning_terms": ["bad label"], "transcript": "do not store either"},
            ),
        )

        outcome = store["clip_outcomes"]["clip_1"]
        self.assertFalse(outcome["like"])
        self.assertTrue(outcome["dislike"])
        self.assertEqual(outcome["last_feedback_type"], "dislike")
        self.assertNotIn("do not store", str(store))

    def test_deleted_and_metadata_events_update_outcome(self):
        store = empty_run_learning()
        store = append_event(
            store,
            build_metadata_event(
                event_id="m1",
                timestamp="2026-06-26T00:00:00Z",
                clip_id="clip_1",
                source_id="src_1",
                clip_filename="clip.mp4",
                title="Great Clip",
                game_title="Alan Wake",
            ),
        )
        store = append_event(
            store,
            build_clip_deleted_event(
                event_id="d1",
                timestamp="2026-06-26T00:02:00Z",
                clip_id="clip_1",
                source_id="src_1",
                clip_filename="clip.mp4",
            ),
        )

        outcome = store["clip_outcomes"]["clip_1"]
        self.assertTrue(outcome["metadata_generated"])
        self.assertTrue(outcome["deleted"])
        self.assertEqual(outcome["last_event_type"], "clip_deleted")

    def test_sanitize_malformed_learning_file(self):
        clean = sanitize_run_learning(
            {
                "schema_version": "old",
                "runs": "bad",
                "events": [{"event_type": "feedback_like", "clip_id": "clip_1"}],
                "clip_outcomes": {"clip_1": {"like": True}},
            }
        )
        self.assertEqual(clean["schema_version"], 1)
        self.assertEqual(len(clean["runs"]), 0)
        self.assertEqual(len(clean["events"]), 1)
        self.assertTrue(clean["clip_outcomes"]["clip_1"]["like"])
        self.assertFalse(redacted_summary(clean)["stores_raw_media"])

    def test_montage_feedback_updates_storyboard_and_beat_outcomes(self):
        store = empty_run_learning()
        storyboard = {
            "storyboard_id": "montage_1",
            "status": "ready",
            "ready": True,
            "source_ids": ["src_1"],
            "source": {"source_stem": "source", "game_title": "Alan Wake"},
            "settings": {"target_duration": 60, "story_shape": "hook_escalate_payoff"},
            "summary": {"beat_count": 1, "planned_duration_seconds": 18, "category_counts": {"high_energy": 1}},
            "beats": [
                {
                    "beat_id": "beat_1",
                    "role": "hook",
                    "clip_id": "clip_1",
                    "source_id": "src_1",
                    "clip_filename": "clip.mp4",
                    "start": 5,
                    "end": 23,
                    "duration": 18,
                    "category": "high_energy",
                    "label": "panic chase",
                    "hook_text": "do not persist this exact sentence",
                    "evidence": ["visual", "local_learning"],
                    "score": 0.82,
                }
            ],
        }
        store = append_event(
            store,
            build_montage_feedback_event(
                event_id="mfb1",
                feedback_type="like",
                active=True,
                timestamp="2026-06-26T00:00:00Z",
                storyboard_id="montage_1",
                reason="good sequence",
                storyboard_snapshot=storyboard,
            ),
        )
        store = append_event(
            store,
            build_montage_feedback_event(
                event_id="mfb2",
                feedback_type="dislike",
                active=True,
                timestamp="2026-06-26T00:01:00Z",
                storyboard_id="montage_1",
                reason="wrong beat",
                storyboard_snapshot=storyboard,
                beat_snapshot=storyboard["beats"][0],
            ),
        )

        outcome = store["montage_outcomes"]["montage_1"]
        beat = outcome["beat_outcomes"]["beat_1"]
        summary = redacted_summary(store)
        self.assertTrue(outcome["like"])
        self.assertTrue(beat["dislike"])
        self.assertEqual(beat["role"], "hook")
        self.assertIn("panic chase", str(store))
        self.assertNotIn("do not persist this exact sentence", str(store))
        self.assertEqual(summary["montage_feedback_event_count"], 2)
        self.assertEqual(summary["montage_beat_outcome_count"], 1)
        self.assertFalse(summary["stores_raw_media"])


if __name__ == "__main__":
    unittest.main()
