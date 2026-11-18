#!/usr/bin/env python3
"""Generate deterministic test cases for the PPU testbench (tb_ppu.v).

Everything goes to sim/out/ (git-ignored):
  bg_chr.hex, sp_chr.hex   one CHR set shared by all cases, 2048 x 16 bit
  ncases.hex               number of cases
  case<N>_vram.hex         4096 words, written to 0x0000..0x0FFF
  case<N>_oam.hex          64 words,   written to 0x1000..0x103F
  case<N>_pal.hex          32 words,   written to 0x1040..0x105F
  case<N>_regs.hex         4 words:    scroll A, scroll B, split, ctrl
  cases.json               names, descriptions and expectations

All memory files hold full 32-bit bus words. The random cases put garbage in
the bits the PPU must ignore, so the address/data decoding is tested as well.
"""
import json
import os
import random

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
SEED = 0x05EED5

def spr(x, y, tile, pal=0, hf=0, vf=0, beh=0, big=0):
    """One OAM word, field layout from the ppu.v header."""
    return ((x & 0x1FF) | ((y & 0xFF) << 9) | ((tile & 0xFF) << 17) |
            ((pal & 3) << 25) | ((hf & 1) << 27) | ((vf & 1) << 28) |
            ((beh & 1) << 29) | ((big & 1) << 30))

def scroll(page, y, x):
    return ((page & 1) << 17) | ((y & 0xFF) << 9) | (x & 0x1FF)

def nt(tile, pal=0):
    return ((pal & 3) << 8) | (tile & 0xFF)

def chr_row(pixels):
    """8 colour indices, leftmost first -> 16-bit row, leftmost in [15:14]."""
    v = 0
    for p, c in enumerate(pixels):
        v |= (c & 3) << (14 - 2 * p)
    return v

def rand_tile(rng, p_clear=0.3):
    return [chr_row([0 if rng.random() < p_clear else rng.randint(1, 3)
                     for _ in range(8)]) for _ in range(8)]

def make_chr(rng, solid_base):
    """256 random tiles; four special ones at solid_base..solid_base+3:
    fully transparent, then solid colour 1, 2, 3."""
    tiles = [rand_tile(rng) for _ in range(256)]
    for k in range(4):
        tiles[solid_base + k] = [chr_row([k] * 8)] * 8
    return tiles

BG_CLEAR, BG_SOLID = 0, 3
SP_CLEAR, SP_SOLID1, SP_SOLID2, SP_SOLID3 = 0x40, 0x41, 0x42, 0x43

def distinct_pal(rng, garbage=False):
    vals = rng.sample(range(64), 32)
    if garbage:
        vals = [v | (rng.getrandbits(26) << 6) for v in vals]
    return vals

def rand_vram(rng, garbage=False):
    return [rng.getrandbits(10) | ((rng.getrandbits(22) << 10) if garbage else 0)
            for _ in range(4096)]

def hidden(rng):
    """A sprite that never crosses a visible line: y = 240 for 16x16,
    y = 240..248 for 8x8. Other fields random."""
    if rng.random() < 0.5:
        return spr(rng.randrange(512), 240, rng.randrange(256),
                   rng.randrange(4), rng.randrange(2), rng.randrange(2),
                   rng.randrange(2), 1)
    return spr(rng.randrange(512), rng.randint(240, 248), rng.randrange(256),
               rng.randrange(4), rng.randrange(2), rng.randrange(2),
               rng.randrange(2), 0)

def rand_spr(rng, y_lo=0, y_hi=255, x_lo=0, x_hi=511, beh=None):
    return spr(rng.randint(x_lo, x_hi), rng.randint(y_lo, y_hi),
               rng.randrange(256), rng.randrange(4), rng.randrange(2),
               rng.randrange(2),
               rng.randrange(2) if beh is None else beh, rng.randrange(2))

def fill_oam(rng, oam):
    """Replace None slots with hidden sprites."""
    return [hidden(rng) if o is None else o for o in oam]

def case(name, desc, vram, oam, pal, sa, sb, split, ctrl, expect=None):
    assert len(vram) == 4096 and len(oam) == 64 and len(pal) == 32
    return dict(name=name, desc=desc, vram=vram, oam=oam, pal=pal,
                regs=[sa, sb, split, ctrl], expect=expect or {})

def build_cases(rng):
    cases = []

    cases.append(case(
        "random_a", "all memories and registers random, garbage in unused bits",
        rand_vram(rng, True), [rng.getrandbits(32) for _ in range(64)],
        distinct_pal(rng, True),
        rng.getrandbits(32), rng.getrandbits(32),
        rng.randint(1, 239) | (rng.getrandbits(24) << 8),
        7 | (rng.getrandbits(29) << 3)))

    oam = [rand_spr(rng, 40, 150) for _ in range(64)]
    cases.append(case(
        "random_dense", "random, 64 sprites packed into y 40..150, split 120",
        rand_vram(rng), oam, distinct_pal(rng),
        scroll(0, rng.randrange(256), rng.randrange(512)),
        scroll(1, rng.randrange(256), rng.randrange(512)), 120, 7))

    oam = [None] * 64
    i = 0
    for k in range(15):
        yb = 16 * k + 1
        hf, vf, p = k & 1, (k >> 1) & 1, k & 3
        oam[i + 0] = spr(497 + k, yb + 8, rng.randrange(256), p, hf, vf, 0, 0)
        oam[i + 1] = spr(305 + k, yb + 8, rng.randrange(256), p ^ 1, vf, hf, 0, 0)
        oam[i + 2] = spr(497 + k, yb, rng.randrange(256), p ^ 2, vf, hf, 0, 1)
        oam[i + 3] = spr(305 + k, yb, rng.randrange(256), p ^ 3, hf, vf, 0, 1)
        i += 4
    oam[60] = spr(0, 120, SP_SOLID1, 1)        # exactly at the left edge
    oam[61] = spr(319, 130, SP_SOLID2, 2, big=1)  # one column visible
    oam[62] = spr(320, 140, SP_SOLID3, 3)      # fully off the right
    oam[63] = spr(496, 150, SP_SOLID1, 0, big=1)  # 496..511: fully hidden
    cases.append(case(
        "sprite_x_wrap", "sprites at x 497..511 (wrap in from left) and "
        "305..319 (clipped right), both sizes, flips; x=0/319/320/496 edges",
        rand_vram(rng), fill_oam(rng, oam), distinct_pal(rng),
        scroll(0, rng.randrange(256), rng.randrange(512)),
        scroll(1, rng.randrange(256), rng.randrange(512)),
        rng.randint(1, 239), 7, expect={"overflow": 0}))

    oam = [None] * 64
    oam[0] = spr(50, 240, SP_SOLID1, 0, big=1)
    for j in range(9):                          # 8x8 at y 240..248: hidden
        oam[1 + j] = spr(30 + 30 * j, 240 + j, SP_SOLID2, 1)
    for j in range(8):                          # 16x16 at y 241..248: visible
        oam[10 + j] = spr(8 + 38 * j, 241 + j, rng.randrange(256), j & 3,
                          j & 1, (j >> 1) & 1, 0, 1)
    cases.append(case(
        "sprite_y_wrap_hidden", "16x16 at y 241..248 wrap in at the top (8 on "
        "line 0); decoys at OAM 0..9 (y 240, 8x8 at 240..248) must not take slots",
        rand_vram(rng), fill_oam(rng, oam), distinct_pal(rng),
        scroll(0, 0, 0), scroll(0, 0, 0), 0, 7, expect={"overflow": 0}))

    oam = [None] * 64
    for j in range(7):
        oam[j] = spr(10 + 44 * j, 249 + j, rng.randrange(256), j & 3,
                     j & 1, 0, 0, 0)
        oam[7 + j] = spr(16 + 44 * j, 249 + j, rng.randrange(256), (j + 1) & 3,
                         0, j & 1, 0, 1)
    for j in range(8):
        oam[14 + j] = spr(4 + 38 * j, 241 + j, rng.randrange(256), (j + 2) & 3,
                          j & 1, (j >> 1) & 1, 0, 1)
    cases.append(case(
        "sprite_y_wrap_overflow", "8x8 and 16x16 at y 249..255 plus 16x16 at "
        "241..248: up to 22 on the top lines, lowest 8 OAM indices win",
        rand_vram(rng), fill_oam(rng, oam), distinct_pal(rng),
        scroll(1, 100, 200), scroll(0, 0, 0), 240, 7, expect={"overflow": 1}))

    oam = [None] * 64
    for n, idx in enumerate([0, 1, 3, 4, 6, 7, 8, 10]):   # exactly 8 at 40..55
        oam[idx] = spr(rng.randrange(300), 40, rng.randrange(256), n & 3,
                       n & 1, (n >> 1) & 1, 0, 1)
    for n, idx in enumerate([2, 5, 9, 13, 17, 21, 26, 30, 34, 39, 44, 50]):
        oam[idx] = spr(24 * n + 2, 100, SP_SOLID1 + (n % 3), n & 3)  # 12 on 100..107
    for idx, xx in ((11, 330), (12, 400), (14, 480)):      # 3 invisible thieves
        oam[idx] = spr(xx, 150, SP_SOLID1, 0, big=1)
    for n, idx in enumerate([15, 16, 18, 19, 20, 22, 23, 24]):
        oam[idx] = spr(10 + 36 * n, 150, rng.randrange(256), n & 3,
                       0, 0, 0, 1)                         # only 5 fit
    cases.append(case(
        "overflow_9plus", "12 sprites on lines 100..107, exactly 8 on 40..55, "
        "x-offscreen sprites taking 3 of the 8 slots on 150..165",
        rand_vram(rng), fill_oam(rng, oam), distinct_pal(rng),
        scroll(0, rng.randrange(256), rng.randrange(512)),
        scroll(0, 0, 0), 240, 7, expect={"overflow": 1}))

    oam = []
    cols = [4, 43, 82, 121, 160, 199, 310, 505]   # 310 clips, 505 wraps
    for k in range(8):
        for c in range(8):
            oam.append(spr(cols[c], 2 * c + 16 * k, rng.randrange(256),
                           rng.randrange(4), rng.randrange(2),
                           rng.randrange(2), 0, 1))
    cases.append(case(
        "exactly_8_per_line", "8 columns x 8 stacked 16x16 sprites: exactly 8 "
        "on every line 14..127, never 9 (overflow must stay 0)",
        rand_vram(rng), oam, distinct_pal(rng),
        scroll(1, rng.randrange(256), rng.randrange(512)),
        scroll(0, 0, 0), 240, 7, expect={"overflow": 0}))

    oam = []
    tiles = [0x0F, 0xEF, 0xFF, 0xF0, 0x00, 0x7F, 0x8E, rng.randrange(256)]
    for r in range(8):
        for c in range(8):
            oam.append(spr(4 + 39 * c, 2 + 29 * r, tiles[r], (r + c) & 3,
                           c & 1, (c >> 1) & 1, 0, c >> 2))
    vram = [nt(BG_CLEAR, rng.randrange(4)) for _ in range(4096)]
    cases.append(case(
        "flips_tiles", "8 rows (tiles 0F EF FF F0 00 7F 8E rnd) x 8 columns "
        "(8x8/16x16 x none/h/v/hv) over a clear background",
        vram, oam, distinct_pal(rng),
        scroll(0, 0, 0), scroll(0, 0, 0), 240, 7, expect={"overflow": 0}))

    vram = [nt(rng.randrange(256), rng.randrange(4)) for _ in range(4096)]
    for row in range(16):                       # lines 0..127
        for col in range(64):
            vram[(row << 6) | col] = nt(BG_SOLID if col < 20 else BG_CLEAR,
                                        rng.randrange(4))
    oam = [None] * 64
    oam[0] = spr(140, 8, SP_SOLID1, 0, big=1)
    oam[1] = spr(148, 8, SP_SOLID2, 1, big=1)
    oam[2] = spr(200, 8, rng.randrange(256), 2)
    oam[3] = spr(204, 8, rng.randrange(256), 3)
    oam[4] = spr(140, 30, SP_SOLID1, 2, beh=1, big=1)   # over solid BG
    oam[5] = spr(148, 30, SP_SOLID3, 3, big=1)
    oam[6] = spr(200, 30, SP_SOLID2, 0, beh=1, big=1)   # over clear BG
    oam[7] = spr(206, 30, SP_SOLID1, 1, big=1)
    oam[8] = spr(150, 52, rng.randrange(256), 1, beh=1, big=1)
    oam[9] = spr(40, 52, SP_SOLID3, 2, beh=1)
    oam[10] = spr(250, 52, rng.randrange(256), 3, beh=1)
    oam[11] = spr(152, 74, SP_SOLID1, 0, beh=1, big=1)
    oam[12] = spr(156, 74, SP_SOLID2, 1, big=1)
    oam[13] = spr(150, 74, SP_SOLID3, 2, beh=1, big=1)
    oam[14] = spr(60, 96, rng.randrange(256), 0, 1, 0, 1, 1)
    oam[15] = spr(64, 96, rng.randrange(256), 1, 0, 1, 0, 1)
    oam[16] = spr(220, 96, rng.randrange(256), 2, 0, 0, 1, 1)
    oam[17] = spr(224, 96, rng.randrange(256), 3, 1, 1, 0, 1)
    for idx in range(18, 50):
        oam[idx] = rand_spr(rng, 128, 230, 0, 330)
    cases.append(case(
        "priority_behind", "behind sprites over solid (x<160) vs clear BG; "
        "front/front, behind-low over front-high, behind/front/behind "
        "overlaps; random mix on lines 128..239",
        vram, fill_oam(rng, oam), distinct_pal(rng),
        scroll(0, 0, 0), scroll(1, 77, 333), 240, 7))

    oam = [rand_spr(rng) if k < 24 else None for k in range(64)]
    cases.append(case(
        "split0_scrollB_wrap", "split 0: every line uses B = page 1, y 250, "
        "x 505 (wraps both ways); A is a decoy",
        rand_vram(rng), fill_oam(rng, oam), distinct_pal(rng),
        scroll(0, 37, 100), scroll(1, 250, 505), 0, 7))

    oam = [None] * 64
    for n in range(12):
        oam[n] = spr(25 * n, 50, SP_SOLID1, n & 3, big=n & 1)
    for n in range(12, 40):
        oam[n] = rand_spr(rng)
    cases.append(case(
        "split240_sprites_off", "split 240: every line uses A = page 0, y 255, "
        "x 511; ctrl = display+BG only, so no sprites and no overflow",
        rand_vram(rng), fill_oam(rng, oam), distinct_pal(rng),
        scroll(0, 255, 511), scroll(1, 3, 3), 240, 3, expect={"overflow": 0}))

    oam = [rand_spr(rng, 0, 239, 0, 330) if k < 30 else None for k in range(64)]
    cases.append(case(
        "split_mid_pages", "split 97: A = page 1 (x 3, y 17), B = page 0 "
        "(x 300, y 200)",
        rand_vram(rng), fill_oam(rng, oam), distinct_pal(rng),
        scroll(1, 17, 3), scroll(0, 200, 300), 97, 7))

    vram = [nt(BG_SOLID, rng.randrange(4)) for _ in range(4096)]
    oam = [None] * 64
    oam[0] = spr(40, 20, SP_SOLID1, 0, beh=1, big=1)
    oam[1] = spr(48, 24, SP_SOLID2, 1, big=1)
    oam[2] = spr(100, 20, rng.randrange(256), 2, beh=1)
    oam[3] = spr(104, 22, rng.randrange(256), 3, beh=1, big=1)
    for idx in range(4, 40):
        oam[idx] = rand_spr(rng, 0, 239, 0, 511)
    cases.append(case(
        "bg_off_behind", "ctrl = display+sprites: backdrop (pal 0) everywhere, "
        "'behind' sprites drawn because the BG is never opaque",
        vram, fill_oam(rng, oam), distinct_pal(rng),
        scroll(0, 0, 0), scroll(0, 0, 0), 240, 5))

    oam = [None] * 64
    for n in range(10):
        oam[n] = spr(30 * n, 60, SP_SOLID3, n & 3)
    for n in range(10, 40):
        oam[n] = rand_spr(rng)
    cases.append(case(
        "display_off", "ctrl = BG+sprites, display off: black frame; 10 "
        "sprites on line 60 still set the overflow flag",
        rand_vram(rng), fill_oam(rng, oam), distinct_pal(rng),
        scroll(0, 5, 5), scroll(0, 5, 5), 240, 6, expect={"overflow": 1}))

    return cases

def write_words(path, words, digits):
    with open(path, "w") as f:
        for w in words:
            f.write("%0*x\n" % (digits, w))

def main():
    rng = random.Random(SEED)
    os.makedirs(OUT, exist_ok=True)

    bg = make_chr(rng, 0)
    bg[4] = [chr_row([3, 3, 3, 3, 0, 0, 0, 0])] * 8
    sp = make_chr(rng, SP_CLEAR)
    write_words(os.path.join(OUT, "bg_chr.hex"), [r for t in bg for r in t], 4)
    write_words(os.path.join(OUT, "sp_chr.hex"), [r for t in sp for r in t], 4)

    cases = build_cases(rng)
    for n, c in enumerate(cases):
        base = os.path.join(OUT, "case%d_" % n)
        write_words(base + "vram.hex", c["vram"], 8)
        write_words(base + "oam.hex", c["oam"], 8)
        write_words(base + "pal.hex", c["pal"], 8)
        write_words(base + "regs.hex", c["regs"], 8)
    write_words(os.path.join(OUT, "ncases.hex"), [len(cases)], 8)
    with open(os.path.join(OUT, "cases.json"), "w") as f:
        json.dump([dict(name=c["name"], desc=c["desc"], expect=c["expect"])
                   for c in cases], f, indent=1)
    print("gen_ppu_cases: %d cases written to %s" % (len(cases), OUT))

if __name__ == "__main__":
    main()
