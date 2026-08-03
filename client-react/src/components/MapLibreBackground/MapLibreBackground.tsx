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
import { dlog, dwarn, debugState } from "../../utils/debugLog";
import {
  maplibreRasterTiles,
  maplibreLabelTiles,
  heatTip,
  POI_COLORS,
  type MapStyle,
} from "../../mapStyles";
import { hottestBlockId } from "./hottestBlock";

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
}
export const BLOCK_VOTES_EVENT = "city-edit:block-votes";

// Block-selection payload broadcast by GraphLayer whenever the selection/hover
// changes: the real block ids covering the selected/hovered edges. Rendered as
// feature-state { selected } on the block-select layers (docs §2.4 highlight).
export interface BlockSelectDetail {
  blockIds: number[];
}
export const BLOCK_SELECT_EVENT = "city-edit:block-select";

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
function addPlaceLabels(map: maplibregl.Map, mapStyle: MapStyle): void {
  const url = CONFIG.placeTilesUrl;
  if (!url) {
    dlog("maplibre", "no place-label tiles for this city — skipping layer");
    return;
  }
  if (map.getSource("places")) return;

  map.addSource("places", { type: "vector", url: `pmtiles://${url}` });

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
      "text-field": ["get", "name"],
      "text-font": [PLACE_LABEL_FONT],
      // Mono is wide, so this runs a little smaller than a sans would.
      "text-size": ["interpolate", ["linear"], ["zoom"], 11, 9.5, 15, 10.5, 18, 12],
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
      "text-halo-width": 1.5,
      "text-halo-blur": 0.3,
    },
  });
  dlog("maplibre", "place labels added", url);
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
        addPlaceLabels(map, mapStyle);
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

    // Sync on every move frame for smooth panning
    leafletMap.on("move", syncCamera);
    leafletMap.on("zoomanim", handleZoomAnim);
    leafletMap.on("zoomend", reconcile);
    leafletMap.on("moveend", reconcile);
    document.addEventListener("visibilitychange", handleVisibility);
    // Initial sync
    syncCamera();

    return () => {
      leafletMap.off("move", syncCamera);
      leafletMap.off("zoomanim", handleZoomAnim);
      leafletMap.off("zoomend", reconcile);
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
    const applied = { current: null as { heat: Map<number, number>; denomKey: string } | null };

    const featureOf = (id: number) => ({ source: "blocks", sourceLayer: "blocks", id });

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
      // Two log denominators, one per arm: positives normalize against the
      // busy-map ceiling, negatives against their own (much smaller) floor so
      // net-against blocks actually reach the deep-cold end of the ramp.
      const denomPos = Math.log(Math.max(1, max) + 1);
      const denomNeg = Math.log(Math.max(1, maxNeg) + 1);
      const denomKey = `${denomPos}|${denomNeg}`;
      const signedHeat = (v: number) =>
        v > 0 ? Math.log(v + 1) / denomPos : -Math.log(1 - v) / denomNeg;
      const prev = applied.current;
      // Full rewrite only on the first apply and when a normalization
      // ceiling moved (every lit block's heat changes then — rare). Otherwise
      // write just the blocks whose heat actually changed: a vote touches a
      // handful, and the old always-full rewrite (~47k setFeatureState on the
      // NYC bike map, on EVERY vote and EVERY sourcedata event) was the main
      // "zooming reloads the whole map" cost.
      const full = !prev || prev.denomKey !== denomKey;
      const next = new Map<number, number>();
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
              ml.setFeatureState(featureOf(id), { heat: h });
              writes++;
            }
          }
        }
        // Blocks whose differential just returned to zero: cool back to invisible.
        for (const id of prev.heat.keys()) {
          if (!next.has(id)) {
            ml.setFeatureState(featureOf(id), { heat: 0 });
            writes++;
          }
        }
      }
      // Perf harness hook (perf/instrument.js): stamp the first heat apply —
      // feature-state isn't introspectable from outside, so this is the one
      // signal for "heat is now visible". Guarded: zero-cost in normal runs.
      if (!prev) {
        (window as unknown as { __perf?: { mark: (l: string) => void } }).__perf
          ?.mark("first-heat-apply");
      }
      applied.current = { heat: next, denomKey };
      dlog("blocks", `heat apply (${full ? "full" : "diff"}): ${writes} writes, ${next.size} lit`);
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
      dlog("blocks", `select: [${next.slice(0, 12).join(",")}${next.length > 12 ? ",…" : ""}]`);
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
