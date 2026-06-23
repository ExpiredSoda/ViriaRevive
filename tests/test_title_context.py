import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from title_generator import (  # noqa: E402
    _build_moment_classification_prompt,
    _build_ollama_prompt,
    classify_moment_ai,
    compose_description,
    generate_description,
    generate_tags,
    recommended_hashtags,
    summarize_clip_context,
)


def _clip_context():
    return {
        "game_title": "Alan Wake",
        "start": 120,
        "end": 150,
        "duration": 30,
        "peak_time": 137.5,
        "candidate_rank": 4,
        "candidate_kind": "pre_event",
        "detector_score": 0.83,
        "detector_scores": {"audio": 0.72, "variance": 0.51, "scene": 0.22},
        "scene_detection_status": "ok",
        "quality_score": 0.78,
        "selection_quality_score": 0.78,
        "learned_quality_score": 0.82,
        "quality_rank": 1,
        "word_count": 22,
        "transcript": "Oh my god he is right behind me please run and hide",
        "ranker": {
            "hook_points": 9.0,
            "weak_points": 0.0,
            "aftermath_points": 0.0,
            "first_word_start": 0.4,
            "last_word_end": 14.2,
            "reject_reason": "",
        },
    }


class TitleContextTests(unittest.TestCase):
    def test_prompt_uses_clip_generation_analysis(self):
        transcript = "Oh my god he is right behind me please run and hide"

        prompt = _build_ollama_prompt(
            transcript,
            game_title="Alan Wake",
            clip_context=_clip_context(),
        )

        self.assertIn("Clip analysis from detector/ranker", prompt)
        self.assertIn("Moment type: chase/panic", prompt)
        self.assertIn("Spoken hooks/payoffs", prompt)
        self.assertIn("right behind me", prompt)
        self.assertIn("hook_points=9.0", prompt)
        self.assertIn("candidate_kind=pre_event", prompt)
        self.assertIn("audio=0.72", prompt)
        self.assertIn("Scene detection status: ok", prompt)
        self.assertIn('Transcript: "Oh my god', prompt)

    def test_description_and_tags_use_analysis_context(self):
        context = _clip_context()

        summary = summarize_clip_context(context["transcript"], "Alan Wake", context)
        description = generate_description(
            "He Was Right Behind Me In Alan Wake #shorts #AlanWake",
            "Alan Wake",
            clip_context=context,
        )
        tags = generate_tags("Alan Wake", context["transcript"], clip_context=context)

        self.assertEqual(summary["moment_type"], "chase/panic")
        self.assertIn("A tense Alan Wake chase/panic moment", description)
        self.assertIn("#shorts #AlanWake #gaming", description)
        self.assertIn("chase gameplay", tags)
        self.assertIn("right behind me", tags)

    def test_description_composition_keeps_custom_text_and_hashtags_separate(self):
        context = _clip_context()

        description = compose_description(
            "He Was Right Behind Me In Alan Wake #shorts #AlanWake",
            "Alan Wake",
            clip_context=context,
            custom_text="Watch the full stream tonight.",
            auto_hashtags=True,
        )

        self.assertIn("Watch the full stream tonight.", description)
        self.assertTrue(description.endswith("#shorts #AlanWake #gaming"))
        self.assertEqual(description.count("#shorts"), 1)
        self.assertEqual(description.count("#AlanWake"), 1)

    def test_description_composition_can_disable_auto_hashtags(self):
        description = compose_description(
            "A Weird Moment",
            "Alan Wake",
            custom_text="Custom footer only.",
            auto_hashtags=False,
        )

        self.assertIn("Custom footer only.", description)
        self.assertNotIn("#shorts", description)
        self.assertNotIn("#AlanWake", description)

    def test_recommended_hashtags_deduplicate_gaming_game_title(self):
        self.assertEqual(recommended_hashtags("Gaming"), ["#shorts", "#Gaming"])

    def test_ai_moment_classification_falls_back_when_model_not_ready(self):
        context = _clip_context()

        result = classify_moment_ai(
            context["transcript"],
            "Alan Wake",
            context,
            enabled=True,
            ollama_ready=False,
        )

        self.assertEqual(result["status"], "model_not_ready")
        self.assertEqual(result["provider"], "heuristic")
        self.assertTrue(result["fallback_used"])
        self.assertEqual(result["selection_impact"], "none")
        self.assertFalse(result["output_changed"])
        self.assertIsInstance(result["ai_viral_score"], int)
        self.assertGreaterEqual(result["ai_viral_score"], 0)
        self.assertLessEqual(result["ai_viral_score"], 99)
        self.assertIn("hook", result["ai_dimensions"])
        self.assertEqual(result["ai_adjustment"], 0.0)
        self.assertIsNone(result["ai_rank_delta"])
        self.assertIn(result["primary_category"], {
            "high_energy",
            "death_or_failure",
            "tutorial_or_explainer",
            "commentary_or_review",
            "lore_or_story",
            "atmosphere_or_visual",
            "low_value",
        })

    def test_ai_moment_classification_sanitizes_valid_ollama_json(self):
        with patch("title_generator.ask_ollama_json", return_value={
            "primary_category": "tutorial_explainer",
            "fine_labels": ["tutorial_tip", "unknown_label", "tutorial_tip"],
            "confidence": 0.82,
            "reason": "The creator is explaining how the section works.",
            "ai_viral_score": 87,
            "ai_viral_reason": "Clear explanation with useful game context.",
            "ai_dimensions": {
                "hook": 0.5,
                "flow": 0.8,
                "value": 0.9,
                "platform_fit": 0.7,
                "game_context": 0.8,
            },
            "ai_confidence": 0.77,
            "ai_adjustment": 0.05,
            "ai_rank_delta": -2,
            "selection_impact": "capped_rank_adjustment",
            "output_changed": True,
        }):
            result = classify_moment_ai(
                "Go here, use this path, and this is how you avoid the enemy.",
                "Alan Wake",
                _clip_context(),
                enabled=True,
                ollama_ready=True,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["provider"], "ollama")
        self.assertEqual(result["primary_category"], "tutorial_or_explainer")
        self.assertEqual(result["fine_labels"], ["tutorial_tip"])
        self.assertEqual(result["ai_viral_score"], 87)
        self.assertEqual(result["ai_viral_reason"], "Clear explanation with useful game context.")
        self.assertEqual(result["ai_dimensions"]["value"], 0.9)
        self.assertEqual(result["ai_confidence"], 0.77)
        self.assertEqual(result["ai_adjustment"], 0.0)
        self.assertIsNone(result["ai_rank_delta"])
        self.assertEqual(result["selection_impact"], "none")
        self.assertFalse(result["output_changed"])

    def test_ai_moment_classification_handles_malformed_ollama_json(self):
        with patch("title_generator.ask_ollama_json", return_value={"primary_category": "romance_arc"}):
            result = classify_moment_ai(
                "This part is a strange hallway setup.",
                "Alan Wake",
                _clip_context(),
                enabled=True,
                ollama_ready=True,
            )

        self.assertEqual(result["status"], "invalid_response")
        self.assertEqual(result["provider"], "heuristic")
        self.assertTrue(result["fallback_used"])

    def test_moment_classification_prompt_redacts_obvious_local_secrets(self):
        prompt = _build_moment_classification_prompt(
            r"refresh_token=abc123 C:\Users\ExpiredSoda\client_secrets.json oh no run",
            "Alan Wake",
            {**_clip_context(), "source_path": r"C:\Users\ExpiredSoda\client_secrets.json"},
        )

        self.assertIn("refresh_token=[redacted]", prompt)
        self.assertIn("[local-path]", prompt)
        self.assertNotIn("client_secrets.json", prompt)

    def test_moment_classification_prompt_uses_compact_metadata_only(self):
        context = {
            **_clip_context(),
            "commentary_guard": {"segments": [{"text": "raw segment text"}]},
            "voice_profile": {"centroid": [0.1, 0.2, 0.3]},
            "review_filter": "death_or_failure",
            "feedback_state": {"like": True, "reason": "private note"},
        }
        prompt = _build_moment_classification_prompt("word " * 1000, "Alan Wake", context)

        self.assertIn("transcript_preview", prompt)
        self.assertNotIn("raw segment text", prompt)
        self.assertNotIn("centroid", prompt)
        self.assertNotIn("review_filter", prompt)
        self.assertNotIn("private note", prompt)
        self.assertLess(prompt.count("word "), 750)


if __name__ == "__main__":
    unittest.main()
