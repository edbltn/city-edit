# Audience: view counts and co-presence

Two features, one thesis.

The owner's word for what these are is **rational ritual**: the value is not
that someone looked, it is that *we both know* someone looked. Common knowledge
is the product. That inverts the usual analytics engineering. A normal metric
wants to capture as much as possible and is tuned by how much signal it
retains. These two numbers are read by the people being counted, and they are
worthless the moment those people suspect the number is padded — so they are
tuned by how little they can be doubted. Under-counting costs a little.
Over-counting costs everything, all at once, and permanently.

Everything below follows from that.

---

## 1. The identity problem, first

`device_id` is not a person key, and right now it is not even a browser key.

`server/vote_identity.py` already records why. The measurement on prod
(2026-08-13): one iPhone, one IP, six page loads in three minutes, **six
distinct `voter_id`s**. Three of the resulting `device_id`s landed in the same
block and made it the top-ranked proposal on the map with a deduped count of 3
— from one person. The causes are structural, not a bug that will be fixed:
Private Browsing and "Block All Cookies" mint a fresh id per document load; a
sticker scan is *two* document loads; Safari's ITP deletes script-writable
storage after 7 days without a first-party interaction — exactly the profile of
a scan-once visitor; and the canonical-subdomain hop crosses an origin, which
localStorage does not survive.

The app's answer for votes was to introduce a second identity. Storage identity
(`device_id`) still owns a row — it has to, because the browser that cast is the
only thing that can take a vote back. But **counting identity** is the hashed
IP, and that is what the block layer treats as one person.

**These features dedupe on the counting identity. Nothing here reads
`device_id`.** In fact nothing here reads any client-supplied identifier at
all: the key is taken from the WebSocket handshake's IP, server-side, so the
client is never asked who it is and could not lie if it wanted to.

### What it degrades to

This is the part that matters, so it is stated in both directions.

| Failure | Effect on the count | Direction |
|---|---|---|
| `device_id` churns (the case under investigation) | **none** — six page loads are one member added six times, and PFADD is idempotent | — |
| Household / office / school / carrier NAT | many people count as one | **under** |
| One person, several networks over months | a few members instead of one | **over**, bounded |
| Redis unreachable | 0, which renders as silence | **under** |
| Backgrounded tab | not present, not counted | **under** |

The only inflation path left needs a *network* change, not a page load. Reload
a hundred times and neither number moves. That is the property the whole design
was chosen for: the failure mode that is invisible from the outside — a count
that rises precisely as identity degrades, so the more broken we get the more
impressive we look — is structurally unavailable, rather than monitored for.

The residual over-count (one person on home + work + phone) is real and is not
hidden. It is bounded by how many networks a person uses.

If `VOTE_COUNT_IDENTITY=device` is ever set, both features inherit that choice
along with votes, and both start inflating together. That is the correct
coupling: there should not be two answers on one map to "how many people is
this".

---

## 2. Feature 1 — view counts on proposal modals

### What a "view" is

An **interactive** proposal card, open and on screen, for **2 seconds**.

- Hover cards never count. They are `pointer-events:none` transients the mouse
  crosses on its way somewhere else; counting them would make the number
  largely a record of mouse trajectories. Anyone who drags across a busy map
  and watches cards tick up learns in four seconds that the number is junk.
- A minimized card never counts — it is a pill with a label on it.
- A hidden tab never counts. The client refuses to send, and the server
  refuses to record, independently.
- A card with no proposal on it never counts. A bare block is a place.

### The hard part: route proposals have no durable id

The brief asks for per-block tracking that does not double count at the route
level. The obvious implementation — a counter per proposal — cannot be built,
and it is worth being precise about why, because it is the constraint that
shaped everything else.

`RouteProposal.id` is an FNV-1a hash of the corridor's sorted *path edges*
(`routeProposals.ts`). It is deterministic across clients at one instant, which
is what makes it URL-shareable — but it rotates whenever a vote lengthens,
shortens or reroutes the corridor. The client already relies on that: an id
change is how `publishRouteProposals` detects that a corridor moved. A counter
keyed on it would silently reset every proposal's audience to zero on the next
vote.

`legendIdx` is worse. It is assigned by first-encounter order while the server
builds each `graph-votes` snapshot, so one vote reordering one edge can shift
every index for everybody.

So there is no stable name for a route proposal to hang a counter on.

### The resolution: store per block, union at read

```
  pv:<slug>:<type>:<block_id>   →  Redis HyperLogLog of counting identities
```

- **Write**: opening a proposal PFADDs the viewer into the sketch of *every
  block the proposal spans*.
- **Read**: the count is `PFCOUNT k1 k2 … kn` over the proposal's current
  blocks — which returns the cardinality of the **union**, not the sum.

Someone who opens block A's card and later block B's card of the same corridor
is present in two sketches and contributes exactly **1**. The dedupe is a
property of the data structure, not bookkeeping anyone has to keep correct.

And the proposal never has to be named. Its identity is supplied at read time
as a set of block ids — server-owned, baked artifacts, already the unit the
vote system counts in. Reshape the corridor tomorrow and the number reshapes
with it: no migration, no backfill, no id to keep alive.

This makes the count precisely sayable, and the copy says exactly this and no
more:

> the number of people who have opened a proposal of this type covering any
> part of this one.

### Two decisions inside that

**The type is in the key.** Without it, every proposal on a block shares one
audience, and the 200 people who read a Protected-bike-lane corridor would
appear as the audience for a Fix-signal-timing pin on the same corner — a
different proposal, by a different author, about a different thing. Keying by
type confines bleed to same-type proposals overlapping the same ground, which
the proposal layer already limits. The key uses the **label**, the one part of
a proposal's identity that is stable across recomputes and across clients.

**A view writes every block, not just the clicked one.** This was the second
design; the first was wrong. Anchoring on the clicked block alone means a
40-block corridor read by 100 people puts all 100 into one sketch — so a
sub-corridor's count would read 100 if it happened to contain that block and 0
if it didn't, for the same street and the same readers. Writing every block
makes each sketch a true statement on its own terms.

### TTL, storage and privacy

The honest minimum is a structure that *can only ever produce a count*. A
HyperLogLog is a lossy register array: the stored bytes cannot be turned back
into a list of who was here. There is no row per view, no timestamp, no IP, no
`device_id`, nothing enumerable, and nothing joinable to a vote. It is also
~200 bytes for a block a few dozen people have seen — the private choice is
also the cheap one.

TTL is 180 days, refreshed on write, so a proposal people still read never
expires and a dead map garbage-collects itself.

The count is cumulative and unwindowed. "47 people have looked at this" is what
the sketch can honestly support; "47 this week" would need per-day sketches
unioned across days *and* blocks, which is thousands of keys per read for a
worse claim.

### Display honesty

- **Nothing at all below 2.** "1 person has looked at this", printed to the one
  person looking at it, reads as either a bug or a boast.
- **"N people have opened this", not "N views."** Views are cheap and everyone
  knows it, so a view count reads as a vanity metric and gets discounted on
  sight. It is also the literal truth of what the sketch holds.
- **Above 1000 it stops pretending.** HLL error (~0.81%) is no longer smaller
  than the last digit, so the server returns `exact: false` and the UI says
  "about 1.2k people" rather than a confident 1,247 it cannot stand behind.
- **Nothing renders while it is unknown.** No skeleton, no zero, no dash. A
  number that appears only when it has something true to say is the point.

---

## 3. Feature 2 — co-presence

> "3 other viewers"

Shown from **one** other viewer up (`MIN_TOTAL = 2` in `CoPresence.tsx`).

The brief asked for two others, and that was the shipped behaviour from
2026-08-14 until it was changed — during which the strip was never once seen.
The reason is in §1: the counting identity is a hash of the client IP, so a
household, an office or a carrier NAT is **one** member. Three marks therefore
needs three separate *networks* on the same map inside the same two-second
push. That was verified rather than assumed — three real browsers on one
machine are pushed `n=1`; it takes three distinct source IPs to be pushed
`n=3` — so the owner could not reach the old threshold even deliberately,
because every device in the house leaves by the same egress.

A ritual nobody attends produces no common knowledge, which is the only thing
the feature is for. One other person is where the claim starts, and the
under-count above means a displayed 2 is a floor: the real room is two or more,
never fewer. Raising it back is a one-line change to `MIN_TOTAL`.

### Structure: a sorted set, deliberately not a HyperLogLog

HLL is right for views and wrong here, for one reason: **presence has to go
down.** A HLL cannot forget a member, so a map 40 people visited over an
afternoon would read "40 people are here now" forever — monotonic inflation,
the exact failure this feature cannot survive.

```
  pres:<slug>   →  ZSET, member = counting identity, score = last heartbeat
```

- Heartbeat every 10s from the socket's own loop; entries older than 35s are
  pruned at read.
- **Departure is explicit** (`ZREM`), not left to expiry. A count that stays
  high for 35 seconds after everyone leaves is over-reporting.

  This did not hold as shipped. The `ZREM` runs in the WS handler's `finally`,
  and the handler only learns the peer is gone from `ws.receive()` raising
  `ConnectionClosed` — which the inbound pump's exception filter swallowed by
  substring ("connection closed" is on its ignore list, alongside the benign
  no-data cases). So the loop kept spinning and only unwound when a keepalive
  `send` failed, up to `KEEPALIVE` seconds later: **measured at 30.2s** between
  a real browser closing and the `ZREM`, with every remaining viewer told for
  that whole time that they had company who had left. The pump now catches the
  type and breaks; the same measurement is ~1.1s. Explicit departure is only as
  explicit as the close detection under it.
- A per-process refcount means closing one of a person's two tabs does not
  evict them; closing the last one does.
- The set stays small — its cardinality is people on one map now, not people
  ever.

### Backgrounded tabs

This app throttles background tabs hard, so a buried viewer is doing nothing at
all, and "looking at this" has to mean *looking*. The client reports
`visibilitychange` over the socket and a hidden tab leaves the room
immediately. `visibilitychange` is not throttled, so this needs no timer.

Crucially, presence is **server-driven**: heartbeats come from the server's own
socket loop, not a client interval, so throttling cannot make a live viewer
silently expire. The client sends exactly two things — one message per
visibility transition, and nothing else.

### Cross-instance

The count is in Redis, so it is correct across the app's 1–6 Cloud Run
instances. `delta_hub.subscriber_count()` already existed but is per-process and
would have under-reported by a factor of the instance count. Each process
memoizes the count for 2s so a crowded map costs one Redis read per tick rather
than one per socket; `arrive`/`depart` drop that memo, because the two changes a
process makes itself are stale by construction. (That was a real bug caught in
testing: joining a map you were alone on showed "0 others" for two seconds.)

### It rides the existing socket

Both features use the WebSocket the client already holds. No second channel, no
new endpoint, no codec version bump — the wire codec's `KIND_DELTA`/`KIND_SYNC`
group layout does not fit a scalar anyway, and client→server binary is rejected
in frame version 1. Four JSON messages:

| Direction | Message | Meaning |
|---|---|---|
| → server | `{"type":"here","visible":B}` | this tab became visible / hidden |
| → server | `{"type":"view","k":K,"t":label,"b":[blocks]}` | a card was read |
| ← client | `{"type":"presence","n":N}` | N distinct people here, including you |
| ← client | `{"type":"views","k":K,"n":N,"exact":B}` | that proposal's audience |

Presence pushes are **edge-triggered** — sent only when the number actually
changes — so a steady room costs nothing. `k` echoes the client's request token
so a reply that lands after the reader moved on is discarded rather than
painted onto the wrong proposal.

Replaying `view` cannot inflate anything (PFADD is idempotent), so its 1/second
throttle is a work bound only, not an integrity control.

---

## 4. The UI

Subtle, per the owner, and in the app's existing hand: Red Hat Mono, zero
radius, hairline borders, `color-mix(… var(--ink) N%, transparent)` for
de-emphasis rather than hardcoded greys.

**View count** rides the proposal card's existing meta line — inside the 106px
right-padding that clears the tool icons, in the same breath as the block
count, one step quieter than the text it trails. It fades in, because it
arrives seconds after the card and an unannounced layout shift at that distance
reads as a glitch.

**Co-presence** is a small strip directly under the CITY EDIT logo, top-left:

```
  👥  3 other viewers
```

Its position is not in the stylesheet. The logo island is being resized,
squared and repadded, so the strip measures the island's live box and follows
it (`useLogoAnchor.ts`); an offset committed against today's numbers would go
stale without visibly breaking, which is the worst way for a layout constant to
be wrong. Left edge flush with the island's, top on its underside — but never
higher than the bottom of the chrome the logo sits in. That second clause is
not defensive: on a phone the logo is the FIRST ROW of a 194px stacked topbar,
and anchoring to it alone put the strip at y=42 behind `.legend-item-coords`,
invisible. One rule covers both breakpoints instead of a second copy of the
topbar's media query.

The proper fix is a slot in the topbar — a child under the logo, laid out by
normal flow, inheriting the island's width and padding for free and needing no
JavaScript. That is TopBar.tsx, owned by the agent reworking the chrome, so it
is requested rather than taken.

A figure glyph leads it — one person for a single other viewer, two for
several — drawn inline on `currentColor` so it sits in the strip's muted ink
and flips with the basemap, which an `<img>` icon could not. The same shape is
now the app's one people icon: `public/icons/community.svg` and
`public/icons/tactical.svg` were two different figure drawings in two different
brand colours, and are now one monochrome outline glyph mirrored from the
component's paths.

**What was removed, and what it cost.** An earlier version drew one hollow
square per person with yours filled, so the numeral could be counted against
something — the argument being that a number you can check is a number you can
believe, and that this is the difference between a metric and a ritual. The
owner removed the squares. Recording the trade rather than quietly dropping it:
the count is no longer verifiable at a glance, so trustworthiness now rests
entirely on how it is COUNTED (§1, §3) plus the wording and the hover note. The
wording is load-bearing as a result — "other viewers" is what now says the
total excludes the reader, which the filled square used to say without words.

The small print is one hover away rather than absent: *counted once per network
connection, and only while a tab is in the foreground; people sharing a
connection count as one, so this is more likely to be low than high.*

---

## 5. Files

| File | |
|---|---|
| `server/presence.py` | live co-presence ZSET, cross-instance |
| `server/view_counts.py` | HLL sketches + the union read |
| `server/app.py` | WS handler: identity, heartbeat, edge-triggered push, inbound `here`/`view` |
| `server/tests/unit/test_audience_counts.py` | the two lies, pinned |
| `client-react/src/context/WebSocketContext.tsx` | `viewerCount`, `reportProposalView`, visibility reporting |
| `client-react/src/hooks/useProposalAudience.ts` | dwell timer, display floor, phrasing |
| `client-react/src/components/CoPresence/` | the strip |
| `client-react/src/components/GraphLayer/GraphLayer.tsx` | `proposalBlockIds` + the meta-line render |

## 6. Deliberately not built

- **Per-person view history.** Nothing is stored that could answer "what has
  this person looked at". The count is the only question the data can answer.
- **Windowed counts** ("N this week"). Thousands of keys per read for a weaker
  claim.
- **Avatars, names, cursors.** Co-presence here is a fact about a room, not a
  social feature; identity is an IP hash and must stay that way.
- **A presence count on the map list / landing page.** Nothing currently holds
  a socket there, and manufacturing one to produce a number would be exactly
  the incentive this design exists to resist.
