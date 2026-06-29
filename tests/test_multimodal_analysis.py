import json
import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from candidate_ranker import select_best_candidates  # noqa: E402
from multimodal_analysis import (  # noqa: E402
    MULTIMODAL_SELECTION_MAX_ADJUSTMENT,
    _VISION_TEST_IMAGE,
    _ask_ollama_vision_json,
    _build_vision_prompt,
    _candidate_frame_packet_times,
    apply_multimodal_scoring,
    analyze_candidate_frames_with_ollama,
    build_multimodal_ranking_report,
    preflight_ollama_vision_model,
    sanitize_vision_analysis,
    select_ollama_vision_model,
)


def _evaluation(candidate_rank, quality_score, analysis, start=0):
    candidate = {
        "candidate_rank": candidate_rank,
        "candidate_kind": "primary",
        "start": start,
        "end": start + 30,
        "duration": 30,
        "peak_time": start + 15,
    }
    moment = {
        **candidate,
        "quality_score": quality_score,
        "quality_floor": 0.0,
        "transcript": "panic chase right behind me run please",
        "word_count": 7,
        "analysis_word_count": 7,
        "subtitle_word_count": 7,
        "ranker": {"reject_reason": ""},
        "multimodal_analysis": analysis,
    }
    return {
        "accepted": True,
        "quality_score": quality_score,
        "candidate": candidate,
        "moment": moment,
        "words": [],
    }


class _FakeOllamaResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class MultimodalAnalysisTests(unittest.TestCase):
    def test_vision_prompt_includes_compact_game_knowledge(self):
        prompt = _build_vision_prompt(
            {
                "start": 12,
                "end": 42,
                "duration": 30,
                "peak_time": 28,
                "candidate_rank": 2,
                "candidate_kind": "pre_event",
            },
            "oh no he is right behind me",
            "Alan Wake",
            game_context={
                "status": "ok",
                "provider": "wikidata",
                "qid": "Q575505",
                "label": "Alan Wake",
                "description": "2010 video game",
                "source_url": "https://www.wikidata.org/wiki/Q575505",
                "license": "CC0-1.0",
                "facts": {
                    "genres": ["survival horror"],
                    "developers": ["Remedy Entertainment"],
                    "fictional_universes": ["Remedy Connected Universe"],
                },
            },
        )

        self.assertIn("Use game knowledge as background only", prompt)
        self.assertIn('"game_knowledge"', prompt)
        self.assertIn("Remedy Connected Universe", prompt)
        self.assertIn("wikidata", prompt)

    def test_vision_prompt_includes_frame_packet_timestamps(self):
        prompt = _build_vision_prompt(
            {"start": 10, "end": 40, "peak_time": 25},
            "look at this chase",
            "Star Wars Outlaws",
            frames=[
                {"time": 12.5, "source": "setup", "visual_summary": {"visual_energy": 0.2}},
                {"time": 25.0, "source": "candidate_peak", "visual_summary": {"visual_energy": 0.8}},
            ],
        )

        self.assertIn('"frame_packet"', prompt)
        self.assertIn('"candidate_peak"', prompt)
        self.assertIn('"time": 25.0', prompt)

    def test_candidate_frame_packet_times_are_ordered_and_richer_than_default(self):
        times = _candidate_frame_packet_times(
            {
                "start": 100,
                "end": 140,
                "peak_time": 126,
                "visual_diagnostics": {"sample_times": [105, 126, 136]},
            },
            1000,
        )

        self.assertGreaterEqual(len(times), 6)
        self.assertEqual(times, sorted(times, key=lambda row: row[0]))
        sources = {source for _, source in times}
        self.assertIn("early_context", sources)
        self.assertIn("candidate_peak", sources)

    def test_select_ollama_vision_model_prefers_known_vision_models(self):
        model = select_ollama_vision_model([
            "qwen3.5:4b",
            "llava:7b",
            "qwen3-vl:latest",
            "qwen2.5vl:3b",
        ])

        self.assertEqual(model, "qwen3-vl:latest")

    def test_select_ollama_vision_model_ignores_text_only_qwen(self):
        model = select_ollama_vision_model(["qwen3.5:4b"])

        self.assertEqual(model, "")

    def test_select_ollama_vision_model_falls_back_to_name_hints(self):
        model = select_ollama_vision_model(["custom-llama3.2-vision:latest"])

        self.assertEqual(model, "custom-llama3.2-vision:latest")

    def test_preflight_test_image_is_valid_base64(self):
        decoded = base64.b64decode(_VISION_TEST_IMAGE, validate=True)

        self.assertGreater(len(decoded), 20)
        self.assertEqual(decoded[:8], b"\x89PNG\r\n\x1a\n")

    def test_vision_json_request_disables_thinking_and_parses_message_content(self):
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeOllamaResponse({
                "message": {
                    "content": "{\"primary_visual_label\":\"high_energy\",\"confidence\":0.8}"
                }
            })

        with patch("multimodal_analysis.urllib.request.urlopen", side_effect=fake_urlopen):
            result = _ask_ollama_vision_json(
                "inspect frames",
                ["base64-frame"],
                "qwen3-vl:latest",
                timeout=44,
            )

        self.assertEqual(result["primary_visual_label"], "high_energy")
        self.assertEqual(captured["timeout"], 44)
        self.assertFalse(captured["body"]["think"])
        self.assertEqual(captured["body"]["format"], "json")
        self.assertEqual(captured["body"]["keep_alive"], "10m")
        self.assertEqual(captured["body"]["options"]["temperature"], 0.0)
        self.assertGreaterEqual(captured["body"]["options"]["num_predict"], 700)

    def test_vision_json_request_parses_qwen_thinking_field_when_response_empty(self):
        def fake_urlopen(req, timeout=0):
            return _FakeOllamaResponse({
                "response": "",
                "thinking": "{\"primary_visual_label\":\"atmosphere_or_visual\",\"confidence\":0.9}",
                "done": True,
                "done_reason": "stop",
            })

        with patch("multimodal_analysis.urllib.request.urlopen", side_effect=fake_urlopen):
            result = _ask_ollama_vision_json(
                "inspect frames",
                ["base64-frame"],
                "qwen3-vl:latest",
                timeout=44,
            )

        self.assertEqual(result["primary_visual_label"], "atmosphere_or_visual")
        self.assertEqual(result["confidence"], 0.9)

    def test_preflight_vision_model_returns_ok_for_valid_json(self):
        with patch("multimodal_analysis._VISION_PREFLIGHT_CACHE", {}), patch(
            "multimodal_analysis._ask_ollama_vision_json",
            return_value={"primary_visual_label": "unclear", "confidence": 0.5},
        ) as ask:
            result = preflight_ollama_vision_model("qwen3-vl:latest")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ok")
        ask.assert_called_once()

    def test_analyze_candidate_retries_smaller_frame_packet_after_bad_json(self):
        frames = [
            {"time": 1.0, "source": "setup", "image": "a"},
            {"time": 2.0, "source": "peak", "image": "b"},
            {"time": 3.0, "source": "payoff", "image": "c"},
            {"time": 4.0, "source": "late", "image": "d"},
        ]
        calls = []

        def fake_ask(prompt, images, model, timeout=0):
            calls.append(list(images))
            if len(calls) == 1:
                return None
            return {
                "primary_visual_label": "high_energy",
                "confidence": 0.8,
                "ranking_adjustment": 0.01,
            }

        with patch("multimodal_analysis.select_ollama_vision_model", return_value="qwen3-vl:latest"), patch(
            "multimodal_analysis._extract_frame_images", return_value=frames
        ), patch("multimodal_analysis._ask_ollama_vision_json", side_effect=fake_ask):
            result = analyze_candidate_frames_with_ollama(
                "video.mp4",
                {"start": 0, "end": 10, "peak_time": 5},
                enabled=True,
                timeout=20,
            )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["fallback_used"])
        self.assertEqual(result["initial_status"], "bad_json")
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(calls[1]), 3)

    def test_sanitize_vision_analysis_clamps_adjustment_and_lists(self):
        result = sanitize_vision_analysis(
            {
                "primary_visual_label": "combat_action",
                "visible_summary": "A Taken enemy rushes the player near the flashlight beam.",
                "visual_labels": ["combat", "made_up", "visible_enemy_or_threat"],
                "detected_events": ["enemy rushing", "flashlight aimed"],
                "title_hooks": ["Enemy rush in the dark"],
                "metadata_keywords": ["enemy", "flashlight", "panic"],
                "confidence": 0.91,
                "ranking_adjustment": 0.50,
                "reject_flags": ["not_real"],
            },
            model="qwen3-vl:latest",
            frames=[{"time": 1.0}, {"time": 2.0}],
            elapsed=1.25,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["primary_visual_label"], "high_energy")
        self.assertEqual(result["visual_labels"], ["combat", "visible_enemy_or_threat"])
        self.assertLessEqual(result["ranking_adjustment"], MULTIMODAL_SELECTION_MAX_ADJUSTMENT)
        self.assertEqual(result["frame_count"], 2)
        self.assertEqual(result["sample_times"], [1.0, 2.0])

    def test_reject_flags_prevent_positive_adjustment(self):
        result = sanitize_vision_analysis(
            {
                "primary_visual_label": "low_value",
                "confidence": 0.88,
                "ranking_adjustment": 0.02,
                "reject_flags": ["black_screen"],
            },
            model="llava:7b",
        )

        self.assertLessEqual(result["ranking_adjustment"], 0.0)

    def test_apply_multimodal_scoring_can_flip_close_deep_candidate(self):
        plain = _evaluation(1, 0.600, {
            "status": "ok",
            "provider": "ollama",
            "model": "qwen3-vl:latest",
            "primary_visual_label": "low_value",
            "confidence": 0.85,
            "ranking_adjustment": -0.015,
        }, start=0)
        preferred = _evaluation(2, 0.590, {
            "status": "ok",
            "provider": "ollama",
            "model": "qwen3-vl:latest",
            "primary_visual_label": "high_energy",
            "visible_summary": "The player is being chased through a dark hallway.",
            "confidence": 0.90,
            "ranking_adjustment": 0.020,
        }, start=45)
        evaluations = [plain, preferred]
        baseline = select_best_candidates(evaluations, 1, min_gap=8, score_key="quality_score")

        result = apply_multimodal_scoring(evaluations, enabled=True, score_key="quality_score")
        selected = select_best_candidates(evaluations, 1, min_gap=8, score_key="multimodal_quality_score")
        report = build_multimodal_ranking_report(
            evaluations,
            baseline,
            selected,
            enabled=True,
            max_count=1,
            min_gap=8,
            score_key="quality_score",
        )

        self.assertTrue(result["ranking_enabled"])
        self.assertEqual(baseline[0]["candidate"]["candidate_rank"], 1)
        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 2)
        self.assertEqual(selected[0]["selection_score_source"], "multimodal_quality_score")
        self.assertLessEqual(
            abs(preferred["multimodal_scoring"]["multimodal_adjustment"]),
            MULTIMODAL_SELECTION_MAX_ADJUSTMENT,
        )
        self.assertTrue(report["output_changed"])
        self.assertEqual(preferred["multimodal_scoring"]["selection_delta"], "added_by_multimodal")
        self.assertEqual(plain["multimodal_scoring"]["selection_delta"], "dropped_by_multimodal")

    def test_multimodal_scoring_blocks_positive_boost_for_game_narration_guard(self):
        candidate = _evaluation(1, 0.600, {
            "status": "ok",
            "provider": "ollama",
            "model": "qwen3-vl:latest",
            "primary_visual_label": "lore_or_story",
            "visual_labels": ["dialogue_scene"],
            "visible_summary": "Two in-game characters are talking during a story beat.",
            "confidence": 0.90,
            "ranking_adjustment": 0.020,
        }, start=0)
        guard = {
            "policy": "creator",
            "summary": {
                "primary_label": "game_narration",
                "confidence": 0.86,
                "game_narration_word_ratio": 0.92,
                "creator_word_ratio": 0.0,
            },
            "selection": {
                "policy": "creator",
                "selection_penalty": 0.04,
                "selection_impact": "quality_penalty",
            },
        }
        candidate["commentary_guard"] = guard
        candidate["moment"]["commentary_guard"] = guard

        result = apply_multimodal_scoring([candidate], enabled=True, score_key="quality_score")

        self.assertFalse(result["has_multimodal_scores"])
        scoring = candidate["multimodal_scoring"]
        self.assertEqual(scoring["positive_block_reason"], "commentary_guard_game_narration")
        self.assertEqual(scoring["multimodal_adjustment"], 0.0)
        self.assertEqual(scoring["multimodal_quality_score"], 0.6)

    def test_multimodal_positive_boost_blocked_by_source_confidence(self):
        candidate = _evaluation(1, 0.600, {
            "status": "ok",
            "provider": "ollama",
            "model": "qwen3-vl:latest",
            "primary_visual_label": "high_energy",
            "confidence": 0.90,
            "ranking_adjustment": 0.020,
        }, start=0)
        source = {
            "policy": "creator",
            "primary_source": "game",
            "confidence": 0.70,
            "creator_probability": 0.20,
            "game_or_npc_probability": 0.60,
            "music_or_lyrics_probability": 0.03,
            "unknown_probability": 0.17,
            "creator_safe": False,
        }
        candidate["speech_source"] = source
        candidate["moment"]["speech_source"] = source

        result = apply_multimodal_scoring([candidate], enabled=True, score_key="quality_score")

        self.assertFalse(result["has_multimodal_scores"])
        scoring = candidate["multimodal_scoring"]
        self.assertEqual(scoring["positive_block_reason"], "speech_source_game_or_npc")
        self.assertEqual(scoring["multimodal_adjustment"], 0.0)
        self.assertEqual(scoring["multimodal_quality_score"], 0.6)

    def test_negative_multimodal_adjustment_still_applies_to_game_source(self):
        candidate = _evaluation(1, 0.600, {
            "status": "ok",
            "provider": "ollama",
            "model": "qwen3-vl:latest",
            "primary_visual_label": "low_value",
            "confidence": 0.90,
            "ranking_adjustment": -0.015,
        }, start=0)
        candidate["speech_source"] = {
            "policy": "creator",
            "primary_source": "game",
            "confidence": 0.70,
            "creator_probability": 0.20,
            "game_or_npc_probability": 0.60,
            "music_or_lyrics_probability": 0.03,
            "unknown_probability": 0.17,
            "creator_safe": False,
        }

        result = apply_multimodal_scoring([candidate], enabled=True, score_key="quality_score")

        self.assertTrue(result["has_multimodal_scores"])
        self.assertLess(candidate["multimodal_scoring"]["multimodal_adjustment"], 0.0)
        self.assertEqual(candidate["multimodal_scoring"]["positive_block_reason"], "speech_source_game_or_npc")

    def test_multimodal_scoring_requires_ok_ollama_analysis(self):
        fallback = _evaluation(1, 0.600, {
            "status": "heuristic",
            "provider": "heuristic",
            "confidence": 0.99,
            "ranking_adjustment": 0.02,
        }, start=0)
        evaluations = [fallback]

        result = apply_multimodal_scoring(evaluations, enabled=True, score_key="quality_score")

        self.assertFalse(result["has_multimodal_scores"])
        self.assertEqual(fallback["multimodal_scoring"]["multimodal_adjustment"], 0.0)
        self.assertEqual(fallback["multimodal_scoring"]["ineligible_reason"], "vision_status_not_ok")

    def test_multimodal_scoring_rescues_near_floor_visual_candidate(self):
        near_miss = _evaluation(3, 0.565, {
            "status": "ok",
            "provider": "ollama",
            "model": "qwen3-vl:latest",
            "primary_visual_label": "high_energy",
            "visible_summary": "A visible chase is happening in the gameplay frames.",
            "confidence": 0.91,
            "ranking_adjustment": 0.02,
            "reject_flags": [],
        }, start=90)
        near_miss["accepted"] = False
        near_miss["reject_reason"] = "low_transcript_quality"
        near_miss["quality_floor"] = 0.60
        near_miss["multimodal_rescue_candidate"] = True
        near_miss["multimodal_rescue_reason"] = "near_quality_floor"
        near_miss["moment"]["ranker"]["reject_reason"] = "low_transcript_quality"

        result = apply_multimodal_scoring([near_miss], enabled=True, score_key="quality_score")
        selected = select_best_candidates([near_miss], 1, min_gap=8, score_key="multimodal_quality_score")

        self.assertTrue(near_miss["accepted"])
        self.assertEqual(near_miss["original_reject_reason"], "low_transcript_quality")
        self.assertEqual(near_miss["reject_reason"], "")
        self.assertTrue(near_miss["multimodal_scoring"]["visual_rescue_applied"])
        self.assertEqual(result["rescued_candidate_count"], 1)
        self.assertEqual(selected[0]["candidate"]["candidate_rank"], 3)

    def test_multimodal_scoring_respects_relative_rescue_floor(self):
        near_miss = _evaluation(6, 0.355, {
            "status": "ok",
            "provider": "ollama",
            "model": "qwen3-vl:latest",
            "primary_visual_label": "tutorial_or_explainer",
            "visible_summary": "The player is showing a clear gameplay detail while speaking.",
            "confidence": 0.89,
            "ranking_adjustment": 0.02,
            "reject_flags": [],
        }, start=180)
        near_miss["accepted"] = False
        near_miss["reject_reason"] = "low_transcript_quality"
        near_miss["quality_floor"] = 0.50
        near_miss["multimodal_rescue_candidate"] = True
        near_miss["multimodal_rescue_reason"] = "near_quality_floor"
        near_miss["multimodal_rescue_relative_floor"] = 0.34
        near_miss["moment"]["ranker"]["reject_reason"] = "low_transcript_quality"

        result = apply_multimodal_scoring([near_miss], enabled=True, score_key="quality_score")

        self.assertTrue(near_miss["accepted"])
        self.assertTrue(near_miss["multimodal_scoring"]["visual_rescue_applied"])
        self.assertEqual(result["rescued_candidate_count"], 1)

    def test_multimodal_scoring_does_not_rescue_too_few_words_candidate(self):
        rejected = _evaluation(4, 0.58, {
            "status": "ok",
            "provider": "ollama",
            "model": "qwen3-vl:latest",
            "primary_visual_label": "high_energy",
            "confidence": 0.95,
            "ranking_adjustment": 0.02,
            "reject_flags": [],
        }, start=120)
        rejected["accepted"] = False
        rejected["reject_reason"] = "too_few_words"
        rejected["quality_floor"] = 0.60
        rejected["multimodal_rescue_candidate"] = True
        rejected["moment"]["ranker"]["reject_reason"] = "too_few_words"

        result = apply_multimodal_scoring([rejected], enabled=True, score_key="quality_score")

        self.assertFalse(rejected["accepted"])
        self.assertEqual(rejected["multimodal_scoring"]["visual_rescue_applied"], False)
        self.assertEqual(result["rescued_candidate_count"], 0)

    def test_multimodal_rescue_blocked_for_high_confidence_game_source(self):
        near_miss = _evaluation(5, 0.565, {
            "status": "ok",
            "provider": "ollama",
            "model": "qwen3-vl:latest",
            "primary_visual_label": "high_energy",
            "confidence": 0.91,
            "ranking_adjustment": 0.02,
            "reject_flags": [],
        }, start=150)
        near_miss["accepted"] = False
        near_miss["reject_reason"] = "low_transcript_quality"
        near_miss["quality_floor"] = 0.60
        near_miss["multimodal_rescue_candidate"] = True
        near_miss["moment"]["ranker"]["reject_reason"] = "low_transcript_quality"
        source = {
            "policy": "creator",
            "primary_source": "game",
            "confidence": 0.70,
            "creator_probability": 0.20,
            "game_or_npc_probability": 0.60,
            "music_or_lyrics_probability": 0.03,
            "unknown_probability": 0.17,
            "creator_safe": False,
        }
        near_miss["speech_source"] = source
        near_miss["moment"]["speech_source"] = source

        result = apply_multimodal_scoring([near_miss], enabled=True, score_key="quality_score")

        self.assertFalse(near_miss["accepted"])
        self.assertFalse(near_miss["multimodal_scoring"]["visual_rescue_applied"])
        self.assertEqual(near_miss["multimodal_scoring"]["positive_block_reason"], "speech_source_game_or_npc")
        self.assertEqual(result["rescued_candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
