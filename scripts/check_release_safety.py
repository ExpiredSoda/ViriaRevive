"""Fail a release build if private runtime data is included."""

from __future__ import annotations

import fnmatch
import json
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath


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
    "voice_profile.json",
    "voice_profile.*.bak",
    "voice_profile.json.*.tmp",
    "processing_history.json",
    "processing_history.*.bak",
    "processing_history.json.*.tmp",
    "game_context.sqlite3",
    "game_context.sqlite3-*",
    "*_candidate_debug.json",
    "*_run_debug.json",
)

ALLOWED_TEMPLATE_FILES = {
    "client_secrets.example.json",
}

PRIVATE_CONTENT_MARKERS = (
    '"refresh_token"',
    '"private_key"',
    '"client_secret"',
    '"access_token"',
    '"api_key"',
    "gemini_api_key",
)

SECRET_VALUE_KEYS = {
    "refresh_token",
    "refreshtoken",
    "private_key",
    "privatekey",
    "client_secret",
    "clientsecret",
    "access_token",
    "accesstoken",
    "api_key",
    "apikey",
    "gemini_api_key",
    "geminiapikey",
    "token",
}

PLACEHOLDER_SECRET_VALUES = {
    "",
    "changeme",
    "change-me",
    "example",
    "placeholder",
    "your-api-key",
    "your-client-secret",
    "your-gemini-api-key",
    "your-refresh-token",
    "your-token",
    "insert-key-here",
}

PRIVATE_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "clips",
    "analysis_cache",
    "carryover_backups",
    "downloads",
    "game_context",
    "music",
    "subtitles",
    "tokens",
}

ALWAYS_PRIVATE_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "analysis_cache",
    "carryover_backups",
    "clips",
    "downloads",
    "game_context",
    "music",
    "tokens",
}


def _matches_private_file(path: Path) -> bool:
    return _matches_private_name(path.name)


def _matches_private_name(name: str) -> bool:
    name = str(name or "").lower()
    if name in ALLOWED_TEMPLATE_FILES:
        return False
    return any(fnmatch.fnmatch(name, pattern.lower()) for pattern in PRIVATE_FILE_PATTERNS)


def _looks_like_private_content(path: Path) -> bool:
    if path.suffix.lower() not in {".json", ".env", ".txt", ".ini", ".cfg"}:
        return False
    if _is_public_google_discovery_parts([part.lower() for part in path.parts]):
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:200_000]
    except OSError:
        return False
    if path.suffix.lower() == ".json":
        try:
            return _json_contains_secret_value(json.loads(text))
        except json.JSONDecodeError:
            pass
    return _text_contains_secret(text)


def _json_contains_secret_value(value) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = _normalized_secret_key(key)
            if normalized_key in SECRET_VALUE_KEYS and _looks_like_real_secret(item):
                return True
            if _json_contains_secret_value(item):
                return True
    elif isinstance(value, list):
        return any(_json_contains_secret_value(item) for item in value)
    return False


def _looks_like_real_secret(value) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered in PLACEHOLDER_SECRET_VALUES:
        return False
    placeholder_fragments = ("your-", "your_", "<", ">", "xxxxx", "example_", "example-")
    if any(fragment in lowered for fragment in placeholder_fragments):
        return False
    return len(text) >= 8


def _normalized_secret_key(key) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key or "").lower())


def _text_contains_secret(text: str) -> bool:
    lowered = text.lower()
    if any(marker in lowered for marker in PRIVATE_CONTENT_MARKERS):
        return True
    for match in re.finditer(
        r"(?im)^\s*([a-z0-9_.-]*(?:token|secret|api[_-]?key)[a-z0-9_.-]*)\s*=\s*['\"]?([^'\"\r\n#]+)",
        text,
    ):
        if _normalized_secret_key(match.group(1)) in SECRET_VALUE_KEYS and _looks_like_real_secret(match.group(2)):
            return True
    return False


def _is_public_google_discovery_schema(path: Path) -> bool:
    return _is_public_google_discovery_parts([part.lower() for part in path.parts])


def _is_public_google_discovery_parts(parts: list[str]) -> bool:
    marker = ["_internal", "googleapiclient", "discovery_cache", "documents"]
    for index in range(0, max(0, len(parts) - len(marker) + 1)):
        if parts[index:index + len(marker)] == marker:
            return True
    return False


def _looks_like_private_text(rel_parts: list[str], suffix: str, text: str) -> bool:
    if suffix.lower() not in {".json", ".env", ".txt", ".ini", ".cfg"}:
        return False
    if _is_public_google_discovery_parts(rel_parts):
        return False
    if suffix.lower() == ".json":
        try:
            return _json_contains_secret_value(json.loads(text))
        except json.JSONDecodeError:
            pass
    return _text_contains_secret(text)


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
        if any(part in ALWAYS_PRIVATE_DIR_NAMES for part in rel_parts[1:]):
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
        if _private_dir_violation(rel_parts):
            violations.append(path)
            continue
        if path.is_file():
            if _matches_private_file(path) or _looks_like_private_content(path):
                violations.append(path)
            if path.suffix.lower() == ".zip":
                violations.extend(_scan_zip(path))
    return violations


def _archive_member_path(archive_path: Path, member_name: str) -> Path:
    return Path(f"{archive_path}!{member_name}")


def _scan_zip(path: Path) -> list[Path]:
    violations: list[Path] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                member = PurePosixPath(info.filename)
                rel_parts = [part.lower() for part in member.parts if part not in {"", "."}]
                if not rel_parts or any(part == ".." for part in rel_parts):
                    violations.append(_archive_member_path(path, info.filename))
                    continue
                if _private_dir_violation(rel_parts) or _matches_private_name(member.name):
                    violations.append(_archive_member_path(path, info.filename))
                    continue
                suffix = member.suffix.lower()
                if suffix in {".json", ".env", ".txt", ".ini", ".cfg"}:
                    try:
                        with archive.open(info) as handle:
                            raw = handle.read(200_000)
                        text = raw.decode("utf-8", errors="ignore")
                    except (OSError, zipfile.BadZipFile):
                        continue
                    if _looks_like_private_text(rel_parts, suffix, text):
                        violations.append(_archive_member_path(path, info.filename))
    except zipfile.BadZipFile:
        violations.append(path)
    except OSError:
        pass
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
