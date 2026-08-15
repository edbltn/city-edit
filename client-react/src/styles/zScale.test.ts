/**
 * The z-index ladder, enforced.
 *
 * Two collisions landed in one day — a nav-rail tooltip that read as hidden,
 * and an event banner painting over an open proposal card — and both were
 * "fixed" historically by picking a number big enough to win that one fight.
 * That is what produced 990 / 1000 / 1050 / 1100 / 1300 / 1400 / 3000 / 9999.
 * These tests make the ladder in globals.css the only place the order can be
 * decided, so the next collision has to be argued about as an ORDER rather
 * than settled with a bigger number.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = join(fileURLToPath(new URL(".", import.meta.url)), "..");
const GLOBALS = readFileSync(join(SRC, "styles/globals.css"), "utf8");

/** The ladder, lowest rung first. Changing this list changes the app's order. */
const LADDER = [
  "--z-map",
  "--z-map-overlay",
  "--z-map-notice",
  "--z-map-card",
  "--z-map-card-elevated",
  "--z-map-card-hover",
  "--z-chrome",
  "--z-dropdown",
  "--z-autocomplete",
  "--z-splash",
  "--z-modal",
  "--z-pac-container",
  "--z-ribbon",
  "--z-crash",
];

/**
 * The landing page is a different screen: no map, no chrome, no dialogs — just
 * a hero and a search box, whose handful of local values order nothing outside
 * themselves. Everything else has to name a rung.
 */
const RAW_Z_ALLOWED = new Set(["components/Landing/Landing.css"]);

function cssFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return cssFiles(full);
    return name.endsWith(".css") ? [full] : [];
  });
}

function rungValue(token: string): number {
  const m = GLOBALS.match(new RegExp(`${token}:\\s*(\\d+)\\s*;`));
  if (!m) throw new Error(`${token} is not defined in globals.css`);
  return Number(m[1]);
}

describe("z-index ladder", () => {
  it("defines every rung", () => {
    for (const token of LADDER) expect(() => rungValue(token)).not.toThrow();
  });

  it("climbs: each rung is strictly above the one below it", () => {
    const values = LADDER.map((t) => [t, rungValue(t)] as const);
    for (let i = 1; i < values.length; i++) {
      const [, prev] = values[i - 1];
      const [, value] = values[i];
      expect(value).toBeGreaterThan(prev);
    }
  });

  it("keeps the rungs far enough apart to slot a variant between them", () => {
    const values = LADDER.map(rungValue);
    for (let i = 2; i < values.length; i++) {
      expect(values[i] - values[i - 1]).toBeGreaterThanOrEqual(100);
    }
  });

  it("puts a dialog over the chrome, and the crash screen over everything", () => {
    expect(rungValue("--z-modal")).toBeGreaterThan(rungValue("--z-chrome"));
    // The loader doubles as the passcode gate's backdrop: a backdrop that
    // outranks its own dialog hides it.
    expect(rungValue("--z-modal")).toBeGreaterThan(rungValue("--z-splash"));
    // The bug this ladder was written for: a transient strip must never cover
    // the proposal the reader opened.
    expect(rungValue("--z-map-card")).toBeGreaterThan(rungValue("--z-map-notice"));
    expect(rungValue("--z-crash")).toBe(Math.max(...LADDER.map(rungValue)));
  });

  it("is the only place a z-index comes from", () => {
    const offenders: string[] = [];
    for (const file of cssFiles(SRC)) {
      const rel = relative(SRC, file);
      if (RAW_Z_ALLOWED.has(rel)) continue;
      for (const line of readFileSync(file, "utf8").split("\n")) {
        const m = line.match(/^\s*z-index:\s*([^;]+);/);
        if (!m) continue;
        const value = m[1].trim();
        const named = value.startsWith("auto") || /var\(--z-/.test(value);
        if (!named) offenders.push(`${rel}: z-index: ${value}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
