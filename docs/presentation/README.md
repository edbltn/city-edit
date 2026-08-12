# Presentation assets

Slide-ready image assets for the story in [docs/story.md](../story.md). Built from
the app's own vocabulary — kite waypoints, the desire-path selection line, real
block polygons, the signed heat ramp — drawn over a real slice of the NYC walk
graph (Flatiron / Madison Square, from `server/osm_data/nyc`).

- **`story/city-edit-story.pdf`** — the whole deck, 22 pages, 16:9.
- **`story/*.svg`** — one panel per frame (vector, 1600×900).
- **`story/png/*.png`** — the same panels at 1.5× (2400×1350), for slides that want a bitmap.
- **`story/deck.html`** — the page-per-panel wrapper used to print the PDF.

## The four sets

Every frame inside a set is pixel-aligned with the ones before it, so clicking
through a set reads as one thing being built up, layer by layer.

| Set | Frames | The idea (story.md) |
|---|---|---|
| **01 · Selection** | map → start → end → midpoint → removed → shareable | Waypoints: one ordered list of coordinates is the whole interface; everything else is derived |
| **02 · Grain** | one street → ten votes → one block → counted once | Votes are *stored* on edges but *counted* on blocks (17 real OSM edges in the block shown) |
| **03 · Heat** | quiet → support → opposition → contested → cancelled | Heat is a signed argument, not a traffic count — and zero is invisible |
| **04 · Proposal** | support → seed → corridor → ghost waypoints → five waypoints | Corridors grow out of the vote field; any proposal is reproducible as ≤ 5 waypoints |

Plus a title and a closing frame.

## Rebuilding

```bash
tools/presentation/render.sh        # panels + 2x PNGs + PDF
```

The scene geometry is cached in `tools/presentation/scene.json` (wide, ~1.2 km
across) and `scene_close.json` (a 240 m close-up that includes sidewalks and
crossings). Regenerate them from the graph — needs `server/osm_data/nyc` and the
server venv — with:

```bash
server/env/bin/python tools/presentation/extract_scene.py --out scene.json
server/env/bin/python tools/presentation/extract_scene.py --out scene_close.json \
  --lat 40.7405 --lon -73.9896 --width 240 --foot
```

Colours and marker geometry mirror the client (`client-react/src/styles/globals.css`,
`colors.ts`, `mapStyles.ts`) — if the app's palette moves, update `deck.py`'s
palette block to match.
