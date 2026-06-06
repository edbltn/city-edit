// ==========================================================================
// Type Definitions for City Edit
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
  /** The exact graph edge this point votes on, when it was chosen by selecting a
   *  specific edge (a top-proposal indicator) rather than dropped at a bare
   *  coordinate. The banner's Cast buttons vote on THIS edge instead of
   *  re-snapping the coordinate — which is how the banner and the in-map
   *  proposal modal stay on the same edge (re-snapping a midpoint can land on a
   *  neighbouring edge/node, and then the two disagree forever). Absent for plain
   *  map clicks, drags, and address searches, which re-snap the coordinate. */
  voteEdgeId?: number | null;
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
  edgeIds: number[];
}

export interface VoteDelta {
  rev: number;
  edges: number[];
  m: string;
  vt: number;
  vtLabel?: string;
  /** Vote direction: 1 (up) | -1 (down). Absent on legacy deltas → treat as up. */
  dir?: number;
  /** True when this delta reverses a prior opposite vote (move one vote across directions). */
  reversed?: boolean;
  /** Authoritative post-write [up, down] per edge id for this vote type. Present
   *  on directional votes → clients SET (idempotent) instead of incrementing. */
  vtCounts?: Record<string, [number, number]>;
}

export interface RouteResponse {
  route: RouteData;
  desire_path: DesirePathData | null;
  desire_path_segments?: [number, number][][];
  edge_ids?: number[];
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
  nodes: [number, number][];
  edges: [number, number, string][];
  node_votes?: number[];
  edge_votes?: number[];
  vote_type_legend?: string[];
  // Per-edge / per-node vote-type breakdown: [legendIdx, up, down][]
  edge_vote_types?: [number, number, number][][];
  node_vote_types?: [number, number, number][][];
  rev?: number;
  vote_types?: Record<string, string>;
}

// Vote type suggestion for the selector
export interface VoteTypeSuggestion {
  label: string;
  icon: string;
  pointType: "route" | "point";
}

// A waypoint's match to a top proposal: the proposal's edge index and vote-type
// label (the label builds the pin icon's glyph). Lives here (not in GraphLayer)
// so the component file exports only its component (react-refresh).
export interface ProposalMatch {
  edgeIdx: number;
  label: string;
}
