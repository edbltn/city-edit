/** Print a before/after table from two measure.mjs result files.
 *  Usage: node compare.mjs results/baseline.json results/optimized.json */
import { readFileSync } from "fs";

const [aPath, bPath] = process.argv.slice(2);
const A = JSON.parse(readFileSync(aPath, "utf8"));
const B = JSON.parse(readFileSync(bPath, "utf8"));

const rows = [
  ["load→heatmap (ms)", (m) => m.loadToHeatmapMs, false],
  ["pan avg FPS", (m) => m.pan.avgFps, true],
  ["pan p95 frame (ms)", (m) => m.pan.p95FrameMs, false],
  ["pan jank frames/drag", (m) => m.pan.jankFramesPerDrag, false],
  ["pan redraw delay (ms)", (m) => m.pan.redrawDelayMs, false],
  ["post-pan max frame (ms)", (m) => m.pan.postPanMaxFrameMs, false],
  ["zoom avg FPS", (m) => m.zoom.avgFps, true],
  ["zoom p95 frame (ms)", (m) => m.zoom.p95FrameMs, false],
  ["zoom blackout (ms)", (m) => m.zoom.blackoutMs, false],
];

console.log(`\n  ${A.label}  vs  ${B.label}`);
console.log(`  (viewport ${A.viewport.width}x${A.viewport.height}@2x, cpu ${A.cpuThrottle ?? 1}x)\n`);
const table = {};
for (const [name, get, higherBetter] of rows) {
  const a = get(A.metrics);
  const b = get(B.metrics);
  if (a == null || b == null) { table[name] = { [A.label]: a, [B.label]: b, delta: "n/a" }; continue; }
  const pct = a === 0 ? (b === 0 ? 0 : Infinity) : ((b - a) / a) * 100;
  const better = higherBetter ? b > a : b < a;
  const sign = pct > 0 ? "+" : "";
  table[name] = {
    [A.label]: a,
    [B.label]: b,
    delta: `${sign}${pct.toFixed(0)}% ${a === b ? "=" : better ? "✓ better" : "✗ worse"}`,
  };
}
console.table(table);
