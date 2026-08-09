"""
Text-as-vector-paths for the merch artwork.

Print files must not depend on a font being installed on whatever machine (or
print-on-demand backend) opens them, so every glyph is converted to an SVG
<path> up front. Red Hat Mono ships as a single variable font; each weight we
use is a static instance cut from it at import time.

Everything here assumes a MONOSPACED face: one advance width serves all glyphs,
which is what makes exact tracking and centering cheap.
"""

from functools import lru_cache
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

FONT_PATH = Path(__file__).parent / "fonts" / "RedHatMono[wght].ttf"


class Face:
    """One static weight of Red Hat Mono, ready to emit glyph outlines."""

    def __init__(self, weight: int):
        font = TTFont(FONT_PATH)
        instancer.instantiateVariableFont(font, {"wght": weight}, inplace=True)
        self.font = font
        self.upem = font["head"].unitsPerEm
        self.cmap = font.getBestCmap()
        self.glyphs = font.getGlyphSet()
        # Monospace: every glyph advances the same, so measure one and reuse it.
        self.advance = font["hmtx"][self.cmap[ord("M")]][0]

    def outline(self, char: str) -> str:
        """SVG path data for one glyph, in font units with the y axis pointing up."""
        name = self.cmap.get(ord(char))
        if name is None:
            return ""
        pen = SVGPathPen(self.glyphs)
        self.glyphs[name].draw(pen)
        return pen.getCommands()


@lru_cache(maxsize=None)
def face(weight: int) -> Face:
    return Face(weight)


def text_width(s: str, size: float, weight: int = 400, tracking: float = 0.0) -> float:
    """Advance width of `s` at `size` px, with `tracking` in em units."""
    if not s:
        return 0.0
    f = face(weight)
    step = size * f.advance / f.upem + size * tracking
    # The trailing letter contributes its advance but not its trailing tracking.
    return len(s) * step - size * tracking


def text(
    s: str,
    x: float,
    y: float,
    size: float,
    *,
    weight: int = 400,
    fill: str = "#000",
    tracking: float = 0.0,
    anchor: str = "start",
    opacity: float | None = None,
) -> str:
    """
    Render `s` as a group of outlined glyphs. `y` is the BASELINE.

    `anchor` is one of start / middle / end, matching SVG's text-anchor.
    """
    if not s:
        return ""
    f = face(weight)
    scale = size / f.upem
    step = size * f.advance / f.upem + size * tracking

    width = text_width(s, size, weight, tracking)
    origin = x - width / 2 if anchor == "middle" else x - width if anchor == "end" else x

    parts = []
    for i, ch in enumerate(s):
        d = f.outline(ch)
        if not d:
            continue
        parts.append(f'<path transform="translate({i * step / scale:.2f},0)" d="{d}"/>')

    op = f' opacity="{opacity}"' if opacity is not None else ""
    inner = "".join(parts)
    # scale(k,-k) flips the font's y-up outlines into SVG's y-down space.
    return (
        f'<g transform="translate({origin:.2f},{y:.2f}) scale({scale:.6f},{-scale:.6f})" '
        f'fill="{fill}"{op}>{inner}</g>'
    )
