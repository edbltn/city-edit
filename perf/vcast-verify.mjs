/**
 * Correctness check for the per-type corridor invalidation: after a press, the
 * incrementally-recomputed corridor list must equal what a cold load (which
 * clusters every type from scratch) produces in the SAME vote state.
 */
import { chromium } from "playwright";

const MAP = "nyc-walkways";
const LABEL = "Widen sidewalk";
const W = "40.712800,-74.006000;40.718000,-74.002000";
const URL = `http://localhost:3000/m/${MAP}?tab=vcastv&w=${encodeURIComponent(W)}`
  + `&vt=${encodeURIComponent(LABEL)}#15/40.7154/-74.0040`;

const INIT = () => {
  window.__lists = [];
  const origLog = console.log.bind(console);
  console.log = function (...a) {
    if (a[0] === "[proposals]" && typeof a[1] === "string" && a[1].startsWith("recompute:")) {
      window.__lists.push({ note: a[1], list: a[2] });
    }
    return origLog(...a);
  };
};

async function open(browser) {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  await page.addInitScript(INIT);
  await page.goto(URL, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(
    () => window.cityedit && window.cityedit.dumpState().blockHeatNonzero > 0,
    null, { timeout: 90000 });
  await page.waitForSelector("button.btn-cast-up", { timeout: 30000 });
  await page.waitForTimeout(6000);
  return page;
}

const browser = await chromium.launch({ headless: true, args: ["--use-gl=angle"] });

// Session A: cold load, then a press → the INCREMENTAL list.
const a = await open(browser);
await a.evaluate(() => { window.__lists.length = 0; });
await a.evaluate(() => document.querySelector("button.btn-cast-up")
  .dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window, button: 0 })));
await a.waitForTimeout(6000);
const incremental = await a.evaluate(() => window.__lists.at(-1));
await a.close();

// Session B: a fresh load in the state that press left behind → the FULL list.
const b = await open(browser);
const full = await b.evaluate(() => window.__lists.at(-1));

// Put the map back where it was so the run is repeatable.
await b.evaluate(() => document.querySelector("button.btn-cast-up")
  .dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window, button: 0 })));
await b.waitForTimeout(3000);
await b.close();
await browser.close();

console.log("incremental:", incremental?.note);
console.log("full       :", full?.note);
console.log("lists match:", JSON.stringify(incremental?.list) === JSON.stringify(full?.list));
if (JSON.stringify(incremental?.list) !== JSON.stringify(full?.list)) {
  console.log("  incremental:", JSON.stringify(incremental?.list));
  console.log("  full       :", JSON.stringify(full?.list));
  process.exitCode = 1;
}
