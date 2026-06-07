# Vote-to-Heatmap Pipeline: Complete System Documentation

## Overview

The system has **two parallel paths** for getting votes onto the heatmap:

1. **Optimistic path** (instant, local-only) — the voting client sees heatmap changes immediately via client-side delta application
2. **Broadcast path** (cross-client, ~500ms+ latency) — all other clients see updates via Redis pub/sub -> WebSocket -> HTTP poll

The voting client's own heatmap responds in **<16ms** (a single `requestAnimationFrame`). Other clients respond in **~500ms–1500ms** due to a debounce + full HTTP re-fetch of vote data.

---

## Phase 1: Route Calculation (before any vote)

### Client: User places start + end points

When both points are set, `RouteContext.tsx:787-841` fires the main calculation effect:

```tsx
// client-react/src/context/RouteContext.tsx:819
calculateRoute({ start: startCoords, end: endCoords, waypoints: [] });
```

### Client: `useRouteCalculation.ts` — POST to `/api/routes`

`client-react/src/hooks/useRouteCalculation.ts:81-91`:
```tsx
const response = await fetchWithRetry(`${CONFIG.apiUrl}/routes`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    start: [start.lat, start.lng],
    end: [end.lat, end.lng],
    waypoints: waypoints?.map((wp) => [wp.lat, wp.lng]) || [],
  }),
});
```

### Server: `/api/routes` handler

`server/app.py:692-758`. Calls OSRM for pathfinding:

```python
# server/app.py:734-738
route = route_router.calculate_route(
    start=(start[0], start[1]),
    end=(end[0], end[1]),
    mode="walk",
    waypoints=[]
)
```

### Server: OSRM Router

`server/osrm_router.py:28-82`. Makes an HTTP GET to the self-hosted OSRM instance:

```python
# server/osrm_router.py:45-48
url = (
    f"{self._base_url}/route/v1/{profile}/{coords_str}"
    "?overview=full&geometries=geojson&steps=false"
)
```

Returns a GeoJSON LineString geometry with the full route coordinates.

### Server: Extract vote segments

`server/app.py:747` — before returning, the route is decomposed into consecutive coordinate pairs (segments):

```python
vote_segments = extract_all_segments(route.get("geometry"))
```

`server/desire_path_voting.py:21-42`:
```python
def extract_all_segments(geometry: dict) -> list[list]:
    coords = geometry.get("coordinates", [])
    segments = []
    for i in range(len(coords) - 1):
        segments.append([coords[i], coords[i + 1]])
    return segments
```

### Server: Response

`server/app.py:748-753`:
```python
return jsonify({
    "route": route,
    "desire_path": None,
    "desire_path_segments": vote_segments,
    "vote_mode": "walk"
})
```

The **segments are NOT voted yet** — they're returned to the client for the user to review and confirm.

---

## Phase 2: User Casts a Vote

### Client: `castVote()` in RouteContext

`client-react/src/context/RouteContext.tsx:699-773`. The flow:

**Step 1 — Collect segments to vote on:**
```tsx
// RouteContext.tsx:700-703
const segmentsToVote = splitDesirePaths.length > 0
  ? splitDesirePaths.flatMap(sp => sp.segments)
  : desirePathSegments;
```

**Step 2 — Optimistic update (instant, before network):**
```tsx
// RouteContext.tsx:723-727
window.dispatchEvent(new CustomEvent("optimistic-vote", {
  detail: { segments: segmentsToVote, mode: theme.mode, voteType, voteId },
}));
```

**Step 3 — POST to server:**
```tsx
// RouteContext.tsx:741-746
const response = await fetch(`${CONFIG.apiUrl}/vote`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
```

**Step 4 — On success, confirm optimistic:**
```tsx
// RouteContext.tsx:757-760
window.dispatchEvent(new CustomEvent("optimistic-vote-confirmed", {
  detail: { voteId },
}));
```

**Step 4b — On failure, rollback optimistic:**
```tsx
// RouteContext.tsx:765-768
window.dispatchEvent(new CustomEvent("optimistic-vote-rollback", {
  detail: { voteId },
}));
```

---

## Phase 3: Optimistic Update (Voting Client Only)

GraphLayer listens for the `optimistic-vote` CustomEvent at `client-react/src/components/GraphLayer/GraphLayer.tsx:856-899`.

### 3a. Compute the delta

`GraphLayer.tsx:857-875` — the `handleApply` listener:

```tsx
const delta = computeOptimisticDelta(segments, voteType, voteId, coordToEdge, coordToNode);
```

`computeOptimisticDelta` at `GraphLayer.tsx:499-531` translates voted `[lon,lat]` segments into graph edge/node index increments using precomputed coordinate lookup maps:

```tsx
function computeOptimisticDelta(...): OptimisticDelta | null {
  for (const seg of segments) {
    const key = clientSegmentKey(seg[0], seg[1]);     // "lon,lat|lon,lat" at 5dp
    const edgeIndices = coordToEdge.get(key);         // Map<string, number[]>
    if (edgeIndices) {
      for (const idx of edgeIndices) {
        edgeIncrements.set(idx, (edgeIncrements.get(idx) || 0) + 1);
      }
    }
    // ...same for node increments
  }
}
```

The coord lookup maps (`coordToEdgeIdx`, `coordToNodeIdx`) are built once when the graph topology loads, at `GraphLayer.tsx:463-486`:

```tsx
function buildCoordLookups(topology) {
  // Mirrors the server's _load_graph_cache() in app.py:155-179
  for (let i = 0; i < topology.edges.length; i++) {
    const c1 = `${round5(fromLon)},${round5(fromLat)}`;
    const c2 = `${round5(toLon)},${round5(toLat)}`;
    pushToMapList(coordToEdgeIdx, `${c1}|${c2}`, i);
    if (c1 !== c2) pushToMapList(coordToEdgeIdx, `${c2}|${c1}`, i);
  }
}
```

### 3b. Apply delta and redraw

`GraphLayer.tsx:873-874`:
```tsx
optimisticDeltaRef.current = delta;
refreshGraphDisplayRef.current();
```

`refreshGraphDisplay` at `GraphLayer.tsx:682-713` clones the server vote snapshot and mutates the clone with the delta:

```tsx
const merged: GraphData = {
  ...topology,
  edge_votes: [...sv.edge_votes],
  node_votes: [...sv.node_votes],
  // ...deep-clone vote types too
};
applyOptimisticDelta(merged, delta);
graphDataRef.current = merged;
```

`applyOptimisticDelta` at `GraphLayer.tsx:534-574` adds the increments:
```tsx
for (const [idx, inc] of delta.edgeIncrements) {
  edgeVotes[idx] = (edgeVotes[idx] || 0) + inc;
}
```

Then `scheduleRedrawRef.current()` fires `requestAnimationFrame(redraw)` — the canvas repaints with the new vote data in the next frame.

---

## Phase 4: Server Processes the Vote

### 4a. `/api/vote` handler

`server/app.py:761-848`. The critical path:

**Segment votes to Redis (fast):**
```python
# app.py:818
vote_count = cast_desire_path_votes(redis_client, segments, mode, ip_hash, vote_type=vote_type)
```

**Node votes to Redis (fast):**
```python
# app.py:821-822
node_coords = extract_unique_node_coords(segments)
node_count = cast_node_votes(redis_client, node_coords, mode, ip_hash, vote_type=vote_type)
```

**Bump revision & publish:**
```python
# app.py:826-827
redis_client.incr("revision")
publish_votes_changed()
```

**Background DB persistence (non-blocking):**
```python
# app.py:831-838
def _persist():
    record_segment_votes(segments, mode, ip_hash, vote_type)
    if node_count:
        record_node_votes(node_coords, mode, ip_hash, vote_type)
threading.Thread(target=_persist, daemon=True).start()
```

### 4b. `cast_desire_path_votes` — writing to Redis

`server/desire_path_voting.py:80-122`:

```python
def cast_desire_path_votes(redis_client, segments, mode, ip_hash, vote_type=""):
    pipe = redis_client.pipeline()
    keys_to_update = []
    for segment in segments:
        coord1, coord2 = segment[0], segment[1]
        key = segment_key(coord1, coord2, mode)   # "lon,lat|lon,lat|mode" at 5dp
        pipe.hincrby(SEGMENT_VOTES_KEY, key, 1)   # HINCRBY segment_votes <key> 1
        if vote_type:
            keys_to_update.append(key)
    pipe.execute()

    if vote_type and keys_to_update:
        _update_segment_vote_types(redis_client, keys_to_update, vote_type)
```

**Redis keys used:**

| Key | Type | Format | Purpose |
|-----|------|--------|---------|
| `segment_votes` | Hash | `lon,lat\|lon,lat\|mode` -> count | Raw segment vote counts |
| `segment_vote_types` | Hash | `lon,lat\|lon,lat\|mode` -> JSON `{type: count}` | Per-segment vote type breakdown |
| `node_votes` | Hash | `lon,lat\|mode` -> count | Raw node vote counts |
| `node_vote_types` | Hash | `lon,lat\|mode` -> JSON `{type: count}` | Per-node vote type breakdown |
| `revision` | String | integer | Global revision counter |

The `segment_key()` function at `desire_path_voting.py:68-77` creates **order-independent** keys at 5 decimal places:

```python
def segment_key(coord1, coord2, mode):
    p1 = (round(coord1[0], 5), round(coord1[1], 5))
    p2 = (round(coord2[0], 5), round(coord2[1], 5))
    if p1 < p2:
        return f"{p1[0]},{p1[1]}|{p2[0]},{p2[1]}|{mode}"
    else:
        return f"{p2[0]},{p2[1]}|{p1[0]},{p1[1]}|{mode}"
```

### 4c. `publish_votes_changed` — Redis pub/sub broadcast

`server/app.py:71-80`:

```python
def publish_votes_changed():
    _bump_votes_revision()     # increments local _votes_revision counter
    rev = redis_client.get("revision") or 1
    redis_client.publish(REDIS_CHANNEL, json.dumps({
        "type": "votes_changed",
        "revision": int(rev)
    }))
```

This publishes to the `state_updates` Redis channel. **All Flask instances** subscribe to this channel.

### 4d. Postgres persistence (background thread)

`server/database.py:161-185`:

```python
def record_segment_votes(segments, mode, ip_hash, vote_type=""):
    data = []
    for seg in segments:
        coord1, coord2 = seg
        if coord1 > coord2:
            coord1, coord2 = coord2, coord1
        segment_key = f"{coord1[0]:.6f},{coord1[1]:.6f}|{coord2[0]:.6f},{coord2[1]:.6f}|{mode}"
        data.append((segment_key, mode, ip_hash, 1.0, vote_type or None))

    execute_values(cursor,
        """INSERT INTO votes (...) VALUES %s
           ON CONFLICT (segment_key, ip_hash, vote_type) DO NOTHING""",
        data)
```

Note: Postgres stores at **6dp** while Redis uses **5dp**. The startup migration normalizes this.

---

## Phase 5: Cross-Instance Cache Invalidation

### 5a. Pub/sub listener on each Flask replica

`server/app.py:83-110`:

```python
def start_pubsub_listener():
    def listener():
        pubsub = pubsub_client.pubsub()
        pubsub.subscribe(REDIS_CHANNEL)
        for message in pubsub.listen():
            if message["type"] != "message":
                continue
            data = json.loads(message["data"])
            if data.get("type") == "votes_changed":
                _bump_votes_revision()  # invalidates _graph_votes_cache
```

This runs in a **daemon thread** on every Flask instance. When any instance casts a vote, all instances invalidate their cached `/api/graph-votes` response.

### 5b. Graph-votes response cache

`server/app.py:59-64`:
```python
_votes_revision = 0
_graph_votes_cache: dict = {}
```

The `/api/graph-votes` endpoint at `app.py:1637-1671` uses this cache:

```python
cached = _graph_votes_cache.get(cache_key)
if cached and cached["revision"] == _votes_revision:
    body = cached["body"]         # cache hit — skip Redis scan
else:
    body = _build_graph_votes_body(mode_filter)  # cache miss — scan Redis
```

When `_bump_votes_revision()` fires (from local vote or pub/sub), the revision changes and the next request triggers a full rebuild.

---

## Phase 6: WebSocket Broadcast to All Clients

### 6a. WebSocket handler

Each connected client has its own WebSocket loop at `server/app.py:634-689`:

```python
@sock.route("/ws")
def ws(ws):
    # Each WS connection has its own Redis pub/sub subscription
    pubsub = ws_pubsub_client.pubsub()
    pubsub.subscribe(REDIS_CHANNEL)

    # Send initial state on connect
    state_msg = {"type": "map_state", "state": make_state(rev)}
    ws.send(json.dumps(state_msg))

    while True:
        redis_msg = pubsub.get_message(timeout=0.1)  # poll every 100ms
        if redis_msg and data.get("type") == "votes_changed":
            should_push = True

        if should_push:
            state_msg = {"type": "map_state", "state": make_state(rev)}
            ws.send(json.dumps(state_msg))
```

**Key timing:** The pub/sub poll has a `timeout=0.1` (100ms), so a WebSocket push happens within ~100ms of the Redis publish.

### 6b. `make_state` — what gets pushed

`server/app.py:611-619`:

```python
def make_state(rev, mode_filter=None):
    segment_overlay = get_segment_overlay(mode_filter)
    return {
        "revision": rev,
        "overlays": { "desire_paths": segment_overlay }
    }
```

`get_segment_overlay` at `app.py:553-608` reads `segment_votes` from Redis and builds a GeoJSON FeatureCollection. **This is the legacy overlay path** — the heatmap canvas does NOT use this. The WebSocket message is used **only for its `revision` field**.

### 6c. Client: WebSocketContext receives the message

`client-react/src/context/WebSocketContext.tsx:42-56`:

```tsx
ws.onmessage = (evt) => {
  const msg = JSON.parse(evt.data);
  if (msg.type === "map_state" && msg.state) {
    if (rawState.revision > latestRevisionRef.current) {
      latestRevisionRef.current = rawState.revision;
      setMapState(rawState);     // triggers React re-render
    }
  }
};
```

---

## Phase 7: Heatmap Update on Other Clients

### 7a. GraphLayer detects revision change

`client-react/src/components/GraphLayer/GraphLayer.tsx:1141-1145`:

```tsx
const mapStateRevision = mapState?.revision;
useEffect(() => {
  if (mapStateRevision === undefined) return;
  debouncedFetchVotes();       // 500ms debounce
}, [mapStateRevision, debouncedFetchVotes]);
```

### 7b. Debounced vote fetch

`GraphLayer.tsx:795-798`:

```tsx
const debouncedFetchVotes = useCallback(() => {
  if (voteDebounceRef.current) clearTimeout(voteDebounceRef.current);
  voteDebounceRef.current = setTimeout(() => fetchVotesRef.current(), 500);
}, []);
```

**This 500ms debounce is the biggest source of latency for other clients.** It collapses rapid successive WebSocket notifications into a single fetch.

### 7c. `fetchVotes` — GET `/api/graph-votes`

`GraphLayer.tsx:778-789`:

```tsx
const fetchVotes = useCallback(async () => {
  const url = `${CONFIG.apiUrl}/graph-votes?mode=${encodeURIComponent(themeMode)}`;
  const response = await fetch(url);
  serverVotesRef.current = await response.json();
  refreshGraphDisplayRef.current();     // merge + redraw
}, [themeMode]);
```

### 7d. Server: `/api/graph-votes` response

`server/app.py:1516-1634`. The heavy lifting is in `_build_graph_votes_body`:

```python
def _build_graph_votes_body(mode_filter):
    segment_votes = redis_client.hgetall(SEGMENT_VOTES_KEY)

    # Walk the (small) vote rows, not the (huge) edge list
    edge_votes = [0] * len(edges)
    for seg_key, vote_count in segment_votes.items():
        for edge_idx in coord_to_edge_idx.get((parts[0], parts[1]), ()):
            edge_votes[edge_idx] += v

    # Same for node votes, edge vote types, node vote types...
    return json.dumps({
        "edge_votes": edge_votes,        # [0, 0, 3, 0, 1, ...]  — one per edge
        "node_votes": node_votes,        # [0, 2, 0, 0, ...]     — one per node
        "vote_type_legend": legend,      # ["Add bike lane", ...]
        "edge_vote_types": edge_vote_types,  # [[[0, 3]], [], ...]
        "node_vote_types": node_vote_types,
    })
```

This response uses **index-aligned arrays** matching the topology from `/api/graph-topology`. The topology is fetched once on page load and cached; only the vote arrays are re-fetched.

### 7e. Canvas redraw

`refreshGraphDisplay` at `GraphLayer.tsx:682-713` sets `graphDataRef.current` and calls `scheduleRedraw()`, which fires `requestAnimationFrame(redraw)`.

The `redraw` function at `GraphLayer.tsx:1003-1129` renders the heatmap using multi-pass additive blending:

```tsx
// Phase 1: zero-vote baseline (faint white network)
ctx.globalCompositeOperation = "source-over";
ctx.globalAlpha = 0.05;

// Phase 2: voted edges, sorted by intensity, additive blending
ctx.globalCompositeOperation = "lighter";
for (const i of voted) {
  const norm = Math.log((edgeVotes[i] ?? 0) + 1) / Math.log(maxVotes + 1);
  // Pass 1: deep red halo
  // Pass 2: warm orange heat
  // Pass 3: yellow hot core (norm > 0.2)
  // Pass 4: white-hot peak (norm > 0.7)
}
```

---

## Timing Summary: End-to-End Latencies

| Step | Latency | Component |
|------|---------|-----------|
| User clicks "Vote" | 0ms | Client |
| Optimistic delta applied + canvas redrawn | ~16ms (1 frame) | Client (same browser) |
| POST `/api/vote` reaches Flask | ~50-200ms | Network |
| Redis HINCRBY pipeline executes | ~1ms | Flask -> Redis |
| `redis_client.publish()` fires | ~1ms | Flask -> Redis |
| Pub/sub poll on WebSocket handler | up to 100ms | Flask WS loop |
| WebSocket push to all clients | ~1ms | Flask -> Client |
| Debounce timer in GraphLayer | **500ms** | Other clients |
| GET `/api/graph-votes` round trip | ~50-200ms | Network |
| Canvas redraw | ~16ms | Other clients |
| **Total for voting client** | **~16ms** | Optimistic |
| **Total for other clients** | **~700ms-1000ms** | Broadcast path |

---

## Data Flow Diagram

```
VOTING CLIENT                        SERVER (Flask x3)                    OTHER CLIENTS
===============                      =================                    =============

1. castVote()
   |
   +- dispatch("optimistic-vote")--> GraphLayer.handleApply()
   |                                   computeOptimisticDelta()
   |                                   applyOptimisticDelta()
   |                                   redraw() ---------------> HEATMAP UPDATES (~16ms)
   |
   +- POST /api/vote ---------------> Flask /api/vote handler
   |                                   |
   |                                   +- cast_desire_path_votes()
   |                                   |    HINCRBY segment_votes ...
   |                                   |    HSET segment_vote_types ...
   |                                   |
   |                                   +- cast_node_votes()
   |                                   |    HINCRBY node_votes ...
   |                                   |    HSET node_vote_types ...
   |                                   |
   |                                   +- redis.incr("revision")
   |                                   |
   |                                   +- redis.publish("state_updates",
   |                                   |    {"type": "votes_changed"})
   |                                   |         |
   |                                   |    +----+------------------+
   |                                   |    | Redis Pub/Sub Fan-out |
   |                                   |    +----+------------------+
   |                                   |         |
   |  +-------------------------------------- --+
   |  |  All Flask instances:                    |
   |  |  pubsub_listener thread                  |
   |  |    _bump_votes_revision()                |
   |  |    (invalidates _graph_votes_cache)      |
   |  |                                          |
   |  |  All WS handler loops:                   |
   |  |    pubsub.get_message(timeout=0.1)       |
   |  |    ws.send({"type":"map_state", ...}) ---+--> WebSocket message
   |                                             |        |
   |                                             |    WebSocketContext.onmessage()
   |                                             |      setMapState({revision: N})
   |                                             |        |
   |                                             |    GraphLayer useEffect
   |                                             |      debouncedFetchVotes() (500ms)
   |                                             |        |
   |                                             |    GET /api/graph-votes --> Flask
   |                                             |                             |
   |                                             |    _build_graph_votes_body()
   |                                             |      hgetall(segment_votes)
   |                                             |      hgetall(node_votes)
   |                                             |      hgetall(segment_vote_types)
   |                                             |      hgetall(node_vote_types)
   |                                             |        |
   |                                             |    <-- JSON response -----------+
   |                                             |    refreshGraphDisplay()
   |                                             |    redraw() --> HEATMAP UPDATES
   |                                             |                  (~700-1000ms)
   |
   +- Response 200 OK <-------------- return jsonify({"success": True})
   |
   +- dispatch("optimistic-vote-confirmed")
        GraphLayer marks delta.confirmed = true
        (next fetchVotes clears it without flash)
```

---

## Key Redis Data Structures

```
Redis DB 0
+-- segment_votes (HASH)
|   +-- "-73.99123,40.75001|-73.99087,40.75034|bikepaths" -> "7"
|   +-- "-73.98765,40.74321|-73.98754,40.74333|walkways"  -> "3"
|   +-- ...
|
+-- segment_vote_types (HASH)
|   +-- "-73.99123,40.75001|-73.99087,40.75034|bikepaths" -> '{"Add bike lane":5,"Protected lane":2}'
|   +-- ...
|
+-- node_votes (HASH)
|   +-- "-73.99123,40.75001|bikepaths" -> "12"
|   +-- ...
|
+-- node_vote_types (HASH)
|   +-- "-73.99123,40.75001|bikepaths" -> '{"Add bike lane":8,"Better crossing":4}'
|   +-- ...
|
+-- revision (STRING) -> "4217"
|
+-- Channel: "state_updates"
    +-- Messages: {"type": "votes_changed", "revision": 4217}
```

---

## Bottlenecks for "Instant Sync"

1. **500ms debounce** in `GraphLayer.tsx:796` — the single biggest delay for other clients. Exists to collapse rapid WebSocket notifications into a single fetch.

2. **Full `/api/graph-votes` re-fetch** — every vote change triggers a complete re-read of all 4 Redis hashes (segment_votes, node_votes, segment_vote_types, node_vote_types) and rebuilds the full JSON response. Even with the server-side cache (`_graph_votes_cache`), the client must download and parse the full response (~hundreds of KB).

3. **WebSocket carries `make_state()` GeoJSON** — the WS message includes a full GeoJSON overlay (`get_segment_overlay`) that nobody uses. The client only reads `revision` from it. This is wasted bandwidth and server CPU.

4. **100ms pub/sub poll interval** — the WS handler polls Redis every 100ms (`pubsub.get_message(timeout=0.1)`), adding up to 100ms latency.

5. **No delta-based updates** — other clients can't receive "edge X got +1 vote" deltas. They must re-fetch everything.
