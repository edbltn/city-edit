#!/usr/bin/env python3
"""PROD repair for the EV/LES waterfront hairpin cast (proposal fa8b70e11).

1. direction=0 clears voter dotproj:b4720d0ae0faba14's 438 'Add traffic
   calming' votes along the hairpin (edge ids captured from prod graph-votes
   rev 61 on 2026-08-01 — prod topology 6a07332e7ea729fc).
2. Recasts the project as a 'Street redesign' point pin at Delancey St &
   the East River Park esplanade (40.7155,-73.9765), mid-study-area.

Run:  python3 repair_prod_hairpin.py            (defaults to prod)
Verify after: top proposal should become #128218e4 (Linden Boulevard).
"""
import json, os, urllib.request

BASE = os.environ.get("BASE_URL", "https://cityedit.org")
EDGES = json.load(open(os.path.join(os.path.dirname(__file__), "prod-hairpin-edges.json")))
assert len(EDGES) == 438

def vote(payload):
    req = urllib.request.Request(f"{BASE}/api/vote",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=180))

base = {"map": "nyc-proposals", "mode": "walk",
        "voter_id": "dotproj:b4720d0ae0faba14", "ip_from_voter": True}
r1 = vote({**base, "vote_type": "Add traffic calming", "point_type": "route",
           "direction": 0, "edge_ids": EDGES})
print("clear:", len(r1.get("changed", [])), "edges changed")
r2 = vote({**base, "vote_type": "Street redesign", "point_type": "point",
           "direction": 1, "point": [40.7155, -73.9765]})
print("recast:", r2.get("changed"))
