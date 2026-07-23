/**
 * Map rendering perf harness.
 *
 * Drives a deterministic pan/zoom sequence with real input events and reports:
 *   - loadToHeatmapMs   : navigation → first heatmap stroke
 *   - pan: avgFps, p95FrameMs, maxFrameMs, jankFrames (>33ms) during drags
 *   - panRedrawDelayMs  : mouseup → heatmap redraw (the "pop-in" delay)
 *   - zoomBlackoutMs    : heatmap clear at zoomstart → redraw after zoomend
 *   - zoom: frame stats during the zoom animation windows
 *
 * Usage:
 *   node measure.mjs --url http://localhost:3000 --label baseline \
 *        [--reps 3] [--out results/baseline.json] [--view lat,lng,z]
 */
import { chromium } from "playwright";
import { readFileSync, mkdirSync, writeFileSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));

// --- args ---
const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, arr) => {
    if (a.startsWith("--")) acc.push([a.slice(2), arr[i + 1]]);
    return acc;
  }, [])
);
const URL_BASE = args.url ?? "http://localhost:3000";
const LABEL = args.label ?? "run";
const REPS = parseInt(args.reps ?? "3", 10);
const OUT = args.out ?? `results/${LABEL}.json`;
// Default view centers on the downtown graph area at a busy zoom.
const [LAT, LNG, Z] = (args.view ?? "40.722,-74.000,15").split(",").map(Number);
// CPU throttle factor (CDP emulation) — 4 ≈ mid-range laptop/phone.
const CPU = parseFloat(args.cpu ?? "1");

const VIEWPORT = {
  width: parseInt(args.width ?? "1280", 10),
  height: parseInt(args.height ?? "800", 10),
};
const PAN_STEPS = 30;       // mouse.move interpolation steps per drag
const PAN_MS = 650;         // approximate drag duration budget
const SETTLE_MS = 1400;     // wait after each gesture

function pct(sorted, p) {
  if (sorted.length === 0) return null;
  const i = Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length));
  return sorted[i];
}

function frameStats(frames, t0, t1) {
  const deltas = [];
  for (let i = 1; i < frames.length; i++) {
    if (frames[i] >= t0 && frames[i] <= t1) deltas.push(frames[i] - frames[i - 1]);
  }
  if (deltas.length < 2) return null;
  const sorted = [...deltas].sort((a, b) => a - b);
  const sum = deltas.reduce((a, b) => a + b, 0);
  return {
    frames: deltas.length,
    avgFps: +(1000 / (sum / deltas.length)).toFixed(1),
    p95FrameMs: +pct(sorted, 95).toFixed(1),
    maxFrameMs: +sorted[sorted.length - 1].toFixed(1),
    jankFrames: deltas.filter((d) => d > 33).length,
  };
}

const median = (xs) => {
  const s = xs.filter((x) => x != null).sort((a, b) => a - b);
  return s.length ? s[Math.floor(s.length / 2)] : null;
};

async function main() {
  mkdirSync(join(__dirname, dirname(OUT)), { recursive: true });
  const instrument = readFileSync(join(__dirname, "instrument.js"), "utf8");

  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: 2, // retina-like: doubles canvas pixel work, matches real usage
  });
  await context.addInitScript(instrument);
  const page = await context.newPage();
  if (CPU > 1) {
    const cdp = await context.newCDPSession(page);
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: CPU });
    console.log(`[${LABEL}] CPU throttled ${CPU}x`);
  }

  const url = `${URL_BASE}/m/nyc-bikes?lat=${LAT}&lng=${LNG}&z=${Z}`;
  console.log(`[${LABEL}] loading ${url}`);
  const navStart = Date.now();
  await page.goto(url, { waitUntil: "domcontentloaded" });

  // Wait for the first heatmap stroke (topology + votes loaded and painted).
  await page.waitForFunction(() => window.__perf?.firstStrokeT != null, null, {
    timeout: 90_000,
  });
  const loadToHeatmapMs = await page.evaluate(() => window.__perf.firstStrokeT);
  console.log(`[${LABEL}] heatmap first paint at ${loadToHeatmapMs.toFixed(0)}ms`);
  await page.waitForTimeout(2500); // let tiles/webfonts settle

  const cx = VIEWPORT.width / 2;
  const cy = VIEWPORT.height / 2;

  const reps = [];
  for (let rep = 0; rep < REPS; rep++) {
    // ---- PAN: 4 drags (right, down, left, up) back to start ----
    const dirs = [
      [280, 40], [-60, 220], [-280, -40], [60, -220],
    ];
    for (let d = 0; d < dirs.length; d++) {
      const [dx, dy] = dirs[d];
      await page.evaluate((l) => window.__perfMark(l), `pan-${rep}-${d}-start`);
      await page.mouse.move(cx, cy);
      await page.mouse.down();
      for (let s = 1; s <= PAN_STEPS; s++) {
        await page.mouse.move(cx + (dx * s) / PAN_STEPS, cy + (dy * s) / PAN_STEPS);
        await page.waitForTimeout(PAN_MS / PAN_STEPS);
      }
      await page.mouse.up();
      await page.evaluate((l) => window.__perfMark(l), `pan-${rep}-${d}-end`);
      await page.waitForTimeout(SETTLE_MS);
    }

    // ---- ZOOM: wheel in ×2, out ×2 ----
    for (let zdir = 0; zdir < 4; zdir++) {
      const dy = zdir < 2 ? -240 : 240;
      await page.evaluate((l) => window.__perfMark(l), `zoom-${rep}-${zdir}-start`);
      await page.mouse.move(cx, cy);
      await page.mouse.wheel(0, dy);
      await page.waitForTimeout(SETTLE_MS);
      await page.evaluate((l) => window.__perfMark(l), `zoom-${rep}-${zdir}-end`);
    }
    reps.push(rep);
  }

  // ---- Collect + compute ----
  const perf = await page.evaluate(() => window.__perf);
  const markAt = (label) => perf.marks.find((m) => m.label === label)?.t;

  const panStats = [];
  const postPanStats = [];
  const panRedrawDelays = [];
  const zoomBlackouts = [];
  const zoomStats = [];

  for (let rep = 0; rep < REPS; rep++) {
    for (let d = 0; d < 4; d++) {
      const t0 = markAt(`pan-${rep}-${d}-start`);
      const t1 = markAt(`pan-${rep}-${d}-end`);
      if (t0 == null || t1 == null) continue;
      const fs = frameStats(perf.frames, t0, t1);
      if (fs) panStats.push(fs);
      // pop-in: first strokeBatch after drag end
      const redraw = perf.canvasOps.find((o) => o.op === "strokeBatch" && o.t > t1);
      if (redraw) panRedrawDelays.push(redraw.t - t1);
      // post-pan window: the moveend redraw burst shows up as long frames here
      const ps = frameStats(perf.frames, t1, t1 + 700);
      if (ps) postPanStats.push(ps);
    }
    for (let z = 0; z < 4; z++) {
      const t0 = markAt(`zoom-${rep}-${z}-start`);
      const t1 = markAt(`zoom-${rep}-${z}-end`);
      if (t0 == null || t1 == null) continue;
      // Frame stats over the actual zoom animation (leaflet-zoom-anim class),
      // not the full settle window (idle frames would dilute the numbers).
      const anim = perf.mapEvents.find((e) => e.type === "zoomanim-start" && e.t > t0 && e.t < t1);
      const animEnd = anim && perf.mapEvents.find((e) => e.type === "zoomanim-end" && e.t > anim.t);
      const fs = frameStats(perf.frames, anim ? anim.t : t0, animEnd ? animEnd.t + 400 : t1);
      if (fs) zoomStats.push(fs);
      // Blackout: the zoomstart wipe is a clear with NO strokes shortly after
      // it (a normal redraw clears and strokes in the same frame). Blackout =
      // that clear → the next strokeBatch (the zoomend redraw).
      const wipe = perf.canvasOps.find((o) =>
        o.op === "clear" && o.t > t0 && o.t < t1 &&
        !perf.canvasOps.some((s) => s.op === "strokeBatch" && s.t > o.t && s.t < o.t + 80)
      );
      if (wipe) {
        const redraw = perf.canvasOps.find((o) => o.op === "strokeBatch" && o.t > wipe.t);
        if (redraw) zoomBlackouts.push(redraw.t - wipe.t);
      }
    }
  }

  const agg = (stats, key) => median(stats.map((s) => s[key]));
  const result = {
    label: LABEL,
    url,
    timestamp: new Date().toISOString(),
    viewport: VIEWPORT,
    deviceScaleFactor: 2,
    cpuThrottle: CPU,
    reps: REPS,
    metrics: {
      loadToHeatmapMs: +loadToHeatmapMs.toFixed(0),
      pan: {
        avgFps: agg(panStats, "avgFps"),
        p95FrameMs: agg(panStats, "p95FrameMs"),
        maxFrameMs: agg(panStats, "maxFrameMs"),
        jankFramesPerDrag: agg(panStats, "jankFrames"),
        redrawDelayMs: median(panRedrawDelays) != null ? +median(panRedrawDelays).toFixed(0) : null,
        postPanMaxFrameMs: agg(postPanStats, "maxFrameMs"),
      },
      zoom: {
        avgFps: agg(zoomStats, "avgFps"),
        p95FrameMs: agg(zoomStats, "p95FrameMs"),
        maxFrameMs: agg(zoomStats, "maxFrameMs"),
        blackoutMs: median(zoomBlackouts) != null ? +median(zoomBlackouts).toFixed(0) : null,
      },
    },
    raw: { panStats, postPanStats, panRedrawDelays, zoomStats, zoomBlackouts },
  };

  writeFileSync(join(__dirname, OUT), JSON.stringify(result, null, 2));
  console.log(`\n[${LABEL}] results → perf/${OUT}`);
  console.table({
    "load→heatmap (ms)": result.metrics.loadToHeatmapMs,
    "pan avg FPS": result.metrics.pan.avgFps,
    "pan p95 frame (ms)": result.metrics.pan.p95FrameMs,
    "pan jank frames/drag": result.metrics.pan.jankFramesPerDrag,
    "pan redraw delay (ms)": result.metrics.pan.redrawDelayMs,
    "post-pan max frame (ms)": result.metrics.pan.postPanMaxFrameMs,
    "zoom avg FPS": result.metrics.zoom.avgFps,
    "zoom blackout (ms)": result.metrics.zoom.blackoutMs,
  });

  await browser.close();
}

main().catch((e) => { console.error(e); process.exit(1); });
