# Merch

Print-ready artwork for the City Edit tees, plus the runbook for getting them
made, sold and linked from the site.

```bash
cd tools/merch
python3 -m venv env && uv pip install --python ./env/bin/python -r requirements.txt
./env/bin/python build_merch.py --lookbook --clean
```

Everything lands in `out/` (gitignored — the build is deterministic, so it is
always exactly reproducible from this directory). `out/lookbook.html` is a
single self-contained page proofing all four shirts.

## The line

Two designs, two colourways each. That is the whole catalogue.

| | Design | Colourways | Canvas |
|---|--------|-----------|--------|
| 01 | **Isometric Grid** — the logo grid built as the street plan it always was | amber on black · grey on white | 3300 × 3900 (11 × 13 in) |
| 02 | **One Note** — `I ♥ THIS CITY / BUT I HAVE ONE NOTE` + a code to one proposal | white on black · black on white | 3300 × 3300 (11 × 11 in) |

**The art is transparent.** The garment is the background, so a design puts
down ink and nothing else. Don't flatten it onto a rectangle before uploading
or you'll print a black box on a black shirt at roughly triple the ink cost.

**Each colourway is tied to its blank.** Fills are opaque colours
pre-composited against the garment (`GARMENT_BLACK` / `GARMENT_WHITE` in
`palette.py`), because stacked transparency prints as compounding ink rather
than the tone you asked for. Print the black files on navy or heather and the
tones will read off.

## Files

- `palette.py` — the two blanks, the two inks, the accent, and `svg()`
- `typo.py` — Red Hat Mono as vector outlines, so no print shop needs the font
  installed. The variable font is vendored in `fonts/` under the OFL
- `iso.py` — design 01. The projection, the volumes, the standing letters
- `qr_tee.py` — design 02. The heart, the type, the code
- `build_merch.py` — the four SKUs and the proof sheet

The docstrings carry the reasoning; the short version is that the letters on 01
stand up rather than lie on the roofs (on the ground plane a glyph shears in
both axes and turns to mush at chest scale), and 02's heart is the Donate mark
from `NavRail/icons.tsx` — the one curve in an otherwise square icon set.

## The code on design 02

`--qr-url` sets what it points at; every wearer's is different.

Keep it **short and upper-case**. Upper-case URLs encode in QR's alphanumeric
mode rather than byte mode, and the difference is not small:

| Target | Modules |
|--------|---------|
| `HTTPS://CITYEDIT.ORG/S/K4M9X` | 29 × 29 |
| `https://cityedit.org/s/k4m9x` | 33 × 33 |
| a deep link carrying coordinates | 45 × 45 |

At the same 3.6 in print that is the difference between forgiving and fussy on
cotton. `tools/stickers` made `/s/<code>` case-insensitive for exactly this
reason.

⚠️ A sticker code resolves to a *location* on first scan, which is right for a
lamp post and wrong for a chest. Design 02 wants its own short-code namespace
that resolves straight to a proposal — a server change, not an art change, and
worth settling before any print run.

Both colourways are rendered and decoded back with OpenCV as part of iterating
on this design. Do that again if you touch the code path.

## Print constraints the files already respect

- **≥ 4px stroke at 300 DPI.** Thin lines print broken or invisible on DTG.
- **300 DPI stamped in the PNG's `pHYs` chunk** — uploaders read it to
  sanity-check physical size, and cairosvg doesn't write one on its own.
- **Error correction H (30%)** on the code, because a shirt creases, stretches
  and takes ink spread.
- **A light panel behind the code on dark garments.** Decoders have handled
  inverted codes for years, but "mostly" isn't good enough for the one element
  that has to work — so the modules are knocked out of a panel and the garment
  is the dark half of the code.

## Selling it

Recommended: **[Fourthwall](https://fourthwall.com)**. No monthly fee,
print-on-demand and checkout in one product, and — the part that matters for a
one-person project — it acts as *merchant of record*, so it registers, collects
and remits sales tax and VAT rather than leaving that to you. You pay the base
cost only when something sells.

Base costs at the time of writing: tees from ~$9.25 (Bella+Canvas ~$11.75).
Card processing is 2.9% + $0.30. You set the retail price and keep the rest.

The alternative is Printful + Shopify: better storefront control, but $39/mo
before you've sold anything and the tax filing becomes yours. Not worth it
below serious volume.

### Step by step

1. Build the files: `./env/bin/python build_merch.py --lookbook --clean`.
2. Create the Fourthwall shop. Pick a black tee and a white tee; match them to
   `GARMENT_BLACK` / `GARMENT_WHITE` as closely as the blank allows.
3. Upload one product per row of the table above, "fit to print area" — the
   canvases are already the right physical size, so nothing needs scaling.
4. **Order a sample of all four before listing any of them.** Whether five flat
   greys survive a white underbase, and whether the code scans off cotton at
   arm's length, are not things a screen can tell you.
5. Price from the sample. ~$32 is roughly 2.5× base and in line with other
   civic-project shops.
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
