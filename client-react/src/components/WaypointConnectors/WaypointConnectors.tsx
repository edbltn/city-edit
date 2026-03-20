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

// Generate a straight line between a waypoint and a path endpoint
function generateLine(
  from: LatLng,
  to: [number, number]
): [number, number][] {
  return [
    [from.lat, from.lng],
    [to[1], to[0]],
  ];
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

  const linePoints = generateLine(waypoint, target);
  return { key, positions: linePoints };
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
            color: "#999999",
            weight: 2,
            opacity: 0.6,
            dashArray: "1, 4",
            lineCap: "round",
          }}
        />
      ))}
    </>
  );
}
