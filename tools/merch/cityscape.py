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

One honest caveat, found by sweeping and not fixable by tuning: there is a
narrow band of capture widths — around 325–350 px across the whole print — where
some of these fail while 300 px and 375 px both pass. That is the aliasing
`tools/stickers/isoqr.py` documents: the rhombus edges run at 45° and beat
against the binariser's sampling at particular scales. It is a band, not a
floor, and it sits at a framing where a 12-inch print is a small part of a
photograph.

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

# Clear cells between the code and the first building — the boulevard the code's
# district sits inside. Wider and the code floats in a moat with no continuity to
# the city; narrower and it stops reading.
#
# Five, not the spec's four, and the extra cell is bought by the whole-cube
# buildings. At four, decodes failed across a broad span of capture widths; at
# five they fail only in one narrow band (see below). Dimming the near ring
# instead was tried and measured WORSE — the binariser needs that contrast to
# find the plaza's edge, so taking it away costs more than it buys.
MARGIN = 5

# How far the city reaches past the code, measured RADIALLY from its centre.
SPAN = 17

# The street plan. Roads run on the SAME integer lattice as the code, so the
# city and the thing it surrounds share one grid — which is what stops the
# surround reading as noise scattered around a graphic.
ROAD_EVERY = 7
ROAD_PHASE = 3

# Not every street survives. Roughly one road SEGMENT in four is built over,
# merging the two blocks either side into a superblock — done per segment
# rather than per cell so it reads as a block that was never cut through,
# rather than as rubble dropped in the road.
ROAD_KEPT = 4

# Two storeys, and nothing between. Every roof in the CODE sits at exactly one
# cube by construction — that is what makes it readable — so the city around it
# is built from the same unit: one cube or two, never 0.43 of one. Fractional
# heights read as a pile of oddments; whole ones read as architecture on the
# same grid as everything else.
HEIGHTS = (1.0, 1.0, 2.0)   # two thirds low, one third tall
LOT = 2



# Not every lot is built. Roughly one in seven is a plaza, a yard or a parking
# lot — which is what stops a regular street grid reading as a quilt. An
# ordered city is not a uniform one.
EMPTY_EVERY = 7


def _hash2(a: int, b: int, salt: int) -> int:
    """
    A scalar hash of two grid coordinates.

    Not `(a*3 + b*7) % n`. That form looks like a hash and is a diagonal stripe:
    with a stride sharing a factor with the modulus, one coordinate drops out
    entirely — the first version of this made every third ROW two storeys tall,
    the whole way along, which is exactly the clustering it was meant to avoid.
    """
    h = (a * 73856093) ^ (b * 19349663) ^ (salt * 83492791)
    h &= 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 1274126177) & 0xFFFFFFFF
    return h ^ (h >> 16)


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
    # The code's centre, and the radius at which the city may start: the quiet
    # zone stays a SQUARE ring, because a square code needs a square one — only
    # the fade is circular.
    mid = (n - 1) / 2.0
    inner = mid + MARGIN
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
            # RADIAL distance from the code's centre, not distance to its
            # square. Measuring to the box gives a rounded square, which this
            # projection turns into a diamond; measuring from the centre gives a
            # circle, and the projection very nearly preserves it — its two
            # basis vectors come out equal in length and all but orthogonal.
            radial = ((c - mid) ** 2 + (r - mid) ** 2) ** 0.5
            t = (radial - inner) / SPAN
            if t > 1.0:
                continue
            t = max(0.0, t)

            # Streets, on the code's own lattice — with some segments built
            # over. A segment is the run of road between two crossings, keyed by
            # which road it is and how far along, so the whole stretch goes or
            # stays together.
            on_v = c % ROAD_EVERY == ROAD_PHASE
            on_h = r % ROAD_EVERY == ROAD_PHASE
            if on_v and _hash2(c // ROAD_EVERY, r // ROAD_EVERY, 1) % ROAD_KEPT:
                continue
            if on_h and _hash2(r // ROAD_EVERY, c // ROAD_EVERY, 2) % ROAD_KEPT:
                continue

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
            if _hash2(lot[0], lot[1], 3) % EMPTY_EVERY == 0:
                continue
            height = HEIGHTS[_hash2(lot[0], lot[1], 4) % len(HEIGHTS)]
            cells.append((c, r, height, _fade_tones(ground, ink, fade)))

    # Painter's order over the WHOLE scene, code and district together: depth
    # increases with col + row, so a nearer block covers a farther one. Drawing
    # the two sets separately would let scenery paint over the code.
    cells.sort(key=lambda t: t[0] + t[1])
    out = [isoqr._block(c, r, h, tones, True, k)[0] for c, r, h, tones in cells]

    # Bounds cover the district, not the code. Nominal like isoqr's, so the
    # framing does not shift when a corner module or a random block flips.
    reach = SPAN
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
