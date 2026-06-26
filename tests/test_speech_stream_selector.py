import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from speech_stream_selector import (  # noqa: E402
    choose_stream_from_profiles,
    select_speech_stream,
    score_stream_profile,
    should_accept_alternate_stream,
)


class SpeechStreamSelectorTests(unittest.TestCase):
    def test_malformed_candidate_windows_fall_back_without_extraction(self):
        streams = [
            {"ordinal": 0, "title": "Microphone"},
            {"ordinal": 1, "title": "Desktop Audio"},
        ]
        moments = [
            {"score": {"bad": "score"}, "start": "not-a-time", "end": 40},
            {"score": "0.8", "start": 10, "end": "not-a-time"},
            {"score": float("nan"), "start": 30, "end": 20},
            "not a dict",
        ]

        with patch("speech_stream_selector.get_audio_streams", return_value=streams), \
             patch("speech_stream_selector.extract_audio_clip") as extract_audio:
            selected = select_speech_stream(
                Path("example.mp4"),
                moments,
                model_size="tiny",
                language=None,
                scratch_dir=Path("."),
            )

        self.assertEqual(selected, 0)
        extract_audio.assert_not_called()

    def test_mic_title_can_beat_game_track_with_more_words(self):
        mic = score_stream_profile(
            0,
            "Microphone_vertical",
            "oh my god he is right behind me please run",
            words_total=42,
            sample_hits=3,
            sampled_seconds=60,
        )
        game = score_stream_profile(
            1,
            "Desktop Audio",
            "checkpoint reached objective updated press enter loading mission",
            words_total=52,
            sample_hits=4,
            sampled_seconds=60,
        )

        report = choose_stream_from_profiles([game, mic])

        self.assertEqual(report["selected_stream"], 0)
        self.assertEqual(report["runner_up_stream"], 1)
        self.assertIn(
            report["selected_reason"],
            {"mic_title_hint_over_more_words", "mic_title_hint_and_speech", "mic_creator_signal_over_more_words"},
        )
        self.assertGreater(report["confidence"], 0.4)
        self.assertEqual(report["stream_profiles"][0]["ordinal"], 0)

    def test_creator_like_mic_beats_unlabeled_track_with_moderate_word_gap(self):
        mic = score_stream_profile(
            0,
            "Microphone",
            "wait what are we doing i think he is right behind me no no please run",
            words_total=34,
            sample_hits=3,
            sampled_seconds=60,
        )
        unknown = score_stream_profile(
            1,
            "Track 2",
            "door opens footsteps ambient voice says find the key continue down the hallway",
            words_total=66,
            sample_hits=4,
            sampled_seconds=60,
        )

        report = choose_stream_from_profiles([unknown, mic])

        self.assertEqual(report["selected_stream"], 0)
        self.assertEqual(report["runner_up_stream"], 1)
        self.assertEqual(report["selected_reason"], "mic_creator_signal_over_more_words")
        self.assertGreater(report["stream_profiles"][0]["mic_creator_preference_bonus"], 0.0)

    def test_mic_title_does_not_beat_large_word_gap_without_creator_signal(self):
        mic = score_stream_profile(
            0,
            "Microphone",
            "test signal channel check tone test signal",
            words_total=10,
            sample_hits=1,
            sampled_seconds=60,
        )
        unknown = score_stream_profile(
            1,
            "Track 2",
            "a long clear narration segment with many transcribed words from another source",
            words_total=78,
            sample_hits=4,
            sampled_seconds=60,
        )

        report = choose_stream_from_profiles([unknown, mic])

        self.assertEqual(report["selected_stream"], 1)
        self.assertEqual(report["runner_up_stream"], 0)
        self.assertEqual(mic["mic_creator_preference_bonus"], 0.0)

    def test_game_track_wins_when_mic_signal_is_too_weak(self):
        mic = score_stream_profile(
            0,
            "Mic",
            "okay",
            words_total=3,
            sample_hits=1,
            sampled_seconds=60,
        )
        game = score_stream_profile(
            1,
            "Game Capture",
            "this is a long clear narration segment with many words and no creator reaction",
            words_total=90,
            sample_hits=4,
            sampled_seconds=60,
        )

        report = choose_stream_from_profiles([game, mic])

        self.assertEqual(report["selected_stream"], 1)
        self.assertEqual(report["runner_up_stream"], 0)
        self.assertEqual(report["selected_reason"], "more_whisper_words")
        self.assertEqual(mic["mic_creator_preference_bonus"], 0.0)

    def test_mic_title_does_not_win_when_transcript_is_lyrics(self):
        mic_music = score_stream_profile(
            0,
            "Microphone_vertical",
            (
                "wasted wasted gta love bitches wasted wasted im on these drugs "
                "i feel wasted wasted diamonds got the flu wedding ring necklace "
                "hold up hold up hold up"
            ),
            words_total=150,
            sample_hits=5,
            sampled_seconds=90,
        )
        creator = score_stream_profile(
            1,
            "Track3_vertical",
            "what the hell is going on im picking rifle batteries and revolver ammo no im joking",
            words_total=58,
            sample_hits=3,
            sampled_seconds=60,
        )

        report = choose_stream_from_profiles([mic_music, creator])

        self.assertEqual(report["selected_stream"], 1)
        self.assertGreater(mic_music["lyric_likelihood"], 0.55)
        self.assertLess(mic_music["selection_score"], creator["selection_score"])

    def test_report_contains_runner_up_reason_and_profiles(self):
        first = score_stream_profile(
            0,
            "Commentary",
            "wait what are we doing here",
            words_total=30,
            sample_hits=2,
            sampled_seconds=40,
        )
        second = score_stream_profile(
            1,
            "Track 2",
            "some other speech",
            words_total=20,
            sample_hits=1,
            sampled_seconds=40,
        )

        report = choose_stream_from_profiles([first, second])

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["mode"], "diagnostic_v2")
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["selected_stream"], 0)
        self.assertEqual(report["runner_up_stream"], 1)
        self.assertEqual(report["selected_title"], "Commentary")
        self.assertEqual(len(report["stream_profiles"]), 2)
        self.assertIn("creator_phrase_score", report["stream_profiles"][0])

    def test_no_profiles_falls_back_without_crashing(self):
        report = choose_stream_from_profiles([], fallback_stream=2)

        self.assertEqual(report["status"], "no_profiles")
        self.assertEqual(report["selected_stream"], 2)
        self.assertEqual(report["selected_reason"], "no_stream_profiles")

    def test_natural_dialogue_score_is_not_exact_phrase_only(self):
        creator = score_stream_profile(
            0,
            "Track 1",
            "wait what are we doing here i dont know where to go no come on",
            words_total=16,
            sample_hits=2,
            sampled_seconds=30,
        )
        system = score_stream_profile(
            1,
            "Track 2",
            "checkpoint reached objective updated press enter to continue",
            words_total=16,
            sample_hits=2,
            sampled_seconds=30,
        )

        self.assertGreater(creator["natural_dialogue_score"], system["natural_dialogue_score"])
        self.assertGreater(creator["creator_likeness_score"], system["creator_likeness_score"])

    def test_creator_policy_rejects_alternate_with_game_bed(self):
        profile = score_stream_profile(
            1,
            "Desktop Audio",
            "this is a clear spoken section with many words",
            words_total=44,
            sample_hits=3,
            sampled_seconds=40,
            acoustic_profile={
                "status": "ok",
                "game_bed_score": 0.86,
                "gap_to_speech_ratio": 0.9,
                "speech_coverage": 0.18,
            },
        )

        decision = should_accept_alternate_stream(profile, subtitle_policy="creator")

        self.assertFalse(decision["accepted"])
        self.assertEqual(decision["reason"], "background_bed_suggests_game_audio")

    def test_creator_policy_rejects_alternate_with_high_game_source_confidence(self):
        profile = {
            "ordinal": 1,
            "title": "Microphone",
            "words": 34,
            "chars": 120,
            "hits": 3,
            "sampled_seconds": 40,
            "creator_likeness_score": 0.30,
            "natural_dialogue_score": 1.0,
            "scripted_game_score": 1.1,
            "acoustic_game_bed_score": 0.40,
            "lyric_likelihood": 0.0,
            "creator_exception_score": 0.0,
            "voice_title_hints": ["microphone"],
            "game_title_hints": [],
            "selection_score": 40,
            "speech_source": {
                "policy": "creator",
                "primary_source": "game",
                "confidence": 0.70,
                "creator_probability": 0.22,
                "game_or_npc_probability": 0.58,
                "music_or_lyrics_probability": 0.04,
                "unknown_probability": 0.16,
                "creator_safe": False,
            },
        }

        decision = should_accept_alternate_stream(profile, subtitle_policy="creator")

        self.assertFalse(decision["accepted"])
        self.assertEqual(decision["reason"], "source_confidence_game_or_npc")
        self.assertEqual(decision["speech_source"]["primary_source"], "game")

    def test_creator_policy_rejects_unlabeled_alternate_with_weak_creator_confidence(self):
        profile = {
            "ordinal": 1,
            "title": "Track3_vertical",
            "words": 48,
            "chars": 160,
            "hits": 3,
            "sampled_seconds": 40,
            "creator_likeness_score": 0.50,
            "natural_dialogue_score": 2.4,
            "scripted_game_score": 0.5,
            "acoustic_game_bed_score": 0.20,
            "lyric_likelihood": 0.0,
            "creator_exception_score": 0.0,
            "voice_title_hints": [],
            "game_title_hints": [],
            "selection_score": 40,
            "speech_source": {
                "policy": "creator",
                "primary_source": "creator",
                "confidence": 0.42,
                "creator_probability": 0.52,
                "game_or_npc_probability": 0.22,
                "music_or_lyrics_probability": 0.05,
                "unknown_probability": 0.21,
                "creator_safe": False,
            },
        }

        decision = should_accept_alternate_stream(profile, subtitle_policy="creator")

        self.assertFalse(decision["accepted"])
        self.assertEqual(decision["reason"], "alternate_lacks_creator_confidence")

    def test_creator_policy_rejects_alternate_that_is_song_lyrics(self):
        profile = score_stream_profile(
            1,
            "Track3_vertical",
            (
                "hold up hold up diamonds got the flu wedding ring necklace "
                "wasted wasted gta love bitches wasted drugs codeine"
            ),
            words_total=64,
            sample_hits=3,
            sampled_seconds=40,
            acoustic_profile={"status": "ok", "game_bed_score": 0.18},
        )

        decision = should_accept_alternate_stream(profile, subtitle_policy="creator")

        self.assertFalse(decision["accepted"])
        self.assertEqual(decision["reason"], "music_lyrics_not_creator_commentary")

    def test_all_or_game_policy_can_accept_non_creator_alternate(self):
        profile = score_stream_profile(
            1,
            "Game Capture",
            "objective updated press enter to continue",
            words_total=20,
            sample_hits=2,
            sampled_seconds=20,
            acoustic_profile={"status": "ok", "game_bed_score": 0.9},
        )

        self.assertTrue(should_accept_alternate_stream(profile, subtitle_policy="all")["accepted"])
        self.assertTrue(should_accept_alternate_stream(profile, subtitle_policy="game")["accepted"])

    def test_creator_policy_accepts_natural_dialogue_alternate(self):
        profile = score_stream_profile(
            0,
            "Microphone",
            "wait what are we doing i dont know where he went please run",
            words_total=38,
            sample_hits=3,
            sampled_seconds=40,
            acoustic_profile={"status": "ok", "game_bed_score": 0.12},
        )

        decision = should_accept_alternate_stream(profile, subtitle_policy="creator")

        self.assertTrue(decision["accepted"])
        self.assertIn(decision["reason"], {"creator_like_alternate", "mic_hint_and_natural_dialogue"})

    def test_creator_source_confidence_can_beat_longer_game_stream(self):
        creator = score_stream_profile(
            0,
            "Track 1",
            "okay yeah i think this is where we try a different route",
            words_total=24,
            sample_hits=3,
            sampled_seconds=40,
            acoustic_profile={"status": "ok", "game_bed_score": 0.10},
        )
        game = score_stream_profile(
            1,
            "Track 2",
            "you are required to continue through the corridor now",
            words_total=32,
            sample_hits=3,
            sampled_seconds=40,
            acoustic_profile={"status": "ok", "game_bed_score": 0.80},
        )

        selected = choose_stream_from_profiles([game, creator])
        selected_profile = next(row for row in selected["stream_profiles"] if row["ordinal"] == selected["selected_stream"])

        self.assertEqual(selected["selected_stream"], 0)
        self.assertEqual(selected["selected_reason"], "creator_source_confidence_over_more_words")
        self.assertTrue(selected_profile["speech_source"]["creator_safe"])


if __name__ == "__main__":
    unittest.main()
