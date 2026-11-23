#!/usr/bin/env python3
"""
Generate the "All Maps" view: images/maps/all.png plus the maps.json entry and
the duplicated map_locations in locations/locations.json.

Layout is a 3x2 grid, cells numbered left-to-right, top-to-bottom:

    1 2 3        1 = blank          4 = Hedge Maze
    4 5 6        2 = Mystic Manor   5 = Hub
                 3 = blank          6 = Smuggler's Cove

Every pin on the four individual maps is copied onto the combined map with its
coordinates offset into the right cell, so the All Maps tab tracks exactly the
same checks as the per-area tabs.

Re-run after moving any pin or changing a map image:

    python3 tools/make_all_maps.py

It is idempotent: existing "all" entries are dropped and rebuilt each run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

try:
    from PIL import Image
except ImportError:
    print("this script needs Pillow:  pip install Pillow", file=sys.stderr)
    raise

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.abspath(os.path.join(HERE, ".."))

ALL_MAP_NAME = "all"
OUT_IMAGE = "images/maps/all.png"

# cell index (1-based, reading order) -> map name; missing numbers stay blank
CELL_MAP = {
    2: "manor",
    4: "hedge",
    5: "hub",
    6: "cove",
}
COLS, ROWS = 3, 2
BLANK = (0, 0, 0, 255)  # solid black, matching the tracker background


def load_jsonc(path: str):
    src = open(path, encoding="utf-8-sig").read()
    out, i, n, instr = [], 0, len(src), False
    while i < n:
        c = src[i]
        if instr:
            out.append(c)
            if c == "\\":
                out.append(src[i + 1])
                i += 2
                continue
            if c == '"':
                instr = False
            i += 1
            continue
        if c == '"':
            instr = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        out.append(c)
        i += 1
    return json.loads(re.sub(r",(\s*[\]}])", r"\1", "".join(out)))


def dump(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent="\t", ensure_ascii=False)
        f.write("\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=1.0,
                    help="scale the composite image; below 1.0 trades sharpness "
                         "for pack size")
    ap.add_argument("--pin-scale", type=float, default=1.0,
                    help="pin size relative to the map art. 1.0 (default) makes a "
                         "pin cover the same part of the map as it does on the "
                         "single-area tabs; 3.0 would keep its on-screen pixel "
                         "size instead, which looks enormous on the shrunken panels")
    args = ap.parse_args()

    maps_path = os.path.join(PACK, "maps", "maps.json")
    locs_path = os.path.join(PACK, "locations", "locations.json")
    maps = load_jsonc(maps_path)

    by_name = {m["name"]: m for m in maps if m.get("name") != ALL_MAP_NAME}
    missing = [n for n in CELL_MAP.values() if n not in by_name]
    if missing:
        raise SystemExit(f"maps.json is missing: {missing}")

    images = {n: Image.open(os.path.join(PACK, by_name[n]["img"])).convert("RGBA")
              for n in CELL_MAP.values()}

    # one uniform cell big enough for the largest map, so every panel keeps its
    # own scale and nothing is distorted
    cell_w = max(im.width for im in images.values())
    cell_h = max(im.height for im in images.values())
    full_w, full_h = cell_w * COLS, cell_h * ROWS

    canvas = Image.new("RGBA", (full_w, full_h), BLANK)
    offsets = {}
    for cell, name in CELL_MAP.items():
        col = (cell - 1) % COLS
        row = (cell - 1) // COLS
        im = images[name]
        # centre each map inside its cell
        ox = col * cell_w + (cell_w - im.width) // 2
        oy = row * cell_h + (cell_h - im.height) // 2
        canvas.alpha_composite(im, (ox, oy))
        offsets[name] = (ox, oy)

    if args.scale != 1.0:
        full_w = int(full_w * args.scale)
        full_h = int(full_h * args.scale)
        canvas = canvas.resize((full_w, full_h), Image.LANCZOS)
        offsets = {n: (int(x * args.scale), int(y * args.scale)) for n, (x, y) in offsets.items()}

    out_path = os.path.join(PACK, OUT_IMAGE)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.convert("RGB").save(out_path, optimize=True)
    size_kb = os.path.getsize(out_path) / 1024
    print(f"wrote {OUT_IMAGE}  {full_w}x{full_h}  ({size_kb:.0f} KB)")

    # Pin sizes are in map pixels. At --pin-scale 1.0 a pin covers the same part
    # of the art as on the single-area maps, so the combined view looks like the
    # individual ones, just smaller.
    pin_scale = args.pin_scale * args.scale
    base = next(m for m in maps if m["name"] == "hub")
    loc_size = max(1, round(base.get("location_size", 32) * pin_scale))
    border = max(1, round(base.get("location_border_thickness", 1) * pin_scale))

    maps = [m for m in maps if m.get("name") != ALL_MAP_NAME]
    maps.append({
        "name": ALL_MAP_NAME,
        "location_size": loc_size,
        "location_border_thickness": border,
        "img": OUT_IMAGE,
    })
    dump(maps_path, maps)
    print(f"updated maps/maps.json  (location_size {loc_size}, border {border}, "
          f"pin scale x{pin_scale:.2f} relative to the art)")

    # ---- duplicate every pin onto the combined map -----------------------
    locs = load_jsonc(locs_path)
    added = 0

    def walk(nodes):
        nonlocal added
        for nd in nodes:
            ml = nd.get("map_locations")
            if ml:
                kept = [m for m in ml if m.get("map") != ALL_MAP_NAME]
                extra = []
                for m in kept:
                    name = m.get("map")
                    if name not in offsets:
                        continue
                    ox, oy = offsets[name]
                    copy = dict(m)
                    copy["map"] = ALL_MAP_NAME
                    copy["x"] = int(round(m["x"] * args.scale)) + ox
                    copy["y"] = int(round(m["y"] * args.scale)) + oy
                    if "size" in m:  # per-pin override has to grow too
                        copy["size"] = max(1, round(m["size"] * pin_scale))
                    extra.append(copy)
                    added += 1
                nd["map_locations"] = kept + extra
            walk(nd.get("children", []) or [])

    walk(locs)
    dump(locs_path, locs)
    print(f"updated locations/locations.json  ({added} pins copied onto '{ALL_MAP_NAME}')")
    return 0


if __name__ == "__main__":
    sys.exit(main())
