/**
 * MapLibre GL JS background map — renders base map tiles and the OSM graph
 * from PMTiles. Sits behind the Leaflet overlay (which handles interactive
 * layers: routes, markers, drag-to-insert).
 *
 * Camera is synchronized from the Leaflet map via `move` events.
 */

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";
import { CONFIG, type BlockTilesConfig } from "../../config";
import { dlog, dburst, dwarn, debugState } from "../../utils/debugLog";
import {
  maplibreRasterTiles,
  maplibreLabelTiles,
  heatTip,
  POI_COLORS,
  proposalLabelColors,
  type MapStyle,
} from "../../mapStyles";
import { makeHeatNormalization, type HeatNormalization } from "../../map/blockHeat";
import {
  createArrivalAnimator,
  type ArrivalAnimator,
  type HeatChange,
} from "../../map/arrivalAnimation";
import { hottestBlockId } from "./hottestBlock";
import { registerPlaceIcons, placeIconExpression } from "./placeIcons";
import { PLACE_LABEL_TEXT_SIZE } from "./placeLabelStyle";
import {
  PIN_W,
  PIN_H,
  PROPOSAL_LABEL_FILTER,
  PROPOSAL_LABEL_FONT,
  PROPOSAL_LABEL_TEXT_SIZE,
  PROPOSAL_MIN_LEAFLET_ZOOM,
  PROPOSAL_PUCK_IMAGE,
  PROPOSAL_PUCK_OFFSET,
  PROPOSAL_PUCK_SIZE,
  PROPOSAL_TEXT_MAX_WIDTH,
  PROPOSAL_TEXT_OFFSET_EM,
  proposalTextField,
} from "./proposalLabelStyle";

// Register PMTiles protocol once at module level
const protocol = new Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

// Leaflet zoom N ≠ MapLibre zoom N: Leaflet's scale is defined against 256px
// world tiles, MapLibre's against 512px, so the same number renders MapLibre
// at exactly 2× Leaflet's scale. Subtract 1 when driving the MapLibre camera
// from Leaflet or the two renderers agree only at the screen center and every
// Leaflet-drawn layer (pins, routes, hover edges) drifts outward from there.
const LEAFLET_TO_MAPLIBRE_ZOOM = 1;

// Block-vote payload broadcast by GraphLayer (which owns the /api/graph-votes
// fetch). MapLibreBackground colors the block fills from it via feature-state.
export interface BlockVotesDetail {
  // SIGNED vote differential (up − down) of each block's top-ranked proposal
  // (ranked by differential — the max across the block's vote types). Positive
  // reads on the warm arm of the ramp, negative on the cold arm, zero is
  // invisible (cancelled/contested signal carries no heat).
  blockDiff: ArrayLike<number>;
  max: number;    // positive-arm ceiling (floored so quiet maps don't saturate)
  maxNeg: number; // negative-arm ceiling (own, tighter floor — see GraphLayer)
  // Why this payload was broadcast. ONLY "delta" — a vote landing, the caster's
  // own or anyone else's, arriving over the WebSocket delta hub — animates (see
  // map/arrivalAnimation). Loads paint instantly because the whole map is
  // arriving at once and a citywide shimmer says nothing; a legend toggle
  // paints instantly because re-ranking every block is the user's own filter
  // action, not news from the city. Absent → treated as "load".
  source?: "load" | "delta" | "filter";
}
export const BLOCK_VOTES_EVENT = "city-edit:block-votes";

// Block-selection payload broadcast by GraphLayer whenever the selection/hover
// changes: the real block ids covering the selected/hovered edges. Rendered as
// feature-state { selected } on the block-select layers (docs §2.4 highlight).
export interface BlockSelectDetail {
  blockIds: number[];
}
export const BLOCK_SELECT_EVENT = "city-edit:block-select";

// Top-proposal labels broadcast by GraphLayer whenever the two proposal lists
// (PBTP squares, RBTP diamonds) settle. The PINS stay Leaflet's — they are
// interactive, and MapLibre here is `interactive: false` — but their WORDS are
// drawn as a GL symbol layer, because the one thing a hand-rolled label on a
// divIcon cannot do is get out of another label's way. See addProposalLabels.
export interface ProposalLabelDetail {
  /** Stable per-proposal key: the PBTP's edge index or the RBTP's id. Used as
   *  the GeoJSON feature id, so it must not collide between the two families. */
  key: string;
  lat: number;
  lng: number;
  /** Vote-type label, already uppercased for drawing. */
  text: string;
  /** Signed vote count, already formatted ("+412", "+12.4k"). */
  count: string;
  /** 0 = strongest. Drives both the reveal zoom and collision priority. */
  rank: number;
  /** Leaflet zoom this label may first appear at (proposalRevealZoom(rank)). */
  minz: number;
}
export const PROPOSAL_LABELS_EVENT = "city-edit:proposal-labels";

// ── Block hit-test bridge ───────────────────────────────────────────────────
// GraphLayer's hover/selection resolver constrains its nearest-edge/node search
// to the block whose polygon is under the cursor (the block defines the
// eligible member set; no block → unrestricted). Only this component owns the
// MapLibre instance, so it registers the point→block resolver here;
// module-scoped so the Leaflet-side GraphLayer can call it synchronously on
// every mousemove without a React bridge.
let blockAtResolver: ((lat: number, lng: number) => number | null) | null = null;

/** Block id whose polygon contains the point, or null (no block there, or the
 *  MapLibre style isn't ready — callers treat null as "unrestricted"). */
export function blockIdAtLatLng(lat: number, lng: number): number | null {
  return blockAtResolver ? blockAtResolver(lat, lng) : null;
}

/** fill-color / fill-opacity expressions driven by feature-state "heat" ∈ [−1,1].
 *  Heat is the SIGNED top-proposal differential: 0 is fully transparent (no net
 *  signal → invisible), the positive arm ramps warm → incandescent tip, the
 *  negative arm ramps into the style's cold colors (net-against blocks read
 *  almost blue at the floor). Blocks ARE the heat display, so there is no
 *  baseline edge layer (edges show only on Leaflet hover/selection). */
function blockFillPaint(style: MapStyle): maplibregl.FillLayerSpecification["paint"] {
  const heat = style.heat;
  const h: maplibregl.ExpressionSpecification = ["coalesce", ["feature-state", "heat"], 0];
  return {
    // Positive arm starts at `warm` (not halo) so even a single vote reads
    // clearly, and the fill stays translucent — the outline layer below
    // carries the brightness, so a voted block doesn't render as a solid slab.
    // Stops span the FULL heat domain, ending in the incandescent tip: votes
    // are heavy-tailed, so the log-normalized top of a busy map piles up near
    // 1.0 — a ramp that plateaus early (the old 0.6 → peak flat top) painted
    // every hot corridor the same color. Now peak→tip keeps resolving there.
    // The negative arm mirrors it in cold: any net-against block wears `cold`,
    // deepening to `coldDeep` at the negative ceiling. The tiny stop at −0.001
    // pins the whole mild-negative range to `cold` — without it a −0.1 block
    // would interpolate mostly toward `warm` and read as faint support.
    "fill-color": [
      "interpolate", ["linear"], h,
      -1.0, heat.coldDeep,
      -0.001, heat.cold,
      0.0, heat.warm,
      0.35, heat.hot,
      0.7, heat.peak,
      1.0, heatTip(heat, style.basemap),
    ],
    // Opacity rises with |heat|, symmetric about the invisible zero. The
    // negative arm peaks slightly higher: cold fills fight the basemap harder
    // than additive/multiply warm tones do.
    "fill-opacity": [
      "interpolate", ["linear"], h,
      -1.0, 0.72,
      -0.001, 0.38,
      0.0, 0.0,
      0.001, 0.38,
      1.0, 0.66,
    ],
  };
}

/** Thin bright outline tracing a voted block — the crisp edge that makes the
 *  heat pop without thickening the fill. Same ramp, heat-driven opacity, and a
 *  width that grows with heat so the hottest blocks read etched, not just lit. */
function blockLinePaint(style: MapStyle): maplibregl.LineLayerSpecification["paint"] {
  const heat = style.heat;
  const h: maplibregl.ExpressionSpecification = ["coalesce", ["feature-state", "heat"], 0];
  return {
    "line-color": [
      "interpolate", ["linear"], h,
      -1.0, heat.coldDeep,
      -0.001, heat.cold,
      0.0, heat.warm,
      0.35, heat.hot,
      0.7, heat.peak,
      1.0, heatTip(heat, style.basemap),
    ],
    "line-width": [
      "interpolate", ["linear"], h,
      -1.0, 2.0,
      -0.7, 1.4,
      0.0, 1.1,
      0.7, 1.4,
      1.0, 2.0,
    ],
    "line-opacity": [
      "interpolate", ["linear"], h,
      -1.0, 1.0,
      -0.001, 0.72,
      0.0, 0.0,
      0.001, 0.72,
      1.0, 1.0,
    ],
  };
}

/** The arrival spark: a bright hairline ring that traces a block for the ~half
 *  second after a vote lands on it, then leaves nothing behind. Driven by
 *  feature-state "arrive" ∈ [0,1], which map/arrivalAnimation eases from a fast
 *  attack down to zero — this layer is pure paint, so the whole gesture is a
 *  per-frame GPU evaluation of one opacity, with no geometry work.
 *
 *  ONE constant colour, deliberately. The spark says "here, just now"; the heat
 *  underneath — already easing up or down the ramp over the same window — says
 *  what landed and in which direction. Keeping the colour constant also keeps
 *  `line-color` out of the per-vertex paint attributes, so this layer adds
 *  exactly one data-driven binder to the block tiles' vertex memory, which is
 *  the kind of thing that matters on the NYC tileset. heatTip is the ramp's
 *  incandescent end and flips polarity with the basemap for free: a warm
 *  near-white flare on the dark maps, a deep ink one on the light (multiply)
 *  maps, either way the map's own identity hue rather than a generic white. */
function blockArrivalPaint(style: MapStyle): maplibregl.LineLayerSpecification["paint"] {
  const arrive: maplibregl.ExpressionSpecification = [
    "coalesce", ["feature-state", "arrive"], 0,
  ];
  return {
    "line-color": heatTip(style.heat, style.basemap),
    "line-opacity": ["*", arrive, 0.85],
    // A touch wider than the heat outline (1.1–2.0px) so the pulse reads as a
    // ring AROUND the block rather than as its outline briefly changing colour.
    "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1.4, 16, 2.6],
    // Just enough bleed to look like light rather than like a border.
    "line-blur": 0.9,
  };
}

/**
 * Swap the blocks source onto the city's z/x/y tile template. The GL map is
 * created at mount, but the map config (and with it CONFIG.blockTiles) resolves
 * from the network — whichever comes second must reconcile, so buildStyle reads
 * CONFIG at map creation and this rebinds on the map's `load` event. setTiles()
 * reloads the source in place; tile bytes are identical to the pmtiles:// path,
 * and feature-state lives on the source, so applied heat survives the swap.
 */
function rebindBlockTiles(map: maplibregl.Map): void {
  const blockTiles = CONFIG.blockTiles;
  if (!blockTiles) return;
  const src = map.getSource("blocks") as maplibregl.VectorTileSource | undefined;
  if (!src || typeof src.setTiles !== "function") return;
  const template = blockTiles.template.startsWith("http")
    ? blockTiles.template
    : `${window.location.origin}${blockTiles.template}`;
  if (src.tiles?.[0] === template) return;
  src.setTiles([template]);
  dlog("maplibre", "blocks source rebound to z/x/y template", template);
}

/** The font stack baked by `npm run build:glyphs` and served from /fonts. */
const PLACE_LABEL_FONT = "Red Hat Mono Regular";

/**
 * Add the place/business label layer, if the active city ships one.
 *
 * Done on the map's `load` event rather than in buildStyle because the GL map
 * is created at mount while CONFIG.placeTilesUrl only resolves once the map
 * config lands from the network — reading it here means we never add a source
 * for a city that has no labels, instead of adding one that 404s on every pan.
 *
 * The layer is added LAST, so it sits above the basemap's own CARTO label
 * raster as well as the heat. The two label systems can't collide-avoid each
 * other (one is baked pixels, the other live symbols), which is the accepted
 * cost of keeping CARTO's street-name cartography; POIs anchor inside blocks
 * while street names run along centerlines, so they mostly stay out of each
 * other's way.
 */
async function addPlaceLabels(map: maplibregl.Map, mapStyle: MapStyle): Promise<void> {
  const url = CONFIG.placeTilesUrl;
  if (!url) {
    dlog("maplibre", "no place-label tiles for this city — skipping layer");
    return;
  }
  if (map.getSource("places")) return;

  map.addSource("places", { type: "vector", url: `pmtiles://${url}` });

  // Icons are rasterised before the layer is added, so MapLibre never renders a
  // frame asking for an image that doesn't exist yet.
  await registerPlaceIcons(map, mapStyle.basemap);
  if (!map.getSource("places")) return; // style torn down while we rasterised

  const palette = POI_COLORS[mapStyle.basemap];
  const colorByCategory = [
    "match", ["get", "cat"],
    ...Object.entries(palette).flatMap(([cat, color]) => [cat, color]),
    palette.retail, // a category this client build doesn't know yet
  ] as unknown as maplibregl.ExpressionSpecification;

  map.addLayer({
    id: "place-labels",
    type: "symbol",
    source: "places",
    "source-layer": "places",
    // `minz` is a LEAFLET zoom (the units used by CONFIG.maxZoom, map URLs and
    // the builder); MapLibre drives its camera one level lower, hence the +1.
    filter: ["<=", ["get", "minz"], ["+", ["zoom"], 1]],
    layout: {
      // Icon above, name below. `text-optional` is what makes this degrade
      // gracefully: where a block is too busy for both, MapLibre drops the text
      // and keeps the mark, so a dense strip thins into icons instead of either
      // vanishing or turning into a wall of names.
      "icon-image": placeIconExpression,
      "icon-size": ["interpolate", ["linear"], ["zoom"], 11, 0.72, 15, 0.84, 18, 0.94],
      "icon-anchor": "center",
      "icon-padding": 2,
      "text-optional": true,
      "text-anchor": "top",
      "text-offset": [0, 0.62],
      "text-field": ["get", "name"],
      "text-font": [PLACE_LABEL_FONT],
      // Ramped by zoom, with subway stops a point larger. Defined and tested in
      // placeLabelStyle.ts — the shape is load-bearing (one zoom curve only) in
      // a way that fails silently, so it gets a guard.
      "text-size": PLACE_LABEL_TEXT_SIZE,
      // Wrap width is in characters, and mono means characters are uniformly
      // wide — 9 turned "New York Institute of Technology" into a three-line
      // clump. 11 keeps most names to two lines without widening the footprint
      // enough to start losing collisions.
      "text-max-width": 11,
      "text-line-height": 1.1,
      "text-letter-spacing": 0.01,
      // Keeps labels from crowding each other into an unreadable clump — the
      // last line of defence after the builder's grid thinning.
      "text-padding": 6,
      // Lower sorts first and wins placement. `minz` IS the importance
      // ranking: the builder reveals a POI at the earliest zoom its rank can
      // claim, so an early-revealing landmark outranks a late-revealing deli
      // without shipping a separate rank property.
      "symbol-sort-key": ["get", "minz"],
    },
    paint: {
      "text-color": colorByCategory,
      // Halo in the basemap's own paper/ink so a label stays readable over a
      // saturated heat block — the contrast control the raster label overlay
      // above it doesn't get to have.
      "text-halo-color": mapStyle.base,
      // Scaled down with the type: at 1.5 against ~9px text the halo starts to
      // read as a bold weight rather than as separation from the basemap.
      "text-halo-width": 1.2,
      "text-halo-blur": 0.3,
      // Icons are already baked in their (muted) category colour; this takes
      // them down again so a mark reads as annotation and never as a pin.
      "icon-opacity": 0.72,
    },
  });
  dlog("maplibre", "place labels added", url);
}

/** Source and layer ids for the proposal label layer. */
const PROPOSAL_SOURCE = "proposal-labels";
const PROPOSAL_LAYER = "proposal-labels";

/** An empty payload, so the layer can exist before GraphLayer has anything to
 *  say — the alternative is adding the layer late and having to re-establish
 *  its position on top of the stack. */
const EMPTY_PROPOSALS: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

/**
 * Register the transparent stand-in for a proposal PIN.
 *
 * The pins are Leaflet divIcons floating above the GL canvas, so MapLibre has
 * no idea they exist and will cheerfully place a deli's name straight through
 * one. This puts a PIN_W × PIN_H hole in the collision index at each proposal's
 * location: fully transparent pixels (a zeroed RGBA buffer — collision boxes
 * come from the image's dimensions, never its contents), positioned by
 * PROPOSAL_PUCK_OFFSET to coincide exactly with the pin drawn over it.
 *
 * It is registered at pixelRatio 1 rather than placeIcons' 2 because there is
 * nothing to keep crisp; the size is a footprint, not artwork.
 */
function registerProposalPuck(map: maplibregl.Map): void {
  if (map.hasImage(PROPOSAL_PUCK_IMAGE)) return;
  map.addImage(
    PROPOSAL_PUCK_IMAGE,
    { width: PIN_W, height: PIN_H, data: new Uint8Array(PIN_W * PIN_H * 4) },
    { pixelRatio: 1 },
  );
}

/**
 * Add the top-proposal label layer — the words beside each proposal pin.
 *
 * WHY THIS IS A GL LAYER AND NOT TEXT ON THE PIN. A label bolted onto the
 * divIcon would inherit the pin's zoom scaling and fan-out for free, and would
 * also overlap every neighbour, every place label and every street name,
 * because nothing in Leaflet is watching. MapLibre already runs a global
 * collision index across all symbol layers, so putting the text here means the
 * decluttering is not ours to write: crowded labels simply do not place.
 *
 * WHY IT GOES ON TOP. Placement runs from the TOPMOST symbol layer down, so the
 * last layer added is the first one placed and wins every contest it enters.
 * Added after `place-labels`, proposals therefore claim their space first and
 * the POI names move aside — including around the pucks, so a business name can
 * no longer land underneath a proposal pin, which it could before this layer
 * existed.
 *
 * WHY IT IS THE VIVID ONE. Place labels are pulled 58% toward grey (POI_MUTE)
 * to leave the proposals room to be the loudest thing on the map. Until now
 * that room was spent entirely on pin shapes and glow rings, which can say that
 * something is wanted somewhere but never what. These labels spend it on the
 * words instead — full-chroma accent, the same color family the heat wears.
 */
function addProposalLabels(map: maplibregl.Map, mapStyle: MapStyle): void {
  if (map.getSource(PROPOSAL_SOURCE)) return;

  registerProposalPuck(map);
  map.addSource(PROPOSAL_SOURCE, {
    type: "geojson",
    data: EMPTY_PROPOSALS,
    // Feature ids come from the payload's `key` (edge index / route id), which
    // is a string; promoteId keeps them addressable without GeoJSON numeric ids.
    promoteId: "key",
  });

  const colors = proposalLabelColors(mapStyle);

  map.addLayer({
    id: PROPOSAL_LAYER,
    type: "symbol",
    source: PROPOSAL_SOURCE,
    // Below the earliest reveal zoom there is nothing this layer could draw, so
    // skip placing it at all rather than filtering the results away. `minz` is
    // a LEAFLET zoom; MapLibre's camera runs one lower.
    minzoom: PROPOSAL_MIN_LEAFLET_ZOOM - 1,
    filter: PROPOSAL_LABEL_FILTER,
    layout: {
      // The invisible pin footprint. `icon-allow-overlap` keeps it from ever
      // being suppressed — the Leaflet pin is drawn whatever MapLibre decides,
      // so its footprint must always be reserved — while leaving
      // `icon-ignore-placement` at false so everything else still avoids it.
      "icon-image": PROPOSAL_PUCK_IMAGE,
      "icon-size": PROPOSAL_PUCK_SIZE,
      "icon-anchor": "bottom",
      "icon-offset": PROPOSAL_PUCK_OFFSET,
      "icon-allow-overlap": true,
      // Text hangs BELOW the tail tip, the one side of the anchor the pin does
      // not occupy. Deliberately a fixed anchor rather than
      // `text-variable-anchor`: letting labels hop around their pins to dodge
      // collisions buys a few more placements and costs the map its composure,
      // since every pan re-picks the side. A label that is always directly
      // under its pin is also unambiguously ITS label.
      "text-anchor": "top",
      // Clearance below the pin's tail. Derived from the pin geometry and
      // guarded across the zoom range — see PROPOSAL_TEXT_OFFSET_EM, which also
      // explains why this cannot be solved with a z-index.
      "text-offset": PROPOSAL_TEXT_OFFSET_EM,
      // Without this, a label that cannot place takes the puck down with it and
      // the pin loses its reserved footprint. With it, a crowded pin keeps its
      // space and simply goes quiet — the same graceful degradation the place
      // labels use, and the reason a dense downtown thins to pins rather than
      // turning into a wall of type.
      "text-optional": true,
      "text-field": proposalTextField(colors.count),
      "text-font": [PROPOSAL_LABEL_FONT],
      "text-size": PROPOSAL_LABEL_TEXT_SIZE,
      "text-max-width": PROPOSAL_TEXT_MAX_WIDTH,
      "text-line-height": 1.15,
      // Caps need air between the letters to stay readable at this size; this
      // is also the second half of the tier signal (place labels sit at 0.01).
      "text-letter-spacing": 0.05,
      "text-justify": "center",
      // Tighter than the place labels' 6. Started at 8, on the reasoning that
      // two labels close enough to read as one paragraph is worse than dropping
      // one — but padding is charged on EVERY side of a three-line block, so 8
      // was silencing neighbours that had real room between them. 4 still keeps
      // a visible gutter and buys back the placements.
      "text-padding": 4,
      // Lower sorts first and wins placement, and rank 0 is the strongest
      // proposal — so when two labels want the same space, the one with more
      // votes behind it takes it. Ties never happen: rank is a dense sequence.
      "symbol-sort-key": ["get", "rank"],
    },
    paint: {
      "text-color": colors.label,
      // Heavier than the place labels' 1.2. This text has to survive both a
      // saturated heat block underneath it and CARTO's baked-in street names,
      // which are pixels in a raster and cannot be asked to move.
      "text-halo-color": colors.halo,
      // Heavier and HARDER than the place labels' 1.2/0.3. Measured on the NYC
      // bike map, where the block heat under a label is a dense field of
      // saturated green and blue: at 1.2 the type dissolved into it, and a
      // blurred halo only smeared the boundary. A tight 2.0 cuts a clean gap
      // without the halo starting to read as a bold weight — which matters
      // because there is no bold cut of the SDF font to be confused with.
      "text-halo-width": 2.0,
      "text-halo-blur": 0.2,
    },
  });
  dlog("maplibre", "proposal labels added");
}

function buildStyle(
  _graphTilesUrl: string,
  blockTilesUrl: string,
  blockTiles: BlockTilesConfig | null,
  tiles: string[],
  labelTiles: string[],
  mapStyle: MapStyle,
): maplibregl.StyleSpecification {
  // z/x/y endpoint when the city advertises one: discrete tile URLs are
  // browser/nginx/CDN-cacheable, while the pmtiles:// protocol's range
  // requests (206s) never are — warm visits re-downloaded ~1MB. Tile bytes
  // are identical either way, so feature ids and all feature-state heat/
  // selection logic are untouched. MapLibre wants absolute tile URLs; build
  // them by string concat — new URL() percent-encodes the {z} braces.
  const blocksSource: maplibregl.VectorSourceSpecification = blockTiles
    ? {
      type: "vector",
      tiles: [
        blockTiles.template.startsWith("http")
          ? blockTiles.template
          : `${window.location.origin}${blockTiles.template}`,
      ],
      minzoom: blockTiles.minzoom,
      maxzoom: blockTiles.maxzoom,
      ...(blockTiles.bounds ? { bounds: blockTiles.bounds } : {}),
    }
    : { type: "vector", url: `pmtiles://${blockTilesUrl}` };

  return {
    version: 8,
    name: "desire-path",
    sources: {
      // CartoDB raster tiles as base map (same as the Leaflet TileLayer)
      "carto-base": {
        type: "raster",
        tiles,
        tileSize: 256,
        // CartoDB serves tiles up to z19; capping the SOURCE makes MapLibre
        // overzoom (stretch) z19 tiles beyond that, like Leaflet's
        // maxNativeZoom. A maxzoom on the LAYER would instead hide the base
        // map entirely once the camera passes it.
        maxzoom: 19,
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
      },
      // The text half of the same CARTO cartography, as transparent PNGs. Same
      // tile scheme and maxzoom as carto-base, so the two are pixel-locked and
      // scale together through Leaflet's zoom-animation ride — the labels can
      // never drift off the streets they name.
      "carto-labels": {
        type: "raster",
        tiles: labelTiles,
        tileSize: 256,
        maxzoom: 19,
      },
      // Block polygons from PMTiles (one per street segment / merged foot path).
      // block_id is the NATIVE feature id (tippecanoe --use-attribute-for-id
      // moves the attribute onto the id and out of properties) — do NOT set
      // promoteId: it would look up the now-absent property and override every
      // id with undefined, silently detaching all feature-state (heat,
      // selection).
      blocks: blocksSource,
    },
    layers: [
      // Background fill (shows through before tiles load) — matches the map style.
      {
        id: "background",
        type: "background",
        paint: { "background-color": mapStyle.base },
      },
      // Base map raster tiles
      {
        id: "carto-tiles",
        type: "raster",
        source: "carto-base",
      },
      // Block heat — the primary vote display, colored from feature-state:
      // a translucent fill plus a thin bright outline (brightness without bulk).
      {
        id: "block-heat",
        type: "fill",
        source: "blocks",
        "source-layer": "blocks",
        paint: blockFillPaint(mapStyle),
      },
      {
        id: "block-heat-line",
        type: "line",
        source: "blocks",
        "source-layer": "blocks",
        paint: blockLinePaint(mapStyle),
      },
      // Arrival spark — transient, above the heat it announces and below the
      // selection UI (a landing vote must never outshout what the user is
      // pointing at). Idle cost is nil: feature-state "arrive" defaults to 0,
      // so every block renders at zero opacity until one is actually animating.
      {
        id: "block-arrival",
        type: "line",
        source: "blocks",
        "source-layer": "blocks",
        paint: blockArrivalPaint(mapStyle),
      },
      // Block selection — feature-state { selected } lights the block polygons
      // covering the current selection/hover: a faint translucent fill plus a
      // hairline casing in the style's selection token (white on dark,
      // near-black on light). Deliberately SUBTLER than the route trace
      // (Leaflet's thick selection polyline drawn above): the route line is
      // the primary "what you selected" signal, the block wash is secondary
      // "what it covers" context — a clear visual hierarchy instead of two
      // competing white treatments.
      {
        id: "block-select",
        type: "fill",
        source: "blocks",
        "source-layer": "blocks",
        paint: {
          "fill-color": mapStyle.selection,
          "fill-opacity": [
            "case", ["boolean", ["feature-state", "selected"], false], 0.11, 0,
          ],
        },
      },
      {
        id: "block-select-casing",
        type: "line",
        source: "blocks",
        "source-layer": "blocks",
        paint: {
          "line-color": mapStyle.selection,
          "line-width": 1.5,
          "line-opacity": [
            "case", ["boolean", ["feature-state", "selected"], false], 0.6, 0,
          ],
        },
      },
      // Place names — LAST, so they sit above the heat and the selection wash.
      // Underneath them a name would be swallowed by a hot block's fill; on top
      // it stays readable at every heat level, which is the whole point of
      // splitting CARTO's basemap into nolabels + only_labels. Held below full
      // opacity so orientation text supports the heat rather than competing
      // with it.
      {
        id: "carto-labels",
        type: "raster",
        source: "carto-labels",
        paint: { "raster-opacity": mapStyle.labelOpacity },
      },
    ],
    // Self-hosted SDF glyphs (client-react/public/fonts, baked by
    // `npm run build:glyphs`). This used to point at demotiles.maplibre.org —
    // fine while nothing drew text, but the place labels below make it a
    // load-bearing dependency on MapLibre's DEMO server.
    glyphs: "/fonts/{fontstack}/{range}.pbf",
  };
}

interface MapLibreBackgroundProps {
  /** Leaflet map instance to sync camera from */
  leafletMap: L.Map | null;
  /** Active map style — drives basemap tiles + background color */
  mapStyle: MapStyle;
  /** Called once the base map has first loaded (or immediately if WebGL is
   *  unavailable and the raster fallback takes over). `active` is true only in
   *  the WebGL case — the caller uses it to drop the opaque Leaflet raster
   *  fallback, WITHOUT which every MapLibre layer (block heat, block selection)
   *  is painted over and invisible. Idempotent upstream. */
  onReady?: (active: boolean) => void;
}

export function MapLibreBackground({ leafletMap, mapStyle, onReady }: MapLibreBackgroundProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  // Owned by the heat effect below; read by the camera effect, which has to be
  // able to call off the animation when a Leaflet zoom takes over the container.
  const arrivalRef = useRef<ArrivalAnimator | null>(null);
  // True for the length of a Leaflet zoom ride. Set by the camera effect, read
  // by the animator — a vote that lands mid-ride must paint instantly, the same
  // as one already in flight when the ride started.
  const zoomRidingRef = useRef(false);

  // Initialize MapLibre map (gracefully handles WebGL unavailability)
  useEffect(() => {
    if (!containerRef.current) return;

    try {
      const map = new maplibregl.Map({
        container: containerRef.current,
        style: buildStyle(
          CONFIG.graphTilesUrl,
          CONFIG.blockTilesUrl,
          CONFIG.blockTiles,
          maplibreRasterTiles(mapStyle),
          maplibreLabelTiles(mapStyle),
          mapStyle,
        ),
        center: [CONFIG.initialView.lon, CONFIG.initialView.lat],
        zoom: CONFIG.initialView.zoom - LEAFLET_TO_MAPLIBRE_ZOOM,
        interactive: false,
        attributionControl: false,
        // Red Hat Mono has no CJK coverage, and baking those ranges as SDF
        // glyphs would be megabytes. This draws CJK/kana/hangul from a system
        // font at runtime instead — without it every Chinatown shop name would
        // render as nothing at all.
        localIdeographFontFamily: "sans-serif",
      });

      map.on("error", (e) => {
        dwarn("maplibre", "error (non-fatal):", e.error?.message ?? e);
      });

      // Base map has rendered for the first time — let the loader dismiss and
      // the Leaflet raster fallback unmount (MapLibre is the base map now).
      map.on("load", () => {
        dlog("maplibre", "load — MapLibre is the base map (raster fallback unmounts)");
        debugState("maplibreLoaded", true);
        onReady?.(true);
        rebindBlockTiles(map);
        // Order matters and is the whole decluttering scheme: placement runs
        // from the topmost symbol layer down, so proposals must be added AFTER
        // the place labels to be placed BEFORE them and win the contested
        // space. Awaited (addPlaceLabels rasterises its icons first) rather
        // than fired in parallel, which would race the two addLayer calls.
        void addPlaceLabels(map, mapStyle).then(() => {
          if (mapRef.current === map) addProposalLabels(map, mapStyle);
        });
      });
      debugState("maplibreLoaded", false);

      mapRef.current = map;
      if (import.meta.env.DEV) {
        (window as unknown as Record<string, unknown>).__ml = map;
      }

      // Point→block resolver for GraphLayer's hover constraint. Fill layers
      // are queryable regardless of their (heat-driven, possibly 0) opacity;
      // guard on the layer existing so a not-yet-loaded style reads as null.
      // Where polygons overlap, the hottest block wins the point (see
      // hottestBlockId) — render order made hot blocks under a cool overlap
      // unhoverable and unselectable.
      blockAtResolver = (lat: number, lng: number) => {
        const ml = mapRef.current;
        if (!ml || !ml.getLayer("block-heat")) return null;
        const feats = ml.queryRenderedFeatures(ml.project([lng, lat]), {
          layers: ["block-heat"],
        });
        return hottestBlockId(feats);
      };

      return () => {
        map.remove();
        mapRef.current = null;
        blockAtResolver = null;
      };
    } catch (err) {
      dwarn("maplibre", "GL JS unavailable (WebGL required) — raster fallback stays:", err);
      debugState("maplibreLoaded", "webgl-unavailable");
      // Leaflet TileLayer provides the fallback base map — it's already
      // mounting, so consider the base map ready (and keep the raster).
      onReady?.(false);
    }
    // mapStyle is resolved once at bootstrap and stable for the session.
    // onReady is a stable callback (useCallback) so it won't re-init the map.
  }, [mapStyle, onReady]);

  // Keep the GL canvas the size of its container.
  //
  // MapLibre sizes its own canvas from an internal ResizeObserver, and that
  // observer has two properties that combine badly: deliveries are frame-driven
  // (a backgrounded or occluded tab produces no frames, so nothing arrives),
  // and it deliberately discards the FIRST entry it is ever handed. So a size
  // change that lands while the tab isn't rendering — a window resize, a Cmd-+
  // page zoom, an iOS URL bar collapsing — can be dropped outright, and then
  // the canvas keeps its old size for the whole life of the page. The container
  // still tracks the viewport, so `--paper` shows through as a strip along the
  // bottom and the right of the map. Leaflet listens to `window.resize`
  // directly and stays correct, which is exactly why only the GL layers came up
  // short: the strip is the width of the base map's own gap.
  //
  // Observed on prod: a 1800x945 container holding an 1636x859 canvas — the
  // viewport as it was at 110% zoom, kept after the zoom went back to 100%.
  //
  // So measure rather than trust. On every signal that the viewport may have
  // moved, compare the canvas against its container and resize only when they
  // actually disagree — a no-op in the overwhelming majority of calls.
  // `visibilitychange` is the one that has to be here: it is the first moment a
  // tab that missed a resize while hidden is rendering again.
  useEffect(() => {
    let pending: ReturnType<typeof setTimeout> | null = null;

    const syncSize = () => {
      pending = null;
      const ml = mapRef.current;
      const el = containerRef.current;
      if (!ml || !el) return;
      // A zoom ride hands the container to a CSS transform (handleZoomAnim):
      // it is being scaled, not resized, and endZoomRide reconciles after it.
      if (el.style.transform) return;
      const canvas = ml.getCanvas();
      const cssWidth = parseFloat(canvas.style.width) || 0;
      const cssHeight = parseFloat(canvas.style.height) || 0;
      // Sub-pixel container sizes are normal (fractional viewports), so only a
      // whole pixel of disagreement counts as a gap worth repainting for.
      if (Math.abs(cssWidth - el.clientWidth) <= 1
        && Math.abs(cssHeight - el.clientHeight) <= 1) return;
      dlog(
        "maplibre",
        `canvas ${cssWidth}x${cssHeight} in a ${el.clientWidth}x${el.clientHeight}`
        + " container — resizing",
      );
      ml.resize();
      // Re-measure Leaflet in the same breath. It tracks `window.resize` on its
      // own, so it is almost always already right — but a container that
      // changed WITHOUT the window changing would otherwise leave the two maps
      // disagreeing about their size, and every Leaflet-drawn layer sliding off
      // the base map is a worse bug than the strip. A no-op when the size is
      // unchanged (Leaflet returns early).
      leafletMap?.invalidateSize({ animate: false, pan: false });
    };

    // Trailing-edge only: a window drag fires resize at frame rate, and each
    // one would otherwise reallocate the GL drawing buffer. Deliberately a
    // timer rather than rAF — the tab that most needs this isn't painting yet.
    const schedule = () => {
      if (pending === null) pending = setTimeout(syncSize, 50);
    };

    const onVisibility = () => {
      if (!document.hidden) schedule();
    };

    const observer = new ResizeObserver(schedule);
    if (containerRef.current) observer.observe(containerRef.current);
    window.addEventListener("resize", schedule);
    window.addEventListener("orientationchange", schedule);
    window.visualViewport?.addEventListener("resize", schedule);
    document.addEventListener("visibilitychange", onVisibility);
    schedule();

    return () => {
      if (pending !== null) clearTimeout(pending);
      observer.disconnect();
      window.removeEventListener("resize", schedule);
      window.removeEventListener("orientationchange", schedule);
      window.visualViewport?.removeEventListener("resize", schedule);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [leafletMap]);

  // Sync camera from Leaflet
  useEffect(() => {
    if (!leafletMap) return;

    const syncCamera = () => {
      const ml = mapRef.current;
      if (!ml) return;
      const center = leafletMap.getCenter();
      const zoom = leafletMap.getZoom() - LEAFLET_TO_MAPLIBRE_ZOOM;
      ml.jumpTo({
        center: [center.lng, center.lat],
        zoom,
        bearing: 0,
        pitch: 0,
      });
    };

    // During Leaflet's ANIMATED zoom (wheel / zoom buttons) no `move` events
    // fire until the 250ms CSS transition ends — Leaflet suppresses them
    // (`_move(…, supressEvent=true)`) — so jumpTo alone leaves the GL base map
    // + block heat frozen mid-zoom while the Leaflet layers glide, then snaps.
    // Ride the animation instead: transition the GL container with the SAME
    // duration/curve Leaflet gives `.leaflet-zoom-animated` layers, mapping
    // every current container point p to `q + p·scale` (its position in the
    // target frame). Pinch-zoom is untouched: it fires per-frame `move` events
    // and stays live through syncCamera.
    const handleZoomAnim = (e: L.ZoomAnimEvent) => {
      const el = containerRef.current;
      const animating = (leafletMap as unknown as { _animatingZoom?: boolean })._animatingZoom;
      if (!el || !mapRef.current || !animating) return;
      const scale = leafletMap.getZoomScale(e.zoom, leafletMap.getZoom());
      // Where the viewport's current top-left corner lands in the target frame
      // (container coords): project() scales linearly with zoom, so the whole
      // current frame maps into the target one by translate(q) scale(scale).
      const nw = leafletMap.containerPointToLatLng([0, 0]);
      const q = leafletMap
        .project(nw, e.zoom)
        .subtract(leafletMap.project(e.center, e.zoom))
        .add(leafletMap.getSize().divideBy(2));
      el.style.transition = "transform 0.25s cubic-bezier(0, 0, 0.25, 1)";
      el.style.transform = `translate3d(${q.x}px, ${q.y}px, 0) scale(${scale})`;
    };

    // Failsafe re-anchor. The happy path at zoom end is: final `move` →
    // syncCamera jumpTo, then `zoomend` clears the ride transform in the same
    // compositor frame as MapLibre's re-render (its rAF runs before paint), so
    // the crisp frame swaps in without a flash. But every link in that chain
    // can drop — a zoomend without a final move, a rAF frozen in an occluded
    // window, an interrupted animation — and then the GL frame sits at a STALE
    // camera, misaligned with every Leaflet-drawn layer until the next
    // interaction. So on zoomend/moveend/visibility-restore: clear any leftover
    // ride transform, re-jump the camera unconditionally, and force one GL
    // repaint. Each step is a cheap no-op when nothing drifted.
    const reconcile = () => {
      const el = containerRef.current;
      const ml = mapRef.current;
      if (!el || !ml) return;
      if ((leafletMap as unknown as { _animatingZoom?: boolean })._animatingZoom) return;
      if (el.style.transform) {
        el.style.transition = "";
        el.style.transform = "";
      }
      syncCamera();
      ml.triggerRepaint();
    };
    const handleVisibility = () => {
      if (!document.hidden) reconcile();
    };

    // A zoom takes the container away from MapLibre: for the length of the ride
    // the GL frame is a frozen bitmap being CSS-scaled, so anything still
    // animating feature-state would be repainting a stale camera underneath a
    // transform — cost without a picture. Land every in-flight arrival on its
    // final heat, and hold the flag that makes votes landing MID-ride paint
    // instantly too. The animation is news about a moment; once the user has
    // moved on to changing the view, that moment is over.
    const startZoomRide = () => {
      zoomRidingRef.current = true;
      arrivalRef.current?.settle();
    };
    const endZoomRide = () => {
      zoomRidingRef.current = false;
      reconcile();
    };

    // Sync on every move frame for smooth panning
    leafletMap.on("move", syncCamera);
    leafletMap.on("zoomanim", handleZoomAnim);
    leafletMap.on("zoomstart", startZoomRide);
    leafletMap.on("zoomend", endZoomRide);
    leafletMap.on("moveend", reconcile);
    document.addEventListener("visibilitychange", handleVisibility);
    // Initial sync
    syncCamera();

    return () => {
      leafletMap.off("move", syncCamera);
      leafletMap.off("zoomanim", handleZoomAnim);
      leafletMap.off("zoomstart", startZoomRide);
      leafletMap.off("zoomend", endZoomRide);
      leafletMap.off("moveend", reconcile);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [leafletMap]);

  // Color the block fills from GraphLayer's block-vote broadcasts via
  // feature-state. Heat is log-normalized (like the old edge heatmap) so a quiet
  // map's 1–2 votes don't saturate. The latest payloads (votes + selection) are
  // retained so they apply even if they arrive before the block source loads.
  useEffect(() => {
    const latest = { current: null as BlockVotesDetail | null };
    // Block ids currently holding feature-state { selected: true }.
    const selected = { current: [] as number[] };
    // What the source actually holds: the sparse id→heat map last written, and
    // the normalization it was computed with. Feature-state lives on the SOURCE
    // (tiles pick it up as they load), so once applied it survives every tile
    // (re)load — this is what lets apply() diff instead of rewriting the world.
    const applied = {
      current: null as
        { heat: Map<number, number>; denomKey: string; norm: HeatNormalization } | null,
    };

    const featureOf = (id: number) => ({ source: "blocks", sourceLayer: "blocks", id });

    // The arrival animation. It only ever touches blocks the diff below hands
    // it, and it writes both of its keys in one setFeatureState per block per
    // frame — the whole gesture costs (animating blocks × frames), not
    // (blocks × tiles). `selected` is untouched: setFeatureState merges keys.
    const arrival = createArrivalAnimator({
      write: (id, heat, arrive) => {
        const ml = mapRef.current;
        if (ml && ml.getSource("blocks")) ml.setFeatureState(featureOf(id), { heat, arrive });
      },
      now: () => performance.now(),
      requestFrame: (cb) => requestAnimationFrame(cb),
      cancelFrame: (h) => cancelAnimationFrame(h),
      suspended: () => zoomRidingRef.current,
    });
    arrivalRef.current = arrival;

    const applySelected = () => {
      const ml = mapRef.current;
      if (!ml || !ml.getSource("blocks")) return;
      for (const id of selected.current) {
        ml.setFeatureState(featureOf(id), { selected: true });
      }
    };

    const apply = () => {
      const ml = mapRef.current;
      const detail = latest.current;
      if (!ml || !ml.getSource("blocks")) return;
      if (!detail) {
        applySelected();
        return;
      }
      const { blockDiff, max, maxNeg } = detail;
      const prev = applied.current;
      // Signed differential → ramp position, one log denominator per arm
      // (docs/algorithms/05-heat-coloring.md).
      //
      // A LEGEND TOGGLE KEEPS THE SCALE IT FOUND. Hiding a vote type doesn't
      // change anyone's votes, only which of them each block is ranked by, so
      // the observed ceilings wobble (hiding the type that held the strongest
      // opposition moved the negative ceiling from 93 to 80 on the NYC bike
      // map) — and a moved ceiling means every lit block's heat moves, i.e. a
      // full rewrite of ~42k feature-states, ~250ms of render, to change the
      // handful of blocks the filter actually touched. Pinning the scale keeps
      // the toggle a diff. It also reads better: the surviving proposals are
      // measured against the same ruler as before, so what you're comparing is
      // their support, not a rescale.
      const { heatOf: signedHeat, denomKey } = detail.source === "filter" && prev
        ? prev.norm
        : makeHeatNormalization(max, maxNeg);
      const norm: HeatNormalization = { heatOf: signedHeat, denomKey };
      // Full rewrite only on the first apply and when a normalization
      // ceiling moved (every lit block's heat changes then — rare). Otherwise
      // write just the blocks whose heat actually changed: a vote touches a
      // handful, and the old always-full rewrite (~47k setFeatureState on the
      // NYC bike map, on EVERY vote and EVERY sourcedata event) was the main
      // "zooming reloads the whole map" cost.
      const full = !prev || prev.denomKey !== denomKey;
      // A vote landing is the one thing worth animating: the block it touched
      // should bloom into its new heat rather than snap. Everything else — the
      // first paint, a cache/network load, a legend toggle, a renormalized full
      // rewrite — paints instantly, because in those the whole map is changing
      // and motion would carry no information. The animator additionally
      // honours prefers-reduced-motion and caps itself under a burst, so this
      // flag is about MEANING; the policy for how much motion lives there.
      const animate = !full && detail.source === "delta";
      // Hand the source back before writing directly to it, or an in-flight
      // tween would overwrite these values on its next frame.
      if (!animate) arrival[full ? "reset" : "settle"]();
      const next = new Map<number, number>();
      const changes: HeatChange[] = [];
      let writes = 0;
      if (full) {
        // Clear prior states, then set only the blocks with a nonzero
        // differential (sparse). The wholesale clear drops `selected` too —
        // re-apply after.
        ml.removeFeatureState({ source: "blocks", sourceLayer: "blocks" });
        for (let id = 0; id < blockDiff.length; id++) {
          const v = blockDiff[id];
          if (v !== 0) {
            const h = signedHeat(v);
            next.set(id, h);
            ml.setFeatureState(featureOf(id), { heat: h });
            writes++;
          }
        }
        applySelected();
      } else {
        for (let id = 0; id < blockDiff.length; id++) {
          const v = blockDiff[id];
          if (v !== 0) {
            const h = signedHeat(v);
            next.set(id, h);
            if (prev.heat.get(id) !== h) {
              if (animate) changes.push({ id, from: prev.heat.get(id) ?? 0, to: h });
              else ml.setFeatureState(featureOf(id), { heat: h });
              writes++;
            }
          }
        }
        // Blocks whose differential just returned to zero: cool back to invisible.
        for (const id of prev.heat.keys()) {
          if (!next.has(id)) {
            if (animate) changes.push({ id, from: prev.heat.get(id)!, to: 0 });
            else ml.setFeatureState(featureOf(id), { heat: 0 });
            writes++;
          }
        }
        // ONE cohort per apply: a vote that lit a whole corridor blooms as a
        // single gesture rather than as N independently-timed twinkles.
        arrival.arrive(changes);
      }
      // Perf harness hook (perf/instrument.js): stamp the first heat apply —
      // feature-state isn't introspectable from outside, so this is the one
      // signal for "heat is now visible". Guarded: zero-cost in normal runs.
      if (!prev) {
        (window as unknown as { __perf?: { mark: (l: string) => void } }).__perf
          ?.mark("first-heat-apply");
      }
      applied.current = { heat: next, denomKey, norm };
      // A diff that wrote nothing changed nothing — the interesting applies are
      // the ones that moved heat, and votes can move it in bursts.
      if (writes > 0 || full) {
        dburst("blocks", "heat",
          `heat apply (${full ? "full" : "diff"}): ${writes} writes, ${next.size} lit`
          + (animate ? `, ${changes.length} arriving (${arrival.size} in flight)` : ""));
      }
      debugState("blockHeatNonzero", next.size);
    };

    const onVotes = (e: Event) => {
      latest.current = (e as CustomEvent<BlockVotesDetail>).detail;
      apply();
    };

    // Selection updates are incremental per-key writes ({ selected: false } on
    // the blocks leaving, true on the ones entering) — never removeFeatureState,
    // which would also drop the heat key set above.
    const onSelect = (e: Event) => {
      const next = (e as CustomEvent<BlockSelectDetail>).detail.blockIds;
      dburst("blocks", "select",
        `select: [${next.slice(0, 12).join(",")}${next.length > 12 ? ",…" : ""}]`);
      const ml = mapRef.current;
      if (ml && ml.getSource("blocks")) {
        const nextSet = new Set(next);
        for (const id of selected.current) {
          if (!nextSet.has(id)) ml.setFeatureState(featureOf(id), { selected: false });
        }
        for (const id of next) {
          ml.setFeatureState(featureOf(id), { selected: true });
        }
      }
      selected.current = next;
    };

    // Votes can arrive before the blocks source exists (apply() bails then).
    // Retry on sourcedata ONLY until the first successful apply: feature-state
    // is stored on the source, not the tiles, so once written it persists
    // through every subsequent tile load — re-applying per sourcedata event
    // (which fires per TILE) multiplied the full rewrite by ~a hundred per
    // zoom, the storm behind the slow bike-map zoom.
    const onSourceData = () => {
      if (!applied.current) apply();
    };

    window.addEventListener(BLOCK_VOTES_EVENT, onVotes);
    window.addEventListener(BLOCK_SELECT_EVENT, onSelect);
    const ml = mapRef.current;
    ml?.on("sourcedata", onSourceData);
    return () => {
      window.removeEventListener(BLOCK_VOTES_EVENT, onVotes);
      window.removeEventListener(BLOCK_SELECT_EVENT, onSelect);
      ml?.off("sourcedata", onSourceData);
      arrival.reset();
      arrivalRef.current = null;
    };
  }, []);

  // Draw GraphLayer's top-proposal labels. The proposal lists are recomputed on
  // a one-minute batch (never per vote), so this runs a handful of times per
  // session and can afford to rebuild the whole FeatureCollection each time.
  useEffect(() => {
    // Proposals routinely settle before the GL style finishes loading, so the
    // last payload is retained and re-applied once the source exists.
    const latest = { current: null as ProposalLabelDetail[] | null };
    // The payload currently in the source. setData on a GeoJSON source fires
    // `sourcedata` for that same source, so without this the retry below would
    // re-apply its own write in an unbroken loop.
    const applied = { current: null as ProposalLabelDetail[] | null };

    const apply = () => {
      const ml = mapRef.current;
      const items = latest.current;
      if (!ml || !items || applied.current === items) return;
      const source = ml.getSource(PROPOSAL_SOURCE) as maplibregl.GeoJSONSource | undefined;
      if (!source) return;
      applied.current = items;
      source.setData({
        type: "FeatureCollection",
        features: items.map((p) => ({
          type: "Feature" as const,
          geometry: { type: "Point" as const, coordinates: [p.lng, p.lat] },
          properties: { key: p.key, text: p.text, count: p.count, rank: p.rank, minz: p.minz },
        })),
      });
      debugState("proposalLabels", items.length);
    };

    const onLabels = (e: Event) => {
      latest.current = (e as CustomEvent<ProposalLabelDetail[]>).detail;
      apply();
    };

    // The layer is added asynchronously (after the place icons rasterise), so a
    // payload arriving first has nowhere to go; this catches it the moment the
    // source appears. `applied` makes it a no-op every other time.
    const onSourceData = (e: maplibregl.MapSourceDataEvent) => {
      if (e.sourceId === PROPOSAL_SOURCE) apply();
    };

    window.addEventListener(PROPOSAL_LABELS_EVENT, onLabels);
    const ml = mapRef.current;
    ml?.on("sourcedata", onSourceData);
    return () => {
      window.removeEventListener(PROPOSAL_LABELS_EVENT, onLabels);
      ml?.off("sourcedata", onSourceData);
    };
  }, []);

  return (
    <div
      ref={containerRef}
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        width: "100%",
        height: "100%",
        zIndex: 0,
        // The zoom-animation ride scales this element about the viewport's
        // top-left corner (see handleZoomAnim).
        transformOrigin: "0 0",
      }}
    />
  );
}
