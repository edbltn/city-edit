"""
OSRM-based routing via the OSRM HTTP API.

Uses a self-hosted OSRM instance for fast, local routing.
"""
import logging
from typing import Optional

import requests

from router_interface import RouterInterface

logger = logging.getLogger(__name__)

OSRM_MODE_MAP = {
    "walk": "foot",
    "bike": "bicycle",
    "drive": "car",
}


class OsrmRouter(RouterInterface):

    def __init__(self, host: str = "localhost", port: int = 5000):
        self._base_url = f"http://{host}:{port}"
        logger.info(f"[OSRM] Router initialized: {self._base_url}")

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
            "?overview=full&geometries=geojson&steps=false"
        )

        logger.info(f"[OSRM] GET {url}")

        try:
            resp = requests.get(url, timeout=10)
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
        logger.info(
            f"[OSRM] Route OK: {route['distance']:.0f}m, "
            f"{route['duration']:.0f}s, "
            f"{len(route['geometry']['coordinates'])} coords"
        )

        return {
            "geometry": route["geometry"],
            "distance": route["distance"],
            "duration": route["duration"],
            "mode": mode,
            "_cache_hit": False,
        }
