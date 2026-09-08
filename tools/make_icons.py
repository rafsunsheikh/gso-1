#!/usr/bin/env python3
"""Render the GSO-1 mark to PNG, for the places that will not take an SVG.

iOS is the reason this exists. `apple-touch-icon` accepts PNG only, so a page
that offers an SVG gets no icon at all: "Add to Home Screen" then screenshots
the page and uses that, which is how GSO-1 ended up on a phone home screen
looking like a bookmark of itself.

Two details matter and neither is obvious:

* **Full bleed, square corners.** iOS applies its own rounded mask. Handing it
  an icon that is already a rounded square inside a transparent margin, which
  is what `desktop/assets/icon.png` is, gets that shape masked a second time:
  a small double-rounded tile floating on black. So the background here runs
  to the edge and the corners are left square.
* **No alpha.** Transparent pixels are composited against something the page
  does not choose. Everything is painted onto the background colour instead.

Geometry and colour follow static/favicon.svg exactly, on its 96-unit grid, so
the two marks stay the same mark. Run it when that SVG changes:

    python3 tools/make_icons.py
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "thecmanager" / "static"

BG = (0x14, 0x12, 0x1F)          # the SVG's rounded square
PURPLE = (0x50, 0x2C, 0xE7)
TEAL = (0x00, 0xE0, 0xB7)


def blend(fg: tuple, bg: tuple, a: float) -> tuple:
    """The SVG uses fill-opacity for two cells; resolve it up front."""
    return tuple(round(f * a + b * (1 - a)) for f, b in zip(fg, bg))


HALF = blend(PURPLE, BG, 0.5)

# x, y, w, h, r on the 96-grid, then the colour. Straight from the SVG.
CELLS = [
    (24, 24, 20, 20, 5, PURPLE),
    (52, 24, 20, 20, 5, HALF),
    (24, 52, 20, 20, 5, HALF),
    (52, 52, 20, 20, 5, TEAL),
]

GRID = 96
SS = 4          # supersample factor; the corners are curves and 1x is jagged


def render(size: int) -> bytes:
    """RGB bytes for a `size` x `size` icon."""
    big = size * SS
    scale = big / GRID
    buf = bytearray(bytes(BG) * (big * big))

    for cx, cy, cw, ch, cr, colour in CELLS:
        x0, y0 = cx * scale, cy * scale
        x1, y1 = (cx + cw) * scale, (cy + ch) * scale
        r = cr * scale
        col = bytes(colour)
        for py in range(int(y0), int(y1) + 1):
            if not (0 <= py < big):
                continue
            for pxx in range(int(x0), int(x1) + 1):
                if not (0 <= pxx < big):
                    continue
                # Inside a rounded rect: clamp to the inner rect, measure back.
                qx = min(max(pxx + 0.5, x0 + r), x1 - r)
                qy = min(max(py + 0.5, y0 + r), y1 - r)
                dx, dy = pxx + 0.5 - qx, py + 0.5 - qy
                if dx * dx + dy * dy <= r * r:
                    o = (py * big + pxx) * 3
                    buf[o:o + 3] = col

    # Box-downsample the supersampled buffer.
    out = bytearray()
    n = SS * SS
    for y in range(size):
        out.append(0)                     # PNG filter type 0 for this row
        for x in range(size):
            rs = gs = bs = 0
            for sy in range(SS):
                base = ((y * SS + sy) * big + x * SS) * 3
                for sx in range(SS):
                    o = base + sx * 3
                    rs += buf[o]; gs += buf[o + 1]; bs += buf[o + 2]
            out += bytes((rs // n, gs // n, bs // n))
    return bytes(out)


def write_png(path: Path, size: int, raw: bytes) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)   # 8-bit RGB
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    path.write_bytes(png)


def main() -> None:
    # 180 is what iOS asks for; 192 and 512 are what the web manifest wants.
    for size in (180, 192, 512):
        raw = render(size)
        out = OUT / f"icon-{size}.png"
        write_png(out, size, raw)
        print(f"  {out.relative_to(OUT.parent.parent)}  {out.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
