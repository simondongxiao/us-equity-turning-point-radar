"""Build a portable source-only Skill archive with an embedded checksum manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "us-equity-turning-point-radar"
VERSION = "1.1.2-directional-turn"
ROOT_FILES = (
    "SKILL.md",
    "CHANGELOG.md",
    "README.md",
    "BOOTSTRAP_PROMPT.txt",
    "CODEX_PROMPT.txt",
    "requirements.txt",
)
SOURCE_DIRS = ("references", "assets", "contracts", "scripts", "tests", ".github")
SECRET_PATTERNS = (
    re.compile(rb"gho_[A-Za-z0-9]{20,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def source_files() -> list[Path]:
    files = [ROOT / name for name in ROOT_FILES]
    for directory in SOURCE_DIRS:
        files.extend(
            path
            for path in (ROOT / directory).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )
    return sorted(set(files), key=lambda path: path.relative_to(ROOT).as_posix())


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    files = source_files()
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing package inputs: " + ", ".join(missing))
    entries: dict[str, dict[str, int | str]] = {}
    payloads: dict[str, bytes] = {}
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        payload = path.read_bytes()
        for pattern in SECRET_PATTERNS:
            if pattern.search(payload):
                raise ValueError(f"Potential secret found in {relative}")
        payloads[relative] = payload
        entries[relative] = {"sha256": sha256(payload), "bytes": len(payload)}
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unavailable"
    manifest = {
        "package": PACKAGE,
        "version": VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": commit,
        "file_count": len(entries),
        "excluded_runtime": [".git", "data", "outputs", "site", "state", "secrets", "caches"],
        "files": entries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite {args.output}")
    prefix = PACKAGE + "/"
    with zipfile.ZipFile(args.output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, payload in payloads.items():
            archive.writestr(prefix + relative, payload)
        archive.writestr(
            prefix + "PACKAGE-MANIFEST.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    with zipfile.ZipFile(args.output) as archive:
        for relative, expected in entries.items():
            actual = sha256(archive.read(prefix + relative))
            if actual != expected["sha256"]:
                raise ValueError(f"Archive verification failed: {relative}")
    print(json.dumps({"archive": str(args.output), "sha256": sha256(args.output.read_bytes()), **manifest}, ensure_ascii=False))


if __name__ == "__main__":
    main()
