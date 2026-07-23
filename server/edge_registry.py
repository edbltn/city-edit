"""
Stable edge IDs (eids) per city.

An edge's ID has always been its array position in the topology build — the
key for every vote in Redis (24-bit packed field), Postgres (edge_votes.edge_id),
WebSocket deltas, and the client's vote arrays. Positional IDs break the moment
the graph is rebuilt from fresh OSM data, which is why OSM refreshes have been
frozen on cities with votes.

This registry pins the assignment instead of changing the key type: eids stay
dense small ints, but which edge owns which eid is recorded durably, keyed by
the OSM node ids of the edge's endpoints:

    stable_key = "<min_osm_id>:<max_osm_id>:<occurrence>"

(occurrence disambiguates parallel edges sharing endpoints). On every graph
load the edges array is reordered so that index == eid. Rebuilds keep matching
edges' eids, allocate fresh eids for new edges, and leave TOMBSTONE entries in
the slots of edges that no longer exist — their votes stay addressed, the
geometry is just gone. Tombstones (and OSM self-loops) are recognizable as
from_idx == to_idx and are skipped by hit-testing, heat, tiles, and lookups.

File: osm_data/<city>/edge_registry.json — a build artifact that ships beside
walk_graph.pkl and MUST be updated whenever the pkl is rebuilt
(refresh_osm.py does this; see also build_pmtiles.py which bakes eids into
tile feature ids).

Usage:
    python edge_registry.py --city nyc     # seed/update the registry for a city
    python edge_registry.py --selftest     # synthetic rebuild/shuffle test
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

REGISTRY_FILENAME = "edge_registry.json"
REGISTRY_VERSION = 1

# Placeholder for a retired eid's slot: degenerate (from == to) so every
# consumer's self-loop guard skips it.
TOMBSTONE = [0, 0, "", "", 0.0]


def registry_path(data_dir: str) -> str:
    return os.path.join(data_dir, REGISTRY_FILENAME)


def load_registry(data_dir: str) -> dict | None:
    path = registry_path(data_dir)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        reg = json.load(f)
    if reg.get("version") != REGISTRY_VERSION:
        raise ValueError(f"Unsupported edge registry version in {path}: {reg.get('version')}")
    return reg


def save_registry(data_dir: str, registry: dict) -> None:
    path = registry_path(data_dir)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(registry, f, separators=(",", ":"))
    os.replace(tmp, path)


def stable_edge_keys(edges: list, idx_to_osm: dict[int, int]) -> list[str]:
    """One durable key per edge, from the OSM node ids of its endpoints.

    Parallel edges sharing endpoints get occurrence ordinals assigned in
    attribute order (name/highway/length), NOT list order — so the ordinals
    are deterministic across rebuilds that iterate edges differently.
    """
    groups: dict[str, list[int]] = {}
    bases: list[str] = []
    for i, e in enumerate(edges):
        u = idx_to_osm.get(e[0])
        v = idx_to_osm.get(e[1])
        if u is None or v is None:
            # Shouldn't happen (every graph node has an OSM id) — positional
            # fallback keeps the build usable but won't survive a rebuild.
            base = f"g{e[0]}:{e[1]}"
        else:
            base = f"{u}:{v}" if u <= v else f"{v}:{u}"
        bases.append(base)
        groups.setdefault(base, []).append(i)

    def attrs(i: int) -> tuple:
        e = edges[i]
        return (
            str(e[2]) if len(e) > 2 else "",
            str(e[3]) if len(e) > 3 else "",
            float(e[4]) if len(e) > 4 else 0.0,
        )

    keys: list[str] = [""] * len(edges)
    for base, idxs in groups.items():
        if len(idxs) == 1:
            keys[idxs[0]] = f"{base}:0"
        else:
            for k, i in enumerate(sorted(idxs, key=attrs)):
                keys[i] = f"{base}:{k}"
    return keys


def apply_edge_registry(data_dir: str, data: dict, persist: bool = False) -> dict:
    """Reorder data['edges'] in place so that index == stable eid.

    Rebuilds data['node_pair_to_edge'] to match. Idempotent per data dict
    (guarded by a marker key — the provider caches and re-serves the same
    dict). Returns a stats dict.

    persist=True (build time) creates the registry when missing (identity
    seed from the current order) and saves newly-allocated eids.
    persist=False (server runtime) applies read-only; a missing registry
    leaves the build positional (legacy behavior).
    """
    if data.get("_eids_applied"):
        return {"applied": True, "cached": True}

    edges = data.get("edges", [])
    osm_to_graph_idx = data.get("osm_to_graph_idx") or {}
    idx_to_osm = {idx: int(osm) for osm, idx in osm_to_graph_idx.items()}

    registry = load_registry(data_dir)

    if registry is None:
        if not persist:
            logger.warning(
                f"[EIDS] No edge registry in {data_dir} — edge ids are positional "
                f"and will NOT survive a graph rebuild. Run: python edge_registry.py"
            )
            return {"applied": False, "reason": "no-registry"}
        # Identity seed: pin the current order as the durable assignment, so
        # every existing vote (keyed by today's positional ids) stays correct.
        keys = stable_edge_keys(edges, idx_to_osm)
        registry = {
            "version": REGISTRY_VERSION,
            "next_eid": len(edges),
            "edges": {k: i for i, k in enumerate(keys)},
        }
        save_registry(data_dir, registry)
        data["_eids_applied"] = True
        logger.info(f"[EIDS] Seeded registry: {len(edges)} edges (identity mapping)")
        return {"applied": True, "seeded": True, "total": len(edges), "new": 0, "retired": 0}

    keys = stable_edge_keys(edges, idx_to_osm)
    mapping = registry["edges"]
    next_eid = registry["next_eid"]

    new_count = 0
    by_eid: dict[int, list] = {}
    for i, key in enumerate(keys):
        eid = mapping.get(key)
        if eid is None:
            eid = next_eid
            next_eid += 1
            mapping[key] = eid
            new_count += 1
        by_eid[eid] = edges[i]

    size = next_eid
    out: list = [None] * size
    for eid, e in by_eid.items():
        out[eid] = e
    retired = 0
    for eid in range(size):
        if out[eid] is None:
            out[eid] = list(TOMBSTONE)
            retired += 1

    data["edges"] = out
    # (graph_node_a, graph_node_b) → eid, both directions; skip degenerates.
    node_pair_to_edge: dict[tuple[int, int], int] = {}
    for i, e in enumerate(out):
        if e[0] == e[1]:
            continue
        node_pair_to_edge[(e[0], e[1])] = i
        node_pair_to_edge[(e[1], e[0])] = i
    data["node_pair_to_edge"] = node_pair_to_edge
    data["_eids_applied"] = True

    if persist and new_count:
        registry["next_eid"] = next_eid
        save_registry(data_dir, registry)

    stats = {"applied": True, "total": len(out), "new": new_count, "retired": retired}
    logger.info(f"[EIDS] Registry applied: {stats}")
    return stats


# ── CLI: seed/update a city's registry, or run the selftest ────────────────

def _seed_city(city_id: str) -> None:
    from cities import get_city
    from python_router import PythonRouter

    city = get_city(city_id)
    if not city:
        raise SystemExit(f"Unknown city: {city_id}")
    router = PythonRouter(data_dir=city.data_dir)
    south, west, north, east = city.bbox
    data = router.get_graph_for_bbox(south, west, north, east)
    stats = apply_edge_registry(city.data_dir, data, persist=True)
    print(f"{city_id}: {stats}")


def _selftest() -> None:
    """Simulate a rebuild: shuffle edge order, drop one edge, add one — the
    surviving edges must keep their eids and the dropped slot must tombstone."""
    import random
    import tempfile

    nodes = [[40.0 + i * 0.001, -74.0 + i * 0.001] for i in range(6)]
    osm_to_graph_idx = {1000 + i: i for i in range(6)}
    build1 = [
        [0, 1, "A st", "residential", 10.0],
        [1, 2, "B st", "residential", 11.0],
        [2, 3, "C st", "residential", 12.0],
        [3, 4, "D st", "residential", 13.0],
        [1, 2, "B st dup", "path", 14.0],  # parallel edge (occurrence 1)
    ]

    with tempfile.TemporaryDirectory() as d:
        data1 = {"edges": [list(e) for e in build1], "osm_to_graph_idx": dict(osm_to_graph_idx)}
        s1 = apply_edge_registry(d, data1, persist=True)
        assert s1["applied"] and s1.get("seeded"), s1
        assert data1["edges"][0][2] == "A st"

        # "Rebuild": shuffled order, C st gone, one new edge appears.
        build2 = [list(e) for e in build1 if e[2] != "C st"] + [[4, 5, "E st", "residential", 15.0]]
        random.Random(7).shuffle(build2)
        data2 = {
            "edges": build2,
            "osm_to_graph_idx": dict(osm_to_graph_idx),
            "nodes": nodes,
        }
        s2 = apply_edge_registry(d, data2, persist=True)
        assert s2["new"] == 1 and s2["retired"] == 1, s2

        e = data2["edges"]
        assert e[0][2] == "A st", e[0]        # kept eid 0
        assert e[1][2] == "B st", e[1]        # kept eid 1
        assert e[2][0] == e[2][1], e[2]       # C st slot tombstoned
        assert e[3][2] == "D st", e[3]        # kept eid 3
        assert e[4][2] == "B st dup", e[4]    # parallel edge kept eid 4
        assert e[5][2] == "E st", e[5]        # new edge got eid 5
        assert data2["node_pair_to_edge"][(0, 1)] == 0
        assert (2, 3) not in data2["node_pair_to_edge"]

        # Third application (same build) is stable: no new, same retired.
        data3 = {"edges": [list(x) for x in build2], "osm_to_graph_idx": dict(osm_to_graph_idx)}
        s3 = apply_edge_registry(d, data3, persist=True)
        assert s3["new"] == 0 and s3["retired"] == 1, s3
        assert [x[2] for x in data3["edges"]] == [x[2] for x in data2["edges"]]

    print("edge_registry selftest: OK")


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--city", help="Seed/update the registry for this city id")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
    elif args.city:
        _seed_city(args.city)
    else:
        ap.print_help()
