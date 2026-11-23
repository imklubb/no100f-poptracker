#!/usr/bin/env python3
"""
Generate layouts/keys.json and layouts/keyrings.json.

Keys are grouped by the map their door is on, which is how you actually think
about them while playing, and sorted alphabetically by nameplate label inside
each group. The grouping comes from the apworld: each key gates a
connection, and the connection's region tells you the map. Two keys (Hedge Maze
and Fishing Village) gate doors leading OUT of the Mystery Machine, so they are
Hub keys even though the rooms they open are elsewhere.

Each key is a wide "nameplate" item rather than a square icon with a three-letter
header above it. That reads better and is shorter: a group header costs 36px of
height, so dropping 15 of them saves far more than the plates cost.

    python3 tools/make_key_grids.py

Both the normal and the compact (portrait) variants are written, so there is one
source of truth for the grouping.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.abspath(os.path.join(HERE, ".."))

sys.path.insert(0, HERE)
from make_key_nameplates import LABELS, GROUP_ORDER, GROUP_NAME  # noqa: E402

# (item_size, columns) for each variant
NORMAL = ("96,32", 2)
COMPACT = ("72,24", 4)

# In the portrait layout the four groups stacked in one column are taller than
# they need to be, so they are split across two side-by-side columns. Mystic
# Manor has 12 keys, as many as the other three put together, so it gets its own.
COMPACT_COLUMNS = [
    ["hub", "hedge", "cove"],
    ["manor"],
]

# The broadcast view is a tall, narrow overlay, so its keys stay in one stacked
# column with the plates two abreast rather than four.
BROADCAST = ("72,24", 2)


def plate_file(code: str, kind: str) -> str:
    name = code if kind == "key" else code + "ring"
    return re.sub(r"[^a-z0-9_-]", "", name.replace("'", "")) + ".png"


def make_group(g: str, kind: str, item_size: str, columns: int):
    """One titled group: a map's keys as a grid of nameplates."""
    # alphabetical by the nameplate label, so the plates read in order
    codes = sorted((c for c, (_l, grp) in LABELS.items() if grp == g),
                   key=lambda c: LABELS[c][0])
    if not codes:
        return None
    if kind == "keyring":
        codes = [c + "ring" for c in codes]
    rows = [codes[i:i + columns] for i in range(0, len(codes), columns)]
    return OrderedDict([
        ("type", "group"),
        ("header", GROUP_NAME[g]),
        ("content", OrderedDict([
            ("type", "itemgrid"),
            ("item_size", item_size),
            ("item_margin", 1),
            ("h_alignment", "left"),
            ("rows", rows),
        ])),
    ])


def column(groups):
    return OrderedDict([
        ("type", "array"),
        ("dock", "left"),
        ("orientation", "vertical"),
        ("h_alignment", "left"),
        ("v_alignment", "top"),
        ("margin", 0),
        ("content", groups),
    ])


def side_by_side(kind: str, item_size: str, columns: int):
    """Two columns of groups. A dock is used rather than a horizontal array
    because an HBox always stretches its LAST child to fill the leftover width,
    which drags the group's title bar across the whole window. Inside a dock,
    children that are all docked "left" keep their own width."""
    cols = []
    for names in COMPACT_COLUMNS:
        groups = [g for g in (make_group(n, kind, item_size, columns) for n in names) if g]
        if groups:
            cols.append(column(groups))
    return OrderedDict([
        ("type", "dock"),
        ("h_alignment", "left"),
        ("margin", 0),
        ("content", cols),
    ])


def grid(kind: str, item_size: str, columns: int):
    """A vertical array of one group per map, each holding a nameplate grid."""
    groups = []
    for g in GROUP_ORDER:
        # alphabetical by the nameplate label, so the plates read in order
        codes = sorted((c for c, (_l, grp) in LABELS.items() if grp == g),
                       key=lambda c: LABELS[c][0])
        if not codes:
            continue
        if kind == "keyring":
            codes = [c + "ring" for c in codes]
        rows = [codes[i:i + columns] for i in range(0, len(codes), columns)]
        groups.append(OrderedDict([
            ("type", "group"),
            ("header", GROUP_NAME[g]),
            ("content", OrderedDict([
                ("type", "itemgrid"),
                ("item_size", item_size),
                ("item_margin", 1),
                ("h_alignment", "left"),
                ("rows", rows),
            ])),
        ]))
    return OrderedDict([
        ("type", "array"),
        ("orientation", "vertical"),
        ("h_alignment", "left"),
        ("margin", 0),
        ("content", groups),
    ])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-art", action="store_true",
                    help="fail if a nameplate image is missing")
    args = ap.parse_args()

    missing = []
    for kind, out, base in (("key", "layouts/keys.json", "keys_grid"),
                            ("keyring", "layouts/keyrings.json", "keyrings_grid")):
        data = OrderedDict([
            (base, grid(kind, *NORMAL)),
            (base + "_compact", side_by_side(kind, *COMPACT)),
            (base + "_broadcast", grid(kind, *BROADCAST)),
        ])
        path = os.path.join(PACK, out)
        with open(path, "w", encoding="utf-8") as f:
            f.write("// Generated by tools/make_key_grids.py - do not hand-edit.\n")
            json.dump(data, f, indent="\t")
            f.write("\n")
        n = sum(len(r) for g in data[base]["content"] for r in g["content"]["rows"])
        cols = data[base + "_compact"]["content"]
        shape = " | ".join("+".join(c["content"][i]["header"] for i in range(len(c["content"])))
                           for c in cols)
        print(f"wrote {out}  ({len(data[base]['content'])} groups, {n} items)")
        print(f"    portrait columns: {shape}")

        folder = "images/items/keys" if kind == "key" else "images/items/keyrings"
        for code in LABELS:
            p = os.path.join(PACK, folder, plate_file(code, kind))
            if not os.path.exists(p):
                missing.append(p)

    for g in GROUP_ORDER:
        codes = sorted((c for c, (_l, grp) in LABELS.items() if grp == g),
                       key=lambda c: LABELS[c][0])
        print(f"  {GROUP_NAME[g]:16} {len(codes):2}  "
              f"{', '.join(LABELS[c][0] for c in codes)}")

    if missing:
        print(f"\n  {len(missing)} nameplate image(s) missing; "
              f"run tools/make_key_nameplates.py", file=sys.stderr)
        if args.check_art:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
