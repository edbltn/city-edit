"""
Abstract base class for route calculation.

All routing implementations (ORS, OSM, etc.) must implement this interface.
This enables swapping routing backends with a single line change.
"""
from abc import ABC, abstractmethod
from typing import Optional


class RouterInterface(ABC):
    """
    Abstract base class for route calculation.

    All routing implementations must implement calculate_route().
    """

    @abstractmethod
    def calculate_route(
        self,
        start: tuple[float, float],  # (lat, lon)
        end: tuple[float, float],    # (lat, lon)
        mode: str,                   # "walk", "bike", "drive"
        waypoints: Optional[list[tuple[float, float]]] = None
    ) -> dict:
        """
        Calculate a route between two points.

        Args:
            start: Starting point as (lat, lon)
            end: Ending point as (lat, lon)
            mode: Transport mode - "walk", "bike", or "drive"
            waypoints: Optional list of intermediate points as (lat, lon) tuples

        Returns:
            dict with keys:
                - geometry: GeoJSON LineString geometry
                - distance: float (meters)
                - duration: float (seconds)
                - mode: str
                - _cache_hit: bool (True if result was from cache)
                - error: str (only present if calculation failed)
        """
        pass
