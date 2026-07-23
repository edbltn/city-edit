/**
 * Seed route-shaped votes onto the local map so the heatmap's voted-edge
 * rendering path is exercised realistically (a fresh DB has zero votes → only
 * the cheap baseline pass runs, which under-stresses the renderer).
 *
 * Real votes come from submitted routes — contiguous corridors of edges — so
 * the seeder walks random chains along the graph adjacency rather than
 * sprinkling isolated edges. Chain lengths and per-chain vote counts follow a
 * power-law-ish distribution: a few hot corridors, many warm ones.
 *
 * Street maps serve binary topology only (format=bin, GTB1/GTB2 — see
 * client-react/src/components/GraphLayer/graphTopology.ts); the header/edge
 * decode is ported here. Casts go through /api/vote with the WHOLE chain as
 * edge_ids in one request: voting is block-scoped clear-then-cast per voter,
 * so casting a chain edge-by-edge under one voter_id would wipe the earlier
 * edges of the same block — one route-shaped cast per (chain, vote) matches
 * how real route votes land.
 *
 * Usage: node seed-votes.mjs [--api http://localhost:5001/api] [--map nyc-walkways]
 *        [--mode walk] [--chains 400] [--maxVotes 25]
 */
const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, arr) => {
    if (a.startsWith("--")) acc.push([a.slice(2), arr[i + 1]]);
    return acc;
  }, [])
);
const API = args.api ?? "http://localhost:5001/api";
const MAP = args.map ?? "nyc-walkways";
const MODE = args.mode ?? "walk";
const N_CHAINS = parseInt(args.chains ?? "400", 10);
const MAX_VOTES = parseInt(args.maxVotes ?? "25", 10);

// 'GTB1' / 'GTB2' little-endian as uint32 (G=0x47, T=0x54, B=0x42).
const TOPOLOGY_BIN_MAGIC_V1 = 0x31425447;
const TOPOLOGY_BIN_MAGIC_V2 = 0x32425447;

/** Decode just enough of a GTB1/GTB2 blob to enumerate edge node pairs:
 *  [magic u32, nNodes u32, nEdges u32, (GTB2: nBlocks u32)] + 2·nNodes i32
 *  coords + 2·nEdges u32 ends (+ optional trailing edge_block_id, ignored). */
function decodeTopologyEnds(buf) {
  if (buf.byteLength < 12) throw new Error(`topology blob too small (${buf.byteLength} bytes)`);
  const head = new Uint32Array(buf, 0, 3);
  const magic = head[0];
  const v2 = magic === TOPOLOGY_BIN_MAGIC_V2;
  if (!v2 && magic !== TOPOLOGY_BIN_MAGIC_V1) {
    throw new Error(`bad topology magic 0x${magic.toString(16)}`);
  }
  const headerBytes = v2 ? 16 : 12;
  const nNodes = head[1];
  const nEdges = head[2];
  const endsOffset = headerBytes + nNodes * 8;
  if (buf.byteLength < endsOffset + nEdges * 8) {
    throw new Error(`topology blob truncated: ${buf.byteLength} bytes for nNodes=${nNodes}, nEdges=${nEdges}`);
  }
  return { nEdges, ends: new Uint32Array(buf, endsOffset, nEdges * 2) };
}

async function main() {
  const r = await fetch(`${API}/graph-topology?map=${MAP}&format=bin`);
  if (!r.ok) throw new Error(`topology fetch failed: ${r.status}`);
  const { nEdges: edgeCount, ends } = decodeTopologyEnds(await r.arrayBuffer());
  console.log(`graph has ${edgeCount} edges; seeding ${N_CHAINS} route-shaped chains`);

  const mapCfg = await (await fetch(`${API}/maps/${MAP}`)).json();
  const voteTypes = (mapCfg.voteTypes ?? []).map((v) => v.label);
  if (voteTypes.length === 0) throw new Error("no vote types on map");

  // Node → incident edge ids, for walking contiguous chains.
  const adj = new Map();
  for (let i = 0; i < edgeCount; i++) {
    const a = ends[2 * i];
    const b = ends[2 * i + 1];
    if (!adj.has(a)) adj.set(a, []);
    if (!adj.has(b)) adj.set(b, []);
    adj.get(a).push(i);
    adj.get(b).push(i);
  }

  // Deterministic PRNG so reseeding produces the same distribution.
  let s = 42;
  const rand = () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);

  let total = 0;
  const jobs = [];
  for (let c = 0; c < N_CHAINS; c++) {
    // Walk a contiguous chain from a random edge: at each node pick a random
    // incident edge we haven't used in this chain (approximates a route).
    const chainLen = 5 + Math.floor(35 * Math.pow(rand(), 2)); // 5–40 edges
    const used = new Set();
    let edgeId = Math.floor(rand() * edgeCount);
    let node = rand() < 0.5 ? ends[2 * edgeId] : ends[2 * edgeId + 1];
    const chain = [];
    for (let step = 0; step < chainLen; step++) {
      chain.push(edgeId);
      used.add(edgeId);
      const a = ends[2 * edgeId];
      const b = ends[2 * edgeId + 1];
      node = a === node ? b : a; // cross to the far node
      const options = (adj.get(node) ?? []).filter((e) => !used.has(e));
      if (options.length === 0) break;
      edgeId = options[Math.floor(rand() * options.length)];
    }
    // Power law: most chains get 1-3 votes, a few become hot corridors.
    const votes = Math.max(1, Math.floor(MAX_VOTES * Math.pow(rand(), 3)));
    const label = voteTypes[Math.floor(rand() * voteTypes.length)];
    for (let v = 0; v < votes; v++) {
      total += chain.length;
      jobs.push({ edgeIds: chain, label, voter: `seed-${c}-${v}` });
    }
  }
  console.log(`casting ${total} edge-votes across ${jobs.length} route casts...`);

  let done = 0, failed = 0;
  const CONCURRENCY = 24;
  const queue = [...jobs];
  await Promise.all(
    Array.from({ length: CONCURRENCY }, async () => {
      while (queue.length) {
        const j = queue.pop();
        try {
          const r = await fetch(`${API}/vote`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              map: MAP, edge_ids: j.edgeIds, mode: MODE,
              vote_type: j.label, direction: 1, voter_id: j.voter,
            }),
          });
          if (!r.ok) failed++;
          else done++;
        } catch { failed++; }
        if ((done + failed) % 200 === 0) console.log(`  ${done + failed}/${jobs.length}`);
      }
    })
  );
  console.log(`done: ${done} casts ok, ${failed} failed`);
}

main().catch((e) => { console.error(e); process.exit(1); });
