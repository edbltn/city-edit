"""
Canonical preset vote-type lists.

A *vote-type list* is a named collection of vote types (label + icon + whether it
attaches to a route or a single point). These three presets mirror the historical
"themes" (Bikes / Trees / Walkways) that the client hardcoded in themes.ts; they
seed the `vote_type_lists` table and back the preset NYC maps. New maps may pick a
preset list by id or supply their own custom list.

Each vote type: {"label": str, "icon": str, "pointType": "route" | "point"}.
"""

import os

PRESET_LISTS: dict[str, dict] = {
    "bikes": {
        "name": "Bikes",
        "tagline": "Vote for better cycling infrastructure.",
        "symbol": "bikes",
        "vote_types": [
            {"label": "Improve bike lane", "icon": "bikes", "pointType": "route"},
            {"label": "Add bike lane", "icon": "bikes", "pointType": "route"},
            {"label": "Add protected bike lane", "icon": "safety", "pointType": "route"},
            {"label": "Widen bike lane", "icon": "bikes", "pointType": "route"},
            {"label": "Add sharrow (shared lane markings)", "icon": "bikes", "pointType": "route"},
            {"label": "Repave bike path", "icon": "bikes", "pointType": "route"},
            {"label": "Add bike signal phase", "icon": "traffic-reduction", "pointType": "route"},
            {"label": "Add bike bridge", "icon": "parks", "pointType": "route"},
            {"label": "Add bike greenway", "icon": "parks", "pointType": "route"},
            {"label": "Add bike parking", "icon": "bikes", "pointType": "point"},
            {"label": "Add secure bike parking", "icon": "safety", "pointType": "point"},
            {"label": "Add e-bike charging point", "icon": "bikes", "pointType": "point"},
            {"label": "Add bike share station", "icon": "bikes", "pointType": "point"},
            {"label": "Add bike repair station", "icon": "bikes", "pointType": "point"},
            {"label": "Add bike counter", "icon": "mapping", "pointType": "point"},
            {"label": "Fix dangerous intersection", "icon": "safety", "pointType": "point"},
        ],
    },
    "trees": {
        "name": "Trees",
        "tagline": "Vote to greenify your city.",
        "symbol": "trees",
        "vote_types": [
            {"label": "Add tree-lined street", "icon": "trees", "pointType": "route"},
            {"label": "Create green corridor", "icon": "parks", "pointType": "route"},
            {"label": "Add greenway", "icon": "parks", "pointType": "route"},
            {"label": "De-pave street section", "icon": "public-space", "pointType": "route"},
            {"label": "Add bioswale corridor", "icon": "waterfront", "pointType": "route"},
            {"label": "Add tree", "icon": "trees", "pointType": "point"},
            {"label": "Plant native shrubs", "icon": "parks", "pointType": "point"},
            {"label": "Create a tree pit", "icon": "trees", "pointType": "point"},
            {"label": "Add planter boxes", "icon": "parks", "pointType": "point"},
            {"label": "Create a community garden", "icon": "community", "pointType": "point"},
            {"label": "Restore soil", "icon": "public-space", "pointType": "point"},
            {"label": "Add a bioswale", "icon": "waterfront", "pointType": "point"},
            {"label": "Protect existing tree", "icon": "safety", "pointType": "point"},
            {"label": "Tree needs pruning", "icon": "trees", "pointType": "point"},
            {"label": "Tree needs maintenance", "icon": "trees", "pointType": "point"},
        ],
    },
    "walkways": {
        "name": "Walkways",
        "tagline": "Vote for a more walkable city.",
        "symbol": "walkways",
        "vote_types": [
            {"label": "Improve sidewalk", "icon": "walkways", "pointType": "route"},
            {"label": "Add crosswalk", "icon": "pedestrian-streets", "pointType": "route"},
            {"label": "Widen sidewalk", "icon": "walkways", "pointType": "route"},
            {"label": "Fix broken sidewalk", "icon": "walkways", "pointType": "route"},
            {"label": "Add pedestrian bridge", "icon": "walkways", "pointType": "route"},
            {"label": "Add street lighting", "icon": "public-space", "pointType": "route"},
            {"label": "Add traffic calming", "icon": "traffic-reduction", "pointType": "route"},
            {"label": "Improve accessibility", "icon": "accessibility", "pointType": "route"},
            {"label": "Add pedestrian signal", "icon": "traffic-reduction", "pointType": "point"},
            {"label": "Fix curb cut", "icon": "accessibility", "pointType": "point"},
            {"label": "Add bench", "icon": "public-space", "pointType": "point"},
            {"label": "Add water fountain", "icon": "waterfront", "pointType": "point"},
            {"label": "Add public restroom", "icon": "public-space", "pointType": "point"},
            {"label": "Add bus shelter", "icon": "transit", "pointType": "point"},
        ],
    },
}

# Preset maps seeded at startup. slug → (name, city_id, list key, subdomain).
# `subdomain` preserves the existing NYC bikepaths./trees./walkways. routing; new
# cities launch subdomain-less (reachable at /m/<slug>) until DNS/TLS is added.
PRESET_MAPS: list[dict] = [
    {"slug": "nyc-bikes", "name": "NYC Bikes", "city_id": "nyc",
     "list_key": "bikes", "subdomain": "bikepaths",
     "subtitle": "Better cycling in New York City"},
    {"slug": "nyc-trees", "name": "NYC Trees", "city_id": "nyc",
     "list_key": "trees", "subdomain": "trees",
     "subtitle": "A greener New York City"},
    {"slug": "nyc-walkways", "name": "NYC Walkways", "city_id": "nyc",
     "list_key": "walkways", "subdomain": "walkways",
     "subtitle": "A more walkable New York City"},

    # Washington, D.C. — no vanity subdomains yet (reach via /m/<slug>).
    {"slug": "dc-bikes", "name": "D.C. Bikes", "city_id": "dc",
     "list_key": "bikes", "subdomain": None,
     "subtitle": "Better cycling in Washington, D.C."},
    {"slug": "dc-trees", "name": "D.C. Trees", "city_id": "dc",
     "list_key": "trees", "subdomain": None,
     "subtitle": "A greener Washington, D.C."},
    {"slug": "dc-walkways", "name": "D.C. Walkways", "city_id": "dc",
     "list_key": "walkways", "subdomain": None,
     "subtitle": "A more walkable Washington, D.C."},

    # Philadelphia — no vanity subdomains yet (reach via /m/<slug>).
    {"slug": "philly-bikes", "name": "Philly Bikes", "city_id": "philly",
     "list_key": "bikes", "subdomain": None,
     "subtitle": "Better cycling in Philadelphia"},
    {"slug": "philly-trees", "name": "Philly Trees", "city_id": "philly",
     "list_key": "trees", "subdomain": None,
     "subtitle": "A greener Philadelphia"},
    {"slug": "philly-walkways", "name": "Philly Walkways", "city_id": "philly",
     "list_key": "walkways", "subdomain": None,
     "subtitle": "A more walkable Philadelphia"},

]

# Block-pipeline playground (local experiments on tiny test cities). Opt-in via
# SEED_TEST_MAPS=1 so seed_presets() never recreates them on prod/staging.
if os.environ.get("SEED_TEST_MAPS", "").strip().lower() in ("1", "true", "yes"):
    PRESET_MAPS += [
        {"slug": "test-central-park", "name": "Test: Central Park", "city_id": "test-cp",
         "list_key": "walkways", "subdomain": None,
         "subtitle": "Block-pipeline playground — Central Park"},
        {"slug": "test-midtown", "name": "Test: Midtown", "city_id": "test-mid",
         "list_key": "walkways", "subdomain": None,
         "subtitle": "Block-pipeline playground — Midtown"},
    ]
