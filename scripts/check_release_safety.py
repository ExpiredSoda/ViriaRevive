"""Fail a release build if private runtime data is included."""

from __future__ import annotations

import fnmatch
import sys
from pathlib import Path


PRIVATE_FILE_PATTERNS = (
    ".env",
    ".env.*",
    "*.env",
    "*.env.*",
    "client_secret*.json",
    "client_secrets.json",
    "credentials*.json",
    "token.json",
    "*.token.json",
    "*.pickle",
    "*.pkl",
    "viria_state.json",
    "viria_state.*.bak",
    "viria_state.json.*.tmp",
    "personalization.json",
    "personalization.*.bak",
    "personalization.json.*.tmp",
    "personalization_export*.json",
    "*_candidate_debug.json",
    "*_run_debug.json",
)

ALLOWED_TEMPLATE_FILES = {
    "client_secrets.example.json",
}

PRIVATE_CONTENT_MARKERS = (
    '"refresh_token"',
    '"private_key"',
)

PRIVATE_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "clips",
    "downloads",
    "music",
    "subtitles",
    "tokens",
}


def _matches_private_file(path: Path) -> bool:
    name = path.name.lower()
    if name in ALLOWED_TEMPLATE_FILES:
        return False
    return any(fnmatch.fnmatch(name, pattern.lower()) for pattern in PRIVATE_FILE_PATTERNS)


def _looks_like_private_content(path: Path) -> bool:
    if path.suffix.lower() not in {".json", ".env", ".txt", ".ini", ".cfg"}:
        return False
    if path.name.lower() in ALLOWED_TEMPLATE_FILES:
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:200_000].lower()
    except OSError:
        return False
    return any(marker in text for marker in PRIVATE_CONTENT_MARKERS)


def _private_dir_violation(rel_parts: list[str]) -> bool:
    if "__pycache__" in rel_parts:
        return True
    if not rel_parts:
        return False
    top_level = rel_parts[0]
    if top_level in PRIVATE_DIR_NAMES:
        return True
    if top_level == "_internal":
        app_owned_internal = len(rel_parts) >= 2 and rel_parts[1] in {"gui", "bin"}
        if len(rel_parts) >= 2 and rel_parts[1] in PRIVATE_DIR_NAMES:
            return True
        return app_owned_internal and any(part in PRIVATE_DIR_NAMES for part in rel_parts[2:])
    return any(part in PRIVATE_DIR_NAMES for part in rel_parts)


def scan(root: Path) -> list[Path]:
    violations: list[Path] = []
    for path in root.rglob("*"):
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = path
        rel_parts = [part.lower() for part in rel.parts]
        top_level = rel_parts[0] if rel_parts else ""
        app_owned_internal = len(rel_parts) >= 2 and rel_parts[0] == "_internal" and rel_parts[1] in {"gui", "bin"}
        if _private_dir_violation(rel_parts):
            violations.append(path)
            continue
        if path.is_file():
            scan_private_content = top_level != "_internal" or app_owned_internal
            if _matches_private_file(path) or (scan_private_content and _looks_like_private_content(path)):
                violations.append(path)
    return violations


def main(argv: list[str]) -> int:
    require_exists = "--require-exists" in argv
    paths = [arg for arg in argv[1:] if arg != "--require-exists"]
    if len(paths) != 1:
        print("Usage: python scripts/check_release_safety.py [--require-exists] <release-folder>")
        return 2

    root = Path(paths[0]).resolve()
    if not root.exists():
        message = f"[release-safety] Folder does not exist: {root}"
        if require_exists:
            print(message)
            return 1
        print(f"{message}; skipping")
        return 0

    violations = scan(root)
    if violations:
        print("[release-safety] Private runtime data found in release output:")
        for path in violations[:50]:
            print(f"  - {path}")
        if len(violations) > 50:
            print(f"  ... and {len(violations) - 50} more")
        return 1

    print(f"[release-safety] OK: no private runtime data found in {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
