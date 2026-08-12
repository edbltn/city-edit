"""
The colours the line is built from, and the one SVG wrapper everything returns.

Two garments, two inks, one accent. Keeping this in a single file is what stops
the tee modules from drifting apart — a design that mixed its own greys would
stop being part of the same line the moment either blank changed.
"""

# Blanks. Designs that pre-composite their fills resolve every tone against
# these, so a colourway is tied to the garment it was built for — printing the
# black files on navy or heather will read off.
GARMENT_BLACK = "#17171a"
GARMENT_WHITE = "#ffffff"

# Ink, per garment.
INK_ON_BLACK = "#ffffff"
INK_ON_WHITE = "#141414"

# --accent from client-react/src/styles/globals.css. Tuned for a #0d0d0d
# surface, so it is only ever used on the black colourway.
ACCENT = "#e0a23a"


def is_dark(hex_color: str) -> bool:
    """Whether ink should be light on this surface."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return (0.299 * r + 0.587 * g + 0.114 * b) < 128


def svg(width: int, height: int, body: str, background: str | None = None) -> str:
    bg = (
        f'<rect width="{width}" height="{height}" fill="{background}"/>'
        if background else ""
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">{bg}{body}</svg>'
    )
