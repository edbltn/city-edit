# Presentation assets

Slide images for the story in [docs/story.md](../story.md). No basemap, no
captions — plain paper and the app's own drawing vocabulary (kite waypoints,
the desire-path selection stroke, block polygons, the signed heat ramp, colours
straight out of `globals.css` / `colors.ts` / `mapStyles.ts`).

- **`story/city-edit-story.pdf`** — all 8 panels, 16:9, one per page.
- **`story/*.svg`** — the panels (vector, 1600×900); **`story/png/*.png`** at 2× (3200×1800).
- **`story/deck.html`** — the page-per-panel wrapper used to print the PDF.

## 01–04 · The selection, built up

Four frames on the same transform, so they overlay exactly when clicked through:

| | |
|---|---|
| `01-start` | the start waypoint alone |
| `02-end` | the end waypoint — the route between them appears, derived |
| `03-midpoint` | a midpoint inserted; the route re-derives through it (old route dashed) |
| `04-midpoint-removed` | the midpoint deleted; the route snaps back |

Route geometry is real — shortest paths through the NYC walk graph — drawn on
plain paper rather than over a map.

## 05–08 · How the algorithms work

Stage-by-stage schematics, read left→right (06 reads in a Z):

| | |
|---|---|
| `05-algorithm-blocks` | **Block aggregation.** One street is many OSM edges (roadway, both sidewalks, crossing stubs); ten votes land on ten different edges → the block that owns those edges → one place, counted once per device per vote type. |
| `06-algorithm-corridor` | **Corridor growth (route proposals).** The vote field → the heaviest edge is the seed → grow off either tip while support pays for the metres → the finished corridor expressed as ≤ 5 waypoints (2 anchors + 3 ghosts). |
| `07-algorithm-heat` | **Signed heat.** Up-votes over down-votes: net positive rides the warm arm, net negative the cold arm, and a cancelled block carries no heat at all. |
| `08-algorithm-threading` | **Threading a proposal.** Left: the shortest path ignores a nearby proposal. Right: drop a waypoint onto the proposal and the segment leaving it follows the corridor verbatim (old path dashed). |

## Rebuilding

```bash
tools/presentation/render.sh        # panels + 2x PNGs + PDF
```

Scene geometry is cached in `tools/presentation/scene.json` (wide) and
`scene_close.json` (a 240 m close-up including sidewalks and crossings).
Regenerate from the graph — needs `server/osm_data/nyc` and the server venv:

```bash
server/env/bin/python tools/presentation/extract_scene.py --out scene.json
server/env/bin/python tools/presentation/extract_scene.py --out scene_close.json \
  --lat 40.7405 --lon -73.9896 --width 240 --foot
```
