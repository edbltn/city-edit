"""
I ♥ THIS CITY (BUT I HAVE ONE NOTE) — the QR tee.

A souvenir shirt from ten feet and a correction from five. The love is the
premise, which is what lets the note stay small: the wearer isn't a zealot with
a cause, they're a fan with one amendment. An earlier draft read SINGLE ISSUE
VOTER and landed as a threat — a promise to corner you at a party — which is
the opposite of what a pet peeve actually feels like.

Nothing on the shirt names a corner, a block or a street: the code can point at
one crossing or a two-mile corridor, so the QR has to be the noun. "One note"
is also the whole joke, which is why there is no line under the code. It would
be a second punchline for a shirt that already landed one.
"""

import sys
from pathlib import Path

from typo import face, size_to_fit, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stickers"))

import isoqr  # noqa: E402

# 11 × 11 in. The composition is near-square, and a taller canvas would just be
# empty print area that the uploader scales the artwork down to fill.
W, H = 3300, 3300
# Wider than the flat code it replaced. A diamond reads smaller than its
# bounding box — the corners are empty — so matching the old square's width
# would have left it looking like an afterthought under the type.
QR_SIZE = 1850

HEART = "@"                 # stands in for the mark inside a monospaced line
LINE = "I @ THIS CITY"
TURN = "BUT I HAVE ONE NOTE"

LINE_MEASURE = 2500
TURN_MEASURE = 2400

# The Donate heart from client-react NavRail/icons.tsx, on its 24×24 grid.
# Deliberately the only curve in an otherwise square icon set — which is what
# makes it unmistakably ours, and unmistakably not the heart on the souvenir
# shirt this is quoting.
HEART_PATH = "M12 20.4 L4.4 12.8 A5 5 0 0 1 12 7.5 A5 5 0 0 1 19.6 12.8 Z"
HEART_BOX = (3.6, 6.2, 20.4, 20.4)   # x0, y0, x1, y1, including the arc bulge

# Short and upper-case on purpose. Upper-case URLs encode in QR's alphanumeric
# mode rather than byte mode: HTTPS://CITYEDIT.ORG/S/K4M9X is a 29×29 code where
# the same link lower-case is 33×33 and a deep link with coordinates is 45×45.
# Coarser modules survive ink spread and a creased chest. See tools/stickers,
# whose /s/<code> route is case-insensitive for exactly this reason.
DEFAULT_URL = "HTTPS://CITYEDIT.ORG/S/K4M9X"


def heart(x: float, baseline: float, size: float, cell: float, fill: str) -> str:
    """The mark, set as if it were a glyph: one advance wide, sitting on the baseline."""
    f = face(600)
    cap = f.cap_height / f.upem * size
    hx0, hy0, hx1, hy1 = HEART_BOX
    # Scaled to cap height and allowed to overflow its advance sideways. A
    # cap-height heart is ~1.4 cells wide and no monospace cell can hold it, but
    # both neighbours in "I @ THIS" are spaces, so the room is already there.
    # Clamping it to one advance instead is what turned it into a bullet.
    scale = cap * 1.02 / (hy1 - hy0)
    left = x + (cell - (hx1 - hx0) * scale) / 2
    return (
        f'<g transform="translate({left - hx0 * scale:.2f},'
        f'{baseline - hy1 * scale:.2f}) scale({scale:.5f})">'
        f'<path d="{HEART_PATH}" fill="{fill}"/></g>'
    )


def mono_line(s: str, x: float, baseline: float, size: float, fill: str,
              tracking: float = 0.0, weight: int = 600,
              anchor: str = "start") -> str:
    """
    A line of monospace in which HEART sets as one more glyph.

    Drawn character by character rather than as a single run, because the mark
    has to occupy exactly one advance — anything else and the line stops being
    monospaced right where the eye is looking.
    """
    f = face(weight)
    advance = size * f.advance / f.upem
    step = advance + size * tracking
    width = len(s) * step - size * tracking
    origin = x - width / 2 if anchor == "middle" else x

    out = []
    for i, ch in enumerate(s):
        cx = origin + i * step
        if ch == HEART:
            out.append(heart(cx, baseline, size, advance, fill))
        elif ch != " ":
            out.append(text(ch, cx, baseline, size, weight=weight, fill=fill))
    return "".join(out)


def tee_one_note(ink: str, ground: str, *,
                 url: str | None = None) -> tuple[int, int, str]:
    # Resolved here, not defaulted in the signature, so --qr-url can rebind the
    # module global and have it take effect.
    url = url or DEFAULT_URL

    cap = face(600).cap_height / face(600).upem
    head_size = size_to_fit(LINE, LINE_MEASURE, 600, 0.06)
    turn_size = size_to_fit(TURN, TURN_MEASURE, 400, 0.10)

    # The same isometric code the back carries. It needs no light panel behind
    # it — its light half IS the garment — so on a black shirt this is a city
    # of pale towers rather than a white sticker, which is both less ink and
    # much more the thing the shirt is about.
    tones = isoqr.tones_for(ground, ink)
    qr_body, qr_w, qr_h, _pitch = isoqr.block_for_width(url, tones, QR_SIZE)

    # Measure the whole column, then centre it. Laying out from a fixed top
    # leaves the slack at the bottom, which reads as a design that ran out.
    gap_turn, gap_qr, gap_url = 240, 170, 120
    total = (head_size * cap + gap_turn + turn_size * cap + gap_qr
             + qr_h + gap_url + 64)
    top = (H - total) / 2 - 30          # bias up; a chest print reads better high

    body = []
    y = top + head_size * cap
    body.append(mono_line(LINE, W / 2, y, head_size, ink,
                          tracking=0.06, anchor="middle"))

    y += gap_turn + turn_size * cap
    body.append(text(TURN, W / 2, y, turn_size, weight=400, fill=ink,
                     tracking=0.10, anchor="middle"))

    qr_y = y + gap_qr
    body.append(
        f'<g transform="translate({(W - qr_w) / 2:.2f},{qr_y:.2f})">{qr_body}</g>'
    )
    body.append(
        text("CITYEDIT.ORG", W / 2, qr_y + qr_h + gap_url, 64, fill=ink,
             tracking=0.42, anchor="middle", opacity=0.5)
    )
    return W, H, "".join(body)


