"""City Edit presentation panels — drawing kit.

Everything here is a direct restatement of something the app actually draws:

  * kite waypoint markers          → utils/kiteIcon.ts + .ascii-marker CSS
  * the desire-path selection line → RouteLayer's getDesirePathStyles
  * block selection wash           → MapLibreBackground "block-select" layers
  * block heat fill/outline        → MapLibreBackground blockFillPaint/blockLinePaint
  * top-proposal pin + diamond     → GraphLayer/voteTypeIcon.ts geometry
  * the basemap                    → CARTO dark tiles (fetch_basemap.py)
"""

import base64
import heapq
import json
import math
import re
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent.parent
W, H = 1600.0, 900.0

# ---------------------------------------------------------------------------
# Palette — lifted from the client (globals.css / colors.ts / mapStyles.ts)
# ---------------------------------------------------------------------------
PAPER = "#0d0d0d"
INK = "#d4d4d4"
HAIRLINE = "rgba(212,212,212,0.2)"
START = "#00C4D4"
END = "#DC343B"
SEL = "#FFFFFF"
ACCENT = "#E0A23A"
HEAT = {
    "warm": (196, 96, 56),
    "hot": (232, 154, 54),
    "peak": (250, 214, 120),
    "cold": (74, 84, 190),
    "coldDeep": (92, 118, 250),
}
FONT = "RedHatMono, ui-monospace, Menlo, monospace"


def rgb(c):
    return f"rgb({c[0]},{c[1]},{c[2]})"


def mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _lerp_stops(stops, v):
    """MapLibre ["interpolate", ["linear"], h, ...] in Python."""
    if v <= stops[0][0]:
        return stops[0][1]
    for (x0, y0), (x1, y1) in zip(stops, stops[1:]):
        if v <= x1:
            t = 0 if x1 == x0 else (v - x0) / (x1 - x0)
            if isinstance(y0, tuple):
                return mix(y0, y1, t)
            return y0 + (y1 - y0) * t
    return stops[-1][1]


# heatTip(): peak mixed 68% toward the dark basemap's near-white tip.
HEAT_TIP = mix(HEAT["peak"], (255, 252, 242), 0.68)

HEAT_COLOR_STOPS = [
    (-1.0, HEAT["coldDeep"]), (-0.001, HEAT["cold"]), (0.0, HEAT["warm"]),
    (0.35, HEAT["hot"]), (0.7, HEAT["peak"]), (1.0, HEAT_TIP),
]
FILL_OPACITY_STOPS = [(-1.0, 0.72), (-0.001, 0.38), (0.0, 0.0), (0.001, 0.38), (1.0, 0.66)]
LINE_WIDTH_STOPS = [(-1.0, 2.0), (-0.7, 1.4), (0.0, 1.1), (0.7, 1.4), (1.0, 2.0)]
LINE_OPACITY_STOPS = [(-1.0, 1.0), (-0.001, 0.72), (0.0, 0.0), (0.001, 0.72), (1.0, 1.0)]


def heat_color(h):
    return rgb(_lerp_stops(HEAT_COLOR_STOPS, h))


# ---------------------------------------------------------------------------
# Scene: a real graph slice, already projected to panel pixels
# ---------------------------------------------------------------------------
class Scene:
    def __init__(self, filename: str):
        raw = json.loads((HERE / filename).read_text())
        self.streets = raw["streets"]
        self.nodes = {int(k): tuple(v) for k, v in raw["nodes"].items()}
        self.blocks = {b["id"]: b for b in raw["blocks"]}
        self.roads = [e for e in self.streets if e["c"] == "road"]
        self.adj: dict[int, list] = {}
        for e in self.roads:
            d = math.dist(e["a"], e["b"])
            self.adj.setdefault(e["u"], []).append((e["v"], d, e))
            self.adj.setdefault(e["v"], []).append((e["u"], d, e))

    def node_at(self, pt, named=None):
        best, bestd = None, 1e18
        for e in self.roads:
            if named and named.lower() not in e["n"].lower():
                continue
            for nid in (e["u"], e["v"]):
                d = math.dist(self.nodes[nid], pt)
                if d < bestd:
                    best, bestd = nid, d
        return best

    def street_edges(self, name, bbox=None):
        x0, y0, x1, y1 = bbox or (0, 0, W, H)
        return [e for e in self.roads if name.lower() in e["n"].lower()
                and all(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in (e["a"], e["b"]))]

    def blocks_of(self, edges):
        seen, out = set(), []
        for e in edges:
            if e["bid"] > 0 and e["bid"] not in seen and e["bid"] in self.blocks:
                seen.add(e["bid"])
                out.append(self.blocks[e["bid"]])
        return out

    def route(self, a: int, b: int):
        dist, prev = {a: 0.0}, {}
        pq, seen = [(0.0, a)], set()
        while pq:
            d, n = heapq.heappop(pq)
            if n in seen:
                continue
            seen.add(n)
            if n == b:
                break
            for m, w, e in self.adj.get(n, ()):
                nd = d + w
                if nd < dist.get(m, 1e18):
                    dist[m], prev[m] = nd, (n, e)
                    heapq.heappush(pq, (nd, m))
        if b not in prev:
            return [], []
        pts, edges, cur = [self.nodes[b]], [], b
        while cur != a:
            cur, e = prev[cur]
            edges.append(e)
            pts.append(self.nodes[cur])
        return pts[::-1], edges[::-1]

    def multi_route(self, ids):
        pts, edges = [], []
        for i in range(len(ids) - 1):
            p, e = self.route(ids[i], ids[i + 1])
            pts += p if not pts else p[1:]
            edges += e
        return pts, edges


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def path_d(pts, close=False):
    return "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in pts) + (" Z" if close else "")


def poly_d(block):
    return " ".join(path_d(ring, close=True) for ring in block["rings"])


def fit_into(groups, box):
    """One shared transform mapping several point groups into a pixel box."""
    x0b, y0b, x1b, y1b = box
    pts = [p for g in groups for p in g]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    k = min((x1b - x0b) / max(1.0, x1 - x0), (y1b - y0b) / max(1.0, y1 - y0))
    ox = (x0b + x1b) / 2 - (x0 + x1) / 2 * k
    oy = (y0b + y1b) / 2 - (y0 + y1) / 2 * k
    return lambda pt: (pt[0] * k + ox, pt[1] * k + oy)


def at(cx, cy, body, scale=1.0):
    return f'<g transform="translate({cx:.1f},{cy:.1f}) scale({scale})">{body}</g>'


def line(a, b, color=INK, width=2.0, opacity=1.0, dash=None, cap="round"):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<path d="{path_d([a, b])}" fill="none" stroke="{color}" stroke-width="{width}" '
            f'stroke-opacity="{opacity}" stroke-linecap="{cap}"{d}/>')


def tick(pt, color=INK, r=4.0, opacity=0.95, ring=PAPER):
    x, y = pt
    return (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{color}" fill-opacity="{opacity}" '
            f'stroke="{ring}" stroke-width="1.4"/>')


# ---------------------------------------------------------------------------
# App assets, redrawn
# ---------------------------------------------------------------------------
def kite(pt, color, scale=1.0, opacity=1.0):
    """The waypoint marker: ◆ head over a short stem, anchored at pt
       (utils/kiteIcon.ts + .ascii-marker). Mids are the selection colour —
       solid white, same weight as start/end."""
    x, y = pt
    s = 12.0 * scale
    stem_h, stem_w = 14 * scale, 2.4 * scale
    hy = y - stem_h - s * 0.70
    ring = "rgba(0,0,0,0.72)"
    d = (f"M {x:.1f} {hy - s:.1f} L {x + s * 0.80:.1f} {hy:.1f} "
         f"L {x:.1f} {hy + s:.1f} L {x - s * 0.80:.1f} {hy:.1f} Z")
    return (f'<g opacity="{opacity}">'
            f'<path d="{d}" fill="{color}" stroke="{ring}" stroke-width="2.6"/>'
            f'<rect x="{x - stem_w / 2:.1f}" y="{y - stem_h:.1f}" width="{stem_w:.1f}" '
            f'height="{stem_h:.1f}" fill="{color}" opacity="0.72" stroke="{ring}" stroke-width="1"/>'
            f'</g>')


def selection_line(pts, color=SEL, opacity=1.0, width=7.0, dashed=False):
    """RouteLayer's desire-path stroke."""
    dash = f' stroke-dasharray="{width * 1.5:.1f} {width * 1.3:.1f}"' if dashed else ""
    return (f'<path d="{path_d(pts)}" fill="none" stroke="{color}" stroke-width="{width}" '
            f'stroke-opacity="{opacity}" stroke-linecap="round" stroke-linejoin="round"{dash}/>')


def block_selected(poly_path, color=SEL, w=1.5):
    """The block-select wash: fill 0.11, casing 1.5 px @ 0.6 (MapLibreBackground)."""
    return (f'<path d="{poly_path}" fill="{color}" fill-opacity="0.11" stroke="{color}" '
            f'stroke-opacity="0.6" stroke-width="{w}" stroke-linejoin="round"/>')


def block_heat(poly_path, h, width_scale=1.0):
    """Block heat exactly as MapLibre paints it: one ramp colour, opacity and
       outline width driven by |heat|. Zero paints nothing."""
    if h == 0:
        return ""
    col = heat_color(h)
    return (f'<path d="{poly_path}" fill="{col}" fill-opacity="{_lerp_stops(FILL_OPACITY_STOPS, h):.3f}" '
            f'stroke="{col}" stroke-opacity="{_lerp_stops(LINE_OPACITY_STOPS, h):.3f}" '
            f'stroke-width="{_lerp_stops(LINE_WIDTH_STOPS, h) * width_scale:.2f}" '
            f'stroke-linejoin="round"/>')


# -- top-proposal indicators (GraphLayer/voteTypeIcon.ts, 34x42 viewBox) -----
PIN_OUTER = "M3,3 H31 V31 H23 L17,36 L11,31 H3 Z"
PIN_INNER = "M5.25,5.25 H28.75 V28.75 H22.2 L17,33.1 L11.8,28.75 H5.25 Z"
DIAMOND_OUTER = "M17,3 L31,17 L17,36 L3,17 Z"
DIAMOND_INNER = "M17,6.2 L28,17.2 L17,32.2 L6,17.2 Z"


def _icon_data_uri(name):
    svg = (REPO / "client-react/public/icons" / f"{name}.svg").read_bytes()
    return "data:image/svg+xml;base64," + base64.b64encode(svg).decode()


def proposal_icon(pt, icon, diamond=False, heat=0.0, selected=False, scale=2.2):
    """The top-proposal indicator: paper-filled pin (point) or diamond (route),
       hairline outline, themed icon at 16 px, and the solid heat glow ring
       whose width grows with rank (1.75 + heat*2.25 px)."""
    outer = DIAMOND_OUTER if diamond else PIN_OUTER
    inner = DIAMOND_INNER if diamond else PIN_INNER
    hc = heat_color(max(heat, 0.001)) if heat > 0 else None
    fill = rgb(mix((13, 13, 13), _lerp_stops(HEAT_COLOR_STOPS, max(heat, 0.001)), heat * 0.26)) \
        if heat > 0 else PAPER
    g = [f'<g transform="translate({pt[0]:.1f},{pt[1]:.1f}) scale({scale}) translate(-17,-36)">']
    if hc:
        g.append(f'<path d="{outer}" fill="none" stroke="{hc}" '
                 f'stroke-width="{1.75 + heat * 2.25:.2f}" stroke-linejoin="round"/>')
    g.append(f'<path d="{outer}" fill="{fill}"/>')
    g.append(f'<path d="{outer}" fill="none" stroke="{SEL if selected else HAIRLINE}" '
             f'stroke-width="1.5" stroke-linejoin="round"/>')
    if selected:
        g.append(f'<path d="{inner}" fill="none" stroke="{SEL}" stroke-width="1.5" '
                 f'stroke-linejoin="round"/>')
    g.append(f'<image href="{_icon_data_uri(icon)}" x="9" y="9" width="16" height="16"/>')
    g.append("</g>")
    return "".join(g)


# ---------------------------------------------------------------------------
# Text — scannable, bold keywords. `**word**` renders bright + bold.
# ---------------------------------------------------------------------------
def _xml(t):
    """Escape for SVG text — a raw & or < in a URL example breaks the whole file."""
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Inline emphasis: **bold white**, @@start colour@@, ##end colour##.
_MARKS = {"**": (SEL, "700"), "@@": (START, "700"), "##": (END, "700")}
_TOKEN = re.compile(r"(\*\*.*?\*\*|@@.*?@@|##.*?##)")


def rich(x, y, text, size=21, dim=0.62, anchor="start"):
    """One line of copy — one face, one size; only weight and colour vary."""
    spans = []
    for part in _TOKEN.split(text):
        if not part:
            continue
        mark = part[:2]
        if mark in _MARKS and part.endswith(mark) and len(part) > 4:
            colour, weight = _MARKS[mark]
            spans.append(f'<tspan font-weight="{weight}" fill="{colour}">{_xml(part[2:-2])}</tspan>')
        else:
            spans.append(f'<tspan fill-opacity="{dim}">{_xml(part)}</tspan>')
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{INK}" text-anchor="{anchor}" '
            f'xml:space="preserve">{"".join(spans)}</text>')


def lines(x, lines_, size=25, leading=46, mid=450):
    """The slide's copy: plain mono at one size, keywords bold. No subtitles,
       no second face — the only variation is what the words say."""
    y0 = mid - (len(lines_) - 1) * leading / 2
    return "".join(rich(x, y0 + i * leading, t, size, dim=0.66) for i, t in enumerate(lines_))


# ---------------------------------------------------------------------------
# Panel assembly
# ---------------------------------------------------------------------------
FONT_B64 = base64.b64encode(
    (REPO / "tools/merch/fonts/RedHatMono[wght].woff2").read_bytes()
).decode()

DEFS = f"""<defs>
  <style>
    @font-face {{ font-family: "RedHatMono";
      src: url(data:font/woff2;base64,{FONT_B64}) format("woff2");
      font-weight: 300 700; }}
    text {{ font-family: {FONT}; }}
  </style>
  <linearGradient id="scrimRight" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="{PAPER}" stop-opacity="0.78"/>
    <stop offset="1" stop-color="{PAPER}" stop-opacity="0"/>
  </linearGradient>
  <linearGradient id="scrim" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="{PAPER}" stop-opacity="0"/>
    <stop offset="0.45" stop-color="{PAPER}" stop-opacity="0.72"/>
    <stop offset="1" stop-color="{PAPER}" stop-opacity="0.94"/>
  </linearGradient>
</defs>"""


def panel(body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{H:.0f}" '
            f'viewBox="0 0 {W:.0f} {H:.0f}">{DEFS}'
            f'<rect width="{W}" height="{H}" fill="{PAPER}"/>{body}</svg>')
