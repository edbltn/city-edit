"""
Build the votable walk graph from the SAME OSM PBF that feeds OSRM.

OSRM routes the paths users vote on; this module builds the topology graph those
votes map onto. To guarantee every routed node/edge is a votable entity, we read
the same per-city PBF (the city's `pbf_url`) and keep every way OSRM's foot profile
would route (foot_profile.way_is_foot_routable, a mirror of osrm/foot.lua), so the
topology graph is a SUPERSET of OSRM's foot network. Nodes are keyed by OSM node id
— the shared key OSRM returns via `annotations=nodes` — so vote_store.osm_nodes_to_edge_ids
resolves cleanly.

Emits a networkx.MultiDiGraph with the exact shape python_router._ensure_loaded
expects: node attrs y=lat, x=lon; edge attrs length (m), name, highway.
"""
import logging
import math
import os
import time
from pathlib import Path

import networkx as nx
import requests

from foot_profile import way_is_foot_routable

logger = logging.getLogger(__name__)

try:
    import osmium
    HAS_OSMIUM = True
except ImportError as e:  # pragma: no cover
    HAS_OSMIUM = False
    osmium = None
    logger.warning(f"pyosmium not available: {e}")

# osmnx's earth radius, so edge lengths match the previous build's units.
_EARTH_RADIUS_M = 6371008.8


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two lat/lon points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return _EARTH_RADIUS_M * 2 * math.asin(math.sqrt(a))


# bbbike.org reliably refuses connections from datacenter IP ranges (e.g. Cloud
# Build), where it just connect-times-out. Each bbbike city extract therefore has a
# geofabrik regional fallback: the builder clips to the city bbox, so a regional
# extract yields the SAME graph — only the download is larger. geofabrik does not
# block automated clients, so it's the reliable source for CI image builds.
_PBF_FALLBACKS = {
    "https://download.bbbike.org/osm/bbbike/SanFrancisco/SanFrancisco.osm.pbf":
        "https://download.geofabrik.de/north-america/us/california-latest.osm.pbf",
    "https://download.bbbike.org/osm/bbbike/Chicago/Chicago.osm.pbf":
        "https://download.geofabrik.de/north-america/us/illinois-latest.osm.pbf",
    "https://download.bbbike.org/osm/bbbike/WashingtonDC/WashingtonDC.osm.pbf":
        "https://download.geofabrik.de/north-america/us/district-of-columbia-latest.osm.pbf",
}

# (connect, read) timeouts: a short connect timeout fails fast over to the fallback
# when a host is silently dropping connections; the read window stays generous for
# large (~1GB) regional extracts.
_PBF_TIMEOUT = (30, 600)
_PBF_ATTEMPTS = 3


def _stream_pbf(url: str, tmp: Path) -> None:
    """Stream one PBF URL to a temp file (raises on any HTTP/network error)."""
    with requests.get(url, stream=True, timeout=_PBF_TIMEOUT) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)


def ensure_pbf(pbf_url: str, dest_dir: str, force: bool = False) -> str:
    """Download the source PBF into dest_dir/source.osm.pbf (cached). Returns the path.

    Mirrors the freshness-skip pattern used elsewhere: skip the download if the file
    already exists unless force=True. Streams to a .part file and renames atomically
    so an interrupted download never leaves a truncated PBF that looks complete. Tries
    the primary URL, then any geofabrik fallback (see _PBF_FALLBACKS), retrying each
    with backoff so a flaky/blocking mirror doesn't fail the whole image build.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    pbf_path = dest / "source.osm.pbf"

    if pbf_path.exists() and not force:
        logger.info(f"[PBF] cached: {pbf_path} ({pbf_path.stat().st_size / 1e6:.0f} MB)")
        return str(pbf_path)

    tmp = pbf_path.with_suffix(".pbf.part")
    urls = [pbf_url]
    if pbf_url in _PBF_FALLBACKS:
        urls.append(_PBF_FALLBACKS[pbf_url])

    last_err: Exception | None = None
    for url in urls:
        for attempt in range(1, _PBF_ATTEMPTS + 1):
            try:
                logger.info(f"[PBF] downloading {url} -> {pbf_path} (attempt {attempt})")
                _stream_pbf(url, tmp)
                os.replace(tmp, pbf_path)
                logger.info(f"[PBF] downloaded: {pbf_path} "
                            f"({pbf_path.stat().st_size / 1e6:.0f} MB)")
                return str(pbf_path)
            except Exception as e:
                last_err = e
                logger.warning(f"[PBF] download failed ({url}, attempt {attempt}): {e}")
                tmp.unlink(missing_ok=True)
                if attempt < _PBF_ATTEMPTS:
                    time.sleep(min(30, 5 * attempt))
        if url != urls[-1]:
            logger.warning(f"[PBF] giving up on {url}; trying fallback")

    raise RuntimeError(f"[PBF] all sources failed for {pbf_url}: {last_err}")


def build_walk_graph_from_pbf(pbf_path: str, bbox: tuple) -> "nx.MultiDiGraph":
    """Read a PBF and build the foot-routable walk graph clipped to bbox.

    bbox = (south, west, north, east). A way is included if it's foot-routable AND
    has at least one node inside the bbox (boundary-crossing ways are kept whole,
    matching the old osmnx truncate_by_edge=True). Every consecutive node pair on an
    accepted way becomes a bidirectional edge (foot ignores general oneway), so the
    granularity matches the old simplify=False build and OSRM's per-node annotations.
    """
    if not HAS_OSMIUM:
        raise RuntimeError(
            "pyosmium not available. Install with: uv pip install osmium"
        )

    south, west, north, east = bbox

    def _in_bbox(lat: float, lon: float) -> bool:
        return south <= lat <= north and west <= lon <= east

    class _WalkHandler(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.G = nx.MultiDiGraph()
            self.ways_seen = 0
            self.ways_kept = 0

        def way(self, w):
            self.ways_seen += 1
            tags = {t.k: t.v for t in w.tags}
            if not way_is_foot_routable(tags):
                return

            # Resolve node coords (locations=True populates n.location). Keep the way
            # only if it touches the bbox; otherwise skip to bound memory for big
            # extracts (the whole NY-state PBF for NYC).
            pts = []  # (osm_id, lat, lon)
            touches = False
            for n in w.nodes:
                loc = n.location
                if not loc.valid():
                    pts.append(None)  # gap: break the edge chain here
                    continue
                lat, lon = loc.lat, loc.lon
                pts.append((n.ref, lat, lon))
                if _in_bbox(lat, lon):
                    touches = True
            if not touches:
                return

            self.ways_kept += 1
            name = tags.get("name", "") or ""
            highway = tags.get("highway", "") or ""
            G = self.G
            prev = None
            for pt in pts:
                if pt is None:
                    prev = None
                    continue
                nid, lat, lon = pt
                if nid not in G:
                    G.add_node(nid, y=lat, x=lon)
                if prev is not None:
                    pid, plat, plon = prev
                    d = _haversine_m(plat, plon, lat, lon)
                    # Bidirectional: foot.lua ignores general oneway (only oneway:foot).
                    G.add_edge(pid, nid, length=d, name=name, highway=highway)
                    G.add_edge(nid, pid, length=d, name=name, highway=highway)
                prev = pt

    handler = _WalkHandler()
    # locations=True does the way->node coordinate join in C++ via the flex_mem index
    # (auto-sized; the NodeLocationsForWays equivalent), so we never hold all nodes
    # in Python.
    handler.apply_file(pbf_path, locations=True, idx="flex_mem")

    logger.info(
        f"[GRAPH] PBF build: kept {handler.ways_kept}/{handler.ways_seen} ways, "
        f"{handler.G.number_of_nodes()} nodes, {handler.G.number_of_edges()} directed edges"
    )
    return handler.G
