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
    _selected_audio_stream_profile,
    _candidate_transcription_chunks,
    _transcribe_candidate_wav_chunks,
    _normalize_audio_source_settings,
    _normalize_generation_mode,
    _normalize_montage_settings,
    _normalize_processing_depth,
    _processing_depth_profile,
    _clip_speech_policy_summary,
    _subtitle_words_for_render_start,
)
from config import CLIPS_DIR  # noqa: E402
from downloader import resolve_downloaded_path  # noqa: E402
from voice_profile import VOICE_PROFILE_FEATURE_COUNT, empty_voice_profile  # noqa: E402


class ApiBridgePathSafetyTests(unittest.TestCase):
    def test_custom_output_dir_controls_generated_clip_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._user_settings = {"output_dir": temp_dir}

            clips_dir = bridge._clips_dir()
            output = bridge._unique_clip_output_path("sample source", 1)

            self.assertEqual(clips_dir, Path(temp_dir).resolve())
            self.assertEqual(output.parent, Path(temp_dir).resolve())
            self.assertTrue(output.name.endswith("_viral1.mp4"))

    def test_youtube_download_metadata_feeds_game_identity_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            clip_path = Path(temp_dir) / "Alan_Wake_Part_4.mp4"
            clip_path.write_text("video", encoding="utf-8")
            bridge = ApiBridge.__new__(ApiBridge)
            compact = bridge._compact_download_info(
                {
                    "title": "Alan Wake Remastered - Part 4 - Getting Chased",
                    "uploader": "Expired Soda",
                    "webpage_url": "https://youtube.com/watch?v=abc",
                    "categories": ["Gaming"],
                    "tags": ["Alan Wake", "horror"],
                    "description": "Blind run. Game: Alan Wake Remastered.",
                },
                "https://youtube.com/watch?v=abc",
            )
            bridge._download_info_by_path = {str(clip_path.resolve()): compact}

            context = bridge._youtube_context_for_source(clip_path)

        self.assertEqual(context["title"], "Alan Wake Remastered - Part 4 - Getting Chased")
        self.assertIn("YouTube title: Alan Wake Remastered", context["context_text"])
        self.assertIn("Game: Alan Wake Remastered", context["context_text"])
        self.assertEqual(context["tags"], ["Alan Wake", "horror"])

    def test_remembered_source_game_identity_is_reused_without_network(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            clip_path = Path(temp_dir) / "Alan_Wake_Part_4.mp4"
            source_id = ApiBridge.__new__(ApiBridge)._source_id_for(clip_path)
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._source_context = {
                source_id: {
                    "source_id": source_id,
                    "source_path": str(clip_path.resolve()),
                    "game_title": "Alan Wake",
                    "game_identity": {
                        "status": "cache_hit",
                        "title": "Alan Wake",
                        "qid": "Q575505",
                        "confidence": 0.91,
                        "game_context": {
                            "status": "cache_hit",
                            "qid": "Q575505",
                            "label": "Alan Wake",
                        },
                    },
                    "game_context": {
                        "status": "cache_hit",
                        "qid": "Q575505",
                        "label": "Alan Wake",
                    },
                }
            }
            bridge._download_info_by_path = {}

            with patch("api_bridge.resolve_game_identity") as resolver:
                identity = bridge._game_identity_for_source(clip_path, allow_network=True)

        resolver.assert_not_called()
        self.assertEqual(identity["qid"], "Q575505")
        self.assertEqual(identity["title"], "Alan Wake")

    def test_source_game_hint_is_passed_to_identity_resolver_and_persisted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            clip_path = Path(temp_dir) / "unknown_capture.mp4"
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._source_context = {}
            bridge._download_info_by_path = {}
            bridge._infer_game_title_from_path = lambda _path: "Unknown Capture"

            with patch("api_bridge.resolve_game_identity", return_value={
                "schema_version": 1,
                "status": "ok",
                "title": "Alan Wake",
                "qid": "Q575505",
                "confidence": 0.94,
                "game_context": {
                    "schema_version": 1,
                    "status": "ok",
                    "qid": "Q575505",
                    "label": "Alan Wake",
                    "facts": {"genres": ["survival horror"]},
                },
            }) as resolver:
                identity = bridge._game_identity_for_source(
                    clip_path,
                    allow_network=True,
                    explicit_title="Alan Wake",
                )

            kwargs = resolver.call_args.kwargs
            self.assertEqual(kwargs["explicit_title"], "Alan Wake")
            self.assertEqual(identity["title"], "Alan Wake")
            records = list(bridge._source_context.values())
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["game_title_hint"], "Alan Wake")
            self.assertEqual(records[0]["game_identity"]["qid"], "Q575505")

    def test_game_identity_source_memory_persists_immediately(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "viria_state.json"
            clip_path = Path(temp_dir) / "alan wake clip.mp4"
            clip_path.write_text("video", encoding="utf-8")
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._state_lock = threading.RLock()
            bridge._results = [clip_path]
            bridge._moments = [{}]
            bridge._scheduled = []
            bridge._upload_history = []
            bridge._delete_after_upload = False
            bridge._user_settings = {}
            bridge._download_info_by_path = {}
            bridge._source_context = {}

            with patch("api_bridge.STATE_FILE", state_file), patch("api_bridge.resolve_game_identity", return_value={
                "schema_version": 1,
                "status": "ok",
                "title": "Alan Wake",
                "qid": "Q575505",
                "confidence": 0.94,
                "game_context": {
                    "schema_version": 1,
                    "status": "ok",
                    "qid": "Q575505",
                    "label": "Alan Wake",
                    "facts": {"genres": ["survival horror"]},
                },
            }):
                bridge._game_identity_for_source(
                    clip_path,
                    allow_network=True,
                    explicit_title="Alan Wake",
                )

            data = json.loads(state_file.read_text(encoding="utf-8"))
            sources = data["source_context"]["sources"]
            self.assertEqual(len(sources), 1)
            record = next(iter(sources.values()))
            self.assertEqual(record["game_title_hint"], "Alan Wake")
            self.assertEqual(record["game_identity"]["qid"], "Q575505")

    def test_source_context_and_download_info_restore_from_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "viria_state.json"
            state_file.write_text(json.dumps({
                "schema_version": 3,
                "results": [],
                "moments": [],
                "scheduled": [],
                "upload_history": [],
                "delete_after_upload": False,
                "user_settings": {},
                "download_info_by_path": {
                    str(Path(temp_dir, "video.mp4")): {
                        "schema_version": 1,
                        "source": "yt_dlp",
                        "title": "Alan Wake stream",
                        "tags": ["Alan Wake"],
                    }
                },
                "source_context": {
                    "schema_version": 1,
                    "sources": {
                        "src_test": {
                            "source_id": "src_test",
                            "source_path": str(Path(temp_dir, "video.mp4")),
                            "game_title_hint": "Alan Wake",
                            "game_identity": {
                                "status": "ok",
                                "title": "Alan Wake",
                                "qid": "Q575505",
                                "confidence": 0.9,
                                "game_context": {
                                    "status": "ok",
                                    "qid": "Q575505",
                                    "label": "Alan Wake",
                                },
                            },
                        }
                    },
                },
            }), encoding="utf-8")
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._state_lock = threading.RLock()

            with patch("api_bridge.STATE_FILE", state_file):
                bridge._load_state()

        self.assertIn("src_test", bridge._source_context)
        self.assertEqual(bridge._source_context["src_test"]["game_title_hint"], "Alan Wake")
        self.assertEqual(bridge._source_context["src_test"]["game_identity"]["qid"], "Q575505")
        self.assertEqual(len(bridge._download_info_by_path), 1)

    def test_safe_clip_path_rejects_non_video_files_inside_clips_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            video = temp / "clip.mp4"
            text = temp / "clip.txt"
            video.write_bytes(b"video")
            text.write_text("metadata", encoding="utf-8")
            bridge = ApiBridge.__new__(ApiBridge)

            with patch("api_bridge.CLIPS_DIR", temp):
                self.assertEqual(bridge._safe_clip_path(video), video.resolve())
                self.assertIsNone(bridge._safe_clip_path(text))

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

    def test_ai_shadow_shortlist_can_include_safe_near_misses_without_accepted_candidates(self):
        bridge = ApiBridge.__new__(ApiBridge)

        def near_miss(rank, quality):
            return {
                "accepted": False,
                "reject_reason": "low_transcript_quality",
                "quality_score": quality,
                "learned_quality_score": quality,
                "candidate": {"candidate_rank": rank, "candidate_kind": "primary"},
                "moment": {
                    "start": rank * 40,
                    "end": rank * 40 + 30,
                    "transcript": "I think this is a useful creator commentary moment worth checking",
                    "ranker": {"reject_reason": "low_transcript_quality"},
                },
                "word_count": 12,
                "subtitle_word_count": 12,
                "music_lyrics_guard": {"reject_candidate": False},
                "speech_source": {
                    "primary_source": "creator",
                    "creator_safe": True,
                    "game_or_npc_probability": 0.04,
                    "music_or_lyrics_probability": 0.01,
                },
                "commentary_guard": {"summary": {"primary_label": "creator_commentary"}},
            }

        evaluations = [near_miss(1, 0.50), near_miss(2, 0.48), near_miss(3, 0.46)]

        shortlist = bridge._ai_shadow_shortlist(
            evaluations,
            max_count=3,
            score_key="learned_quality_score",
            include_near_misses=True,
        )

        self.assertEqual(len(shortlist), 3)
        self.assertTrue(all(item.get("ai_rescue_candidate") for item in shortlist))
        self.assertTrue(all(item["moment"]["ranker"].get("ai_rescue_candidate") for item in shortlist))

    def test_multimodal_shortlist_reserves_a_slot_for_safe_near_misses(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._cancel = False
        bridge._push = lambda *args, **kwargs: None
        bridge._game_context_for_source = lambda *args, **kwargs: {"label": "Test Game"}
        bridge._infer_game_title_from_path = lambda path: "Test Game"

        def accepted(rank, quality):
            return {
                "accepted": True,
                "quality_score": quality,
                "learned_quality_score": quality,
                "candidate": {
                    "candidate_rank": rank,
                    "candidate_kind": "primary",
                    "start": rank * 40,
                    "end": rank * 40 + 30,
                },
                "moment": {
                    "start": rank * 40,
                    "end": rank * 40 + 30,
                    "duration": 30,
                    "transcript": "good creator commentary",
                    "ranker": {},
                },
                "word_count": 8,
            }

        def near_miss(rank, quality):
            row = accepted(rank, quality)
            row["accepted"] = False
            row["reject_reason"] = "low_transcript_quality"
            row["quality_floor"] = 0.60
            row["subtitle_word_count"] = 12
            row["word_count"] = 12
            row["music_lyrics_guard"] = {"reject_candidate": False}
            row["speech_source"] = {
                "primary_source": "creator",
                "creator_safe": True,
                "game_or_npc_probability": 0.04,
                "music_or_lyrics_probability": 0.01,
            }
            row["commentary_guard"] = {"summary": {"primary_label": "creator_commentary"}}
            row["moment"]["ranker"]["reject_reason"] = "low_transcript_quality"
            return row

        evaluations = [
            accepted(1, 0.92),
            accepted(2, 0.88),
            accepted(3, 0.84),
            accepted(4, 0.80),
            accepted(5, 0.76),
            near_miss(6, 0.54),
        ]

        with patch("api_bridge.ollama_vision_status", return_value={"model_ready": True, "model": "test-vision"}), patch(
            "api_bridge.preflight_ollama_vision_model",
            return_value={"ok": True, "status": "ok", "model": "test-vision"},
        ), patch(
            "api_bridge.analyze_candidate_frames_with_ollama",
            return_value={
                "status": "ok",
                "model": "test-vision",
                "frame_count": 3,
                "sample_times": [0, 10, 20],
                "primary_visual_label": "creator_moment",
                "visible_summary": "Creator is talking over gameplay.",
                "visual_labels": ["creator_moment"],
                "detected_events": [],
                "confidence": 0.7,
                "ranking_adjustment": 0.02,
                "reject_flags": [],
            },
        ):
            report = bridge._analyze_multimodal_candidate_shortlist(
                evaluations,
                [],
                Path("source.mp4"),
                enabled=True,
                score_key="learned_quality_score",
                video_duration=180,
                max_count=4,
            )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["shortlist_count"], 4)
        self.assertEqual(report["accepted_shortlist_count"], 3)
        self.assertEqual(report["near_miss_shortlist_count"], 1)
        self.assertTrue(any(row.get("rescue_candidate") for row in report["rows"]))

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

    def test_unlink_clip_file_retries_transient_player_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            clip_path = Path(temp_dir) / "locked preview.mp4"
            clip_path.write_bytes(b"video")
            bridge = ApiBridge.__new__(ApiBridge)
            original_unlink = Path.unlink
            attempts = []

            def flaky_unlink(path_self, *args, **kwargs):
                if Path(path_self) == clip_path and len(attempts) < 2:
                    attempts.append(True)
                    raise PermissionError("locked by preview")
                return original_unlink(path_self, *args, **kwargs)

            with patch.object(Path, "unlink", flaky_unlink), patch("api_bridge.time.sleep") as sleep_mock:
                ok, error = bridge._unlink_clip_file(clip_path, attempts=4, delay=0.01)

            self.assertTrue(ok)
            self.assertEqual(error, "")
            self.assertFalse(clip_path.exists())
            self.assertEqual(len(attempts), 2)
            self.assertEqual(sleep_mock.call_count, 2)

    def test_unlink_clip_file_returns_friendly_message_for_persistent_player_lock(self):
        bridge = ApiBridge.__new__(ApiBridge)
        with patch.object(Path, "unlink", side_effect=PermissionError("locked by preview")), patch("api_bridge.time.sleep"):
            ok, error = bridge._unlink_clip_file(Path("locked.mp4"), attempts=2, delay=0.01)

        self.assertFalse(ok)
        self.assertIn("preview/player", error)
        self.assertNotIn("WinError", error)

    def test_bulk_library_delete_prunes_state_and_keeps_partial_failures(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_clips = Path(temp_dir)
            clip_a = temp_clips / "alpha.mp4"
            clip_b = temp_clips / "bravo.mp4"
            clip_c = temp_clips / "charlie.mp4"
            for path in (clip_a, clip_b, clip_c):
                path.write_text("video", encoding="utf-8")
                path.with_suffix(".txt").write_text("metadata", encoding="utf-8")

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
            self.assertEqual(result["sidecars_deleted"], ["alpha.txt", "bravo.txt"])
            self.assertEqual(result["failed"], [{"filename": "missing.mp4", "error": "File not found"}])
            self.assertFalse(clip_a.exists())
            self.assertFalse(clip_b.exists())
            self.assertFalse(clip_a.with_suffix(".txt").exists())
            self.assertFalse(clip_b.with_suffix(".txt").exists())
            self.assertTrue(clip_c.exists())
            self.assertTrue(clip_c.with_suffix(".txt").exists())
            self.assertEqual([path.name for path in bridge._results], ["charlie.mp4"])
            self.assertEqual([moment["clip_id"] for moment in bridge._moments], ["clip-c"])
            self.assertEqual([item["clip_id"] for item in bridge._scheduled], ["clip-c"])
            self.assertTrue(bridge._personalization["clips"]["clip-a"]["rendered_file_deleted"])
            self.assertTrue(bridge._personalization["clips"]["clip-b"]["rendered_file_deleted"])
            self.assertNotIn("rendered_file_deleted", bridge._personalization["clips"]["clip-c"])
            self.assertEqual(saves, [True])

    def test_bulk_library_delete_prunes_already_missing_state_clip_without_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_clips = Path(temp_dir)
            missing_clip = temp_clips / "already gone.mp4"
            missing_clip.with_suffix(".txt").write_text("metadata", encoding="utf-8")

            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [missing_clip]
            bridge._moments = [{"clip_id": "clip-gone", "source_id": "source-a"}]
            bridge._scheduled = [{"clip_id": "clip-gone", "clip_filename": missing_clip.name}]
            bridge._state_lock = threading.RLock()
            bridge._personalization_lock = threading.RLock()
            bridge._personalization = {
                "schema_version": 1,
                "events": [],
                "clips": {
                    "clip-gone": {
                        "clip_id": "clip-gone",
                        "clip_filename": missing_clip.name,
                    }
                },
            }
            bridge._save_personalization = lambda: None
            saves = []
            bridge._save_state = lambda: saves.append(True)

            with patch("api_bridge.CLIPS_DIR", temp_clips):
                result = bridge.delete_library_files([missing_clip.name])

            self.assertTrue(result["ok"])
            self.assertEqual(result["deleted"], [])
            self.assertEqual(result["missing_pruned"], [missing_clip.name])
            self.assertEqual(result["failed"], [])
            self.assertEqual(bridge._results, [])
            self.assertEqual(bridge._moments, [])
            self.assertEqual(bridge._scheduled, [])
            self.assertFalse(missing_clip.with_suffix(".txt").exists())
            self.assertTrue(bridge._personalization["clips"]["clip-gone"]["rendered_file_deleted"])
            self.assertTrue(saves)

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
                "game_title_hint": "Alan Wake",
                "game_title": "Alan Wake",
                "game_identity": {
                    "status": "ok",
                    "title": "Alan Wake",
                    "qid": "Q575505",
                    "confidence": 0.94,
                    "matched_via": "wikidata_search",
                    "matched_candidate": {
                        "title": "Alan Wake",
                        "sources": ["explicit_title"],
                    },
                },
                "game_context": {
                    "status": "ok",
                    "qid": "Q575505",
                    "label": "Alan Wake",
                },
                "multi_signal_ai_scoring": {
                    "game_context_nudge": {
                        "status": "scored",
                        "adjustment": 0.009,
                        "reason": "horror_survival_context_match",
                    }
                },
                "multimodal_analysis": {
                    "status": "ok",
                    "model": "llava:7b",
                    "frame_count": 3,
                    "metadata_keywords": ["dark hallway"],
                },
            }]
            bridge._ensure_moment_identity = lambda moment, path: moment

            payload = bridge._clip_payload(0, clip_path)

            self.assertIn("clip%20with%20%23%20tag.mp4", payload["url"])
            self.assertEqual(payload["primary_category"], "high_energy")
            self.assertEqual(payload["moment_categories"]["primary"], "high_energy")
            self.assertEqual(payload["ai_moment_classification"]["provider"], "ollama")
            self.assertEqual(payload["truth_summary"]["game_title"], "Alan Wake")
            self.assertEqual(payload["truth_summary"]["game_confidence"], 0.94)
            self.assertEqual(payload["truth_summary"]["game_source"], "user_hint")
            self.assertEqual(payload["truth_summary"]["game_qid"], "Q575505")
            self.assertTrue(payload["truth_summary"]["game_context_affected_ranking"])
            self.assertEqual(payload["truth_summary"]["game_context_score_delta"], 0.009)
            self.assertTrue(payload["truth_summary"]["visual_analysis_used"])
            self.assertEqual(payload["truth_summary"]["vision_model"], "llava:7b")
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
                "game_title": "Alan Wake",
                "game_identity": {
                    "status": "cache_hit",
                    "title": "Alan Wake",
                    "qid": "Q575505",
                    "confidence": 0.88,
                    "matched_via": "local_cache",
                    "matched_candidate": {
                        "title": "Alan Wake",
                        "sources": ["source_filename"],
                    },
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
            self.assertEqual(result["clips"][0]["truth_summary"]["game_title"], "Alan Wake")
            self.assertEqual(result["clips"][0]["truth_summary"]["game_source"], "filename")
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

    def test_clip_title_context_is_sanitized_and_reaches_metadata_context(self):
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
                "game_context": {
                    "status": "ok",
                    "provider": "wikidata",
                    "qid": "Q575505",
                    "label": "Alan Wake",
                    "description": "2010 video game",
                    "source_url": "https://www.wikidata.org/wiki/Q575505",
                    "license": "CC0-1.0",
                    "facts": {"genres": ["survival horror"]},
                },
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

            result = bridge.save_clip_title_context(
                "clip-1",
                0,
                "clip.mp4",
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
            self.assertEqual(context["game_context"]["qid"], "Q575505")
            self.assertEqual(bridge._scheduled[0]["description_generated"], "")
            self.assertIs(bridge._scheduled[0]["metadata_stale"], True)
            self.assertIn("Creator Context: Blind nursing home chapter", sidecar_text)

    def test_title_context_uses_source_level_game_hint_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            clip_path = Path(temp_dir) / "clip.mp4"
            clip_path.write_text("video", encoding="utf-8")
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [clip_path]
            bridge._moments = [{
                "clip_id": "clip-1",
                "source_id": "src-alan",
                "source_path": str(clip_path),
                "transcript": "this is the part where he is right behind me",
            }]
            bridge._source_context = {
                "src-alan": {
                    "source_id": "src-alan",
                    "source_path": str(clip_path.resolve()),
                    "game_title_hint": "Alan Wake",
                    "game_title": "Alan Wake",
                    "game_identity": {
                        "status": "ok",
                        "title": "Alan Wake",
                        "qid": "Q575505",
                        "confidence": 0.94,
                        "game_context": {
                            "status": "ok",
                            "qid": "Q575505",
                            "label": "Alan Wake",
                        },
                    },
                    "game_context": {
                        "status": "ok",
                        "qid": "Q575505",
                        "label": "Alan Wake",
                        "facts": {"genres": ["survival horror"]},
                    },
                }
            }
            bridge._personalization_lock = threading.RLock()
            bridge._personalization = {"schema_version": 1, "events": [], "clips": {}}

            context = bridge._title_context_for_clip(0)

        self.assertEqual(context["game_title_hint"], "Alan Wake")
        self.assertEqual(context["game_title"], "Alan Wake")
        self.assertEqual(context["game_identity"]["qid"], "Q575505")
        self.assertEqual(context["game_context"]["qid"], "Q575505")
        self.assertEqual(context["source_context"]["game_title_hint"], "Alan Wake")

    def test_generate_title_for_clip_uses_explicit_creator_context_from_reroll(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_clips = Path(temp_dir)
            clip_path = temp_clips / "clip.mp4"
            clip_path.write_text("video", encoding="utf-8")
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [clip_path]
            bridge._moments = [{"transcript": "look at this part"}]
            bridge._user_settings = {}
            bridge._ensure_metadata_vision_context = lambda _idx, context: context
            bridge._game_title_for_clip = lambda _idx: "Alan Wake"
            seen = {}

            def fake_generate_title(transcript, game_title="", clip_context=None):
                seen["transcript"] = transcript
                seen["game_title"] = game_title
                seen["clip_context"] = clip_context or {}
                return "Fresh Context Title #shorts"

            with patch("api_bridge.CLIPS_DIR", temp_clips), \
                    patch("api_bridge.generate_title", side_effect=fake_generate_title), \
                    patch("api_bridge.generate_ai_description_body", return_value="Context-aware description"):
                result = bridge.generate_title_for_clip(
                    0,
                    save=False,
                    creator_title_context="after the fact boss fight note",
                )

            self.assertEqual(result["title"], "Fresh Context Title #shorts")
            self.assertEqual(result["creator_title_context"], "after the fact boss fight note")
            self.assertEqual(seen["clip_context"]["creator_title_context"], "after the fact boss fight note")
            self.assertEqual(bridge._moments[0]["creator_title_context"], "after the fact boss fight note")

    def test_generate_title_for_clip_refreshes_weak_game_identity_from_ai_notes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_clips = Path(temp_dir)
            clip_path = temp_clips / "clip.mp4"
            clip_path.write_text("video", encoding="utf-8")
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [clip_path]
            bridge._moments = [{
                "transcript": "the Taken are chasing me",
                "game_identity": {"status": "no_match", "confidence": 0.0},
                "game_context": {"status": "cache_miss", "available": False},
            }]
            bridge._user_settings = {}
            bridge._ensure_metadata_vision_context = lambda _idx, context: context
            seen = {}

            def fake_identity(video_path, **kwargs):
                seen["identity_kwargs"] = kwargs
                return {
                    "status": "ok",
                    "title": "Alan Wake",
                    "qid": "Q575505",
                    "confidence": 0.94,
                    "matched_via": "wikidata_search",
                    "game_context": {
                        "status": "ok",
                        "qid": "Q575505",
                        "label": "Alan Wake",
                        "facts": {"genres": ["survival horror"]},
                    },
                }

            bridge._game_identity_for_source = fake_identity

            def fake_generate_title(transcript, game_title="", clip_context=None):
                seen["title_game"] = game_title
                seen["title_context"] = clip_context or {}
                return "Alan Wake Chase #shorts"

            with patch("api_bridge.CLIPS_DIR", temp_clips), \
                    patch("api_bridge.generate_title", side_effect=fake_generate_title), \
                    patch("api_bridge.generate_ai_description_body", return_value="AI description"):
                result = bridge.generate_title_for_clip(
                    0,
                    save=False,
                    creator_title_context="blind Alan Wake run",
                )

            self.assertEqual(result["game_title"], "Alan Wake")
            self.assertEqual(seen["title_game"], "Alan Wake")
            self.assertEqual(seen["title_context"]["game_context"]["qid"], "Q575505")
            self.assertEqual(seen["identity_kwargs"]["creator_context"], "blind Alan Wake run")
            self.assertEqual(bridge._moments[0]["game_identity"]["qid"], "Q575505")

    def test_creator_policy_blocks_rendered_audio_backfill_when_selected_track_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            clip_path = Path(temp_dir) / "clip.mp4"
            clip_path.write_bytes(b"fake video")
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [clip_path]
            bridge._moments = [{
                "word_count": 0,
                "subtitle_word_count": 0,
                "analysis_word_count": 62,
                "audio_source": {
                    "subtitle_policy": "creator",
                    "stream_count": 2,
                    "selected_stream": 1,
                    "selected_reason": "manual_stream",
                    "render_audio": "all_source_streams_mixed",
                },
                "stream_selection": {
                    "status": "manual",
                    "selected_stream": 1,
                    "selected_title": "Microphone",
                    "confidence": 0.82,
                },
            }]

            with patch("api_bridge.extract_audio_clip") as extract_audio, \
                    patch("api_bridge.transcribe_clip") as transcribe:
                bridge._backfill_transcript_single(0)

            extract_audio.assert_not_called()
            transcribe.assert_not_called()
            policy = bridge._moments[0]["speech_policy"]
            self.assertEqual(policy["status"], "no_selected_commentary_speech")
            self.assertTrue(policy["metadata_backfill_blocked"])
            self.assertTrue(policy["mixed_speech_without_selected_track"])
            self.assertTrue(bridge._moments[0]["metadata_needs_context"])

    def test_generate_title_for_clip_reports_missing_creator_transcript_without_backfill(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            clip_path = Path(temp_dir) / "clip.mp4"
            clip_path.write_bytes(b"fake video")
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [clip_path]
            bridge._moments = [{
                "word_count": 0,
                "subtitle_word_count": 0,
                "analysis_word_count": 20,
                "audio_source": {
                    "subtitle_policy": "creator",
                    "stream_count": 2,
                    "selected_stream": 1,
                    "render_audio": "all_source_streams_mixed",
                },
            }]
            bridge._save_state = lambda: None

            with patch("api_bridge.extract_audio_clip") as extract_audio:
                result = bridge.generate_title_for_clip(0, save=True)

            extract_audio.assert_not_called()
            self.assertEqual(result["error"], "No commentary transcript for this clip")
            self.assertEqual(result["speech_policy"]["status"], "no_selected_commentary_speech")
            self.assertTrue(result["metadata_needs_context"])

    def test_clip_speech_policy_allows_legacy_rendered_backfill_when_not_track_aware(self):
        policy = _clip_speech_policy_summary({
            "word_count": 0,
            "subtitle_word_count": 0,
            "analysis_word_count": 0,
        })

        self.assertEqual(policy["status"], "ok")
        self.assertFalse(policy["metadata_backfill_blocked"])

    def test_clip_title_context_updates_one_clip_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            clip_a = root / "a.mp4"
            clip_b = root / "b.mp4"
            clip_a.write_text("video", encoding="utf-8")
            clip_b.write_text("video", encoding="utf-8")
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [clip_a, clip_b]
            bridge._moments = [
                {
                    "clip_id": "clip-a",
                    "source_id": "source-1",
                    "source_stem": "same-source",
                    "generated_metadata": {"title": "stale"},
                },
                {
                    "clip_id": "clip-b",
                    "source_id": "source-1",
                    "source_stem": "same-source",
                    "generated_metadata": {"title": "keep"},
                },
            ]
            bridge._scheduled = [
                {
                    "clipIdx": 0,
                    "clip_id": "clip-a",
                    "source_id": "source-1",
                    "source_stem": "same-source",
                    "description_generated": "stale",
                    "generated_description": "stale",
                },
                {
                    "clipIdx": 1,
                    "clip_id": "clip-b",
                    "source_id": "source-1",
                    "source_stem": "same-source",
                    "description_generated": "keep",
                    "generated_description": "keep",
                },
            ]
            bridge._state_lock = threading.RLock()
            bridge._save_state = lambda: None

            result = bridge.save_clip_title_context("clip-a", 0, "a.mp4", "specific boss fight note")

            self.assertTrue(result["ok"])
            self.assertEqual(result["updated_scheduled"], 1)
            self.assertEqual(bridge._moments[0]["creator_title_context"], "specific boss fight note")
            self.assertNotIn("creator_title_context", bridge._moments[1])
            self.assertNotIn("generated_metadata", bridge._moments[0])
            self.assertEqual(bridge._moments[1]["generated_metadata"]["title"], "keep")
            self.assertEqual(bridge._scheduled[0]["creator_title_context"], "specific boss fight note")
            self.assertEqual(bridge._scheduled[0]["description_generated"], "")
            self.assertIs(bridge._scheduled[0]["metadata_stale"], True)
            self.assertEqual(bridge._scheduled[1]["description_generated"], "keep")

    def test_clip_title_context_noop_does_not_save_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            clip_path = Path(temp_dir) / "clip.mp4"
            clip_path.write_text("video", encoding="utf-8")
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [clip_path]
            bridge._moments = [{
                "clip_id": "clip-1",
                "source_id": "source-1",
                "source_stem": "clip",
                "source_path": str(clip_path),
                "creator_title_context": "already saved",
            }]
            bridge._scheduled = [{
                "clipIdx": 0,
                "clip_id": "clip-1",
                "creator_title_context": "already saved",
                "description_generated": "",
                "generated_description": "",
            }]
            bridge._state_lock = threading.RLock()
            saves = []
            bridge._save_state = lambda: saves.append(True)

            result = bridge.save_clip_title_context("clip-1", 0, "clip.mp4", "already saved")

            self.assertTrue(result["ok"])
            self.assertFalse(result["changed"])
            self.assertEqual(saves, [])

    def test_library_clip_game_title_can_be_saved_by_safe_filename(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            clips_dir = Path(temp_dir)
            clip_path = clips_dir / "old_library_clip.mp4"
            clip_path.write_text("video", encoding="utf-8")
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = []
            bridge._moments = []
            bridge._scheduled = []
            bridge._state_lock = threading.RLock()
            saves = []
            bridge._save_state = lambda: saves.append(True)

            with patch("api_bridge.CLIPS_DIR", clips_dir):
                result = bridge.save_clip_game_title(
                    clip_id="",
                    clip_index=None,
                    filename="old_library_clip.mp4",
                    text="Alan Wake",
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["clip_index"], 0)
            self.assertEqual(result["game_title"], "Alan Wake")
            self.assertEqual(bridge._results, [clip_path.resolve()])
            self.assertEqual(bridge._moments[0]["game_title"], "Alan Wake")
            self.assertEqual(bridge._moments[0]["truth_summary"]["game_title"], "Alan Wake")
            self.assertEqual(len(saves), 1)

    def test_library_clip_game_title_rejects_unsafe_filename(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            clips_dir = Path(temp_dir) / "clips"
            outside_dir = Path(temp_dir) / "outside"
            clips_dir.mkdir()
            outside_dir.mkdir()
            outside = outside_dir / "secret.mp4"
            outside.write_text("video", encoding="utf-8")
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = []
            bridge._moments = []
            bridge._scheduled = []
            bridge._state_lock = threading.RLock()
            saves = []
            bridge._save_state = lambda: saves.append(True)

            with patch("api_bridge.CLIPS_DIR", clips_dir):
                result = bridge.save_clip_game_title(
                    clip_id="",
                    clip_index=None,
                    filename=str(outside),
                    text="Alan Wake",
                )

            self.assertEqual(result["error"], "Clip not found")
            self.assertEqual(bridge._results, [])
            self.assertEqual(bridge._moments, [])
            self.assertEqual(saves, [])

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

    def test_selected_moments_retry_cached_ollama_error(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._infer_game_title_from_path = lambda path: "Test Game"
        bridge._game_context_for_source = lambda *args, **kwargs: {"label": "Test Game"}
        bridge._feedback_learning_prompt_context = lambda: {"enabled": False}
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
        cache_key = bridge._ai_moment_cache_key(selected[0])
        cache = {cache_key: {"status": "ollama_error", "fallback_used": True, "primary_category": "low_value"}}
        fresh = {
            "status": "ok",
            "provider": "ollama",
            "fallback_used": False,
            "primary_category": "high_energy",
            "fine_labels": ["chase_panic"],
            "confidence": 0.8,
            "ai_viral_score": 72,
            "ai_confidence": 0.8,
            "ai_dimensions": {"hook": 0.7, "flow": 0.6, "value": 0.6, "platform_fit": 0.7, "game_context": 0.6},
            "selection_impact": "none",
            "output_changed": False,
        }

        with patch("api_bridge.is_ollama_model_ready", return_value=True), \
                patch("api_bridge.classify_moment_ai", return_value=fresh) as classify_mock:
            report = bridge._classify_selected_moments(
                selected,
                Path("A:/Videos/Test Game/source.mp4"),
                enabled=True,
                max_ollama=1,
                classification_cache=cache,
            )

        self.assertEqual(report["reused_shadow_count"], 0)
        self.assertEqual(report["ollama_attempted_count"], 1)
        classify_mock.assert_called_once()
        self.assertEqual(selected[0]["moment"]["ai_moment_classification"]["status"], "ok")

    def test_manual_audio_profile_does_not_fabricate_creator_confidence_from_title(self):
        profile = _selected_audio_stream_profile(
            {
                "mode": "stream",
                "streams": [{"ordinal": 0, "title": "Microphone_vertical", "likely_role": "commentary"}],
                "stream_selection": {
                    "status": "forced",
                    "mode": "manual_stream",
                    "selected_stream": 0,
                    "selected_title": "Microphone_vertical",
                    "selected_reason": "user_selected_stream",
                    "confidence": 1.0,
                    "stream_profiles": [],
                },
            },
            selected_stream=0,
        )

        self.assertEqual(profile["selected_reason"], "user_selected_stream")
        self.assertLessEqual(profile["creator_likeness_score"], 0.2)
        self.assertEqual(profile["natural_dialogue_score"], 0.0)

    def test_manual_audio_selection_is_locked_against_auto_override(self):
        source = (ROOT / "api_bridge.py").read_text(encoding="utf-8")

        self.assertIn("manual_stream_locked = forced_speech_stream is not None", source)
        self.assertIn(
            'final_stream = speech_stream if manual_stream_locked else m.get("speech_stream", speech_stream)',
            source,
        )
        self.assertIn("User-selected transcription stream locked", source)
        self.assertNotIn("manual_audio_stream_overridden_by_creator_detection", source)
        self.assertNotIn("creator_detection_overrode_manual_stream", source)
        self.assertNotIn("manual_overridden", source)

    def test_local_video_server_sends_thumbnail_safe_headers(self):
        source = (ROOT / "api_bridge.py").read_text(encoding="utf-8")

        self.assertNotIn('self.send_header("Access-Control-Allow-Origin", "*")', source)
        self.assertIn('self.send_header("Access-Control-Allow-Origin", allowed_origin)', source)
        self.assertIn('self.send_header("Access-Control-Allow-Headers", "Range, Content-Type")', source)
        self.assertIn("http.server.ThreadingHTTPServer", source)

    def test_local_video_server_origin_allowlist_is_loopback_only(self):
        self.assertIsNone(_allowed_media_origin("null"))
        self.assertEqual(_allowed_media_origin("http://localhost:3000"), "http://localhost:3000")
        self.assertEqual(_allowed_media_origin("http://127.0.0.1:3000"), "http://127.0.0.1:3000")
        self.assertEqual(_allowed_media_origin("http://[::1]:3000"), "http://[::1]:3000")
        self.assertIsNone(_allowed_media_origin("https://example.com"))
        self.assertIsNone(_allowed_media_origin("http://192.168.1.10:3000"))

    def test_ollama_model_downloads_are_allowlisted(self):
        bridge = ApiBridge.__new__(ApiBridge)

        with patch("api_bridge.ensure_model") as ensure_model:
            text_result = bridge.ensure_ollama_model("unexpected:latest")
            vision_result = bridge.ensure_ollama_vision_model("unexpected-vision:latest")

        self.assertIn("error", text_result)
        self.assertIn("error", vision_result)
        ensure_model.assert_not_called()

    def test_ollama_status_reports_text_and_vision_models(self):
        bridge = ApiBridge.__new__(ApiBridge)

        with patch("api_bridge.ollama_status", return_value={
            "running": True,
            "model": "qwen3.5:4b",
            "model_ready": True,
            "using_ollama": True,
            "models": ["qwen3.5:4b", "qwen3-vl:latest"],
            "version": "0.9.0",
        }), patch("api_bridge.ollama_vision_status", return_value={
            "model_ready": True,
            "model": "qwen3-vl:latest",
            "supported_model_hints": ["qwen3-vl"],
        }), patch("api_bridge.shutil.which", return_value=None):
            status = bridge.get_ollama_status()

        self.assertTrue(status["text_model"]["model_ready"])
        self.assertEqual(status["text_model"]["model"], "qwen3.5:4b")
        self.assertTrue(status["vision"]["model_ready"])
        self.assertEqual(status["vision"]["model"], "qwen3-vl:latest")
        self.assertTrue(status["using_ollama_vision"])
        self.assertFalse(status["installed"])

    def test_ollama_powershell_install_api_is_removed(self):
        bridge = ApiBridge.__new__(ApiBridge)

        self.assertFalse(hasattr(bridge, "prepare_ollama_install"))
        self.assertFalse(hasattr(bridge, "install_ollama_with_powershell"))

    def test_rename_clip_rejects_results_outside_clips_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outside = Path(temp_dir) / "outside.mp4"
            outside.write_text("video", encoding="utf-8")
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [outside]

            result = bridge.rename_clip(0, "new title")

            self.assertEqual(result["error"], "File not found")
            self.assertTrue(outside.exists())

    def test_auto_metadata_renames_fresh_clip_and_rewrites_sidecar(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_clips = Path(temp_dir)
            old_clip = temp_clips / "source_viral1.mp4"
            old_clip.write_text("video", encoding="utf-8")
            old_sidecar = old_clip.with_suffix(".txt")
            old_sidecar.write_text("Title: old\n", encoding="utf-8")

            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [old_clip]
            bridge._moments = [{"transcript": "wait what happened"}]
            bridge._cancel = False
            bridge._user_settings = {}
            bridge._push = lambda *args, **kwargs: None
            bridge._title_context_for_clip = lambda _idx: {
                "transcript": "wait what happened",
                "quality_score": 0.7,
            }
            bridge.generate_title_for_clip = lambda _idx, save=False: {
                "title": "Better Clip Title #shorts",
                "description": "Better description",
                "final_description": "Better description",
                "generated_description": "Better description",
                "description_custom_text": "",
                "description_auto_hashtags": True,
                "tags": "Better, Clip",
                "game_title": "Test Game",
                "creator_title_context": "",
                "metadata_file": str(old_sidecar),
            }
            final_debug = [{"path": str(old_clip)}]

            with patch("api_bridge.CLIPS_DIR", temp_clips):
                metadata = bridge._generate_auto_metadata_for_results(0, 1, final_debug, [])

            new_clip = temp_clips / "Better Clip Title #shorts.mp4"
            new_sidecar = new_clip.with_suffix(".txt")
            self.assertTrue(new_clip.exists())
            self.assertTrue(new_sidecar.exists())
            self.assertFalse(old_clip.exists())
            self.assertFalse(old_sidecar.exists())
            self.assertEqual(bridge._results[0].resolve(), new_clip.resolve())
            self.assertEqual(metadata[0]["filename"], new_clip.name)
            self.assertEqual(Path(final_debug[0]["path"]).resolve(), new_clip.resolve())
            sidecar_text = new_sidecar.read_text(encoding="utf-8")
            self.assertIn("Title: Better Clip Title #shorts", sidecar_text)
            self.assertIn(new_clip.name, sidecar_text)

    def test_generated_metadata_records_stable_clip_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            clip_path = Path(temp_dir) / "clip.mp4"
            clip_path.write_text("video", encoding="utf-8")
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [clip_path]
            bridge._moments = [{
                "clip_id": "clip-identity",
                "source_id": "source-identity",
                "transcript": "this is a useful moment",
            }]
            bridge._user_settings = {}

            bridge._store_generated_metadata(
                0,
                "Useful Moment",
                "Description",
                "tags",
                "Test Game",
                str(clip_path.with_suffix(".txt")),
                {"transcript": "this is a useful moment"},
            )

            meta = bridge._moments[0]["generated_metadata"]
            self.assertEqual(meta["clip_id"], "clip-identity")
            self.assertEqual(meta["source_id"], "source-identity")
            self.assertEqual(meta["clip_filename"], "clip.mp4")

    def test_montage_metadata_uses_storyboard_context_for_title_and_description(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            clip_path = Path(temp_dir) / "montage.mp4"
            clip_path.write_text("video", encoding="utf-8")
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [clip_path]
            bridge._moments = [{"clip_id": "clip-montage", "source_id": "src-montage"}]
            bridge._user_settings = {"description_profile": {"auto_hashtags": False, "custom_text": ""}}
            bridge._game_context_for_title = lambda *_args, **_kwargs: {
                "status": "ok",
                "label": "Star Wars Outlaws",
                "facts": {"genres": ["action-adventure game"]},
            }
            bridge._feedback_learning_prompt_context = lambda: {"enabled": False}
            seen = {}

            def fake_generate_title(transcript, game_title="", clip_context=None):
                seen["transcript"] = transcript
                seen["clip_context"] = clip_context
                return "That Bribe Was Not Worth It #shorts"

            bridge._generated_description_for_clip = lambda title, transcript, game_title, clip_context: (
                "The club bribe somehow gets worse."
            )
            storyboard = {
                "source": {"game_title": "Star Wars Outlaws"},
                "summary": {"beat_count": 3, "planned_duration_seconds": 58},
                "beats": [
                    {"role": "setup", "category": "commentary_or_review", "hook_text": "we need to sneak into the club"},
                    {"role": "escalation", "category": "high_energy", "hook_text": "we have to bribe this guy for fifty credits"},
                    {"role": "punchline", "category": "commentary_or_review", "hook_text": "that was not worth it"},
                ],
            }

            with patch("api_bridge.generate_title", side_effect=fake_generate_title):
                metadata = bridge._write_montage_metadata(0, storyboard)

            self.assertEqual(metadata["title"], "That Bribe Was Not Worth It #shorts")
            self.assertIn("setup:", seen["transcript"])
            self.assertIn("bribe this guy", seen["transcript"])
            self.assertIn("montage_storyboard", seen["clip_context"])
            self.assertIn("montage_quality_explanation", bridge._moments[0])
            self.assertIn("Montage Quality:", clip_path.with_suffix(".txt").read_text(encoding="utf-8"))

    def test_manual_ai_metadata_reroll_renames_clip_and_removes_stale_sidecar(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_clips = Path(temp_dir)
            old_clip = temp_clips / "source_viral1.mp4"
            old_clip.write_text("video", encoding="utf-8")
            old_sidecar = old_clip.with_suffix(".txt")
            old_sidecar.write_text("Title: old\n", encoding="utf-8")

            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [old_clip]
            bridge._moments = [{
                "transcript": "this is a great moment",
                "creator_title_context": "blind Alan Wake run",
                "generated_metadata": {"metadata_file": str(old_sidecar)},
            }]
            bridge._user_settings = {}
            bridge._save_state = lambda: None
            bridge._js = lambda _script: None
            seen = {}

            def fake_refresh(idx, **_kwargs):
                bridge._moments[idx]["game_title"] = "Alan Wake"
                bridge._moments[idx]["game_identity"] = {
                    "status": "ok",
                    "title": "Alan Wake",
                    "qid": "Q575505",
                    "confidence": 0.94,
                }
                bridge._moments[idx]["game_context"] = {
                    "status": "ok",
                    "qid": "Q575505",
                    "label": "Alan Wake",
                    "facts": {"genres": ["survival horror"]},
                }
                return bridge._moments[idx]["game_identity"]

            bridge._refresh_clip_game_identity_for_metadata = fake_refresh
            bridge._title_context_for_clip = lambda _idx: {
                "transcript": bridge._moments[0]["transcript"],
                "game_title": bridge._moments[0].get("game_title"),
                "game_identity": bridge._moments[0].get("game_identity"),
                "game_context": bridge._moments[0].get("game_context"),
                "creator_title_context": bridge._moments[0].get("creator_title_context"),
                "quality_score": 0.8,
            }
            bridge._ensure_metadata_vision_context = lambda _idx, context: {
                **context,
                "multimodal_analysis": {"status": "ok", "visible_summary": "dark hallway"},
            }
            def fake_generated_description(title, transcript, game_title, clip_context):
                seen["description_context"] = {
                    "title": title,
                    "transcript": transcript,
                    "game_title": game_title,
                    "creator_title_context": clip_context.get("creator_title_context"),
                    "vision": clip_context.get("multimodal_analysis"),
                }
                return "AI generated description"

            bridge._generated_description_for_clip = fake_generated_description

            def fake_titles_batch(transcripts, *_args, **kwargs):
                seen["title_context"] = kwargs["clip_contexts"][0]
                seen["game_title"] = kwargs["game_titles"][0]
                return ["Fresh Clip Title #shorts"]

            with patch("api_bridge.CLIPS_DIR", temp_clips), \
                    patch("api_bridge.is_ollama_model_ready", return_value=True), \
                    patch("api_bridge.generate_titles_batch", side_effect=fake_titles_batch):
                bridge._run_title_gen([0])

            new_clip = temp_clips / "Fresh Clip Title #shorts.mp4"
            new_sidecar = new_clip.with_suffix(".txt")
            self.assertTrue(new_clip.exists())
            self.assertTrue(new_sidecar.exists())
            self.assertFalse(old_clip.exists())
            self.assertFalse(old_sidecar.exists())
            self.assertEqual(bridge._results[0].resolve(), new_clip.resolve())
            self.assertEqual(
                Path(bridge._moments[0]["generated_metadata"]["metadata_file"]).resolve(),
                new_sidecar.resolve(),
            )
            sidecar_text = new_sidecar.read_text(encoding="utf-8")
            self.assertIn("Title: Fresh Clip Title #shorts", sidecar_text)
            self.assertIn("AI generated description", sidecar_text)
            self.assertEqual(seen["game_title"], "Alan Wake")
            self.assertEqual(seen["title_context"]["creator_title_context"], "blind Alan Wake run")
            self.assertEqual(seen["title_context"]["multimodal_analysis"]["status"], "ok")
            self.assertEqual(seen["description_context"]["game_title"], "Alan Wake")
            self.assertEqual(seen["description_context"]["creator_title_context"], "blind Alan Wake run")
            self.assertEqual(seen["description_context"]["vision"]["visible_summary"], "dark hallway")

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

        bridge.save_settings({"clip_duration": 220})
        self.assertEqual(bridge.get_settings()["clip_duration"], 180)

        bridge.save_settings({"clip_duration": -5})
        self.assertEqual(bridge.get_settings()["clip_duration"], 30)

        bridge.save_settings({"min_gap": 5})
        self.assertEqual(bridge.get_settings()["min_gap"], 5)

        bridge.save_settings({"min_gap": 999})
        self.assertEqual(bridge.get_settings()["min_gap"], 60)

        bridge.save_settings({"min_gap": -30})
        self.assertEqual(bridge.get_settings()["min_gap"], 15)

    def test_generation_mode_and_montage_settings_are_sanitized(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._user_settings = {}
        bridge._save_state = lambda: None

        self.assertEqual(_normalize_generation_mode("montage"), "montage")
        self.assertEqual(_normalize_generation_mode("../montage"), "clips")
        self.assertEqual(_normalize_montage_settings({
            "template": "death / failure",
            "target_duration": 999,
            "count": 99,
            "prompt": "make it scary\n" * 80,
        }), {
            "template": "panic",
            "target_duration": 60,
            "count": 5,
            "prompt": ("make it scary " * 80).strip()[:500],
        })

        bridge.save_settings({
            "generation_mode": "montage",
            "montage": {
                "template": "story",
                "target_duration": 90,
                "count": 3,
                "prompt": "story recap with a clean payoff",
            },
        })
        settings = bridge.get_settings()
        self.assertEqual(settings["generation_mode"], "montage")
        self.assertEqual(settings["montage"]["template"], "story")
        self.assertEqual(settings["montage"]["target_duration"], 90)
        self.assertEqual(settings["montage"]["count"], 3)
        self.assertEqual(settings["montage"]["prompt"], "story recap with a clean payoff")

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

    def test_candidate_transcription_chunks_are_bounded_by_audio_seconds(self):
        paths = [Path(f"probe_{idx}.wav") for idx in range(5)]
        durations = {str(path): 90 for path in paths}

        chunks = _candidate_transcription_chunks(paths, durations, max_seconds=180, max_files=8)

        self.assertEqual([[path.name for path in chunk] for chunk in chunks], [
            ["probe_0.wav", "probe_1.wav"],
            ["probe_2.wav", "probe_3.wav"],
            ["probe_4.wav"],
        ])

    def test_candidate_transcription_chunks_are_bounded_by_file_count(self):
        paths = [Path(f"probe_{idx}.wav") for idx in range(5)]
        durations = {str(path): 10 for path in paths}

        chunks = _candidate_transcription_chunks(paths, durations, max_seconds=180, max_files=2)

        self.assertEqual([[path.name for path in chunk] for chunk in chunks], [
            ["probe_0.wav", "probe_1.wav"],
            ["probe_2.wav", "probe_3.wav"],
            ["probe_4.wav"],
        ])

    def test_candidate_transcription_chunks_preserve_completed_results(self):
        paths = [Path(f"probe_{idx}.wav") for idx in range(4)]
        durations = {str(path): 90 for path in paths}

        def fake_transcribe(chunk, model_size, language):
            if chunk[0].name == "probe_0.wav":
                return [[{"text": "first", "start": 0, "end": 1}], [{"text": "second", "start": 0, "end": 1}]]
            return [[], []]

        with patch("api_bridge.transcribe_clips", side_effect=fake_transcribe) as transcribe:
            words_by_path, chunk_count = _transcribe_candidate_wav_chunks(
                paths,
                durations,
                model_size="base",
                language=None,
            )

        self.assertEqual(chunk_count, 2)
        self.assertEqual(transcribe.call_count, 2)
        self.assertEqual(words_by_path[str(paths[0])][0]["text"], "first")
        self.assertEqual(words_by_path[str(paths[1])][0]["text"], "second")
        self.assertEqual(words_by_path[str(paths[2])], [])
        self.assertEqual(words_by_path[str(paths[3])], [])

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

    def test_recovered_run_debug_merge_preserves_unselected_candidates(self):
        payload = {
            "debug_stage": "candidate_pre_render",
            "candidate_count": 3,
            "selected_count": 2,
            "timing": {"candidate_analysis": 12.5},
            "candidates": [
                {"selected": True, "start": 10, "end": 20, "candidate": {"candidate_rank": 1}},
                {"selected": False, "start": 30, "end": 40, "candidate": {"candidate_rank": 2}},
                {"selected": True, "start": 50, "end": 60, "candidate": {"candidate_rank": 3}},
            ],
        }
        final_clips = [
            {"index": 1, "path": "clip1.mp4", "transcript": "first"},
            {"index": 2, "path": "clip2.mp4", "transcript": "second"},
        ]

        recovered = ApiBridge._merge_recovered_run_debug_payload(
            payload,
            debug_path=Path("A:/ViriaRevive/subtitles/source_candidate_debug.json"),
            final_clip_debug=final_clips,
            run_warnings=["rendered_from_candidate_debug"],
            stage_timings={"final_render": 3.4},
            auto_metadata_count=2,
        )

        self.assertEqual(recovered["debug_stage"], "run_post_render")
        self.assertTrue(recovered["final_render_metadata_included"])
        self.assertTrue(recovered["recovered_from_candidate_debug"])
        self.assertEqual(recovered["candidate_count"], 3)
        self.assertEqual(len(recovered["candidates"]), 3)
        self.assertFalse(recovered["candidates"][1]["selected"])
        self.assertEqual(recovered["candidates"][0]["final_render"]["path"], "clip1.mp4")
        self.assertEqual(recovered["candidates"][2]["final_render"]["path"], "clip2.mp4")
        self.assertEqual(recovered["timing"]["candidate_analysis"], 12.5)
        self.assertEqual(recovered["timing"]["stage_timings"]["final_render"], 3.4)
        self.assertEqual(recovered["auto_metadata_count"], 2)

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
        sidecar_path = clip_path.with_suffix(".txt")
        sidecar_path.write_text("metadata", encoding="utf-8")
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
            self.assertTrue(result["sidecar_deleted"])
            self.assertFalse(clip_path.exists())
            self.assertFalse(sidecar_path.exists())
            entry = bridge._personalization["clips"]["clip_delete"]
            self.assertTrue(entry["rendered_file_deleted"])
            self.assertEqual(entry["deleted_filename"], clip_path.name)
            self.assertEqual(entry["learning_terms"], ["menu", "loading"])
        finally:
            try:
                clip_path.unlink(missing_ok=True)
                sidecar_path.unlink(missing_ok=True)
            except Exception:
                pass

    def test_prune_missing_results_deletes_orphan_metadata_sidecar(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_clips = Path(temp_dir)
            missing_clip = temp_clips / "folder deleted video.mp4"
            sidecar_path = missing_clip.with_suffix(".txt")
            sidecar_path.write_text("metadata", encoding="utf-8")

            bridge = ApiBridge.__new__(ApiBridge)
            bridge._results = [missing_clip]
            bridge._moments = [{"clip_id": "clip_missing", "source_id": "source_1"}]
            bridge._scheduled = []
            bridge._state_lock = threading.RLock()
            bridge._personalization_lock = threading.RLock()
            bridge._personalization = {
                "schema_version": 1,
                "events": [],
                "clips": {
                    "clip_missing": {
                        "clip_id": "clip_missing",
                        "clip_filename": missing_clip.name,
                    }
                },
            }
            bridge._save_state = lambda: None
            bridge._save_personalization = lambda: None

            with patch("api_bridge.CLIPS_DIR", temp_clips):
                removed = bridge._prune_missing_results()

            self.assertEqual(removed, 1)
            self.assertFalse(sidecar_path.exists())
            self.assertEqual(bridge._results, [])
            self.assertTrue(bridge._personalization["clips"]["clip_missing"]["rendered_file_deleted"])

    def test_orphan_metadata_sidecar_cleanup_only_removes_generated_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_clips = Path(temp_dir)
            orphan = temp_clips / "old renamed clip.txt"
            orphan.write_text(
                "Title: Old\n\nDescription:\nOld description\n\nAnalysis Context:\nMoment Type: test\n",
                encoding="utf-8",
            )
            notes = temp_clips / "creator notes.txt"
            notes.write_text("keep this note", encoding="utf-8")
            matched_video = temp_clips / "current clip.mp4"
            matched_video.write_text("video", encoding="utf-8")
            matched_sidecar = matched_video.with_suffix(".txt")
            matched_sidecar.write_text(
                "Title: Current\n\nDescription:\nCurrent description\n\nAnalysis Context:\nMoment Type: test\n",
                encoding="utf-8",
            )

            bridge = ApiBridge.__new__(ApiBridge)
            with patch("api_bridge.CLIPS_DIR", temp_clips):
                removed = bridge._prune_orphan_metadata_sidecars()

            self.assertEqual(removed, 1)
            self.assertFalse(orphan.exists())
            self.assertTrue(notes.exists())
            self.assertTrue(matched_sidecar.exists())

    def test_candidate_debug_recovery_defines_and_persists_stage_timings(self):
        source = inspect.getsource(ApiBridge._run_candidate_debug_recovery)

        self.assertIn("stage_timings: dict[str, float] = {}", source)
        self.assertIn('stage_timings["final_render"]', source)
        self.assertIn("_merge_recovered_run_debug_payload(", source)
        self.assertIn('"trim_adjusted_start": m.get("trim_adjusted_start")', source)
        self.assertIn('"selection_primary_category": selection_primary_category', source)
        self.assertIn('"ranking_primary_category": ranking_primary_category', source)
        self.assertIn('"final_primary_category": final_primary_category', source)
        self.assertIn('"final_moment_categories": final_moment_categories', source)

    def test_final_render_applies_trim_suggestion_and_records_selected_window(self):
        source = inspect.getsource(ApiBridge._run_pipeline)

        self.assertIn("selected_start, selected_end = int(m[\"start\"]), int(m[\"end\"])", source)
        self.assertIn("_subtitle_words_for_render_start", source)
        self.assertIn('m["trim_adjusted_start"] = trim_start', source)
        self.assertIn('m["subtitle_timing_offset"]', source)
        self.assertIn('m["trim_adjusted_from_selected"] = (', source)
        self.assertIn("start, end = trim_start, trim_end", source)
        self.assertIn('m["start"] = start', source)
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
        sidecar_path = clip_path.with_suffix(".txt")
        sidecar_path.write_text("metadata", encoding="utf-8")
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
            self.assertFalse(sidecar_path.exists())
            self.assertEqual(bridge._results, [])
            self.assertEqual(bridge._moments, [])
            self.assertTrue(any("onClipDeleted" in call for call in bridge._pending_js))
            self.assertTrue(any('"clipId": "clip-delete"' in call for call in bridge._pending_js))
            self.assertTrue(saves)
        finally:
            clip_path.unlink(missing_ok=True)
            sidecar_path.unlink(missing_ok=True)

    def test_start_upload_rejects_when_upload_lock_is_held(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._processing = False
        bridge._upload_lock = threading.Lock()
        bridge._upload_lock.acquire()
        try:
            result = bridge.start_upload([{"title": "Clip", "privacy": "private"}])
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
