/**
 * Correctness companion to vtoggle.mjs: does a legend toggle still produce the
 * SAME proposal list the old full-recompute path produced, and does the cache
 * invalidate when votes actually change?
 *
 * Checks, on the live dev app:
 *   1. hide a type      → its corridors/pins vanish, others survive
 *   2. show it again    → the list returns to exactly the pre-hide list
 *   3. reload with it hidden, then show → it is clustered on demand (cold cache)
 *
 * Usage: node vtoggle-verify.mjs --map nyc-bikes --label "Improve bike lane"
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
const LABEL = args.label ?? "Improve bike lane";

const INIT = () => {
  window.__vt = { lines: [] };
  const origLog = console.log.bind(console);
  console.log = function (...a) {
    if (a[0] === "[proposals]" && typeof a[1] === "string" && a[1].startsWith("recompute:")) {
      window.__vt.lines.push({ t: +performance.now().toFixed(0), head: a[1], list: a[2] });
    }
    return origLog(...a);
  };
};

async function boot(page, url) {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(
    () => window.cityedit && window.cityedit.dumpState().routeProposals !== undefined,
    null, { timeout: 90000 }
  );
  await page.waitForTimeout(5000);
  // Open the legend panel.
  await page.evaluate(() => {
    const combo = document.querySelector(".vote-type-selector input, .vote-type-selector [role=combobox]")
      || [...document.querySelectorAll("input")].find((i) => /Type to search|proposal/i.test(i.placeholder || ""));
    if (combo) {
      combo.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window, button: 0 }));
      combo.focus();
    }
  });
  await page.waitForTimeout(900);
  const opened = await page.evaluate(() => document.querySelectorAll("button.vt-vis").length);
  if (!opened) throw new Error("legend panel did not open");
}

const toggle = (page, label) => page.evaluate((lbl) => {
  const btn = [...document.querySelectorAll("button.vt-vis")]
    .find((b) => (b.getAttribute("aria-label") || "").includes(lbl));
  if (!btn) return "not found";
  btn.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window, button: 0 }));
  return "ok";
}, label);

const lastList = (page) => page.evaluate(() => {
  const l = window.__vt.lines[window.__vt.lines.length - 1];
  return l ? l.list : null;
});

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
await page.addInitScript(INIT);

await boot(page, `${URL_BASE}/m/${MAP}?tab=vtverify`);
const baseline = await lastList(page);

const r1 = await toggle(page, LABEL);
if (r1 !== "ok") throw new Error("toggle failed: " + r1);
await page.waitForTimeout(2500);
const hidden = await lastList(page);

await toggle(page, LABEL);
await page.waitForTimeout(2500);
const restored = await lastList(page);

// Cold cache: reload with the type hidden (sessionStorage survives reload in
// the same tab), then show it — it was never clustered this page load.
await toggle(page, LABEL);
await page.waitForTimeout(1500);
await boot(page, `${URL_BASE}/m/${MAP}?tab=vtverify`);
const coldHidden = await lastList(page);
const t0 = Date.now();
await toggle(page, LABEL);
await page.waitForTimeout(4000);
const coldShown = await lastList(page);
const coldMs = Date.now() - t0;

const has = (list, label) => (list ?? []).some((s) => s.startsWith(label + "#"));

console.log(JSON.stringify({
  label: LABEL,
  baseline,
  hidden,
  restored,
  coldHidden,
  coldShown,
  checks: {
    "hidden list drops the type": !has(hidden, LABEL),
    "hidden list keeps other types": (hidden ?? []).length > 0,
    "showing restores the exact baseline": JSON.stringify(restored) === JSON.stringify(baseline),
    "cold-load hidden list matches the in-session hidden list":
      JSON.stringify(coldHidden) === JSON.stringify(hidden),
    "cold show clusters the type back in": has(coldShown, LABEL),
    "cold show restores the exact baseline": JSON.stringify(coldShown) === JSON.stringify(baseline),
  },
  coldShowMs: coldMs,
  firstToggle: r1,
}, null, 1));
await browser.close();
