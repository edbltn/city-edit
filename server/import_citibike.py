#!/usr/bin/env python3
"""
Import Citibike trip data as votes into the Desire Path Mapper.

Downloads 1 month of 2025 Citibike data, picks 5 random days,
samples 1000 rows, filters for Manhattan rides, then concurrently
routes and votes each ride via the API.

Includes health checking and automatic Docker restart on backend failure.

Usage:
    python -u import_citibike.py [--api-base URL] [--sample-size N] [--days N] [--month M] [--concurrency N] [--dry-run]

Requires: aiohttp, pandas, requests
"""

import argparse
import asyncio
import random
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import ssl as _ssl

import aiohttp
import pandas as pd
import requests

TRIPDATA_URL = "https://s3.amazonaws.com/tripdata/{month}-citibike-tripdata.zip"
CACHE_DIR = Path(__file__).parent / "citibike_data"

MANHATTAN_BOUNDS = {
    "min_lat": 40.700,
    "max_lat": 40.880,
    "min_lon": -74.020,
    "max_lon": -73.907,
}

VOTE_TYPE = "\U0001f6b4 Add bike lane"
COMPOSE_DIR = Path(__file__).resolve().parent.parent


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def download_month(month_str: str) -> Path:
    """Download a monthly Citibike zip file, caching locally."""
    CACHE_DIR.mkdir(exist_ok=True)
    zip_path = CACHE_DIR / f"{month_str}-citibike-tripdata.zip"

    if zip_path.exists():
        log(f"Using cached {zip_path.name}")
        return zip_path

    url = TRIPDATA_URL.format(month=month_str)
    log(f"Downloading {url} ...")
    resp = requests.get(url, stream=True, timeout=120)
    resp.raise_for_status()

    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    with open(zip_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded * 100 // total
                print(f"\r  {pct}% ({downloaded >> 20} / {total >> 20} MB)", end="", flush=True)
    print(flush=True)
    return zip_path


def read_csv_from_zip(zip_path: Path) -> pd.DataFrame:
    """Read the first CSV file from a Citibike zip archive."""
    with zipfile.ZipFile(zip_path) as zf:
        csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not csv_names:
            raise ValueError(f"No CSV found in {zip_path}")
        log(f"Reading {csv_names[0]} from {zip_path.name}")
        with zf.open(csv_names[0]) as f:
            return pd.read_csv(f, low_memory=False)


def in_manhattan(row: pd.Series) -> bool:
    b = MANHATTAN_BOUNDS
    return (
        b["min_lat"] <= row["start_lat"] <= b["max_lat"]
        and b["min_lon"] <= row["start_lng"] <= b["max_lon"]
        and b["min_lat"] <= row["end_lat"] <= b["max_lat"]
        and b["min_lon"] <= row["end_lng"] <= b["max_lon"]
    )


def health_check(api_base: str, timeout: int = 15) -> bool:
    """Check if the backend is healthy."""
    try:
        resp = requests.get(f"{api_base}/health", timeout=timeout, verify=False)
        return resp.status_code == 200
    except Exception:
        return False


def restart_docker():
    """Restart the Docker Compose stack and wait for health."""
    log("Restarting Docker Compose stack...")
    subprocess.run(
        ["docker", "compose", "down"],
        cwd=COMPOSE_DIR,
        capture_output=True,
    )
    time.sleep(2)
    subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=COMPOSE_DIR,
        capture_output=True,
    )
    log("Docker Compose started, waiting for backend to become healthy...")


def wait_for_healthy(api_base: str, max_wait: int = 120) -> bool:
    """Poll health endpoint until healthy or timeout."""
    start = time.time()
    while time.time() - start < max_wait:
        if health_check(api_base):
            log("Backend is healthy.")
            return True
        time.sleep(3)
    log("ERROR: Backend did not become healthy within timeout.")
    return False


class Stats:
    def __init__(self, total: int):
        self.total = total
        self.processed = 0
        self.success = 0
        self.failed = 0
        self.segments = 0
        self.consecutive_failures = 0
        self.lock = asyncio.Lock()

    async def record(self, ok: bool, segs: int = 0):
        async with self.lock:
            self.processed += 1
            if ok:
                self.success += 1
                self.segments += segs
                self.consecutive_failures = 0
            else:
                self.failed += 1
                self.consecutive_failures += 1
            if self.processed % 10 == 0:
                log(f"[{self.processed}/{self.total}] {self.success} ok, {self.failed} failed, {self.segments} segments")


async def route_and_vote(
    session: aiohttp.ClientSession,
    api_base: str,
    start: list,
    end: list,
    stats: Stats,
    voter_id: str = None,
):
    """Calculate route then immediately cast vote for a single ride."""
    max_retries = 3

    # Route
    route_data = None
    for attempt in range(max_retries):
        try:
            async with session.post(
                f"{api_base}/api/routes",
                json={"start": start, "end": end, "mode": "bike", "waypoints": []},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log(f"Route failed (HTTP {resp.status}): {body[:200]}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    await stats.record(False)
                    return
                route_data = await resp.json()
        except Exception as e:
            log(f"Route exception (attempt {attempt + 1}): {type(e).__name__}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            await stats.record(False)
            return
        break

    if route_data is None or "error" in route_data:
        if route_data and "error" in route_data:
            log(f"Route returned error: {route_data['error']}")
        await stats.record(False)
        return

    segments = route_data.get("desire_path_segments", [])
    vote_mode = route_data.get("vote_mode", "bike")

    if not segments:
        await stats.record(False)
        return

    # Vote
    for attempt in range(max_retries):
        try:
            async with session.post(
                f"{api_base}/api/vote",
                json={"segments": segments, "mode": vote_mode, "vote_type": VOTE_TYPE, "voter_id": voter_id},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log(f"Vote failed (HTTP {resp.status}): {body[:200]}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    await stats.record(False)
                    return
                vote_data = await resp.json()
                if vote_data.get("success"):
                    await stats.record(True, vote_data.get("segments_voted", len(segments)))
                else:
                    log(f"Vote not successful: {vote_data}")
                    await stats.record(False)
                return
        except Exception as e:
            log(f"Vote exception (attempt {attempt + 1}): {type(e).__name__}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            await stats.record(False)
            return


async def run_async(rides: pd.DataFrame, api_base: str, concurrency: int):
    """Process rides in batches, checking health between batches."""
    stats = Stats(len(rides))
    batch_size = 20

    ride_list = [
        ([float(r["start_lat"]), float(r["start_lng"])],
         [float(r["end_lat"]), float(r["end_lng"])],
         str(r.get("ride_id", "")))
        for _, r in rides.iterrows()
    ]

    is_local = "localhost" in api_base or "127.0.0.1" in api_base

    i = 0
    restarts = 0
    max_restarts = 5

    while i < len(ride_list):
        # Health check before each batch
        if not health_check(api_base):
            log(f"Backend unhealthy before batch starting at ride {i}.")
            if is_local:
                if restarts >= max_restarts:
                    log(f"Exceeded max restarts ({max_restarts}). Aborting.")
                    break
                restart_docker()
                if not wait_for_healthy(api_base):
                    log("Backend failed to recover. Aborting.")
                    break
                restarts += 1
                concurrency = max(1, concurrency - 1)
                log(f"Reduced concurrency to {concurrency} after restart.")
            else:
                # Remote server — just wait for it to come back
                log("Waiting for remote backend to recover...")
                if not wait_for_healthy(api_base, max_wait=300):
                    log("Remote backend did not recover. Aborting.")
                    break
            continue

        batch = ride_list[i:i + batch_size]
        sem = asyncio.Semaphore(concurrency)

        connector = aiohttp.TCPConnector(limit=concurrency, ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async def throttled(start, end, rid):
                async with sem:
                    await route_and_vote(session, api_base, start, end, stats, voter_id=rid)

            tasks = [asyncio.create_task(throttled(s, e, rid)) for s, e, rid in batch]
            await asyncio.gather(*tasks)

        # Check if too many consecutive failures (backend probably crashed)
        if stats.consecutive_failures >= batch_size:
            log(f"Entire batch of {batch_size} failed consecutively. Backend likely crashed.")
            if is_local:
                if restarts >= max_restarts:
                    log(f"Exceeded max restarts ({max_restarts}). Aborting.")
                    break
                restart_docker()
                if not wait_for_healthy(api_base):
                    log("Backend failed to recover. Aborting.")
                    break
                restarts += 1
                concurrency = max(1, concurrency - 1)
                log(f"Reduced concurrency to {concurrency} after restart.")
            else:
                log("Waiting for remote backend to recover...")
                if not wait_for_healthy(api_base, max_wait=300):
                    log("Remote backend did not recover. Aborting.")
                    break
            # Retry this batch
            continue

        i += batch_size

    return stats


def main():
    parser = argparse.ArgumentParser(description="Import Citibike data as votes")
    parser.add_argument("--api-base", default="http://localhost:8080", help="API base URL")
    parser.add_argument("--sample-size", type=int, default=1000, help="Number of rows to sample")
    parser.add_argument("--days", type=int, default=5, help="Number of random days to pick")
    parser.add_argument("--month", type=int, default=None, help="Specific month (1-12), or random if omitted")
    parser.add_argument("--concurrency", type=int, default=3, help="Max concurrent API requests")
    parser.add_argument("--dry-run", action="store_true", help="Download and filter only, skip API calls")
    args = parser.parse_args()

    api_base = args.api_base.rstrip("/")

    # Step 1: Download 1 month
    month = args.month or random.randint(1, 12)
    month_str = f"2025{month:02d}"
    log(f"Selected month: {month_str}")

    zip_path = download_month(month_str)
    df = read_csv_from_zip(zip_path)

    # Step 2: Pick random days
    df["started_at"] = pd.to_datetime(df["started_at"], errors="coerce")
    df["date"] = df["started_at"].dt.date
    available_dates = sorted(df["date"].dropna().unique())
    num_days = min(args.days, len(available_dates))
    chosen_dates = sorted(random.sample(list(available_dates), num_days))
    log(f"Picked {num_days} days: {', '.join(str(d) for d in chosen_dates)}")

    combined = df[df["date"].isin(chosen_dates)].copy()
    log(f"Total rides across {num_days} days: {len(combined)}")

    # Step 3: Sample and filter for Manhattan
    combined = combined.dropna(subset=["start_lat", "start_lng", "end_lat", "end_lng"])
    if len(combined) > args.sample_size:
        combined = combined.sample(n=args.sample_size, random_state=42)
    log(f"After sampling {args.sample_size}: {len(combined)} rides")

    manhattan_mask = combined.apply(in_manhattan, axis=1)
    manhattan_rides = combined[manhattan_mask].reset_index(drop=True)
    log(f"Manhattan rides: {len(manhattan_rides)}")

    if manhattan_rides.empty:
        log("No Manhattan rides found. Exiting.")
        return

    if args.dry_run:
        log("--dry-run: skipping API calls.")
        return

    # Step 4: Health check before starting
    log(f"Checking backend health at {api_base} ...")
    if not health_check(api_base):
        log("Backend is not healthy. Attempting Docker restart...")
        restart_docker()
        if not wait_for_healthy(api_base):
            log("Backend failed to start. Exiting.")
            return

    # Step 5: Route and vote in batches
    log(f"Routing and voting {len(manhattan_rides)} rides via {api_base} (concurrency={args.concurrency}) ...")
    t0 = time.time()
    stats = asyncio.run(run_async(manhattan_rides, api_base, args.concurrency))
    elapsed = time.time() - t0

    log(f"Done in {elapsed:.1f}s!")
    log(f"  Rides processed: {stats.processed}")
    log(f"  Successful votes: {stats.success}")
    log(f"  Failed: {stats.failed}")
    log(f"  Total segments voted: {stats.segments}")


if __name__ == "__main__":
    main()
