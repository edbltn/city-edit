"""
The City Edit mark as a city.

The logo's grid was never a crossword — it is a street plan, and its cells are
blocks. This builds it that way: the same six-by-four grid lifted into true
isometric, every cell a volume. Letter cells are plinths with the letter
standing on them; plain cells are low-rise; the logo's crossed cells become
vacant lots. Flatten it back and it is still the wordmark.

Projection is 30° isometric, chosen because it keeps the vertical axis dead
vertical on screen. That single property is what makes the rest cheap: a plane
of constant y maps through a plain affine transform with no vertical shear, and
extruding a letterform is a pure screen translation rather than a solved
silhouette.
"""

import math

from palette import is_dark
from typo import face, text

# Horizontal foreshortening, moved toward the QR city's (isoqr uses ~0.517) and
# away from the textbook 30° isometric this used to draw (0.866). With the two
# far apart the front and back read as two different worlds — same palette, same
# blocks, still obviously not the same city.
#
# It stops partway on purpose. Matching the code exactly was tried and made the
# wordmark illegible: the letters stand in the wall plane, where +x maps to
# (K, ½), so shrinking K steepens their baseline until CITY EDIT looks like it
# is falling over. 0.70 is as square as the grid gets while the letters still
# read, which is the whole reason they were stood up.
K = 0.70

# The logo, cell for cell: "X" crossed, "." empty, anything else a letter.
# Row 3's last cell is the logo's BETA box — merch shouldn't carry a version
# stamp, so it becomes another low block.
GRID = [
    "X....X",
    "CITY..",
    "..EDIT",
    ".X....",
]

# Everything below is in grid units (one cell pitch = 1) and scaled to the
# canvas at the end, so the geometry never has to know the print size.
STREET = 0.22           # gap between blocks, matching the logo's 25% gutter
SIDE = 1.0 - STREET
LOT_H = 0.05            # vacant lots are a kerb, not a hole
LOW_H = (0.22, 0.34, 0.46, 0.62)

# Letter cells share one plinth so the words sit level — and it has to clear the
# tallest low-rise, or the block in front crops the letters off at the knee.
# The condition is plinth > max(LOW_H) − STREET/2.
BASE_H = 0.74
LETTER_CAP = 0.60       # cap height of a standing letter, in grid units
LETTER_DEPTH = 0.36     # how far it extrudes back into its block
LETTER_SETBACK = 0.07   # off the roof's front lip, so it stands rather than teeters
EXTRUDE_STEP = 0.0025   # sweep step; ~0.8px at print size

# Face tones. The city is deliberately quiet and the wordmark is not: letters
# carry the full ink, blocks sit well below it. Physically the light would be
# more even than this — but the mark has to lead, and hierarchy beats accuracy
# on a garment seen from across a room.
LADDER_ON_DARK = (0.50, 0.30, 0.17)     # top, left, right
LETTER_FACE = 1.0
LETTER_SIDE = 0.72

# A light garment needs its own ladder, not the same numbers. The dark one
# bottoms out around 17% coverage, which is a readable grey over black and a
# near-invisible one over white — on cotton the right-hand faces would drop out
# of the print altogether and the blocks would lose their third side.
LADDER_ON_LIGHT = (0.66, 0.44, 0.28)


def project(x: float, y: float, z: float) -> tuple[float, float]:
    """Grid space → screen. +x runs down-right, +y down-left, +z straight up."""
    return ((x - y) * K, (x + y) * 0.5 - z)


def mix(ground: str, ink: str, amount: float) -> str:
    """
    Ink laid over the garment at `amount` coverage, resolved to a solid colour.

    Faces must be opaque. Drawn with `opacity`, every block in front lets the
    one behind ghost through — on screen it reads as glass, and on cloth it is
    worse: overlapping half-tones print as compounding ink densities rather than
    as the tone asked for. Pre-compositing against the garment gives flat,
    predictable coverage and lets the painter's ordering actually occlude.
    """
    g = [int(ground[i:i + 2], 16) for i in (1, 3, 5)]
    k = [int(ink[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(
        round(gc + (kc - gc) * amount) for gc, kc in zip(g, k)
    )


def _poly(points, fill):
    pts = " ".join(f"{sx:.4f},{sy:.4f}" for sx, sy in points)
    return f'<polygon points="{pts}" fill="{fill}"/>'


def _low_height(col: int, row: int) -> float:
    """
    A fixed skyline — the same cell is the same height on every build.

    Coprime strides rather than a hash: they guarantee no two neighbours land on
    the same step, which is what stops the low-rise reading as a chessboard.
    """
    return LOW_H[(col * 3 + row * 2) % len(LOW_H)]


def block(col: int, row: int, height: float, tones: dict) -> tuple[str, list]:
    """One volume, drawn back faces first. Returns its markup and its points."""
    x0, y0 = col + STREET / 2, row + STREET / 2
    x1, y1 = x0 + SIDE, y0 + SIDE

    top = [(x0, y0, height), (x1, y0, height), (x1, y1, height), (x0, y1, height)]
    left = [(x0, y1, height), (x1, y1, height), (x1, y1, 0), (x0, y1, 0)]
    right = [(x1, y0, height), (x1, y1, height), (x1, y1, 0), (x1, y0, 0)]

    out, points = [], []
    for corners, key in ((left, "left"), (right, "right"), (top, "top")):
        flat = [project(*c) for c in corners]
        points += flat
        out.append(_poly(flat, tones[key]))
    return "".join(out), points


def standing_letter(char: str, col: int, row: int, base: float,
                    tones: dict) -> tuple[str, str, list]:
    """
    A capital standing upright on its block, extruded back into it.

    Two earlier attempts lay the letter flat on the roof, and both failed the
    same way: on the ground plane a glyph is sheared in both axes at once, so it
    reads as a smudge at chest scale and the block in front of it crops what's
    left. Stood up in the wall plane, only the baseline slants — every vertical
    stem stays vertical — and the wordmark survives the trip across a room.

    The maths falls out of the projection. On a plane of constant y, the glyph's
    +x maps to (K, ½) and its +z to (0, −1), so the transform has a zero in the
    c slot: no vertical shear at all. Extruding backwards is a pure screen
    translation up-and-right, which is why the body can be the front face
    stamped repeatedly rather than a solved silhouette.
    """
    f = face(600)
    em = LETTER_CAP / (f.cap_height / f.upem)
    scale = em / f.upem
    glyph_w = f.advance * scale

    x0 = col + STREET / 2 + (SIDE - glyph_w) / 2
    y_face = row + 1 - STREET / 2 - LETTER_SETBACK
    origin = project(x0, y_face, base)

    a, b = K * scale, 0.5 * scale
    matrix = f"matrix({a:.8f},{b:.8f},0,{-scale:.8f},{origin[0]:.4f},{origin[1]:.4f})"
    gid = f"L{col}{row}"
    defs = f'<g id="{gid}" transform="{matrix}"><path d="{f.outline(char)}"/></g>'

    # Sub-pixel steps at print size, so the swept body shows no banding.
    steps = max(2, round(LETTER_DEPTH / EXTRUDE_STEP))
    stack = "".join(
        f'<use href="#{gid}" x="{K * LETTER_DEPTH * k / steps:.5f}" '
        f'y="{-0.5 * LETTER_DEPTH * k / steps:.5f}"/>'
        for k in range(steps, 0, -1)
    )
    markup = (
        f'<g fill="{tones["letter_side"]}">{stack}</g>'
        f'<use href="#{gid}" fill="{tones["letter_face"]}"/>'
    )

    top = base + LETTER_CAP
    corners = [
        project(x0, y_face, base),
        project(x0 + glyph_w, y_face, top),
        project(x0 + glyph_w, y_face - LETTER_DEPTH, top),
    ]
    return defs, markup, corners


def city(tones: dict, grid: list[str] | None = None,
         ) -> tuple[str, tuple[float, float, float, float]]:
    """
    The grid, painted back to front. Returns markup and its bounds.

    `grid` takes a fragment instead of the whole plan — the full city collapses
    into a grey blob below about 200px, so anywhere it has to work as a badge
    gets a corner of it rather than a shrunken copy.
    """
    grid = grid or GRID
    cells = [
        (col, row, kind)
        for row, line in enumerate(grid)
        for col, kind in enumerate(line)
    ]
    # Painter's order: in this projection depth increases with col + row, so
    # sorting by it means a nearer block is always drawn over a farther one.
    cells.sort(key=lambda c: c[0] + c[1])

    blocks, letters, defs, points = [], [], [], []
    for col, row, kind in cells:
        if kind == "X":
            height = LOT_H
        elif kind == ".":
            height = _low_height(col, row)
        else:
            height = BASE_H

        markup, pts = block(col, row, height, tones)
        blocks.append(markup)
        points += pts

        if kind not in "X.":
            tower_defs, tower, corners = standing_letter(kind, col, row, height, tones)
            defs.append(tower_defs)
            letters.append(tower)
            points += corners

    # Every block, then every letter. Per-cell ordering alone notches the
    # letterforms: a letter extrudes back into screen space its same-row
    # neighbour also occupies, and which of the two is nearer is genuinely
    # ambiguous — one has the greater x, the other the greater y. The plinths
    # are taller than every low-rise, so no block can legitimately cover a
    # letter, which makes "letters last" both correct and unambiguous.
    out = blocks + letters

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    body = f'<defs>{"".join(defs)}</defs>{"".join(out)}'
    return body, (min(xs), min(ys), max(xs), max(ys))


# --------------------------------------------------------------------------
# Tee — the mark as a city. Centre chest, one ink.
# --------------------------------------------------------------------------

def tones_for(ground: str, ink: str, letter_ink: str | None = None) -> dict:
    """
    Every surface in the drawing, resolved against one garment colour.

    `letter_ink` lets the wordmark take a different ink from the city — the
    accent, say — without disturbing the tonal ladder the blocks are built on.
    """
    letter_ink = letter_ink or ink
    top, left, right = LADDER_ON_DARK if is_dark(ground) else LADDER_ON_LIGHT
    return {
        "top": mix(ground, ink, top),
        "left": mix(ground, ink, left),
        "right": mix(ground, ink, right),
        "letter_face": mix(ground, letter_ink, LETTER_FACE),
        "letter_side": mix(ground, letter_ink, LETTER_SIDE),
    }


def tee_isogrid(ink: str, ground: str,
                letter_ink: str | None = None) -> tuple[int, int, str]:
    """The mark as a city. Centre chest."""
    W, H = 3300, 3900
    margin = 210
    body, (x0, y0, x1, y1) = city(tones_for(ground, ink, letter_ink))

    # Fit the projected grid to the print width, then set the whole composition
    # — city, tagline, URL — as one optically centred column.
    scale = (W - margin * 2) / (x1 - x0)
    art_h = (y1 - y0) * scale
    gap, lead = 400, 210
    total = art_h + gap + lead
    top = (H - total) / 2 - 90        # bias up; a chest print reads better high

    art = (
        f'<g transform="translate({margin - x0 * scale:.2f},{top - y0 * scale:.2f}) '
        f'scale({scale:.4f})">{body}</g>'
    )
    baseline = top + art_h + gap
    return W, H, "".join([
        art,
        text("REDRAW YOUR CITY.", W / 2, baseline, 196, weight=600, fill=ink,
             tracking=0.14, anchor="middle"),
        text("CITYEDIT.ORG", W / 2, baseline + lead, 88, fill=ink, tracking=0.42,
             anchor="middle", opacity=0.52),
    ])
