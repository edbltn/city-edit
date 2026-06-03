#!/usr/bin/env python3
"""
Import Lyft bikeshare trip data as votes into City Edit.

Lyft operates Citibike (NYC), Bay Wheels (SF) and Divvy (Chicago) and publishes
all three with the *same* monthly trip schema (ride_id, started_at, start_lat,
start_lng, end_lat, end_lng, ...). This one script ingests any of them: it
downloads the monthly zip(s) covering a date range, filters to the city's bbox,
samples, then routes and votes each ride via the API exactly like a user would.

Granularity note: these feeds publish *monthly* with a ~1-month lag, so a literal
"last week" is usually not published yet. With no explicit range, the script
defaults to the most recent 7 days actually available. An explicit range that
isn't published yet errors with the latest available month.

Usage:
    # Latest available 7 days for a city:
    python -u import_lyft.py --city sf

    # Explicit range (max 7 days):
    python -u import_lyft.py --city chicago --start 2026-04-10 --end 2026-04-16

    # Anchor on a start date, take N days:
    python -u import_lyft.py --city nyc --start 2026-03-01 --days 7

Requires: aiohttp, pandas, requests
"""

import argparse
import asyncio
import datetime as dt
import subprocess
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path

import aiohttp
import pandas as pd
import requests

from cities import CITIES, get_city

# Per-city Lyft trip-data sources. Each city publishes monthly zips named
# "{YYYYMM}-{infix}-tripdata.zip"; Bay Wheels intermittently appends ".csv"
# before ".zip", so we probe both. bbox/name come from the cities.py registry.
LYFT_SOURCES = {
    "nyc": {"base": "https://s3.amazonaws.com/tripdata", "infix": "citibike"},
    "sf": {"base": "https://s3.amazonaws.com/baywheels-data", "infix": "baywheels"},
    "chicago": {"base": "https://divvy-tripdata.s3.amazonaws.com", "infix": "divvy"},
}

CACHE_DIR = Path(__file__).parent / "lyft_data"
MAX_RANGE_DAYS = 7
MONTHS_BACK_PROBE = 18  # how far back to look for the latest published month

# Canonical bike-map vote label (matches the "bikes" preset list, so imported
# rides share the same legend/filter as user votes). Override with --vote-type.
DEFAULT_VOTE_TYPE = "Add bike lane"
COMPOSE_DIR = Path(__file__).resolve().parent.parent


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ── Data source resolution ───────────────────────────────────────────────────

def candidate_urls(city_id: str, yyyymm: str) -> list[str]:
    """Candidate download URLs for a city's monthly file (handles .csv.zip)."""
    src = LYFT_SOURCES[city_id]
    stem = f"{src['base']}/{yyyymm}-{src['infix']}-tripdata"
    return [f"{stem}.zip", f"{stem}.csv.zip"]


def resolve_month_url(city_id: str, yyyymm: str) -> str | None:
    """Return the first candidate URL that exists (HTTP 200), or None."""
    for url in candidate_urls(city_id, yyyymm):
        try:
            resp = requests.head(url, timeout=30, allow_redirects=True)
            if resp.status_code == 200:
                return url
        except requests.RequestException:
            continue
    return None


def months_in_range(start: dt.date, end: dt.date) -> list[str]:
    """All YYYYMM strings spanning [start, end] inclusive (a week can span 2)."""
    months, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(f"{y}{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return months


def latest_available_month(city_id: str) -> str | None:
    """Probe backwards from this month for the most recent published file."""
    today = dt.date.today()
    y, m = today.year, today.month
    for _ in range(MONTHS_BACK_PROBE):
        yyyymm = f"{y}{m:02d}"
        if resolve_month_url(city_id, yyyymm):
            return yyyymm
        m -= 1
        if m < 1:
            y, m = y - 1, 12
    return None


def download_month(city_id: str, yyyymm: str) -> Path | None:
    """Download a monthly zip, caching locally. Returns None if unavailable."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache_path = CACHE_DIR / f"{yyyymm}-{LYFT_SOURCES[city_id]['infix']}-tripdata.zip"
    if cache_path.exists():
        log(f"Using cached {cache_path.name}")
        return cache_path

    url = resolve_month_url(city_id, yyyymm)
    if not url:
        return None

    log(f"Downloading {url} ...")
    resp = requests.get(url, stream=True, timeout=300)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    with open(cache_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded * 100 // total
                print(f"\r  {pct}% ({downloaded >> 20} / {total >> 20} MB)", end="", flush=True)
    print(flush=True)
    return cache_path


def read_trips(zip_path: Path) -> pd.DataFrame:
    """Read and concat every CSV in a Lyft zip (NYC splits months into parts).

    Handles nested zips (older Bay Wheels archives) one level deep.
    """
    frames = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.startswith("__MACOSX") or name.endswith("/"):
                continue
            if name.endswith(".csv"):
                with zf.open(name) as f:
                    frames.append(pd.read_csv(f, low_memory=False))
            elif name.endswith(".zip"):
                with zf.open(name) as f:
                    with zipfile.ZipFile(BytesIO(f.read())) as inner:
                        for inner_name in inner.namelist():
                            if inner_name.endswith(".csv") and not inner_name.startswith("__MACOSX"):
                                with inner.open(inner_name) as cf:
                                    frames.append(pd.read_csv(cf, low_memory=False))
    if not frames:
        raise ValueError(f"No CSV found in {zip_path}")
    log(f"Read {len(frames)} CSV file(s) from {zip_path.name}")
    return pd.concat(frames, ignore_index=True)


# ── Date range resolution ────────────────────────────────────────────────────

def resolve_range(args) -> tuple[dt.date, dt.date, list[str]]:
    """Resolve the (start, end) window and the months it spans.

    - explicit --start/--end (or --start + --days): use as-is, error if months
      aren't published yet.
    - neither: default to the most recent `--days` available in the city's
      latest published month.
    """
    days = args.days
    if days < 1 or days > MAX_RANGE_DAYS:
        sys.exit(f"ERROR: --days must be 1..{MAX_RANGE_DAYS} (got {days}).")

    start = dt.date.fromisoformat(args.start) if args.start else None
    end = dt.date.fromisoformat(args.end) if args.end else None

    if start and end:
        pass
    elif start:
        end = start + dt.timedelta(days=days - 1)
    elif end:
        start = end - dt.timedelta(days=days - 1)
    else:
        # No range given: anchor on the latest published month's last day.
        latest = latest_available_month(args.city)
        if not latest:
            sys.exit(
                f"ERROR: no Lyft trip data available for '{args.city}'. "
                f"Checked the last {MONTHS_BACK_PROBE} months."
            )
        log(f"No range given; using latest available month {latest}.")
        path = download_month(args.city, latest)
        df = read_trips(path)
        max_date = pd.to_datetime(df["started_at"], errors="coerce").dt.date.dropna().max()
        end = max_date
        start = end - dt.timedelta(days=days - 1)
        log(f"Latest available {days} days: {start} → {end}")

    if end < start:
        sys.exit(f"ERROR: --end ({end}) is before --start ({start}).")
    span = (end - start).days + 1
    if span > MAX_RANGE_DAYS:
        sys.exit(f"ERROR: range is {span} days; max is {MAX_RANGE_DAYS}.")

    return start, end, months_in_range(start, end)


# ── Trip loading + filtering ─────────────────────────────────────────────────

def in_bbox(df: pd.DataFrame, bbox: tuple) -> pd.Series:
    """Both endpoints inside (south, west, north, east)."""
    s, w, n, e = bbox
    return (
        df["start_lat"].between(s, n) & df["start_lng"].between(w, e)
        & df["end_lat"].between(s, n) & df["end_lng"].between(w, e)
    )


def load_rides(args, city, start: dt.date, end: dt.date, months: list[str]) -> pd.DataFrame:
    """Download months, filter to date range + city bbox, sample."""
    frames = []
    missing = []
    for yyyymm in months:
        path = download_month(args.city, yyyymm)
        if path is None:
            missing.append(yyyymm)
            continue
        frames.append(read_trips(path))

    if missing:
        latest = latest_available_month(args.city)
        sys.exit(
            f"ERROR: month(s) {', '.join(missing)} not published for '{args.city}'. "
            f"Latest available month is {latest or 'none'}. "
            f"Omit --start/--end to use the latest available {args.days} days."
        )

    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["start_lat", "start_lng", "end_lat", "end_lng", "started_at"])

    df["date"] = pd.to_datetime(df["started_at"], errors="coerce").dt.date
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    log(f"Rides in {start}→{end}: {len(df)}")

    df = df[in_bbox(df, city.bbox)].reset_index(drop=True)
    log(f"Rides inside {city.name} bbox: {len(df)}")

    if len(df) > args.sample_size:
        df = df.sample(n=args.sample_size, random_state=args.seed).reset_index(drop=True)
        log(f"Sampled down to {len(df)} (seed={args.seed})")
    return df


# ── Backend health / routing / voting (city-agnostic) ────────────────────────

def health_check(api_base: str, timeout: int = 15) -> bool:
    try:
        resp = requests.get(f"{api_base}/health", timeout=timeout, verify=False)
        return resp.status_code == 200
    except Exception:
        return False


def fetch_map_mode(api_base: str, slug: str) -> str | None:
    """Look up a map's vote namespace (mode) from the public maps API.

    Votes only display on a map when cast under that map's mode, so we read it
    here rather than trusting the route response's generic vote_mode.
    """
    try:
        resp = requests.get(f"{api_base}/api/maps/{slug}", timeout=30, verify=False)
        if resp.status_code != 200:
            return None
        return (resp.json() or {}).get("mode")
    except Exception:
        return None


def restart_docker():
    log("Restarting Docker Compose stack...")
    subprocess.run(["docker", "compose", "down"], cwd=COMPOSE_DIR, capture_output=True)
    time.sleep(2)
    subprocess.run(["docker", "compose", "up", "-d"], cwd=COMPOSE_DIR, capture_output=True)
    log("Docker Compose started, waiting for backend to become healthy...")


def wait_for_healthy(api_base: str, max_wait: int = 120) -> bool:
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


async def route_and_vote(session, api_base, start, end, stats, voter_id=None,
                          map_slug=None, vote_mode_override=None,
                          vote_type=DEFAULT_VOTE_TYPE):
    """Calculate route then immediately cast vote for a single ride.

    `map_slug` scopes both routing (which city graph) and the vote (which map).
    `vote_mode_override` forces the vote's namespace to match the target map's
    mode (e.g. "bikepaths"); without it the route's reported vote_mode is used.
    """
    max_retries = 3

    route_data = None
    for attempt in range(max_retries):
        try:
            async with session.post(
                f"{api_base}/api/routes",
                json={"start": start, "end": end, "mode": "bike", "waypoints": [], "map": map_slug},
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
    edge_ids = route_data.get("edge_ids", [])
    vote_mode = vote_mode_override or route_data.get("vote_mode", "bike")
    if not segments:
        await stats.record(False)
        return

    for attempt in range(max_retries):
        try:
            async with session.post(
                f"{api_base}/api/vote",
                # ip_from_voter ties the server-side ip_hash to this ride's
                # voter_id (the ride_id) instead of our single source IP, so
                # every imported ride is unique on BOTH the device and IP axes
                # and never trips the per-IP abuse cap that gates human voters.
                json={"segments": segments, "edge_ids": edge_ids, "map": map_slug,
                      "mode": vote_mode, "vote_type": vote_type, "voter_id": voter_id,
                      "ip_from_voter": True},
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


async def run_async(rides: pd.DataFrame, api_base: str, concurrency: int,
                    map_slug=None, vote_mode_override=None, vote_type=DEFAULT_VOTE_TYPE):
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
    i, restarts, max_restarts = 0, 0, 5

    while i < len(ride_list):
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
                    await route_and_vote(
                        session, api_base, start, end, stats, voter_id=rid,
                        map_slug=map_slug, vote_mode_override=vote_mode_override,
                        vote_type=vote_type,
                    )

            tasks = [asyncio.create_task(throttled(s, e, rid)) for s, e, rid in batch]
            await asyncio.gather(*tasks)

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
            continue

        i += batch_size

    return stats


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    valid = ", ".join(c for c in CITIES if c in LYFT_SOURCES)
    parser = argparse.ArgumentParser(description="Import Lyft bikeshare data as votes")
    parser.add_argument("--city", required=True, help=f"Dataset/city id for download + bbox ({valid})")
    parser.add_argument("--map", dest="map_slug", help="Target map slug to vote onto (e.g. sf-bike-lanes). Defaults to the backend's default map.")
    parser.add_argument("--mode", help="Vote namespace; defaults to the target map's mode (e.g. bikepaths)")
    parser.add_argument("--vote-type", default=DEFAULT_VOTE_TYPE, help=f"Vote type label (default {DEFAULT_VOTE_TYPE!r})")
    parser.add_argument("--start", help="Start date YYYY-MM-DD (default: latest available)")
    parser.add_argument("--end", help="End date YYYY-MM-DD (default: start + days - 1)")
    parser.add_argument("--days", type=int, default=MAX_RANGE_DAYS, help=f"Window length, 1..{MAX_RANGE_DAYS} (default {MAX_RANGE_DAYS})")
    parser.add_argument("--sample-size", type=int, default=10000, help="Max rides to import (default 10000)")
    parser.add_argument("--seed", type=int, default=42, help="Sampling seed; vary it across batches to draw different rides")
    parser.add_argument("--api-base", default="http://localhost:8080", help="API base URL")
    parser.add_argument("--concurrency", type=int, default=3, help="Max concurrent API requests")
    parser.add_argument("--dry-run", action="store_true", help="Download and filter only, skip API calls")
    parser.add_argument("--no-restart", action="store_true", help="Never restart Docker, even for a local backend (just wait/abort)")
    args = parser.parse_args()

    city = get_city(args.city)
    if not city or args.city not in LYFT_SOURCES:
        sys.exit(f"ERROR: unknown or unsupported city '{args.city}'. Valid: {valid}")
    args.api_base = args.api_base.rstrip("/")

    start, end, months = resolve_range(args)
    log(f"City: {city.name} | Range: {start} → {end} | Months: {', '.join(months)}")

    rides = load_rides(args, city, start, end, months)
    if rides.empty:
        sys.exit(f"ERROR: no rides found for '{args.city}' in {start}→{end} within the city bbox.")

    if args.dry_run:
        log(f"--dry-run: {len(rides)} rides ready; skipping API calls.")
        return

    # Only ever touch Docker for a local backend. Against a remote target a
    # `docker compose` restart is wrong (it cycles the local stack, not prod)
    # and dangerous, so we just wait for the remote to recover on its own.
    is_local = "localhost" in args.api_base or "127.0.0.1" in args.api_base
    log(f"Checking backend health at {args.api_base} ...")
    if not health_check(args.api_base):
        if is_local and not args.no_restart:
            log("Local backend not healthy. Attempting Docker restart...")
            restart_docker()
            if not wait_for_healthy(args.api_base):
                sys.exit("ERROR: backend failed to start.")
        else:
            log("Backend not healthy; waiting for it to recover (no restart for remote)...")
            if not wait_for_healthy(args.api_base, max_wait=180):
                sys.exit("ERROR: backend did not become healthy. Aborting (no restart for remote).")

    # Resolve the vote namespace: explicit --mode wins, else the target map's
    # own mode (so votes display), else fall back to the route's vote_mode.
    vote_mode = args.mode
    if not vote_mode and args.map_slug:
        vote_mode = fetch_map_mode(args.api_base, args.map_slug)
        if not vote_mode:
            sys.exit(
                f"ERROR: could not resolve mode for map '{args.map_slug}'. "
                f"Pass --mode explicitly or check the slug exists at {args.api_base}."
            )
    if not args.map_slug:
        log("WARNING: no --map given; voting onto the backend's default map.")
    log(f"Target map: {args.map_slug or '(default)'} | vote mode: {vote_mode or '(route default)'} | vote type: {args.vote_type!r}")

    log(f"Routing and voting {len(rides)} rides via {args.api_base} (concurrency={args.concurrency}) ...")
    t0 = time.time()
    stats = asyncio.run(run_async(
        rides, args.api_base, args.concurrency,
        map_slug=args.map_slug, vote_mode_override=vote_mode, vote_type=args.vote_type,
    ))
    elapsed = time.time() - t0

    log(f"Done in {elapsed:.1f}s!")
    log(f"  Rides processed: {stats.processed}")
    log(f"  Successful votes: {stats.success}")
    log(f"  Failed: {stats.failed}")
    log(f"  Total segments voted: {stats.segments}")


if __name__ == "__main__":
    main()
