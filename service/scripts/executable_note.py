"""Startup note bundled into the clean-image executable."""

from __future__ import annotations

import os
import sys
from pathlib import Path

NOTE_FILENAME = "executable_note.txt"


def note_path() -> Path | None:
    """Where the build stored the note, if this process is that executable."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base) / NOTE_FILENAME
        return None
    override = os.environ.get("WATERMARKS_EXECUTABLE_NOTE")
    if override:
        return Path(override)
    return None


def load_note() -> str:
    path = note_path()
    if path is None or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def show_note() -> None:
    note = load_note()
    if note:
        print(note, file=sys.stderr)
        print(file=sys.stderr)
