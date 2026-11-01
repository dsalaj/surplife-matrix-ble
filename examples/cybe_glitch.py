"""CYBE — vertical cyberpunk glitch scroller for a portrait-mounted 90x12 panel.

Mounting: the panel is rotated so the device's top-left pixel sits at the
PHYSICAL bottom-left. We draw on a 12x90 portrait canvas and rotate each
frame 90 degrees clockwise into the device's 90x12 landscape framebuffer.

Style follows ../led_matrix_animator (pure red, block / scanline /
chromatic glitch layers, pixel noise, deterministic seamless loop).

    .venv/bin/python examples/cybe_glitch.py            # writes animations/cybe_glitch.gif
    .venv/bin/python matrix.py gif:animations/cybe_glitch.gif:100
"""
import os
import random
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from make_gif import save_safe_gif  # noqa: E402

PW, PH = 12, 90          # portrait canvas (physical view)
FRAMES = 90              # 9 s loop; 1 px/frame scroll (period = 90 px)
STEP = 1                 # px scrolled per frame
HOLD = 2                 # each glitch state is held for HOLD frames (easier to perceive)
DELAY_MS = 100           # 10 fps, the device minimum

RED = (255, 0, 0)        # pure red; everything is red except rare white sparks
DIM = (90, 0, 0)
DEEP = (40, 0, 0)
WHITE = (255, 255, 255)

GLYPHS = {
    "C": """
..XXXXXXXX
.XXXXXXXXX
XX........
XX........
XX........
XX........
XX........
XX........
XX........
XX........
XX........
.XXXXXXXXX
..XXXXXXXX""",
    "Y": """
XX......XX
XX......XX
XX......XX
.XX....XX.
..XX..XX..
...XXXX...
....XX....
....XX....
....XX....
....XX....
....XX....
....XX....
....XX....""",
    "B": """
XXXXXXXX..
XXXXXXXXX.
XX.....XX.
XX.....XX.
XX.....XX.
XXXXXXXX..
XXXXXXXXX.
XX......XX
XX......XX
XX......XX
XX......XX
XXXXXXXXXX
XXXXXXXXX.""",
    "E": """
XXXXXXXXXX
XXXXXXXXXX
XX........
XX........
XX........
XXXXXXX...
XXXXXXX...
XX........
XX........
XX........
XX........
XXXXXXXXXX
XXXXXXXXXX""",
}
GLYPHS = {k: [r for r in v.strip("\n").splitlines()] for k, v in GLYPHS.items()}
GW, GH, PITCH = 10, 13, 16       # glyph size, vertical letter pitch
TEXT = "CYBE"
TEXT_TOP = 6                     # y of first letter inside the 90 px period


class Canvas:
    def __init__(self):
        self.px = [[(0, 0, 0)] * PW for _ in range(PH)]

    def set(self, x, y, c):
        if 0 <= x < PW and 0 <= y < PH:
            self.px[y][x] = c

    def get(self, x, y):
        return self.px[y][x] if 0 <= x < PW and 0 <= y < PH else (0, 0, 0)

    def image(self):
        im = Image.new("RGB", (PW, PH))
        im.putdata([c for row in self.px for c in row])
        return im


def text_mask(offset):
    """Set of (x, y) lit by CYBE, scrolled DOWN by `offset`, tiled every PH px."""
    lit = {}
    for li, ch in enumerate(TEXT):
        for gy, row in enumerate(GLYPHS[ch]):
            for gx, v in enumerate(row):
                if v == "X":
                    y = (TEXT_TOP + li * PITCH + gy + offset) % PH
                    lit[(1 + gx, y)] = li
    return lit


# Glitch bursts: frame -> list of effects. Calm frames between bursts read as
# "signal", bursts read as "interference". Fixed seed = identical every export.
BURSTS = {
    3: ["block"],
    5: ["tear", "chroma"], 6: ["tear", "block"], 7: ["tear", "chroma"], 8: ["block"],
    12: ["tear"],
    15: ["block"], 16: ["scramble", "block", "tear"], 17: ["tear"],
    20: ["block"],
    23: ["tear", "block"], 24: ["tear", "chroma", "block"], 25: ["tear", "noise+"],
    26: ["block"],
    29: ["tear"],
    33: ["scramble", "block"], 34: ["tear", "chroma"], 35: ["tear", "block"],
    38: ["block"],
    40: ["tear", "block"], 41: ["tear", "noise+", "block"], 42: ["chroma", "tear"],
}


def render(f):
    g = f // HOLD                       # glitch timeline runs on 45 'beats'
    rnd = random.Random(1337 + g * 7919)
    fx = BURSTS.get(g, [])
    c = Canvas()
    offset = f * STEP

    # 1) side rails: dim dashes flowing UP (counter to the text) + data packets
    for y in range(PH):
        if (y - f * STEP) % 6 < 2:
            c.set(0, y, DEEP)
        if (y + f * STEP) % 6 < 2:
            c.set(PW - 1, y, DEEP)
    for k, x in ((0, 0), (1, PW - 1)):  # a bright packet travelling each rail
        py = (f * STEP * (1 if k else -1) + k * 45) % PH
        for d in range(3):
            c.set(x, (py - d * (1 if k else -1)) % PH, RED if d == 0 else DIM)

    # 2) text
    lit = text_mask(offset)
    scramble = rnd.randrange(len(TEXT)) if "scramble" in fx else None
    text_col = RED
    for (x, y), li in lit.items():
        if li == scramble:
            continue
        c.set(x, y, text_col)
    if scramble is not None:  # corrupted letter: random bits in its box, white/red
        y0 = (TEXT_TOP + scramble * PITCH + offset) % PH
        for gy in range(GH):
            for gx in range(GW):
                if rnd.random() < 0.45:
                    c.set(1 + gx, (y0 + gy) % PH, WHITE if rnd.random() < 0.08 else RED)

    # 3) CRT scan bar: one bright row sweeping down, once per loop (2 px/frame)
    sy = (f * STEP) % PH
    for x in range(PW):
        cur = c.get(x, sy)
        c.set(x, sy, WHITE if cur == RED else (110, 0, 0))

    # 4) glitch effects
    if "chroma" in fx:  # red echo: dim ghost copies offset left/right (only where empty)
        snap = [row[:] for row in c.px]
        for (x, y) in lit:
            if snap[y][x] in (RED, DIM):
                if c.get(x - 1, y) == (0, 0, 0):
                    c.set(x - 1, y, DIM)
                if c.get(x + 1, y) == (0, 0, 0):
                    c.set(x + 1, y, DIM)
    if "tear" in fx:  # horizontal tear: shift a band of rows sideways (wraps)
        for _ in range(rnd.randint(1, 3)):
            y0, h = rnd.randrange(PH), rnd.randint(2, 7)
            s = rnd.choice([-3, -2, 2, 3])
            for y in range(y0, min(PH, y0 + h)):
                row = c.px[y][:]
                c.px[y] = [row[(x - s) % PW] for x in range(PW)]
    if "block" in fx:  # block corruption
        for _ in range(rnd.randint(2, 4)):
            bw, bh = rnd.randint(3, 8), rnd.randint(1, 3)
            bx, by = rnd.randrange(PW - 2), rnd.randrange(PH)
            col = rnd.choice([RED, RED, RED, DIM])
            for y in range(by, by + bh):
                for x in range(bx, bx + bw):
                    c.set(x, y, col)

    # 5) pixel noise (phosphor static), heavier during bursts
    for _ in range(18 if "noise+" in fx else 4):
        x, y = rnd.randrange(PW), rnd.randrange(PH)
        if c.get(x, y) == (0, 0, 0):
            c.set(x, y, rnd.choice([DEEP, DIM, DIM, RED] if "noise+" in fx else [DEEP, DIM]))

    return c.image()


def portrait_to_device(im):
    """Physical portrait 12x90 -> device 90x12 (device top-left = physical bottom-left)."""
    return im.transpose(Image.Transpose.ROTATE_270)  # 90 deg clockwise


def preview(portraits, path, scale=6):
    """Contact sheet of all portrait frames, as seen when mounted."""
    gap = 3
    sheet = Image.new("RGB", (len(portraits) * (PW * scale + gap), PH * scale), (25, 25, 25))
    for i, p in enumerate(portraits):
        sheet.paste(p.resize((PW * scale, PH * scale), Image.NEAREST), (i * (PW * scale + gap), 0))
    sheet.save(path)


if __name__ == "__main__":
    root = os.path.join(os.path.dirname(__file__), "..")
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, "animations", "cybe_glitch.gif")
    portraits = [render(f) for f in range(FRAMES)]
    save_safe_gif([portrait_to_device(p) for p in portraits], out, delay_ms=DELAY_MS)
    if len(sys.argv) > 2:
        preview(portraits, sys.argv[2])
