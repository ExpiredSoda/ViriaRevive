import sys
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from candidate_ranker import (  # noqa: E402
    LEARNED_SELECTION_MAX_ADJUSTMENT,
    apply_learned_scoring,
    build_learning_status,
    build_shadow_scoring_report,
    select_best_candidates,
    write_debug_report,
)


def _evaluation(candidate_rank, quality_score, transcript, start=0):
    return {
        "accepted": True,
        "quality_score": quality_score,
        "candidate": {
            "candidate_rank": candidate_rank,
            "candidate_kind": "primary",
        },
        "moment": {
            "start": start,
            "end": start + 30,
            "duration": 30,
            "transcript": transcript,
            "ranker": {},
        },
        "transcript": transcript,
        "word_count": max(6, len(transcript.split())),
    }


def _personalization(latest, transcript="panic chase right behind me run please", events=None):
    return {
        "schema_version": 1,
        "events": events or [],
        "clips": {
            "clip_1": {
                "clip_id": "clip_1",
                "source_id": "source_1",
                "source_stem": "source",
                "clip_filename": "clip.mp4",
                "latest": latest,
                "clip_snapshot": {"transcript": transcript},
                "event_count": 1,
            }
        },
    }


class FeedbackReconciliationTests(unittest.TestCase):
    def test_learning_status_empty_personalization_is_idle(self):
        status = build_learning_status({"schema_version": 1, "events": [], "clips": {}})

        self.assertFalse(status["enabled"])
        self.assertEqual(status["active_feedback_signals"], 0)
        self.assertEqual(status["positive_feedback_signals"], 0)
        self.assertEqual(status["negative_feedback_signals"], 0)
        self.assertEqual(status["learned_cap"], LEARNED_SELECTION_MAX_ADJUSTMENT)

    def test_learning_status_uses_latest_active_feedback(self):
        status = build_learning_status(
            _personalization(
                {
                    "like": True,
                    "dislike": False,
                    "favorite": True,
                    "reason": "panic chase right behind run please",
                }
            )
        )

        self.assertTrue(status["enabled"])
        self.assertEqual(status["active_feedback_signals"], 2)
        self.assertEqual(status["positive_feedback_signals"], 2)
        self.assertEqual(status["negative_feedback_signals"], 0)
        self.assertEqual(status["favorite_signals"], 1)

    def test_learning_status_ignores_removed_stale_event(self):
        stale_event = {
            "event_type": "like",
            "active": True,
            "clip_id": "clip_1",
            "reason": "panic chase right behind run please",
            "clip_snapshot": {"transcript": "panic chase right behind me run please"},
        }
        status = build_learning_status(
            _personalization(
                {
                    "like": False,
                    "dislike": False,
                    "favorite": False,
                    "reason": "",
                },
                events=[stale_event],
            )
        )

        self.assertFalse(status["enabled"])
        self.assertEqual(status["active_feedback_signals"], 0)

    def test_positive_learning_is_capped(self):
        evaluation = _evaluation(1, 0.5, "panic chase right behind me run please")

        apply_learned_scoring(
            [evaluation],
            _personalization(
                {
                    "like": True,
                    "dislike": False,
                    "favorite": True,
                    "reason": "panic chase right behind run please",
                }
            ),
        )

        shadow = evaluation["shadow_scoring"]
        self.assertAlmostEqual(shadow["learned_adjustment"], LEARNED_SELECTION_MAX_ADJUSTMENT)
        self.assertAlmostEqual(evaluation["learned_quality_score"], 0.5 + LEARNED_SELECTION_MAX_ADJUSTMENT)

    def test_removed_feedback_no_longer_counts(self):
        old_active_like = {
            "event_type": "like",
            "active": True,
            "clip_id": "clip_1",
            "like": True,
            "dislike": False,
            "favorite": False,
            "reason": "panic chase right behind run please",
            "clip_snapshot": {"transcript": "panic chase right behind me run please"},
        }
        evaluation = _evaluation(1, 0.5, "panic chase right behind me run please")

        apply_learned_scoring(
            [evaluation],
            _personalization(
                {
                    "like": False,
                    "dislike": False,
                    "favorite": False,
                    "reason": "",
                },
                events=[old_active_like],
            ),
        )

        shadow = evaluation["shadow_scoring"]
        self.assertFalse(shadow["learned_selection_enabled"])
        self.assertEqual(shadow["learned_adjustment"], 0.0)
        self.assertEqual(evaluation["learned_quality_score"], 0.5)

    def test_like_to_dislike_flip_becomes_negative_signal(self):
        old_active_like = {
            "event_type": "like",
            "active": True,
            "clip_id": "clip_1",
            "like": True,
            "dislike": False,
            "favorite": False,
            "reason": "panic chase right behind run please",
            "clip_snapshot": {"transcript": "panic chase right behind me run please"},
        }
        evaluation = _evaluation(1, 0.5, "panic chase right behind me run please")

        apply_learned_scoring(
            [evaluation],
            _personalization(
                {
                    "like": False,
                    "dislike": True,
                    "favorite": False,
                    "reason": "not this kind of clip",
                },
                events=[old_active_like],
            ),
        )

        shadow = evaluation["shadow_scoring"]
        self.assertLess(shadow["learned_adjustment"], 0.0)
        self.assertLess(evaluation["learned_quality_score"], 0.5)
        self.assertEqual(shadow["signals"]["positive_points"], 0)
        self.assertGreater(shadow["signals"]["negative_points"], 0)

    def test_learned_score_can_change_selection_report(self):
        plain = _evaluation(1, 0.60, "walking around checking this room", start=0)
        preferred = _evaluation(2, 0.57, "panic chase right behind me run please", start=45)
        evaluations = [plain, preferred]
        personalization = _personalization(
            {
                "like": True,
                "dislike": False,
                "favorite": True,
                "reason": "panic chase right behind run please",
            }
        )

        apply_learned_scoring(evaluations, personalization)
        selected = select_best_candidates(
            evaluations,
            1,
            min_gap=8,
            score_key="learned_quality_score",
        )
        report = build_shadow_scoring_report(evaluations, selected, personalization, max_count=1, min_gap=8)

        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 2)
        self.assertEqual(selected[0]["selection_quality_score"], 0.57)
        self.assertEqual(report["mode"], "learned_blend")
        self.assertTrue(report["output_changed"])
        self.assertEqual(report["top_changes"][0]["selection_delta"], "added_by_learning")

    def test_debug_reports_keep_stage_and_learning_fields(self):
        plain = _evaluation(1, 0.60, "walking around checking this room", start=0)
        preferred = _evaluation(2, 0.57, "panic chase right behind me run please", start=45)
        evaluations = [plain, preferred]
        personalization = _personalization(
            {
                "like": True,
                "dislike": False,
                "favorite": True,
                "reason": "panic chase right behind run please",
            }
        )

        apply_learned_scoring(evaluations, personalization)
        selected = select_best_candidates(
            evaluations,
            1,
            min_gap=8,
            score_key="learned_quality_score",
        )
        report = build_shadow_scoring_report(evaluations, selected, personalization, max_count=1, min_gap=8)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            candidate_debug = temp_path / "source_candidate_debug.json"
            run_debug = temp_path / "source_run_debug.json"

            write_debug_report(
                candidate_debug,
                temp_path / "source.mp4",
                [item["candidate"] for item in evaluations],
                evaluations,
                selected,
                shadow_scoring=report,
            )
            candidate_payload = json.loads(candidate_debug.read_text(encoding="utf-8"))

            rows = {
                row["candidate"]["candidate_rank"]: row
                for row in candidate_payload["candidates"]
            }
            preferred_row = rows[2]

            self.assertEqual(candidate_payload["debug_stage"], "candidate_pre_render")
            self.assertFalse(candidate_payload["final_render_metadata_included"])
            self.assertEqual(candidate_payload["final_clips"], [])
            self.assertIn("base_quality_score", preferred_row)
            self.assertIn("learned_adjustment", preferred_row)
            self.assertIn("learned_score", preferred_row)
            self.assertIn("learned_quality_score", preferred_row)
            self.assertIn("rank_delta", preferred_row)
            self.assertEqual(preferred_row["selection_delta"], "added_by_learning")
            self.assertEqual(
                candidate_payload["shadow_scoring"]["selection_delta_counts"]["added_by_learning"],
                1,
            )

            write_debug_report(
                run_debug,
                temp_path / "source.mp4",
                [item["candidate"] for item in evaluations],
                evaluations,
                selected,
                final_clips=[{"index": 1, "path": "source_viral1.mp4"}],
                shadow_scoring=report,
            )
            run_payload = json.loads(run_debug.read_text(encoding="utf-8"))

            self.assertEqual(run_payload["debug_stage"], "run_post_render")
            self.assertTrue(run_payload["final_render_metadata_included"])
            self.assertEqual(run_payload["final_clips"][0]["path"], "source_viral1.mp4")


if __name__ == "__main__":
    unittest.main()
