"""Generate the app icon. Pure stdlib: writes PNGs by hand, then iconutil."""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import zlib

SIZE = 1024
BG = (22, 26, 32)
FG = (126, 231, 135)
DIM = (86, 96, 110)


def png(path: str, pixels: bytearray, size: int) -> None:
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter type 0
        raw += pixels[y * size * 4 : (y + 1) * size * 4]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)))
        fh.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        fh.write(chunk(b"IEND", b""))


def draw() -> bytearray:
    s = SIZE
    px = bytearray(s * s * 4)
    radius = int(s * 0.22)

    def put(x: int, y: int, color, alpha=255) -> None:
        if 0 <= x < s and 0 <= y < s:
            i = (y * s + x) * 4
            px[i], px[i + 1], px[i + 2], px[i + 3] = color[0], color[1], color[2], alpha

    # Rounded-rectangle background.
    for y in range(s):
        for x in range(s):
            cx = min(max(x, radius), s - radius)
            cy = min(max(y, radius), s - radius)
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius:
                put(x, y, BG)

    def thick_line(x0, y0, x1, y1, width, color):
        steps = int(max(abs(x1 - x0), abs(y1 - y0)) * 2) + 1
        half = width // 2
        for step in range(steps + 1):
            t = step / steps
            cx = round(x0 + (x1 - x0) * t)
            cy = round(y0 + (y1 - y0) * t)
            for dy in range(-half, half + 1):
                for dx in range(-half, half + 1):
                    if dx * dx + dy * dy <= half * half:
                        put(cx + dx, cy + dy, color)

    # A prompt chevron and cursor bar: ">_"
    w = int(s * 0.075)
    thick_line(int(s * 0.28), int(s * 0.31), int(s * 0.50), int(s * 0.50), w, FG)
    thick_line(int(s * 0.50), int(s * 0.50), int(s * 0.28), int(s * 0.69), w, FG)
    thick_line(int(s * 0.58), int(s * 0.69), int(s * 0.76), int(s * 0.69), w, FG)

    # A faint second chevron behind, suggesting a restored session.
    thick_line(int(s * 0.60), int(s * 0.25), int(s * 0.72), int(s * 0.355), int(w * 0.6), DIM)
    return px


def main(dest_icns: str) -> int:
    work = dest_icns + ".iconset"
    os.makedirs(work, exist_ok=True)
    master = os.path.join(work, "icon_512x512@2x.png")
    png(master, draw(), SIZE)
    for size in (16, 32, 128, 256, 512):
        for scale, suffix in ((1, ""), (2, "@2x")):
            px = size * scale
            if size == 512 and scale == 2:
                continue
            out = os.path.join(work, f"icon_{size}x{size}{suffix}.png")
            subprocess.run(
                ["/usr/bin/sips", "-z", str(px), str(px), master, "--out", out],
                capture_output=True,
                check=True,
            )
    subprocess.run(
        ["/usr/bin/iconutil", "-c", "icns", work, "-o", dest_icns], check=True
    )
    subprocess.run(["/bin/rm", "-rf", work], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
