"""Write SHA256 sidecars for release artifacts."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _write_sidecar(path: Path, digest: str) -> None:
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{digest}  {path.name}\n",
        encoding="utf-8",
    )


def main(argv: list[str]) -> int:
    if len(argv) not in {2, 3} or (len(argv) == 3 and argv[2] != "--include-installer"):
        print("Usage: python scripts/write_release_hashes.py <app-version> [--include-installer]")
        return 2

    version = argv[1]
    include_installer = "--include-installer" in argv[2:]
    release_dir = Path("release")
    required = [
        release_dir / f"ViriaRevive-v{version}-Windows-x64.zip",
        release_dir / "ViriaRevive-Windows-x64.zip",
    ]

    for path in required:
        if not path.exists():
            print(f"[release-hash] Missing release artifact: {path}")
            return 1

    versioned_digest = _sha256(required[0])
    latest_digest = _sha256(required[1])
    if latest_digest != versioned_digest:
        print("[release-hash] ZIP artifacts differ; refusing to reuse latest sidecar digest")
        print(f"  - {required[0]}: {versioned_digest}")
        print(f"  - {required[1]}: {latest_digest}")
        return 1
    _write_sidecar(required[0], versioned_digest)
    _write_sidecar(required[1], latest_digest)
    print(f"[release-hash] ZIP SHA256: {versioned_digest}")

    if include_installer:
        installer = release_dir / f"ViriaReviveSetup-v{version}.exe"
        if not installer.exists():
            print(f"[release-hash] Missing installer artifact: {installer}")
            return 1
        digest = _sha256(installer)
        _write_sidecar(installer, digest)
        print(f"[release-hash] {installer.name} SHA256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
