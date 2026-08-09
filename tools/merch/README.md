# Merch

Print-ready artwork for City Edit tees and mugs, plus the runbook for getting
them made, sold, and linked from the site.

```bash
cd tools/merch
python3 -m venv env && uv pip install --python ./env/bin/python fonttools cairosvg pillow
./env/bin/python build_merch.py --previews --clean
```

Writes ten files to `out/` — five designs × two colourways — as both an SVG
master and a 300 DPI PNG. Deterministic: same input, byte-identical output.

## The line

| File | Product | Print | Canvas |
|------|---------|-------|--------|
| `tee-isogrid` | Tee, centre chest | greyscale | 3300 × 3900 (11" × 13") |
| `tee-desire-path` | Tee, full front | 3-colour | 3300 × 4200 (11" × 14") |
| `tee-heat` | Tee, full back | full colour | 3900 × 5100 (13" × 17") |
| `mug-heat-ramp` | 11oz mug, full wrap | full colour | 2475 × 1155 (9.25" × 3.8") |
| `mug-clues` | 11oz mug, full wrap | full colour | 2475 × 1155 |

Colourways are `--black` (light ink, for dark garments and for a dark full-bleed
mug wrap) and `--natural` / `--bone` (dark ink, for pale garments).

`tee-isogrid` is the active design; the rest predate it and are unreviewed.
`mug-clues` in particular was the crossword reading of the logo, which is
retired — it should probably go.

**Tee art is transparent** — the garment is the background. Don't flatten it
onto a black rectangle before uploading or you'll print a black box on a black
shirt, at roughly triple the ink cost.

**Mug art is full-bleed.** Both mug files are dark-field art for a *white*
glossy mug blank — that is not the same thing as ordering a black mug blank.
Order the white one.

**`tee-isogrid` is tied to its blank.** Its faces are opaque colours
pre-composited against the garment (`GARMENT_BLACK` / `GARMENT_NATURAL` in
`build_merch.py`), because stacked transparency prints as compounding ink
rather than the tone you asked for. Print the black files on navy or heather
and the tones will read off.

## Why it looks like this

Everything is built from moves borrowed straight from the app: the mono grid,
the logo's cell grid (`components/Logo/Logo.tsx`), the heat ramp
(`--heat-gradient` in `styles/globals.css`), and the cyan/red start-end kites.

`iso.py` is the isometric design. The logo's grid is a street plan, not a
crossword, and it is built as one — cells become volumes, letter cells become
plinths with the letter standing on them. The letters stand up rather than lie
on the roofs: on the ground plane a glyph shears in both axes at once and turns
to mush at chest scale, while in the wall plane only the baseline slants and
every vertical stem stays vertical. See the docstrings there for the rest.
The palette in `designs.py` mirrors `globals.css` — if the app's accent moves,
move it here too.

Two deliberate departures from the CSS:

- **Ink opacity is floored at 30%.** The logo draws empty cells at 15%; over a
  black garment, DTG lays that over a white underbase and it silts up into a
  muddy near-black. 30% is the faintest thing that survives fabric.
- **Pale colourways get a deeper amber** (`ACCENT_ON_LIGHT`). `#E0A23A` is
  tuned for a `#0d0d0d` surface and disappears on natural cotton.

Text is converted to vector outlines at build time, so no print shop ever needs
Red Hat Mono installed. The variable font is vendored in `fonts/` under the OFL.

## Print constraints these files already respect

- **≥ 4px stroke at 300 DPI.** Thin lines print broken or invisible on DTG. The
  lightest rule in the set is 6px.
- **300 DPI, stamped in the PNG's `pHYs` chunk** — uploaders read it to
  sanity-check physical size, and cairosvg doesn't write one on its own.
- **Nothing critical within 0.5" of a mug edge**, which is where the wrap meets
  the handle and where registration drifts.

Verify the mug template against your fulfiller's own download before the first
order — 2475 × 1155 is the common 11oz spec but it is not universal. It lives
in one place: `MUG_W`/`MUG_H` in `designs.py`.

## Selling it

Recommended: **[Fourthwall](https://fourthwall.com)**. No monthly fee, print-on-
demand and checkout in one product, and — the part that actually matters for a
one-person project — it acts as *merchant of record*, so it registers,
collects and remits sales tax and VAT rather than leaving you to. You pay the
base cost only when something sells.

Base costs at the time of writing: tees from ~$9.25 (Bella+Canvas ~$11.75),
white glossy 11oz mug from ~$5.95. Card processing is 2.9% + $0.30. You set the
retail price and keep the difference.

The alternative is Printful + Shopify: better control and a nicer storefront,
but $39/mo before you've sold anything, and sales tax becomes your problem.
Not worth it below serious volume.

### Step by step

1. Build the files: `./env/bin/python build_merch.py --previews --clean`.
2. Create the Fourthwall shop. Pick the blanks: a black tee, a natural tee, a
   white glossy 11oz mug.
3. Upload one product per row of the table above. Choose "fit to print area" —
   the canvases are already the right physical size, so nothing needs scaling.
4. **Order a sample of every design before listing any of them.** This is the
   step to not skip: the mug wrap seam, whether 30% grey survives DTG on black,
   and whether the heat ramp's purple end reads as purple or as mud are all
   things you cannot check on a screen.
5. Price from the sample. Tees ~$32, mugs ~$20 is roughly a 2.5× markup and in
   line with what other civic-project shops charge.
6. Point `shop.cityedit.org` at the store: add a CNAME at the registrar to the
   host Fourthwall gives you, then connect the subdomain in its dashboard.

   ⚠️ **Do not add `shop.cityedit.org` to `custom_domains` in
   `terraform/main.tf`.** That list is for hostnames Cloud Run answers, and a
   Cloud Run domain mapping requires DNS pointed at Google — the opposite of
   what this hostname needs. This is why the shop is *not* an nginx redirect
   like `donate.cityedit.org`: a CNAME is one DNS record and zero deploys.

7. Turn on the nav link: set `SHOP_URL = "https://shop.cityedit.org"` in
   `client-react/src/components/NavRail/NavRail.tsx`. The tote glyph is hidden
   while that constant is empty, so the link cannot ship ahead of the store.
   Deploy the client (`cloudbuild.overlay.yaml`).

### Photography

Fourthwall's generated mockups are fine to launch on. When you want better,
the products photograph best on the surfaces they're about — a tee on asphalt,
a mug on a curb. Shoot in shade: the heat ramp blows out in direct sun.
