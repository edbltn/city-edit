#!/usr/bin/env python3
"""Export QGIS-ready artifacts for one City Edit map slug.

Produces a single GeoPackage (EPSG:4326) with one layer per artifact, so the
whole export drags into QGIS as one file:

  blocks                 Layer-2 streetscape polygons + their aggregated votes
  graph_edges            Layer-1 votable graph edges + net votes (full city)
  graph_nodes            voted graph nodes (all nodes with --all-nodes)
  proposals_point        PBTPs — square-pin proposals, point at edge midpoint
  proposals_point_edges  the winning edge of each PBTP, as a line
  proposals_route        RBTPs — diamond-pin corridors, the ordered path line
  proposals_route_blocks per-RBTP block-edge union (the highlight/vote set)
  proposals_route_anchors the two terminal intersections of each RBTP
  sample_routes          OSRM geometries for sample A→B requests
  sample_route_edges     the graph edges each sample route mapped to (edge_ids)

Data sources (everything a browser client sees, plus the block polygons):
  - the LOCAL dev API (Flask on :5001) for topology, votes, and routing
  - server/streetscape_blocks/output/blocks_generic_<city>.geojson for polygons
  - client-react/scripts/export-proposals.ts (via vite-node) for proposals —
    the REAL client modules compute them, so nothing here can drift

Run with the streetscape geo venv (it has geopandas/shapely/pyogrio):

  server/streetscape_blocks/env/bin/python scripts/export_qgis_artifacts.py \
      nyc-walkways

Full usage: --help. Guide: docs/qgis-export.md.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import shapely

REPO = Path(__file__).resolve().parents[1]
BLOCKS_DIR = REPO / "server" / "streetscape_blocks" / "output"
CLIENT_DIR = REPO / "client-react"
CRS = "EPSG:4326"

# GTB2 binary topology header (see client graphTopology.ts / graph_registry).
TOPOLOGY_MAGIC_V1 = 0x31425447  # 'GTB1'
TOPOLOGY_MAGIC_V2 = 0x32425447  # 'GTB2'

SAMPLE_ROUTE_MIN_M = 800
SAMPLE_ROUTE_MAX_M = 6_000
SAMPLE_ROUTES_FROM_PROPOSALS = 3  # additionally route between top-RBTP anchors


# ── API helpers (stdlib urllib — the geo venv has no requests) ──────────────

def api_json(base: str, path: str):
    with urllib.request.urlopen(f"{base}{path}", timeout=120) as resp:
        return json.load(resp)


def api_bytes(base: str, path: str) -> bytes:
    with urllib.request.urlopen(f"{base}{path}", timeout=120) as resp:
        return resp.read()


def api_post_json(base: str, path: str, body: dict):
    req = urllib.request.Request(
        f"{base}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


# ── Topology ────────────────────────────────────────────────────────────────

class Topology:
    """Decoded GTB1/GTB2 blob: node coords, edge endpoints, edge→block ids."""

    def __init__(self, buf: bytes):
        head = np.frombuffer(buf, dtype=np.uint32, count=4)
        if head[0] == TOPOLOGY_MAGIC_V2:
            header_bytes, self.n_blocks = 16, int(head[3])
        elif head[0] == TOPOLOGY_MAGIC_V1:
            header_bytes, self.n_blocks = 12, 0
        else:
            raise ValueError(f"bad topology magic 0x{head[0]:x}")
        self.n_nodes = int(head[1])
        self.n_edges = int(head[2])

        off = header_bytes
        coords = np.frombuffer(buf, dtype=np.int32, count=2 * self.n_nodes, offset=off)
        off += coords.nbytes
        self.lat = coords[0::2] / 1e7
        self.lon = coords[1::2] / 1e7
        self.ends = np.frombuffer(
            buf, dtype=np.uint32, count=2 * self.n_edges, offset=off,
        ).reshape(-1, 2)
        off += self.ends.nbytes
        if self.n_blocks > 0:
            self.edge_block_id = np.frombuffer(buf, dtype=np.int32, count=self.n_edges, offset=off)
        else:
            self.edge_block_id = np.full(self.n_edges, -1, dtype=np.int32)

    def edge_lines(self, edge_ids: np.ndarray):
        """Vectorized 2-point LineStrings for the given edge ids."""
        e = self.ends[edge_ids]                       # (n, 2) node indices
        xy = np.stack([self.lon[e], self.lat[e]], axis=-1)  # (n, 2, 2) lon/lat
        return shapely.linestrings(xy.reshape(-1, 2), indices=np.repeat(np.arange(len(e)), 2))

    def multiline(self, edge_ids) -> shapely.MultiLineString:
        return shapely.MultiLineString(list(self.edge_lines(np.asarray(edge_ids, dtype=np.int64))))


# ── Vote-array helpers ──────────────────────────────────────────────────────

def vt_summary(vt_entry, legend):
    """(top_label, up_total, down_total, breakdown_json) for one [idx,up,dn] list."""
    if not vt_entry:
        return None, 0, 0, None
    up = sum(p[1] for p in vt_entry)
    down = sum(p[2] for p in vt_entry)
    # Entries arrive sorted by net desc (server encode order) — first is the top.
    top = legend[vt_entry[0][0]] if vt_entry[0][0] < len(legend) else None
    breakdown = {legend[i]: {"up": u, "down": d} for i, u, d in vt_entry if i < len(legend)}
    return top, up, down, json.dumps(breakdown)


# ── Layer builders ──────────────────────────────────────────────────────────

def build_blocks_layer(city_id: str, network: str, votes: dict) -> gpd.GeoDataFrame:
    meta_path = REPO / "server" / "osm_data" / city_id / f"edge_blocks_{network}.json"
    meta = json.loads(meta_path.read_text())
    blocks_path = BLOCKS_DIR / meta["blocks_file"]

    sha = hashlib.sha256(blocks_path.read_bytes()).hexdigest()[:16]
    if sha != meta["blocks_sha256"]:
        print(f"  WARNING: {blocks_path.name} sha {sha} != baked {meta['blocks_sha256']} "
              f"— polygons may not match the served block ids", file=sys.stderr)

    print(f"  reading {blocks_path.name} …")
    gdf = gpd.read_file(blocks_path)

    n_blocks = votes["n_blocks"]
    legend = votes["block_vote_type_legend"]
    net = np.asarray(votes["block_votes"], dtype=np.int32)
    bvt = votes["block_vote_types"]

    bid = gdf["block_id"].to_numpy()
    valid = (bid >= 0) & (bid < n_blocks)
    gdf["net_votes"] = np.where(valid, net[np.clip(bid, 0, n_blocks - 1)], 0).astype(np.int32)

    tops, ups, downs, jsons = [], [], [], []
    for b, ok in zip(bid, valid):
        top, up, down, js = vt_summary(bvt[b] if ok else None, legend)
        tops.append(top); ups.append(up); downs.append(down); jsons.append(js)
    gdf["top_label"] = tops
    gdf["up_votes"] = np.asarray(ups, dtype=np.int32)
    gdf["down_votes"] = np.asarray(downs, dtype=np.int32)
    gdf["vote_types"] = jsons
    return gdf


def build_graph_edge_layer(topo: Topology, votes: dict) -> gpd.GeoDataFrame:
    legend = votes["vote_type_legend"]
    net = np.asarray(votes["edge_votes"], dtype=np.int32)
    evt = votes["edge_vote_types"]

    top_label = np.full(topo.n_edges, None, dtype=object)
    for eid in np.nonzero(net)[0]:
        top_label[eid], _, _, _ = vt_summary(evt[eid], legend)

    all_edges = np.arange(topo.n_edges, dtype=np.int64)
    return gpd.GeoDataFrame(
        {
            "edge_id": all_edges.astype(np.int32),
            "from_node": topo.ends[:, 0].astype(np.int32),
            "to_node": topo.ends[:, 1].astype(np.int32),
            "block_id": topo.edge_block_id,
            "net_votes": net,
            "top_label": top_label,
        },
        geometry=topo.edge_lines(all_edges), crs=CRS,
    )


def build_graph_node_layer(topo: Topology, votes: dict, all_nodes: bool) -> gpd.GeoDataFrame:
    net = np.asarray(votes["node_votes"], dtype=np.int32)
    ids = np.arange(topo.n_nodes, dtype=np.int64) if all_nodes else np.nonzero(net)[0]
    return gpd.GeoDataFrame(
        {"node_id": ids.astype(np.int32), "net_votes": net[ids]},
        geometry=shapely.points(topo.lon[ids], topo.lat[ids]), crs=CRS,
    )


def run_proposals_helper(api: str, slug: str, mode: str) -> dict:
    """Compute PBTPs/RBTPs with the actual client TS modules via vite-node."""
    out_json = Path(tempfile.mkstemp(suffix=".json", prefix="proposals-")[1])
    cmd = [
        str(CLIENT_DIR / "node_modules" / ".bin" / "vite-node"),
        "scripts/export-proposals.ts", "--", api, slug, mode, str(out_json),
    ]
    subprocess.run(cmd, cwd=CLIENT_DIR, check=True)
    data = json.loads(out_json.read_text())
    out_json.unlink()
    return data


def build_proposal_layers(topo: Topology, proposals: dict) -> dict[str, gpd.GeoDataFrame]:
    pts, routes = proposals["points"], proposals["routes"]
    layers: dict[str, gpd.GeoDataFrame] = {}

    if pts:
        rows = pd.DataFrame([
            {
                "edge_id": p["edge_id"], "label": p["label"], "net_votes": p["net"],
                "block_id": int(topo.edge_block_id[p["edge_id"]]),
                "covered_by_route": bool(p["covered_by_route"]),
            }
            for p in pts
        ])
        mids = [((a[1] + b[1]) / 2, (a[0] + b[0]) / 2) for a, b in (p["ends"] for p in pts)]
        layers["proposals_point"] = gpd.GeoDataFrame(
            rows, geometry=[shapely.Point(x, y) for x, y in mids], crs=CRS)
        layers["proposals_point_edges"] = gpd.GeoDataFrame(
            rows, geometry=[
                shapely.LineString([(a[1], a[0]), (b[1], b[0])]) for a, b in (p["ends"] for p in pts)
            ], crs=CRS)

    if routes:
        rows = pd.DataFrame([
            {
                "proposal_id": r["id"], "label": r["label"], "score": r["score"],
                "n_path_edges": len(r["edge_ids"]), "n_blocks": r["n_blocks"],
                "block_ids": ",".join(str(b) for b in sorted(
                    {int(topo.edge_block_id[e]) for e in r["block_edge_ids"]} - {-1})),
            }
            for r in routes
        ])
        layers["proposals_route"] = gpd.GeoDataFrame(
            rows, geometry=[
                shapely.LineString([(lng, lat) for lat, lng in r["path_latlngs"]]) for r in routes
            ], crs=CRS)
        layers["proposals_route_blocks"] = gpd.GeoDataFrame(
            rows, geometry=[topo.multiline(r["block_edge_ids"]) for r in routes], crs=CRS)
        anchor_rows, anchor_geoms = [], []
        for r in routes:
            for which, (lat, lng) in zip(("start", "end"), r["anchor_coords"]):
                anchor_rows.append({"proposal_id": r["id"], "label": r["label"], "which": which})
                anchor_geoms.append(shapely.Point(lng, lat))
        layers["proposals_route_anchors"] = gpd.GeoDataFrame(
            pd.DataFrame(anchor_rows), geometry=anchor_geoms, crs=CRS)

    return layers


def sample_route_pairs(topo: Topology, proposals: dict, n_random: int, seed: int):
    """(kind, [lat,lng] start, [lat,lng] end) pairs: top-RBTP anchors + seeded
    random node pairs SAMPLE_ROUTE_MIN_M–MAX_M apart (routable, city-scale)."""
    pairs = []
    for r in proposals.get("routes", [])[:SAMPLE_ROUTES_FROM_PROPOSALS]:
        (alat, alng), (blat, blng) = r["anchor_coords"]
        pairs.append((f"proposal:{r['id']}", [alat, alng], [blat, blng]))

    degree = np.bincount(topo.ends.ravel(), minlength=topo.n_nodes)
    connected = np.nonzero(degree > 0)[0]
    rng = np.random.default_rng(seed)
    attempts = 0
    while len(pairs) < n_random + min(SAMPLE_ROUTES_FROM_PROPOSALS,
                                      len(proposals.get("routes", []))) and attempts < 10_000:
        attempts += 1
        a, b = connected[rng.integers(0, len(connected), size=2)]
        dlat = (topo.lat[a] - topo.lat[b]) * 111_320
        dlon = (topo.lon[a] - topo.lon[b]) * 111_320 * np.cos(np.radians(topo.lat[a]))
        dist = float(np.hypot(dlat, dlon))
        if SAMPLE_ROUTE_MIN_M <= dist <= SAMPLE_ROUTE_MAX_M:
            pairs.append(("random", [float(topo.lat[a]), float(topo.lon[a])],
                          [float(topo.lat[b]), float(topo.lon[b])]))
    return pairs


def build_sample_route_layers(api: str, slug: str, topo: Topology, pairs) -> dict[str, gpd.GeoDataFrame]:
    rows, geoms, edge_geoms = [], [], []
    for i, (kind, start, end) in enumerate(pairs):
        try:
            resp = api_post_json(api, "/api/routes", {"map": slug, "start": start, "end": end})
        except urllib.error.HTTPError as e:
            print(f"  sample route {i} ({kind}): HTTP {e.code} — skipped", file=sys.stderr)
            continue
        route = resp.get("route") or {}
        coords = (route.get("geometry") or {}).get("coordinates") or []
        edge_ids = resp.get("edge_ids") or []
        if len(coords) < 2:
            print(f"  sample route {i} ({kind}): no geometry — skipped", file=sys.stderr)
            continue
        rows.append({
            "route_idx": i, "kind": kind,
            "start_lat": start[0], "start_lng": start[1],
            "end_lat": end[0], "end_lng": end[1],
            "distance_m": route.get("distance"), "duration_s": route.get("duration"),
            "n_edge_ids": len(edge_ids),
            "edge_ids": ",".join(str(e) for e in edge_ids),
        })
        geoms.append(shapely.LineString(coords))  # already [lon, lat]
        edge_geoms.append(topo.multiline(edge_ids) if edge_ids else shapely.MultiLineString([]))

    if not rows:
        return {}
    df = pd.DataFrame(rows)
    return {
        "sample_routes": gpd.GeoDataFrame(df, geometry=geoms, crs=CRS),
        "sample_route_edges": gpd.GeoDataFrame(df, geometry=edge_geoms, crs=CRS),
    }


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("slug", help="map slug, e.g. nyc-walkways")
    ap.add_argument("--api", default="http://localhost:5001", help="Flask API base")
    ap.add_argument("--out", default=None, help="output dir (default exports/qgis/<slug>)")
    ap.add_argument("--routes", type=int, default=6, help="number of random sample routes")
    ap.add_argument("--seed", type=int, default=42, help="RNG seed for sample routes")
    ap.add_argument("--bbox", default=None, help="clip blocks/graph to 'west,south,east,north'")
    ap.add_argument("--all-nodes", action="store_true",
                    help="export every graph node (default: voted nodes only)")
    ap.add_argument("--skip", default="", help="comma list of blocks,graph,proposals,routes")
    args = ap.parse_args()
    skip = set(filter(None, args.skip.split(",")))

    out_dir = Path(args.out) if args.out else REPO / "exports" / "qgis" / args.slug
    out_dir.mkdir(parents=True, exist_ok=True)
    gpkg = out_dir / f"city-edit-{args.slug}.gpkg"
    if gpkg.exists():
        gpkg.unlink()  # fresh file — stale layers from a previous run must not linger

    t0 = time.time()
    info = api_json(args.api, f"/api/maps/{args.slug}")
    city_id, mode, network = info["cityId"], info["mode"], info["network"]
    print(f"[export] {args.slug}: city={city_id} mode={mode} network={network}")

    print("[export] fetching topology + votes …")
    topo = Topology(api_bytes(args.api, f"/api/graph-topology?map={args.slug}&format=bin"))
    votes = api_json(args.api, f"/api/graph-votes?map={args.slug}&mode={mode}")
    if votes["n_edges"] != topo.n_edges or votes["n_nodes"] != topo.n_nodes:
        sys.exit(f"FATAL: votes ({votes['n_nodes']}n/{votes['n_edges']}e) don't match "
                 f"topology ({topo.n_nodes}n/{topo.n_edges}e)")
    print(f"[export] topology {topo.n_nodes:,} nodes / {topo.n_edges:,} edges / "
          f"{topo.n_blocks:,} blocks, vote revision {votes['rev']}")

    bbox = tuple(float(x) for x in args.bbox.split(",")) if args.bbox else None
    written = {}

    def write(name: str, gdf: gpd.GeoDataFrame):
        if bbox is not None and name.startswith(("blocks", "graph")):
            gdf = gdf.cx[bbox[0]:bbox[2], bbox[1]:bbox[3]]
        gdf.to_file(gpkg, layer=name, driver="GPKG")
        written[name] = len(gdf)
        print(f"  layer {name}: {len(gdf):,} features")

    if "blocks" not in skip and topo.n_blocks > 0:
        print("[export] blocks + votes …")
        write("blocks", build_blocks_layer(city_id, network, votes))
    elif topo.n_blocks == 0:
        print("[export] map has no block artifacts — skipping blocks layer")

    if "graph" not in skip:
        print("[export] route graph …")
        write("graph_edges", build_graph_edge_layer(topo, votes))
        write("graph_nodes", build_graph_node_layer(topo, votes, args.all_nodes))

    proposals = {"points": [], "routes": []}
    if "proposals" not in skip:
        print("[export] proposals (vite-node → client TS modules) …")
        proposals = run_proposals_helper(args.api, args.slug, mode)
        for name, gdf in build_proposal_layers(topo, proposals).items():
            write(name, gdf)

    if "routes" not in skip and network == "streets":
        print("[export] sample routes …")
        pairs = sample_route_pairs(topo, proposals, args.routes, args.seed)
        for name, gdf in build_sample_route_layers(args.api, args.slug, topo, pairs).items():
            write(name, gdf)
    elif network != "streets":
        print(f"[export] network '{network}' has no routing — skipping sample routes")

    manifest = {
        "slug": args.slug, "city": city_id, "mode": mode, "network": network,
        "api": args.api, "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "vote_revision": votes["rev"], "topology_version": votes.get("topology_version"),
        "blocks_version": votes.get("blocks_version"),
        "seed": args.seed, "bbox": args.bbox, "layers": written,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"[export] done in {time.time() - t0:.0f}s → {gpkg}")


if __name__ == "__main__":
    main()
