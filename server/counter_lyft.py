#!/usr/bin/env python3
"""
Counter-vote pass for Lyft bikeshare imports (the import_lyft.py corrective).

import_lyft.py ingests bikeshare rides by routing them through the FOOT
profile ("pedestrianized") and upvoting every edge. That over-credits streets
a legal bike route would have used anyway — especially corridors that already
have bike lanes near stations. This script subtracts that signal: for every
ingested ride it routes the SAME trip through a bike-legality OSRM
(scripts/build_bike_osrm.sh, profile osrm/bicycle-flat.lua — shortest LEGAL
path, no pushing-the-bike onto foot-only ways) and CANCELS the ride's own
votes (direction=0, same voter_id and vote type) on the upvoted edges the
bike route covers. Only the divergent stretches — pedestrian-only paths and
counter-one-way riding a bike can't legally use — keep their upvotes.

Two structural choices make "covers" mean "this stretch was legally ridable":

1. Via-guided routing. The voted edge set is re-ordered into a path (walking
   the edges from the endpoint nearest the trip start) and every
   ~VIA_STRIDE-th node along it becomes an OSRM via-point. With the flat
   profile routing by shortest legal distance, the bike route then follows
   the ride's own corridor wherever a bike may ride it and detours only
   around the stretches it can't — it never wanders off to a faster parallel
   avenue and spares the corridor by accident. Rides whose voted set doesn't
   reconstruct into a clean path (round trips, resnap fragments) fall back to
   plain endpoint-to-endpoint routing.

2. Geometric corridor coverage, not edge-id overlap. Edge ids can't see the
   two cases that matter most: the votable graph stores parallel duplicate
   edges per node pair (ids resolve to either copy), and NYC protected bike
   lanes are separate OSM ways running parallel to the roadway the foot
   route walked — same street, zero shared ids. So a voted edge is countered
   when it lies bodily inside the bike route's corridor: both endpoints AND
   its midpoint within COVER_DIST_M (20 m — half an avenue's width, well
   under the ~80 m to the next parallel street) of the route polyline. A
   pedestrian-park or counter-one-way stretch forces the bike route a block
   away, putting its edges far outside the corridor: structurally
   un-subtractable.

Ingested rides are recognized by identity, not bookkeeping: import_lyft casts
with voter_id = ride_id, which the server stores as device_id =
sha256(ride_id)[:16], so hashing the ride_ids in the cached monthly zips
(server/lyft_data/) recovers exactly the imported devices and their trip
endpoints. Human voters never match.

Idempotent: re-running plans no-ops (the voter has no remaining votes on the
covered blocks), so a second pass changes nothing.

Cancellation only ever removes the device's OWN votes, so a (vote_type, edge)
tally can never go negative and no device is counted "against" at block level
(block counts dedupe per voter — block_votes.py). The legacy --flip mode
casts direction=-1 instead, guarded by a running tally gate (NetGate) that
keeps every tally ≥ 0: a flip is a −2 swing so it's only cast while the tally
stays positive; the last upvote is downgraded to a removal; zeroed edges are
left alone. Flips still surface as block-level downvotes — prefer the default.

Usage:
    # Build + serve the bike graph first:
    #   scripts/build_bike_osrm.sh nyc --serve 5006
    python -u counter_lyft.py --city nyc --map nyc-bikes \
        --api-base http://localhost:5001

    # Inspect without voting:
    python -u counter_lyft.py --city nyc --map nyc-bikes --dry-run

Requires: aiohttp, pandas, requests (same as import_lyft.py)
"""

import argparse
import asyncio
import hashlib
import os
import sys
import time
import zipfile
from collections import defaultdict
from io import BytesIO
from pathlib import Path

import aiohttp
import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()  # DATABASE_URL, read at import by database.py

from cities import get_city
from database import get_cursor
from graph_registry import CityGraph
from import_lyft import LYFT_SOURCES, fetch_map_mode, health_check
from python_router import PythonRouter

CACHE_DIR = Path(__file__).parent / "lyft_data"
RIDE_COLS = ["ride_id", "start_lat", "start_lng", "end_lat", "end_lng"]
VIA_STRIDE = 8       # sample every Nth path node as an OSRM via-point (~250m)
MAX_VIAS = 40        # long rides widen the stride instead of growing the URL
COVER_DIST_M = 20.0  # corridor half-width for "the bike route covers this edge"
M_PER_DEG_LAT = 110540.0
M_PER_DEG_LON_EQ = 111320.0  # × cos(lat)


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def device_of(ride_id: str) -> str:
    """The server-side device_id an imported ride votes under (app._resolve_user)."""
    return hashlib.sha256(ride_id.encode()).hexdigest()[:16]


# ── DB: the imported votes to counter ────────────────────────────────────────

def load_upvotes(map_slug: str) -> tuple[dict[str, dict[int, set[int]]], set[str]]:
    """({device_id: {vote_type_id: {edge_id}}}, {import-marked device_id}).

    The marker set is devices whose rows carry ip_hash == device_id — the
    ip_from_voter signature only bulk imports produce (humans get an IP hash)."""
    votes: dict[str, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    marked: set[str] = set()
    with get_cursor() as cur:
        cur.execute(
            "SELECT device_id, vote_type_id, edge_id, (ip_hash = device_id) "
            "FROM edge_votes WHERE map_slug = %s AND direction = 1",
            (map_slug,),
        )
        for device_id, vt_id, edge_id, marker in cur.fetchall():
            votes[device_id][vt_id].add(edge_id)
            if marker:
                marked.add(device_id)
    return votes, marked


def load_net_counts(map_slug: str) -> dict[tuple[int, int], int]:
    """{(vote_type_id, edge_id): SUM(direction)} — the tally the gate runs on."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT vote_type_id, edge_id, SUM(direction) FROM edge_votes "
            "WHERE map_slug = %s GROUP BY 1, 2",
            (map_slug,),
        )
        return {(int(vt), int(e)): int(n) for vt, e, n in cur.fetchall()}


class NetGate:
    """Running per-(vote_type, edge) tally guard: a counter cast must never
    push a tally below zero.

    A flip (+1 → −1) moves the tally by −2, so it's only allowed while the
    tally is ≥ 2; at exactly 1 the device's vote is REMOVED instead
    (direction 0 — tally lands on 0, never negative); at ≤ 0 the edge is
    left alone. Synchronous mutation is safe under asyncio (no await inside).
    """

    def __init__(self, net: dict[tuple[int, int], int]):
        self.net = net

    def take(self, vt_id: int, edges) -> tuple[list[int], list[int]]:
        """Split covered edges into (flip_to_against, remove_vote)."""
        flips, zeros = [], []
        for e in edges:
            n = self.net.get((vt_id, e), 0)
            if n >= 2:
                flips.append(e)
                self.net[(vt_id, e)] = n - 2
            elif n == 1:
                zeros.append(e)
                self.net[(vt_id, e)] = 0
        return flips, zeros


def load_vote_type_labels(vt_ids: set[int]) -> dict[int, str]:
    if not vt_ids:
        return {}
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, label FROM vote_types WHERE id = ANY(%s)",
            (list(vt_ids),),
        )
        return dict(cur.fetchall())


# ── Cached trip zips: recover ride endpoints for imported devices ────────────

def iter_cached_csvs(city_id: str):
    """Yield (zip_name, DataFrame[RIDE_COLS]) for every cached monthly zip."""
    infix = LYFT_SOURCES[city_id]["infix"]
    zips = sorted(CACHE_DIR.glob(f"*-{infix}-tripdata.zip"))
    if not zips:
        sys.exit(f"ERROR: no cached {infix} zips in {CACHE_DIR}. "
                 "The counter pass needs the same monthly files the import used.")
    for zip_path in zips:
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                if name.startswith("__MACOSX") or name.endswith("/"):
                    continue
                if name.endswith(".csv"):
                    with zf.open(name) as f:
                        yield zip_path.name, pd.read_csv(
                            f, usecols=RIDE_COLS, low_memory=False)
                elif name.endswith(".zip"):
                    with zf.open(name) as f:
                        with zipfile.ZipFile(BytesIO(f.read())) as inner:
                            for iname in inner.namelist():
                                if iname.endswith(".csv") and not iname.startswith("__MACOSX"):
                                    with inner.open(iname) as cf:
                                        yield zip_path.name, pd.read_csv(
                                            cf, usecols=RIDE_COLS, low_memory=False)


def match_rides(city_id: str, devices: set[str]) -> dict[str, tuple]:
    """{device_id: (ride_id, start(lat,lon), end(lat,lon))} for imported rides."""
    matched: dict[str, tuple] = {}
    for zip_name, df in iter_cached_csvs(city_id):
        df = df.dropna(subset=RIDE_COLS)
        for row in df.itertuples(index=False):
            rid = str(row.ride_id)
            dev = device_of(rid)
            if dev in devices and dev not in matched:
                matched[dev] = (
                    rid,
                    (float(row.start_lat), float(row.start_lng)),
                    (float(row.end_lat), float(row.end_lng)),
                )
        log(f"  {zip_name}: matched {len(matched)}/{len(devices)} devices so far")
        if len(matched) == len(devices):
            break
    return matched


# ── Bike routing → overlap edges ─────────────────────────────────────────────

def _walk_voted_path(graph, edge_ids: set[int], start) -> list[int]:
    """Ordered node positions along the voted path, or [] if unwalkable.

    Walks the voted edge set from the degree-1 node nearest `start` (any
    degree-1 node when start is None), preferring neighbors that still have
    onward edges (so resnap spurs don't dead-end the walk early). [] when the
    set has no walkable path structure (round trips, heavy fragmentation).
    """
    adj: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for eid in edge_ids:
        a, b = (int(x) for x in graph.edge_ends[eid])
        adj[a].append((b, eid))
        adj[b].append((a, eid))
    deg1 = [n for n, nbrs in adj.items() if len(nbrs) == 1]
    if not deg1:
        return []

    if start is not None:
        def dist2(n):
            lat, lon = graph.node_coords[n]
            return (lat - start[0]) ** 2 + (lon - start[1]) ** 2
        cur = min(deg1, key=dist2)
    else:
        cur = deg1[0]
    used: set[int] = set()
    order = [cur]
    while True:
        nxt = [(nb, eid) for nb, eid in adj[cur] if eid not in used]
        if not nxt:
            break
        # Prefer a continuation (neighbor with onward unused edges) over a spur.
        nb, eid = next(
            ((nb, eid) for nb, eid in nxt
             if any(e2 != eid and e2 not in used for _, e2 in adj[nb])),
            nxt[0],
        )
        used.add(eid)
        order.append(nb)
        cur = nb
    if len(used) < max(2, len(edge_ids) // 2):
        return []  # walk covered too little of the set to trust its order
    return order


def _vias_from_order(graph, order: list[int]) -> list[tuple[float, float]]:
    inner = order[1:-1]
    stride = max(VIA_STRIDE, -(-len(inner) // MAX_VIAS))
    return [(float(graph.node_coords[n][0]), float(graph.node_coords[n][1]))
            for n in inner[stride - 1::stride]]


def voted_path_vias(graph, edge_ids: set[int], start) -> list[tuple[float, float]]:
    """Ordered (lat, lon) via-points sampled along the ride's voted path."""
    order = _walk_voted_path(graph, edge_ids, start)
    return _vias_from_order(graph, order) if order else []


def reconstructed_trip(graph, edge_ids: set[int]):
    """(start, end, vias) recovered from the voted edges themselves, or None.

    For import rows whose ride ids are no longer published anywhere (their
    voter_id preimage is gone), the trip endpoints ARE the voted path's own
    ends. Which end was the true start is unknowable, so the caller routes
    BOTH directions and intersects coverage — one-way legality then never
    depends on the guess (a counter-one-way stretch fails coverage in at
    least one direction and survives).

    Twice-resnapped vote sets are topologically shredded (each resnap picks
    among parallel duplicate edges, so consecutive edges stop sharing nodes)
    but their midpoints still trace the corridor — so when the node walk
    fails, fall back to pure geometry: endpoints ≈ the farthest-apart pair of
    edge midpoints, via order by greedy nearest-neighbor chaining from one end.
    """
    order = _walk_voted_path(graph, edge_ids, None)
    if order and len(order) >= 4:
        start = (float(graph.node_coords[order[0]][0]), float(graph.node_coords[order[0]][1]))
        end = (float(graph.node_coords[order[-1]][0]), float(graph.node_coords[order[-1]][1]))
        return start, end, _vias_from_order(graph, order)

    if len(edge_ids) < 4:
        return None
    eids = np.fromiter(edge_ids, dtype=np.int64, count=len(edge_ids))
    ends = graph.edge_ends[eids]
    mids = (graph.node_coords[ends[:, 0]] + graph.node_coords[ends[:, 1]]) / 2
    lat0 = float(mids[:, 0].mean())
    xy = np.stack([mids[:, 1] * M_PER_DEG_LON_EQ * np.cos(np.radians(lat0)),
                   mids[:, 0] * M_PER_DEG_LAT], axis=1)
    # Farthest-pair approximation: farthest from centroid, then farthest from it.
    i0 = int(((xy - xy.mean(0)) ** 2).sum(1).argmax())
    i1 = int(((xy - xy[i0]) ** 2).sum(1).argmax())
    # Greedy nearest-neighbor chain from one end orders midpoints along the corridor.
    order_idx = [i0]
    remaining = set(range(len(xy))) - {i0}
    cur = i0
    while remaining:
        nxt = min(remaining, key=lambda j: ((xy[j] - xy[cur]) ** 2).sum())
        order_idx.append(nxt)
        remaining.discard(nxt)
        cur = nxt
    stride = max(VIA_STRIDE, -(-max(0, len(order_idx) - 2) // MAX_VIAS))
    vias = [(float(mids[j, 0]), float(mids[j, 1]))
            for j in order_idx[1:-1][stride - 1::stride]]
    return ((float(mids[i0, 0]), float(mids[i0, 1])),
            (float(mids[i1, 0]), float(mids[i1, 1])), vias)


async def bike_route_geometry(session, osrm_base, start, end, vias) -> np.ndarray | None:
    """Route start→(vias)→end on the bike graph; return [N,2] (lat, lon), or None."""
    points = [start, *vias, end]
    coords = ";".join(f"{lon},{lat}" for lat, lon in points)
    url = (f"{osrm_base}/route/v1/cycling/{coords}"
           "?overview=full&geometries=geojson&steps=false")
    for attempt in range(3):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    return None
                data = await resp.json()
        except Exception:
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            return None
        break
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    coords = data["routes"][0].get("geometry", {}).get("coordinates", [])
    if len(coords) < 2:
        return None
    return np.array([(c[1], c[0]) for c in coords], dtype=np.float64)


def covered_edges(graph, edge_ids: set[int], route_latlon: np.ndarray) -> set[int]:
    """Voted edges lying bodily inside the bike route's corridor.

    An edge counts only when BOTH endpoints and its midpoint are within
    COVER_DIST_M of the route polyline — a perpendicular street crossed at an
    intersection has its far endpoint a block away, so it never qualifies.
    Distances are computed in a local equirectangular projection (meters).
    """
    if not edge_ids or route_latlon is None:
        return set()
    lat0 = float(route_latlon[0, 0])
    kx = M_PER_DEG_LON_EQ * np.cos(np.radians(lat0))

    def to_xy(latlon):
        return np.stack([latlon[..., 1] * kx, latlon[..., 0] * M_PER_DEG_LAT], axis=-1)

    poly = to_xy(route_latlon)
    eids = np.fromiter(edge_ids, dtype=np.int64, count=len(edge_ids))
    ends = graph.edge_ends[eids]
    p1 = to_xy(graph.node_coords[ends[:, 0]])
    p2 = to_xy(graph.node_coords[ends[:, 1]])
    pts = np.concatenate([p1, p2, (p1 + p2) / 2])  # endpoints then midpoints

    a, b = poly[:-1], poly[1:]
    ab = b - a
    ab2 = (ab ** 2).sum(1)
    ab2[ab2 == 0] = 1e-9
    ap = pts[:, None, :] - a[None, :, :]
    t = np.clip((ap * ab[None]).sum(-1) / ab2[None], 0.0, 1.0)
    proj = a[None] + t[..., None] * ab[None]
    dmin = np.sqrt(((pts[:, None, :] - proj) ** 2).sum(-1)).min(1)

    k = len(eids)
    ok = (dmin[:k] <= COVER_DIST_M) & (dmin[k:2 * k] <= COVER_DIST_M) \
        & (dmin[2 * k:] <= COVER_DIST_M)
    return {int(e) for e in eids[ok]}


class Stats:
    def __init__(self, total: int):
        self.total = total
        self.processed = 0
        self.route_failed = 0
        self.no_overlap = 0
        self.vote_failed = 0
        self.countered = 0
        self.via_guided = 0
        self.reconstructed = 0
        self.edges_upvoted = 0
        self.edges_downvoted = 0
        self.edges_zeroed = 0
        self.edges_cleared = 0
        self.lock = asyncio.Lock()

    async def bump_quiet(self, **kw):
        """Count without advancing `processed` (per-ride sub-events)."""
        async with self.lock:
            for k, v in kw.items():
                setattr(self, k, getattr(self, k) + v)

    async def bump(self, **kw):
        async with self.lock:
            self.processed += 1
            for k, v in kw.items():
                setattr(self, k, getattr(self, k) + v)
            if self.processed % 200 == 0:
                log(f"[{self.processed}/{self.total}] countered={self.countered} "
                    f"no_overlap={self.no_overlap} route_failed={self.route_failed} "
                    f"vote_failed={self.vote_failed} via={self.via_guided} | edges: "
                    f"{self.edges_downvoted} down / {self.edges_upvoted} upvoted "
                    f"(+{self.edges_cleared} cleared)")


async def counter_one(session, args, device, ride, vt_edges, vt_labels, graph,
                      vote_mode, gate, stats):
    """Bike-route one ride and downvote the overlap with its prior upvotes."""
    rid, start, end = ride
    n_up = sum(len(s) for s in vt_edges.values())
    all_edges = set().union(*vt_edges.values())

    if rid is None:
        # Reconstructed trip (no published ride): endpoints from the voted
        # path's own ends, routed BOTH ways; coverage is the intersection, so
        # the unknowable true direction can never mark a one-way stretch.
        rec = reconstructed_trip(graph, all_edges)
        if rec is None:
            await stats.bump(route_failed=1, edges_upvoted=n_up)
            return
        start, end, vias = rec
        g1 = await bike_route_geometry(session, args.bike_osrm_url, start, end, vias)
        g2 = await bike_route_geometry(session, args.bike_osrm_url, end, start,
                                       list(reversed(vias)))
        if g1 is None or g2 is None:
            await stats.bump(route_failed=1, edges_upvoted=n_up)
            return
        await stats.bump_quiet(via_guided=1, reconstructed=1)
        bike_set = (covered_edges(graph, all_edges, g1)
                    & covered_edges(graph, all_edges, g2))
    else:
        vias = voted_path_vias(graph, all_edges, start) if len(all_edges) >= 4 else []
        geom = await bike_route_geometry(session, args.bike_osrm_url, start, end, vias)
        if geom is None and vias:
            # A via may have snapped somewhere unroutable; the plain trip still
            # tells us which stretches a bike could have used.
            vias = []
            geom = await bike_route_geometry(session, args.bike_osrm_url, start, end, [])
        if geom is None:
            await stats.bump(route_failed=1, edges_upvoted=n_up)
            return
        if vias:
            await stats.bump_quiet(via_guided=1)
        bike_set = covered_edges(graph, all_edges, geom)

    async def cast(label, cast_edges, direction):
        """POST one directional cast; returns the response body or None."""
        payload = {
            "map": args.map_slug, "mode": vote_mode, "vote_type": label,
            "voter_id": rid or device, "edge_ids": cast_edges,
            "direction": direction, "ip_from_voter": True,
        }
        headers = {}
        if rid is None:
            # Act as the stored device directly (its voter_id preimage is
            # unpublished); requires the backend's admin token.
            payload["admin_device_id"] = device
            headers["X-Admin-Token"] = args.admin_token or ""
        for attempt in range(3):
            try:
                async with session.post(
                    f"{args.api_base}/api/vote", json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        if attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        return None
                    body = await resp.json()
                    return body if body.get("success") else None
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return None

    downs, zeroed, cleared = 0, 0, 0
    any_overlap = False
    ok = True
    for vt_id, edges in vt_edges.items():
        overlap = sorted(edges & bike_set)
        if not overlap:
            continue
        if args.flip:
            # Tally gate: never push a (vote_type, edge) tally below zero —
            # flip only edges that keep a positive tally, downgrade the last
            # upvote to a removal, and leave already-zeroed edges alone.
            flips, zeros = gate.take(vt_id, overlap)
        else:
            # Cancel mode (default): remove the device's OWN votes on the
            # covered blocks (direction 0). A removal only ever subtracts what
            # this device cast, so tallies can't go negative and no device is
            # ever counted "against" at block level — the correction erases
            # imported credit instead of manufacturing downvotes.
            flips, zeros = [], overlap
        if not flips and not zeros:
            continue
        any_overlap = True
        label = vt_labels.get(vt_id)
        if label is None:
            ok = False
            continue
        if args.dry_run:
            downs += len(flips)
            zeroed += len(zeros)
            continue
        if flips:
            body = await cast(label, flips, -1)
            if body is None:
                ok = False
            else:
                downs += len(flips)
                # Edges unvoted beyond the cast set: upvotes in touched
                # blocks that the bike route only partially covered.
                cleared += len(body.get("cleared", []))
        if zeros:
            body = await cast(label, zeros, 0)
            if body is None:
                ok = False
            else:
                zeroed += len(zeros)
                cleared += len(body.get("cleared", []))

    if not any_overlap:
        await stats.bump(no_overlap=1, edges_upvoted=n_up)
    elif ok:
        await stats.bump(countered=1, edges_upvoted=n_up, edges_downvoted=downs,
                         edges_zeroed=zeroed, edges_cleared=cleared)
    else:
        await stats.bump(vote_failed=1, edges_upvoted=n_up, edges_downvoted=downs,
                         edges_zeroed=zeroed, edges_cleared=cleared)


async def run_async(args, work, vt_labels, graph, vote_mode, gate) -> Stats:
    stats = Stats(len(work))
    sem = asyncio.Semaphore(args.concurrency)
    connector = aiohttp.TCPConnector(limit=args.concurrency * 2, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        async def throttled(device, ride, vt_edges):
            async with sem:
                await counter_one(session, args, device, ride, vt_edges,
                                  vt_labels, graph, vote_mode, gate, stats)

        tasks = [asyncio.create_task(throttled(d, r, v)) for d, r, v in work]
        await asyncio.gather(*tasks)
    return stats


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Downvote imported bikeshare votes where a bike route overlaps")
    parser.add_argument("--city", required=True,
                        help=f"City id ({', '.join(LYFT_SOURCES)})")
    parser.add_argument("--map", dest="map_slug", required=True,
                        help="Map slug holding the imported votes (e.g. nyc-bikes)")
    parser.add_argument("--mode", help="Vote namespace; defaults to the map's mode")
    parser.add_argument("--bike-osrm-url", default="http://localhost:5006",
                        help="Bicycle-profile OSRM (scripts/build_bike_osrm.sh)")
    parser.add_argument("--api-base", default="http://localhost:8080",
                        help="City Edit API base URL")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--graph-dir",
                        help="Directory with the TARGET deployment's "
                             "walk_graph_arrays.npz. Required when countering a "
                             "remote backend: DB edge ids live in that "
                             "deployment's topology space, so vias/coverage must "
                             "use its graph, not the local build (extract with "
                             "docker cp from the serving image).")
    parser.add_argument("--limit", type=int,
                        help="Counter at most N rides (smoke runs)")
    parser.add_argument("--reconstruct-unmatched", action="store_true",
                        help="Also counter import-marked devices whose ride ids "
                             "match no cached month: endpoints come from the "
                             "voted path's own ends, routed both directions "
                             "with coverage intersected. Needs --admin-token "
                             "(casts act as the stored device directly).")
    parser.add_argument("--only-unmatched", action="store_true",
                        help="With --reconstruct-unmatched: process ONLY the "
                             "reconstructed devices (skip re-analyzing rides "
                             "already countered by a prior matched pass).")
    parser.add_argument("--admin-token", default=os.environ.get("ADMIN_TOKEN"),
                        help="Backend ADMIN_TOKEN for --reconstruct-unmatched")
    parser.add_argument("--flip", action="store_true",
                        help="Legacy behavior: cast direction=-1 flips (gated "
                             "by the running tally so no (vote_type, edge) "
                             "goes negative) instead of the default "
                             "direction=0 cancellation. Flips still mark "
                             "devices 'against' at block level; prefer the "
                             "default.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute overlaps and report; cast nothing")
    args = parser.parse_args()
    args.api_base = args.api_base.rstrip("/")
    args.bike_osrm_url = args.bike_osrm_url.rstrip("/")

    city = get_city(args.city)
    if not city or args.city not in LYFT_SOURCES:
        sys.exit(f"ERROR: unknown or unsupported city '{args.city}'")

    # Bike OSRM must be up before we spend minutes on DB/zip scans.
    try:
        requests.get(f"{args.bike_osrm_url}/route/v1/cycling/"
                     f"{city.center[1]},{city.center[0]};"
                     f"{city.center[1]},{city.center[0]}", timeout=10)
    except requests.RequestException:
        sys.exit(f"ERROR: bike OSRM not reachable at {args.bike_osrm_url}. "
                 f"Run scripts/build_bike_osrm.sh {args.city} --serve first.")

    log(f"Loading upvotes for map '{args.map_slug}' ...")
    votes, marked = load_upvotes(args.map_slug)
    log(f"  {len(votes)} devices with upvotes ({len(marked)} import-marked)")
    if args.flip:
        gate = NetGate(load_net_counts(args.map_slug))
        log(f"  tally gate over {len(gate.net)} (vote_type, edge) pairs")
    else:
        gate = NetGate({})  # cancel mode removes own votes only; no gate needed

    log("Matching devices against cached ride zips ...")
    matched = match_rides(args.city, set(votes))
    reconstruct = sorted(marked - set(matched)) if args.reconstruct_unmatched else []
    unmatched = len(votes) - len(matched) - len(reconstruct)
    log(f"  {len(matched)} imported rides matched, {len(reconstruct)} import-marked "
        f"but unpublished (reconstructing), {unmatched} left alone")
    if not matched and not reconstruct:
        sys.exit("ERROR: no imported rides matched. Wrong --city/--map, or the "
                 "monthly zips the import used are no longer in lyft_data/.")
    if reconstruct and not args.dry_run and not args.admin_token:
        sys.exit("ERROR: --reconstruct-unmatched needs --admin-token (or ADMIN_TOKEN env)")

    vt_ids = {vt for dev in list(matched) + reconstruct for vt in votes[dev]}
    vt_labels = load_vote_type_labels(vt_ids)
    log(f"  vote types to counter: "
        f"{', '.join(f'{i}={vt_labels.get(i)!r}' for i in sorted(vt_ids))}")

    log(f"Loading {args.city} votable graph "
        f"({args.graph_dir or city.data_dir}) ...")
    graph = CityGraph(city)
    if args.graph_dir:
        graph.provider = PythonRouter(data_dir=args.graph_dir)
    graph.ensure_loaded()
    log(f"  {graph.n_nodes} nodes / {graph.n_edges} edges")

    vote_mode = None
    if not args.dry_run:
        if not health_check(args.api_base):
            sys.exit(f"ERROR: backend not healthy at {args.api_base}")
        vote_mode = args.mode or fetch_map_mode(args.api_base, args.map_slug)
        if not vote_mode:
            sys.exit(f"ERROR: could not resolve mode for map '{args.map_slug}'; "
                     "pass --mode explicitly.")
        log(f"Target: map={args.map_slug} mode={vote_mode} via {args.api_base}")

    work = ([] if args.only_unmatched else
            [(dev, matched[dev], votes[dev]) for dev in sorted(matched)])
    work += [(dev, (None, None, None), votes[dev]) for dev in reconstruct]
    if args.limit:
        work = work[:args.limit]
    log(f"{'DRY RUN: analyzing' if args.dry_run else 'Counter-voting'} "
        f"{len(work)} rides (concurrency={args.concurrency}) ...")

    t0 = time.time()
    stats = asyncio.run(run_async(args, work, vt_labels, graph, vote_mode, gate))
    log(f"Done in {time.time() - t0:.1f}s")
    log(f"  Rides processed:     {stats.processed}")
    log(f"  Via-guided routes:   {stats.via_guided} "
        f"(rest routed endpoint-to-endpoint)")
    log(f"  Reconstructed trips: {stats.reconstructed} "
        f"(both-direction coverage intersection)")
    log(f"  Countered:           {stats.countered}")
    log(f"  No overlap:          {stats.no_overlap} (fully divergent — left alone)")
    log(f"  Route failed:        {stats.route_failed} (left alone)")
    log(f"  Vote failed:         {stats.vote_failed}")
    log(f"  Edges upvoted:       {stats.edges_upvoted}")
    log(f"  Edges downvoted:     {stats.edges_downvoted} "
        f"({100 * stats.edges_downvoted / max(1, stats.edges_upvoted):.1f}% of upvotes)")
    log(f"  Edges zeroed:        {stats.edges_zeroed} "
        f"(last upvote removed instead of flipped — tally floor)")
    log(f"  Edges cleared:       {stats.edges_cleared} (partial-block clears)")
    if args.dry_run:
        log("  (dry run — nothing cast)")


if __name__ == "__main__":
    main()
