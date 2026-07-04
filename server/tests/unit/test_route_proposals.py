"""Route-based top proposals: per-type Leiden clustering → heaviest simple path.

These exercise the pure clustering/route-extraction logic in `route_proposals`
without Flask/Redis/Postgres. The substrate is the intersection graph (nodes =
intersections, edges = street segments / "blocks"), weighted per vote-type by net
support. A proposal is a single SIMPLE PATH (a route, never a branching tree),
expressed in BLOCK units (edges grouped by `edge_block_id`, with an
edge-as-singleton-block fallback where no block artifacts exist yet).

See docs/vote-system-design.md §2 for the block grain this mirrors.
"""
import route_proposals as R

# Vote-type legend reused across tests. Index = legendIdx.
BIKE, TREE = 0, 1
LEGEND = ["Bike lane", "Tree"]


# --------------------------------------------------------------------------
# Helpers — build the per-edge vote-type breakdown the server already ships
# (arrays["edge_vote_types"]: per edge, a list of [legendIdx, up, down]).
# --------------------------------------------------------------------------
def evt(*per_edge):
    """`evt([(BIKE, 5, 1)], [(TREE, 3, 0)], ...)` → edge_vote_types list."""
    return [[[t, up, dn] for (t, up, dn) in pairs] for pairs in per_edge]


def line_coords(n):
    """n nodes strung west→east at a fixed latitude (coords only matter for anchors)."""
    return [(40.70, -74.00 + 0.001 * i) for i in range(n)]


def proposals(edges, edge_vote_types, *, coords=None, legend=LEGEND, **kw):
    n_nodes = 1 + max(max(u, v) for u, v in edges)
    return R.compute_route_proposals(
        edges=edges,
        node_coords=coords or line_coords(n_nodes),
        edge_vote_types=edge_vote_types,
        legend=legend,
        **kw,
    )


def node_seq_of(p):
    """Reconstruct the node sequence a proposal's ordered edge_ids walk."""
    return p.node_seq


# ==========================================================================
# Graph build
# ==========================================================================
def test_vote_graph_weights_are_net_support():
    edges = [(0, 1), (1, 2)]
    nets = [4, 2]  # net up-down per edge
    g = R.build_type_graph(edges, nets, min_net=1)
    weights = {tuple(sorted((g.vs[e.source]["name"], g.vs[e.target]["name"]))): e["weight"]
               for e in g.es}
    assert weights == {(0, 1): 4, (1, 2): 2}


def test_nonpositive_net_edges_excluded():
    edges = [(0, 1), (1, 2), (2, 3)]
    nets = [5, 0, -3]  # only edge 0 qualifies
    g = R.build_type_graph(edges, nets, min_net=1)
    assert g.ecount() == 1
    assert set(g.es["eid"]) == {0}


def test_isolated_and_empty_graph_do_not_crash():
    # No positive-net edges → no communities, no proposals, no exception.
    assert proposals([(0, 1)], evt([(BIKE, 0, 0)])) == []


# ==========================================================================
# Leiden clustering
# ==========================================================================
def test_leiden_is_deterministic_with_seed():
    # A 4x4 grid with random-ish weights; the partition must be identical across
    # runs (proposals are broadcast to every client — they cannot differ by run).
    edges, nets = [], []
    for r in range(4):
        for c in range(3):
            edges.append((r * 4 + c, r * 4 + c + 1)); nets.append(1 + (r + c) % 3)
    for r in range(3):
        for c in range(4):
            edges.append((r * 4 + c, (r + 1) * 4 + c)); nets.append(1 + (r * c) % 3)
    g = R.build_type_graph(edges, nets, min_net=1)
    a = R.leiden_communities(g, seed=7)
    b = R.leiden_communities(g, seed=7)
    assert [sorted(c) for c in a] == [sorted(c) for c in b]


def test_dense_corridor_clusters_together():
    # A hot 4-node corridor + a cold perpendicular spur. The corridor edges land
    # in one community; the weak spur is not absorbed into the corridor's path.
    edges = [(0, 1), (1, 2), (2, 3), (1, 9)]
    ps = proposals(edges, evt(
        [(BIKE, 20, 0)], [(BIKE, 22, 0)], [(BIKE, 19, 0)], [(BIKE, 1, 0)]))
    assert len(ps) == 1
    assert set(ps[0].edge_ids) == {0, 1, 2}  # the spur (edge 3) is excluded


# ==========================================================================
# Heaviest simple path (a route, never a tree)
# ==========================================================================
def test_path_is_simple_and_contiguous():
    edges = [(0, 1), (1, 2), (2, 3)]
    p = proposals(edges, evt([(BIKE, 9, 0)], [(BIKE, 8, 0)], [(BIKE, 7, 0)]))[0]
    seq = node_seq_of(p)
    assert len(set(seq)) == len(seq)                       # no repeated node
    for a, b in zip(seq, seq[1:]):                          # each step is a real edge
        assert (a, b) in {(0, 1), (1, 2), (2, 3)} or (b, a) in {(0, 1), (1, 2), (2, 3)}


def test_path_is_not_a_tree():
    # Y junction: 0-1-2 then 2 forks to 3 and to 4. The route must pick ONE arm,
    # so node 2 can have at most degree 2 within the chosen path.
    edges = [(0, 1), (1, 2), (2, 3), (2, 4)]
    p = proposals(edges, evt(
        [(BIKE, 10, 0)], [(BIKE, 10, 0)], [(BIKE, 10, 0)], [(BIKE, 10, 0)]))[0]
    seq = node_seq_of(p)
    assert len(set(seq)) == len(seq)
    # node 2 borders 3 path-eligible edges but the path uses at most 2 of them.
    used_at_2 = sum(1 for a, b in zip(seq, seq[1:]) if 2 in (a, b))
    assert used_at_2 <= 2


def test_path_matches_bruteforce_on_tiny_graph():
    # On a small community, the extractor must return the GLOBALLY heaviest
    # simple path (exact), not just a greedy one.
    edges = [(0, 1), (1, 2), (2, 3), (0, 2)]
    nets = [3, 3, 10, 1]
    g = R.build_type_graph(edges, nets, min_net=1)
    eids, _seq = R.heaviest_path(g)
    got = sum(nets[e] for e in eids)
    # brute force: heaviest simple path over the same edges
    best = _bruteforce_heaviest(edges, nets)
    assert got == best


def _bruteforce_heaviest(edges, nets):
    adj = {}
    for i, (u, v) in enumerate(edges):
        if nets[i] <= 0:
            continue
        adj.setdefault(u, []).append((v, i)); adj.setdefault(v, []).append((u, i))
    best = 0
    def dfs(node, seen_nodes, used, weight):
        nonlocal best
        best = max(best, weight)
        for nxt, eid in adj.get(node, ()):
            if nxt in seen_nodes:
                continue
            dfs(nxt, seen_nodes | {nxt}, used | {eid}, weight + nets[eid])
    for start in adj:
        dfs(start, {start}, set(), 0)
    return best


def test_single_edge_community_returns_that_edge():
    p = proposals([(0, 1)], evt([(BIKE, 5, 0)]))[0]
    assert p.edge_ids == [0]
    assert node_seq_of(p) == [0, 1]


def test_cycle_community_returns_open_path():
    # A triangle (cycle). The route may use 2 of the 3 edges but never repeats a node.
    edges = [(0, 1), (1, 2), (2, 0)]
    p = proposals(edges, evt([(BIKE, 5, 0)], [(BIKE, 5, 0)], [(BIKE, 5, 0)]))[0]
    seq = node_seq_of(p)
    assert len(set(seq)) == len(seq)


# ==========================================================================
# Per-type selection / scoring / labeling
# ==========================================================================
def test_route_is_single_type_and_labeled_by_it():
    # Two disjoint corridors, one Bike one Tree. Each proposal carries its own type.
    edges = [(0, 1), (1, 2), (10, 11), (11, 12)]
    ps = proposals(edges, evt(
        [(BIKE, 9, 0)], [(BIKE, 8, 0)], [(TREE, 7, 0)], [(TREE, 6, 0)]))
    by_label = {p.label: p for p in ps}
    assert set(by_label) == {"Bike lane", "Tree"}
    assert set(by_label["Bike lane"].edge_ids) == {0, 1}
    assert set(by_label["Tree"].edge_ids) == {2, 3}
    assert by_label["Bike lane"].legend_idx == BIKE


def test_only_positive_net_routes_proposed():
    # A net-downvoted corridor yields no proposal.
    edges = [(0, 1), (1, 2)]
    assert proposals(edges, evt([(BIKE, 1, 5)], [(BIKE, 0, 4)])) == []


def test_proposals_capped_and_ranked_by_score():
    # Three disjoint Bike corridors of descending strength; limit=2 keeps the top 2.
    edges = [(0, 1), (2, 3), (4, 5)]
    ps = proposals(edges, evt(
        [(BIKE, 30, 0)], [(BIKE, 20, 0)], [(BIKE, 10, 0)]), limit=2)
    assert [p.score for p in ps] == [30, 20]


# ==========================================================================
# Criss-cross / dedup
# ==========================================================================
def test_high_overlap_same_type_collapse_to_stronger():
    # Two Bike paths sharing most of their edges → keep the stronger, drop the other.
    # (Constructed so clustering would otherwise surface both.)
    edges = [(0, 1), (1, 2), (2, 3)]
    a = R.RouteProposal(label="Bike lane", legend_idx=BIKE, score=30,
                        edge_ids=[0, 1, 2], blocks=[[0], [1], [2]],
                        block_edge_ids=[0, 1, 2], anchors=(0, 3),
                        anchor_coords=((0, 0), (0, 0)), node_seq=[0, 1, 2, 3])
    b = R.RouteProposal(label="Bike lane", legend_idx=BIKE, score=10,
                        edge_ids=[0, 1], blocks=[[0], [1]],
                        block_edge_ids=[0, 1], anchors=(0, 2),
                        anchor_coords=((0, 0), (0, 0)), node_seq=[0, 1, 2])
    kept = R.dedupe_routes([a, b], jaccard_threshold=0.5)
    assert [p.score for p in kept] == [30]


def test_crossing_routes_both_kept():
    # Two same-type routes that only share a single intersection (low overlap)
    # both survive — a crossing is not a duplicate.
    a = R.RouteProposal(label="Bike lane", legend_idx=BIKE, score=20,
                        edge_ids=[0, 1], blocks=[[0], [1]], block_edge_ids=[0, 1],
                        anchors=(0, 2), anchor_coords=((0, 0), (0, 0)), node_seq=[0, 1, 2])
    b = R.RouteProposal(label="Bike lane", legend_idx=BIKE, score=18,
                        edge_ids=[2, 3], blocks=[[2], [3]], block_edge_ids=[2, 3],
                        anchors=(5, 7), anchor_coords=((0, 0), (0, 0)), node_seq=[5, 1, 7])
    kept = R.dedupe_routes([a, b], jaccard_threshold=0.5)
    assert len(kept) == 2


def test_subset_route_dropped():
    a = R.RouteProposal(label="Bike lane", legend_idx=BIKE, score=30,
                        edge_ids=[0, 1, 2, 3], blocks=[[0], [1], [2], [3]],
                        block_edge_ids=[0, 1, 2, 3], anchors=(0, 4),
                        anchor_coords=((0, 0), (0, 0)), node_seq=[0, 1, 2, 3, 4])
    b = R.RouteProposal(label="Bike lane", legend_idx=BIKE, score=12,
                        edge_ids=[1, 2], blocks=[[1], [2]], block_edge_ids=[1, 2],
                        anchors=(1, 3), anchor_coords=((0, 0), (0, 0)), node_seq=[1, 2, 3])
    kept = R.dedupe_routes([a, b], jaccard_threshold=0.5)
    assert [p.edge_ids for p in kept] == [[0, 1, 2, 3]]


def test_different_types_never_suppress_each_other():
    # Same edges, different vote types → both kept (a bike corridor and a tree
    # corridor can ride the same street).
    a = R.RouteProposal(label="Bike lane", legend_idx=BIKE, score=20,
                        edge_ids=[0, 1], blocks=[[0], [1]], block_edge_ids=[0, 1],
                        anchors=(0, 2), anchor_coords=((0, 0), (0, 0)), node_seq=[0, 1, 2])
    b = R.RouteProposal(label="Tree", legend_idx=TREE, score=18,
                        edge_ids=[0, 1], blocks=[[0], [1]], block_edge_ids=[0, 1],
                        anchors=(0, 2), anchor_coords=((0, 0), (0, 0)), node_seq=[0, 1, 2])
    kept = R.dedupe_routes([a, b], jaccard_threshold=0.5)
    assert len(kept) == 2


# ==========================================================================
# Anchors
# ==========================================================================
def test_anchors_are_path_terminals():
    edges = [(0, 1), (1, 2), (2, 3)]
    p = proposals(edges, evt([(BIKE, 9, 0)], [(BIKE, 8, 0)], [(BIKE, 7, 0)]))[0]
    assert set(p.anchors) == {node_seq_of(p)[0], node_seq_of(p)[-1]}


def test_anchor_coords_match_terminal_nodes():
    edges = [(0, 1), (1, 2)]
    coords = [(40.0, -74.0), (40.1, -74.1), (40.2, -74.2)]
    p = proposals(edges, evt([(BIKE, 9, 0)], [(BIKE, 8, 0)]), coords=coords)[0]
    a0, a1 = p.anchors
    assert p.anchor_coords[0] == tuple(coords[a0])
    assert p.anchor_coords[1] == tuple(coords[a1])


# ==========================================================================
# Block grain (docs/vote-system-design.md §2.2) + the voting union
# ==========================================================================
def test_blocks_grouped_by_edge_block_id():
    # Edges 0 and 1 are the two directions of ONE street segment → one block.
    edges = [(0, 1), (1, 0), (1, 2)]
    edge_block_id = [100, 100, 101]
    p = proposals(edges, evt(
        [(BIKE, 9, 0)], [(BIKE, 9, 0)], [(BIKE, 8, 0)]),
        edge_block_id=edge_block_id)[0]
    # Each block appears once; the shared-segment block carries both its edges.
    block_sets = [set(b) for b in p.blocks]
    assert {0, 1} in block_sets
    assert {2} in block_sets
    assert len(p.blocks) == 2


def test_edge_singleton_block_fallback():
    # No block artifacts → each edge is its own block (edge-layer fallback).
    edges = [(0, 1), (1, 2)]
    p = proposals(edges, evt([(BIKE, 9, 0)], [(BIKE, 8, 0)]))[0]
    assert sorted(p.blocks) == [[0], [1]]


def test_proposal_edge_ids_span_all_block_edges():
    # The voting union must include EVERY edge in every block (we cast on all
    # underlying edges of the block, not just the path's representative edge).
    edges = [(0, 1), (1, 0), (1, 2)]
    edge_block_id = [100, 100, 101]
    p = proposals(edges, evt(
        [(BIKE, 9, 0)], [(BIKE, 9, 0)], [(BIKE, 8, 0)]),
        edge_block_id=edge_block_id)[0]
    assert set(p.block_edge_ids) == {0, 1, 2}


# ==========================================================================
# Incremental == full recompute  (per-type independence)
# ==========================================================================
def _engine(edges, edge_block_id=None):
    n_nodes = 1 + max(max(u, v) for u, v in edges)
    return R.RouteProposalEngine(edges=edges, node_coords=line_coords(n_nodes),
                                 edge_block_id=edge_block_id)


def test_incremental_equals_full_recompute():
    edges = [(0, 1), (1, 2), (10, 11), (11, 12)]
    v1 = evt([(BIKE, 9, 0)], [(BIKE, 8, 0)], [(TREE, 7, 0)], [(TREE, 6, 0)])
    v2 = evt([(BIKE, 30, 0)], [(BIKE, 28, 0)], [(TREE, 7, 0)], [(TREE, 6, 0)])  # Bike grew

    eng = _engine(edges)
    eng.recompute(v1, LEGEND)
    incr = eng.recompute(v2, LEGEND, changed_types=[BIKE])

    fresh = _engine(edges)
    full = fresh.recompute(v2, LEGEND)

    key = lambda ps: sorted((p.label, tuple(p.edge_ids), p.score) for p in ps)
    assert key(incr) == key(full)


def test_vote_of_one_type_leaves_other_types_identical():
    edges = [(0, 1), (1, 2), (10, 11), (11, 12)]
    v1 = evt([(BIKE, 9, 0)], [(BIKE, 8, 0)], [(TREE, 7, 0)], [(TREE, 6, 0)])
    v2 = evt([(BIKE, 30, 0)], [(BIKE, 28, 0)], [(TREE, 7, 0)], [(TREE, 6, 0)])

    eng = _engine(edges)
    before = {p.label: tuple(p.edge_ids) for p in eng.recompute(v1, LEGEND)}
    after = {p.label: tuple(p.edge_ids) for p in eng.recompute(v2, LEGEND, changed_types=[BIKE])}
    assert after["Tree"] == before["Tree"]  # Tree proposal untouched by a Bike vote


# ==========================================================================
# Auto-select coverage predicate (block-grained)
# ==========================================================================
def test_to_dict_wire_contract():
    # The endpoint serializes proposals via to_dict(); pin the JSON keys/shape.
    edges = [(0, 1), (1, 2)]
    p = proposals(edges, evt([(BIKE, 9, 0)], [(BIKE, 8, 0)]))[0]
    d = p.to_dict()
    assert set(d) == {
        "id", "kind", "label", "legendIdx", "score", "edge_ids", "path_edge_ids",
        "blocks", "block_edge_ids", "anchors", "anchor_coords",
    }
    assert d["kind"] == "route"
    assert d["path_edge_ids"] == d["edge_ids"]
    assert d["label"] == "Bike lane"
    assert len(d["anchors"]) == 2
    assert len(d["anchor_coords"]) == 2 and len(d["anchor_coords"][0]) == 2


def test_is_route_covered_requires_every_block():
    blocks = [[0, 1], [2], [3]]  # 3 blocks; first has two edges
    assert R.is_route_covered(blocks, selected_edges={1, 2, 3}) is True   # one per block
    assert R.is_route_covered(blocks, selected_edges={0, 2}) is False     # block [3] uncovered
    assert R.is_route_covered(blocks, selected_edges=set()) is False
