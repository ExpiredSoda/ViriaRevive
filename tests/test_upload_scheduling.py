import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api_bridge import ApiBridge  # noqa: E402
import uploader  # noqa: E402
from uploader import upload_to_youtube  # noqa: E402


class UploadSchedulingTests(unittest.TestCase):
    def test_publish_at_zulu_parses_as_utc(self):
        parsed = ApiBridge._parse_publish_at({"publish_at": "2026-11-01T05:30:00.000Z"})

        self.assertEqual(parsed, datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc))

    def test_dst_ambiguous_local_time_uses_captured_offset(self):
        daylight = ApiBridge._parse_publish_at({
            "scheduled_local": "2026-11-01T01:30:00",
            "timezone_offset_minutes": 240,
        })
        standard = ApiBridge._parse_publish_at({
            "scheduled_local": "2026-11-01T01:30:00",
            "timezone_offset_minutes": 300,
        })

        self.assertEqual(daylight, datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc))
        self.assertEqual(standard, datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc))

    def test_upload_metadata_sorts_by_per_clip_publish_at(self):
        ordered = ApiBridge._ordered_upload_metadata([
            {"title": "Later", "publish_at": "2026-06-22T18:00:00Z"},
            {"title": "Earlier", "publish_at": "2026-06-22T14:00:00Z"},
        ])

        self.assertEqual([item[1]["title"] for item in ordered], ["Earlier", "Later"])

    def test_public_past_publish_time_rejected_but_private_upload_allowed(self):
        bridge = ApiBridge.__new__(ApiBridge)
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        with self.assertRaises(ValueError):
            bridge._validate_upload_metadata([{"title": "Past public", "privacy": "public", "publish_at": past}])

        ordered = bridge._validate_upload_metadata([{
            "title": "Past private",
            "privacy": "private",
            "publish_at": past,
        }])
        self.assertEqual(len(ordered), 1)

    def test_public_upload_requires_publish_at(self):
        bridge = ApiBridge.__new__(ApiBridge)

        with self.assertRaises(ValueError):
            bridge._validate_upload_metadata([{"title": "No schedule", "privacy": "public"}])

    def test_active_upload_checks_metadata_channel_id(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._moments = [{"clip_id": "clip-1"}]
        bridge._results = []
        bridge._scheduled = [{
            "clipIdx": 0,
            "clip_id": "clip-1",
            "channel_id": "channel-a",
            "account_id": "account-a",
            "title": "Clip",
            "uploaded": False,
        }]

        self.assertTrue(bridge._scheduled_upload_active(0, {
            "clip_id": "clip-1",
            "channel_id": "channel-a",
            "account_id": "account-a",
            "title": "Clip",
        }))
        self.assertFalse(bridge._scheduled_upload_active(0, {
            "clip_id": "clip-1",
            "channel_id": "channel-b",
            "account_id": "account-a",
            "title": "Clip",
        }))

    def test_stale_schedule_identity_does_not_match_shifted_index(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._moments = [{"clip_id": "fresh-clip"}]
        bridge._results = [Path("fresh.mp4")]
        bridge._scheduled = [{
            "clipIdx": 0,
            "clip_id": "old-clip",
            "clip_filename": "old.mp4",
            "title": "Clip",
            "uploaded": False,
        }]

        self.assertFalse(bridge._scheduled_upload_active(0, {
            "clip_id": "fresh-clip",
            "clip_filename": "fresh.mp4",
            "title": "Clip",
        }))
        self.assertFalse(bridge._mark_scheduled_uploaded(0, {
            "clip_id": "fresh-clip",
            "clip_filename": "fresh.mp4",
            "title": "Clip",
        }))
        self.assertFalse(bridge._scheduled[0].get("uploaded", False))

    def test_legacy_schedule_without_identity_can_still_match_index(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._moments = []
        bridge._results = []
        bridge._scheduled = [{"clipIdx": 0, "title": "Legacy", "uploaded": False}]

        self.assertTrue(bridge._scheduled_upload_active(0, {"title": "Legacy"}))

    def test_run_upload_reports_missing_scheduled_clip_as_failure(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._results = []
        bridge._moments = []
        bridge._scheduled = [{
            "clipIdx": 0,
            "clip_id": "missing-clip",
            "title": "Missing",
            "uploaded": False,
        }]
        bridge._cancel = False
        bridge._processing = True
        bridge._js_messages = []
        bridge._js = bridge._js_messages.append

        bridge._run_upload([
            {"clip_id": "missing-clip", "title": "Missing", "privacy": "private"}
        ], None, None, None, None)

        self.assertTrue(any("onPipelineComplete(false, 0, 1" in msg for msg in bridge._js_messages))
        self.assertFalse(bridge._processing)

    def test_scheduler_marks_missed_public_upload_without_auto_uploading(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._scheduler_running = True
        bridge._scheduled = [{
            "clipIdx": 0,
            "title": "Missed Public",
            "privacy": "public",
            "date": "2026-01-01",
            "time": "12:00",
            "uploaded": False,
        }]
        bridge._results = []
        bridge._moments = []
        bridge._processing = False
        bridge._save_state = lambda: None
        bridge._js_messages = []
        bridge._js = bridge._js_messages.append

        def stop_after_first_loop(_seconds):
            bridge._scheduler_running = False

        with patch("api_bridge.time.sleep", side_effect=stop_after_first_loop):
            with patch("api_bridge.upload_to_youtube") as upload:
                bridge._scheduler_loop()

        upload.assert_not_called()
        self.assertFalse(bridge._scheduled[0].get("uploaded"))
        self.assertEqual(bridge._scheduled[0].get("scheduler_status"), "missed")
        self.assertTrue(any("window.onScheduleUpdated()" in msg for msg in bridge._js_messages))

    def test_scheduler_skips_disconnected_account_items(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._scheduler_running = True
        bridge._scheduled = [{
            "clipIdx": 0,
            "title": "Disconnected",
            "privacy": "private",
            "date": "2026-01-01",
            "time": "12:00",
            "uploaded": False,
            "scheduler_status": "account_disconnected",
        }]
        bridge._results = [Path("missing.mp4")]
        bridge._moments = []
        bridge._processing = False
        bridge._save_state = lambda: None
        bridge._js_messages = []
        bridge._js = bridge._js_messages.append

        def stop_after_first_loop(_seconds):
            bridge._scheduler_running = False

        with patch("api_bridge.time.sleep", side_effect=stop_after_first_loop):
            with patch("api_bridge.upload_to_youtube") as upload:
                bridge._scheduler_loop()

        upload.assert_not_called()
        self.assertEqual(len(bridge._scheduled), 1)
        self.assertEqual(bridge._scheduled[0].get("scheduler_status"), "account_disconnected")

    def test_scheduled_upload_failure_sets_retry_backoff(self):
        bridge = ApiBridge.__new__(ApiBridge)
        item = {"title": "Retry Me"}
        now = datetime(2026, 6, 22, 12, 0, 0)

        bridge._mark_scheduled_upload_failed(item, RuntimeError("auth failed"), now)

        self.assertEqual(item["scheduler_status"], "upload_failed")
        self.assertEqual(item["failure_count"], 1)
        self.assertIn("auth failed", item["last_error"])
        self.assertEqual(item["retry_after"], "2026-06-22T12:05:00")
        self.assertFalse(bridge._scheduled_retry_due(item, datetime(2026, 6, 22, 12, 4, 59)))
        self.assertTrue(bridge._scheduled_retry_due(item, datetime(2026, 6, 22, 12, 5, 0)))

        bridge._mark_scheduled_upload_failed(item, RuntimeError("still failed"), now)
        self.assertEqual(item["failure_count"], 2)
        self.assertEqual(item["retry_after"], "2026-06-22T12:10:00")

    def test_public_scheduled_upload_sets_youtube_publish_at(self):
        class FakeRequest:
            def next_chunk(self):
                return None, {"id": "video-id"}

        class FakeService:
            def __init__(self):
                self.body = None

            def videos(self):
                return self

            def insert(self, part, body, media_body):
                self.body = body
                return FakeRequest()

        service = FakeService()
        scheduled = datetime.now(timezone.utc) + timedelta(days=2)

        with tempfile.NamedTemporaryFile(suffix=".mp4") as video_file:
            with patch("uploader.get_youtube_service", return_value=service):
                with patch("googleapiclient.http.MediaFileUpload", return_value=object()):
                    result = upload_to_youtube(
                        Path(video_file.name),
                        title="Scheduled Clip",
                        description="Test",
                        privacy="public",
                        scheduled_time=scheduled,
                    )

        self.assertEqual(result["id"], "video-id")
        self.assertEqual(service.body["status"]["privacyStatus"], "private")
        self.assertIn("publishAt", service.body["status"])

    def test_cancel_after_final_chunk_still_returns_uploaded_video(self):
        class FakeStatus:
            def progress(self):
                return 1.0

        class FakeRequest:
            def next_chunk(self):
                return FakeStatus(), {"id": "video-id"}

        class FakeService:
            def videos(self):
                return self

            def insert(self, part, body, media_body):
                return FakeRequest()

        calls = {"cancel": 0}

        def cancel_check():
            calls["cancel"] += 1
            return calls["cancel"] > 1

        progress = []
        with tempfile.NamedTemporaryFile(suffix=".mp4") as video_file:
            with patch("uploader.get_youtube_service", return_value=FakeService()):
                with patch("googleapiclient.http.MediaFileUpload", return_value=object()):
                    result = upload_to_youtube(
                        Path(video_file.name),
                        title="Uploaded Clip",
                        privacy="private",
                        cancel_check=cancel_check,
                        on_progress=progress.append,
                    )

        self.assertEqual(result["id"], "video-id")
        self.assertEqual(progress, [100])

    def test_upload_loop_has_chunk_cap(self):
        class FakeRequest:
            def next_chunk(self):
                return None, None

        class FakeService:
            def videos(self):
                return self

            def insert(self, part, body, media_body):
                return FakeRequest()

        with tempfile.NamedTemporaryFile(suffix=".mp4") as video_file:
            with patch("uploader.get_youtube_service", return_value=FakeService()):
                with patch("googleapiclient.http.MediaFileUpload", return_value=object()):
                    with patch.object(uploader, "YOUTUBE_UPLOAD_MAX_CHUNKS", 2):
                        with self.assertRaises(TimeoutError):
                            upload_to_youtube(Path(video_file.name), title="Stuck", privacy="private")

    def test_upload_loop_has_wall_clock_timeout(self):
        class FakeRequest:
            def next_chunk(self):
                return None, None

        class FakeService:
            def videos(self):
                return self

            def insert(self, part, body, media_body):
                return FakeRequest()

        times = iter([0, 10_000])
        with tempfile.NamedTemporaryFile(suffix=".mp4") as video_file:
            with patch("uploader.get_youtube_service", return_value=FakeService()):
                with patch("googleapiclient.http.MediaFileUpload", return_value=object()):
                    with patch("uploader.time.monotonic", side_effect=lambda: next(times)):
                        with self.assertRaises(TimeoutError):
                            upload_to_youtube(Path(video_file.name), title="Stuck", privacy="private")


if __name__ == "__main__":
    unittest.main()
