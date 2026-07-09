// Opt-in perf harness — measures computeRouteProposals against the real
// nyc-bikes graph served by the local dev Flask (needs the dev stack up).
// Skipped unless PERF=1:  PERF=1 npx vitest run …/routeProposals.perf.test.ts
// Reference numbers (M-series laptop, 3.3M edges, ~183k voted): job setup
// ~45ms, worst per-type slice ~250ms, full recompute ~750ms, PBTP scan ~80ms.
import { describe, it, expect } from "vitest";
import { decodeTopologyBin, buildNodeAdj, buildBlockIndex } from "./graphTopology";
import { computeRouteProposals, createRouteProposalJob } from "./routeProposals";
import { selectTopProposals } from "./topProposals";

const RUN = process.env.PERF === "1";

describe.runIf(RUN)("routeProposals perf (real nyc-bikes graph)", () => {
  it("times computeRouteProposals", async () => {
    const base = "http://localhost:5001/api";
    const topoRes = await fetch(`${base}/graph-topology?map=nyc-bikes&format=bin`);
    const buf = await topoRes.arrayBuffer();
    const t0 = performance.now();
    const topo = decodeTopologyBin(buf);
    const tDecode = performance.now() - t0;

    const votesRes = await fetch(`${base}/graph-votes?map=nyc-bikes&mode=bikepaths`);
    const votes = await votesRes.json();

    const t1 = performance.now();
    const adj = buildNodeAdj(topo);
    const tAdj = performance.now() - t1;
    const t2 = performance.now();
    const blockIndex = buildBlockIndex(topo);
    const tBlockIdx = performance.now() - t2;

    const data = { ...topo, ...votes };

    // Warm + 3 timed runs with the prebuilt index (the new in-app path).
    computeRouteProposals(topo, adj, data, { blockIndex });
    const runs: number[] = [];
    let n = 0;
    for (let i = 0; i < 3; i++) {
      const s = performance.now();
      n = computeRouteProposals(topo, adj, data, { blockIndex }).length;
      runs.push(performance.now() - s);
    }

    // Per-slice (per-type) durations — the longest is the worst main-thread
    // block the sliced in-app job can cause.
    const tJob0 = performance.now();
    const job = createRouteProposalJob(topo, adj, data, { blockIndex });
    const setupMs = performance.now() - tJob0;
    const sliceMs = job.types.map((t) => {
      const s = performance.now();
      job.step(t);
      return Math.round(performance.now() - s);
    });

    const t3 = performance.now();
    const winners = selectTopProposals(data, 12345, 20, 120);
    const tPbtp = performance.now() - t3;

    // eslint-disable-next-line no-console
    console.log(JSON.stringify({
      nEdges: topo.nEdges,
      decodeMs: Math.round(tDecode),
      nodeAdjMs: Math.round(tAdj),
      blockIndexMs: Math.round(tBlockIdx),
      rbtpRunsMs: runs.map(Math.round),
      corridors: n,
      jobSetupMs: Math.round(setupMs),
      sliceMs,
      pbtpMs: Math.round(tPbtp),
      pbtpWinners: winners.length,
    }));
    expect(n).toBeGreaterThan(0);
  }, 300_000);
});
