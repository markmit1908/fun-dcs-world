#!/usr/bin/env python3
"""
Copy the generated .miz files next to this script into the DCS Missions folder,
and install the loss tracker's GUI hook into the sibling Scripts\\Hooks folder.

Does nothing if the Missions folder doesn't exist (for example on a computer
without DCS), so it is safe to run anywhere.

Run:
    python install_missions.py                  # default: ~/Saved Games/DCS/Missions
    python install_missions.py --dest "D:/DCS Missions"
    python install_missions.py --no-hook        # missions only
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SOURCE_DIR = Path(__file__).resolve().parent
DEFAULT_DEST = Path.home() / "Saved Games" / "DCS" / "Missions"
HOOK = SOURCE_DIR / "loss_tracker" / "LossTrackerGameGUI.lua"


def install(dest: Path) -> list[Path]:
    """Copy every .miz in SOURCE_DIR into dest; return the copied paths."""
    copied = []
    for miz in sorted(SOURCE_DIR.glob("*.miz")):
        target = dest / miz.name
        shutil.copy2(miz, target)
        copied.append(target)
    return copied


def install_hook(missions_dir: Path) -> Path:
    """Copy the GUI hook into <Saved Games>/DCS/Scripts/Hooks next to missions_dir."""
    hooks = missions_dir.parent / "Scripts" / "Hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    target = hooks / HOOK.name
    shutil.copy2(HOOK, target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_DEST,
        help=f"DCS Missions folder (default: {DEFAULT_DEST})",
    )
    parser.add_argument(
        "--no-hook",
        action="store_true",
        help="don't install the loss tracker GUI hook",
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

    if not args.no_hook:
        target = install_hook(args.dest)
        print(f"Installed {target.name} -> {target} (restart DCS to load it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
