# Debugging — named tabs, debug channels, and where every log lives

One consistent way to see what the app is doing, designed so a human and
Claude can debug the *same* tab together. Three pieces: **named debug tabs**
(you open them, Claude can find them), **client debug channels** (namespaced
`[channel]` console lines), and **tagged server logs**.

## The workflow (TL;DR)

1. Open the app with a `tab` param and give it any name:

   ```
   http://localhost:3000/m/nyc-walkways?tab=eric
   ```

   That one param does three things: appends **`[dbg:eric]`** to the tab title
   (so you can find it in the tab strip and Claude can find it in the tab
   list), enables **all client debug channels** in that tab, and exposes the
   `cityedit` console helpers. The name sticks across reloads and in-app
   navigation (sessionStorage), so it survives the app rewriting the URL.

2. Reproduce whatever you're testing in that tab.

3. Tell Claude: *"check tab eric"*. Claude will locate the tab by its
   `[dbg:eric]` title, read the `[channel]`-prefixed console lines, run
   `cityedit.dumpState()`, and take screenshots — of the exact tab you used,
   with the exact state you produced.

Anything that behaves oddly: **don't close the tab** — the console history is
the evidence.

## Client debug channels

Every debug line goes through `dlog(channel, …)` (`src/utils/debugLog.ts`) and
prints as `[channel] message`, so filtering is reliable in devtools
(filter box: `[cast]`) and in automated console reads.

| Channel | What it logs |
|---|---|
| `topo` | topology fetch/decode (GTB blob version, IndexedDB cache hit/miss, edge/block counts), corrupt-cache refetches |
| `votes` | `/api/graph-votes` loads (rev, legend), WS deltas applied, revision gaps → full refetch |
| `cast` | **every press**: the planBlockVote decision (`cast`/`clear` counts, or `UNVOTE-ALL`) + the server's response (`changed/cleared/capped/evicted`) |
| `store` | local my-votes store: load-time server reset (how many stale entries dropped) |
| `blocks` | block heat broadcasts (how many blocks lit) + block-selection sets |
| `proposals` | route-proposal recomputes: corridor count, scores, timing |
| `maplibre` | lifecycle: `load` fired (raster fallback unmounts), WebGL-unavailable fallback, source errors |
| `ws` | websocket connect / disconnect / bad messages |

Enabling without a named tab:

```js
cityedit.debug.enable("cast,blocks")   // specific channels
cityedit.debug.enable("*")             // everything (persisted in localStorage)
cityedit.debug.disable()
```

or set `localStorage.cityedit_debug = "*"` and reload.

### `cityedit.dumpState()` — the one-call health check

Returns the facts subsystems have registered: topology loaded (edges/blocks,
cache vs network), `maplibreLoaded` (true / false / `"webgl-unavailable"`),
current vote revision, lit-block count, route-proposal count, tab name, active
channels. First thing to run when "nothing shows up":

```js
cityedit.dumpState()
// { tab: "eric", channels: [...], topology: {nEdges: 3299152, nBlocks: 147349,
//   cached: true}, maplibreLoaded: true, votesRev: 12, blockHeatNonzero: 4, … }
```

Dev builds also expose raw map handles: `__lmap` (Leaflet) and `__ml`
(MapLibre) — e.g. `__lmap.setView([40.73,-73.98], 16)`,
`__ml.getFeatureState({source:"blocks", sourceLayer:"blocks", id: 4059})`.

## Diagnosis cheat-sheet

| Symptom | Check |
|---|---|
| No heat anywhere | `dumpState()`: `maplibreLoaded` must be `true` (if `false`, see `[maplibre]` lines — CORS/pmtiles or WebGL); `blockHeatNonzero` > 0? Then `__ml.getFeatureState(...)` on a known block id |
| A press did something weird | `[cast]` lines show exactly what the press was planned as (UNVOTE-ALL vs cast) and what the server did — compare the two |
| Votes look stale/wrong for "me" | `[store]` reset line on load (how many local entries dropped); `GET /api/my-votes?map=…&voter_id=…` is the server truth |
| No route diamonds | `[proposals]` recompute lines — 0 corridors means the vote state doesn't clear the gates (score ≥ 3, ≥ 2 connected edges, net ≥ 1/edge) |
| Counts drift after votes | `[votes]` delta lines (blockCounts present?) and gap-refetch warnings |

## Server logs

Host Flask logs to stdout — in local dev that's the terminal you launched it
from (the CLAUDE.md nohup recipe writes to the scratchpad `flask.log`).
`LOG_LEVEL=DEBUG ./env/bin/python app.py` for the chatty lines. Every line is
tagged; grep by tag:

| Tag | Subsystem |
|---|---|
| `[VOTE]` / `[VOTE:slug]` | `/api/vote` writes: direction, changed/cleared counts, touched blocks, caps/evictions |
| `[GRAPH]` | city graph loads, edge-block mapping load/skip (topology_etag mismatch) |
| `[DB]` | Postgres schema/init/persistence errors |
| `[HYDRATE]` / `[POPULATE]` | Redis replay from Postgres (cold start / lazy) |
| `[BLOCKVOTES]` | block dedup state rebuilds + invariant warnings |
| `[RESNAP:slug]` / `[REPAIR:…]` | vote migration after graph rebuilds |
| `[PUBSUB]` / `[WS]` | delta broadcast + graph_reload listeners |
| `[OSRM]` / `[ROUTE]` | routing engine |
| `[STARTUP]` / `[REDIS]` / `[ADMIN]` | boot, cache, admin endpoints |

Useful curl probes (server truth, bypassing the client):

```bash
# vote arrays incl. block fields (rev, n_blocks, blocks_version)
curl -s 'localhost:5001/api/graph-votes?map=nyc-walkways&mode=walkways' | python3 -m json.tool | head -30
# one device's full vote set for a map (what the client resets its store from)
curl -s 'localhost:5001/api/my-votes?map=nyc-walkways&voter_id=<id>'
# GTB2 topology header: magic, nNodes, nEdges, nBlocks
curl -s 'localhost:5001/api/graph-topology?map=nyc-walkways&format=bin' -r 0-15 -o - | xxd
```

Postgres (canonical votes) and Redis (serving counts) direct access:

```bash
docker exec city-edit-postgres-1 sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT …"'
redis-cli hgetall ev:nyc-walkways | head        # packed edge counts
redis-cli hgetall bagg:nyc-walkways:2 | head    # deduped block counts (mode 2)
```

## Conventions when adding logs

- Client: `dlog("channel", …)` for debug detail, `dwarn`/`derror` for always-on
  problems — never bare `console.log`. Pick an existing channel before minting
  a new one; new channels go into `DebugChannel` + the table above.
- Register load-bearing facts with `debugState(key, value)` so `dumpState()`
  stays the one-call health check.
- Server: every line starts with a `[TAG]`; reuse the table above.
- Logs state *facts* (counts, ids, decisions taken), not narration; one line
  per event.
