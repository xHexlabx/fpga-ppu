#!/usr/bin/env python3
"""gen_assets.py - the "cartridge ROM" data for project05_ppu (PPU tech demo).

On a 3rd-generation console the graphics live on the cartridge: pattern ROMs
with the 8x8 tiles, plus whatever tile map and palette the game program copies
into the PPU at power-on. This script draws all of that from scratch (original
art, nothing ripped from any existing game) and writes it as $readmemh files
into src/, plus PNG previews into images/ for the README.

Run from project05_ppu/:   python3 scripts/gen_assets.py
Needs Python 3 with numpy and Pillow. Fully deterministic (no randomness
except a fixed-seed RNG), so re-running gives byte-identical output.

------------------------------------------------------------------------------
Output files (all plain $readmemh text, one hex word per line, no comments)
------------------------------------------------------------------------------
src/palette.mem    32 lines x 2 hex digits.
                   Line i = palette RAM entry i, a 6-bit RGB222 colour,
                   value = R*16 + G*4 + B with each channel 0..3.
                   0..15 = background palettes 0..3 (4 entries each),
                   16..31 = sprite palettes 4..7. Entry 4p+0 is colour index
                   0 of palette p, which is transparent and never displayed;
                   entry 0 is the exception: it is the backdrop colour that
                   shows wherever nothing opaque is drawn. Unused entries = 00.

src/chr_bg.mem     2048 lines x 4 hex digits: the background pattern ROM.
src/chr_spr.mem    2048 lines x 4 hex digits: the sprite pattern ROM.
                   256 tiles of 8x8 pixels, 2 bits per pixel.
                   Line index = tile*8 + row (row 0 = top).
                   Each line is one tile row of 8 pixels; pixel p (0 = leftmost)
                   is the colour index in bits [15-2p : 14-2p], so the
                   leftmost pixel is in bits [15:14].
                   Colour index 0 = transparent, 1..3 pick palette entries.

src/demo_map.mem   4096 lines x 3 hex digits: the VRAM image the demo copies
                   into the PPU at power-on.
                   Line index = page*2048 + row*64 + col
                   (page 0..1, row 0..31, col 0..63).
                   Entry = (palette << 8) | tile, i.e. bits [9:8] = background
                   palette 0..3, bits [7:0] = tile number in chr_bg.

images/*.png       previews (not used by the hardware):
                   chr_bg.png       BG pattern ROM, 16 tiles per row, each tile
                                    drawn in the palette it is mostly used with
                   chr_spr.png      sprite pattern ROM in palette 4, plus every
                                    sprite frame in all four sprite palettes
                   world_page0.png  VRAM page 0 rendered (512x256), scale 2
                   hud_page1.png    VRAM page 1 rows 0-1 (the HUD), scale 2

------------------------------------------------------------------------------
Tile allocation
------------------------------------------------------------------------------
chr_bg:   0x00          blank (all colour index 0): open sky
          0x20..0x5F    ASCII 0x20..0x5F from the Lat15-VGA8 console font,
                        ink = colour index 3, paper = 1 (so with palette 1 it
                        is white on navy; 0x20 is a solid navy block)
          0x01..0x1F,
          0x60..0xFF    scenery, cut out of the painted world (below)
chr_spr:  16x16 sprite at tile T uses T, T+1 (top row), T+16, T+17 (bottom)
          0x00..0x1F    creatures k = 0..3, frame f = 0..1 at T = k*4 + f*2:
                        k0 sprout blob (round / squashed), k1 bird
                        (wing up / down), k2 quadcopter drone (rotor blur,
                        blinking light), k3 jellyfish (tentacles sway).
                        All face right; the demo h-flips them.
          0x40..0x4F    8x8 items t = 0..3, frame f = 0..3 at 0x40 + t*4 + f:
                        t0 star twinkle, t1 gem shine, t2 arrow (points up,
                        a light pulse travels up it), t3 spinning coin
          rest          blank
Sprite colour roles, so every sprite reads in all four sprite palettes:
          ci1 = dark outline, ci2 = main body, ci3 = light highlight/eye white.

------------------------------------------------------------------------------
How the world is made
------------------------------------------------------------------------------
Page 0 (512x256 px) is painted as a picture in real colours (RGB222 values,
-1 = transparent), back layer first. Then it is cut into 8x8 cells. For each
cell the script finds the background palette that contains every colour in
it (an 8x8 cell can only use ONE palette - the same "attribute" limit the
real consoles had), turns the pixels into colour indices, and deduplicates
the resulting patterns into tiles. Cells that match a font glyph reuse the
font tile (sign text is painted with the font, so it comes out as ASCII
tiles). The painter wraps x at 512, so the left and right edges join.
"""

import gzip
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

PROJ = Path(__file__).resolve().parent.parent
SRC_DIR = PROJ / "src"
IMG_DIR = PROJ / "images"
FONT_PATH = Path("/usr/share/consolefonts/Lat15-VGA8.psf.gz")

WORLD_W, WORLD_H = 512, 256          # one VRAM page: 64 x 32 tiles of 8x8
MAP_COLS, MAP_ROWS = 64, 32

PALETTE = [
    0x1B, 0x04, 0x1C, 0x24,      # sky | dark green, green, brown
    0x00, 0x01, 0x2A, 0x3F,      # - | navy, grey, white
    0x00, 0x10, 0x24, 0x39,      # - | dark red-brown, brown, tan
    0x00, 0x02, 0x07, 0x0B,      # - | deep blue, blue, light blue
    0x00, 0x11, 0x36, 0x3F,      # pal4: purple / pink / white
    0x00, 0x14, 0x38, 0x3C,      # pal5: olive / orange / yellow (yellow is
    0x00, 0x01, 0x0F, 0x3F,      # pal6: navy / cyan / white
    0x00, 0x04, 0x2C, 0x3E,      # pal7: dark green / lime / pale yellow
]
assert len(PALETTE) == 32 and all(0 <= c < 64 for c in PALETTE)

BG_PAL = [[PALETTE[4 * p + ci] for ci in range(4)] for p in range(4)]

def C(p, ci):
    return BG_PAL[p][ci]

TRANSP = -1
DKGREEN, GREEN, BROWN = C(0, 1), C(0, 2), C(0, 3)
NAVY, GREY, WHITE = C(1, 1), C(1, 2), C(1, 3)
BRK_DK, BRK, BRK_LT = C(2, 1), C(2, 2), C(2, 3)

def rgb888(c):
    """RGB222 colour value -> (r, g, b) 0..255 for the previews."""
    return ((c >> 4) & 3) * 85, ((c >> 2) & 3) * 85, (c & 3) * 85

def load_psf1(path):
    """Return (glyphs, unicode_map) of a PSF1 font.

    glyphs: list of `charsize`-byte bitmaps, one byte per row, MSB = left.
    unicode_map: {codepoint: glyph index}, from the font's unicode table.
    PSF1 table format: for each glyph, a list of little-endian u16
    codepoints, optionally 0xFFFE followed by combining sequences, then
    0xFFFF to end that glyph's entry.
    """
    raw = gzip.open(path, "rb").read()
    if raw[:2] != b"\x36\x04":
        sys.exit(f"{path}: not a PSF1 font")
    mode, charsize = raw[2], raw[3]
    if charsize != 8:
        sys.exit(f"{path}: charsize {charsize}, want an 8x8 font")
    nglyph = 512 if (mode & 0x01) else 256
    glyphs = [raw[4 + i * charsize: 4 + (i + 1) * charsize] for i in range(nglyph)]
    off = 4 + nglyph * charsize
    umap = {}
    if mode & 0x02:
        idx, in_seq = 0, False
        while off + 1 < len(raw) and idx < nglyph:
            cp = int.from_bytes(raw[off:off + 2], "little")
            off += 2
            if cp == 0xFFFF:
                idx, in_seq = idx + 1, False
            elif cp == 0xFFFE:
                in_seq = True              # multi-codepoint sequences: skip
            elif not in_seq and cp not in umap:
                umap[cp] = idx
    return glyphs, umap

def font_tiles():
    """{ascii code: 8x8 array of colour indices} for codes 0x20..0x5F."""
    glyphs, umap = load_psf1(FONT_PATH)
    out = {}
    for code in range(0x20, 0x60):
        gi = umap.get(code, code if not umap else None)
        if gi is None:
            sys.exit(f"font has no glyph for U+{code:04X}")
        t = np.ones((8, 8), dtype=np.uint8)          # paper = ci 1
        for r, b in enumerate(glyphs[gi]):
            for p in range(8):
                if b & (0x80 >> p):
                    t[r, p] = 3                      # ink = ci 3
        out[code] = t
    return out

def encode_rows(tile):
    """8x8 colour indices -> 8 16-bit words, pixel p in bits [15-2p:14-2p]."""
    words = []
    for r in range(8):
        w = 0
        for p in range(8):
            w |= int(tile[r, p]) << (14 - 2 * p)
        words.append(w)
    return words

def write_chr(path, tiles):
    """tiles: list of 256 8x8 arrays -> 2048-line .mem file."""
    lines = []
    for t in tiles:
        lines += [f"{w:04X}" for w in encode_rows(t)]
    assert len(lines) == 2048
    path.write_text("\n".join(lines) + "\n")

def blank_tile():
    return np.zeros((8, 8), dtype=np.uint8)

ART_CI = {".": 0, "#": 1, "x": 2, "o": 3}

def art(rows, size):
    a = np.array([[ART_CI[ch] for ch in r] for r in rows], dtype=np.uint8)
    assert a.shape == (size, size), (a.shape, rows)
    return outline(a)

def outline(a):
    body = (a == 2) | (a == 3)
    out = a.copy()
    h, w = a.shape
    for y in range(h):
        for x in range(w):
            if a[y, x] != 0:
                continue
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                yy, xx = y + dy, x + dx
                if 0 <= yy < h and 0 <= xx < w and body[yy, xx]:
                    out[y, x] = 1
                    break
    assert not body[0, :].any() and not body[-1, :].any(), "fill touches top/bottom"
    assert not body[:, 0].any() and not body[:, -1].any(), "fill touches left/right"
    return out

BLOB = [
    [
        "................",
        ".........xx.....",
        "........#xo.....",
        "........#.......",
        "......xxxx......",
        "....xooxxxxx....",
        "...xoxxxxxxxx...",
        "..xoxxxxxxxxxx..",
        "..xxxxxxx#xx#x..",
        "..xxxxxxx#xx#x..",
        "..xxxxxxxxxxxx..",
        "..xxxxxxxx##xx..",
        "..xxxxxxxxxxxx..",
        "...xxxxxxxxxx...",
        "....xxxxxxxx....",
        "................",
    ],
    [
        "................",
        "................",
        "................",
        "..........xx....",
        ".........#xo....",
        ".........#......",
        "................",
        ".....xxxxxx.....",
        "...xxooxxxxxx...",
        "..xxoxxxxxxxxx..",
        ".xxxxxxxx#xx#xx.",
        ".xxxxxxxx#xx#xx.",
        ".xxxxxxxxx##xxx.",
        ".xxxxxxxxxxxxxx.",
        "..xxxxxxxxxxxx..",
        "................",
    ],
]

BIRD = [
    [
        "................",
        "..xx............",
        "..xox...........",
        "...xox...xxx....",
        "...xxoxxxxxxx...",
        "....xxxxxxooxx..",
        "..xxxxxxxxo#xx..",
        ".xxxxxxxxxxxxoo.",
        "..xxxxxxxxxxxo..",
        "...xxxxxxxxxx...",
        "....xxxxxxxx....",
        ".....xxxxxx.....",
        "................",
        "......#..#......",
        "................",
        "................",
    ],
    [
        "................",
        "................",
        "................",
        ".........xxx....",
        "....xxxxxxxxx...",
        "....xxxxxxooxx..",
        "..xxxxxxxxo#xx..",
        ".xxx####xxxxxoo.",
        "..xoooox#xxxxo..",
        "...xooxx#xxxx...",
        "....xoxx#xxx....",
        ".....xox#xx.....",
        "......xx#.......",
        "................",
        ".........#......",
        "................",
    ],
]

DRONE = [
    [
        "................",
        ".xxxxx....xxxxx.",
        "................",
        "...#...oo...#...",
        "...#........#...",
        "....xxxxxxxx....",
        "....xoxxooxx....",
        "....xoxoo##x....",
        "....xxxoo##x....",
        "....xxxxooxx....",
        "....xxxxxxxx....",
        "....xxxxxxxx....",
        "................",
        ".....#....#.....",
        "....###..###....",
        "................",
    ],
    [
        "................",
        "..xxx......xxx..",
        "................",
        "...#...xx...#...",
        "...#........#...",
        "....xxxxxxxx....",
        "....xoxxooxx....",
        "....xoxoo##x....",
        "....xxxoo##x....",
        "....xxxxooxx....",
        "....xxxxxxxx....",
        "....xxxxxxxx....",
        "................",
        ".....#....#.....",
        "....###..###....",
        "................",
    ],
]

JELLY = [
    [
        "................",
        "......xxxx......",
        "....xxxxxxxx....",
        "...xooxxxxxxx...",
        "..xoxxxxxxxxxx..",
        "..xxxxxxx#xx#x..",
        "..xxxxxxx#xx#x..",
        "..xxxxxxxxxxxx..",
        "..oxoxoxoxoxox..",
        "................",
        "...x...x...x....",
        "....x...x...x...",
        "....x...x...x...",
        "...x...x...x....",
        "...x...x...x....",
        "................",
    ],
    [
        "................",
        "......xxxx......",
        "....xxxxxxxx....",
        "...xooxxxxxxx...",
        "..xoxxxxxxxxxx..",
        "..xxxxxxx#xx#x..",
        "..xxxxxxx#xx#x..",
        "..xxxxxxxxxxxx..",
        "..xoxoxoxoxoxo..",
        "................",
        "....x...x...x...",
        "...x...x...x....",
        "...x...x...x....",
        "....x...x...x...",
        "....x...x...x...",
        "................",
    ],
]

CREATURES = [BLOB, BIRD, DRONE, JELLY]

STAR = [
    ["........",
     "........",
     "........",
     "...o....",
     "........",
     "........",
     "........",
     "........"],
    ["........",
     "........",
     "...x....",
     "..xox...",
     "...x....",
     "........",
     "........",
     "........"],
    ["........",
     "...x....",
     "...x....",
     ".xxoxx..",
     "...x....",
     "...x....",
     "........",
     "........"],
    ["........",
     "........",
     "..x.x...",
     "...o....",
     "..x.x...",
     "........",
     "........",
     "........"],
]

GEM = [
    ["........",
     "..xxxx..",
     ".xoxxxx.",
     ".xxxxxx.",
     "..xxxx..",
     "...xx...",
     "........",
     "........"],
    ["........",
     "..oxxx..",
     ".xooxxx.",
     ".xxoxxx.",
     "..xxxx..",
     "...xx...",
     "........",
     "........"],
    ["........",
     "..xxox..",
     ".xxooxx.",
     ".xxxoox.",
     "..xxxo..",
     "...xx...",
     "........",
     "........"],
    ["........",
     "..xxxx..",
     ".xoxxxx.",
     ".xxxxox.",
     "..xxoo..",
     "...xo...",
     "........",
     "........"],
]

ARROW = [   # points up; a light band climbs the arrow over the 4 frames
    ["........",
     "...xx...",
     "..xxxx..",
     ".xxxxxx.",
     "...xx...",
     "...xx...",
     "...oo...",
     "........"],
    ["........",
     "...xx...",
     "..xxxx..",
     ".xxxxxx.",
     "...oo...",
     "...xx...",
     "...xx...",
     "........"],
    ["........",
     "...xx...",
     "..xxxx..",
     ".oooooo.",
     "...xx...",
     "...xx...",
     "...xx...",
     "........"],
    ["........",
     "...oo...",
     "..xoox..",
     ".xxxxxx.",
     "...xx...",
     "...xx...",
     "...xx...",
     "........"],
]

COIN = [    # full face -> narrow -> edge-on -> narrow (other side)
    ["........",
     "..xxxx..",
     ".xoxxxx.",
     ".xox#xx.",
     ".xox#xx.",
     ".xxxxxx.",
     "..xxxx..",
     "........"],
    ["........",
     "...xx...",
     "..xoxx..",
     "..xo#x..",
     "..xo#x..",
     "..xxxx..",
     "...xx...",
     "........"],
    ["........",
     "...o....",
     "...o....",
     "...o....",
     "...o....",
     "...o....",
     "...o....",
     "........"],
    ["........",
     "...xx...",
     "..xxox..",
     "..x#ox..",
     "..x#ox..",
     "..xxxx..",
     "...xx...",
     "........"],
]

ITEMS = [STAR, GEM, ARROW, COIN]

def build_sprite_tiles():
    tiles = [blank_tile() for _ in range(256)]
    big_frames, small_frames = [], []
    for k, frames in enumerate(CREATURES):
        for f, rows in enumerate(frames):
            a = art(rows, 16)
            big_frames.append(a)
            t = k * 4 + f * 2
            tiles[t] = a[0:8, 0:8]
            tiles[t + 1] = a[0:8, 8:16]
            tiles[t + 16] = a[8:16, 0:8]
            tiles[t + 17] = a[8:16, 8:16]
    for t, frames in enumerate(ITEMS):
        for f, rows in enumerate(frames):
            a = art(rows, 8)
            small_frames.append(a)
            tiles[0x40 + t * 4 + f] = a
    return tiles, big_frames, small_frames

class World:
    """A 512x256 picture in RGB222 colours, -1 = transparent. x wraps."""

    def __init__(self):
        self.px = np.full((WORLD_H, WORLD_W), TRANSP, dtype=np.int16)

    def set(self, x, y, c):
        if 0 <= y < WORLD_H:
            self.px[y, x % WORLD_W] = c

    def get(self, x, y):
        if 0 <= y < WORLD_H:
            return int(self.px[y, x % WORLD_W])
        return TRANSP

    def rect(self, x0, y0, w, h, c):
        for y in range(y0, y0 + h):
            for x in range(x0, x0 + w):
                self.set(x, y, c)

    def mask(self, x0, y0, m, colour_of):
        """Paint a boolean mask m (h x w) at (x0, y0); colour_of(lx, ly)
        gives the colour for each set pixel (local coordinates)."""
        h, w = m.shape
        for ly in range(h):
            for lx in range(w):
                if m[ly, lx]:
                    self.set(x0 + lx, y0 + ly, colour_of(lx, ly))

def edge_of(m):
    """Pixels of mask m that have a 4-neighbour outside m (or off the edge)."""
    h, w = m.shape
    e = np.zeros_like(m)
    for y in range(h):
        for x in range(w):
            if not m[y, x]:
                continue
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                yy, xx = y + dy, x + dx
                if not (0 <= yy < h and 0 <= xx < w) or not m[yy, xx]:
                    e[y, x] = True
                    break
    return e

def circles_mask(w, h, circles, flat_bottom=None):
    """Union of circles (cx, cy, r) in a w x h box, optionally cut flat."""
    yy, xx = np.mgrid[0:h, 0:w]
    m = np.zeros((h, w), dtype=bool)
    for cx, cy, r in circles:
        m |= (xx + 0.5 - cx) ** 2 + (yy + 0.5 - cy) ** 2 <= r * r
    if flat_bottom is not None:
        m[flat_bottom:, :] = False
    return m

GRASS_ROW = 21            # y 168..175: grass top (pal0)
DIRT_ROWS = (22, 23)      # y 176..191: dirt (pal2)
WATER_ROW = 24            # y 192..255: water (pal3), to the bottom edge
GROUND_Y = GRASS_ROW * 8  # 168: things standing on the ground end here

def paint_hill(w, cx, half_w, height):
    """A rolling hill (pal0) centred on x = cx, base hidden under the grass.
    Dark green rim, green body with small dark tufts on a fixed 8x8 grid
    (in world coordinates, so the inside of every hill is the same tile)."""
    base = GROUND_Y + 8                       # runs down under the grass row
    x0, x1 = cx - half_w, cx + half_w
    bw, bh = x1 - x0, height + 8
    m = np.zeros((bh, bw), dtype=bool)
    for lx in range(bw):
        t = (lx + 0.5 - half_w) / half_w
        if abs(t) >= 1:
            continue
        top = height * (1 - abs(t) ** 2.2) ** 0.55
        for ly in range(bh):
            if (bh - ly) <= top + 8:
                m[ly, lx] = True
    e = edge_of(m)
    e[-1, :] = False                          # no rim along the hidden base
    y0 = base - bh

    def col(lx, ly):
        if e[ly, lx]:
            return DKGREEN
        wx, wy = (x0 + lx) % WORLD_W, y0 + ly
        sx = (wx + (4 if (wy // 8) % 2 else 0)) % 8
        sy = wy % 8
        if (sy == 3 and sx in (2, 4)) or (sy == 4 and sx == 3):
            return DKGREEN
        return GREEN

    w.mask(x0, y0, m, col)

CLOUD_BIG = circles_mask(48, 24, [(10, 16, 7), (20, 11, 9), (31, 9, 8),
                                  (39, 15, 7), (24, 17, 7)], flat_bottom=23)
CLOUD_SMALL = circles_mask(32, 16, [(9, 10, 6), (17, 7, 7), (24, 10, 6)],
                           flat_bottom=15)

def paint_cloud(w, x0, y0, m):
    """Cloud (pal1): white with a grey underside. No outline."""
    h, _ = m.shape
    e = edge_of(m)

    def col(lx, ly):
        below = ly + 1 >= h or not m[ly + 1, lx]
        if ly >= h - 3 or (below and ly > h // 2) or (e[ly, lx] and ly > h // 2 + 2):
            return GREY
        return WHITE

    w.mask(x0, y0, m, col)

TREE_LOBES = [(16, 8.5, 8), (8.5, 14.5, 7.5), (23.5, 14.5, 7.5), (16, 19, 8.5)]

def paint_tree(w, col):
    """Tree, all pal0: a canopy of four leaf clumps (32x28) on a 4-px brown
    trunk. Each clump is light green with a dark-green rim and a dark
    lower-right shadow, painted back to front so the clumps overlap. The
    trunk is pal0 (brown + dark green) rather than pal2 so a tree can stand
    in front of a hill: an 8x8 cell can only use one palette."""
    x0 = col * 8
    y_top = GROUND_Y - 40
    for y in range(y_top + 24, GROUND_Y):
        for lx in range(14, 18):
            c = DKGREEN if lx in (14, 17) else BROWN
            if lx == 16 and y % 5 == 2:
                c = DKGREEN                   # bark mark
            w.set(x0 + lx, y, c)
    for cx, cy, r in TREE_LOBES:
        for ly in range(28):
            for lx in range(32):
                dx, dy = lx + 0.5 - cx, ly + 0.5 - cy
                d = math.hypot(dx, dy)
                if d > r:
                    continue
                if d > r - 1.2:
                    c = DKGREEN               # rim
                elif 0.55 * dx + 0.85 * dy > 0.05 * r and d > r * 0.4:
                    c = DKGREEN               # shadow, lower right
                else:
                    c = GREEN
                w.set(x0 + lx, y_top + ly, c)

def brick_colour(lx, ly):
    """Brick wall texture in pal2, 8-px period in x and y (local coords)."""
    course = (ly // 4) % 2
    bx = (lx + 4 * course) % 8
    by = ly % 4
    if by == 3 or bx == 7:
        return BRK_DK                         # mortar
    if by == 0 or bx == 0:
        return BRK_LT                         # lit top/left edge of a brick
    return BRK

def paint_pillar(w, col, top_row):
    """Brick pillar (pal2), 2 tiles wide, from top_row down to the ground,
    with a stone cap. Every cell is fully opaque, so it may stand anywhere."""
    x0, y0 = col * 8, top_row * 8
    for y in range(y0, GROUND_Y):
        for lx in range(16):
            ly = y - y0
            if ly < 8:                        # cap slab
                if ly in (0, 7) or lx in (0, 15):
                    c = BRK_DK
                elif ly == 1 or lx == 1:
                    c = BRK_LT
                elif ly == 6:
                    c = BRK_DK if lx % 4 == 3 else BRK
                else:
                    c = BRK
            elif lx in (0, 15):
                c = BRK_DK                    # dark sides
            else:
                c = brick_colour(lx, ly)
            w.set(x0 + lx, y, c)

def paint_platform(w, col, row, ncols):
    """Floating brick ledge (pal2), one tile tall, fully opaque."""
    x0, y0 = col * 8, row * 8
    wpx = ncols * 8
    for ly in range(8):
        for lx in range(wpx):
            if ly in (0, 7) or lx in (0, wpx - 1):
                c = BRK_DK
            elif ly == 1:
                c = BRK_LT
            else:
                c = brick_colour(lx, ly + 2)
            w.set(x0 + lx, y0 + ly, c)

def frame_colour(lx, ly, wpx, hpx):
    """Sign frame (pal1): navy edge, white bevel top/left, grey bevel
    bottom/right, navy inside. Square corners, so frame cells are opaque."""
    if lx == 0 or ly == 0 or lx == wpx - 1 or ly == hpx - 1:
        return NAVY
    if lx in (2, wpx - 3) or ly in (2, hpx - 3):
        if lx >= 2 and ly >= 2 and lx <= wpx - 3 and ly <= hpx - 3:
            if ly == 2 or lx == 2:
                return WHITE
            return GREY
    return NAVY

def paint_sign(w, col, row, lines, posts=True, glyphs=None):
    """A sign board: a navy panel with a bevelled frame one tile wide on
    every side, text in the font (white on navy, pal1), and optionally two
    brown pal0 posts down to the ground."""
    tw = max(len(s) for s in lines)
    cols, rows = tw + 2, len(lines) + 2
    x0, y0 = col * 8, row * 8
    wpx, hpx = cols * 8, rows * 8
    for ly in range(hpx):
        for lx in range(wpx):
            w.set(x0 + lx, y0 + ly, frame_colour(lx, ly, wpx, hpx))
    for i, s in enumerate(lines):
        s = s.center(tw)
        for j, ch in enumerate(s):
            blit_glyph(w, col + 1 + j, row + 1 + i, ch, glyphs)
    if posts:
        for pc in (col + 1, col + cols - 2):
            for y in range(y0 + hpx, GROUND_Y):
                for lx in range(2, 6):
                    c = DKGREEN if lx in (2, 5) else BROWN
                    w.set(pc * 8 + lx, y, c)

def blit_glyph(w, col, row, ch, glyphs):
    g = glyphs[ord(ch)]
    for ly in range(8):
        for lx in range(8):
            w.set(col * 8 + lx, row * 8 + ly, NAVY if g[ly, lx] == 1 else WHITE)

def blit_text(w, col, row, s, glyphs):
    for j, ch in enumerate(s):
        blit_glyph(w, col + j, row, ch, glyphs)

def paint_ground(w, rng):
    """Grass row (pal0) over dirt rows (pal2)."""
    tips = ["..g.......g..g..",
            ".ggg..g..ggggg.g",
            "gggggggggggggggg",
            "gggggggggggggggg",
            "ggdggggggdgggggg",
            "dddddddddddddddd",
            "bbbbdbbbbbbbbbdb",
            "bbbbbbbbbbbbbbbb"]
    cmap = {"g": GREEN, "d": DKGREEN, "b": BROWN}
    y0 = GRASS_ROW * 8
    for ly in range(8):
        for x in range(WORLD_W):
            ch = tips[ly][x % 16]
            if ch != ".":
                w.set(x, y0 + ly, cmap[ch])
    blk = np.full((16, 32), BRK, dtype=np.int16)
    for _ in range(22):
        bx, by = rng.integers(0, 32), rng.integers(0, 14)
        blk[by, bx] = BRK_DK
    for _ in range(7):                        # pebbles: tan with a dark base
        bx, by = rng.integers(0, 31), rng.integers(1, 13)
        blk[by, bx] = BRK_LT
        blk[by, (bx + 1) % 32] = BRK_LT
        blk[by + 1, bx] = BRK_DK
        blk[by + 1, (bx + 1) % 32] = BRK_DK
    blk[15, :] = BRK_DK                       # wet edge above the water
    blk[14, ::3] = BRK_DK
    yd = DIRT_ROWS[0] * 8
    for ly in range(16):
        for x in range(WORLD_W):
            w.set(x, yd + ly, int(blk[ly, x % 32]))

def water_ci(x, y):
    """Colour index (1..3) of a water pixel.

    Horizontal bands 2 px tall, bent by a sine of period 64 px. Along y the
    index steps 1 -> 2 -> 3 -> 1, so when the demo rotates the three water
    colours (entry 13 takes 14's colour, 14 takes 15's, 15 takes 13's) every
    band moves up 2 px: waves rolling in towards the shore. The pattern
    repeats every 64 px in x (512 = 8 x 64, so the wrap is seamless) and
    every 24 px in y, which keeps it to at most 8 x 3 = 24 distinct tiles."""
    wave = round(2.4 * math.sin(2 * math.pi * x / 64))
    return 1 + ((y + wave) // 2) % 3

def paint_water(w):
    for y in range(WATER_ROW * 8, WORLD_H):
        for x in range(WORLD_W):
            w.set(x, y, C(3, water_ci(x, y)))

MARKERS = [(0, "X=0"), (16, "X=128"), (32, "X=256"), (48, "X=384")]
MARKER_ROW = 2

HILLS = [  # (centre x px, half width px, height px)
    (104, 56, 40),
    (312, 80, 64),
    (488, 56, 40),     # same shape as the first and same x mod 8: shared tiles
]
CLOUDS = [  # (x px, y px, shape) - x multiple of 8 so repeated clouds share tiles
    (216, 24, CLOUD_BIG),
    (328, 64, CLOUD_SMALL),
    (376, 16, CLOUD_BIG),
    (480, 56, CLOUD_BIG),      # crosses x = 511 -> 0
    (112, 72, CLOUD_SMALL),
]
PLATFORMS = [  # (col, row, width in tiles)
    (9, 12, 5),
    (34, 11, 6),
    (53, 9, 4),
]
PILLARS = [  # (col, top row)
    (20, 11),
    (43, 15),
]
TREES = [13, 37, 60]   # canopy left column
SIGNS = [  # (col, row, lines)
    (3, 15, ["D-PAD:", "SCROLL"]),
    (24, 15, ["START:", "AUTO PAN"]),
    (47, 15, ["SELECT:", "HUD ON/OFF"]),
]
TITLE = (3, 4, ["PAGE 0: 64X32 TILES"])

def paint_world(glyphs):
    rng = np.random.default_rng(597)          # fixed seed: dirt speckle only
    w = World()
    for cx, hw, h in HILLS:
        paint_hill(w, cx, hw, h)
    for x, y, m in CLOUDS:
        paint_cloud(w, x, y, m)
    for col in TREES:
        paint_tree(w, col)
    for col, top in PILLARS:
        paint_pillar(w, col, top)
    for col, row, n in PLATFORMS:
        paint_platform(w, col, row, n)
    for col, row, lines in SIGNS:
        paint_sign(w, col, row, lines, posts=True, glyphs=glyphs)
    col, row, lines = TITLE
    paint_sign(w, col, row, lines, posts=False, glyphs=glyphs)
    for col, s in MARKERS:
        blit_text(w, col, MARKER_ROW, s, glyphs)
    paint_ground(w, rng)
    paint_water(w)
    return w

SCENERY_IDS = list(range(0x01, 0x20)) + list(range(0x60, 0x100))   # 191 slots
PAL_PREFERENCE = [0, 2, 1, 3]     # for cells whose colours fit several palettes

def cut_world(world, font):
    """Return (map entries [32][64], bg tile list[256], tile -> palette use
    counts). Fails loudly on a cell that mixes two palettes' colours."""
    tiles = [blank_tile() for _ in range(256)]
    lookup = {blank_tile().tobytes(): 0}
    for code, t in font.items():
        tiles[code] = t
        lookup.setdefault(t.tobytes(), code)
    free = iter(SCENERY_IDS)
    n_scenery = 0
    entries = [[0] * MAP_COLS for _ in range(MAP_ROWS)]
    use = {}
    clashes = []
    for row in range(MAP_ROWS):
        for col in range(MAP_COLS):
            cell = world.px[row * 8:row * 8 + 8, col * 8:col * 8 + 8]
            colours = {int(c) for c in cell.flat if c != TRANSP}
            if not colours:
                entries[row][col] = 0x000
                continue
            cands = [p for p in PAL_PREFERENCE if colours <= set(BG_PAL[p][1:])]
            if not cands:
                clashes.append((col, row, sorted(colours)))
                continue
            p = cands[0]
            t = np.zeros((8, 8), dtype=np.uint8)
            for y in range(8):
                for x in range(8):
                    c = int(cell[y, x])
                    t[y, x] = 0 if c == TRANSP else BG_PAL[p].index(c, 1)
            key = t.tobytes()
            if key not in lookup:
                try:
                    tid = next(free)
                except StopIteration:
                    sys.exit("out of scenery tiles (191 slots)")
                tiles[tid] = t
                lookup[key] = tid
                n_scenery += 1
            tid = lookup[key]
            entries[row][col] = (p << 8) | tid
            use.setdefault(tid, {}).setdefault(p, 0)
            use[tid][p] += 1
    if clashes:
        for col, row, cs in clashes:
            print(f"palette clash at col {col} row {row}: colours "
                  + " ".join(f"{c:02X}" for c in cs))
        sys.exit(f"{len(clashes)} cells need two palettes - fix the layout")
    return entries, tiles, use, n_scenery

HUD_ROWS = [
    " HW5 PPU DEMO  SCROLL X:000 Y:00 OVF:NO ",
    "A:FLICK OFF B:BEHIND OFF Y:ROW OFF X:P0 ",
]
assert all(len(s) == 40 for s in HUD_ROWS)
assert HUD_ROWS[0][24:27] == "000" and HUD_ROWS[0][30:32] == "00"
assert HUD_ROWS[0][37:40] == "NO "
assert HUD_ROWS[1][8:11] == "OFF" and HUD_ROWS[1][21:24] == "OFF"
assert HUD_ROWS[1][31:34] == "OFF" and HUD_ROWS[1][38] == "0"

def hud_page():
    entries = [[0x000] * MAP_COLS for _ in range(MAP_ROWS)]
    for r, s in enumerate(HUD_ROWS):
        for c in range(MAP_COLS):
            ch = s[c] if c < len(s) else " "
            entries[r][c] = (1 << 8) | ord(ch)
    return entries

def colourise(tile, pal4):
    """8x8 ci -> 8x8x3 RGB, ci0 drawn as pal4[0] (a 6-bit colour)."""
    lut = np.array([rgb888(c) for c in pal4], dtype=np.uint8)
    return lut[tile]

def render_map(entries, bg_tiles, rows, cols):
    img = np.zeros((rows * 8, cols * 8, 3), dtype=np.uint8)
    for r in range(rows):
        for c in range(cols):
            e = entries[r][c]
            p, t = e >> 8, e & 0xFF
            pal4 = [PALETTE[0]] + BG_PAL[p][1:]
            img[r * 8:r * 8 + 8, c * 8:c * 8 + 8] = colourise(bg_tiles[t], pal4)
    return img

def scale(img, s):
    return np.repeat(np.repeat(img, s, axis=0), s, axis=1)

def sheet_image(tiles, pal_of, s=4, gap=2, bgcol=(40, 40, 48)):
    """16x16 tile sheet, each tile at scale s with a gap, labelled axes."""
    cell = 8 * s + gap
    margin = 22
    W = margin + 16 * cell + gap
    img = Image.new("RGB", (W, W), bgcol)
    d = ImageDraw.Draw(img)
    for i in range(16):
        d.text((margin + gap + i * cell + 8 * s // 2 - 3, 5), f"{i:X}", fill=(200, 200, 200))
        d.text((4, margin + gap + i * cell + 8 * s // 2 - 5), f"{i:X}0", fill=(200, 200, 200))
    arr = np.array(img)
    for t in range(256):
        pal4 = pal_of(t)
        if pal4 is None:
            continue
        r, c = divmod(t, 16)
        y0 = margin + gap + r * cell
        x0 = margin + gap + c * cell
        arr[y0:y0 + 8 * s, x0:x0 + 8 * s] = scale(colourise(tiles[t], pal4), s)
    return Image.fromarray(arr)

def write_previews(bg_tiles, bg_use, spr_tiles, big, small, page0, page1, used_bg):
    IMG_DIR.mkdir(exist_ok=True)

    def bg_pal_of(t):
        if t not in used_bg:
            return None
        if 0x20 <= t < 0x60 and t not in bg_use:
            p = 1
        elif t in bg_use:
            p = max(bg_use[t].items(), key=lambda kv: (kv[1], -kv[0]))[0]
        else:
            p = 0
        return [PALETTE[0]] + BG_PAL[p][1:]

    sheet_image(bg_tiles, bg_pal_of).save(IMG_DIR / "chr_bg.png")

    spr_pal = lambda p: [PALETTE[0]] + PALETTE[16 + 4 * p + 1:16 + 4 * p + 4]
    sheet = sheet_image(spr_tiles, lambda t: spr_pal(0))
    s = 4
    strip_rows = []
    for p in range(4):
        pal4 = spr_pal(p)
        row_big = np.concatenate([colourise(a, pal4) for a in big], axis=1)
        row_small = np.concatenate([colourise(a, pal4) for a in small], axis=1)
        pad = np.zeros((8, 128, 3), dtype=np.uint8)
        pad[:] = rgb888(PALETTE[0])
        strip_rows += [row_big, np.concatenate([row_small, pad], axis=0)]
    strip = np.concatenate(strip_rows, axis=0)
    strip = scale(strip, s)
    out = Image.new("RGB", (max(sheet.width, strip.shape[1] + 44),
                            sheet.height + strip.shape[0] + 30), (40, 40, 48))
    out.paste(sheet, (0, 0))
    d = ImageDraw.Draw(out)
    d.text((24, sheet.height + 6), "every frame in sprite palettes 4, 5, 6, 7 "
           "(creatures k0..k3 x frame 0..1, items t0..t3 x frame 0..3)",
           fill=(200, 200, 200))
    out.paste(Image.fromarray(strip), (24, sheet.height + 24))
    for p in range(4):
        d.text((4, sheet.height + 24 + p * 32 * s + 12 * s), f"P{p + 4}", fill=(220, 220, 220))
    out.save(IMG_DIR / "chr_spr.png")

    Image.fromarray(scale(render_map(page0, bg_tiles, 32, 64), 2)).save(
        IMG_DIR / "world_page0.png")
    Image.fromarray(scale(render_map(page1, bg_tiles, 2, 40), 2)).save(
        IMG_DIR / "hud_page1.png")

def read_mem(path, digits, count):
    lines = path.read_text().split()
    assert len(lines) == count, f"{path.name}: {len(lines)} lines, want {count}"
    assert all(len(x) == digits for x in lines), f"{path.name}: bad word width"
    return [int(x, 16) for x in lines]

def decode_tile(chr_words, tile):
    """{tile, row} -> 8x8 colour indices, pixel p from bits [15-2p:14-2p]."""
    t = np.zeros((8, 8), dtype=np.uint8)
    for r in range(8):
        w = chr_words[tile * 8 + r]
        for p in range(8):
            t[r, p] = (w >> (14 - 2 * p)) & 3
    return t

def verify_files(world, big, small):
    """Re-read the written files and check that they reproduce the painted
    world pixel for pixel, the sprites at their contract tile numbers, and
    the HUD page. This tests the files, not the in-memory arrays."""
    pal = read_mem(SRC_DIR / "palette.mem", 2, 32)
    bg = read_mem(SRC_DIR / "chr_bg.mem", 4, 2048)
    sp = read_mem(SRC_DIR / "chr_spr.mem", 4, 2048)
    vram = read_mem(SRC_DIR / "demo_map.mem", 3, 4096)
    assert pal == PALETTE and all(v < 64 for v in pal)
    assert all(v < 0x400 for v in vram)
    for e in (4, 8, 12, 16, 20, 24, 28):
        assert pal[e] == 0

    for y in range(WORLD_H):
        for x in range(WORLD_W):
            e = vram[0 * 2048 + (y >> 3) * 64 + (x >> 3)]
            p, t = e >> 8, e & 0xFF
            ci = (bg[t * 8 + (y & 7)] >> (14 - 2 * (x & 7))) & 3
            got = TRANSP if ci == 0 else pal[4 * p + ci]
            assert got == world.px[y, x], f"page 0 mismatch at x={x} y={y}"

    for r in range(MAP_ROWS):
        for c in range(MAP_COLS):
            e = vram[2048 + r * 64 + c]
            if r < 2:
                ch = HUD_ROWS[r][c] if c < 40 else " "
                assert e == 0x100 | ord(ch), f"HUD mismatch row {r} col {c}"
            else:
                assert e == 0, f"page 1 row {r} col {c} not empty"
    assert all(v == 0x5555 for v in bg[0x20 * 8:0x20 * 8 + 8]), "space must be solid ci1"
    assert all(v == 0 for v in bg[0:8]), "tile 0 must be blank"

    for i, a in enumerate(big):
        k, f = divmod(i, 2)
        T = k * 4 + f * 2
        got = np.block([[decode_tile(sp, T), decode_tile(sp, T + 1)],
                        [decode_tile(sp, T + 16), decode_tile(sp, T + 17)]])
        assert (got == a).all(), f"creature {k} frame {f} mismatch"
    for i, a in enumerate(small):
        t, f = divmod(i, 4)
        assert (decode_tile(sp, 0x40 + t * 4 + f) == a).all(), f"item {t}/{f}"
    used = {k * 4 + f * 2 + d for k in range(4) for f in range(2) for d in (0, 1, 16, 17)}
    used |= set(range(0x40, 0x50))
    for t in range(256):
        if t not in used:
            assert all(v == 0 for v in sp[t * 8:t * 8 + 8]), f"sprite tile {t:02X} not blank"

def main():
    SRC_DIR.mkdir(exist_ok=True)
    font = font_tiles()

    (SRC_DIR / "palette.mem").write_text("".join(f"{c:02X}\n" for c in PALETTE))

    spr_tiles, big, small = build_sprite_tiles()
    write_chr(SRC_DIR / "chr_spr.mem", spr_tiles)

    world = paint_world(font)
    page0, bg_tiles, bg_use, n_scenery = cut_world(world, font)
    assert n_scenery <= len(SCENERY_IDS)
    write_chr(SRC_DIR / "chr_bg.mem", bg_tiles)

    page1 = hud_page()
    lines = []
    for page in (page0, page1):
        for r in range(MAP_ROWS):
            for c in range(MAP_COLS):
                lines.append(f"{page[r][c]:03X}")
    assert len(lines) == 4096
    (SRC_DIR / "demo_map.mem").write_text("\n".join(lines) + "\n")

    verify_files(world, big, small)
    used_bg = {0} | set(range(0x20, 0x60)) | {e & 0xFF for row in page0 for e in row}
    font_reused = sorted({e & 0xFF for row in page0 for e in row
                          if 0x20 <= (e & 0xFF) < 0x60})
    write_previews(bg_tiles, bg_use, spr_tiles, big, small, page0, page1, used_bg)

    transparent = sum(1 for row in page0 for e in row if e == 0)
    print(f"palette.mem   32 colours")
    print(f"chr_spr.mem   {len(big)} creature frames, {len(small)} item frames")
    print(f"chr_bg.mem    {n_scenery} unique scenery tiles "
          f"(+ blank + 64 font tiles), {len(SCENERY_IDS) - n_scenery} free")
    print(f"demo_map.mem  page 0: {transparent}/2048 cells open sky (tile 00); "
          f"font tiles used in the world: {len(font_reused)}")
    print(f"previews in {IMG_DIR}")

if __name__ == "__main__":
    main()
