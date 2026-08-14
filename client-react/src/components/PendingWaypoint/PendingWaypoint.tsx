import { useMemo } from "react";
import { Marker, Polyline } from "react-leaflet";
import { kiteGhostIcon } from "../../utils/kiteIcon";
import { useTheme } from "../../context";
import { mapStyleForTheme } from "../../themes";
import type { PendingWaypoint as PendingWaypointState } from "../../context/RouteContext";

// Same dotted grey as the drag trail and the waypoint connectors: "this pin is
// attached to the route, but not by a computed path". The dotted line is the
// app's existing vocabulary for a provisional link, so a pin awaiting its route
// reads as unfinished rather than as a straight-line shortcut.
const PENDING_LINE_STYLE = {
  color: "#999999",
  weight: 2,
  opacity: 0.6,
  dashArray: "1, 4",
  lineCap: "round" as const,
};

interface PendingWaypointProps {
  pending: PendingWaypointState | null;
}

/**
 * A dropped waypoint whose route hasn't come back yet: it FLOATS at the drop
 * point, tied to its neighbours by dotted connectors, and is non-interactive.
 *
 * Nothing here is route geometry. Until the router returns a path, the app has
 * no idea what the route to this point looks like — drawing a solid line to it
 * would be inventing one, which is exactly the bug this replaces.
 */
export function PendingWaypoint({ pending }: PendingWaypointProps) {
  const selection = mapStyleForTheme(useTheme()).selection;
  const icon = useMemo(() => kiteGhostIcon(selection), [selection]);

  if (!pending) return null;

  const { coords, prev, next } = pending;
  const links: { key: string; positions: [number, number][] }[] = [];
  if (prev) {
    links.push({ key: "pending-prev", positions: [[prev.lat, prev.lng], [coords.lat, coords.lng]] });
  }
  if (next) {
    links.push({ key: "pending-next", positions: [[coords.lat, coords.lng], [next.lat, next.lng]] });
  }

  return (
    <>
      {links.map(({ key, positions }) => (
        <Polyline key={key} positions={positions} pathOptions={PENDING_LINE_STYLE} />
      ))}
      <Marker position={[coords.lat, coords.lng]} icon={icon} interactive={false} />
    </>
  );
}
