import inspect
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api_bridge import (  # noqa: E402
    ApiBridge,
    _allowed_media_origin,
    _candidate_model_for_depth,
    _normalize_audio_source_settings,
    _normalize_processing_depth,
    _processing_depth_profile,
    _subtitle_words_for_render_start,
)
from config import CLIPS_DIR  # noqa: E402
from downloader import resolve_downloaded_path  # noqa: E402
from voice_profile import VOICE_PROFILE_FEATURE_COUNT, empty_voice_profile  # noqa: E402


class ApiBridgePathSafetyTests(unittest.TestCase):
    def test_download_path_resolver_prefers_final_merged_filepath(self):
        class FakeYdl:
            @staticmethod
            def prepare_filename(_info):
                return "prepared.webm"

        resolved = resolve_downloaded_path(
            {"requested_downloads": [{"filepath": "merged.mp4"}]},
            FakeYdl(),
        )
        self.assertEqual(resolved, Path("merged.mp4"))

        with tempfile.TemporaryDirectory() as temp_dir:
            prepared = Path(temp_dir) / "video.webm"
            merged = prepared.with_suffix(".mp4")
            merged.write_bytes(b"ok")

            class PreparedYdl:
                @staticmethod
                def prepare_filename(_info):
                    return str(prepared)

            self.assertEqual(resolve_downloaded_path({}, PreparedYdl()), merged)

    def test_ai_shadow_classification_is_diagnostic_and_reused_for_selected_labels(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._infer_game_title_from_path = lambda path: "Test Game"
        evaluations = [
            {
                "accepted": True,
                "quality_score": 0.62,
                "learned_quality_score": 0.62,
                "candidate": {"candidate_rank": 1, "candidate_kind": "primary", "start": 0, "end": 30},
                "moment": {
                    "start": 0,
                    "end": 30,
                    "transcript": "oh my god he is right behind me please run",
                    "primary_category": "high_energy",
                    "moment_categories": {"primary": "high_energy", "confidence": 0.9},
                    "ranker": {},
                },
            },
            {
                "accepted": True,
                "quality_score": 0.58,
                "learned_quality_score": 0.58,
                "candidate": {"candidate_rank": 2, "candidate_kind": "primary", "start": 40, "end": 70},
                "moment": {
                    "start": 40,
                    "end": 70,
                    "transcript": "walking around checking this room again",
                    "primary_category": "commentary_or_review",
                    "moment_categories": {"primary": "commentary_or_review", "confidence": 0.7},
                    "ranker": {},
                },
            },
        ]
        selected = [evaluations[0]]
        before_shadow = json.dumps(evaluations, sort_keys=True)

        with patch("api_bridge.is_ollama_model_ready", return_value=False):
            shadow_report, cache = bridge._classify_ai_moment_shadow(
                evaluations,
                selected,
                Path("source.mp4"),
                enabled=True,
                score_key="learned_quality_score",
                max_count=2,
                max_ollama=1,
            )

        self.assertEqual(json.dumps(evaluations, sort_keys=True), before_shadow)
        self.assertTrue(shadow_report["diagnostic_only"])
        self.assertEqual(shadow_report["selection_impact"], "none")
        self.assertFalse(shadow_report["output_changed"])
        self.assertEqual(shadow_report["classified_count"], 2)
        self.assertEqual(shadow_report["ai_viral_potential"]["selection_impact"], "none")
        self.assertTrue(shadow_report["ai_viral_potential"]["diagnostic_only"])
        self.assertEqual(shadow_report["ai_viral_potential"]["scored_count"], 2)
        self.assertEqual(shadow_report["rows"][0]["candidate_rank"], 1)
        self.assertIn("ai_viral_score", shadow_report["rows"][0])
        self.assertIn("ai_viral_reason", shadow_report["rows"][0])
        self.assertIn("ai_dimensions", shadow_report["rows"][0])
        self.assertEqual(shadow_report["rows"][0]["ai_moment_classification"]["selection_impact"], "none")
        self.assertIsInstance(shadow_report["rows"][0]["ai_moment_classification"]["ai_viral_score"], int)
        self.assertGreaterEqual(len(cache), 1)

        with patch("api_bridge.is_ollama_model_ready", return_value=False):
            selected_report = bridge._classify_selected_moments(
                selected,
                Path("source.mp4"),
                enabled=True,
                max_ollama=1,
                classification_cache=cache,
            )

        self.assertEqual(selected_report["reused_shadow_count"], 1)
        self.assertEqual(selected_report["ollama_attempted_count"], 0)
        self.assertEqual(selected_report["ai_viral_potential"]["selection_impact"], "none")
        self.assertEqual(selected_report["ai_viral_potential"]["scored_count"], 1)
        self.assertEqual(selected[0]["ai_moment_classification"]["selection_impact"], "none")

    def test_delete_clip_does_not_unlink_outside_clips_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outside = Path(temp_dir) / "outside.mp4"
            outside.write_text("do not delete", encoding="utf-8")

            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [outside]
            bridge._moments = [{"clip_id": "clip-outside"}]
            bridge._scheduled = []
            bridge._state_lock = threading.RLock()
            bridge._save_state = lambda: None

            result = bridge.delete_clip(0)

            self.assertIn("error", result)
            self.assertTrue(outside.exists())

    def test_safe_child_path_rejects_parent_traversal(self):
        self.assertIsNone(ApiBridge._safe_child_path(CLIPS_DIR, "..\\outside.mp4"))

    def test_bulk_library_delete_rejects_traversal_without_unlinking_outside_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_clips = Path(temp_dir) / "clips"
            temp_clips.mkdir()
            outside = Path(temp_dir) / "outside.mp4"
            outside.write_text("do not delete", encoding="utf-8")

            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = []
            bridge._moments = []
            bridge._scheduled = []
            bridge._state_lock = threading.RLock()
            bridge._personalization = {"schema_version": 1, "events": [], "clips": {}}
            bridge._personalization_lock = threading.RLock()
            bridge._save_personalization = lambda: None
            saves = []
            bridge._save_state = lambda: saves.append(True)

            with patch("api_bridge.CLIPS_DIR", temp_clips):
                result = bridge.delete_library_files(["..\\outside.mp4"])

            self.assertFalse(result["ok"])
            self.assertEqual(result["deleted"], [])
            self.assertEqual(result["failed"][0]["error"], "Invalid filename")
            self.assertTrue(outside.exists())
            self.assertEqual(saves, [])

    def test_bulk_library_delete_prunes_state_and_keeps_partial_failures(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_clips = Path(temp_dir)
            clip_a = temp_clips / "alpha.mp4"
            clip_b = temp_clips / "bravo.mp4"
            clip_c = temp_clips / "charlie.mp4"
            for path in (clip_a, clip_b, clip_c):
                path.write_text("video", encoding="utf-8")

            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [clip_a, clip_b, clip_c]
            bridge._moments = [
                {"clip_id": "clip-a", "source_id": "source-a"},
                {"clip_id": "clip-b", "source_id": "source-b"},
                {"clip_id": "clip-c", "source_id": "source-c"},
            ]
            bridge._scheduled = [
                {"clip_id": "clip-b", "clip_filename": "bravo.mp4", "title": "Bravo", "description": "Done"},
                {"clip_id": "clip-c", "clip_filename": "charlie.mp4", "title": "Charlie", "description": "Keep"},
            ]
            bridge._state_lock = threading.RLock()
            bridge._personalization = {
                "schema_version": 1,
                "events": [],
                "clips": {
                    "clip-a": {"clip_id": "clip-a", "clip_filename": "alpha.mp4"},
                    "clip-b": {"clip_id": "clip-b", "clip_filename": "bravo.mp4"},
                    "clip-c": {"clip_id": "clip-c", "clip_filename": "charlie.mp4"},
                },
            }
            bridge._personalization_lock = threading.RLock()
            bridge._save_personalization = lambda: None
            saves = []
            bridge._save_state = lambda: saves.append(True)

            with patch("api_bridge.CLIPS_DIR", temp_clips):
                result = bridge.delete_library_files(["alpha.mp4", "bravo.mp4", "missing.mp4"])

            self.assertTrue(result["ok"])
            self.assertEqual(result["deleted"], ["alpha.mp4", "bravo.mp4"])
            self.assertEqual(result["failed"], [{"filename": "missing.mp4", "error": "File not found"}])
            self.assertFalse(clip_a.exists())
            self.assertFalse(clip_b.exists())
            self.assertTrue(clip_c.exists())
            self.assertEqual([path.name for path in bridge._results], ["charlie.mp4"])
            self.assertEqual([moment["clip_id"] for moment in bridge._moments], ["clip-c"])
            self.assertEqual([item["clip_id"] for item in bridge._scheduled], ["clip-c"])
            self.assertTrue(bridge._personalization["clips"]["clip-a"]["rendered_file_deleted"])
            self.assertTrue(bridge._personalization["clips"]["clip-b"]["rendered_file_deleted"])
            self.assertNotIn("rendered_file_deleted", bridge._personalization["clips"]["clip-c"])
            self.assertEqual(saves, [True])

    def test_unique_clip_output_path_does_not_reuse_existing_render(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_clips = Path(temp_dir)
            existing = temp_clips / "Same Stem_viral1.mp4"
            existing.write_text("previous clip", encoding="utf-8")

            bridge = ApiBridge.__new__(ApiBridge)
            with patch("api_bridge.CLIPS_DIR", temp_clips):
                path = bridge._unique_clip_output_path("Same Stem", 1)

            self.assertEqual(path.name, "Same Stem_viral1_2.mp4")
            self.assertTrue(existing.exists())

    def test_clip_payload_url_escapes_special_filename_chars(self):
        CLIPS_DIR.mkdir(exist_ok=True)
        clip_path = CLIPS_DIR / "clip with # tag.mp4"
        clip_path.write_text("video", encoding="utf-8")
        try:
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._video_port = 12345
            bridge._moments = [{
                "clip_id": "clip-1",
                "source_id": "source-1",
                "primary_category": "high_energy",
                "moment_categories": {"primary": "high_energy"},
                "ai_moment_classification": {
                    "status": "ok",
                    "provider": "ollama",
                    "primary_category": "high_energy",
                },
            }]
            bridge._ensure_moment_identity = lambda moment, path: moment

            payload = bridge._clip_payload(0, clip_path)

            self.assertIn("clip%20with%20%23%20tag.mp4", payload["url"])
            self.assertEqual(payload["primary_category"], "high_energy")
            self.assertEqual(payload["moment_categories"]["primary"], "high_energy")
            self.assertEqual(payload["ai_moment_classification"]["provider"], "ollama")
        finally:
            clip_path.unlink(missing_ok=True)

    def test_list_all_clips_escapes_library_urls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_clips = Path(temp_dir)
            clip_path = temp_clips / "library clip #1.mp4"
            clip_path.write_text("video", encoding="utf-8")

            bridge = ApiBridge.__new__(ApiBridge)
            bridge._video_port = 12345
            bridge._results = [clip_path]
            bridge._moments = [{
                "clip_id": "clip-library",
                "source_id": "source-library",
                "primary_category": "commentary_or_review",
                "moment_categories": {
                    "primary": "commentary_or_review",
                    "confidence": 0.77,
                    "scores": {"commentary_or_review": 0.9, "low_value": 0.1},
                    "signals": {"transcript": ["private raw phrase"]},
                },
                "ai_moment_classification": {
                    "status": "model_not_ready",
                    "provider": "heuristic",
                    "primary_category": "commentary_or_review",
                    "selection_impact": "none",
                },
            }]
            bridge._scheduled = []
            bridge._state_lock = threading.RLock()
            bridge._prune_missing_results = lambda: 0

            with patch("api_bridge.CLIPS_DIR", temp_clips):
                result = bridge.list_all_clips()

            self.assertIn("library%20clip%20%231.mp4", result["clips"][0]["url"])
            self.assertTrue(result["clips"][0]["clip_id"])
            self.assertTrue(result["clips"][0]["source_id"])
            self.assertEqual(result["clips"][0]["primary_category"], "commentary_or_review")
            self.assertEqual(result["clips"][0]["moment_categories"]["primary"], "commentary_or_review")
            self.assertEqual(result["clips"][0]["moment_categories"]["confidence"], 0.77)
            self.assertNotIn("scores", result["clips"][0]["moment_categories"])
            self.assertNotIn("signals", result["clips"][0]["moment_categories"])
            self.assertEqual(result["clips"][0]["ai_moment_classification"]["provider"], "heuristic")
            self.assertEqual(result["clips"][0]["ai_moment_classification"]["selection_impact"], "none")
            for bulky_key in (
                "transcript",
                "ranker",
                "visual_diagnostics",
                "commentary_guard",
                "voice_profile",
                "quality_score",
                "selection_rank_score",
            ):
                self.assertNotIn(bulky_key, result["clips"][0])

    def test_source_title_context_is_sanitized_and_reaches_metadata_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            clip_path = Path(temp_dir) / "clip.mp4"
            clip_path.write_text("video", encoding="utf-8")
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [clip_path]
            bridge._moments = [{
                "clip_id": "clip-1",
                "source_id": "source-1",
                "source_stem": "source-stem",
                "transcript": "oh no run",
            }]
            bridge._scheduled = [{
                "clipIdx": 0,
                "clip_id": "clip-1",
                "source_id": "source-1",
                "source_stem": "source-stem",
                "title": "Old",
                "description_generated": "stale",
                "generated_description": "stale",
            }]
            bridge._state_lock = threading.RLock()
            bridge._save_state = lambda: None
            bridge._game_title_for_clip = lambda _idx: "Alan Wake"

            result = bridge.save_source_title_context(
                "source-1",
                "source-stem",
                r"Blind nursing home chapter C:\Users\ExpiredSoda\client_secrets.json api_key=secret123",
            )
            payload = bridge._clip_payload(0, clip_path, include_url=False)
            context = bridge._title_context_for_clip(0)
            sidecar = bridge._write_metadata_sidecar(0, "Title", "Alan Wake", "Description", "tags", context)
            sidecar_text = Path(sidecar).read_text(encoding="utf-8")

            self.assertTrue(result["ok"])
            self.assertIn("Blind nursing home chapter", result["creator_title_context"])
            self.assertIn("[local-path]", result["creator_title_context"])
            self.assertIn("api_key=[redacted]", result["creator_title_context"])
            self.assertNotIn("client_secrets.json", result["creator_title_context"])
            self.assertEqual(payload["creator_title_context"], result["creator_title_context"])
            self.assertEqual(context["creator_title_context"], result["creator_title_context"])
            self.assertEqual(bridge._scheduled[0]["description_generated"], "")
            self.assertIn("Creator Context: Blind nursing home chapter", sidecar_text)

    def test_classify_selected_moments_does_not_fake_ollama_when_skipped(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._infer_game_title_from_path = lambda path: "Test Game"
        selected = [{
            "moment": {
                "start": 0,
                "end": 30,
                "transcript": "Oh no he is right behind me run",
                "moment_categories": {"primary": "high_energy"},
                "primary_category": "high_energy",
                "ranker": {},
            },
            "candidate": {"candidate_rank": 1},
        }]

        with patch("api_bridge.is_ollama_model_ready", return_value=True):
            report = bridge._classify_selected_moments(
                selected,
                Path("A:/Videos/Test Game/source.mp4"),
                enabled=True,
                max_ollama=0,
            )

        classification = selected[0]["moment"]["ai_moment_classification"]
        self.assertTrue(report["ollama_ready"])
        self.assertEqual(report["ollama_attempted_count"], 0)
        self.assertEqual(classification["status"], "ollama_skipped_limit")
        self.assertEqual(classification["provider"], "heuristic")
        self.assertTrue(classification["fallback_used"])
        self.assertEqual(classification["selection_impact"], "none")

    def test_local_video_server_sends_thumbnail_safe_headers(self):
        source = (ROOT / "api_bridge.py").read_text(encoding="utf-8")

        self.assertNotIn('self.send_header("Access-Control-Allow-Origin", "*")', source)
        self.assertIn('self.send_header("Access-Control-Allow-Origin", allowed_origin)', source)
        self.assertIn('self.send_header("Access-Control-Allow-Headers", "Range, Content-Type")', source)
        self.assertIn("http.server.ThreadingHTTPServer", source)

    def test_local_video_server_origin_allowlist_is_loopback_only(self):
        self.assertEqual(_allowed_media_origin("null"), "null")
        self.assertEqual(_allowed_media_origin("http://localhost:3000"), "http://localhost:3000")
        self.assertEqual(_allowed_media_origin("http://127.0.0.1:3000"), "http://127.0.0.1:3000")
        self.assertEqual(_allowed_media_origin("http://[::1]:3000"), "http://[::1]:3000")
        self.assertIsNone(_allowed_media_origin("https://example.com"))
        self.assertIsNone(_allowed_media_origin("http://192.168.1.10:3000"))

    def test_delete_after_upload_toggle_persists_immediately(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._delete_after_upload = False
        saves = []
        bridge._save_state = lambda: saves.append(True)

        result = bridge.set_delete_after_upload(True)

        self.assertTrue(result["enabled"])
        self.assertEqual(saves, [True])

    def test_detection_preference_settings_are_sanitized(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._user_settings = {}
        bridge._save_state = lambda: None

        self.assertEqual(bridge.get_settings()["detection_preference"], "auto")
        self.assertEqual(bridge.get_settings()["processing_depth"], "balanced")

        bridge.save_settings({"detection_preference": "quality"})
        self.assertEqual(bridge.get_settings()["detection_preference"], "quality")

        bridge.save_settings({"detection_preference": "../quantity"})
        self.assertEqual(bridge.get_settings()["detection_preference"], "auto")

        bridge.save_settings({"processing_depth": "deep-analysis"})
        self.assertEqual(bridge.get_settings()["processing_depth"], "deep")

        bridge.save_settings({"processing_depth": "../deep"})
        self.assertEqual(bridge.get_settings()["processing_depth"], "balanced")

    def test_processing_depth_profile_maps_runtime_cost(self):
        fast = _processing_depth_profile("fast", "quality", 7200)
        balanced = _processing_depth_profile("balanced", "quality", 7200)
        deep = _processing_depth_profile("deep", "quality", 7200)

        self.assertEqual(_normalize_processing_depth("normal"), "balanced")
        self.assertEqual(fast["scene_mode"], "skip")
        self.assertEqual(balanced["scene_mode"], "sampled")
        self.assertEqual(deep["scene_mode"], "targeted")
        self.assertLess(fast["candidate_multiplier"], balanced["candidate_multiplier"])
        self.assertLess(balanced["candidate_multiplier"], deep["candidate_multiplier"])
        self.assertLess(balanced["candidate_pool_cap"], deep["candidate_pool_cap"])
        self.assertFalse(fast["visual_diagnostics"])
        self.assertFalse(fast["moment_category_ranking"])
        self.assertFalse(fast["ai_moment_classification"])
        self.assertFalse(fast["voice_profile_ranking"])
        self.assertTrue(balanced["moment_category_ranking"])
        self.assertTrue(deep["moment_category_ranking"])
        self.assertTrue(deep["ai_moment_classification"])
        self.assertIsNone(deep["voice_profile_ranking"])

    def test_deep_uses_lighter_candidate_whisper_than_final_model(self):
        self.assertEqual(_candidate_model_for_depth("deep", "large-v3"), "small")
        self.assertEqual(_candidate_model_for_depth("deep", "medium"), "small")
        self.assertEqual(_candidate_model_for_depth("deep", "base"), "base")

    def test_candidate_debug_recovery_rebuilds_selected_rows_in_start_order(self):
        bridge = ApiBridge.__new__(ApiBridge)
        payload = {
            "candidates": [
                {
                    "selected": True,
                    "start": 30,
                    "end": 45,
                    "candidate": {"candidate_rank": 2},
                    "final": {"start": 30, "end": 45, "transcript": "second"},
                    "shadow_scoring": {"learned_quality_score": 0.5},
                    "primary_category": "high_energy",
                    "moment_categories": {"primary": "high_energy"},
                    "music_lyrics_guard": {"status": "ok"},
                    "visual_diagnostics": {"status": "ok"},
                },
                {
                    "selected": False,
                    "start": 1,
                    "end": 9,
                    "final": {"start": 1, "end": 9},
                },
                {
                    "selected": True,
                    "start": 10,
                    "end": 20,
                    "candidate": {"candidate_rank": 1},
                    "selection": {"start": 10, "end": 20, "transcript": "first"},
                },
            ]
        }

        items = bridge._selected_items_from_candidate_debug(payload)

        self.assertEqual([item["moment"]["start"] for item in items], [10, 30])
        self.assertEqual([item["moment"]["end"] for item in items], [20, 45])
        self.assertEqual(items[0]["moment"]["duration"], 10)
        self.assertEqual(items[1]["shadow_scoring"]["learned_quality_score"], 0.5)
        self.assertEqual(items[1]["primary_category"], "high_energy")
        self.assertEqual(items[1]["moment"]["primary_category"], "high_energy")
        self.assertEqual(items[1]["moment"]["music_lyrics_guard"]["status"], "ok")
        self.assertEqual(items[1]["moment"]["visual_diagnostics"]["status"], "ok")
        self.assertEqual(items[0]["words"], [])

    def test_record_feedback_reuses_stored_learning_terms_after_clip_is_missing(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._results = []
        bridge._moments = []
        bridge._personalization_lock = threading.RLock()
        bridge._personalization = {
            "schema_version": 1,
            "events": [],
            "clips": {
                "clip_1": {
                    "clip_id": "clip_1",
                    "source_id": "source_1",
                    "source_stem": "source",
                    "clip_filename": "deleted.mp4",
                    "latest": {"like": True, "dislike": False, "favorite": False, "reason": ""},
                    "clip_snapshot": {
                        "learning_terms": ["panic chase", "right behind"],
                        "learning_terms_count": 2,
                    },
                    "learning_terms": ["panic chase", "right behind"],
                    "event_count": 1,
                }
            },
        }
        bridge._save_personalization = lambda: None

        result = bridge.record_feedback(
            {
                "clip_id": "clip_1",
                "source_id": "source_1",
                "clip_filename": "deleted.mp4",
                "event_type": "dislike",
                "active": True,
            }
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["event"]["learning_terms"], ["panic chase", "right behind"])
        self.assertEqual(result["clip"]["learning_terms"], ["panic chase", "right behind"])
        self.assertEqual(
            result["clip"]["clip_snapshot"]["learning_terms"],
            ["panic chase", "right behind"],
        )
        self.assertTrue(result["clip"]["latest"]["dislike"])
        self.assertFalse(result["clip"]["latest"]["like"])

    def test_record_feedback_preserves_per_action_reasons(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._results = []
        bridge._moments = []
        bridge._personalization_lock = threading.RLock()
        bridge._personalization = {"schema_version": 1, "events": [], "clips": {}}
        bridge._save_personalization = lambda: None

        liked = bridge.record_feedback(
            {
                "clip_id": "clip_reason",
                "source_id": "source_1",
                "clip_filename": "reason.mp4",
                "event_type": "like",
                "active": True,
                "reason": "Strong panic hook",
            }
        )
        favorited = bridge.record_feedback(
            {
                "clip_id": "clip_reason",
                "source_id": "source_1",
                "clip_filename": "reason.mp4",
                "event_type": "favorite",
                "active": True,
            }
        )
        disliked = bridge.record_feedback(
            {
                "clip_id": "clip_reason",
                "source_id": "source_1",
                "clip_filename": "reason.mp4",
                "event_type": "dislike",
                "active": True,
                "reason": "Wrong label",
            }
        )

        self.assertTrue(liked["ok"])
        self.assertEqual(liked["clip"]["latest"]["reasons"]["like"], "Strong panic hook")
        self.assertTrue(favorited["ok"])
        self.assertEqual(favorited["clip"]["latest"]["reasons"]["like"], "Strong panic hook")
        self.assertNotIn("favorite", favorited["clip"]["latest"]["reasons"])
        self.assertEqual(favorited["clip"]["latest"]["reason"], "Strong panic hook")
        self.assertTrue(disliked["ok"])
        self.assertFalse(disliked["clip"]["latest"]["like"])
        self.assertTrue(disliked["clip"]["latest"]["favorite"])
        self.assertNotIn("like", disliked["clip"]["latest"]["reasons"])
        self.assertEqual(disliked["clip"]["latest"]["reasons"]["dislike"], "Wrong label")
        self.assertEqual(disliked["clip"]["latest"]["reason"], "Wrong label")

    def test_record_feedback_migrates_legacy_reason_without_event_type(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._results = []
        bridge._moments = []
        bridge._personalization_lock = threading.RLock()
        bridge._personalization = {
            "schema_version": 1,
            "events": [],
            "clips": {
                "clip_legacy_reason": {
                    "clip_id": "clip_legacy_reason",
                    "source_id": "source_1",
                    "clip_filename": "legacy.mp4",
                    "latest": {"like": True, "dislike": False, "favorite": False, "reason": "Legacy strong hook"},
                }
            },
        }
        bridge._save_personalization = lambda: None

        result = bridge.record_feedback(
            {
                "clip_id": "clip_legacy_reason",
                "source_id": "source_1",
                "clip_filename": "legacy.mp4",
                "event_type": "favorite",
                "active": True,
            }
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["clip"]["latest"]["reasons"]["like"], "Legacy strong hook")
        self.assertEqual(result["clip"]["latest"]["reason"], "Legacy strong hook")
        self.assertTrue(result["clip"]["latest"]["favorite"])

    def test_record_feedback_parses_string_false_as_inactive(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._results = []
        bridge._moments = []
        bridge._personalization_lock = threading.RLock()
        bridge._personalization = {
            "schema_version": 1,
            "events": [],
            "clips": {
                "clip_string_false": {
                    "clip_id": "clip_string_false",
                    "latest": {"like": True, "reason": "Keep until inactive", "event_type": "like"},
                }
            },
        }
        bridge._save_personalization = lambda: None

        result = bridge.record_feedback(
            {
                "clip_id": "clip_string_false",
                "event_type": "like",
                "active": "false",
            }
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["clip"]["latest"]["like"])
        self.assertNotIn("like", result["clip"]["latest"]["reasons"])

    def test_delete_clip_keeps_personalization_and_marks_file_deleted(self):
        clip_path = CLIPS_DIR / "feedback delete persists test.mp4"
        clip_path.write_bytes(b"clip")
        try:
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [clip_path]
            bridge._moments = [{"clip_id": "clip_delete", "source_id": "source_1"}]
            bridge._scheduled = []
            bridge._state_lock = threading.RLock()
            bridge._personalization_lock = threading.RLock()
            bridge._personalization = {
                "schema_version": 1,
                "events": [],
                "clips": {
                    "clip_delete": {
                        "clip_id": "clip_delete",
                        "clip_filename": clip_path.name,
                        "latest": {"dislike": True},
                        "learning_terms": ["menu", "loading"],
                    }
                },
            }
            bridge._save_state = lambda: None
            bridge._save_personalization = lambda: None

            result = bridge.delete_clip(0)

            self.assertTrue(result["ok"])
            self.assertFalse(clip_path.exists())
            entry = bridge._personalization["clips"]["clip_delete"]
            self.assertTrue(entry["rendered_file_deleted"])
            self.assertEqual(entry["deleted_filename"], clip_path.name)
            self.assertEqual(entry["learning_terms"], ["menu", "loading"])
        finally:
            try:
                clip_path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_candidate_debug_recovery_defines_and_persists_stage_timings(self):
        source = inspect.getsource(ApiBridge._run_candidate_debug_recovery)

        self.assertIn("stage_timings: dict[str, float] = {}", source)
        self.assertIn('stage_timings["final_render"]', source)
        self.assertIn('recovered["stage_timings"] = dict(stage_timings)', source)
        self.assertIn('"trim_adjusted_start": m.get("trim_adjusted_start")', source)
        self.assertIn('"selection_primary_category": selection_primary_category', source)
        self.assertIn('"ranking_primary_category": ranking_primary_category', source)
        self.assertIn('"final_primary_category": final_primary_category', source)
        self.assertIn('"final_moment_categories": final_moment_categories', source)

    def test_final_render_preserves_selected_window_and_records_trim_suggestion(self):
        source = inspect.getsource(ApiBridge._run_pipeline)

        self.assertIn("selected_start, selected_end = int(m[\"start\"]), int(m[\"end\"])", source)
        self.assertIn("_subtitle_words_for_render_start", source)
        self.assertIn('m["trim_adjusted_start"] = trim_start', source)
        self.assertIn('m["subtitle_timing_offset"]', source)
        self.assertIn('m["trim_adjusted_from_selected"] = (', source)
        self.assertIn('m["start"] = selected_start', source)
        self.assertIn('"selected_start": m.get("selected_start", start)', source)
        self.assertIn('"trim_adjusted_from_selected": m.get("trim_adjusted_from_selected", False)', source)
        self.assertIn('"selection_primary_category": selection_primary_category', source)
        self.assertIn('"ranking_primary_category": ranking_primary_category', source)
        self.assertIn('"final_primary_category": final_primary_category', source)
        self.assertIn('"final_moment_categories": final_moment_categories', source)
        self.assertIn('m["ai_moment_classification_stage"] = "selection_pre_render"', source)

    def test_ai_shadow_report_stays_deep_only_and_debug_only(self):
        source = inspect.getsource(ApiBridge._run_pipeline)

        self.assertIn('processing_depth == "deep"', source)
        self.assertIn("_classify_ai_moment_shadow(", source)
        self.assertIn("classification_cache=ai_shadow_cache", source)
        self.assertIn("ai_moment_classification_shadow=ai_moment_classification_shadow", source)

    def test_no_quality_clip_outcome_completes_with_structured_warning(self):
        source = inspect.getsource(ApiBridge._run_pipeline)

        self.assertIn('_timing_payload("no_quality_clips"', source)
        self.assertIn('"completion_state": "no_quality_clips"', source)
        self.assertIn("write_debug_report(\n                        run_debug_path", source)
        self.assertIn("window.onPipelineComplete(true, 0, 0, null", source)
        self.assertNotIn('return self._error("No high-quality clips found', source)

    def test_trim_relative_subtitle_words_shift_to_selected_render_window(self):
        words = [
            {"text": "hello", "start": 0.4, "end": 0.9},
            {"text": "there", "start": 1.0, "end": 1.35},
        ]

        shifted = _subtitle_words_for_render_start(words, trim_start=105, render_start=100)

        self.assertEqual(shifted[0]["start"], 5.4)
        self.assertEqual(shifted[0]["end"], 5.9)
        self.assertEqual(shifted[1]["start"], 6.0)
        self.assertEqual(shifted[1]["end"], 6.35)
        self.assertEqual(words[0]["start"], 0.4)

    def test_voice_profile_scoring_handles_missing_or_empty_wav(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._voice_profile = {
            "schema_version": 1,
            "feature_version": 1,
            "enabled": True,
            "enrolled": True,
            "centroid": [0.1] * 8,
            "sample_count": 1,
            "total_active_seconds": 2.0,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.wav"
            result = bridge._voice_profile_score_for_wav(missing)
            self.assertEqual(result["reason"], "wav_missing")

            empty = Path(temp_dir) / "empty.wav"
            empty.write_bytes(b"")
            result = bridge._voice_profile_score_for_wav(empty)
            self.assertEqual(result["reason"], "wav_empty")

    def test_voice_profile_temp_wav_cleanup_is_narrow(self):
        bridge = ApiBridge.__new__(ApiBridge)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            stale = temp_path / "voice_profile_dead.wav"
            keep = temp_path / "voice_profile_dead.txt"
            stale.write_bytes(b"old wav")
            keep.write_text("not a wav", encoding="utf-8")

            with patch("api_bridge.SUBTITLES_DIR", temp_path):
                bridge._cleanup_voice_profile_temp_wavs()

            self.assertFalse(stale.exists())
            self.assertTrue(keep.exists())

    def test_voice_profile_enrollment_eligibility_uses_creator_guard_metadata(self):
        creator = {
            "analysis_word_count": 14,
            "commentary_guard": {
                "policy": "creator",
                "summary": {
                    "primary_label": "creator_commentary",
                    "creator_word_ratio": 0.8,
                    "game_narration_word_ratio": 0.0,
                    "confidence": 0.9,
                },
            },
        }
        self.assertTrue(ApiBridge._voice_profile_enrollment_eligibility(creator)["eligible"])

        game_narration = {
            "analysis_word_count": 16,
            "commentary_guard": {
                "policy": "creator",
                "summary": {
                    "primary_label": "game_narration",
                    "creator_word_ratio": 0.05,
                    "game_narration_word_ratio": 0.9,
                    "confidence": 0.86,
                },
            },
        }
        self.assertEqual(
            ApiBridge._voice_profile_enrollment_eligibility(game_narration)["reason"],
            "likely_game_narration",
        )

        music = {
            "analysis_word_count": 18,
            "music_lyrics_guard": {
                "selection_penalty": 0.12,
                "lyric_likelihood": 0.76,
                "creator_exception_score": 0.1,
            },
        }
        self.assertEqual(
            ApiBridge._voice_profile_enrollment_eligibility(music)["reason"],
            "likely_music_or_lyrics",
        )
        self.assertEqual(
            ApiBridge._voice_profile_enrollment_eligibility({"analysis_word_count": 3})["reason"],
            "too_few_creator_words",
        )
        self.assertEqual(
            ApiBridge._voice_profile_enrollment_eligibility({})["reason"],
            "missing_analysis_metadata",
        )
        mixed = {
            "analysis_word_count": 18,
            "commentary_guard": {
                "policy": "creator",
                "output_changed": True,
                "summary": {
                    "primary_label": "creator_commentary",
                    "creator_word_ratio": 0.7,
                    "game_narration_word_ratio": 0.2,
                    "confidence": 0.8,
                },
            },
        }
        self.assertEqual(
            ApiBridge._voice_profile_enrollment_eligibility(mixed)["reason"],
            "mixed_or_filtered_speech",
        )
        all_speech = {
            "analysis_word_count": 18,
            "commentary_guard": {
                "policy": "all",
                "summary": {
                    "primary_label": "creator_commentary",
                    "creator_word_ratio": 0.8,
                    "game_narration_word_ratio": 0.0,
                    "confidence": 0.9,
                },
            },
        }
        self.assertEqual(
            ApiBridge._voice_profile_enrollment_eligibility(all_speech)["reason"],
            "not_creator_policy",
        )
        dual_track_creator = {
            "analysis_word_count": 18,
            "audio_source": {
                "stream_count": 2,
                "selected_stream": 1,
                "selected_reason": "creator_phrase_signal",
                "selected_confidence": 0.96,
                "subtitle_policy": "creator",
            },
            "moment_categories": {
                "signals": {
                    "speech_source": "creator_commentary",
                    "speech_source_confidence": 0.92,
                    "creator_speech": 0.9,
                    "game_speech": 0.0,
                }
            },
        }
        self.assertEqual(
            ApiBridge._voice_profile_enrollment_eligibility(dual_track_creator)["reason"],
            "dual_track_creator_stream",
        )

    def test_voice_profile_enrollment_skips_game_music_and_low_speech_clips(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._voice_profile_lock = threading.RLock()
        bridge._voice_profile = empty_voice_profile(enabled=True)
        bridge._user_settings = {}
        bridge._prune_missing_results = lambda: None
        bridge._safe_clip_path = lambda path: Path(path)
        bridge._probe_media_duration = lambda path, default=0.0: 12.0
        bridge._save_voice_profile = lambda: None

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            game = temp_path / "game.mp4"
            music = temp_path / "music.mp4"
            creator = temp_path / "creator.mp4"
            for path in (game, music, creator):
                path.write_bytes(b"fake mp4")

            bridge._results = [str(game), str(music), str(creator)]
            bridge._moments = [
                {
                    "analysis_word_count": 18,
                    "commentary_guard": {
                        "policy": "creator",
                        "summary": {
                            "primary_label": "game_narration",
                            "creator_word_ratio": 0.05,
                            "game_narration_word_ratio": 0.9,
                            "confidence": 0.88,
                        },
                    },
                },
                {
                    "analysis_word_count": 18,
                    "music_lyrics_guard": {
                        "selection_penalty": 0.1,
                        "lyric_likelihood": 0.72,
                        "creator_exception_score": 0.1,
                    },
                },
                {
                    "analysis_word_count": 18,
                    "commentary_guard": {
                        "policy": "creator",
                        "summary": {
                            "primary_label": "creator_commentary",
                            "creator_word_ratio": 0.72,
                            "game_narration_word_ratio": 0.0,
                            "confidence": 0.91,
                        },
                    },
                },
            ]

            with (
                patch("api_bridge.SUBTITLES_DIR", temp_path),
                patch("api_bridge.VOICE_PROFILE_FILE", temp_path / "voice_profile.json"),
                patch("api_bridge.extract_audio_clip", return_value=True) as extract_audio,
                patch(
                    "api_bridge.extract_voice_features",
                    return_value={
                        "ok": True,
                        "features": [0.2] * VOICE_PROFILE_FEATURE_COUNT,
                        "active_seconds": 2.5,
                    },
                ),
            ):
                result = bridge.enroll_voice_profile_from_current_clips()

        self.assertTrue(result["ok"])
        self.assertEqual(result["enrolled_samples"], 1)
        self.assertEqual(result["eligible_candidates"], 1)
        self.assertEqual(extract_audio.call_count, 1)
        self.assertEqual(extract_audio.call_args.args[0], creator)
        skipped_reasons = {item["reason"] for item in result["skipped"]}
        self.assertIn("likely_game_narration", skipped_reasons)
        self.assertIn("likely_music_or_lyrics", skipped_reasons)
        self.assertEqual(result["skipped_by_reason"]["likely_game_narration"], 1)
        self.assertEqual(result["skipped_by_reason"]["likely_music_or_lyrics"], 1)
        self.assertNotIn("clip", result["skipped"][0])

    def test_voice_profile_enrollment_scans_past_first_sixteen_metadata_rows(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._voice_profile_lock = threading.RLock()
        bridge._voice_profile = empty_voice_profile(enabled=True)
        bridge._user_settings = {}
        bridge._prune_missing_results = lambda: None
        bridge._safe_clip_path = lambda path: Path(path)
        bridge._probe_media_duration = lambda path, default=0.0: 12.0
        bridge._save_voice_profile = lambda: None

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            paths = []
            moments = []
            for index in range(18):
                path = temp_path / f"clip_{index}.mp4"
                path.write_bytes(b"fake mp4")
                paths.append(str(path))
                moments.append({"analysis_word_count": 12})
            moments[-1] = {
                "analysis_word_count": 18,
                "commentary_guard": {
                    "policy": "creator",
                    "summary": {
                        "primary_label": "creator_commentary",
                        "creator_word_ratio": 0.7,
                        "game_narration_word_ratio": 0.0,
                        "confidence": 0.9,
                    },
                },
            }
            bridge._results = paths
            bridge._moments = moments

            with (
                patch("api_bridge.SUBTITLES_DIR", temp_path),
                patch("api_bridge.VOICE_PROFILE_FILE", temp_path / "voice_profile.json"),
                patch("api_bridge.extract_audio_clip", return_value=True) as extract_audio,
                patch(
                    "api_bridge.extract_voice_features",
                    return_value={
                        "ok": True,
                        "features": [0.2] * VOICE_PROFILE_FEATURE_COUNT,
                        "active_seconds": 2.5,
                    },
                ),
            ):
                result = bridge.enroll_voice_profile_from_current_clips()

        self.assertTrue(result["ok"])
        self.assertEqual(result["enrolled_samples"], 1)
        self.assertEqual(result["eligible_candidates"], 1)
        self.assertEqual(extract_audio.call_count, 1)
        self.assertEqual(Path(extract_audio.call_args.args[0]).name, "clip_17.mp4")
        self.assertEqual(result["skipped_by_reason"]["missing_commentary_guard"], 17)

    def test_voice_profile_enrollment_uses_original_speech_stream_for_dual_track(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._voice_profile_lock = threading.RLock()
        bridge._voice_profile = empty_voice_profile(enabled=True)
        bridge._user_settings = {}
        bridge._prune_missing_results = lambda: None
        bridge._safe_clip_path = lambda path: Path(path)
        bridge._probe_media_duration = lambda path, default=0.0: 28.0
        bridge._save_voice_profile = lambda: None

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            rendered = temp_path / "rendered.mp4"
            source = temp_path / "source.mkv"
            rendered.write_bytes(b"fake mp4")
            source.write_bytes(b"fake mkv")
            bridge._results = [str(rendered)]
            bridge._moments = [{
                "analysis_word_count": 18,
                "source_path": str(source),
                "render_start": 120.2,
                "render_end": 150.4,
                "speech_stream": 1,
                "audio_source": {
                    "stream_count": 2,
                    "selected_stream": 1,
                    "selected_reason": "creator_phrase_signal",
                    "selected_confidence": 0.96,
                    "subtitle_policy": "creator",
                },
                "moment_categories": {
                    "signals": {
                        "speech_source": "creator_commentary",
                        "speech_source_confidence": 0.92,
                        "creator_speech": 0.9,
                        "game_speech": 0.0,
                    }
                },
            }]

            with (
                patch("api_bridge.SUBTITLES_DIR", temp_path),
                patch("api_bridge.VOICE_PROFILE_FILE", temp_path / "voice_profile.json"),
                patch("api_bridge.extract_audio_clip", return_value=True) as extract_audio,
                patch(
                    "api_bridge.extract_voice_features",
                    return_value={
                        "ok": True,
                        "features": [0.2] * VOICE_PROFILE_FEATURE_COUNT,
                        "active_seconds": 3.0,
                    },
                ),
            ):
                result = bridge.enroll_voice_profile_from_current_clips()

        self.assertTrue(result["ok"])
        self.assertEqual(result["enrolled_samples"], 1)
        self.assertEqual(extract_audio.call_args.args[0], source)
        self.assertEqual(extract_audio.call_args.args[1], 120)
        self.assertEqual(extract_audio.call_args.args[2], 151)
        self.assertEqual(extract_audio.call_args.kwargs["audio_stream"], 1)

    def test_voice_profile_enrollment_reports_all_rejected_without_audio_extract(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._voice_profile_lock = threading.RLock()
        bridge._voice_profile = empty_voice_profile(enabled=True)
        bridge._user_settings = {}
        bridge._prune_missing_results = lambda: None
        bridge._safe_clip_path = lambda path: Path(path)
        bridge._probe_media_duration = lambda path, default=0.0: 12.0
        bridge._save_voice_profile = lambda: None

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            clip = temp_path / "game.mp4"
            clip.write_bytes(b"fake mp4")
            bridge._results = [str(clip)]
            bridge._moments = [
                {
                    "analysis_word_count": 16,
                    "commentary_guard": {
                        "policy": "creator",
                        "summary": {
                            "primary_label": "game_narration",
                            "creator_word_ratio": 0.0,
                            "game_narration_word_ratio": 0.95,
                            "confidence": 0.9,
                        },
                    },
                }
            ]

            with (
                patch("api_bridge.VOICE_PROFILE_FILE", temp_path / "voice_profile.json"),
                patch("api_bridge.extract_audio_clip", return_value=True) as extract_audio,
            ):
                result = bridge.enroll_voice_profile_from_current_clips()

        self.assertIn("No usable creator-commentary voice samples", result["error"])
        self.assertEqual(result["eligible_candidates"], 0)
        self.assertEqual(result["skipped"][0]["reason"], "likely_game_narration")
        extract_audio.assert_not_called()

    def test_voice_profile_ranking_setting_is_opt_in_and_sanitized(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._user_settings = {}
        saves = []
        bridge._save_state = lambda: saves.append(True)

        self.assertFalse(bridge.get_settings()["voice_profile_ranking"])

        bridge.save_settings({"voice_profile_ranking": "true"})
        self.assertTrue(bridge.get_settings()["voice_profile_ranking"])

        bridge.save_settings({"voice_profile_ranking": "../true"})
        self.assertFalse(bridge.get_settings()["voice_profile_ranking"])

        result = bridge.set_voice_profile_ranking_enabled(True)
        self.assertTrue(result["voice_profile"]["ranking_enabled"])
        self.assertEqual(saves, [True, True, True])

    def test_visual_diagnostics_setting_is_sanitized(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._user_settings = {}
        bridge._save_state = lambda: None

        self.assertTrue(bridge.get_settings()["visual_diagnostics"])

        bridge.save_settings({"visual_diagnostics": "false"})
        self.assertFalse(bridge.get_settings()["visual_diagnostics"])

        bridge.save_settings({"visual_diagnostics": "../true"})
        self.assertTrue(bridge.get_settings()["visual_diagnostics"])

    def test_ai_moment_classification_setting_is_opt_in_and_sanitized(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._user_settings = {}
        bridge._save_state = lambda: None

        self.assertFalse(bridge.get_settings()["ai_moment_classification"])

        bridge.save_settings({"ai_moment_classification": "true"})
        self.assertTrue(bridge.get_settings()["ai_moment_classification"])

        bridge.save_settings({"ai_moment_classification": "../true"})
        self.assertFalse(bridge.get_settings()["ai_moment_classification"])

    def test_moment_category_ranking_setting_is_opt_in_and_sanitized(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._user_settings = {}
        bridge._save_state = lambda: None

        self.assertFalse(bridge.get_settings()["moment_category_ranking"])

        bridge.save_settings({"moment_category_ranking": "true"})
        self.assertTrue(bridge.get_settings()["moment_category_ranking"])

        bridge.save_settings({"moment_category_ranking": "../true"})
        self.assertFalse(bridge.get_settings()["moment_category_ranking"])

    def test_subtitle_style_setting_is_sanitized(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._user_settings = {}
        bridge._save_state = lambda: None

        bridge.save_settings({"subtitle_style": "none"})
        self.assertEqual(bridge.get_settings()["subtitle_style"], "none")

        bridge.save_settings({"subtitle_style": "../tiktok"})
        self.assertNotEqual(bridge.get_settings()["subtitle_style"], "../tiktok")

    def test_audio_source_settings_are_sanitized(self):
        self.assertEqual(
            _normalize_audio_source_settings({"audio_source": {"mode": "stream", "stream": "1"}}),
            {"mode": "stream", "stream": 1, "commentary_guard": True, "subtitle_policy": "creator"},
        )
        for value in (-1, 32, "../../1", "nan", ""):
            result = _normalize_audio_source_settings({"audio_source": {"mode": "stream", "stream": value}})
            self.assertEqual(result["mode"], "auto")
            self.assertIsNone(result["stream"])

        result = _normalize_audio_source_settings({
            "audio_source": {"mode": "stream", "stream": 0, "commentary_guard": "false"}
        })
        self.assertEqual(result["mode"], "stream")
        self.assertEqual(result["stream"], 0)
        self.assertFalse(result["commentary_guard"])
        self.assertEqual(result["subtitle_policy"], "creator")

        result = _normalize_audio_source_settings({
            "audio_source": {"mode": "auto", "subtitle_policy": "all"}
        })
        self.assertEqual(result["subtitle_policy"], "all")
        result = _normalize_audio_source_settings({
            "audio_source": {"mode": "auto", "subtitle_focus": "game"}
        })
        self.assertEqual(result["subtitle_policy"], "game")
        for value in ("../game", "prefer_game", {"bad": "value"}, 4):
            result = _normalize_audio_source_settings({
                "audio_source": {"mode": "auto", "subtitle_policy": value}
            })
            self.assertEqual(result["subtitle_policy"], "creator")

    def test_probe_audio_sources_defers_remote_and_rejects_folders(self):
        bridge = ApiBridge.__new__(ApiBridge)

        remote = bridge.probe_audio_sources("https://youtube.com/watch?v=abc")
        self.assertEqual(remote["mode"], "deferred")
        scheme_less_remote = bridge.probe_audio_sources("youtu.be/abc")
        self.assertEqual(scheme_less_remote["mode"], "deferred")

        with tempfile.TemporaryDirectory() as temp_dir:
            folder = bridge.probe_audio_sources(temp_dir)
            self.assertEqual(folder["mode"], "error")
            self.assertIn("folder", folder["message"].lower())

            missing = bridge.probe_audio_sources(str(Path(temp_dir) / "missing.mp4"))
            self.assertEqual(missing["mode"], "error")
            self.assertIn("not found", missing["message"].lower())

    def test_probe_audio_sources_distinguishes_probe_failure_from_no_audio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            clip_path = Path(temp_dir) / "source.mp4"
            clip_path.write_text("video", encoding="utf-8")
            bridge = ApiBridge.__new__(ApiBridge)

            with patch("api_bridge.get_audio_streams", return_value=()), \
                 patch("api_bridge.get_last_audio_stream_diagnostics", return_value={"status": "timeout"}):
                result = bridge.probe_audio_sources(str(clip_path))
            self.assertEqual(result["mode"], "error")
            self.assertIn("timed out", result["message"].lower())

            with patch("api_bridge.get_audio_streams", return_value=()), \
                 patch("api_bridge.get_last_audio_stream_diagnostics", return_value={"status": "no_audio"}):
                result = bridge.probe_audio_sources(str(clip_path))
            self.assertEqual(result["mode"], "no_audio")
            self.assertIn("No audio", result["message"])

    def test_probe_audio_sources_returns_public_local_streams(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            clip_path = Path(temp_dir) / "source.mp4"
            clip_path.write_text("video", encoding="utf-8")
            bridge = ApiBridge.__new__(ApiBridge)
            streams = (
                {"ordinal": 0, "index": 1, "title": "Desktop Audio", "codec": "aac", "channels": 2, "layout": "stereo"},
                {"ordinal": 1, "index": 3, "title": "Microphone", "codec": "aac", "channels": 1, "layout": "mono"},
            )
            with patch("api_bridge.get_audio_streams", return_value=streams), \
                 patch("api_bridge.pick_voice_stream_ordinal", return_value=1):
                result = bridge.probe_audio_sources(str(clip_path))

            self.assertEqual(result["mode"], "multi")
            self.assertEqual(result["stream_count"], 2)
            self.assertEqual(result["recommended_stream"], 1)
            self.assertEqual(result["streams"][0]["ordinal"], 0)
            self.assertEqual(result["streams"][1]["likely_role"], "commentary")

    def test_downloaders_use_bounded_network_retries(self):
        api_source = (ROOT / "api_bridge.py").read_text(encoding="utf-8")
        downloader_source = (ROOT / "downloader.py").read_text(encoding="utf-8")

        for source in (api_source, downloader_source):
            self.assertIn('"socket_timeout": 30', source)
            self.assertIn('"retries": 3', source)
            self.assertIn('"fragment_retries": 3', source)

    def test_disconnect_youtube_marks_affected_scheduled_items(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._scheduled = [
            {"account_id": "account-a", "title": "A"},
            {"account_id": "account-b", "title": "B"},
            {"title": "Default"},
        ]
        bridge._save_state = lambda: None
        bridge._js_messages = []
        bridge._js = bridge._js_messages.append

        with patch("api_bridge.list_accounts", side_effect=[
            [{"id": "account-a"}, {"id": "account-b"}],
            [{"id": "account-b"}],
        ]), patch("api_bridge.disconnect") as disconnect:
            result = bridge.disconnect_youtube("account-a")

        self.assertTrue(result["ok"])
        disconnect.assert_called_once_with("account-a")
        self.assertEqual(bridge._scheduled[0]["scheduler_status"], "account_disconnected")
        self.assertNotIn("scheduler_status", bridge._scheduled[1])
        self.assertNotIn("scheduler_status", bridge._scheduled[2])
        self.assertTrue(any("onScheduleUpdated" in msg for msg in bridge._js_messages))

    def test_subtitle_preview_url_uses_latest_safe_clip(self):
        CLIPS_DIR.mkdir(exist_ok=True)
        old_clip = CLIPS_DIR / "old preview clip.mp4"
        latest_clip = CLIPS_DIR / "latest preview #clip.mp4"
        old_clip.write_text("old", encoding="utf-8")
        latest_clip.write_text("latest", encoding="utf-8")
        try:
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._video_port = 12345
            bridge._results = [old_clip, latest_clip]
            bridge._moments = []
            bridge._scheduled = []
            bridge._state_lock = threading.RLock()
            bridge._save_state = lambda: None
            bridge._ensure_moment_identity = lambda moment, path: moment

            result = bridge.get_subtitle_preview_url()

            self.assertEqual(result["filename"], latest_clip.name)
            self.assertIn("latest%20preview%20%23clip.mp4", result["url"])
        finally:
            old_clip.unlink(missing_ok=True)
            latest_clip.unlink(missing_ok=True)

    def test_auto_delete_prunes_deleted_clip_from_results(self):
        CLIPS_DIR.mkdir(exist_ok=True)
        clip_path = CLIPS_DIR / "auto delete prune test.mp4"
        clip_path.write_text("video", encoding="utf-8")
        try:
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [clip_path]
            bridge._moments = [{"clip_id": "clip-delete"}]
            bridge._scheduled = [{"clip_id": "clip-delete", "clipIdx": 0, "uploaded": True}]
            bridge._window = None
            bridge._pending_js = []
            bridge._state_lock = threading.RLock()
            saves = []
            bridge._save_state = lambda: saves.append(True)

            bridge._delete_uploaded_clip(0, clip_path)

            self.assertFalse(clip_path.exists())
            self.assertEqual(bridge._results, [])
            self.assertEqual(bridge._moments, [])
            self.assertTrue(any("onClipDeleted" in call for call in bridge._pending_js))
            self.assertTrue(saves)
        finally:
            clip_path.unlink(missing_ok=True)

    def test_start_upload_rejects_when_upload_lock_is_held(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._processing = False
        bridge._upload_lock = threading.Lock()
        bridge._upload_lock.acquire()
        try:
            result = bridge.start_upload([{"title": "Clip", "privacy": "private"}], None, None)
        finally:
            bridge._upload_lock.release()

        self.assertIn("already in progress", result["error"])

    def test_stale_schedule_identity_does_not_fall_back_to_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            current = temp / "current.mp4"
            current.write_text("video", encoding="utf-8")

            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [current]
            bridge._moments = [{"clip_id": "current-id"}]

            normalized = bridge._normalize_scheduled_items([
                {
                    "clipIdx": 0,
                    "clip_id": "deleted-id",
                    "clip_filename": "deleted.mp4",
                    "title": "Deleted",
                }
            ])

            self.assertEqual(normalized, [])

    def test_legacy_schedule_without_identity_can_use_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            current = temp / "current.mp4"
            current.write_text("video", encoding="utf-8")

            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [current]
            bridge._moments = [{"clip_id": "current-id"}]
            bridge._description_profile = lambda: {"custom_text": "", "auto_hashtags": True}
            bridge._schedule_game_title = lambda item, idx: ""
            bridge._title_context_for_clip = lambda idx: {}
            bridge._compose_clip_description = lambda title, game_title, **kwargs: {
                "description": title,
                "final_description": title,
                "generated_description": title,
                "description_custom_text": "",
                "description_auto_hashtags": True,
                "recommended_hashtags": [],
            }

            normalized = bridge._normalize_scheduled_items([{"clipIdx": 0, "title": "Legacy"}])

            self.assertEqual(len(normalized), 1)
            self.assertEqual(normalized[0]["clip_id"], "current-id")


if __name__ == "__main__":
    unittest.main()
