"""
Build PMTiles file from the OSM routing graph.

Generates MVT (Mapbox Vector Tile) encoded tiles for client-side rendering
with MapLibre GL JS. Two layers per tile: "edges" (LineString) and "nodes" (Point).

Usage:
    python build_pmtiles.py [--output graph.pmtiles]
"""

import json
import logging
import os
import sys

import mapbox_vector_tile
import mercantile
from pmtiles.tile import zxy_to_tileid, TileType, Compression
from pmtiles.writer import Writer

from python_router import PythonRouter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Mapped region bounds (matches client config.ts mappedBounds)
DEFAULT_BBOX = (-74.03069, 40.70121, -73.90752, 40.87043)  # west, south, east, north
MIN_ZOOM = 12
MAX_ZOOM = 16


def get_all_nodes_edges():
    """Get all nodes and edges from the graph."""
    router = PythonRouter("osm_data")
    west, south, east, north = DEFAULT_BBOX
    data = router.get_graph_for_bbox(south, west, north, east)
    return data.get("nodes", []), data.get("edges", [])


def build_pmtiles(output_path: str = "graph.pmtiles"):
    """Build PMTiles file with MVT-encoded tiles from graph data."""
    logger.info(f"Building PMTiles: {output_path}")

    nodes, edges = get_all_nodes_edges()
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

    # Process edges
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
                "min_lon_e7": int(DEFAULT_BBOX[0] * 1e7),
                "min_lat_e7": int(DEFAULT_BBOX[1] * 1e7),
                "max_lon_e7": int(DEFAULT_BBOX[2] * 1e7),
                "max_lat_e7": int(DEFAULT_BBOX[3] * 1e7),
            },
            metadata={
                "name": "desire-path-graph",
                "description": "OSM walk graph for desire path mapper",
                "format": "pbf",
                "type": "overlay",
                "minzoom": str(MIN_ZOOM),
                "maxzoom": str(MAX_ZOOM),
                "bounds": ",".join(str(b) for b in DEFAULT_BBOX),
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
    parser.add_argument("--output", "-o", default="graph.pmtiles", help="Output path")
    args = parser.parse_args()
    build_pmtiles(args.output)
