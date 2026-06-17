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
        return {
            "id": self.id,
            "name": self.name,
            "bounds": {"sw": {"lat": s, "lon": w}, "ne": {"lat": n, "lon": e}},
            "center": {"lat": self.center[0], "lon": self.center[1]},
            "defaultZoom": self.default_zoom,
            "minZoom": self.min_zoom,
            "maxZoom": self.max_zoom,
            "tilesPath": f"/api/tiles/{self.id}/graph.pmtiles",
        }


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
}


def get_city(city_id: str) -> City | None:
    return CITIES.get(city_id)


def all_cities() -> list[City]:
    return list(CITIES.values())
