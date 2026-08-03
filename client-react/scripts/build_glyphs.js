#!/usr/bin/env node
/**
 * Generate the SDF glyph ranges MapLibre needs to draw the place-label layer.
 *
 * MapLibre cannot render a `text-field` from a webfont — it needs signed-
 * distance-field glyphs served as protobuf ranges. The style used to point at
 * demotiles.maplibre.org, which is MapLibre's DEMO server and not something to
 * put in front of real traffic, so we bake our own from the same Red Hat Mono
 * the rest of the UI uses and serve them as static assets.
 *
 * Only Latin ranges are generated. Non-Latin names (Chinatown's Chinese shop
 * names, for one) are handled at runtime by MapLibre's `localIdeographFontFamily`,
 * which draws CJK from a system font instead of from SDF glyphs — Red Hat Mono
 * has no CJK coverage, so without that those labels would render as nothing.
 *
 *   npm run build:glyphs        # from client-react/
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import fontnik from "fontnik";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const FONT_URL =
  "https://github.com/google/fonts/raw/main/ofl/redhatmono/RedHatMono%5Bwght%5D.ttf";
const FONTSTACK = "Red Hat Mono Regular";

// Basic Latin + Latin-1 Supplement + Latin Extended-A/B cover US place names
// including accented ones; General Punctuation carries the curly quotes and
// dashes that show up in business names ("Joe's" is often U+2019, not U+0027).
const RANGES = [
  [0, 255],
  [256, 511],
  [512, 767],
  [8192, 8447],
];

async function main() {
  const fontPath = process.argv[2];
  const outDir = path.resolve(__dirname, "..", "public", "fonts", FONTSTACK);

  let font;
  if (fontPath) {
    font = fs.readFileSync(fontPath);
  } else {
    process.stdout.write(`downloading ${FONT_URL}\n`);
    const res = await fetch(FONT_URL);
    if (!res.ok) throw new Error(`font download failed: ${res.status}`);
    font = Buffer.from(await res.arrayBuffer());
  }

  fs.mkdirSync(outDir, { recursive: true });
  for (const [start, end] of RANGES) {
    const buffer = await new Promise((resolve, reject) => {
      fontnik.range({ font, start, end }, (err, data) =>
        err ? reject(err) : resolve(data),
      );
    });
    const outPath = path.join(outDir, `${start}-${end}.pbf`);
    fs.writeFileSync(outPath, buffer);
    process.stdout.write(`  ${start}-${end}.pbf  ${buffer.length} bytes\n`);
  }
  process.stdout.write(`glyphs written to ${outDir}\n`);
}

main().catch((err) => {
  process.stderr.write(`${err.stack}\n`);
  process.exit(1);
});
