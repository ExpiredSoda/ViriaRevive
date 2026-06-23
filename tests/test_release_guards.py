import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import check_release_safety, write_release_hashes  # noqa: E402


class ReleaseGuardTests(unittest.TestCase):
    def test_startup_shortcut_uses_literal_quote_char_not_backslash_quotes(self):
        setup = (ROOT / "setup_startup.bat").read_text(encoding="utf-8")

        self.assertIn("$q = [char]34", setup)
        self.assertIn("$s.Arguments = $q + $env:VIRIA_VBS_PATH + $q", setup)
        self.assertNotIn("'\\\"' + $env:VIRIA_VBS_PATH + '\\\"'", setup)

    def test_installer_removes_startup_shortcut_on_uninstall(self):
        iss = (ROOT / "installer" / "ViriaRevive.iss").read_text(encoding="utf-8")

        self.assertIn("[UninstallDelete]", iss)
        self.assertIn('Name: "{userstartup}\\{#MyAppName}.lnk"', iss)

    def test_installer_build_clears_stale_setup_artifacts_before_compile(self):
        script = (ROOT / "build_installer.bat").read_text(encoding="utf-8")

        self.assertIn('del /Q "release\\ViriaReviveSetup-v%APP_VERSION%.exe"', script)
        self.assertIn('del /Q "release\\ViriaReviveSetup-v%APP_VERSION%.exe.sha256"', script)

    def test_release_safety_scans_app_owned_internal_dirs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            token_dir = root / "_internal" / "gui" / "tokens"
            token_dir.mkdir(parents=True)
            (token_dir / "UC123.json").write_text('{"refresh_token":"secret"}', encoding="utf-8")

            violations = check_release_safety.scan(root)

            self.assertTrue(any(path.name == "tokens" or path.name == "UC123.json" for path in violations))

    def test_release_safety_scans_private_dirs_anywhere_in_internal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            token_dir = root / "_internal" / "tokens"
            token_dir.mkdir(parents=True)
            (token_dir / "UC123.json").write_text('{"refresh_token":"secret"}', encoding="utf-8")

            violations = check_release_safety.scan(root)

            self.assertTrue(any(path.name == "tokens" or path.name == "UC123.json" for path in violations))

    def test_release_safety_scans_nested_private_dirs_in_internal_packages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            token_dir = root / "_internal" / "somepkg" / "tokens"
            token_dir.mkdir(parents=True)
            (token_dir / "UCKwDooGvMCQNdsRP64svXOQ.json").write_text("{}", encoding="utf-8")

            violations = check_release_safety.scan(root)

            self.assertTrue(any(path.name == "tokens" or path.name.endswith(".json") for path in violations))

    def test_release_safety_blocks_carryover_backups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            backup_dir = root / "carryover_backups"
            backup_dir.mkdir(parents=True)
            backup = backup_dir / "voice_profile.json"
            backup.write_text('{"sample_count":1}', encoding="utf-8")

            violations = check_release_safety.scan(root)

            self.assertTrue(any(path.name == "carryover_backups" or path == backup for path in violations))

    def test_release_safety_scans_private_content_in_internal_packages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "_internal" / "somepkg" / "data"
            data_dir.mkdir(parents=True)
            secret = data_dir / "session.json"
            secret.write_text('{"refresh_token":"1//real-looking-refresh-token"}', encoding="utf-8")

            violations = check_release_safety.scan(root)

            self.assertIn(secret, violations)

    def test_release_safety_allows_public_schema_json_that_mentions_refresh_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            data_dir = root / "_internal" / "googleapiclient" / "discovery_cache" / "documents"
            data_dir.mkdir(parents=True)
            schema = data_dir / "identitytoolkit.v2.json"
            schema.write_text(
                '{"properties":{"refresh_token":{"type":"string","description":"OAuth field"}}}',
                encoding="utf-8",
            )

            violations = check_release_safety.scan(root)

            self.assertEqual(violations, [])

    def test_release_safety_allows_placeholder_client_secret_example(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            example = root / "client_secrets.example.json"
            example.write_text(
                '{"installed":{"client_secret":"your-client-secret","api_key":"your-api-key"}}',
                encoding="utf-8",
            )

            violations = check_release_safety.scan(root)

            self.assertEqual(violations, [])

    def test_release_safety_blocks_real_secret_in_example_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            example = root / "client_secrets.example.json"
            example.write_text(
                '{"installed":{"client_secret":"GOCSPX-real-looking-client-secret"}}',
                encoding="utf-8",
            )

            violations = check_release_safety.scan(root)

            self.assertIn(example, violations)

    def test_release_safety_blocks_access_tokens_and_api_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secrets = root / "settings.json"
            secrets.write_text(
                '{"access_token":"ya29.real-looking-token","gemini_api_key":"AIzaSyRealLookingKey"}',
                encoding="utf-8",
            )

            violations = check_release_safety.scan(root)

            self.assertIn(secrets, violations)

    def test_release_safety_blocks_camel_case_and_generic_token_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            secrets = root / "settings.json"
            secrets.write_text(
                '{"refreshToken":"1//real-looking-refresh-token","token":"ya29.real-looking-access-token"}',
                encoding="utf-8",
            )

            violations = check_release_safety.scan(root)

            self.assertIn(secrets, violations)

    def test_release_safety_blocks_env_style_secret_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env = root / "settings.txt"
            env.write_text(
                "api_key=AIzaSyRealLookingKey\nrefresh_token=1//real-looking-refresh-token\n",
                encoding="utf-8",
            )

            violations = check_release_safety.scan(root)

            self.assertIn(env, violations)

    def test_release_safety_scans_zip_contents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "ViriaRevive-v9.9.9-Windows-x64.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("tokens/account.json", '{"refresh_token":"1//real-looking-refresh-token"}')

            violations = check_release_safety.scan(root)

            self.assertTrue(any("tokens" in str(path) and "account.json" in str(path) for path in violations))

    def test_release_safety_scans_zip_env_style_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "ViriaRevive-v9.9.9-Windows-x64.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("settings.txt", "api_key=AIzaSyRealLookingKey\n")

            violations = check_release_safety.scan(root)

            self.assertTrue(any("settings.txt" in str(path) for path in violations))

    def test_release_safety_allows_google_discovery_schema_inside_zip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "ViriaRevive-v9.9.9-Windows-x64.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(
                    "_internal/googleapiclient/discovery_cache/documents/example.v1.json",
                    '{"properties":{"access_token":{"type":"string"}}}',
                )

            violations = check_release_safety.scan(root)

            self.assertEqual(violations, [])

    def test_release_safety_blocks_voice_profile_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile = root / "voice_profile.json"
            profile.write_text('{"centroid":[0.1],"enabled":true}', encoding="utf-8")

            violations = check_release_safety.scan(root)

            self.assertIn(profile, violations)

    def test_release_safety_blocks_processing_history_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            history = root / "processing_history.json"
            history.write_text('{"runs":[{"elapsed_seconds":12.5}]}', encoding="utf-8")

            violations = check_release_safety.scan(root)

            self.assertIn(history, violations)

    def test_release_safety_blocks_analysis_cache_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache = root / "analysis_cache"
            cache.mkdir()
            (cache / "scene_abc.json").write_text('{"timestamps":[1,2,3]}', encoding="utf-8")

            violations = check_release_safety.scan(root)

            self.assertTrue(any(path.name == "analysis_cache" for path in violations))

    def test_release_safety_allows_third_party_internal_subtitles_package(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subtitles_dir = root / "_internal" / "av" / "subtitles"
            subtitles_dir.mkdir(parents=True)
            (subtitles_dir / "codeccontext.pyd").write_bytes(b"binary")

            violations = check_release_safety.scan(root)

            self.assertEqual(violations, [])

    def test_hash_writer_rejects_diverged_latest_zip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            release = root / "release"
            release.mkdir()
            (release / "ViriaRevive-v2.0.0-Windows-x64.zip").write_bytes(b"versioned")
            (release / "ViriaRevive-Windows-x64.zip").write_bytes(b"latest")

            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                with redirect_stdout(StringIO()):
                    code = write_release_hashes.main(["write_release_hashes.py", "2.0.0"])
            finally:
                os.chdir(old_cwd)

            self.assertEqual(code, 1)
            self.assertFalse((release / "ViriaRevive-Windows-x64.zip.sha256").exists())


if __name__ == "__main__":
    unittest.main()
