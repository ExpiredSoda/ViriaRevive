import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from title_generator import (  # noqa: E402
    _build_ollama_prompt,
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


if __name__ == "__main__":
    unittest.main()
