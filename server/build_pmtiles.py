"""
Build per-city PMTiles from each city's OSM walk graph.

This is the SINGLE canonical tile builder, invoked identically by every build
pipeline (the prod Dockerfile, the docker-compose osm-refresh sidecar, and the
local `make tiles` / `make dev` targets). It reads a city's bbox from cities.py
and the same walk_graph.pkl the routing/topology code uses, so the background
network rendered by MapLibreBackground is derived from one source everywhere —
no more "prod has a denser graph than local" drift.

Output: osm_data/<city>/graph.pmtiles, served at /api/tiles/<city>/graph.pmtiles.

Resolution is a build-time *profile*, not a coverage difference: every profile
covers the city's full bbox, but a smaller zoom band means fewer tiles to encode
(faster build, smaller file). MapLibre overzooms past the max zoom, so a "dev"
build still renders when zoomed in — just with coarser tile boundaries.

  dev  (z12-14)  — local default: quick to build, full-city coverage
  full (z12-16)  — prod default: crisp tiles deep into the zoom range

Usage:
    python build_pmtiles.py --all                 # every city, dev profile
    python build_pmtiles.py --all --profile full  # every city, full profile (prod)
    python build_pmtiles.py --city sf             # one city
    python build_pmtiles.py --city nyc --min-zoom 12 --max-zoom 15  # explicit band
    python build_pmtiles.py --city nyc --force    # rebuild even if up to date
"""

import argparse
import json
import logging
import os
import sys

import mapbox_vector_tile
import mercantile
from pmtiles.tile import zxy_to_tileid, TileType, Compression
from pmtiles.writer import Writer

from cities import CITIES, City, get_city
from python_router import PythonRouter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Resolution profiles: (min_zoom, max_zoom). Coverage is always the full city
# bbox; only the zoom band (and thus build cost / file size) differs.
PROFILES: dict[str, tuple[int, int]] = {
    "dev": (12, 14),
    "full": (12, 16),
}


def _output_path_for(city: City) -> str:
    return os.path.join(city.data_dir, "graph.pmtiles")


def _is_up_to_date(city: City, output_path: str) -> bool:
    """True if a tile file already exists and is newer than the walk graph.

    Lets `make dev` and repeated builds skip the expensive encode when nothing
    upstream changed. `--force` bypasses this.
    """
    graph_path = os.path.join(city.data_dir, "walk_graph.pkl")
    if not os.path.exists(output_path) or not os.path.exists(graph_path):
        return False
    return os.path.getmtime(output_path) >= os.path.getmtime(graph_path)


def _collect_tiles(nodes, edges, min_zoom, max_zoom, include_nodes):
    """Bucket graph features into MVT tiles. Returns {(z, x, y): {edges, nodes}}.

    Edges are the only layer MapLibreBackground renders, so nodes are off by
    default — building them for a full-city graph is a large cost for a layer
    nothing draws.
    """
    node_coords = {i: (lat, lon) for i, (lat, lon) in enumerate(nodes)}
    tiles: dict[tuple[int, int, int], dict] = {}

    if include_nodes:
        for node_idx, (lat, lon) in enumerate(nodes):
            for zoom in range(min_zoom, max_zoom + 1):
                tile = mercantile.tile(lon, lat, zoom)
                key = (zoom, tile.x, tile.y)
                bucket = tiles.setdefault(key, {"edges": [], "nodes": []})
                bucket["nodes"].append({
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": {"node_id": node_idx},
                    "id": node_idx,
                })

    for edge_idx, edge in enumerate(edges):
        from_idx, to_idx = edge[0], edge[1]
        name = edge[2] if len(edge) > 2 else ""
        highway = edge[3] if len(edge) > 3 else ""

        from_lat, from_lon = node_coords[from_idx]
        to_lat, to_lon = node_coords[to_idx]

        feature = {
            "geometry": {
                "type": "LineString",
                "coordinates": [[from_lon, from_lat], [to_lon, to_lat]],
            },
            "properties": {
                "name": name or "",
                "highway": highway or "",
                "from_idx": from_idx,
                "to_idx": to_idx,
            },
            "id": edge_idx,
        }

        minx, maxx = min(from_lon, to_lon), max(from_lon, to_lon)
        miny, maxy = min(from_lat, to_lat), max(from_lat, to_lat)

        for zoom in range(min_zoom, max_zoom + 1):
            min_tile = mercantile.tile(minx, miny, zoom)
            max_tile = mercantile.tile(maxx, maxy, zoom)
            for x in range(min_tile.x, max_tile.x + 1):
                for y in range(min_tile.y, max_tile.y + 1):
                    key = (zoom, x, y)
                    bucket = tiles.setdefault(key, {"edges": [], "nodes": []})
                    bucket["edges"].append(feature)

    return tiles


def build_city_pmtiles(
    city: City,
    min_zoom: int,
    max_zoom: int,
    output_path: str | None = None,
    include_nodes: bool = False,
) -> str:
    """Build one city's graph.pmtiles. Returns the output path."""
    output_path = output_path or _output_path_for(city)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    logger.info(
        f"[{city.id}] Building PMTiles (z{min_zoom}-{max_zoom}, "
        f"nodes={'on' if include_nodes else 'off'}) -> {output_path}"
    )

    # Same source the topology API / router use: this city's walk graph, full bbox.
    router = PythonRouter(city.data_dir)
    south, west, north, east = city.bbox
    data = router.get_graph_for_bbox(south, west, north, east)
    nodes, edges = data.get("nodes", []), data.get("edges", [])
    logger.info(f"[{city.id}] {len(nodes)} nodes, {len(edges)} edges in bbox")

    tiles = _collect_tiles(nodes, edges, min_zoom, max_zoom, include_nodes)
    logger.info(f"[{city.id}] Encoding {len(tiles)} tiles as MVT")

    # Write to a temp file and atomically rename, so an interrupted/failed build
    # never leaves a partial graph.pmtiles that's newer than the graph (which the
    # freshness check would then wrongly skip).
    tmp_path = output_path + ".tmp"
    w, s, e, n = west, south, east, north
    with open(tmp_path, "wb") as f:
        writer = Writer(f)
        for (z, x, y), layers in sorted(tiles.items()):
            bounds = mercantile.bounds(mercantile.Tile(x, y, z))
            qb = (bounds.west, bounds.south, bounds.east, bounds.north)

            mvt_layers = []
            if layers["edges"]:
                mvt_layers.append({"name": "edges", "features": layers["edges"]})
            if layers["nodes"]:
                mvt_layers.append({"name": "nodes", "features": layers["nodes"]})

            tile_data = mapbox_vector_tile.encode(mvt_layers, quantize_bounds=qb)
            writer.write_tile(zxy_to_tileid(z, x, y), tile_data)

        writer.finalize(
            header={
                "tile_type": TileType.MVT,
                "tile_compression": Compression.NONE,
                "min_zoom": min_zoom,
                "max_zoom": max_zoom,
                "min_lon_e7": int(w * 1e7),
                "min_lat_e7": int(s * 1e7),
                "max_lon_e7": int(e * 1e7),
                "max_lat_e7": int(n * 1e7),
            },
            metadata={
                "name": f"desire-path-graph-{city.id}",
                "description": f"OSM walk graph for {city.name}",
                "format": "pbf",
                "type": "overlay",
                "minzoom": str(min_zoom),
                "maxzoom": str(max_zoom),
                "bounds": ",".join(str(b) for b in (w, s, e, n)),
                "vector_layers": json.dumps([
                    {
                        "id": "edges",
                        "fields": {
                            "name": "String",
                            "highway": "String",
                            "from_idx": "Number",
                            "to_idx": "Number",
                        },
                    },
                    {"id": "nodes", "fields": {"node_id": "Number"}},
                ]),
            },
        )

    os.replace(tmp_path, output_path)
    size_mb = os.path.getsize(output_path) / 1024 / 1024
    logger.info(f"[{city.id}] PMTiles built: {output_path} ({size_mb:.1f} MB)")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Build per-city PMTiles from the OSM graph")
    parser.add_argument("--city", help="City id to build (default: nyc unless --all)")
    parser.add_argument("--all", action="store_true", help="Build every registered city")
    parser.add_argument(
        "--profile", choices=sorted(PROFILES), default="dev",
        help="Resolution profile (default: dev). dev=z12-14, full=z12-16.",
    )
    parser.add_argument("--min-zoom", type=int, help="Override profile min zoom")
    parser.add_argument("--max-zoom", type=int, help="Override profile max zoom")
    parser.add_argument("--nodes", action="store_true", help="Also emit the (unused) nodes layer")
    parser.add_argument("--force", action="store_true", help="Rebuild even if up to date")
    parser.add_argument("-o", "--output", help="Explicit output path (single city only)")
    args = parser.parse_args()

    min_zoom, max_zoom = PROFILES[args.profile]
    if args.min_zoom is not None:
        min_zoom = args.min_zoom
    if args.max_zoom is not None:
        max_zoom = args.max_zoom

    if args.all:
        targets = list(CITIES.values())
    else:
        city = get_city(args.city or "nyc")
        if city is None:
            logger.error(f"Unknown city '{args.city}'. Known: {list(CITIES)}")
            sys.exit(1)
        targets = [city]

    if args.output and len(targets) != 1:
        logger.error("--output can only be used when building a single city")
        sys.exit(1)

    for city in targets:
        out = args.output or _output_path_for(city)
        if not args.force and _is_up_to_date(city, out):
            logger.info(f"[{city.id}] tiles up to date, skipping (use --force to rebuild)")
            continue
        try:
            build_city_pmtiles(city, min_zoom, max_zoom, out, include_nodes=args.nodes)
        except Exception as e:
            logger.error(f"[{city.id}] PMTiles build failed: {e}")
            if not args.all:
                sys.exit(1)


if __name__ == "__main__":
    main()
