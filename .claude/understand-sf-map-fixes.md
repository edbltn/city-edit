# Understand — SF map fixes session

Tracking deep understanding. Items checked off only once *verified* (restated +
quizzed + reasoned), not merely explained.

## Stage 1 — The off-center bug (MapView initial view)
- [ ] Problem: why did SF & Chicago open off-center but NYC was fine?
- [ ] Why: module-load vs render timing; `getInitialMapView()` ran before `applyCityConfig`
- [ ] Why off-center (not "showing NYC"): interaction with `maxBounds` clamping
- [ ] Solution: move the call inside the component, memoized; why `useMemo([], …)` is correct
- [ ] Edge cases: within-city URL-param nav still works; why only non-default cities hit it
- [ ] Context: impacts every non-default city, not just SF/Chicago

## Stage 2 — High-res graph (simplify=False)
- [ ] Problem: "choppy" lines not matching streets — what produced them?
- [ ] Why: osmnx `simplify=True` collapses degree-2 chains → straight chords
- [ ] Solution: `simplify=False` keeps shape-points as nodes
- [ ] Tradeoffs: node/edge count, ~29MB topology payload, gzip
- [ ] Context: feeds both the rendered graph (/graph-topology) and snapping (/graph)

## Stage 3 — /api/graph 500 (tuple keys)
- [ ] Problem: endpoint 500'd whenever a viewport had edges
- [ ] Why: `node_pair_to_edge` has int-tuple keys → not JSON-serializable
- [ ] Solution: return only `nodes`/`edges`; why the lookup maps are server-side only
- [ ] Context: silently broke node snapping (caught & swallowed client-side)

## Stage 4 — SF bbox sizing journey
- [ ] bbox convention (S, W, N, E) and how it propagates to the client
- [ ] Why center must equal the bbox midpoint
- [ ] The dashed line: it's the basemap's SF county boundary, not our code
- [ ] `truncate_by_edge` → graph spills past the box (Treasure Island); final sizing
- [ ] Why we did NOT rebuild the graph after the final resize

## Stage 5 — Mode-switch location behavior
- [ ] Within-city: preserve view via URL params; cross-city: land at city center
- [ ] How a full-page `<a href>` nav + bootstrap makes this work
