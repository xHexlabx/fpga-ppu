#!/usr/bin/env python3
"""
frames_to_png.py - turn the testbench's frame dumps into pictures.

Each sim/out/<name>.hex is 320x240 lines of one RGB222 value (as read off the
Tiny VGA pins). Every frame is saved as sim/out/<name>.png at 2x, and all of
them side by side as sim/out/sheet.png.

    python3 sim/frames_to_png.py            # every .hex in sim/out
    python3 sim/frames_to_png.py a b        # just these names
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
W, H = 320, 240

def rgb222(v):
    v = np.asarray(v, dtype=np.uint16)
    lv = np.array([0, 85, 170, 255], dtype=np.uint8)
    return np.stack([lv[(v >> 4) & 3], lv[(v >> 2) & 3], lv[v & 3]], axis=-1)

def load(name):
    with open(os.path.join(OUT, name + ".hex")) as f:
        vals = [int(line, 16) for line in f if line.strip()]
    if len(vals) != W * H:
        raise SystemExit(f"{name}.hex: {len(vals)} pixels, expected {W * H}")
    return rgb222(np.array(vals).reshape(H, W))

def scale(a, k):
    """Nearest-neighbour upscale (PIL's resize is broken on this machine)."""
    return np.repeat(np.repeat(a, k, axis=0), k, axis=1)

def main(names):
    if not names:
        names = sorted(n[:-4] for n in os.listdir(OUT) if n.endswith(".hex")
                       and not n.startswith(("case", "bg_chr", "sp_chr", "ncases"))
                       and not n[:-4].endswith(("_vram", "_oam", "_pal", "_regs", "_rtl")))
    pics = []
    for n in names:
        a = load(n)
        Image.fromarray(scale(a, 2)).save(os.path.join(OUT, n + ".png"))
        pics.append((n, Image.fromarray(a)))
    if not pics:
        return
    cols = 3
    rows = (len(pics) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (W + 8), rows * (H + 20)), (40, 40, 40))
    d = ImageDraw.Draw(sheet)
    for k, (n, img) in enumerate(pics):
        x, y = (k % cols) * (W + 8), (k // cols) * (H + 20)
        sheet.paste(img, (x, y + 16))
        d.text((x + 2, y + 2), n, fill=(255, 255, 255))
    sheet.save(os.path.join(OUT, "sheet.png"))
    print(f"wrote {len(pics)} frames and sheet.png to {OUT}")

if __name__ == "__main__":
    main(sys.argv[1:])
