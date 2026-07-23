"""
Build PMTiles file from the OSM routing graph.

Generates MVT (Mapbox Vector Tile) encoded tiles for client-side rendering
with MapLibre GL JS. Two layers per tile: "edges" (LineString) and "nodes" (Point).

Edge feature ids are the STABLE eids from edge_registry.py (the registry is
applied/updated before tiling), so tiles, topology, and votes all agree on
edge identity. Rebuild these tiles whenever the walk graph is rebuilt.

Usage:
    python build_pmtiles.py --city nyc [--output osm_data/nyc/graph.pmtiles]
"""

import json
import logging
import os
import sys

import mapbox_vector_tile
import mercantile
from pmtiles.tile import zxy_to_tileid, TileType, Compression
from pmtiles.writer import Writer

from cities import get_city
from edge_registry import apply_edge_registry
from python_router import PythonRouter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MIN_ZOOM = 12
MAX_ZOOM = 16


def get_all_nodes_edges(city_id: str):
    """City graph with the stable-eid registry applied (index == eid)."""
    city = get_city(city_id)
    if not city:
        raise SystemExit(f"Unknown city: {city_id}")
    router = PythonRouter(city.data_dir)
    south, west, north, east = city.bbox
    data = router.get_graph_for_bbox(south, west, north, east)
    apply_edge_registry(city.data_dir, data, persist=True)
    bbox = (west, south, east, north)
    return data.get("nodes", []), data.get("edges", []), bbox


def build_pmtiles(city_id: str, output_path: str):
    """Build PMTiles file with MVT-encoded tiles from graph data."""
    logger.info(f"Building PMTiles for {city_id}: {output_path}")

    nodes, edges, bbox = get_all_nodes_edges(city_id)
    logger.info(f"Got {len(nodes)} nodes and {len(edges)} edges")

    node_coords = {i: (lat, lon) for i, (lat, lon) in enumerate(nodes)}

    # Collect features per tile: (z, x, y) -> {"edges": [...], "nodes": [...]}
    tiles = {}

    # Process nodes
    for node_idx, (lat, lon) in enumerate(nodes):
        for zoom in range(MIN_ZOOM, MAX_ZOOM + 1):
            tile = mercantile.tile(lon, lat, zoom)
            key = (zoom, tile.x, tile.y)
            if key not in tiles:
                tiles[key] = {"edges": [], "nodes": []}

            tiles[key]["nodes"].append({
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"node_id": node_idx},
                "id": node_idx,
            })

    # Process edges — feature id == eid. Tombstones (retired eids) and
    # self-loops have no drawable geometry and are skipped.
    for edge_idx, edge in enumerate(edges):
        from_idx, to_idx = edge[0], edge[1]
        if from_idx == to_idx:
            continue
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

        minx = min(from_lon, to_lon)
        maxx = max(from_lon, to_lon)
        miny = min(from_lat, to_lat)
        maxy = max(from_lat, to_lat)

        for zoom in range(MIN_ZOOM, MAX_ZOOM + 1):
            min_tile = mercantile.tile(minx, miny, zoom)
            max_tile = mercantile.tile(maxx, maxy, zoom)

            for x in range(min_tile.x, max_tile.x + 1):
                for y in range(min_tile.y, max_tile.y + 1):
                    key = (zoom, x, y)
                    if key not in tiles:
                        tiles[key] = {"edges": [], "nodes": []}
                    tiles[key]["edges"].append(feature)

    # Encode tiles as MVT and write to PMTiles
    logger.info(f"Encoding {len(tiles)} tiles as MVT")

    if os.path.exists(output_path):
        os.remove(output_path)

    with open(output_path, "wb") as f:
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
            tileid = zxy_to_tileid(z, x, y)
            writer.write_tile(tileid, tile_data)

        writer.finalize(
            header={
                "tile_type": TileType.MVT,
                "tile_compression": Compression.NONE,
                "min_zoom": MIN_ZOOM,
                "max_zoom": MAX_ZOOM,
                "min_lon_e7": int(bbox[0] * 1e7),
                "min_lat_e7": int(bbox[1] * 1e7),
                "max_lon_e7": int(bbox[2] * 1e7),
                "max_lat_e7": int(bbox[3] * 1e7),
            },
            metadata={
                "name": "desire-path-graph",
                "description": "OSM walk graph for city edit",
                "format": "pbf",
                "type": "overlay",
                "minzoom": str(MIN_ZOOM),
                "maxzoom": str(MAX_ZOOM),
                "bounds": ",".join(str(b) for b in bbox),
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
                    {
                        "id": "nodes",
                        "fields": {"node_id": "Number"},
                    },
                ]),
            },
        )

    file_size = os.path.getsize(output_path)
    logger.info(f"PMTiles built: {output_path} ({file_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Build PMTiles from OSM graph")
    parser.add_argument("--city", default="nyc", help="City id (see cities.py)")
    parser.add_argument("--output", "-o", default=None,
                        help="Output path (default: osm_data/<city>/graph.pmtiles)")
    args = parser.parse_args()
    out = args.output or os.path.join("osm_data", args.city, "graph.pmtiles")
    build_pmtiles(args.city, out)
