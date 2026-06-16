#!/usr/bin/env python3
"""RESUMABLE pull of NYC planimetric roadbed + sidewalk (full CityEdit nyc bbox)
plus the osmnx drive centerline graph. Each download page is checkpointed to disk,
so an interruption never loses more than the in-flight page.

Output dir defaults to ./output next to this script; override with BLOCKS_OUT env var.
"""
import json, os, time, pickle, urllib.parse, urllib.request
from pathlib import Path

OUT = Path(os.environ.get("BLOCKS_OUT", Path(__file__).resolve().parent / "output"))
OUT.mkdir(parents=True, exist_ok=True)

S, W, N, E = 40.4774, -74.2591, 40.9176, -73.7004          # CityEdit nyc bbox (s,w,n,e)
BOX = f"{N},{W},{S},{E}"                                    # within_box(latNW,lonNW,latSE,lonSE)
DATASETS = {"roadbed": "i36f-5ih7", "sidewalk": "52n9-sdep"}  # NYC Open Data Socrata ids
PAGE = 10000   # smaller pages = more frequent checkpoints

def pagedir(name):
    d = OUT / f"pages_{name}"; d.mkdir(exist_ok=True); return d

def pull(name, ds):
    final = OUT / f"nyc_{name}_full.geojson"
    if final.exists() and final.stat().st_size > 1000:
        print(f"[{name}] final cached, skip", flush=True); return
    pd = pagedir(name)
    offset = 0
    while True:
        pf = pd / f"page_{offset:07d}.json"
        if pf.exists():
            if len(json.load(open(pf))) < PAGE: break
            offset += PAGE; continue
        q = (f"https://data.cityofnewyork.us/resource/{ds}.geojson?"
             f"$where=within_box(the_geom,{urllib.parse.quote(BOX)})"
             f"&$order=:id&$limit={PAGE}&$offset={offset}")
        feats = None
        for attempt in range(5):
            try:
                with urllib.request.urlopen(q, timeout=300) as r:
                    feats = json.load(r).get("features", [])
                break
            except Exception as ex:
                print(f"[{name}] offset {offset} try {attempt}: {ex}", flush=True); time.sleep(6)
        if feats is None:
            print(f"[{name}] FAILED at offset {offset}; rerun to resume", flush=True); raise SystemExit(2)
        tmp = pf.with_suffix(".tmp"); json.dump(feats, open(tmp, "w")); os.replace(tmp, pf)
        print(f"[{name}] saved page offset {offset}: {len(feats)} feats", flush=True)
        if len(feats) < PAGE: break
        offset += PAGE
    feats, o = [], 0
    while True:
        pf = pd / f"page_{o:07d}.json"
        if not pf.exists(): break
        page = json.load(open(pf)); feats.extend(page)
        if len(page) < PAGE: break
        o += PAGE
    tmp = final.with_suffix(".tmp")
    json.dump({"type": "FeatureCollection", "features": feats}, open(tmp, "w")); os.replace(tmp, final)
    print(f"[{name}] WROTE {final.name}: {len(feats)} features", flush=True)

def build_graph():
    gp = OUT / "nyc_drive_consolidated.pkl"
    if gp.exists(): print("[graph] cached, skip", flush=True); return
    import warnings; warnings.filterwarnings("ignore"); import osmnx as ox
    print("[graph] downloading NYC drive network...", flush=True)
    G = ox.graph_from_bbox(bbox=(W, S, E, N), network_type="drive", simplify=True, truncate_by_edge=True)
    Gc = ox.consolidate_intersections(ox.project_graph(G, to_crs=32618), tolerance=12,
                                      rebuild_graph=True, dead_ends=False)
    tmp = gp.with_suffix(".tmp"); pickle.dump(Gc, open(tmp, "wb")); os.replace(tmp, gp)
    print(f"[graph] WROTE {gp.name}: {len(Gc.nodes())} nodes", flush=True)

if __name__ == "__main__":
    for name, ds in DATASETS.items(): pull(name, ds)
    build_graph()
    print("ALL PULLS DONE", flush=True)
