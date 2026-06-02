"""Capture map preview screenshots and save locally, with optional GCS sync.

Usage:
  # Local dev (capture from running dev server):
  python capture.py --url http://localhost:3000 --output ../client-react/public/previews

  # Production (capture from live site, sync to GCS):
  python capture.py --output /var/www/html/previews --bucket cityedit-map-previews
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

THEMES = ["bikepaths", "trees", "walkways"]
VIEWPORT = {"width": 1400, "height": 900}
RENDER_WAIT_MS = 12_000


def theme_url(base_url: str, theme_id: str) -> str:
    if "localhost" in base_url or "127.0.0.1" in base_url:
        sep = "&" if "?" in base_url else "?"
        return f"{base_url}{sep}theme={theme_id}"
    return f"https://{theme_id}.cityedit.org"


async def capture_theme(page, base_url: str, theme_id: str) -> bytes:
    url = theme_url(base_url, theme_id)
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
        }""", timeout=20_000)
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


def sync_to_gcs(bucket_name: str, theme_id: str, png: bytes):
    try:
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        blob = bucket.blob(f"{theme_id}-{ts}.png")
        blob.upload_from_string(png, content_type="image/png")
        blob.cache_control = "public, max-age=86400"
        blob.patch()
        print(f"  Synced to gs://{bucket_name}/{theme_id}-{ts}.png")
    except Exception as e:
        print(f"  GCS sync skipped: {e}", file=sys.stderr)


async def main():
    parser = argparse.ArgumentParser(description="Capture map preview screenshots")
    parser.add_argument("--url", default="https://cityedit.org",
                        help="Base URL to capture from (default: production site)")
    parser.add_argument("--output", default=str(Path(__file__).parent.parent / "client-react" / "public" / "previews"),
                        help="Local directory to save screenshots")
    parser.add_argument("--bucket", default=os.environ.get("PREVIEW_BUCKET", ""),
                        help="GCS bucket for versioned backup (e.g. cityedit-map-previews-prod)")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Saving to: {output_dir}")
    if args.bucket:
        print(f"GCS bucket: {args.bucket}")

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport=VIEWPORT)

        for theme_id in THEMES:
            print(f"Capturing {theme_id}...", flush=True)
            try:
                png = await capture_theme(page, args.url, theme_id)

                local_path = output_dir / f"{theme_id}.png"
                local_path.write_bytes(png)
                print(f"  Saved {local_path} ({len(png):,} bytes)")

                if args.bucket:
                    sync_to_gcs(args.bucket, theme_id, png)
            except Exception as e:
                print(f"  Error capturing {theme_id}: {e}", file=sys.stderr)

        await browser.close()

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
