#!/usr/bin/env python3
"""
Generate layouts/compact.json - smaller variants of the item grids, used by the
portrait ("tracker_vertical") layout so the map gets most of the window.

Why a generator instead of hand-written JSON: PopTracker's item_size is a fixed
pixel value on each node and is NOT inherited from a parent, so a compact layout
needs its own full copy of every grid. Keeping that copy by hand would mean
editing each grid twice and letting them drift. Instead this reads the normal
grids and emits scaled copies, so there is still one source of truth.

Re-run it after editing any of the normal grids:

    python3 tools/make_compact_layouts.py

Sizing notes (measured against PopTracker's own layout code):
  * A "group" adds 36px of height: 24px title bar + 4px top padding +
    4px spacing + 4px bottom padding.
  * A group's WIDTH is driven by its header text, not its icon - the title label
    carries 11px of margin on each side. "GDDT" at DejaVuSans-Bold 11pt is ~35px,
    so a key/warp group is ~65px wide whether the icon is 32px or 24px.
  * Only one of keys/keyrings is ever shown (scripts/keysanity_layout.lua swaps
    them), so the band is sized for the larger of the two, not both.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.abspath(os.path.join(HERE, ".."))

# PopTracker layout constants, from src/uilib/group.cpp and src/ui/defaults.h
GROUP_TITLE_HEIGHT = 24
GROUP_TITLE_MARGIN = 11
GROUP_PADDING = 4
GROUP_SPACING = 4
GROUP_CHROME_H = GROUP_PADDING * 2 + GROUP_TITLE_HEIGHT + GROUP_SPACING  # 36
GROUP_CHROME_W = GROUP_PADDING * 2  # 8
FONT_SIZE = 11
# PopTracker renders group titles in DejaVuSans-Bold 11pt. The font is not
# vendored here (it has its own license); the fit report looks for a system copy
# and falls back to an estimate, which only affects the printed numbers.
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/DejaVuSans-Bold.ttf",
    os.path.join(HERE, "DejaVuSans-Bold.ttf"),
]

# item_size remaps: normal -> compact
SIZE_MAP = {"64": "32", "32": "24"}
MARGIN_MAP = {4: 2, 2: 1}

# Grids to generate: (source file, source key, compact key, reflow-to-N-per-row or None)
GRIDS = [
    ("items.json", "shared_item_grid", "shared_item_grid_compact", None),
    ("tokens.json", "collectables_grid", "collectables_grid_compact", None),
]
# keys.json / keyrings.json are produced by tools/make_key_grids.py, which emits
# its own *_compact variants, so they are not rescaled here.


def load_jsonc(path: str):
    """Parse PopTracker's JSON dialect (// comments, trailing commas)."""
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
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return json.loads(re.sub(r",(\s*[\]}])", r"\1", "".join(out)))


def shrink(node):
    """Recursively remap item_size / item_margin to their compact values."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "item_size":
                s = str(v)
                out[k] = SIZE_MAP.get(s, s)
            elif k == "item_margin" and isinstance(v, int):
                out[k] = MARGIN_MAP.get(v, v)
            else:
                out[k] = shrink(v)
        return out
    if isinstance(node, list):
        return [shrink(v) for v in node]
    return node


def collect_groups(node, acc):
    """Pull every {"type":"group"} out of a grid, in order."""
    if isinstance(node, dict):
        if node.get("type") == "group":
            acc.append(node)
            return
        for v in node.values():
            collect_groups(v, acc)
    elif isinstance(node, list):
        for v in node:
            collect_groups(v, acc)


def reflow(grid, per_row):
    """Rebuild a vertical array of group-rows with `per_row` groups per row."""
    groups = []
    collect_groups(grid, groups)
    rows = [groups[i:i + per_row] for i in range(0, len(groups), per_row)]
    return {
        "type": "array",
        "orientation": "vertical",
        "h_alignment": grid.get("h_alignment", "center"),
        "margin": 0,
        "content": [
            {"type": "array", "orientation": "horizontal", "margin": "0,0", "content": row}
            for row in rows
        ],
    }


# ---------------------------------------------------------------- measuring

def text_width(s: str) -> float:
    """Width of a header string in the title font, for the fit report."""
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return len(s) * 7.5  # rough fallback; only affects the printed report
    path = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
    if path is None:
        return len(s) * 7.5
    font = TTFont(path)
    upm = font["head"].unitsPerEm
    hmtx = font["hmtx"]
    cmap = font.getBestCmap()
    total = 0
    for ch in s:
        g = cmap.get(ord(ch))
        if g:
            total += hmtx[g][0]
    return total / upm * FONT_SIZE


def measure(node) -> tuple[int, int]:
    """Approximate (width, height) in px, mirroring PopTracker's layout rules."""
    if not isinstance(node, dict):
        return (0, 0)
    t = node.get("type")

    if t == "itemgrid":
        rows = node.get("rows", [])
        # item_size is either "32" or "<width>,<height>"
        raw = str(node.get("item_size", 32)).split(",")
        sw = int(raw[0])
        sh = int(raw[1]) if len(raw) > 1 else sw
        m = node.get("item_margin", 0)
        mx = my = int(m) if isinstance(m, int) else 0
        cols = max((len(r) for r in rows), default=0)
        return (cols * (sw + 2 * mx), len(rows) * (sh + 2 * my))

    if t == "group":
        cw, ch = measure(node.get("content", {}))
        label = node.get("header", "")
        lw = text_width(label) + 2 * GROUP_TITLE_MARGIN
        return (int(max(cw, lw)) + GROUP_CHROME_W, ch + GROUP_CHROME_H)

    if t == "array":
        kids = node.get("content", [])
        if isinstance(kids, dict):
            kids = [kids]
        dims = [measure(k) for k in kids]
        if not dims:
            return (0, 0)
        if node.get("orientation") == "vertical":
            return (max(d[0] for d in dims), sum(d[1] for d in dims))
        return (sum(d[0] for d in dims), max(d[1] for d in dims))

    if t == "dock":
        kids = node.get("content", [])
        if isinstance(kids, dict):
            kids = [kids]
        w = h = 0
        for k in kids:
            if not isinstance(k, dict):
                continue
            kw, kh = measure(k)
            if k.get("dock") in ("left", "right"):
                w += kw
                h = max(h, kh)
            elif k.get("dock") in ("top", "bottom"):
                h += kh
                w = max(w, kw)
            else:  # floater: takes what is left, contributes its minimum
                w = max(w, kw)
                h = max(h, kh)
        return (w, h)

    if t in ("container", "layout"):
        return measure(node.get("content", {}))

    kids = node.get("content", [])
    if isinstance(kids, dict):
        kids = [kids]
    dims = [measure(k) for k in kids if isinstance(k, dict)]
    if not dims:
        return (0, 0)
    return (max(d[0] for d in dims), sum(d[1] for d in dims))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(PACK, "layouts", "compact.json"))
    ap.add_argument("--width", type=int, default=950,
                    help="portrait window width to check the strip against")
    args = ap.parse_args()

    compact = {}
    report = []
    for fname, src_key, dst_key, per_row in GRIDS:
        path = os.path.join(PACK, "layouts", fname)
        grid = load_jsonc(path)[src_key]
        before = measure(grid)
        small = shrink(copy.deepcopy(grid))
        # the normal key grids carry a 25px left margin to clear the column edge;
        # in the portrait band they sit flush, so drop it
        if small.get("margin") == "25,0,0,0":
            small["margin"] = 0
        if per_row:
            small = reflow(small, per_row)
        after = measure(small)
        compact[dst_key] = small
        report.append((dst_key, before, after))

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(compact, f, indent="\t")
        f.write("\n")
    print(f"wrote {args.out}")

    print("\n  grid                          normal        compact")
    for key, before, after in report:
        print(f"  {key:28} {before[0]:4}x{before[1]:<4}  ->  {after[0]:4}x{after[1]:<4}")

    # The portrait band: row 1 is [items+collectables | keys | keyrings],
    # row 2 is the reflowed warps. Check both against the target width.
    d = {k: measure(v) for k, v in compact.items()}
    col_a = (max(d["shared_item_grid_compact"][0], d["collectables_grid_compact"][0]),
             d["shared_item_grid_compact"][1] + d["collectables_grid_compact"][1])
    # each of these sits in a titled group, which adds chrome
    def grouped(wh):
        return (wh[0] + GROUP_CHROME_W, wh[1] + GROUP_CHROME_H)
    # Only one key grid is ever on screen (keys and keyrings are mutually
    # exclusive, swapped by scripts/keysanity_layout.lua) and the warp grid was
    # dropped in 1.1.0, so the band is one row:
    #   [items + collectables] [whichever key grid is active]
    keys = load_jsonc(os.path.join(PACK, "layouts", "keys.json"))["keys_grid_compact"]
    rings = load_jsonc(os.path.join(PACK, "layouts", "keyrings.json"))["keyrings_grid_compact"]
    kd, rd = measure(keys), measure(rings)
    keyw, keyh = max(kd[0], rd[0]), max(kd[1], rd[1])
    row = [grouped(col_a), grouped((keyw, keyh))]
    band_w, band_h = sum(x[0] for x in row), max(x[1] for x in row)

    print(f"\n  portrait band: [items+collectables {grouped(col_a)[0]}px]"
          f" [active keys {grouped((keyw, keyh))[0]}px]")
    print(f"  band total  : {band_w}x{band_h}  (0 when keysanity is off)")
    print(f"  at a {args.width}px-wide window: "
          f"{'FITS' if band_w <= args.width else 'OVERFLOWS by ' + str(band_w - args.width) + 'px'}"
          f" (needs >= {band_w}px)")
    print(f"  map then gets the remaining height: window_height - {band_h} - ~55px of chrome")
    if band_w > args.width:
        print("\n  WARNING: the band is wider than the target window and will be clipped.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
