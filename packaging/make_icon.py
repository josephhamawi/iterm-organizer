"""Generate the app icon.

Pure stdlib: every size is rendered directly from signed distance fields, so
edges are anti-aliased and the small sizes stay crisp instead of being blurry
downscales of the 1024 master.  The PNGs are written by hand and handed to
iconutil.

The artwork is a terminal window split into panes - the thing the app puts
back together - with a green prompt in the large pane.  Geometry is expressed
in a 1024x1024 design space and follows Apple's icon grid: the rounded body is
824pt wide, centred, leaving room for the shadow.
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
import sys
import zlib

DESIGN = 1024.0

# Body: Apple's grid puts a 824pt squircle inside the 1024pt canvas.
BODY_HALF = 412.0
BODY_N = 5.3  # superellipse exponent, matched to the macOS squircle
CENTER = 512.0

BG_TOP = (40, 48, 63)
BG_BOTTOM = (16, 20, 27)
SCREEN = (9, 12, 17)
GREEN = (126, 231, 135)
DIM = (74, 88, 108)
EDGE = (255, 255, 255)

# The three panes, inset 118 from the body edge. Their corner radius is the
# body's minus that inset, so every curve in the icon is concentric.
PANE_R = 66.0
LEFT = (218.0, 218.0, 520.0, 806.0)
RIGHT_TOP = (540.0, 218.0, 806.0, 502.0)
RIGHT_BOTTOM = (540.0, 522.0, 806.0, 806.0)

# Everything inside the panes.
PROMPT = (
    (302.0, 368.0, 406.0, 486.0),
    (406.0, 486.0, 302.0, 604.0),
    (302.0, 678.0, 446.0, 678.0),
)
PROMPT_W = 52.0
OUTPUT_W = 24.0
OUTPUT_ROWS = (
    (288.0, 190.0),
    (350.0, 138.0),
    (412.0, 166.0),
    (592.0, 152.0),
    (654.0, 194.0),
    (716.0, 120.0),
)


def inside(x: float, y: float, box) -> bool:
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def squircle_sd(x: float, y: float, half: float, n: float) -> float:
    """Signed distance to a superellipse, positive outside.

    Approximated along the ray from the centre, which is exact on the axes and
    at the diagonal and well under a pixel everywhere else - all the accuracy
    an anti-aliased edge needs.
    """
    dx = abs(x - CENTER)
    dy = abs(y - CENTER)
    if dx == 0.0 and dy == 0.0:
        return -half
    v = ((dx / half) ** n + (dy / half) ** n) ** (1.0 / n)
    r = math.hypot(dx, dy)
    return r * (v - 1.0) / v


def rrect_sd(x: float, y: float, box, radius: float) -> float:
    """Signed distance to a rounded rectangle, positive outside."""
    x0, y0, x1, y1 = box
    hx = (x1 - x0) / 2.0 - radius
    hy = (y1 - y0) / 2.0 - radius
    px = abs(x - (x0 + x1) / 2.0) - hx
    py = abs(y - (y0 + y1) / 2.0) - hy
    ox = px if px > 0.0 else 0.0
    oy = py if py > 0.0 else 0.0
    outside = math.hypot(ox, oy)
    inside = min(max(px, py), 0.0)
    return outside + inside - radius


def capsule_sd(x: float, y: float, seg, width: float) -> float:
    """Signed distance to a round-capped line segment."""
    ax, ay, bx, by = seg
    vx, vy = bx - ax, by - ay
    wx, wy = x - ax, y - ay
    denom = vx * vx + vy * vy
    t = 0.0 if denom == 0.0 else clamp((wx * vx + wy * vy) / denom)
    return math.hypot(wx - vx * t, wy - vy * t) - width / 2.0


class Canvas:
    """Straight-alpha RGBA canvas in float, composited source-over."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.scale = DESIGN / size
        self.px = [[0.0, 0.0, 0.0, 0.0] for _ in range(size * size)]

    def design_xy(self, ix: int, iy: int) -> tuple[float, float]:
        return (ix + 0.5) * self.scale, (iy + 0.5) * self.scale

    def bbox(self, box, pad: float) -> tuple[int, int, int, int]:
        x0, y0, x1, y1 = box
        s = self.scale
        return (
            max(0, int((x0 - pad) / s)),
            max(0, int((y0 - pad) / s)),
            min(self.size - 1, int((x1 + pad) / s) + 1),
            min(self.size - 1, int((y1 + pad) / s) + 1),
        )

    def blend(self, ix: int, iy: int, color, alpha: float) -> None:
        if alpha <= 0.0:
            return
        dst = self.px[iy * self.size + ix]
        out_a = alpha + dst[3] * (1.0 - alpha)
        if out_a <= 0.0:
            return
        for c in range(3):
            dst[c] = (color[c] * alpha + dst[c] * dst[3] * (1.0 - alpha)) / out_a
        dst[3] = out_a

    def fill(self, sd, color, box, pad=2.0, opacity=1.0, feather=None,
             skip_box=None, solid_box=None) -> None:
        """Fill wherever `sd` is negative, anti-aliased over one pixel.

        `color` may be a callable taking (x, y), which is how a layer carries a
        gradient. `feather` widens the falloff into a soft edge, which is how
        the drop shadow is drawn. `skip_box` and `solid_box` name a region
        where the result is known without evaluating the distance field -
        hidden, or fully covered - which keeps the 1024px pass quick.
        """
        x0, y0, x1, y1 = self.bbox(box, pad + (feather or 0.0))
        soft = feather if feather else self.scale
        dynamic = callable(color)
        for iy in range(y0, y1 + 1):
            for ix in range(x0, x1 + 1):
                x, y = self.design_xy(ix, iy)
                if skip_box and inside(x, y, skip_box):
                    continue
                if solid_box and inside(x, y, solid_box):
                    cov = 1.0
                else:
                    d = sd(x, y)
                    if d >= soft:
                        continue
                    cov = clamp(-d / soft) if feather else clamp(0.5 - d / soft)
                    if cov <= 0.0:
                        continue
                self.blend(ix, iy, color(x, y) if dynamic else color, cov * opacity)

    def to_png_bytes(self) -> bytes:
        raw = bytearray()
        size = self.size
        for y in range(size):
            raw.append(0)  # filter type 0
            row = self.px[y * size : (y + 1) * size]
            for r, g, b, a in row:
                raw += bytes(
                    (
                        int(clamp(r, 0, 255) + 0.5),
                        int(clamp(g, 0, 255) + 0.5),
                        int(clamp(b, 0, 255) + 0.5),
                        int(clamp(a) * 255 + 0.5),
                    )
                )
        return bytes(raw)


def seg_box(seg, stroke: float):
    """Bounding box of a capsule, so a fill only walks the pixels it can touch."""
    ax, ay, bx, by = seg
    return (
        min(ax, bx) - stroke,
        min(ay, by) - stroke,
        max(ax, bx) + stroke,
        max(ay, by) + stroke,
    )


def recentre(segs, factor: float):
    """Scale a group of segments about its own centre and park it mid-canvas.

    Used by the small sizes, where the prompt is the whole icon and therefore
    belongs in the middle rather than in the left-hand pane.
    """
    xs = [v for seg in segs for v in (seg[0], seg[2])]
    ys = [v for seg in segs for v in (seg[1], seg[3])]
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    return tuple(
        (
            CENTER + (seg[0] - cx) * factor,
            CENTER + (seg[1] - cy) * factor,
            CENTER + (seg[2] - cx) * factor,
            CENTER + (seg[3] - cy) * factor,
        )
        for seg in segs
    )


def lerp_color(a, b, t: float):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def render(size: int) -> Canvas:
    cv = Canvas(size)
    body_box = (CENTER - BODY_HALF, CENTER - BODY_HALF, CENTER + BODY_HALF, CENTER + BODY_HALF)
    body = lambda x, y: squircle_sd(x, y, BODY_HALF, BODY_N)

    # The largest square that fits inside the body: its corners sit exactly on
    # the curve. Layers use it to skip the distance field where the answer is
    # already known, which is most of a 1024px canvas.
    inner = BODY_HALF / 2.0 ** (1.0 / BODY_N) - 2.0
    inner_box = (CENTER - inner, CENTER - inner, CENTER + inner, CENTER + inner)

    # 1. Drop shadow, sitting just below the body. The part behind the body is
    #    never seen, so it is not drawn.
    cv.fill(
        lambda x, y: squircle_sd(x, y - 14.0, BODY_HALF, BODY_N),
        (0, 0, 0),
        body_box,
        pad=44.0,
        opacity=0.30,
        feather=40.0,
        skip_box=inner_box,
    )

    # 2. Body, lit from the top.
    top, bottom = CENTER - BODY_HALF, CENTER + BODY_HALF
    cv.fill(
        body,
        lambda x, y: lerp_color(BG_TOP, BG_BOTTOM, clamp((y - top) / (bottom - top))),
        body_box,
        solid_box=inner_box,
    )

    # 3. Hairline just inside the edge, so the body reads against a dark
    #    desktop. Its thickness follows the pixel grid rather than the design
    #    grid, or it would disappear at 16px.
    rim = max(1.5, cv.scale * 1.2)
    cv.fill(
        lambda x, y: abs(body(x, y) + rim / 2.0) - rim / 2.0,
        EDGE,
        body_box,
        opacity=0.13,
        skip_box=inner_box,
    )

    # Below 64px the panes collapse into mush, so those sizes carry a single
    # screen with the prompt, enlarged. The silhouette does not change.
    detailed = size >= 64
    panes = (LEFT, RIGHT_TOP, RIGHT_BOTTOM) if detailed else (
        (CENTER - 294.0, CENTER - 294.0, CENTER + 294.0, CENTER + 294.0),
    )

    # 4. The screens.
    for pane in panes:
        cv.fill(lambda x, y, p=pane: rrect_sd(x, y, p, PANE_R), SCREEN, pane)

    # 5. The prompt: a chevron over a cursor rule.
    prompt, stroke = PROMPT, PROMPT_W
    if not detailed:
        # At 16px the cursor rule and the chevron sit a pixel apart and merge
        # into a blob, so that size keeps the chevron alone.
        segs = PROMPT if size >= 32 else PROMPT[:2]
        factor = 1.45 if size >= 32 else 1.7
        prompt, stroke = recentre(segs, factor), PROMPT_W * factor
    for seg in prompt:
        cv.fill(
            lambda x, y, s=seg, w=stroke: capsule_sd(x, y, s, w),
            GREEN,
            seg_box(seg, stroke),
        )

    # 6. Lines of output in the side screens. Dropped below 128px, where they
    #    would only smear the screens grey.
    if size >= 128:
        for cy, length in OUTPUT_ROWS:
            seg = (RIGHT_TOP[0] + 36.0, cy, RIGHT_TOP[0] + 36.0 + length, cy)
            cv.fill(
                lambda x, y, s=seg: capsule_sd(x, y, s, OUTPUT_W),
                DIM,
                seg_box(seg, OUTPUT_W),
                opacity=0.9,
            )
    return cv


def write_png(path: str, cv: Canvas) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", struct.pack(">IIBBBBB", cv.size, cv.size, 8, 6, 0, 0, 0)))
        fh.write(chunk(b"IDAT", zlib.compress(cv.to_png_bytes(), 9)))
        fh.write(chunk(b"IEND", b""))


# (name, pixel size) for every slot iconutil expects.
SLOTS = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]


def main(dest_icns: str) -> int:
    work = dest_icns + ".iconset"
    subprocess.run(["/bin/rm", "-rf", work], check=True)
    os.makedirs(work, exist_ok=True)

    cache: dict[int, Canvas] = {}
    for name, px in SLOTS:
        if px not in cache:
            cache[px] = render(px)
        write_png(os.path.join(work, name), cache[px])

    subprocess.run(["/usr/bin/iconutil", "-c", "icns", work, "-o", dest_icns], check=True)
    if os.environ.get("KEEP_ICONSET") != "1":
        subprocess.run(["/bin/rm", "-rf", work], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
