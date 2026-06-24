"""Generate release provenance and third-party notice files."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IMPORTANT_PACKAGES = {
    "ultralytics",
    "ultralytics-thop",
    "pystray",
    "pyinstaller",
    "yt-dlp",
    "opencv-python-headless",
    "faster-whisper",
    "scenedetect-headless",
    "torch",
    "torchvision",
    "ctranslate2",
    "av",
    "pywebview",
}
NATIVE_MEDIA_PATTERNS = (
    "avcodec*.dll",
    "avformat*.dll",
    "avutil*.dll",
    "swresample*.dll",
    "swscale*.dll",
    "libx264*.dll",
    "libx265*.dll",
    "opencv_videoio_ffmpeg*.dll",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _run(cmd: list[str], timeout: int = 15) -> dict[str, object]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def _git_commit() -> str | None:
    result = _run(["git", "rev-parse", "--short=12", "HEAD"], timeout=5)
    if result.get("ok"):
        return str(result.get("stdout") or "").strip() or None
    return None


def _git_dirty() -> bool | None:
    result = _run(["git", "status", "--porcelain"], timeout=5)
    if result.get("ok"):
        return bool(str(result.get("stdout") or "").strip())
    return None


def _app_version() -> str:
    namespace: dict[str, object] = {}
    version_file = ROOT / "version.py"
    if version_file.exists():
        exec(version_file.read_text(encoding="utf-8"), namespace)
    return str(namespace.get("APP_VERSION") or "unknown")


def _license_text(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression.strip()
    license_field = meta.get("License")
    if license_field:
        first_line = license_field.strip().splitlines()[0]
        return first_line[:140]
    classifiers = meta.get_all("Classifier") or []
    licenses = [item for item in classifiers if "License ::" in item]
    return "; ".join(licenses[:3]) if licenses else "See package metadata/license files"


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value).strip("._") or "package"


def _copy_license_files(dist: metadata.Distribution, output_dir: Path) -> list[str]:
    copied: list[str] = []
    files = list(dist.files or [])
    package_dir = output_dir / "licenses" / "python-packages" / _safe_name(dist.metadata["Name"])
    for file in files:
        lowered = str(file).lower()
        if not any(marker in lowered for marker in ("license", "copying", "notice")):
            continue
        if lowered.endswith((".pyc", ".pyo", ".pyd", ".dll", ".exe")):
            continue
        source = Path(dist.locate_file(file))
        if not source.is_file():
            continue
        try:
            if source.stat().st_size > 2_000_000:
                continue
        except OSError:
            continue
        target = package_dir / Path(str(file)).name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(str(target.relative_to(output_dir)).replace("\\", "/"))
    return sorted(set(copied))


def _installed_packages(output_dir: Path) -> list[dict[str, object]]:
    packages: list[dict[str, object]] = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        packages.append(
            {
                "name": name,
                "version": dist.version,
                "license": _license_text(dist),
                "important": name.lower().replace("_", "-") in IMPORTANT_PACKAGES,
                "license_files": _copy_license_files(dist, output_dir),
            }
        )
    return packages


def _ffmpeg_info(output_dir: Path) -> dict[str, object]:
    bin_dir = output_dir / "bin"
    ffmpeg = bin_dir / "ffmpeg.exe"
    ffprobe = bin_dir / "ffprobe.exe"
    build_json = bin_dir / "FFMPEG_BUILD.json"
    present = ffmpeg.exists() or ffprobe.exists()
    info: dict[str, object] = {
        "bundled": present,
        "ffmpeg_path": "bin/ffmpeg.exe" if ffmpeg.exists() else None,
        "ffprobe_path": "bin/ffprobe.exe" if ffprobe.exists() else None,
    }
    if not present:
        return info

    info["ffmpeg_sha256"] = _sha256(ffmpeg) if ffmpeg.exists() else None
    info["ffprobe_sha256"] = _sha256(ffprobe) if ffprobe.exists() else None
    if build_json.exists():
        try:
            info["provenance"] = json.loads(build_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            info["provenance_error"] = str(exc)

    version = _run([str(ffmpeg), "-hide_banner", "-version"]) if ffmpeg.exists() else {"ok": False}
    buildconf = _run([str(ffmpeg), "-hide_banner", "-buildconf"]) if ffmpeg.exists() else {"ok": False}
    encoders = _run([str(ffmpeg), "-hide_banner", "-encoders"]) if ffmpeg.exists() else {"ok": False}
    info["version_output"] = version.get("stdout") or version.get("stderr") or version.get("error")
    info["buildconf_output"] = buildconf.get("stdout") or buildconf.get("stderr") or buildconf.get("error")
    info["has_libx264_encoder"] = "libx264" in str(encoders.get("stdout") or "")
    return info


def _native_media_libraries(output_dir: Path) -> list[dict[str, object]]:
    libraries: list[dict[str, object]] = []
    seen: set[Path] = set()
    for pattern in NATIVE_MEDIA_PATTERNS:
        for path in output_dir.rglob(pattern):
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            rel = path.relative_to(output_dir)
            lower = path.name.lower()
            likely_source = "PyAV wheel"
            if "opencv_videoio_ffmpeg" in lower:
                likely_source = "opencv-python-headless wheel"
            libraries.append(
                {
                    "path": str(rel).replace("\\", "/"),
                    "name": path.name,
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                    "likely_source": likely_source,
                    "license_note": "Review wheel/package license files and upstream FFmpeg component licenses.",
                }
            )
    return sorted(libraries, key=lambda item: str(item["path"]).lower())


def _write_notices(output_dir: Path, manifest: dict[str, object]) -> None:
    packages = manifest["python_packages"]
    important = [pkg for pkg in packages if pkg.get("important")]
    ffmpeg = manifest["ffmpeg"]
    native_media = manifest.get("native_media_libraries") or []
    lines = [
        "# Third-Party Notices",
        "",
        "ViriaRevive is open-source software. This file is generated for release builds",
        "to summarize bundled tools and Python dependencies. The app's own source is",
        "licensed under the project license in LICENSE.",
        "",
        "This notice is informational and is not legal advice. The release owner should",
        "review dependency licenses before publishing public binaries.",
        "",
        "## Bundled FFmpeg",
        "",
    ]
    if ffmpeg.get("bundled"):
        provenance = ffmpeg.get("provenance") or {}
        lines.extend(
            [
                "This release includes standalone `ffmpeg.exe` and `ffprobe.exe` binaries.",
                "They are invoked as separate executables and are not linked into ViriaRevive.",
                "",
                f"- Provider: {provenance.get('provider', 'See bin/FFMPEG_BUILD.json')}",
                f"- Variant: {provenance.get('variant', 'See bin/FFMPEG_BUILD.json')}",
                f"- License: {provenance.get('license', 'See FFmpeg build configuration')}",
                f"- Download URL: {provenance.get('download_url', 'See bin/FFMPEG_BUILD.json')}",
                f"- Build source URL: {provenance.get('source_url', 'See bin/FFMPEG_BUILD.json')}",
                f"- FFmpeg source URL: {provenance.get('ffmpeg_source_url', 'See bin/FFMPEG_BUILD.json')}",
                f"- ffmpeg.exe SHA256: {ffmpeg.get('ffmpeg_sha256')}",
                f"- ffprobe.exe SHA256: {ffmpeg.get('ffprobe_sha256')}",
                "",
                "Do not distribute an FFmpeg build that was configured with `--enable-nonfree`.",
                "Release packages must include the applicable FFmpeg GPL/LGPL license terms",
                "and clear corresponding-source information for the exact FFmpeg build.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "No FFmpeg binaries are bundled in this build. Users must provide FFmpeg",
                "through PATH or the app-local `bin/` folder. The frozen Python package",
                "may still include FFmpeg-related DLLs through media wheels; see below.",
                "",
            ]
        )

    lines.extend(["## FFmpeg-Related Wheel Libraries", ""])
    if native_media:
        lines.extend(
            [
                "This release also contains native media libraries from Python wheels such",
                "as PyAV and OpenCV. These can include FFmpeg component DLLs and codec",
                "libraries. They are listed in `BUILD-MANIFEST.json` with paths and hashes.",
                "",
                "| File | Likely source | SHA256 |",
                "| --- | --- | --- |",
            ]
        )
        for item in native_media:
            lines.append(f"| {item['path']} | {item['likely_source']} | {item['sha256']} |")
        lines.append("")
    else:
        lines.extend(
            [
                "No FFmpeg-related wheel DLLs were detected in this release output.",
                "",
            ]
        )

    lines.extend(
        [
            "## Important Python/Model Dependencies",
            "",
            "- Ultralytics YOLO is AGPL-3.0 under the public open-source license path.",
            "  Public ViriaRevive releases must keep complete corresponding source available",
            "  or replace/license that feature differently.",
            "- ultralytics-thop is distributed under AGPL-3.0; public ViriaRevive",
            "  releases that include it must keep complete corresponding source available",
            "  with the rest of the AGPL-covered dependency source.",
            "- PyInstaller is GPL with a bootloader exception that permits distributing",
            "  applications built with it, subject to dependency license compliance.",
            "- pystray is LGPLv3; keep its notice/license available in binary releases.",
            "- OpenCV, Torch, faster-whisper, yt-dlp, PySceneDetect, and Google client",
            "  libraries retain their own license terms listed below and in copied license files.",
            "- PyAV and OpenCV wheels can carry native media libraries; review the files",
            "  listed in `BUILD-MANIFEST.json` before publishing binaries.",
            "",
            "## Package Summary",
            "",
            "| Package | Version | License |",
            "| --- | --- | --- |",
        ]
    )
    for package in important:
        lines.append(f"| {package['name']} | {package['version']} | {package['license']} |")
    lines.extend(
        [
            "",
            "A complete package inventory is available in `BUILD-MANIFEST.json`.",
            "Copied license texts are under `licenses/python-packages/` when package metadata exposes them.",
            "",
        ]
    )
    (output_dir / "THIRD_PARTY_NOTICES.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="dist/ViriaRevive")
    args = parser.parse_args(argv)

    output_dir = (ROOT / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "app": {
            "name": "ViriaRevive",
            "version": _app_version(),
            "git_commit": _git_commit(),
            "git_dirty": _git_dirty(),
            "open_source_posture": "Public releases are intended to remain open source.",
            "license_file": "LICENSE",
        },
        "ffmpeg": _ffmpeg_info(output_dir),
        "native_media_libraries": _native_media_libraries(output_dir),
        "python": {
            "executable_name": Path(sys.executable).name,
            "version": sys.version,
        },
        "python_packages": _installed_packages(output_dir),
    }
    (output_dir / "BUILD-MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _write_notices(output_dir, manifest)
    print(f"[release-manifest] Wrote {output_dir / 'BUILD-MANIFEST.json'}")
    print(f"[release-manifest] Wrote {output_dir / 'THIRD_PARTY_NOTICES.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
