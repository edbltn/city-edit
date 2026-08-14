# Stickers

Round die-cut stickers for lamp posts, signal boxes and sign poles. A black disc
with white type and a code, and nothing else — no border, no wordmark, no icon.

```bash
cd tools/stickers
python3 -m venv env && uv pip install --python ./env/bin/python -r requirements.txt

./env/bin/python build_stickers.py --all --proof     # 2.5" sheets
./env/bin/python build_stickers.py --stock 3 --all   # 3" sheets
./env/bin/python verify_scan.py                      # read them back
```

The thing to print is the packet:

```
out/2.5/cityedit-stickers-2.5in.pdf     3 pages, 36 stickers
out/3/cityedit-stickers-3in.pdf         3 pages, 18 stickers
```

Vector all the way through — the pages are the SVG masters, not the PNGs — and
every page is asserted to be exactly **612 x 792 pt (US Letter)** at build time.
That check is not ceremony: cairosvg measures in CSS pixels and writes points,
so the first attempt produced a 459 pt page, which every print dialog would have
"fit to page" without comment and the die would have missed by 25%.

Everything else lands in `out/<stock>/` (gitignored — the build is
deterministic, so it is always exactly reproducible from this directory).

**Two messages to a sheet**, split down the middle: a sheet of twelve identical
stickers was the wrong unit, because you do not go out to fix one thing twelve
times — you go out with a couple of complaints and whichever poles you pass. The
12-up splits into a top and bottom half of two rows each; the 6-up has an odd
number of rows so it splits into a left and a right column instead. Either way
each half is exactly half the sheet and the boundary is a straight line you can
see while peeling. `--sheets N` prints N sheets per pair.

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
   claim, a cast vote is a commitment. The code now IS that place, whether the
   vote was a single pin or a whole route, and whether the spot came from GPS or
   was placed by hand.
4. **Every scan after that** goes straight to the vote at that location. Nobody
   is ever asked for their location twice for the same sticker.

Skipping the prompt is a supported ending, not an error: the map opens with the
vote type ready and the selection left to the visitor, and it **still binds the
code when they cast**. The binding is earned by the vote, not by how the spot
was chosen — someone who looks at the map and picks the corner is being more
deliberate than a GPS fix, not less. (This used to withhold the binding on that
path, which meant the most considered way to use a sticker was the one way that
never bound it.)

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
starters plus one more in the same voice — four in all.

| Line | Casts | Kind |
|------|-------|------|
| Tired of waiting? | Fix signal timing | point |
| Fix this intersection. | Add traffic calming | route |
| Whose streets? | Add tree | point |
| What side are you on? | Add crosswalk | route |

**"What side are you on?"** is literally which side of the road you are stranded
on, and audibly the other thing — the same double reading as "Whose streets?".
It casts `Add crosswalk`, the crossing ask that left with "No way across.", in
better words.

**The length rule is the longest WORD, not the word count.** It used to be a
three-word cap, and that cap was standing in for "short enough to read at a
glance" — but a monospaced stack is sized by its longest *line*, and a line
cannot break mid-word, so one long word sets the whole sticker:

| line | words | longest word | sets at |
|---|---|---|---|
| WHAT SIDE ARE YOU ON? | 5 | 4 chars | **16.5 pt** |
| FIX THIS INTERSECTION. | 3 | 13 chars | 14.6 pt |

Five short words read bigger than three long ones, so counting words was
rejecting the better sticker. `campaign.validate()` caps the longest word at 13
characters instead.
**Three words, hard limit.** The character cap is about whether the type *fits*;
the word cap is about whether anyone reads it. A sticker is taken in at a glance,
side-on, by someone walking. The four- and five-word lines this line-up started
with ("You can't see the kids.", "This sidewalk is a ledge.") were all worse than
the three-word ones at exactly that moment, and they set smaller too, since the
type is sized by its own longest line. `campaign.validate()` enforces it.

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

### The 2.5" grid is measured, not from a catalogue

It shipped wrong once. Methdic publishes Avery-template compatibility rather
than a dimensioned drawing, so the first version used the generic "12-up 2.5
circle" layout — and every axis was out by about 0.03":

| | generic | real | error |
|---|---|---|---|
| left margin | 0.250 | 0.280 | −0.030 |
| h gap | 0.250 | 0.220 | +0.030 |
| top margin | 0.406 | 0.368 | +0.038 |
| v gap | 0.0625 | 0.088 | −0.026 |

`validate()` could not catch it, because a wrong grid closes on the sheet just
as neatly as a right one. And the errors compound in *opposite* directions —
too much top margin with too little row gap, too little side margin with too
much column gap — so the sheet splays rather than shifting, and no printer
offset can dial it out.

`check_die.py` measures the die off a photo of a blank sheet and compares:

```bash
./env/bin/python check_die.py --stock 2.5 --photo ../../data/sticker.jpg   --overlay /tmp/align.png
```

It only trusts a photo that passes two independent checks — the sheet's aspect
must match Letter, and the measured die must match the stock's nominal diameter
— so a bad photograph cannot "correct" a good grid. It is strict on **gaps**
(pitch errors accumulate and cannot be corrected) and advisory on **margins**
(a uniform shift, and the thing a photo measures worst — the sheet edge in a
product shot is a drawn line, so ±0.02" is the floor).

**The 3" grid is still the catalogue layout** (OnlineLabels OL2279), not a
measurement, so it carries the same risk. Photograph a blank sheet and run
`check_die.py --stock 3` before a long run.

**Print at 100%.** Any "fit to page" or "shrink oversized pages" setting
rescales by a percent or two — invisible in a photo, fatal on a die-cut grid,
with every sticker creeping further off its circle towards the edges of the
page. Run one `-proof.png` on plain paper first and hold it against a label
sheet up to a window.

**The 3" stock is glossy and laser-only.** It has no inkjet coating: ink sits on
the surface, never dries, and smears on the first sticker you peel. That also
makes it the right stock for the default dark colourway, where the field is a
flood of near-black.

**There is no border any more.** The amber band came off because a ring of
radius is the scarcest thing on a 2.5" circle. Removing it let the code grow
from 0.92 mm per module to **1.01** *and* the line from 14.9 pt to **17** — both
at once. The band's two jobs are covered elsewhere: edge definition is now the
black field's (far more contrast against street furniture than an amber hairline
had), and drift tolerance was always the bleed's job, since the field overshoots
the die either way. `BAND_WIDTH` in `sticker.py` brings it back.

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

**The type is bold and large on purpose.** Red Hat Mono is monospaced, so its
advance is identical at every weight — the heaviest cut sets at exactly the same
size and simply puts more ink on the paper, which makes weight 700 free. Tracking
is kept to 0.02 em for the opposite reason: inside a circle the binding
constraint is the chord, so every em of tracking is paid for directly in type
size.

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

## Colourways

The default is now **white on black** — the app's own terminal palette printed,
not a negative of the light version: `--paper` for the field, `--accent` for the
band, a warm off-white for the type.

```bash
./env/bin/python build_stickers.py --all                    # dark (default)
./env/bin/python build_stickers.py --all --colourway light   # dark ink on bare stock
```

Two things follow from it, and both are measured rather than assumed.

**The code is knocked straight out of the black.** No light chip — the sticker
is genuinely white on black. That is a trade taken with the numbers in hand:

| | zxing | OpenCV | type |
|---|---|---|---|
| inverted (shipping) | **100%** | **0%** | 20.2 pt |
| on a light chip | 100% | ~100% | 17.0 pt |

zxing is the decoder lineage behind most phone scanners and reads every code at
every capture width; OpenCV cannot read an inverted code at all, at any size,
including a plain segno code before our artwork touches it. So a `0/N` OpenCV
column in `verify_scan.py` is the expected reading here, not a regression.

The chip also cost real estate — it has to cover the quiet zone, so the layout
reserved the whole square and the type shrank. Dropping it took the line from
17.0 pt to 20.2.

**If a field report shows scans failing in some app, this is the first thing to
put back**: set `code_panel` to the ink and `code_ink` to the paper in
`COLOURWAYS["dark"]`. `tools/merch/qr_tee.py` made the opposite call for dark
garments and explains why it is defensible either way.

**It costs ink.** The field is no longer bare stock, so a 2.5" disc is a full
flood of near-black. On the 3" laser stock that is free; on the 2.5" **matte
inkjet** paper it is slow to dry, prone to curl, and expensive. Use
`--colourway light` there if in doubt.

## The city (`--style iso`)

The code as true isometric cubes — unit cubes on the dark modules, empty ground
on the light ones — squeezed horizontally so the whole block is a **perfect
square diamond** rather than the 1.73:1 rhombus textbook isometric gives.

```bash
./env/bin/python build_stickers.py --messages whose --style iso
./env/bin/python verify_scan.py          # ← do not skip this one
```

**Full-height cubes now work, and they did not at first.** With pale walls a
unit cube reads **0%**, at every capture width: each finder pattern is a solid
7×7 block wrapped in a **one-module** light ring — exactly what a decoder scans
for — and a cube a full cell tall throws a wall a full cell wide that covers its
neighbour's ring completely. The pattern stops existing before any data matters.

Two changes fixed it, and both were asked for on looks rather than on function.
Darkening the walls pulled them clear of the roofs' tone, so the **roofs** became
the only signal — and every roof sits at z=1, coplanar, which is a clean affine
image of the module grid. The decoder locks onto the roof plane and reads it like
a QR photographed at an angle. Squeezing to a square diamond narrowed each cube
at the same time. Height stopped mattering the moment the walls stopped being
signal.

**It is still not the default, for two reasons.**

| | module | type | zxing | OpenCV |
|---|---|---|---|---|
| flat | **1.01 mm** | 17.0 pt | 100% | ~100% |
| city | 0.78 mm | 15.9 pt | 100% at ≥300 px, 97% at 200 | **0%** |

The module is 29% smaller, which is real margin against a bad camera. And
**OpenCV cannot read the isometric code at all**, at any size — the same class
of risk as an inverted code, and the reason the dark colourway prints its flat
code on a light chip. zxing is the lineage most phone scanners use and it reads
these perfectly, so a phone will probably be fine; "probably" is why this is
opt-in.

A methodological note worth keeping: a **twelve-code sweep of this scored 100%**
and the full 108-sticker run found failures at 200 px. Small samples flatter this
design. Any change here needs the full run, not a spot check.

## Retiring a line

Dropping a message from `campaign.py` retires it from the PRINTABLE set. It does
not retire the stickers: those are on poles, their codes stay seeded, and they
keep resolving — because the server answers a scan from the `sticker_codes` row,
which carries its own headline, map and vote type, and never reads
`campaign.py`. That separation is what makes the printable set safe to change.

It is also what makes the one real failure silent. Nothing in the build will
ever mention a retired code again, so nothing in the build can notice if the map
stops offering the vote type those codes cast — and a missing vote type does not
404, it falls back, so the sticker keeps scanning and quietly casts something
its reader never asked for.

`check_legacy.py` is the guard. It asks the DATABASE what is actually out there
— every seeded code, retired or not — and checks each against the live map:

```bash
DATABASE_URL=… ./env/bin/python check_legacy.py
```

It needs a database URL because the public API has no "list every code"
endpoint, and should not have one. Run it after removing a line, and after any
change to a map's vote types.

## Painted codes (qrart)

The plain module grid can be replaced with a diffusion-painted one from
[qrart](../../../qrart) — a sibling project, not part of this repo — which uses
the clean QR as a ControlNet condition so Stable Diffusion paints your prompt
while light and dark keep lining up with the modules. Nothing else about the
sticker changes: same band, same line, same geometry.

```bash
# always start here: readiness, cache hits and a time estimate, no generation
./env/bin/python build_stickers.py --messages wait \
  --art "aerial view of a dense city at dusk, ink and gold, high detail" \
  --art-dry-run

# then, for real
./env/bin/python build_stickers.py --messages wait --art "..." --art-strength 1.2
./env/bin/python verify_scan.py     # ← now the gate that matters
```

**This is not free.** Art is per *code*, and every sticker has a different code —
that is the entire premise of the scan flow — so a 12-up sheet is twelve
diffusion runs, not one. At the defaults that is roughly **an hour per sheet** on
Apple Silicon, plus a ~4 GB model download the first time. Results are cached in
`art-cache/` keyed by payload plus every parameter that affects the image, so
re-running a build is free and only a changed prompt or a new code pays again.
The cache deliberately lives outside `out/`, where `--clean` cannot reach it.

**Our payload is unusually well suited to this.** qrart's own guidance is that
anything past ~60 characters pushes the code to a denser version and tanks the
success rate — the model needs coarse modules to hide in. A sticker URL is 28
characters in alphanumeric mode: a 29-module version-3 code, about as sparse as
a useful URL gets. The short-code scheme was chosen for print legibility and
turns out to be exactly what makes this technique viable.

**Two gates, and the second one is ours.** qrart asks "does this decode?".
`_qrart_worker.py` asks "does this decode to the URL printed on *this*
sticker?" — a painted code that scans beautifully to the wrong place is worse
than one that does not scan at all — and only an exact match is accepted, out of
`--art-candidates` tries (seed variance in this technique is large). Then
`verify_scan.py` re-renders the *finished disc* with the art in place and
decodes that, which is the check that actually decides whether a painted run is
printable. A code that yields nothing scannable falls back to its plain vector
version rather than failing the sheet, and the count is reported.

**Tuning.** `--art-strength` is the dial: 0.9 beautiful but fragile, 1.1
balanced, 1.3+ obviously a QR. Prompts that work are textured and patchy —
forests, hillside villages, clouds, crowds, circuitry, fabric — because they
give the model places to hide modules; flat styles, big faces and empty skies
fail. `--art-ref` takes a reference image (IP-Adapter) to steer palette and mood.

**Finding qrart:** `QRART_PYTHON` (its venv interpreter), else `QRART_HOME`, else
a checkout beside this repo. Nothing here imports qrart — it pulls in torch and
diffusers, and this pipeline is six small packages and stays that way, so
generation runs in qrart's own interpreter as a subprocess over a JSON pipe.
With qrart absent the build produces exactly the vector stickers it always did.

## Verifying against the live site

`verify_scan.py` proves a sticker decodes to the URL the manifest intends.
`verify_prod.py` proves that URL is a real page, that the campaign's vocabulary
exists there, and that the codes are seeded — different failures, and only the
first is catchable locally.

```bash
./env/bin/python verify_prod.py                        # against prod
./env/bin/python verify_prod.py --base http://localhost:3000
```

It walks **pixels → URL → live page**, in that order, because that is the chain
a person with a phone actually walks; comparing the manifest to prod would only
prove the manifest agrees with itself. Decode and resolve gate the exit code
(those are what make a *printed* sticker wrong); seeding and vocabulary are
reported loudly but do not fail, since both are fixed after the sheets come off
the printer and neither needs a reprint.

### A URL-format change does not invalidate a sticker

A sticker encodes `HTTPS://CITYEDIT.ORG/S/<CODE>` and **nothing else** — no map,
no coordinates, no vote type, no selection params. Where a scan lands is
resolved server-side at scan time, so the app's address format can be
refactored freely without touching anything already on a pole.

This has been exercised: `ce3c92c` ("make the address bar a projection of the
selection") reworked the selection URL and shipped to prod, and every code
regenerated byte-identical afterwards. `canonicalSearch()` in
`selection/urlSync.ts` preserves `?src=` and `?stk=` by name, so the tracking
and the scan flow both survive that class of change. **Run `verify_prod.py`
after such a refactor to confirm it — do not reprint.**

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
| `isoqr.py` | optional: the code drawn as an isometric city |
| `verify_scan.py` | decode the finished art back |
| `qrart_bridge.py` | optional: locate qrart, cache painted codes |
| `_qrart_worker.py` | runs inside qrart's venv; never imported here |
