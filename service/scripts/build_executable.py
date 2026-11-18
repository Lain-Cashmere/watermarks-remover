#!/usr/bin/env python3
"""Build a one-file clean-image executable for this operating system.

PyInstaller only builds for the OS it is running on. This Linux machine
produces the Linux file. The Mac file is built by the ``clean-image``
GitHub Actions workflow, which runs on a Mac and uploads ``dist/clean-image``.
The MIT license is bundled next to the note. ``--note`` is printed to stderr
every time the executable starts.

  python3 service/scripts/build_executable.py --note "Built for the Lain desk."

If PyInstaller is not already installed, the script creates
``dist/pyinstaller-venv`` and installs it there. That avoids the
externally-managed-environment error from system pip.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "service" / "scripts"
LICENSE = ROOT / "LICENSE"
ENTRY = SCRIPTS / "clean_executable.py"
NOTE_FILENAME = "executable_note.txt"


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _interpreter_has_pyinstaller(python: str) -> bool:
    result = subprocess.run(
        [python, "-c", "import PyInstaller"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _pyinstaller(out: Path) -> list[str]:
    """Use an existing PyInstaller, or install one into a build virtualenv."""
    if importlib.util.find_spec("PyInstaller") is not None:
        return [sys.executable, "-m", "PyInstaller"]
    found = shutil.which("pyinstaller")
    if found:
        return [found]
    venv = out / "pyinstaller-venv"
    python = _venv_python(venv)
    if not python.is_file():
        print(
            f"Creating {venv} for PyInstaller. System pip will not install it.",
            file=sys.stderr,
        )
        created = subprocess.run([sys.executable, "-m", "venv", str(venv)], check=False)
        if created.returncode != 0 or not python.is_file():
            raise SystemExit(
                "Could not create a virtual environment. "
                "On Debian or Ubuntu: sudo apt install python3-venv"
            )
    if not _interpreter_has_pyinstaller(str(python)):
        print("Installing PyInstaller into the build virtual environment.", file=sys.stderr)
        installed = subprocess.run(
            [str(python), "-m", "pip", "install", "pyinstaller"],
            check=False,
        )
        if installed.returncode != 0:
            raise SystemExit(installed.returncode)
    return [str(python), "-m", "PyInstaller"]


def build(note: str, *, name: str, out: Path) -> Path:
    if not LICENSE.is_file():
        raise SystemExit(f"missing {LICENSE}")
    if not ENTRY.is_file():
        raise SystemExit(f"missing {ENTRY}")
    out.mkdir(parents=True, exist_ok=True)
    work = out / "pyinstaller"
    work.mkdir(parents=True, exist_ok=True)
    note_path = work / NOTE_FILENAME
    note_path.write_text(note.strip() + "\n", encoding="utf-8")
    # PyInstaller uses ';' between source and dest on Windows, ':' elsewhere.
    sep = ";" if os.name == "nt" else ":"
    cmd = [
        *_pyinstaller(out),
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        name,
        "--distpath",
        str(out),
        "--workpath",
        str(work / "work"),
        "--specpath",
        str(work),
        "--paths",
        str(SCRIPTS),
        "--add-data",
        f"{note_path}{sep}.",
        "--add-data",
        f"{LICENSE}{sep}.",
        str(ENTRY),
    ]
    result = subprocess.run(cmd, cwd=ROOT, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    binary = out / (name + (".exe" if os.name == "nt" else ""))
    if not binary.is_file():
        raise SystemExit(f"build finished but {binary} is missing")
    return binary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--note",
        required=True,
        help="Text the executable prints on startup. The MIT license is still bundled.",
    )
    parser.add_argument(
        "--name",
        default="clean-image",
        help="Executable file name (default: clean-image)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "dist",
        help="Directory for the executable (default: dist/)",
    )
    args = parser.parse_args()
    binary = build(args.note, name=args.name, out=args.out.expanduser().resolve())
    print(binary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
