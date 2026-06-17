"""
OSRM-based routing via the OSRM HTTP API.

Uses a self-hosted OSRM instance for fast, local routing. A single merged OSRM
dataset serves every city (coordinates are global, so one routed instance covers
NYC + SF + Chicago). Locally that's the `osrm` compose service over plain HTTP;
in prod it's a private Cloud Run service reached over HTTPS with a Google-signed
ID token for service-to-service auth.
"""
import logging
import time
from typing import Optional

import requests

from router_interface import RouterInterface

logger = logging.getLogger(__name__)

OSRM_MODE_MAP = {
    "walk": "foot",
    "bike": "bicycle",
    "drive": "car",
}

# GCE/Cloud Run metadata server: mints an OIDC ID token whose audience is the
# target service URL. Cloud Run validates it before forwarding to the container.
_METADATA_ID_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/identity"
)


class OsrmRouter(RouterInterface):

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5000,
        base_url: Optional[str] = None,
        use_id_token: bool = False,
    ):
        self._base_url = base_url.rstrip("/") if base_url else f"http://{host}:{port}"
        self._session = requests.Session()
        # When talking to a private Cloud Run OSRM service, every request must
        # carry an ID token scoped to that service's URL.
        self._use_id_token = use_id_token
        self._token: Optional[str] = None
        self._token_exp: float = 0.0
        logger.info(
            f"[OSRM] Router initialized: {self._base_url} (auth={'id-token' if use_id_token else 'none'})"
        )

    def _auth_headers(self) -> dict:
        """Bearer header with a cached Cloud Run ID token (refreshed before expiry)."""
        if not self._use_id_token:
            return {}
        now = time.time()
        if self._token and now < self._token_exp:
            return {"Authorization": f"Bearer {self._token}"}
        try:
            resp = requests.get(
                _METADATA_ID_TOKEN_URL,
                params={"audience": self._base_url, "format": "full"},
                headers={"Metadata-Flavor": "Google"},
                timeout=5,
            )
            resp.raise_for_status()
            self._token = resp.text
            # Tokens live ~1h; refresh a little early to avoid edge-of-expiry 401s.
            self._token_exp = now + 50 * 60
            return {"Authorization": f"Bearer {self._token}"}
        except requests.RequestException as e:
            logger.error(f"[OSRM] Failed to fetch ID token from metadata server: {e}")
            return {}

    def calculate_route(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        mode: str,
        waypoints: Optional[list[tuple[float, float]]] = None,
    ) -> dict:
        profile = OSRM_MODE_MAP.get(mode, "foot")

        # Build coordinate string: lon,lat;lon,lat;...
        coords = [f"{start[1]},{start[0]}"]
        if waypoints:
            for wp in waypoints:
                coords.append(f"{wp[1]},{wp[0]}")
        coords.append(f"{end[1]},{end[0]}")
        coords_str = ";".join(coords)

        url = (
            f"{self._base_url}/route/v1/{profile}/{coords_str}"
            "?overview=full&geometries=geojson&steps=false&annotations=nodes"
        )

        logger.info(f"[OSRM] GET {url}")

        try:
            resp = self._session.get(url, timeout=10, headers=self._auth_headers())
            logger.info(f"[OSRM] Response status={resp.status_code}, body={resp.text[:500]}")
            resp.raise_for_status()
            data = resp.json()
        except requests.ConnectionError as e:
            logger.error(f"[OSRM] Connection refused to {self._base_url}: {e}")
            return {"error": "OSRM service unavailable"}
        except requests.RequestException as e:
            logger.error(f"[OSRM] Request failed: {e}")
            return {"error": f"OSRM request failed: {e}"}

        if data.get("code") != "Ok" or not data.get("routes"):
            msg = data.get("message", "No route found")
            logger.warning(f"[OSRM] Routing failed: code={data.get('code')}, msg={msg}")
            return {"error": msg}

        route = data["routes"][0]

        # Collect OSM node IDs from leg annotations
        osm_node_ids: list[int] = []
        for leg in route.get("legs", []):
            ann = leg.get("annotation", {})
            nodes = ann.get("nodes", [])
            if osm_node_ids and nodes:
                # Legs share a junction node — skip the duplicate
                osm_node_ids.extend(nodes[1:])
            else:
                osm_node_ids.extend(nodes)

        logger.info(
            f"[OSRM] Route OK: {route['distance']:.0f}m, "
            f"{route['duration']:.0f}s, "
            f"{len(route['geometry']['coordinates'])} coords, "
            f"{len(osm_node_ids)} osm nodes"
        )

        return {
            "geometry": route["geometry"],
            "distance": route["distance"],
            "duration": route["duration"],
            "mode": mode,
            "osm_node_ids": osm_node_ids,
            "_cache_hit": False,
        }


def extract_all_segments(geometry: dict) -> list[list]:
    """Split an OSRM route geometry into consecutive coordinate-pair segments.

    Args:
        geometry: Route geometry with ``coordinates`` [[lon, lat], ...].

    Returns:
        List of ``[coord1, coord2]`` segments (empty if fewer than 2 coords).
    """
    if not geometry:
        return []

    coords = geometry.get("coordinates", [])
    if len(coords) < 2:
        return []

    return [[coords[i], coords[i + 1]] for i in range(len(coords) - 1)]
