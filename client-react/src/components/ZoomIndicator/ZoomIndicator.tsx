import { useState, useEffect } from "react";
import { useMap } from "react-leaflet";
import "./ZoomIndicator.css";

export function ZoomIndicator() {
  const map = useMap();
  const [zoom, setZoom] = useState(map.getZoom());

  useEffect(() => {
    const handleZoom = () => setZoom(Math.round(map.getZoom() * 10) / 10);
    map.on("zoomend", handleZoom);
    map.on("zoom", handleZoom);
    return () => {
      map.off("zoomend", handleZoom);
      map.off("zoom", handleZoom);
    };
  }, [map]);

  // Create a Leaflet control-style element
  useEffect(() => {
    const container = document.createElement("div");
    container.className = "zoom-indicator leaflet-control";
    container.innerHTML = `Zoom: ${zoom.toFixed(1)}`;

    const controlContainer = map.getContainer().querySelector(".leaflet-bottom.leaflet-left");
    if (controlContainer) {
      controlContainer.appendChild(container);
    }

    return () => {
      container.remove();
    };
  }, [map, zoom]);

  return null;
}
