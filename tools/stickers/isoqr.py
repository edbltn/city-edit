"""
The code as a city.

Every module of the QR becomes a city block in the same 30° isometric the
`tee-isogrid` merch is drawn in (`tools/merch/iso.py`): +x runs down-right, +y
down-left, +z straight up. Dark modules are buildings, light modules are the
low kerb between them, and the whole code comes out as a diamond because that is
what a square grid does under this projection.

## What a decoder actually needs, and what this costs

A decoder does not read pixels, it reads a *plane*. It finds the three finder
patterns, solves the transform from the module grid to the image, and samples
each module's centre. An isometric projection of a flat grid is an affine map —
a special case of that transform — so in principle a projected code is no harder
to read than a QR photographed at an angle, which phones do all day.

Three things were measured on the way to this file, and all three are load-
bearing:

  * **Streets break it outright.** A gutter between blocks was the first thing
    tried. A finder pattern is a solid 7x7 field whose 1:1:3:1:1 run signature is
    exactly what a decoder scans for, and cutting a gutter through it turns that
    one run into a picket fence. A 0.16-cell street failed at every size.
  * **Raised roofs move the signal off the plane.** A block drawn h units tall
    puts its roof h up-screen from the cell the decoder samples. With pale walls
    the module's own cell then reads light, and anything past about a tenth of a
    cell started failing. Dark walls fix it — the tower's own cell stays dark all
    the way down — and heights up to 0.4 of a cell then pass.
  * **A kerb on light modules costs reads.** A faint grey roof on every light
    module drags it toward the binariser's threshold. Bare paper is better.

## The honest bottom line

Even with all three fixed, this does not match the flat code. Measured through
the FINISHED sticker across a range of capture widths, the flat version decodes
100% and this one about 80%, failing completely at some widths rather than
degrading. Making the diamond bigger does not fix it and neither does the 3"
stock, so it is not a resolution problem: the rhombus edges run at 30° and alias
against the decoder's binariser at particular scales, where the flat code's
axis-aligned edges never do.

So this ships as an opt-in style, not the default, and anything printed with it
wants a phone test before a run — see the README.
"""

import math
import sys
from pathlib import Path

import segno

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "merch"))

from iso import mix, project  # noqa: E402

#: True 30° isometric, inherited from iso.py's `project`. One consequence worth
#: knowing: the diamond is 2·cos30 ≈ 1.73 times as wide as it is tall, so inside
#: a circle it is width that binds and the modules come out about 18% smaller
#: than the same code drawn square.
K = math.cos(math.radians(30))

#: NO gutter, and this one is not negotiable. Streets between blocks were the
#: first thing tried and they break the code outright: a finder pattern is a
#: solid 7x7 field whose 1:1:3:1:1 run signature is exactly what a decoder scans
#: for, and cutting a gutter through it turns that one run into a picket fence.
#: Measured — a 0.16 street fails at every size, 0.08 fails at most, 0 passes
#: everywhere. Blocks touching is also the better city: runs of dark modules
#: merge into single large buildings instead of a uniform grid of huts.
STREET = 0.0
#: Light modules are bare paper, not a low kerb. A kerb was the first idea and
#: it measurably costs reads: a faint grey roof on every light module drags it
#: toward the binariser's threshold, and a light module that reads dark is a bit
#: error. Measured through the finished sticker, dropping it took the pass rate
#: from 70% to 73%, and dropping it AND darkening the walls took it to 91%.
KERB_H = 0.0
TOWER_H = (0.20, 0.30, 0.40, 0.26)   # the skyline dark modules choose from

# Ink coverage per face. Roofs carry the signal and take the full ink; walls
# stay near the paper because their overlap into the cell behind is unavoidable.
TONE_ROOF = 1.00
#: Walls are nearly as dark as roofs. The 3D read would be stronger with paler
#: walls, but a tower's own cell has to stay unambiguously dark all the way down
#: — that is what lets the roofs sit raised at all — and pale walls measurably
#: cost reads.
TONE_LEFT = 0.86
TONE_RIGHT = 0.74
TONE_KERB_ROOF = 0.0                  # the ground is the paper
TONE_KERB_LEFT = 0.0
TONE_KERB_RIGHT = 0.0


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
        "kerb_roof": mix(paper, ink, TONE_KERB_ROOF),
        "kerb_left": mix(paper, ink, TONE_KERB_LEFT),
        "kerb_right": mix(paper, ink, TONE_KERB_RIGHT),
    }


def _poly(points, fill):
    pts = " ".join(f"{sx:.3f},{sy:.3f}" for sx, sy in points)
    return f'<polygon points="{pts}" fill="{fill}"/>'


def _tower_height(col: int, row: int) -> float:
    """A fixed skyline — the same module is the same height on every build.

    Coprime strides rather than a hash, borrowed from iso.py: they guarantee no
    two neighbours land on the same step, which is what stops a field of dark
    modules reading as one flat slab.
    """
    return TOWER_H[(col * 3 + row * 2) % len(TOWER_H)]


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

    keys = (("left", "right", "roof") if dark
            else ("kerb_left", "kerb_right", "kerb_roof"))
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
        height = (_tower_height(col, row) if dark else KERB_H) * scale_h
        markup, pts = _block(col, row, height, tones, bool(dark))
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
    cell = width / (n * 2 * K)      # inches per cell unit
    pitch = cell                     # the rhombus's short diagonal
    markup = (f'<g transform="translate({-x0 * s:.3f},{-y0 * s:.3f}) '
              f'scale({s:.6f})">{body}</g>')
    return markup, width, height, pitch
