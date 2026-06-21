import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import windows_subprocess  # noqa: E402


class WindowsSubprocessTests(unittest.TestCase):
    def test_create_new_console_launches_are_not_hidden(self):
        class FakeStartupInfo:
            def __init__(self):
                self.dwFlags = 0
                self.wShowWindow = None

        class FakePopen:
            calls = []

            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
                FakePopen.calls.append(kwargs)

        create_new_console = 0x10
        create_no_window = 0x08000000
        had_marker = hasattr(subprocess, "_viria_hidden_windows")
        old_marker = getattr(subprocess, "_viria_hidden_windows", None)
        if had_marker:
            delattr(subprocess, "_viria_hidden_windows")
        try:
            with patch.object(windows_subprocess.sys, "platform", "win32"):
                with patch.object(windows_subprocess.subprocess, "Popen", FakePopen):
                    with patch.object(windows_subprocess.subprocess, "STARTUPINFO", FakeStartupInfo, create=True):
                        with patch.object(windows_subprocess.subprocess, "STARTF_USESHOWWINDOW", 1, create=True):
                            with patch.object(windows_subprocess.subprocess, "CREATE_NEW_CONSOLE", create_new_console, create=True):
                                with patch.object(windows_subprocess.subprocess, "CREATE_NO_WINDOW", create_no_window, create=True):
                                    windows_subprocess.hide_child_console_windows()
                                    hidden_popen = windows_subprocess.subprocess.Popen

                                    hidden_popen(["powershell.exe"], creationflags=create_new_console)
                                    visible_kwargs = FakePopen.calls[-1]
                                    self.assertEqual(visible_kwargs["creationflags"], create_new_console)
                                    self.assertNotIn("startupinfo", visible_kwargs)

                                    hidden_popen(["ffmpeg.exe"])
                                    hidden_kwargs = FakePopen.calls[-1]
                                    self.assertEqual(hidden_kwargs["startupinfo"].wShowWindow, 0)
                                    self.assertTrue(hidden_kwargs["creationflags"] & create_no_window)
        finally:
            if hasattr(subprocess, "_viria_hidden_windows"):
                delattr(subprocess, "_viria_hidden_windows")
            if had_marker:
                subprocess._viria_hidden_windows = old_marker


if __name__ == "__main__":
    unittest.main()
