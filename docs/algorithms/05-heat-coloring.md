---
title: Heat colouring
description: What number a block's colour is actually showing, and how it becomes a position on the ramp.
sources:
  - path: client-react/src/components/GraphLayer/voteApply.ts
    anchors: [topProposalDiffs, applyBlockCounts, applyAuthoritativeCounts]
  - path: client-react/src/map/blockHeat.ts
    anchors: [makeHeatNormalization, HeatNormalization]
  - path: client-react/src/mapStyles.ts
    anchors: [HeatRamp, buildHeatRampStops, buildPinRampStops, sampleHeatRamp, heatGradientCss, heatTip, HEAT_PEAK_POS]
  - path: client-react/src/components/GraphLayer/geometryHelpers.ts
    anchors: [HEAT_FULL_SCALE, NEG_HEAT_FULL_SCALE, HEATMAP_OPACITY]
---

# Heat colouring

## Why it exists

The heatmap is the product's one-glance claim. Getting the number behind it
wrong doesn't produce a bug report — it produces a map that quietly lies, which
is worse.

The number is **not** "how many votes did this block get". It is the **signed
differential of the block's top-ranked proposal**: pick the block's best
proposal *by differential*, and show `up − down` for that one. A block whose
best idea is still net-opposed goes cold. A block where support and opposition
cancel goes invisible.

That choice matters. Total-votes heat would paint the most *argued-about* block
the hottest, which is the opposite of what a map of civic agreement should say.

## Inputs and outputs

```
per-block, per-type (up, down)          [server, deduped per device]
        │  topProposalDiffs             — pick the top proposal, keep its sign
        ▼
signed differential per block  +  observed per-arm ceilings
        │  makeHeatNormalization        — two-armed log, floored ceilings
        ▼
ramp position in [−1, 1]
        │  MapLibre feature-state + the style's ramp expression
        ▼
colour
```

## Pseudocode

```
── 1. Reduce each block to one signed number (topProposalDiffs) ────────
for each block:
    visible = its vote types not hidden by the legend filter
    diff[block] = max over visible types of (up - down)      # signed
    # "top-ranked proposal" is ranked BY DIFFERENTIAL, so a block whose
    # best idea is net-opposed yields a NEGATIVE number and renders cold.
maxPos = max(diff, 0);  maxNeg = max(-diff, 0)

── 2. Floor the ceilings ───────────────────────────────────────────────
max    = max(HEAT_FULL_SCALE,     maxPos)
maxNeg = max(NEG_HEAT_FULL_SCALE, maxNeg)
# Without the floor, a brand-new map's single vote IS the maximum and paints
# at full peak. The floors say "this is what a busy map looks like" so a
# quiet one reads as quiet.

── 3. Normalize, one log denominator per ARM (makeHeatNormalization) ───
denomPos = log(max + 1);   denomNeg = log(maxNeg + 1)
heat(v) = v > 0 ?  log(v + 1) / denomPos
                : -log(1 - v) / denomNeg
# log, not linear: vote totals are heavy-tailed, and a linear scale on a busy
#   map paints everything below the top corridor the same near-black.
# v + 1, not v: a differential of exactly 1 must be VISIBLE. The first vote on
#   a new map appearing to do nothing is a product failure.
# separate arms: opposition is rarer and smaller in magnitude than support, so
#   a shared denominator leaves every net-against block a faint smudge instead
#   of reaching the deep-cold end of the ramp.

── 4. Paint (MapLibreBackground) ───────────────────────────────────────
write heat as MapLibre feature-state on the "blocks" SOURCE
    full rewrite  only when a denominator moved (every lit block changes)
    otherwise     write only the blocks whose heat actually changed
    blocks that returned to zero are explicitly cooled to 0

── 5. Ramp (mapStyles) ─────────────────────────────────────────────────
buildHeatRampStops:  coldDeep → cold → halo → warm → hot → peak → tip
    `peak` sits at HEAT_PEAK_POS (0.85), NOT 1, with an incandescent tip
    above it — so the very top of the scale still has somewhere to go.
```

**Zero is invisible on purpose.** It is not a colour at the middle of the ramp;
it is no paint at all. A block where support and opposition cancel carries no
signal, and painting it lukewarm would claim a consensus that does not exist.

**The legend filter changes the ranking, not just the rendering.** With types
toggled off, each block re-ranks over only what is visible, so the heatmap
answers "how much support do *these* proposals have" rather than "…any
proposal". This is deliberate and is what makes the legend a genuine analysis
tool rather than a display toggle.

## Tuning knobs

| Knob | Value | Defined in | What breaks if you change it |
|---|---|---|---|
| `HEAT_FULL_SCALE` | `50` | `geometryHelpers.ts` | Positive-arm ceiling floor. Lower it and quiet maps saturate — every early vote paints at peak, and the map claims consensus it hasn't earned. Raise it and a real city-scale demand looks tepid. |
| `NEG_HEAT_FULL_SCALE` | `10` | `geometryHelpers.ts` | Negative-arm ceiling floor. Deliberately much tighter than the positive floor: opposition is rarer, so it needs a smaller denominator to be legible at all. |
| `HEATMAP_OPACITY` | `"0.55"` | `geometryHelpers.ts` | Fill opacity of the heat layer (a string — it is interpolated straight into a style value). Raise it and the basemap's street labels stop reading through the heat. |
| `HEAT_PEAK_POS` | `0.85` | `mapStyles.ts` | Where `peak` sits on the ramp, leaving the incandescent tip above it. Push it to 1 and the hottest blocks all flatten to the same colour. |

Each map style carries its own `HeatRamp` (`coldDeep`/`cold`/`halo`/`warm`/`hot`/`peak`),
so the *palette* is per-theme while the *arithmetic* above is global. Place
labels are muted toward neutral grey (`POI_MUTE`) specifically so the heat and
the top-proposal pins stay the poppiest thing on screen.

## Invariants

Enforced by `blockHeat.test.ts`, `voteApply.test.ts`, and `mapStyles.test.ts`:

- `heat(0) === 0` — and zero renders as nothing.
- `heat(max) === 1`, `heat(-maxNeg) === -1` — the arms hit their ends exactly.
- `heat(1) > 0` — one vote is visible.
- **Monotone and compressive.** More votes is always more heat, sublinearly.
- **Ceilings never divide by zero.** Both are floored at 1 internally, so an
  empty map is safe.
- **Feature-state lives on the SOURCE**, not the tile — so it survives tile
  reloads, which is what allows the diff-apply in step 4.

## Failure modes and history

| What went wrong | Why | Fix |
|---|---|---|
| Zooming re-rendered the whole map | Heat was rewritten in full on every `sourcedata` event — which fires **per tile**: ~4.4 M `setFeatureState` calls per zoom on the NYC bike map | Diff-apply keyed on `denomKey`; full rewrite only when a denominator moves |
| Contested blocks looked like consensus | Heat was total votes, so the most-argued block was the hottest | Signed top-proposal differential ([2026-07-23](https://github.com/edbltn/city-edit/blob/main/changelog/index.html)) |
| Net-against blocks were an invisible smudge | Both arms shared one denominator | Separate `denomNeg`, floored at `NEG_HEAT_FULL_SCALE` |
| A cold pin painted at `−0.001` was treated as unlit | Sign handling at the boundary | Zero handled explicitly as "no paint" |
| Heat vanished when you zoomed out | Not this pipeline — tippecanoe thinned `blocks.pmtiles` by density | `--no-tiny-polygon-reduction`, gated by `verify_blocks_tiles.py` |
| The heatmap disappeared mid-zoom | The canvas cleared on `zoomstart` and only repainted on `zoomend` | Ride Leaflet's zoom animation with a CSS transform ([2026-06-14](https://github.com/edbltn/city-edit/blob/main/changelog/2026-06-14-caching-concurrency-zoom.html)) |

## Extension points

The user-facing question this pipeline answers is *"how much net support does
this block's best idea have?"*. Several better questions are within reach, and
all of them are changes to **step 1 only** — steps 2–5 take any signed number.

- **Confidence, not just magnitude.** 60–40 from 100 people and 6–4 from 10 paint
  identically today. A Wilson score or a beta posterior would separate "settled"
  from "early", and could drive saturation while the differential drives hue.
- **Recency.** A corridor that was hot two years ago and is quiet now looks
  identical to a live campaign. A half-life weighting in `topProposalDiffs`
  would fix that, at the cost of making heat time-dependent (and therefore no
  longer a pure function of vote state — see the determinism note below).
- **Distinct voters, not vote rows.** Heat currently sums the deduped per-block
  counts; [dossier 07](07-counts.md) explains why that is not the same as
  people, and where the honest number comes from.
- **Multi-signal blends.** Crash data, transit access, and demographic equity
  are all available or derivable. Blending them into the ramp would change what
  the map *argues*, not just how it looks — worth doing deliberately, and worth
  a legend that says so.
- **Per-map heat semantics.** Maps already carry a `network` and a style. A map
  could plausibly choose its heat function (support, contention, participation)
  the way it chooses its palette.

> **Determinism caveat for anything time-based.** Everything above step 4 is
> currently a pure function of (vote state, legend filter). Introducing a clock
> breaks that, and the same break would ripple into
> [dossier 03](03-route-proposals.md), whose whole contract is byte-identical
> output on every client. If heat becomes time-dependent, the time input must be
> quantized and shared (e.g. a server-stamped epoch), not read from `Date.now()`.
