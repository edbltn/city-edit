"""
Curated registry of supported cities.

A *city* defines the geographic bounding box, default map view, the OSRM endpoint
that serves its routing dataset, and where its graph/tiles live on disk. The
user-facing *maps* (a.k.a. "modes") each reference a city by id.

Adding a city here + building its graph (`refresh_osm.py --city <id>`) and OSRM
dataset (a service in docker-compose) is all that's needed to make it selectable
when proposing a new map.

bbox convention throughout the app: (south, west, north, east).
"""
import os
from dataclasses import dataclass

from pmtiles.reader import Reader as PMTilesReader, MmapSource


# blocks.pmtiles zoom range + coverage bounds per archive path, keyed on the
# mtime cache-buster so a lazy re-bake is picked up without re-reading the
# header on every request.
_blocks_header_cache: dict[str, tuple[int, tuple[int, int], list[float]]] = {}


def _blocks_header_info(path: str, version: int) -> tuple[tuple[int, int], list[float]]:
    """((minzoom, maxzoom), [minLon, minLat, maxLon, maxLat]) from the header."""
    cached = _blocks_header_cache.get(path)
    if cached and cached[0] == version:
        return cached[1], cached[2]
    with open(path, "rb") as f:
        header = PMTilesReader(MmapSource(f)).header()
    bounds = [
        header["min_lon_e7"] / 1e7, header["min_lat_e7"] / 1e7,
        header["max_lon_e7"] / 1e7, header["max_lat_e7"] / 1e7,
    ]
    entry = (version, (header["min_zoom"], header["max_zoom"]), bounds)
    _blocks_header_cache[path] = entry
    return entry[1], entry[2]


def _env_osrm_host(city_id: str, default: str) -> str:
    """Per-city OSRM host override, e.g. OSRM_HOST_NYC=localhost for local dev."""
    key = f"OSRM_HOST_{city_id.upper().replace('-', '_')}"
    return os.environ.get(key) or os.environ.get("OSRM_HOST") or default


@dataclass(frozen=True)
class City:
    id: str
    name: str
    bbox: tuple[float, float, float, float]  # south, west, north, east
    center: tuple[float, float]              # lat, lon
    default_zoom: int
    min_zoom: int
    max_zoom: int
    pbf_url: str                             # source extract for the OSRM build
    osrm_service: str                        # docker service / default host

    @property
    def data_dir(self) -> str:
        """Per-city directory for walk_graph.pkl, metadata.json, graph.pmtiles."""
        return f"osm_data/{self.id}"

    @property
    def osrm_host(self) -> str:
        return _env_osrm_host(self.id, self.osrm_service)

    @property
    def osrm_port(self) -> int:
        return int(os.environ.get("OSRM_PORT", "5000"))

    @property
    def geocode_bbox(self) -> str:
        """Photon bbox string: minLon,minLat,maxLon,maxLat."""
        s, w, n, e = self.bbox
        return f"{w},{s},{e},{n}"

    def to_public(self) -> dict:
        """Client-facing shape (matches the bounds/center used in config.ts)."""
        s, w, n, e = self.bbox
        blocks_version = self._blocks_version()
        return {
            "id": self.id,
            "name": self.name,
            "bounds": {"sw": {"lat": s, "lon": w}, "ne": {"lat": n, "lon": e}},
            "center": {"lat": self.center[0], "lon": self.center[1]},
            "defaultZoom": self.default_zoom,
            "minZoom": self.min_zoom,
            "maxZoom": self.max_zoom,
            "tilesPath": f"/api/tiles/{self.id}/graph.pmtiles",
            # Cache-buster for blocks.pmtiles (served with max-age=1w under a
            # fixed URL): the client appends ?v=<this>, so a re-baked block set
            # is never mixed with week-old cached ranges. A cheap stat — no
            # graph load — and None when the city has no block artifacts.
            "blocksVersion": blocks_version,
            # z/x/y block-tile source (browser/CDN-cacheable, unlike the
            # pmtiles range requests). ?v= busts every cache on re-bake.
            "blockTiles": self._block_tiles(blocks_version),
        }

    def _block_tiles(self, blocks_version: int | None) -> dict | None:
        """MapLibre z/x/y tile-source descriptor, or None when no archive."""
        if blocks_version is None:
            return None
        path = os.path.join(os.path.dirname(__file__), self.data_dir, "blocks.pmtiles")
        try:
            # ?v= must be unique per BAKE: st_mtime_ns, not the second-
            # resolution blocksVersion — two bakes in the same second would
            # otherwise share a URL and durably poison the immutable caches.
            mtime_ns = os.stat(path).st_mtime_ns
            (minzoom, maxzoom), bounds = _blocks_header_info(path, blocks_version)
        except Exception:
            # OSError when absent; anything else = header unreadable (e.g. a
            # mid-re-bake read of the in-place-written archive) — advertise no
            # z/x/y source and let the client fall back to pmtiles://.
            return None
        return {
            "template": f"/api/tile/{self.id}/blocks/{{z}}/{{x}}/{{y}}.mvt?v={mtime_ns}",
            "minzoom": minzoom,
            "maxzoom": maxzoom,
            # Coverage bbox: MapLibre skips requesting tiles wholly outside it,
            # killing the per-pan stream of water/edge empty-tile requests.
            "bounds": bounds,
        }

    def _blocks_version(self) -> int | None:
        """mtime of this city's blocks.pmtiles, or None when absent."""
        path = os.path.join(os.path.dirname(__file__), self.data_dir, "blocks.pmtiles")
        try:
            return int(os.stat(path).st_mtime)
        except OSError:
            return None


# ── Registry ─────────────────────────────────────────────────────────────────

DEFAULT_CITY_ID = "nyc"

CITIES: dict[str, City] = {
    "nyc": City(
        id="nyc",
        name="New York City",
        bbox=(40.4774, -74.2591, 40.9176, -73.7004),
        center=(40.7580, -73.9855),
        default_zoom=14,
        min_zoom=10,
        max_zoom=21,
        pbf_url="https://download.geofabrik.de/north-america/us/new-york-latest.osm.pbf",
        osrm_service="osrm-nyc",
    ),
    "sf": City(
        id="sf",
        name="San Francisco",
        # Sized to just contain the built walk graph. osmnx's truncate_by_edge
        # spills nodes slightly past SF proper — most visibly Treasure Island to
        # the north (~37.830) — so the box is nudged out a smidge on each side to
        # keep the whole graph inside the votable area. center is the bbox midpoint
        # so the map opens centered. (Roughly: Daly City line → Treasure Island.)
        bbox=(37.700, -122.516, 37.832, -122.354),
        center=(37.766, -122.435),
        default_zoom=13,
        min_zoom=11,
        max_zoom=21,
        # BBBike publishes pre-clipped per-city extracts (small, no clipping needed).
        pbf_url="https://download.bbbike.org/osm/bbbike/SanFrancisco/SanFrancisco.osm.pbf",
        osrm_service="osrm-sf",
    ),
    "chicago": City(
        id="chicago",
        name="Chicago",
        bbox=(41.78, -87.75, 42.02, -87.58),
        # Center on the middle of the bbox so the votable area opens centered.
        center=(41.90, -87.665),
        default_zoom=12,
        min_zoom=10,
        max_zoom=21,
        pbf_url="https://download.bbbike.org/osm/bbbike/Chicago/Chicago.osm.pbf",
        osrm_service="osrm-chicago",
    ),
    "dc": City(
        id="dc",
        name="Washington, D.C.",
        # The District proper (diamond outline); box sized to enclose it. Slightly
        # larger N-S than SF, comparable to Chicago E-W.
        bbox=(38.79, -77.12, 39.00, -76.91),
        # Center on the bbox midpoint so the votable area opens centered.
        center=(38.895, -77.015),
        default_zoom=12,
        min_zoom=11,
        max_zoom=21,
        pbf_url="https://download.bbbike.org/osm/bbbike/WashingtonDC/WashingtonDC.osm.pbf",
        osrm_service="osrm-dc",
    ),
    "philly": City(
        id="philly",
        name="Philadelphia",
        # City limits: elongated NE-SW (Navy Yard in the south up to Somerton in
        # the far northeast). Box sized to enclose the whole city.
        bbox=(39.867, -75.280, 40.138, -74.956),
        # Center on the bbox midpoint so the votable area opens centered.
        center=(40.003, -75.118),
        default_zoom=12,
        min_zoom=11,
        max_zoom=21,
        pbf_url="https://download.bbbike.org/osm/bbbike/Philadelphia/Philadelphia.osm.pbf",
        osrm_service="osrm-philly",
    ),
    "sacramento": City(
        id="sacramento",
        name="Sacramento",
        # City limits run S 38.4377 → N 38.6855, W -121.5601 → E -121.3627
        # (Nominatim). Padded a touch on every side so osmnx's truncate_by_edge
        # spill stays inside the votable area, and so the west edge reaches the
        # Sacramento River crossings into West Sacramento (Tower / I Street
        # bridges are the subject of real city projects).
        bbox=(38.430, -121.580, 38.690, -121.355),
        # Center on the bbox midpoint so the votable area opens centered.
        center=(38.560, -121.4675),
        default_zoom=12,
        min_zoom=10,
        max_zoom=21,
        pbf_url="https://download.bbbike.org/osm/bbbike/Sacramento/Sacramento.osm.pbf",
        osrm_service="osrm-sacramento",
    ),
    # ── Block-pipeline playground areas (local experiments; reuse the NYC PBF
    # by hardlinking osm_data/nyc/source.osm.pbf into their data dirs) ────────
    "test-cp": City(
        id="test-cp",
        name="Test: Central Park",
        bbox=(40.7640, -73.9830, 40.8010, -73.9490),
        center=(40.7825, -73.9660),
        default_zoom=15,
        min_zoom=13,
        max_zoom=21,
        pbf_url="https://download.geofabrik.de/north-america/us/new-york-latest.osm.pbf",
        osrm_service="osrm-nyc",
    ),
    "test-mid": City(
        id="test-mid",
        name="Test: Midtown",
        bbox=(40.7380, -74.0000, 40.7650, -73.9680),
        center=(40.7515, -73.9840),
        default_zoom=15,
        min_zoom=13,
        max_zoom=21,
        pbf_url="https://download.geofabrik.de/north-america/us/new-york-latest.osm.pbf",
        osrm_service="osrm-nyc",
    ),
}


def get_city(city_id: str) -> City | None:
    return CITIES.get(city_id)


def all_cities() -> list[City]:
    return list(CITIES.values())
