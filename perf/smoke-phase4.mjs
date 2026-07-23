/**
 * Phase-4 smoke test: loads the app on the MapLibre-only build, exercises the
 * core interactions, and reports console errors + DOM assertions.
 */
import { chromium } from "playwright";

const URL = process.argv[2] ?? "http://localhost:3000/?lat=40.722&lng=-74.000&z=15";
const SHOT_DIR = process.argv[3] ?? ".";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });

const errors = [];
page.on("console", (msg) => {
  if (msg.type() === "error") errors.push(msg.text());
});
page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));

const results = [];
const check = (name, ok, extra = "") =>
  results.push(`${ok ? "PASS" : "FAIL"} ${name}${extra ? ` — ${extra}` : ""}`);

await page.goto(URL, { waitUntil: "networkidle" });

// 1. MapLibre canvas present, no leaflet remnants
await page.waitForSelector(".maplibregl-canvas", { timeout: 10000 });
check("maplibre canvas mounted", true);
check("no leaflet DOM", (await page.$$(".leaflet-container")).length === 0);

// 2. Wait for graph/heatmap data to settle
await page.waitForTimeout(3500);

// 3. Zoom control present and clickable
const zoomIn = await page.$(".map-zoom-control button[aria-label='Zoom in']");
check("zoom control present", !!zoomIn);
if (zoomIn) {
  await zoomIn.click();
  await page.waitForTimeout(600);
}

// 4. Click the map → start marker + pinned proposal card
await page.mouse.click(640, 400);
await page.waitForTimeout(1200);
const markers = await page.$$(".maplibregl-marker .ascii-marker");
check("start marker placed on click", markers.length >= 1, `${markers.length} kite markers`);
const card = await page.$(".graph-proposal-card.is-interactive");
check("pinned proposal card shown", !!card);

// 5. Card X removes the selection
if (card) {
  const closeBtn = await page.$(".graph-proposal-close");
  if (closeBtn) {
    await closeBtn.click();
    await page.waitForTimeout(600);
    const cardGone = !(await page.$(".graph-proposal-card.is-interactive"));
    check("card X clears selection", cardGone);
  } else {
    check("card X present", false);
  }
}

// 6. Hover a street → hover card appears. Topology (and thus hit-testing)
// deliberately loads after tiles settle, so poll up to 12s for readiness.
let hoverCard = null;
for (let tries = 0; tries < 24 && !hoverCard; tries++) {
  await page.mouse.move(500, 300);
  await page.waitForTimeout(100);
  await page.mouse.move(620, 380, { steps: 6 });
  await page.waitForTimeout(400);
  hoverCard = await page.$(".graph-proposal-card.is-hover");
}
check("hover card on mousemove", !!hoverCard);

// 7. Drag-pan the map (should not throw, cursor logic runs)
await page.mouse.move(640, 400);
await page.mouse.down();
await page.mouse.move(740, 450, { steps: 12 });
await page.mouse.up();
await page.waitForTimeout(600);
check("drag-pan executed", true);

// 8. Screenshot for eyeballing
await page.screenshot({ path: `${SHOT_DIR}/smoke.png` });

const realErrors = errors.filter(
  (e) =>
    !e.includes("favicon") &&
    !e.includes("WebSocket") && // dev HMR noise
    !e.includes("demotiles.maplibre.org") // glyphs CDN hiccups
);
check("no console errors", realErrors.length === 0, realErrors.slice(0, 5).join(" | "));

console.log(results.join("\n"));
await browser.close();
process.exit(results.some((r) => r.startsWith("FAIL")) ? 1 : 0);
