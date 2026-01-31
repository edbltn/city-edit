# Roam: Multi-Modal Routing Engine

**Roam** ("Freedom to Roam") is a custom multi-modal routing algorithm that treats the city as unified traversable public space.

---

## Table of Contents

1. [Design Philosophy](#design-philosophy)
2. [Design Decisions & Rationale](#design-decisions--rationale)
3. [Architecture](#architecture)
4. [Tile System](#tile-system)
5. [Portal Edges](#portal-edges)
6. [Path Caching](#path-caching)
7. [Daily Refresh](#daily-refresh)
8. [Implementation Checklist](#implementation-checklist)

---

## Design Philosophy

Roam operates under the "freedom to roam" hypothesis: **all walkable, bikeable, and driveable surfaces are public land**. This philosophical stance has practical implications:

1. **One-way streets are ignored** - If you can physically traverse a path (walk against traffic, bike on a sidewalk), Roam allows it
2. **Mode switching is frictionless** - No artificial penalty for transitioning between walk/bike/drive
3. **Desire paths emerge** - By computing optimal routes unconstrained by regulations, we identify where infrastructure should be built

The goal is to reveal where footbridges, bike lanes, and pedestrian crossings would have the greatest impact.

---

## Design Decisions & Rationale

### 1. Lat-Lon Degree Grid (not Web Mercator)

**Decision**: Use a simple lat-lon degree-based tile system instead of Web Mercator (z/x/y).

**Rationale**:
- **Human-readable tile IDs**: A tile ID like `40.710_-74.005` immediately tells you it's in Lower Manhattan. Compare to `z16_19294_24642` which requires computation to interpret.
- **Easy debugging**: When a route fails, you can visually locate problem tiles on a map just by reading the coordinates.
- **Simple math**: No projection formulas needed. `tile = floor(coord / tile_size) * tile_size`.
- **Intuitive neighbor lookup**: North neighbor is `tile_lat + 0.005`. No edge-case handling for tile wrapping.

**Trade-off**: Tiles are slightly non-square (latitude degrees are ~111km, longitude degrees are ~85km at NYC's latitude). This is acceptable for our caching use case.

### 2. Tile Size: 0.005° (~500m)

**Decision**: Each tile spans 0.005 degrees in both latitude and longitude.

**Calculation at NYC latitude (40.7°N)**:
- **Latitude**: 0.005° × 111 km/° ≈ **555 meters**
- **Longitude**: 0.005° × 111 km/° × cos(40.7°) ≈ 0.005 × 111 × 0.758 ≈ **421 meters**

**Rationale**:
- ~500m tiles create ~1,600 tiles for the NYC bounding box (manageable)
- Large enough that most short routes stay within one tile (cache efficiency)
- Small enough that cache invalidation doesn't wipe too much data

**NYC Coverage**:
```
Bounding box: (40.49, -74.26) to (40.92, -73.70)
Lat range: 0.43° → 86 tiles
Lon range: 0.56° → 112 tiles
Total: ~9,600 tiles
```

### 3. Portal Distance: 0.001° (~111m)

**Decision**: Create "portal edges" between nodes in different mode graphs that are within 0.001° of each other.

**Calculation**:
- 0.001° latitude ≈ 111 meters
- 0.001° longitude ≈ 85 meters at NYC latitude
- Maximum diagonal distance: √(111² + 85²) ≈ **140 meters**

**Rationale**:
- Matches the existing `hybrid_router.py` threshold of 100m
- Generous enough to find connections between mode networks
- Tight enough to avoid creating unrealistic shortcuts

**Why degrees instead of meters**:
- **Consistency**: Same unit system as tiles
- **Fast comparison**: No haversine calculation needed, just `abs(lat1 - lat2) < 0.001`
- **Readable thresholds**: 0.001° is easy to reason about (roughly one city block)

### 4. NYC Only

**Decision**: Hardcode the NYC bounding box rather than making the system city-agnostic.

**Rationale**:
- **Data quality**: NYC has exceptionally rich OSM data due to active community mappers
- **Scope control**: Building a working system for one city before abstracting
- **Memory constraints**: Each city's graph requires 2-5 GB; multi-city would require dynamic loading
- **Proves concept**: If Roam works well for NYC, abstraction to other cities is straightforward

**Future extension**: Replace `NYC_BBOX` constant with a config file; add lazy graph loading per city.

---

## Architecture

### Component Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      RoamRouter                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Unified Super-Graph                     │   │
│  │   Walk nodes ←→ Portal edges ←→ Bike nodes          │   │
│  │   Bike nodes ←→ Portal edges ←→ Drive nodes         │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Tile System                            │
│   coords_to_tile() │ tile_to_bbox() │ get_tile_nodes()     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Path Cache (Redis)                     │
│   roam:path:{from_tile}:{to_tile} → cached route data      │
│   roam:tile_paths:{tile_id} → set of affected path keys    │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Request arrives**: `POST /api/routes/roam` with start/end coordinates
2. **Tile lookup**: Convert coordinates to tile IDs
3. **Cache check**: Look for `roam:path:{start_tile}:{end_tile}`
4. **Cache hit**: Stitch cached path with local routing to exact endpoints
5. **Cache miss**: Run A* on super-graph, cache result for future queries
6. **Response**: Return GeoJSON with mode annotations per segment

---

## Tile System

### Tile ID Format

Each tile is identified by its **southwest corner** coordinates:

```
Format: "{lat:.3f}_{lon:.3f}"
Example: "40.710_-74.005"
```

**Why southwest corner**:
- Consistent convention (like page numbering from bottom-left)
- Matches typical lat-lon ordering (latitude first)
- Tile "contains" all points with lat >= tile_lat and lat < tile_lat + 0.005

### Coordinate to Tile Conversion

```python
TILE_SIZE = 0.005  # degrees

def coords_to_tile(lat: float, lon: float) -> str:
    """Convert coordinates to tile ID (southwest corner)."""
    tile_lat = math.floor(lat / TILE_SIZE) * TILE_SIZE
    tile_lon = math.floor(lon / TILE_SIZE) * TILE_SIZE
    return f"{tile_lat:.3f}_{tile_lon:.3f}"
```

**Examples**:
| Coordinate | Tile ID | Location |
|------------|---------|----------|
| (40.7128, -74.0060) | `40.710_-74.010` | Financial District |
| (40.7580, -73.9855) | `40.755_-73.990` | Times Square |
| (40.6892, -74.0445) | `40.685_-74.045` | Statue of Liberty |

### Tile Bounds

```python
def tile_to_bbox(tile_id: str) -> tuple[float, float, float, float]:
    """Return (min_lat, min_lon, max_lat, max_lon) for tile."""
    tile_lat, tile_lon = map(float, tile_id.split("_"))
    return (
        tile_lat,
        tile_lon,
        tile_lat + TILE_SIZE,
        tile_lon + TILE_SIZE
    )
```

### NYC Tile Coverage

```python
NYC_BBOX = {
    "south": 40.49, "north": 40.92,
    "west": -74.26, "east": -73.70
}

def get_all_nyc_tiles() -> list[str]:
    """Generate all tile IDs covering NYC."""
    tiles = []
    lat = NYC_BBOX["south"]
    while lat < NYC_BBOX["north"]:
        lon = NYC_BBOX["west"]
        while lon < NYC_BBOX["east"]:
            tiles.append(f"{lat:.3f}_{lon:.3f}")
            lon += TILE_SIZE
        lat += TILE_SIZE
    return tiles  # ~9,600 tiles
```

---

## Portal Edges

### Concept

Portal edges connect nodes across different mode graphs, enabling seamless mode switching:

```
Walk Graph                    Bike Graph
    │                             │
   W42 ──── portal edge ────── B17
    │                             │
```

### Portal Detection Algorithm

```python
PORTAL_THRESHOLD = 0.001  # degrees (~111m lat, ~85m lon)

def are_within_threshold(lat1, lon1, lat2, lon2) -> bool:
    """Fast degree-based proximity check."""
    return abs(lat1 - lat2) < PORTAL_THRESHOLD and \
           abs(lon1 - lon2) < PORTAL_THRESHOLD
```

**Why degree-based, not meter-based**:
- No expensive haversine computation
- Consistent with tile system units
- Fast: just two floating-point comparisons
- Close enough for our purposes (off by ~23% in lon dimension at NYC latitude)

### Portal Edge Properties

```python
portal_edge = {
    "type": "portal",
    "from_mode": "walk",
    "to_mode": "bike",
    "distance": actual_haversine_distance,  # meters
    "travel_time": distance / WALK_SPEED,   # assume walking between
}
```

### Building the Portal Index

For efficient portal lookup, we build a spatial index:

```python
from rtree import index

def build_portal_index(graphs: dict) -> dict:
    """Build R-tree index for each mode's nodes."""
    indexes = {}
    for mode, G in graphs.items():
        idx = index.Index()
        for node, data in G.nodes(data=True):
            lat, lon = data["y"], data["x"]
            # R-tree uses (minx, miny, maxx, maxy)
            idx.insert(node, (lon, lat, lon, lat))
        indexes[mode] = idx
    return indexes
```

---

## Path Caching

### Cache Keys

**Path cache**:
```
roam:path:{from_tile}:{to_tile} → JSON {
    "time": 324.5,
    "distance": 2150,
    "waypoint_tiles": ["40.710_-74.010", "40.715_-74.005", ...],
    "boundary_nodes": [["walk", 123], ["bike", 456], ...],
    "geometry_hash": "abc123"  # for stale detection
}
```

**Inverted index** (for cache invalidation):
```
roam:tile_paths:{tile_id} → SET of "{from_tile}:{to_tile}" keys
```

**Tile hashes** (for change detection):
```
roam:tile:{tile_id}:hash → "a1b2c3d4..."
```

### Query Flow

```
1. start_tile = coords_to_tile(start_lat, start_lon)
2. end_tile = coords_to_tile(end_lat, end_lon)

3. IF start_tile == end_tile:
     → Local A* within single tile
     → No caching needed

4. cached = redis.get(f"roam:path:{start_tile}:{end_tile}")
   IF cached:
     → Route: start → first_boundary_node → cached_path → last_boundary_node → end
     → Return stitched result

5. ELSE:
     → Run full A* on super-graph
     → For each waypoint tile pair, cache the sub-path
     → Register all waypoint tiles in inverted index
     → Return result
```

### Cache Warming

On startup or after refresh, optionally pre-compute common routes:

```python
HIGH_TRAFFIC_PAIRS = [
    ("40.750_-73.995", "40.710_-74.005"),  # Times Sq → FiDi
    ("40.750_-73.995", "40.685_-73.980"),  # Times Sq → Brooklyn
    # ... etc
]

def warm_cache(router, pairs):
    for from_tile, to_tile in pairs:
        if not get_cached_path(from_tile, to_tile):
            # Route between tile centers
            from_lat, from_lon = tile_center(from_tile)
            to_lat, to_lon = tile_center(to_tile)
            router.route(from_lat, from_lon, to_lat, to_lon)
```

---

## Daily Refresh

### Refresh Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│  1. Download fresh OSM data (osmnx)                         │
├─────────────────────────────────────────────────────────────┤
│  2. Build walk/bike/drive graphs                            │
├─────────────────────────────────────────────────────────────┤
│  3. Make all edges bidirectional                            │
├─────────────────────────────────────────────────────────────┤
│  4. Build portal edges                                      │
├─────────────────────────────────────────────────────────────┤
│  5. Compute tile hashes (nodes + edges per tile)            │
├─────────────────────────────────────────────────────────────┤
│  6. Compare to stored hashes → identify changed tiles       │
├─────────────────────────────────────────────────────────────┤
│  7. Invalidate cache entries for changed tiles              │
├─────────────────────────────────────────────────────────────┤
│  8. Save new graphs + hashes                                │
└─────────────────────────────────────────────────────────────┘
```

### Tile Hash Computation

```python
import hashlib
import json

def compute_tile_hash(graph, tile_id: str) -> str:
    """Compute deterministic hash of nodes/edges in tile."""
    min_lat, min_lon, max_lat, max_lon = tile_to_bbox(tile_id)

    # Collect nodes in tile
    nodes = []
    for node, data in graph.nodes(data=True):
        lat, lon = data["y"], data["x"]
        if min_lat <= lat < max_lat and min_lon <= lon < max_lon:
            nodes.append(node)

    # Collect edges touching tile nodes
    edges = []
    node_set = set(nodes)
    for u, v, data in graph.edges(data=True):
        if u in node_set or v in node_set:
            edges.append((u, v, data.get("length", 0)))

    # Deterministic hash
    payload = {
        "nodes": sorted(nodes),
        "edges": sorted(edges)
    }
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()[:16]
```

### Cache Invalidation

```python
def invalidate_tile(redis_client, tile_id: str):
    """Remove all cached paths that pass through this tile."""
    path_keys = redis_client.smembers(f"roam:tile_paths:{tile_id}")
    for key in path_keys:
        redis_client.delete(f"roam:path:{key}")
    redis_client.delete(f"roam:tile_paths:{tile_id}")
    redis_client.delete(f"roam:tile:{tile_id}:hash")
```

### Stored Files

```
server/graph_cache/
├── roam_unified.gpickle    # NetworkX super-graph with portals
├── roam_portals.json       # Portal edge list (for debugging)
├── tile_hashes.json        # {tile_id: hash} for all tiles
└── metadata.json           # {last_update, node_counts, bbox}
```

---

## Implementation Checklist

Use this checklist to coordinate parallel implementation. Each item can be worked on independently once dependencies are met.

### Phase 1: Core Infrastructure

- [ ] **1.1** Create `server/tiles.py` - Tile system utilities
  - [ ] `TILE_SIZE = 0.005` constant
  - [ ] `coords_to_tile(lat, lon) -> str`
  - [ ] `tile_to_bbox(tile_id) -> tuple`
  - [ ] `tile_center(tile_id) -> tuple`
  - [ ] `get_all_nyc_tiles() -> list`
  - [ ] `get_tile_nodes(graph, tile_id) -> set`
  - [ ] `compute_tile_hash(graph, tile_id) -> str`

- [ ] **1.2** Create `server/roam_router.py` - Core routing engine
  - [ ] `class RoamRouter`
  - [ ] `__init__(self, redis_client)`
  - [ ] `load_or_build()` - Load cached or build super-graph
  - [ ] `make_bidirectional(G)` - Add reverse edges
  - [ ] `build_portal_index(graphs)` - R-tree for each mode
  - [ ] `build_portals(walk_g, bike_g, drive_g)` - Create portal edges
  - [ ] `route(start_lat, start_lon, end_lat, end_lon)` - Main routing method
  - [ ] `_astar_search()` - A* on super-graph
  - [ ] `_build_route_result(path)` - Convert to GeoJSON response

### Phase 2: Caching Layer

- [ ] **2.1** Create `server/roam_cache.py` - Redis caching
  - [ ] `cache_path(redis, from_tile, to_tile, path_data)`
  - [ ] `get_cached_path(redis, from_tile, to_tile) -> dict | None`
  - [ ] `invalidate_tile(redis, tile_id)`
  - [ ] `register_path_tiles(redis, from_tile, to_tile, waypoint_tiles)`
  - [ ] `get_cache_stats(redis) -> dict` - Cache hit/miss stats

- [ ] **2.2** Integrate caching into `RoamRouter.route()`
  - [ ] Check cache before computing
  - [ ] Cache result after computing
  - [ ] Cache intermediate tile pairs along path

### Phase 3: Refresh System

- [ ] **3.1** Create `server/refresh.py` - Daily OSM refresh
  - [ ] `download_osm_graphs()` - Fetch walk/bike/drive from OSM
  - [ ] `compute_all_tile_hashes(graph) -> dict`
  - [ ] `detect_changes(old_hashes, new_hashes) -> list`
  - [ ] `invalidate_changed_tiles(redis, changed_tiles)`
  - [ ] `save_graphs_and_hashes()` - Persist to disk
  - [ ] `main()` - Entry point for cron

- [ ] **3.2** Add refresh trigger
  - [ ] Run on server startup (if graphs stale)
  - [ ] Add cron job support (daily at 4 AM)
  - [ ] Optional: `POST /api/admin/refresh` endpoint (protected)

### Phase 4: Flask Integration

- [ ] **4.1** Modify `server/app.py`
  - [ ] Import `RoamRouter`
  - [ ] Initialize global `roam_router` instance
  - [ ] Add `POST /api/routes/roam` endpoint
  - [ ] Return GeoJSON with mode annotations

### Phase 5: Client Integration

- [ ] **5.1** Modify `client-react/src/components/CommuteInput/`
  - [ ] Add "Roam" option to mode dropdown

- [ ] **5.2** Modify `client-react/src/context/RouteContext.tsx`
  - [ ] Handle "roam" mode selection
  - [ ] Update route calculation to call `/api/routes/roam`

- [ ] **5.3** Modify `client-react/src/components/RouteLayer/`
  - [ ] Add multi-segment rendering (different color per mode)
  - [ ] Create roam route layer function

### Phase 6: Testing

- [ ] **6.1** Create `server/test_tiles.py`
  - [ ] Test `coords_to_tile()` edge cases
  - [ ] Test `tile_to_bbox()` round-trip
  - [ ] Test NYC tile coverage

- [ ] **6.2** Create `server/test_roam_cache.py`
  - [ ] Test cache hit/miss
  - [ ] Test invalidation propagation
  - [ ] Test inverted index integrity

- [ ] **6.3** Integration tests
  - [ ] Route: Times Square → Brooklyn Bridge
  - [ ] Verify mode switches occur
  - [ ] Verify cache works (second request faster)
  - [ ] Verify bidirectionality (reverse route same distance)

---

## Constants Reference

```python
# Tile system
TILE_SIZE = 0.005           # degrees (~500m)
PORTAL_THRESHOLD = 0.001    # degrees (~111m)

# Speeds (km/h → used for travel time calculation)
WALK_SPEED = 5.0
BIKE_SPEED = 18.0
DRIVE_SPEED = 35.0

# NYC bounding box
NYC_BBOX = {
    "north": 40.92,
    "south": 40.49,
    "east": -73.70,
    "west": -74.26
}

# Redis key prefixes
REDIS_PATH_PREFIX = "roam:path:"
REDIS_TILE_PREFIX = "roam:tile:"
REDIS_TILE_PATHS_PREFIX = "roam:tile_paths:"
```

---

## Appendix: Degree to Meter Conversions

At NYC latitude (40.7°N):

| Degrees | Latitude (N-S) | Longitude (E-W) |
|---------|----------------|-----------------|
| 1° | 111.0 km | 84.4 km |
| 0.01° | 1,110 m | 844 m |
| 0.005° | 555 m | 422 m |
| 0.001° | 111 m | 84 m |
| 0.0001° | 11.1 m | 8.4 m |

Formula: `lon_meters = lat_meters × cos(latitude)`

At 40.7°N: `cos(40.7°) ≈ 0.758`
