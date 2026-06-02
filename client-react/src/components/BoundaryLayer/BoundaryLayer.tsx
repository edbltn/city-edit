/**
 * Boundary Layer — votable-region scrim.
 *
 * Dims everything *outside* the mapped bounding box and leaves the votable
 * area at full brightness, so it's intuitively clear where you can and can't
 * vote. Rendered as a single Leaflet polygon: a world-sized outer ring with
 * the city bbox punched out as a hole. The hole's border doubles as a crisp
 * hairline edge marking the boundary.
 *
 * Driven entirely by `CONFIG.mappedBounds`, so it adapts to any city/bbox.
 * Non-interactive — clicks pass through to the map (out-of-bounds clicks
 * still surface the "stay within bounds" error via useMapClick).
 */

import { useEffect } from "react";
import { useMap } from "react-leaflet";
import L from "leaflet";
import { CONFIG } from "../../config";

const PANE_NAME = "boundaryPane";

// Sits above the base map/graph scrim target but below routes, markers, and
// the desire path (routePane 450, desirePathPane 440, graphPane 430).
const PANE_Z_INDEX = "405";

// Outer ring covers the whole globe so only the bbox hole stays bright. The
// world edges fall far offscreen, leaving just the inner border visible.
const WORLD_RING: L.LatLngTuple[] = [
  [85, -180],
  [85, 180],
  [-85, 180],
  [-85, -180],
];

const scrimStyle: L.PolylineOptions = {
  // Very light tint outside the votable area — just enough to set it apart while
  // the basemap roads/water still read through. The boundary line does the real
  // "here's the edge" work.
  fillColor: "#000",
  fillOpacity: 0.12,
  fillRule: "evenodd",
  // Hairline edge marking the boundary (drawn on the hole's border).
  color: "rgba(255, 255, 255, 0.35)",
  weight: 1.5,
  dashArray: "6 6",
  lineCap: "round",
  lineJoin: "round",
  interactive: false,
};

export function BoundaryLayer() {
  const map = useMap();

  useEffect(() => {
    if (!map.getPane(PANE_NAME)) {
      map.createPane(PANE_NAME);
      const pane = map.getPane(PANE_NAME);
      if (pane) pane.style.zIndex = PANE_Z_INDEX;
    }

    const { sw, ne } = CONFIG.mappedBounds;
    const hole: L.LatLngTuple[] = [
      [sw.lat, sw.lon],
      [ne.lat, sw.lon],
      [ne.lat, ne.lon],
      [sw.lat, ne.lon],
    ];

    const scrim = L.polygon([WORLD_RING, hole], {
      ...scrimStyle,
      pane: PANE_NAME,
    });
    scrim.addTo(map);

    return () => {
      scrim.remove();
    };
  }, [map]);

  return null;
}
