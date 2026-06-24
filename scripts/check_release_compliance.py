"""Release compliance checks for bundled third-party tools and notices."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_NOTICE_MARKERS = (
    "Ultralytics",
    "AGPL",
    "PyInstaller",
    "pystray",
    "FFmpeg",
    "PyAV",
    "OpenCV",
)
REQUIRED_FFMPEG_PROVENANCE = (
    "provider",
    "variant",
    "license",
    "download_url",
    "source_url",
    "ffmpeg_source_url",
    "sha256_ffmpeg",
    "sha256_ffprobe",
)
NATIVE_MEDIA_PATTERNS = (
    "avcodec*.dll",
    "avformat*.dll",
    "avutil*.dll",
    "swresample*.dll",
    "swscale*.dll",
    "libx264*.dll",
    "libx265*.dll",
    "opencv_videoio_ffmpeg*.dll",
    "libfdk*.dll",
    "*nonfree*.dll",
    "ffmpeg.exe",
    "ffprobe.exe",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _run(cmd: list[str], timeout: int = 15) -> tuple[int | None, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    return proc.returncode, (proc.stdout or "") + "\n" + (proc.stderr or "")


def _load_json(path: Path, errors: list[str]) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"Missing required file: {path}")
    except json.JSONDecodeError as exc:
        errors.append(f"Invalid JSON in {path}: {exc}")
    return {}


def _check_notices(root: Path, errors: list[str]) -> None:
    notices = root / "THIRD_PARTY_NOTICES.md"
    manifest = root / "BUILD-MANIFEST.json"
    license_file = root / "LICENSE"
    readme = root / "README.md"
    for path in (notices, manifest, license_file, readme):
        if not path.exists():
            errors.append(f"Missing release file: {path}")
    if notices.exists():
        text = notices.read_text(encoding="utf-8", errors="ignore")
        for marker in REQUIRED_NOTICE_MARKERS:
            if marker.lower() not in text.lower():
                errors.append(f"{notices} does not mention required notice marker: {marker}")
    data = _load_json(manifest, errors) if manifest.exists() else {}
    if data:
        packages = data.get("python_packages") or []
        names = {str(pkg.get("name", "")).lower() for pkg in packages}
        for required in ("ultralytics", "pyinstaller", "pystray"):
            if required not in names:
                errors.append(f"BUILD-MANIFEST.json is missing package inventory for {required}")
        if "ultralytics-thop" in names:
            if notices.exists():
                text = notices.read_text(encoding="utf-8", errors="ignore").lower()
                if "ultralytics-thop" not in text or "agpl" not in text:
                    errors.append(f"{notices} must mention ultralytics-thop AGPL obligations")
        if _scan_native_media_libraries(root) and not data.get("native_media_libraries"):
            errors.append("BUILD-MANIFEST.json is missing native_media_libraries for bundled media DLLs")


def _check_ffmpeg(root: Path, errors: list[str]) -> None:
    bin_dir = root / "bin"
    ffmpeg = bin_dir / "ffmpeg.exe"
    ffprobe = bin_dir / "ffprobe.exe"
    present = ffmpeg.exists() or ffprobe.exists()
    if not present:
        print("[release-compliance] No bundled FFmpeg binaries found; system FFmpeg remains user-provided.")
        return
    if not ffmpeg.exists() or not ffprobe.exists():
        errors.append("Bundled FFmpeg must include both bin/ffmpeg.exe and bin/ffprobe.exe")
        return
    notices = root / "THIRD_PARTY_NOTICES.md"
    if notices.exists():
        notice_text = notices.read_text(encoding="utf-8", errors="ignore").lower()
        for marker in ("gpl", "source"):
            if marker not in notice_text:
                errors.append(f"{notices} must mention FFmpeg {marker} obligations")

    provenance_path = bin_dir / "FFMPEG_BUILD.json"
    provenance = _load_json(provenance_path, errors)
    for key in REQUIRED_FFMPEG_PROVENANCE:
        if not provenance.get(key):
            errors.append(f"{provenance_path} missing required field: {key}")

    if provenance:
        variant = str(provenance.get("variant", "")).lower()
        license_name = str(provenance.get("license", "")).lower()
        if "nonfree" in variant or "nonfree" in license_name:
            errors.append("Bundled FFmpeg provenance must not be nonfree.")
        if "gpl" not in license_name and "lgpl" not in license_name:
            errors.append("Bundled FFmpeg provenance must explicitly identify a GPL or LGPL license posture.")
        for label, path in (("sha256_ffmpeg", ffmpeg), ("sha256_ffprobe", ffprobe)):
            expected = str(provenance.get(label, "")).lower().strip()
            actual = _sha256(path)
            if expected and expected != actual:
                errors.append(f"{path.name} SHA256 mismatch: expected {expected}, actual {actual}")

    returncode, version_output = _run([str(ffmpeg), "-hide_banner", "-version"])
    if returncode != 0:
        errors.append(f"Could not run bundled ffmpeg.exe -version: {version_output.strip()}")
        return
    lowered = version_output.lower()
    if "--enable-nonfree" in lowered:
        errors.append("Bundled FFmpeg was built with --enable-nonfree; do not distribute it.")

    returncode, encoders = _run([str(ffmpeg), "-hide_banner", "-encoders"])
    if returncode != 0:
        errors.append(f"Could not inspect bundled FFmpeg encoders: {encoders.strip()}")
    elif "libx264" not in encoders:
        errors.append("Bundled FFmpeg does not expose libx264, but ViriaRevive render paths require it.")


def _scan_native_media_libraries(root: Path) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for pattern in NATIVE_MEDIA_PATTERNS:
        for path in root.rglob(pattern):
            if path not in seen and path.is_file():
                seen.add(path)
                found.append(path)
    return sorted(found, key=lambda path: str(path).lower())


def _check_native_media_libraries(root: Path, errors: list[str]) -> None:
    for path in _scan_native_media_libraries(root):
        rel = path.relative_to(root)
        lower = path.name.lower()
        if "libfdk" in lower or "nonfree" in lower:
            errors.append(f"Nonfree-looking media library must not be bundled: {rel}")
        parts = tuple(part.lower() for part in rel.parts)
        if len(parts) >= 3 and parts[0] == "_internal" and parts[-2:] in {
            ("bin", "ffmpeg.exe"),
            ("bin", "ffprobe.exe"),
        }:
            errors.append(f"Do not bundle hidden FFmpeg binaries under _internal: {rel}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_folder", nargs="?", default="dist/ViriaRevive")
    parser.add_argument("--require-exists", action="store_true")
    args = parser.parse_args(argv)

    root = (ROOT / args.release_folder).resolve()
    if not root.exists():
        message = f"[release-compliance] Folder does not exist: {root}"
        if args.require_exists:
            print(message)
            return 1
        print(f"{message}; skipping")
        return 0

    errors: list[str] = []
    _check_notices(root, errors)
    _check_ffmpeg(root, errors)
    _check_native_media_libraries(root, errors)
    if errors:
        print("[release-compliance] Release compliance checks failed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"[release-compliance] OK: notices, manifest, and bundled-tool checks passed for {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
