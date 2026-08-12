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

import segno

from typo import face, size_to_fit, text

# 11 × 11 in. The composition is near-square, and a taller canvas would just be
# empty print area that the uploader scales the artwork down to fill.
W, H = 3300, 3300
QR_SIZE = 1080
QR_QUIET = 4                # modules of quiet zone, per the QR spec's minimum

HEART = "@"                 # stands in for the mark inside a monospaced line
BLOCK = ["I @", "THIS CITY"]
ONE_LINE = "I @ THIS CITY"
TURN = "BUT I HAVE ONE NOTE"

BLOCK_MEASURE = 2350
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


def is_dark(hex_color: str) -> bool:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return (0.299 * r + 0.587 * g + 0.114 * b) < 128


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


def qr_block(url: str, x: float, y: float, size: float, ink: str,
             panel: bool) -> str:
    """
    A scannable code, built for cloth.

    Error correction H (30%): a shirt creases, stretches and takes ink spread,
    and the code has one job. On a dark garment the modules are knocked out of a
    light panel rather than printed light-on-dark — decoders have handled
    inverted codes for years, but "mostly" is not good enough for the one
    element on the shirt that has to work.
    """
    matrix = [list(row) for row in segno.make(url, error="h").matrix]
    modules = len(matrix)
    span = modules + QR_QUIET * 2
    m = size / span

    def rect(cx, cy, w, h):
        return (f"M{x + cx * m:.2f},{y + cy * m:.2f}h{w * m:.2f}"
                f"v{h * m:.2f}h{-w * m:.2f}Z")

    # Merge each row's dark runs into one rect apiece — a code is a thousand-odd
    # modules, and runs cut that to a couple of hundred subpaths.
    runs = []
    for r, row in enumerate(matrix):
        c = 0
        while c < modules:
            if row[c]:
                start = c
                while c < modules and row[c]:
                    c += 1
                runs.append(rect(QR_QUIET + start, QR_QUIET + r, c - start, 1))
            else:
                c += 1

    if panel:
        # One even-odd path: light panel, modules punched clean through it, so
        # the garment itself is the dark half of the code.
        return (f'<path fill-rule="evenodd" fill="{ink}" '
                f'd="{rect(0, 0, span, span)}{"".join(runs)}"/>')
    return f'<path fill="{ink}" d="{"".join(runs)}"/>'


def tee_one_note(ink: str, accent: str, ground: str, *,
                 url: str | None = None, layout: str = "stack",
                 ) -> tuple[int, int, str]:
    # Resolved here, not defaulted in the signature, so --qr-url can rebind the
    # module global and have it take effect.
    url = url or DEFAULT_URL

    cap = face(600).cap_height / face(600).upem
    if layout == "line":
        lines = [ONE_LINE]
        # One size, one measure — nothing to balance.
        block_sizes = [size_to_fit(ONE_LINE, LINE_MEASURE, 600, 0.06)]
    else:
        lines = BLOCK
        # Both lines at ONE size, set by the longer. Justifying each to the same
        # measure instead makes "I @" three times the size of "THIS CITY",
        # which stops reading as a souvenir block and starts reading as a
        # mistake.
        size = size_to_fit(BLOCK[1], BLOCK_MEASURE, 600, 0.06)
        block_sizes = [size, size]

    turn_size = size_to_fit(TURN, TURN_MEASURE, 400, 0.10)

    # Measure the whole column, then centre it. Laying out from a fixed top
    # leaves the slack at the bottom, which reads as a design that ran out.
    block_h = block_sizes[0] * cap + sum(s * 1.02 for s in block_sizes[1:])
    gap_turn, gap_qr, gap_url = 250, 230, 130
    total = (block_h + gap_turn + turn_size * cap + gap_qr
             + QR_SIZE + gap_url + 64)
    top = (H - total) / 2 - 40          # bias up; a chest print reads better high

    body = []
    y = top + block_sizes[0] * cap
    for i, line in enumerate(lines):
        if i:
            y += block_sizes[i] * 1.02
        body.append(mono_line(line, W / 2, y, block_sizes[i], ink,
                              tracking=0.06, anchor="middle"))

    y += gap_turn + turn_size * cap
    body.append(text(TURN, W / 2, y, turn_size, weight=400, fill=ink,
                     tracking=0.10, anchor="middle"))

    qr_y = y + gap_qr
    body.append(
        qr_block(url, (W - QR_SIZE) / 2, qr_y, QR_SIZE, ink, is_dark(ground))
    )
    body.append(
        text("CITYEDIT.ORG", W / 2, qr_y + QR_SIZE + gap_url, 64, fill=ink,
             tracking=0.42, anchor="middle", opacity=0.5)
    )
    return W, H, "".join(body)


def qr_version(url: str) -> int:
    """How dense the code is. Longer, lower-case URLs push this up fast."""
    return segno.make(url, error="h").version
