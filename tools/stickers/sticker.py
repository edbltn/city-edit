"""
One sticker: a circle, a QR code, one line of text.

## The idea

It is a roundel. Not a brand sticker — a warning roundel, the vocabulary of the
things it will be stuck next to: signal boxes, sign posts, work notices. An
amber band bleeds off the die, the field inside is the bare white label stock,
and everything printed on that field is black. Three reading distances, the
same structure as the tee:

    ten feet   the amber ring, which is the only thing on a grey pole
    three feet the line, set as large as a circle will physically allow
    six inches the code, which is the only thing here that does anything

Nothing else is on it. No wordmark, no URL, no icon. A sticker that has to
explain itself has already lost the person walking past it.

## Why the field is unprinted

The label stock is matte white, and so is the brand's paper. Printing a white
field onto white stock buys nothing and costs a full pass of ink, so the field
is simply left alone: the sticker is the paper. That has a second, larger
payoff. Sheet-fed label printing drifts — a sixteenth of an inch of registration
error is ordinary — and drift is only visible where printed art meets the die
cut. With no art at the edge except a band that deliberately overshoots it, a
drifted sheet reads as a band a hair thicker on one side, instead of a white
crescent down the edge of every sticker.

The band's overshoot is bounded by the sheet, not by taste: it may not reach the
neighbouring label, and on the 12-up sheet the labels are only 0.0625" apart
vertically. See sheet.py.

## Why the type is stacked

The measure available to a line inside a circle is a chord, and a chord gets
shorter the further it sits from the centre. Set "TIRED OF WAITING?" as one line
under the code and it fits at about ten point — legible in a proof, invisible on
a lamp post. Broken to "TIRED OF / WAITING?" the same words set at twenty.

So the type is not laid out, it is solved: try one, two and three lines, break
each into the most even stack the words allow, fit every stack to its own chords,
and print the one that comes out biggest. This is `size_to_fit` from the merch
typography — lines flush to a measure, sized by their own length — with the
measure varying per line because the paper is round.
"""

import base64
import math
import sys
from pathlib import Path

import segno

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "merch"))

from typo import face, text  # noqa: E402

DPI = 300

# --------------------------------------------------------------------------
# Palette. Mirrors tools/merch/designs.py, resolved for dark ink on pale stock.
# --------------------------------------------------------------------------

INK = "#141414"          # QR + type. Near-black, not pure black: pure black on
                         # matte stock reads as a hole rather than as ink.
ACCENT = "#9A6410"       # The band. --accent (#E0A23A) cut deeper, because the
                         # app's amber is tuned for a dark surface and washes
                         # out to nothing on white.

# --------------------------------------------------------------------------
# Geometry, all in inches and all as a fraction of the die, so the 2.5" and the
# 3" are the same drawing at two sizes rather than two drawings.
# --------------------------------------------------------------------------

BAND_WIDTH = 0.045       # visible band, × die
GUTTER = 0.032           # clear space between band and any content, × die
QR_SIZE = 0.42           # drawn module area, × die. Tuned against the whole
                         # line at 2.5": bigger starves the type (0.50 drops the
                         # longest line to 8 pt), smaller buys little back and
                         # makes the code timid on the thing whose entire job is
                         # to be scanned. At 0.42 a module is 0.92 mm — still
                         # more than twice the 0.4 mm a phone camera needs.
QR_GAP = 0.042           # code baseline to cap-top of the first line, × die
QR_QUIET = 4             # modules of quiet zone, the QR spec's minimum
TRACKING = 0.02          # em, on the line. Kept small: inside a circle the
                         # binding constraint is the CHORD, so every em of
                         # tracking is paid for directly in type size.
LEADING = 1.00           # × type size
WEIGHT = 700             # the variable font's maximum. Free, in the only sense
                         # that matters here: Red Hat Mono is monospaced, so its
                         # advance is identical at every weight — a bolder cut
                         # sets at exactly the same size and simply puts more
                         # ink on the paper.
MAX_LINES = 4
CAP_RATIO = 0.085        # cap height ceiling, × die — past this a two-word line
                         # out-shouts the code it exists to deliver
OPTICAL_RISE = 0.020     # × die. Type is visually lighter than a solid code, so
                         # a mathematically centred block sits low.


def _px(inches: float) -> float:
    return inches * DPI


def qr_paths(url: str, x: float, y: float, size: float, ink: str) -> str:
    """A scannable code as a single filled path, dark modules on bare stock.

    Error correction H: 30% of the code can be gone and it still decodes. A
    sticker lives outdoors on a pole and gets rained on, scraped, and stuck over
    at the corner — this is the one element that has to survive all of that. The
    payload is short enough (see codes.py) that H still fits a 29-module code.

    Runs of dark modules in a row merge into one rect apiece, which turns ~840
    module squares into a couple of hundred subpaths.
    """
    code = segno.make(url, error="h")
    matrix = [list(row) for row in code.matrix]
    modules = len(matrix)
    m = size / modules

    parts = []
    for r, row in enumerate(matrix):
        c = 0
        while c < modules:
            if row[c]:
                start = c
                while c < modules and row[c]:
                    c += 1
                parts.append(
                    f"M{x + start * m:.2f},{y + r * m:.2f}"
                    f"h{(c - start) * m:.2f}v{m:.2f}h{-(c - start) * m:.2f}Z"
                )
            else:
                c += 1
    return f'<path fill="{ink}" d="{"".join(parts)}"/>'


def qr_modules(url: str) -> int:
    """Module count of the code this URL produces — the density knob the sheet
    report prints, since it decides the physical size of one module."""
    return len(segno.make(url, error="h").matrix)


def art_image(path: Path, x: float, y: float, size: float) -> str:
    """A diffusion-painted code (qrart) in place of the drawn modules.

    Embedded as a base64 data URI rather than a file reference, so the SVG stays
    the same self-contained single file the vector version is — one that can be
    mailed to a print shop without an assets folder.

    The painted image includes its own quiet zone (qrart encodes with the QR
    spec's 4-module border), so `size` here is the FULL quiet square, not the
    module square. Sizing it that way keeps the module pitch identical to the
    vector version, which means the painted sticker occupies exactly the
    envelope the composition already reserved and nothing else has to move.
    """
    b64 = base64.b64encode(path.read_bytes()).decode()
    return (f'<image x="{x:.2f}" y="{y:.2f}" width="{size:.2f}" height="{size:.2f}" '
            f'preserveAspectRatio="none" '
            f'xlink:href="data:image/png;base64,{b64}" '
            f'href="data:image/png;base64,{b64}"/>')


# --------------------------------------------------------------------------
# Setting the line
# --------------------------------------------------------------------------

def _splits(words: list[str], n: int) -> list[list[str]] | None:
    """Break `words` into `n` contiguous lines, as evenly as the words allow.

    Brute force over every split point: a sticker line is at most five words, so
    there are a handful of arrangements and no reason to be clever. "Evenly" is
    measured by the longest resulting line, because monospaced type is sized by
    its longest line and nothing else.
    """
    if n > len(words):
        return None
    if n == 1:
        return [words]

    best, best_score = None, None
    # Choose n-1 cut positions among the len(words)-1 gaps.
    def walk(start: int, remaining: int, acc: list[list[str]]):
        nonlocal best, best_score
        if remaining == 1:
            lines = acc + [words[start:]]
            score = max(len(" ".join(ln)) for ln in lines)
            if best_score is None or score < best_score:
                best, best_score = lines, score
            return
        for cut in range(start + 1, len(words) - remaining + 2):
            walk(cut, remaining - 1, acc + [words[start:cut]])

    walk(0, n, [])
    return best


def _chord_measure(radius: float, y: float, inset: float) -> float:
    """Usable width of the disc at height `y` from centre (y down-positive).

    The half-chord of a circle at height y is sqrt(r² - y²); `inset` keeps the
    ends of a line from kissing the curve, which reads as a mistake even when
    it is geometrically exact.
    """
    if abs(y) >= radius:
        return 0.0
    return 2.0 * math.sqrt(radius * radius - y * y) - 2.0 * inset


def _fits(lines: list[str], size: float, die: float, qr_bottom: float,
          content_r: float) -> bool:
    """Does this stack, set at `size`, sit inside the disc under the code?

    Growing the type does two things at once — every line gets wider, and every
    line after the first gets pushed further down into a narrower chord — so
    feasibility is monotone in size. That is what makes the search below a
    binary search rather than a fixed point that has to be argued about.
    """
    f = face(WEIGHT)
    adv = f.advance / f.upem
    cap = f.cap_height / f.upem
    inset = GUTTER * die
    first_base = qr_bottom + QR_GAP * die + cap * size

    for i, ln in enumerate(lines):
        base = first_base + i * LEADING * size
        measure = _chord_measure(content_r, base, inset)
        if measure <= 0:
            return False
        # Descenders hang below the baseline; a comma or a Q on the last line
        # must still clear the curve.
        if base + 0.22 * size > content_r:
            return False
        width = (len(ln) * adv + (len(ln) - 1) * TRACKING) * size
        if width > measure:
            return False
    return True


def _fit_stack(lines: list[str], die: float, qr_bottom: float,
               content_r: float) -> tuple[float, float] | None:
    """Largest type size at which this stack fits, and the first baseline's y."""
    f = face(WEIGHT)
    cap = f.cap_height / f.upem
    if not lines or max(len(ln) for ln in lines) == 0:
        return None

    hi = CAP_RATIO * die / cap          # the ceiling, not the answer
    if _fits(lines, hi, die, qr_bottom, content_r):
        return hi, qr_bottom + QR_GAP * die + cap * hi

    lo = 0.0
    if not _fits(lines, hi / 64, die, qr_bottom, content_r):
        return None                      # no room at any usable size
    lo = hi / 64
    for _ in range(28):                  # ~1e-8 of the die: exact for print
        mid = (lo + hi) / 2
        if _fits(lines, mid, die, qr_bottom, content_r):
            lo = mid
        else:
            hi = mid
    return lo, qr_bottom + QR_GAP * die + cap * lo


def set_line(line: str, die: float, qr_bottom: float,
             content_r: float) -> tuple[list[str], float, float]:
    """Choose the stack that sets this line largest. Returns (lines, size,
    first baseline y)."""
    words = line.split()
    best = None
    for n in range(1, min(MAX_LINES, len(words)) + 1):
        stack = _splits(words, n)
        if stack is None:
            continue
        joined = [" ".join(ln) for ln in stack]
        fitted = _fit_stack(joined, die, qr_bottom, content_r)
        if fitted is None:
            continue
        size, first_base = fitted
        if best is None or size > best[1]:
            best = (joined, size, first_base)
    if best is None:
        raise ValueError(f"cannot set {line!r} inside a {die}\" disc")
    return best


# --------------------------------------------------------------------------
# The disc
# --------------------------------------------------------------------------

class Layout:
    """A solved sticker: where the code sits, how the line broke, how big it set."""

    def __init__(self, band_inner, content_r, qr_side, qr_top, modules, quiet,
                 lines, size, first_base):
        self.band_inner = band_inner
        self.content_r = content_r
        self.qr_side = qr_side
        self.qr_top = qr_top
        self.modules = modules
        #: One quiet-zone margin, in inches. Bare stock in the vector version;
        #: painted, and part of the image, when qrart art is supplied.
        self.quiet = quiet
        self.lines = lines
        self.size = size
        self.first_base = first_base


def compose(line: str, url: str, die: float) -> Layout:
    """Solve the whole composition: code, gap, type stack, optically centred.

    The stack's height depends on the type size, the size depends on how much
    room is left under the code, and how much room is left depends on where the
    code sits — which depends on the stack's height. Settled by climbing to the
    fixed point from below: start with a zero-height stack (the code sitting as
    low as it ever will), and every pass can only push the code up and the type
    larger, so the sequence is monotone and lands on the same layout regardless
    of where it started.
    """
    r = die / 2.0
    band_inner = r - BAND_WIDTH * die
    content_r = band_inner - GUTTER * die
    qr_side = QR_SIZE * die
    modules = qr_modules(url)
    # The quiet zone is bare stock, so it costs no ink — but it still has to
    # clear the band, or the amber eats the code's margin and the sticker stops
    # scanning. It is the quiet square's corners, not the modules', that must fit.
    quiet = qr_side * QR_QUIET / modules

    f = face(WEIGHT)
    cap = f.cap_height / f.upem

    # How high the code may ride before its own quiet zone runs into the band.
    # Without this ceiling the fixed point below has no upper bound and simply
    # runs away: every pass frees more room under the code, which sets the type
    # larger, which makes the block taller, which lifts the code further.
    half = qr_side / 2 + quiet
    if half >= band_inner:
        raise ValueError(
            f'{die}": a {qr_side:.3f}" code plus its {quiet:.3f}" quiet zone is '
            f'wider than the {band_inner * 2:.3f}" field — lower QR_SIZE'
        )
    qr_ceiling = math.sqrt(band_inner ** 2 - half ** 2) - quiet
    if qr_ceiling <= qr_side / 2:
        raise ValueError(
            f'{die}": no vertical room for a {qr_side:.3f}" code inside the '
            f'{band_inner * 2:.3f}" field — lower QR_SIZE'
        )

    qr_top = -(qr_side + QR_GAP * die) / 2.0 - OPTICAL_RISE * die
    lines, size, first_base = [], 0.0, 0.0
    for _ in range(12):
        qr_top = max(qr_top, -qr_ceiling)
        lines, size, first_base = set_line(line, die, qr_top + qr_side, content_r)
        stack_h = cap * size + (len(lines) - 1) * LEADING * size
        total = qr_side + QR_GAP * die + stack_h
        qr_top = -total / 2.0 - OPTICAL_RISE * die
    # The loop's last pass solved the type against the previous position, so
    # settle both against the final one.
    qr_top = max(qr_top, -qr_ceiling)
    lines, size, first_base = set_line(line, die, qr_top + qr_side, content_r)

    return Layout(band_inner, content_r, qr_side, qr_top, modules, quiet,
                  lines, size, first_base)


def disc(line: str, url: str, die: float, bleed: float,
         *, ink: str = INK, accent: str = ACCENT,
         art: Path | None = None) -> tuple[float, str]:
    """Draw one sticker.

    Returns (canvas size in px, SVG body). The canvas is the die plus bleed on
    every side, and the origin of the returned body is that canvas's top-left,
    so a sheet can place it with a single translate.

    `art` is an optional qrart-painted code (see qrart_bridge.py) that replaces
    the drawn modules. Nothing else about the sticker changes: same band, same
    line, same geometry — the painted image lands in exactly the envelope the
    module grid would have occupied.
    """
    r = die / 2.0
    canvas_in = die + 2 * bleed
    cx = cy = _px(canvas_in) / 2.0
    lay = compose(line, url, die)
    band_inner, qr_side, qr_top = lay.band_inner, lay.qr_side, lay.qr_top

    body = []

    # The band. One even-odd path (outer disc minus inner disc) so it is a true
    # annulus rather than a stroked circle whose width the renderer has to
    # resolve against the die.
    outer = r + bleed
    body.append(
        f'<path fill="{accent}" fill-rule="evenodd" d="'
        f'M{cx - _px(outer):.2f},{cy:.2f}'
        f'a{_px(outer):.2f},{_px(outer):.2f} 0 1,0 {_px(outer) * 2:.2f},0'
        f'a{_px(outer):.2f},{_px(outer):.2f} 0 1,0 {-_px(outer) * 2:.2f},0Z'
        f'M{cx - _px(band_inner):.2f},{cy:.2f}'
        f'a{_px(band_inner):.2f},{_px(band_inner):.2f} 0 1,0 {_px(band_inner) * 2:.2f},0'
        f'a{_px(band_inner):.2f},{_px(band_inner):.2f} 0 1,0 {-_px(band_inner) * 2:.2f},0Z'
        f'"/>'
    )

    if art is not None:
        # The painted image carries its own quiet zone, so it is drawn one
        # quiet margin larger on every side than the module square.
        full = qr_side + 2 * lay.quiet
        body.append(art_image(
            art, cx - _px(full) / 2, cy + _px(qr_top - lay.quiet), _px(full)
        ))
    else:
        body.append(
            qr_paths(url, cx - _px(qr_side) / 2, cy + _px(qr_top), _px(qr_side), ink)
        )

    for i, ln in enumerate(lay.lines):
        base = lay.first_base + i * LEADING * lay.size
        body.append(
            text(ln, cx, cy + _px(base), _px(lay.size), weight=WEIGHT, fill=ink,
                 tracking=TRACKING, anchor="middle")
        )

    return _px(canvas_in), "".join(body)


def metrics(line: str, url: str, die: float, bleed: float) -> dict:
    """What the proof sheet reports: the numbers that decide whether this
    sticker works on a pole rather than on a screen."""
    lay = compose(line, url, die)
    f = face(WEIGHT)
    cap = f.cap_height / f.upem
    size, qr_side, modules = lay.size, lay.qr_side, lay.modules

    return {
        "lines": lay.lines,
        "qr_modules": modules,
        "qr_in": qr_side,
        "module_mm": qr_side * 25.4 / modules,
        "cap_mm": cap * size * 25.4,
        "type_pt": size * 72.0,
    }
