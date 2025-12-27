export const CONFIG = {
  // Initial camera
  initialView: { lat: 40.7831, lon: -73.9712, zoom: 12 },

  // Prevent panning too far away (rough Manhattan-ish bbox)
  manhattanBounds: {
    sw: { lat: 40.6980, lon: -74.0470 },
    ne: { lat: 40.8820, lon: -73.9070 }
  },

  // Zoom limits
  minZoom: 11,
  maxZoom: 19,

  // Tiles (simple raster). For production, use a proper provider / your own tiles.
  tileUrlTemplate: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
  tileAttribution: "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ, TomTom, Intermap, iPC, USGS, FAO, NPS, NRCAN, GeoBase, Kadaster NL, Ordnance Survey, Esri Japan, METI, Esri China (Hong Kong), and the GIS User Community",
  tileSubdomains: "",

  // Leaflet behaviors
  preferCanvas: true,
  maxBoundsViscosity: 1.0,

  // Socket URL
  wsUrl: "ws://localhost:5001/ws"
};
