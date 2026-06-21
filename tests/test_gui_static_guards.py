import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GuiStaticGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_js = (ROOT / "gui" / "app.js").read_text(encoding="utf-8")

    def test_schedule_identity_does_not_fall_back_to_index_when_identity_exists(self):
        self.assertIn("if (item.clip_id || item.clip_filename) return -1;", self.app_js)

    def test_upload_completion_refreshes_schedule_from_backend(self):
        self.assertIn("await refreshScheduleFromBackend(false);", self.app_js)
        self.assertIn("window.onPipelineComplete = async function", self.app_js)
        self.assertIn("window.onPipelineCancelled = async function", self.app_js)

    def test_tags_are_not_overwritten_when_present(self):
        self.assertIn("if (!String(item.tags || '').trim()) item.tags = DEFAULT_UPLOAD_TAGS;", self.app_js)

    def test_today_scheduling_skips_past_peak_slots(self):
        self.assertIn("const SCHEDULE_BUFFER_MINUTES = 10;", self.app_js)
        self.assertIn("function _availableScheduleSlotsForDate", self.app_js)
        self.assertIn("function _resolveSchedulableDateTime", self.app_js)
        self.assertIn("while (!availableSlots.length)", self.app_js)

    def test_upload_preflight_rejects_past_public_schedule(self):
        self.assertIn("Reschedule missed uploads first", self.app_js)
        self.assertIn("s.privacy === 'public' && s._scheduledDate <= now", self.app_js)

    def test_channel_identity_does_not_guess_account_id_from_channel_id(self):
        self.assertIn("account_id: ch?.account_id || null", self.app_js)
        self.assertIn("state.scheduled = normalizeScheduledMetadata(state.scheduled);", self.app_js)

    def test_missed_actions_clear_backend_missed_state(self):
        self.assertIn("delete s.scheduler_status;", self.app_js)
        self.assertIn("delete s.missed_at;", self.app_js)
        self.assertIn("ensureSchedulerForPending();", self.app_js)

    def test_ollama_setup_buttons_are_status_aware(self):
        self.assertIn("id=\"btn-ollama-download\"", (ROOT / "gui" / "index.html").read_text(encoding="utf-8"))
        self.assertIn("id=\"btn-ollama-install\"", (ROOT / "gui" / "index.html").read_text(encoding="utf-8"))
        self.assertIn("id=\"btn-ollama-model\"", (ROOT / "gui" / "index.html").read_text(encoding="utf-8"))
        self.assertIn("function updateOllamaActionButtons", self.app_js)
        self.assertIn("Open Ollama Folder", self.app_js)
        self.assertIn("Ollama Installed", self.app_js)
        self.assertIn("Title Model Ready", self.app_js)

    def test_subtitle_none_option_is_available_in_settings_and_wizard(self):
        html = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")

        self.assertIn('name="subtitle-style" value="none"', html)
        self.assertIn('name="picker-style" value="none"', html)
        self.assertIn("Captions disabled", (ROOT / "api_bridge.py").read_text(encoding="utf-8"))
        self.assertIn("captions-disabled", self.app_js)


if __name__ == "__main__":
    unittest.main()
