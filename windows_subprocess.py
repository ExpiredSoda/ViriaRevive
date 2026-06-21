"""Windows process defaults for GUI launches.

When ViriaRevive runs through pythonw, child console programs such as ffmpeg,
ffprobe, or tools spawned by yt-dlp can otherwise open a blank console window.
This module installs a small process-wide wrapper so those helper commands stay
hidden while the GUI remains visible.
"""

from __future__ import annotations

import subprocess
import sys


def hide_child_console_windows() -> None:
    """Hide console windows for subprocesses spawned by this Python process."""
    if sys.platform != "win32" or getattr(subprocess, "_viria_hidden_windows", False):
        return

    original_popen = subprocess.Popen

    class HiddenPopen(original_popen):
        def __init__(self, *args, **kwargs):
            flags = kwargs.get("creationflags", 0) or 0
            if flags & getattr(subprocess, "CREATE_NEW_CONSOLE", 0):
                super().__init__(*args, **kwargs)
                return

            startupinfo = kwargs.get("startupinfo")
            if startupinfo is None:
                startupinfo = subprocess.STARTUPINFO()
                kwargs["startupinfo"] = startupinfo

            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE

            kwargs["creationflags"] = flags | getattr(subprocess, "CREATE_NO_WINDOW", 0)

            super().__init__(*args, **kwargs)

    subprocess.Popen = HiddenPopen
    subprocess._viria_hidden_windows = True
