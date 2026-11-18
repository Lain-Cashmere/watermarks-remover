#!/usr/bin/env python3
"""Strip C2PA and AI-related metadata, then stamp iPhone 17 Pro Max capture metadata.

JPEG, PNG, and WebP are stamped in-process. HEIC, AVIF, and TIFF are stamped
when exiftool is installed. The location is Sydney CBD, NSW or unknown.
Artist and Copyright can be copied from the input or set explicitly.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import backup_path, cleaned_path, eprint
from image_meta import clean_image
from iphone_capture import (
    CREDIT_TAGS,
    _validate_credit_values,
    resolve_credit_tags,
    stamp_iphone_capture,
)


def _cli_tags(items: list[str]) -> dict[str, str]:
    tags: dict[str, str] = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"tag must be Name=Value, got {item!r}")
        tags[name] = value
    return tags


def _parse_taken_at(value: str) -> datetime:
    try:
        when = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"taken-at must be an ISO-8601 time, got {value!r}") from exc
    if when.tzinfo is None:
        raise ValueError("taken-at needs a timezone offset")
    return when


def _apply_capture_stamp(
    dest: Path,
    src: Path,
    result: dict,
    *,
    tags: dict[str, str] | None = None,
    seed: int | None = None,
    taken_at: datetime | None = None,
) -> None:
    """Write the same iPhone capture tags /clean appends, after the strip."""
    if not dest.is_file():
        return
    stamped, capture = stamp_iphone_capture(
        dest.read_bytes(), tags=tags, seed=seed, taken_at=taken_at
    )
    if capture["written"]:
        dest.write_bytes(stamped)
    result.setdefault("actions", []).append(capture["action"])
    result["capture"] = {
        "written": capture["written"],
        "make": capture["make"],
        "model": capture["model"],
        "software": capture["software"],
        "location": capture["location"],
        "taken_at": capture["taken_at"],
        "tags": capture["tags"],
    }
    result["bytes_out"] = dest.stat().st_size
    # In-place with an existing backup reads and writes the same path, so the
    # pre-stamp `changed` flag is the one that still knows the original bytes.
    if src.resolve() != dest.resolve():
        result["changed"] = dest.read_bytes() != src.read_bytes()
    elif capture["written"]:
        result["changed"] = True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("path", type=Path, help="Input image (PNG/JPEG/WebP/AVIF/HEIC/BMP/GIF/TIFF)")
    p.add_argument("-o", "--output", type=Path, help="Output path (default: *.cleaned.*)")
    p.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite input (writes .bak backup first)",
    )
    p.add_argument(
        "--keep-non-ai-metadata",
        action="store_true",
        help="Only drop segments/chunks that look like C2PA/AI (less aggressive)",
    )
    p.add_argument(
        "--keep-tag",
        action="append",
        default=[],
        choices=tuple(CREDIT_TAGS),
        help="Copy Artist or Copyright from the input onto the cleaned file",
    )
    p.add_argument(
        "--tag",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Set Artist or Copyright, overriding --keep-tag. "
        "Example: --tag 'Copyright=Lain Cashmere'",
    )
    p.add_argument(
        "--fill-credit",
        default=None,
        help="Keep Artist and Copyright, and set either missing one to this text",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Repeat the same location, shutter and ISO when this number is the same",
    )
    p.add_argument(
        "--taken-at",
        default=None,
        help="Capture time as ISO-8601 with a timezone offset",
    )
    p.add_argument("--json", action="store_true", help="JSON result on stdout")
    p.add_argument(
        "--synthid-dir",
        type=str,
        default=None,
        help="reverse-SynthID checkout root for optional pixel SynthID scoring",
    )
    p.add_argument(
        "--remove-pixel",
        choices=["ctrlregen", "diffusion"],
        default=None,
        help="Run optional pixel-watermark removal after metadata cleaning "
        "(ctrlregen = CtrlRegen regeneration; diffusion = MarkDiffusion "
        "DiffusionPurification regeneration)",
    )
    p.add_argument(
        "--ctrlregen-dir",
        type=str,
        default=None,
        help="noai-watermark checkout root (default: $NOAI_WATERMARK_DIR)",
    )
    p.add_argument(
        "--ctrlregen-intensity",
        type=float,
        default=0.25,
        help="CtrlRegen intensity in (0, 1] (default: 0.25, conservative)",
    )
    p.add_argument(
        "--ctrlregen-steps",
        type=int,
        default=50,
        help="CtrlRegen diffusion steps (default: 50)",
    )
    p.add_argument(
        "--ctrlregen-device",
        type=str,
        default=None,
        help="CtrlRegen device: auto|cpu|cuda|mps (default: auto)",
    )
    p.add_argument(
        "--ctrlregen-seed",
        type=int,
        default=None,
        help="Optional CtrlRegen RNG seed",
    )
    p.add_argument(
        "--ctrlregen-timeout",
        type=int,
        default=3600,
        help="CtrlRegen subprocess timeout in seconds (default: 3600)",
    )
    p.add_argument(
        "--markdiffusion-dir",
        type=str,
        default=None,
        help="MarkDiffusion bootstrap dir (default: $MARKDIFFUSION_DIR)",
    )
    p.add_argument(
        "--markdiffusion-intensity",
        type=float,
        default=0.3,
        help="DiffusionPurification intensity in (0, 1] (default: 0.3)",
    )
    p.add_argument(
        "--markdiffusion-model",
        type=str,
        default=None,
        help="Stable Diffusion model for purification (default: SD 2.1 base)",
    )
    p.add_argument(
        "--markdiffusion-size",
        type=int,
        default=512,
        help="Purification working size in px (default: 512)",
    )
    p.add_argument(
        "--markdiffusion-steps",
        type=int,
        default=50,
        help="Purification diffusion steps (default: 50)",
    )
    p.add_argument(
        "--markdiffusion-device",
        type=str,
        default=None,
        help="Purification device: auto|cpu|cuda|mps (default: auto)",
    )
    p.add_argument(
        "--markdiffusion-timeout",
        type=int,
        default=3600,
        help="Purification subprocess timeout in seconds (default: 3600)",
    )
    args = p.parse_args()

    if not args.path.is_file():
        eprint(f"not a file: {args.path}")
        return 2

    if args.in_place:
        bak, created = backup_path(args.path)
        if not created:
            eprint(f"backup {bak} already exists from an earlier run; keeping the original backup")
            src = args.path
        else:
            src = bak
        dest = args.path
    else:
        src = args.path
        dest = args.output or cleaned_path(args.path)

    try:
        keep = list(args.keep_tag)
        if args.fill_credit is not None:
            for name in CREDIT_TAGS:
                if name not in keep:
                    keep.append(name)
        credit_tags = resolve_credit_tags(
            src.read_bytes(),
            keep=keep,
            set_tags=_cli_tags(args.tag),
        )
        if args.fill_credit is not None:
            fill = _validate_credit_values({"Artist": args.fill_credit})["Artist"]
            for name in CREDIT_TAGS:
                credit_tags.setdefault(name, fill)
        taken_at = _parse_taken_at(args.taken_at) if args.taken_at else None
    except ValueError as exc:
        eprint(f"error: {exc}")
        return 2

    try:
        result = clean_image(
            src,
            dest,
            strip_all_metadata=not args.keep_non_ai_metadata,
            synthid_dir=args.synthid_dir,
            remove_pixel=args.remove_pixel,
            ctrlregen_dir=args.ctrlregen_dir,
            ctrlregen_intensity=args.ctrlregen_intensity,
            ctrlregen_steps=args.ctrlregen_steps,
            ctrlregen_device=args.ctrlregen_device,
            ctrlregen_seed=args.ctrlregen_seed,
            ctrlregen_timeout=args.ctrlregen_timeout,
            markdiffusion_dir=args.markdiffusion_dir,
            markdiffusion_intensity=args.markdiffusion_intensity,
            markdiffusion_model=args.markdiffusion_model,
            markdiffusion_size=args.markdiffusion_size,
            markdiffusion_steps=args.markdiffusion_steps,
            markdiffusion_device=args.markdiffusion_device,
            markdiffusion_timeout=args.markdiffusion_timeout,
        )
        _apply_capture_stamp(
            dest, src, result, tags=credit_tags, seed=args.seed, taken_at=taken_at
        )
    except Exception as e:
        eprint(f"error: {e}")
        return 1

    pr = result.get("pixel_removal")
    residual = result["still_has_c2pa"] or result["still_has_ai_metadata"]
    failed = residual or (pr is not None and not pr.get("available"))

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        eprint(f"wrote {result['output']} ({result['bytes_in']} -> {result['bytes_out']})")
        for a in result["actions"]:
            eprint(f"  - {a}")
        if result.get("synthid_before") and result["synthid_before"].get("available"):
            label = "yes" if result["synthid_before"].get("is_watermarked") else "no"
            eprint(
                "SynthID before: "
                f"confidence {result['synthid_before'].get('confidence', 0.0):.3f} "
                f"(watermarked: {label})"
            )
        if result.get("synthid_after") and result["synthid_after"].get("available"):
            label = "yes" if result["synthid_after"].get("is_watermarked") else "no"
            eprint(
                "SynthID after: "
                f"confidence {result['synthid_after'].get('confidence', 0.0):.3f} "
                f"(watermarked: {label})"
            )
        if pr is not None:
            if pr.get("available"):
                engine = (
                    "CtrlRegen" if args.remove_pixel == "ctrlregen" else "DiffusionPurification"
                )
                eprint(f"{engine}: removed on {pr.get('device', 'unknown device')}")
            else:
                engine = (
                    "CtrlRegen" if args.remove_pixel == "ctrlregen" else "DiffusionPurification"
                )
                eprint(f"{engine}: unavailable: {pr.get('error', 'unknown error')}")
        if residual:
            eprint("warning: residual C2PA/AI signals may remain")
            for f in result.get("post_findings") or []:
                eprint(f"  ! {f}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
