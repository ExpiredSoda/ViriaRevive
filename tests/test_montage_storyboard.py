import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api_bridge import ApiBridge  # noqa: E402
from montage_storyboard import (  # noqa: E402
    build_candidate_audit,
    build_storyboard_from_audit,
    write_candidate_audit,
    write_storyboard,
)
from montage_renderer import render_draft_montage  # noqa: E402
from run_learning import empty_run_learning  # noqa: E402


def _run_debug(rows):
    return {
        "run_id": "run_test",
        "debug_stage": "run_post_render",
        "video": "D:/Recording Video Files/Alan Wake/source.mp4",
        "video_duration": 3600,
        "candidate_count": len(rows),
        "selected_count": sum(1 for row in rows if row.get("selected")),
        "settings": {
            "processing_depth": "deep",
            "generation": {"mode": "montage"},
            "game_identity": {"title": "Alan Wake"},
            "source_context": {"source_stem": "source"},
        },
        "final_clips": [{"filename": "source_viral1.mp4"}],
        "candidates": rows,
    }


def _row(start, end, score, category, *, selected=False, accepted=True, transcript=""):
    return {
        "selected": selected,
        "accepted": accepted,
        "start": start,
        "end": end,
        "selection_quality_score": score,
        "primary_category": category,
        "clip_id": f"clip_{start}",
        "source_id": "src_test",
        "clip_filename": f"clip_{start}.mp4",
        "transcript": transcript,
        "visual_diagnostics": {"black_frame_ratio": 0.05, "labels": ["gameplay"]},
        "ai_moment_classification": {"label": category.replace("_", " ")},
        "learned_adjustment": 0.02 if selected else 0,
    }


def _context_row(start, end, score, category, *, transcript="", word_count=30):
    row = _row(start, end, score, category, selected=False, accepted=False, transcript=transcript)
    row["reject_reason"] = "low_transcript_quality"
    row["word_count"] = word_count
    row["selection_quality_score"] = score
    return row


class MontageStoryboardTests(unittest.TestCase):
    def test_candidate_audit_reports_ready_with_compact_beats(self):
        long_transcript = "this is a strong chase moment " * 20
        audit = build_candidate_audit(
            _run_debug(
                [
                    _row(0, 18, 0.82, "high_energy", selected=True, transcript=long_transcript),
                    _row(30, 48, 0.74, "tutorial_or_explainer", selected=True, transcript="useful route explanation"),
                    _row(60, 80, 0.68, "lore_or_story", transcript="story reveal"),
                    _row(90, 120, 0.44, "high_energy", transcript="weak"),
                ]
            ),
            target_beats=3,
        )

        self.assertTrue(audit["ready"])
        self.assertEqual(audit["status"], "ready")
        self.assertEqual(audit["counts"]["usable_beat_count"], 3)
        self.assertEqual(audit["source"]["game_title"], "Alan Wake")
        self.assertTrue(audit["feature_status"]["visual_used"])
        self.assertTrue(audit["feature_status"]["ai_label_used"])
        self.assertTrue(audit["feature_status"]["learning_used"])
        self.assertFalse(audit["stores_raw_media"])
        self.assertFalse(audit["stores_full_transcripts"])
        self.assertLessEqual(len(audit["beats"][0]["hook_text"]), 140)
        self.assertNotIn(long_transcript, json.dumps(audit))

    def test_candidate_audit_reports_thin_when_not_enough_usable_beats(self):
        audit = build_candidate_audit(
            _run_debug(
                [
                    _row(0, 18, 0.8, "high_energy", selected=True),
                    _row(25, 28, 0.9, "lore_or_story", selected=True),
                    _row(45, 64, 0.5, "low_value", selected=True),
                ]
            ),
            target_beats=3,
        )

        self.assertFalse(audit["ready"])
        self.assertEqual(audit["status"], "thin")
        self.assertEqual(audit["counts"]["usable_beat_count"], 1)
        self.assertIn("too_short", audit["rejected_summary"])
        self.assertIn("low_value_category", audit["rejected_summary"])

    def test_candidate_audit_filters_music_guard_and_black_frames(self):
        audit = build_candidate_audit(
            _run_debug(
                [
                    {
                        **_row(0, 18, 0.8, "high_energy", selected=True),
                        "music_lyrics_guard": {"status": "lyrics_detected"},
                    },
                    {
                        **_row(30, 48, 0.8, "lore_or_story", selected=True),
                        "visual_diagnostics": {"black_frame_ratio": 0.9},
                    },
                ]
            )
        )

        self.assertEqual(audit["status"], "no_usable_beats")
        self.assertEqual(audit["rejected_summary"]["music_or_lyrics_guard"], 1)
        self.assertEqual(audit["rejected_summary"]["mostly_black_frames"], 1)

    def test_write_candidate_audit_persists_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "montages" / "audit.json"
            audit = build_candidate_audit(_run_debug([_row(0, 18, 0.8, "high_energy", selected=True)]))
            write_candidate_audit(path, audit)

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["audit_id"], audit["audit_id"])

    def test_storyboard_assigns_roles_and_render_plan_without_rendering(self):
        audit = build_candidate_audit(
            _run_debug(
                [
                    _row(0, 18, 0.82, "high_energy", selected=True, transcript="panic hook"),
                    _row(30, 48, 0.74, "tutorial_or_explainer", selected=True, transcript="route explanation"),
                    _row(60, 80, 0.68, "lore_or_story", transcript="story payoff"),
                ]
            ),
            target_beats=3,
        )

        storyboard = build_storyboard_from_audit(
            audit,
            target_duration_seconds=45,
            story_shape="hook_escalate_payoff",
            created_at="2026-06-26T00:00:00Z",
        )

        self.assertTrue(storyboard["ready"])
        self.assertEqual(storyboard["status"], "ready")
        self.assertEqual(storyboard["created_at"], "2026-06-26T00:00:00Z")
        self.assertEqual([beat["role"] for beat in storyboard["beats"]], ["hook", "escalation", "payoff"])
        self.assertEqual(storyboard["beats"][0]["clip_id"], "clip_0")
        self.assertEqual(storyboard["beats"][-1]["transition_after"], "none")
        self.assertEqual(storyboard["render_plan"][0]["render_action"], "segment_copy_later")
        self.assertTrue(storyboard["memory_snapshot"]["local_only"])
        self.assertFalse(storyboard["stores_raw_media"])

    def test_storyboard_caps_duration_and_preserves_source_window(self):
        audit = build_candidate_audit(
            _run_debug(
                [
                    _row(0, 40, 0.82, "high_energy", selected=True, transcript="panic hook"),
                    _row(50, 90, 0.76, "commentary_or_review", selected=True, transcript="funny setup"),
                    _row(100, 140, 0.7, "lore_or_story", selected=True, transcript="story payoff"),
                ]
            ),
            target_beats=3,
            target_duration_seconds=30,
        )

        storyboard = build_storyboard_from_audit(
            audit,
            target_duration_seconds=30,
            story_shape="hook_escalate_payoff",
            created_at="2026-06-26T00:00:00Z",
        )

        self.assertLessEqual(storyboard["summary"]["planned_duration_seconds"], 30.1)
        self.assertTrue(all(beat["duration"] <= 14.1 for beat in storyboard["beats"]))
        self.assertTrue(all(beat["source_duration"] >= beat["duration"] for beat in storyboard["beats"]))
        self.assertTrue(any(beat.get("trimmed_for_montage") for beat in storyboard["beats"]))

    def test_funny_storyboard_prefers_spoken_punchline_over_blank_visual(self):
        audit = build_candidate_audit(
            _run_debug(
                [
                    _row(0, 30, 0.9, "atmosphere_or_visual", selected=True, transcript=""),
                    _row(40, 70, 0.72, "commentary_or_review", selected=True, transcript="this bribe is weird"),
                    _row(80, 110, 0.7, "high_energy", selected=True, transcript="oh my god are they stupid"),
                    _row(120, 150, 0.69, "lore_or_story", selected=True, transcript="no way that's hilarious payoff"),
                ]
            ),
            target_beats=3,
            target_duration_seconds=45,
        )

        storyboard = build_storyboard_from_audit(
            audit,
            target_duration_seconds=45,
            story_shape="setup_escalate_punchline",
            created_at="2026-06-26T00:00:00Z",
        )

        texts = " ".join(beat["hook_text"].lower() for beat in storyboard["beats"])
        self.assertIn("oh my god", texts)
        self.assertNotEqual(storyboard["beats"][-1]["hook_text"], "")
        self.assertEqual(storyboard["beats"][-1]["role"], "punchline")

    def test_funny_storyboard_prefers_coherent_cluster_over_scattered_beats(self):
        audit = build_candidate_audit(
            _run_debug(
                [
                    _row(1064, 1101, 0.65, "commentary_or_review", selected=True, transcript="nix tutorial joke"),
                    _row(1172, 1212, 0.64, "commentary_or_review", selected=True, transcript="forgot the name joke"),
                    _row(1907, 1947, 0.95, "high_energy", selected=True, transcript="oh my god game inside the game"),
                    _row(1965, 2015, 0.82, "high_energy", selected=True, transcript="what the hell do I do here"),
                    _row(2198, 2239, 0.88, "high_energy", selected=True, transcript="bribe this guy oh my god fifty credits"),
                    _row(2818, 2864, 0.66, "lore_or_story", selected=True, transcript="story started too random"),
                    _row(4715, 4748, 0.69, "commentary_or_review", selected=True, transcript="what the hell how convenient"),
                ]
            ),
            target_beats=3,
            target_duration_seconds=60,
        )

        storyboard = build_storyboard_from_audit(
            audit,
            target_duration_seconds=60,
            story_shape="setup_escalate_punchline",
            created_at="2026-06-26T00:00:00Z",
        )

        starts = [beat["source_start"] for beat in storyboard["beats"]]
        self.assertEqual(len(starts), 3)
        self.assertTrue(all(1850 <= start <= 2250 for start in starts))
        self.assertLess(max(starts) - min(starts), 360)

    def test_storyboard_can_exclude_used_beats_for_additional_montages(self):
        audit = build_candidate_audit(
            _run_debug(
                [
                    _row(100, 125, 0.86, "high_energy", selected=True, transcript="first thread hook"),
                    _row(140, 165, 0.81, "commentary_or_review", selected=True, transcript="first thread escalation"),
                    _row(180, 205, 0.78, "lore_or_story", selected=True, transcript="first thread payoff"),
                    _row(900, 925, 0.8, "high_energy", selected=True, transcript="second thread hook"),
                    _row(940, 965, 0.76, "commentary_or_review", selected=True, transcript="second thread escalation"),
                    _row(980, 1005, 0.72, "lore_or_story", selected=True, transcript="second thread payoff"),
                ]
            ),
            target_beats=3,
            target_duration_seconds=60,
        )
        first = build_storyboard_from_audit(
            audit,
            target_duration_seconds=60,
            story_shape="hook_escalate_payoff",
            created_at="2026-06-26T00:00:00Z",
            storyboard_index=1,
        )
        used = {beat["beat_id"] for beat in first["beats"]}
        second = build_storyboard_from_audit(
            audit,
            target_duration_seconds=60,
            story_shape="hook_escalate_payoff",
            created_at="2026-06-26T00:00:00Z",
            excluded_beat_ids=used,
            storyboard_index=2,
        )

        self.assertEqual(len(first["beats"]), 3)
        self.assertEqual(len(second["beats"]), 3)
        self.assertTrue(used.isdisjoint({beat["beat_id"] for beat in second["beats"]}))
        self.assertNotEqual(first["storyboard_id"], second["storyboard_id"])

    def test_context_continuation_can_follow_a_strong_story_beat(self):
        audit = build_candidate_audit(
            _run_debug(
                [
                    _row(1907, 1947, 0.95, "high_energy", selected=True, transcript="game inside the game arcade joke"),
                    _row(1965, 2015, 0.82, "high_energy", selected=True, transcript="we will just sneak in to the club"),
                    _row(2198, 2239, 0.88, "high_energy", selected=True, transcript="we have to bribe this guy fifty credits not worth it"),
                    _context_row(2257, 2307, 0.52, "commentary_or_review", transcript="the door still will not open after the bribe"),
                ]
            ),
            target_beats=3,
            target_duration_seconds=60,
        )

        storyboard = build_storyboard_from_audit(
            audit,
            target_duration_seconds=60,
            story_shape="setup_escalate_punchline",
            created_at="2026-06-26T00:00:00Z",
        )

        starts = [beat["source_start"] for beat in storyboard["beats"]]
        self.assertIn(2257, starts)
        self.assertNotIn(1907, starts)
        self.assertTrue(any(beat.get("context_only") for beat in audit["beats"]))

    def test_funny_storyboard_reserves_more_time_for_payoff(self):
        audit = build_candidate_audit(
            _run_debug(
                [
                    _row(0, 40, 0.74, "commentary_or_review", selected=True, transcript="setup"),
                    _row(60, 100, 0.76, "commentary_or_review", selected=True, transcript="escalation"),
                    {
                        **_row(
                            120,
                            160,
                            0.88,
                            "high_energy",
                            selected=True,
                            transcript="we have to bribe this guy fifty credits and it was not worth it",
                        ),
                        "first_word_start": 3,
                    },
                ]
            ),
            target_beats=3,
            target_duration_seconds=60,
        )

        storyboard = build_storyboard_from_audit(
            audit,
            target_duration_seconds=60,
            story_shape="setup_escalate_punchline",
            created_at="2026-06-26T00:00:00Z",
        )

        self.assertLessEqual(storyboard["summary"]["planned_duration_seconds"], 60.1)
        self.assertGreaterEqual(storyboard["beats"][-1]["duration"], 24)
        self.assertEqual(storyboard["beats"][-1]["trim_reason"], "payoff_context")

    def test_funny_storyboard_penalizes_repeated_chatter_without_payoff(self):
        audit = build_candidate_audit(
            _run_debug(
                [
                    _row(100, 130, 0.92, "commentary_or_review", selected=True, transcript="pre bon pre bon pre bon pre bon pre bon pre bon"),
                    _row(140, 170, 0.74, "commentary_or_review", selected=True, transcript="we need credits to get into the club"),
                    _row(180, 210, 0.72, "commentary_or_review", selected=True, transcript="we have to bribe this guy and it was not worth it"),
                    _row(220, 250, 0.7, "lore_or_story", selected=True, transcript="the door still does not open after paying"),
                ]
            ),
            target_beats=3,
            target_duration_seconds=60,
        )

        storyboard = build_storyboard_from_audit(
            audit,
            target_duration_seconds=60,
            story_shape="setup_escalate_punchline",
            created_at="2026-06-26T00:00:00Z",
        )

        texts = " ".join(beat["hook_text"].lower() for beat in storyboard["beats"])
        repeated = next(beat for beat in audit["beats"] if "pre bon pre bon" in beat["hook_text"].lower())
        self.assertGreaterEqual(repeated["repetition_penalty"], 0.14)
        self.assertNotIn("pre bon pre bon", texts)

    def test_storyboard_marks_thin_if_ready_audit_collapses_to_one_window(self):
        audit = build_candidate_audit(
            _run_debug(
                [
                    _row(0, 18, 0.82, "high_energy", selected=True, transcript="first"),
                    _row(1, 19, 0.8, "commentary_or_review", selected=True, transcript="second"),
                    _row(1.5, 20, 0.78, "lore_or_story", selected=True, transcript="third"),
                ]
            ),
            target_beats=3,
        )

        storyboard = build_storyboard_from_audit(
            audit,
            target_duration_seconds=45,
            created_at="2026-06-26T00:00:00Z",
        )

        self.assertEqual(storyboard["status"], "thin_draft")
        self.assertIn("selected 1 beat", storyboard["reasons"][0])

    def test_storyboard_can_be_written_and_read_as_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audit = build_candidate_audit(_run_debug([_row(0, 18, 0.8, "high_energy", selected=True)]))
            storyboard = build_storyboard_from_audit(audit, created_at="2026-06-26T00:00:00Z")
            path = Path(temp_dir) / "montages" / "storyboard.json"
            write_storyboard(path, storyboard)

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["storyboard_id"], storyboard["storyboard_id"])

    def test_api_montage_audit_uses_latest_safe_run_debug(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subtitles = root / "subtitles"
            montages = root / "analysis_cache" / "montages"
            subtitles.mkdir(parents=True)
            debug_path = subtitles / "source_run_debug.json"
            debug_path.write_text(
                json.dumps(_run_debug([_row(0, 18, 0.8, "high_energy", selected=True)])),
                encoding="utf-8",
            )
            outside = root / "outside_run_debug.json"
            outside.write_text(json.dumps(_run_debug([])), encoding="utf-8")

            bridge = ApiBridge.__new__(ApiBridge)
            with patch("api_bridge.SUBTITLES_DIR", subtitles), patch("api_bridge.MONTAGES_DIR", montages):
                blocked = bridge.get_montage_candidate_audit(str(outside))
                result = bridge.get_montage_candidate_audit()

            self.assertFalse(blocked["ok"])
            self.assertTrue(result["ok"])
            self.assertEqual(result["audit"]["counts"]["usable_beat_count"], 1)
            self.assertTrue(Path(result["path"]).is_file())
            self.assertTrue(str(Path(result["path"]).resolve()).startswith(str(montages.resolve())))

    def test_api_draft_montage_writes_storyboard_and_blocks_outside_read(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subtitles = root / "subtitles"
            montages = root / "analysis_cache" / "montages"
            subtitles.mkdir(parents=True)
            debug_path = subtitles / "source_run_debug.json"
            debug_path.write_text(
                json.dumps(
                    _run_debug(
                        [
                            _row(0, 18, 0.82, "high_energy", selected=True),
                            _row(30, 48, 0.74, "tutorial_or_explainer", selected=True),
                            _row(60, 80, 0.68, "lore_or_story"),
                        ]
                    )
                ),
                encoding="utf-8",
            )
            outside = root / "outside_storyboard.json"
            outside.write_text("{}", encoding="utf-8")

            bridge = ApiBridge.__new__(ApiBridge)
            with patch("api_bridge.SUBTITLES_DIR", subtitles), patch("api_bridge.MONTAGES_DIR", montages):
                draft = bridge.draft_montage(target_duration_seconds=45)
                readback = bridge.get_montage_storyboard()
                blocked = bridge.get_montage_storyboard(str(outside))

            self.assertTrue(draft["ok"])
            self.assertEqual(draft["storyboard"]["summary"]["beat_count"], 3)
            self.assertTrue(Path(draft["path"]).is_file())
            self.assertTrue(readback["ok"])
            self.assertEqual(readback["storyboard"]["storyboard_id"], draft["storyboard"]["storyboard_id"])
            self.assertFalse(blocked["ok"])

    def test_render_draft_montage_creates_output_with_hard_cut_plan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            output = root / "clips" / "source_montage1.mp4"
            temp = root / "tmp"
            audit = build_candidate_audit(
                {
                    **_run_debug(
                        [
                            _row(0, 18, 0.82, "high_energy", selected=True),
                            _row(30, 48, 0.74, "tutorial_or_explainer", selected=True),
                        ]
                    ),
                    "video": str(source),
                },
                target_beats=2,
            )
            storyboard = build_storyboard_from_audit(audit, created_at="2026-06-26T00:00:00Z")

            def fake_run(cmd, **_kwargs):
                Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
                Path(cmd[-1]).write_bytes(b"mp4")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with patch("montage_renderer._run_ffmpeg", side_effect=fake_run):
                result = render_draft_montage(storyboard, output, temp_dir=temp)

            self.assertTrue(result["ok"])
            self.assertEqual(result["render_type"], "draft_hard_cut")
            self.assertEqual(len(result["segments"]), 2)
            self.assertTrue(output.exists())
            self.assertFalse(any(temp.glob("*_seg*.mp4")))

    def test_render_draft_montage_cleans_partial_segment_on_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            output = root / "clips" / "source_montage1.mp4"
            temp = root / "tmp"
            audit = build_candidate_audit(
                {
                    **_run_debug([_row(0, 18, 0.82, "high_energy", selected=True)]),
                    "video": str(source),
                },
                target_beats=1,
            )
            storyboard = build_storyboard_from_audit(audit, created_at="2026-06-26T00:00:00Z")

            def fake_run(cmd, **_kwargs):
                Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
                Path(cmd[-1]).write_bytes(b"partial")
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="failed")

            with patch("montage_renderer._run_ffmpeg", side_effect=fake_run):
                result = render_draft_montage(storyboard, output, temp_dir=temp)

            self.assertFalse(result["ok"])
            self.assertFalse(any(temp.glob("*_seg*.mp4")))

    def test_render_draft_montage_returns_failure_on_ffmpeg_exception(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            output = root / "clips" / "source_montage1.mp4"
            temp = root / "tmp"
            audit = build_candidate_audit(
                {
                    **_run_debug([_row(0, 18, 0.82, "high_energy", selected=True)]),
                    "video": str(source),
                },
                target_beats=1,
            )
            storyboard = build_storyboard_from_audit(audit, created_at="2026-06-26T00:00:00Z")

            with patch("montage_renderer._run_ffmpeg", side_effect=RuntimeError("boom")):
                result = render_draft_montage(storyboard, output, temp_dir=temp)

            self.assertFalse(result["ok"])
            self.assertIn("boom", result["error"])

    def test_api_render_montage_draft_adds_clip_and_blocks_unknown_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subtitles = root / "subtitles"
            montages = root / "analysis_cache" / "montages"
            clips = root / "clips"
            state_file = root / "viria_state.json"
            source = root / "source.mp4"
            source.write_bytes(b"video")
            subtitles.mkdir(parents=True)
            clips.mkdir(parents=True)
            run_debug = _run_debug(
                [
                    _row(0, 18, 0.82, "high_energy", selected=True),
                    _row(30, 48, 0.74, "tutorial_or_explainer", selected=True),
                    _row(60, 80, 0.68, "lore_or_story"),
                ]
            )
            run_debug["video"] = str(source)
            (subtitles / "source_run_debug.json").write_text(json.dumps(run_debug), encoding="utf-8")

            bridge = ApiBridge.__new__(ApiBridge)
            bridge._state_lock = threading.RLock()
            bridge._results = []
            bridge._moments = []
            bridge._scheduled = []
            bridge._upload_history = []
            bridge._delete_after_upload = False
            bridge._user_settings = {}
            bridge._source_context = {}
            bridge._download_info_by_path = {}

            def fake_render(storyboard, output_path, **_kwargs):
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_bytes(b"mp4")
                return {
                    "schema_version": 1,
                    "ok": True,
                    "status": "ok",
                    "output_path": str(output_path),
                    "filename": Path(output_path).name,
                    "size_bytes": 3,
                    "segments": [],
                    "render_type": "draft_hard_cut",
                    "stores_raw_media": False,
                }

            with patch("api_bridge.SUBTITLES_DIR", subtitles):
                with patch("api_bridge.MONTAGES_DIR", montages):
                    with patch("api_bridge.CLIPS_DIR", clips):
                        with patch("api_bridge.STATE_FILE", state_file):
                            with patch("api_bridge.render_draft_montage", side_effect=fake_render):
                                draft = bridge.draft_montage(target_duration_seconds=45)
                                rendered = bridge.render_montage_draft(draft["path"])
                                tampered = dict(draft["storyboard"])
                                tampered["source"] = dict(tampered["source"])
                                tampered["source"]["video"] = str(root / "unknown.mp4")
                                bad_path = montages / "bad_montage_storyboard.json"
                                bad_path.write_text(json.dumps(tampered), encoding="utf-8")
                                blocked = bridge.render_montage_draft(str(bad_path))

            self.assertTrue(rendered["ok"])
            self.assertEqual(rendered["clip"]["primary_category"], "montage")
            self.assertEqual(len(bridge._results), 1)
            self.assertTrue(Path(rendered["path"]).is_file())
            self.assertFalse(blocked["ok"])
            self.assertIn("saved run debug", blocked["error"])

    def test_api_render_montage_forces_plan_source_to_validated_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subtitles = root / "subtitles"
            montages = root / "analysis_cache" / "montages"
            clips = root / "clips"
            state_file = root / "viria_state.json"
            source = root / "source.mp4"
            other = root / "other.mp4"
            source.write_bytes(b"video")
            other.write_bytes(b"other")
            subtitles.mkdir(parents=True)
            clips.mkdir(parents=True)
            run_debug = _run_debug([_row(0, 18, 0.82, "high_energy", selected=True)])
            run_debug["video"] = str(source)
            (subtitles / "source_run_debug.json").write_text(json.dumps(run_debug), encoding="utf-8")

            bridge = ApiBridge.__new__(ApiBridge)
            bridge._state_lock = threading.RLock()
            bridge._results = []
            bridge._moments = []
            bridge._scheduled = []
            bridge._upload_history = []
            bridge._delete_after_upload = False
            bridge._user_settings = {}
            bridge._source_context = {}
            bridge._download_info_by_path = {}

            captured = {}

            def fake_render(storyboard_payload, output_path, **kwargs):
                captured["source_video"] = storyboard_payload["render_plan"][0]["source_video"]
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_bytes(b"mp4")
                return {
                    "schema_version": 1,
                    "ok": True,
                    "status": "ok",
                    "output_path": str(output_path),
                    "filename": Path(output_path).name,
                    "size_bytes": 3,
                    "segments": [{"duration": 18, "subtitles_burned": False}],
                    "render_type": kwargs.get("render_type"),
                    "stores_raw_media": False,
                }

            with patch("api_bridge.SUBTITLES_DIR", subtitles):
                with patch("api_bridge.MONTAGES_DIR", montages):
                    with patch("api_bridge.CLIPS_DIR", clips):
                        with patch("api_bridge.STATE_FILE", state_file):
                            with patch("api_bridge.render_draft_montage", side_effect=fake_render):
                                draft = bridge.draft_montage(target_duration_seconds=45)
                                storyboard_path = Path(draft["path"])
                                storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
                                storyboard["render_plan"][0]["source_video"] = str(other)
                                storyboard_path.write_text(json.dumps(storyboard), encoding="utf-8")
                                rendered = bridge.render_montage_draft(str(storyboard_path))

            self.assertTrue(rendered["ok"])
            self.assertEqual(Path(captured["source_video"]), source)

    def test_api_render_montage_final_writes_metadata_sidecar(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subtitles = root / "subtitles"
            montages = root / "analysis_cache" / "montages"
            clips = root / "clips"
            state_file = root / "viria_state.json"
            source = root / "source.mp4"
            source.write_bytes(b"video")
            subtitles.mkdir(parents=True)
            clips.mkdir(parents=True)
            run_debug = _run_debug(
                [
                    _row(0, 18, 0.82, "high_energy", selected=True, transcript="panic hook"),
                    _row(30, 48, 0.74, "tutorial_or_explainer", selected=True, transcript="route explanation"),
                    _row(60, 80, 0.68, "lore_or_story", transcript="story payoff"),
                ]
            )
            run_debug["video"] = str(source)
            (subtitles / "source_run_debug.json").write_text(json.dumps(run_debug), encoding="utf-8")

            bridge = ApiBridge.__new__(ApiBridge)
            bridge._state_lock = threading.RLock()
            bridge._results = []
            bridge._moments = []
            bridge._scheduled = []
            bridge._upload_history = []
            bridge._delete_after_upload = False
            bridge._user_settings = {"description_profile": {"custom_text": "", "auto_hashtags": True}}
            bridge._source_context = {}
            bridge._download_info_by_path = {}

            def fake_render(storyboard, output_path, **kwargs):
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_bytes(b"mp4")
                return {
                    "schema_version": 1,
                    "ok": True,
                    "status": "ok",
                    "output_path": str(output_path),
                    "filename": Path(output_path).name,
                    "size_bytes": 3,
                    "segments": [],
                    "render_type": kwargs.get("render_type"),
                    "stores_raw_media": False,
                }

            with patch("api_bridge.SUBTITLES_DIR", subtitles):
                with patch("api_bridge.MONTAGES_DIR", montages):
                    with patch("api_bridge.CLIPS_DIR", clips):
                        with patch("api_bridge.STATE_FILE", state_file):
                            with patch("api_bridge.render_draft_montage", side_effect=fake_render):
                                draft = bridge.draft_montage(target_duration_seconds=45)
                                rendered = bridge.render_montage_final(draft["path"])

            self.assertTrue(rendered["ok"])
            self.assertEqual(rendered["render"]["render_type"], "final_hard_cut")
            self.assertFalse(bridge._moments[0]["subtitles_burned"])
            self.assertTrue(bridge._moments[0]["upload_ready"])
            self.assertEqual(bridge._moments[0]["montage_render_type"], "final_hard_cut")
            metadata_file = Path(rendered["metadata"]["metadata_file"])
            self.assertTrue(metadata_file.is_file())
            text = metadata_file.read_text(encoding="utf-8")
            self.assertIn("Title:", text)
            self.assertIn("#shorts", text)
            self.assertIn("Game: Alan Wake", text)

    def test_pipeline_can_render_in_memory_storyboard_without_saved_run_debug(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            montages = root / "analysis_cache" / "montages"
            clips = root / "clips"
            state_file = root / "viria_state.json"
            source = root / "source.mp4"
            source.write_bytes(b"video")
            clips.mkdir(parents=True)
            audit = build_candidate_audit(
                {
                    **_run_debug(
                        [
                            _row(0, 18, 0.82, "high_energy", selected=True, transcript="panic hook"),
                            _row(30, 48, 0.74, "tutorial_or_explainer", selected=True, transcript="route explanation"),
                            _row(60, 80, 0.68, "lore_or_story", transcript="story payoff"),
                        ]
                    ),
                    "video": str(source),
                },
                target_beats=3,
            )
            storyboard = build_storyboard_from_audit(audit, created_at="2026-06-26T00:00:00Z")
            storyboard_path = montages / "source_montage_storyboard.json"

            bridge = ApiBridge.__new__(ApiBridge)
            bridge._state_lock = threading.RLock()
            bridge._results = []
            bridge._moments = []
            bridge._scheduled = []
            bridge._upload_history = []
            bridge._delete_after_upload = False
            bridge._user_settings = {"description_profile": {"custom_text": "", "auto_hashtags": True}}
            bridge._source_context = {}
            bridge._download_info_by_path = {}

            def fake_render(storyboard_payload, output_path, **kwargs):
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_bytes(b"mp4")
                return {
                    "schema_version": 1,
                    "ok": True,
                    "status": "ok",
                    "storyboard_id": storyboard_payload["storyboard_id"],
                    "output_path": str(output_path),
                    "filename": Path(output_path).name,
                    "size_bytes": 3,
                    "segments": [],
                    "render_type": kwargs.get("render_type"),
                    "stores_raw_media": False,
                }

            with patch("api_bridge.MONTAGES_DIR", montages):
                with patch("api_bridge.CLIPS_DIR", clips):
                    with patch("api_bridge.STATE_FILE", state_file):
                        with patch("api_bridge.render_draft_montage", side_effect=fake_render):
                            rendered = bridge._render_montage_storyboard_payload(
                                storyboard,
                                storyboard_path=str(storyboard_path),
                                final=True,
                            )

            self.assertTrue(rendered["ok"])
            self.assertEqual(rendered["render"]["render_type"], "final_hard_cut")
            self.assertEqual(len(bridge._results), 1)
            self.assertTrue(bridge._moments[0]["montage"])
            self.assertEqual(bridge._moments[0]["montage_render_type"], "final_hard_cut")
            self.assertTrue(Path(rendered["path"]).is_file())

    def test_api_record_montage_feedback_updates_run_learning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            montages = root / "analysis_cache" / "montages"
            run_learning_file = root / "run_learning.json"
            montages.mkdir(parents=True)
            audit = build_candidate_audit(
                _run_debug(
                    [
                        _row(0, 18, 0.82, "high_energy", selected=True, transcript="panic hook"),
                        _row(30, 48, 0.74, "tutorial_or_explainer", selected=True, transcript="route explanation"),
                    ]
                ),
                target_beats=2,
            )
            storyboard = build_storyboard_from_audit(audit, created_at="2026-06-26T00:00:00Z")
            path = montages / "source_montage_storyboard.json"
            write_storyboard(path, storyboard)

            bridge = ApiBridge.__new__(ApiBridge)
            bridge._run_learning_lock = threading.RLock()
            bridge._run_learning = empty_run_learning()

            with patch("api_bridge.MONTAGES_DIR", montages), patch("api_bridge.RUN_LEARNING_FILE", run_learning_file):
                whole = bridge.record_montage_feedback(
                    {
                        "storyboard_path": str(path),
                        "storyboard_id": storyboard["storyboard_id"],
                        "feedback_type": "like",
                        "reason": "good pacing",
                    }
                )
                beat = bridge.record_montage_feedback(
                    {
                        "storyboard_path": str(path),
                        "feedback_type": "dislike",
                        "beat_id": storyboard["beats"][0]["beat_id"],
                        "reason": "wrong opener",
                    }
                )
                blocked = bridge.record_montage_feedback(
                    {
                        "storyboard_path": str(path),
                        "feedback_type": "like",
                        "beat_id": "missing",
                    }
                )

            self.assertTrue(whole["ok"])
            self.assertTrue(beat["ok"])
            self.assertFalse(blocked["ok"])
            self.assertEqual(beat["event"]["beat_role"], "hook")
            self.assertEqual(beat["run_learning"]["montage_feedback_event_count"], 2)
            self.assertEqual(beat["run_learning"]["montage_beat_outcome_count"], 1)
            self.assertTrue(run_learning_file.is_file())


if __name__ == "__main__":
    unittest.main()
