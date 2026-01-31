import { useMemo } from "react";
import { Polyline } from "react-leaflet";
import type { LatLng, SplitDesirePath } from "../../types";

// Minimum distance (in degrees) to show a connector arc
// ~20 meters at NYC latitude
const MIN_CONNECTOR_DISTANCE = 0.0002;

// Maximum distance (in degrees) for a valid connector
// If distance is larger, the route data is probably stale
// ~500 meters at NYC latitude
const MAX_CONNECTOR_DISTANCE = 0.005;

interface WaypointConnectorsProps {
  start: LatLng | null;
  end: LatLng | null;
  ghostWaypoints: LatLng[];
  routeGeometry: [number, number][] | null; // Used when no split paths
  splitDesirePaths: SplitDesirePath[];
}

// Generate a curved arc between a waypoint and a path endpoint
// from: {lat, lng} waypoint
// to: [lng, lat] GeoJSON coordinate
function generateArc(
  from: LatLng,
  to: [number, number],
  segments: number = 10
): [number, number][] {
  const points: [number, number][] = [];
  const toLat = to[1];
  const toLng = to[0];

  const dx = toLng - from.lng;
  const dy = toLat - from.lat;
  const len = Math.sqrt(dx * dx + dy * dy);

  if (len === 0) return [];

  // Calculate midpoint with perpendicular offset for the arc
  const midLat = (from.lat + toLat) / 2;
  const midLng = (from.lng + toLng) / 2;

  // Arc height is proportional to distance (but capped)
  const arcHeight = Math.min(len * 0.3, 0.0003);

  // Perpendicular direction
  const perpX = -dy / len;
  const perpY = dx / len;

  // Control point for quadratic bezier
  const controlLat = midLat + perpX * arcHeight;
  const controlLng = midLng + perpY * arcHeight;

  // Generate points along the quadratic bezier curve
  for (let i = 0; i <= segments; i++) {
    const t = i / segments;
    const t1 = 1 - t;

    // Quadratic bezier: P = (1-t)²P0 + 2(1-t)tP1 + t²P2
    const lat = t1 * t1 * from.lat + 2 * t1 * t * controlLat + t * t * toLat;
    const lng = t1 * t1 * from.lng + 2 * t1 * t * controlLng + t * t * toLng;

    points.push([lat, lng]);
  }

  return points;
}

function createConnector(
  waypoint: LatLng,
  target: [number, number],
  key: string
): { key: string; positions: [number, number][] } | null {
  const dx = target[0] - waypoint.lng;
  const dy = target[1] - waypoint.lat;
  const distance = Math.sqrt(dx * dx + dy * dy);

  // Too close - no visible connector needed
  if (distance <= MIN_CONNECTOR_DISTANCE) return null;

  // Too far - route data is probably stale (waypoint moved but route not recalculated)
  if (distance > MAX_CONNECTOR_DISTANCE) return null;

  const arcPoints = generateArc(waypoint, target);
  if (arcPoints.length === 0) return null;

  return { key, positions: arcPoints };
}

export function WaypointConnectors({
  start,
  end,
  ghostWaypoints,
  routeGeometry,
  splitDesirePaths,
}: WaypointConnectorsProps) {
  const connectors = useMemo(() => {
    // If there are ghost waypoints but no split paths yet, we're mid-calculation
    // Don't show stale connectors from the old route geometry
    if (ghostWaypoints.length > 0 && splitDesirePaths.length === 0) {
      return [];
    }

    const arcs: { key: string; positions: [number, number][] }[] = [];

    if (splitDesirePaths.length > 0) {
      // With split paths: each segment's endpoints are the targets
      // Start → first point of first segment
      if (start) {
        const coords = splitDesirePaths[0].geometry.coordinates;
        if (coords.length > 0) {
          const connector = createConnector(start, coords[0], "connector-start");
          if (connector) arcs.push(connector);
        }
      }

      // Ghost waypoint i → last point of segment i
      for (let i = 0; i < ghostWaypoints.length; i++) {
        if (i < splitDesirePaths.length) {
          const coords = splitDesirePaths[i].geometry.coordinates;
          if (coords.length > 0) {
            const connector = createConnector(
              ghostWaypoints[i],
              coords[coords.length - 1],
              `connector-ghost-${i}`
            );
            if (connector) arcs.push(connector);
          }
        }
      }

      // End → last point of last segment
      if (end) {
        const lastSegment = splitDesirePaths[splitDesirePaths.length - 1];
        const coords = lastSegment.geometry.coordinates;
        if (coords.length > 0) {
          const connector = createConnector(end, coords[coords.length - 1], "connector-end");
          if (connector) arcs.push(connector);
        }
      }
    } else if (routeGeometry && routeGeometry.length > 0) {
      // Single route: start → first point, end → last point
      if (start) {
        const connector = createConnector(start, routeGeometry[0], "connector-start");
        if (connector) arcs.push(connector);
      }
      if (end) {
        const connector = createConnector(
          end,
          routeGeometry[routeGeometry.length - 1],
          "connector-end"
        );
        if (connector) arcs.push(connector);
      }
    }

    return arcs;
  }, [start, end, ghostWaypoints, routeGeometry, splitDesirePaths]);

  if (connectors.length === 0) return null;

  return (
    <>
      {connectors.map(({ key, positions }) => (
        <Polyline
          key={key}
          positions={positions}
          pathOptions={{
            color: "#444444",
            weight: 2,
            opacity: 0.8,
            dashArray: "4, 4",
          }}
        />
      ))}
    </>
  );
}
