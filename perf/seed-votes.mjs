/**
 * Seed route-shaped votes onto the local map so the heatmap's multi-pass
 * voted-edge rendering path is exercised realistically (a fresh DB has zero
 * votes → only the cheap baseline pass runs, which under-stresses the
 * renderer).
 *
 * Real votes come from submitted routes — contiguous corridors of edges — so
 * the seeder walks random chains along the graph adjacency rather than
 * sprinkling isolated edges. Chain lengths and per-chain vote counts follow a
 * power-law-ish distribution: a few hot corridors, many warm ones.
 *
 * Usage: node seed-votes.mjs [--api http://localhost:5001/api] [--map nyc-bikes]
 *        [--mode bikepaths] [--chains 400] [--maxVotes 25]
 */
const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, arr) => {
    if (a.startsWith("--")) acc.push([a.slice(2), arr[i + 1]]);
    return acc;
  }, [])
);
const API = args.api ?? "http://localhost:5001/api";
const MAP = args.map ?? "nyc-bikes";
const MODE = args.mode ?? "bikepaths";
const N_CHAINS = parseInt(args.chains ?? "400", 10);
const MAX_VOTES = parseInt(args.maxVotes ?? "25", 10);

async function main() {
  const topo = await (await fetch(`${API}/graph-topology?map=${MAP}`)).json();
  const edgeCount = topo.edges.length;
  console.log(`graph has ${edgeCount} edges; seeding ${N_CHAINS} route-shaped chains`);

  const mapCfg = await (await fetch(`${API}/maps/${MAP}`)).json();
  const voteTypes = (mapCfg.voteTypes ?? []).map((v) => v.label);
  if (voteTypes.length === 0) throw new Error("no vote types on map");

  // Node → incident edge ids, for walking contiguous chains.
  const adj = new Map();
  for (let i = 0; i < edgeCount; i++) {
    const [a, b] = topo.edges[i];
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
    let node = topo.edges[edgeId][rand() < 0.5 ? 0 : 1];
    const chain = [];
    for (let step = 0; step < chainLen; step++) {
      chain.push(edgeId);
      used.add(edgeId);
      const [a, b] = topo.edges[edgeId];
      node = a === node ? b : a; // cross to the far node
      const options = (adj.get(node) ?? []).filter((e) => !used.has(e));
      if (options.length === 0) break;
      edgeId = options[Math.floor(rand() * options.length)];
    }
    // Power law: most chains get 1-3 votes, a few become hot corridors.
    const votes = Math.max(1, Math.floor(MAX_VOTES * Math.pow(rand(), 3)));
    const label = voteTypes[Math.floor(rand() * voteTypes.length)];
    for (let v = 0; v < votes; v++) {
      for (const eid of chain) {
        total++;
        jobs.push({ edgeId: eid, label, voter: `seed-${c}-${v}` });
      }
    }
  }
  console.log(`casting ${total} votes across chains...`);

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
              map: MAP, edge_id: j.edgeId, mode: MODE,
              vote_type: j.label, direction: 1, voter_id: j.voter,
            }),
          });
          if (!r.ok) failed++;
          else done++;
        } catch { failed++; }
        if ((done + failed) % 2000 === 0) console.log(`  ${done + failed}/${total}`);
      }
    })
  );
  console.log(`done: ${done} ok, ${failed} failed`);
}

main().catch((e) => { console.error(e); process.exit(1); });
