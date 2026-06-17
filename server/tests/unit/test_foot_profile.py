"""Routability predicate tests for foot_profile.way_is_foot_routable.

The predicate is the Python mirror of osrm/foot.lua. The cases that matter most are
the ones where the two implementations historically drifted — ferries above all (a
route=ferry way tagged foot=yes used to pass via the access whitelist fallback on
both sides, so OSRM routed the Staten Island Ferry across open water and the topology
graph carried the matching long over-water edge). These tests lock the ferry cutoff
in on the Python side; osrm/foot.lua's `data.route == 'ferry'` abort is the OSRM half.
"""
from foot_profile import way_is_foot_routable


def test_plain_footway_is_routable():
    assert way_is_foot_routable({"highway": "footway"})


def test_residential_street_is_routable():
    assert way_is_foot_routable({"highway": "residential"})


def test_access_whitelist_fallback_routes_uncategorised_way():
    # A way with no speed-table category but foot=designated is still routable
    # (mirrors WayHandlers.speed's access fallback — how cycleways get walked).
    assert way_is_foot_routable({"highway": "cycleway", "foot": "designated"})


def test_ferry_without_foot_tag_rejected():
    assert not way_is_foot_routable({"route": "ferry"})


def test_ferry_with_foot_yes_rejected():
    # The exact leak: foot=yes used to satisfy the access whitelist fallback.
    assert not way_is_foot_routable({"route": "ferry", "foot": "yes"})


def test_ferry_with_access_yes_rejected():
    assert not way_is_foot_routable({"route": "ferry", "access": "yes"})


def test_ferry_with_motorboat_designated_rejected():
    assert not way_is_foot_routable({"route": "ferry", "foot": "designated"})


def test_access_blacklist_blocks_routable_category():
    assert not way_is_foot_routable({"highway": "footway", "foot": "no"})


def test_untagged_way_not_routable():
    assert not way_is_foot_routable({"name": "Some Way"})
