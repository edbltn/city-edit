import type { GraphTopology } from "./graphTopology";
import type { RouteProposal } from "./routeProposals";
import type { HoverTarget } from "./spatialLookup";
import { nodeLat, nodeLon, edgeFrom, edgeTo } from "./graphTopology";

/**
 * The representative on-graph coordinate for a resolved target: an edge's
 * midpoint (where its indicator icon sits) or a node's position. This is the
 * ONE place that maps a target to a point — the pinned modal uses it as both
 * its anchor AND its React key, so the card tracks the selected FEATURE and is
 * never positioned by the raw click coordinates.
 */
export function targetLatLng(
  target: HoverTarget | null,
  data: GraphTopology | null
): { lat: number; lng: number } | null {
  if (!target || !data) return null;
  if (target.kind === "edge") {
    if (target.index >= data.nEdges) return null;
    const from = edgeFrom(data, target.index), to = edgeTo(data, target.index);
    if (from >= data.nNodes || to >= data.nNodes) return null;
    return {
      lat: (nodeLat(data, from) + nodeLat(data, to)) / 2,
      lng: (nodeLon(data, from) + nodeLon(data, to)) / 2,
    };
  }
  if (target.index >= data.nNodes) return null;
  return { lat: nodeLat(data, target.index), lng: nodeLon(data, target.index) };
}

/**
 * Display anchor for an RBTP diamond: its middle path edge's midpoint, else the
 * mean of its two anchors. ONE definition shared by the marker render, the
 * cluster fan-out, and the drop hit-test so they can never disagree on where a
 * diamond "is".
 */
export function rbtpDisplayPos(topo: GraphTopology, p: RouteProposal): [number, number] {
  const midEdge = p.edgeIds[Math.floor(p.edgeIds.length / 2)];
  if (midEdge != null && midEdge < topo.nEdges) {
    const from = edgeFrom(topo, midEdge), to = edgeTo(topo, midEdge);
    if (from < topo.nNodes && to < topo.nNodes) {
      return [
        (nodeLat(topo, from) + nodeLat(topo, to)) / 2,
        (nodeLon(topo, from) + nodeLon(topo, to)) / 2,
      ];
    }
  }
  return [
    (p.anchorCoords[0].lat + p.anchorCoords[1].lat) / 2,
    (p.anchorCoords[0].lng + p.anchorCoords[1].lng) / 2,
  ];
}

// Spread-map keys — the fanned-out grid mixes POINT proposals (edge-keyed
// squares) and ROUTE proposals (id-keyed diamonds), so keys carry the kind.
export const spreadKeyEdge = (edgeIdx: number) => `e${edgeIdx}`;
export const spreadKeyRoute = (id: string) => `r${id}`;

/**
 * True when a vote payload is indexed against the SAME topology we hold. Vote
 * arrays are sized to the server's current graph; the server stamps n_edges /
 * n_nodes onto the payload so the client can reject votes that don't line up
 * with its (possibly day-old cached) topology — painting a mismatch is what
 * indexed past the array ends and crashed mobile Safari. Falls back to the
 * edge_votes length when the dimensions aren't present (older server).
 */
export function votesMatchTopology(
  voteData: { n_edges?: number; n_nodes?: number; n_blocks?: number;
    edge_votes?: ArrayLike<unknown>; block_votes?: ArrayLike<unknown> },
  topology: GraphTopology | null,
): boolean {
  if (!topology) return false;
  const nEdges = voteData.n_edges ?? voteData.edge_votes?.length;
  if (nEdges != null && nEdges !== topology.nEdges) return false;
  if (voteData.n_nodes != null && voteData.n_nodes !== topology.nNodes) return false;
  // Block ids renumber on every blocks re-bake (SAME topology etag), so a body
  // built against a different block set colors the wrong polygons.
  const nBlocks = voteData.n_blocks ?? voteData.block_votes?.length;
  if (nBlocks != null && topology.nBlocks != null && nBlocks !== topology.nBlocks) {
    return false;
  }
  return true;
}

/**
 * True when /api/graph-votes served a snapshot OLDER than the live revision.
 *
 * The endpoint does this on purpose: under sustained voting the revision bumps
 * on every cast, so it serves the last complete snapshot (debounced, or stale
 * while a background rebuild runs) rather than rebuilding the full arrays per
 * vote. A live session never notices — it reconciles forward from WebSocket
 * deltas, which carry authoritative counts. A PAGE LOAD has no deltas to
 * reconcile from: it paints the body it was handed, full stop. So the caster
 * who reloads right after voting sees their own cast missing, with nothing in
 * the session to correct it. The server stamps both revisions on the response
 * so we can tell, and come back for the rebuilt body.
 *
 * False whenever the headers are absent (an older server, or a response whose
 * custom headers the browser won't expose) — never guess staleness.
 */
export function servedRevIsStale(response: { headers: Headers }): boolean {
  const servedRaw = response.headers.get("X-Vote-Rev");
  const currentRaw = response.headers.get("X-Vote-Rev-Current");
  if (servedRaw == null || currentRaw == null) return false;
  const served = Number(servedRaw);
  const current = Number(currentRaw);
  return Number.isFinite(served) && Number.isFinite(current) && current > served;
}
