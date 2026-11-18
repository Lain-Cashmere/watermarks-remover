#!/usr/bin/env python3
"""Entry point for the packaged clean-image executable.

Prints the note supplied to ``build_executable.py --note``, then runs the
image cleaner. The note goes to stderr so ``--json`` on stdout stays intact.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from executable_note import show_note


def main() -> int:
    show_note()
    from clean_image import main as clean_main

    return clean_main()


if __name__ == "__main__":
    raise SystemExit(main())
