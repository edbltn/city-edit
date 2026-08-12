# Stickers

Round die-cut stickers for lamp posts, signal boxes and sign poles. Each one is
a QR code and one line of text, and nothing else.

```bash
cd tools/stickers
python3 -m venv env && uv pip install --python ./env/bin/python -r requirements.txt

./env/bin/python build_stickers.py --all --proof     # 2.5" sheets
./env/bin/python build_stickers.py --stock 3 --all   # 3" sheets
./env/bin/python verify_scan.py                      # read them back
```

Everything lands in `out/<stock>/` (gitignored — the build is deterministic, so
it is always exactly reproducible from this directory).

## What a scan does

A sticker is a code on a pole. It knows what it wants — one vote type on the
`nyc-proposals` map — but not where it is, because nobody knows that until
someone puts it somewhere.

1. **First scan.** `https://cityedit.org/s/<code>` loads the app, which asks the
   server about the code. It has no location yet, so the visitor gets one screen:
   the line off the sticker, and a button to share their location.
2. **They share it.** The map opens at that spot with the vote type
   preselected, ready to cast.
3. **They vote.** *That* is when the sticker gets pinned — a shared location is a
   claim, a cast vote is a commitment. The code now IS that place.
4. **Every scan after that** goes straight to the vote at that location. Nobody
   is ever asked for their location twice for the same sticker.

Refusing location is a supported ending, not an error: the map still opens with
the vote type ready and the pin left to the visitor, and the sticker stays
unresolved for the next person — because a hand-placed pin says where the *user*
guessed, not where the *sticker* is.

The pin is write-once and the race is settled in SQL, so two people scanning a
fresh sticker at the same moment cannot fight over where it lives.

## Tracking

Two layers, at two resolutions, because they answer different questions.

| | Carried as | Lands in | Answers |
|---|---|---|---|
| **Message** | `?src=stk-<key>` | `[MAPLOAD] … src=` → `cityedit_map_load_ms`'s `src` label | "Which line pulls people in?" |
| **Sticker** | the code itself | `sticker_codes` rows in Postgres | "Which poles get scanned? Which found a home?" |

The split is deliberate. `?src=` is a Cloud Monitoring **metric label**, and
metric labels are a cardinality budget: a dozen message tags is a dashboard,
ten thousand per-sticker tags is a billing incident and an unreadable chart.
Per-sticker cardinality is just rows, so it lives in the database.

```sql
-- how the campaign is doing
SELECT src_tag,
       count(*)                          AS printed,
       count(*) FILTER (WHERE scans > 0) AS scanned,
       count(resolved_at)                AS placed,
       sum(scans)                        AS total_scans
FROM sticker_codes WHERE campaign = 'nyc-2026-08'
GROUP BY src_tag ORDER BY placed DESC;

-- where the placed ones ended up
SELECT code, src_tag, lat, lon, scans, resolved_at
FROM sticker_codes WHERE resolved_at IS NOT NULL ORDER BY resolved_at DESC;
```

Visits by message are a group-by on `cityedit_map_load_ms` in Metrics Explorer.

## The line

`campaign.py` holds every message, the vote type it casts, and why. The three
starters, then nine more in the same voice.

| Line | Casts | Kind |
|------|-------|------|
| Tired of waiting? | Fix signal timing | point |
| Fix this intersection. | Add traffic calming | route |
| Whose streets? | Add tree | point |
| Nowhere to stand. | Add pedestrian refuge island | point |
| You can't see the kids. | Daylight this corner | point |
| It's dark here. | Add intersection lighting | point |
| They turn into you. | Ban turn on red | point |
| Press. Wait. Wait. | Fix signal timing | point |
| This corner kills. | Fix dangerous intersection | point |
| Slow this street down. | Add traffic calming | route |
| No way across. | Add crosswalk | route |
| This sidewalk is a ledge. | Widen sidewalk | route |

Lines are capped at 26 characters. That is an editorial limit, not a rendering
one — the art will set anything you give it, but a sticker read from six feet
away, at an angle, in the rain, gets about four words.

### "Shorten the light signal"

`nyc-proposals` already has a label for this and it is **Fix signal timing**.
That won over minting a new "shorten the light" type for two reasons. It is the
honest ask — what you want at a bad crossing is a shorter wait, which is a
*timing* change, and sometimes that means a longer walk phase rather than a
shorter cycle. And it is the label the map's imported DOT proposals already use,
so a sticker vote stacks onto the existing pile at that corner instead of
starting a near-duplicate one beside it.

### The route-kind wrinkle

Three lines cast **route** types (`Add traffic calming`, `Add crosswalk`,
`Widen sidewalk`). A route type describes a corridor, and one shared coordinate
is a point — so those stickers open the map with the scanned end placed and the
vote type ready, and the visitor extends it along the stretch they mean. In
practice a single dropped pin already selects the block it lands on, so a cast
works immediately; extending it just makes the claim bigger and more specific.

The commissioned mapping for **"Fix this intersection."** is `Add traffic
calming`, which is kept. Worth knowing: the map also has `Fix dangerous
intersection`, a **point** type whose wording matches that line exactly and
which needs no corridor. If the route step turns out to cost scans in the field,
that swap is a one-line change in `campaign.py` plus a reprint.

## Before the first sticker goes on a pole

1. **Add the tree vote type** — `Whose streets?` casts `Add tree`, which is not
   yet on `nyc-proposals`:
   ```bash
   cd server && ./env/bin/python manage_vote_types.py add nyc-proposals \
     "Add tree" --icon trees --kind point
   ```
   (Against prod, run it through the bastion tunnel — see `docs/gcp-deployment.md`.
   Idempotent; running Flask picks it up within a minute.)
2. **Seed the codes** — every code must exist in the database before the sheet
   leaves the house, or a scan is a 404 at the kerb:
   ```bash
   psql "$DATABASE_URL" -f out/2.5/seed_stickers.sql
   ```
   Safe to re-run: existing codes, and any location they have already resolved
   to, are left untouched.
3. **Scan one off a printed sheet** — not off a screen — before the rest go in
   your bag.

## Printing

| | 2.5" | 3" |
|---|---|---|
| Product | Methdic, matte white | Premium Label Supply, glossy white |
| ASIN | `B0D1BRRPS7` | `B0D92RBZHN` |
| Per sheet | 12 (3 × 4) | 6 (2 × 3) |
| Pack | 360 labels, 30 sheets | 60 labels, 10 sheets |
| Printer | inkjet or laser | **laser only** |
| Bleed | 0.030" | 0.0625" |

Both are circles on 8.5×11 carrier sheets, so the art is not cropped — it is
*registered*. `sheet.py` carries the grid and re-derives that it closes exactly
on a Letter sheet; the build refuses to run if it does not.

**Print at 100%.** Any "fit to page" or "shrink oversized pages" setting
rescales by a percent or two — invisible in a photo, fatal on a die-cut grid,
with every sticker creeping further off its circle towards the edges of the
page. Run one `-proof.png` on plain paper first and hold it against a label
sheet up to a window.

**The 3" stock is glossy and laser-only.** It has no inkjet coating: ink sits on
the surface, never dries, and smears on the first sticker you peel.

## Why it looks like this

It is a roundel, in the vocabulary of the things it gets stuck next to — signal
boxes, sign posts, work notices. An amber band, the bare white stock inside it,
a black code, one black line.

**The field is unprinted.** The stock is matte white and so is the brand's
paper, so printing a white field onto white stock costs a full pass of ink and
buys nothing. It also buys drift tolerance: registration error is only visible
where printed art meets the die, and the only art at the edge is a band that
deliberately overshoots it. A drifted sheet reads as a band a hair thicker on one
side instead of a white crescent down the edge of every sticker. Each stock's
bleed is capped at half the gap to its neighbour, and the build checks it.

**The type is solved, not laid out.** A line inside a circle has a chord for a
measure, and the chord shrinks the further it sits from the centre. Set "TIRED
OF WAITING?" as one line under the code and it fits at about ten point —
readable in a proof, invisible on a lamp post. Broken to `TIRED OF / WAITING?`
the same words set at twenty. So `sticker.py` tries one, two and three lines,
breaks each into the most even stack the words allow, fits every stack against
its own chords, and prints whichever comes out biggest. Every sticker's type
ends up a different size, sized by its own words — the same "justified by size,
not by tracking" rule as the poster and tee type in `tools/merch/typo.py`, whose
Red Hat Mono outlining this borrows.

**The code is uppercase.** `HTTPS://CITYEDIT.ORG/S/AB3KM` is entirely inside
QR's *alphanumeric* character set, which packs at 5.5 bits per character instead
of byte mode's 8. That fits the link into a 29-module code at error correction
**H** — the highest, 30% of the code can be destroyed and it still reads, which
is the right setting for a thing that lives outdoors and gets rained on. The
same URL in lowercase falls into byte mode and needs 33 modules in the same 1.1
inches. The server lowercases the path, so the shouting never reaches anyone.

## Verifying

`verify_scan.py` does not inspect the matrix the generator produced. It
rasterises the finished artwork and decodes the pixels, one sticker at a time —
because that is the real situation, a phone held up to a single disc — and
checks that each one decodes **to the URL the manifest says is at that grid
cell**. A layout bug that swapped two discs would otherwise give a sheet where
every code scans perfectly and every one is pinned to the wrong pole. It also
refuses a run where any two stickers share a code.

zxing-cpp is the gate; OpenCV is reported beside it and deliberately advisory,
since it rejects a handful of these codes at any resolution *including the raw
segno output*, i.e. it is measuring its own detector rather than our sticker.

The `--px` sweep is the margin measurement. All 216 stickers decode down to 200
px across the disc (3.0 px per module) and degrade at 2.1 px/module, which is
the sampling floor for a 29-module code — so the design is bounded by physics,
not by anything wrong with the art. A phone a foot from a 2.5" sticker sees
several hundred pixels of it.

## Codes

Five characters from a 31-character alphabet with the ambiguous glyphs removed
(no `0`/`O`/`1`/`I`/`L`) — 28.6M possibilities, all inside QR's alphanumeric set.

Derived, not drawn: `sha1(campaign / stock / message / index)`. Re-run the build
and you get the same codes, so a reprint of sheet 3 is the same twelve stickers —
which matters once some of them are already on poles and already resolved.
Minting more means extending the index range with `--copies`, never reshuffling
it. The stock is in the seed because a 2.5" and a 3" sticker are different
physical objects that go on different poles and must never share an identity.

## Files

| File | What |
|------|------|
| `campaign.py` | the line: messages, vote types, campaign tags |
| `codes.py` | minting codes and the URL that goes in the QR |
| `sticker.py` | the artwork for one disc |
| `sheet.py` | the two label stocks' grids, and the checks that they close |
| `build_stickers.py` | the CLI: sheets, manifest, seed SQL, contact sheet |
| `verify_scan.py` | decode the finished art back |
