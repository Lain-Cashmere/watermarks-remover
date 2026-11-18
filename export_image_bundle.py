#!/usr/bin/env python3
"""Copy the image metadata cleaner into another repo as a small standalone bundle.

    python3 export_image_bundle.py [DEST]

DEST defaults to ../lain-image-retouch/tools/remove-ai-marks.

The bundle is the stdlib-only files behind `clean_image.py` and
`inspect_image.py` (C2PA / EXIF / XMP / AI-metadata strip for PNG, JPEG, WebP,
AVIF, HEIC, BMP, GIF, TIFF, then an iPhone 17 Pro Max capture stamp), the
licence, and a BUNDLE.json recording the commit and a SHA-256 per file. It
needs Python 3.10+ and nothing else: no HTTP service, no Docker, no pip
installs. It strips metadata and does not rewrite pixels. After the strip,
`clean_image.py` stamps JPEG, PNG, and WebP in-process (HEIC, AVIF, and TIFF
when exiftool is installed) as an Apple iPhone 17 Pro Max capture, with the
location set to Sydney CBD, NSW or left unknown. `--keep-tag` copies Artist or
Copyright off the input, and `--tag Name=Value` sets either one (for example
`--tag 'Copyright=Lain Cashmere'`).

Re-run after pulling to refresh the copy. The script only writes the files it
lists; anything else in DEST is left alone. It exits 1 without writing when a
bundled file would import a module that is not in the bundle, and after
writing it runs the copied CLIs once in isolated mode to prove they start.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "service" / "scripts"
DEFAULT_DEST = ROOT.parent / "lain-image-retouch" / "tools" / "remove-ai-marks"

# Order is the import order: common has no local imports, image_meta needs
# common, iphone_capture needs both, and the two CLIs need those.
MODULES = (
    "common.py",
    "image_meta.py",
    "iphone_capture.py",
    "clean_image.py",
    "inspect_image.py",
)
EXTRA = {"LICENSE": ROOT / "LICENSE"}
MANIFEST = "BUNDLE.json"


def local_imports(path: Path) -> set[str]:
    """Names of sibling modules in service/scripts that ``path`` imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return {name for name in names if (SCRIPTS / f"{name}.py").is_file()}


def missing_modules() -> dict[str, list[str]]:
    """Bundled file -> sibling modules it imports that the bundle leaves out."""
    bundled = {Path(name).stem for name in MODULES}
    missing: dict[str, list[str]] = {}
    for name in MODULES:
        absent = sorted(local_imports(SCRIPTS / name) - bundled)
        if absent:
            missing[name] = absent
    return missing


def git(*args: str) -> str:
    exe = shutil.which("git")
    if not exe:
        return ""
    try:
        result = subprocess.run(
            [exe, "-C", str(ROOT), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(dest: Path) -> dict[str, object]:
    sources = {name: SCRIPTS / name for name in MODULES} | EXTRA
    absent = [str(path) for path in sources.values() if not path.is_file()]
    if absent:
        raise SystemExit(f"missing source files: {', '.join(absent)}")
    missing = missing_modules()
    if missing:
        detail = "; ".join(f"{name} imports {', '.join(mods)}" for name, mods in missing.items())
        raise SystemExit(f"bundle would not be self-contained: {detail}")

    dest.mkdir(parents=True, exist_ok=True)
    for name, source in sources.items():
        (dest / name).write_bytes(source.read_bytes())

    tracked = [str(path.relative_to(ROOT)) for path in sources.values()]
    manifest: dict[str, object] = {
        "source": "watermarks-remover (service/scripts)",
        "commit": git("rev-parse", "HEAD") or None,
        # True when a bundled file differs from that commit, so the copy
        # cannot be reproduced from the commit alone.
        "modified_since_commit": bool(git("status", "--porcelain", "--", *tracked)),
        "built": datetime.now(UTC).strftime("%Y-%m-%d"),
        "scope": (
            "image metadata strip (C2PA / EXIF / XMP / AI metadata), then an "
            "iPhone 17 Pro Max capture stamp (Sydney CBD, NSW or unknown); "
            "optional Artist/Copyright keep or set"
        ),
        "files": {name: sha256(dest / name) for name in sources},
    }
    (dest / MANIFEST).write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


def smoke(dest: Path) -> list[str]:
    """Start each copied CLI in isolated mode; return the ones that fail."""
    failed = []
    for cli in ("clean_image.py", "inspect_image.py"):
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-B", str(dest / cli), "--help"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failed.append(f"{cli}: {exc}")
            continue
        if result.returncode != 0:
            lines = result.stderr.strip().splitlines()
            failed.append(f"{cli}: {lines[-1] if lines else 'exit ' + str(result.returncode)}")
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument(
        "dest",
        nargs="?",
        type=Path,
        default=DEFAULT_DEST,
        help=f"folder to write the bundle into (default: {DEFAULT_DEST})",
    )
    args = parser.parse_args()
    dest = args.dest.expanduser().resolve()
    if dest == SCRIPTS or SCRIPTS in dest.parents:
        print(f"refusing to write the bundle into its own source: {dest}", file=sys.stderr)
        return 2
    # One missing level (a new tools/ folder) is created; more than that is
    # usually a mistyped path or a repo that is not checked out.
    if not dest.parent.parent.is_dir():
        print(f"folder does not exist: {dest.parent.parent}", file=sys.stderr)
        return 2

    manifest = build(dest)
    failed = smoke(dest)
    if failed:
        print("bundle written but it does not run:", file=sys.stderr)
        for line in failed:
            print(f"  {line}", file=sys.stderr)
        return 1
    commit = str(manifest["commit"] or "unknown commit")[:12]
    note = " (with uncommitted changes)" if manifest["modified_since_commit"] else ""
    files = manifest["files"]
    assert isinstance(files, dict)
    print(f"{len(files)} files -> {dest}")
    print(f"from {commit}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
