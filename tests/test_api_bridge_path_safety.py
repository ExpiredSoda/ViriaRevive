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
from config import CLIPS_DIR  # noqa: E402


class ApiBridgePathSafetyTests(unittest.TestCase):
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

    def test_clip_payload_url_escapes_special_filename_chars(self):
        CLIPS_DIR.mkdir(exist_ok=True)
        clip_path = CLIPS_DIR / "clip with # tag.mp4"
        clip_path.write_text("video", encoding="utf-8")
        try:
            bridge = ApiBridge.__new__(ApiBridge)
            bridge._video_port = 12345
            bridge._moments = [{"clip_id": "clip-1", "source_id": "source-1"}]
            bridge._ensure_moment_identity = lambda moment, path: moment

            payload = bridge._clip_payload(0, clip_path)

            self.assertIn("clip%20with%20%23%20tag.mp4", payload["url"])
        finally:
            clip_path.unlink(missing_ok=True)

    def test_list_all_clips_escapes_library_urls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_clips = Path(temp_dir)
            clip_path = temp_clips / "library clip #1.mp4"
            clip_path.write_text("video", encoding="utf-8")

            bridge = ApiBridge.__new__(ApiBridge)
            bridge._video_port = 12345
            bridge._results = []
            bridge._moments = []
            bridge._scheduled = []
            bridge._state_lock = threading.RLock()
            bridge._prune_missing_results = lambda: 0

            with patch("api_bridge.CLIPS_DIR", temp_clips):
                result = bridge.list_all_clips()

            self.assertIn("library%20clip%20%231.mp4", result["clips"][0]["url"])

    def test_delete_after_upload_toggle_persists_immediately(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._delete_after_upload = False
        saves = []
        bridge._save_state = lambda: saves.append(True)

        result = bridge.set_delete_after_upload(True)

        self.assertTrue(result["enabled"])
        self.assertEqual(saves, [True])

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
