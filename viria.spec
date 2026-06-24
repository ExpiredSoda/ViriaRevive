# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)


ROOT = Path(SPECPATH).resolve() if "SPECPATH" in globals() else Path.cwd().resolve()
_version_ns = {}
exec((ROOT / "version.py").read_text(encoding="utf-8"), _version_ns)
APP_NAME = _version_ns["APP_NAME"]
APP_VERSION = _version_ns["APP_VERSION"]
APP_VERSION_TUPLE = _version_ns["APP_VERSION_TUPLE"]
APP_COMPANY = _version_ns["APP_COMPANY"]
APP_DESCRIPTION = _version_ns["APP_DESCRIPTION"]

VERSION_INFO = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=APP_VERSION_TUPLE,
        prodvers=APP_VERSION_TUPLE,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo([
            StringTable(
                "040904B0",
                [
                    StringStruct("CompanyName", APP_COMPANY),
                    StringStruct("FileDescription", APP_DESCRIPTION),
                    StringStruct("FileVersion", APP_VERSION),
                    StringStruct("InternalName", APP_NAME),
                    StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
                    StringStruct("ProductName", APP_NAME),
                    StringStruct("ProductVersion", APP_VERSION),
                ],
            )
        ]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

DATA_FILES = []

GUI_DIR = ROOT / "gui"
for name in ("index.html", "app.js", "style.css"):
    DATA_FILES.append((str(GUI_DIR / name), "gui"))

HIDDEN_IMPORTS = [
    "version",
    "audio_streams",
    "candidate_ranker",
    "speech_stream_selector",
    "windows_subprocess",
    "googleapiclient.discovery",
    "google_auth_oauthlib.flow",
    "google.oauth2.credentials",
    "google.auth.transport.requests",
    "faster_whisper",
    "scenedetect",
    "scenedetect.detectors",
    "cv2",
    "numpy",
    "ultralytics",
]

a = Analysis(
    ["app.pyw"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=DATA_FILES,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=VERSION_INFO,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=APP_NAME,
)
