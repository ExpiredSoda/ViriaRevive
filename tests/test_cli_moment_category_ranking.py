import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main as cli_main  # noqa: E402
from candidate_ranker import apply_learned_scoring, select_best_candidates  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
