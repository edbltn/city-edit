# Referenced by: docs/algorithms/04-route-finding.md — this file and
#   osrm/foot.lua must change TOGETHER, or OSRM routes over edges the
#   votable graph does not have.
"""
Foot routability rules — the Python mirror of osrm/foot.lua (OSRM v5.25.0).

OSRM routes the actual paths users vote on; this module decides which OSM ways go
into the *votable* topology graph (osm_graph_builder.build_walk_graph_from_pbf).
For votes to map onto graph edges (vote_store.osm_nodes_to_edge_ids), the topology
graph must be a SUPERSET of OSRM's foot network. So these rules mirror foot.lua's
routable categories + access gate, and intentionally bias toward *accepting* when
unsure — an extra topology edge is harmless (just never auto-routed), a missing one
loses a vote.

KEEP IN SYNC with osrm/foot.lua. If that profile's `speeds`/access tables change,
update the sets below. We deliberately ignore foot.lua's speed/surface/smoothness
penalties (they affect cost, not routability) and its barrier handling (a node-level
concern — osm_graph_builder keeps barrier nodes so OSRM's node IDs still resolve).
"""

# foot.lua `speeds` table — any way tagged with one of these (key, value) pairs is
# assigned a walking speed and is therefore routable.
_ROUTABLE_HIGHWAY = frozenset({
    "primary", "primary_link",
    "secondary", "secondary_link",
    "tertiary", "tertiary_link",
    "unclassified", "residential", "road", "living_street",
    "service", "track", "path", "steps", "pedestrian", "footway", "pier",
})
_ROUTABLE_RAILWAY = frozenset({"platform"})
_ROUTABLE_AMENITY = frozenset({"parking", "parking_entrance"})
_ROUTABLE_MAN_MADE = frozenset({"pier"})
_ROUTABLE_LEISURE = frozenset({"track"})

# foot.lua `route_speeds` — DELIBERATELY EMPTY: ferries are disabled (see foot.lua).
# A ferry leg is a long straight edge over open water with no real walkable path,
# which lets routes jump the water and breaks the client's on-path midpoint-drag
# hit test (a long thin band that fires from clicks far off the visual route).
#
# An empty _ROUTABLE_ROUTE is NOT enough on its own: it only keeps route=ferry out
# of _has_routable_category. A ferry tagged foot=yes (e.g. the Staten Island Ferry)
# would still pass via the `access in _ACCESS_WHITELIST` fallback at the end of
# way_is_foot_routable — mirroring exactly how OSRM's WayHandlers.speed admitted it
# until the route=ferry abort in foot.lua's process_way. So way_is_foot_routable
# rejects route=ferry explicitly (see _ROUTE_BLACKLIST). KEEP IN SYNC with osrm/foot.lua.
_ROUTABLE_ROUTE = frozenset()

# route values that are never foot-routable, regardless of access tags. Mirrors the
# `data.route == 'ferry'` abort in foot.lua process_way.
_ROUTE_BLACKLIST = frozenset({"ferry"})

# foot.lua access tables + hierarchy (check `foot`, then `access`).
_ACCESS_WHITELIST = frozenset({"yes", "foot", "permissive", "designated"})
_ACCESS_BLACKLIST = frozenset({"no", "agricultural", "forestry", "private", "delivery"})
_ACCESS_HIERARCHY = ("foot", "access")

# foot.lua process_way aborts unless the way has at least one of these tags (its
# "is this remotely routable" prefilter). A way with none of them is never routed.
_PREFETCH_KEYS = (
    "highway", "bridge", "route", "leisure", "man_made",
    "railway", "platform", "amenity", "public_transport",
)

# Node-level barrier blacklist (mirrored for parity; NOT used to prune nodes — see
# module docstring). Kept so the rule is documented in one place.
BARRIER_BLACKLIST = frozenset({"yes", "wall", "fence"})


def _has_routable_category(tags: dict) -> bool:
    return (
        tags.get("highway") in _ROUTABLE_HIGHWAY
        or tags.get("railway") in _ROUTABLE_RAILWAY
        or tags.get("amenity") in _ROUTABLE_AMENITY
        or tags.get("man_made") in _ROUTABLE_MAN_MADE
        or tags.get("leisure") in _ROUTABLE_LEISURE
        or tags.get("route") in _ROUTABLE_ROUTE
    )


def _access_value(tags: dict):
    """find_access_tag: the first present of (foot, access), else None."""
    for key in _ACCESS_HIERARCHY:
        val = tags.get(key)
        if val:
            return val
    return None


def way_is_foot_routable(tags: dict) -> bool:
    """True if OSRM's foot profile would assign this way a walking speed.

    Mirrors foot.lua process_way + WayHandlers.speed: the way must carry at least
    one prefetch tag and not be access-blacklisted, then it's routable if either
    (a) it has a category in the speeds table (highway/railway/route/...), OR
    (b) its foot/access value is explicitly whitelisted — WayHandlers.speed falls
    back to default_speed for any accessible way (this is how cycleways/paths with
    `foot=designated` get routed even though `cycleway` isn't in the speeds table).

    `tags` is a plain {key: value} dict of the way's OSM tags.
    """
    if not any(k in tags for k in _PREFETCH_KEYS):
        return False
    # Hard cutoff for ferries before the access fallback below (see _ROUTE_BLACKLIST).
    if tags.get("route") in _ROUTE_BLACKLIST:
        return False
    access = _access_value(tags)
    if access in _ACCESS_BLACKLIST:
        return False
    if _has_routable_category(tags):
        return True
    return access in _ACCESS_WHITELIST


def node_blocks_foot(tags: dict) -> bool:
    """Mirror of foot.lua process_node barrier logic. Defined for parity/auditing;
    NOT used to remove nodes (pruning would break the consecutive-node-pair chain
    and lose adjacent edges — a superset graph is the goal)."""
    # Access tag on the node overrides a barrier (matches find_access_tag precedence).
    for key in _ACCESS_HIERARCHY:
        val = tags.get(key)
        if val:
            return val in _ACCESS_BLACKLIST
    barrier = tags.get("barrier")
    if barrier and barrier in BARRIER_BLACKLIST:
        # Rising bollards are passable in foot.lua.
        return tags.get("bollard") != "rising"
    return False
