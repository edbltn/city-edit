# Map labels: the two tiers, and why the fonts don't match

The map draws text from two independent systems, and they are set in different
typefaces. This document says exactly what each tier owns, what it would take to
put them both in one font, and why — as of 2026-08-19 — we are choosing not to.

## The two tiers

**Tier 1 — CARTO's label raster.** `dark_only_labels` / `light_only_labels`,
transparent PNGs of the same cartography as the `*_nolabels` basemap under it,
drawn *above* the vote heat (`mapStyles.ts` `labelTileUrl`, added last in
`MapLibreBackground.buildStyle`). Typeface: **Montserrat**. It carries street
names, place names, water, parks and a few landmarks.

**Tier 2 — our own `places.pmtiles`.** Businesses, transit, civic and culture
POIs from each city's `source.osm.pbf` (`server/build_place_labels.py`), drawn as
a MapLibre symbol layer with self-hosted SDF glyphs. Typeface: **Red Hat Mono**,
the same face as the rest of the UI.

Tier 1 is pre-rendered pixels. There is no font knob, no halo knob, no colour
knob — only `raster-opacity`. So "make the basemap labels match our font" is not
a styling change. It is a decision to render those labels ourselves.

## What tier 1 actually provides

Enumerated from CARTO's own `dark-matter-gl-style` (the cartography the raster is
rendered from) and verified against fetched `dark_only_labels` tiles over NYC.
Zooms below are **raster/style zooms**; the app's Leaflet zoom is one higher, and
`places.pmtiles` `minz` is in Leaflet units (see `build_place_labels.py`).

| Class | Style layers | Style zoom | Leaflet zoom |
|---|---|---|---|
| Motorway + trunk names | `roadname_major` | 13+ | 14+ |
| Primary road names | `roadname_pri` | 14+ | 15+ |
| Secondary + tertiary names | `roadname_sec` | 15+ | 16+ |
| Minor + service road names | `roadname_minor` | 16+ | 17+ |
| House numbers | `housenumber` | 17+ | 18+ |
| Country / state / continent | `place_country_*`, `place_state`, … | 0–10 | 1–11 |
| City, town, village | `place_city_*`, `place_town`, `place_villages` | 4–15 | 5–16 |
| Suburb (borough), neighbourhood, hamlet | `place_suburbs`, `place_hamlet` | 12–16 | 13–17 |
| Ocean / sea / lake / river names | `watername_*`, `waterway_label` | 0+ | 1+ |
| Park, stadium, cemetery, attraction | `poi_park`, `poi_stadium` | 15+ | 16+ |

Two things fall out of that table.

**A takeover is much bigger than "neighborhood names."** It is streets, water,
green space and landmarks as well. Drop the raster and every one of those
disappears at once.

**The mismatch you actually see is a street-name mismatch.** Counted by hand off
the captures in the report linked below: a mid-Manhattan screen at Leaflet z15
shows **one** place label (`MANHATTAN`) against roughly sixty street labels; a
sparse Queens screen at z15 shows **two** place labels against roughly forty-five
street labels. Neighborhood names are the ones you notice, but they are a
rounding error in the actual quantity of mismatched type on screen.

## There is no CARTO variant that splits streets from places

CARTO publishes exactly three raster splits per basemap — `_all`, `_nolabels`,
`_only_labels` — plus `rastertiles/voyager_labels_under`, which puts the whole
label set *under* the geometry rather than subsetting it. Every speculative
streets-only name returns 404 (`dark_only_streets`, `dark_only_labels_streets`,
`dark_street_labels`, `dark_labels_under`, `dark_only_labels_no_places`).

So the shrunken version of this job — keep CARTO for streets, render place names
ourselves — **is not available**. Both tiers would draw the same neighborhood,
in two fonts, on the same anchor. That is precisely the failure the builder's own
docstring records for green space: labelling parks in both cartographies printed
"Grand Street Garden" twice and read as a broken map. Adding place names on top
of a live raster is the same bug, deliberately.

Masking the raster's place labels client-side was considered and rejected: we know
where all 365 anchors are, but a patch big enough to cover the text also covers
the streets under it, and text extents are not knowable from the tile.

## What the full takeover costs

Measured against `server/osm_data/nyc/source.osm.pbf` (all named `highway=*` ways
in the extract), grouped by the CARTO tier that would have to be reproduced:

| Tier | Classes | Named ways | Coordinate nodes | Distinct names |
|---|---|---|---|---|
| z13 | motorway, trunk (+links) | 19,319 | 190,382 | ~655 |
| z14 | primary (+link) | 34,221 | 308,196 | ~1,850 |
| z15 | secondary, tertiary (+links) | 70,574 | 1,021,947 | ~10,761 |
| z16 | residential, unclassified, living_street, service | 294,832 | 3,838,728 | ~110,141 |
| **Total** | | **418,946** | **5,359,253** | |

(A further 38,768 named ways are footway/path/cycleway/track/steps — geometry
CARTO carries but deliberately does *not* road-label.)

For comparison, the place names the complaint is actually about are **365
features** for all of NYC: 304 `neighbourhood`, 21 `quarter`, 16 `village`, 15
`town`, 6 `suburb`, 3 `city` (named `place=*` nodes inside the city bbox). That
half is nearly free. The street half is the whole job.

The work behind those numbers:

1. **A second PBF pass for named ways.** Folded into the existing
   `PoiCollector` pass, but it needs way geometry, not centroids.
2. **Name-chain merging.** OSM chunks a street into dozens of ways; 260,006
   residential ways carry 91,109 distinct names. Without merging into maximal
   connected polylines per name, `symbol-placement: line` reprints the name every
   ~80 m. Merging must be by *connectivity*, not name — "Broadway" exists in four
   boroughs.
3. **A line tiler we do not have.** `build_place_labels._collect_tiles` is built
   on the fact that "a point label is never clipped by a tile edge"; line
   geometry needs real clipping and buffering. The honest answer is tippecanoe —
   which the blocks bake already uses but which **is not installed in the prod
   image** (`Dockerfile` apt-gets nginx, supervisor and curl only).
4. **The green space, water and landmark names we gave away**, taken back:
   `build_place_labels.py` drops parks *because CARTO owns them*. A takeover
   un-makes that decision and adds polygon-centroid and line-placed labels.
5. **Client re-tuning.** The basemap labels stop being inert pixels and join
   MapLibre's collision index, competing with POI and proposal labels. That is a
   real improvement (see below) but it moves every existing placement, so the
   `POI_MUTE` / sort-key / reveal-zoom tuning gets re-litigated.
6. **Build and ship.** The new stage must sit inside the Dockerfile `RUN` before
   `rm -f osm_data/*/source.osm.pbf`, and a new artifact needs its own overlay
   build, Flask route and map-config field.

### The walk-graph shortcut does not work

`graph.pmtiles` already ships every edge with `name` and `highway`, which looks
like free street-label geometry. It is not:

- The foot profile (`server/foot_profile.py` `_ROUTABLE_HIGHWAY`) excludes
  `motorway`, `trunk` and `cycleway`, so the 19,319 named ways CARTO labels
  *first* — the FDR, the BQE, the tunnels and bridges — are absent.
- It includes named `footway` / `path` / `steps`, which CARTO does not label.
- Each edge is a separate two-point LineString, which is the merging problem in
  its worst possible form.

### What we would lose

Our first version would be worse cartography than CARTO's: no route shields, no
`ref` numbers, no generalisation, no `name_en` fallbacks, coarser zoom tuning —
and a permanent maintenance surface for something currently free, correct and
maintained by someone else.

## The alternative that does solve it: CARTO's vector tiles

`https://tiles.basemaps.cartocdn.com/vector/carto.streets/v1/` serves the vector
tiles the raster is rendered from, publicly and without a key. Point a MapLibre
symbol layer set at it and every basemap label — streets included — is ours to
set in Red Hat Mono, with our own halo and colour, with no data pipeline at all.

The cost is bytes. Measured on the z14 tile covering midtown Manhattan:

| | Bytes (gzipped, as served) |
|---|---|
| Whole vector tile | 300,090 |
| The label layers we would use (`place`, `transportation_name`, `water_name`, `park`, …) | 15,731 |
| The `poi` layer alone — 4,661 features we already have our own copy of | 131,796 |
| `housenumber` — 1,112 features we would never draw | 26,545 |
| Equivalent raster label coverage (4 × `dark_only_labels` @2x) | 57,404 |

So roughly **5× the label bytes to use 5% of them**, plus parsing and holding
4,661 POIs and 1,112 house numbers per tile in worker memory. On the app whose
recorded history is a mobile-Safari heatmap memory crash and a `cityedit_map_load_ms`
P99 dashboard row, that is the wrong axis to spend on for a typeface.

## Reversing the direction

Baking Montserrat SDF glyphs and pointing `PLACE_LABEL_FONT` and
`PROPOSAL_LABEL_FONT` at it would make the two tiers match for the price of one
`npm run build:glyphs`. It is rejected because Red Hat Mono is not a map-label
choice, it is the app's face — `build_glyphs.js` bakes it precisely because it is
"the same Red Hat Mono the rest of the UI uses". Matching the basemap would
un-match the chrome, and would erase the cue that mono currently carries: *this
text is City Edit's data, not the basemap's*.

## Decision

**Leave the mismatch.** The cheap half of the takeover (place names, 365
features) cannot ship on its own, because the raster keeps drawing them and we
would double-label every neighborhood. The half that would actually change what
you see is street names, and that is a multi-day pipeline plus a new build
dependency plus permanently worse cartography than the thing it replaces.

### What would change the answer

- **CARTO publishing a streets-only label raster.** The job collapses to the 365
  place features and one small builder change. Worth re-checking occasionally.
- **Wanting cross-tier collision avoidance for its own sake.** Today the two
  systems cannot see each other, and it shows: in the captures, `MANHATTAN` sits
  under a proposal pin and our `5th Avenue–53rd Street` prints across CARTO's own
  `53rd Street`. That is a real defect, it is more consequential than the
  typeface, and the takeover is the only thing that fixes it. If that becomes the
  goal, the font falls out for free — but then it is a legibility project with a
  budget, not a font tweak.
- **Any move to a vector basemap for other reasons** (theming, dark/light
  parity, offline). The font question stops being separately expensive.

### Traps for whoever revisits this

- `minz` is a **Leaflet** zoom; the client filter compares `["+", ["zoom"], 1]`.
  The CARTO tiers in the table above are style zooms. Confusing the two moves
  every reveal zoom by one step.
- **Rank and floor are separate knobs.** Place names want a *floor* per class
  (mirroring CARTO's tiers) and *rank* only to break collisions among themselves.
  Demoting a rank when a floor was meant is how 496 subway stops once vanished to
  z17–19.
- Street labels must **not** go through `assign_zooms`. The grid thinner is for
  point labels; line labels want MapLibre's own collision plus `symbol-spacing`.
  New point classes need their own grid, not a shared one — the transit grid is
  finer for exactly this reason.
- New label classes must join the `POI_MUTE = 0.58` discipline. Basemap text is
  orientation furniture; the top-proposal pins stay the loudest thing on the map.
- The tile build runs **inside the Dockerfile `RUN`, before
  `rm -f osm_data/*/source.osm.pbf`**. A stage added after that line has no PBF.
