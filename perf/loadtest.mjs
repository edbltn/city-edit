/**
 * Concurrency load test against a prod-shaped server (gunicorn + gevent).
 *
 * Phases:
 *  1. Connect N WebSocket clients (default 300), all receiving one map's deltas.
 *  2. Cast a vote → measure delta fan-out latency to ALL clients (p50/p95/max).
 *  3. While sockets stay connected: M concurrent "cold visitor" API bursts
 *     (heat + votes + 4 tiles + gzipped topology) → per-endpoint p95.
 *  4. Sustained voting (default 20 votes) → verify every client received every
 *     delta in order (no gaps → no client would need a recovery refetch).
 *
 * Usage: node loadtest.mjs [--base http://localhost:5002] [--ws ws://localhost:5002/ws]
 *        [--map nyc-bikes] [--mode bikepaths] [--clients 300] [--visitors 100]
 */
import WebSocket from "ws";

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, arr) => {
    if (a.startsWith("--")) acc.push([a.slice(2), arr[i + 1]]);
    return acc;
  }, [])
);
const BASE = args.base ?? "http://localhost:5002";
const WS_BASE = args.ws ?? BASE.replace("http", "ws") + "/ws";
const MAP = args.map ?? "nyc-bikes";
const MODE = args.mode ?? "bikepaths";
const N_CLIENTS = parseInt(args.clients ?? "300", 10);
const N_VISITORS = parseInt(args.visitors ?? "100", 10);
const N_VOTES = parseInt(args.votes ?? "20", 10);

const pct = (xs, p) => {
  const s = [...xs].sort((a, b) => a - b);
  return s.length ? s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))] : null;
};
const fmt = (xs) => `p50 ${Math.round(pct(xs, 50))}ms  p95 ${Math.round(pct(xs, 95))}ms  max ${Math.round(Math.max(...xs))}ms`;

// Pick a real vote type for the map
const mapCfg = await (await fetch(`${BASE}/api/maps/${MAP}`)).json();
const voteType = (mapCfg.voteTypes ?? [])[0]?.label;
if (!voteType) throw new Error("no vote types on map");

// ── Phase 1: connect WS clients ─────────────────────────────────────────────
console.log(`connecting ${N_CLIENTS} WebSocket clients…`);
const clients = [];
let inited = 0;
const t0 = Date.now();
await new Promise((resolve, reject) => {
  for (let i = 0; i < N_CLIENTS; i++) {
    const ws = new WebSocket(`${WS_BASE}?map=${MAP}`);
    const c = { ws, deltas: [], deltaTimes: [] };
    ws.on("message", (raw) => {
      const msg = JSON.parse(raw.toString());
      if (msg.type === "init") {
        if (++inited === N_CLIENTS) resolve();
      } else if (msg.rev != null) {
        c.deltas.push(msg.rev);
        c.deltaTimes.push(Date.now());
      }
    });
    ws.on("error", (e) => reject(new Error(`ws ${i}: ${e.message}`)));
    clients.push(c);
  }
  setTimeout(() => reject(new Error(`only ${inited}/${N_CLIENTS} inited after 60s`)), 60000);
});
console.log(`all ${N_CLIENTS} connected+inited in ${Date.now() - t0}ms`);

const castVote = () =>
  fetch(`${BASE}/api/vote`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      map: MAP, edge_id: Math.floor(Math.random() * 500000), mode: MODE,
      vote_type: voteType, direction: 1, voter_id: `loadtest-${Math.random().toString(36).slice(2, 10)}`,
    }),
  });

// ── Phase 2: single-vote fan-out latency ────────────────────────────────────
const before = clients.map((c) => c.deltas.length);
const tCast = Date.now();
const vresp = await castVote();
if (!vresp.ok) throw new Error(`vote failed: ${vresp.status}`);
await new Promise((r) => setTimeout(r, 3000));
const latencies = [];
let received = 0;
clients.forEach((c, i) => {
  if (c.deltas.length > before[i]) {
    received++;
    latencies.push(c.deltaTimes[before[i]] - tCast);
  }
});
console.log(`fan-out: ${received}/${N_CLIENTS} clients got the delta — ${fmt(latencies)}`);

// ── Phase 3: concurrent cold-visitor API bursts while sockets stay open ────
console.log(`${N_VISITORS} concurrent visitor bursts (heat+votes+4 tiles+topology)…`);
const timings = { heat: [], votes: [], tile: [], topology: [] };
const timed = async (bucket, url) => {
  const s = Date.now();
  const r = await fetch(url, { headers: { "Accept-Encoding": "gzip" } });
  await r.arrayBuffer();
  if (!r.ok && r.status !== 204) throw new Error(`${url} -> ${r.status}`);
  timings[bucket].push(Date.now() - s);
};
const tBurst = Date.now();
await Promise.all(
  Array.from({ length: N_VISITORS }, (_, i) =>
    (async () => {
      await Promise.all([
        timed("heat", `${BASE}/api/heat?map=${MAP}&mode=${MODE}&_=${i}`),
        timed("votes", `${BASE}/api/graph-votes?map=${MAP}&mode=${MODE}&_=${i}`),
        timed("tile", `${BASE}/api/tile/nyc/14/4825/${6150 + (i % 8)}.mvt`),
        timed("tile", `${BASE}/api/tile/nyc/14/${4820 + (i % 8)}/6156.mvt`),
        timed("tile", `${BASE}/api/tile/nyc/13/${2410 + (i % 4)}/3078.mvt`),
        timed("tile", `${BASE}/api/tile/nyc/15/${9650 + (i % 8)}/12312.mvt`),
        timed("topology", `${BASE}/api/graph-topology?map=${MAP}&_=${i}`),
      ]);
    })()
  )
);
console.log(`burst wall-clock: ${Date.now() - tBurst}ms`);
for (const [k, xs] of Object.entries(timings)) console.log(`  ${k.padEnd(9)} ${fmt(xs)}  (${xs.length} reqs)`);

// ── Phase 4: sustained votes, verify no gaps ────────────────────────────────
console.log(`casting ${N_VOTES} votes over ~4s…`);
const baseCounts = clients.map((c) => c.deltas.length);
for (let i = 0; i < N_VOTES; i++) {
  await castVote();
  await new Promise((r) => setTimeout(r, 4000 / N_VOTES));
}
await new Promise((r) => setTimeout(r, 3000));
let ok = 0, gaps = 0;
clients.forEach((c, i) => {
  const got = c.deltas.length - baseCounts[i];
  if (got === N_VOTES) ok++;
  const revs = c.deltas.slice(baseCounts[i]);
  for (let j = 1; j < revs.length; j++) if (revs[j] !== revs[j - 1] + 1) gaps++;
});
console.log(`sustained: ${ok}/${N_CLIENTS} clients got all ${N_VOTES} deltas, rev gaps: ${gaps}`);

clients.forEach((c) => c.ws.close());
const pass = received === N_CLIENTS && ok === N_CLIENTS && gaps === 0;
console.log(pass ? "LOADTEST PASS" : "LOADTEST FAIL");
process.exit(pass ? 0 : 1);
