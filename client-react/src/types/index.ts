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

export interface HexOverlay {
  res?: number;                      // Resolution level
  hexes: Record<string, number>;     // Expanded format (used internally)
  max_votes: number;
  suggestionLegend?: string[];                  // Unique vote_type labels
  suggestions?: Record<string, number[]>;       // hex_id → indices into legend (top 3)
  rawCounts?: Record<string, number>;           // hex_id → raw (unweighted) vote count
}

// Compact format from server (h = array of [hex_id, weight] tuples, m = max_votes)
export interface HexOverlayCompact {
  res: number;
  h: [string, number][];
  m: number;
  sl?: string[];                          // Suggestion legend
  s?: Record<string, number[]>;           // hex_id → legend indices (top 3)
  rc?: Record<string, number>;            // hex_id → raw vote count
}

export interface MapState {
  revision: number;
  overlays: Record<string, GeoJSONOverlay>;
  hex_overlays?: Record<number, HexOverlay>;  // All resolutions: {10: {...}, 11: {...}, ...}
}

// Raw state from server (before parsing)
export interface RawMapState {
  revision: number;
  overlays: Record<string, GeoJSONOverlay>;
  hex_overlays?: Record<number, HexOverlayCompact>;  // All resolutions in compact format
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

export interface ModeOption {
  mode: TransportMode;
  icon: string;
  label: string;
}

export const MODES: ModeOption[] = [
  { mode: "bike", icon: "\u{1F6B2}", label: "Bike" },
  { mode: "walk", icon: "\u{1F6B6}", label: "Walk" },
  { mode: "drive", icon: "\u{1F697}", label: "Drive" },
];

// Vote type suggestion for the selector
export interface VoteTypeSuggestion {
  label: string;          // Natural language suggestion, e.g. "Add bike lane"
  pointType: "route" | "point";  // route = 2 points, point = 1 point
  modes: TransportMode[]; // Which modes this suggestion appears for
}
