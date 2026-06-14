# Archived documentation

These documents are kept for historical context but **no longer describe the
running code**. Do not treat them as current. Each entry notes what it was and
what supersedes it.

| Doc | Status | Superseded by |
|-----|--------|---------------|
| [roam.md](roam.md) | **Never built.** A design spec for a "Roam" multi-modal routing engine (`server/tiles.py`, `server/roam_router.py`, `server/roam_cache.py`, `server/refresh.py`, and a `POST /api/routes/roam` endpoint). None of these exist; the implementation checklist is entirely unchecked. | The shipped router is OSRM-per-city with a Python/Dijkstra fallback (`server/osrm_router.py`, `server/python_router.py`), exposed at `POST /api/routes`. |
| [vote-to-heatmap-pipeline.md](vote-to-heatmap-pipeline.md) | **Superseded.** Describes the old segment/node vote model (`cast_desire_path_votes`, the `segment_votes`/`node_votes` Redis hashes, `desire_path_voting.py`, `extract_all_segments`). That whole path was removed. Cited `app.py` line numbers are stale. | [../voting-architecture.md](../voting-architecture.md) — the unified, edge-based packed-codec model and the single `POST /api/vote` directional path. |

If you remove the dead code these docs reference (e.g. `server/desire_path_voting.py`), you can delete the corresponding archive entry too.
</content>
