# Referenced by: docs/algorithms/07-counts.md (what a vote is counted PER) and
#   docs/vote-system-design.md §2 (the block dedup key).
"""
Who counts as one voter.

Two identities run through the vote system, and conflating them is the bug this
module exists to prevent:

  STORAGE identity — `device_id`, the sha256 of the browser's `voter_id`
    (a UUID in localStorage). It OWNS a row: whose vote it is, whether a press
    is a re-vote or a fresh cast, what `/api/my-votes` returns, what an unvote
    is allowed to delete. Nothing here changes that — ownership has to stay with
    the browser that cast, because that browser is the only thing that can take
    the vote back.

  COUNTING identity — what the block layer treats as ONE person when it
    deduplicates (`block_vote_types`, and therefore top-proposal ranking and the
    route-summary card). That is what this module returns.

Why they can't be the same key. `device_id` is exactly as stable as
localStorage, and on the traffic that matters most — a QR sticker scanned off a
lamp post, on a phone — localStorage is routinely not stable at all:

  · Private Browsing and "Block All Cookies" make every document load mint a
    fresh id (getVoterId's catch falls back to an ephemeral one), and a scan is
    TWO document loads: the /s/<code> landing, then StickerGate's
    location.replace onto /m/<slug>.
  · Safari's ITP deletes script-writable storage after 7 days without a
    first-party interaction — precisely the profile of a scan-once visitor.
  · The canonical-subdomain hop (cityedit.org → walkways.cityedit.org) crosses
    an ORIGIN, and localStorage is per-origin.

Measured on prod 2026-08-13: one iPhone, one IP, six page loads in three
minutes, six voter_ids. Three of the resulting device_ids landed in the same
block and made it the top-ranked "Fix signal timing" proposal on the map with a
deduped count of 3 — from one person who pressed the button three times because
each reload told them, truthfully for the id it was holding, that they hadn't
voted yet.

So counting collapses onto the IP hash, which survives every mode above for the
length of a visit. It is a strictly COARSER key than device_id, and that cut has
a real cost in the other direction: a household, an office, a school or a
carrier NAT counts once no matter how many people vote from it. That is the
deliberate trade — under-counting a shared network is a smaller lie than one
phone manufacturing a city-wide #1 — and it is reversible without a redeploy
through VOTE_COUNT_IDENTITY.

Neither key is a person. This narrows the gap; it does not close it.
"""

import os

# "ip" (default): devices behind one IP count once. "device": the pre-2026-08-13
# behaviour, one count per browser. Read once at import — and deliberately
# feeding SCHEME below, because flipping this changes what an already-stored
# count MEANS, and the derived bd:/bagg: structures must be rebuilt from
# Postgres rather than left holding two keyings mixed in one hash.
COUNT_BY = (os.environ.get("VOTE_COUNT_IDENTITY") or "ip").strip().lower()
if COUNT_BY not in ("ip", "device"):
    COUNT_BY = "ip"

# Rides in the block-layer version marker (app.py's `bver:` key) so a deploy
# that changes the counting key rebuilds the aggregate instead of corrupting it.
SCHEME = f"cid-{COUNT_BY}"

# The SQL for the same rule, for counts Postgres does directly rather than
# through the Redis block mirror (database.count_unique_voters_for_edges).
# COALESCE mirrors the `ip_hash or device_id` fallback below.
SQL_IDENTITY = "device_id" if COUNT_BY == "device" else "COALESCE(ip_hash, device_id)"


def counting_identity(device_id: str, ip_hash: str | None) -> str:
    """The key one vote is counted under at the block grain.

    Falls back to `device_id` whenever there is no IP to collapse on — rows
    written before the ip_hash column existed, and any path that stored none.
    The fallback is never worse than the old behaviour; it IS the old behaviour,
    for that row alone.

    Bulk imports pass `ip_from_voter=True`, which stores ip_hash = device_id
    (unique per ride, per scraped proposal), so an import stays one voter per
    row through this function with no special case here.
    """
    if COUNT_BY == "device":
        return device_id
    return ip_hash or device_id
