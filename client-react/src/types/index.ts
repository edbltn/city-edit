// ==========================================================================
// Type Definitions for Desire Path Mapper
// ==========================================================================

export type TransportMode = "bike" | "walk" | "drive";

export interface LatLng {
  lat: number;
  lng: number;
}

export interface RoutePoint {
  coords: LatLng | null;
  timestamp: number | null;
  address?: string | null;
}

export interface RouteGeometry {
  type: "LineString";
  coordinates: [number, number][];
}

export interface RouteData {
  geometry: RouteGeometry;
  duration?: number;
  distance?: number;
  mode: TransportMode;
}

export interface DesirePathData {
  geometry: RouteGeometry;
}

export interface SplitDesirePath {
  id: string;
  segmentIndex: number;
  geometry: RouteGeometry;
  segments: [number, number][][];
}

export interface RouteResponse {
  route: RouteData;
  desire_path: DesirePathData | null;
  desire_path_segments?: [number, number][][];
  vote_mode?: TransportMode;
}

export interface GeocodeResult {
  lat: number;
  lon: number;
  display_name: string;
}

export interface MapState {
  revision: number;
  overlays: Record<string, GeoJSONOverlay>;
}

// Raw state from server (before parsing)
export interface RawMapState {
  revision: number;
  overlays: Record<string, GeoJSONOverlay>;
}

export interface GeoJSONOverlay {
  type: "geojson";
  data: GeoJSON.FeatureCollection;
  options?: {
    style?: Record<string, unknown>;
  };
}

export interface WebSocketMessage {
  type: string;
  state?: MapState;
}

export interface GraphData {
  nodes: [number, number][];                              // [lat, lon]
  edges: [number, number, string, string, number][];      // [from_idx, to_idx, name, highway, length_m]
  node_votes?: number[];                                  // Vote count for each node
  edge_votes?: number[];                                  // Vote count for each edge
  vote_type_legend?: string[];                            // Unique vote type labels
  edge_vote_types?: [number, number][][];                  // Per-edge [legend_idx, count] pairs, sorted by frequency
}

// Vote type suggestion for the selector
export interface VoteTypeSuggestion {
  label: string;          // Natural language suggestion, e.g. "Add bike lane"
  pointType: "route" | "point";  // route = 2 points, point = 1 point
}
