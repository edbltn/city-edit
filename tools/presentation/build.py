"""Build the City Edit presentation panels.

Eight slides: the basemap, the selection built up waypoint by waypoint, the
graph→block mapping, block heat, and the two families of top proposal. Art on
the right, a short scannable note on the left. Everything drawn is the app's
own asset (see deck.py).
"""

import base64
import math
from pathlib import Path

from deck import (
    END, INK, PAPER, SEL, START, W, H, Scene,
    at, block_heat, block_selected, fit_into, kite, line, panel,
    path_d, poly_d, proposal_icon, selection_line, text_block, tick,
)

OUT = Path(__file__).resolve().parent.parent.parent / "docs/presentation/story"
OUT.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent

wide = Scene("scene.json")
panels: list[tuple[str, str, str]] = []

TEXT_X = 96
ART = (700, 90, 1540, 810)      # the art column: x0, y0, x1, y1


def add(name, title, body):
    panels.append((name, title, panel(body)))


# ===========================================================================
# 01 — The basemap: Leaflet + CARTO
# ===========================================================================
def slide_basemap():
    png = base64.b64encode((HERE / "basemap.png").read_bytes()).decode()
    b = [f'<image href="data:image/png;base64,{png}" x="0" y="0" width="{W}" height="{H}"/>',
         f'<rect x="0" y="0" width="660" height="{H}" fill="{PAPER}" opacity="0.88"/>',
         f'<rect x="660" y="0" width="260" height="{H}" fill="url(#scrimRight)"/>',
         text_block(TEXT_X, 300, "Basemap", [
             "**Leaflet** owns the camera —",
             "42 kB, imperative, plugin-rich.",
             "**CARTO dark** raster tiles, split",
             "**nolabels** + **only_labels**, so text",
             "sits **above** the vote heat.",
         ])]
    add("01-basemap", "Basemap", "".join(b))


# ===========================================================================
# 02–04 — The selection, one waypoint at a time
# ===========================================================================
def slides_selection():
    s_id = wide.node_at((700, 762))
    e_id = wide.node_at((1148, 236))
    m_id = wide.node_at((1272, 512))
    direct_raw, _ = wide.route(s_id, e_id)
    via_raw, _ = wide.multi_route([s_id, m_id, e_id])
    f = fit_into([direct_raw, via_raw], (ART[0] + 70, ART[1] + 50, ART[2] - 70, ART[3] - 50))
    direct = [f(p) for p in direct_raw]
    via = [f(p) for p in via_raw]
    S, E, M = direct[0], direct[-1], f(wide.nodes[m_id])
    K = 1.7

    add("02-waypoint-start", "Start waypoint",
        kite(S, START, K) +
        text_block(TEXT_X, 205, "Waypoints", [
            "One **ordered list of coordinates**",
            "is the whole interface.",
            "The URL is the serialization:",
            "**?w=lat,lng;…&vt=<label>**",
            "A point **snaps to the walk graph** —",
            "**Flatbush** R-tree hit-test, O(log n)",
            "over 3.3 M edges. Coordinates survive",
            "a **graph rebuild**; edge ids don't.",
        ]))

    add("03-waypoint-route", "Route",
        selection_line(direct) + kite(S, START, K) + kite(E, END, K) +
        text_block(TEXT_X, 205, "Routing", [
            "The path between waypoints is",
            "**derived**, never stored.",
            "**OSRM**, self-hosted: **foot profile**,",
            "**MLD** partitioning, sub-ms queries.",
            "**annotations=nodes** hands back **OSM**",
            "**node ids** → our edge ids, 1:1.",
            "The votable graph is built from the",
            "**same PBF + foot filter** as OSRM.",
        ]))

    add("04-waypoint-mid", "Midpoint",
        selection_line(direct, opacity=0.10, dashed=True) + selection_line(via) +
        kite(S, START, K) + kite(M, SEL, K) + kite(E, END, K) +
        text_block(TEXT_X, 150, "Mid waypoints", [
            "**Drag the polyline** to pull out a mid;",
            "the route re-derives through it.",
            "Tap vs drag is **time-based** —",
            "**TAP_MAX_MS = 300**, one feel for the",
            "marker and the path alike.",
            "First is **start**, last is **end**, the",
            "middle **rebalances**. Phase, vote type",
            "and button state are all **derived**.",
            "**Tap a kite to delete** it — one pure",
            "reducer, no async, 46 unit tests.",
        ]))


# ===========================================================================
# 05–07 — An X intersection: graph → blocks → heat → the point proposal
# ===========================================================================
T1, T2 = math.radians(24), math.radians(24 + 78)
D1 = (math.cos(T1), math.sin(T1))
D2 = (math.cos(T2), math.sin(T2))
N1 = (-math.sin(T1), math.cos(T1))
N2 = (-math.sin(T2), math.cos(T2))
WALK = 23.0            # sidewalk offset from the roadway centreline
BAND1 = BAND2 = 41.0   # block half-width across the street
ARM = 232.0            # arm length out from the junction
STUB = 62.0            # crossing-stub station along each arm


def solve(r1, v1, r2, v2):
    det = r1[0] * r2[1] - r1[1] * r2[0]
    return ((v1 * r2[1] - r1[1] * v2) / det, (r1[0] * v2 - v1 * r2[0]) / det)


def axis_pt(d, n, s, o):
    return (d[0] * s + n[0] * o, d[1] * s + n[1] * o)


def street_lines():
    out = []
    for d, n in ((D1, N1), (D2, N2)):
        for o in (-WALK, 0.0, WALK):          # roadway + both sidewalks
            out.append((axis_pt(d, n, -ARM, o), axis_pt(d, n, ARM, o)))
        for s in (-STUB, STUB):               # the crossing stubs
            out.append((axis_pt(d, n, s, -WALK), axis_pt(d, n, s, WALK)))
    return out


def street_nodes():
    pts = [solve(N1, o1, N2, o2) for o1 in (-WALK, 0, WALK) for o2 in (-WALK, 0, WALK)]
    for d, n in ((D1, N1), (D2, N2)):
        for s in (-STUB, STUB):
            pts += [axis_pt(d, n, s, o) for o in (-WALK, 0, WALK)]
    return pts


def junction_poly():
    return [solve(N1, s1 * BAND1, N2, s2 * BAND2)
            for s1, s2 in ((1, 1), (1, -1), (-1, -1), (-1, 1))]


def arm_polys():
    out = []
    for d, n, on, ob in ((D1, N1, N2, BAND2), (D2, N2, N1, BAND1)):
        sigma = 1 if (on[0] * d[0] + on[1] * d[1]) > 0 else -1
        half = BAND1 if n is N1 else BAND2
        for way in (1, -1):
            s = sigma * way
            out.append([
                solve(n, half, on, s * ob),
                solve(n, half, d, way * ARM),
                solve(n, -half, d, way * ARM),
                solve(n, -half, on, s * ob),
            ])
    return out


def x_scene(block_paint=None, ghost=1.0, extra=""):
    """The X intersection. `block_paint(i, path)` paints the five blocks
       (0 = junction, 1–4 = arms); `ghost` fades the graph beneath them."""
    g = []
    for a, b in street_lines():                       # the graph, ghosted underneath
        g.append(line(a, b, INK, 2.2, 0.6 * ghost))
    for p in street_nodes():
        g.append(tick(p, INK, 3.6, 0.85 * ghost))
    if block_paint:
        for i, p in enumerate([junction_poly()] + arm_polys()):
            g.append(block_paint(i, path_d(p, close=True)))
    g.append(extra)
    return at((ART[0] + ART[2]) / 2, (ART[1] + ART[3]) / 2, "".join(g), 1.42)


HEATS = [0.92, 0.0, 0.46, 0.0, 0.18]   # junction hottest; two arms carry nothing


def slides_blocks():
    add("05-graph-to-blocks", "Graph → blocks",
        x_scene(lambda i, d: block_selected(d), ghost=0.85) +
        text_block(TEXT_X, 150, "Blocks", [
            "One street is **6–20 OSM edges**:",
            "roadway, both sidewalks, crossing stubs.",
            "Membership is decided **topologically**",
            "**first**, edge by edge — then each polygon",
            "is generated **from its own members**.",
            "Junction nodes **cluster** into a single",
            "intersection block; a **Voronoi trim**",
            "keeps every block **disjoint**.",
            "Coverage holds **by construction**, and a",
            "device counts **once per block, per type**.",
        ]))

    add("06-block-heat", "Block heat",
        x_scene(lambda i, d: block_heat(d, HEATS[i], 2.0), ghost=0.55) +
        text_block(TEXT_X, 205, "Heat", [
            "A block's heat is its **top proposal's**",
            "**differential** — up minus down.",
            "**Log-normalized per arm**, with ceilings",
            "floored so a quiet map can't saturate.",
            "Warm arm, cold arm, and **zero is**",
            "**invisible** — cancelled signal, no heat.",
            "Applied as **MapLibre feature-state**,",
            "diffed per block, never re-styled.",
        ]))

    pin = proposal_icon((0, 4), "safety", heat=0.92, selected=True, scale=1.7)
    add("07-top-proposal-point", "Point proposal",
        x_scene(lambda i, d: block_heat(d, HEATS[i], 2.0), ghost=0.5, extra=pin) +
        text_block(TEXT_X, 150, "Top proposal · point", [
            "Per block, per **vote type**:",
            "**net = up − down**, ranked by differential.",
            "The block's winner becomes **one pin**.",
            "Support floor: **TOP_PROPOSAL_MIN_NET**",
            "**= 100**, overridable per map.",
            "**6 per type** reach the spacing step, then",
            "same-type **non-max suppression at 600 m**",
            "so one avenue can't stack identical pins.",
            "Rank drives the **glow ring** — never the",
            "outline, which belongs to hover.",
        ]))


# ===========================================================================
# 08 — The route family
# ===========================================================================
def slide_route_proposal():
    edges = wide.street_edges("Broadway", (120, 300, 1480, 800))
    blocks = wide.blocks_of(edges)
    # Drop the junction blocks: a block carrying a second street name is a
    # crossing, not part of this corridor.
    names = {}
    for e in wide.streets:
        names.setdefault(e["bid"], set()).add(e["n"])
    blocks = [b for b in blocks if names.get(b["id"], set()) <= {"Broadway", ""}]

    ordered = sorted(blocks, key=lambda b: b["c"][1])
    ang = math.atan2(ordered[-1]["c"][1] - ordered[0]["c"][1],
                     ordered[-1]["c"][0] - ordered[0]["c"][0])
    turn = math.radians(122) - ang          # lay the corridor across the frame
    ca, sa = math.cos(turn), math.sin(turn)
    piv = ordered[0]["c"]

    def rot(p):
        dx, dy = p[0] - piv[0], p[1] - piv[1]
        return (piv[0] + dx * ca - dy * sa, piv[1] + dx * sa + dy * ca)

    rings = [[rot(p) for p in r] for b in blocks for r in b["rings"]]
    f = fit_into(rings, (ART[0] + 40, ART[1] + 60, ART[2] - 40, ART[3] - 60))
    mv = sorted(
        ({"rings": [[list(f(rot(p))) for p in r] for r in b["rings"]], "c": f(rot(b["c"]))}
         for b in ordered),
        key=lambda b: b["c"][0],
    )
    # Heat tapers from the corridor's heart out to its tips — what peeling from
    # the heaviest edge actually leaves behind.
    def profile(i):
        t = i / max(1, len(mv) - 1)
        return round(0.32 + 0.63 * math.sin(math.pi * t) ** 0.7, 3)

    art = "".join(block_heat(poly_d(b), profile(i), 2.0) for i, b in enumerate(mv))

    spine = [b["c"] for b in mv]
    ghosts = [spine[len(spine) // 4], spine[len(spine) // 2], spine[3 * len(spine) // 4]]
    art += selection_line(spine, opacity=0.9, width=5)
    art += "".join(kite(g, SEL, 1.3) for g in ghosts)
    art += kite(spine[0], START, 1.4) + kite(spine[-1], END, 1.4)
    art += proposal_icon(spine[len(spine) // 2 - 1], "walkways", diamond=True,
                         heat=0.95, selected=True, scale=2.3)

    add("08-top-proposal-route", "Route proposal", art +
        text_block(TEXT_X, 120, "Top proposal · route", [
            "Edges with **net ≥ 1** form a per-type",
            "subgraph → **connected components**.",
            "**Peel** a corridor from the heaviest edge,",
            "taking the strongest arc off either tip.",
            "Budget **2700 m + 660·√score**, cap 10.5 km",
            "— support buys reach **sublinearly**.",
            "Keep an extension only if routing still",
            "**hands the stretch back** (**≥ 85 % of blocks**);",
            "otherwise **pin a ghost waypoint** there.",
            "**MAX_GHOST_WAYPOINTS = 3**, so a corridor",
            "is always **≤ 5 waypoints** — shareable,",
            "re-routable, **deterministic** on every client.",
        ], size=19, leading=36))


# ===========================================================================
slide_basemap()
slides_selection()
slides_blocks()
slide_route_proposal()

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
