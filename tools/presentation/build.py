"""Build the City Edit story panels (SVG) + the deck HTML."""

import math
from pathlib import Path

FOCUS_BLOCK = 207728

from deck import (
    END, H, HEAT, INK, PAPER, SEL, START, W, Scene,
    block_glow, block_outline, chrome, heat_edges, kite, logo, panel,
    path_d, selection_line, tick, zoom,
)

OUT = Path(__file__).resolve().parent.parent.parent / "docs/presentation/story"
OUT.mkdir(parents=True, exist_ok=True)

wide = Scene("scene.json")
close = Scene("scene_close.json")

panels: list[tuple[str, str, str]] = []


def add(name, title, body):
    panels.append((name, title, panel(body)))


def wide_base(dim=1.0):
    return wide.basemap(block_outline=0.055, block_fill=0.012, dim=1.35 * dim)


def url_strip(text, y=None):
    y = y if y is not None else 196
    return (f'<rect x="64" y="{y}" width="{W - 128}" height="60" fill="{PAPER}" fill-opacity="0.82" '
            f'stroke="{INK}" stroke-opacity="0.28"/>'
            f'<text x="90" y="{y + 39}" font-size="21" letter-spacing="0.5" fill="{INK}" '
            f'fill-opacity="0.92">{text}</text>')


# ===========================================================================
# 00 — Title
# ===========================================================================
def title_panel():
    b = [wide.basemap(dim=1.5, block_outline=0.05, block_fill=0.02),
         f'<rect width="{W}" height="{H}" fill="{PAPER}" opacity="0.18"/>',
         f'<rect x="0" y="0" width="820" height="{H}" fill="url(#scrimLeft)"/>',
         logo(120, 130, 33),
         f'<line x1="120" y1="480" x2="720" y2="480" stroke="{INK}" stroke-opacity="0.35"/>',
         f'<text x="120" y="566" font-size="48" font-weight="600" letter-spacing="4" fill="{INK}">HOW A CITY</text>',
         f'<text x="120" y="628" font-size="48" font-weight="600" letter-spacing="4" fill="{INK}">ARGUES WITH ITSELF</text>',
         f'<text x="120" y="726" font-size="17" letter-spacing="5" fill="{INK}" fill-opacity="0.45">'
         f'CITYEDIT.ORG</text>']
    for i, (col, lab) in enumerate([(START, "START"), (SEL, "WAYPOINT"), (END, "END")]):
        x = 1180
        y = 480 + i * 84
        b.append(kite((x, y), col, 1.25))
        b.append(f'<text x="{x + 52}" y="{y - 14}" font-size="16" letter-spacing="3.5" '
                 f'fill="{INK}" fill-opacity="0.55">{lab}</text>')
    add("00-title", "Title", "".join(b))


# ===========================================================================
# SET A — Selection: one ordered list of coordinates is the whole interface
# ===========================================================================
def set_a():
    s_id = wide.node_at((700, 762))
    e_id = wide.node_at((1148, 236))
    m_id = wide.node_at((1272, 512))
    S, E, M = wide.nodes[s_id], wide.nodes[e_id], wide.nodes[m_id]
    direct, _ = wide.route(s_id, e_id)
    via, _ = wide.multi_route([s_id, m_id, e_id])

    label = "01 · Selection"
    steps = 6
    add("a0-map", "The map", wide_base() +
        chrome(label, "The map", 0, steps, "nothing selected — no mode, no state"))

    add("a1-start", "Start", wide_base() + kite(S, START, 1.3) +
        chrome(label, "Start", 1, steps, "waypoints[0]"))

    add("a2-end", "End", wide_base() + selection_line(direct) + kite(S, START, 1.3) + kite(E, END, 1.3) +
        chrome(label, "End", 2, steps, "waypoints[n−1] — the route between them is derived"))

    add("a3-mid", "Midpoint", wide_base() + selection_line(direct, opacity=0.09, dashed=True, halo=False) +
        selection_line(via) + kite(S, START, 1.3) + kite(M, SEL, 1.3) + kite(E, END, 1.3) +
        chrome(label, "Midpoint", 3, steps, "insert a mid — the route re-derives through it"))

    add("a4-removed", "Removed", wide_base() + selection_line(via, opacity=0.09, dashed=True, halo=False) +
        selection_line(direct) + kite(M, SEL, 1.3, ghost=True, opacity=0.75, struck=True) +
        kite(S, START, 1.3) + kite(E, END, 1.3) +
        chrome(label, "Removed", 4, steps, "remove it — nothing else had to be told"))

    add("a5-url", "Shareable", wide_base() + selection_line(direct) + kite(S, START, 1.3) + kite(E, END, 1.3) +
        url_strip("?w=40.7391,-73.9905;40.7462,-73.9862&amp;vt=Pedestrianise+this+street") +
        chrome(label, "Shareable", 5, steps, "the list is the whole serialization"))


# ===========================================================================
# SET B — Grain: votes are stored on edges, counted on blocks
# ===========================================================================
def set_b():
    focus = close.blocks[FOCUS_BLOCK]
    fam = close.edges_in_block(focus["id"])
    k, c = close.fit(fam, pad=1.18)
    cx, cy = c

    def base(extra=""):
        return zoom(close.basemap(dim=2.6, k=k ** 0.55) + extra, cx, cy, k)

    fam_lines = "".join(
        f'<path d="{path_d([e["a"], e["b"]])}" fill="none" stroke="{INK}" stroke-opacity="0.75" '
        f'stroke-width="{2.6 / k:.2f}" stroke-linecap="round"/>' for e in fam)
    ends = "".join(tick(p, INK, 3.4, 0.75, k) for e in fam for p in (e["a"], e["b"]))

    # ten votes, each on a different member edge
    ordered = sorted(fam, key=lambda e: -math.dist(e["a"], e["b"]))
    picks = ordered[:10] if len(ordered) >= 10 else ordered
    marks = "".join(
        tick(((e["a"][0] + e["b"][0]) / 2, (e["a"][1] + e["b"][1]) / 2), INK, 8.0, 0.95, k)
        for e in picks)

    label = "02 · Grain"
    steps = 4
    add("b0-edges", "One street", base(fam_lines + ends) +
        chrome(label, "One street", 0, steps,
               f"{len(fam)} OSM edges — roadway, both sidewalks, crossing stubs"))

    add("b1-votes", "Ten votes", base(fam_lines + marks) +
        chrome(label, "Ten votes", 1, steps, "ten people, ten different edges, ten faint marks"))

    add("b2-block", "One block", base(fam_lines + block_outline(focus, SEL, 0.85, k) + marks) +
        chrome(label, "One block", 2, steps, "every edge belongs to exactly one block"))

    add("b3-counted", "Counted once", base(
        block_glow(focus, HEAT["hot"], 0.06, k) + heat_edges(fam, 0.9, 1, k * 1.45) + block_outline(focus, SEL, 0.4, k)) +
        chrome(label, "Counted once", 3, steps,
               "one device counts once per block per vote type"))


# ===========================================================================
# SET C — Heat is a signed argument, not a traffic count
# ===========================================================================
def set_c():
    box = (120, 90, 1480, 810)
    bway = wide.street_edges("Broadway", (120, 340, 1480, 810))
    fifth = wide.street_edges("5th Avenue", box)
    park = wide.street_edges("Park Avenue South", box)

    label = "03 · Heat"
    steps = 5
    add("c0-quiet", "Quiet", wide_base() +
        chrome(label, "Quiet", 0, steps, "a block with no conclusion carries no heat"))

    add("c1-support", "Support", wide_base() + heat_edges(bway, 1.0, 1) +
        chrome(label, "Support", 1, steps, "net + · the warm arm"))

    add("c2-opposition", "Opposition", wide_base() + heat_edges(bway, 1.0, 1) +
        heat_edges(fifth, 0.85, -1) +
        chrome(label, "Opposition", 2, steps, "net − · the cold arm"))

    add("c3-contested", "Contested", wide_base() + heat_edges(bway, 1.0, 1) +
        heat_edges(fifth, 0.85, -1) + heat_edges(park, 0.7, 1) +
        chrome(label, "Contested", 3, steps, "+128 for · −127 against"))

    add("c4-cancelled", "Cancelled", wide_base() + heat_edges(bway, 1.0, 1) +
        heat_edges(fifth, 0.85, -1) +
        chrome(label, "Cancelled", 4, steps, "+128 −128 = 0 · zero is invisible"))


# ===========================================================================
# SET D — Proposals: votes aggregate back up into a corridor
# ===========================================================================
def set_d():
    box = (120, 210, 1480, 800)
    corridor_edges = wide.street_edges("Broadway", box)
    corr_pts = _chain(corridor_edges)
    blocks = wide.blocks_of(corridor_edges)
    seed = max(blocks, key=lambda b: b["len"])
    seed_edges = [e for e in corridor_edges if e["bid"] == seed["id"]]

    # a spray of individually-supported blocks, corridor blocks among them
    spray = sorted(wide.blocks.values(), key=lambda b: math.dist(b["c"], (830, 450)))[:190:7]
    # scattered support: a spray of blocks, plus only every other corridor block —
    # the corridor is not visible yet, it has to be grown.
    scatter = ("".join(heat_edges(wide.edges_in_block(b["id"]), 0.33, 1) for b in spray) +
               "".join(heat_edges(wide.edges_in_block(b["id"]), 0.42, 1) for b in blocks[::2]))

    ghosts = [corr_pts[len(corr_pts) // 4], corr_pts[len(corr_pts) // 2],
              corr_pts[3 * len(corr_pts) // 4]]

    label = "04 · Proposal"
    steps = 5
    add("d0-votes", "Support", wide_base() + scatter +
        chrome(label, "Support", 0, steps, "point-level support answers where, not what"))

    add("d1-seed", "Seed", wide_base() + scatter + heat_edges(seed_edges, 1.0, 1) +
        block_outline(seed, SEL, 0.85) +
        chrome(label, "Seed", 1, steps, "start from the heaviest block"))

    add("d2-corridor", "Corridor", wide_base() + scatter + heat_edges(corridor_edges, 1.0, 1) +
        chrome(label, "Corridor", 2, steps, "grow off either tip while the votes pay for the metres"))

    add("d3-ghosts", "Ghost waypoints", wide_base() + heat_edges(corridor_edges, 0.85, 1) +
        "".join(kite(g, SEL, 1.15, ghost=True) for g in ghosts) +
        chrome(label, "Ghost waypoints", 3, steps, "pin the tip where routing would not hand it back"))

    add("d4-shareable", "Five waypoints", wide_base() + heat_edges(corridor_edges, 0.3, 1) +
        selection_line(corr_pts) + "".join(kite(g, SEL, 1.15, ghost=True) for g in ghosts) +
        kite(corr_pts[0], START, 1.3) + kite(corr_pts[-1], END, 1.3) +
        url_strip("?w=◆;◆;◆;◆;◆&amp;vt=Add+protected+bike+lane") +
        chrome(label, "Five waypoints", 4, steps,
               "two anchors + three ghosts — a proposal you can share and walk"))


def _chain(edges):
    """Order a street's edges into one polyline (they arrive unordered)."""
    if not edges:
        return []
    ends = {}
    for e in edges:
        ends.setdefault(e["u"], []).append((e["v"], e))
        ends.setdefault(e["v"], []).append((e["u"], e))
    start = next((n for n, v in ends.items() if len(v) == 1), next(iter(ends)))
    pts, seen, cur, prev = [], set(), start, None
    scene_nodes = wide.nodes
    while cur is not None:
        pts.append(scene_nodes[cur])
        seen.add(cur)
        nxt = None
        for n, _e in ends.get(cur, ()):
            if n not in seen:
                nxt = n
                break
        prev, cur = cur, nxt
    return pts


# ===========================================================================
# 99 — Closing
# ===========================================================================
def closing_panel():
    box = (120, 90, 1480, 810)
    warm = wide.street_edges("Broadway", (120, 340, 1480, 810)) + \
        wide.street_edges("East 23rd", box) + wide.street_edges("West 23rd", box)
    cold = wide.street_edges("5th Avenue", box)
    b = [wide_base(1.15), heat_edges(warm, 1.0, 1), heat_edges(cold, 0.8, -1),
         f'<rect width="{W}" height="{H}" fill="{PAPER}" opacity="0.25"/>',
         f'<rect x="0" y="{H - 420}" width="{W}" height="420" fill="url(#scrimBottom)"/>',
         logo(64, 58, 15),
         f'<line x1="64" y1="{H - 250}" x2="700" y2="{H - 250}" stroke="{INK}" stroke-opacity="0.3"/>',
         f'<text x="64" y="{H - 178}" font-size="38" font-weight="600" letter-spacing="2.5" fill="{INK}">'
         f'NOT WHERE PEOPLE ARE.</text>',
         f'<text x="64" y="{H - 126}" font-size="38" font-weight="600" letter-spacing="2.5" fill="{INK}">'
         f'WHERE A CITY HAS REACHED A CONCLUSION.</text>',
         f'<text x="{W - 64}" y="{H - 126}" font-size="17" letter-spacing="5" fill="{INK}" '
         f'fill-opacity="0.45" text-anchor="end">CITYEDIT.ORG</text>']
    add("99-closing", "Closing", "".join(b))


# ===========================================================================
title_panel()
set_a()
set_b()
set_c()
set_d()
closing_panel()

for name, _t, svg in panels:
    (OUT / f"{name}.svg").write_text(svg)
print(f"wrote {len(panels)} panels to {OUT}")

# ---------------------------------------------------------------------------
# Deck HTML — one 16:9 page per panel, for print-to-PDF
# ---------------------------------------------------------------------------
pages = "\n".join(
    f'<section><img src="{name}.svg" alt="{title}"></section>' for name, title, _ in panels
)
(OUT / "deck.html").write_text(f"""<!doctype html>
<meta charset="utf-8">
<title>City Edit — the story, in panels</title>
<style>
  @page {{ size: 16.667in 9.375in; margin: 0; }}
  html, body {{ margin: 0; padding: 0; background: {PAPER}; }}
  section {{ width: 1600px; height: 900px; page-break-after: always; overflow: hidden; }}
  section:last-child {{ page-break-after: auto; }}
  img {{ display: block; width: 1600px; height: 900px; }}
</style>
{pages}
""")
print("\n".join(n for n, _, _ in panels))
