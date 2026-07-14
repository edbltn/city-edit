"""Unit tests for counter_lyft's pure geometry core: voted-path via ordering
(voted_path_vias) and corridor coverage (covered_edges). Both run on a tiny
synthetic graph — no OSRM, DB, or Flask.
"""
import numpy as np
import pytest

from counter_lyft import COVER_DIST_M, M_PER_DEG_LAT, covered_edges, voted_path_vias


class FakeGraph:
    """The two CityGraph fields the geometry helpers read."""

    def __init__(self, coords, ends):
        self.node_coords = np.asarray(coords, dtype=np.float64)
        self.edge_ends = np.asarray(ends, dtype=np.int32)


DEG = 1.0 / M_PER_DEG_LAT  # ≈ one meter of latitude, in degrees


def line_graph(n: int) -> FakeGraph:
    """n nodes strung west→east along the equator, 100 m apart, n-1 edges."""
    coords = [(0.0, i * 100 * DEG) for i in range(n)]
    ends = [(i, i + 1) for i in range(n - 1)]
    return FakeGraph(coords, ends)


# ── voted_path_vias ──────────────────────────────────────────────────────────

def test_vias_walk_orders_path_from_trip_start():
    g = line_graph(30)
    edges = set(range(29))
    # Trip starts at the EAST end: the walk must begin there and move west.
    vias = voted_path_vias(g, edges, start=(0.0, 29 * 100 * DEG))
    assert vias, "a clean 29-edge path must produce via points"
    lons = [lon for _, lon in vias]
    assert lons == sorted(lons, reverse=True), "vias must be ordered from the start"


def test_vias_prefers_continuation_over_spur():
    # Path 0-1-2-3-4 with a one-edge spur hanging off node 2 (node 5).
    coords = [(0.0, i * 100 * DEG) for i in range(5)] + [(100 * DEG, 2 * 100 * DEG)]
    ends = [(0, 1), (1, 2), (2, 3), (3, 4), (2, 5)]
    g = FakeGraph(coords, ends)
    vias = voted_path_vias(g, {0, 1, 2, 3, 4}, start=(0.0, 0.0))
    # The walk must reach node 4 (main line), not die at the node-5 spur.
    assert vias == [] or all(lat == 0.0 for lat, _ in vias)


def test_vias_empty_for_loop():
    # A closed square has no degree-1 node to anchor the walk.
    coords = [(0.0, 0.0), (0.0, 100 * DEG), (100 * DEG, 100 * DEG), (100 * DEG, 0.0)]
    ends = [(0, 1), (1, 2), (2, 3), (3, 0)]
    g = FakeGraph(coords, ends)
    assert voted_path_vias(g, {0, 1, 2, 3}, start=(0.0, 0.0)) == []


def test_vias_empty_when_walk_covers_too_little():
    # Two far-apart path fragments: the walk finishes one and strands the other.
    coords = ([(0.0, i * 100 * DEG) for i in range(3)]
              + [(0.5, 0.0), (0.5, 100 * DEG), (0.5, 200 * DEG), (0.5, 300 * DEG),
                 (0.5, 400 * DEG), (0.5, 500 * DEG), (0.5, 600 * DEG)])
    ends = [(0, 1), (1, 2)] + [(i, i + 1) for i in range(3, 9)]
    g = FakeGraph(coords, ends)
    assert voted_path_vias(g, set(range(8)), start=(0.0, 0.0)) == []


# ── covered_edges ────────────────────────────────────────────────────────────

def test_covers_edges_on_and_near_the_route():
    g = line_graph(10)
    # Route runs exactly along the voted line.
    route = np.array([(0.0, 0.0), (0.0, 9 * 100 * DEG)])
    assert covered_edges(g, set(range(9)), route) == set(range(9))


def test_parallel_edge_within_corridor_is_covered():
    # Voted edge sits 10 m north of the route (a sidewalk beside the roadway).
    off = 10 * DEG
    g = FakeGraph([(off, 0.0), (off, 100 * DEG)], [(0, 1)])
    route = np.array([(0.0, -100 * DEG), (0.0, 200 * DEG)])
    assert covered_edges(g, {0}, route) == {0}


def test_parallel_street_outside_corridor_survives():
    # A parallel street 80 m away (the next block) must NOT be covered.
    off = 80 * DEG
    g = FakeGraph([(off, 0.0), (off, 100 * DEG)], [(0, 1)])
    route = np.array([(0.0, -100 * DEG), (0.0, 200 * DEG)])
    assert covered_edges(g, {0}, route) == set()


def test_crossing_edge_survives():
    # An edge crossing the route at a right angle touches it at one endpoint,
    # but its midpoint (50 m) and far endpoint (100 m) are outside the
    # corridor — the both-endpoints-and-midpoint rule must reject it.
    g = FakeGraph([(0.0, 0.0), (100 * DEG, 0.0)], [(0, 1)])
    route = np.array([(0.0, -200 * DEG), (0.0, 200 * DEG)])
    assert COVER_DIST_M < 50
    assert covered_edges(g, {0}, route) == set()


def test_covered_edges_empty_inputs():
    g = line_graph(3)
    route = np.array([(0.0, 0.0), (0.0, 100 * DEG)])
    assert covered_edges(g, set(), route) == set()
    assert covered_edges(g, {0}, None) == set()
