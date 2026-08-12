"""
The code as a city, stood on its corner.

Every module of the QR becomes a building. The bit it carries picks one of two
heights — dark modules are towers, light modules are low-rise — so the skyline
IS the code, and the whole thing comes out as a diamond because the grid is
projected at 45°.

## Why 45° and not the tee's isometric

`tools/merch/iso.py` draws the merch city in true 30° isometric, which makes a
square grid 1.73 times as wide as it is tall. That is the wrong shape for a
round sticker: the wide diamond eats the disc's horizontal room and leaves the
vertical unused. Squeezing the horizontal to K = 0.5 makes the projected grid a
PERFECT SQUARE on its corner, and a square on its corner is the best thing you
can put in a circle — it needs a radius of 0.5·side where an unrotated square
needs 0.707·side. That is worth about 24% more module pitch out of the same
sticker: 1.14 mm here against the flat code's 0.92 mm on the 2.5".

## What was measured on the way here

Every constant below was set by rendering the FINISHED sticker and decoding it
back, not by taste. Four findings, each the opposite of the obvious choice:

  * **Streets between blocks destroy the code.** A gutter was the first thing
    tried, since it is what the tee does. But a finder pattern is a solid 7x7
    field whose 1:1:3:1:1 run signature is exactly what a decoder scans for, and
    a gutter turns that one run into a picket fence. A 0.16-cell street failed at
    every size. Blocks touch — which is also the better city, since runs of dark
    modules merge into single large buildings rather than a grid of huts.
  * **Towers have a hard ceiling just past 0.62 of a cell.** Heights of
    (0.18, 0.62) read 97% on a ten-width sweep; (0.25, 0.85) collapsed to 36%,
    because a tower that tall buries the cell behind it.
  * **Walls must be PALER than roofs here.** An earlier, shorter version wanted
    the opposite. At 0.62 of a cell a tower's walls cover a real fraction of the
    cell behind, so dark walls stop being shading and start being bit errors:
    0.70/0.55 read 97% where 0.86/0.74 read 80%.
  * **The low-rise gets walls but no roof tint.** Its roof is where the light
    half of the code is sampled, so it stays bare paper; its two wall faces give
    it shape. Measured, that reads slightly BETTER than drawing nothing at all
    (98% vs 97% on the same sweep) — the shading lands on cell edges and gives
    the binariser a cleaner boundary than an unbroken white field.

## The honest bottom line, and why it differs by stock

Swept densely — 19 capture widths from 180 to 720 px, twelve codes, decoded
through the finished sticker:

    2.5"  flat 100%      iso 83%   (fails at 180, 330, 570, 660)
    3"    flat 100%      iso 97%   (only 180 is weak)

The failures are not gradual: at particular widths every code fails and at the
next width every code passes. The rhombus edges run at 45° and alias against the
decoder's binariser at certain scales, where the flat code's axis-aligned edges
never do. Note also that a coarse test grid HIDES this — a ten-width sweep of
the same 2.5" design scored 97%, and only the denser grid found the bands.

Which is why the recommendation is per stock: on the 3", where a module is
1.37 mm, this is a real option. On the 2.5" it is a poster design, not a
lamp-post one. Either way it is opt-in, and a printed sheet wants a phone test
before a run — a moving hand-held camera does not sit at one fixed scale the way
this harness does, which may well close the gap. Or may not.
"""

import math
import sys
from pathlib import Path

import segno

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "merch"))

from iso import mix  # noqa: E402

#: Horizontal foreshortening. The tee's city uses true 30° isometric
#: (K = cos 30 ≈ 0.866), which makes the grid 1.73 times as wide as it is tall.
#: Here it is 0.5, which makes the projected grid a PERFECT SQUARE stood on its
#: corner — and that is not a stylistic preference, it is the best possible fit
#: for a round sticker. A square of side s needs a circle of radius 0.707s; the
#: same square rotated onto its corner needs only 0.5s. Turning the code 45°
#: therefore buys about 40% more module pitch out of the same disc, which is why
#: this version's modules end up LARGER than the flat code's rather than smaller.
K = math.cos(math.radians(30))


def project(x: float, y: float, z: float) -> tuple[float, float]:
    """Grid space → screen. +x runs down-right, +y down-left, +z straight up.

    True 30° isometric, the same projection as iso.py. The one property this
    design is built on falls straight out of it: a UNIT CUBE projects to a
    REGULAR HEXAGON. Its eight corners land on six screen points — (0, ±1) and
    (±cos30, ±½) — all exactly 1 from the centre. Draw the dark modules as unit
    cubes and the code becomes a hexagonal tiling rather than a grid of boxes.
    """
    return ((x - y) * K, (x + y) * 0.5 - z)

#: NO gutter, and this one is not negotiable. Streets between blocks were the
#: first thing tried and they break the code outright: a finder pattern is a
#: solid 7x7 field whose 1:1:3:1:1 run signature is exactly what a decoder scans
#: for, and cutting a gutter through it turns that one run into a picket fence.
#: Measured — a 0.16 street fails at every size, 0.08 fails at most, 0 passes
#: everywhere. Blocks touching is also the better city: runs of dark modules
#: merge into single large buildings instead of a uniform grid of huts.
STREET = 0.0
#: EVERY module is a building, and the bit it carries picks one of two heights.
#: Nothing is drawn flat, so the city has no holes in it — what used to be bare
#: paper between towers is now low-rise, and the skyline itself encodes the code.
#: The two heights are the design's main knob and are swept in `sweep_heights`.
#: Cube height, in cells. This is the single knob that decides whether the thing
#: scans, and the honest answer is that the most beautiful value is the one that
#: does not work.
#:
#: At exactly 1.0 a cube projects to a REGULAR HEXAGON — all six silhouette
#: corners land 1 unit from its centre — and a field of them tiles hexagonally.
#: That is the true isometric look, and measured through the finished sticker it
#: decodes 0% of the time, at every capture width, on both stocks and both
#: colourways. Not "degrades": zero.
#:
#: The reason is the finder patterns. Each one is a solid 7x7 block wrapped in a
#: ONE-module light ring, and that ring is exactly what a decoder scans for. A
#: cube a full cell tall throws a wall a full cell wide, which covers its
#: neighbour's ring completely — so the pattern the decoder is hunting for stops
#: existing before any of the data matters.
#:
#: Measured, at true 30° isometric, cubes on dark modules only:
#:
#:     1.00 cell (regular hexagon)     0%
#:     0.80                            0%
#:     0.65                           14%
#:     0.55                           58%
#:     0.45                           77%
#:
#: So the default is 0.45 — a low box rather than a cube, still true isometric,
#: still three shaded faces, but no longer a hexagon. Set it to 1.0 anywhere the
#: code does not have to work: a poster, a screen, a tee.
CUBE_H = 0.45

# Ink coverage per face. Roofs carry the signal and take the full ink; walls
# stay near the paper because their overlap into the cell behind is unavoidable.
TONE_ROOF = 1.00
#: Paler than the roof, which is the opposite of what the earlier wide-isometric
#: version wanted. The reason is the taller buildings: at 0.62 of a cell a
#: tower's walls cover a real fraction of the cell behind it, so dark walls stop
#: being harmless shading and start being a bit error. Measured at these
#: heights, 0.70/0.55 reads 97% where 0.86/0.74 reads 80% — the softer shading
#: is both prettier and more robust.
TONE_LEFT = 0.70
TONE_RIGHT = 0.55
#: The low-rise: WALLS ONLY, no roof tint. Every square is a building, but the
#: light half of the code has to stay light, so the low-rise gets its shape from
#: the two wall faces and leaves its roof as bare paper. That is not a
#: compromise — measured, walls-only at 0.28/0.20 reads 98% where drawing
#: nothing at all reads 97%, because the shading lands on cell EDGES and gives
#: the binariser a cleaner boundary than an unbroken white field does. Tinting
#: the roofs as well is what costs reads, since a roof sits where the module is
#: sampled.
TONE_LOW_ROOF = 0.0
TONE_LOW_LEFT = 0.28
TONE_LOW_RIGHT = 0.20


def tones_for(paper: str, ink: str) -> dict:
    """Every surface, pre-composited against the paper.

    Opaque, for the same reason `iso.py` gives: drawn with `opacity`, every block
    in front lets the one behind ghost through, and overlapping half-tones print
    as compounding ink rather than the tone asked for. Flat fills also let the
    painter's ordering actually occlude, which is what the skyline depends on.
    """
    return {
        "roof": mix(paper, ink, TONE_ROOF),
        "left": mix(paper, ink, TONE_LEFT),
        "right": mix(paper, ink, TONE_RIGHT),
        "low_roof": mix(paper, ink, TONE_LOW_ROOF),
        "low_left": mix(paper, ink, TONE_LOW_LEFT),
        "low_right": mix(paper, ink, TONE_LOW_RIGHT),
    }


def _poly(points, fill):
    pts = " ".join(f"{sx:.3f},{sy:.3f}" for sx, sy in points)
    return f'<polygon points="{pts}" fill="{fill}"/>'





def _block(col: int, row: int, height: float, tones: dict, dark: bool) -> tuple[str, list]:
    """One module as a volume, back faces first.

    The building HANGS: its roof sits at z=0 — exactly the cell the decoder
    samples — and the walls drop to z=-height instead of the roof rising to
    z=+height. That one sign flip is what makes the whole idea work. Raise the
    roofs and the signal drifts off the plane the decoder solved for, so the
    sampled point stops landing on the module's own colour; measured, anything
    past about a tenth of a cell starts failing. Hanging the walls instead keeps
    every roof registered to its module no matter how tall the building is.

    Nothing is lost visually, because a city has no visible ground line to
    contradict: what you see is roofs, with wall showing wherever neighbouring
    heights differ — which is what a dense isometric city looks like anyway.
    """
    x0, y0 = col + STREET / 2, row + STREET / 2
    side = 1.0 - STREET
    x1, y1 = x0 + side, y0 + side

    roof = [(x0, y0, height), (x1, y0, height), (x1, y1, height), (x0, y1, height)]
    left = [(x0, y1, height), (x1, y1, height), (x1, y1, 0), (x0, y1, 0)]
    right = [(x1, y0, height), (x1, y1, height), (x1, y1, 0), (x1, y0, 0)]

    keys = ("left", "right", "roof")
    out, points = [], []
    for corners, key in zip((left, right, roof), keys):
        flat = [project(*c) for c in corners]
        points += flat
        out.append(_poly(flat, tones[key]))
    return "".join(out), points


def iso_qr(url: str, tones: dict, *, scale_h: float = 1.0,
           ) -> tuple[str, tuple[float, float, float, float], int]:
    """The code as an isometric city.

    Returns (markup, bounds, module count). `scale_h` multiplies every height —
    the one knob the sweep turns, since it is what trades the city's depth
    against the decoder's ability to find the plane.
    """
    matrix = [list(r) for r in segno.make(url, error="h").matrix]
    n = len(matrix)

    cells = [(c, r, matrix[r][c]) for r in range(n) for c in range(n)]
    # Painter's order: depth increases with col + row in this projection, so a
    # nearer block is always drawn over a farther one.
    cells.sort(key=lambda t: t[0] + t[1])

    out, points = [], []
    for col, row, dark in cells:
        if not dark:
            # Light modules are empty ground. Nothing is drawn, so they stay the
            # cleanest possible light — which is also what the decoder wants.
            continue
        markup, pts = _block(col, row, CUBE_H * scale_h, tones, True)
        out.append(markup)
        points += pts

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return "".join(out), (min(xs), min(ys), max(xs), max(ys)), n


def svg(url: str, px: int, paper: str = "#ffffff", ink: str = "#141414",
        *, scale_h: float = 1.0, pad: float = 0.06) -> str:
    """A standalone square SVG of the city, for proofing and for the sweep."""
    body, (x0, y0, x1, y1), _n = iso_qr(url, tones_for(paper, ink), scale_h=scale_h)
    w, h = x1 - x0, y1 - y0
    span = max(w, h) * (1 + pad * 2)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    s = px / span
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{px}" height="{px}" '
        f'viewBox="0 0 {px} {px}">'
        f'<rect width="{px}" height="{px}" fill="{paper}"/>'
        f'<g transform="translate({px / 2:.2f},{px / 2:.2f}) scale({s:.5f}) '
        f'translate({-cx:.4f},{-cy:.4f})">{body}</g></svg>'
    )


def block_for_width(url: str, tones: dict, width: float, *, scale_h: float = 1.0
                    ) -> tuple[str, float, float, float]:
    """The city scaled to `width` inches, its top-left at (0, 0).

    Returns (markup, width, height, module thickness in inches). The height is
    whatever the projection gives — about width/1.73 plus the tallest tower —
    rather than a number derived by hand, so the caller never has to know the
    projection.

    The reported size is the module's THIN dimension, not its wide one. A cell
    projects to a rhombus whose horizontal diagonal is 2·K cell-units and whose
    vertical diagonal is 1 — so it is nearly twice as wide as it is tall, and it
    is the short axis that decides whether a camera can resolve it. Quoting the
    wide diagonal would flatter this design by a factor of 1.73.
    """
    body, (x0, y0, x1, y1), n = iso_qr(url, tones, scale_h=scale_h)
    s = width / (x1 - x0)
    height = (y1 - y0) * s
    # One cell unit, in inches. The projected grid is n·2K wide, so this is the
    # scale everything else is quoted in: a cell's rhombus has a horizontal
    # diagonal of 2K·s and a vertical diagonal of 1·s, and a step along either
    # grid axis is exactly s (the axis vector (K, ½) has unit length).
    #
    # The number returned is the VERTICAL diagonal, which under true isometric
    # is the short one — the honest answer to "how fine is a module", and the
    # unit a quiet zone should be counted in.
    pitch = width / (n * 2 * K)
    markup = (f'<g transform="translate({-x0 * s:.3f},{-y0 * s:.3f}) '
              f'scale({s:.6f})">{body}</g>')
    return markup, width, height, pitch
