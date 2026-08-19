/**
 * A capped scroller must keep its scroller.
 *
 * The floating chrome broke the vote-type menu by restyling it: the skin sheet
 * set `overflow: hidden` on `.vote-type-dropdown` to clip its rows to a corner
 * radius, and `overflow` is a SHORTHAND, so it also reset the `overflow-y:
 * auto` that the menu's own sheet had set. The menu kept its `max-height` and
 * lost its scrollbar — 585px of rows in a 459px box, the last 126px clipped
 * away and unreachable by wheel, trackpad, drag or keyboard. It read as two
 * bugs, "cannot scroll" and "cannot pick anything", and was one.
 *
 * The shape of that mistake is what this guards, not the one instance: an
 * element is made a CAPPED SCROLLER (a height cap plus an auto/scroll axis) by
 * one rule, and a later rule — usually a cosmetic one, in a different file,
 * written by someone thinking about corners — takes the scroller away with a
 * shorthand it did not mean to be reaching that far.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = join(fileURLToPath(new URL(".", import.meta.url)), "..");

function cssFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return cssFiles(full);
    return name.endsWith(".css") ? [full] : [];
  });
}

interface Rule {
  file: string;
  selector: string;
  body: string;
}

/**
 * Innermost rules only. The `[^{}]` classes mean a match can never span a
 * brace, so an `@media` wrapper is skipped over rather than mistaken for a
 * selector, and its nested rules are matched on their own.
 */
function rules(): Rule[] {
  const out: Rule[] = [];
  for (const file of cssFiles(SRC)) {
    const css = readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      out.push({ file: relative(SRC, file), selector: m[1].trim(), body: m[2] });
    }
  }
  return out;
}

const decl = (body: string, prop: string) =>
  new RegExp(`(^|;|\\s)${prop}\\s*:\\s*([^;]+)`).exec(body)?.[2].trim();

/** Class names named anywhere in a selector. */
const classesIn = (selector: string) =>
  [...selector.matchAll(/\.([A-Za-z0-9_-]+)/g)].map((m) => m[1]);

const ALL = rules();

/**
 * A class is a capped scroller when ONE rule both caps its height and turns an
 * axis into a scroller. Both halves are required: a cap without a scroller is
 * a clip on purpose, and a scroller without a cap never overflows.
 */
const cappedScrollers = new Set<string>();
for (const rule of ALL) {
  const cap = decl(rule.body, "max-height");
  const y = decl(rule.body, "overflow-y");
  const x = decl(rule.body, "overflow-x");
  const scrolls = [y, x].some((v) => v === "auto" || v === "scroll");
  if (cap && scrolls) for (const c of classesIn(rule.selector)) cappedScrollers.add(c);
}

describe("capped scroll panels", () => {
  it("finds the panels it is meant to protect", () => {
    // If a rename empties this set the suite would pass by doing nothing.
    expect(cappedScrollers.size).toBeGreaterThan(0);
    expect([...cappedScrollers]).toContain("vote-type-dropdown");
  });

  it("never has its scroller reset by the `overflow` shorthand", () => {
    const offenders: string[] = [];
    for (const rule of ALL) {
      const shorthand = decl(rule.body, "overflow");
      if (!shorthand) continue;
      // The rule that MAKES the scroller may of course say `overflow`; what
      // must not happen is a different rule reaching in and undoing it.
      if (decl(rule.body, "max-height")) continue;
      for (const c of classesIn(rule.selector)) {
        if (!cappedScrollers.has(c)) continue;
        offenders.push(
          `${rule.file}: \`${rule.selector}\` sets \`overflow: ${shorthand}\`, ` +
            `which resets the scroller on .${c}`,
        );
      }
    }
    expect(offenders).toEqual([]);
  });
});
