import sys
import tempfile
import threading
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
    def test_youtube_service_strips_308_redirect_for_resumable_upload_progress(self):
        class FakeHttp:
            def __init__(self, timeout=None):
                self.timeout = timeout
                self.redirect_codes = frozenset({300, 301, 308})

        captured = {}

        def fake_authorized_http(creds, http):
            captured["creds"] = creds
            captured["http"] = http
            return "authorized-http"

        with patch("httplib2.Http", FakeHttp):
            with patch("google_auth_httplib2.AuthorizedHttp", side_effect=fake_authorized_http):
                with patch("googleapiclient.discovery.build", return_value="service") as build:
                    service = uploader._build_service("creds")

        self.assertEqual(service, "service")
        self.assertEqual(captured["creds"], "creds")
        self.assertNotIn(308, captured["http"].redirect_codes)
        self.assertIn(300, captured["http"].redirect_codes)
        build.assert_called_once_with("youtube", "v3", http="authorized-http")

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

    def test_public_publish_time_inside_backend_buffer_rejected(self):
        bridge = ApiBridge.__new__(ApiBridge)
        soon = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

        with self.assertRaisesRegex(ValueError, "at least 10 minutes"):
            bridge._validate_upload_metadata([{"title": "Too soon", "privacy": "public", "publish_at": soon}])

        ordered = bridge._validate_upload_metadata([{
            "title": "Private soon",
            "privacy": "private",
            "publish_at": soon,
        }])
        self.assertEqual(len(ordered), 1)

    def test_public_upload_requires_publish_at(self):
        bridge = ApiBridge.__new__(ApiBridge)

        with self.assertRaises(ValueError):
            bridge._validate_upload_metadata([{"title": "No schedule", "privacy": "public"}])

    def test_normalize_upload_history_defaults_corrupt_schema_version(self):
        bridge = ApiBridge.__new__(ApiBridge)

        normalized = bridge._normalize_upload_history([
            {"schema_version": "bad", "youtube_id": "yt-1"},
            {"schema_version": None, "youtube_id": "yt-2"},
            "not a row",
        ])

        self.assertEqual([row["schema_version"] for row in normalized], [1, 1])
        self.assertEqual([row["youtube_id"] for row in normalized], ["yt-1", "yt-2"])

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

    def test_mark_uploaded_records_upload_history(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._moments = [{"clip_id": "clip-1", "source_id": "source-1", "source_stem": "source"}]
        bridge._results = [Path("clip.mp4")]
        bridge._upload_history = []
        bridge._user_settings = {}
        bridge._save_state = lambda: None
        bridge._scheduled = [{
            "clipIdx": 0,
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "source_id": "source-1",
            "title": "Clip",
            "privacy": "public",
            "date": "2026-06-24",
            "time": "09:00",
            "uploaded": False,
        }]

        changed = bridge._mark_scheduled_uploaded(0, {
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "title": "Clip",
            "privacy": "public",
            "publish_at": "2026-06-24T13:00:00Z",
        }, {"id": "yt-123", "url": "https://youtu.be/yt-123"})

        self.assertTrue(changed)
        self.assertTrue(bridge._scheduled[0]["uploaded"])
        self.assertEqual(bridge._scheduled[0]["youtube_id"], "yt-123")
        self.assertEqual(bridge._scheduled[0]["upload_state"], "youtube_scheduled")
        self.assertEqual(len(bridge._upload_history), 1)
        self.assertEqual(bridge._upload_history[0]["youtube_id"], "yt-123")
        self.assertEqual(bridge._upload_history[0]["status"], "youtube_scheduled")

    def test_upload_attempt_marker_is_saved_before_youtube_upload_and_cleared_on_success(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._moments = [{"clip_id": "clip-1", "source_id": "source-1"}]
        bridge._results = [Path("clip.mp4")]
        bridge._upload_history = []
        saves = []
        bridge._save_state = lambda: saves.append(True)
        bridge._scheduled = [{
            "clipIdx": 0,
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "title": "Clip",
            "privacy": "private",
            "date": "2026-06-24",
            "time": "09:00",
            "uploaded": False,
        }]

        attempt_id = bridge._begin_scheduled_upload_attempt(0, {
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "title": "Clip",
        }, trigger="manual")

        self.assertTrue(attempt_id)
        self.assertTrue(saves)
        self.assertEqual(bridge._scheduled[0]["scheduler_status"], "uploading")
        self.assertEqual(bridge._scheduled[0]["upload_attempt_id"], attempt_id)
        self.assertTrue(bridge._scheduled[0]["upload_attempt_fingerprint"])
        self.assertEqual(bridge._scheduled[0]["upload_attempt_trigger"], "manual")

        bridge._mark_scheduled_uploaded(0, {
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "title": "Clip",
            "privacy": "private",
        }, {"id": "yt-123"})

        self.assertTrue(bridge._scheduled[0]["uploaded"])
        self.assertNotIn("scheduler_status", bridge._scheduled[0])
        self.assertNotIn("upload_attempt_id", bridge._scheduled[0])
        self.assertNotIn("upload_attempt_fingerprint", bridge._scheduled[0])

    def test_in_flight_upload_cannot_mark_rescheduled_slot_uploaded(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._moments = [{"clip_id": "clip-1", "source_id": "source-1"}]
        bridge._results = [Path("clip.mp4")]
        bridge._upload_history = []
        bridge._user_settings = {}
        saves = []
        bridge._save_state = lambda: saves.append(True)
        bridge._scheduled = [{
            "clipIdx": 0,
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "source_id": "source-1",
            "title": "Clip",
            "privacy": "private",
            "date": "2026-06-24",
            "time": "09:00",
            "uploaded": False,
        }]

        meta = {
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "source_id": "source-1",
            "title": "Clip",
            "privacy": "private",
            "date": "2026-06-24",
            "time": "09:00",
        }
        attempt_id = bridge._begin_scheduled_upload_attempt(0, meta, trigger="manual")
        self.assertTrue(attempt_id)

        # Simulate the user moving the calendar slot while YouTube still has
        # the old upload request in flight.
        bridge._scheduled[0]["date"] = "2026-06-25"
        bridge._scheduled[0]["time"] = "10:30"

        self.assertFalse(bridge._scheduled_upload_active(0, meta, attempt_id=attempt_id))
        self.assertFalse(bridge._mark_scheduled_uploaded(0, meta, {"id": "yt-old"}, attempt_id=attempt_id))
        self.assertFalse(bridge._scheduled[0].get("uploaded", False))
        self.assertNotIn("youtube_id", bridge._scheduled[0])
        self.assertFalse(bridge._mark_scheduled_upload_failed_for_clip(0, meta, RuntimeError("old upload failed"), attempt_id=attempt_id))
        self.assertNotEqual(bridge._scheduled[0].get("scheduler_status"), "upload_failed")

    def test_stale_upload_attempt_reopens_as_unknown_outcome_and_is_not_active(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._moments = [{"clip_id": "clip-1"}]
        bridge._results = [Path("clip.mp4")]
        bridge._scheduled = [{
            "clipIdx": 0,
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "title": "Clip",
            "privacy": "private",
            "uploaded": False,
            "scheduler_status": "uploading",
            "upload_attempt_id": "manual-old",
            "upload_attempt_started_at": "2026-06-23T12:00:00Z",
        }]

        changed = bridge._reconcile_incomplete_upload_attempts(datetime(2026, 6, 23, 12, 5, tzinfo=timezone.utc))

        self.assertTrue(changed)
        self.assertEqual(bridge._scheduled[0]["scheduler_status"], "upload_outcome_unknown")
        self.assertEqual(bridge._scheduled[0]["upload_unknown_at"], "2026-06-23T12:05:00Z")
        self.assertIn("Check YouTube Studio", bridge._scheduled[0]["scheduler_note"])
        self.assertFalse(bridge._scheduled_upload_active(0, {
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "title": "Clip",
        }))

    def test_schedule_refresh_does_not_mark_live_upload_unknown(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._state_lock = threading.RLock()
        bridge._moments = [{"clip_id": "clip-1"}]
        bridge._results = [Path("clip.mp4")]
        bridge._upload_history = []
        bridge._user_settings = {}
        bridge._save_state = lambda: None
        bridge._scheduled = [{
            "clipIdx": 0,
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "title": "Clip",
            "privacy": "private",
            "date": "2026-06-24",
            "time": "09:00",
            "uploaded": False,
        }]
        bridge._prune_missing_results = lambda: None
        bridge._mark_overdue_schedules_missed = lambda: False

        attempt_id = bridge._begin_scheduled_upload_attempt(0, {
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "title": "Clip",
        }, trigger="manual")

        refreshed = bridge.get_all_scheduled()

        self.assertTrue(attempt_id)
        self.assertEqual(refreshed["scheduled"][0]["scheduler_status"], "uploading")
        self.assertEqual(refreshed["scheduled"][0]["upload_attempt_id"], attempt_id)
        self.assertTrue(bridge._scheduled_upload_active(0, {
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "title": "Clip",
        }))

    def test_save_scheduled_preserves_backend_owned_status_fields(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._moments = [{
            "clip_id": "clip-1",
            "source_id": "source-1",
            "generated_metadata": {"generated_description": "stale old generated summary"},
        }]
        bridge._results = [Path("clip.mp4")]
        bridge._state_lock = threading.RLock()
        bridge._upload_history = []
        bridge._user_settings = {}
        saves = []
        bridge._save_state = lambda: saves.append(True)
        bridge._scheduled = [{
            "clipIdx": 0,
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "title": "Clip",
            "date": "2026-06-24",
            "time": "09:00",
            "channel_id": "channel-a",
            "account_id": "account-a",
            "uploaded": True,
            "uploaded_at": "2026-06-23T12:00:00Z",
            "youtube_id": "yt-123",
            "youtube_url": "https://youtu.be/yt-123",
        }]

        bridge.save_scheduled([{
            "clipIdx": 0,
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "title": "Clip",
            "date": "2026-06-24",
            "time": "09:00",
            "channel_id": "channel-a",
            "account_id": "account-a",
            "uploaded": False,
        }])

        self.assertTrue(saves)
        self.assertTrue(bridge._scheduled[0]["uploaded"])
        self.assertEqual(bridge._scheduled[0]["youtube_id"], "yt-123")

    def test_save_scheduled_resets_upload_success_fields_when_slot_changes(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._moments = [{"clip_id": "clip-1", "source_id": "source-1"}]
        bridge._results = [Path("clip.mp4")]
        bridge._state_lock = threading.RLock()
        bridge._upload_history = []
        bridge._user_settings = {}
        bridge._save_state = lambda: None
        bridge._scheduled = [{
            "clipIdx": 0,
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "title": "Clip",
            "description": "desc",
            "date": "2026-06-24",
            "time": "09:00",
            "channel_id": "channel-a",
            "account_id": "account-a",
            "uploaded": True,
            "uploaded_at": "2026-06-23T12:00:00Z",
            "youtube_id": "yt-123",
            "youtube_url": "https://youtu.be/yt-123",
            "upload_state": "sent_to_youtube",
            "send_status": "sent_to_youtube",
        }]

        bridge.save_scheduled([{
            "clipIdx": 0,
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "title": "Clip",
            "description": "desc",
            "date": "2026-06-25",
            "time": "09:00",
            "channel_id": "channel-a",
            "account_id": "account-a",
            "uploaded": True,
            "uploaded_at": "2026-06-23T12:00:00Z",
            "youtube_id": "yt-123",
            "youtube_url": "https://youtu.be/yt-123",
            "upload_state": "sent_to_youtube",
            "send_status": "sent_to_youtube",
        }])

        item = bridge._scheduled[0]
        self.assertFalse(item["uploaded"])
        self.assertNotIn("uploaded_at", item)
        self.assertNotIn("youtube_id", item)
        self.assertNotIn("youtube_url", item)
        self.assertNotIn("upload_state", item)
        self.assertNotIn("send_status", item)

    def test_save_scheduled_preserves_in_progress_upload_attempt_fields(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._moments = [{"clip_id": "clip-1", "source_id": "source-1"}]
        bridge._results = [Path("clip.mp4")]
        bridge._state_lock = threading.RLock()
        bridge._upload_history = []
        bridge._user_settings = {}
        bridge._description_profile = lambda: {"custom_text": "", "auto_hashtags": True}
        bridge._schedule_game_title = lambda item, idx: ""
        bridge._title_context_for_clip = lambda idx: {}
        bridge._compose_clip_description = lambda title, game_title, **kwargs: {
            "description": title,
            "final_description": title,
            "generated_description": "",
            "description_custom_text": "",
            "description_auto_hashtags": True,
            "recommended_hashtags": [],
        }
        bridge._save_state = lambda: None
        bridge._scheduled = [{
            "clipIdx": 0,
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "title": "Clip",
            "date": "2026-06-24",
            "time": "09:00",
            "uploaded": False,
            "scheduler_status": "uploading",
            "upload_attempt_id": "manual-abc",
            "upload_attempt_started_at": "2026-06-23T12:00:00Z",
            "upload_attempt_trigger": "manual",
        }]

        bridge.save_scheduled([{
            "clipIdx": 0,
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "title": "Clip",
            "date": "2026-06-24",
            "time": "09:00",
            "uploaded": False,
        }])

        self.assertEqual(bridge._scheduled[0]["scheduler_status"], "uploading")
        self.assertEqual(bridge._scheduled[0]["upload_attempt_id"], "manual-abc")
        self.assertEqual(bridge._scheduled[0]["upload_attempt_trigger"], "manual")

    def test_save_scheduled_preserves_creator_title_context(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._moments = [{"clip_id": "clip-1", "source_id": "source-1"}]
        bridge._results = [Path("clip.mp4")]
        bridge._state_lock = threading.RLock()
        bridge._upload_history = []
        bridge._user_settings = {}
        bridge._description_profile = lambda: {"custom_text": "", "auto_hashtags": True}
        bridge._schedule_game_title = lambda item, idx: ""
        bridge._title_context_for_clip = lambda idx: {"creator_title_context": "chapter note"}
        bridge._compose_clip_description = lambda title, game_title, **kwargs: {
            "description": kwargs.get("generated_text") or title,
            "final_description": kwargs.get("generated_text") or title,
            "generated_description": kwargs.get("generated_text") or "",
            "description_custom_text": "",
            "description_auto_hashtags": True,
            "recommended_hashtags": [],
        }
        bridge._save_state = lambda: None
        bridge._scheduled = []

        bridge.save_scheduled([{
            "clipIdx": 0,
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "source_id": "source-1",
            "title": "Clip",
            "date": "2026-06-24",
            "time": "09:00",
            "creator_title_context": "  chapter note  ",
            "description_generated": "",
            "generated_description": "",
        }])

        self.assertEqual(bridge._scheduled[0]["creator_title_context"], "chapter note")
        self.assertEqual(bridge._moments[0]["creator_title_context"], "chapter note")
        self.assertEqual(bridge._scheduled[0]["description_generated"], "")

    def test_reopen_marks_overdue_schedule_missed(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._scheduled = [{
            "title": "Old Private",
            "privacy": "private",
            "date": "2026-06-22",
            "time": "12:00",
            "uploaded": False,
        }]

        changed = bridge._mark_overdue_schedules_missed(datetime(2026, 6, 22, 12, 11, 0))

        self.assertTrue(changed)
        self.assertEqual(bridge._scheduled[0]["scheduler_status"], "missed")
        self.assertEqual(bridge._scheduled[0]["missed_at"], "2026-06-22T12:11:00")

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
        ], None, None)

        self.assertTrue(any("onPipelineComplete(false, 0, 1" in msg for msg in bridge._js_messages))
        self.assertFalse(bridge._processing)

    def test_scheduler_marks_missed_upload_without_auto_uploading(self):
        bridge = ApiBridge.__new__(ApiBridge)
        bridge._scheduler_running = True
        bridge._scheduled = [{
            "clipIdx": 0,
            "title": "Missed Upload",
            "privacy": "private",
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

    def test_scheduler_retries_failed_upload_even_after_original_window(self):
        bridge = ApiBridge.__new__(ApiBridge)
        due = (datetime.now() - timedelta(hours=2)).replace(microsecond=0)
        retry_due = (datetime.now() - timedelta(minutes=1)).replace(microsecond=0)
        bridge._scheduler_running = True
        bridge._scheduled = [{
            "clipIdx": 0,
            "clip_id": "clip-retry",
            "clip_filename": "retry.mp4",
            "title": "Retry Clip",
            "description": "desc",
            "privacy": "private",
            "date": due.strftime("%Y-%m-%d"),
            "time": due.strftime("%H:%M"),
            "uploaded": False,
            "scheduler_status": "upload_failed",
            "retry_after": retry_due.isoformat(),
        }]
        bridge._results = [Path("retry.mp4")]
        bridge._moments = [{"clip_id": "clip-retry"}]
        bridge._upload_history = []
        bridge._processing = False
        bridge._cancel = False
        bridge._delete_after_upload = False
        bridge._upload_lock = threading.Lock()
        bridge._safe_clip_path = lambda path: Path(path)
        bridge._save_state = lambda: None
        bridge._js_messages = []
        bridge._js = bridge._js_messages.append

        def stop_after_first_loop(_seconds):
            bridge._scheduler_running = False

        with patch("api_bridge.time.sleep", side_effect=stop_after_first_loop):
            with patch("api_bridge.upload_to_youtube", return_value={"id": "yt-retry", "url": "https://youtu.be/yt-retry"}) as upload:
                bridge._scheduler_loop()

        upload.assert_called_once()
        self.assertTrue(bridge._scheduled[0]["uploaded"])
        self.assertEqual(bridge._scheduled[0]["youtube_id"], "yt-retry")
        self.assertNotEqual(bridge._scheduled[0].get("scheduler_status"), "missed")

    def test_scheduler_success_records_youtube_result_and_history(self):
        bridge = ApiBridge.__new__(ApiBridge)
        due = (datetime.now() - timedelta(minutes=1)).replace(microsecond=0)
        bridge._scheduler_running = True
        bridge._scheduled = [{
            "clipIdx": 0,
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "title": "Scheduler Clip",
            "description": "desc",
            "privacy": "private",
            "date": due.strftime("%Y-%m-%d"),
            "time": due.strftime("%H:%M"),
            "uploaded": False,
        }]
        bridge._results = [Path("clip.mp4")]
        bridge._moments = [{"clip_id": "clip-1"}]
        bridge._upload_history = []
        bridge._processing = False
        bridge._cancel = False
        bridge._delete_after_upload = False
        bridge._upload_lock = threading.Lock()
        bridge._safe_clip_path = lambda path: Path(path)
        bridge._save_state = lambda: None
        bridge._js_messages = []
        bridge._js = bridge._js_messages.append

        def stop_after_first_loop(_seconds):
            bridge._scheduler_running = False

        with patch("api_bridge.time.sleep", side_effect=stop_after_first_loop):
            with patch("api_bridge.upload_to_youtube", return_value={"id": "yt-456", "url": "https://youtu.be/yt-456"}):
                bridge._scheduler_loop()

        self.assertTrue(bridge._scheduled[0]["uploaded"])
        self.assertEqual(bridge._scheduled[0]["youtube_id"], "yt-456")
        self.assertEqual(bridge._scheduled[0]["upload_state"], "sent_to_youtube")
        self.assertEqual(bridge._upload_history[0]["trigger"], "scheduler")
        self.assertEqual(bridge._upload_history[0]["youtube_url"], "https://youtu.be/yt-456")

    def test_upload_single_clip_uses_schedule_state_and_publish_time(self):
        bridge = ApiBridge.__new__(ApiBridge)
        publish_at = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(microsecond=0)
        bridge._results = [Path("clip.mp4")]
        bridge._moments = [{"clip_id": "clip-1"}]
        bridge._scheduled = [{
            "clipIdx": 0,
            "clip_id": "clip-1",
            "clip_filename": "clip.mp4",
            "title": "Single Clip",
            "description": "desc",
            "privacy": "public",
            "publish_at": publish_at.isoformat(),
            "channel_id": "channel-1",
            "account_id": "account-1",
            "uploaded": False,
        }]
        bridge._upload_history = []
        bridge._processing = False
        bridge._cancel = False
        bridge._safe_clip_path = lambda path: Path(path)
        bridge._save_state = lambda: None
        bridge._js_messages = []
        bridge._js = bridge._js_messages.append

        with patch("api_bridge.upload_to_youtube", return_value={"id": "yt-single", "url": "https://youtu.be/yt-single"}) as upload:
            result = bridge.upload_single_clip(0, dict(bridge._scheduled[0]), channel_id="channel-1")

        self.assertTrue(result["ok"])
        self.assertEqual(upload.call_args.kwargs["scheduled_time"], publish_at)
        self.assertTrue(bridge._scheduled[0]["uploaded"])
        self.assertEqual(bridge._scheduled[0]["upload_state"], "youtube_scheduled")
        self.assertEqual(bridge._scheduled[0]["youtube_id"], "yt-single")
        self.assertEqual(bridge._upload_history[0]["trigger"], "single")
        self.assertFalse(bridge._processing)
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
        item = {
            "title": "Retry Me",
            "scheduler_status": "uploading",
            "upload_attempt_id": "scheduler-abc",
            "upload_attempt_started_at": "2026-06-22T11:59:00Z",
        }
        now = datetime(2026, 6, 22, 12, 0, 0)

        bridge._mark_scheduled_upload_failed(item, RuntimeError("auth failed"), now)

        self.assertEqual(item["scheduler_status"], "upload_failed")
        self.assertEqual(item["failure_count"], 1)
        self.assertIn("auth failed", item["last_error"])
        self.assertEqual(item["retry_after"], "2026-06-22T12:05:00")
        self.assertNotIn("upload_attempt_id", item)
        self.assertFalse(bridge._scheduled_retry_due(item, datetime(2026, 6, 22, 12, 4, 59)))
        self.assertTrue(bridge._scheduled_retry_due(item, datetime(2026, 6, 22, 12, 5, 0)))

        bridge._mark_scheduled_upload_failed(item, RuntimeError("still failed"), now)
        self.assertEqual(item["failure_count"], 2)
        self.assertEqual(item["retry_after"], "2026-06-22T12:10:00")

    def test_scheduled_retry_due_accepts_timezone_aware_retry_after(self):
        bridge = ApiBridge.__new__(ApiBridge)
        item = {"retry_after": "2026-06-22T12:05:00+00:00"}

        self.assertFalse(bridge._scheduled_retry_due(item, datetime(2026, 6, 22, 12, 4, 59, tzinfo=timezone.utc)))
        self.assertTrue(bridge._scheduled_retry_due(item, datetime(2026, 6, 22, 12, 5, 0, tzinfo=timezone.utc)))

        item["retry_after"] = "2026-06-22T12:05:00Z"
        self.assertTrue(bridge._scheduled_retry_due(item, datetime(2026, 6, 22, 12, 5, 0, tzinfo=timezone.utc)))

    def test_uploader_rejects_public_schedule_inside_buffer_before_auth(self):
        soon = datetime.now(timezone.utc) + timedelta(minutes=5)

        with patch("uploader.get_youtube_service") as service:
            with tempfile.NamedTemporaryFile(suffix=".mp4") as video_file:
                with self.assertRaisesRegex(ValueError, "at least 10 minutes"):
                    upload_to_youtube(
                        Path(video_file.name),
                        title="Too Soon",
                        privacy="public",
                        scheduled_time=soon,
                    )

        service.assert_not_called()

    def test_public_scheduled_upload_sets_youtube_publish_at(self):
        class FakeRequest:
            def next_chunk(self, **kwargs):
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
            def next_chunk(self, **kwargs):
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

    def test_upload_uses_google_client_chunk_retries(self):
        calls = []

        class FakeRequest:
            def next_chunk(self, **kwargs):
                calls.append(kwargs)
                return None, {"id": "video-id"}

        class FakeService:
            def videos(self):
                return self

            def insert(self, part, body, media_body):
                return FakeRequest()

        with tempfile.NamedTemporaryFile(suffix=".mp4") as video_file:
            with patch("uploader.get_youtube_service", return_value=FakeService()):
                with patch("googleapiclient.http.MediaFileUpload", return_value=object()):
                    result = upload_to_youtube(Path(video_file.name), title="Uploaded Clip", privacy="private")

        self.assertEqual(result["id"], "video-id")
        self.assertEqual(calls, [{"num_retries": uploader.YOUTUBE_UPLOAD_CHUNK_RETRIES}])

    def test_upload_loop_has_chunk_cap(self):
        class FakeRequest:
            def next_chunk(self, **kwargs):
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
            def next_chunk(self, **kwargs):
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
