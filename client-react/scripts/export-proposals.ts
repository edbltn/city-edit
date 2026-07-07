// ==========================================================================
// export-proposals.ts — dump top proposals (PBTPs + RBTPs) as plain JSON
// ==========================================================================
// Helper for scripts/export_qgis_artifacts.py. Runs the SAME modules the app
// uses (topProposals.ts / routeProposals.ts / graphTopology.ts) against the
// live local API, so the exported proposals are byte-faithful to what the
// client would show — no reimplementation to drift.
//
// Run from client-react/ (vite-node resolves the src/ imports):
//   node_modules/.bin/vite-node scripts/export-proposals.ts -- \
//     <apiBase> <mapSlug> <mode> <outJsonPath>
//
// The one deliberate divergence from the app: the tiebreak salt is fixed to 0
// (the app randomizes it per page load to rotate equal-net proposals), so the
// export is deterministic for a given (topology, vote state).

import { writeFileSync } from "node:fs";

import {
  buildNodeAdj,
  decodeTopologyBin,
  edgeFrom,
  edgeTo,
  nodeLatLng,
  type GraphTopology,
} from "../src/components/GraphLayer/graphTopology";
import { selectTopProposals } from "../src/components/GraphLayer/topProposals";
import {
  computeRouteProposals,
  dropPointsCoveredByRoutes,
  type RouteProposal,
} from "../src/components/GraphLayer/routeProposals";

const TIEBREAK_SALT = 0; // fixed for reproducibility (app uses a per-load random)
const POINT_PROPOSAL_LIMIT = 20; // GraphLayer's TOP_PROPOSAL_LIMIT

/** Node sequence of a simple path, walked from `startNode` along ordered edges. */
function chainPathNodes(topo: GraphTopology, edgeIds: number[], startNode: number): number[] {
  const nodes = [startNode];
  let cur = startNode;
  for (const e of edgeIds) {
    const u = edgeFrom(topo, e);
    const v = edgeTo(topo, e);
    cur = u === cur ? v : u;
    nodes.push(cur);
  }
  return nodes;
}

async function main() {
  // vite-node passes everything after "--" through; tolerate its absence.
  const args = process.argv.slice(2).filter((a) => a !== "--");
  const [apiBase, slug, mode, outPath] = args;
  if (!apiBase || !slug || !mode || !outPath) {
    console.error("usage: vite-node scripts/export-proposals.ts -- <apiBase> <slug> <mode> <outJson>");
    process.exit(2);
  }

  const topoResp = await fetch(`${apiBase}/api/graph-topology?map=${slug}&format=bin`);
  if (!topoResp.ok) throw new Error(`graph-topology ${topoResp.status}`);
  const topo = decodeTopologyBin(await topoResp.arrayBuffer());

  const votesResp = await fetch(`${apiBase}/api/graph-votes?map=${slug}&mode=${mode}`);
  if (!votesResp.ok) throw new Error(`graph-votes ${votesResp.status}`);
  const votes = await votesResp.json();

  if (votes.n_edges !== topo.nEdges || votes.n_nodes !== topo.nNodes) {
    throw new Error(
      `votes/topology mismatch: votes ${votes.n_nodes}n/${votes.n_edges}e vs ` +
        `topology ${topo.nNodes}n/${topo.nEdges}e — is a stale cache being served?`,
    );
  }

  const adj = buildNodeAdj(topo);
  const data = {
    vote_type_legend: votes.vote_type_legend as string[],
    edge_vote_types: votes.edge_vote_types as [number, number, number][][],
  };

  // RBTPs — the deterministic corridor extraction (limit 20, all defaults).
  const routes: RouteProposal[] = computeRouteProposals(topo, adj, data);

  // PBTPs — the four-step pin selection GraphLayer runs.
  const points = selectTopProposals({ ...topo, ...data }, TIEBREAK_SALT, POINT_PROPOSAL_LIMIT);

  // Flag (rather than drop) points a same-type route already covers, so the
  // QGIS layer can show both regimes and filter interactively.
  const surviving = new Set(dropPointsCoveredByRoutes(points, routes).map((p) => p.edgeIdx));

  const out = {
    slug,
    mode,
    salt: TIEBREAK_SALT,
    legend: data.vote_type_legend,
    points: points.map((p) => ({
      edge_id: p.edgeIdx,
      label: p.label,
      legend_idx: p.legendIdx,
      net: p.count,
      ends: [nodeLatLng(topo, edgeFrom(topo, p.edgeIdx)), nodeLatLng(topo, edgeTo(topo, p.edgeIdx))],
      covered_by_route: !surviving.has(p.edgeIdx),
    })),
    routes: routes.map((r) => ({
      id: r.id,
      label: r.label,
      legend_idx: r.legendIdx,
      score: r.score,
      edge_ids: r.edgeIds,
      block_edge_ids: r.blockEdgeIds,
      n_blocks: r.blocks.length,
      anchors: r.anchors,
      anchor_coords: r.anchorCoords.map((c) => [c.lat, c.lng]),
      path_latlngs: chainPathNodes(topo, r.edgeIds, r.anchors[0]).map((n) => nodeLatLng(topo, n)),
    })),
  };

  writeFileSync(outPath, JSON.stringify(out));
  console.error(
    `[export-proposals] ${slug}/${mode}: ${out.points.length} point + ${out.routes.length} route proposals -> ${outPath}`,
  );
}

main().catch((e) => {
  console.error(`[export-proposals] FAILED: ${e?.stack || e}`);
  process.exit(1);
});
