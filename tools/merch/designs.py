"""
City Edit merch artwork — five print-ready designs, drawn as SVG.

The line is deliberately narrow and deliberately monochrome-plus-accent: the
brand is a terminal and a crossword, not a sticker pack. Every design is built
from the same four moves — the mono grid, the crossword cell, the heat ramp,
and the start/end kites — so a tee and a mug read as one object from across a
room.

Canvases are sized in real print pixels (300 DPI), so a print file drops into a
print-on-demand uploader at 1:1 with "fit to print area".

Tee art is transparent-background on purpose: the "paper" is the garment, which
is both truer to the brand and materially less ink. Mug art is full-bleed,
because an 11oz ceramic mug is white until you cover it.
"""

from typo import text, text_width

# --------------------------------------------------------------------------
# Palette — mirrors client-react/src/styles/globals.css. Keep in sync.
# --------------------------------------------------------------------------

PAPER = "#0d0d0d"          # --paper (dark terminal surface)
INK = "#d4d4d4"            # --ink
ACCENT = "#E0A23A"         # --accent, as it reads on a dark garment
# The same amber on bone or natural cotton washes out to nothing, so pale
# colourways get a deeper cut of it rather than losing the accent entirely.
ACCENT_ON_LIGHT = "#9A6410"
COLOR_START = "#00C4D4"    # --color-start
COLOR_END = "#DC343B"      # --color-end

# --heat-gradient, as (position, r, g, b).
HEAT_STOPS = [
    (0.00, 96, 56, 120),
    (0.40, 196, 96, 56),
    (0.75, 232, 154, 54),
    (1.00, 250, 214, 120),
]

# Ink opacities. Floored well above the CSS values: 15% grey over a black
# garment is a muddy near-black once DTG lays it over the white underbase, so
# the faintest thing we print is ~30%.
DIM = 0.30
MID = 0.52


def heat_color(t: float) -> str:
    """Sample the heat ramp at t ∈ [0, 1]."""
    t = max(0.0, min(1.0, t))
    for (p0, r0, g0, b0), (p1, r1, g1, b1) in zip(HEAT_STOPS, HEAT_STOPS[1:]):
        if t <= p1:
            k = (t - p0) / (p1 - p0)
            return "#%02x%02x%02x" % (
                round(r0 + (r1 - r0) * k),
                round(g0 + (g1 - g0) * k),
                round(b0 + (b1 - b0) * k),
            )
    return "#%02x%02x%02x" % HEAT_STOPS[-1][1:]


def lcg(seed: int):
    """A tiny deterministic PRNG — the art must be byte-identical every build."""
    state = seed

    def rand() -> float:
        nonlocal state
        state = (1103515245 * state + 12345) % (1 << 31)
        return state / (1 << 31)

    return rand


# --------------------------------------------------------------------------
# The crossword — CITY and EDIT sit exactly as they do in the logo mark; CROSS,
# TREE and IDEA thread through them. Every contiguous run of two or more cells
# is a real word, so it reads as a solvable grid rather than letter soup.
# --------------------------------------------------------------------------

GRID = [
    "#....E#",
    "CITY.X.",
    "R.R..I.",
    "O.EDIT.",
    "S.E.D..",
    "S...E..",
    "#...A.#",
]

# (col, row) → clue number, for the cells that begin a word. Standard
# numbering: scan left-to-right, top-to-bottom, number every cell that starts
# an across or down run.
CLUE_NUMBERS = {(5, 0): 1, (0, 1): 2, (2, 1): 3, (2, 3): 4, (4, 3): 5}

CLUES = [
    ("ACROSS", None),
    ("2  CITY", "what we share"),
    ("4  EDIT", "what we owe it"),
    ("DOWN", None),
    ("1  EXIT", "how you leave"),
    ("2  CROSS", "where it fails"),
    ("3  TREE", "what it lacks"),
    ("5  IDEA", "where it starts"),
]


def crossword(x0: float, y0: float, cell: float, ink: str, accent: str, *,
              numbers: bool = True) -> str:
    """The 7×7 grid. `cell` is the box size; the gutter is 25% of it, as in the logo."""
    gap = cell * 0.25
    pitch = cell + gap
    stroke = cell * 0.0625      # the logo's 1.5-on-24 box weight
    cross = cell * 0.104        # and its 2.5-on-24 X weight
    inset = cell * 0.167
    out = []

    for row, line in enumerate(GRID):
        for col, kind in enumerate(line):
            x = x0 + col * pitch
            y = y0 + row * pitch
            is_letter = kind not in ".#"
            opacity = 1.0 if is_letter else (0.42 if kind == "#" else DIM)

            out.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell:.1f}" height="{cell:.1f}" '
                f'fill="none" stroke="{ink}" stroke-width="{stroke:.1f}" opacity="{opacity}"/>'
            )

            if kind == "#":
                a, b = x + inset, x + cell - inset
                c, d = y + inset, y + cell - inset
                out.append(
                    f'<g stroke="{ink}" stroke-width="{cross:.1f}" opacity="0.42" '
                    f'stroke-linecap="round">'
                    f'<line x1="{a:.1f}" y1="{c:.1f}" x2="{b:.1f}" y2="{d:.1f}"/>'
                    f'<line x1="{b:.1f}" y1="{c:.1f}" x2="{a:.1f}" y2="{d:.1f}"/></g>'
                )
            elif is_letter:
                # Optical centre: cap-height text hangs slightly low of the box
                # midpoint, so the baseline sits at 0.72 of the cell, not 0.5.
                out.append(
                    text(kind, x + cell / 2, y + cell * 0.72, cell * 0.70,
                         weight=600, fill=ink, anchor="middle")
                )
                if numbers and (col, row) in CLUE_NUMBERS:
                    out.append(
                        text(str(CLUE_NUMBERS[(col, row)]), x + cell * 0.13,
                             y + cell * 0.29, cell * 0.21, fill=accent)
                    )

    return "".join(out)


def kite(cx: float, cy: float, size: float, color: str, outline: str) -> str:
    """The app's start/end waypoint marker: a diamond drawn out to a point below."""
    w = size * 0.5
    d = (
        f"M {cx:.1f} {cy - size:.1f} "
        f"L {cx + w:.1f} {cy - size * 0.28:.1f} "
        f"L {cx:.1f} {cy + size * 0.62:.1f} "
        f"L {cx - w:.1f} {cy - size * 0.28:.1f} Z"
    )
    return (
        f'<path d="{d}" fill="{color}" stroke="{outline}" '
        f'stroke-width="{size * 0.10:.1f}" stroke-linejoin="round"/>'
    )


def svg(width: int, height: int, body: str, background: str | None = None) -> str:
    bg = (
        f'<rect width="{width}" height="{height}" fill="{background}"/>'
        if background else ""
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">{bg}{body}</svg>'
    )


# --------------------------------------------------------------------------
# Tee 1 — CROSSWORD. Centre-chest, one colour, the flagship.
# --------------------------------------------------------------------------

def tee_crossword(ink: str, accent: str) -> tuple[int, int, str]:
    W, H = 3300, 3900
    cell, gap = 330.0, 82.5
    grid_w = 7 * cell + 6 * gap
    body = [
        crossword((W - grid_w) / 2, 250, cell, ink, accent),
        f'<line x1="{(W - grid_w) / 2:.0f}" y1="3300" x2="{(W + grid_w) / 2:.0f}" y2="3300" '
        f'stroke="{ink}" stroke-width="8" opacity="{MID}"/>',
        text("REDRAW YOUR CITY.", W / 2, 3600, 196, weight=600, fill=ink,
             tracking=0.14, anchor="middle"),
        text("CITYEDIT.ORG", W / 2, 3830, 88, fill=ink, tracking=0.42,
             anchor="middle", opacity=MID),
    ]
    return W, H, "".join(body)


# --------------------------------------------------------------------------
# Tee 2 — DESIRE PATH. The whole thesis in one picture: the route the network
# offers (dashed, along the grid) against the route people actually take.
# --------------------------------------------------------------------------

def tee_desire_path(ink: str, accent: str) -> tuple[int, int, str]:
    W, H = 3300, 4200
    x0, y0, span = 200, 220, 2700
    pitch = span / 9
    body = []

    # Street grid — every third line reads as an avenue.
    for i in range(10):
        major = i % 3 == 0
        w, op = (16, 0.46) if major else (9, DIM)
        p = x0 + i * pitch
        q = y0 + i * pitch
        body.append(
            f'<g stroke="{ink}" stroke-width="{w}" opacity="{op}">'
            f'<line x1="{p:.1f}" y1="{y0}" x2="{p:.1f}" y2="{y0 + span:.1f}"/>'
            f'<line x1="{x0}" y1="{q:.1f}" x2="{x0 + span:.1f}" y2="{q:.1f}"/></g>'
        )

    def px(i: float) -> float:
        return x0 + i * pitch

    def py(i: float) -> float:
        return y0 + i * pitch

    start = (px(1), py(8))
    end = (px(8), py(1))

    # What the router returns: a staircase that only ever turns at corners. It
    # has to hold its own against the accent, or the contrast the whole design
    # rests on collapses into "line on a grid".
    stair = [(1, 8), (1, 6), (3, 6), (3, 4), (5, 4), (5, 2), (8, 2), (8, 1)]
    pts = " ".join(f"{px(i):.1f},{py(j):.1f}" for i, j in stair)
    body.append(
        f'<polyline points="{pts}" fill="none" stroke="{ink}" stroke-width="26" '
        f'opacity="0.62" stroke-dasharray="54 44" stroke-linecap="round" '
        f'stroke-linejoin="round"/>'
    )

    # What people walk. Two passes: a wide soft band for the trodden ground,
    # then the crisp line over it — which is what a desire path looks like
    # from above, and what a single flat stroke never manages to say.
    worn = [(1, 8), (2.6, 6.6), (4.1, 5.2), (5.4, 3.6), (6.9, 2.4), (8, 1)]
    wpts = " ".join(f"{px(i):.1f},{py(j):.1f}" for i, j in worn)
    for width, opacity in ((104, 0.16), (62, 0.28), (34, 1.0)):
        body.append(
            f'<polyline points="{wpts}" fill="none" stroke="{accent}" '
            f'stroke-width="{width}" opacity="{opacity}" stroke-linecap="round" '
            f'stroke-linejoin="round"/>'
        )

    body.append(kite(*start, 130, COLOR_START, PAPER))
    body.append(kite(*end, 130, COLOR_END, PAPER))

    for i, line in enumerate(["THE FASTEST ROUTE", "IS THE ONE PEOPLE", "ALREADY WALK."]):
        body.append(text(line, x0, 3260 + i * 260, 208, weight=600, fill=ink, tracking=0.03))
    body.append(
        text("CITY EDIT · CITYEDIT.ORG", x0, 4060, 84, fill=ink,
             tracking=0.36, opacity=MID)
    )
    return W, H, "".join(body)


# --------------------------------------------------------------------------
# Tee 3 — HEAT. A back print at poster scale: the product itself, abstracted to
# a grid where colour and weight both carry the vote count.
# --------------------------------------------------------------------------

def tee_heat(ink: str, accent: str) -> tuple[int, int, str]:
    W, H = 3900, 5100
    left, right = 300, 3600
    top, bottom = 760, 4340
    rand = lcg(20260809)
    body = [
        text("CITY EDIT", W / 2, 430, 260, weight=600, fill=ink,
             tracking=0.26, anchor="middle"),
        f'<line x1="{left}" y1="560" x2="{right}" y2="560" stroke="{ink}" '
        f'stroke-width="8" opacity="{MID}"/>',
    ]

    AVENUES, STREETS = 9, 17

    def ax(i: float) -> float:
        return left + i * (right - left) / (AVENUES - 1)

    def sy(j: float) -> float:
        return top + j * (bottom - top) / (STREETS - 1)

    # A demand field, not a stripe pattern. Heat falls off from two hubs and
    # rises along the diagonal, and every block between two crossings is drawn
    # separately — which is what makes this look like the actual heatmap
    # instead of plaid. One uniform stroke per street cannot say "corridor".
    hubs = [(3.1, 6.4, 1.00), (6.4, 3.2, 0.70)]

    # The diagonal runs (i=8, j=0.6) → (i=0.6, j=15.6), i.e. 2.03·u + v ≈ 16.8.
    # Blocks near that line inherit its traffic.
    def demand(u: float, v: float) -> float:
        heat = 0.0
        for hu, hv, weight in hubs:
            dist = ((u - hu) ** 2 + ((v - hv) * 0.50) ** 2) ** 0.5
            heat = max(heat, weight * max(0.0, 1.0 - dist / 4.4))
        heat = max(heat, 0.60 * max(0.0, 1.0 - abs(2.03 * u + v - 16.8) / 4.2))
        return min(1.0, max(0.02, heat + (rand() - 0.5) * 0.16))

    def segment(x1, y1, x2, y2, heat, boost=0.0):
        h = min(1.0, heat + boost)
        # A real network has gaps. Thinning the coldest blocks keeps the frame
        # from squaring off into graph paper.
        if h < 0.14 and rand() < 0.30:
            return
        body.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{heat_color(h)}" stroke-width="{8 + h * 46:.1f}" '
            f'stroke-linecap="round" opacity="{0.62 + h * 0.38:.2f}"/>'
        )

    # Crosstown blocks.
    for j in range(STREETS):
        for i in range(AVENUES - 1):
            segment(ax(i), sy(j), ax(i + 1), sy(j), demand(i + 0.5, j))

    # Avenue blocks — north-south carries more, so nudge them up.
    for i in range(AVENUES):
        for j in range(STREETS - 1):
            segment(ax(i), sy(j), ax(i), sy(j + 1), demand(i, j + 0.5), boost=0.10)

    # And the diagonal that breaks the grid — the hottest line on the map.
    for k in range(12):
        t0, t1 = k / 12, (k + 1) / 12
        segment(
            ax(8 - 7.4 * t0), sy(0.6 + 15 * t0),
            ax(8 - 7.4 * t1), sy(0.6 + 15 * t1),
            0.86 + rand() * 0.14,
        )

    body.append(
        text("OBSERVED DESIRE — NOT PLANNED INTENT", W / 2, 4640, 104, fill=ink,
             tracking=0.24, anchor="middle")
    )
    body.append(
        text("CITYEDIT.ORG", W / 2, 4850, 84, fill=ink, tracking=0.42,
             anchor="middle", opacity=MID)
    )
    return W, H, "".join(body)


# --------------------------------------------------------------------------
# Mugs — 2475 × 1155, the 11oz full-wrap template. Content stays 150px clear of
# the left and right edges, which is where the handle lands.
# --------------------------------------------------------------------------

MUG_W, MUG_H = 2475, 1155


def mug_heat_ramp(ink: str, paper: str, accent: str) -> tuple[int, int, str]:
    """The heatmap legend, blown up to mug scale and wrapped the whole way round."""
    x0, x1 = 150, 2325
    bar_y, bar_h = 396, 190
    stops = "".join(
        f'<stop offset="{p * 100:.0f}%" stop-color="rgb({r},{g},{b})"/>'
        for p, r, g, b in HEAT_STOPS
    )
    body = [
        f'<defs><linearGradient id="heat" x1="0" y1="0" x2="1" y2="0">{stops}'
        f'</linearGradient></defs>',
        text("CITY EDIT", x0, 186, 116, weight=600, fill=ink, tracking=0.24),
        text("NET VOTES PER BLOCK", x1, 186, 64, fill=ink, tracking=0.38,
             anchor="end", opacity=0.62),
        f'<line x1="{x0}" y1="272" x2="{x1}" y2="272" stroke="{ink}" '
        f'stroke-width="6" opacity="{DIM}"/>',
        f'<rect x="{x0}" y="{bar_y}" width="{x1 - x0}" height="{bar_h}" fill="url(#heat)"/>',
    ]

    # Ticks + the scale the ramp actually encodes: a signed differential, cold
    # arm and hot arm, zero in the middle where a block goes invisible.
    labels = ["−10", "", "−5", "", "0", "", "+5", "", "+10"]
    for i, label in enumerate(labels):
        x = x0 + i * (x1 - x0) / (len(labels) - 1)
        long = bool(label)
        body.append(
            f'<line x1="{x:.1f}" y1="{bar_y + bar_h + 16}" x2="{x:.1f}" '
            f'y2="{bar_y + bar_h + (58 if long else 34):.0f}" stroke="{ink}" '
            f'stroke-width="7" opacity="{0.62 if long else DIM}"/>'
        )
        if label:
            body.append(
                text(label, x, bar_y + bar_h + 136, 60, fill=ink,
                     anchor="middle", opacity=0.72)
            )

    body += [
        text("COLD", x0, 340, 60, fill=ink, tracking=0.34, opacity=0.62),
        text("HOT", x1, 340, 60, fill=ink, tracking=0.34, anchor="end", opacity=0.62),
        f'<line x1="{x0}" y1="880" x2="{x1}" y2="880" stroke="{ink}" '
        f'stroke-width="6" opacity="{DIM}"/>',
        text("REDRAW YOUR CITY.", MUG_W / 2, 1030, 128, weight=600, fill=ink,
             tracking=0.20, anchor="middle"),
    ]
    return MUG_W, MUG_H, "".join(body)


def mug_clues(ink: str, paper: str, accent: str) -> tuple[int, int, str]:
    """
    The tee's puzzle with the clues it has no room for — so the pair is one
    object: you wear the grid, you drink the answers.
    """
    cell = 118.0
    grid = 7 * cell + 6 * (cell * 0.25)
    body = [crossword(140, (MUG_H - grid) / 2, cell, ink, accent)]

    x = 1310
    gutter = text_width("2  CROSS   ", 64, 600)
    y = 236
    for label, clue in CLUES:
        if clue is None:
            body.append(text(label, x, y, 58, fill=accent, tracking=0.36))
            y += 104
            continue
        body.append(text(label, x, y, 64, weight=600, fill=ink))
        body.append(text(f"— {clue}", x + gutter, y, 58, fill=ink, opacity=0.66))
        y += 92

    body.append(
        text("CITYEDIT.ORG", x, 1074, 54, fill=ink, tracking=0.4, opacity=MID)
    )
    return MUG_W, MUG_H, "".join(body)
