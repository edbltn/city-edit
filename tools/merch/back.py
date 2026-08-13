"""
The Isometric Grid back — a city you can scan.

A QR code drawn as an isometric skyline — a dark module is a tower, a light one
is low-rise — under two words:

    PLS FIX
    [ the city ]

Two words and a city is the whole back. It reads as a text message to a
municipality, which is the correct register for a complaint about one crossing,
and it needs no instruction to scan: a code this size on a person's back is
self-evidently there to be pointed at.

The projection is `tools/stickers/isoqr.py`, not the front's true 30° isometric.
Its horizontal squeeze makes the grid a square diamond rather than one 1.73×
wider than tall, which is the right footprint for a back print and, on a
sticker, the reason the design exists at all. Every constant in there was set by
decoding finished art rather than by eye; this reuses it rather than forking it,
so there is one isometric-code design in the repo and not two that drift.
"""

import sys
from pathlib import Path

from palette import is_dark
from typo import face, size_to_fit, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stickers"))

import isoqr  # noqa: E402

# 12 × 15 in. A full back print, sized so the code alone clears nine inches —
# at that pitch scannability stops being a design constraint and the layout can
# be decided on how it looks.
W, H = 3600, 4500

HEADLINE = "PLS FIX"

HEAD_MEASURE = 2600
QR_WIDTH = 2900

DEFAULT_URL = "HTTPS://CITYEDIT.ORG/S/K4M9X"


def tee_back_isogrid(ink: str, ground: str, *,
                     url: str | None = None) -> tuple[int, int, str]:
    url = url or DEFAULT_URL
    cap = face(600).cap_height / face(600).upem
    head_size = size_to_fit(HEADLINE, HEAD_MEASURE, 600, 0.08)

    # The code is drawn against the garment, so a dark shirt gets light towers.
    tones = isoqr.tones_for(ground, ink)
    qr_body, qr_w, qr_h, _pitch = isoqr.block_for_width(url, tones, QR_WIDTH)

    gap = 260
    total = head_size * cap + gap + qr_h
    top = (H - total) / 2

    y = top + head_size * cap
    body = [
        text(HEADLINE, W / 2, y, head_size, weight=600, fill=ink,
             tracking=0.08, anchor="middle"),
        f'<g transform="translate({(W - qr_w) / 2:.2f},{y + gap:.2f})">{qr_body}</g>',
    ]
    return W, H, "".join(body)


def module_pitch_in(url: str = DEFAULT_URL) -> float:
    """The code's short module diagonal, in inches at print size."""
    tones = isoqr.tones_for("#ffffff", "#141414")
    _, _, _, pitch = isoqr.block_for_width(url, tones, QR_WIDTH)
    return pitch / 300.0


# Keep the linter honest about the helper this module leans on.
_ = is_dark
