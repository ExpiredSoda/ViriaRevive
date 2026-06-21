"""Check that release-facing version metadata uses version.py."""

from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from version import APP_VERSION, APP_VERSION_DISPLAY  # noqa: E402


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _stale_versions(rel: str) -> list[str]:
    """Return semver-looking strings in release-facing files that drifted."""
    text = _read(rel)
    stale = []
    for match in re.finditer(r"(?<![\w.])v\d+\.\d+\.\d+(?![\d.])", text):
        raw = match.group(0)
        normalized = raw[1:]
        if normalized != APP_VERSION:
            stale.append(f"{rel}:{match.start()}: {raw}")
    return stale


def main() -> int:
    checks = [
        ("README.md", APP_VERSION_DISPLAY in _read("README.md")),
        ("gui/index.html", APP_VERSION_DISPLAY in _read("gui/index.html")),
        ("installer/ViriaRevive.iss", "MyAppVersion \"2.0.0\"" not in _read("installer/ViriaRevive.iss")),
        ("installer/ViriaRevive.iss", "VersionInfoVersion={#MyAppVersionQuad}" in _read("installer/ViriaRevive.iss")),
        ("viria.spec", "VERSION_INFO" in _read("viria.spec")),
        ("build.bat", "from version import APP_VERSION" in _read("build.bat")),
        ("build_installer.bat", "from version import APP_VERSION" in _read("build_installer.bat")),
    ]
    failed = [name for name, ok in checks if not ok]
    stale = _stale_versions("README.md") + _stale_versions("gui/index.html")
    if failed or stale:
        print(f"[version-sync] APP_VERSION is {APP_VERSION} ({APP_VERSION_DISPLAY})")
        if failed:
            print("[version-sync] Version metadata drift detected:")
            for name in failed:
                print(f"  - {name}")
        if stale:
            print("[version-sync] Stale release-facing version strings:")
            for item in stale:
                print(f"  - {item}")
        return 1
    print(f"[version-sync] OK: {APP_VERSION_DISPLAY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
