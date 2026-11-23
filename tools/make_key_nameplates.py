#!/usr/bin/env python3
"""
Generate placeholder nameplate images for the key / keyring items.

Every key currently shares one image (images/items/key.png), which is why the
grid needed three-letter headers to tell them apart. A nameplate puts the name on
the item itself, so the headers can go.

These are PLACEHOLDERS, kept for bringing up a new key or re-checking sizing.
THE PACK NOW SHIPS HAND-DRAWN PLATES, so this script refuses to overwrite an
existing file unless you pass --force. To install real art use
tools/install_nameplates.py instead.

    python3 tools/make_key_nameplates.py            # fills in anything missing
    python3 tools/make_key_nameplates.py --force    # overwrites the real art

Plate size is 96x32 at 1x. The files are rendered at 4x (384x128) so they stay
sharp when PopTracker scales them; it fits the image to the item box, so only the
aspect ratio has to match.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.abspath(os.path.join(HERE, ".."))

PLATE_W, PLATE_H = 96, 32
SUPERSAMPLE = 4
ICON_SRC = {"key": "images/items/key.png", "keyring": "images/items/keyring.png"}
OUT_DIR = {"key": "images/items/keys", "keyring": "images/items/keyrings"}

PLATE_BG = (24, 24, 28, 235)
PLATE_EDGE = (90, 90, 98, 255)
TEXT = (236, 232, 222, 255)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]

# Short label per key, and which map group it belongs to. Derived from the
# apworld (see tools/key_reference.json) and kept here so the art and the layout
# stay in step.
LABELS = {
    # Hub - gated straight off the Mystery Machine
    "hedgemazekey": ("HEDGE", "hub"),
    "fishingvillagekey": ("FISHING", "hub"),
    # Mystic Manor (mansion, basement, passages, rooftops)
    "clamor1key": ("CLAMOR 1", "manor"),
    "clamor4key": ("CLAMOR 4", "manor"),
    "mindyourmanorskey": ("MANORS", "manor"),
    "cellar2key": ("CELLAR 2", "manor"),
    "cellar3key": ("CELLAR 3", "manor"),
    "creepy2key": ("CREEPY 2", "manor"),
    "creepy3key": ("CREEPY 3", "manor"),
    "gusts1key": ("GUSTS 1", "manor"),
    "gusts2key": ("GUSTS 2", "manor"),
    "attickey": ("ATTIC", "manor"),
    "knightkey": ("KNIGHT", "manor"),
    "don'tlookdownkey": ("DON'T LOOK", "manor"),
    # Hedge Maze (hedge, cliffs, graveyard)
    "graveplotkey": ("GRAVE PLOT", "hedge"),
    "cave-inkey": ("CAVE-IN", "hedge"),
    "tomb1key": ("TOMB 1", "hedge"),
    "tomb3key": ("TOMB 3", "hedge"),
    # Smuggler's Cove (docks, fishing village, shipwrecks)
    "coastkey": ("COAST", "cove"),
    "fishyclueskey": ("FISHY CLUES", "cove"),
    "shiverkey": ("SHIVER", "cove"),
}

GROUP_ORDER = ["hub", "manor", "hedge", "cove"]
GROUP_NAME = {
    "hub": "Hub",
    "manor": "Mystic Manor",
    "hedge": "Hedge Maze",
    "cove": "Smuggler's Cove",
}


def load_font(px: int):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return ImageFont.truetype(p, px)
    return ImageFont.load_default()


def fit_text(draw, text, max_w, start_px):
    """Largest font size that fits text into max_w."""
    size = start_px
    while size > 6:
        font = load_font(size)
        if draw.textlength(text, font=font) <= max_w:
            return font
        size -= 1
    return load_font(6)


def uniform_font(labels, avail_w, start_px):
    """One font size that fits every label, so all plates look like a set."""
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    size = start_px
    while size > 6:
        font = load_font(size)
        if all(probe.textlength(l, font=font) <= avail_w for l in labels):
            return font
        size -= 1
    return load_font(6)


def make_plate(icon: Image.Image, label: str, out_path: str, font):
    s = SUPERSAMPLE
    w, h = PLATE_W * s, PLATE_H * s
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    r = 4 * s
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=PLATE_BG, outline=PLATE_EDGE,
                        width=max(1, s // 2))

    # key icon on the left, square, inset slightly
    pad = 2 * s
    icon_h = h - pad * 2
    ic = icon.resize((icon_h, icon_h), Image.LANCZOS)
    img.alpha_composite(ic, (pad, pad))

    # name fills the rest
    text_x = pad * 2 + icon_h
    avail = w - text_x - pad
    tw = d.textlength(label, font=font)
    bbox = font.getbbox(label)
    ty = (h - (bbox[3] - bbox[1])) // 2 - bbox[1]
    d.text((text_x + (avail - tw) / 2, ty), label, font=font, fill=TEXT)

    img = img.resize((PLATE_W * 2, PLATE_H * 2), Image.LANCZOS)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path, optimize=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["key", "keyring", "both"], default="both")
    ap.add_argument("--force", action="store_true",
                    help="overwrite plates that already exist (destroys hand-drawn art)")
    args = ap.parse_args()

    kinds = ["key", "keyring"] if args.kind == "both" else [args.kind]
    made = skipped = 0
    for kind in kinds:
        icon = Image.open(os.path.join(PACK, ICON_SRC[kind])).convert("RGBA")
        s_ = SUPERSAMPLE
        pad = 2 * s_
        icon_h = PLATE_H * s_ - pad * 2
        avail = PLATE_W * s_ - (pad * 2 + icon_h) - pad
        font = uniform_font([l for l, _ in LABELS.values()], avail, int(PLATE_H * s_ * 0.52))
        for code, (label, _group) in LABELS.items():
            name = code if kind == "key" else code + "ring"
            fn = re.sub(r"[^a-z0-9_-]", "", name.replace("'", "")) + ".png"
            out = os.path.join(PACK, OUT_DIR[kind], fn)
            if os.path.exists(out) and not args.force:
                skipped += 1
                continue
            make_plate(icon, label, out, font)
            made += 1
    print(f"wrote {made} placeholder nameplates "
          f"({PLATE_W}x{PLATE_H} at 1x, saved at 2x)")
    if skipped:
        print(f"kept {skipped} existing plate(s); pass --force to overwrite them")
    for kind in kinds:
        print(f"  {OUT_DIR[kind]}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
