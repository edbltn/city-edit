"""
Vote migration — keep votes aligned to the current graph after a rebuild.

Votes are keyed by edge id, but edge ids are only valid for one build of a city's
graph. Each vote also stores its lat/lon (the edge midpoint it was cast on) as a
stable anchor. After a graph rebuild the edge ids shift, so this module re-snaps
every vote's anchor to the nearest edge on the *current* graph — an idempotent
lat/lon → current-edge-id resolution. It also rebuilds the map's Redis aggregate
from the corrected rows and (best-effort) repairs orphaned vote-type ids so the
legend resolves to real labels instead of "#<id>".

Run it for all maps via `python migrate_votes.py` (CLI), or let app.py run it
automatically when it sees a graph_reload signal. Dedup/idempotency guarantees
live in database.resnap_edge_votes.
"""
import logging

import block_votes
import database
import vote_store

logger = logging.getLogger(__name__)


def resnap_map(cg, slug: str) -> dict:
    """Re-anchor a map's edge votes to the current graph. Returns a summary dict."""
    anchors = database.get_edge_vote_anchors(slug)  # [(row_id, lat, lon)]
    if not anchors:
        return {"map": slug, "votes": 0, "kept": 0, "merged": 0}

    cg.ensure_loaded()
    points = [(lat, lon) for _, lat, lon in anchors]
    new_edges = cg.nearest_edges(points)  # vectorized kdtree lookup
    pairs = [
        (row_id, eid)
        for (row_id, _, _), eid in zip(anchors, new_edges)
        if eid is not None
    ]
    result = database.resnap_edge_votes(slug, pairs)
    logger.info(
        f"[RESNAP:{slug}] {result['rows']} votes → {result['kept']} kept, "
        f"{result['merged']} merged (collisions deduped)"
    )
    return {"map": slug, "votes": result["rows"], **result}


def rebuild_redis_for_map(redis_client, slug: str, mode_int: int) -> int:
    """Rebuild ALL of a map's Redis serving state from the canonical DB rows.

    Postgres is canonical; Redis holds three derived layers, and a DB-level
    change to a vote invalidates every one of them:

      * ``ev:<slug>``            the per-(edge, type, direction) aggregate
      * ``bd:<slug>:<mode>:…``   the per-block identity hashes
      * ``bagg:<slug>:<mode>``   the deduped per-block aggregate

    This used to rebuild only the first. The block layers are keyed by VOTE TYPE
    as well as by block, so re-pointing votes at a different type (or deleting
    them) left the old slots standing and the pins kept rendering from state
    nothing would ever correct — the heatmap and the proposal pins disagreeing
    about which votes exist. The block layers are keyed by edge→block, which
    lives on the graph and is not available here, so they are CLEARED rather
    than replayed: `/api/graph-votes` rebuilds them from Postgres on the next
    request, against the graph that instance actually has (app.py, `bagg_cold`).

    The revision is bumped FORWARD, never reset. Dropping the key sent it back
    to 1 on the next cast, which is lower than the revision connected clients
    and cached bodies already hold — so a client comparing revisions concludes
    it is up to date and 304-pins itself to a body describing votes that no
    longer exist. INCR is the only safe move: it is atomic, it cannot collide
    with a concurrent caster, and it always lands above whatever was there.

    Returns the number of edge-aggregate fields written.
    """
    if redis_client is None:
        return 0
    redis_client.delete(vote_store.hash_key(slug))
    # Derived block state — dropped here so it can't outlive the votes it was
    # built from. Cheap and idempotent when the map has no block layer.
    block_votes.clear(redis_client, slug, mode_int)

    rows = database.aggregate_votes_for_replay(slug)  # [(slug, edge, vt, dir, cnt)]
    n = 0
    if rows:
        pipe = redis_client.pipeline(transaction=False)
        for _slug, edge_id, vt_id, direction, cnt in rows:
            field = vote_store.redis_field(edge_id, mode_int, vt_id, direction)
            pipe.hset(vote_store.hash_key(slug), str(field), int(cnt))
            n += 1
        pipe.execute()

    rev = redis_client.incr(vote_store.revision_key(slug))
    logger.info(f"[REBUILD:{slug}] {n} edge fields, block state cleared, revision → {rev}")
    return n


def repair_vote_types(slug: str, list_labels: list[str]) -> dict:
    """Best-effort: make a map's orphaned vote-type ids resolve to real labels.

    The global vote_types registry can lose rows (e.g. a reset) while edge_votes
    still reference the old ids → the legend shows "#<id>". We pair each orphaned
    id (most-voted first) with the map's configured labels (in list order),
    register the label, and re-point those votes onto the real id. The dominant
    orphan is the reliable one (it's the bulk of the data); we report — never
    guess — any orphans beyond the available labels.
    """
    orphans = database.orphaned_vote_type_counts(slug)  # [(vt_id, rows)] desc
    repaired, unresolved = [], []
    for i, (orphan_id, count) in enumerate(orphans):
        if i < len(list_labels):
            label = list_labels[i]
            real_id = vote_store.get_vote_type_id(label)  # registers + caches
            moved = database.remap_edge_vote_type(slug, orphan_id, real_id)
            repaired.append({"orphan_id": orphan_id, "rows": moved,
                             "label": label, "new_id": real_id})
        else:
            unresolved.append({"orphan_id": orphan_id, "rows": count})

    # Register any remaining labels so future votes on this map resolve too.
    for label in list_labels:
        vote_store.get_vote_type_id(label)

    if repaired or unresolved:
        logger.info(f"[REPAIR:{slug}] repaired {len(repaired)} orphan type(s), "
                    f"{len(unresolved)} left unresolved")
    return {"map": slug, "repaired": repaired, "unresolved": unresolved}


def migrate_map(cg, redis_client, slug: str, mode_int: int,
                list_labels: list[str], repair: bool = True) -> dict:
    """Full per-map migration: repair vote types → re-snap edges → rebuild Redis."""
    report = {"map": slug}
    if repair:
        report["repair"] = repair_vote_types(slug, list_labels)
    report["resnap"] = resnap_map(cg, slug)
    report["redis_fields"] = rebuild_redis_for_map(redis_client, slug, mode_int)
    return report
