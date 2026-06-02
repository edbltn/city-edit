"""Capture map preview screenshots and save locally, with optional GCS sync.

Captures one preview per map in the registry (fetched from /api/maps): preset
maps via their subdomain (bikepaths.cityedit.org, …), all other maps via the
slug route (https://cityedit.org/m/<slug>). Each preview is written as
<slug>.png both locally and (optionally) to GCS — a stable name plus a
timestamped copy for history.

Usage:
  # Local dev (capture from running dev server):
  python capture.py --url http://localhost:3000 --output ../client-react/public/previews

  # Production (capture from live site, sync to GCS):
  python capture.py --output /var/www/html/previews --bucket cityedit-map-previews-prod
"""

import argparse
import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

# Used only as a fallback if /api/maps can't be reached.
FALLBACK = [
    {"slug": "nyc-bikes", "subdomain": "bikepaths"},
    {"slug": "nyc-trees", "subdomain": "trees"},
    {"slug": "nyc-walkways", "subdomain": "walkways"},
]
VIEWPORT = {"width": 1400, "height": 900}
RENDER_WAIT_MS = 12_000


def is_local(base_url: str) -> bool:
    return "localhost" in base_url or "127.0.0.1" in base_url


def fetch_maps(base_url: str) -> list[dict]:
    """All maps from the API; fall back to the preset list on any error."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/maps", timeout=20) as r:
            data = json.load(r)
        maps = data.get("maps", [])
        return maps if maps else FALLBACK
    except Exception as e:
        print(f"  Could not fetch /api/maps ({e}); using preset fallback", file=sys.stderr)
        return FALLBACK


def map_url(base_url: str, m: dict) -> str:
    """Preset maps render on their subdomain in prod; everything else by slug."""
    slug = m["slug"]
    subdomain = m.get("subdomain")
    if is_local(base_url):
        sep = "&" if "?" in base_url else "?"
        return f"{base_url}/m/{slug}" if "/m/" not in base_url else f"{base_url}{sep}map={slug}"
    if subdomain:
        return f"https://{subdomain}.cityedit.org"
    return f"https://cityedit.org/m/{slug}"


async def capture_url(page, url: str) -> bytes:
    print(f"  Navigating to {url}", flush=True)
    await page.goto(url, wait_until="networkidle")
    await page.wait_for_selector(".leaflet-container", state="visible")

    try:
        await page.wait_for_function("""() => {
            const canvases = document.querySelectorAll('.leaflet-container canvas');
            for (const c of canvases) {
                try {
                    const ctx = c.getContext('2d');
                    if (!ctx) continue;
                    const d = ctx.getImageData(0, 0, c.width, c.height).data;
                    for (let i = 3; i < d.length; i += 400) {
                        if (d[i] > 0) return true;
                    }
                } catch (_) {}
            }
            return false;
        }""", timeout=30_000)
    except Exception:
        print("  Warning: canvas content check timed out, capturing anyway", flush=True)

    await page.wait_for_timeout(RENDER_WAIT_MS)

    await page.evaluate("""() => {
        const hide = ['.leaflet-control-container', '.topbar', '.address-search', '.sidebar'];
        hide.forEach(sel => {
            document.querySelectorAll(sel).forEach(el => { el.style.display = 'none'; });
        });
    }""")

    await page.wait_for_timeout(500)
    return await page.locator(".leaflet-container").screenshot(type="png")


def sync_to_gcs(bucket_name: str, slug: str, png: bytes):
    try:
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        # Stable name the site can reference + a timestamped copy for history.
        for name in (f"{slug}.png", f"{slug}-{ts}.png"):
            blob = bucket.blob(name)
            blob.cache_control = "public, max-age=3600"
            blob.upload_from_string(png, content_type="image/png")
        print(f"  Synced to gs://{bucket_name}/{slug}.png (+timestamped)")
    except Exception as e:
        print(f"  GCS sync skipped: {e}", file=sys.stderr)


async def main():
    parser = argparse.ArgumentParser(description="Capture map preview screenshots")
    parser.add_argument("--url", default="https://cityedit.org",
                        help="Base URL to capture from (default: production site)")
    parser.add_argument("--api-url", default="",
                        help="API base for /api/maps if different from --url "
                             "(dev only: app on :3000, API on :5001)")
    parser.add_argument("--output", default=str(Path(__file__).parent.parent / "client-react" / "public" / "previews"),
                        help="Local directory to save screenshots")
    parser.add_argument("--bucket", default=os.environ.get("PREVIEW_BUCKET", ""),
                        help="GCS bucket for versioned backup (e.g. cityedit-map-previews-prod)")
    parser.add_argument("--only", default="",
                        help="Comma-separated slugs to limit capture to (default: all maps)")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    maps = fetch_maps(args.api_url or args.url)
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        maps = [m for m in maps if m["slug"] in wanted]

    print(f"Saving to: {output_dir}")
    if args.bucket:
        print(f"GCS bucket: {args.bucket}")
    print(f"Capturing {len(maps)} map(s): {', '.join(m['slug'] for m in maps)}")

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport=VIEWPORT)

        for m in maps:
            slug = m["slug"]
            print(f"Capturing {slug}...", flush=True)
            try:
                png = await capture_url(page, map_url(args.url, m))
                local_path = output_dir / f"{slug}.png"
                local_path.write_bytes(png)
                print(f"  Saved {local_path} ({len(png):,} bytes)")
                if args.bucket:
                    sync_to_gcs(args.bucket, slug, png)
            except Exception as e:
                print(f"  Error capturing {slug}: {e}", file=sys.stderr)

        await browser.close()

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
