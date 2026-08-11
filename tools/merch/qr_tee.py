"""
SINGLE ISSUE VOTER — the QR tee.

An advocacy shirt that works as a joke first. The headline is political
boilerplate; the code under it goes to one proposal on the map, and the line
under that admits how small the issue is. Three tiers, three reading distances:
the joke lands at ten feet, the self-deprecation at three, the actual cause
only if someone gets their phone out.

The subline stays deliberately unspecific — the QR can point at any proposal on
any map, so nothing above it may name a crosswalk, a corridor or a borough.
"""

import segno

from typo import face, size_to_fit, text

W, H = 3300, 3900
MEASURE = 2150              # every headline line is flush to this
HEAD_TRACKING = 0.08
QR_SIZE = 1080
QR_QUIET = 4                # modules of quiet zone, per the QR spec's minimum

HEADLINE = ["SINGLE", "ISSUE", "VOTER"]

# Sublines, all interchangeable and all generic on purpose.
SUBLINES = {
    "petty": "IT'S PETTIER THAN YOU THINK",
    "quiet": "AND I WON'T SHUT UP ABOUT IT",
    "scan": "SCAN TO SEE HOW PETTY",
}

DEFAULT_URL = "https://cityedit.org/m/nyc-proposals"


def is_dark(hex_color: str) -> bool:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return (0.299 * r + 0.587 * g + 0.114 * b) < 128


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
    code = segno.make(url, error="h")
    matrix = [list(row) for row in code.matrix]
    modules = len(matrix)
    span = modules + QR_QUIET * 2
    m = size / span

    def rect(cx, cy, w, h):
        return (f"M{x + cx * m:.2f},{y + cy * m:.2f}h{w * m:.2f}"
                f"v{h * m:.2f}h{-w * m:.2f}Z")

    # Merge each row's dark runs into one rect apiece — a version-5 code is
    # 1369 modules, and runs cut that to a couple of hundred subpaths.
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


def tee_single_issue(ink: str, accent: str, ground: str, *,
                     url: str | None = None, subline: str = "petty",
                     ) -> tuple[int, int, str]:
    # Resolved here, not defaulted in the signature, so --qr-url can rebind the
    # module global and have it take effect.
    url = url or DEFAULT_URL
    left = (W - MEASURE) / 2
    sizes = [size_to_fit(w, MEASURE, 600, HEAD_TRACKING) for w in HEADLINE]

    # Measure the whole composition, then centre it as one column. Laying it out
    # from a fixed top instead leaves the slack at the bottom, which reads as a
    # design that ran out rather than one that was placed.
    cap = face(600).cap_height / face(600).upem
    head_h = sizes[0] * cap + sum(s * 0.90 for s in sizes[1:])
    gap_qr, gap_sub, gap_url = 260, 190, 150
    total = head_h + gap_qr + QR_SIZE + gap_sub + 92 + gap_url
    top = (H - total) / 2 - 70          # bias up; a chest print reads better high

    body = []
    y = top + sizes[0] * cap
    for i, word in enumerate(HEADLINE):
        if i:
            y += sizes[i] * 0.90
        body.append(
            text(word, left, y, sizes[i], weight=600, fill=ink,
                 tracking=HEAD_TRACKING)
        )

    qr_y = y + gap_qr
    body.append(
        qr_block(url, (W - QR_SIZE) / 2, qr_y, QR_SIZE, ink, is_dark(ground))
    )

    sub_y = qr_y + QR_SIZE + gap_sub
    body.append(
        text(SUBLINES[subline], W / 2, sub_y, 92, fill=ink,
             tracking=0.22, anchor="middle")
    )
    body.append(
        text("CITYEDIT.ORG", W / 2, sub_y + gap_url, 68, fill=ink,
             tracking=0.42, anchor="middle", opacity=0.5)
    )
    return W, H, "".join(body)


def qr_version(url: str) -> int:
    """How dense the code is. Longer per-wearer URLs push this up fast."""
    return segno.make(url, error="h").version
