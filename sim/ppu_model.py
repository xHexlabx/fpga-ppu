#!/usr/bin/env python3
"""Golden model of the PPU (src/ppu.v header = spec) and RTL checker.

  python3 ppu_model.py            check every case in out/ against the RTL dumps
  python3 ppu_model.py --png-all  also save side-by-side PNGs of passing cases

The model is written from the spec, not from the RTL's pass structure:
  * line r uses scroll A if r < split else scroll B; map coordinates wrap at
    512 x 256 inside the selected page
  * BG pixel = pal[4*palette + ci] if BG on and ci != 0, else backdrop pal[0]
  * sprite evaluation: OAM 0..63 in order, a sprite is on line r when
    (r - y) mod 256 < height; the first 8 are kept, any further one sets the
    overflow flag (only while sprites are enabled)
  * sprite pixel x = (sprite x + column) mod 512, drawn only if x < 320
  * final pixel = the lowest-index selected sprite whose pixel is opaque and
    not (behind && BG pixel opaque); otherwise the BG pixel
  * display off -> 0 (black) everywhere; overflow is still computed

Reads out/tb_results.txt and out/case<N>_rtl.hex, writes out/case<N>_cmp.png
for failing cases. Exits non-zero on any failure.
"""
import json
import os
import sys

import numpy as np
from PIL import Image

OUT = os.environ.get("PPU_SIM_OUT") or \
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
W, H = 320, 240

def read_words(path):
    with open(path) as f:
        return [int(t, 16) for t in f.read().split()]

def load_chr():
    return read_words(os.path.join(OUT, "bg_chr.hex")), \
        read_words(os.path.join(OUT, "sp_chr.hex"))

def load_case(n, bgchr, spchr):
    base = os.path.join(OUT, "case%d_" % n)
    vram = [w & 0x3FF for w in read_words(base + "vram.hex")]
    oam = read_words(base + "oam.hex")
    pal = [w & 0x3F for w in read_words(base + "pal.hex")]
    regs = read_words(base + "regs.hex")
    assert len(vram) == 4096 and len(oam) == 64 and len(pal) == 32
    return dict(vram=vram, oam=oam, pal=pal,
                sa=regs[0] & 0x3FFFF, sb=regs[1] & 0x3FFFF,
                split=regs[2] & 0xFF, ctrl=regs[3] & 7,
                bgchr=bgchr, spchr=spchr)

def decode_spr(o):
    return dict(x=o & 0x1FF, y=(o >> 9) & 0xFF, tile=(o >> 17) & 0xFF,
                pal=(o >> 25) & 3, hf=(o >> 27) & 1, vf=(o >> 28) & 1,
                beh=(o >> 29) & 1, big=(o >> 30) & 1)

def ci_of(row_bits, p):
    return (row_bits >> (14 - 2 * p)) & 3

def bg_pixel(cs, r, x):
    """-> (colour, opaque, info dict) for background pixel x of line r."""
    s = cs["sa"] if r < cs["split"] else cs["sb"]
    page, sy, sx = s >> 17, (s >> 9) & 0xFF, s & 0x1FF
    wy = (r + sy) & 0xFF
    wx = (x + sx) & 0x1FF
    addr = (page << 11) | ((wy >> 3) << 6) | (wx >> 3)
    e = cs["vram"][addr]
    tile, bpal = e & 0xFF, e >> 8
    ci = ci_of(cs["bgchr"][(tile << 3) | (wy & 7)], wx & 7)
    bg_on = (cs["ctrl"] >> 1) & 1
    opaque = bool(bg_on and ci)
    colour = cs["pal"][(bpal << 2) | ci] if opaque else cs["pal"][0]
    info = dict(scroll="A" if r < cs["split"] else "B", page=page, wx=wx, wy=wy,
                vaddr=addr, tile=tile, bpal=bpal, ci=ci, bg_on=bg_on)
    return colour, opaque, info

def select_sprites(cs, r):
    """-> (list of up to 8 OAM indices on line r, overflow on this line)."""
    if not (cs["ctrl"] >> 2) & 1:
        return [], False
    sel, ovf = [], False
    for i in range(64):
        s = decode_spr(cs["oam"][i])
        if ((r - s["y"]) & 0xFF) < (16 if s["big"] else 8):
            if len(sel) < 8:
                sel.append(i)
            else:
                ovf = True
    return sel, ovf

def spr_pixel(cs, i, r, x):
    """Pixel of sprite i at screen (x, r): None if outside, else
    (ci, colour, detail)."""
    s = decode_spr(cs["oam"][i])
    size = 16 if s["big"] else 8
    dy = (r - s["y"]) & 0xFF
    p = (x - s["x"]) & 0x1FF
    if dy >= size or p >= size:
        return None
    row = size - 1 - dy if s["vf"] else dy
    col = size - 1 - p if s["hf"] else p
    tile = (s["tile"] + (16 if row >= 8 else 0) + (1 if col >= 8 else 0)) & 0xFF
    ci = ci_of(cs["spchr"][(tile << 3) | (row & 7)], col & 7)
    colour = cs["pal"][16 + (s["pal"] << 2) + ci]
    return ci, colour, dict(row=row, col=col, tile=tile)

def render(cs):
    """-> (img uint8 [240][320] of RGB222, overflow flag, per-line stats)."""
    img = np.zeros((H, W), dtype=np.uint8)
    overflow = False
    n8 = n9 = 0
    disp = cs["ctrl"] & 1
    for r in range(H):
        line, solid = [0] * W, [False] * W
        for x in range(W):
            line[x], solid[x], _ = bg_pixel(cs, r, x)
        sel, ovf = select_sprites(cs, r)
        overflow |= ovf
        n8 += (len(sel) == 8 and not ovf)
        n9 += ovf
        taken = [False] * W
        for i in sel:                      # priority order: lowest index first
            s = decode_spr(cs["oam"][i])
            size = 16 if s["big"] else 8
            for p in range(size):
                x = (s["x"] + p) & 0x1FF
                if x >= W or taken[x]:
                    continue
                ci, colour, _ = spr_pixel(cs, i, r, x)
                if ci == 0 or (s["beh"] and solid[x]):
                    continue
                line[x] = colour
                taken[x] = True
        if disp:
            img[r] = line
    return img, overflow, dict(lines_with_8=n8, lines_over_8=n9)

def explain(cs, x, r, rtl_val):
    """Human-readable account of what the spec says pixel (x, r) should be."""
    out = []
    if not cs["ctrl"] & 1:
        return ["display off: expected 0"]
    colour, opaque, bi = bg_pixel(cs, r, x)
    out.append("BG: scroll %s page %d map (%d,%d) vram[0x%03x] tile 0x%02x pal %d "
               "ci %d -> %s 0x%02x" % (bi["scroll"], bi["page"], bi["wx"], bi["wy"],
                                     bi["vaddr"], bi["tile"], bi["bpal"], bi["ci"],
                                     "opaque" if opaque else
                                     ("backdrop (BG off)" if not bi["bg_on"] else
                                      "backdrop"), colour))
    sel, ovf = select_sprites(cs, r)
    out.append("line %d sprites (first 8): %s%s" %
               (r, sel, " + overflow" if ovf else ""))
    for i in range(64):
        px = spr_pixel(cs, i, r, x)
        if px is None:
            continue
        s = decode_spr(cs["oam"][i])
        ci, scol, d = px
        state = ("selected" if i in sel else "NOT selected (9th+)")
        if ci == 0:
            why = "transparent"
        elif s["beh"] and opaque:
            why = "behind, hidden by opaque BG"
        else:
            why = "opaque -> 0x%02x" % scol
        out.append("  OAM %d %s: x %d y %d %s hf %d vf %d beh %d tile 0x%02x "
                   "-> row %d col %d subtile 0x%02x ci %d, %s" %
                   (i, state, s["x"], s["y"], "16x16" if s["big"] else "8x8",
                    s["hf"], s["vf"], s["beh"], s["tile"], d["row"], d["col"],
                    d["tile"], ci, why))
    guesses = []
    if rtl_val == cs["pal"][0]:
        guesses.append("backdrop pal[0]")
    for k in range(1, 32):
        if rtl_val == cs["pal"][k] and k & 3:
            guesses.append("pal[%d] (%s palette %d ci %d)" %
                           (k, "BG" if k < 16 else "sprite", (k >> 2) & 3, k & 3))
    out.append("RTL value 0x%02x matches: %s" %
               (rtl_val, ", ".join(guesses) if guesses else "nothing in the palette"))
    return out

def to_rgb(img):
    v = img.astype(np.uint16)
    rgb = np.stack([(v >> 4) & 3, (v >> 2) & 3, v & 3], axis=-1) * 85
    return rgb.astype(np.uint8)

def save_png(path, rtl, model, bad):
    gap = np.full((H, 4, 3), 128, dtype=np.uint8)
    diff = (to_rgb(model) // 4)
    diff[bad] = (255, 0, 0)
    strip = np.concatenate([to_rgb(rtl), gap, to_rgb(model), gap, diff], axis=1)
    strip = np.repeat(np.repeat(strip, 2, axis=0), 2, axis=1)   # 2x, nearest
    Image.fromarray(np.ascontiguousarray(strip)).save(path)

def read_rtl(path):
    img = np.zeros((H, W), dtype=np.int16)
    with open(path) as f:
        rows = f.read().split("\n")
    for r in range(H):
        toks = rows[r].split() if r < len(rows) else []
        for x in range(W):
            try:
                img[r, x] = int(toks[x], 16)
            except (IndexError, ValueError):   # missing or x/z
                img[r, x] = -1
    return img

def read_results():
    res, glob = {}, None
    path = os.path.join(OUT, "tb_results.txt")
    if not os.path.exists(path):
        return res, glob
    with open(path) as f:
        for line in f:
            t = line.split()
            if not t:
                continue
            kv = {t[k]: int(t[k + 1]) for k in range(1 if t[0] == "global" else 2,
                                                     len(t) - 1, 2)}
            if t[0] == "case":
                res[int(t[1])] = kv
            elif t[0] == "global":
                glob = dict(glob or {}, **kv)
    return res, glob

def main():
    png_all = "--png-all" in sys.argv
    with open(os.path.join(OUT, "cases.json")) as f:
        meta = json.load(f)
    bgchr, spchr = load_chr()
    res, glob = read_results()
    failures = 0

    for n, m in enumerate(meta):
        cs = load_case(n, bgchr, spchr)
        model, movf, stats = render(cs)
        problems = []

        exp = m.get("expect", {})
        if "overflow" in exp and exp["overflow"] != int(movf):
            problems.append("generator expected overflow %d but the model says %d "
                            "(test case does not do what it claims)" %
                            (exp["overflow"], movf))

        rtl_path = os.path.join(OUT, "case%d_rtl.hex" % n)
        r = res.get(n)
        if r is None or not os.path.exists(rtl_path):
            problems.append("no RTL result (testbench did not finish this case)")
            rtl = None
        else:
            rtl = read_rtl(rtl_path)
            if r["overflow"] != int(movf):
                problems.append("sprite_overflow: RTL %d, model %d" %
                                (r["overflow"], movf))
            if r["nonuniform"]:
                problems.append("%d 2x2 blocks not uniform" % r["nonuniform"])
            if r["de_count"] != 640 * 480:
                problems.append("de was high %d clocks in the frame, not 307200" %
                                r["de_count"])
            if r["readback_err"]:
                problems.append("%d VRAM read-back errors" % r["readback_err"])
            if r["late_writes"]:
                problems.append("bus writes did not finish before line 523")

        mism = 0
        if rtl is not None:
            bad = rtl != model.astype(np.int16)
            mism = int(bad.sum())
            if mism:
                ys, xs = np.nonzero(bad)
                problems.append("%d pixel mismatches (first at x=%d y=%d)" %
                                (mism, xs[0], ys[0]))
            if mism or png_all:
                save_png(os.path.join(OUT, "case%d_cmp.png" % n),
                         np.clip(rtl, 0, 63).astype(np.uint8), model, bad)

        status = "FAIL" if problems else "PASS"
        print("[%s] case %2d %-24s overflow %d, lines with 8: %3d, lines with 9+: %3d"
              % (status, n, m["name"], int(movf), stats["lines_with_8"],
                 stats["lines_over_8"]))
        for p in problems:
            print("        " + p)
        if mism:
            ys, xs = np.nonzero(bad)
            for k in range(min(4, mism)):
                x, y = int(xs[k]), int(ys[k])
                print("        mismatch at x=%d y=%d: RTL 0x%02x, model 0x%02x" %
                      (x, y, rtl[y, x] & 0xFF, model[y, x]))
                for line in explain(cs, x, y, int(rtl[y, x])):
                    print("          " + line)
            print("        side-by-side (RTL | model | diff): out/case%d_cmp.png" % n)
        failures += bool(problems)

    print()
    if glob is None:
        print("[FAIL] no global line in tb_results.txt (testbench did not finish)")
        failures += 1
    else:
        gp = []
        if glob.get("timeout"):
            gp.append("testbench timed out")
        if glob.get("sync_err", 1):
            gp.append("%d sync/de errors (see vvp log)" % glob.get("sync_err", -1))
        if glob.get("build_err", 1):
            gp.append("%d line-build overruns (see vvp log)" % glob.get("build_err", -1))
        if glob.get("frames_checked", 0) < len(meta):
            gp.append("only %d frames had their sync checked" %
                      glob.get("frames_checked", 0))
        if glob.get("max_build", 9999) >= 1600:
            gp.append("worst line build %d clocks >= 1600" % glob.get("max_build"))
        print("[%s] timing: %d frames checked, sync errors %d, build errors %d, "
              "line build %d..%d clocks (limit 1600) over %d lines" %
              ("FAIL" if gp else "PASS", glob.get("frames_checked", 0),
               glob.get("sync_err", -1), glob.get("build_err", -1),
               glob.get("min_build", -1), glob.get("max_build", -1),
               glob.get("builds", 0)))
        for p in gp:
            print("        " + p)
        failures += bool(gp)

    print("\n%s: %d of %d checks failed" %
          ("FAIL" if failures else "PASS", failures, len(meta) + 1))
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
