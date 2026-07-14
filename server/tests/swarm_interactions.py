"""Multi-tenant interaction swarm: N simulated users across all live maps.

Every interaction a real client performs on map load and while voting is
timed individually, with a per-interaction TIMEOUT and a per-interaction
LATENCY BUDGET (p95). The run FAILS (exit 1) if any interaction class blows
its budget or errors — the point is a deployable go/no-go signal, not vibes.

Agent behavior (mirrors the React client):
  1. GET /api/maps/<slug>            (map meta)
  2. GET /api/graph-version          (cache key)
  3. GET /api/graph-topology?format=bin   (the big one — GTB2 blob)
  4. GET /api/graph-votes            (vote arrays + blocks)
  5. WS /ws?map=<slug>               (held open for the session)
  6. loop: POST /api/routes → POST /api/vote → GET /api/reverse-geocode

Tenancy: agents are spread across every map passed via --maps (default: all
public maps fetched from /api/maps), weighted toward --primary (default
nyc-walkways). 30 agents across ~16 maps ≈ the 30-tenant target.

Usage:
  python swarm_interactions.py --base https://cityedit.org --agents 30 --cycles 5
  python swarm_interactions.py --base http://localhost:5001 --agents 30
"""

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
import uuid

import aiohttp

# Per-interaction budgets: (hard timeout s, p95 budget s, max error rate).
# Budgets are for a WARM server (the deploy prewarms every map); the first
# agent per map may eat a cold snapshot build, which the p95 absorbs.
BUDGETS = {
    "map_meta":        (10,  1.0, 0.02),
    "graph_version":   (10,  1.5, 0.02),
    # topology_bin is BANDWIDTH-bound when the swarm runs from one machine:
    # N agents × ~18MB share the test host's downlink and finish together, so
    # client p95 ≈ N×size/bandwidth regardless of server health (single-stream
    # prod: ~1s; server-side latency: <0.3s). The budget below allows a 40-agent
    # run on a ~100Mbps link; judge the server by errors + Cloud Run latency.
    "topology_bin":    (120, 90.0, 0.02),
    "graph_votes":     (30,  5.0, 0.02),
    "ws_connect":      (15,  5.0, 0.05),
    "route":           (20,  3.0, 0.05),   # OSRM + edge mapping
    "vote":            (20,  3.0, 0.02),
    "reverse_geocode": (15,  3.0, 0.05),
}

VOTE_TYPES = [
    "Improve bike lane", "Add bike lane", "Widen sidewalk",
    "More trees", "Better lighting",
]


class Metrics:
    def __init__(self):
        self.samples = {}  # interaction -> list[(latency, status, slug)]
        self.ws_msgs = 0

    def add(self, name, latency, status, slug=""):
        self.samples.setdefault(name, []).append((latency, status, slug))

    def report(self):
        failures = []
        table = {}
        for name, rows in sorted(self.samples.items()):
            lats = sorted(r[0] for r in rows)
            ok = sum(1 for r in rows if isinstance(r[1], int) and 200 <= r[1] < 300)
            errs = {}
            worst = {}
            for lat, s, slug in rows:
                if not (isinstance(s, int) and 200 <= s < 300):
                    errs[str(s)] = errs.get(str(s), 0) + 1
                    worst[str(s)] = slug
            def pct(p):
                return lats[min(len(lats) - 1, int(p * len(lats)))]
            p95 = pct(0.95)
            err_rate = 1 - ok / len(rows)
            timeout_s, budget, max_err = BUDGETS.get(name, (60, 60, 0.05))
            verdict = "OK"
            if p95 > budget:
                verdict = f"FAIL p95 {p95:.2f}s > budget {budget}s"
            if err_rate > max_err:
                verdict = f"FAIL err {err_rate:.1%} > {max_err:.0%} ({errs} e.g. {worst})"
            if verdict != "OK":
                failures.append(f"{name}: {verdict}")
            table[name] = {
                "n": len(rows), "ok": ok, "errors": errs,
                "p50": round(pct(0.50), 3), "p95": round(p95, 3),
                "max": round(lats[-1], 3),
                "mean": round(statistics.fmean(lats), 3),
                "verdict": verdict,
            }
        return table, failures


async def timed_get(session, metrics, name, url, slug, headers=None):
    timeout_s = BUDGETS[name][0]
    t0 = time.monotonic()
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=timeout_s), headers=headers,
        ) as resp:
            await resp.read()
            metrics.add(name, time.monotonic() - t0, resp.status, slug)
            return resp
    except asyncio.TimeoutError:
        metrics.add(name, time.monotonic() - t0, "TIMEOUT", slug)
    except Exception as e:
        metrics.add(name, time.monotonic() - t0, type(e).__name__, slug)
    return None


async def ws_hold(base_ws, session, slug, metrics, stop):
    t0 = time.monotonic()
    try:
        async with session.ws_connect(
            f"{base_ws}/ws?map={slug}",
            timeout=aiohttp.ClientWSTimeout(ws_close=10),
        ) as ws:
            metrics.add("ws_connect", time.monotonic() - t0, 200, slug)
            while not stop.is_set():
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                    metrics.ws_msgs += 1
                except asyncio.TimeoutError:
                    continue
    except Exception as e:
        metrics.add("ws_connect", time.monotonic() - t0, type(e).__name__, slug)


async def agent(i, base, slug, bbox, cycles, metrics, stop, cast_votes=True):
    rng = random.Random(4200 + i)
    device = f"swarm-{i}-{uuid.uuid4().hex[:8]}"
    base_ws = base.replace("https://", "wss://").replace("http://", "ws://")
    connector = aiohttp.TCPConnector(force_close=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        await asyncio.sleep(rng.uniform(0, 3.0))  # stagger joins

        # ── map-load sequence ──
        await timed_get(session, metrics, "map_meta",
                        f"{base}/api/maps/{slug}", slug)
        await timed_get(session, metrics, "graph_version",
                        f"{base}/api/graph-version?map={slug}", slug)
        await timed_get(session, metrics, "topology_bin",
                        f"{base}/api/graph-topology?map={slug}&format=bin", slug,
                        headers={"Accept-Encoding": "gzip"})
        await timed_get(session, metrics, "graph_votes",
                        f"{base}/api/graph-votes?map={slug}", slug,
                        headers={"Accept-Encoding": "gzip"})

        ws_task = asyncio.create_task(ws_hold(base_ws, session, slug, metrics, stop))

        # ── interaction loop ──
        def rand_pt():
            return [round(rng.uniform(bbox[0], bbox[1]), 6),
                    round(rng.uniform(bbox[2], bbox[3]), 6)]

        for _ in range(cycles):
            if stop.is_set():
                break
            edge_ids = []
            t0 = time.monotonic()
            try:
                async with session.post(
                    f"{base}/api/routes",
                    json={"map": slug, "start": rand_pt(), "end": rand_pt()},
                    timeout=aiohttp.ClientTimeout(total=BUDGETS["route"][0]),
                ) as resp:
                    data = await resp.json(content_type=None)
                    # 404 = OSRM genuinely can't route the random pair — not a
                    # server health signal; count only 5xx/timeouts against us.
                    status = 200 if resp.status == 404 else resp.status
                    metrics.add("route", time.monotonic() - t0, status, slug)
                    if resp.status == 200:
                        edge_ids = data.get("edge_ids") or []
            except asyncio.TimeoutError:
                metrics.add("route", time.monotonic() - t0, "TIMEOUT", slug)
            except Exception as e:
                metrics.add("route", time.monotonic() - t0, type(e).__name__, slug)

            if edge_ids and cast_votes:
                t0 = time.monotonic()
                try:
                    async with session.post(
                        f"{base}/api/vote",
                        json={"map": slug, "mode": "walk",
                              "vote_type": rng.choice(VOTE_TYPES),
                              "voter_id": device, "direction": 1,
                              "edge_ids": edge_ids[:40]},
                        timeout=aiohttp.ClientTimeout(total=BUDGETS["vote"][0]),
                    ) as resp:
                        await resp.read()
                        metrics.add("vote", time.monotonic() - t0, resp.status, slug)
                except asyncio.TimeoutError:
                    metrics.add("vote", time.monotonic() - t0, "TIMEOUT", slug)
                except Exception as e:
                    metrics.add("vote", time.monotonic() - t0, type(e).__name__, slug)

            pt = rand_pt()
            await timed_get(
                session, metrics, "reverse_geocode",
                f"{base}/api/reverse-geocode?lat={pt[0]}&lng={pt[1]}&map={slug}", slug)
            await asyncio.sleep(rng.uniform(0.2, 1.0))

        ws_task.cancel()
        try:
            await ws_task
        except (asyncio.CancelledError, Exception):
            pass


async def fetch_maps(base):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{base}/api/maps",
                               timeout=aiohttp.ClientTimeout(total=30)) as resp:
            data = await resp.json()
    out = {}
    for m in data["maps"]:
        if m.get("requiresPasscode"):
            continue
        b = m["city"]["bounds"]
        # shrink toward center: random points in the core route more reliably
        lat_c = (b["sw"]["lat"] + b["ne"]["lat"]) / 2
        lon_c = (b["sw"]["lon"] + b["ne"]["lon"]) / 2
        lat_r = (b["ne"]["lat"] - b["sw"]["lat"]) * 0.15
        lon_r = (b["ne"]["lon"] - b["sw"]["lon"]) * 0.15
        out[m["slug"]] = (lat_c - lat_r, lat_c + lat_r, lon_c - lon_r, lon_c + lon_r)
    return out


async def main_async(args):
    maps = await fetch_maps(args.base)
    if args.maps:
        want = args.maps.split(",")
        maps = {k: v for k, v in maps.items() if k in want}
    if args.primary not in maps:
        print(f"primary map {args.primary} not available; have {list(maps)}")
        return 2
    # station networks don't route; they still get load-sequence agents
    slugs = list(maps)
    weighted = [args.primary] * (args.agents // 2)
    rest = [s for s in slugs for _ in range(3)]
    rng = random.Random(7)
    while len(weighted) < args.agents:
        weighted.append(rng.choice(rest))
    rng.shuffle(weighted)

    print(f"swarm: {args.agents} agents over {len(set(weighted))} maps "
          f"(primary {args.primary} x{weighted.count(args.primary)}), "
          f"{args.cycles} cycles → {args.base}")

    # Synthetic votes only land on the primary map and test maps; every other
    # tenant map gets the full read path (load, topology, votes, WS, routing)
    # without polluting real vote data.
    def may_vote(slug):
        return slug == args.primary or slug.startswith("test-") or "test" in slug

    metrics = Metrics()
    stop = asyncio.Event()
    t0 = time.monotonic()
    tasks = [asyncio.create_task(agent(i, args.base, s, maps[s], args.cycles,
                                       metrics, stop, cast_votes=may_vote(s)))
             for i, s in enumerate(weighted)]
    await asyncio.gather(*tasks, return_exceptions=True)
    stop.set()
    wall = time.monotonic() - t0

    table, failures = metrics.report()
    print(f"\n=== swarm result ({wall:.0f}s wall, ws_msgs={metrics.ws_msgs}) ===")
    print(f"{'interaction':<16}{'n':>5}{'ok':>5}{'p50':>8}{'p95':>8}{'max':>8}  verdict")
    for name, r in table.items():
        print(f"{name:<16}{r['n']:>5}{r['ok']:>5}{r['p50']:>8}{r['p95']:>8}"
              f"{r['max']:>8}  {r['verdict']}")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nALL BUDGETS MET")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:5001")
    p.add_argument("--agents", type=int, default=30)
    p.add_argument("--cycles", type=int, default=5)
    p.add_argument("--primary", default="nyc-walkways")
    p.add_argument("--maps", default="", help="comma list; default all public maps")
    args = p.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
