import os
import sys
import tempfile
import unittest
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
