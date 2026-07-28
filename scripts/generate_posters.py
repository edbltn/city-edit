#!/usr/bin/env python3
"""
Procedurally generate street-safety posters for NYC's most dangerous
intersections, one per (campaign x intersection), each with a QR deep link
into City Edit at that exact intersection.

Inputs:
  - data/analysis/intersections_master.csv   (from build_intersection_rankings.py)
  - data/posters/templates/<campaign>.html   (replica/novel templates with slot
    ids: headline / statline / cta / qr / neighborhood — leaf elements)

Campaigns (danger definition -> template -> copy):
  ped_no1     rank by recency-decayed pedestrian score -> "THE #<n> MOST
              DANGEROUS INTERSECTION FOR PEDESTRIANS IN NYC" (poster 2 replica)
  cyc_no1     recency-decayed cyclist score (poster 3 replica)
  overall     recency-decayed combined score (poster 1 "is killer!" replica)
  dark        merged ped+cyclist danger (rank-sum of both lists; poster 4 dark)
  nabe_no1    per-NTA-neighborhood #1 by fresh weighted score (novel)
  after_dark  night-crash share among active intersections (novel)

Usage:
  python scripts/generate_posters.py                # all campaigns, default Ns
  python scripts/generate_posters.py --campaign nabe_no1 --limit 40 --scale 2
"""

import argparse
import csv
import html
import re
import subprocess
from pathlib import Path

import qrcode

REPO = Path(__file__).resolve().parent.parent
MASTER = REPO / "data" / "analysis" / "intersections_master.csv"
TEMPLATES = REPO / "data" / "posters" / "templates"
OUT = REPO / "data" / "posters" / "out"
RENDER = REPO / "scripts" / "render_poster.sh"
BASE_URL = "https://cityedit.org/m/nyc-intersections"
# Preselected vote type carried in every QR: scanning lands with the pin set
# and this (point-kind) type ready to cast.
QR_VOTE_TYPE = "Fix dangerous intersection"


def load_rows():
    rows = list(csv.DictReader(open(MASTER)))
    num = ["crashes", "ped_inj", "ped_kill", "cyc_inj", "cyc_kill", "f12_crashes",
           "f12_ped_inj", "f12_ped_kill", "f12_cyc_inj", "f12_cyc_kill",
           "score_f12_all_w", "recency_score", "recency_ped", "recency_cyc",
           "base_rate_12mo", "heat_surprise", "cool_surprise", "night_share"]
    for r in rows:
        for k in num:
            r[k] = float(r[k])
    return rows


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]


def fill_slot(doc, slot_id, content):
    """Replace the inner HTML of the (leaf) element carrying id=slot_id.

    Only safe for leaf slots (no nested same-tag children) — structured slots
    like the ped_no1 headline are edited via targeted `swaps` instead."""
    pat = re.compile(r'(<(\w+)[^>]*\bid="' + slot_id + r'"[^>]*>).*?(</\2>)', re.S)
    if not pat.search(doc):
        return doc
    return pat.sub(lambda m: m.group(1) + content + m.group(3), doc)


def set_style(doc, slot_id, extra_css):
    """Append inline CSS to the element carrying id=slot_id."""
    pat = re.compile(r'<(\w+)([^>]*\bid="' + slot_id + r'"[^>]*)>')
    def sub(m):
        attrs = m.group(2)
        if 'style="' in attrs:
            attrs = attrs.replace('style="', f'style="{extra_css};', 1)
        else:
            attrs += f' style="{extra_css}"'
        return f"<{m.group(1)}{attrs}>"
    return pat.sub(sub, doc, count=1)


# Short per-poster codes ("PED-01") stamped tiny on each poster and listed in
# the placement checklist, so a stack of printed posters maps back to
# intersections without reading QR codes.
CODE_PREFIX = {
    "ped_no1": "PED", "cyc_no1": "CYC", "overall": "OVR", "dark": "DRK",
    "nabe_no1": "NBH", "heating_up": "HOT", "after_dark": "NGT",
}


def poster_code(campaign, rank):
    return f"{CODE_PREFIX[campaign]}-{rank:02d}"


def stamp_footer(doc, code, dark=False):
    """Tiny footer on every poster: placement code bottom-left, City Edit
    wordmark (boxed letters, echoing the app logo) + cityedit.org bottom-right."""
    ink = "#d9d9d9" if dark else "#4a4a4a"
    dim = "#9b9b9b" if dark else "#8a8a8a"
    box = (
        "display:inline-block;border:1.3px solid " + ink + ";border-radius:2px;"
        "padding:0 2px;margin:0 0.5px;font:700 8px ui-monospace,Menlo,monospace;"
        "line-height:11px;color:" + ink + ";"
    )
    letters = "".join(
        f'<span style="{box}">{ch}</span>' if ch != " " else
        '<span style="display:inline-block;width:4px;"></span>'
        for ch in "CITY EDIT")
    tag = (
        '<div style="position:absolute;left:10px;bottom:5px;z-index:99;'
        "font:600 11px ui-monospace,Menlo,monospace;letter-spacing:1.5px;"
        f'color:{dim};">{code}</div>'
        '<div style="position:absolute;right:10px;bottom:5px;z-index:99;">'
        f'{letters}<span style="font:600 10px ui-monospace,Menlo,monospace;'
        f'color:{dim};margin-left:6px;">cityedit.org</span></div>'
    )
    if "</body>" in doc:
        return doc.replace("</body>", tag + "</body>", 1)
    return doc + tag


def nabe_display(nta):
    """Poster-friendly neighborhood name: drop parenthetical qualifiers."""
    return re.sub(r"\s*\([^)]*\)", "", nta).strip()


def night_fraction(share):
    from fractions import Fraction
    f = Fraction(share).limit_denominator(10)
    return f.numerator, f.denominator


def set_qr(doc, qr_relpath):
    return re.sub(r'(<img[^>]*\bid="qr"[^>]*\bsrc=")[^"]*(")',
                  lambda m: m.group(1) + qr_relpath + m.group(2), doc)


def victims_line(r, window="full"):
    if window == "full":
        inj = int(r["ped_inj"] + r["cyc_inj"])
        kil = int(r["ped_kill"] + r["cyc_kill"])
        since = "since 2023"
    else:
        inj = int(r["f12_ped_inj"] + r["f12_cyc_inj"])
        kil = int(r["f12_ped_kill"] + r["f12_cyc_kill"])
        since = "in the last 12 months"
    if inj and kil:
        what = f"{inj} people injured and {kil} killed"
    elif kil:
        what = f"{kil} {'person' if kil == 1 else 'people'} killed"
    else:
        what = f"{inj} {'person' if inj == 1 else 'people'} injured"
    return f"{what} here {since}"


def _nabe_fill(r, rank):
    name = nabe_display(r["nta"]).upper()
    return {"neighborhood": html.escape(name),
            "statline": html.escape(victims_line(r))}


def _nabe_style(r, rank):
    # MELROSE (7 chars) renders at the template's native 124px; longer names
    # shrink proportionally so the line always fits the 850px canvas.
    name = nabe_display(r["nta"])
    size = max(38, min(124, int(124 * 7.4 / max(len(name), 5))))
    return {"neighborhood": f"font-size:{size}px"}


def _after_dark_fill(r, rank):
    num, den = night_fraction(r["night_share"])
    return {"statline": f"<b>{num} out of {den}</b> crashes here happen<br>"
                        f"between <b>9pm and 6am</b>"}


def _dark_select(rows):
    """Merged ped+cyclist danger: sum each intersection's RANK on the
    pedestrian and cyclist recency lists (Borda-style), so the winners are
    dangerous to BOTH modes — a pure victim sum is pedestrian-dominated.
    Requires real signal in each mode."""
    ped_rank = {id(r): i for i, r in enumerate(sorted(rows, key=lambda r: -r["recency_ped"]))}
    cyc_rank = {id(r): i for i, r in enumerate(sorted(rows, key=lambda r: -r["recency_cyc"]))}
    both = [r for r in rows if r["recency_ped"] > 1 and r["recency_cyc"] > 1]
    return sorted(both, key=lambda r: ped_rank[id(r)] + cyc_rank[id(r)])


# Campaigns run in priority order and each intersection is claimed ONCE — a
# location never appears twice in the packet/checklist. `claim` controls how
# a campaign handles an already-claimed intersection:
#   rank    → drop it but keep the true rank number (a "#N" claim must stay true)
#   unique  → drop it (the "#1 in <nabe>" claim can't transfer to a runner-up)
#   generic → substitute the next-best unclaimed one (claim is generic)
CAMPAIGN_ORDER = ["ped_no1", "cyc_no1", "nabe_no1", "dark", "after_dark", "overall"]

CAMPAIGNS = {
    # "#N" rank posters keep their nested per-line headline markup; only the
    # rank number is swapped in place.
    "ped_no1": {
        "template": "ped_no1.html", "default_n": 10, "claim": "rank",
        "select": lambda rows: sorted(rows, key=lambda r: -r["recency_ped"]),
        "swaps": lambda r, rank: [(r"THE #\d+ MOST", f"THE #{rank} MOST")],
        # NOTE: this template's id="statline" is the YOU ARE HERE pin text —
        # the design carries no stat line, so nothing is filled here.
        "fill": lambda r, rank: {},
    },
    "cyc_no1": {
        "template": "cyc_no1.html", "default_n": 10, "claim": "rank",
        "select": lambda rows: sorted(rows, key=lambda r: -r["recency_cyc"]),
        "swaps": lambda r, rank: [(r"THE #\d+ MOST", f"THE #{rank} MOST")],
        "fill": lambda r, rank: {},
    },
    "nabe_no1": {
        "template": "nabe_no1.html", "default_n": 30, "claim": "unique",
        "select": None,  # special-cased: one per neighborhood
        "fill": _nabe_fill,
        "style": _nabe_style,
    },
    "dark": {
        "template": "dark_gear.html", "default_n": 10, "claim": "generic",
        "select": _dark_select,
        "fill": lambda r, rank: {},
    },
    "after_dark": {
        "template": "after_dark.html", "default_n": 12, "claim": "generic",
        "select": lambda rows: [r for r in sorted(rows, key=lambda r: -r["night_share"])
                                if r["f12_crashes"] >= 4],
        "fill": _after_dark_fill,
        "dark": True,
    },
    "overall": {
        "template": "bike_killer.html", "default_n": 10, "claim": "generic",
        "select": lambda rows: sorted(rows, key=lambda r: -r["recency_score"]),
        # The mockup's own placeholder is "your <bike thing> is killer!" — fill
        # it with something a bike nerd would grin at that matches the drawn
        # step-through city bike.
        "fill": lambda r, rank: {"headline": "your step-through is killer!"},
    },
}
CAMPAIGNS["dark"]["dark"] = True


def nabe_targets(rows, n):
    best = {}
    for r in rows:
        if not r["nta"] or r["score_f12_all_w"] < 3:
            continue
        cur = best.get(r["nta"])
        if cur is None or (r["score_f12_all_w"], r["recency_score"]) > (cur["score_f12_all_w"], cur["recency_score"]):
            best[r["nta"]] = r
    return sorted(best.values(), key=lambda r: -r["score_f12_all_w"])[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", choices=list(CAMPAIGNS), action="append",
                    help="campaign(s) to build (default: all)")
    ap.add_argument("--limit", type=int, help="posters per campaign (default per-campaign)")
    ap.add_argument("--scale", type=int, default=2,
                    help="device scale factor (2 -> 1700x2268 px, good for print)")
    ap.add_argument("--base-url", default=BASE_URL)
    ap.add_argument("--pdf", action="store_true", default=True,
                    help="assemble out/poster-book.pdf (checklist + all posters)")
    ap.add_argument("--no-pdf", dest="pdf", action="store_false")
    args = ap.parse_args()

    rows = load_rows()
    manifest = []
    used = set()  # (lat, lon) already claimed — one poster per intersection
    for name in [c for c in CAMPAIGN_ORDER if not args.campaign or c in args.campaign]:
        c = CAMPAIGNS[name]
        tpl_path = TEMPLATES / c["template"]
        if not tpl_path.exists():
            print(f"!! {name}: template {tpl_path} missing, skipping")
            continue
        tpl = tpl_path.read_text()
        n = args.limit or c["default_n"]
        cands = nabe_targets(rows, n) if name == "nabe_no1" else c["select"](rows)
        # (row, rank) targets under the campaign's claim policy (see above).
        targets = []
        if c["claim"] == "generic":
            for r in cands:
                if (r["lat"], r["lon"]) not in used:
                    targets.append((r, len(targets) + 1))
                if len(targets) >= n:
                    break
        else:  # rank / unique: never renumber, never substitute
            for rank, r in enumerate(cands[:n], start=1):
                if (r["lat"], r["lon"]) in used:
                    print(f"   {name} #{rank} already claimed, dropped: {r['name_osm']}")
                    continue
                targets.append((r, rank))
        outdir = OUT / name
        outdir.mkdir(parents=True, exist_ok=True)
        for r, rank in targets:
            used.add((r["lat"], r["lon"]))
            from urllib.parse import quote_plus
            url = f'{args.base_url}?w={r["lat"]},{r["lon"]}&vt={quote_plus(QR_VOTE_TYPE)}'
            tag = f"{rank:02d}_{slug(r['name_osm'])}"
            qr_png = outdir / f"{tag}_qr.png"
            qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                               border=1, box_size=12)
            qr.add_data(url)
            qr.make(fit=True)
            # match each template's QR colorway by the asset it references
            dark_qr = "qr_white_on_black" in tpl
            red_qr = "qr_red" in tpl
            qr.make_image(
                fill_color="white" if dark_qr else ("#d0312d" if red_qr else "black"),
                back_color="black" if dark_qr else "white").save(qr_png)

            code = poster_code(name, rank)
            doc = tpl
            for pat, repl in (c["swaps"](r, rank) if "swaps" in c else []):
                doc = re.sub(pat, repl, doc)
            for slot, content in c["fill"](r, rank).items():
                doc = fill_slot(doc, slot, content)
            for slot, css in (c["style"](r, rank).items() if "style" in c else []):
                doc = set_style(doc, slot, css)
            doc = set_qr(doc, qr_png.name)
            doc = stamp_footer(doc, code, dark=c.get("dark", False))
            html_path = outdir / f"{tag}.html"
            html_path.write_text(doc)
            subprocess.run([str(RENDER), str(html_path), str(outdir / f"{tag}.png"),
                            "850", "1134", str(args.scale)],
                           check=True, capture_output=True)
            manifest.append({"code": code, "campaign": name, "rank": rank,
                             "name": r["name_osm"],
                             "nta": r["nta"], "borough": r["borough"],
                             "lat": r["lat"], "lon": r["lon"], "url": url,
                             "png": str((outdir / f"{tag}.png").relative_to(REPO))})
            print(f"{code}: {r['name_osm']} ({r['nta']})")

    with open(OUT / "manifest.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest[0].keys()))
        w.writeheader()
        w.writerows(manifest)
    print(f"\n{len(manifest)} posters -> {OUT} (manifest.csv written)")

    if args.pdf:
        build_poster_book(manifest, args.scale)


CAMPAIGN_TITLE = {
    "ped_no1": "Pedestrian #N citywide", "cyc_no1": "Cyclist #N citywide",
    "overall": "Overall (recency)", "dark": "Ped + cyclist merged",
    "nabe_no1": "Neighborhood #1", "after_dark": "After dark",
}

CHECKLIST_ROWS_PER_PAGE = 24

CHECKLIST_CSS = """
<style>
  * { box-sizing: border-box; }
  body { margin: 0; width: 850px; height: 1134px; background: #fff;
         font: 14px system-ui, -apple-system, sans-serif; color: #161512;
         padding: 44px 48px; }
  .eyebrow { font-size: 11px; font-weight: 700; letter-spacing: 0.14em;
             text-transform: uppercase; color: #c0674a; }
  h1 { font-size: 26px; font-weight: 800; margin: 4px 0 2px; }
  .sub { color: #6b6760; font-size: 12.5px; margin-bottom: 18px; }
  table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
  th { text-align: left; font-size: 10.5px; text-transform: uppercase;
       letter-spacing: 0.07em; color: #8a857a; padding: 6px 8px;
       border-bottom: 2px solid #161512; }
  td { padding: 7.5px 8px; border-bottom: 1px solid #e8e6de; font-size: 13px;
       vertical-align: middle; }
  .box { width: 15px; height: 15px; border: 2px solid #161512; border-radius: 3px;
         display: inline-block; }
  .code { font: 700 12.5px ui-monospace, Menlo, monospace; letter-spacing: 1px; }
  .nta { color: #6b6760; font-size: 11.5px; display: block; }
  .camp { color: #6b6760; font-size: 11.5px; white-space: nowrap; }
  .pageno { position: absolute; bottom: 18px; right: 48px; color: #9b9b9b;
            font-size: 11px; }
</style>
"""


def build_poster_book(manifest, scale):
    """Checklist pages + every poster, one PDF (out/poster-book.pdf)."""
    from PIL import Image

    pages = [manifest[i:i + CHECKLIST_ROWS_PER_PAGE]
             for i in range(0, len(manifest), CHECKLIST_ROWS_PER_PAGE)]
    checklist_pngs = []
    for pi, rows in enumerate(pages):
        body = "".join(
            f"<tr><td><span class='box'></span></td>"
            f"<td class='code'>{m['code']}</td>"
            f"<td>{html.escape(m['name'])}<span class='nta'>"
            f"{html.escape(m['nta'])} · {html.escape(m['borough'])}</span></td>"
            f"<td class='camp'>{CAMPAIGN_TITLE[m['campaign']]}</td></tr>"
            for m in rows)
        doc = (CHECKLIST_CSS +
               "<div class='eyebrow'>City Edit · Dangerous-intersections campaign</div>"
               "<h1>Poster placement checklist</h1>"
               "<div class='sub'>Each poster carries its code bottom-left. "
               "Tick when hung; QR links open the intersection on "
               "cityedit.org/m/nyc-intersections.</div>"
               "<table><tr><th></th><th>Code</th><th>Intersection</th><th>Campaign</th></tr>"
               f"{body}</table>"
               f"<div class='pageno'>checklist {pi + 1} / {len(pages)}</div>")
        hp = OUT / f"checklist_{pi + 1}.html"
        pp = OUT / f"checklist_{pi + 1}.png"
        hp.write_text(doc)
        subprocess.run([str(RENDER), str(hp), str(pp), "850", "1134", str(scale)],
                       check=True, capture_output=True)
        checklist_pngs.append(pp)

    sheets = [Image.open(p).convert("RGB") for p in checklist_pngs]
    sheets += [Image.open(REPO / m["png"]).convert("RGB") for m in manifest]
    pdf = OUT / "poster-book.pdf"
    sheets[0].save(pdf, save_all=True, append_images=sheets[1:],
                   resolution=int(72 * scale), quality=85)
    print(f"poster book: {pdf} ({len(sheets)} pages: "
          f"{len(checklist_pngs)} checklist + {len(manifest)} posters)")


if __name__ == "__main__":
    main()
