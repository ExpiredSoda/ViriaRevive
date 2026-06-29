import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from title_generator import (  # noqa: E402
    TIMEOUT,
    _ask_ollama,
    _build_moment_classification_prompt,
    _build_ollama_prompt,
    ask_ollama_json,
    classify_moment_ai,
    compose_description,
    generate_ai_description_body,
    generate_description,
    generate_tags,
    generate_titles_batch,
    recommended_hashtags,
    summarize_clip_context,
)


def _game_context():
    return {
        "schema_version": 1,
        "status": "ok",
        "provider": "wikidata",
        "qid": "Q575505",
        "label": "Alan Wake",
        "description": "2010 video game",
        "source_url": "https://www.wikidata.org/wiki/Q575505",
        "license": "CC0-1.0",
        "facts": {
            "first_release_date": "2010-05-14T00:00:00Z",
            "genres": ["action-adventure game", "survival horror"],
            "developers": ["Remedy Entertainment"],
            "series": ["Alan Wake"],
            "fictional_universes": ["Remedy Connected Universe"],
        },
    }


def _clip_context():
    return {
        "game_title": "Alan Wake",
        "game_context": _game_context(),
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
        "multimodal_analysis": {
            "status": "ok",
            "provider": "ollama",
            "model": "qwen3-vl:latest",
            "primary_visual_label": "high_energy",
            "visible_summary": "The player is being chased through a dark hallway.",
            "visual_labels": ["chase_or_panic", "visible_enemy_or_threat"],
            "detected_events": ["enemy visible behind the player"],
            "title_hooks": ["Chased through the dark"],
            "metadata_keywords": ["chase", "dark hallway"],
            "confidence": 0.88,
            "ranking_adjustment": 0.02,
        },
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


class TitleContextTests(unittest.TestCase):
    def test_title_ollama_request_disables_thinking_and_keeps_model_warm(self):
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeOllamaResponse({"response": "Specific Chase Title"})

        with patch("title_generator.urllib.request.urlopen", side_effect=fake_urlopen):
            title = _ask_ollama("oh no he is right behind me", game_title="Alan Wake")

        self.assertEqual(title, "Specific Chase Title")
        self.assertEqual(captured["timeout"], TIMEOUT)
        self.assertFalse(captured["body"]["think"])
        self.assertEqual(captured["body"]["keep_alive"], "10m")

    def test_ollama_json_request_parses_message_content_without_thinking(self):
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeOllamaResponse({
                "message": {"content": "{\"primary_category\":\"high_energy\"}"}
            })

        with patch("title_generator.urllib.request.urlopen", side_effect=fake_urlopen):
            result = ask_ollama_json("return json", timeout=22)

        self.assertEqual(result, {"primary_category": "high_energy"})
        self.assertEqual(captured["timeout"], 22)
        self.assertFalse(captured["body"]["think"])
        self.assertEqual(captured["body"]["keep_alive"], "10m")

    def test_ai_description_uses_ollama_without_footer_or_hashtags(self):
        context = _clip_context()
        with patch("title_generator.is_ollama_model_ready", return_value=True), \
                patch("title_generator.ask_ollama_json", return_value={
                    "description": "The chase gets way too close as the darkness closes in and the panic takes over."
                }) as ask_json:
            description = generate_ai_description_body(
                "Alan Wake Had Me Running",
                context["transcript"],
                "Alan Wake",
                context,
            )

        self.assertEqual(
            description,
            "The chase gets way too close as the darkness closes in and the panic takes over.",
        )
        prompt = ask_json.call_args.args[0]
        self.assertIn("Vision model analysis:", prompt)
        self.assertIn("Return ONLY valid JSON", prompt)
        self.assertNotIn("#shorts", description)

    def test_ai_description_rejects_prompt_or_detector_sounding_copy(self):
        context = _clip_context()
        with patch("title_generator.is_ollama_model_ready", return_value=True), \
                patch("title_generator.ask_ollama_json", return_value={
                    "description": "A high-energy Alan Wake moment selected from action, panic, or reaction cues."
                }):
            description = generate_ai_description_body(
                "Alan Wake Had Me Running",
                context["transcript"],
                "Alan Wake",
                context,
            )

        self.assertEqual(description, "")

    def test_ai_description_removes_title_echo_when_model_repeats_title_first(self):
        context = _clip_context()
        with patch("title_generator.is_ollama_model_ready", return_value=True), \
                patch("title_generator.ask_ollama_json", return_value={
                    "description": "Alan Wake Had Me Running. The chase gets too close as the darkness closes in."
                }):
            description = generate_ai_description_body(
                "Alan Wake Had Me Running #shorts #AlanWake",
                context["transcript"],
                "Alan Wake",
                context,
            )

        self.assertEqual(description, "The chase gets too close as the darkness closes in.")

    def test_batch_title_generation_warms_model_before_parallel_titles(self):
        def fake_ask(transcript, model, game_title=None, clip_context=None):
            return f"{game_title} {transcript}"

        with patch("title_generator.is_ollama_model_ready", return_value=True), \
                patch("title_generator._warm_ollama_model", return_value=True) as warm, \
                patch("title_generator._ask_ollama", side_effect=fake_ask):
            titles = generate_titles_batch(
                ["chase moment", "boss fight"],
                game_titles=["Alan Wake", "Alan Wake"],
            )

        warm.assert_called_once()
        self.assertEqual(len(titles), 2)
        self.assertTrue(all("#shorts #AlanWake" in title for title in titles))

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
        self.assertIn("Vision model analysis:", prompt)
        self.assertIn("chased through a dark hallway", prompt)
        self.assertIn("primary_visual=high_energy", prompt)
        self.assertIn("Game knowledge:", prompt)
        self.assertIn("Remedy Connected Universe", prompt)
        self.assertIn('Transcript: "Oh my god', prompt)

    def test_prompt_uses_sanitized_creator_title_context(self):
        context = {
            **_clip_context(),
            "creator_title_context": r"Blind Alan Wake nursing home run C:\Users\ExpiredSoda\client_secrets.json api_key=secret123 " + "extra " * 120,
        }

        prompt = _build_ollama_prompt(
            context["transcript"],
            game_title="Alan Wake",
            clip_context=context,
        )
        summary = summarize_clip_context(context["transcript"], "Alan Wake", context)
        description = generate_description(
            "He Was Right Behind Me In Alan Wake #shorts #AlanWake",
            "Alan Wake",
            clip_context=context,
        )

        self.assertIn("Creator-provided context:", prompt)
        self.assertIn("Blind Alan Wake nursing home run", prompt)
        self.assertIn("[local-path]", prompt)
        self.assertIn("api_key=[redacted]", prompt)
        self.assertNotIn("client_secrets.json", prompt)
        self.assertLessEqual(len(summary["creator_title_context"]), 420)
        self.assertNotIn("Blind Alan Wake nursing home run", description)

    def test_prompt_uses_compact_feedback_learning_context(self):
        context = {
            **_clip_context(),
            "feedback_learning_context": {
                "enabled": True,
                "positive_feedback_count": 3,
                "negative_feedback_count": 1,
                "favorite_count": 1,
                "run_learning_signal_count": 5,
                "montage_learning_signal_count": 2,
                "positive_terms": ["panic chase", "right behind"],
                "negative_terms": ["menu pause", "song lyrics"],
                "guidance": "prefer panic chase; avoid menu pause",
            },
        }

        prompt = _build_ollama_prompt(
            context["transcript"],
            game_title="Alan Wake",
            clip_context=context,
        )
        label_prompt = _build_moment_classification_prompt(context["transcript"], "Alan Wake", context)

        self.assertIn("Creator feedback learning:", prompt)
        self.assertIn("likes=panic chase, right behind", prompt)
        self.assertIn("dislikes=menu pause, song lyrics", prompt)
        self.assertIn("run_memory=5", prompt)
        self.assertIn("montage_memory=2", prompt)
        self.assertIn('"creator_learning"', label_prompt)
        self.assertIn("panic chase", label_prompt)
        self.assertIn("song lyrics", label_prompt)

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
        self.assertTrue(summary["game_knowledge"]["available"])
        self.assertEqual(summary["game_knowledge"]["release_year"], 2010)
        self.assertIn("Alan Wake gets tense here", description)
        self.assertIn("#shorts #AlanWake #gaming", description)
        self.assertIn("chase gameplay", tags)
        self.assertIn("dark hallway", tags)
        self.assertIn("right behind me", tags)

    def test_fallback_description_prefers_context_over_title_copy(self):
        context = {
            **_clip_context(),
            "multimodal_analysis": {},
            "transcript": "Run run run",
        }

        description = generate_description(
            "Alan Wake Shadow Tornado Killed The Cops In Seconds #shorts #AlanWake",
            "Alan Wake",
            clip_context=context,
            auto_hashtags=False,
        )

        self.assertNotEqual(description, "Alan Wake Shadow Tornado Killed The Cops In Seconds")
        self.assertIn("Alan Wake turns tense fast", description)

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
            "cinematic_dialogue",
            "atmosphere_or_visual",
            "low_value",
        })

    def test_cinematic_dialogue_context_avoids_horror_streamer_tags(self):
        context = {
            "game_title": "Star Wars Outlaws",
            "game_context": {
                "schema_version": 1,
                "status": "ok",
                "label": "Star Wars Outlaws",
                "facts": {"genres": ["action-adventure game"]},
            },
            "transcript": "Mission updated. Kay needs to meet the contact before the checkpoint.",
            "primary_category": "cinematic_dialogue",
            "moment_categories": {
                "primary": "cinematic_dialogue",
                "confidence": 0.63,
                "scores": {"cinematic_dialogue": 0.67},
            },
            "speech_source": {
                "primary_source": "game",
                "creator_probability": 0.08,
                "game_or_npc_probability": 0.76,
                "music_or_lyrics_probability": 0.02,
                "creator_safe": False,
            },
            "ai_moment_classification": {
                "primary_category": "cinematic_dialogue",
                "fine_labels": ["npc_dialogue"],
                "confidence": 0.72,
            },
        }

        summary = summarize_clip_context(context["transcript"], "Star Wars Outlaws", context)
        tags = generate_tags("Star Wars Outlaws", context["transcript"], clip_context=context)
        result = classify_moment_ai(
            context["transcript"],
            "Star Wars Outlaws",
            context,
            enabled=True,
            ollama_ready=False,
        )

        self.assertEqual(summary["moment_type"], "cinematic/dialogue")
        self.assertEqual(result["primary_category"], "cinematic_dialogue")
        self.assertIn("npc_dialogue", result["fine_labels"])
        self.assertIn("cinematic gameplay", tags)
        self.assertIn("story moment", tags)
        self.assertNotIn("horror gaming", tags)
        self.assertNotIn("streamer moments", tags)

    def test_title_context_carries_creator_speech_policy_warning(self):
        context = {
            **_clip_context(),
            "speech_policy": {
                "subtitle_policy": "creator",
                "status": "no_selected_commentary_speech",
                "warning": "No commentary transcript was found on the selected track.",
                "metadata_transcript_source": "none_selected_track",
                "selected_track_has_speech": False,
                "selected_track_word_count": 0,
                "analysis_word_count": 42,
                "selected_stream": 1,
                "selected_title": "Microphone",
                "render_audio": "all_source_streams_mixed",
                "mixed_speech_without_selected_track": True,
                "metadata_backfill_blocked": True,
            },
            "metadata_warning": "No commentary transcript was found on the selected track.",
            "metadata_needs_context": True,
        }

        summary = summarize_clip_context("", "Star Wars Outlaws", context)
        prompt = _build_ollama_prompt("short selected transcript", "Star Wars Outlaws", context)

        self.assertTrue(summary["metadata_needs_context"])
        self.assertEqual(summary["speech_policy"]["metadata_transcript_source"], "none_selected_track")
        self.assertIn("Speech policy", prompt)
        self.assertIn("selected creator-commentary transcript", prompt)
        self.assertIn("no selected creator speech", prompt)
        self.assertIn("fan favorite", prompt)

    def test_ai_moment_visual_only_failure_uses_possible_failure_label(self):
        context = {
            **_clip_context(),
            "transcript": "Look at this hallway and keep moving forward.",
            "ranker": {"hook_points": 0.0, "weak_points": 0.0, "aftermath_points": 0.0},
            "moment_categories": {"primary": "commentary_or_review", "confidence": 0.42, "scores": {}},
            "primary_category": "commentary_or_review",
            "visual_diagnostics": {"possible_failure_score": 0.56},
        }

        result = classify_moment_ai(
            context["transcript"],
            "Alan Wake",
            context,
            enabled=True,
            ollama_ready=False,
        )

        self.assertEqual(result["primary_category"], "death_or_failure")
        self.assertIn("possible_failure", result["fine_labels"])
        self.assertNotIn("death_scene", result["fine_labels"])

    def test_ai_moment_confirmed_failure_can_use_death_scene_label(self):
        context = {
            **_clip_context(),
            "transcript": "I died right there and got sent all the way back.",
            "ranker": {"hook_points": 0.0, "weak_points": 0.0, "aftermath_points": 3.0},
            "moment_categories": {"primary": "death_or_failure", "confidence": 0.72},
            "primary_category": "death_or_failure",
            "visual_diagnostics": {"possible_failure_score": 0.56},
        }

        result = classify_moment_ai(
            context["transcript"],
            "Alan Wake",
            context,
            enabled=True,
            ollama_ready=False,
        )

        self.assertEqual(result["primary_category"], "death_or_failure")
        self.assertIn("death_scene", result["fine_labels"])
        self.assertNotIn("possible_failure", result["fine_labels"])

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
            "feedback_learning_context": {"enabled": True, "positive_terms": ["useful panic"], "guidance": "private note"},
        }
        prompt = _build_moment_classification_prompt("word " * 1000, "Alan Wake", context)

        self.assertIn("transcript_preview", prompt)
        self.assertIn('"game_knowledge"', prompt)
        self.assertIn("Remedy Connected Universe", prompt)
        self.assertIn("useful panic", prompt)
        self.assertNotIn("raw segment text", prompt)
        self.assertNotIn("centroid", prompt)
        self.assertNotIn("review_filter", prompt)
        self.assertNotIn("private note", prompt)
        self.assertLess(prompt.count("word "), 750)


if __name__ == "__main__":
    unittest.main()
