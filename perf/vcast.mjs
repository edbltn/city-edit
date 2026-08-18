/**
 * Vote-cast latency harness — the sibling of vtoggle.mjs.
 *
 * Answers one question: when you press − or + in the top bar, how long until
 * the screen actually changes, and which stage owns the wait?
 *
 * Reports per press:
 *   handlerMs    : mousedown → the synchronous click handler returning
 *   castStateMs  : mousedown → the button's red/blue `is-cast` class flipping
 *   heatDeltaMs  : mousedown → a `city-edit:block-votes` broadcast that
 *                  actually MOVES a block's differential (the map changing)
 *   glDrawMs     : mousedown → the first WebGL draw after that broadcast
 *   postMs       : mousedown → POST /api/vote resolving
 *   wsMs         : mousedown → the vote delta arriving over the WebSocket
 *   longTasks    : main-thread tasks > 50ms in the window
 *
 * Usage:
 *   node vcast.mjs --map nyc-walkways --w "40.7128,-74.0060;40.7180,-74.0020" \
 *                  --label "Widen sidewalk"
 */
import { chromium } from "playwright";

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, arr) => {
    if (a.startsWith("--")) acc.push([a.slice(2), arr[i + 1] ?? "true"]);
    return acc;
  }, [])
);
const URL_BASE = args.url ?? "http://localhost:3000";
const MAP = args.map ?? "nyc-walkways";
const W = args.w ?? "40.712800,-74.006000;40.718000,-74.002000";
const LABEL = args.label ?? "Widen sidewalk";
const VIEW = args.view ?? "40.7154,-74.0040,15";
const WINDOW_MS = parseInt(args.window ?? "6000", 10);
const PRESSES = (args.presses ?? "up,up,down,down").split(",");
const LATENCY = parseInt(args.latency ?? "0", 10);
const GAP_MS = parseInt(args.gap ?? String(WINDOW_MS), 10);
const [LAT, LNG, Z] = VIEW.split(",").map(Number);

const INIT = () => {
  const P = { frames: [], marks: [], tasks: [], t0: 0 };
  window.__vc = P;
  P.mark = (label, extra) =>
    P.marks.push({ t: +(performance.now() - P.t0).toFixed(1), label, ...extra });

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
      for (const e of l.getEntries())
        P.tasks.push({ t: +(e.startTime - P.t0).toFixed(1), ms: +e.duration.toFixed(1) });
    }).observe({ entryTypes: ["longtask"] });
  } catch { /* unsupported */ }

  // WebGL draws: MapLibre renders only when dirty, so a draw IS a repaint.
  const origGetContext = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (type, ...rest) {
    const ctx = origGetContext.call(this, type, ...rest);
    if (ctx && /webgl/.test(String(type)) && !ctx.__vcHooked) {
      ctx.__vcHooked = true;
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

  // Block heat: record only broadcasts whose differential actually MOVED.
  let prevDiff = null;
  window.addEventListener("city-edit:block-votes", (e) => {
    const d = e.detail.blockDiff;
    let changed = 0;
    if (prevDiff && prevDiff.length === d.length) {
      for (let i = 0; i < d.length; i++) if (d[i] !== prevDiff[i]) changed++;
    }
    P.mark("block-votes", { changed, source: e.detail.source });
    prevDiff = d.slice();
  });

  // POST /api/vote round trip.
  const origFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const url = String(typeof input === "string" ? input : input?.url ?? "");
    const isVote = /\/api\/vote(\?|$)/.test(url);
    const isVotes = /graph-votes|my-votes|route-votes/.test(url);
    if (!isVote && !isVotes) return origFetch(input, init);
    const a = performance.now();
    if (isVote) P.mark("POST /vote sent");
    return origFetch(input, init).then((r) => {
      P.mark((isVote ? "POST /vote done " : "fetch ") + url.replace(/^.*\/api\//, ""),
        { ms: +(performance.now() - a).toFixed(1), status: r.status });
      return r;
    });
  };

  // WebSocket vote deltas.
  const OrigWS = window.WebSocket;
  window.WebSocket = function (...a) {
    const ws = new OrigWS(...a);
    ws.addEventListener("message", () => P.mark("ws message"));
    return ws;
  };
  window.WebSocket.prototype = OrigWS.prototype;

  // The button's cast-state class, watched from the moment it exists.
  P.watchCast = () => {
    if (P.castObserver) P.castObserver.disconnect();
    const obs = new MutationObserver((recs) => {
      for (const r of recs) {
        const el = r.target;
        if (el.classList && el.classList.contains("btn-cast")) {
          P.mark("cast-class", {
            btn: el.className.includes("btn-cast-up") ? "up" : "down",
            isCast: el.classList.contains("is-cast"),
          });
        }
      }
    });
    for (const b of document.querySelectorAll("button.btn-cast")) {
      obs.observe(b, { attributes: true, attributeFilter: ["class"] });
    }
    P.castObserver = obs;
  };

  const origLog = console.log.bind(console);
  console.log = function (...a) {
    if (typeof a[0] === "string" && /^\[(blocks|proposals|cast|votes|store)\]/.test(a[0])) {
      P.mark("log " + [a[0], a[1], a[2]].join(" ").slice(0, 130));
    }
    return origLog(...a);
  };

  P.reset = () => {
    P.frames.length = 0; P.marks.length = 0; P.tasks.length = 0;
    P.t0 = performance.now();
  };
};

function summarize(P, windowMs) {
  const at = (pred) => { const m = P.marks.find(pred); return m ? m.t : null; };
  const syncEnd = at((m) => m.label === "--sync end--");
  const castClass = P.marks.find((m) => m.label === "cast-class");
  const heat = P.marks.find((m) => m.label === "block-votes" && m.changed > 0);
  const glAfterHeat = heat
    ? P.marks.find((m) => m.label === "gl draw" && m.t >= heat.t) : null;
  const post = P.marks.find((m) => m.label.startsWith("POST /vote done"));
  return {
    handlerMs: syncEnd,
    castStateMs: castClass ? castClass.t : null,
    castState: castClass ? castClass.isCast : null,
    heatDeltaMs: heat ? heat.t : null,
    heatBlocksChanged: heat ? heat.changed : 0,
    heatSource: heat ? heat.source : null,
    glDrawMs: glAfterHeat ? glAfterHeat.t : null,
    postMs: post ? post.t : null,
    postStatus: post ? post.status : null,
    wsMs: at((m) => m.label === "ws message"),
    longTasks: P.tasks.filter((t) => t.ms > 50 && t.t <= windowMs),
    marks: P.marks.filter((m) => m.label !== "gl draw"),
  };
}

const browser = await chromium.launch({
  headless: args.headed !== "true",
  args: ["--use-gl=angle", "--enable-gpu"],
});
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
await page.addInitScript(INIT);
const url = `${URL_BASE}/m/${MAP}?tab=vcast&w=${encodeURIComponent(W)}`
  + `&vt=${encodeURIComponent(LABEL)}#${Z}/${LAT}/${LNG}`;
await page.goto(url, { waitUntil: "domcontentloaded" });

await page.waitForFunction(
  () => window.cityedit && window.cityedit.dumpState().blockHeatNonzero > 0,
  null, { timeout: 90000 },
);
await page.waitForSelector("button.btn-cast-up", { timeout: 30000 });
await page.waitForTimeout(5000); // let the route resolve + proposals settle

// Emulate a real network AFTER the load, so only the vote round trips pay it.
if (LATENCY > 0) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Network.enable");
  await cdp.send("Network.emulateNetworkConditions", {
    offline: false, latency: LATENCY,
    downloadThroughput: 5_000_000 / 8, uploadThroughput: 2_000_000 / 8,
  });
}

const results = [];
for (const press of PRESSES) {
  const ok = await page.evaluate((dir) => {
    const P = window.__vc;
    const btn = document.querySelector(`button.btn-cast-${dir}`);
    if (!btn) return false;
    P.reset();
    P.watchCast();
    P.mark("--press--", { wasCast: btn.classList.contains("is-cast") });
    btn.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window, button: 0 }));
    btn.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window, button: 0 }));
    btn.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window, button: 0 }));
    P.mark("--sync end--");
    return true;
  }, press);
  if (!ok) { results.push({ press, error: "button not found" }); continue; }
  await page.waitForTimeout(GAP_MS);
  const P = await page.evaluate(() => ({
    frames: window.__vc.frames, marks: window.__vc.marks, tasks: window.__vc.tasks,
  }));
  results.push({ press, ...summarize(P, WINDOW_MS) });
}

const table = results.map((r) => ({
  press: r.press, handlerMs: r.handlerMs, castStateMs: r.castStateMs,
  heatDeltaMs: r.heatDeltaMs, glDrawMs: r.glDrawMs, postMs: r.postMs, wsMs: r.wsMs,
  blockedMs: (r.longTasks ?? []).reduce((s2, t) => s2 + t.ms, 0),
}));
console.error("\n== " + MAP + " latency=" + LATENCY + "ms gap=" + GAP_MS + "ms ==");
console.table(table);
console.log(JSON.stringify({ map: MAP, label: LABEL, latency: LATENCY, results }, null, 1));
await browser.close();
