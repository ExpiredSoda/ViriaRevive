import sys
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import candidate_ranker  # noqa: E402
from candidate_ranker import (  # noqa: E402
    COMMENTARY_GUARD_SELECTION_MAX_PENALTY,
    AI_MOMENT_SELECTION_MAX_ADJUSTMENT,
    LEARNED_SELECTION_MAX_ADJUSTMENT,
    MOMENT_CATEGORY_SELECTION_MAX_ADJUSTMENT,
    MULTI_SIGNAL_AI_MAX_NEGATIVE_ADJUSTMENT,
    MULTI_SIGNAL_AI_MAX_POSITIVE_ADJUSTMENT,
    VOICE_PROFILE_SELECTION_MAX_ADJUSTMENT,
    VOICE_PROFILE_SHADOW_MAX_ADJUSTMENT,
    attach_ai_moment_classification,
    apply_ai_moment_scoring,
    apply_learned_scoring,
    apply_moment_category_scoring,
    apply_multi_signal_ai_scoring,
    apply_voice_profile_scoring,
    build_learning_prompt_context,
    build_learning_status,
    build_ai_moment_ranking_report,
    build_moment_category_ranking_report,
    build_multi_signal_ai_ranking_report,
    build_shadow_scoring_report,
    build_voice_profile_ranking_report,
    build_voice_profile_shadow_report,
    classify_commentary_guard,
    classify_music_lyrics_guard,
    evaluate_candidate,
    needs_stream_retry,
    normalize_commentary_subtitle_policy,
    quality_floor_for_preference,
    score_moment_categories,
    select_best_candidates,
    select_near_quality_fallback_candidates,
    trim_candidate_with_transcript,
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


def _ranker_candidate():
    return {
        "start": 0,
        "end": 30,
        "peak_time": 15,
        "score": 0.7,
        "candidate_rank": 1,
        "candidate_kind": "primary",
        "detector_scores": {"audio": 0.6, "variance": 0.4, "scene": 0.0},
    }


def _words_from_tokens(tokens, start=0.1):
    words = []
    t = float(start)
    for token in tokens:
        words.append({"text": token, "start": t, "end": t + 0.2})
        t += 0.25
    return words


def _mixed_creator_game_words():
    creator = _words_from_tokens(
        "oh my god he is right behind me please run".split(),
        start=0.1,
    )
    game_start = creator[-1]["end"] + 1.05
    game = _words_from_tokens("objective updated find the key to the door.".split(), start=game_start)
    return creator + game


def _evaluate_with_guard(words, policy="creator"):
    return evaluate_candidate(
        _ranker_candidate(),
        words,
        extraction_start=0,
        extraction_end=35,
        video_duration=60,
        target_duration=30,
        selected_stream=0,
        quality_floor=0.0,
        commentary_guard=True,
        commentary_guard_policy=policy,
    )


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
    def test_category_scoring_separates_tutorial_from_high_energy(self):
        categories = score_moment_categories(
            "Here is how you do this. First go here, then use this mechanic.",
            {"detector_scores": {"audio": 0.2, "variance": 0.2, "scene": 0.1}},
            word_count=12,
            duration=22,
        )

        self.assertEqual(categories["primary"], "tutorial_or_explainer")
        self.assertGreater(categories["scores"]["tutorial_or_explainer"], categories["scores"]["high_energy"])

    def test_category_scoring_does_not_treat_technical_difference_as_panic(self):
        categories = score_moment_categories(
            "I don't know enough about guns to know what the difference between a magnum revolver is and a regular revolver.",
            {"detector_scores": {"audio": 0.95, "variance": 0.85, "scene": 0.0}},
            hook_points=3,
            word_count=19,
            duration=16,
        )

        self.assertEqual(categories["primary"], "tutorial_or_explainer")
        self.assertGreater(categories["scores"]["tutorial_or_explainer"], categories["scores"]["high_energy"])

    def test_category_scoring_uses_context_over_generic_action_words(self):
        categories = score_moment_categories(
            "Here is how you survive this part. First run to the door, then hide and wait for the mechanic.",
            {"detector_scores": {"audio": 0.86, "variance": 0.74, "scene": 0.0}},
            hook_points=4,
            word_count=18,
            duration=24,
        )

        self.assertEqual(categories["primary"], "tutorial_or_explainer")
        self.assertGreater(categories["scores"]["tutorial_or_explainer"], categories["scores"]["high_energy"])
        self.assertIn("context_tempered_high_energy", categories["evidence_notes"])

    def test_category_scoring_tracks_game_narration_without_high_energy_label(self):
        categories = score_moment_categories(
            "Objective updated. You must run to the door and find the key before the next checkpoint.",
            {"detector_scores": {"audio": 0.92, "variance": 0.82, "scene": 0.0}},
            word_count=15,
            duration=18,
        )

        self.assertNotEqual(categories["primary"], "high_energy")
        self.assertEqual(categories["primary"], "cinematic_dialogue")
        self.assertEqual(categories["signals"]["speech_source"], "game_narration")
        self.assertGreater(categories["signals"]["game_speech"], categories["signals"]["creator_speech"])
        self.assertGreater(categories["scores"]["cinematic_dialogue"], categories["scores"]["high_energy"])

    def test_category_scoring_uses_visual_dialogue_scene_for_cinematic_label(self):
        categories = score_moment_categories(
            "I need you to listen carefully before the next chapter begins.",
            {
                "detector_scores": {"audio": 0.62, "variance": 0.44, "scene": 0.28},
                "visual_diagnostics": {
                    "primary_visual_label": "lore_or_story",
                    "labels": ["dialogue_scene"],
                },
            },
            word_count=12,
            duration=20,
        )

        self.assertEqual(categories["primary"], "cinematic_dialogue")
        self.assertIn("visual_dialogue_scene", categories["signals"])
        self.assertGreaterEqual(categories["signals"]["visual_dialogue_scene"], 0.7)

    def test_category_scoring_keeps_lore_over_actionish_narration(self):
        categories = score_moment_categories(
            "The manuscript chapter says the narrator watched him run from the dark presence.",
            {"detector_scores": {"audio": 0.84, "variance": 0.72, "scene": 0.0}},
            word_count=12,
            duration=16,
        )

        self.assertEqual(categories["primary"], "lore_or_story")
        self.assertGreater(categories["scores"]["lore_or_story"], categories["scores"]["high_energy"])

    def test_phrase_only_reactive_words_stay_low_confidence(self):
        categories = score_moment_categories(
            "run please",
            {"detector_scores": {"audio": 0.2, "variance": 0.15, "scene": 0.0}},
            word_count=2,
            duration=8,
        )

        self.assertLess(categories["scores"]["high_energy"], 0.30)

    def test_category_scoring_marks_death_or_failure_moments(self):
        categories = score_moment_categories(
            "Oh my god he got me midair. We died.",
            {"detector_scores": {"audio": 0.8, "variance": 0.7, "scene": 0.4}},
            hook_points=4,
            aftermath_points=4,
            word_count=9,
            duration=14,
        )

        self.assertEqual(categories["primary"], "death_or_failure")
        self.assertGreater(categories["scores"]["death_or_failure"], 0.5)

    def test_visual_diagnostics_can_support_failure_category_without_selection_score(self):
        categories = score_moment_categories(
            "",
            {
                "detector_scores": {"audio": 0.2, "variance": 0.2, "scene": 0.1},
                "visual_diagnostics": {
                    "status": "ok",
                    "visual_energy": 0.2,
                    "possible_failure_score": 1.0,
                    "red_flash_score": 0.9,
                    "ui_density": 0.4,
                },
            },
            word_count=0,
            duration=18,
        )

        self.assertEqual(categories["primary"], "death_or_failure")
        self.assertGreater(categories["scores"]["death_or_failure"], categories["scores"]["atmosphere_or_visual"])
        self.assertEqual(categories["signals"]["visual_status"], "ok")

    def test_visual_diagnostics_can_support_atmosphere_without_blank_inflation(self):
        scenic = score_moment_categories(
            "",
            {
                "detector_scores": {"audio": 0.1, "variance": 0.2, "scene": 0.2},
                "visual_diagnostics": {
                    "status": "ok",
                    "visual_energy": 0.1,
                    "scenic_score": 0.9,
                    "dark_scene_score": 0.1,
                    "ui_density": 0.0,
                },
            },
            word_count=0,
            duration=18,
        )
        blank = score_moment_categories(
            "",
            {
                "detector_scores": {"audio": 0.1, "variance": 0.0, "scene": 0.0},
                "visual_diagnostics": {
                    "status": "ok",
                    "visual_energy": 0.0,
                    "scenic_score": 0.0,
                    "dark_scene_score": 1.0,
                    "black_frame_ratio": 1.0,
                },
            },
            word_count=0,
            duration=18,
        )

        self.assertEqual(scenic["primary"], "atmosphere_or_visual")
        self.assertGreater(scenic["scores"]["atmosphere_or_visual"], blank["scores"]["atmosphere_or_visual"])
        self.assertLess(blank["scores"]["atmosphere_or_visual"], 0.25)
        self.assertEqual(blank["primary"], "low_value")
        self.assertEqual(blank["signals"]["visual_black_frames"], 1.0)
        self.assertIn("confirmed_black_frames_tempered_category", blank["evidence_notes"])

    def test_black_frame_category_can_downrank_close_candidate(self):
        blank = _evaluation(1, 0.60, "", start=0)
        normal = _evaluation(2, 0.585, "panic chase right behind me run please", start=45)
        blank_categories = score_moment_categories(
            "",
            {
                "detector_scores": {"audio": 0.05, "variance": 0.0, "scene": 0.0},
                "visual_diagnostics": {
                    "status": "ok",
                    "visual_energy": 0.0,
                    "scenic_score": 0.0,
                    "black_frame_ratio": 1.0,
                },
            },
            word_count=0,
            duration=18,
        )
        normal_categories = score_moment_categories(
            normal["transcript"],
            {"detector_scores": {"audio": 0.7, "variance": 0.5, "scene": 0.1}},
            hook_points=5,
            word_count=8,
            duration=20,
        )
        blank["moment_categories"] = blank_categories
        blank["moment"]["moment_categories"] = blank_categories
        normal["moment_categories"] = normal_categories
        normal["moment"]["moment_categories"] = normal_categories
        evaluations = [blank, normal]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        learned_selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="learned_quality_score")

        apply_moment_category_scoring(evaluations, enabled=True, score_key="learned_quality_score")
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="moment_category_quality_score")

        self.assertEqual(blank_categories["primary"], "low_value")
        self.assertEqual(learned_selected[0]["candidate"]["candidate_rank"], 1)
        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 2)
        self.assertAlmostEqual(blank["moment_category_scoring"]["category_adjustment"], -MOMENT_CATEGORY_SELECTION_MAX_ADJUSTMENT)

    def test_category_scoring_marks_stat_or_end_screen_low_value(self):
        categories = score_moment_categories(
            "statistics results screen mission complete collected items",
            {"detector_scores": {"audio": 0.1, "variance": 0.1, "scene": 0.0}},
            word_count=6,
            duration=20,
        )

        self.assertEqual(categories["primary"], "low_value")
        self.assertGreater(categories["scores"]["low_value"], categories["scores"]["atmosphere_or_visual"])

    def test_stat_end_screen_downrank_is_capped_when_category_ranking_enabled(self):
        stat_screen = _evaluation(1, 0.60, "statistics results screen mission complete collected items", start=0)
        normal = _evaluation(2, 0.585, "panic chase right behind me run please", start=45)
        stat_categories = score_moment_categories(
            stat_screen["transcript"],
            {"detector_scores": {"audio": 0.1, "variance": 0.1, "scene": 0.0}},
            word_count=6,
            duration=20,
        )
        normal_categories = score_moment_categories(
            normal["transcript"],
            {"detector_scores": {"audio": 0.7, "variance": 0.5, "scene": 0.1}},
            hook_points=5,
            word_count=8,
            duration=20,
        )
        stat_screen["moment_categories"] = stat_categories
        stat_screen["moment"]["moment_categories"] = stat_categories
        normal["moment_categories"] = normal_categories
        normal["moment"]["moment_categories"] = normal_categories
        evaluations = [stat_screen, normal]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})

        apply_moment_category_scoring(evaluations, enabled=True, score_key="learned_quality_score")
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="moment_category_quality_score")

        self.assertEqual(stat_categories["primary"], "low_value")
        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 2)
        self.assertAlmostEqual(stat_screen["moment_category_scoring"]["category_adjustment"], -MOMENT_CATEGORY_SELECTION_MAX_ADJUSTMENT)

    def test_stat_end_screen_does_not_override_strong_failure_payoff(self):
        categories = score_moment_categories(
            "mission complete but oh no he got me and we died",
            {"detector_scores": {"audio": 0.8, "variance": 0.7, "scene": 0.2}},
            hook_points=4,
            aftermath_points=4,
            word_count=10,
            duration=16,
        )

        self.assertEqual(categories["primary"], "death_or_failure")
        self.assertGreater(categories["scores"]["death_or_failure"], categories["scores"]["low_value"])

    def test_ai_moment_classification_attaches_without_changing_scores(self):
        evaluation = _evaluation(
            1,
            0.72,
            "Oh my god he is right behind me please run",
        )
        categories = score_moment_categories(evaluation["moment"]["transcript"], evaluation["candidate"])
        evaluation["moment"]["moment_categories"] = categories
        evaluation["moment"]["primary_category"] = categories["primary"]
        evaluation["moment"]["ranker"]["moment_categories"] = categories
        before_quality = evaluation["quality_score"]
        before_primary = evaluation["moment"]["primary_category"]

        attached = attach_ai_moment_classification(evaluation, {
            "schema_version": 1,
            "enabled": True,
            "status": "ok",
            "provider": "ollama",
            "model": "qwen2.5:3b",
            "primary_category": "death_or_failure",
            "fine_labels": ["death_scene"],
            "confidence": 0.76,
            "reason": "Visual and transcript cues point at a failure moment.",
            "fallback_used": False,
            "selection_impact": "none",
            "output_changed": False,
        })

        self.assertEqual(evaluation["quality_score"], before_quality)
        self.assertEqual(evaluation["moment"]["primary_category"], before_primary)
        self.assertEqual(evaluation["moment"]["moment_categories"]["primary"], before_primary)
        self.assertEqual(attached["selection_impact"], "none")
        self.assertFalse(attached["output_changed"])
        self.assertEqual(
            evaluation["moment"]["moment_categories"]["ai"]["primary_category"],
            "death_or_failure",
        )

    def test_ai_moment_classification_forces_no_selection_impact(self):
        evaluation = _evaluation(1, 0.72, "This chase went wrong fast")

        attached = attach_ai_moment_classification(evaluation, {
            "schema_version": 1,
            "enabled": True,
            "status": "ok",
            "provider": "ollama",
            "model": "qwen2.5:3b",
            "primary_category": "high_energy",
            "fine_labels": ["chase_panic"],
            "confidence": 0.92,
            "reason": "Malicious or malformed impact fields should not leak through.",
            "fallback_used": False,
            "selection_impact": "capped_rank_adjustment",
            "output_changed": True,
        })

        self.assertEqual(attached["selection_impact"], "none")
        self.assertFalse(attached["output_changed"])
        self.assertEqual(
            evaluation["moment"]["ai_moment_classification"]["selection_impact"],
            "none",
        )
        self.assertFalse(evaluation["moment"]["ai_moment_classification"]["output_changed"])

    def test_ai_moment_classification_unknown_primary_is_explicit_diagnostic_state(self):
        evaluation = _evaluation(1, 0.72, "This chase went wrong fast")

        attached = attach_ai_moment_classification(evaluation, {
            "schema_version": 1,
            "enabled": True,
            "status": "ok",
            "provider": "ollama",
            "model": "qwen2.5:3b",
            "primary_category": "romance_arc",
            "fine_labels": ["unexpected_label"],
            "confidence": 0.92,
            "reason": "Unknown categories should not masquerade as low value.",
            "selection_impact": "capped_rank_adjustment",
            "output_changed": True,
        })

        self.assertEqual(attached["primary_category"], "unknown")
        self.assertTrue(attached["invalid_primary_category"])
        self.assertEqual(attached["selection_impact"], "none")
        self.assertFalse(attached["output_changed"])
        self.assertEqual(
            evaluation["moment"]["moment_categories"]["ai"]["primary_category"],
            "unknown",
        )

    def test_ai_moment_labels_do_not_change_selection_order(self):
        strong = _evaluation(1, 0.80, "This is a quiet setup but it is scored higher", start=0)
        weak = _evaluation(2, 0.52, "Oh my god this is a huge chase panic moment", start=60)

        baseline = select_best_candidates([strong, weak], 1, min_gap=8, score_key="quality_score")
        attach_ai_moment_classification(strong, {
            "primary_category": "low_value",
            "status": "ok",
            "provider": "ollama",
            "confidence": 0.95,
            "selection_impact": "none",
            "output_changed": False,
        })
        attach_ai_moment_classification(weak, {
            "primary_category": "high_energy",
            "status": "ok",
            "provider": "ollama",
            "confidence": 0.95,
            "selection_impact": "none",
            "output_changed": False,
        })
        selected = select_best_candidates([strong, weak], 1, min_gap=8, score_key="quality_score")

        self.assertEqual(baseline[0]["candidate"]["candidate_rank"], 1)
        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 1)

    def test_later_selection_clears_stale_overlap_reject_reason(self):
        first = _evaluation(1, 0.90, "first candidate starts stronger", start=0)
        second = _evaluation(2, 0.80, "second candidate later wins another ranking pass", start=10)
        first["alternate_quality_score"] = 0.10
        second["alternate_quality_score"] = 1.0

        baseline = select_best_candidates([first, second], 2, min_gap=60, score_key="quality_score")
        self.assertEqual(baseline[0]["candidate"]["candidate_rank"], 1)
        self.assertEqual(second["reject_reason"], "overlaps_better_candidate")

        selected = select_best_candidates([first, second], 2, min_gap=60, score_key="alternate_quality_score")

        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 2)
        self.assertEqual(selected[0]["reject_reason"], "")
        self.assertEqual(selected[0]["moment"]["ranker"]["reject_reason"], "")

    def test_debug_report_includes_ai_moment_classification_metadata(self):
        evaluation = _evaluation(1, 0.72, "Oh my god he is right behind me please run")
        attach_ai_moment_classification(evaluation, {
            "schema_version": 1,
            "enabled": True,
            "status": "model_not_ready",
            "provider": "heuristic",
            "model": "qwen2.5:3b",
            "primary_category": "high_energy",
            "fine_labels": ["chase_panic"],
            "confidence": 0.65,
            "reason": "Fallback label from hook phrases.",
            "fallback_used": True,
            "selection_impact": "none",
            "output_changed": False,
        })

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            out = temp_path / "debug.json"
            write_debug_report(
                out,
                temp_path / "source.mp4",
                [evaluation["candidate"]],
                [evaluation],
                [evaluation],
                ai_moment_classification={
                    "enabled": True,
                    "status": "ok",
                    "selection_impact": "none",
                    "output_changed": False,
                },
                ai_moment_classification_shadow={
                    "schema_version": 1,
                    "enabled": True,
                    "status": "ok",
                    "diagnostic_only": True,
                    "selection_impact": "none",
                    "output_changed": False,
                    "shortlist_count": 1,
                    "rows": [
                        {
                            "candidate_rank": 1,
                            "selected_in_output": True,
                            "ai_moment_classification": {
                                "status": "model_not_ready",
                                "primary_category": "high_energy",
                                "selection_impact": "none",
                                "output_changed": False,
                            },
                        }
                    ],
                },
            )
            payload = json.loads(out.read_text(encoding="utf-8"))

        row = payload["candidates"][0]
        self.assertEqual(payload["ai_moment_classification"]["selection_impact"], "none")
        self.assertEqual(payload["ai_moment_classification_shadow"]["selection_impact"], "none")
        self.assertTrue(payload["ai_moment_classification_shadow"]["diagnostic_only"])
        self.assertEqual(payload["ai_moment_classification_shadow"]["rows"][0]["candidate_rank"], 1)
        self.assertEqual(row["ai_moment_classification"]["primary_category"], "high_energy")
        self.assertEqual(row["moment_categories"]["ai"]["fine_labels"], ["chase_panic"])
        self.assertEqual(row["selection"]["ai_moment_classification"]["status"], "model_not_ready")

    def test_detection_preference_quality_floor_changes_acceptance(self):
        candidate = {
            "start": 0,
            "end": 30,
            "peak_time": 15,
            "score": 0.55,
            "candidate_rank": 1,
            "candidate_kind": "primary",
            "detector_scores": {"audio": 0.4, "variance": 0.3, "scene": 0.0},
        }
        words = [
            {"text": "walking", "start": 0.2, "end": 0.5},
            {"text": "around", "start": 0.5, "end": 0.9},
            {"text": "checking", "start": 0.9, "end": 1.2},
            {"text": "this", "start": 1.2, "end": 1.4},
            {"text": "room", "start": 1.4, "end": 1.8},
            {"text": "again", "start": 1.8, "end": 2.1},
        ]

        quality_eval = evaluate_candidate(
            candidate,
            words,
            extraction_start=0,
            extraction_end=35,
            video_duration=60,
            target_duration=30,
            selected_stream=1,
            quality_floor=0.99,
            detection_preference="quality",
        )
        quantity_eval = evaluate_candidate(
            candidate,
            words,
            extraction_start=0,
            extraction_end=35,
            video_duration=60,
            target_duration=30,
            selected_stream=1,
            quality_floor=0.0,
            detection_preference="quantity",
        )

        self.assertFalse(quality_eval["accepted"])
        self.assertEqual(quality_eval["reject_reason"], "low_transcript_quality")
        self.assertTrue(quantity_eval["accepted"])
        self.assertEqual(quantity_eval["detection_preference"], "quantity")
        self.assertEqual(quality_floor_for_preference("bad-value"), 0.50)

    def test_near_quality_fallback_promotes_creator_misses_when_strict_floor_selects_zero(self):
        def near_miss(rank, quality, start):
            return {
                "accepted": False,
                "reject_reason": "low_transcript_quality",
                "quality_score": quality,
                "learned_quality_score": quality,
                "quality_floor": 0.60,
                "candidate": {"candidate_rank": rank, "candidate_kind": "primary"},
                "moment": {
                    "start": start,
                    "end": start + 35,
                    "duration": 35,
                    "transcript": "I think this is actually a pretty good moment with creator commentary",
                    "ranker": {"reject_reason": "low_transcript_quality"},
                },
                "word_count": 18,
                "subtitle_word_count": 18,
                "music_lyrics_guard": {"reject_candidate": False},
                "speech_source": {
                    "primary_source": "creator",
                    "creator_safe": True,
                    "game_or_npc_probability": 0.05,
                    "music_or_lyrics_probability": 0.01,
                },
                "commentary_guard": {"summary": {"primary_label": "creator_commentary"}},
            }

        evaluations = [
            near_miss(1, 0.56, 0),
            near_miss(2, 0.52, 80),
            near_miss(3, 0.49, 160),
        ]

        selected = select_near_quality_fallback_candidates(
            evaluations,
            5,
            min_gap=8,
            score_key="learned_quality_score",
        )

        self.assertEqual(len(selected), 2)
        self.assertTrue(all(item["accepted"] for item in selected))
        self.assertEqual({item["candidate"]["candidate_rank"] for item in selected}, {1, 2})
        self.assertTrue(all(item["near_quality_fallback"]["applied"] for item in selected))
        self.assertEqual(selected[0]["moment"]["ranker"]["reject_reason"], "")
        self.assertEqual(selected[0]["moment"]["ranker"]["original_reject_reason"], "low_transcript_quality")
        self.assertTrue(all(item["selection_tier"] == "near_quality_pick" for item in selected))
        self.assertTrue(all(item["moment"]["selection_tier"] == "near_quality_pick" for item in selected))

    def test_near_quality_fallback_does_not_promote_game_or_music_rejections(self):
        base = {
            "accepted": False,
            "reject_reason": "low_transcript_quality",
            "quality_score": 0.57,
            "learned_quality_score": 0.57,
            "quality_floor": 0.60,
            "candidate": {"candidate_rank": 1, "candidate_kind": "primary"},
            "moment": {
                "start": 0,
                "end": 35,
                "duration": 35,
                "transcript": "objective updated find the route to the ship",
                "ranker": {"reject_reason": "low_transcript_quality"},
            },
            "word_count": 12,
            "subtitle_word_count": 12,
            "music_lyrics_guard": {"reject_candidate": False},
            "speech_source": {
                "primary_source": "game",
                "creator_safe": False,
                "game_or_npc_probability": 0.8,
                "music_or_lyrics_probability": 0.02,
            },
            "commentary_guard": {"summary": {"primary_label": "game_narration"}},
        }
        music = json.loads(json.dumps(base))
        music["candidate"]["candidate_rank"] = 2
        music["moment"]["start"] = 80
        music["moment"]["end"] = 115
        music["speech_source"]["primary_source"] = "music"
        music["music_lyrics_guard"] = {"reject_candidate": True}
        low = json.loads(json.dumps(base))
        low["candidate"]["candidate_rank"] = 3
        low["moment"]["start"] = 160
        low["moment"]["end"] = 195
        low["quality_score"] = 0.42
        low["learned_quality_score"] = 0.42

        selected = select_near_quality_fallback_candidates(
            [base, music, low],
            5,
            min_gap=8,
            score_key="learned_quality_score",
        )

        self.assertEqual(selected, [])

    def test_near_quality_fallback_does_not_promote_missing_creator_transcript(self):
        missing_creator = {
            "accepted": False,
            "reject_reason": "low_transcript_quality",
            "quality_score": 0.57,
            "learned_quality_score": 0.57,
            "quality_floor": 0.60,
            "candidate": {"candidate_rank": 1, "candidate_kind": "primary"},
            "moment": {
                "start": 0,
                "end": 35,
                "duration": 35,
                "transcript": "",
                "metadata_needs_context": True,
                "speech_policy": {
                    "status": "no_selected_commentary_speech",
                    "metadata_backfill_blocked": True,
                    "selected_track_has_speech": False,
                    "selected_track_word_count": 0,
                    "track_aware": True,
                },
                "ranker": {"reject_reason": "low_transcript_quality"},
            },
            "word_count": 16,
            "subtitle_word_count": 16,
            "music_lyrics_guard": {"reject_candidate": False},
            "speech_source": {
                "primary_source": "creator",
                "creator_safe": True,
                "game_or_npc_probability": 0.02,
                "music_or_lyrics_probability": 0.01,
            },
            "commentary_guard": {"summary": {"primary_label": "creator_commentary"}},
        }

        selected = select_near_quality_fallback_candidates(
            [missing_creator],
            3,
            min_gap=8,
            score_key="learned_quality_score",
            subtitle_policy="creator",
        )

        self.assertEqual(selected, [])

    def test_partial_near_quality_fallback_fills_deep_auto_shortfall(self):
        existing = _evaluation(1, 0.58, "oh my god this is a strong creator moment", start=0)

        def near_miss(rank, quality, start):
            return {
                "accepted": False,
                "reject_reason": "low_transcript_quality",
                "quality_score": quality,
                "learned_quality_score": quality,
                "multi_signal_ai_quality_score": quality + 0.04,
                "quality_floor": 0.50,
                "candidate": {"candidate_rank": rank, "candidate_kind": "primary"},
                "moment": {
                    "start": start,
                    "end": start + 35,
                    "duration": 35,
                    "transcript": "I think this is actually useful creator commentary about what is happening",
                    "ranker": {"reject_reason": "low_transcript_quality"},
                },
                "word_count": 14,
                "subtitle_word_count": 14,
                "music_lyrics_guard": {"reject_candidate": False},
                "speech_source": {
                    "primary_source": "creator",
                    "creator_safe": True,
                    "game_or_npc_probability": 0.05,
                    "music_or_lyrics_probability": 0.01,
                },
                "commentary_guard": {"summary": {"primary_label": "creator_commentary"}},
            }

        evaluations = [
            existing,
            near_miss(2, 0.36, 80),
            near_miss(3, 0.34, 160),
            near_miss(4, 0.26, 240),
        ]

        selected = select_near_quality_fallback_candidates(
            evaluations,
            4,
            min_gap=8,
            score_key="multi_signal_ai_quality_score",
            existing_selected=[existing],
            allow_partial=True,
            reason="deep_auto_best_available_fill",
        )

        self.assertEqual(len(selected), 3)
        extra = [item for item in selected if item.get("near_quality_fallback")]
        self.assertEqual(len(extra), 2)
        self.assertTrue(all(item["accepted"] for item in extra))
        self.assertEqual({item["candidate"]["candidate_rank"] for item in extra}, {2, 3})
        self.assertTrue(all(item["near_quality_fallback"]["selection_tier"] == "extra_candidate" for item in extra))
        self.assertTrue(all(item["selection_tier"] == "extra_pick" for item in extra))
        self.assertEqual(existing["selection_tier"], "recommended")
        self.assertEqual(extra[0]["near_quality_fallback"]["reason"], "deep_auto_best_available_fill")

    def test_laid_back_commentary_gets_capped_quality_boost(self):
        quiet_words = _words_from_tokens(
            "I think this game is actually doing something interesting here because the jacket is just her size".split(),
            start=0.1,
        )
        plain_words = _words_from_tokens(
            "walking around through this hallway with nothing much happening right now".split(),
            start=0.1,
        )

        quiet_eval = evaluate_candidate(
            _ranker_candidate(),
            quiet_words,
            extraction_start=0,
            extraction_end=35,
            video_duration=90,
            target_duration=30,
            selected_stream=1,
            quality_floor=0.0,
            detection_preference="auto",
        )
        plain_eval = evaluate_candidate(
            _ranker_candidate(),
            plain_words,
            extraction_start=0,
            extraction_end=35,
            video_duration=90,
            target_duration=30,
            selected_stream=1,
            quality_floor=0.0,
            detection_preference="auto",
        )

        self.assertGreater(quiet_eval["laid_back_commentary_boost"], 0)
        self.assertGreater(quiet_eval["quality_score"], plain_eval["quality_score"])
        self.assertIn("laid_back_commentary", quiet_eval["moment"]["ranker"])

    def test_rich_context_commentary_gets_capped_quality_boost(self):
        rich_words = _words_from_tokens(
            "I love how this is chaos because of course the game ran me over and that was hilarious".split(),
            start=0.1,
        )
        plain_words = _words_from_tokens(
            "walking around through this hallway with nothing much happening right now".split(),
            start=0.1,
        )

        rich_eval = evaluate_candidate(
            _ranker_candidate(),
            rich_words,
            extraction_start=0,
            extraction_end=35,
            video_duration=90,
            target_duration=30,
            selected_stream=1,
            quality_floor=0.0,
            detection_preference="auto",
        )
        plain_eval = evaluate_candidate(
            _ranker_candidate(),
            plain_words,
            extraction_start=0,
            extraction_end=35,
            video_duration=90,
            target_duration=30,
            selected_stream=1,
            quality_floor=0.0,
            detection_preference="auto",
        )

        self.assertGreater(rich_eval["rich_context_boost"], 0)
        self.assertGreater(rich_eval["quality_score"], plain_eval["quality_score"])
        self.assertIn("rich_context", rich_eval["moment"]["ranker"])

    def test_commentary_guard_shadow_classifies_creator_and_game_segments(self):
        creator = classify_commentary_guard([
            {"text": "oh", "start": 0.0, "end": 0.2},
            {"text": "my", "start": 0.2, "end": 0.3},
            {"text": "god", "start": 0.3, "end": 0.5},
            {"text": "he", "start": 0.5, "end": 0.7},
            {"text": "is", "start": 0.7, "end": 0.8},
            {"text": "right", "start": 0.8, "end": 1.0},
            {"text": "behind", "start": 1.0, "end": 1.2},
            {"text": "me", "start": 1.2, "end": 1.4},
        ])
        game = classify_commentary_guard([
            {"text": "objective", "start": 0.0, "end": 0.4},
            {"text": "updated", "start": 0.4, "end": 0.8},
            {"text": "find", "start": 0.8, "end": 1.0},
            {"text": "the", "start": 1.0, "end": 1.1},
            {"text": "key", "start": 1.1, "end": 1.3},
            {"text": "to", "start": 1.3, "end": 1.4},
            {"text": "the", "start": 1.4, "end": 1.5},
            {"text": "door", "start": 1.5, "end": 1.8},
        ])

        self.assertFalse(creator["output_changed"])
        self.assertEqual(creator["summary"]["primary_label"], "creator_commentary")
        self.assertEqual(creator["segments"][0]["label"], "creator_commentary")
        self.assertEqual(game["summary"]["primary_label"], "game_narration")
        self.assertEqual(game["segments"][0]["label"], "game_narration")

    def test_commentary_guard_prefers_stronger_game_source_evidence(self):
        game = classify_commentary_guard([
            {"text": t, "start": i * 0.2, "end": i * 0.2 + 0.1}
            for i, t in enumerate("Objective updated you must run to the door and find the key".split())
        ])

        self.assertEqual(game["summary"]["primary_label"], "game_narration")
        self.assertEqual(game["segments"][0]["label"], "game_narration")
        self.assertGreater(
            game["segments"][0]["scores"]["game_narration"],
            game["segments"][0]["scores"]["creator_commentary"],
        )

    def test_commentary_guard_disabled_and_empty_are_noops(self):
        disabled = classify_commentary_guard(
            [{"text": "objective", "start": 0.0, "end": 0.5}],
            enabled=False,
        )
        empty = classify_commentary_guard([])

        self.assertFalse(disabled["enabled"])
        self.assertEqual(disabled["reason"], "disabled")
        self.assertEqual(disabled["segments"], [])
        self.assertEqual(disabled["selection_impact"], "none")
        self.assertEqual(disabled["subtitle_impact"], "none")
        self.assertEqual(empty["reason"], "no_words")
        self.assertEqual(empty["segments"], [])

    def test_evaluate_candidate_adds_commentary_guard_without_changing_acceptance(self):
        candidate = _ranker_candidate()
        words = [
            {"text": "oh", "start": 0.1, "end": 0.2},
            {"text": "my", "start": 0.2, "end": 0.3},
            {"text": "god", "start": 0.3, "end": 0.5},
            {"text": "he", "start": 0.5, "end": 0.7},
            {"text": "is", "start": 0.7, "end": 0.8},
            {"text": "right", "start": 0.8, "end": 1.0},
            {"text": "behind", "start": 1.0, "end": 1.2},
            {"text": "me", "start": 1.2, "end": 1.4},
        ]

        unguarded = evaluate_candidate(
            candidate,
            words,
            extraction_start=0,
            extraction_end=35,
            video_duration=60,
            target_duration=30,
            selected_stream=0,
            quality_floor=0.0,
            commentary_guard=False,
        )
        guarded = evaluate_candidate(
            candidate,
            words,
            extraction_start=0,
            extraction_end=35,
            video_duration=60,
            target_duration=30,
            selected_stream=0,
            quality_floor=0.0,
            commentary_guard=True,
        )

        self.assertEqual(unguarded["accepted"], guarded["accepted"])
        self.assertFalse(guarded["commentary_guard"]["output_changed"])
        self.assertTrue(guarded["commentary_guard"]["enabled"])
        self.assertEqual(guarded["moment"]["commentary_guard"]["summary"]["primary_label"], "creator_commentary")
        self.assertNotIn("segments", guarded["moment"]["commentary_guard"])

    def test_commentary_guard_filters_mixed_track_to_creator_by_default(self):
        guarded = _evaluate_with_guard(_mixed_creator_game_words())

        self.assertTrue(guarded["accepted"])
        self.assertIn("right behind me", guarded["moment"]["transcript"])
        self.assertNotIn("objective updated", guarded["moment"]["transcript"])
        self.assertEqual(guarded["word_count"], 18)
        self.assertEqual(guarded["moment"]["analysis_word_count"], 18)
        self.assertEqual(guarded["moment"]["subtitle_word_count"], 10)
        self.assertEqual(guarded["commentary_guard"]["mode"], "light_filter")
        self.assertTrue(guarded["commentary_guard"]["output_changed"])
        self.assertEqual(guarded["commentary_guard"]["subtitle_impact"], "filtered_words")
        self.assertEqual(guarded["commentary_guard"]["application"]["policy"], "creator")
        self.assertEqual(guarded["commentary_guard"]["application"]["removed_labels"], ["game_narration"])
        self.assertEqual(guarded["commentary_guard"]["selection_impact"], "none")

    def test_commentary_guard_all_speech_opt_in_keeps_game_narration(self):
        guarded = _evaluate_with_guard(_mixed_creator_game_words(), policy="all")

        self.assertIn("objective updated", guarded["moment"]["transcript"])
        self.assertIn("right behind me", guarded["moment"]["transcript"])
        self.assertFalse(guarded["commentary_guard"]["output_changed"])
        self.assertEqual(guarded["commentary_guard"]["application"]["reason"], "all_speech_policy")
        self.assertEqual(guarded["moment"]["subtitle_word_count"], 18)

    def test_commentary_guard_prefer_game_opt_in_uses_game_when_available(self):
        guarded = _evaluate_with_guard(_mixed_creator_game_words(), policy="game")

        self.assertIn("objective updated", guarded["moment"]["transcript"])
        self.assertNotIn("right behind me", guarded["moment"]["transcript"])
        self.assertTrue(guarded["commentary_guard"]["output_changed"])
        self.assertEqual(guarded["commentary_guard"]["application"]["policy"], "game")
        self.assertEqual(guarded["commentary_guard"]["application"]["removed_labels"], ["creator_commentary"])

    def test_commentary_guard_prefers_no_creator_subtitles_over_game_dialogue(self):
        game_only = _words_from_tokens("objective updated find the key to the door.".split(), start=0.1)
        guarded = _evaluate_with_guard(game_only)

        self.assertEqual(guarded["moment"]["transcript"], "")
        self.assertTrue(guarded["commentary_guard"]["output_changed"])
        self.assertFalse(guarded["commentary_guard"]["application"]["fallback_used"])
        self.assertEqual(guarded["commentary_guard"]["application"]["reason"], "no_creator_commentary_after_filter")
        self.assertEqual(guarded["moment"]["subtitle_word_count"], 0)

    def test_commentary_guard_restores_unclear_creator_like_stream(self):
        words = _words_from_tokens(
            "oh great little hole gonna get hit right in the knee".split(),
            start=0.1,
        )
        guarded = evaluate_candidate(
            _ranker_candidate(),
            words,
            extraction_start=0,
            extraction_end=35,
            video_duration=60,
            target_duration=30,
            selected_stream=1,
            quality_floor=0.0,
            commentary_guard=True,
            commentary_guard_policy="creator",
            stream_profile={
                "creator_likeness_score": 0.58,
                "natural_dialogue_score": 4.4,
                "scripted_game_score": 0.25,
                "acoustic_game_bed_score": 0.02,
                "lyric_likelihood": 0.05,
                "voice_title_hints": [],
                "game_title_hints": [],
            },
        )

        self.assertIn("little hole", guarded["moment"]["transcript"])
        self.assertEqual(guarded["moment"]["subtitle_word_count"], len(words))
        self.assertEqual(
            guarded["commentary_guard"]["application"]["reason"],
            "trusted_creator_track_unclear_restored",
        )
        self.assertTrue(guarded["commentary_guard"]["application"]["trusted_unclear_creator_track"])

    def test_commentary_guard_restores_creator_selected_stream(self):
        words = _words_from_tokens(
            "oh great little hole gonna get hit right in the knee".split(),
            start=0.1,
        )
        guarded = evaluate_candidate(
            {
                **_ranker_candidate(),
                "multimodal_analysis": {
                    "primary_visual_label": "lore_or_story",
                    "visual_labels": ["dialogue_scene", "facecam_visible"],
                },
            },
            words,
            extraction_start=0,
            extraction_end=35,
            video_duration=60,
            target_duration=30,
            selected_stream=1,
            quality_floor=0.0,
            commentary_guard=True,
            commentary_guard_policy="creator",
            stream_profile={
                "title": "Track3_vertical",
                "selected_reason": "creator_source_confidence",
                "selected_confidence": 0.724,
                "creator_likeness_score": 0.62,
                "natural_dialogue_score": 3.6,
                "scripted_game_score": 0.25,
                "acoustic_game_bed_score": 0.02,
                "lyric_likelihood": 0.05,
                "voice_title_hints": [],
                "game_title_hints": [],
            },
        )

        self.assertIn("little hole", guarded["moment"]["transcript"])
        self.assertEqual(guarded["moment"]["subtitle_word_count"], len(words))
        self.assertEqual(
            guarded["commentary_guard"]["application"]["reason"],
            "trusted_creator_track_unclear_restored",
        )
        self.assertTrue(guarded["commentary_guard"]["application"]["trusted_unclear_creator_track"])

    def test_commentary_guard_repairs_sparse_late_creator_subtitles(self):
        segments = [
            "oh thank god a flashlight you know how theres the oh candy joke".split(),
            "objective updated manuscript page found checkpoint reached chapter mission goal inventory quest map".split(),
            "and then you just get hit by like ten barrels".split(),
            "oh I wonder if I dont fight them".split(),
            "okay thats not going to work oh thats not going to work oh no".split(),
            "theyre all over me".split(),
            "now how am I supposed to turn that on if theyre just like in front of it".split(),
            "seriously hes got some good aim oh hes got some amazing aim".split(),
        ]
        words = []
        start = 0.1
        for segment in segments:
            part = _words_from_tokens(segment, start=start)
            words.extend(part)
            start = part[-1]["end"] + 1.05

        guarded = evaluate_candidate(
            _ranker_candidate(),
            words,
            extraction_start=0,
            extraction_end=75,
            video_duration=90,
            target_duration=70,
            selected_stream=1,
            quality_floor=0.0,
            commentary_guard=True,
            commentary_guard_policy="creator",
            stream_profile={
                "title": "Track3_vertical",
                "selected_reason": "creator_source_confidence",
                "selected_confidence": 0.724,
                "creator_likeness_score": 0.64,
                "natural_dialogue_score": 3.8,
                "scripted_game_score": 0.25,
                "acoustic_game_bed_score": 0.02,
                "lyric_likelihood": 0.05,
                "voice_title_hints": [],
                "game_title_hints": [],
            },
        )

        transcript = guarded["moment"]["transcript"]
        application = guarded["commentary_guard"]["application"]
        self.assertIn("dont fight them", transcript)
        self.assertIn("thats not going to work", transcript)
        self.assertIn("how am I supposed", transcript)
        self.assertNotIn("objective updated", transcript)
        self.assertGreater(guarded["moment"]["subtitle_word_count"], 7)
        self.assertEqual(application["reason"], "trusted_creator_track_sparse_subtitles_repaired")
        self.assertEqual(application["original_filter_reason"], "creator_subtitle_filter_applied")
        self.assertTrue(application["trusted_unclear_creator_track"])
        self.assertTrue(application["sparse_filtered_subtitles"])

    def test_commentary_guard_does_not_restore_game_speech_from_creator_like_stream(self):
        game_only = _words_from_tokens("objective updated find the key to the door.".split(), start=0.1)
        guarded = evaluate_candidate(
            _ranker_candidate(),
            game_only,
            extraction_start=0,
            extraction_end=35,
            video_duration=60,
            target_duration=30,
            selected_stream=1,
            quality_floor=0.0,
            commentary_guard=True,
            commentary_guard_policy="creator",
            stream_profile={
                "creator_likeness_score": 0.58,
                "natural_dialogue_score": 4.4,
                "scripted_game_score": 0.25,
                "acoustic_game_bed_score": 0.02,
                "lyric_likelihood": 0.05,
                "voice_title_hints": [],
                "game_title_hints": [],
            },
        )

        self.assertEqual(guarded["moment"]["transcript"], "")
        self.assertEqual(
            guarded["commentary_guard"]["application"]["reason"],
            "no_creator_commentary_after_filter",
        )

    def test_commentary_guard_restores_creator_setup_but_drops_read_game_text(self):
        words = _words_from_tokens(
            (
                "Good job. Yes. Going to pat myself on the back. Ooh, piece of candy. "
                "What does this say? The Night Owl. The voice of Pat Main all night, every night. "
                "His picture there does not do him justice. Early bird, start your day right, 7 to 10 a.m."
            ).split(),
            start=0.1,
        )
        guarded = evaluate_candidate(
            _ranker_candidate(),
            words,
            extraction_start=0,
            extraction_end=35,
            video_duration=60,
            target_duration=30,
            selected_stream=1,
            quality_floor=0.0,
            commentary_guard=True,
            commentary_guard_policy="creator",
            stream_profile={
                "creator_likeness_score": 0.76,
                "natural_dialogue_score": 4.4,
                "scripted_game_score": 0.25,
                "acoustic_game_bed_score": 0.02,
                "lyric_likelihood": 0.05,
                "voice_title_hints": [],
                "game_title_hints": [],
            },
        )

        self.assertIn("What does this say", guarded["moment"]["transcript"])
        self.assertIn("pat myself", guarded["moment"]["transcript"])
        self.assertNotIn("The Night Owl", guarded["moment"]["transcript"])
        self.assertNotIn("Pat Main", guarded["moment"]["transcript"])
        self.assertNotIn("Early bird", guarded["moment"]["transcript"])
        self.assertGreater(guarded["moment"]["subtitle_word_count"], 0)
        self.assertLess(guarded["moment"]["subtitle_word_count"], len(words))
        self.assertEqual(
            guarded["commentary_guard"]["application"]["reason"],
            "trusted_creator_track_unclear_segments_restored",
        )
        self.assertGreater(
            guarded["commentary_guard"]["application"]["dropped_after_read_prompt_segments"],
            0,
        )

    def test_commentary_guard_creator_policy_penalizes_game_narration_without_hard_reject(self):
        game_only = _words_from_tokens("objective updated find the key to the door.".split(), start=0.1)
        guarded = _evaluate_with_guard(game_only, policy="creator")

        self.assertTrue(guarded["accepted"])
        self.assertEqual(guarded["reject_reason"], "")
        self.assertGreater(guarded["commentary_guard_selection_penalty"], 0.0)
        self.assertLessEqual(
            guarded["commentary_guard_selection_penalty"],
            COMMENTARY_GUARD_SELECTION_MAX_PENALTY,
        )
        self.assertEqual(guarded["commentary_guard"]["selection_impact"], "quality_penalty")
        self.assertEqual(
            guarded["commentary_guard_selection"]["reason"],
            "high_confidence_game_narration_under_creator_policy",
        )
        self.assertEqual(
            guarded["moment"]["commentary_guard"]["selection"]["selection_impact"],
            "quality_penalty",
        )

    def test_speech_source_classifier_attaches_probabilities_and_penalty(self):
        words = _words_from_tokens(
            "you are required to continue through the corridor now".split(),
            start=0.1,
        )
        guarded = evaluate_candidate(
            _ranker_candidate(),
            words,
            extraction_start=0,
            extraction_end=35,
            video_duration=60,
            target_duration=30,
            selected_stream=0,
            quality_floor=0.0,
            commentary_guard=True,
            commentary_guard_policy="creator",
            stream_profile={
                "creator_likeness_score": 0.18,
                "natural_dialogue_score": 0.4,
                "scripted_game_score": 3.0,
                "acoustic_game_bed_score": 0.76,
                "voice_title_hints": ["microphone"],
                "game_title_hints": [],
            },
            voice_profile={"reason": "scored", "confidence": 0.18, "sample_count": 6},
        )

        source = guarded["speech_source"]
        self.assertEqual(source["primary_source"], "game")
        self.assertGreater(source["game_or_npc_probability"], source["creator_probability"])
        self.assertGreaterEqual(source["selection"]["raw_selection_penalty"], source["selection_penalty"])
        self.assertEqual(guarded["moment"]["speech_source"]["primary_source"], "game")
        self.assertIn("speech_source", guarded["moment"]["ranker"])

    def test_stream_retry_triggers_for_game_or_weak_creator_speech(self):
        game_words = _words_from_tokens(
            "objective updated you must find the key to the door before checkpoint".split(),
            start=0.1,
        )
        npc_words = _words_from_tokens(
            "want it done right you do it yourself bram".split(),
            start=0.1,
        )

        self.assertTrue(
            needs_stream_retry(
                game_words,
                30,
                subtitle_policy="creator",
                commentary_guard=True,
            )
        )
        self.assertTrue(
            needs_stream_retry(
                npc_words,
                30,
                subtitle_policy="creator",
                commentary_guard=True,
            )
        )
        self.assertFalse(
            needs_stream_retry(
                game_words,
                30,
                subtitle_policy="all",
                commentary_guard=True,
            )
        )

    def test_short_commentary_trim_keeps_visual_setup_preroll(self):
        candidate = {
            "start": 4718,
            "end": 4748,
            "duration": 30,
            "peak_time": 4740,
            "candidate_kind": "primary",
        }
        words = _words_from_tokens(
            "i like it how its just her size too like what the hell how convenient".split(),
            start=20.2,
        )

        start, end, render_words = trim_candidate_with_transcript(
            candidate,
            words,
            extraction_start=4718,
            extraction_end=4748,
            video_duration=5000,
            target_duration=30,
        )

        self.assertLessEqual(start, 4734)
        self.assertGreaterEqual(words[0]["start"] + 4718 - start, 4.0)
        self.assertGreater(end, start)
        self.assertGreater(render_words[0]["start"], 0.0)

    def test_shorts_trim_cap_prevents_max_extension_over_180_seconds(self):
        candidate = {
            "start": 0,
            "end": 240,
            "duration": 240,
            "peak_time": 20,
            "candidate_kind": "primary",
        }
        words = [
            {"text": "word", "start": 2.0 + i * 0.25, "end": 2.2 + i * 0.25}
            for i in range(830)
        ]

        start, end, render_words = trim_candidate_with_transcript(
            candidate,
            words,
            extraction_start=0,
            extraction_end=240,
            video_duration=500,
            target_duration=180,
        )

        self.assertEqual(start, 0)
        self.assertLessEqual(end - start, 180)
        self.assertEqual(end, 180)
        self.assertLessEqual(render_words[-1]["start"], 180)

    def test_shorts_trim_cap_applies_without_transcript_words(self):
        candidate = {
            "start": 12.4,
            "end": 260.2,
            "duration": 247.8,
            "peak_time": 120,
            "candidate_kind": "primary",
        }

        start, end, render_words = trim_candidate_with_transcript(
            candidate,
            [],
            extraction_start=12.4,
            extraction_end=260.2,
            video_duration=500,
            target_duration=180,
        )

        self.assertEqual(render_words, [])
        self.assertLessEqual(end - start, 180)

    def test_shorts_trim_cap_applies_to_end_fallback_branch(self):
        candidate = {
            "start": 0,
            "end": 240,
            "duration": 240,
            "peak_time": 20,
            "candidate_kind": "primary",
        }
        words = [{"text": "word", "start": 2.0, "end": 2.2}]

        with patch.object(candidate_ranker, "_natural_end_after", return_value=0.0):
            start, end, render_words = trim_candidate_with_transcript(
                candidate,
                words,
                extraction_start=0,
                extraction_end=240,
                video_duration=500,
                target_duration=220,
            )

        self.assertEqual(start, 0)
        self.assertLessEqual(end - start, 180)
        self.assertEqual(end, 180)
        self.assertEqual(render_words, [])

    def test_commentary_guard_all_and_game_policy_do_not_penalize_game_narration(self):
        game_only = _words_from_tokens("objective updated find the key to the door.".split(), start=0.1)

        for policy in ("all", "game"):
            with self.subTest(policy=policy):
                guarded = _evaluate_with_guard(game_only, policy=policy)
                self.assertEqual(guarded["commentary_guard_selection_penalty"], 0.0)
                self.assertEqual(guarded["commentary_guard"]["selection_impact"], "none")
                self.assertEqual(guarded["commentary_guard_selection"]["reason"], "non_creator_policy")

    def test_commentary_guard_penalty_can_flip_only_close_creator_policy_call(self):
        game_words = _words_from_tokens("objective updated find the key to the door.".split(), start=0.1)
        creator_words = _words_from_tokens("oh my god I need to run please".split(), start=0.1)
        game_eval = _evaluate_with_guard(game_words, policy="creator")
        creator_eval = _evaluate_with_guard(creator_words, policy="creator")
        unpenalized_game_score = 0.60
        game_eval["quality_score"] = unpenalized_game_score - game_eval["commentary_guard_selection_penalty"]
        game_eval["moment"]["quality_score"] = game_eval["quality_score"]
        creator_eval["quality_score"] = 0.585
        creator_eval["moment"]["quality_score"] = 0.585

        selected = select_best_candidates(
            [game_eval, creator_eval],
            1,
            min_gap=8,
            score_key="quality_score",
        )

        self.assertGreater(game_eval["commentary_guard_selection_penalty"], 0.0)
        self.assertGreater(unpenalized_game_score, creator_eval["quality_score"])
        self.assertLess(game_eval["quality_score"], creator_eval["quality_score"])
        self.assertEqual(selected[0]["moment"]["transcript"], creator_eval["moment"]["transcript"])

    def test_debug_report_includes_commentary_guard_selection_penalty(self):
        game_eval = _evaluate_with_guard(
            _words_from_tokens("objective updated find the key to the door.".split(), start=0.1),
            policy="creator",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            out = temp_path / "debug.json"
            write_debug_report(
                out,
                temp_path / "source.mp4",
                [game_eval["candidate"]],
                [game_eval],
                [game_eval],
            )
            payload = json.loads(out.read_text(encoding="utf-8"))

        row = payload["candidates"][0]
        self.assertGreater(row["commentary_guard_selection_penalty"], 0.0)
        self.assertEqual(row["commentary_guard_selection"]["selection_impact"], "quality_penalty")
        self.assertEqual(row["commentary_guard"]["selection"]["selection_impact"], "quality_penalty")

    def test_commentary_guard_filter_keeps_quality_unless_music_lyrics_guard_triggers(self):
        words = _mixed_creator_game_words()
        all_speech = _evaluate_with_guard(words, policy="all")
        creator_only = _evaluate_with_guard(words, policy="creator")

        self.assertEqual(all_speech["quality_score"], creator_only["quality_score"])
        self.assertEqual(all_speech["accepted"], creator_only["accepted"])
        self.assertEqual(normalize_commentary_subtitle_policy("../game"), "creator")

    def test_music_lyrics_guard_rejects_song_only_candidate(self):
        lyric_tokens = (
            "wasted wasted gta love bitches wasted wasted im on these drugs i feel "
            "wasted wasted get her off my mind when im wasted wasted diamonds got "
            "the flu wedding ring necklace codeine hold up hold up hold up"
        ).split()

        direct = classify_music_lyrics_guard(_words_from_tokens(lyric_tokens), policy="creator")
        guarded = _evaluate_with_guard(_words_from_tokens(lyric_tokens), policy="creator")

        self.assertGreaterEqual(direct["lyric_likelihood"], 0.68)
        self.assertFalse(guarded["accepted"])
        self.assertEqual(guarded["reject_reason"], "music_lyrics_not_creator_commentary")
        self.assertGreaterEqual(guarded["music_lyrics_guard"]["lyric_likelihood"], 0.68)
        self.assertGreater(guarded["music_lyrics_penalty"], 0.0)

    def test_music_lyrics_guard_keeps_creator_joke_over_song_context(self):
        words = _words_from_tokens(
            (
                "what the hell is going on im picking rifle batteries and revolver "
                "ammo i think im just going to sing the rest of this no im joking "
                "thats enough singing"
            ).split()
        )

        guarded = _evaluate_with_guard(words, policy="creator")

        self.assertTrue(guarded["accepted"])
        self.assertLess(guarded["music_lyrics_guard"]["lyric_likelihood"], 0.62)
        self.assertGreater(guarded["music_lyrics_guard"]["creator_exception_score"], 0.45)

    def test_select_best_candidates_uses_precomputed_guarded_quality(self):
        high_quality_game = _evaluation(1, 0.9, "objective updated collect the key", start=0)
        high_quality_game["commentary_guard"] = classify_commentary_guard([
            {"text": "objective", "start": 0.0, "end": 0.4},
            {"text": "updated", "start": 0.4, "end": 0.8},
            {"text": "collect", "start": 0.8, "end": 1.1},
            {"text": "the", "start": 1.1, "end": 1.2},
            {"text": "key", "start": 1.2, "end": 1.5},
        ])
        low_quality_creator = _evaluation(2, 0.6, "oh my god I need to run please", start=45)
        low_quality_creator["commentary_guard"] = classify_commentary_guard([
            {"text": "oh", "start": 0.0, "end": 0.2},
            {"text": "my", "start": 0.2, "end": 0.3},
            {"text": "god", "start": 0.3, "end": 0.5},
            {"text": "I", "start": 0.5, "end": 0.6},
            {"text": "need", "start": 0.6, "end": 0.8},
            {"text": "to", "start": 0.8, "end": 0.9},
            {"text": "run", "start": 0.9, "end": 1.1},
            {"text": "please", "start": 1.1, "end": 1.4},
        ])

        high_quality_game["accepted"] = False
        high_quality_game["reject_reason"] = "music_lyrics_not_creator_commentary"
        selected = select_best_candidates(
            [low_quality_creator, high_quality_game],
            1,
            min_gap=8,
            score_key="quality_score",
        )

        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 2)

    def test_commentary_guard_is_not_a_learned_scoring_signal(self):
        evaluation = _evaluation(1, 0.5, "")
        evaluation["moment"]["commentary_guard"] = {
            "summary": {"primary_label": "creator_commentary"},
            "segments": [{"text": "oh my god run please", "label": "creator_commentary"}],
        }
        personalization = {
            "schema_version": 1,
            "clips": {
                "clip_1": {
                    "latest": {"like": True, "dislike": False, "favorite": False, "reason": ""},
                    "clip_snapshot": {
                        "commentary_guard": {
                            "segments": [{"text": "oh my god run please", "label": "creator_commentary"}],
                        }
                    },
                }
            },
            "events": [],
        }

        apply_learned_scoring([evaluation], personalization)
        shadow = evaluation["shadow_scoring"]

        self.assertEqual(shadow["learned_adjustment"], 0.0)
        self.assertEqual(shadow["signals"]["positive_matches"], [])
        self.assertEqual(shadow["signals"]["negative_matches"], [])

    def test_commentary_guard_is_not_used_by_learning_source(self):
        selection_source = inspect.getsource(candidate_ranker.select_best_candidates)
        scoring_source = inspect.getsource(candidate_ranker._score_shadow_candidate)
        feedback_source = inspect.getsource(candidate_ranker._add_feedback_signal)

        for forbidden in (
            "commentary_guard",
            "creator_commentary",
            "game_narration",
            "speech_source",
            "source_confidence",
            "creator_source_confidence",
            "game_source_confidence",
            "game_or_npc_probability",
        ):
            self.assertNotIn(forbidden, scoring_source)
            self.assertNotIn(forbidden, feedback_source)
        self.assertNotIn("music_lyrics_guard", scoring_source)
        self.assertNotIn("music_lyrics_guard", feedback_source)

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

    def test_learning_terms_boost_when_snapshot_transcript_is_missing(self):
        evaluation = _evaluation(1, 0.5, "panic chase right behind me run please")
        personalization = {
            "schema_version": 1,
            "events": [],
            "clips": {
                "clip_1": {
                    "clip_id": "clip_1",
                    "latest": {"like": True, "dislike": False, "favorite": False, "reason": ""},
                    "clip_snapshot": {},
                    "learning_terms": ["panic chase", "right behind", "run please"],
                }
            },
        }

        apply_learned_scoring([evaluation], personalization)

        shadow = evaluation["shadow_scoring"]
        self.assertGreater(shadow["learned_adjustment"], 0.0)
        self.assertGreater(evaluation["learned_quality_score"], 0.5)
        self.assertGreater(shadow["signals"]["positive_points"], 0)

    def test_learning_terms_dislike_lowers_matching_candidate(self):
        evaluation = _evaluation(1, 0.5, "panic chase right behind me run please")
        personalization = {
            "schema_version": 1,
            "events": [],
            "clips": {
                "clip_1": {
                    "clip_id": "clip_1",
                    "latest": {"like": False, "dislike": True, "favorite": False, "reason": ""},
                    "clip_snapshot": {},
                    "learning_terms": ["panic chase", "right behind", "run please"],
                }
            },
        }

        apply_learned_scoring([evaluation], personalization)

        shadow = evaluation["shadow_scoring"]
        self.assertLess(shadow["learned_adjustment"], 0.0)
        self.assertLess(evaluation["learned_quality_score"], 0.5)
        self.assertGreater(shadow["signals"]["negative_points"], 0)

    def test_pairwise_terms_nudge_close_calls_with_same_source_feedback(self):
        evaluation = _evaluation(1, 0.5, "quiet tutorial explanation puzzle route")
        personalization = {
            "schema_version": 1,
            "events": [],
            "clips": {
                "clip_positive": {
                    "clip_id": "clip_positive",
                    "source_id": "src_same",
                    "latest": {"like": True, "dislike": False, "favorite": False, "reason": ""},
                    "clip_snapshot": {},
                    "learning_terms": ["quiet tutorial", "puzzle route"],
                },
                "clip_negative": {
                    "clip_id": "clip_negative",
                    "source_id": "src_same",
                    "latest": {"like": False, "dislike": True, "favorite": False, "reason": ""},
                    "clip_snapshot": {},
                    "learning_terms": ["npc dialogue", "cutscene chatter"],
                },
            },
        }

        apply_learned_scoring([evaluation], personalization)

        shadow = evaluation["shadow_scoring"]
        self.assertGreater(shadow["signals"]["pairwise_adjustment"], 0.0)
        self.assertGreater(shadow["signals"]["pairwise_positive_points"], 0.0)
        self.assertEqual(shadow["signals"]["pairwise_negative_points"], 0.0)
        self.assertLessEqual(abs(shadow["learned_adjustment"]), LEARNED_SELECTION_MAX_ADJUSTMENT)
        report = build_shadow_scoring_report([evaluation], [evaluation], personalization, max_count=1, min_gap=8)
        self.assertEqual(report["profile"]["pairwise_source_count"], 1)
        self.assertGreater(len(report["profile"]["pairwise_positive_terms"]), 0)

    def test_pairwise_terms_require_positive_and_negative_source_examples(self):
        evaluation = _evaluation(1, 0.5, "quiet tutorial explanation puzzle route")
        personalization = {
            "schema_version": 1,
            "events": [],
            "clips": {
                "clip_positive": {
                    "clip_id": "clip_positive",
                    "source_id": "src_same",
                    "latest": {"like": True, "dislike": False, "favorite": False, "reason": ""},
                    "clip_snapshot": {},
                    "learning_terms": ["quiet tutorial", "puzzle route"],
                },
            },
        }

        apply_learned_scoring([evaluation], personalization)

        shadow = evaluation["shadow_scoring"]
        self.assertEqual(shadow["signals"]["pairwise_adjustment"], 0.0)
        self.assertEqual(shadow["signals"]["pairwise_positive_points"], 0)

    def test_run_learning_outcomes_feed_learning_when_personalization_missing(self):
        evaluation = _evaluation(1, 0.5, "quiet tutorial puzzle route")
        run_learning = {
            "schema_version": 1,
            "events": [],
            "clip_outcomes": {
                "clip_positive": {
                    "clip_id": "clip_positive",
                    "source_id": "src_same",
                    "like": True,
                    "learning_terms": ["quiet tutorial", "puzzle route"],
                },
                "clip_negative": {
                    "clip_id": "clip_negative",
                    "source_id": "src_same",
                    "dislike": True,
                    "learning_terms": ["npc dialogue"],
                },
            },
        }

        apply_learned_scoring(
            [evaluation],
            {"schema_version": 1, "events": [], "clips": {}},
            run_learning=run_learning,
        )

        shadow = evaluation["shadow_scoring"]
        self.assertTrue(shadow["learned_selection_enabled"])
        self.assertGreater(shadow["learned_adjustment"], 0.0)
        report = build_shadow_scoring_report(
            [evaluation],
            [evaluation],
            {"schema_version": 1, "events": [], "clips": {}},
            run_learning=run_learning,
            max_count=1,
            min_gap=8,
        )
        self.assertEqual(report["profile"]["run_learning_signal_count"], 2)
        self.assertEqual(report["profile"]["pairwise_source_count"], 1)

    def test_montage_outcomes_feed_learning_and_prompt_context(self):
        evaluation = _evaluation(1, 0.5, "panic chase hook")
        run_learning = {
            "schema_version": 1,
            "events": [],
            "clip_outcomes": {},
            "montage_outcomes": {
                "montage_1": {
                    "storyboard_id": "montage_1",
                    "source_id": "src_same",
                    "like": True,
                    "learning_terms": ["panic chase", "strong hook"],
                    "beat_outcomes": {
                        "beat_1": {
                            "beat_id": "beat_1",
                            "clip_id": "clip_beat_1",
                            "source_id": "src_same",
                            "role": "hook",
                            "category": "high_energy",
                            "like": True,
                            "learning_terms": ["panic chase"],
                        },
                        "beat_2": {
                            "beat_id": "beat_2",
                            "clip_id": "clip_beat_2",
                            "source_id": "src_same",
                            "role": "payoff",
                            "category": "cinematic_dialogue",
                            "dislike": True,
                            "learning_terms": ["npc dialogue"],
                        },
                    },
                }
            },
        }

        apply_learned_scoring(
            [evaluation],
            {"schema_version": 1, "events": [], "clips": {}},
            run_learning=run_learning,
        )

        shadow = evaluation["shadow_scoring"]
        prompt_context = build_learning_prompt_context(
            {"schema_version": 1, "events": [], "clips": {}},
            run_learning=run_learning,
        )
        self.assertTrue(shadow["learned_selection_enabled"])
        self.assertGreater(shadow["learned_adjustment"], 0.0)
        self.assertEqual(prompt_context["montage_learning_signal_count"], 3)
        self.assertIn("panic chase", prompt_context["positive_terms"])
        self.assertIn("npc dialogue", prompt_context["negative_terms"])

    def test_feedback_learning_can_match_ai_and_vision_terms(self):
        evaluation = _evaluation(1, 0.5, "")
        evaluation["moment"]["ai_moment_classification"] = {
            "primary_category": "atmosphere_or_visual",
            "fine_labels": ["beautiful_vista"],
        }
        evaluation["moment"]["multimodal_analysis"] = {
            "primary_visual_label": "atmosphere_or_visual",
            "metadata_keywords": ["beautiful vista", "forest overlook"],
            "visual_labels": ["scenic_view"],
        }
        personalization = {
            "schema_version": 1,
            "events": [],
            "clips": {
                "clip_1": {
                    "clip_id": "clip_1",
                    "latest": {"like": True, "dislike": False, "favorite": False, "reason": ""},
                    "clip_snapshot": {
                        "ai_moment_classification": {
                            "primary_category": "atmosphere_or_visual",
                            "fine_labels": ["beautiful_vista"],
                        },
                        "multimodal_analysis": {
                            "primary_visual_label": "atmosphere_or_visual",
                            "metadata_keywords": ["beautiful vista", "forest overlook"],
                            "visual_labels": ["scenic_view"],
                        },
                    },
                }
            },
        }

        apply_learned_scoring([evaluation], personalization)

        shadow = evaluation["shadow_scoring"]
        self.assertGreater(shadow["signals"]["positive_points"], 0)
        self.assertGreater(shadow["learned_adjustment"], 0.0)

    def test_removed_learning_terms_feedback_no_longer_counts(self):
        evaluation = _evaluation(1, 0.5, "panic chase right behind me run please")
        personalization = {
            "schema_version": 1,
            "events": [
                {
                    "event_type": "like",
                    "active": True,
                    "clip_id": "clip_1",
                    "learning_terms": ["panic chase", "right behind", "run please"],
                }
            ],
            "clips": {
                "clip_1": {
                    "clip_id": "clip_1",
                    "latest": {"like": False, "dislike": False, "favorite": False, "reason": ""},
                    "clip_snapshot": {},
                    "learning_terms": ["panic chase", "right behind", "run please"],
                }
            },
        }

        apply_learned_scoring([evaluation], personalization)

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

    def test_learning_terms_like_to_dislike_flip_becomes_negative_signal(self):
        evaluation = _evaluation(1, 0.5, "panic chase right behind me run please")
        personalization = {
            "schema_version": 1,
            "events": [
                {
                    "event_type": "like",
                    "active": True,
                    "clip_id": "clip_1",
                    "learning_terms": ["panic chase", "right behind", "run please"],
                }
            ],
            "clips": {
                "clip_1": {
                    "clip_id": "clip_1",
                    "latest": {"like": False, "dislike": True, "favorite": False, "reason": ""},
                    "clip_snapshot": {},
                    "learning_terms": ["panic chase", "right behind", "run please"],
                }
            },
        }

        apply_learned_scoring([evaluation], personalization)

        shadow = evaluation["shadow_scoring"]
        self.assertLess(shadow["learned_adjustment"], 0.0)
        self.assertEqual(shadow["signals"]["positive_points"], 0)
        self.assertGreater(shadow["signals"]["negative_points"], 0)

    def test_per_action_like_reason_survives_favorite_without_reason(self):
        evaluation = _evaluation(1, 0.5, "panic chase right behind me run please")
        personalization = {
            "schema_version": 1,
            "events": [],
            "clips": {
                "clip_1": {
                    "clip_id": "clip_1",
                    "latest": {
                        "like": True,
                        "dislike": False,
                        "favorite": True,
                        "reason": "",
                        "reasons": {"like": "panic chase right behind run please"},
                    },
                    "clip_snapshot": {},
                }
            },
        }

        apply_learned_scoring([evaluation], personalization)

        shadow = evaluation["shadow_scoring"]
        self.assertGreater(shadow["learned_adjustment"], 0.0)
        self.assertGreater(shadow["signals"]["positive_points"], 0)

    def test_event_replay_clears_flipped_like_reason(self):
        evaluation = _evaluation(1, 0.5, "panic chase right behind me run please")
        personalization = {
            "schema_version": 1,
            "events": [
                {
                    "event_type": "like",
                    "active": True,
                    "clip_id": "clip_1",
                    "like": True,
                    "dislike": False,
                    "favorite": False,
                    "reason": "panic chase right behind run please",
                },
                {
                    "event_type": "favorite",
                    "active": True,
                    "clip_id": "clip_1",
                    "like": True,
                    "dislike": False,
                    "favorite": True,
                    "reason": "",
                },
                {
                    "event_type": "dislike",
                    "active": True,
                    "clip_id": "clip_1",
                    "like": False,
                    "dislike": True,
                    "favorite": True,
                    "reason": "panic chase wrong kind",
                },
            ],
            "clips": {},
        }

        apply_learned_scoring([evaluation], personalization)

        shadow = evaluation["shadow_scoring"]
        self.assertLess(shadow["learned_adjustment"], 0.0)
        self.assertEqual(shadow["signals"]["positive_points"], 0)
        self.assertGreater(shadow["signals"]["negative_points"], 0)

    def test_empty_clip_summary_does_not_block_event_replay(self):
        evaluation = _evaluation(1, 0.5, "panic chase right behind me run please")
        personalization = {
            "schema_version": 1,
            "events": [
                {
                    "event_type": "like",
                    "active": True,
                    "clip_id": "clip_1",
                    "reason": "panic chase right behind run please",
                }
            ],
            "clips": {"clip_1": {}},
        }

        apply_learned_scoring([evaluation], personalization)

        shadow = evaluation["shadow_scoring"]
        self.assertGreater(shadow["learned_adjustment"], 0.0)
        self.assertGreater(shadow["signals"]["positive_points"], 0)

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

    def test_voice_profile_ranking_is_off_by_default(self):
        plain = _evaluation(1, 0.60, "walking around checking this room", start=0)
        preferred = _evaluation(2, 0.57, "panic chase right behind me run please", start=45)
        plain["voice_profile"] = {"enabled": True, "enrolled": True, "reason": "scored", "confidence": 0.0}
        preferred["voice_profile"] = {"enabled": True, "enrolled": True, "reason": "scored", "confidence": 1.0}
        evaluations = [plain, preferred]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        learned_selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="learned_quality_score")

        result = apply_voice_profile_scoring(
            evaluations,
            {"enabled": True, "enrolled": True, "ranking_enabled": False},
            score_key="learned_quality_score",
        )
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="voice_profile_quality_score")
        report = build_voice_profile_ranking_report(
            evaluations,
            learned_selected,
            selected,
            {"enabled": True, "enrolled": True, "ranking_enabled": False},
            max_count=1,
            min_gap=8,
        )

        self.assertFalse(result["ranking_enabled"])
        self.assertEqual(selected[0]["candidate"]["candidate_rank"], learned_selected[0]["candidate"]["candidate_rank"])
        self.assertEqual(preferred["voice_scoring"]["voice_adjustment"], 0.0)
        self.assertEqual(report["selection_impact"], "none")
        self.assertFalse(report["output_changed"])

    def test_moment_category_ranking_is_off_by_default(self):
        plain = _evaluation(1, 0.60, "walking around checking this room", start=0)
        preferred = _evaluation(2, 0.585, "panic chase right behind me run please", start=45)
        plain["moment_categories"] = {"primary": "low_value", "confidence": 0.9, "scores": {}}
        plain["moment"]["moment_categories"] = plain["moment_categories"]
        preferred["moment_categories"] = {"primary": "high_energy", "confidence": 0.9, "scores": {}}
        preferred["moment"]["moment_categories"] = preferred["moment_categories"]
        evaluations = [plain, preferred]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        learned_selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="learned_quality_score")

        result = apply_moment_category_scoring(evaluations, enabled=False, score_key="learned_quality_score")
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="moment_category_quality_score")
        report = build_moment_category_ranking_report(
            evaluations,
            learned_selected,
            selected,
            enabled=False,
            max_count=1,
            min_gap=8,
        )

        self.assertFalse(result["ranking_enabled"])
        self.assertEqual(selected[0]["candidate"]["candidate_rank"], learned_selected[0]["candidate"]["candidate_rank"])
        self.assertEqual(preferred["moment_category_scoring"]["category_adjustment"], 0.0)
        self.assertEqual(report["selection_impact"], "none")
        self.assertFalse(report["output_changed"])

    def test_visual_and_ai_metadata_do_not_change_selection_when_category_ranking_disabled(self):
        visual_low_value = _evaluation(1, 0.60, "walking around checking this room", start=0)
        ai_preferred = _evaluation(2, 0.585, "panic chase right behind me run please", start=45)
        visual_low_value["visual_diagnostics"] = {
            "schema_version": 1,
            "status": "ok",
            "visual_energy": 0.0,
            "black_frame_ratio": 1.0,
            "labels": ["black_frame"],
        }
        visual_low_value["candidate"]["visual_diagnostics"] = visual_low_value["visual_diagnostics"]
        visual_low_value["moment"]["visual_diagnostics"] = visual_low_value["visual_diagnostics"]
        visual_low_value["moment_categories"] = {
            "primary": "low_value",
            "confidence": 0.95,
            "scores": {"low_value": 0.92},
        }
        visual_low_value["moment"]["moment_categories"] = visual_low_value["moment_categories"]
        ai_preferred["visual_diagnostics"] = {
            "schema_version": 1,
            "status": "ok",
            "visual_energy": 0.9,
            "possible_failure_score": 0.8,
            "labels": ["high_motion", "possible_failure_screen"],
        }
        ai_preferred["candidate"]["visual_diagnostics"] = ai_preferred["visual_diagnostics"]
        ai_preferred["moment"]["visual_diagnostics"] = ai_preferred["visual_diagnostics"]
        ai_preferred["moment_categories"] = {
            "primary": "high_energy",
            "confidence": 0.93,
            "scores": {"high_energy": 0.88},
        }
        ai_preferred["moment"]["moment_categories"] = ai_preferred["moment_categories"]
        attach_ai_moment_classification(visual_low_value, {
            "status": "ok",
            "provider": "ollama",
            "primary_category": "low_value",
            "confidence": 0.95,
            "selection_impact": "capped_rank_adjustment",
            "output_changed": True,
        })
        attach_ai_moment_classification(ai_preferred, {
            "status": "ok",
            "provider": "ollama",
            "primary_category": "high_energy",
            "confidence": 0.95,
            "selection_impact": "capped_rank_adjustment",
            "output_changed": True,
        })
        evaluations = [visual_low_value, ai_preferred]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        learned_selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="learned_quality_score")

        result = apply_moment_category_scoring(evaluations, enabled=False, score_key="learned_quality_score")
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="moment_category_quality_score")
        report = build_moment_category_ranking_report(
            evaluations,
            learned_selected,
            selected,
            enabled=False,
            max_count=1,
            min_gap=8,
        )

        self.assertFalse(result["ranking_enabled"])
        self.assertEqual(learned_selected[0]["candidate"]["candidate_rank"], 1)
        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 1)
        self.assertEqual(selected[0]["candidate"]["candidate_rank"], learned_selected[0]["candidate"]["candidate_rank"])
        self.assertEqual(visual_low_value["moment_category_scoring"]["category_adjustment"], 0.0)
        self.assertEqual(ai_preferred["moment_category_scoring"]["category_adjustment"], 0.0)
        self.assertEqual(visual_low_value["ai_moment_classification"]["selection_impact"], "none")
        self.assertFalse(ai_preferred["ai_moment_classification"]["output_changed"])
        self.assertEqual(report["selection_impact"], "none")
        self.assertEqual(report["selection_score_source"], "learned_quality_score")
        self.assertFalse(report["output_changed"])

    def test_moment_category_ranking_can_flip_close_call_when_opted_in(self):
        plain = _evaluation(1, 0.60, "where am i all the way back", start=0)
        preferred = _evaluation(2, 0.585, "panic chase right behind me run please", start=45)
        plain["moment_categories"] = {"primary": "low_value", "confidence": 0.9, "scores": {}}
        plain["moment"]["moment_categories"] = plain["moment_categories"]
        preferred["moment_categories"] = {"primary": "high_energy", "confidence": 0.9, "scores": {}}
        preferred["moment"]["moment_categories"] = preferred["moment_categories"]
        evaluations = [plain, preferred]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        learned_selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="learned_quality_score")

        result = apply_moment_category_scoring(evaluations, enabled=True, score_key="learned_quality_score")
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="moment_category_quality_score")
        report = build_moment_category_ranking_report(
            evaluations,
            learned_selected,
            selected,
            enabled=True,
            max_count=1,
            min_gap=8,
        )

        self.assertTrue(result["ranking_enabled"])
        self.assertEqual(learned_selected[0]["candidate"]["candidate_rank"], 1)
        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 2)
        self.assertEqual(selected[0]["selection_score_source"], "moment_category_quality_score")
        self.assertLessEqual(abs(preferred["moment_category_scoring"]["category_adjustment"]), MOMENT_CATEGORY_SELECTION_MAX_ADJUSTMENT)
        self.assertAlmostEqual(preferred["moment_category_scoring"]["category_adjustment"], MOMENT_CATEGORY_SELECTION_MAX_ADJUSTMENT)
        self.assertAlmostEqual(plain["moment_category_scoring"]["category_adjustment"], -MOMENT_CATEGORY_SELECTION_MAX_ADJUSTMENT)
        self.assertEqual(report["selection_impact"], "capped_rank_adjustment")
        self.assertTrue(report["output_changed"])
        self.assertEqual(preferred["moment_category_scoring"]["selection_delta"], "added_by_category")
        self.assertEqual(plain["moment_category_scoring"]["selection_delta"], "dropped_by_category")

    def test_moment_category_report_falls_back_source_when_no_usable_category_scores(self):
        first = _evaluation(1, 0.60, "walking around checking this room", start=0)
        second = _evaluation(2, 0.57, "checking another quiet hallway", start=45)
        for row in (first, second):
            row["moment_categories"] = {"primary": "commentary_or_review", "confidence": 0.9, "scores": {}}
            row["moment"]["moment_categories"] = row["moment_categories"]
        evaluations = [first, second]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        learned_selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="learned_quality_score")
        apply_moment_category_scoring(evaluations, enabled=True, score_key="learned_quality_score")
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="moment_category_quality_score")

        report = build_moment_category_ranking_report(
            evaluations,
            learned_selected,
            selected,
            enabled=True,
            max_count=1,
            min_gap=8,
        )

        self.assertTrue(report["ranking_enabled"])
        self.assertFalse(report["has_category_scores"])
        self.assertEqual(report["selection_score_source"], "learned_quality_score")

    def test_moment_category_ranking_does_not_overpower_large_quality_gap(self):
        strong = _evaluation(1, 0.70, "where am i all the way back", start=0)
        weak_category = _evaluation(2, 0.60, "panic chase right behind me run please", start=45)
        strong["moment_categories"] = {"primary": "low_value", "confidence": 0.9, "scores": {}}
        strong["moment"]["moment_categories"] = strong["moment_categories"]
        weak_category["moment_categories"] = {"primary": "high_energy", "confidence": 0.9, "scores": {}}
        weak_category["moment"]["moment_categories"] = weak_category["moment_categories"]
        evaluations = [strong, weak_category]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        learned_selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="learned_quality_score")
        apply_moment_category_scoring(evaluations, enabled=True, score_key="learned_quality_score")

        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="moment_category_quality_score")
        report = build_moment_category_ranking_report(
            evaluations,
            learned_selected,
            selected,
            enabled=True,
            max_count=1,
            min_gap=8,
        )

        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 1)
        self.assertFalse(report["output_changed"])
        self.assertGreater(strong["moment_category_quality_score"], weak_category["moment_category_quality_score"])

    def test_death_or_failure_category_can_nudge_close_call_when_opted_in(self):
        plain = _evaluation(1, 0.60, "walking around checking this room", start=0)
        failure = _evaluation(2, 0.585, "oh no he got me right there", start=45)
        failure_categories = score_moment_categories(
            failure["transcript"],
            {
                "detector_scores": {"audio": 0.8, "variance": 0.7, "scene": 0.0},
                "visual_diagnostics": {"possible_failure_score": 1.0, "red_flash_score": 1.0},
            },
            hook_points=4,
            word_count=8,
            duration=20,
        )
        self.assertEqual(failure_categories["primary"], "death_or_failure")
        plain["moment_categories"] = {"primary": "commentary_or_review", "confidence": 0.9, "scores": {}}
        plain["moment"]["moment_categories"] = plain["moment_categories"]
        failure["moment_categories"] = failure_categories
        failure["moment"]["moment_categories"] = failure_categories
        evaluations = [plain, failure]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        learned_selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="learned_quality_score")
        apply_moment_category_scoring(evaluations, enabled=True, score_key="learned_quality_score")

        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="moment_category_quality_score")
        report = build_moment_category_ranking_report(
            evaluations,
            learned_selected,
            selected,
            enabled=True,
            max_count=1,
            min_gap=8,
        )

        self.assertEqual(learned_selected[0]["candidate"]["candidate_rank"], 1)
        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 2)
        self.assertTrue(report["output_changed"])

    def test_category_diversity_can_nudge_close_underrepresented_explainer(self):
        high_one = _evaluation(1, 0.620, "panic chase right behind me run please", start=0)
        high_two = _evaluation(2, 0.610, "oh my god run he is right behind me", start=45)
        explainer = _evaluation(3, 0.608, "here is how you use the flashlight battery and revolver ammo", start=90)
        for row in (high_one, high_two):
            row["moment_categories"] = {"primary": "high_energy", "confidence": 0.9, "scores": {}}
            row["moment"]["moment_categories"] = row["moment_categories"]
        explainer["moment_categories"] = {"primary": "tutorial_or_explainer", "confidence": 0.9, "scores": {}}
        explainer["moment"]["moment_categories"] = explainer["moment_categories"]
        evaluations = [high_one, high_two, explainer]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        learned_selected = select_best_candidates(evaluations, 2, min_gap=8, score_key="learned_quality_score")

        result = apply_moment_category_scoring(
            evaluations,
            enabled=True,
            score_key="learned_quality_score",
            max_count=2,
            min_gap=8,
        )
        selected = select_best_candidates(evaluations, 2, min_gap=8, score_key="moment_category_quality_score")
        report = build_moment_category_ranking_report(
            evaluations,
            learned_selected,
            selected,
            enabled=True,
            max_count=2,
            min_gap=8,
        )

        self.assertTrue(result["ranking_enabled"])
        self.assertEqual([row["candidate"]["candidate_rank"] for row in learned_selected], [1, 2])
        self.assertIn(3, [row["candidate"]["candidate_rank"] for row in selected])
        self.assertGreater(explainer["moment_category_scoring"]["category_diversity_adjustment"], 0.0)
        self.assertGreaterEqual(report["diversity_candidate_count"], 1)

    def test_ai_moment_ranking_can_flip_close_deep_call(self):
        plain = _evaluation(1, 0.600, "walking around checking this room", start=0)
        preferred = _evaluation(2, 0.590, "panic chase right behind me run please", start=45)
        attach_ai_moment_classification(plain, {
            "status": "ok",
            "provider": "ollama",
            "primary_category": "low_value",
            "confidence": 0.92,
            "ai_confidence": 0.92,
            "ai_viral_score": 28,
            "ai_dimensions": {"hook": 0.2, "flow": 0.3, "value": 0.2, "platform_fit": 0.3, "game_context": 0.3},
        })
        attach_ai_moment_classification(preferred, {
            "status": "ok",
            "provider": "ollama",
            "primary_category": "high_energy",
            "confidence": 0.92,
            "ai_confidence": 0.92,
            "ai_viral_score": 88,
            "ai_dimensions": {"hook": 0.95, "flow": 0.8, "value": 0.85, "platform_fit": 0.9, "game_context": 0.9},
        })
        evaluations = [plain, preferred]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        baseline = select_best_candidates(evaluations, 1, min_gap=8, score_key="learned_quality_score")

        result = apply_ai_moment_scoring(evaluations, enabled=True, score_key="learned_quality_score")
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="ai_moment_quality_score")
        report = build_ai_moment_ranking_report(
            evaluations,
            baseline,
            selected,
            enabled=True,
            max_count=1,
            min_gap=8,
            score_key="learned_quality_score",
        )

        self.assertTrue(result["ranking_enabled"])
        self.assertEqual(baseline[0]["candidate"]["candidate_rank"], 1)
        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 2)
        self.assertEqual(selected[0]["selection_score_source"], "ai_moment_quality_score")
        self.assertLessEqual(abs(preferred["ai_moment_scoring"]["ai_adjustment"]), AI_MOMENT_SELECTION_MAX_ADJUSTMENT)
        self.assertEqual(report["selection_impact"], "capped_rank_adjustment")
        self.assertTrue(report["output_changed"])
        self.assertEqual(preferred["ai_moment_scoring"]["selection_delta"], "added_by_ai")
        self.assertEqual(plain["ai_moment_scoring"]["selection_delta"], "dropped_by_ai")

    def test_ai_moment_ranking_requires_real_high_confidence_ollama_label(self):
        plain = _evaluation(1, 0.600, "walking around checking this room", start=0)
        heuristic = _evaluation(2, 0.590, "panic chase right behind me run please", start=45)
        attach_ai_moment_classification(heuristic, {
            "status": "ok",
            "provider": "heuristic",
            "primary_category": "high_energy",
            "confidence": 0.95,
            "ai_confidence": 0.95,
            "ai_viral_score": 95,
            "ai_dimensions": {"hook": 1.0, "flow": 1.0, "value": 1.0, "platform_fit": 1.0, "game_context": 1.0},
        })
        evaluations = [plain, heuristic]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        baseline = select_best_candidates(evaluations, 1, min_gap=8, score_key="learned_quality_score")

        result = apply_ai_moment_scoring(evaluations, enabled=True, score_key="learned_quality_score")
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="ai_moment_quality_score")
        report = build_ai_moment_ranking_report(
            evaluations,
            baseline,
            selected,
            enabled=True,
            max_count=1,
            min_gap=8,
            score_key="learned_quality_score",
        )

        self.assertFalse(result["has_ai_scores"])
        self.assertEqual(selected[0]["candidate"]["candidate_rank"], baseline[0]["candidate"]["candidate_rank"])
        self.assertEqual(heuristic["ai_moment_scoring"]["ai_adjustment"], 0.0)
        self.assertEqual(heuristic["ai_moment_scoring"]["ai_ineligible_reason"], "not_ollama")
        self.assertEqual(report["selection_score_source"], "learned_quality_score")

    def test_ai_moment_scoring_can_influence_marked_near_miss(self):
        near_miss = _evaluation(7, 0.470, "I think this scene is actually useful commentary", start=90)
        near_miss["accepted"] = False
        near_miss["reject_reason"] = "low_transcript_quality"
        near_miss["quality_floor"] = 0.50
        near_miss["ai_rescue_candidate"] = True
        near_miss["moment"]["ranker"]["reject_reason"] = "low_transcript_quality"
        attach_ai_moment_classification(near_miss, {
            "status": "ok",
            "provider": "ollama",
            "primary_category": "commentary_or_review",
            "confidence": 0.91,
            "ai_confidence": 0.91,
            "ai_viral_score": 78,
            "ai_dimensions": {"hook": 0.55, "flow": 0.8, "value": 0.9, "platform_fit": 0.75, "game_context": 0.7},
        })

        result = apply_ai_moment_scoring([near_miss], enabled=True, score_key="quality_score")

        self.assertTrue(result["has_ai_scores"])
        self.assertGreater(near_miss["ai_moment_scoring"]["ai_adjustment"], 0.0)
        self.assertLessEqual(
            abs(near_miss["ai_moment_scoring"]["ai_adjustment"]),
            AI_MOMENT_SELECTION_MAX_ADJUSTMENT,
        )
        self.assertFalse(near_miss["accepted"])

    def test_multi_signal_ai_blend_can_select_stronger_ai_vision_candidate(self):
        plain = _evaluation(1, 0.620, "walking around checking this room", start=0)
        preferred = _evaluation(2, 0.590, "panic chase right behind me run please", start=45)
        for row in (plain, preferred):
            row["candidate"]["detector_scores"] = {"audio": 0.65, "variance": 0.55, "scene": 0.35}
            row["moment"]["detector_scores"] = row["candidate"]["detector_scores"]
        plain["shadow_scoring"] = {"learned_adjustment": -0.06, "learned_selection_cap": 0.06}
        preferred["shadow_scoring"] = {"learned_adjustment": 0.06, "learned_selection_cap": 0.06}
        plain["multimodal_scoring"] = {
            "ranking_enabled": True,
            "scoring_eligible": True,
            "multimodal_adjustment": -0.02,
            "multimodal_selection_max_adjustment": 0.02,
            "primary_visual_label": "low_value",
        }
        preferred["multimodal_scoring"] = {
            "ranking_enabled": True,
            "scoring_eligible": True,
            "multimodal_adjustment": 0.02,
            "multimodal_selection_max_adjustment": 0.02,
            "primary_visual_label": "high_energy",
        }
        plain["moment_category_scoring"] = {
            "ranking_enabled": True,
            "category_signal": -1.0,
            "category_diversity_adjustment": 0.0,
            "category_diversity_cap": 0.006,
        }
        preferred["moment_category_scoring"] = {
            "ranking_enabled": True,
            "category_signal": 1.0,
            "category_diversity_adjustment": 0.006,
            "category_diversity_cap": 0.006,
        }
        plain["voice_scoring"] = {"ranking_enabled": True, "voice_reason": "scored", "voice_confidence": 0.0}
        preferred["voice_scoring"] = {"ranking_enabled": True, "voice_reason": "scored", "voice_confidence": 1.0}
        attach_ai_moment_classification(plain, {
            "status": "ok",
            "provider": "ollama",
            "primary_category": "low_value",
            "confidence": 0.95,
            "ai_confidence": 0.95,
            "ai_viral_score": 24,
            "ai_dimensions": {"hook": 0.15, "flow": 0.2, "value": 0.1, "platform_fit": 0.2, "game_context": 0.2},
        })
        attach_ai_moment_classification(preferred, {
            "status": "ok",
            "provider": "ollama",
            "primary_category": "high_energy",
            "confidence": 0.95,
            "ai_confidence": 0.95,
            "ai_viral_score": 92,
            "ai_dimensions": {"hook": 0.95, "flow": 0.9, "value": 0.8, "platform_fit": 0.9, "game_context": 0.85},
        })
        evaluations = [plain, preferred]
        baseline = select_best_candidates(evaluations, 1, min_gap=8, score_key="quality_score")

        result = apply_multi_signal_ai_scoring(evaluations, enabled=True)
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="multi_signal_ai_quality_score")
        report = build_multi_signal_ai_ranking_report(
            evaluations,
            baseline,
            selected,
            enabled=True,
            max_count=1,
            min_gap=8,
        )

        self.assertTrue(result["ranking_enabled"])
        self.assertEqual(baseline[0]["candidate"]["candidate_rank"], 1)
        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 2)
        self.assertEqual(selected[0]["selection_score_source"], "multi_signal_ai_quality_score")
        self.assertLessEqual(
            preferred["multi_signal_ai_scoring"]["multi_signal_adjustment"],
            MULTI_SIGNAL_AI_MAX_POSITIVE_ADJUSTMENT,
        )
        self.assertGreaterEqual(
            plain["multi_signal_ai_scoring"]["multi_signal_adjustment"],
            -MULTI_SIGNAL_AI_MAX_NEGATIVE_ADJUSTMENT,
        )
        self.assertTrue(report["output_changed"])
        self.assertEqual(preferred["multi_signal_ai_scoring"]["selection_delta"], "added_by_multi_signal_ai")
        self.assertEqual(plain["multi_signal_ai_scoring"]["selection_delta"], "dropped_by_multi_signal_ai")

    def test_multi_signal_ai_does_not_rescue_music_guard_rejections(self):
        candidate = _evaluation(1, 0.500, "song lyric sounding speech with music", start=0)
        candidate["shadow_scoring"] = {"learned_adjustment": 0.06, "learned_selection_cap": 0.06}
        candidate["multimodal_scoring"] = {
            "ranking_enabled": True,
            "scoring_eligible": True,
            "multimodal_adjustment": 0.02,
            "multimodal_selection_max_adjustment": 0.02,
            "primary_visual_label": "high_energy",
        }
        candidate["voice_scoring"] = {"ranking_enabled": True, "voice_reason": "scored", "voice_confidence": 1.0}
        candidate["music_lyrics_guard"] = {"reject_candidate": True}
        candidate["moment"]["music_lyrics_guard"] = candidate["music_lyrics_guard"]
        attach_ai_moment_classification(candidate, {
            "status": "ok",
            "provider": "ollama",
            "primary_category": "high_energy",
            "confidence": 0.95,
            "ai_confidence": 0.95,
            "ai_viral_score": 95,
            "ai_dimensions": {"hook": 1.0, "flow": 1.0, "value": 1.0, "platform_fit": 1.0, "game_context": 1.0},
        })

        apply_multi_signal_ai_scoring([candidate], enabled=True)

        scoring = candidate["multi_signal_ai_scoring"]
        self.assertEqual(scoring["blocked_positive_reason"], "music_lyrics_guard_rejected")
        self.assertLessEqual(scoring["multi_signal_adjustment"], 0.0)
        self.assertEqual(scoring["multi_signal_ai_quality_score"], 0.5)

    def test_multi_signal_ai_blocks_positive_boost_for_game_narration_guard(self):
        candidate = _evaluation(1, 0.500, "objective updated find the key to the door", start=0)
        guard = classify_commentary_guard(
            _words_from_tokens("objective updated find the key to the door".split()),
            enabled=True,
        )
        guard["policy"] = "creator"
        guard["selection"] = {
            "policy": "creator",
            "selection_penalty": 0.04,
            "selection_impact": "quality_penalty",
        }
        candidate["commentary_guard"] = guard
        candidate["moment"]["commentary_guard"] = guard
        candidate["shadow_scoring"] = {"learned_adjustment": 0.06, "learned_selection_cap": 0.06}
        candidate["multimodal_scoring"] = {
            "ranking_enabled": True,
            "scoring_eligible": True,
            "multimodal_adjustment": 0.02,
            "multimodal_selection_max_adjustment": 0.02,
            "primary_visual_label": "lore_or_story",
        }
        candidate["voice_scoring"] = {"ranking_enabled": True, "voice_reason": "scored", "voice_confidence": 1.0}
        attach_ai_moment_classification(candidate, {
            "status": "ok",
            "provider": "ollama",
            "primary_category": "lore_or_story",
            "confidence": 0.95,
            "ai_confidence": 0.95,
            "ai_viral_score": 90,
            "ai_dimensions": {"hook": 0.9, "flow": 0.9, "value": 0.8, "platform_fit": 0.9, "game_context": 0.9},
        })

        apply_multi_signal_ai_scoring([candidate], enabled=True)

        scoring = candidate["multi_signal_ai_scoring"]
        self.assertEqual(scoring["blocked_positive_reason"], "commentary_guard_game_narration")
        self.assertEqual(scoring["multi_signal_adjustment"], 0.0)
        self.assertEqual(scoring["multi_signal_ai_quality_score"], 0.5)

    def test_multi_signal_ai_penalizes_ai_game_narration_label(self):
        candidate = _evaluation(1, 0.500, "npc dialogue that sounds like a first person story", start=0)
        candidate["shadow_scoring"] = {"learned_adjustment": 0.06, "learned_selection_cap": 0.06}
        candidate["multimodal_scoring"] = {
            "ranking_enabled": True,
            "scoring_eligible": True,
            "multimodal_adjustment": 0.02,
            "multimodal_selection_max_adjustment": 0.02,
            "primary_visual_label": "lore_or_story",
        }
        attach_ai_moment_classification(candidate, {
            "status": "ok",
            "provider": "ollama",
            "primary_category": "tutorial_or_explainer",
            "fine_labels": ["game_narration"],
            "confidence": 0.95,
            "ai_confidence": 0.95,
            "ai_viral_score": 88,
            "ai_dimensions": {"hook": 0.9, "flow": 0.8, "value": 0.7, "platform_fit": 0.8, "game_context": 0.9},
        })

        apply_multi_signal_ai_scoring([candidate], enabled=True)

        scoring = candidate["multi_signal_ai_scoring"]
        self.assertEqual(scoring["blocked_positive_reason"], "ai_game_narration")
        self.assertLess(scoring["signals"]["text_ai"], 0.0)
        self.assertLessEqual(scoring["multi_signal_adjustment"], 0.0)

    def test_multi_signal_ai_blocks_positive_boost_for_source_confidence(self):
        candidate = _evaluation(1, 0.500, "you are required to continue through the corridor now", start=0)
        source = {
            "policy": "creator",
            "primary_source": "game",
            "confidence": 0.70,
            "creator_probability": 0.22,
            "game_or_npc_probability": 0.58,
            "music_or_lyrics_probability": 0.04,
            "unknown_probability": 0.16,
            "creator_safe": False,
        }
        candidate["speech_source"] = source
        candidate["moment"]["speech_source"] = source
        candidate["shadow_scoring"] = {"learned_adjustment": 0.06, "learned_selection_cap": 0.06}
        candidate["multimodal_scoring"] = {
            "ranking_enabled": True,
            "scoring_eligible": True,
            "multimodal_adjustment": 0.02,
            "multimodal_selection_max_adjustment": 0.02,
            "primary_visual_label": "high_energy",
        }
        candidate["voice_scoring"] = {"ranking_enabled": True, "voice_reason": "scored", "voice_confidence": 1.0}
        attach_ai_moment_classification(candidate, {
            "status": "ok",
            "provider": "ollama",
            "primary_category": "high_energy",
            "confidence": 0.95,
            "ai_confidence": 0.95,
            "ai_viral_score": 92,
            "ai_dimensions": {"hook": 0.95, "flow": 0.9, "value": 0.8, "platform_fit": 0.9, "game_context": 0.85},
        })

        apply_multi_signal_ai_scoring([candidate], enabled=True)

        scoring = candidate["multi_signal_ai_scoring"]
        self.assertEqual(scoring["blocked_positive_reason"], "speech_source_game_or_npc")
        self.assertEqual(scoring["multi_signal_adjustment"], 0.0)
        self.assertEqual(scoring["multi_signal_ai_quality_score"], 0.5)

    def test_multi_signal_ai_uses_requested_base_score_source(self):
        candidate = _evaluation(1, 0.500, "quiet but clearly useful explanation", start=0)
        candidate["learned_quality_score"] = 0.72
        candidate["moment"]["learned_quality_score"] = 0.72

        result = apply_multi_signal_ai_scoring(
            [candidate],
            enabled=True,
            score_key="learned_quality_score",
        )

        scoring = candidate["multi_signal_ai_scoring"]
        self.assertEqual(result["score_source"], "learned_quality_score")
        self.assertEqual(scoring["score_source"], "learned_quality_score")
        self.assertEqual(scoring["base_score"], 0.72)
        self.assertEqual(scoring["multi_signal_ai_quality_score"], 0.72)

    def test_voice_profile_ranking_can_flip_close_call_when_opted_in(self):
        plain = _evaluation(1, 0.60, "walking around checking this room", start=0)
        preferred = _evaluation(2, 0.57, "panic chase right behind me run please", start=45)
        plain["voice_profile"] = {"enabled": True, "enrolled": True, "reason": "scored", "confidence": 0.0}
        preferred["voice_profile"] = {"enabled": True, "enrolled": True, "reason": "scored", "confidence": 1.0}
        evaluations = [plain, preferred]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        learned_selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="learned_quality_score")

        result = apply_voice_profile_scoring(
            evaluations,
            {"enabled": True, "enrolled": True, "ranking_enabled": True},
            score_key="learned_quality_score",
        )
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="voice_profile_quality_score")
        report = build_voice_profile_ranking_report(
            evaluations,
            learned_selected,
            selected,
            {"enabled": True, "enrolled": True, "ranking_enabled": True, "ranking_active": True},
            max_count=1,
            min_gap=8,
        )

        self.assertTrue(result["ranking_enabled"])
        self.assertEqual(learned_selected[0]["candidate"]["candidate_rank"], 1)
        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 2)
        self.assertEqual(selected[0]["selection_score_source"], "voice_profile_quality_score")
        self.assertLessEqual(abs(preferred["voice_scoring"]["voice_adjustment"]), VOICE_PROFILE_SELECTION_MAX_ADJUSTMENT)
        self.assertAlmostEqual(preferred["voice_scoring"]["voice_adjustment"], VOICE_PROFILE_SELECTION_MAX_ADJUSTMENT)
        self.assertAlmostEqual(plain["voice_scoring"]["voice_adjustment"], -VOICE_PROFILE_SELECTION_MAX_ADJUSTMENT)
        self.assertEqual(report["selection_impact"], "capped_rank_adjustment")
        self.assertTrue(report["output_changed"])
        self.assertEqual(preferred["voice_scoring"]["selection_delta"], "added_by_voice")
        self.assertEqual(plain["voice_scoring"]["selection_delta"], "dropped_by_voice")

    def test_voice_profile_ranking_does_not_overpower_large_quality_gap(self):
        strong = _evaluation(1, 0.70, "solid clip with clean context", start=0)
        weak_voice = _evaluation(2, 0.57, "panic chase right behind me run please", start=45)
        strong["voice_profile"] = {"enabled": True, "enrolled": True, "reason": "scored", "confidence": 0.0}
        weak_voice["voice_profile"] = {"enabled": True, "enrolled": True, "reason": "scored", "confidence": 1.0}
        evaluations = [strong, weak_voice]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        learned_selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="learned_quality_score")
        apply_voice_profile_scoring(
            evaluations,
            {"enabled": True, "enrolled": True, "ranking_enabled": True},
            score_key="learned_quality_score",
        )

        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="voice_profile_quality_score")
        report = build_voice_profile_ranking_report(
            evaluations,
            learned_selected,
            selected,
            {"enabled": True, "enrolled": True, "ranking_enabled": True, "ranking_active": True},
            max_count=1,
            min_gap=8,
        )

        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 1)
        self.assertFalse(report["output_changed"])
        self.assertGreater(strong["voice_profile_quality_score"], weak_voice["voice_profile_quality_score"])

    def test_voice_profile_ranking_ignores_malformed_or_unscored_voice(self):
        plain = _evaluation(1, 0.60, "walking around checking this room", start=0)
        preferred = _evaluation(2, 0.57, "panic chase right behind me run please", start=45)
        plain["voice_profile"] = {"enabled": True, "enrolled": True, "reason": "scored", "confidence": "nan"}
        preferred["voice_profile"] = {"enabled": True, "enrolled": True, "reason": "no_features", "confidence": 1.0}
        evaluations = [plain, preferred]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        learned_selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="learned_quality_score")

        result = apply_voice_profile_scoring(
            evaluations,
            {"enabled": True, "enrolled": True, "ranking_enabled": True},
            score_key="learned_quality_score",
            max_adjustment="../bad",
        )
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="voice_profile_quality_score")
        report = build_voice_profile_ranking_report(
            evaluations,
            learned_selected,
            selected,
            {"enabled": True, "enrolled": True, "ranking_enabled": True, "ranking_active": True},
            max_count=1,
            min_gap=8,
            max_adjustment="../bad",
        )

        self.assertFalse(result["ranking_enabled"])
        self.assertEqual(selected[0]["candidate"]["candidate_rank"], learned_selected[0]["candidate"]["candidate_rank"])
        self.assertEqual(plain["voice_scoring"]["voice_adjustment"], 0.0)
        self.assertEqual(preferred["voice_scoring"]["voice_adjustment"], 0.0)
        self.assertFalse(report["has_voice_profile_scores"])
        self.assertEqual(report["selection_score_source"], "learned_quality_score")

    def test_voice_profile_report_falls_back_source_when_no_usable_voice_scores(self):
        plain = _evaluation(1, 0.60, "walking around checking this room", start=0)
        preferred = _evaluation(2, 0.57, "panic chase right behind me run please", start=45)
        plain["voice_profile"] = {"enabled": True, "enrolled": True, "reason": "no_features", "confidence": 0.99}
        preferred["voice_profile"] = {"enabled": True, "enrolled": True, "reason": "not_enough_active_voice", "confidence": None}
        evaluations = [plain, preferred]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        learned_selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="learned_quality_score")
        apply_voice_profile_scoring(
            evaluations,
            {"enabled": True, "enrolled": True, "ranking_enabled": True},
            score_key="learned_quality_score",
        )
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="voice_profile_quality_score")

        report = build_voice_profile_ranking_report(
            evaluations,
            learned_selected,
            selected,
            {"enabled": True, "enrolled": True, "ranking_enabled": True, "ranking_active": True},
            max_count=1,
            min_gap=8,
        )

        self.assertFalse(report["ranking_enabled"])
        self.assertFalse(report["has_voice_profile_scores"])
        self.assertEqual(report["selection_score_source"], "learned_quality_score")

    def test_voice_profile_ranking_requires_enough_scored_candidates(self):
        plain = _evaluation(1, 0.60, "walking around checking this room", start=0)
        preferred = _evaluation(2, 0.57, "panic chase right behind me run please", start=45)
        preferred["voice_profile"] = {"enabled": True, "enrolled": True, "reason": "scored", "confidence": 1.0}
        evaluations = [plain, preferred]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        learned_selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="learned_quality_score")

        result = apply_voice_profile_scoring(
            evaluations,
            {"enabled": True, "enrolled": True, "can_score": True, "ranking_enabled": True},
            score_key="learned_quality_score",
        )
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="voice_profile_quality_score")
        report = build_voice_profile_ranking_report(
            evaluations,
            learned_selected,
            selected,
            {"enabled": True, "enrolled": True, "can_score": True, "ranking_enabled": True},
            max_count=1,
            min_gap=8,
        )

        self.assertFalse(result["ranking_enabled"])
        self.assertFalse(result["has_voice_profile_scores"])
        self.assertEqual(result["disabled_reason"], "insufficient_scored_candidates")
        self.assertEqual(selected[0]["candidate"]["candidate_rank"], learned_selected[0]["candidate"]["candidate_rank"])
        self.assertEqual(report["selection_score_source"], "learned_quality_score")

    def test_learned_scoring_tolerates_malformed_quality_values(self):
        malformed = _evaluation(1, "not-a-score", "panic chase right behind me", start=0)
        normal = _evaluation(2, 0.57, "solid creator commentary", start=45)
        evaluations = [malformed, normal]

        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="learned_quality_score")
        report = build_shadow_scoring_report(
            evaluations,
            selected,
            {"schema_version": 1, "events": [], "clips": {}},
            max_count=1,
            min_gap=8,
        )

        self.assertEqual(malformed["shadow_scoring"]["base_score"], 0.0)
        self.assertEqual(malformed["learned_quality_score"], 0.0)
        self.assertEqual(report["baseline_selected"][0]["quality_score"], 0.57)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            out = temp_path / "debug.json"
            write_debug_report(
                out,
                temp_path / "source.mp4",
                [row["candidate"] for row in evaluations],
                evaluations,
                selected,
            )
            payload = json.loads(out.read_text(encoding="utf-8"))

        malformed_row = next(row for row in payload["candidates"] if row["candidate"].get("candidate_rank") == 1)
        self.assertEqual(malformed_row["quality_score"], 0.0)

    def test_voice_profile_shadow_report_is_capped_and_diagnostic_only(self):
        plain = _evaluation(1, 0.60, "walking around checking this room", start=0)
        preferred = _evaluation(2, 0.57, "panic chase right behind me run please", start=45)
        plain["voice_profile"] = {"enabled": True, "enrolled": True, "reason": "scored", "confidence": 0.0, "sample_count": 3}
        preferred["voice_profile"] = {"enabled": True, "enrolled": True, "reason": "scored", "confidence": 1.0, "sample_count": 3}
        evaluations = [plain, preferred]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="learned_quality_score")

        report = build_voice_profile_shadow_report(
            evaluations,
            selected,
            max_count=1,
            min_gap=8,
            score_key="learned_quality_score",
        )

        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 1)
        self.assertFalse(report["output_changed"])
        self.assertEqual(report["selection_impact"], "none")
        self.assertTrue(report["diagnostic_only"])
        self.assertTrue(report["hypothetical_selection_changed"])
        self.assertLessEqual(abs(preferred["voice_profile_shadow"]["voice_adjustment"]), VOICE_PROFILE_SHADOW_MAX_ADJUSTMENT)
        self.assertEqual(preferred["voice_profile_shadow"]["selection_delta"], "would_add_by_voice")
        self.assertEqual(plain["voice_profile_shadow"]["selection_delta"], "would_drop_by_voice")
        self.assertNotIn("voice_profile_shadow", inspect.getsource(candidate_ranker.select_best_candidates))

    def test_voice_profile_shadow_report_noops_without_scores(self):
        plain = _evaluation(1, 0.60, "walking around checking this room", start=0)
        preferred = _evaluation(2, 0.57, "panic chase right behind me run please", start=45)
        plain["voice_profile"] = {"enabled": False, "reason": "disabled", "confidence": None}
        preferred["voice_profile"] = {"enabled": True, "enrolled": False, "reason": "not_enrolled", "confidence": None}
        evaluations = [plain, preferred]
        apply_learned_scoring(evaluations, {"schema_version": 1, "events": [], "clips": {}})
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="learned_quality_score")

        report = build_voice_profile_shadow_report(evaluations, selected, max_count=1, min_gap=8)

        self.assertFalse(report["has_voice_profile_scores"])
        self.assertFalse(report["hypothetical_selection_changed"])
        self.assertEqual(plain["voice_profile_shadow"]["voice_adjustment"], 0.0)
        self.assertEqual(preferred["voice_profile_shadow"]["voice_adjustment"], 0.0)
        self.assertEqual(report["selection_delta_counts"].get("kept"), 1)

    def test_voice_profile_shadow_report_ignores_malformed_scores(self):
        plain = _evaluation(1, 0.60, "walking around checking this room", start=0)
        preferred = _evaluation(2, 0.57, "panic chase right behind me run please", start=45)
        evaluations = [plain, preferred]
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="quality_score")
        plain["learned_quality_score"] = "not-a-score"
        plain["voice_profile"] = {"enabled": True, "enrolled": True, "reason": "scored", "confidence": "nan"}
        preferred["voice_profile"] = {"enabled": True, "enrolled": True, "reason": "no_features", "confidence": 0.99}

        report = build_voice_profile_shadow_report(
            evaluations,
            selected,
            max_count=1,
            min_gap=8,
            max_adjustment="../bad",
            score_key="learned_quality_score",
        )

        self.assertFalse(report["has_voice_profile_scores"])
        self.assertEqual(report["voice_profile_max_adjustment"], 0.0)
        self.assertEqual(plain["voice_profile_shadow"]["voice_adjustment"], 0.0)
        self.assertEqual(preferred["voice_profile_shadow"]["voice_adjustment"], 0.0)
        self.assertEqual(plain["voice_profile_shadow"]["voice_reason"], "scored")

    def test_debug_reports_keep_stage_and_learning_fields(self):
        plain = _evaluation(1, 0.60, "walking around checking this room", start=0)
        preferred = _evaluation(2, 0.57, "panic chase right behind me run please", start=45)
        preferred["commentary_guard"] = {
            "schema_version": 1,
            "mode": "shadow",
            "enabled": True,
            "output_changed": False,
            "summary": {"primary_label": "creator_commentary"},
            "segments": [{"text": "panic chase right behind me run please", "label": "creator_commentary"}],
        }
        preferred["moment"]["commentary_guard"] = {
            "schema_version": 1,
            "mode": "shadow",
            "enabled": True,
            "output_changed": False,
            "summary": {"primary_label": "creator_commentary"},
        }
        plain["voice_profile"] = {"enabled": True, "enrolled": True, "reason": "scored", "confidence": 0.15, "sample_count": 3}
        plain["moment"]["voice_profile"] = plain["voice_profile"]
        preferred["voice_profile"] = {"enabled": True, "enrolled": True, "reason": "scored", "confidence": 0.85, "sample_count": 3}
        preferred["moment"]["voice_profile"] = preferred["voice_profile"]
        preferred["visual_diagnostics"] = {
            "schema_version": 1,
            "status": "ok",
            "sample_count": 3,
            "visual_energy": 0.62,
            "possible_failure_score": 0.44,
            "scenic_score": 0.18,
            "labels": ["high_motion", "possible_failure_screen"],
        }
        preferred["candidate"]["visual_diagnostics"] = preferred["visual_diagnostics"]
        preferred["moment"]["visual_diagnostics"] = preferred["visual_diagnostics"]
        preferred_categories = {
            "primary": "high_energy",
            "confidence": 0.86,
            "scores": {"high_energy": 0.86, "tutorial_or_explainer": 0.12},
        }
        preferred["moment_categories"] = preferred_categories
        preferred["primary_category"] = "high_energy"
        preferred["moment"]["moment_categories"] = preferred_categories
        preferred["moment"]["primary_category"] = "high_energy"
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
        learned_selected = selected
        voice_status = {"enabled": True, "enrolled": True, "ranking_enabled": True, "ranking_active": True}
        apply_voice_profile_scoring(evaluations, voice_status, score_key="learned_quality_score")
        voice_selected = select_best_candidates(
            evaluations,
            1,
            min_gap=8,
            score_key="voice_profile_quality_score",
        )
        voice_ranking_report = build_voice_profile_ranking_report(
            evaluations,
            selected,
            voice_selected,
            voice_status,
            max_count=1,
            min_gap=8,
        )
        selected = voice_selected
        voice_report = build_voice_profile_shadow_report(evaluations, learned_selected, max_count=1, min_gap=8)

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
                voice_profile_shadow=voice_report,
                voice_profile_ranking=voice_ranking_report,
                visual_diagnostics={
                    "schema_version": 1,
                    "status": "ok",
                    "candidate_count": 2,
                    "sampled_candidate_count": 1,
                    "frames_read": 3,
                },
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
            self.assertIn("voice_adjustment", preferred_row)
            self.assertIn("voice_confidence", preferred_row)
            self.assertIn("voice_reason", preferred_row)
            self.assertIn("voice_max_adjustment", preferred_row)
            self.assertIn("voice_shadow_score", preferred_row)
            self.assertIn("voice_current_rank", preferred_row)
            self.assertIn("voice_shadow_rank", preferred_row)
            self.assertIn("voice_selected_by_current", preferred_row)
            self.assertIn("voice_would_select", preferred_row)
            self.assertIn("voice_profile_shadow", candidate_payload)
            self.assertEqual(candidate_payload["voice_profile_shadow"]["mode"], "voice_profile_shadow")
            self.assertFalse(candidate_payload["voice_profile_shadow"]["output_changed"])
            self.assertEqual(candidate_payload["voice_profile_shadow"]["selection_impact"], "none")
            self.assertIn("voice_profile_ranking", candidate_payload)
            self.assertEqual(candidate_payload["voice_profile_ranking"]["mode"], "voice_profile_blend")
            self.assertEqual(candidate_payload["voice_profile_ranking"]["selection_impact"], "capped_rank_adjustment")
            self.assertEqual(preferred_row["voice_confidence"], 0.85)
            self.assertEqual(preferred_row["voice_profile"]["confidence"], 0.85)
            self.assertTrue(preferred_row["voice_ranking_enabled"])
            self.assertIn("voice_profile_quality_score", preferred_row)
            self.assertEqual(preferred_row["voice_scoring"]["selection_impact"], "capped_rank_adjustment")
            self.assertEqual(preferred_row["voice_profile_shadow"]["selection_impact"], "none")
            self.assertEqual(candidate_payload["visual_diagnostics"]["status"], "ok")
            self.assertEqual(preferred_row["visual_diagnostics"]["visual_energy"], 0.62)
            self.assertIn("possible_failure_screen", preferred_row["visual_diagnostics"]["labels"])
            self.assertIn("moment_categories", preferred_row)
            self.assertEqual(preferred_row["selection_primary_category"], "high_energy")
            self.assertEqual(preferred_row["ranking_primary_category"], "high_energy")
            self.assertEqual(preferred_row["final_primary_category"], "high_energy")
            self.assertEqual(preferred_row["selection_moment_categories"]["primary"], "high_energy")
            self.assertEqual(preferred_row["ranking_moment_categories"]["primary"], "high_energy")
            self.assertEqual(preferred_row["final_moment_categories"]["primary"], "high_energy")
            self.assertEqual(
                preferred_row["commentary_guard"]["summary"]["primary_label"],
                "creator_commentary",
            )
            self.assertEqual(preferred_row["commentary_guard"]["segments"][0]["label"], "creator_commentary")
            self.assertNotIn("segments", preferred["moment"]["commentary_guard"])
            self.assertEqual(preferred_row["selection_delta"], "added_by_learning")
            self.assertEqual(
                candidate_payload["shadow_scoring"]["selection_delta_counts"]["added_by_learning"],
                1,
            )

            preferred["moment"]["primary_category"] = "tutorial_or_explainer"
            preferred["moment"]["moment_categories"] = {
                "primary": "tutorial_or_explainer",
                "confidence": 0.81,
                "scores": {"tutorial_or_explainer": 0.81, "high_energy": 0.18},
            }

            write_debug_report(
                run_debug,
                temp_path / "source.mp4",
                [item["candidate"] for item in evaluations],
                evaluations,
                selected,
                final_clips=[{"index": 1, "path": "source_viral1.mp4"}],
                shadow_scoring=report,
                voice_profile_shadow=voice_report,
                voice_profile_ranking=voice_ranking_report,
                visual_diagnostics={
                    "schema_version": 1,
                    "status": "ok",
                    "candidate_count": 2,
                    "sampled_candidate_count": 1,
                    "frames_read": 3,
                },
            )
            run_payload = json.loads(run_debug.read_text(encoding="utf-8"))

            self.assertEqual(run_payload["debug_stage"], "run_post_render")
            self.assertTrue(run_payload["final_render_metadata_included"])
            self.assertEqual(run_payload["final_clips"][0]["path"], "source_viral1.mp4")
            run_rows = {
                row["candidate"]["candidate_rank"]: row
                for row in run_payload["candidates"]
            }
            run_preferred = run_rows[2]
            self.assertEqual(run_preferred["selection_primary_category"], "high_energy")
            self.assertEqual(run_preferred["ranking_primary_category"], "high_energy")
            self.assertEqual(run_preferred["final_primary_category"], "tutorial_or_explainer")
            self.assertEqual(run_preferred["selection_moment_categories"]["primary"], "high_energy")
            self.assertEqual(run_preferred["final_moment_categories"]["primary"], "tutorial_or_explainer")
            self.assertEqual(run_preferred["final"]["primary_category"], "tutorial_or_explainer")


if __name__ == "__main__":
    unittest.main()
