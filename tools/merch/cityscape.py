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

# Clear cells between the code and the first building — the QR spec's minimum
# quiet zone exactly. It reads as the boulevard the code's district sits inside,
# which is the point: any wider and the code floats in a moat with no continuity
# to the city around it.
MARGIN = 4

# How far the city reaches past that, in cells.
SPAN = 16

# The street plan. Roads run on the SAME integer lattice as the code, so the
# city and the thing it surrounds share one grid — which is what stops the
# surround reading as noise scattered around a graphic.
ROAD_EVERY = 7
ROAD_PHASE = 3

# Buildings are 2×2 cells, so a roof is a roof rather than a single module
# pretending to be one.
LOT = 2

# Heights. Every roof in the CODE sits at one height by construction, which is
# what makes it readable; the city around it varies, which is what makes it a
# city. Kept under the code's own cube so the district never out-shouts it.
LOW_H = (0.18, 0.30, 0.44, 0.60, 0.78, 0.26, 0.52, 0.94)
LANDMARK_H = 1.45
LANDMARK_EVERY = 9

# Not every lot is built. Roughly one in seven is a plaza, a yard or a parking
# lot — which is what stops a regular street grid reading as a quilt. An
# ordered city is not a uniform one.
EMPTY_EVERY = 7


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
            # rounded corners rather than the hard diamond a Chebyshev radius
            # turns into under this projection.
            dx = max(lo - c, c - (hi - 1), 0)
            dy = max(lo - r, r - (hi - 1), 0)
            d = (dx * dx + dy * dy) ** 0.5
            if d > SPAN:
                continue

            # Streets. A cell on a road stays empty, which is what gives the
            # district blocks instead of a solid field — and the roads run on
            # the code's own lattice, so the two read as one plan.
            if c % ROAD_EVERY == ROAD_PHASE or r % ROAD_EVERY == ROAD_PHASE:
                continue

            t = d / SPAN
            fade = (1.0 - t) ** 1.15
            if fade <= 0.06:
                continue
            # Density is left alone until the outer third. Thinning it earlier
            # is what made this look scattered: a city does not lose buildings
            # at random, it just gets further away.
            if t > 0.66 and rand() > (1.0 - t) / 0.34:
                continue

            # One height per 2×2 lot, so four cells share a roof and read as a
            # single building rather than four separate posts.
            lot = (c // LOT, r // LOT)
            if (lot[0] * 11 + lot[1] * 4) % EMPTY_EVERY == 0:
                continue
            landmark = (lot[0] * 5 + lot[1] * 9) % LANDMARK_EVERY == 0 and t < 0.6
            base = (LANDMARK_H if landmark
                    else LOW_H[(lot[0] * 3 + lot[1] * 7) % len(LOW_H)])
            cells.append((c, r, base * (1.0 - 0.4 * t),
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
