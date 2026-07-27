#!/usr/bin/env python3
"""
Build dangerous-intersection rankings from fresh NYPD collision data.

Pipeline:
  1. Load ped/cyclist-involved crashes (data/raw/crashes_pedcyc_2023on.csv,
     pulled from NYC Open Data h9gi-nx95).
  2. Identify *street intersections* in the app's own NYC walk graph
     (server/osm_data/nyc/walk_graph_arrays.npz): nodes whose incident edges
     carry >= 2 distinct street names. Using app graph nodes means every
     ranked intersection has coordinates that deep-link cleanly into City Edit.
  3. Snap each crash to the nearest intersection node within SNAP_RADIUS_M.
  4. Aggregate per intersection over several time windows and score under
     multiple "danger" definitions; tag each with its NTA-2020 neighborhood.
  5. Emit data/analysis/intersections_master.csv + per-definition top lists.

Danger definitions (columns in the master CSV):
  - overall injured/killed counts (ped + cyclist), full window and fresh windows
  - pedestrian-specific and cyclist-specific severity-weighted scores
    (injured + K_WEIGHT x killed)
  - recency-weighted score (exponential decay, 12-month half-life) — "danger
    as of today", which discounts crashes that predate street redesigns
  - trend outliers: Poisson surprise of the last-12-months count given the
    intersection's own 2023->mid-2025 baseline (heating up / cooling down)

Usage:
  python scripts/build_intersection_rankings.py
"""

import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "data" / "raw"
OUT = REPO / "data" / "analysis"
GRAPH_NPZ = REPO / "server" / "osm_data" / "nyc" / "walk_graph_arrays.npz"
NTA_GEOJSON = RAW / "nta2020.geojson"

DATA_END = datetime(2026, 6, 11)        # freshest crash_date in the pull
FRESH12_START = datetime(2025, 6, 12)   # last 12 months
FRESH24_START = datetime(2024, 6, 12)   # last 24 months
BASE_END = datetime(2025, 6, 11)        # baseline window for trend tests
BASE_START = datetime(2023, 1, 1)

SNAP_RADIUS_M = 40.0
K_WEIGHT = 8                # one death counts as 8 injuries in weighted scores
HALFLIFE_MONTHS = 12.0      # recency decay half-life
MIN_FRESH_FOR_TREND = 4     # noise floor for "heating up" outliers

M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = 84_288.0    # cos(40.7 deg) * 111320

NYC_BBOX = (40.49, 40.94, -74.26, -73.68)


def load_intersection_nodes():
    """Graph nodes with >=2 distinct incident street names -> true street
    intersections, with a display name built from the two busiest names."""
    z = np.load(GRAPH_NPZ)
    coords = z["coords"]                      # [lat, lon]
    eu, ev, enm = z["edge_u"], z["edge_v"], z["edge_name"]
    meta = json.loads(z["meta"].tobytes())
    names = meta["names"]

    node_names = defaultdict(Counter)
    for u, v, n in zip(eu, ev, enm):
        nm = names[n]
        if nm:
            node_names[u][nm] += 1
            node_names[v][nm] += 1

    ids, latlon, display = [], [], []
    for node, ctr in node_names.items():
        if len(ctr) >= 2:
            top2 = [nm for nm, _ in ctr.most_common(2)]
            ids.append(node)
            latlon.append(coords[node])
            display.append(" / ".join(top2))
    return np.array(ids), np.array(latlon), display


class GridIndex:
    """Bucket lat/lon points on a ~50m grid for nearest-neighbor snaps."""

    def __init__(self, latlon):
        self.latlon = latlon
        self.cell = 0.0005
        self.buckets = defaultdict(list)
        for i, (la, lo) in enumerate(latlon):
            self.buckets[(int(la / self.cell), int(lo / self.cell))].append(i)

    def nearest(self, lat, lon, max_m):
        ci, cj = int(lat / self.cell), int(lon / self.cell)
        best, best_d = -1, max_m
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for i in self.buckets.get((ci + di, cj + dj), ()):
                    la, lo = self.latlon[i]
                    d = math.hypot((la - lat) * M_PER_DEG_LAT,
                                   (lo - lon) * M_PER_DEG_LON)
                    if d < best_d:
                        best, best_d = i, d
        return best


def load_nta_index():
    from shapely.geometry import shape
    from shapely.strtree import STRtree
    g = json.load(open(NTA_GEOJSON))
    polys, props = [], []
    for f in g["features"]:
        polys.append(shape(f["geometry"]))
        props.append((f["properties"]["ntaname"], f["properties"]["boroname"]))
    return STRtree(polys), polys, props


def poisson_sf(k, lam):
    """P(X >= k) for X ~ Poisson(lam)."""
    if k <= 0:
        return 1.0
    term = math.exp(-lam)
    cdf = term
    for i in range(1, k):
        term *= lam / i
        cdf += term
    return max(1e-300, 1.0 - cdf)


def poisson_cdf(k, lam):
    term = math.exp(-lam)
    cdf = term
    for i in range(1, k + 1):
        term *= lam / i
        cdf += term
    return min(1.0, cdf)


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    print("loading graph intersections…")
    ids, latlon, display = load_intersection_nodes()
    print(f"  {len(ids)} named-street intersections")
    grid = GridIndex(latlon)

    print("snapping crashes…")
    agg = defaultdict(lambda: {
        "crashes": 0, "pi": 0, "pk": 0, "ci": 0, "ck": 0,
        "f_crashes": 0, "f_pi": 0, "f_pk": 0, "f_ci": 0, "f_ck": 0,
        "f24_crashes": 0, "f24_pi": 0, "f24_pk": 0, "f24_ci": 0, "f24_ck": 0,
        "base_crashes": 0, "base_w": 0.0,
        "recency": 0.0, "recency_ped": 0.0, "recency_cyc": 0.0,
        "night": 0, "streets": Counter(), "boroughs": Counter(),
        "last_date": "",
    })
    n_rows = n_nocoord = n_unsnapped = 0
    with open(RAW / "crashes_pedcyc_2023on.csv") as f:
        for row in csv.DictReader(f):
            n_rows += 1
            try:
                lat, lon = float(row["latitude"]), float(row["longitude"])
            except (ValueError, TypeError):
                n_nocoord += 1
                continue
            if not (NYC_BBOX[0] < lat < NYC_BBOX[1] and NYC_BBOX[2] < lon < NYC_BBOX[3]):
                n_nocoord += 1
                continue
            idx = grid.nearest(lat, lon, SNAP_RADIUS_M)
            if idx < 0:
                n_unsnapped += 1
                continue
            d = datetime.fromisoformat(row["crash_date"].split(".")[0])
            pi, pk = int(row["number_of_pedestrians_injured"] or 0), int(row["number_of_pedestrians_killed"] or 0)
            ci, ck = int(row["number_of_cyclist_injured"] or 0), int(row["number_of_cyclist_killed"] or 0)
            a = agg[idx]
            a["crashes"] += 1
            a["pi"] += pi; a["pk"] += pk; a["ci"] += ci; a["ck"] += ck
            if d >= FRESH12_START:
                a["f_crashes"] += 1
                a["f_pi"] += pi; a["f_pk"] += pk; a["f_ci"] += ci; a["f_ck"] += ck
            if d >= FRESH24_START:
                a["f24_crashes"] += 1
                a["f24_pi"] += pi; a["f24_pk"] += pk; a["f24_ci"] += ci; a["f24_ck"] += ck
            if d <= BASE_END:
                a["base_crashes"] += 1
            age_months = (DATA_END - d).days / 30.44
            decay = 0.5 ** (age_months / HALFLIFE_MONTHS)
            w = (pi + ci) + K_WEIGHT * (pk + ck)
            a["recency"] += decay * w
            a["recency_ped"] += decay * (pi + K_WEIGHT * pk)
            a["recency_cyc"] += decay * (ci + K_WEIGHT * ck)
            hh = int((row["crash_time"] or "12:0").split(":")[0])
            if hh >= 21 or hh < 6:
                a["night"] += 1
            on = (row["on_street_name"] or "").strip()
            # NYPD sometimes records the cross street in off_street_name
            cross = (row["cross_street_name"] or "").strip() or (row["off_street_name"] or "").strip()
            if on and cross:
                a["streets"][f"{on.title()} / {cross.title()}"] += 1
            if row["borough"]:
                a["boroughs"][row["borough"].title()] += 1
            ds = row["crash_date"][:10]
            if ds > a["last_date"]:
                a["last_date"] = ds
    print(f"  {n_rows} rows, {n_nocoord} without usable coords, {n_unsnapped} >40m from any intersection, {n_rows-n_nocoord-n_unsnapped} snapped to {len(agg)} intersections")

    print("assigning neighborhoods…")
    from shapely.geometry import Point
    tree, polys, props = load_nta_index()
    node_nta = {}
    for idx in agg:
        p = Point(latlon[idx][1], latlon[idx][0])
        for j in tree.query(p):
            if polys[j].contains(p):
                node_nta[idx] = props[j]
                break

    base_months = (BASE_END - BASE_START).days / 30.44
    rows = []
    for idx, a in agg.items():
        nta, boro_nta = node_nta.get(idx, ("", ""))
        boro = a["boroughs"].most_common(1)[0][0] if a["boroughs"] else boro_nta
        crash_name = a["streets"].most_common(1)[0][0] if a["streets"] else ""
        # Jeffreys-style pseudo-count keeps zero-baseline intersections finite:
        # a brand-new hotspot is surprising, not infinitely surprising.
        lam = (a["base_crashes"] + 0.5) * 12.0 / base_months
        heat_p = poisson_sf(a["f_crashes"], lam) if a["f_crashes"] >= MIN_FRESH_FOR_TREND else 1.0
        cool_p = poisson_cdf(a["f_crashes"], lam) if a["base_crashes"] >= 8 else 1.0
        rows.append({
            "node_id": int(ids[idx]),
            "lat": round(latlon[idx][0], 6), "lon": round(latlon[idx][1], 6),
            "name_osm": display[idx], "name_crash": crash_name,
            "nta": nta, "borough": boro_nta or boro,
            "crashes": a["crashes"], "ped_inj": a["pi"], "ped_kill": a["pk"],
            "cyc_inj": a["ci"], "cyc_kill": a["ck"],
            "f12_crashes": a["f_crashes"], "f12_ped_inj": a["f_pi"], "f12_ped_kill": a["f_pk"],
            "f12_cyc_inj": a["f_ci"], "f12_cyc_kill": a["f_ck"],
            "f24_crashes": a["f24_crashes"], "f24_ped_inj": a["f24_pi"], "f24_ped_kill": a["f24_pk"],
            "f24_cyc_inj": a["f24_ci"], "f24_cyc_kill": a["f24_ck"],
            "score_ped_w": a["pi"] + K_WEIGHT * a["pk"],
            "score_cyc_w": a["ci"] + K_WEIGHT * a["ck"],
            "score_f12_ped_w": a["f_pi"] + K_WEIGHT * a["f_pk"],
            "score_f12_cyc_w": a["f_ci"] + K_WEIGHT * a["f_ck"],
            "score_f12_all_w": a["f_pi"] + a["f_ci"] + K_WEIGHT * (a["f_pk"] + a["f_ck"]),
            "recency_score": round(a["recency"], 3),
            "recency_ped": round(a["recency_ped"], 3),
            "recency_cyc": round(a["recency_cyc"], 3),
            "base_crashes": a["base_crashes"],
            "base_rate_12mo": round(lam, 3),
            "heat_surprise": round(-math.log10(heat_p), 2),
            "cool_surprise": round(-math.log10(max(cool_p, 1e-300)), 2),
            "night_share": round(a["night"] / a["crashes"], 3),
            "last_crash": a["last_date"],
        })

    rows.sort(key=lambda r: -r["recency_score"])
    fields = list(rows[0].keys())
    with open(OUT / "intersections_master.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {OUT/'intersections_master.csv'} ({len(rows)} intersections)")

    def top(rows_, key, n=25, min_val=1):
        rr = [r for r in rows_ if r[key] >= min_val]
        return sorted(rr, key=lambda r: (-r[key], -r["recency_score"]))[:n]

    lists = {
        "rank_overall_full.csv": top(rows, "crashes"),
        "rank_fresh12_overall.csv": top(rows, "score_f12_all_w"),
        "rank_fresh12_ped.csv": top(rows, "score_f12_ped_w"),
        "rank_fresh12_cyc.csv": top(rows, "score_f12_cyc_w"),
        "rank_recency_weighted.csv": top(rows, "recency_score"),
        "rank_recency_ped.csv": top(rows, "recency_ped"),
        "rank_recency_cyc.csv": top(rows, "recency_cyc"),
        "rank_heating_up.csv": top(rows, "heat_surprise", min_val=0.01),
        "rank_cooling_down.csv": top(rows, "cool_surprise", min_val=0.01),
        "rank_manhattan_fresh12.csv": top([r for r in rows if r["borough"] == "Manhattan"], "score_f12_all_w"),
        "rank_manhattan_recency.csv": top([r for r in rows if r["borough"] == "Manhattan"], "recency_score"),
    }
    for fname, rr in lists.items():
        with open(OUT / fname, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rr)
        print(f"wrote {fname} ({len(rr)})")

    # per-neighborhood #1 by fresh-12 weighted score (min 3 weighted points)
    best_by_nta = {}
    for r in rows:
        if not r["nta"] or r["score_f12_all_w"] < 3:
            continue
        cur = best_by_nta.get(r["nta"])
        if cur is None or (r["score_f12_all_w"], r["recency_score"]) > (cur["score_f12_all_w"], cur["recency_score"]):
            best_by_nta[r["nta"]] = r
    nta_rows = sorted(best_by_nta.values(), key=lambda r: -r["score_f12_all_w"])
    with open(OUT / "nta_number_ones.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(nta_rows)
    print(f"wrote nta_number_ones.csv ({len(nta_rows)} neighborhoods)")


if __name__ == "__main__":
    main()
