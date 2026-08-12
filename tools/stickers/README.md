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
starters, then five more in the same voice — eight in all, every one of them
three words or fewer.

| Line | Casts | Kind |
|------|-------|------|
| Tired of waiting? | Fix signal timing | point |
| Fix this intersection. | Add traffic calming | route |
| Whose streets? | Add tree | point |
| Nowhere to stand. | Add pedestrian refuge island | point |
| It's dark here. | Add intersection lighting | point |
| Press. Wait. Wait. | Fix signal timing | point |
| This corner kills. | Fix dangerous intersection | point |
| No way across. | Add crosswalk | route |

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

**The code sits on a light chip, not knocked out of the black.** Inverted codes
are a real compatibility risk: measured here, zxing reads a light-on-dark code
and **OpenCV cannot read one at all**, at any size. Phone cameras mostly cope,
but "mostly" is not good enough for the one element on the sticker that has to
work — which is exactly the call `tools/merch/qr_tee.py` made for dark garments.
With the chip, both decoders read it. It costs type size (14.9 pt against the
light colourway's 17.8), because the chip has to cover the quiet zone too and
the layout reserves the whole thing.

**It costs ink.** The field is no longer bare stock, so a 2.5" disc is a full
flood of near-black. On the 3" laser stock that is free; on the 2.5" **matte
inkjet** paper it is slow to dry, prone to curl, and expensive. Use
`--colourway light` there if in doubt.

## The city (`--style iso`)

The code as true 30° isometric cubes — the same projection as the `tee-isogrid`
merch — sitting on the dark modules, with the light modules left as empty ground.

```bash
./env/bin/python build_stickers.py --messages wait --style iso
./env/bin/python verify_scan.py          # ← do not skip this one
```

**The most beautiful version of this does not scan, and it is worth knowing why.**
At a cube height of exactly 1 cell each cube projects to a *regular hexagon* —
all six silhouette corners land the same distance from its centre — and a field
of them tiles hexagonally. That is the true isometric look. Measured through the
finished sticker it decodes **0%** of the time, at every capture width, on both
stocks and both colourways. Not "degrades": zero.

The cause is the finder patterns. Each is a solid 7×7 block wrapped in a
**one-module** light ring, and that ring is exactly what a decoder scans for. A
cube a full cell tall throws a wall a full cell wide, which covers its
neighbour's ring completely — so the pattern the decoder is hunting for stops
existing before any of the data matters.

Measured, true isometric, cubes on dark modules only:

| cube height | decodes |
|---|---|
| 1.00 cell (regular hexagon) | **0%** |
| 0.80 | 0% |
| 0.65 | 14% |
| 0.55 | 58% |
| 0.45 | 77% |

So `CUBE_H` defaults to **0.45** — a low box rather than a cube, still true
isometric, still three shaded faces, but no longer a hexagon. Set it to `1.0`
anywhere the code does not have to work: a poster, a screen, a tee.

Even at 0.45 this is 77% against the flat code's 100%, so it stays opt-in. Earlier
attempts at a 45° square diamond and a two-height skyline are in the git history
with their own measurements; the short version is that every one of them traded
scans for looks, and the flat code has never lost a single read.

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
