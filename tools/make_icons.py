"""Generate the icons — a speaker glyph and a Watson "W" monogram.

Pure standard library (zlib + struct), so there is no Pillow dependency. Writes
PNGs for the Chrome extension, a multi-size .ico for the desktop window, and a
second .ico with the W monogram for the system tray.

    python tools/make_icons.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PNG_DIR = ROOT / "extension" / "icons"
ICO_PATH = ROOT / "app" / "readaloud.ico"
W_ICO_PATH = ROOT / "app" / "watson.ico"

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


def _dist_to_segment(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


# The W as four thick strokes, drawn a touch asymmetrically so the middle peak
# reads as a peak rather than a spike.
W_STROKE = 0.062
W_POINTS = [
    (0.215, 0.305),
    (0.360, 0.715),
    (0.500, 0.455),
    (0.640, 0.715),
    (0.785, 0.305),
]


def _in_w(x: float, y: float) -> bool:
    for (ax, ay), (bx, by) in zip(W_POINTS, W_POINTS[1:]):
        if _dist_to_segment(x, y, ax, ay, bx, by) <= W_STROKE:
            return True
    return False


def _sample(x: float, y: float, glyph=None):
    """Return an RGBA pixel for a point in the unit square."""
    if not _in_rounded_square(x, y):
        return (0, 0, 0, 0)
    hit = _in_w(x, y) if glyph == "w" else (_in_speaker(x, y) or _in_waves(x, y))
    if hit:
        return (*GLYPH, 255)
    return (*_lerp(BG_TOP, BG_BOTTOM, y), 255)


def render(size: int, glyph=None) -> list[list[tuple[int, int, int, int]]]:
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
                    pr, pg, pb, pa = _sample(x, y, glyph)
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

    speaker: dict[int, bytes] = {}
    for size in sorted(set(PNG_SIZES) | set(ICO_SIZES)):
        speaker[size] = to_png(render(size))

    for size in PNG_SIZES:
        path = PNG_DIR / f"icon{size}.png"
        path.write_bytes(speaker[size])
        print(f"wrote {path.relative_to(ROOT)} ({len(speaker[size]):,} bytes)")

    ico = to_ico({s: speaker[s] for s in ICO_SIZES})
    ICO_PATH.write_bytes(ico)
    print(f"wrote {ICO_PATH.relative_to(ROOT)} ({len(ico):,} bytes)")

    # The tray wants the W monogram: at 16px it stays legible where the speaker
    # glyph's arcs turn to mush.
    watson = {size: to_png(render(size, glyph="w")) for size in ICO_SIZES}
    w_ico = to_ico(watson)
    W_ICO_PATH.write_bytes(w_ico)
    print(f"wrote {W_ICO_PATH.relative_to(ROOT)} ({len(w_ico):,} bytes)")

    preview = PNG_DIR.parent.parent / "app" / "watson128.png"
    preview.write_bytes(watson[128])
    print(f"wrote {preview.relative_to(ROOT)} ({len(watson[128]):,} bytes)")


if __name__ == "__main__":
    main()
