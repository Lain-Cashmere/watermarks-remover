"""Capture metadata stamped onto cleaned images."""

from __future__ import annotations

import struct
import sys
import zlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "service" / "scripts"))

from iphone_capture import (
    MODEL,
    SYDNEY_CBD,
    SYDNEY_LAT_DMS,
    SYDNEY_LON_DMS,
    UNKNOWN,
    read_stamped_capture,
    stamp_iphone_capture,
)

SYDNEY = ZoneInfo("Australia/Sydney")
TAKEN = datetime(2026, 10, 27, 20, 40, 15, tzinfo=SYDNEY)


def _png_chunk(ctype: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(ctype + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", crc)


def _png() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        sig
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00"))
        + _png_chunk(b"IEND", b"")
    )


def _jpeg() -> bytes:
    app0 = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    return b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", len(app0) + 2) + app0 + b"\xff\xd9"


def _webp_chunk(fourcc: bytes, payload: bytes) -> bytes:
    pad = b"\x00" if len(payload) & 1 else b""
    return fourcc + struct.pack("<I", len(payload)) + payload + pad


def _webp() -> bytes:
    body = b"WEBP" + _webp_chunk(b"VP8X", bytes(10)) + _webp_chunk(b"VP8 ", b"\x00" * 10)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_jpeg_sydney_capture_roundtrip():
    stamped, report = stamp_iphone_capture(_jpeg(), location=SYDNEY_CBD, taken_at=TAKEN)
    assert report["written"] is True
    assert report["make"] == "Apple"
    assert report["model"] == MODEL
    assert report["software"] == "26.5.2"
    assert report["location"] == SYDNEY_CBD
    assert report["taken_at"] == "2026-10-27T20:40:15+11:00"
    assert b"2026:10:27 20:40:15" in stamped
    assert b"+11:00" in stamped
    read = read_stamped_capture(stamped)
    assert read is not None
    assert read["make"] == "Apple"
    assert read["model"] == MODEL
    assert read["software"] == "26.5.2"
    assert read["location"] == SYDNEY_CBD
    assert read["latitude"] == ("S", list(SYDNEY_LAT_DMS))
    assert read["longitude"] == ("E", list(SYDNEY_LON_DMS))
    assert stamped.startswith(b"\xff\xd8\xff\xe1")
    assert b"JFIF" in stamped


def test_unknown_location_omits_gps_and_offset():
    stamped, report = stamp_iphone_capture(_png(), location=UNKNOWN, taken_at=TAKEN)
    assert report["written"] is True
    assert report["location"] == UNKNOWN
    assert b"+11:00" not in stamped
    read = read_stamped_capture(stamped)
    assert read is not None
    assert read["location"] == UNKNOWN
    assert read["latitude"] is None
    assert read["model"] == MODEL
    assert b"eXIf" in stamped


def test_restamp_replaces_previous_exif():
    once, _report = stamp_iphone_capture(_jpeg(), location=UNKNOWN, taken_at=TAKEN)
    twice, _report = stamp_iphone_capture(once, location=SYDNEY_CBD, taken_at=TAKEN)
    assert twice.count(MODEL.encode("ascii")) == once.count(MODEL.encode("ascii")) == 3
    assert twice.count(b"Exif\x00\x00") == 1
    assert read_stamped_capture(twice)["location"] == SYDNEY_CBD


def test_webp_sets_exif_flag():
    stamped, report = stamp_iphone_capture(_webp(), location=UNKNOWN, taken_at=TAKEN)
    assert report["written"] is True
    assert read_stamped_capture(stamped)["model"] == MODEL
    # VP8X payload starts 8 bytes after the 'VP8X' fourcc, which itself
    # follows the 12-byte RIFF/WEBP header.
    assert stamped[20] & 0x08


def test_each_clean_picks_sydney_or_unknown():
    seen = set()
    blob = _jpeg()
    for _ in range(40):
        _stamped, report = stamp_iphone_capture(blob)
        seen.add(report["location"])
    assert seen == {SYDNEY_CBD, UNKNOWN}


def test_gif_is_left_unchanged():
    gif = b"GIF89a" + b"\x00" * 16
    stamped, report = stamp_iphone_capture(gif, location=SYDNEY_CBD, taken_at=TAKEN)
    assert stamped == gif
    assert report["written"] is False
    assert "gif" in report["action"]


def test_location_must_be_known():
    with pytest.raises(ValueError):
        stamp_iphone_capture(_jpeg(), location="Melbourne")
