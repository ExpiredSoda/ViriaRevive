import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main as cli_main  # noqa: E402
from candidate_ranker import (  # noqa: E402
    GAME_CONTEXT_SELECTION_MAX_ADJUSTMENT,
    apply_learned_scoring,
    apply_multi_signal_ai_scoring,
    select_best_candidates,
)


def _evaluation(candidate_rank, quality_score, transcript, start=0, primary="commentary_or_review"):
    categories = {"primary": primary, "confidence": 0.9, "scores": {primary: 0.9}}
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
            "moment_categories": categories,
            "primary_category": primary,
        },
        "moment_categories": categories,
        "primary_category": primary,
        "transcript": transcript,
        "word_count": max(6, len(transcript.split())),
    }


class CliMomentCategoryRankingTests(unittest.TestCase):
    def test_cli_duration_is_clamped_to_shorts_bounds(self):
        self.assertEqual(cli_main._normalize_clip_duration(220), 180)
        self.assertEqual(cli_main._normalize_clip_duration(0), 30)
        self.assertEqual(cli_main._normalize_clip_duration(5), 10)

    def test_cli_flag_defaults_off_when_not_passed(self):
        with patch.object(cli_main.sys, "argv", ["main.py", "https://example.test/video"]):
            with patch.object(cli_main, "process") as process:
                cli_main.main()

        self.assertFalse(process.call_args.kwargs["moment_category_ranking"])

    def test_cli_flag_passes_opt_in_to_process(self):
        with patch.object(cli_main.sys, "argv", ["main.py", "https://example.test/video", "--moment-category-ranking"]):
            with patch.object(cli_main, "process") as process:
                cli_main.main()

        self.assertTrue(process.call_args.kwargs["moment_category_ranking"])

    def _learned_selected(self, evaluations):
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        return select_best_candidates(
            evaluations,
            1,
            min_gap=8,
            score_key="learned_quality_score",
        )

    def test_cli_category_ranking_default_keeps_learned_selection_source(self):
        plain = _evaluation(1, 0.60, "where am i all the way back", start=0, primary="low_value")
        preferred = _evaluation(2, 0.585, "panic chase right behind me run please", start=45, primary="high_energy")
        evaluations = [plain, preferred]
        learned_selected = self._learned_selected(evaluations)

        selected, report, score_source = cli_main._apply_cli_moment_category_ranking(
            evaluations,
            learned_selected,
            enabled=False,
            num_clips=1,
            min_gap=8,
        )

        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 1)
        self.assertEqual(score_source, "learned_quality_score")
        self.assertEqual(selected[0]["selection_score_source"], "learned_quality_score")
        self.assertFalse(report["ranking_enabled"])
        self.assertEqual(report["selection_score_source"], "learned_quality_score")
        self.assertFalse(report["output_changed"])

    def test_cli_category_ranking_opt_in_can_switch_selection_source(self):
        plain = _evaluation(1, 0.60, "where am i all the way back", start=0, primary="low_value")
        preferred = _evaluation(2, 0.585, "panic chase right behind me run please", start=45, primary="high_energy")
        evaluations = [plain, preferred]
        learned_selected = self._learned_selected(evaluations)

        selected, report, score_source = cli_main._apply_cli_moment_category_ranking(
            evaluations,
            learned_selected,
            enabled=True,
            num_clips=1,
            min_gap=8,
        )

        self.assertEqual(learned_selected[0]["candidate"]["candidate_rank"], 1)
        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 2)
        self.assertEqual(score_source, "moment_category_quality_score")
        self.assertEqual(selected[0]["selection_score_source"], "moment_category_quality_score")
        self.assertTrue(report["ranking_enabled"])
        self.assertTrue(report["has_category_scores"])
        self.assertTrue(report["output_changed"])
        self.assertEqual(preferred["moment_category_scoring"]["selection_delta"], "added_by_category")
        self.assertEqual(plain["moment_category_scoring"]["selection_delta"], "dropped_by_category")

    def test_cli_upload_title_context_carries_ranking_audio_and_visual_signals(self):
        item = {
            "transcript": "that thing is right behind me please run",
            "selection_rank_score": 0.72,
            "selection_score_source": "moment_category_quality_score",
            "shadow_scoring": {
                "learned_quality_score": 0.68,
                "learned_adjustment": 0.03,
            },
            "moment_categories": {"primary": "high_energy"},
            "primary_category": "high_energy",
            "moment": {
                "start": 42,
                "end": 72,
                "duration": 30,
                "peak_time": 58,
                "transcript": "that thing is right behind me please run",
                "quality_score": 0.65,
                "moment_categories": {"primary": "high_energy"},
                "primary_category": "high_energy",
                "multimodal_analysis": {
                    "metadata_keywords": ["flashlight chase"],
                    "visual_labels": ["dark hallway"],
                },
                "audio_source": {"selected_stream": 1},
                "ranker": {"hook_points": 2, "first_word_start": 1.1},
            },
        }

        context = cli_main._cli_title_context(
            Path("D:/Recording Video Files/Alan Wake/source.mkv"),
            Path("A:/ViriaRevive/clips/source_viral1.mp4"),
            item,
            1,
            {"status": "ok", "selected_stream": 1},
            [{"ordinal": 0}, {"ordinal": 1}],
        )

        self.assertEqual(context["game_title"], "Alan Wake")
        self.assertEqual(context["transcript"], "that thing is right behind me please run")
        self.assertEqual(context["primary_category"], "high_energy")
        self.assertEqual(context["learned_adjustment"], 0.03)
        self.assertEqual(context["selection_score_source"], "moment_category_quality_score")
        self.assertEqual(context["stream_selection"]["selected_stream"], 1)
        self.assertEqual(context["source_audio_streams"][1]["ordinal"], 1)
        self.assertEqual(context["multimodal_analysis"]["metadata_keywords"], ["flashlight chase"])

    def test_multi_signal_ai_uses_verified_game_context_as_capped_component(self):
        evaluation = _evaluation(
            1,
            0.60,
            "oh my god he is right behind me please run",
            primary="atmosphere_or_visual",
        )
        game_context = {
            "status": "ok",
            "qid": "Q575505",
            "label": "Alan Wake",
            "description": "survival horror video game",
            "facts": {"genres": ["survival horror", "action-adventure"]},
        }
        evaluation["game_context"] = game_context
        evaluation["moment"]["game_context"] = game_context
        evaluation["moment"]["multimodal_analysis"] = {
            "metadata_keywords": ["dark flashlight chase"],
            "visual_labels": ["shadow threat"],
        }

        report = apply_multi_signal_ai_scoring([evaluation], enabled=True, score_key="quality_score")

        scoring = evaluation["multi_signal_ai_scoring"]
        self.assertTrue(report["ranking_enabled"])
        self.assertEqual(report["game_context_max_adjustment"], GAME_CONTEXT_SELECTION_MAX_ADJUSTMENT)
        self.assertGreater(scoring["signals"]["game_context"], 0)
        self.assertGreater(scoring["contributions"]["game_context"], 0)
        self.assertLessEqual(scoring["contributions"]["game_context"], GAME_CONTEXT_SELECTION_MAX_ADJUSTMENT)
        self.assertEqual(scoring["game_context_nudge"]["status"], "scored")
        self.assertIn("horror_survival", scoring["game_context_nudge"]["context_families"])
        self.assertGreater(evaluation["multi_signal_ai_quality_score"], 0.60)

    def test_multi_signal_ai_game_context_component_requires_verified_identity(self):
        evaluation = _evaluation(
            1,
            0.60,
            "walking back through the same hallway",
            primary="atmosphere_or_visual",
        )
        evaluation["game_context"] = {
            "status": "no_match",
            "label": "Unknown",
            "description": "survival horror video game",
            "facts": {"genres": ["survival horror"]},
        }

        apply_multi_signal_ai_scoring([evaluation], enabled=True, score_key="quality_score")

        scoring = evaluation["multi_signal_ai_scoring"]
        self.assertEqual(scoring["signals"]["game_context"], 0.0)
        self.assertEqual(scoring["contributions"]["game_context"], 0.0)
        self.assertEqual(scoring["game_context_nudge"]["status"], "unverified_game_context")

    def test_game_context_nudge_preserves_instructional_moments(self):
        evaluation = _evaluation(
            1,
            0.55,
            "you need to go here first and upgrade this before the next area",
            primary="tutorial_or_explainer",
        )
        game_context = {
            "status": "ok",
            "qid": "Q999",
            "label": "Strategy Builder",
            "description": "strategy simulation puzzle video game",
            "facts": {"genres": ["strategy", "simulation", "puzzle"]},
        }
        evaluation["game_context"] = game_context
        evaluation["moment"]["game_context"] = game_context

        apply_multi_signal_ai_scoring([evaluation], enabled=True, score_key="quality_score")

        nudge = evaluation["multi_signal_ai_scoring"]["game_context_nudge"]
        self.assertEqual(nudge["status"], "scored")
        self.assertIn("systems_or_tutorial", nudge["context_families"])
        self.assertIn("instructional_commentary", nudge["context_families"])
        self.assertGreater(nudge["adjustment"], 0)

    def test_game_context_nudge_favors_story_when_commentary_supports_it(self):
        evaluation = _evaluation(
            1,
            0.56,
            "this story chapter explains why that character remembers everything",
            primary="lore_or_story",
        )
        game_context = {
            "status": "ok",
            "qid": "Qstory",
            "label": "Story Game",
            "description": "narrative mystery adventure role-playing video game",
            "facts": {"genres": ["adventure", "role-playing"], "themes": ["mystery"]},
        }
        evaluation["game_context"] = game_context
        evaluation["moment"]["game_context"] = game_context

        apply_multi_signal_ai_scoring([evaluation], enabled=True, score_key="quality_score")

        nudge = evaluation["multi_signal_ai_scoring"]["game_context_nudge"]
        self.assertEqual(nudge["status"], "scored")
        self.assertIn("story_heavy", nudge["context_families"])
        self.assertGreater(nudge["adjustment"], 0)

    def test_game_context_nudge_does_not_steamroll_quality_gap(self):
        stronger = _evaluation(
            1,
            0.70,
            "walking through this room and checking the door",
            start=0,
            primary="commentary_or_review",
        )
        close_context = _evaluation(
            2,
            0.60,
            "oh my god he is right behind me please run",
            start=45,
            primary="high_energy",
        )
        game_context = {
            "status": "ok",
            "qid": "Q575505",
            "label": "Alan Wake",
            "description": "survival horror video game",
            "facts": {"genres": ["survival horror"]},
        }
        close_context["game_context"] = game_context
        close_context["moment"]["game_context"] = game_context
        apply_multi_signal_ai_scoring([stronger, close_context], enabled=True, score_key="quality_score")

        selected = select_best_candidates(
            [stronger, close_context],
            1,
            min_gap=8,
            score_key="multi_signal_ai_quality_score",
        )

        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 1)
        self.assertLessEqual(
            close_context["multi_signal_ai_scoring"]["game_context_nudge"]["adjustment"],
            GAME_CONTEXT_SELECTION_MAX_ADJUSTMENT,
        )


if __name__ == "__main__":
    unittest.main()
