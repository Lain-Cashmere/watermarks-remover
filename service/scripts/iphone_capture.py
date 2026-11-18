"""Stamp cleaned images with iPhone 17 Pro Max capture metadata.

The clean endpoint writes this after the metadata strip. JPEG, PNG, and WebP
are stamped in-process. HEIC, AVIF, and TIFF are stamped with exiftool when
that binary is installed. The location on each call is either a GPS fix in
Sydney CBD, NSW, or no location at all.
"""

from __future__ import annotations

import secrets
import struct
import subprocess
import tempfile
import zlib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from common import (
    safe_arg,
    subprocess_creationflags,
    subprocess_preexec_fn,
    which,
)
from image_meta import _webp_chunks, detect_format

MAKE = "Apple"
MODEL = "iPhone 17 Pro Max"
# Software is the iOS version, which is how Apple fills that tag.
# 26.5.2 is the version real iPhone 17 Pro Max captures were writing in
# September 2026.
SOFTWARE = "26.5.2"
HOST_COMPUTER = MODEL
LENS_MAKE = "Apple"
LENS_MODEL = "iPhone 17 Pro Max back triple camera 6.765mm f/1.78"
FOCAL_LENGTH_MM = (6765, 1000)  # 6.765 mm, the wide camera
FNUMBER = (18, 10)  # f/1.8, as stored on real captures of this lens
FOCAL_35MM = 24
# LensInfo on real files: 2.22-16.890625 mm, f/1.78-f/2.8.
LENS_SPEC = ((222, 100), (16890625, 1000000), (178, 100), (28, 10))

SYDNEY_CBD = "Sydney CBD, NSW"
UNKNOWN = "unknown"
LOCATIONS = (SYDNEY_CBD, UNKNOWN)

# Sydney CBD, near Martin Place: 33°52'4.080" S, 151°12'32.400" E.
SYDNEY_LAT_DMS = ((33, 1), (52, 1), (4080, 1000))
SYDNEY_LON_DMS = ((151, 1), (12, 1), (32400, 1000))
SYDNEY_CBD_ALTITUDE_M = 22

_TYPE_BYTE = 1
_TYPE_ASCII = 2
_TYPE_SHORT = 3
_TYPE_LONG = 4
_TYPE_RATIONAL = 5
_TYPE_UNDEFINED = 7
_TYPE_SRATIONAL = 10

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_JPEG_SOI = b"\xff\xd8"
_EXIF_PREFIX = b"Exif\x00\x00"
_EXIF_FLAG = 0x08
_ALPHA_FLAG = 0x10

_EXPOSURE_DENOMS = (40, 60, 80, 100, 120, 200, 250, 400)
_ISO_VALUES = (32, 40, 50, 64, 80, 100, 125, 160, 200, 250)

_EXIFTOOL_SUFFIX = {"heic": ".heic", "avif": ".avif", "tiff": ".tif"}


def stamp_iphone_capture(
    data: bytes,
    *,
    location: str | None = None,
    taken_at: datetime | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Append iPhone 17 Pro Max EXIF to ``data``.

    ``location`` is ``Sydney CBD, NSW``, ``unknown``, or ``None`` to pick one
    of those two at random. Returns the (possibly unchanged) bytes and a
    report describing what was written.
    """
    chosen = _choose_location(location)
    when = _sydney_time(taken_at)
    exposure = secrets.choice(_EXPOSURE_DENOMS)
    iso = secrets.choice(_ISO_VALUES)
    fmt = detect_format(data)
    tiff = _build_tiff(
        when,
        location=chosen,
        size=_image_size(fmt, data),
        exposure_denom=exposure,
        iso=iso,
    )
    stamped = _embed(fmt, data, tiff, when, chosen)
    written = stamped is not None
    where = chosen if chosen == SYDNEY_CBD else "unknown"
    if written:
        action = f"appended capture metadata: {MAKE} {MODEL}, location {where}"
        out = stamped
    else:
        action = f"capture metadata skipped ({fmt})"
        out = data
    return out, {
        "written": written,
        "make": MAKE,
        "model": MODEL,
        "software": SOFTWARE,
        "location": chosen,
        "taken_at": when.isoformat(timespec="seconds"),
        "action": action,
    }


def read_stamped_capture(data: bytes) -> dict[str, Any] | None:
    """Read back Make, Model, and location from a stamped JPEG, PNG, or WebP."""
    fmt = detect_format(data)
    if fmt == "jpeg":
        tiff = _jpeg_exif(data)
    elif fmt == "png":
        tiff = _png_exif(data)
    elif fmt == "webp":
        tiff = _webp_exif(data)
    else:
        return None
    if not tiff:
        return None
    return _read_tiff_capture(tiff)


def _choose_location(location: str | None) -> str:
    if location is None:
        return secrets.choice(LOCATIONS)
    if location not in LOCATIONS:
        raise ValueError("location must be 'Sydney CBD, NSW' or 'unknown'")
    return location


def _sydney_time(when: datetime | None) -> datetime:
    try:
        zone = ZoneInfo("Australia/Sydney")
    except Exception:
        zone = timezone(timedelta(hours=11))
    if when is None:
        return datetime.now(zone)
    if when.tzinfo is None:
        return when.replace(tzinfo=zone)
    return when.astimezone(zone)


def _offset_text(when: datetime) -> str:
    offset = when.utcoffset() or timedelta(0)
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    minutes = abs(total) // 60
    hours, mins = divmod(minutes, 60)
    return f"{sign}{hours:02d}:{mins:02d}"


def _dms_decimal(parts: tuple[tuple[int, int], ...]) -> float:
    degrees = parts[0][0] / parts[0][1]
    minutes = parts[1][0] / parts[1][1]
    seconds = parts[2][0] / parts[2][1]
    return degrees + minutes / 60 + seconds / 3600


def _ascii(value: str) -> tuple[int, int, bytes]:
    raw = value.encode("ascii") + b"\x00"
    return _TYPE_ASCII, len(raw), raw


def _short(value: int) -> tuple[int, int, bytes]:
    return _TYPE_SHORT, 1, struct.pack(">H", value)


def _long(value: int) -> tuple[int, int, bytes]:
    return _TYPE_LONG, 1, struct.pack(">I", value)


def _rational(num: int, den: int) -> tuple[int, int, bytes]:
    return _TYPE_RATIONAL, 1, struct.pack(">II", num, den)


def _rationals(parts: tuple[tuple[int, int], ...]) -> tuple[int, int, bytes]:
    raw = b"".join(struct.pack(">II", num, den) for num, den in parts)
    return _TYPE_RATIONAL, len(parts), raw


def _undefined(raw: bytes) -> tuple[int, int, bytes]:
    return _TYPE_UNDEFINED, len(raw), raw


def _byte(raw: bytes) -> tuple[int, int, bytes]:
    return _TYPE_BYTE, len(raw), raw


def _build_tiff(
    when: datetime,
    *,
    location: str,
    size: tuple[int, int] | None,
    exposure_denom: int,
    iso: int,
) -> bytes:
    taken = when.strftime("%Y:%m:%d %H:%M:%S")
    subsec = f"{when.microsecond // 1000:03d}"
    include_gps = location == SYDNEY_CBD
    offset = _offset_text(when)

    ifd0: list[tuple[int, int, int, bytes]] = [
        (0x010F, *_ascii(MAKE)),
        (0x0110, *_ascii(MODEL)),
        (0x0112, *_short(1)),
        (0x011A, *_rational(72, 1)),
        (0x011B, *_rational(72, 1)),
        (0x0128, *_short(2)),
        (0x0131, *_ascii(SOFTWARE)),
        (0x0132, *_ascii(taken)),
        (0x013C, *_ascii(HOST_COMPUTER)),
        (0x0213, *_short(1)),
    ]
    exif: list[tuple[int, int, int, bytes]] = [
        (0x829A, *_rational(1, exposure_denom)),
        (0x829D, *_rational(*FNUMBER)),
        (0x8822, *_short(2)),
        (0x8827, *_short(iso)),
        (0x9000, *_undefined(b"0232")),
        (0x9003, *_ascii(taken)),
        (0x9004, *_ascii(taken)),
        (0x9101, *_undefined(b"\x01\x02\x03\x00")),
        (0x9204, _TYPE_SRATIONAL, 1, struct.pack(">ii", 0, 1)),
        (0x920A, *_rational(*FOCAL_LENGTH_MM)),
        (0x9290, *_ascii(subsec)),
        (0x9291, *_ascii(subsec)),
        (0x9292, *_ascii(subsec)),
        (0xA000, *_undefined(b"0100")),
        (0xA001, *_short(65535)),
        (0xA217, *_short(2)),
        (0xA301, *_undefined(b"\x01")),
        (0xA402, *_short(0)),
        (0xA403, *_short(0)),
        (0xA405, *_short(FOCAL_35MM)),
        (0xA406, *_short(0)),
        (0xA432, *_rationals(LENS_SPEC)),
        (0xA433, *_ascii(LENS_MAKE)),
        (0xA434, *_ascii(LENS_MODEL)),
    ]
    if include_gps:
        # A timezone with no GPS still says where the clock was set. Only the
        # Sydney fix carries the offset, so "unknown" does not name a place.
        for tag in (0x9010, 0x9011, 0x9012):
            exif.append((tag, *_ascii(offset)))
    if size is not None:
        width, height = size
        exif.append((0xA002, *_long(width)))
        exif.append((0xA003, *_long(height)))

    gps: list[tuple[int, int, int, bytes]] = []
    if include_gps:
        utc = when.astimezone(UTC)
        gps = [
            (0x0000, *_byte(b"\x02\x03\x00\x00")),
            (0x0001, *_ascii("S")),
            (0x0002, *_rationals(SYDNEY_LAT_DMS)),
            (0x0003, *_ascii("E")),
            (0x0004, *_rationals(SYDNEY_LON_DMS)),
            (0x0005, *_byte(b"\x00")),
            (0x0006, *_rational(SYDNEY_CBD_ALTITUDE_M, 1)),
            (
                0x0007,
                *_rationals(((utc.hour, 1), (utc.minute, 1), (utc.second, 1))),
            ),
            (0x001D, *_ascii(utc.strftime("%Y:%m:%d"))),
        ]

    ifd0.append((0x8769, *_long(0)))  # patched to the Exif IFD offset below
    if gps:
        ifd0.append((0x8825, *_long(0)))

    ifd0_size = 2 + 12 * len(ifd0) + 4
    exif_size = 2 + 12 * len(exif) + 4
    gps_size = 2 + 12 * len(gps) + 4 if gps else 0
    exif_off = 8 + ifd0_size
    gps_off = exif_off + exif_size
    data_base = gps_off + gps_size
    _patch_long(ifd0, 0x8769, exif_off)
    if gps:
        _patch_long(ifd0, 0x8825, gps_off)

    overflow = bytearray()
    ifd0_bytes = _emit_ifd(ifd0, 0, overflow, data_base)
    exif_bytes = _emit_ifd(exif, 0, overflow, data_base)
    gps_bytes = _emit_ifd(gps, 0, overflow, data_base) if gps else b""
    header = b"MM" + struct.pack(">HI", 42, 8)
    return header + ifd0_bytes + exif_bytes + gps_bytes + bytes(overflow)


def _patch_long(entries: list[tuple[int, int, int, bytes]], tag: int, value: int) -> None:
    for index, (existing, typ, count, _payload) in enumerate(entries):
        if existing == tag:
            entries[index] = (tag, typ, count, struct.pack(">I", value))
            return
    raise KeyError(tag)


def _emit_ifd(
    entries: list[tuple[int, int, int, bytes]],
    next_offset: int,
    overflow: bytearray,
    data_base: int,
) -> bytes:
    ordered = sorted(entries, key=lambda entry: entry[0])
    out = bytearray(struct.pack(">H", len(ordered)))
    for tag, typ, count, payload in ordered:
        out += struct.pack(">HHI", tag, typ, count)
        if len(payload) <= 4:
            out += payload + b"\x00" * (4 - len(payload))
        else:
            while len(overflow) % 2:
                overflow.append(0)
            out += struct.pack(">I", data_base + len(overflow))
            overflow.extend(payload)
    out += struct.pack(">I", next_offset)
    return bytes(out)


def _image_size(fmt: str, data: bytes) -> tuple[int, int] | None:
    if fmt == "png":
        return _png_size(data)
    if fmt == "jpeg":
        return _jpeg_size(data)
    if fmt == "webp":
        return _webp_size(data)
    return None


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or not data.startswith(_PNG_SIG) or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    if width < 1 or height < 1:
        return None
    return width, height


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    sof = (
        set(range(0xC0, 0xC4))
        | set(range(0xC5, 0xC8))
        | set(range(0xC9, 0xCC))
        | set(range(0xCD, 0xD0))
    )
    i = 2
    n = len(data)
    while i + 4 <= n and data.startswith(_JPEG_SOI):
        if data[i] != 0xFF:
            return None
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            return None
        marker = data[i]
        i += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if i + 2 > n:
            return None
        seglen = struct.unpack(">H", data[i : i + 2])[0]
        if seglen < 2 or i + seglen > n:
            return None
        payload = data[i + 2 : i + seglen]
        if marker in sof and len(payload) >= 5:
            height, width = struct.unpack(">HH", payload[1:5])
            if width >= 1 and height >= 1:
                return width, height
            return None
        if marker == 0xDA:
            return None
        i += seglen
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    chunks, notes = _webp_chunks(data)
    if not chunks or any(note.startswith(("truncated", "trailing")) for note in notes):
        return None
    for fourcc, payload, _pad in chunks:
        if fourcc == b"VP8X" and len(payload) >= 10:
            width = int.from_bytes(payload[4:7], "little") + 1
            height = int.from_bytes(payload[7:10], "little") + 1
            if width >= 1 and height >= 1:
                return width, height
        if fourcc == b"VP8 ":
            size = _vp8_size(payload)
            if size:
                return size
        if fourcc == b"VP8L":
            size = _vp8l_size(payload)
            if size:
                return size
    return None


def _vp8_size(payload: bytes) -> tuple[int, int] | None:
    if len(payload) < 10 or payload[0] & 1:
        return None
    if payload[3:6] != b"\x9d\x01\x2a":
        return None
    width = int.from_bytes(payload[6:8], "little") & 0x3FFF
    height = int.from_bytes(payload[8:10], "little") & 0x3FFF
    if width < 1 or height < 1:
        return None
    return width, height


def _vp8l_size(payload: bytes) -> tuple[int, int] | None:
    if len(payload) < 5 or payload[0] != 0x2F:
        return None
    bits = int.from_bytes(payload[1:5], "little")
    width = (bits & 0x3FFF) + 1
    height = ((bits >> 14) & 0x3FFF) + 1
    return width, height


def _embed(
    fmt: str,
    data: bytes,
    tiff: bytes,
    when: datetime,
    location: str,
) -> bytes | None:
    if fmt == "jpeg":
        return _embed_jpeg(data, tiff)
    if fmt == "png":
        return _embed_png(data, tiff)
    if fmt == "webp":
        return _embed_webp(data, tiff)
    if fmt in _EXIFTOOL_SUFFIX:
        return _embed_exiftool(data, _EXIFTOOL_SUFFIX[fmt], when, location)
    return None


def _embed_jpeg(data: bytes, tiff: bytes) -> bytes | None:
    if not data.startswith(_JPEG_SOI):
        return None
    payload = _EXIF_PREFIX + tiff
    if len(payload) + 2 > 65535:
        return None
    segment = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    body = _jpeg_without_exif(data)
    return body[:2] + segment + body[2:]


def _jpeg_without_exif(data: bytes) -> bytes:
    out = bytearray(_JPEG_SOI)
    i = 2
    n = len(data)
    while i < n:
        if data[i] != 0xFF:
            out.extend(data[i:])
            break
        start = i
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            break
        marker = data[i]
        i += 1
        if marker == 0xD9:
            out.extend(b"\xff\xd9")
            break
        if marker == 0xD8:
            continue
        if 0xD0 <= marker <= 0xD7:
            out.extend(bytes([0xFF, marker]))
            continue
        if marker == 0xDA:
            out.extend(b"\xff\xda")
            out.extend(data[i:])
            break
        if i + 2 > n:
            out.extend(data[start:])
            break
        seglen = struct.unpack(">H", data[i : i + 2])[0]
        if seglen < 2 or i + seglen > n:
            out.extend(data[start:])
            break
        payload = data[i + 2 : i + seglen]
        if not (marker == 0xE1 and payload.startswith(_EXIF_PREFIX)):
            out.extend(data[start : i + seglen])
        i += seglen
    return bytes(out)


def _png_chunk(ctype: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(ctype + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", crc)


def _embed_png(data: bytes, tiff: bytes) -> bytes | None:
    if not data.startswith(_PNG_SIG):
        return None
    pos = 8
    chunks: list[tuple[bytes, bytes]] = []
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        ctype = data[pos + 4 : pos + 8]
        end = pos + 8 + length
        if end + 4 > len(data):
            return None
        chunks.append((ctype, data[pos + 8 : end]))
        pos = end + 4
        if ctype == b"IEND":
            break
    else:
        return None
    if not any(ctype == b"IHDR" for ctype, _payload in chunks):
        return None
    out = bytearray(_PNG_SIG)
    inserted = False
    exif = _png_chunk(b"eXIf", tiff)
    for ctype, payload in chunks:
        if ctype == b"eXIf":
            continue
        out.extend(_png_chunk(ctype, payload))
        if ctype == b"IHDR" and not inserted:
            out.extend(exif)
            inserted = True
    return bytes(out) if inserted else None


def _embed_webp(data: bytes, tiff: bytes) -> bytes | None:
    chunks, notes = _webp_chunks(data)
    broken = [
        note for note in notes if note == "not a WebP" or note.startswith(("truncated", "trailing"))
    ]
    if not chunks or broken:
        return None
    kept = [(fourcc, payload) for fourcc, payload, _pad in chunks if fourcc != b"EXIF"]
    has_vp8x = any(fourcc == b"VP8X" for fourcc, _payload in kept)
    if not has_vp8x:
        size = _webp_size(data)
        if size is None:
            return None
        width, height = size
        if width > 0xFFFFFF or height > 0xFFFFFF:
            return None
        flags = _EXIF_FLAG
        if any(fourcc == b"ALPH" for fourcc, _payload in kept):
            flags |= _ALPHA_FLAG
        vp8x = bytes([flags, 0, 0, 0])
        vp8x += (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
        kept.insert(0, (b"VP8X", vp8x))
    else:
        updated: list[tuple[bytes, bytes]] = []
        for fourcc, payload in kept:
            if fourcc == b"VP8X" and len(payload) >= 10:
                flagged = bytes([payload[0] | _EXIF_FLAG]) + payload[1:]
                updated.append((fourcc, flagged))
            elif fourcc == b"VP8X":
                return None
            else:
                updated.append((fourcc, payload))
        kept = updated
    kept.append((b"EXIF", tiff))
    body = bytearray(b"WEBP")
    for fourcc, payload in kept:
        body.extend(fourcc)
        body.extend(struct.pack("<I", len(payload)))
        body.extend(payload)
        if len(payload) & 1:
            body.append(0)
    return b"RIFF" + struct.pack("<I", len(body)) + bytes(body)


def _embed_exiftool(data: bytes, suffix: str, when: datetime, location: str) -> bytes | None:
    exiftool = which("exiftool")
    if not exiftool:
        return None
    taken = when.strftime("%Y:%m:%d %H:%M:%S")
    args = [
        exiftool,
        "-overwrite_original",
        f"-Make={MAKE}",
        f"-Model={MODEL}",
        f"-Software={SOFTWARE}",
        f"-HostComputer={HOST_COMPUTER}",
        f"-LensMake={LENS_MAKE}",
        f"-LensModel={LENS_MODEL}",
        f"-DateTimeOriginal={taken}",
        f"-CreateDate={taken}",
        f"-ModifyDate={taken}",
        "-FocalLength=6.765",
        "-FNumber=1.8",
        "-FocalLengthIn35mmFormat=24",
    ]
    if location == SYDNEY_CBD:
        args.extend(
            [
                f"-OffsetTime={_offset_text(when)}",
                f"-OffsetTimeOriginal={_offset_text(when)}",
                f"-GPSLatitude={_dms_decimal(SYDNEY_LAT_DMS)}",
                "-GPSLatitudeRef=S",
                f"-GPSLongitude={_dms_decimal(SYDNEY_LON_DMS)}",
                "-GPSLongitudeRef=E",
                f"-GPSAltitude={SYDNEY_CBD_ALTITUDE_M}",
                "-GPSAltitudeRef=0",
            ]
        )
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            path = Path(handle.name)
            handle.write(data)
        args.append(safe_arg(str(path)))
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            preexec_fn=subprocess_preexec_fn,
            creationflags=subprocess_creationflags,
        )
        if result.returncode != 0 or path is None:
            return None
        stamped = path.read_bytes()
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        if path is not None:
            path.unlink(missing_ok=True)
    if MODEL.encode("ascii") not in stamped:
        return None
    return stamped


def _jpeg_exif(data: bytes) -> bytes | None:
    if not data.startswith(_JPEG_SOI):
        return None
    i = 2
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            return None
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            return None
        marker = data[i]
        i += 1
        if marker in (0xD8, 0xD9) or marker == 0xDA:
            return None
        if 0xD0 <= marker <= 0xD7:
            continue
        if i + 2 > n:
            return None
        seglen = struct.unpack(">H", data[i : i + 2])[0]
        if seglen < 2 or i + seglen > n:
            return None
        payload = data[i + 2 : i + seglen]
        if marker == 0xE1 and payload.startswith(_EXIF_PREFIX):
            return payload[len(_EXIF_PREFIX) :]
        i += seglen
    return None


def _png_exif(data: bytes) -> bytes | None:
    if not data.startswith(_PNG_SIG):
        return None
    pos = 8
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        ctype = data[pos + 4 : pos + 8]
        end = pos + 8 + length
        if end + 4 > len(data):
            return None
        if ctype == b"eXIf":
            return data[pos + 8 : end]
        if ctype == b"IEND":
            return None
        pos = end + 4
    return None


def _webp_exif(data: bytes) -> bytes | None:
    chunks, notes = _webp_chunks(data)
    if notes and not chunks:
        return None
    for fourcc, payload, _pad in chunks:
        if fourcc == b"EXIF":
            return payload
    return None


def _read_ifd(data: bytes, offset: int, endian: str) -> dict[int, bytes]:
    count = struct.unpack_from(endian + "H", data, offset)[0]
    pos = offset + 2
    unit = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 10: 8}
    entries: dict[int, bytes] = {}
    for _ in range(count):
        tag, typ, number = struct.unpack_from(endian + "HHI", data, pos)
        nbytes = unit.get(typ, 1) * number
        if nbytes <= 4:
            raw = data[pos + 8 : pos + 8 + nbytes]
        else:
            ptr = struct.unpack_from(endian + "I", data, pos + 8)[0]
            raw = data[ptr : ptr + nbytes]
        entries[tag] = raw
        pos += 12
    return entries


def _ascii_tag(raw: bytes | None) -> str:
    if not raw:
        return ""
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")


def _read_tiff_capture(tiff: bytes) -> dict[str, Any] | None:
    if len(tiff) < 8 or tiff[:2] not in (b"MM", b"II"):
        return None
    endian = ">" if tiff[:2] == b"MM" else "<"
    if struct.unpack_from(endian + "H", tiff, 2)[0] != 42:
        return None
    ifd0_off = struct.unpack_from(endian + "I", tiff, 4)[0]
    try:
        ifd0 = _read_ifd(tiff, ifd0_off, endian)
    except (struct.error, IndexError, KeyError):
        return None
    location = UNKNOWN
    latitude = None
    longitude = None
    gps_raw = ifd0.get(0x8825)
    if gps_raw and len(gps_raw) >= 4:
        gps_off = struct.unpack(endian + "I", gps_raw[:4])[0]
        try:
            gps = _read_ifd(tiff, gps_off, endian)
        except (struct.error, IndexError, KeyError):
            gps = {}
        latitude = _ascii_tag(gps.get(0x0001)), _rationals_of(gps.get(0x0002), endian)
        longitude = _ascii_tag(gps.get(0x0003)), _rationals_of(gps.get(0x0004), endian)
        location = SYDNEY_CBD if _is_sydney(latitude, longitude) else "other"
    return {
        "make": _ascii_tag(ifd0.get(0x010F)),
        "model": _ascii_tag(ifd0.get(0x0110)),
        "software": _ascii_tag(ifd0.get(0x0131)),
        "location": location,
        "latitude": latitude,
        "longitude": longitude,
    }


def _rationals_of(raw: bytes | None, endian: str) -> list[tuple[int, int]]:
    if not raw:
        return []
    values = []
    for index in range(0, len(raw) - 7, 8):
        num, den = struct.unpack_from(endian + "II", raw, index)
        values.append((num, den))
    return values


def _is_sydney(
    latitude: tuple[str, list[tuple[int, int]]],
    longitude: tuple[str, list[tuple[int, int]]],
) -> bool:
    lat_ref, lat = latitude
    lon_ref, lon = longitude
    if lat_ref != "S" or lon_ref != "E" or not lat or not lon:
        return False
    return lat == list(SYDNEY_LAT_DMS) and lon == list(SYDNEY_LON_DMS)
