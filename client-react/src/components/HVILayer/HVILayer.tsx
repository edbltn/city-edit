/**
 * NYC Heat Vulnerability Index (HVI) choropleth.
 * Loads /hvi.geojson (baked from NYC Open Data 4mhf-duep joined to
 * pri4-ifjk ZCTA polygons) and renders a 5-class YlOrRd ramp.
 * Non-interactive so clicks pass through to plant trees.
 *
 * Status: experimental. Surfaced in the tree theme as a backdrop for
 * siting decisions, but no UI exists to toggle it. May move to a
 * blog-only fork or be replaced once we settle on which sustainability
 * datasets to ship in the main app.
 */

import { useEffect, useState } from "react";
import { GeoJSON } from "react-leaflet";

const HVI_URL = "/hvi.geojson";

const HVI_COLORS: Record<number, string> = {
  1: "#ffffb2",
  2: "#fecc5c",
  3: "#fd8d3c",
  4: "#f03b20",
  5: "#bd0026",
};

function styleFor(feature?: GeoJSON.Feature) {
  const score = Number(feature?.properties?.hvi) || 0;
  return {
    fillColor: HVI_COLORS[score] || "#888",
    fillOpacity: 0.4,
    color: "rgba(0,0,0,0.25)",
    weight: 0.5,
    interactive: false,
  };
}

export function HVILayer() {
  const [data, setData] = useState<GeoJSON.FeatureCollection | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(HVI_URL)
      .then((r) => r.json())
      .then((geo) => {
        if (!cancelled) setData(geo);
      })
      .catch((err) => console.error("Failed to load hvi.geojson:", err));
    return () => {
      cancelled = true;
    };
  }, []);

  if (!data) return null;
  return <GeoJSON data={data} style={styleFor} interactive={false} pane="hviPane" />;
}
