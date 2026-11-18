"""The standalone image-cleaner bundle (export_image_bundle.py) must run on its own."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import export_image_bundle

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(ctype: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(ctype + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", crc)


def _png_with_c2pa() -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return (
        PNG_SIG
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"caBX", b"jumb c2pa manifest")
        + _chunk(b"IDAT", idat)
        + _chunk(b"IEND", b"")
    )


def _export(dest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "export_image_bundle.py"), str(dest)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_bundle_lists_every_local_import():
    assert export_image_bundle.missing_modules() == {}


def test_bundle_cleans_an_image_without_the_repo(tmp_path):
    dest = tmp_path / "bundle"
    result = _export(dest)
    assert result.returncode == 0, result.stderr

    manifest = json.loads((dest / "BUNDLE.json").read_text(encoding="utf-8"))
    names = {*export_image_bundle.MODULES, "LICENSE"}
    assert set(manifest["files"]) == names
    assert {path.name for path in dest.iterdir()} == names | {"BUNDLE.json"}
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((dest / name).read_bytes()).hexdigest() == digest

    source = tmp_path / "shot.png"
    source.write_bytes(_png_with_c2pa())
    cleaned = tmp_path / "shot.cleaned.png"
    # -I keeps the repo off sys.path, so only the copied files can satisfy the imports.
    run = subprocess.run(
        [sys.executable, "-I", "-B", str(dest / "clean_image.py"), str(source), "-o", str(cleaned)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    data = cleaned.read_bytes()
    assert b"caBX" not in data
    assert b"IDAT" in data


def test_bundle_leaves_other_files_in_dest_alone(tmp_path):
    dest = tmp_path / "bundle"
    dest.mkdir()
    (dest / "notes.md").write_text("mine", encoding="utf-8")
    assert _export(dest).returncode == 0
    assert (dest / "notes.md").read_text(encoding="utf-8") == "mine"


def test_export_refuses_a_destination_two_levels_missing(tmp_path):
    result = _export(tmp_path / "no-such-repo" / "tools" / "bundle")
    assert result.returncode == 2
    assert not (tmp_path / "no-such-repo").exists()
