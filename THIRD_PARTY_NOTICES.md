# Third-Party Notices

ViriaRevive is open-source software. This source-level notice summarizes the
major third-party components used by the app. Release builds generate a fuller
`THIRD_PARTY_NOTICES.md`, `BUILD-MANIFEST.json`, and copied package license files
from the build environment.

This file is informational and is not legal advice. Release owners should review
the licenses for the exact dependencies and binaries they publish.

## FFmpeg

ViriaRevive uses FFmpeg/ffprobe for media probing, clipping, audio extraction,
mixing, subtitles, scene detection, and rendering. Public packages may include
standalone `ffmpeg.exe` and `ffprobe.exe` in the app-local `bin/` folder.

When bundling FFmpeg:

- Use a redistributable build from a trusted source linked by FFmpeg's download
  page or from a reviewed internal build.
- Do not distribute a build configured with `--enable-nonfree`.
- Keep `bin/FFMPEG_BUILD.json` beside the binaries with provider, variant,
  build source URL, FFmpeg source URL, download URL, license, and SHA256 hashes.
- Keep FFmpeg as a separate executable; ViriaRevive does not link FFmpeg
  libraries into the Python application.
- Include the applicable FFmpeg GPL/LGPL license terms and corresponding source
  information for the exact binary build in the release. For GPL FFmpeg builds,
  publish or link to complete corresponding source in the manner required by
  that build's license terms.

The current renderer uses `libx264`, so bundled FFmpeg builds must expose a
compatible `libx264` encoder unless the renderer is changed.

Frozen builds can also include FFmpeg-related native libraries through Python
wheels such as PyAV and OpenCV, for example `avcodec*.dll`, `libx264*.dll`,
`libx265*.dll`, or `opencv_videoio_ffmpeg*.dll`. Release builds generate
`BUILD-MANIFEST.json` entries for those files with relative paths and hashes so
they are visible during release review.

## Ultralytics YOLO

ViriaRevive uses Ultralytics YOLO for person-aware crop detection. Ultralytics
YOLO is AGPL-3.0 under the public open-source license path. Public ViriaRevive
releases must keep complete corresponding source available, or the YOLO feature
must be replaced, disabled, or covered by a separate commercial license.

Ultralytics depends on `ultralytics-thop` for model profiling helpers.
`ultralytics-thop` is AGPL-3.0, so public releases that include it must keep
complete corresponding source available with the rest of the AGPL-covered
dependency source.

## Optional Model Downloads

First use can download Faster-Whisper model files, YOLO model files, and optional
Ollama model files when the user opts into local AI titles/labels. Model weights
have their own license terms. Public releases should not bundle model weights
unless each model has explicit source/license/provenance metadata.

## PyInstaller

ViriaRevive uses PyInstaller to build Windows one-folder releases. PyInstaller
is GPL with a bootloader exception that permits distributing applications built
with it, subject to complying with all dependency licenses.

## Other Runtime Dependencies

Important runtime dependencies include yt-dlp, faster-whisper, CTranslate2,
PySceneDetect, OpenCV, Torch/TorchVision, pywebview, pystray, NumPy, pydub, and
Google API/OAuth client libraries. Release builds generate a package inventory
and copy license files exposed by package metadata into `licenses/`.
