"""
The Isometric Grid back — a city you can scan.

The front is the wordmark built as a city. The back is the same idea taken
literally: a QR code drawn as an isometric skyline, where a dark module is a
tower and a light one is low-rise, so the code and the city are the same object.

The entreaty is split either side of it, because the code is the answer to the
line above it and the line below tells you how to get it:

    ONE OF THESE
    BLOCKS IS MINE
    [ the city ]
    SCAN TO SEE WHICH

That works harder than "scan me". It says what the code is (a city), what the
wearer's stake in it is (one block), and what scanning buys you (which one) —
and it is true, because the code resolves to exactly one proposal.

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

HEADLINE = ["ONE OF THESE", "BLOCKS IS MINE"]
ENTREATY = "SCAN TO SEE WHICH"

HEAD_MEASURE = 2500
ENTREATY_MEASURE = 2100
QR_WIDTH = 2850

DEFAULT_URL = "HTTPS://CITYEDIT.ORG/S/K4M9X"


def tee_back_isogrid(ink: str, ground: str, *,
                     url: str | None = None) -> tuple[int, int, str]:
    url = url or DEFAULT_URL
    cap = face(600).cap_height / face(600).upem

    # Both headline lines at ONE size, set by the longer, so the block is a
    # rectangle of type rather than two lines of unrelated scale.
    head_size = size_to_fit(HEADLINE[1], HEAD_MEASURE, 600, 0.06)
    entreaty_size = size_to_fit(ENTREATY, ENTREATY_MEASURE, 400, 0.14)

    # The code is drawn against the garment, so a dark shirt gets light towers.
    tones = isoqr.tones_for(ground, ink)
    qr_body, qr_w, qr_h, pitch = isoqr.block_for_width(url, tones, QR_WIDTH)

    head_h = head_size * cap + head_size * 1.0
    gap_qr, gap_entreaty = 300, 300
    total = head_h + gap_qr + qr_h + gap_entreaty + entreaty_size * cap
    top = (H - total) / 2

    body = []
    y = top + head_size * cap
    for i, line in enumerate(HEADLINE):
        if i:
            y += head_size * 1.0
        body.append(text(line, W / 2, y, head_size, weight=600, fill=ink,
                         tracking=0.06, anchor="middle"))

    qr_y = y + gap_qr
    body.append(
        f'<g transform="translate({(W - qr_w) / 2:.2f},{qr_y:.2f})">{qr_body}</g>'
    )

    y = qr_y + qr_h + gap_entreaty + entreaty_size * cap
    body.append(text(ENTREATY, W / 2, y, entreaty_size, weight=400, fill=ink,
                     tracking=0.14, anchor="middle"))

    return W, H, "".join(body)


def module_pitch_in(url: str = DEFAULT_URL) -> float:
    """The code's short module diagonal, in inches at print size."""
    tones = isoqr.tones_for("#ffffff", "#141414")
    _, _, _, pitch = isoqr.block_for_width(url, tones, QR_WIDTH)
    return pitch / 300.0


# Keep the linter honest about the helper this module leans on.
_ = is_dark
