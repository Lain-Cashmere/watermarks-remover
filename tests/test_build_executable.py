"""The packaged cleaner's startup note."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "service" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import executable_note


def test_load_note_from_override(tmp_path, monkeypatch):
    path = tmp_path / "executable_note.txt"
    path.write_text("  Desk copy.\n", encoding="utf-8")
    monkeypatch.setenv("WATERMARKS_EXECUTABLE_NOTE", str(path))
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert executable_note.load_note() == "Desk copy."


def test_missing_note_is_empty(monkeypatch):
    monkeypatch.delenv("WATERMARKS_EXECUTABLE_NOTE", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert executable_note.load_note() == ""


def test_build_script_requires_a_note():
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "build_executable.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "--note" in result.stderr
