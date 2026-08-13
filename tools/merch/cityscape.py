"""
The code, standing in a city that dissolves around it.

`isoqr` draws a QR as an isometric skyline. This puts that skyline in a wider
one: blocks on the same lattice, spreading outward from the code and fading into
the garment, so the thing you scan reads as one district of a larger city rather
than a graphic dropped on a shirt.

## The quiet zone is the whole design constraint

A QR needs clear ground around it — four modules, by spec — or a decoder cannot
find its edges. So the surround does not start at the code's border; it starts
well outside it, and everything between is left bare. That gap is not styling
and must not be closed to make the picture prettier. `MARGIN` is generous on
purpose: the spec minimum assumes a flat code with hard edges, and this one is a
field of little roofs whose silhouette is busier than a printed square.

`MARGIN` and `SPAN` were not chosen by eye. Both were swept against a decode of
the finished art on both garments, and then again across capture widths, because
the failure this design can have is not "looks wrong" — it is "reads fine on
screen and not off a shirt".

## Buildings hang

Straight from `isoqr._block`: a roof sits at z=0 and its walls drop to
z=−height, rather than the roof rising. That sign flip is what keeps every roof
registered to its module. It matters here too — it means a surround building can
never intrude UPWARD into the code's screen area no matter how tall it is, only
downward and away.
"""

import sys
from pathlib import Path

import segno

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stickers"))

import isoqr  # noqa: E402

# Clear cells between the code and the first building. Four is the QR spec's
# minimum quiet zone; this is nearly double it, because the surround's roofline
# is a far busier neighbour than white paper.
MARGIN = 6

# How far the city reaches past that, in cells. Generous, because the whole
# effect is a gradient — a short span reads as a frame drawn around the code
# rather than a district it happens to sit in.
SPAN = 18

# Height range for an ordinary block, and the occasional landmark that breaks
# the skyline. Kept below the code's own cube height so the district around it
# never out-shouts it.
LOW_H = (0.20, 0.34, 0.48, 0.62)
LANDMARK_H = 1.15
LANDMARK_EVERY = 23     # coprime with the strides below, so they never phase-lock


def _rand(seed: int):
    state = seed

    def nxt() -> float:
        nonlocal state
        state = (1103515245 * state + 12345) % (1 << 31)
        return state / (1 << 31)

    return nxt


def _fade_tones(ground: str, ink: str, fade: float) -> dict:
    """isoqr's tonal ladder, scaled toward the garment by `fade`."""
    return {
        "roof": isoqr.mix(ground, ink, isoqr.TONE_ROOF * fade),
        "left": isoqr.mix(ground, ink, isoqr.TONE_LEFT * fade),
        "right": isoqr.mix(ground, ink, isoqr.TONE_RIGHT * fade),
    }


def scene(url: str, ground: str, ink: str, *, seed: int = 20260813,
          ) -> tuple[str, tuple[float, float, float, float], int]:
    """
    The code plus its district. Same contract as `isoqr.iso_qr`.

    Returns (markup, bounds, module count), in grid units, so a caller scales
    the whole scene as one thing and the code's share of it stays fixed.
    """
    matrix = [list(r) for r in segno.make(url, error="h").matrix]
    n = len(matrix)
    k = isoqr.square_k(n, isoqr.CUBE_H)
    code_tones = isoqr.tones_for(ground, ink)
    rand = _rand(seed)

    lo, hi = -MARGIN, n + MARGIN
    cells = []

    for r in range(n):
        for c in range(n):
            if matrix[r][c]:
                cells.append((c, r, isoqr.CUBE_H, code_tones))

    for c in range(lo - SPAN, hi + SPAN):
        for r in range(lo - SPAN, hi + SPAN):
            # Inside the code or its quiet zone: nothing, ever.
            if lo <= c < hi and lo <= r < hi:
                continue
            # Euclidean distance from the quiet-zone box, so the district has
            # rounded corners. Chebyshev gives a square in grid space, which
            # this projection turns into a hard diamond — a frame, not a place.
            dx = max(lo - c, c - (hi - 1), 0)
            dy = max(lo - r, r - (hi - 1), 0)
            d = (dx * dx + dy * dy) ** 0.5
            if d > SPAN:
                continue

            # Dense and bright against the quiet zone, thinning and dimming all
            # the way out. Both knobs move together: fading tone alone leaves a
            # ghost grid at the rim, and thinning density alone leaves the last
            # survivors at full strength like litter.
            t = d / SPAN
            if rand() > 0.97 - 0.92 * t:
                continue
            fade = (1.0 - t) ** 1.25
            if fade <= 0.05:
                continue

            # Tall near the code, low further out — a downtown that falls away
            # to sheds. Heights stay under the code's own cube either way, so
            # the district never out-shouts the thing it surrounds.
            landmark = (c * 7 + r * 13) % LANDMARK_EVERY == 0 and t < 0.6
            base = LANDMARK_H if landmark else LOW_H[(c * 3 + r * 5) % len(LOW_H)]
            cells.append((c, r, base * (1.0 - 0.55 * t),
                          _fade_tones(ground, ink, fade)))

    # Painter's order over the WHOLE scene, code and district together: depth
    # increases with col + row, so a nearer block covers a farther one. Drawing
    # the two sets separately would let scenery paint over the code.
    cells.sort(key=lambda t: t[0] + t[1])
    out = [isoqr._block(c, r, h, tones, True, k)[0] for c, r, h, tones in cells]

    # Bounds cover the district, not the code. Nominal like isoqr's, so the
    # framing does not shift when a corner module or a random block flips.
    reach = SPAN + MARGIN
    x = (n + 2 * reach) * k
    return ("".join(out),
            (-x, -isoqr.CUBE_H - reach * 0.5, x, float(n) + reach),
            n)


def block_for_width(url: str, ground: str, ink: str, width: float, **kw
                    ) -> tuple[str, float, float]:
    """The scene scaled to `width`, its top-left at (0, 0)."""
    body, (x0, y0, x1, y1), _n = scene(url, ground, ink, **kw)
    s = width / (x1 - x0)
    markup = (f'<g transform="translate({-x0 * s:.3f},{-y0 * s:.3f}) '
              f'scale({s:.6f})">{body}</g>')
    return markup, width, (y1 - y0) * s
