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
  isModified?: boolean;
}

export interface EditVertex {
  position: LatLng;
  coordIndex: number; // index into the original route coordinates array
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
  hexes: Record<string, number>;
  max_votes: number;
}

export interface MapState {
  revision: number;
  overlays: Record<string, GeoJSONOverlay>;
  hex_overlay?: HexOverlay;
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
