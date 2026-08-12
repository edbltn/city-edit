# Presentation assets

Eight slides for talking through City Edit's front end. Art on the right, a
short scannable note on the left — bold words are the keywords to land on.

- **`story/city-edit-story.pdf`** — the deck, 16:9, one panel per page.
- **`story/*.svg`** — the panels (vector, 1600×900); **`story/png/*.png`** at 2×.
- **`story/deck.html`** — the page-per-panel wrapper used to print the PDF.

Everything drawn is the app's own asset, not an approximation:

| Panel element | Source in the client |
|---|---|
| basemap | CARTO `dark_nolabels` + `dark_only_labels` tiles (`mapStyles.ts`), fetched by `fetch_basemap.py` |
| kite waypoints | `utils/kiteIcon.ts` + `.ascii-marker` CSS; mids are the selection colour (solid white) |
| selection line | `RouteLayer` desire-path stroke |
| block selection wash | `MapLibreBackground` `block-select` layers (fill 0.11 / casing 0.6) |
| block heat | `blockFillPaint` / `blockLinePaint` ramps, evaluated in Python from the same stops |
| top-proposal pin + diamond | `GraphLayer/voteTypeIcon.ts` paths, with a real `/icons/*.svg` glyph and the rank glow ring |

## The slides

| | Art | Talks about |
|---|---|---|
| `01-basemap` | bare CARTO dark map | **Leaflet** as the camera; the `nolabels` / `only_labels` split |
| `02-waypoint-start` | one start kite | the `?w=` params and snapping lat/lng onto the walk graph |
| `03-waypoint-route` | start + end + route | **OSRM** foot profile with Multilevel Dijkstra |
| `04-waypoint-mid` | mid pulled out of the line | drag-to-insert, tap-to-delete, start/end derived from the list |
| `05-graph-to-blocks` | an X intersection: graph ghosted under 4 street blocks + 1 junction block | the clustering rules |
| `06-block-heat` | the same blocks, lit | signed differential, log normalization, zero is invisible |
| `07-top-proposal-point` | the square pin on the junction | PBTP: per-block winner, floor, spacing |
| `08-top-proposal-route` | a heated corridor with its diamond + 5 waypoints | RBTP: peeling, budget, ghost waypoints |

Copy is plain mono at one size per slide — bold for keywords, the start/end
colours for the words "start" and "end". `build.py` fails the build if a line
overruns the text column, since an overlong line silently collides with the art.

## Rebuilding

```bash
tools/presentation/render.sh        # build → validate → 2x PNGs → PDF
```

`render.sh` parses every panel before rendering: a raw `&` or `<` in slide copy
produces an SVG the browser silently refuses to draw (blank PDF page).

The basemap tile mosaic is cached as `tools/presentation/basemap.png`
(`fetch_basemap.py` re-fetches it). Scene geometry is cached in `scene.json` /
`scene_close.json`; regenerate from the graph with the server venv:

```bash
server/env/bin/python tools/presentation/extract_scene.py --out scene.json
server/env/bin/python tools/presentation/extract_scene.py --out scene_close.json \
  --lat 40.7405 --lon -73.9896 --width 240 --foot
```
