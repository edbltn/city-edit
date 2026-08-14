#!/usr/bin/env python3
"""
Move a map's votes from one vote type to another (CLI).

Free-text suggestions mint a vote type on the spot, so a map accumulates one-off
labels ("Add Citibike Station") next to the authored ones. Adopting such a vote
means pointing its rows at the canonical vote type — a *retype*, not a re-vote:
nobody's vote is created or destroyed, it just says the map's word for the thing.

The catch is that a vote lives in FOUR places, and the obvious ones are not all
of them. `vote_migration.rebuild_redis_for_map` rebuilds the edge aggregate only
(and resets the revision to 1, which sends it BACKWARDS and can 304-pin stale
bodies on connected clients), so a DB-level retype done that way leaves the block
layers keyed on the old type and stale pins keep rendering. Every layer, in the
order this script writes them:

  1. Postgres `edge_votes.vote_type_id`        — canonical (database.remap_edge_vote_type)
  2. Redis `ev:<slug>`                         — edge aggregate; the type is packed
                                                 INTO the field key, so each field
                                                 moves to a new key
  3. Redis `bd:<slug>:<mode>:<block>:<vt>:<dir>`— per-block identity multiplicities
     and `bagg:<slug>:<mode>`                    + the deduped block aggregate
                                                 (block_votes.py); what the pins and
                                                 the block heat are actually read from
  4. Redis `vote_rev:<slug>`                   — INCR, never SET: the revision must
                                                 only ever go UP, and the bump is what
                                                 tells connected clients to refetch

The block layer is moved by MERGING the identity hash (HINCRBY per identity, so a
voter holding both types on one block keeps one presence) and then rewriting the
aggregate from the merged HLEN — the same definition rebuild_from_db uses, so the
result is what a full rebuild would have produced. No graph artifacts are needed,
which is what makes this runnable against prod from a laptop.

Usage (dry run by default — it prints every layer's move and writes nothing):

    python retype_votes.py nyc-proposals --from "Add Citibike Station" \
                                         --to "Add Citibike station"
    python retype_votes.py nyc-proposals --from "Add Citibike Station" \
                                         --to "Add Citibike station" --apply

`--from` also accepts "#<id>" for a type whose vote_types row is gone. `--to` must
name a type that already exists, unless you pass --create-to with --kind.

Against prod: open the bastion tunnels first (Postgres on local :5433, Memorystore
on local :6380 — docs/gcp-deployment.md), take the DB backup, then pass the prod
DATABASE_URL / REDIS_HOST / REDIS_PORT inline for the one command. Never repoint
server/.env.
"""
import argparse
import os
import sys

import redis
from dotenv import load_dotenv

load_dotenv()

import block_votes  # noqa: E402
import database  # noqa: E402
import vote_store  # noqa: E402

# Modes are a 4-bit enum; a map's votes carry exactly one, but discovering it
# from the data beats trusting the map row (a map that changed style has both).
ALL_MODES = sorted(vote_store.MODE_IDS.values())


def _redis_client():
    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    c = redis.Redis(host=host, port=port, db=0, decode_responses=True)
    c.ping()
    return c


def _resolve_from(labels: dict[str, int], spec: str) -> int:
    """`--from` → vote type id. Accepts a label or "#<id>" for an orphaned id."""
    if spec.startswith("#"):
        return int(spec[1:])
    vid = labels.get(spec)
    if not vid:
        sys.exit(f"No vote type labelled {spec!r}. Pass '#<id>' if the row is gone.")
    return vid


def _resolve_to(labels: dict[str, int], spec: str, args) -> int:
    """`--to` → vote type id, registering the label when asked. Returns 0 for a
    label a dry run WOULD create — the report is the point, so a dry run must
    never be the thing that mints the row."""
    vid = labels.get(spec)
    if vid:
        return vid
    if not args.create_to:
        sys.exit(f"No vote type labelled {spec!r}. Add it to the map's authored set "
                 f"first (manage_vote_types.py), or pass --create-to --kind.")
    if args.kind not in ("point", "route"):
        sys.exit("--create-to needs --kind point|route (the new type's family)")
    if not args.apply:
        return 0
    return database.get_or_create_vote_type_id(spec, args.kind)


def _edge_moves(rc, slug: str, old_vt: int) -> list[tuple[int, int, int]]:
    """[(old_field, new_field_base_inputs…)] → (old_field, edge, mode, dbit, count)
    for every ev: field carrying the old type. One HGETALL: a map's hash is
    thousands of fields, not millions (it is per-map, not per-city)."""
    out = []
    for field, count in rc.hgetall(vote_store.hash_key(slug)).items():
        key = int(field)
        edge, mode, vt, dbit = vote_store.unpack(key)
        if vt == old_vt:
            out.append((key, edge, mode, dbit, int(count)))
    return out


def _block_moves(rc, slug: str, old_vt: int) -> list[tuple[int, int, int, int]]:
    """[(mode, block_id, dbit, aggregate_count)] for every bagg: slot carrying the
    old type. Enumerating from the aggregate (one HGETALL per mode) finds every
    bd: key too: a bd: key only exists while some identity is present in it, and
    that presence is exactly what put a field in bagg."""
    out = []
    for mode in ALL_MODES:
        raw = rc.hgetall(block_votes.bagg_key(slug, mode))
        for field, count in raw.items():
            block_id, vt, dbit = block_votes.unpack_block_field(int(field))
            if vt == old_vt:
                out.append((mode, block_id, dbit, int(count)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Re-point a map's votes from one vote type to another.")
    ap.add_argument("slug", help="Map slug (votes on other maps are untouched)")
    ap.add_argument("--from", dest="src", required=True,
                    help="Label to move votes OFF (or '#<id>')")
    ap.add_argument("--to", dest="dst", required=True, help="Label to move votes ONTO")
    ap.add_argument("--create-to", action="store_true",
                    help="Register --to if it does not exist yet (needs --kind)")
    ap.add_argument("--kind", choices=("point", "route"),
                    help="Family for a newly created --to type")
    ap.add_argument("--apply", action="store_true",
                    help="Write. Without it, report the moves and change nothing.")
    args = ap.parse_args()

    if not database.DATABASE_URL:
        sys.exit("DATABASE_URL is not set — point it at the target database first.")
    labels = {label: vid for vid, label in database.fetch_all_vote_types()}

    old_vt = _resolve_from(labels, args.src)
    new_vt = _resolve_to(labels, args.dst, args)
    if old_vt == new_vt:
        sys.exit(f"--from and --to are the same vote type (#{old_vt}) — nothing to do")

    rc = _redis_client()
    rows = database.count_map_votes_by_type(args.slug, old_vt)
    edges = _edge_moves(rc, args.slug, old_vt)
    blocks = _block_moves(rc, args.slug, old_vt)
    rev_before = int(rc.get(vote_store.revision_key(args.slug)) or 0)

    dst_id = f"#{new_vt}" if new_vt else "(new — would be created)"
    print(f"{args.slug}: '{args.src}' #{old_vt} → '{args.dst}' {dst_id}")
    print(f"  postgres : {rows} edge_votes row(s)")
    print(f"  ev:       : {len(edges)} field(s), {sum(e[4] for e in edges)} vote(s)")
    print(f"  bd:/bagg: : {len(blocks)} block slot(s) "
          f"({sum(1 for b in blocks if b[3] > 0)} with a live count)")
    print(f"  vote_rev  : {rev_before} → {rev_before + 1}")
    if not args.apply:
        print("\n(dry run) nothing written — re-run with --apply")
        return
    if not rows and not edges and not blocks:
        print("\nNothing to move.")
        return

    # 1. Postgres — canonical. Drops rows that would collide with an existing
    #    vote of the target type by the same device on the same edge.
    moved = database.remap_edge_vote_type(args.slug, old_vt, new_vt)
    print(f"\n✓ postgres: {moved} row(s) retyped")

    # 2. Edge aggregate. HINCRBY (not HSET) so a field that already exists on the
    #    target type absorbs the old one instead of overwriting it.
    if edges:
        pipe = rc.pipeline()
        hk = vote_store.hash_key(args.slug)
        for old_field, edge, mode, dbit, count in edges:
            new_field = vote_store.pack(edge, mode, new_vt) | (dbit << vote_store.DIR_BIT)
            pipe.hincrby(hk, str(new_field), count)
            pipe.hdel(hk, str(old_field))
        pipe.execute()
        print(f"✓ ev:{args.slug}: {len(edges)} field(s) moved")

    # 3. Block layers. Merge the identity multiplicities, then restate the
    #    aggregate as the merged hash's HLEN — the deduped count's definition.
    for mode, block_id, dbit, _count in blocks:
        bd_old = block_votes.bd_key(args.slug, mode, block_id, old_vt, dbit)
        bd_new = block_votes.bd_key(args.slug, mode, block_id, new_vt, dbit)
        bagg = block_votes.bagg_key(args.slug, mode)
        old_field = str(block_votes.pack_block_field(block_id, old_vt, dbit))
        new_field = str(block_votes.pack_block_field(block_id, new_vt, dbit))
        mults = rc.hgetall(bd_old)
        if mults:
            pipe = rc.pipeline()
            for identity, mult in mults.items():
                pipe.hincrby(bd_new, identity, int(mult))
            pipe.delete(bd_old)
            pipe.execute()
            rc.hset(bagg, new_field, rc.hlen(bd_new))
        rc.hdel(bagg, old_field)
    if blocks:
        print(f"✓ bd:/bagg:{args.slug}: {len(blocks)} block slot(s) moved")

    # 4. Revision LAST, and only upward: everything the clients will refetch is
    #    already consistent, and a revision that went backwards would let a
    #    cached 304 pin the pre-retype body on a connected client.
    rev_after = rc.incr(vote_store.revision_key(args.slug))
    print(f"✓ vote_rev:{args.slug}: {rev_before} → {rev_after}")
    if rev_after <= rev_before:
        print("  ⚠ revision did not increase — clients may keep a stale body")


if __name__ == "__main__":
    main()
