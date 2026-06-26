import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from speech_source_classifier import (  # noqa: E402
    classify_speech_source,
    positive_boost_block_reason,
    should_retry_for_creator_policy,
    speech_source_selection_penalty,
)


class SpeechSourceClassifierTests(unittest.TestCase):
    def test_voice_profile_and_stream_profile_can_mark_creator_without_phrase_cues(self):
        source = classify_speech_source(
            transcript="okay yeah so i think this part is where we try a different route",
            stream_profile={
                "creator_likeness_score": 0.72,
                "natural_dialogue_score": 3.2,
                "scripted_game_score": 0.2,
                "acoustic_game_bed_score": 0.12,
                "voice_title_hints": ["microphone"],
                "game_title_hints": [],
            },
            voice_profile={"reason": "scored", "confidence": 0.86, "sample_count": 5},
        )

        self.assertEqual(source["primary_source"], "creator")
        self.assertTrue(source["creator_safe"])
        self.assertGreater(source["creator_probability"], source["game_or_npc_probability"])
        self.assertFalse(should_retry_for_creator_policy(source))
        self.assertEqual(positive_boost_block_reason(source), "")

    def test_game_source_uses_multiple_non_phrase_signals(self):
        source = classify_speech_source(
            transcript="you are required to continue through the corridor now",
            stream_profile={
                "creator_likeness_score": 0.18,
                "natural_dialogue_score": 0.4,
                "scripted_game_score": 3.0,
                "acoustic_game_bed_score": 0.78,
                "voice_title_hints": [],
                "game_title_hints": ["desktop"],
            },
            voice_profile={"reason": "scored", "confidence": 0.18, "sample_count": 6},
        )

        self.assertEqual(source["primary_source"], "game")
        self.assertGreater(source["game_or_npc_probability"], source["creator_probability"])
        self.assertTrue(should_retry_for_creator_policy(source))
        self.assertEqual(positive_boost_block_reason(source), "speech_source_game_or_npc")
        penalty = speech_source_selection_penalty(source)
        self.assertEqual(penalty["selection_impact"], "quality_penalty")

    def test_scripted_first_person_npc_dialogue_is_not_creator_safe(self):
        source = classify_speech_source(
            transcript=(
                "want it done right you do it yourself i get it you have hit some bad luck "
                "i am broke and i live in an attic fix yourself some dinner"
            ),
            stream_profile={
                "creator_likeness_score": 0.72,
                "natural_dialogue_score": 3.2,
                "scripted_game_score": 0.2,
                "acoustic_game_bed_score": 0.12,
                "voice_title_hints": ["microphone"],
                "game_title_hints": [],
            },
        )

        self.assertFalse(source["creator_safe"])
        self.assertIn("scripted_dialogue_without_creator_meta", source["risk_flags"])
        self.assertTrue(should_retry_for_creator_policy(source))
        self.assertEqual(positive_boost_block_reason(source), "speech_source_scripted_dialogue")
        penalty = speech_source_selection_penalty(source)
        self.assertEqual(penalty["reason"], "scripted_dialogue_without_creator_meta")
        self.assertEqual(penalty["selection_impact"], "quality_penalty")

    def test_visual_dialogue_scene_blocks_cutscene_speech_without_creator_meta(self):
        source = classify_speech_source(
            transcript=(
                "kanto really does look a lot different from up here huh nyx "
                "i cannot believe we might finally leave this place okay denion "
                "i am in position starting the fireworks come on this way"
            ),
            stream_profile={
                "creator_likeness_score": 0.72,
                "natural_dialogue_score": 3.0,
                "scripted_game_score": 0.2,
                "acoustic_game_bed_score": 0.12,
                "voice_title_hints": ["microphone"],
                "game_title_hints": [],
            },
            visual_context={
                "status": "ok",
                "primary_visual_label": "lore_or_story",
                "visual_labels": ["dialogue_scene"],
                "visible_summary": "Two in-game characters are talking during a story beat.",
            },
        )

        self.assertFalse(source["creator_safe"])
        self.assertGreaterEqual(source["visual_dialogue_scene_score"], 0.8)
        self.assertEqual(positive_boost_block_reason(source), "speech_source_scripted_dialogue")

    def test_creator_review_commentary_still_passes(self):
        source = classify_speech_source(
            transcript=(
                "again oh i do not know what it is about it but it does not look smooth "
                "compare that to how nathan did it in uncharted"
            ),
            stream_profile={
                "creator_likeness_score": 0.72,
                "natural_dialogue_score": 3.2,
                "scripted_game_score": 0.2,
                "acoustic_game_bed_score": 0.12,
                "voice_title_hints": ["microphone"],
                "game_title_hints": [],
            },
        )

        self.assertEqual(source["primary_source"], "creator")
        self.assertTrue(source["creator_safe"])
        self.assertGreater(source["creator_meta_score"], source["scripted_dialogue_risk"])
        self.assertFalse(should_retry_for_creator_policy(source))

    def test_all_policy_does_not_retry_or_penalize_game_source(self):
        source = classify_speech_source(
            transcript="objective updated continue to the checkpoint",
            commentary_guard={
                "summary": {
                    "primary_label": "game_narration",
                    "confidence": 1.0,
                    "game_narration_word_ratio": 1.0,
                    "creator_word_ratio": 0.0,
                }
            },
            subtitle_policy="all",
        )

        self.assertFalse(should_retry_for_creator_policy(source))
        self.assertEqual(positive_boost_block_reason(source, policy="all"), "")
        self.assertEqual(speech_source_selection_penalty(source, policy="all")["selection_impact"], "none")


if __name__ == "__main__":
    unittest.main()
