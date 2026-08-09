#!/usr/bin/env python3
"""
Rank NYC's signalised intersections by how much human time they burn: how
long a pedestrian waits to cross, multiplied by how many pedestrians are
actually there to wait.

    lost person-hours / weekday  =  Σ  crossers · average wait

Two models feed it, both built only from public data.

WAIT — no agency publishes NYC signal timing, so the wait is *estimated* from
signal engineering, not observed:

  · Cycle length C from the road hierarchy at the corner. NYC DOT runs 60 /
    90 / 120-second cycles, longest on the widest arterials.
  · Green split. A pedestrian crossing street A walks parallel to street B and
    goes when B goes, so A's crossing gets B's share of the green. Share is
    apportioned by lanes × class, clamped to [0.30, 0.62] — a side street
    never gets nothing, an arterial never gets everything.
  · Average wait for random arrival at a fixed-time signal (the standard
    uniform-arrival result):        d = (C − g)² / (2C)
  · Staged crossings. Boulevards with medians and service roads appear in OSM
    as several signalised nodes 20-50 m apart; each extra node is another
    island to wait on, and (absent pedestrian-favourable offsets) each costs
    another d in expectation.
  · Exclusive pedestrian phases (Barnes Dance) stop all traffic, so the walk
    interval is sized to the crossing and nothing more — safer, and a longer
    wait. Modelled from the DOT's own location list.
  · Where a crossing is too wide to clear in the green the model computed and
    there is no median to stop on, the green is raised to the MUTCD 3.5 ft/s
    requirement instead — DOT must give the time, so the model must too.

VOLUME — a log-linear model fitted on the 95 street corners where DOT actually
counts pedestrians (its 114 sites less the river crossings and the greenway,
which are not street corners), from two citywide predictors:

  · subway entries near the corner (MTA, distance-decayed)
  · the DOT Pedestrian Mobility Plan demand class of each street

Leave-one-out R² ≈ 0.71, so the model gets a corner's volume right to roughly
a third either way. Two caveats it cannot fix: DOT counts retail corridors,
so the fit is trained where the busy corners are and extrapolates worst on
quiet streets; and subway access carries most of the signal, so volume is
least trustworthy far from the subway network — much of Staten Island and
eastern Queens.

Both models are estimates. The ranking they produce is a way to start an
argument about which corners waste the most of the city's time, not a
measurement of it.

Usage:
  python scripts/build_wait_rankings.py                 # writes the CSVs
  python scripts/build_wait_rankings.py --inspect "Queens Boulevard"
"""

import argparse
import csv
import gzip
import json
import math
import re
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "data" / "raw"
WAIT = RAW / "wait"
OUT = REPO / "data" / "analysis"

M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = 84_400.0  # at NYC's latitude

# ---------------------------------------------------------------- wait model

# How much of a signal's capacity a street commands, per lane. A trunk lane
# gets more green than a residential lane carrying the same count.
CLASS_WEIGHT = {
    "motorway": 1.7, "trunk": 1.6, "primary": 1.5, "secondary": 1.35,
    "tertiary": 1.2, "unclassified": 1.05, "residential": 1.0,
    "living_street": 0.8,
}
BIG_CLASSES = {"motorway", "trunk", "primary", "secondary"}

LANE_M = 3.05          # travel lane width
PARKING_M = 2.4        # curb lane, one per side
MEDIAN_M = 6.0         # island between carriageways
WALK_SPEED = 1.07      # 3.5 ft/s, the MUTCD design pedestrian
WALK_INTERVAL = 7.0    # the WALK indication before the countdown starts
LOST_TIME = 4.0        # yellow + all-red eaten out of each phase
SPLIT_MIN, SPLIT_MAX = 0.30, 0.62
CLUSTER_M = 45.0       # signalised nodes this close are one intersection
MAX_STAGES = 4

# Pedestrians walking past a corner along street B who actually cross street
# A. Not all of them — some turn the corner or enter a door mid-block.
THROUGH_SHARE = 0.65

# The count programme samples 7-9 AM and 4-7 PM. Those five hours are about
# 46% of a 7 AM - 7 PM weekday in an urban pedestrian profile, so the reported
# volume is a 12-hour weekday day, not a 24-hour one.
COUNT_HOURS_SHARE = 0.46


def norm_street(name):
    """Canonical form for matching OSM names against DOT's. Suffixes expand
    only in final position ('ST NICHOLAS AVE' keeps its saint), directions
    only in leading position, and ordinals lose their th/st/nd/rd."""
    s = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper())
    toks = s.split()
    if not toks:
        return ""
    lead = {"E": "EAST", "W": "WEST", "N": "NORTH", "S": "SOUTH"}
    tail = {
        "ST": "STREET", "STR": "STREET", "AVE": "AVENUE", "AV": "AVENUE",
        "RD": "ROAD", "BLVD": "BOULEVARD", "BLV": "BOULEVARD",
        "PKWY": "PARKWAY", "PKY": "PARKWAY", "PWY": "PARKWAY",
        "PL": "PLACE", "DR": "DRIVE", "LN": "LANE", "CT": "COURT",
        "TER": "TERRACE", "TERR": "TERRACE", "SQ": "SQUARE",
        "TPKE": "TURNPIKE", "TPK": "TURNPIKE", "EXPY": "EXPRESSWAY",
        "EXPWY": "EXPRESSWAY", "HWY": "HIGHWAY", "BRG": "BRIDGE",
        "CIR": "CIRCLE", "PLZ": "PLAZA", "WY": "WAY",
    }
    if toks[0] in lead:
        toks[0] = lead[toks[0]]
    if toks[-1] in tail:
        toks[-1] = tail[toks[-1]]
    toks = [re.sub(r"^(\d+)(ST|ND|RD|TH)$", r"\1", t) for t in toks]
    return " ".join(toks)


def cycle_length(cls, lanes):
    """NYC DOT runs 60 / 90 / 120-second cycles; the widest arterials get the
    longest ones so their green can serve the traffic. `lanes` is the whole
    cross-section — a boulevard's service roads count."""
    if lanes >= 8 or (cls in ("motorway", "trunk") and lanes >= 6):
        return 120.0
    if cls in BIG_CLASSES:
        return 90.0
    return 60.0


def street_weight(s):
    return s["lanes"] * CLASS_WEIGHT[s["cls"]]


def crossing_width(street):
    """Kerb-to-kerb metres across the whole street: every travel lane, a parked
    car's width at the two outer kerbs, and an island between carriageways."""
    stages = street.get("stages", 1)
    return street["lanes"] * LANE_M + 2 * PARKING_M + (stages - 1) * MEDIAN_M


def crossing_wait(crossed, along, barnes):
    """Average seconds a pedestrian spends standing before crossing `crossed`,
    walking along `along`. Returns (wait_s, cycle_s, green_s)."""
    stages = crossed.get("stages", 1)
    C = cycle_length(crossed["cls"], crossed["lanes"])

    w_cross, w_along = street_weight(crossed), street_weight(along)
    split = min(SPLIT_MAX, max(SPLIT_MIN, w_along / (w_cross + w_along)))
    g = C * split - LOST_TIME

    stage_m = crossing_width(crossed) / stages
    needed = stage_m / WALK_SPEED + WALK_INTERVAL

    if barnes:
        # All traffic stops, so the walk interval is exactly what the crossing
        # needs and no more — the rest of the cycle belongs to cars.
        g = needed
    elif stages == 1 and needed > g:
        # No island to break the crossing on, so DOT has to lengthen the
        # pedestrian phase to the 3.5 ft/s requirement.
        g = needed

    g = max(5.0, min(g, C - LOST_TIME))
    per_stage = (C - g) ** 2 / (2 * C)
    return stages * per_stage, C, g


# ------------------------------------------------------------- spatial index

class Grid:
    """Uniform lat/lon bucket index for radius queries."""

    def __init__(self, cell_m=200.0):
        self.cell = cell_m
        self.dlat = cell_m / M_PER_DEG_LAT
        self.dlon = cell_m / M_PER_DEG_LON
        self.buckets = defaultdict(list)

    def key(self, lat, lon):
        return int(lat / self.dlat), int(lon / self.dlon)

    def add(self, lat, lon, payload):
        self.buckets[self.key(lat, lon)].append((lat, lon, payload))

    def near(self, lat, lon, radius_m):
        ci, cj = self.key(lat, lon)
        span_i = int(radius_m / self.cell) + 1
        span_j = int(radius_m / self.cell) + 1
        for di in range(-span_i, span_i + 1):
            for dj in range(-span_j, span_j + 1):
                for la, lo, p in self.buckets.get((ci + di, cj + dj), ()):
                    d = math.hypot((la - lat) * M_PER_DEG_LAT,
                                   (lo - lon) * M_PER_DEG_LON)
                    if d <= radius_m:
                        yield d, p


# ------------------------------------------------------------------- loading

def load_osm():
    with gzip.open(WAIT / "osm_intersections.json.gz", "rt") as f:
        return json.load(f)


def load_nta():
    from shapely.geometry import shape
    from shapely.strtree import STRtree
    g = json.load(open(RAW / "nta2020.geojson"))
    polys = [shape(f["geometry"]) for f in g["features"]]
    props = [(f["properties"]["ntaname"], f["properties"]["boroname"])
             for f in g["features"]]
    return STRtree(polys), polys, props


def load_demand_index():
    """Grid of Pedestrian Mobility Plan segment vertices -> (norm name, rank).
    Rank is DOT's 1 (Global, busiest) .. 5 (Baseline)."""
    g = json.loads((WAIT / "ped_demand.geojson").read_text())
    grid = Grid(120.0)
    for f in g["features"]:
        p = f["properties"]
        try:
            rank = int(float(p["rank"]))
        except (KeyError, TypeError, ValueError):
            continue
        name = norm_street(p.get("street"))
        geom = f["geometry"]
        if not geom:
            continue
        lines = (geom["coordinates"] if geom["type"] == "MultiLineString"
                 else [geom["coordinates"]])
        for line in lines:
            # Vertices are dense; every third is plenty for a 60 m query.
            for lon, lat in line[::3] or line[:1]:
                grid.add(lat, lon, (name, rank))
    return grid


def load_subway():
    grid = Grid(400.0)
    for s in json.loads((WAIT / "subway_daily.json").read_text()):
        grid.add(s["lat"], s["lon"], s["daily_entries"])
    return grid


def load_poi(pois):
    grid = Grid(200.0)
    for lat, lon in pois:
        grid.add(lat, lon, 1)
    return grid


def load_point_flags(fname, key_geom="the_geom"):
    grid = Grid(120.0)
    for r in json.loads((WAIT / fname).read_text()):
        geom = r.get(key_geom)
        if not geom or not geom.get("coordinates"):
            continue
        lon, lat = geom["coordinates"][:2]
        grid.add(lat, lon, True)
    return grid


# ----------------------------------------------------------- volume features

SUBWAY_DECAY_M = 300.0
SUBWAY_RADIUS_M = 900.0
POI_RADIUS_M = 200.0


def subway_access(grid, lat, lon):
    """Distance-decayed daily subway entries around a point, doubled because
    every entry is matched by an exit somewhere and both walk on a street."""
    total = 0.0
    for d, entries in grid.near(lat, lon, SUBWAY_RADIUS_M):
        total += entries * math.exp(-d / SUBWAY_DECAY_M)
    return 2.0 * total


def poi_count(grid, lat, lon):
    return sum(1 for _ in grid.near(lat, lon, POI_RADIUS_M))


DEMAND_SCORE = {1: 5.0, 2: 4.0, 3: 3.0, 4: 2.0, 5: 1.0}


def demand_for(grid, lat, lon, name):
    """DOT demand score for a named street at a point: prefer a segment whose
    name matches, fall back to the busiest segment at the corner."""
    want = norm_street(name)
    best_named, best_any = None, None
    for d, (nm, rank) in grid.near(lat, lon, 70.0):
        if best_any is None or rank < best_any:
            best_any = rank
        if want and nm == want and (best_named is None or rank < best_named):
            best_named = rank
    rank = best_named if best_named is not None else best_any
    return DEMAND_SCORE.get(rank, 1.5)


# --------------------------------------------------------------- calibration

COUNT_SURVEYS = [("may25_am", "may25_pm"), ("oct25_am", "oct25_pm"),
                 ("may26_am", "may26_pm")]

# 14 of the 114 count sites are river crossings and the Hudson River Greenway.
# They are counting a very different thing — a walk or ride with no
# alternative for a mile in either direction — and the model is only ever
# asked about street corners, so they are not part of the training set.
NOT_A_STREET = re.compile(
    r"bridge|greenway|path|boardwalk|ferry|promenade", re.I)


def observed_volume(row):
    """12-hour weekday pedestrians at a count site, from the AM (7-9) and PM
    (4-7) weekday screenlines. The Saturday midday column is a different day
    and is deliberately left out."""
    vals = []
    for am, pm in COUNT_SURVEYS:
        try:
            v = float(row[am]) + float(row[pm])
        except (KeyError, TypeError, ValueError):
            continue
        if v > 0:
            vals.append(v)
    if not vals:
        return None
    return (sum(vals) / len(vals)) / COUNT_HOURS_SHARE


def design_matrix(sites):
    """log V = b0 + b1·log1p(subway/1e3) + b2·demand.

    POI density was tested as a third predictor and dropped: it correlates
    0.55 with subway access, adds 0.001 to out-of-sample R², and takes a
    *negative* coefficient once subway access is in — an artefact of
    collinearity, not a finding about shops. Both surviving terms are
    positive and mean what they say."""
    import numpy as np
    return np.array([[1.0, math.log1p(s["subway"] / 1e3), s["demand"]]
                     for s in sites])


def fit_volume_model(sites):
    import numpy as np
    X = design_matrix(sites)
    y = np.array([math.log(s["observed"]) for s in sites])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ beta
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot
    # Spearman without scipy: correlation of ranks.
    def ranks(a):
        order = np.argsort(a)
        r = np.empty(len(a))
        r[order] = np.arange(len(a))
        return r
    ry, rp = ranks(y), ranks(pred)
    rho = float(np.corrcoef(ry, rp)[0, 1])
    resid = float(np.sqrt(ss_res / len(y)))
    return beta, {"r2": r2, "spearman": rho, "log_rmse": resid, "n": len(y)}


def leave_one_out_r2(sites):
    """Honest out-of-sample score: refit without each site and predict it. The
    in-sample R² of a four-parameter fit on ~100 points flatters itself."""
    import numpy as np
    X = design_matrix(sites)
    y = np.array([math.log(s["observed"]) for s in sites])
    pred = np.empty(len(sites))
    for i in range(len(sites)):
        keep = np.ones(len(sites), bool)
        keep[i] = False
        b, *_ = np.linalg.lstsq(X[keep], y[keep], rcond=None)
        pred[i] = X[i] @ b
    return float(1 - ((y - pred) ** 2).sum() / ((y - y.mean()) ** 2).sum())


def predict_volume(beta, subway, demand):
    return math.exp(beta[0] + beta[1] * math.log1p(subway / 1e3)
                    + beta[2] * demand)


# ---------------------------------------------------------------- clustering

def cluster_signals(nodes):
    """Union-find over signalised junction nodes within CLUSTER_M. A boulevard
    crossing shows up in OSM as several nodes in a row; they are one
    intersection with several stages, not several intersections."""
    parent = list(range(len(nodes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    grid = Grid(CLUSTER_M)
    for i, n in enumerate(nodes):
        grid.add(n["lat"], n["lon"], i)
    for i, n in enumerate(nodes):
        for _, j in grid.near(n["lat"], n["lon"], CLUSTER_M):
            if j != i:
                union(i, j)

    groups = defaultdict(list)
    for i in range(len(nodes)):
        groups[find(i)].append(i)
    return list(groups.values())


CLASS_ORDER = list(CLASS_WEIGHT)


def merge_streets(members):
    """Union the streets across a cluster's nodes and split each into its
    *carriageways*.

    This is the crux of the whole model. OSM splits a street into many ways,
    for tagging reasons as much as for medians, so counting ways or counting
    nodes both overstate how divided a street is. What separates the two cases
    is topology: the ways of one undivided street chain together through shared
    nodes, while a boulevard's carriageways never touch each other here. So
    group each street's ways into connected components — one component is one
    carriageway, one thing to walk across before the next island.

    Returns per street: its class, its carriageways (each with the node it
    meets and its lane count), and total lanes across all of them."""
    by_name = {}
    for i, n in enumerate(members):
        for s in n["streets"]:
            key = norm_street(s["name"]) or f"~{s['cls']}"
            cur = by_name.get(key)
            if cur is None:
                cur = by_name[key] = {
                    "name": s["name"], "cls": s["cls"],
                    "named": not key.startswith("~"), "ways": [],
                }
            if CLASS_ORDER.index(s["cls"]) < CLASS_ORDER.index(cur["cls"]):
                cur["cls"] = s["cls"]
            cur["ways"].append({"ids": set(s["w"]), "node": i,
                                "lanes": s["lanes"]})

    for st in by_name.values():
        st["carriageways"] = connected_carriageways(st["ways"])
        st["lanes"] = sum(c["lanes"] for c in st["carriageways"])
    return list(by_name.values())


def connected_carriageways(ways):
    """Union ways that share an OSM way id into carriageways. Each carriageway
    reports the cluster nodes it touches and its widest lane count."""
    parent = list(range(len(ways)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen = {}
    for i, w in enumerate(ways):
        for wid in w["ids"]:
            j = seen.setdefault(wid, i)
            ra, rb = find(i), find(j)
            if ra != rb:
                parent[rb] = ra

    groups = defaultdict(list)
    for i in range(len(ways)):
        groups[find(i)].append(i)
    return [{"nodes": {ways[i]["node"] for i in g},
             "lanes": max(ways[i]["lanes"] for i in g)}
            for g in groups.values()]


def crossing_stages(crossed, along):
    """The carriageways of `crossed` a pedestrian walking along `along` has to
    get over — the ones that meet a node `along` also reaches. Carriageways
    further up the street are its neighbouring corners, not islands in this
    crossing; counting them is what once turned the West Village's cluster of
    ordinary corners into a fake four-stage boulevard."""
    reach = set().union(*(c["nodes"] for c in along["carriageways"]))
    hit = [c for c in crossed["carriageways"] if c["nodes"] & reach]
    return hit or crossed["carriageways"][:1]


# --------------------------------------------------------------------- build

def build(args):
    import numpy as np  # noqa: F401  (fit_volume_model needs it)

    print("loading OSM extract…")
    osm = load_osm()
    signals = [j for j in osm["junctions"] if j["signal"]]
    print(f"  {len(signals):,} signalised junction nodes")

    print("loading NTA polygons…")
    tree, polys, props = load_nta()
    from shapely.geometry import Point

    print("loading pedestrian demand (127k segments)…")
    demand_grid = load_demand_index()
    print("loading subway + POI + signal-treatment layers…")
    subway_grid = load_subway()
    poi_grid = load_poi(osm["pois"])
    barnes_grid = load_point_flags("barnes_dance.json")
    lpi_grid = load_point_flags("lpi.json")

    # -- calibrate the volume model on the DOT count sites -------------------
    print("calibrating volume model on DOT count sites…")
    sites, skipped = [], 0
    for row in json.loads((WAIT / "ped_counts.json").read_text()):
        geom = row.get("the_geom")
        if not geom:
            continue
        obs = observed_volume(row)
        if not obs:
            continue
        where = " ".join(str(row.get(k, "")) for k in
                         ("street_nam", "from_stree", "to_street"))
        if NOT_A_STREET.search(where):
            skipped += 1
            continue
        lon, lat = geom["coordinates"][:2]
        sites.append({
            "name": row.get("street_nam", ""), "lat": lat, "lon": lon,
            "observed": obs,
            "subway": subway_access(subway_grid, lat, lon),
            "poi": poi_count(poi_grid, lat, lon),
            "demand": demand_for(demand_grid, lat, lon, row.get("street_nam")),
        })
    beta, fit = fit_volume_model(sites)
    fit["loo_r2"] = leave_one_out_r2(sites)
    print(f"  n={fit['n']} (+{skipped} bridge/greenway sites held out)  "
          f"R²={fit['r2']:.3f}  leave-one-out R²={fit['loo_r2']:.3f}  "
          f"Spearman={fit['spearman']:.3f}  log-RMSE={fit['log_rmse']:.3f}")
    print(f"  log V = {beta[0]:.3f} + {beta[1]:.3f}·log1p(subway/1k) "
          f"+ {beta[2]:.3f}·demand")

    # -- assemble intersections ---------------------------------------------
    print("clustering signalised nodes into intersections…")
    clusters = cluster_signals(signals)
    print(f"  {len(clusters):,} intersections from {len(signals):,} nodes")

    rows = []
    for members_idx in clusters:
        members = [signals[i] for i in members_idx]
        lat = sum(m["lat"] for m in members) / len(members)
        lon = sum(m["lon"] for m in members) / len(members)

        hits = tree.query(Point(lon, lat))
        nta = boro = ""
        for j in hits:
            if polys[j].contains(Point(lon, lat)):
                nta, boro = props[j]
                break
        if not nta:
            continue  # outside the five boroughs

        streets = merge_streets(members)
        named = [s for s in streets if s["named"]]
        if len(named) < 2:
            continue
        named.sort(key=street_weight, reverse=True)
        major, minor = named[0], named[1]

        # A staged crossing walks over every carriageway of the major street,
        # so its width is the sum of them, not the widest one.
        hit = crossing_stages(major, minor)[:MAX_STAGES]
        major = dict(major, stages=len(hit),
                     lanes=sum(c["lanes"] for c in hit))
        minor = dict(minor, stages=len(crossing_stages(minor, major)[:MAX_STAGES]))

        barnes = any(True for _ in barnes_grid.near(lat, lon, 60.0))
        lpi = any(True for _ in lpi_grid.near(lat, lon, 60.0))

        wait_major, C, g_major = crossing_wait(major, minor, barnes)
        wait_minor, _, _ = crossing_wait(minor, major, barnes)

        subway = subway_access(subway_grid, lat, lon)
        poi = poi_count(poi_grid, lat, lon)
        vol_major = predict_volume(
            beta, subway, demand_for(demand_grid, lat, lon, major["name"]))
        vol_minor = predict_volume(
            beta, subway, demand_for(demand_grid, lat, lon, minor["name"]))

        # People crossing the major street are the ones walking along the
        # minor one, and vice versa.
        cross_major = vol_minor * THROUGH_SHARE
        cross_minor = vol_major * THROUGH_SHARE
        lost_h = (cross_major * wait_major + cross_minor * wait_minor) / 3600.0

        rows.append({
            "lat": round(lat, 6), "lon": round(lon, 6),
            "name": f"{major['name']} / {minor['name']}",
            "major": major["name"], "minor": minor["name"],
            "nta": nta, "borough": boro,
            "nodes": len(members), "stages": major["stages"],
            "major_cls": major["cls"], "major_lanes": round(major["lanes"], 1),
            "minor_cls": minor["cls"], "minor_lanes": round(minor["lanes"], 1),
            "cycle_s": round(C), "green_s": round(g_major, 1),
            "cross_m": round(crossing_width(major), 1),
            "wait_major_s": round(wait_major, 1),
            "wait_minor_s": round(wait_minor, 1),
            "barnes": int(barnes), "lpi": int(lpi),
            "subway_access": round(subway),
            "poi_200m": poi,
            "peds_cross_major": round(cross_major),
            "peds_cross_minor": round(cross_minor),
            "peds_day": round(cross_major + cross_minor),
            "lost_hours_day": round(lost_h, 1),
        })

    rows.sort(key=lambda r: -r["lost_hours_day"])
    for i, r in enumerate(rows, 1):
        r["rank_lost"] = i
    by_wait = sorted(rows, key=lambda r: -r["wait_major_s"])
    for i, r in enumerate(by_wait, 1):
        r["rank_wait"] = i

    OUT.mkdir(parents=True, exist_ok=True)
    master = OUT / "wait_intersections.csv"
    with open(master, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {master} ({len(rows):,} intersections)")

    (OUT / "wait_model_fit.json").write_text(json.dumps({
        "volume_model": {"beta": list(beta), **fit,
                         "form": "log V = b0 + b1*log1p(subway/1e3) "
                                 "+ b2*demand"},
        "count_sites": len(sites),
        "intersections": len(rows),
        "citywide_lost_hours_per_weekday": round(
            sum(r["lost_hours_day"] for r in rows)),
    }, indent=2))

    print(f"\ncitywide: {sum(r['lost_hours_day'] for r in rows):,.0f} "
          f"person-hours per weekday spent waiting to cross\n")
    print(f"{'#':>3}  {'lost h/day':>10} {'wait':>6} {'peds/day':>9}  name")
    for r in rows[:25]:
        print(f"{r['rank_lost']:>3}  {r['lost_hours_day']:>10.1f} "
              f"{r['wait_major_s']:>5.0f}s {r['peds_day']:>9,}  "
              f"{r['name']} ({r['borough']})")
    print(f"\nlongest estimated waits:")
    for r in by_wait[:15]:
        print(f"  {r['wait_major_s']:>5.0f}s  {r['stages']}-stage  "
              f"{r['name']} ({r['borough']})")


def inspect(args):
    rows = list(csv.DictReader(open(OUT / "wait_intersections.csv")))
    q = args.inspect.lower()
    hits = [r for r in rows if q in r["name"].lower()][:20]
    for r in hits:
        print(f"#{r['rank_lost']:>5} lost={r['lost_hours_day']:>7}h  "
              f"wait={r['wait_major_s']:>5}s ({r['stages']} stage, "
              f"C={r['cycle_s']}s g={r['green_s']}s, {r['cross_m']}m)  "
              f"peds={r['peds_day']:>8}  {r['name']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inspect", help="print model internals for matching names")
    args = ap.parse_args()
    if args.inspect:
        inspect(args)
    else:
        build(args)


if __name__ == "__main__":
    main()
