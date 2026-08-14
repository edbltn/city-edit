"""How many people have opened a proposal.

Same honesty bar as presence.py, a different time horizon: this number is
cumulative, so it has to survive being wrong for months rather than for 35
seconds.

WHAT IS STORED — one sketch per (map, vote type, block), nothing per view
------------------------------------------------------------------------
`pv:<slug>:<type>:<block_id>` is a Redis HyperLogLog holding one member per
person who has opened a proposal of that type covering that block. There is no
row per view, no timestamp, no IP, no device_id, and nothing enumerable — a HLL
is a lossy register array, so the stored bytes cannot be turned back into a list
of who was here. The honest minimum for "show a count" is a structure that can
only ever produce a count, and this is that structure. It is also ~200 bytes for
a block a few dozen people have seen (Redis keeps small sketches sparse) against
~40 bytes per row for the obvious alternative, so the private choice is the
cheap one too.

WHY THE ROUTE LEVEL DOES NOT DOUBLE COUNT
-----------------------------------------
A route proposal is a corridor of many blocks, and the same person will open it
from two different blocks on two different days. Summing per-block counts would
report them twice.

We never sum. `PFCOUNT k1 k2 … kn` returns the cardinality of the UNION of the
sketches, so someone present in both block A's and block B's sketch contributes
exactly 1 to a proposal spanning {A, B}. The dedupe is a property of the data
structure, not bookkeeping anyone has to keep correct.

That is also what makes the feature implementable at all, because *route
proposals have no durable id*. `RouteProposal.id` is an FNV-1a hash of the
proposal's sorted path edges (routeProposals.ts), so it rotates every time a
vote lengthens, shortens or reroutes the corridor — the client uses an id change
as the SIGNAL that a corridor moved. `legendIdx` is worse: it is assigned by
first-encounter order while the server builds each graph-votes snapshot, so it
can shift for everyone when one vote reorders one edge. Hanging a counter on
either would have quietly reset every proposal's audience to zero on the next
vote. Here the storage key is a BLOCK id — server-owned, baked, already the unit
of the vote system — plus the vote type's LABEL, which is the one part of a
proposal's identity that is stable across recomputes and across clients. The
corridor itself is supplied at READ time as a set of blocks. Reshape it tomorrow
and the number reshapes with it, still deduped, with no migration and no
backfill.

WHY THE TYPE IS IN THE KEY
--------------------------
Without it, every proposal on a block shares one audience: the 200 people who
read the Protected-bike-lane corridor would appear as the audience for a
Fix-signal-timing pin on the same corner, which is a different proposal by a
different author about a different thing. Keying by type confines any bleed to
proposals of the SAME type overlapping the same ground — which the proposal
layer already limits (one point winner per block per type; route proposals of a
type are diversity-capped at Jaccard 0.5).

WHAT A "VIEW" IS, AND WHY WE WRITE EVERY BLOCK
----------------------------------------------
A view is: an interactive proposal card, open and on screen, for VIEW_DWELL_MS.
Hover cards never count — they are `pointer-events:none` transients the mouse
crosses on the way somewhere else, and counting them would make the number
mostly mouse trajectory. A hidden tab never counts.

One view PFADDs the viewer into every block the opened proposal spans, not just
the one they clicked. Anchoring on the clicked block alone was the first design
and it is wrong: a 40-block corridor read by 100 people would put all 100 in one
sketch, so a sub-corridor's count would depend on whether it happened to contain
that one block — 100 if it did, 0 if it didn't, for the same street and the same
readers. Writing every block makes each sketch a true statement on its own
terms, and makes the count precisely sayable:

    a proposal's count = the number of people who have opened a proposal of
    this type covering any part of this one.

The UI copy is written to that sentence and does not claim more.

IDENTITY, AND WHAT THIS DEGRADES TO
-----------------------------------
The member is the COUNTING identity votes already use (vote_identity.py): the
hashed IP, taken from the WebSocket handshake. Explicitly NOT `device_id`, whose
localStorage preimage is currently fragmenting — one iPhone, six voter_ids,
three minutes (prod 2026-08-13).

Keyed on device_id, that person reads as six viewers, and the count rises every
time identity degrades further: a metric that looks healthier the more broken it
gets, with no way to notice from outside. Keyed on an IP hash, those same six
page loads are one member added six times to one sketch, and PFADD is
idempotent, so the number does not move at all. Reload the tab a hundred times
and nothing happens.

What it degrades to instead:
  · Shared egress — household, office, school, carrier NAT → many people, one
    member, permanently UNDER-counted. Accepted: a modest true number is worth
    more here than a large uncertain one.
  · One person on genuinely different networks over months (home, work, phone)
    → a few members. This is the residual inflation. It is real, but it is
    bounded by how many networks a person uses, not by how often their storage
    clears — a few, not dozens, and it needs a network change rather than a
    page load.
  · Redis unreachable → 0, which reads as "say nothing" at the display floor.
    A broken cache must not be able to invent an audience.

Finally, HLL is approximate (~0.81% relative error) at large cardinality and
effectively exact while the sketch stays sparse, which covers every number these
maps actually produce. `count_views` returns that distinction next to the number
so the UI can stop claiming precision it does not have.
"""

import hashlib
import logging

logger = logging.getLogger(__name__)

# Sketches for a block nobody has opened in half a year are not worth keeping.
# Refreshed on every write, so a proposal people still read never expires.
KEY_TTL = 180 * 86400

# Ceiling on how many blocks one message may write or union. Corridors run to a
# few dozen blocks; this bounds the work one client can ask for per view.
MAX_PROPOSAL_BLOCKS = 256

# Above this, stop printing an exact figure — HLL's error stops being smaller
# than the last digit we would be showing.
EXACT_BELOW = 1000

# Returned when Redis fails or the request is empty.
COUNT_ERROR = (0, True)


def type_token(label: str) -> str:
    """Short stable token for a vote type.

    The LABEL, not `legendIdx` (per-snapshot, shifts under everyone) and not the
    DB `vote_type_id` (which would mean a lookup that can create rows on a path
    driven by client messages). Hashed only to bound the key length and keep
    arbitrary user text out of Redis key names.
    """
    return hashlib.sha1((label or "").strip().encode("utf-8")).hexdigest()[:10]


def block_key(slug: str, token: str, block_id: int) -> str:
    return f"pv:{slug}:{token}:{int(block_id)}"


def normalize_blocks(block_ids) -> list[int]:
    """De-duplicated, integer, length-capped. Block ids may legitimately be
    NEGATIVE — `blockKeyOf` returns `-(edgeId + 2)` for an edge that belongs to
    no baked block — so the guard is on type and count, never on sign."""
    out: list[int] = []
    seen = set()
    for b in block_ids or ():
        try:
            bid = int(b)
        except (TypeError, ValueError):
            continue
        if bid in seen:
            continue
        seen.add(bid)
        out.append(bid)
        if len(out) >= MAX_PROPOSAL_BLOCKS:
            break
    return out


def record_view(redis_client, slug: str, label: str, blocks: list[int],
                identity: str) -> None:
    """Count `identity` as having opened a proposal of type `label` spanning
    `blocks`.

    Idempotent by construction: a hundred reopens from the same identity leave
    every sketch bit-identical, which is why no rate limit is needed to keep the
    number honest (only to keep the work bounded).
    """
    if not blocks:
        return
    try:
        token = type_token(label)
        pipe = redis_client.pipeline()
        for bid in blocks:
            key = block_key(slug, token, bid)
            pipe.pfadd(key, identity)
            pipe.expire(key, KEY_TTL)
        pipe.execute()
    except Exception as e:
        logger.debug(f"[VIEWS] pfadd failed for {slug}/{label}: {e}")


def count_views(redis_client, slug: str, label: str,
                blocks: list[int]) -> tuple[int, bool]:
    """People who have opened a proposal of this type covering any part of this
    one. Returns `(count, exact)`.

    The UNION — not the sum — is what makes one person who opened two different
    blocks of the same corridor count once.
    """
    if not blocks:
        return COUNT_ERROR
    token = type_token(label)
    try:
        n = int(redis_client.pfcount(*[block_key(slug, token, b) for b in blocks]))
    except Exception as e:
        logger.debug(f"[VIEWS] pfcount failed for {slug}: {e}")
        return COUNT_ERROR
    return n, n < EXACT_BELOW
