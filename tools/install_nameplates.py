#!/usr/bin/env python3
"""
Install hand-drawn key nameplates into the pack.

Takes a folder of PNGs named after the plate LABELS (e.g. "GRAVE PLOT.png") and
copies them to the filenames the pack expects, which are based on the tracker
item codes (e.g. graveplotkey.png). Run it whenever imklubb sends new art:

    python3 tools/install_nameplates.py "path/to/Name Plates"

By default the same plate is used for a key and its keyring, since the two are
never on screen at once (scripts/keysanity_layout.lua swaps between them). Pass
--keyrings-from to install a separate set for keyrings.

Labels come from LABELS in tools/make_key_nameplates.py, so that stays the single
place where a plate's text is defined; the generator there produces placeholders
with the same names.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.abspath(os.path.join(HERE, ".."))

sys.path.insert(0, HERE)
from make_key_nameplates import LABELS  # noqa: E402
from make_key_grids import plate_file  # noqa: E402

TARGET = {"key": "images/items/keys", "keyring": "images/items/keyrings"}
EXPECT_ASPECT = 96 / 32  # plates are 3:1; the pack renders them 96x32 and 72x24


def collect(folder: str) -> dict:
    """label -> source path, matching on the filename stem, case-insensitively."""
    found = {}
    for fn in os.listdir(folder):
        if not fn.lower().endswith(".png"):
            continue
        found[os.path.splitext(fn)[0].strip().upper()] = os.path.join(folder, fn)
    return found


def install(folder: str, kind: str, report: list) -> int:
    src = collect(folder)
    out_dir = os.path.join(PACK, TARGET[kind])
    os.makedirs(out_dir, exist_ok=True)
    n = 0
    for code, (label, _group) in LABELS.items():
        key = label.strip().upper()
        if key not in src:
            report.append(f"  no art for {label!r} ({kind}) - leaving the existing file")
            continue
        dst = os.path.join(out_dir, plate_file(code, kind))
        shutil.copyfile(src[key], dst)
        n += 1
    extra = sorted(set(src) - {l.strip().upper() for l, _ in LABELS.values()})
    for e in extra:
        report.append(f"  unused source file {e!r}.png - no key has that label")
    return n


def check_sizes(folder: str, report: list):
    try:
        from PIL import Image
    except ImportError:
        return
    sizes = {}
    for fn in sorted(os.listdir(folder)):
        if not fn.lower().endswith(".png"):
            continue
        with Image.open(os.path.join(folder, fn)) as im:
            sizes.setdefault(im.size, []).append(fn)
            ar = im.width / im.height
            if abs(ar - EXPECT_ASPECT) > 0.02:
                report.append(f"  {fn}: aspect {ar:.2f}, expected {EXPECT_ASPECT:.2f} "
                              f"- it will be letterboxed in the item box")
    for size, files in sizes.items():
        print(f"  {len(files)} file(s) at {size[0]}x{size[1]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="folder of PNGs named after the plate labels")
    ap.add_argument("--keyrings-from", default=None,
                    help="separate folder for keyring plates (default: reuse the keys)")
    args = ap.parse_args()

    if not os.path.isdir(args.folder):
        raise SystemExit(f"not a folder: {args.folder}")

    report = []
    check_sizes(args.folder, report)
    n_keys = install(args.folder, "key", report)
    ring_src = args.keyrings_from or args.folder
    n_rings = install(ring_src, "keyring", report)

    print(f"installed {n_keys} key plates -> {TARGET['key']}/")
    print(f"installed {n_rings} keyring plates -> {TARGET['keyring']}/"
          + ("" if args.keyrings_from else "  (same art as the keys)"))
    if report:
        print("\nnotes:")
        for r in dict.fromkeys(report):
            print(r)
    missing = len(LABELS) - n_keys
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
