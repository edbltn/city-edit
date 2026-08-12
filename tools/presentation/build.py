"""Build the City Edit presentation panels.

Two groups, no basemap and no captions — the drawings carry themselves:

  01–04  the selection gesture: start → end → midpoint → midpoint removed
  05–08  how the core algorithms work, as stage-by-stage schematics

Route geometry is real (a slice of the NYC walk graph) but drawn on plain
paper; the algorithm panels are schematic. Markers, strokes and the heat ramp
are the app's own (see deck.py's palette).
"""

import math
from pathlib import Path

from deck import (
    END, H, HEAT, INK, PAPER, SEL, START, W, Scene,
    heat_edges, kite, panel, path_d, selection_line, tick,
)

OUT = Path(__file__).resolve().parent.parent.parent / "docs/presentation/story"
OUT.mkdir(parents=True, exist_ok=True)

wide = Scene("scene.json")
panels: list[tuple[str, str, str]] = []


def add(name, title, body):
    panels.append((name, title, panel(body)))


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------
def fitter(groups, pad_x=210, pad_y=150):
    """One shared transform for several polylines, so frames stay aligned."""
    pts = [p for g in groups for p in g]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    k = min((W - 2 * pad_x) / max(1.0, x1 - x0), (H - 2 * pad_y) / max(1.0, y1 - y0))
    ox = W / 2 - (x0 + x1) / 2 * k
    oy = H / 2 - (y0 + y1) / 2 * k
    return lambda pt: (pt[0] * k + ox, pt[1] * k + oy)


def scale_g(body, s):
    return f'<g transform="scale({s})">{body}</g>'


def at(cx, cy, body):
    return f'<g transform="translate({cx:.1f},{cy:.1f})">{body}</g>'


def arrow(x, y, length=88, opacity=0.32):
    """A thin step arrow between two stages."""
    return (f'<g opacity="{opacity}">'
            f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x + length - 13:.1f}" y2="{y:.1f}" '
            f'stroke="{INK}" stroke-width="1.6"/>'
            f'<path d="M {x + length:.1f} {y:.1f} L {x + length - 15:.1f} {y - 6.5:.1f} '
            f'L {x + length - 15:.1f} {y + 6.5:.1f} Z" fill="{INK}"/></g>')


def rng(seed):
    """Tiny deterministic LCG — same drawing every build."""
    state = seed

    def nxt():
        nonlocal state
        state = (state * 1103515245 + 12345) % 2147483648
        return state / 2147483648

    return nxt


def line(a, b, color=INK, width=2.0, opacity=1.0, dash=None, cap="round"):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<path d="{path_d([a, b])}" fill="none" stroke="{color}" stroke-width="{width}" '
            f'stroke-opacity="{opacity}" stroke-linecap="{cap}"{d}/>')


def rounded_rect(x, y, w, h, r, color=SEL, opacity=0.85, width=2.2, dash=None, fill="none"):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{r}" ry="{r}" '
            f'fill="{fill}" stroke="{color}" stroke-opacity="{opacity}" stroke-width="{width}"{d}/>')


def heat_seg(a, b, intensity=1.0, sign=1, scale=1.0):
    return heat_edges([{"a": a, "b": b}], intensity, sign, 1 / scale)


# ===========================================================================
# 01–04 — the selection: markers and the route they derive
# ===========================================================================
def selection_frames():
    s_id = wide.node_at((700, 762))
    e_id = wide.node_at((1148, 236))
    m_id = wide.node_at((1272, 512))
    direct_raw, _ = wide.route(s_id, e_id)
    via_raw, _ = wide.multi_route([s_id, m_id, e_id])

    f = fitter([direct_raw, via_raw])
    direct = [f(p) for p in direct_raw]
    via = [f(p) for p in via_raw]
    S, E, M = direct[0], direct[-1], f(wide.nodes[m_id])
    K = 1.9   # nothing else in frame, so the markers carry it

    add("01-start", "Start", kite(S, START, K))

    add("02-end", "End", selection_line(direct, k=0.72, halo=False) + kite(S, START, K) + kite(E, END, K))

    add("03-midpoint", "Midpoint",
        selection_line(direct, opacity=0.10, dashed=True, halo=False, k=0.72) +
        selection_line(via, k=0.72, halo=False) + kite(S, START, K) + kite(M, SEL, K) + kite(E, END, K))

    add("04-midpoint-removed", "Midpoint removed",
        selection_line(via, opacity=0.10, dashed=True, halo=False, k=0.72) +
        selection_line(direct, k=0.72, halo=False) + kite(M, SEL, K, ghost=True, opacity=0.7, struck=True) +
        kite(S, START, K) + kite(E, END, K))


# ===========================================================================
# 05 — Blocks: many edges in, one counted place out
# ===========================================================================
def street_schematic(length=300.0, gap=26.0, spans=4):
    """A street the way OSM has it: roadway, both sidewalks, crossing stubs —
    returns (svg, list of sub-edges as (a, b))."""
    half = length / 2
    step = length / spans
    edges = []
    for row in (-gap, 0.0, gap):
        for i in range(spans):
            edges.append(((-half + i * step, row), (-half + (i + 1) * step, row)))
    for i in range(spans + 1):
        x = -half + i * step
        edges.append(((x, -gap), (x, gap)))
    svg = "".join(line(a, b, INK, 2.2, 0.5) for a, b in edges)
    nodes = {p for e in edges for p in e}
    svg += "".join(tick(p, INK, 3.0, 0.45) for p in sorted(nodes))
    return svg, edges


def alg_blocks():
    length, gap, spans = 300.0, 26.0, 4
    schematic, edges = street_schematic(length, gap, spans)
    r = rng(7)
    picks, seen = [], set()
    while len(picks) < 10:
        i = int(r() * len(edges))
        if i in seen:
            continue
        seen.add(i)
        (ax, ay), (bx, by) = edges[i]
        t = 0.3 + r() * 0.4
        picks.append((ax + (bx - ax) * t, ay + (by - ay) * t))

    votes = "".join(tick(p, INK, 5.4, 0.95) for p in picks)
    block = rounded_rect(-length / 2 - 20, -gap - 20, length + 40, gap * 2 + 40, 22, SEL, 0.85)
    lit = "".join(heat_seg(a, b, 0.95) for a, b in edges)

    lit = (rounded_rect(-length / 2 - 20, -gap - 20, length + 40, gap * 2 + 40, 22,
                        HEAT["warm"], 0.5, 1.6, fill=HEAT["warm"]).replace(
               'fill="rgb(196,96,56)"', 'fill="rgb(196,96,56)" fill-opacity="0.10"') +
           heat_seg((-length / 2, 0), (length / 2, 0), 1.0))

    y = H / 2
    body = [
        at(270, y, scale_g(schematic + votes, 1.25)),
        arrow(505, y, 80),
        at(800, y, scale_g(schematic + votes + block, 1.25)),
        arrow(1035, y, 80),
        at(1330, y, scale_g(lit + tick((0, -gap - 62), INK, 5.6, 1.0), 1.25)),
    ]
    add("05-algorithm-blocks", "Blocks", "".join(body))


# ===========================================================================
# 06 — Corridor growth: seed, grow off both tips, pin ghosts
# ===========================================================================
CORRIDOR = [(-190, 120), (-96, 96), (-16, 34), (58, 8), (128, -58), (196, -120)]


def vote_field(seed=11, n=22, spread=(235, 125)):
    r = rng(seed)
    out = []
    for _ in range(n):
        x = (r() - 0.5) * spread[0] * 2
        y = (r() - 0.5) * spread[1] * 2
        ang = r() * math.pi
        ln = 26 + r() * 34
        a = (x - math.cos(ang) * ln / 2, y - math.sin(ang) * ln / 2)
        b = (x + math.cos(ang) * ln / 2, y + math.sin(ang) * ln / 2)
        out.append((a, b, 0.22 + r() * 0.4))
    return out


def alg_corridor():
    field = vote_field()
    field_svg = "".join(heat_seg(a, b, i) for a, b, i in field)
    seed_a, seed_b = CORRIDOR[2], CORRIDOR[3]

    def corridor_svg(upto):
        return "".join(heat_seg(CORRIDOR[i], CORRIDOR[i + 1], 0.95)
                       for i in range(len(CORRIDOR) - 1) if i in upto)

    # 1 — the vote field
    stage1 = field_svg
    # 2 — the heaviest edge is the seed
    stage2 = field_svg + heat_seg(seed_a, seed_b, 1.0) + rounded_rect(
        min(seed_a[0], seed_b[0]) - 22, min(seed_a[1], seed_b[1]) - 22,
        abs(seed_b[0] - seed_a[0]) + 44, abs(seed_b[1] - seed_a[1]) + 44, 18, SEL, 0.9, 2.0)
    # 3 — grow off either tip
    field_dim = "".join(heat_seg(a, b, i * 0.3) for a, b, i in field)
    stage3 = (field_dim + corridor_svg({1, 2, 3}) +
              arrow_along(CORRIDOR[2], CORRIDOR[1]) + arrow_along(CORRIDOR[3], CORRIDOR[4]))
    # 4 — the finished corridor, expressed as waypoints
    ghosts = [CORRIDOR[1], CORRIDOR[2], CORRIDOR[4]]
    stage4 = (corridor_svg(set(range(len(CORRIDOR) - 1))) +
              selection_line(CORRIDOR, k=1.35, opacity=0.9, halo=False) +
              "".join(kite(g, SEL, 0.95, ghost=True) for g in ghosts) +
              kite(CORRIDOR[0], START, 1.05) + kite(CORRIDOR[-1], END, 1.05))

    cx1, cx2, cy1, cy2 = 440, 1160, 265, 655
    body = [at(cx1, cy1, scale_g(stage1, 1.15)), at(cx2, cy1, scale_g(stage2, 1.15)),
            at(cx1, cy2, scale_g(stage3, 1.15)), at(cx2, cy2, scale_g(stage4, 1.15)),
            arrow(cx1 + 300, cy1), arrow(cx1 + 300, cy2)]
    add("06-algorithm-corridor", "Corridor growth", "".join(body))


def arrow_along(a, b, length=38, opacity=0.55):
    """A growth arrow pointing from a towards b, drawn past b's tip."""
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    sx, sy = b[0] + math.cos(ang) * 12, b[1] + math.sin(ang) * 12
    ex, ey = sx + math.cos(ang) * length, sy + math.sin(ang) * length
    hx, hy = ex - math.cos(ang) * 15, ey - math.sin(ang) * 15
    n = (-math.sin(ang) * 6.5, math.cos(ang) * 6.5)
    return (f'<g opacity="{opacity}">'
            f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{hx:.1f}" y2="{hy:.1f}" stroke="{INK}" stroke-width="1.8"/>'
            f'<path d="M {ex:.1f} {ey:.1f} L {hx + n[0]:.1f} {hy + n[1]:.1f} '
            f'L {hx - n[0]:.1f} {hy - n[1]:.1f} Z" fill="{INK}"/></g>')


# ===========================================================================
# 07 — Signed heat: up minus down, and zero is invisible
# ===========================================================================
def tally(up, down, width=190):
    """Up-triangles over down-triangles — the two sides of one block's argument."""
    out = []
    for i in range(up):
        x = -width / 2 + (i + 0.5) * (width / max(up, 1))
        out.append(f'<path d="M {x:.1f} -104 L {x + 7:.1f} -91 L {x - 7:.1f} -91 Z" '
                   f'fill="{INK}" fill-opacity="0.9"/>')
    for i in range(down):
        x = -width / 2 + (i + 0.5) * (width / max(down, 1))
        out.append(f'<path d="M {x:.1f} -60 L {x + 7:.1f} -73 L {x - 7:.1f} -73 Z" '
                   f'fill="{INK}" fill-opacity="0.45"/>')
    out.append(line((-width / 2 - 14, -80), (width / 2 + 14, -80), INK, 1.2, 0.28))
    return "".join(out)


def alg_heat():
    bar_a, bar_b = (-115, 20), (115, 20)
    cols = [(300, 9, 1, 1, 1.0), (800, 1, 9, -1, 0.95), (1300, 5, 5, 0, 0.0)]
    body = []
    for cx, up, down, sign, intensity in cols:
        if sign == 0:
            heat = (line(bar_a, bar_b, INK, 15, 0.06) +
                    line(bar_a, bar_b, INK, 1.2, 0.16, dash="6 7"))
        else:
            heat = heat_seg(bar_a, bar_b, intensity, sign)
        body.append(at(cx, H / 2 + 40, scale_g(tally(up, down) + heat, 1.5)))
    add("07-algorithm-heat", "Signed heat", "".join(body))


# ===========================================================================
# 08 — Threading: a waypoint dropped on a proposal routes through it
# ===========================================================================
def alg_threading():
    a, b = (-250, 130), (250, -130)
    shortest = [a, (-90, 118), (10, -10), (150, -50), b]
    proposal = [(-140, 40), (-40, 92), (70, 74), (150, 6), (196, -74)]
    drop = proposal[2]
    threaded = [a, (-150, 126)] + proposal[2:] + [(230, -108), b]

    stage1 = ("".join(heat_seg(proposal[i], proposal[i + 1], 0.85)
                      for i in range(len(proposal) - 1)) +
              selection_line(shortest, k=1.3, halo=False) + kite(a, START, 1.1) + kite(b, END, 1.1))

    stage2 = ("".join(heat_seg(proposal[i], proposal[i + 1], 0.85)
                      for i in range(len(proposal) - 1)) +
              selection_line(shortest, opacity=0.10, dashed=True, halo=False, k=1.3) +
              selection_line(threaded, k=1.3, halo=False) +
              kite(a, START, 1.1) + kite(drop, SEL, 1.1) + kite(b, END, 1.1))

    body = [at(400, H / 2, stage1), arrow(W / 2 - 44, H / 2), at(1200, H / 2, stage2)]
    add("08-algorithm-threading", "Threading a proposal", "".join(body))


# ===========================================================================
selection_frames()
alg_blocks()
alg_corridor()
alg_heat()
alg_threading()

for old in OUT.glob("*.svg"):
    old.unlink()
for name, _t, svg in panels:
    (OUT / f"{name}.svg").write_text(svg)

pages = "\n".join(
    f'<section><img src="{name}.svg" alt="{title}"></section>' for name, title, _ in panels
)
(OUT / "deck.html").write_text(f"""<!doctype html>
<meta charset="utf-8">
<title>City Edit — panels</title>
<style>
  @page {{ size: 16.667in 9.375in; margin: 0; }}
  html, body {{ margin: 0; padding: 0; background: {PAPER}; }}
  section {{ width: 1600px; height: 900px; page-break-after: always; overflow: hidden; }}
  section:last-child {{ page-break-after: auto; }}
  img {{ display: block; width: 1600px; height: 900px; }}
</style>
{pages}
""")
print(f"wrote {len(panels)} panels to {OUT}")
print("\n".join(n for n, _, _ in panels))
