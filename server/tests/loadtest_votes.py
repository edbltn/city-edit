"""Tiered load test for City Edit: N simulated voter agents against local Flask.

Each agent behaves like a real client joining the map:
  1. opens a WebSocket (/ws?map=...) and counts pushed messages
  2. fetches /api/graph-votes once (what the client does on load)
  3. loops CYCLES times: POST /api/routes (random Manhattan pair) -> POST /api/vote

A sidecar probe hits /health every second and samples Flask CPU/RSS so we can
see head-of-line blocking and detect lockup independently of agent traffic.
"""

import argparse
import asyncio
import json
import random
import statistics
import subprocess
import time
import uuid

import aiohttp

BASE = "http://localhost:5001"
WS_BASE = "ws://localhost:5001"

# Manhattan core: dense graph, OSRM routes reliably
BBOX = (40.700, 40.800, -74.015, -73.940)  # lat_min, lat_max, lon_min, lon_max

VOTE_TYPES = [
    "Improve bike lane", "Add bike lane", "Add protected bike lane",
    "Widen bike lane", "Repave bike path",
]

REQ_TIMEOUT = 45


def rand_pt(rng):
    return [round(rng.uniform(BBOX[0], BBOX[1]), 6),
            round(rng.uniform(BBOX[2], BBOX[3]), 6)]


class Metrics:
    def __init__(self):
        self.samples = {}   # endpoint -> list[(latency_s, status)]
        self.ws_msgs = 0
        self.ws_errors = 0
        self.ws_connected = 0

    def add(self, endpoint, latency, status):
        self.samples.setdefault(endpoint, []).append((latency, status))

    def summary(self):
        out = {}
        for ep, rows in self.samples.items():
            lats = sorted(r[0] for r in rows)
            ok = sum(1 for r in rows if isinstance(r[1], int) and 200 <= r[1] < 300)
            errs = {}
            for _, s in rows:
                if not (isinstance(s, int) and 200 <= s < 300):
                    errs[str(s)] = errs.get(str(s), 0) + 1

            def pct(p):
                return round(lats[min(len(lats) - 1, int(p * len(lats)))], 3)

            out[ep] = {
                "n": len(rows), "ok": ok, "errors": errs,
                "p50": pct(0.50), "p95": pct(0.95), "p99": pct(0.99),
                "max": round(lats[-1], 3),
                "mean": round(statistics.fmean(lats), 3),
            }
        out["ws"] = {"connected": self.ws_connected, "msgs": self.ws_msgs,
                     "errors": self.ws_errors}
        return out


async def timed(metrics, ep, coro):
    t0 = time.monotonic()
    try:
        async with coro as resp:
            await resp.read()
            metrics.add(ep, time.monotonic() - t0, resp.status)
            return resp.status
    except asyncio.TimeoutError:
        metrics.add(ep, time.monotonic() - t0, "timeout")
    except Exception as e:
        metrics.add(ep, time.monotonic() - t0, type(e).__name__)
    return None


async def ws_listener(session, slug, metrics, stop):
    try:
        async with session.ws_connect(f"{WS_BASE}/ws?map={slug}",
                                      heartbeat=None, timeout=REQ_TIMEOUT) as ws:
            metrics.ws_connected += 1
            while not stop.is_set():
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if msg.type == aiohttp.WSMsgType.TEXT:
                    metrics.ws_msgs += 1
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    metrics.ws_errors += 1
                    return
    except Exception:
        metrics.ws_errors += 1


async def agent(i, slug, cycles, metrics, stop, use_ws):
    rng = random.Random(1000 + i)
    voter = f"agent-{i}-{uuid.uuid4().hex[:8]}"
    timeout = aiohttp.ClientTimeout(total=REQ_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        ws_task = None
        if use_ws:
            ws_task = asyncio.create_task(ws_listener(session, slug, metrics, stop))
        # stagger joins across ~2s
        await asyncio.sleep(rng.uniform(0, 2.0))

        await timed(metrics, "graph-votes",
                    session.get(f"{BASE}/api/graph-votes?map={slug}&mode=walk"))

        for _ in range(cycles):
            if stop.is_set():
                break
            body = {"map": slug, "start": rand_pt(rng), "end": rand_pt(rng)}
            t0 = time.monotonic()
            edge_ids = []
            try:
                async with session.post(f"{BASE}/api/routes", json=body) as resp:
                    data = await resp.json(content_type=None)
                    metrics.add("routes", time.monotonic() - t0, resp.status)
                    if resp.status == 200:
                        edge_ids = data.get("edge_ids") or []
            except asyncio.TimeoutError:
                metrics.add("routes", time.monotonic() - t0, "timeout")
            except Exception as e:
                metrics.add("routes", time.monotonic() - t0, type(e).__name__)

            if edge_ids:
                vote = {"map": slug, "mode": "walk",
                        "vote_type": rng.choice(VOTE_TYPES),
                        "voter_id": voter, "direction": 1,
                        "edge_ids": edge_ids}
                await timed(metrics, "vote",
                            session.post(f"{BASE}/api/vote", json=vote))
            await asyncio.sleep(rng.uniform(0.1, 0.5))

        if ws_task:
            stop_local = asyncio.get_event_loop().time
            ws_task.cancel()
            try:
                await ws_task
            except (asyncio.CancelledError, Exception):
                pass


async def health_probe(metrics, stop, flask_pid, sys_samples):
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while not stop.is_set():
            await timed(metrics, "health", session.get(f"{BASE}/health"))
            if flask_pid:
                try:
                    out = subprocess.run(
                        ["ps", "-o", "rss=,pcpu=", "-p", str(flask_pid)],
                        capture_output=True, text=True, timeout=5).stdout.split()
                    if len(out) >= 2:
                        sys_samples.append((float(out[0]) / 1024, float(out[1])))
                except Exception:
                    pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass


async def run_tier(n_agents, cycles, slug, flask_pid, use_ws):
    metrics = Metrics()
    stop = asyncio.Event()
    sys_samples = []
    probe = asyncio.create_task(health_probe(metrics, stop, flask_pid, sys_samples))
    t0 = time.monotonic()
    await asyncio.gather(*(agent(i, slug, cycles, metrics, stop, use_ws)
                           for i in range(n_agents)))
    wall = time.monotonic() - t0
    stop.set()
    await probe
    summary = metrics.summary()
    summary["_tier"] = {
        "agents": n_agents, "cycles": cycles, "wall_s": round(wall, 1),
        "flask_rss_mb_max": round(max((s[0] for s in sys_samples), default=0), 1),
        "flask_cpu_pct_max": round(max((s[1] for s in sys_samples), default=0), 1),
    }
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", type=int, required=True)
    ap.add_argument("--cycles", type=int, default=6)
    ap.add_argument("--map", default="agent-test-1")
    ap.add_argument("--flask-pid", type=int, default=0)
    ap.add_argument("--no-ws", action="store_true")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    summary = asyncio.run(run_tier(args.agents, args.cycles, args.map,
                                   args.flask_pid, not args.no_ws))
    text = json.dumps(summary, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)


if __name__ == "__main__":
    main()
