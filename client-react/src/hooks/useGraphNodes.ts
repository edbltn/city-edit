import { useEffect, useState } from "react";
import { useMap } from "react-leaflet";
import { CONFIG } from "../config";
import { withMap } from "../map/runtime";

/**
 * Fetch graph nodes for the current map viewport.
 * Returns nodes as [lat, lng] tuples for nearest-node lookup.
 */
export function useGraphNodes(): [number, number][] {
  const map = useMap();
  const [nodes, setNodes] = useState<[number, number][]>([]);

  useEffect(() => {
    const fetchNodes = async () => {
      try {
        const bounds = map.getBounds();
        const bbox = `${bounds.getWest()},${bounds.getSouth()},${bounds.getEast()},${bounds.getNorth()}`;

        const response = await fetch(withMap(`${CONFIG.apiUrl}/graph?bbox=${encodeURIComponent(bbox)}`));
        if (!response.ok) throw new Error(`Graph fetch failed: ${response.status}`);

        const data = await response.json();
        // Nodes are [lat, lon] from the API
        setNodes(data.nodes || []);
      } catch (error) {
        console.error("Failed to fetch graph nodes:", error);
        setNodes([]);
      }
    };

    fetchNodes();

    // Re-fetch when map bounds change
    const onMoveEnd = () => fetchNodes();
    map.on("moveend", onMoveEnd);

    return () => {
      map.off("moveend", onMoveEnd);
    };
  }, [map]);

  return nodes;
}
