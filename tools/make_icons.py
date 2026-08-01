"""Generate the app/extension icons — a speaker glyph on a rounded indigo tile.

Pure standard library (zlib + struct), so there is no Pillow dependency. Writes
PNGs for the Chrome extension and a multi-size .ico for the desktop window.

    python tools/make_icons.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PNG_DIR = ROOT / "extension" / "icons"
ICO_PATH = ROOT / "app" / "readaloud.ico"

PNG_SIZES = (16, 32, 48, 128)
ICO_SIZES = (16, 32, 48, 64, 128, 256)

BG_TOP = (0x7C, 0x8A, 0xFF)
BG_BOTTOM = (0x4F, 0x46, 0xE5)
GLYPH = (0xFF, 0xFF, 0xFF)
SUPERSAMPLE = 4


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _in_rounded_square(x: float, y: float) -> bool:
    """Squircle-ish tile covering most of the canvas."""
    pad, radius = 0.055, 0.22
    lo, hi = pad, 1.0 - pad
    if not (lo <= x <= hi and lo <= y <= hi):
        return False
    cx = min(max(x, lo + radius), hi - radius)
    cy = min(max(y, lo + radius), hi - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius**2


def _in_speaker(x: float, y: float) -> bool:
    # Rectangular throat.
    if 0.255 <= x <= 0.40 and 0.395 <= y <= 0.605:
        return True
    # Cone flaring out to the right.
    if 0.40 <= x <= 0.575:
        half = 0.105 + (x - 0.40) / (0.575 - 0.40) * 0.155
        if abs(y - 0.5) <= half:
            return True
    return False


def _in_waves(x: float, y: float) -> bool:
    if x < 0.60:
        return False
    dx, dy = x - 0.545, y - 0.5
    dist = (dx * dx + dy * dy) ** 0.5
    if dist == 0 or abs(dy) / dist > 0.72:  # keep the arcs inside ±46 degrees
        return False
    return (0.135 <= dist <= 0.175) or (0.225 <= dist <= 0.265)


def _sample(x: float, y: float):
    """Return an RGBA pixel for a point in the unit square."""
    if not _in_rounded_square(x, y):
        return (0, 0, 0, 0)
    if _in_speaker(x, y) or _in_waves(x, y):
        return (*GLYPH, 255)
    return (*_lerp(BG_TOP, BG_BOTTOM, y), 255)


def render(size: int) -> list[list[tuple[int, int, int, int]]]:
    """Supersampled render, so edges and arcs stay smooth at 16px."""
    rows = []
    step = 1.0 / (size * SUPERSAMPLE)
    for py in range(size):
        row = []
        for px in range(size):
            r = g = b = a = 0
            for sy in range(SUPERSAMPLE):
                for sx in range(SUPERSAMPLE):
                    x = (px * SUPERSAMPLE + sx + 0.5) * step
                    y = (py * SUPERSAMPLE + sy + 0.5) * step
                    pr, pg, pb, pa = _sample(x, y)
                    # Weight colour by coverage so transparent samples do not
                    # darken the edge.
                    r += pr * pa
                    g += pg * pa
                    b += pb * pa
                    a += pa
            if a == 0:
                row.append((0, 0, 0, 0))
            else:
                n = SUPERSAMPLE * SUPERSAMPLE
                row.append((round(r / a), round(g / a), round(b / a), round(a / n)))
        rows.append(row)
    return rows


def _chunk(tag: bytes, data: bytes) -> bytes:
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


def to_png(rows) -> bytes:
    size = len(rows)
    raw = b"".join(
        b"\x00" + b"".join(struct.pack("BBBB", *px) for px in row) for row in rows
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def to_ico(images: dict[int, bytes]) -> bytes:
    """ICO holding PNG-compressed entries (supported since Vista)."""
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    entries, blobs = b"", b""
    for size, png in sorted(images.items()):
        entries += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,
            0 if size >= 256 else size,
            0,
            0,
            1,
            32,
            len(png),
            offset,
        )
        blobs += png
        offset += len(png)
    return header + entries + blobs


def main() -> None:
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    ICO_PATH.parent.mkdir(parents=True, exist_ok=True)

    cache: dict[int, bytes] = {}
    for size in sorted(set(PNG_SIZES) | set(ICO_SIZES)):
        cache[size] = to_png(render(size))

    for size in PNG_SIZES:
        path = PNG_DIR / f"icon{size}.png"
        path.write_bytes(cache[size])
        print(f"wrote {path.relative_to(ROOT)} ({len(cache[size]):,} bytes)")

    ico = to_ico({s: cache[s] for s in ICO_SIZES})
    ICO_PATH.write_bytes(ico)
    print(f"wrote {ICO_PATH.relative_to(ROOT)} ({len(ico):,} bytes)")


if __name__ == "__main__":
    main()
