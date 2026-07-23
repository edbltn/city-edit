/**
 * Concurrency load test against a prod-shaped server (gunicorn + gevent).
 *
 * Phases:
 *  1. Connect N WebSocket clients (default 300), all receiving one map's deltas.
 *  2. Cast a vote → measure delta fan-out latency to ALL clients (p50/p95/max).
 *  3. While sockets stay connected: M concurrent "cold visitor" API bursts
 *     (sparse graph-votes + 4 block tiles + binary topology) → per-endpoint p95.
 *  4. Sustained voting (default 20 votes) → verify every client received every
 *     vote's delta item with monotonically increasing revs. (Our DeltaHub
 *     merges batches into {"type":"deltas","items":[…]} frames and revs can
 *     legitimately skip — hydration/manual INCR bump the rev without a delta —
 *     so the check is monotonic order + completeness, not rev[j]==rev[j-1]+1.)
 *
 * Usage: node loadtest.mjs [--base http://localhost:5001] [--ws ws://localhost:5001/ws]
 *        [--map nyc-walkways] [--mode walk] [--city nyc] [--clients 300]
 *        [--visitors 100] [--votes 20] [--edges 500000] [--view lat,lng]
 */
import WebSocket from "ws";

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, arr) => {
    if (a.startsWith("--")) acc.push([a.slice(2), arr[i + 1]]);
    return acc;
  }, [])
);
const BASE = args.base ?? "http://localhost:5001";
const WS_BASE = args.ws ?? BASE.replace("http", "ws") + "/ws";
const MAP = args.map ?? "nyc-walkways";
const MODE = args.mode ?? "walk";
const CITY = args.city ?? "nyc";
const N_CLIENTS = parseInt(args.clients ?? "300", 10);
const N_VISITORS = parseInt(args.visitors ?? "100", 10);
const N_VOTES = parseInt(args.votes ?? "20", 10);
// Random-edge upper bound for cast targets (well inside every city graph).
const N_EDGES = parseInt(args.edges ?? "500000", 10);
// Tile-burst center (defaults to downtown NYC, matching measure.mjs's view).
const [VIEW_LAT, VIEW_LNG] = (args.view ?? "40.722,-74.000").split(",").map(Number);

const pct = (xs, p) => {
  const s = [...xs].sort((a, b) => a - b);
  return s.length ? s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))] : null;
};
const fmt = (xs) => `p50 ${Math.round(pct(xs, 50))}ms  p95 ${Math.round(pct(xs, 95))}ms  max ${Math.round(Math.max(...xs))}ms`;

// Slippy-map tile coords for the burst URLs (blocks tiles are standard z/x/y).
const tileXY = (lat, lng, z) => {
  const n = 2 ** z;
  const latRad = (lat * Math.PI) / 180;
  return {
    x: Math.floor(((lng + 180) / 360) * n),
    y: Math.floor(((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n),
  };
};
const T14 = tileXY(VIEW_LAT, VIEW_LNG, 14);
const T13 = tileXY(VIEW_LAT, VIEW_LNG, 13);
const T15 = tileXY(VIEW_LAT, VIEW_LNG, 15);

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
    const c = { ws, revs: [], revTimes: [] };
    ws.on("message", (raw) => {
      const msg = JSON.parse(raw.toString());
      const now = Date.now();
      if (msg.type === "init") {
        if (++inited === N_CLIENTS) resolve();
      } else if (msg.type === "deltas") {
        // DeltaHub frame: one message, many per-vote items (each carries its rev).
        for (const item of msg.items ?? []) {
          c.revs.push(item.rev);
          c.revTimes.push(now);
        }
      } else if (msg.type === "delta" && msg.rev != null) {
        // Unmerged single-delta fallback (older servers).
        c.revs.push(msg.rev);
        c.revTimes.push(now);
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
      map: MAP, edge_id: Math.floor(Math.random() * N_EDGES), mode: MODE,
      vote_type: voteType, direction: 1, voter_id: `loadtest-${Math.random().toString(36).slice(2, 10)}`,
    }),
  });

// ── Phase 2: single-vote fan-out latency ────────────────────────────────────
const before = clients.map((c) => c.revs.length);
const tCast = Date.now();
const vresp = await castVote();
if (!vresp.ok) throw new Error(`vote failed: ${vresp.status}`);
await new Promise((r) => setTimeout(r, 3000));
const latencies = [];
let received = 0;
clients.forEach((c, i) => {
  if (c.revs.length > before[i]) {
    received++;
    latencies.push(c.revTimes[before[i]] - tCast);
  }
});
console.log(`fan-out: ${received}/${N_CLIENTS} clients got the delta — ${fmt(latencies)}`);

// ── Phase 3: concurrent cold-visitor API bursts while sockets stay open ────
console.log(`${N_VISITORS} concurrent visitor bursts (sparse votes+4 tiles+topology)…`);
const timings = { votes: [], tile: [], topology: [] };
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
        timed("votes", `${BASE}/api/graph-votes?map=${MAP}&mode=${MODE}&format=sparse&_=${i}`),
        timed("tile", `${BASE}/api/tile/${CITY}/blocks/14/${T14.x + (i % 8)}/${T14.y}.mvt`),
        timed("tile", `${BASE}/api/tile/${CITY}/blocks/14/${T14.x}/${T14.y + (i % 8)}.mvt`),
        timed("tile", `${BASE}/api/tile/${CITY}/blocks/13/${T13.x + (i % 4)}/${T13.y}.mvt`),
        timed("tile", `${BASE}/api/tile/${CITY}/blocks/15/${T15.x + (i % 8)}/${T15.y}.mvt`),
        timed("topology", `${BASE}/api/graph-topology?map=${MAP}&format=bin&_=${i}`),
      ]);
    })()
  )
);
console.log(`burst wall-clock: ${Date.now() - tBurst}ms`);
for (const [k, xs] of Object.entries(timings)) console.log(`  ${k.padEnd(9)} ${fmt(xs)}  (${xs.length} reqs)`);

// ── Phase 4: sustained votes, verify order + completeness ───────────────────
console.log(`casting ${N_VOTES} votes over ~4s…`);
const baseCounts = clients.map((c) => c.revs.length);
for (let i = 0; i < N_VOTES; i++) {
  await castVote();
  await new Promise((r) => setTimeout(r, 4000 / N_VOTES));
}
await new Promise((r) => setTimeout(r, 3000));
let ok = 0, disorder = 0;
clients.forEach((c, i) => {
  const got = c.revs.length - baseCounts[i];
  if (got === N_VOTES) ok++;
  // Monotonic-increasing revs (numeric gaps are legitimate — see header).
  const revs = c.revs.slice(baseCounts[i]);
  for (let j = 1; j < revs.length; j++) if (revs[j] <= revs[j - 1]) disorder++;
});
console.log(`sustained: ${ok}/${N_CLIENTS} clients got all ${N_VOTES} delta items, rev order violations: ${disorder}`);

clients.forEach((c) => c.ws.close());
const pass = received === N_CLIENTS && ok === N_CLIENTS && disorder === 0;
console.log(pass ? "LOADTEST PASS" : "LOADTEST FAIL");
process.exit(pass ? 0 : 1);
