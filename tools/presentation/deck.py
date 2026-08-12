"""City Edit presentation panels — drawing kit.

Draws the app's own front-end vocabulary — kite waypoints, the desire-path
selection line, block polygons, the signed heat ramp — over a real slice of the
NYC walk graph (Flatiron / Madison Square). One SVG per frame; frames in a set
are pixel-aligned so they can be flipped through in a talk.
"""

import base64
import heapq
import json
import math
from pathlib import Path

HERE = Path(__file__).parent
W, H = 1600.0, 900.0

# ---------------------------------------------------------------------------
# Palette — lifted from the running client (globals.css / colors.ts / mapStyles.ts)
# ---------------------------------------------------------------------------
PAPER = "#0d0d0d"
INK = "#d4d4d4"
START = "#00C4D4"
END = "#DC343B"
SEL = "#FFFFFF"
HEAT = {
    "halo": "rgb(96,56,120)",
    "warm": "rgb(196,96,56)",
    "hot": "rgb(232,154,54)",
    "peak": "rgb(250,214,120)",
    "cold": "rgb(74,84,190)",
    "coldDeep": "rgb(92,118,250)",
}
FONT = "RedHatMono, ui-monospace, Menlo, monospace"


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

    # -- lookup ------------------------------------------------------------
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

    def block_at(self, pt):
        best, bestd = None, 1e18
        for b in self.blocks.values():
            d = math.dist(b["c"], pt)
            if d < bestd:
                best, bestd = b, d
        return best

    def edges_in_block(self, bid):
        return [e for e in self.streets if e["bid"] == bid]

    def street_edges(self, name, bbox=None):
        """Every edge of one named street inside a pixel bbox — a clean corridor,
        the way a proposal reads, rather than a routed zigzag."""
        x0, y0, x1, y1 = bbox or (0, 0, W, H)
        out = []
        for e in self.roads:
            if name.lower() not in e["n"].lower():
                continue
            if all(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in (e["a"], e["b"])):
                out.append(e)
        return out

    def bbox_of(self, edges):
        xs = [p[0] for e in edges for p in (e["a"], e["b"])]
        ys = [p[1] for e in edges for p in (e["a"], e["b"])]
        return min(xs), min(ys), max(xs), max(ys)

    def fit(self, edges, pad=1.55):
        """Zoom factor + centre that frames a group of edges."""
        x0, y0, x1, y1 = self.bbox_of(edges)
        k = min(W / max(60.0, (x1 - x0) * pad), H / max(60.0, (y1 - y0) * pad))
        return round(k, 3), ((x0 + x1) / 2, (y0 + y1) / 2)

    # -- routing -----------------------------------------------------------
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
        if a == b:
            return [self.nodes[a]], []
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

    def blocks_of(self, edges):
        seen, out = set(), []
        for e in edges:
            if e["bid"] > 0 and e["bid"] not in seen and e["bid"] in self.blocks:
                seen.add(e["bid"])
                out.append(self.blocks[e["bid"]])
        return out

    # -- basemap -----------------------------------------------------------
    def basemap(self, block_outline=0.0, block_fill=0.0, dim=1.0, k=1.0, only_roads=False):
        out = ['<g id="basemap">']
        if block_outline or block_fill:
            b = "".join(
                f'<path d="{poly_d(bl)}" fill="{INK}" fill-opacity="{block_fill}" '
                f'stroke="{INK}" stroke-opacity="{block_outline}" stroke-width="{1 / k:.2f}"/>'
                for bl in self.blocks.values()
            )
            out.append(f"<g>{b}</g>")
        if not only_roads:
            minor = " ".join(path_d([e["a"], e["b"]]) for e in self.streets if e["c"] == "minor")
            out.append(f'<path d="{minor}" fill="none" stroke="{INK}" '
                       f'stroke-opacity="{0.115 * dim:.3f}" stroke-width="{1.2 / k:.2f}"/>')
        road = " ".join(path_d([e["a"], e["b"]]) for e in self.roads)
        out.append(f'<path d="{road}" fill="none" stroke="{INK}" stroke-opacity="{0.26 * dim:.3f}" '
                   f'stroke-width="{2.0 / k:.2f}" stroke-linecap="round"/>')
        out.append("</g>")
        return "".join(out)


def path_d(pts):
    return "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in pts)


def poly_d(block):
    return " ".join(
        "M " + " L ".join(f"{x} {y}" for x, y in ring) + " Z" for ring in block["rings"]
    )


def zoom(body, cx, cy, k):
    """Scale the map group about a scene point, keeping it centred in frame."""
    tx, ty = W / 2 - cx * k, H / 2 - cy * k
    return f'<g transform="translate({tx:.1f},{ty:.1f}) scale({k})">{body}</g>'


# ---------------------------------------------------------------------------
# Front-end assets, redrawn as pure SVG
# ---------------------------------------------------------------------------
def kite(pt, color, scale=1.0, ghost=False, opacity=1.0, struck=False):
    """The app's waypoint marker: a ◆ head over a short stem, anchored at pt."""
    x, y = pt
    s = 12.0 * scale
    stem_h, stem_w = 14 * scale, 2.4 * scale
    hy = y - stem_h - s * 0.70
    ring = "rgba(0,0,0,0.72)"
    d = (f"M {x:.1f} {hy - s:.1f} L {x + s * 0.80:.1f} {hy:.1f} "
         f"L {x:.1f} {hy + s:.1f} L {x - s * 0.80:.1f} {hy:.1f} Z")
    g = [f'<g opacity="{opacity}">']
    if ghost:
        g.append(f'<path d="{d}" fill="{PAPER}" fill-opacity="0.72" stroke="{ring}" stroke-width="4.2"/>')
        g.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2.6" '
                 f'stroke-dasharray="5 3.6"/>')
        g.append(f'<rect x="{x - stem_w / 2:.1f}" y="{y - stem_h:.1f}" width="{stem_w:.1f}" '
                 f'height="{stem_h:.1f}" fill="{color}" opacity="0.45" stroke="{ring}" stroke-width="1"/>')
    else:
        g.append(f'<path d="{d}" fill="{color}" stroke="{ring}" stroke-width="2.6"/>')
        g.append(f'<rect x="{x - stem_w / 2:.1f}" y="{y - stem_h:.1f}" width="{stem_w:.1f}" '
                 f'height="{stem_h:.1f}" fill="{color}" opacity="0.72" stroke="{ring}" stroke-width="1"/>')
    if struck:
        bx, by, r = x + s * 1.9, hy - s * 1.4, s * 0.62
        g.append(f'<rect x="{bx - r * 1.7:.1f}" y="{by - r * 1.7:.1f}" width="{r * 3.4:.1f}" '
                 f'height="{r * 3.4:.1f}" fill="{PAPER}" fill-opacity="0.85" stroke="{color}" '
                 f'stroke-width="1.6" stroke-opacity="0.8"/>')
        g.append(f'<path d="M {bx - r:.1f} {by - r:.1f} L {bx + r:.1f} {by + r:.1f} '
                 f'M {bx + r:.1f} {by - r:.1f} L {bx - r:.1f} {by + r:.1f}" '
                 f'stroke="{color}" stroke-width="2.4" stroke-opacity="0.95" stroke-linecap="round"/>')
    g.append("</g>")
    return "".join(g)


def selection_line(pts, color=SEL, opacity=1.0, dashed=False, k=1.0, halo=True):
    """The desire-path selection polyline (RouteLayer's white stroke + halo)."""
    d = path_d(pts)
    dash = f' stroke-dasharray="{10 / k:.1f} {9 / k:.1f}"' if dashed else ""
    glow = (f'<path d="{d}" fill="none" stroke="{color}" stroke-opacity="0.14" '
            f'stroke-width="{18 / k:.1f}" stroke-linecap="round" stroke-linejoin="round"/>') if halo else ""
    return f"""<g opacity="{opacity}" style="mix-blend-mode:screen">
    {glow}
    <path d="{d}" fill="none" stroke="{color}" stroke-width="{7 / k:.1f}" stroke-linecap="round" stroke-linejoin="round"{dash}/>
  </g>"""


def heat_edges(edges, intensity=1.0, sign=1, k=1.0):
    """Cross-section heat, the way the canvas renderer strokes it: wide faint
    halo, warm body, hot core, peak filament — screen-blended."""
    if not edges or intensity <= 0:
        return ""
    if sign > 0:
        stops = [(HEAT["halo"], 26, 0.40), (HEAT["warm"], 15, 0.70),
                 (HEAT["hot"], 8.0, 0.90), (HEAT["peak"], 3.4, 1.0)]
    else:
        stops = [(HEAT["cold"], 24, 0.30), (HEAT["cold"], 13, 0.55),
                 (HEAT["coldDeep"], 6.0, 0.80)]
    d = " ".join(path_d([e["a"], e["b"]]) for e in edges)
    body = "".join(
        f'<path d="{d}" fill="none" stroke="{c}" stroke-width="{w * (0.5 + 0.5 * intensity) / k:.1f}" '
        f'stroke-opacity="{o * (0.3 + 0.7 * intensity):.2f}" stroke-linecap="round" stroke-linejoin="round"/>'
        for c, w, o in stops
    )
    return f'<g style="mix-blend-mode:screen">{body}</g>'


def block_glow(block, color, opacity, k=1.0):
    return (f'<path d="{poly_d(block)}" fill="{color}" fill-opacity="{opacity}" '
            f'stroke="{color}" stroke-opacity="{min(1, opacity * 2.6):.2f}" stroke-width="{1.4 / k:.2f}" '
            f'style="mix-blend-mode:screen"/>')


def block_outline(block, color=SEL, opacity=0.9, k=1.0, dashed=False):
    dash = f' stroke-dasharray="{7 / k:.1f} {5 / k:.1f}"' if dashed else ""
    return (f'<path d="{poly_d(block)}" fill="none" stroke="{color}" stroke-opacity="{opacity}" '
            f'stroke-width="{2.2 / k:.2f}"{dash}/>')


def tick(pt, color=INK, r=4.0, opacity=0.95, k=1.0):
    x, y = pt
    return (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r / k:.1f}" fill="{color}" fill-opacity="{opacity}" '
            f'stroke="{PAPER}" stroke-width="{1.4 / k:.1f}"/>')


# ---------------------------------------------------------------------------
# Panel chrome — brand furniture (logo mark, hairlines, caption, step ticks)
# ---------------------------------------------------------------------------
LOGO_CELLS = [
    (0, 0, "x"), (1, 0, ""), (2, 0, ""), (3, 0, ""), (4, 0, ""), (5, 0, "x"),
    (0, 1, "C"), (1, 1, "I"), (2, 1, "T"), (3, 1, "Y"), (4, 1, ""), (5, 1, ""),
    (0, 2, ""), (1, 2, ""), (2, 2, "E"), (3, 2, "D"), (4, 2, "I"), (5, 2, "T"),
    (0, 3, ""), (1, 3, "x"), (2, 3, ""), (3, 3, ""), (4, 3, ""), (5, 3, ""),
]


def logo(x, y, cell=16.0, color=INK):
    gap = cell * 0.25
    out = [f'<g transform="translate({x},{y})">']
    for col, row, kind in LOGO_CELLS:
        cx, cy = col * (cell + gap), row * (cell + gap)
        op = 0.16 if kind == "" else (0.3 if kind == "x" else 1.0)
        out.append(f'<rect x="{cx:.1f}" y="{cy:.1f}" width="{cell}" height="{cell}" fill="none" '
                   f'stroke="{color}" stroke-width="{cell * 0.075:.2f}" opacity="{op}"/>')
        if kind == "x":
            p = cell * 0.2
            out.append(f'<path d="M {cx + p:.1f} {cy + p:.1f} L {cx + cell - p:.1f} {cy + cell - p:.1f} '
                       f'M {cx + cell - p:.1f} {cy + p:.1f} L {cx + p:.1f} {cy + cell - p:.1f}" '
                       f'stroke="{color}" stroke-width="{cell * 0.11:.2f}" opacity="0.7"/>')
        elif kind:
            out.append(f'<text x="{cx + cell / 2:.1f}" y="{cy + cell / 2 + 0.5:.1f}" '
                       f'font-size="{cell * 0.66:.1f}" font-weight="600" fill="{color}" '
                       f'text-anchor="middle" dominant-baseline="central">{kind}</text>')
    out.append("</g>")
    return "".join(out)


def chrome(set_label, caption, step=None, steps=0, note=""):
    out = [f'<rect x="0" y="0" width="{W}" height="170" fill="url(#scrimTop)"/>',
           f'<rect x="0" y="{H - 260}" width="{W}" height="260" fill="url(#scrimBottom)"/>',
           logo(64, 58, 15)]
    out.append(f'<text x="{W - 64}" y="76" font-size="15" letter-spacing="5" '
               f'fill="{INK}" fill-opacity="0.5" text-anchor="end">{set_label.upper()}</text>')
    y = H - 92
    out.append(f'<line x1="64" y1="{y - 46}" x2="380" y2="{y - 46}" stroke="{INK}" '
               f'stroke-opacity="0.3" stroke-width="1"/>')
    out.append(f'<text x="64" y="{y}" font-size="40" font-weight="600" letter-spacing="2.5" '
               f'fill="{INK}">{caption.upper()}</text>')
    if note:
        out.append(f'<text x="64" y="{y + 34}" font-size="16" letter-spacing="1.2" '
                   f'fill="{INK}" fill-opacity="0.45">{note}</text>')
    for i in range(steps):
        cx = W - 64 - (steps - 1 - i) * 24
        cur = i == step
        out.append(f'<rect x="{cx - 7.5}" y="{H - 106}" width="15" height="15" '
                   f'fill="{INK if cur else "none"}" stroke="{INK}" stroke-width="1.3" '
                   f'opacity="{0.95 if i <= (step or 0) else 0.22}"/>')
    return "".join(out)


# ---------------------------------------------------------------------------
# Panel assembly
# ---------------------------------------------------------------------------
REPO = HERE.parent.parent
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
  <linearGradient id="scrimTop" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="{PAPER}" stop-opacity="0.9"/>
    <stop offset="1" stop-color="{PAPER}" stop-opacity="0"/>
  </linearGradient>
  <linearGradient id="scrimBottom" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="{PAPER}" stop-opacity="0"/>
    <stop offset="0.5" stop-color="{PAPER}" stop-opacity="0.8"/>
    <stop offset="1" stop-color="{PAPER}" stop-opacity="0.96"/>
  </linearGradient>
  <linearGradient id="scrimLeft" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="{PAPER}" stop-opacity="0.9"/>
    <stop offset="1" stop-color="{PAPER}" stop-opacity="0"/>
  </linearGradient>
  <radialGradient id="vignette" cx="0.5" cy="0.44" r="0.8">
    <stop offset="0.5" stop-color="{PAPER}" stop-opacity="0"/>
    <stop offset="1" stop-color="{PAPER}" stop-opacity="0.45"/>
  </radialGradient>
</defs>"""


def panel(body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{H:.0f}" '
            f'viewBox="0 0 {W:.0f} {H:.0f}">{DEFS}'
            f'<rect width="{W}" height="{H}" fill="{PAPER}"/>{body}'
            f'<rect width="{W}" height="{H}" fill="url(#vignette)" pointer-events="none"/></svg>')
