#!/usr/bin/env python3
"""
Extract everything the wait-time model needs from the NYC OSM extract:
signalised junctions, the streets meeting at them, and the POI field around
them.

Two passes over the PBF (nodes are written before ways, so way membership
can't be known while the nodes stream by):
  pass 1  ways  -> drivable street geometry + tags, and which node ids matter
  pass 2  nodes -> locations for those ids, signal/crossing tags, POI points

Output: data/raw/wait/osm_intersections.json.gz
  junctions: [{id, lat, lon, signal, streets:[{name, highway, lanes, oneway}], ...}]
  pois:      [[lat, lon], ...]        (shop/amenity/office/tourism points)
  crossings: [[lat, lon, signalised]] (highway=crossing nodes)

Usage: python scripts/extract_osm_intersections.py [--pbf …] [--out …]
"""

import argparse
import gzip
import json
from collections import defaultdict
from pathlib import Path

import osmium

REPO = Path(__file__).resolve().parent.parent
DEFAULT_PBF = REPO / "server" / "osm_data" / "nyc" / "source.osm.pbf"
DEFAULT_OUT = REPO / "data" / "raw" / "wait" / "osm_intersections.json.gz"

# Street classes a pedestrian has to wait to cross. Ordered worst-first; the
# index doubles as the "how big is this road" rank used by the green-split
# model.
ROAD_CLASSES = [
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "living_street",
]
LINK_OF = {f"{c}_link": c for c in ROAD_CLASSES}

# Points that mean "people have a reason to be here". Values are excluded
# where the tag is essentially street furniture rather than a destination.
POI_KEYS = ("shop", "office", "tourism")
POI_AMENITIES_SKIP = {
    "bench", "waste_basket", "bicycle_parking", "parking", "parking_space",
    "drinking_water", "shelter", "recycling", "post_box", "telephone",
    "clock", "fountain", "hunting_stand", "grit_bin", "parking_entrance",
    "charging_station", "car_sharing", "bicycle_repair_station",
}


def road_class(tags):
    hw = tags.get("highway")
    if hw in ROAD_CLASSES:
        return hw
    return LINK_OF.get(hw)


def parse_lanes(tags, cls):
    """Total through lanes across the carriageway, tags first then a
    class-typical default. NYC OSM tags `lanes` on most arterials and almost
    no residential streets."""
    for key in ("lanes",):
        v = tags.get(key)
        if v:
            try:
                return max(1.0, min(12.0, float(v.split(";")[0])))
            except ValueError:
                pass
    return {
        "motorway": 6.0, "trunk": 5.0, "primary": 4.0, "secondary": 3.0,
        "tertiary": 2.0, "unclassified": 2.0, "residential": 2.0,
        "living_street": 1.0,
    }[cls]


class WayPass(osmium.SimpleHandler):
    """Pass 1: drivable ways only. Records each way's tags plus its node list,
    and counts how many ways touch each node so pass 2 knows which node
    locations to keep."""

    def __init__(self):
        super().__init__()
        self.ways = []
        self.node_ways = defaultdict(list)  # node id -> way indices

    def way(self, w):
        cls = road_class(w.tags)
        if cls is None:
            return
        if w.tags.get("area") == "yes":
            return
        refs = [n.ref for n in w.nodes]
        if len(refs) < 2:
            return
        idx = len(self.ways)
        self.ways.append({
            "name": (w.tags.get("name") or "").strip(),
            "cls": cls,
            "lanes": parse_lanes(w.tags, cls),
            "oneway": w.tags.get("oneway") in ("yes", "-1", "true", "1"),
            "n": len(refs),
        })
        for r in refs:
            self.node_ways[r].append(idx)


class NodePass(osmium.SimpleHandler):
    """Pass 2: locations for the node ids pass 1 flagged, plus signal and
    crossing tags, plus every POI point in the extract."""

    def __init__(self, wanted):
        super().__init__()
        self.wanted = wanted
        self.loc = {}
        self.signals = set()
        self.crossings = []   # (lat, lon, signalised)
        self.pois = []        # (lat, lon)
        self.button = set()

    def node(self, n):
        tags = n.tags
        hw = tags.get("highway")
        lat, lon = n.location.lat, n.location.lon

        if hw == "crossing" or tags.get("crossing"):
            sig = (tags.get("crossing") in ("traffic_signals", "signals")
                   or tags.get("crossing:signals") == "yes"
                   or hw == "traffic_signals")
            self.crossings.append((round(lat, 6), round(lon, 6), bool(sig)))
            if tags.get("button_operated") == "yes":
                self.button.add(n.id)

        if n.id in self.wanted:
            self.loc[n.id] = (round(lat, 6), round(lon, 6))
            if hw in ("traffic_signals", "traffic_signals;crossing"):
                self.signals.add(n.id)

        if any(k in tags for k in POI_KEYS) or (
                "amenity" in tags and tags.get("amenity") not in POI_AMENITIES_SKIP):
            self.pois.append((round(lat, 5), round(lon, 5)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pbf", type=Path, default=DEFAULT_PBF)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    print(f"pass 1/2  ways   {args.pbf}")
    wp = WayPass()
    wp.apply_file(str(args.pbf))
    print(f"  {len(wp.ways):,} drivable ways, {len(wp.node_ways):,} nodes touched")

    # A junction is any node where two or more distinct ways meet. Ways split
    # for tagging reasons meet end-to-end too, so require at least two
    # DIFFERENT street names (or a genuinely unnamed way) before calling it an
    # intersection — done after locations land, in pass 3.
    wanted = {nid for nid, ws in wp.node_ways.items() if len(ws) >= 2}
    print(f"  {len(wanted):,} candidate junction nodes")

    print("pass 2/2  nodes")
    np_ = NodePass(wanted)
    np_.apply_file(str(args.pbf))
    print(f"  {len(np_.loc):,} located, {len(np_.signals):,} tagged traffic_signals, "
          f"{len(np_.crossings):,} crossings, {len(np_.pois):,} POIs")

    junctions = []
    for nid in wanted:
        if nid not in np_.loc:
            continue
        streets = {}
        for wi in wp.node_ways[nid]:
            w = wp.ways[wi]
            key = w["name"] or f"~{w['cls']}"
            s = streets.get(key)
            if s is None:
                streets[key] = s = {"name": w["name"], "cls": w["cls"],
                                    "lanes": w["lanes"], "oneway": w["oneway"],
                                    "w": []}
            elif ROAD_CLASSES.index(w["cls"]) < ROAD_CLASSES.index(s["cls"]):
                s["cls"], s["oneway"] = w["cls"], w["oneway"]
            s["lanes"] = max(s["lanes"], w["lanes"])
            # Way ids let the model tell a divided boulevard's separate
            # carriageways from one street simply split for tagging: the
            # latter's ways chain through shared nodes, the former's don't.
            s["w"].append(wi)
        if len(streets) < 2:
            continue
        lat, lon = np_.loc[nid]
        junctions.append({
            "id": nid, "lat": lat, "lon": lon,
            "signal": nid in np_.signals,
            "streets": list(streets.values()),
        })
    print(f"  {len(junctions):,} junctions with 2+ distinct streets "
          f"({sum(j['signal'] for j in junctions):,} signalised)")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.out, "wt") as f:
        json.dump({"junctions": junctions, "pois": np_.pois,
                   "crossings": np_.crossings}, f)
    print(f"-> {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
