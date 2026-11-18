#!/usr/bin/env python3
"""
check_demo.py - check the demo's frames against the PPU golden model.

tb_demo.v captures frames off the Tiny VGA pins (out/<name>.hex) and, for
each one, dumps the PPU state the frame was built from (out/<name>_vram.hex,
_oam.hex, _pal.hex, _regs.hex) and the overflow flag the PPU raised for it
(out/demo_results.txt). Here ppu_model.py renders that state with the real
cartridge tiles (src/chr_bg.mem, src/chr_spr.mem) and every pixel, and the
overflow flag, must match.

    python3 sim/check_demo.py
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ppu_model as pm  # noqa: E402

OUT = os.path.join(HERE, "out")
SRC = os.path.join(HERE, "..", "src")

def words(path):
    with open(path) as f:
        return [int(t, 16) for t in f.read().split()]

def main():
    bgchr = words(os.path.join(SRC, "chr_bg.mem"))
    spchr = words(os.path.join(SRC, "chr_spr.mem"))
    with open(os.path.join(OUT, "demo_results.txt")) as f:
        results = [ln.split() for ln in f if ln.strip()]
    if not results:
        print("FAIL no frames recorded")
        return 1
    fails = 0
    for name, ovf in results:
        base = os.path.join(OUT, name + "_")
        regs = words(base + "regs.hex")
        cs = dict(vram=[w & 0x3FF for w in words(base + "vram.hex")],
                  oam=words(base + "oam.hex"),
                  pal=[w & 0x3F for w in words(base + "pal.hex")],
                  sa=regs[0] & 0x3FFFF, sb=regs[1] & 0x3FFFF,
                  split=regs[2] & 0xFF, ctrl=regs[3] & 7,
                  bgchr=bgchr, spchr=spchr)
        model, m_ovf, stats = pm.render(cs)
        rtl = np.array(words(os.path.join(OUT, name + ".hex"))).reshape(pm.H, pm.W)
        bad = rtl != model
        n = int(bad.sum())
        ok_ovf = int(ovf) == int(m_ovf)
        if n or not ok_ovf:
            fails += 1
            msg = f"FAIL {name}: {n} pixels differ"
            if n:
                ys, xs = np.nonzero(bad)
                msg += f", first at ({xs[0]},{ys[0]}) rtl {rtl[ys[0], xs[0]]:02x} " \
                       f"model {model[ys[0], xs[0]]:02x}"
            if not ok_ovf:
                msg += f"; overflow rtl {ovf} model {int(m_ovf)}"
            print(msg)
            pm.save_png(os.path.join(OUT, name + "_cmp.png"), rtl, model, bad)
        else:
            print(f"ok   {name}: every pixel matches the model, overflow {ovf} "
                  f"({stats['lines_over_8']} lines with more than 8 sprites)")
    print("PASS" if not fails else f"FAILED: {fails} frame(s)")
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
