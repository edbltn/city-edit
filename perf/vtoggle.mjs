/**
 * Vote-type visibility toggle latency harness.
 *
 * Answers one question: when you click the legend's eye on a vote type, how
 * long until the map actually changes — and where does that time go?
 *
 * Reports per toggle:
 *   handlerMs      : mousedown → the filter subscriber running (React + store)
 *   syncMs         : the whole synchronous mousedown handler (heat re-derive +
 *                    feature-state writes happen inside it)
 *   heatApplyMs    : the block-votes dispatch (topProposalDiffs → MapLibre
 *                    setFeatureState), plus full-vs-diff and the write count
 *   firstPaintMs   : mousedown → the first rAF that lands after the writes
 *   settleMs       : mousedown → frames back under 20ms for 5 straight frames
 *   longFrames     : every frame gap > 40ms in the 6s after the click
 *   proposalsMs    : mousedown → "[proposals] recompute: … corridors" console line
 *   longTasks      : main-thread tasks > 50ms in the window
 *
 * Usage:
 *   node vtoggle.mjs --map nyc-bikes --label "Improve bike lane" [--headed]
 */
import { chromium } from "playwright";

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, arr) => {
    if (a.startsWith("--")) acc.push([a.slice(2), arr[i + 1] ?? "true"]);
    return acc;
  }, [])
);
const URL_BASE = args.url ?? "http://localhost:3000";
const MAP = args.map ?? "nyc-bikes";
const TARGETS = (args.labels ?? args.label ?? "Improve bike lane").split("|");
const VIEW = args.view ?? "40.745,-73.985,14";
const WINDOW_MS = parseInt(args.window ?? "6000", 10);
const [LAT, LNG, Z] = VIEW.split(",").map(Number);

const INIT = () => {
  const P = {
    frames: [],       // {t, dt}
    marks: [],        // {t, label, ms?}
    tasks: [],        // {t, ms}
    t0: 0,
  };
  window.__vt = P;
  P.mark = (label, extra) => P.marks.push({ t: +(performance.now() - P.t0).toFixed(1), label, ...extra });

  // Frame timeline — one continuous rAF loop for the whole run.
  let last = performance.now();
  const tick = () => {
    const now = performance.now();
    P.frames.push({ t: +(now - P.t0).toFixed(1), dt: +(now - last).toFixed(1) });
    if (P.frames.length > 4000) P.frames.shift();
    last = now;
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);

  try {
    new PerformanceObserver((l) => {
      for (const e of l.getEntries()) P.tasks.push({ t: +(e.startTime - P.t0).toFixed(1), ms: +e.duration.toFixed(1) });
    }).observe({ entryTypes: ["longtask"] });
  } catch { /* unsupported */ }

  // Time the synchronous block-votes broadcast (heat re-derive + feature-state).
  const origDispatch = window.dispatchEvent.bind(window);
  window.dispatchEvent = function (ev) {
    if (ev && typeof ev.type === "string" && ev.type === "city-edit:block-votes") {
      const a = performance.now();
      const r = origDispatch(ev);
      P.mark("block-votes dispatch", { ms: +(performance.now() - a).toFixed(1) });
      return r;
    }
    return origDispatch(ev);
  };

  // Time every idle callback — the proposal recompute and its slices run here.
  const origRIC = window.requestIdleCallback.bind(window);
  window.requestIdleCallback = function (cb, opts) {
    return origRIC(function (dl) {
      const a = performance.now();
      cb(dl);
      const d = performance.now() - a;
      if (d > 1) P.mark("idle cb", { ms: +d.toFixed(1), timeout: opts && opts.timeout });
    }, opts);
  };

  // WebGL repaints: MapLibre only renders when something is dirty, so a draw
  // call after the toggle IS the heat repaint. Record one stamp per GL frame.
  const origGetContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (type, ...rest) {
    const ctx = origGetContext.call(this, type, ...rest);
    if (ctx && /webgl/.test(String(type)) && !ctx.__vtHooked) {
      ctx.__vtHooked = true;
      const origClear = ctx.clear.bind(ctx);
      let lastGl = -1;
      ctx.clear = function (mask) {
        const t = performance.now();
        if (t - lastGl > 4) { lastGl = t; P.mark("gl draw"); }
        return origClear(mask);
      };
    }
    return ctx;
  };

  // How many blocks' RAW differential actually changes across a toggle — the
  // size a diff-applied write would be if the normalization weren't rescaling.
  let prevDiff = null;
  window.addEventListener("city-edit:block-votes", (e) => {
    const d = e.detail.blockDiff;
    if (prevDiff && prevDiff.length === d.length) {
      let changed = 0, lit = 0;
      for (let i = 0; i < d.length; i++) {
        if (d[i] !== prevDiff[i]) changed++;
        if (d[i] !== 0) lit++;
      }
      P.mark("rawDiffChanged", { changed, lit, max: e.detail.max, maxNeg: e.detail.maxNeg });
    }
    prevDiff = d.slice();
  });

  // Timestamp the app's own dlog lines.
  const origLog = console.log.bind(console);
  console.log = function (...a) {
    if (typeof a[0] === "string" && /^\[(blocks|proposals)\]/.test(a[0])) {
      P.mark("log " + [a[0], a[1]].join(" ").slice(0, 110));
    }
    return origLog(...a);
  };

  P.reset = () => { P.frames.length = 0; P.marks.length = 0; P.tasks.length = 0; P.t0 = performance.now(); };
};

function summarize(P, windowMs) {
  const frames = P.frames.filter((f) => f.t >= 0 && f.t <= windowMs);
  const heat = P.marks.find((m) => m.label.startsWith("log [blocks] heat apply"));
  const dispatch = P.marks.find((m) => m.label === "block-votes dispatch");
  const filterLine = P.marks.find((m) => m.label.includes("vote-type filter"));
  const proposals = P.marks.find((m) => m.label.includes("[proposals] recompute:"));
  const syncEnd = P.marks.find((m) => m.label === "--sync end--");

  // First frame that lands after the synchronous handler returned.
  const syncT = syncEnd ? syncEnd.t : 0;
  const firstPaint = frames.find((f) => f.t > syncT);

  // Settle: first time we see 5 consecutive frames under 20ms after the click.
  let settle = null;
  for (let i = 0; i + 5 <= frames.length; i++) {
    if (frames.slice(i, i + 5).every((f) => f.dt < 20)) { settle = frames[i + 4].t; break; }
  }

  const glDraws = P.marks.filter((m) => m.label === "gl draw").map((m) => m.t);
  const idleCbs = P.marks.filter((m) => m.label === "idle cb");

  return {
    rawDiff: P.marks.find((m) => m.label === "rawDiffChanged") ?? null,
    firstGlDrawMs: glDraws.length ? glDraws[0] : null,
    glDraws: glDraws.slice(0, 20),
    idleCbs,
    handlerMs: filterLine ? filterLine.t : null,
    syncMs: syncEnd ? syncEnd.t : null,
    heatApply: heat ? heat.label.replace("log [blocks] ", "") : null,
    heatApplyMs: dispatch ? dispatch.ms : null,
    firstPaintMs: firstPaint ? firstPaint.t : null,
    settleMs: settle,
    longFrames: frames.filter((f) => f.dt > 40).map((f) => [f.t, f.dt]),
    longTasks: P.tasks.filter((t) => t.ms > 50),
    proposalsMs: proposals ? proposals.t : null,
    proposalsLine: proposals ? proposals.label.replace("log [proposals] ", "") : null,
    marks: P.marks,
  };
}

const browser = await chromium.launch({ headless: args.headed !== "true" ? true : false, args: ["--use-gl=angle", "--enable-gpu"] });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
await page.addInitScript(INIT);
await page.goto(`${URL_BASE}/m/${MAP}?tab=vtperf#${Z}/${LAT}/${LNG}`, { waitUntil: "domcontentloaded" });

// Wait for block heat to land.
await page.waitForFunction(
  () => window.cityedit && window.cityedit.dumpState().blockHeatNonzero > 0,
  null, { timeout: 90000 }
);
await page.waitForTimeout(4000);

// Open the vote-type panel (the legend) so we can click its eyes.
await page.evaluate(() => {
  const combo = document.querySelector(".vote-type-selector input, .vote-type-selector [role=combobox]")
    || [...document.querySelectorAll("input")].find((i) => /Type to search|proposal/i.test(i.placeholder || ""));
  if (combo) combo.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window, button: 0 }));
  if (combo) combo.focus();
});
await page.waitForTimeout(800);

const results = [];
for (const label of TARGETS) {
  for (const phase of ["hide", "show"]) {
    const found = await page.evaluate((lbl) => {
      const P = window.__vt;
      const btn = [...document.querySelectorAll("button.vt-vis")]
        .find((b) => (b.getAttribute("aria-label") || "").includes(lbl));
      if (!btn) return false;
      P.reset();
      btn.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window, button: 0 }));
      P.mark("--sync end--");
      return true;
    }, label);
    if (!found) { results.push({ label, phase, error: "button not found" }); continue; }
    await page.waitForTimeout(WINDOW_MS);
    const P = await page.evaluate(() => ({ frames: window.__vt.frames, marks: window.__vt.marks, tasks: window.__vt.tasks }));
    results.push({ label, phase, ...summarize(P, WINDOW_MS) });
  }
}

console.log(JSON.stringify({ map: MAP, results }, null, 1));
await browser.close();
