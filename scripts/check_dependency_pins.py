"""Fail release builds when direct requirement files contain floating pins."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENT_FILES = [
    ROOT / "requirements.txt",
    ROOT / "requirements-build.txt",
]
PINNED_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[^#\s]+")


def _requirement_lines(path: Path) -> list[tuple[int, str]]:
    if not path.exists():
        return []
    rows = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(("-r ", "--")):
            continue
        rows.append((lineno, line))
    return rows


def main() -> int:
    violations = []
    for path in REQUIREMENT_FILES:
        for lineno, line in _requirement_lines(path):
            if not PINNED_RE.match(line):
                violations.append(f"{path.relative_to(ROOT)}:{lineno}: {line}")

    if violations:
        print("[!] Direct dependencies must be pinned with == for release builds.")
        for violation in violations:
            print(f"    {violation}")
        return 1

    print("[+] Direct dependency pins OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
