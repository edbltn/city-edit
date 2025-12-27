const defaultLineStyle = {
  weight: 3,       // stroke width in pixels
  opacity: 0.85,   // overall stroke opacity
  lineCap: "round",
  lineJoin: "round"
};

// Keeps Leaflet layers in sync with mapState.overlays
export function createOverlayManager(map) {
  const layersById = new Map();

  function upsertGeoJsonLayer(id, overlay) {
    let layer = layersById.get(id);
    if (!layer) {
      layer = L.geoJSON([], {
        style: (feature) => ({
          ...defaultLineStyle,
          ...overlay.options?.style
        })
      });
      layer.addTo(map);
      layersById.set(id, layer);
    }
    layer.clearLayers();
    layer.addData(overlay.data);
  }

  function removeLayer(id) {
    const layer = layersById.get(id);
    if (!layer) return;
    map.removeLayer(layer);
    layersById.delete(id);
  }

  function applyMapState(mapState) {
    const desiredIds = new Set(Object.keys(mapState.overlays || {}));

    // remove overlays no longer present
    for (const existingId of layersById.keys()) {
      if (!desiredIds.has(existingId)) removeLayer(existingId);
    }

    // upsert overlays
    for (const [id, overlay] of Object.entries(mapState.overlays || {})) {
      if (overlay.type === "geojson") upsertGeoJsonLayer(id, overlay);
      // later: markers, polylines, heatmaps, etc.
    }
  }

  return { applyMapState };
}
