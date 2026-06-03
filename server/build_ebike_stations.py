#!/usr/bin/env python3
"""
Build server/data/ebike_stations.json — the fixed set of NYC e-bike charging
stations used by the "E-bike stations" network (see graph_registry.py).

The 50 stations come from ebikes.png (a Lyft e-bike charging map), transcribed as
"<Street A> & <Street B>" intersections. We resolve each to a coordinate by
finding the graph node where an edge named A meets an edge named B in the NYC walk
graph — exact, offline, and already on the network we render. Anything the graph
can't resolve (odd plaza names, cross-borough quirks) falls back to Nominatim.
Every final coordinate is snapped to the nearest walk-graph node so the stations
sit cleanly on the votable network.

This is a one-time build helper; the JSON it writes is the canonical data read at
runtime. Re-run it only when the station list changes.

Usage:
    cd server && ./env/bin/python build_ebike_stations.py
"""
import json
import re
import sys
import time
from pathlib import Path

import requests

from cities import get_city
from python_router import PythonRouter

# Intersections transcribed from ebikes.png (Manhattan, the Bronx, Queens, and
# Brooklyn). Order matches the map legend.
STATIONS = [
    "Wadsworth Ave & W 175th St",
    "Broadway & W 172nd St",
    "Broadway & W 171st St",
    "St Nicholas Ave & W 163rd St",
    "Amsterdam Ave & W 138th St",
    "12th Ave & W 125th St",
    "E 138th St & Canal St W",
    "E 138th St & Rider Ave",
    "3rd Ave & E 143rd St",
    "Melrose Ave & E 150th St",
    "W 126th St & Lenox Ave",
    "Lenox Ave & W 125th St",
    "W 110th St & Amsterdam Ave",
    "Frederick Douglass Blvd & W 112th St",
    "7th Ave & W 117th St",
    "W 106th St & Amsterdam Ave",
    "1st Ave & E 103rd St",
    "W 94th St & Columbus Ave",
    "1st Ave & E 99th St",
    "2nd Ave & E 79th St",
    "1st Ave & E 73rd St",
    "9th Ave & W 55th St",
    "10th Ave & W 49th St",
    "2nd Ave & E 57th St",
    "E 54th St & 3rd Ave",
    "29th St & Queens Plaza N",
    "44th Dr & Jackson Ave",
    "11th St & 46th Rd",
    "9th Ave & W 42nd St",
    "10th Ave & W 38th St",
    "7th Ave & W 39th St",
    "2nd Ave & E 30th St",
    "1st Ave & E 15th St",
    "2nd Ave & E 13th St",
    "E 14th St & Avenue A",
    "Cooper Sq & E 6th St",
    "Cooper Sq & E 4th St",
    "1st Ave & E 5th St",
    "E 1st St & 1st Ave",
    "Havemeyer St & Broadway",
    "Marcy Ave & Division Ave",
    "Water St & John St",
    "Water St & Gouverneur Ln",
    "Navy St & Sands St",
    "Cadman Plaza W & Tillary St",
    "Adams St & Fulton St",
    "Duffield St & Willoughby St",
    "Fulton St & Pearl St",
    "Willoughby St & Duffield St",
    "Schermerhorn St & Bond St",
]

ABBREV = {
    "ave": "avenue", "st": "street", "blvd": "boulevard", "dr": "drive",
    "rd": "road", "ln": "lane", "sq": "square", "pl": "place", "plz": "plaza",
    "pkwy": "parkway", "ct": "court", "ter": "terrace", "hwy": "highway",
}
DIRECTION = {"w": "west", "e": "east", "n": "north", "s": "south"}

# Honorific/co-named streets: the legend uses the common name, the graph (OSM)
# uses the official one. Keys + values are normalize()'d. A street matches a node
# if any of its aliases is among the node's adjacent edge names, so the common
# name still resolves elsewhere (e.g. midtown "7th avenue" is unaffected).
ALIASES = {
    "lenox avenue": ["malcolm x boulevard"],
    "7th avenue": ["adam clayton powell jr boulevard"],
    "3rd avenue": ["third avenue"],
    "canal street west": ["canal place"],   # Bronx; legend OCR oddity
    "fulton street": ["fulton mall"],        # pedestrianized downtown-Brooklyn stretch
}


def _ordinal(num: str) -> str:
    n = int(num)
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{num}{suffix}"


def normalize(name: str) -> str:
    """Canonicalize a street name to OSM-style full form, lowercased.

    "W 175th St" → "west 175th street"; "St Nicholas Ave" → "saint nicholas
    avenue"; "Queens Plaza N" → "queens plaza north"; "112 St" → "112th street".
    """
    if not name:
        return ""
    s = name.strip()
    # Leading "St " before a word means "Saint" (e.g. "St Nicholas"), not Street.
    if re.match(r"(?i)^st\.?\s+[a-z]", s):
        s = re.sub(r"(?i)^st\.?\s+", "Saint ", s)

    out = []
    for tok in s.split():
        bare = tok.rstrip(".").lower()
        if bare in DIRECTION:
            out.append(DIRECTION[bare])
        elif bare in ABBREV:
            out.append(ABBREV[bare])
        elif bare.isdigit():
            out.append(_ordinal(bare))
        else:
            out.append(bare)
    return " ".join(out)


def split_intersection(label: str) -> tuple[str, str] | None:
    parts = [p.strip() for p in label.split("&")]
    if len(parts) != 2:
        return None
    return normalize(parts[0]), normalize(parts[1])


def build_node_streets(router: PythonRouter) -> list[set[str]]:
    """node index → set of normalized names of its adjacent edges."""
    node_streets: list[set[str]] = [set() for _ in range(len(router._coords))]
    for node_idx, edge_positions in enumerate(router._edge_adj):
        for ep in edge_positions:
            nm = normalize(router._edge_name[ep])
            if nm:
                node_streets[node_idx].add(nm)
    return node_streets


def _candidates(street: str) -> set[str]:
    return {street, *ALIASES.get(street, [])}


def find_intersection(a: str, b: str, node_streets: list[set[str]], coords,
                      use_aliases: bool = False) -> tuple[float, float] | None:
    """Centroid of graph nodes whose adjacent edges include both streets.

    Two-pass by design: callers try primary names first, then retry with
    `use_aliases=True`. Applying aliases only as a fallback keeps a generic cross
    street (e.g. "pearl street") from averaging in a same-named node in another
    borough that an alias would otherwise reach.
    """
    ca = _candidates(a) if use_aliases else {a}
    cb = _candidates(b) if use_aliases else {b}
    matches = [i for i, names in enumerate(node_streets)
               if names & ca and names & cb]
    if not matches:
        return None
    lat = sum(float(coords[i][0]) for i in matches) / len(matches)
    lon = sum(float(coords[i][1]) for i in matches) / len(matches)
    return lat, lon


def nominatim_intersection(label: str, geocode_bbox: str) -> tuple[float, float] | None:
    """Fallback: geocode "A & B" via Nominatim, viewbox-bounded to NYC."""
    a, b = [p.strip() for p in label.split("&")]
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": f"{a} and {b}, New York, NY",
                "format": "json", "limit": 1,
                "viewbox": geocode_bbox, "bounded": 1,
            },
            headers={"User-Agent": "city-edit-ebike-station-builder/1.0"},
            timeout=30,
        )
        resp.raise_for_status()
        hits = resp.json()
        if hits:
            return float(hits[0]["lat"]), float(hits[0]["lon"])
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"  nominatim error for {label!r}: {e}", file=sys.stderr)
    return None


def main():
    city = get_city("nyc")
    router = PythonRouter(data_dir=city.data_dir)
    print(f"Loading NYC walk graph from {city.data_dir} (this takes a minute)...")
    router._ensure_loaded()
    coords = router._coords
    print(f"Graph loaded: {len(coords)} nodes. Resolving {len(STATIONS)} stations...")

    node_streets = build_node_streets(router)

    out = []
    unresolved = []
    for label in STATIONS:
        pair = split_intersection(label)
        coord = None
        if pair:
            coord = (find_intersection(pair[0], pair[1], node_streets, coords)
                     or find_intersection(pair[0], pair[1], node_streets, coords,
                                          use_aliases=True))
        source = "graph"
        if coord is None:
            coord = nominatim_intersection(label, city.geocode_bbox)
            source = "nominatim"
            time.sleep(1.1)  # respect Nominatim's 1 req/s policy
        if coord is None:
            unresolved.append(label)
            print(f"  UNRESOLVED: {label}")
            continue
        # Snap onto the nearest walk-graph node for clean, on-network coords.
        lat, lon = router.nearest_node_coords(coord[0], coord[1])
        out.append({"name": label, "lat": round(lat, 6), "lon": round(lon, 6)})
        print(f"  [{source}] {label} → {lat:.5f}, {lon:.5f}")

    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)
    out_path = data_dir / "ebike_stations.json"
    out_path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nWrote {len(out)} stations to {out_path}")
    if unresolved:
        print(f"WARNING: {len(unresolved)} unresolved: {unresolved}")


if __name__ == "__main__":
    main()
