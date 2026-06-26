import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import title_generator  # noqa: E402
import uploader  # noqa: E402


class _FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class ExternalStatusTruthTests(unittest.TestCase):
    def test_ollama_status_rejects_non_ollama_json(self):
        old_urlopen = title_generator.urllib.request.urlopen

        def fake_urlopen(req, timeout=0):
            return _FakeResponse({"ok": True})

        title_generator.urllib.request.urlopen = fake_urlopen
        try:
            status = title_generator.ollama_status()
        finally:
            title_generator.urllib.request.urlopen = old_urlopen

        self.assertFalse(status["running"])
        self.assertFalse(status["using_ollama"])
        self.assertEqual(status["models"], [])

    def test_ollama_status_requires_ready_model_for_using_ollama(self):
        old_urlopen = title_generator.urllib.request.urlopen

        def fake_urlopen(req, timeout=0):
            url = getattr(req, "full_url", str(req))
            if url.endswith("/api/tags"):
                return _FakeResponse({"models": [{"name": "qwen3.5:4b"}]})
            if url.endswith("/api/version"):
                return _FakeResponse({"version": "0.9.0"})
            return _FakeResponse({})

        title_generator.urllib.request.urlopen = fake_urlopen
        try:
            status = title_generator.ollama_status()
        finally:
            title_generator.urllib.request.urlopen = old_urlopen

        self.assertTrue(status["running"])
        self.assertTrue(status["using_ollama"])
        self.assertIn("qwen3.5:4b", status["models"])
        self.assertEqual(status["version"], "0.9.0")

    def test_ollama_status_rejects_tags_without_version(self):
        old_urlopen = title_generator.urllib.request.urlopen

        def fake_urlopen(req, timeout=0):
            url = getattr(req, "full_url", str(req))
            if url.endswith("/api/tags"):
                return _FakeResponse({"models": [{"name": "qwen3.5:4b"}]})
            if url.endswith("/api/version"):
                return _FakeResponse({"not_version": "nope"})
            return _FakeResponse({})

        title_generator.urllib.request.urlopen = fake_urlopen
        try:
            status = title_generator.ollama_status()
        finally:
            title_generator.urllib.request.urlopen = old_urlopen

        self.assertFalse(status["running"])
        self.assertFalse(status["using_ollama"])
        self.assertEqual(status["models"], [])

    def test_youtube_client_secret_is_not_a_connected_account(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            self._with_temp_uploader_paths(temp, self._assert_client_secret_not_connected)

    def test_token_account_id_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            self._with_temp_uploader_paths(temp, self._assert_token_account_id_rejects_path_traversal)

    def test_legacy_token_is_kept_when_migration_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            self._with_temp_uploader_paths(temp, self._assert_legacy_token_kept_on_failure)

    def test_list_accounts_skips_invalid_account_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            self._with_temp_uploader_paths(temp, self._assert_list_accounts_skips_invalid_ids)

    def _assert_client_secret_not_connected(self, temp):
        uploader._SECRETS_ROOT.write_text(
            json.dumps({"installed": {"client_id": "abc.apps.googleusercontent.com"}}),
            encoding="utf-8",
        )
        uploader._SECRETS_TOKENS.write_text(
            json.dumps({"installed": {"client_id": "def.apps.googleusercontent.com"}}),
            encoding="utf-8",
        )

        self.assertEqual(uploader.list_accounts(), [])
        self.assertFalse(uploader.is_connected())

        token_path = uploader._TOKENS_DIR / "UC123.json"
        token_path.write_text(
            json.dumps(
                {
                    "_account_id": "UC123",
                    "_account_title": "Expired Soda",
                    "refresh_token": "refresh",
                    "client_id": "abc.apps.googleusercontent.com",
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual(uploader.list_accounts(), [{"id": "UC123", "title": "Expired Soda"}])
        self.assertEqual(
            uploader.list_accounts(validate=True),
            [{"id": "UC123", "title": "Expired Soda", "usable": False, "status": "needs_reauth"}],
        )
        self.assertFalse(uploader.is_connected())

    def _assert_token_account_id_rejects_path_traversal(self, temp):
        outside = temp / "evil.json"
        outside.write_text("still here", encoding="utf-8")

        with self.assertRaises(ValueError):
            uploader._token_path("..\\evil")
        uploader.disconnect("..\\evil")

        self.assertTrue(outside.exists())

    def _assert_legacy_token_kept_on_failure(self, temp):
        uploader._TOKEN_LEGACY.write_text("{not json", encoding="utf-8")

        uploader._ensure_tokens_dir()

        self.assertTrue(uploader._TOKEN_LEGACY.exists())

    def _assert_list_accounts_skips_invalid_ids(self, temp):
        token_path = uploader._TOKENS_DIR / "bad.json"
        token_path.write_text(
            json.dumps(
                {
                    "_account_id": "../evil",
                    "_account_title": "Bad",
                    "refresh_token": "refresh",
                    "client_id": "abc.apps.googleusercontent.com",
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual(uploader.list_accounts(), [])

    def test_list_channels_uses_connected_account_identity_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            self._with_temp_uploader_paths(temp, self._assert_channel_listing_uses_account_identity)

    def _assert_channel_listing_uses_account_identity(self, temp):
        calls = []

        class FakeChannels:
            def list(self, **kwargs):
                calls.append(kwargs)
                return self

            def execute(self):
                return {
                    "items": [
                        {
                            "id": "UC123",
                            "snippet": {
                                "title": "Expired Soda",
                                "thumbnails": {"default": {"url": "thumb.jpg"}},
                            },
                            "statistics": {"subscriberCount": "42"},
                        }
                    ]
                }

        class FakeService:
            def __init__(self):
                self._channels = FakeChannels()

            def channels(self):
                return self._channels

        old_list_accounts = uploader.list_accounts
        old_get_youtube_service = uploader.get_youtube_service
        uploader.list_accounts = lambda: [{"id": "UC123", "title": "Expired Soda"}]
        uploader.get_youtube_service = lambda account_id: FakeService()
        try:
            channels = uploader.list_channels()
        finally:
            uploader.list_accounts = old_list_accounts
            uploader.get_youtube_service = old_get_youtube_service

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], {"part": "snippet,statistics", "mine": True})
        self.assertEqual(channels[0]["id"], "UC123")
        self.assertEqual(channels[0]["account_id"], "UC123")
        self.assertEqual(channels[0]["account_title"], "Expired Soda")

    def _with_temp_uploader_paths(self, temp, callback):
        old_values = {
            "_TOKENS_DIR": uploader._TOKENS_DIR,
            "_SECRETS_ROOT": uploader._SECRETS_ROOT,
            "_SECRETS_TOKENS": uploader._SECRETS_TOKENS,
            "_TOKEN_LEGACY": uploader._TOKEN_LEGACY,
        }
        try:
            tokens = temp / "tokens"
            tokens.mkdir()
            uploader._TOKENS_DIR = tokens
            uploader._SECRETS_ROOT = temp / "client_secrets.json"
            uploader._SECRETS_TOKENS = tokens / "client_secrets.json"
            uploader._TOKEN_LEGACY = temp / "token.json"
            uploader._service_cache.clear()
            return callback(temp)
        finally:
            for key, value in old_values.items():
                setattr(uploader, key, value)
            uploader._service_cache.clear()


if __name__ == "__main__":
    unittest.main()
