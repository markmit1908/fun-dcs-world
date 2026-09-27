#!/usr/bin/env python3
"""
Copy the generated .miz files next to this script into the DCS Missions folder.

Does nothing if the Missions folder doesn't exist (for example on a computer
without DCS), so it is safe to run anywhere.

Run:
    python install_missions.py                  # default: ~/Saved Games/DCS/Missions
    python install_missions.py --dest "D:/DCS Missions"
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SOURCE_DIR = Path(__file__).resolve().parent
DEFAULT_DEST = Path.home() / "Saved Games" / "DCS" / "Missions"


def install(dest: Path) -> list[Path]:
    """Copy every .miz in SOURCE_DIR into dest; return the copied paths."""
    copied = []
    for miz in sorted(SOURCE_DIR.glob("*.miz")):
        target = dest / miz.name
        shutil.copy2(miz, target)
        copied.append(target)
    return copied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_DEST,
        help=f"DCS Missions folder (default: {DEFAULT_DEST})",
    )
    args = parser.parse_args(argv)

    if not args.dest.is_dir():
        print(f"Skipped: {args.dest} doesn't exist.")
        return 0

    copied = install(args.dest)
    if not copied:
        print(f"No .miz files found in {SOURCE_DIR}.")
    for path in copied:
        print(f"Copied {path.name} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
