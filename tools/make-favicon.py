#!/usr/bin/env python3
"""Generate the firehome favicon set from one source of truth.

Emits favicon.svg (scalable, what modern browsers actually use), favicon.png
and apple-touch-icon.png (raster fallbacks), and favicon.ico (for clients that
probe /favicon.ico blindly).

Everything derives from the PLATE/FLAME/INNER path data below, so the vector and
the rasters can never drift. Stdlib only — no Pillow, cairo or pip needed — so
this stays runnable anywhere: paths are flattened to polygons, filled with a
supersampled even-odd test, and encoded as PNG with zlib.

Run from anywhere; output goes to the repo root:

    python3 tools/make-favicon.py
"""

import os
import re
import struct
import sys
import zlib

# ── design ──────────────────────────────────────────────────────────────────
# A 64x64 grid: dark plate matching the dashboard background, two-tone warm
# flame on top. Recolour here — the two flame tones are the only colour choice.
#
# Two things stop this reading as a leaf, which a plain symmetric teardrop does:
# the tip leans right off-centre, and the upper-left edge curves *inward* for a
# licking silhouette rather than bulging out on both sides. The lighter inner
# flame is the other half of the cue, so keep both tones distinct at 16px.
SIZE = 64
BG = (0x22, 0x26, 0x2E, 0xFF)     # #22262e — dashboard background
OUTER = (0xE8, 0x80, 0x4A, 0xFF)  # #e8804a — flame body
INNER = (0xF5, 0xC9, 0x6B, 0xFF)  # #f5c96b — flame core

# Rounded-square plate, drawn with four arcs-as-beziers (r=14 of 64).
PLATE = (
    "M 14 0 L 50 0 C 57.7 0 64 6.3 64 14 L 64 50 C 64 57.7 57.7 64 50 64 "
    "L 14 64 C 6.3 64 0 57.7 0 50 L 0 14 C 0 6.3 6.3 0 14 0 Z"
)

# Widened 15% horizontally against an earlier, narrower draft: at a 16px tab
# size the slimmer flame shrank to an indistinct sliver on the plate.
FLAME = (
    "M 34.3 4 "
    "C 37.75 14 48.1 20 48.68 31 "     # tip out to the right shoulder
    "C 49.83 43 41.78 54 32 59 "       # down to the base
    "C 22.23 54 14.18 43.5 15.33 32 "  # round the bottom, back up the left
    "C 16.48 22 28.55 17 34.3 4 Z"     # concave lick returning to the tip
)

INNER_PATH = (
    "M 33.15 24 "
    "C 34.3 30 39.48 34.5 38.9 41 "
    "C 38.33 48 34.88 52.5 32 55 "
    "C 28.55 52.5 25.1 48 25.1 41 "
    "C 25.1 35 30.85 31 33.15 24 Z"
)

LAYERS = [(PLATE, BG), (FLAME, OUTER), (INNER_PATH, INNER)]

ICO_SIZES = (16, 32, 48)
PNG_SIZE = 32
APPLE_SIZE = 180
SUPERSAMPLE = 4
BEZIER_STEPS = 24


# ── path handling ───────────────────────────────────────────────────────────
def parse_path(d):
    """Flatten an SVG path of M/L/C/Z commands into a list of polygons.

    Only the subset used above is supported; anything else is a bug in the
    design data rather than an input to handle gracefully.
    """
    tokens = re.findall(r"[MLCZ]|-?\d+(?:\.\d+)?", d)
    polys, current, pos, start, i = [], [], (0.0, 0.0), (0.0, 0.0), 0

    def num():
        nonlocal i
        value = float(tokens[i])
        i += 1
        return value

    while i < len(tokens):
        cmd = tokens[i]
        i += 1
        if cmd == "M":
            if len(current) > 1:
                polys.append(current)
            pos = start = (num(), num())
            current = [pos]
        elif cmd == "L":
            pos = (num(), num())
            current.append(pos)
        elif cmd == "C":
            c1 = (num(), num())
            c2 = (num(), num())
            end = (num(), num())
            current.extend(flatten_cubic(pos, c1, c2, end))
            pos = end
        elif cmd == "Z":
            current.append(start)
            polys.append(current)
            current, pos = [], start
        else:
            raise ValueError("unsupported path command %r" % cmd)

    if len(current) > 1:
        polys.append(current)
    return polys


def flatten_cubic(p0, p1, p2, p3, steps=BEZIER_STEPS):
    out = []
    for step in range(1, steps + 1):
        t = step / steps
        u = 1 - t
        out.append((
            u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
            u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
        ))
    return out


def coverage(polys, size):
    """Per-pixel fill fraction via a supersampled even-odd crossing test."""
    scale = size / SIZE
    step = 1.0 / SUPERSAMPLE
    offsets = [(n + 0.5) * step for n in range(SUPERSAMPLE)]

    # Pre-scale and bucket edges by the scanlines they span, so each subsample
    # row only tests edges that can possibly cross it.
    rows = [[] for _ in range(size * SUPERSAMPLE)]
    for poly in polys:
        pts = [(x * scale, y * scale) for x, y in poly]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if y0 == y1:
                continue
            lo, hi = sorted((y0, y1))
            first = max(0, int(lo * SUPERSAMPLE - 1))
            last = min(size * SUPERSAMPLE - 1, int(hi * SUPERSAMPLE + 1))
            for r in range(first, last + 1):
                rows[r].append((x0, y0, x1, y1))

    total = SUPERSAMPLE * SUPERSAMPLE
    grid = [[0.0] * size for _ in range(size)]
    for py in range(size):
        for sy, dy in enumerate(offsets):
            y = py + dy
            edges = rows[py * SUPERSAMPLE + sy]
            if not edges:
                continue
            crossings = []
            for x0, y0, x1, y1 in edges:
                if (y0 <= y < y1) or (y1 <= y < y0):
                    crossings.append(x0 + (y - y0) / (y1 - y0) * (x1 - x0))
            if not crossings:
                continue
            crossings.sort()
            for px in range(size):
                for dx in offsets:
                    x = px + dx
                    if sum(1 for c in crossings if c > x) % 2:
                        grid[py][px] += 1.0 / total
    return grid


def render(size):
    """Composite the layers into a flat RGBA buffer."""
    px = [[(0, 0, 0, 0)] * size for _ in range(size)]
    for path, colour in LAYERS:
        cov = coverage(parse_path(path), size)
        cr, cg, cb, ca = colour
        for y in range(size):
            for x in range(size):
                a = cov[y][x] * (ca / 255.0)
                if a <= 0:
                    continue
                dr, dg, db, da = px[y][x]
                out_a = a + (da / 255.0) * (1 - a)
                if out_a <= 0:
                    continue
                blend = lambda s, d: (s * a + d * (da / 255.0) * (1 - a)) / out_a
                px[y][x] = (
                    int(round(blend(cr, dr))), int(round(blend(cg, dg))),
                    int(round(blend(cb, db))), int(round(out_a * 255)),
                )
    return px


# ── encoding ────────────────────────────────────────────────────────────────
def encode_png(px):
    size = len(px)
    raw = b"".join(
        b"\x00" + b"".join(struct.pack("4B", *px[y][x]) for x in range(size))
        for y in range(size)
    )

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def encode_ico(pngs):
    """ICO wrapping PNG payloads — supported everywhere that matters."""
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for size, blob in pngs:
        entries += struct.pack(
            "<BBBBHHII", size if size < 256 else 0, size if size < 256 else 0,
            0, 0, 1, 32, len(blob), offset,
        )
        blobs += blob
        offset += len(blob)
    return header + entries + blobs


def build_svg():
    layers = "\n".join(
        '  <path d="%s" fill="#%02x%02x%02x"%s/>'
        % (path, c[0], c[1], c[2],
           "" if c[3] == 255 else ' fill-opacity="%.3g"' % (c[3] / 255.0))
        for path, c in LAYERS
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d">\n'
        "  <title>firehome</title>\n%s\n</svg>\n" % (SIZE, SIZE, layers)
    )


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    written = []

    def write(name, data, mode="wb"):
        path = os.path.join(root, name)
        with open(path, mode) as fh:
            fh.write(data)
        written.append("%s (%d bytes)" % (name, len(data)))

    write("favicon.svg", build_svg(), "w")
    write("favicon.png", encode_png(render(PNG_SIZE)))
    write("apple-touch-icon.png", encode_png(render(APPLE_SIZE)))
    write("favicon.ico", encode_ico([(s, encode_png(render(s))) for s in ICO_SIZES]))

    for line in written:
        print("wrote " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
