# Archived documentation

These documents are kept for historical context but **no longer describe the
running code**. Do not treat them as current. Each entry notes what it was and
what supersedes it.

| Doc | Status | Superseded by |
|-----|--------|---------------|
| [roam.md](roam.md) | **Never built.** A design spec for a "Roam" multi-modal routing engine (`server/tiles.py`, `server/roam_router.py`, `server/roam_cache.py`, `server/refresh.py`, and a `POST /api/routes/roam` endpoint). None of these exist; the implementation checklist is entirely unchecked. | The shipped router is OSRM-per-city with a Python/Dijkstra fallback (`server/osrm_router.py`, `server/python_router.py`), exposed at `POST /api/routes`. |
| [vote-to-heatmap-pipeline.md](vote-to-heatmap-pipeline.md) | **Superseded.** Describes the old segment/node vote model (`cast_desire_path_votes`, the `segment_votes`/`node_votes` Redis hashes, `desire_path_voting.py`, `extract_all_segments`). That whole path was removed. Cited `app.py` line numbers are stale. | [../voting-architecture.md](../voting-architecture.md) — the unified, edge-based packed-codec model and the single `POST /api/vote` directional path. |
| [vote-system-design.md](vote-system-design.md) | **Superseded.** The 2026-06-18 block-layer design whose central decision — fan a vote out across every edge of its block at write time (§2.1 "propagate + dedup"), plus a backfill script (§2.8) — was reversed: votes stay on the selection edges only. Its `bd:`/`bagg:` Redis dedup structures (§2.3–2.7) *did* ship and remain accurate. | [../three-layer-model.md](../three-layer-model.md) — blocks as the aggregation/display/interaction grain with block-scoped clear-then-cast semantics. |
| [cluster-model.md](cluster-model.md) | **Superseded.** The "everything is a Cluster" abstraction built on the propagate model above — voting a cluster cast on `blockEdgeIds` (every edge of every block). Its UX-parity ideas (route proposals hover/select like points, block highlight, verbatim ghost-waypoint leg) carried forward. | [../three-layer-model.md](../three-layer-model.md) §3–4 — casts write to selection edges only; blocks define flip/unvote behavior. |
| [unify-voting-waves.md](unify-voting-waves.md) | **Superseded.** Agent delegation prompts for the two docs above; the wave plan (W2-A propagation, W2-B backfill) implements the reversed decision, so the prompts must not be reused. | [../three-layer-model.md](../three-layer-model.md). |

If you remove the dead code these docs reference (e.g. `server/desire_path_voting.py`), you can delete the corresponding archive entry too.
</content>
