# ==========================================================================
# Route-based top proposals
# ==========================================================================
# A *route* proposal is a high-vote CORRIDOR — a single simple path through the
# intersection graph (nodes = intersections, edges = street segments / "blocks")
# where one vote-type has strong net support along a contiguous run.
#
# Pipeline, run independently PER VOTE-TYPE (a bike-lane corridor and a tree
# corridor can ride the same street without competing):
#
#   1. build_type_graph  — the positive-net subgraph for one type, weighted by
#                          net (up − down) support.
#   2. leiden_communities — Leiden partitions it into dense corridors
#                          (deterministic for a fixed seed — proposals are
#                          broadcast to every client and must not vary by run).
#   3. heaviest_path     — within each community, the heaviest SIMPLE PATH (a
#                          route, never a branching tree). Exact for small
#                          communities, greedy beyond a node budget.
#   4. dedupe_routes     — collapse near-duplicate same-type routes by edge-set
#                          Jaccard; a mere crossing (low overlap) keeps both.
#
# Proposals are expressed in BLOCK units: each path edge expands to its full
# block (all edges sharing `edge_block_id`; see docs/vote-system-design.md §2.2),
# with an edge-as-singleton-block fallback where no block artifacts exist yet.
# Selecting/voting a proposal casts on `block_edge_ids` — every underlying edge
# of every block, not just the path's representative edge.
#
# `RouteProposalEngine` caches per-type results so a vote of type T recomputes
# only T (per-type independence) — keeping the dynamic re-cluster on each vote
# cheap. Pure logic only: no Flask/Redis/Postgres here.

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import igraph as ig
import leidenalg


# Default seed: stable across instances so every Flask worker (and thus every
# client) derives the same partition from the same votes.
DEFAULT_SEED = 1
# Below this many vertices a community gets EXACT heaviest-simple-path search;
# above it, the greedy extender (longest-path is NP-hard — exact only stays
# cheap on the small, dense communities Leiden typically yields).
EXACT_PATH_MAX_VERTICES = 12
DEFAULT_LIMIT = 20
DEFAULT_JACCARD = 0.5
MIN_NET = 1  # strictly positive net support required to be path-eligible

# Components at/above this size get Leiden-subdivided before path extraction —
# the "localized" part: it bounds the (super-linear) heaviest-path search to a
# community and keeps the per-vote recompute cheap. Small components (incl. every
# unit test) skip Leiden entirely, which also dodges modularity's habit of
# cutting clean corridors on tiny graphs.
LEIDEN_LOCALIZE_MIN_VERTICES = 60
# Within one component/community we PEEL successive heaviest paths so a region
# with two real corridors yields both. A peeled path is kept only while it scores
# at least this fraction of the component's strongest path — so a weak dead-end
# spur hanging off a hot corridor is dropped, not surfaced as its own route.
PEEL_DOMINANCE = 0.25
PEEL_MAX_PATHS = 8


@dataclass
class RouteProposal:
    label: str
    legend_idx: int
    score: int
    edge_ids: list[int]              # ordered path edges (geometry / anchors)
    blocks: list[list[int]]          # distinct blocks along the path, in order
    block_edge_ids: list[int]        # union of all block edges — the voting set
    anchors: tuple[int, int]         # terminal intersection node indices
    anchor_coords: tuple[tuple[float, float], tuple[float, float]]
    node_seq: list[int]              # the path's node sequence (anchors = ends)
    id: str = field(default="")

    def __post_init__(self):
        if not self.id:
            self.id = _proposal_id(self.legend_idx, self.edge_ids)

    def to_dict(self) -> dict:
        # Wire shape per docs/cluster-model.md §1.3. `kind` + `path_edge_ids` are
        # the shared-Cluster contract; `edge_ids` is kept as the path alias the
        # current client reads (parseRouteProposal).
        return {
            "id": self.id,
            "kind": "route",
            "label": self.label,
            "legendIdx": self.legend_idx,
            "score": self.score,
            "edge_ids": list(self.edge_ids),
            "path_edge_ids": list(self.edge_ids),
            "blocks": [list(b) for b in self.blocks],
            "block_edge_ids": list(self.block_edge_ids),
            "anchors": list(self.anchors),
            "anchor_coords": [list(self.anchor_coords[0]), list(self.anchor_coords[1])],
        }


def _proposal_id(legend_idx: int, edge_ids: list[int]) -> str:
    raw = f"{legend_idx}:" + ",".join(str(e) for e in sorted(edge_ids))
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


# --------------------------------------------------------------------------
# 1. Per-type vote graph
# --------------------------------------------------------------------------
def build_type_graph(edges, nets, min_net: int = MIN_NET) -> ig.Graph:
    """Subgraph of segments with net ≥ `min_net`, weighted by net support.

    `edges[i]` is the `(u, v)` node-index pair of edge id `i`; `nets[i]` is that
    edge's net (up − down) for ONE vote-type. Vertices are only the node indices
    that actually appear (kept contiguous for igraph); the original node index is
    stored as the vertex `name` and the original edge id as the edge `eid`.
    """
    kept = [(i, u, v) for i, (u, v) in enumerate(edges)
            if i < len(nets) and nets[i] >= min_net and u != v]
    nodes = sorted({u for _, u, _ in kept} | {v for _, _, v in kept})
    idx = {n: k for k, n in enumerate(nodes)}

    g = ig.Graph()
    g.add_vertices(len(nodes))
    if nodes:
        g.vs["name"] = nodes
    g.add_edges([(idx[u], idx[v]) for _, u, v in kept])
    g.es["eid"] = [i for i, _, _ in kept]
    g.es["weight"] = [int(nets[i]) for i, _, _ in kept]
    return g


# --------------------------------------------------------------------------
# 2. Leiden communities
# --------------------------------------------------------------------------
def leiden_communities(g: ig.Graph, seed: int = DEFAULT_SEED) -> list[list[int]]:
    """Partition `g` into communities (lists of igraph vertex ids).

    Deterministic for a fixed `seed`. Singletons (isolated vertices) are dropped —
    they can't host a path. Uses modularity so the partition is parameter-free.
    """
    if g.ecount() == 0:
        return []
    part = leidenalg.find_partition(
        g, leidenalg.ModularityVertexPartition, weights="weight", seed=seed,
    )
    return [list(c) for c in part if len(c) >= 2]


# --------------------------------------------------------------------------
# 3. Heaviest simple path within a community
# --------------------------------------------------------------------------
def _adj_of(g: ig.Graph) -> dict[int, list[tuple[int, int, int]]]:
    """node-name → list of (neighbor-name, eid, weight). Undirected (both ways)."""
    adj: dict[int, list[tuple[int, int, int]]] = {}
    for v in g.vs:
        adj.setdefault(v["name"], [])
    for e in g.es:
        a = g.vs[e.source]["name"]
        b = g.vs[e.target]["name"]
        adj.setdefault(a, []).append((b, e["eid"], e["weight"]))
        adj.setdefault(b, []).append((a, e["eid"], e["weight"]))
    return adj


def heaviest_path(g: ig.Graph, vertices: list[int] | None = None):
    """Return `(ordered_edge_ids, node_seq)` for the heaviest simple path.

    `ordered_edge_ids` are ORIGINAL edge ids (the `eid` attr); `node_seq` are
    ORIGINAL node indices (the `name` attr). Exact search under
    `EXACT_PATH_MAX_VERTICES`, greedy two-way extension above it.
    """
    sub = g if vertices is None else g.subgraph(vertices)
    edges, nodes, _w = _heaviest_path_from_adj(_adj_of(sub))
    return edges, nodes


def _heaviest_path_from_adj(adj):
    """Heaviest simple path over an adjacency map → (edges, nodes, weight)."""
    n_edges = sum(len(v) for v in adj.values()) // 2
    if n_edges == 0:
        return [], [], 0
    if len(adj) <= EXACT_PATH_MAX_VERTICES:
        return _exact_heaviest_path(adj)
    return _greedy_heaviest_path(adj)


def _exact_heaviest_path(adj):
    best = {"w": -1, "nodes": [], "edges": []}

    def dfs(node, seen_nodes, nodes, edges, weight):
        if weight > best["w"]:
            best.update(w=weight, nodes=list(nodes), edges=list(edges))
        for nxt, eid, ew in adj.get(node, ()):
            if nxt in seen_nodes:
                continue
            seen_nodes.add(nxt); nodes.append(nxt); edges.append(eid)
            dfs(nxt, seen_nodes, nodes, edges, weight + ew)
            seen_nodes.discard(nxt); nodes.pop(); edges.pop()

    for start in adj:
        dfs(start, {start}, [start], [], 0)
    return best["edges"], best["nodes"], max(best["w"], 0)


def _greedy_heaviest_path(adj):
    """Seed from each endpoint of the heaviest edge, extend greedily both ways,
    keep the heaviest path found. Cheap and path-valued for large communities."""
    # Heaviest edge as the seed.
    seed_a, seed_eid, seed_w, seed_b = None, None, -1, None
    for a, nbrs in adj.items():
        for b, eid, w in nbrs:
            if w > seed_w:
                seed_a, seed_b, seed_eid, seed_w = a, b, eid, w

    if seed_a is None:
        return [], [], 0

    def extend(frm, to, first_eid):
        nodes = [frm, to]
        edges = [first_eid]
        seen = {frm, to}
        weight = adj_weight(frm, to)
        cur = to
        while True:
            nxt = max(
                (cand for cand in adj.get(cur, ()) if cand[0] not in seen),
                key=lambda c: c[2], default=None,
            )
            if nxt is None:
                break
            seen.add(nxt[0]); nodes.append(nxt[0]); edges.append(nxt[1]); cur = nxt[0]
        return nodes, edges

    def adj_weight(a, b):
        for nb, _eid, w in adj.get(a, ()):
            if nb == b:
                return w
        return 0

    # Extend from b away from a, and from a away from b, then splice into one path.
    fwd_nodes, fwd_edges = extend(seed_a, seed_b, seed_eid)
    bwd_nodes, bwd_edges = extend(seed_b, seed_a, seed_eid)
    # bwd starts at seed_b going to seed_a then onward; reverse it and drop the
    # shared seed edge so the two halves join at seed_a→seed_b once.
    bwd_nodes = list(reversed(bwd_nodes))       # … → seed_a, seed_b? no: ends at seed_a side
    # Reconstruct: bwd path is [seed_b, seed_a, x, y…]; reversed → [… y, x, seed_a, seed_b]
    # fwd path is [seed_a, seed_b, p, q…]. Join on the seed_a→seed_b edge.
    left_nodes = bwd_nodes[:-1]                 # … , seed_a  (drop trailing seed_b)
    left_edges = list(reversed(bwd_edges[1:]))  # edges past the seed, reversed
    nodes = left_nodes + fwd_nodes              # …→seed_a→seed_b→…
    edges = left_edges + fwd_edges
    weight = _path_weight(edges, adj)
    return edges, nodes, weight


def _path_weight(edge_ids, adj) -> int:
    seen, total = set(), 0
    for nbrs in adj.values():
        for _b, eid, w in nbrs:
            if eid in edge_ids and eid not in seen:
                seen.add(eid); total += w
    return total


# --------------------------------------------------------------------------
# Localize (components, Leiden-subdivided when large) + heaviest-path peeling
# --------------------------------------------------------------------------
def _localize(g: ig.Graph, seed: int):
    """Yield one adjacency map per extraction unit: each connected component,
    subdivided by Leiden only when it's large enough to need localizing."""
    for comp in g.connected_components():
        if len(comp) < 2:
            continue
        sub = g.subgraph(comp)
        if sub.vcount() >= LEIDEN_LOCALIZE_MIN_VERTICES:
            for community in leiden_communities(sub, seed=seed):
                csub = sub.subgraph(community)
                if csub.ecount():
                    yield _adj_of(csub)
        else:
            yield _adj_of(sub)


def _remove_edges(adj, eids: set[int]):
    """Adjacency with `eids` removed (drops now-isolated nodes)."""
    out: dict[int, list[tuple[int, int, int]]] = {}
    for a, nbrs in adj.items():
        kept = [(b, eid, w) for (b, eid, w) in nbrs if eid not in eids]
        if kept:
            out[a] = kept
    return out


def peel_paths(adj, dominance: float = PEEL_DOMINANCE, max_paths: int = PEEL_MAX_PATHS):
    """Successively pull the heaviest simple path, removing its edges each round.

    Stops when a path scores below `dominance × first_path_score` — so a region's
    real corridors surface while weak dead-end residue (e.g. a spur off a hot
    avenue) is dropped. Returns `[(edge_ids, node_seq, weight), …]`."""
    paths, first = [], None
    work = adj
    while work and len(paths) < max_paths:
        edges, nodes, weight = _heaviest_path_from_adj(work)
        if not edges:
            break
        if first is None:
            first = weight
        elif weight < dominance * first:
            break
        paths.append((edges, nodes, weight))
        work = _remove_edges(work, set(edges))
    return paths


# --------------------------------------------------------------------------
# Block grouping (docs/vote-system-design.md §2.2)
# --------------------------------------------------------------------------
def _block_of(edge_id: int, edge_block_id) -> int | None:
    if edge_block_id is None:
        return None
    if 0 <= edge_id < len(edge_block_id):
        b = edge_block_id[edge_id]
        return None if b is None or b < 0 else int(b)
    return None


def group_blocks(edge_ids, edge_block_id):
    """Expand an ordered path into its distinct blocks (in path order).

    A block = all edges sharing the same `edge_block_id`. Edges with no block
    (`-1`/absent) are their own singleton block (edge-layer fallback). Returns
    `(blocks, block_edge_ids)` where `block_edge_ids` is the de-duplicated union
    of every edge in every block — the voting set.
    """
    # block_id → all edges in that block (across the whole map, not just the path).
    block_members: dict[int, list[int]] = {}
    if edge_block_id is not None:
        for eid, bid in enumerate(edge_block_id):
            if bid is not None and bid >= 0:
                block_members.setdefault(int(bid), []).append(eid)

    blocks: list[list[int]] = []
    seen_block_ids: set[int] = set()
    union: list[int] = []
    union_seen: set[int] = set()

    for eid in edge_ids:
        bid = _block_of(eid, edge_block_id)
        if bid is None:
            members = [eid]                       # singleton fallback
            key = ("e", eid)
        else:
            members = sorted(block_members.get(bid, [eid]))
            key = ("b", bid)
        if key in seen_block_ids:
            continue
        seen_block_ids.add(key)
        blocks.append(members)
        for m in members:
            if m not in union_seen:
                union_seen.add(m); union.append(m)
    return blocks, union


# --------------------------------------------------------------------------
# 4. Criss-cross dedup
# --------------------------------------------------------------------------
def dedupe_routes(proposals: list[RouteProposal], jaccard_threshold: float = DEFAULT_JACCARD):
    """Drop near-duplicate SAME-TYPE routes, keeping the higher score.

    Overlap is edge-set Jaccard `|A∩B| / |A∪B|`; a subset also counts as a
    duplicate (containment ratio `|A∩B| / min(|A|,|B|)` == 1). Different vote
    types never suppress each other. Input order is irrelevant — we sort by score.
    """
    ordered = sorted(proposals, key=lambda p: p.score, reverse=True)
    kept: list[RouteProposal] = []
    kept_sets: list[tuple[int, set[int]]] = []
    for p in ordered:
        s = set(p.edge_ids)
        dup = False
        for legend_idx, ks in kept_sets:
            if legend_idx != p.legend_idx:
                continue
            inter = len(s & ks)
            if not inter:
                continue
            jaccard = inter / len(s | ks)
            containment = inter / min(len(s), len(ks))
            if jaccard >= jaccard_threshold or containment >= 0.999:
                dup = True
                break
        if not dup:
            kept.append(p)
            kept_sets.append((p.legend_idx, s))
    return kept


# --------------------------------------------------------------------------
# Coverage predicate (auto-select)
# --------------------------------------------------------------------------
def is_route_covered(blocks, selected_edges) -> bool:
    """True iff the selection covers EVERY block (≥1 of each block's edges).

    The block grain matches the display dedup grain: a route auto-selects only
    once the current route touches each of its blocks.
    """
    sel = set(selected_edges)
    return bool(blocks) and all(any(e in sel for e in block) for block in blocks)


# --------------------------------------------------------------------------
# Type → proposals, and the full multi-type extractor
# --------------------------------------------------------------------------
def _nets_for_type(edge_vote_types, n_edges, legend_idx) -> list[int]:
    nets = [0] * n_edges
    for eid in range(n_edges):
        pairs = edge_vote_types[eid] if eid < len(edge_vote_types) else None
        if not pairs:
            continue
        for t, up, dn in pairs:
            if t == legend_idx:
                nets[eid] += up - dn
    return nets


def _proposals_for_type(
    *, edges, node_coords, nets, legend_idx, label, edge_block_id, seed,
) -> list[RouteProposal]:
    g = build_type_graph(edges, nets, min_net=MIN_NET)
    out: list[RouteProposal] = []
    for unit_adj in _localize(g, seed):
        for edge_ids, node_seq, score in peel_paths(unit_adj):
            if not edge_ids or score < MIN_NET:
                continue
            blocks, block_edge_ids = group_blocks(edge_ids, edge_block_id)
            a, b = node_seq[0], node_seq[-1]
            out.append(RouteProposal(
                label=label, legend_idx=legend_idx, score=score,
                edge_ids=edge_ids, blocks=blocks, block_edge_ids=block_edge_ids,
                anchors=(a, b),
                anchor_coords=(tuple(node_coords[a]), tuple(node_coords[b])),
                node_seq=node_seq,
            ))
    return dedupe_routes(out)


def compute_route_proposals(
    *, edges, node_coords, edge_vote_types, legend,
    edge_block_id=None, limit: int = DEFAULT_LIMIT,
    jaccard_threshold: float = DEFAULT_JACCARD, seed: int = DEFAULT_SEED,
) -> list[RouteProposal]:
    """Full (all-types) route-proposal extraction. See module docstring."""
    eng = RouteProposalEngine(
        edges=edges, node_coords=node_coords, edge_block_id=edge_block_id,
        limit=limit, jaccard_threshold=jaccard_threshold, seed=seed,
    )
    return eng.recompute(edge_vote_types, legend)


class RouteProposalEngine:
    """Per-type-cached extractor: a vote of type T recomputes only T.

    Holds the per-type proposal lists (pre global cap). `recompute(..., changed_types=[T])`
    rebuilds only those types and reuses cached others, then re-applies the global
    rank+cap — equal to a from-scratch full recompute because types are independent.
    """

    def __init__(self, *, edges, node_coords, edge_block_id=None,
                 limit: int = DEFAULT_LIMIT, jaccard_threshold: float = DEFAULT_JACCARD,
                 seed: int = DEFAULT_SEED):
        self.edges = edges
        self.node_coords = node_coords
        self.edge_block_id = edge_block_id
        self.limit = limit
        self.jaccard_threshold = jaccard_threshold
        self.seed = seed
        self._by_type: dict[int, list[RouteProposal]] = {}

    def recompute(self, edge_vote_types, legend, changed_types=None) -> list[RouteProposal]:
        n_edges = len(self.edges)
        types = range(len(legend)) if changed_types is None else changed_types
        for legend_idx in types:
            label = legend[legend_idx]
            if not label:
                self._by_type[legend_idx] = []
                continue
            nets = _nets_for_type(edge_vote_types, n_edges, legend_idx)
            self._by_type[legend_idx] = _proposals_for_type(
                edges=self.edges, node_coords=self.node_coords, nets=nets,
                legend_idx=legend_idx, label=label, edge_block_id=self.edge_block_id,
                seed=self.seed,
            )
        return self._assemble()

    def _assemble(self) -> list[RouteProposal]:
        flat = [p for ps in self._by_type.values() for p in ps]
        flat.sort(key=lambda p: p.score, reverse=True)
        return flat[: self.limit]
