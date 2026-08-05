"""Curation rules for the place-label tileset (build_place_labels.py).

These cover the things that decide whether the label layer reads as a map or as
a directory dump: WHICH features survive the allowlist, WHEN each one is allowed
to appear, and whether every kind still has an icon to draw. All easy to regress
silently — a bad allowlist entry just quietly puts 15,000 subway platforms back
on the map, and a rank tweak can make a whole category disappear without
erroring anywhere.
"""

import pathlib
import re

import pytest

from build_place_labels import (
    KINDS,
    MAX_LABEL_ZOOM,
    SUBWAY_STATION,
    Poi,
    assign_zooms,
    classify,
    dedup,
    normalize_name,
)

# classify() returns (category, icon, rank, floor).
CAT, ICON, RANK, FLOOR = 0, 1, 2, 3


class TestClassify:
    def test_allowlisted_kinds_are_kept_with_their_tier(self):
        assert classify({"amenity": "library"}) == ("civic", "library", 68, 15)
        assert classify({"leisure": "stadium"}) == ("culture", "stadium", 88, 13)
        assert classify({"amenity": "restaurant"}) == ("retail", "restaurant", 30, 18)

    @pytest.mark.parametrize("tags", [
        # The five most common named POI tags in the NYC extract; four are noise.
        {"public_transport": "platform"},
        {"railway": "rail"},
        {"railway": "abandoned"},
        {"leisure": "pitch"},
        # Green space is the basemap raster's job — labelling it here too
        # printed every park name twice, once in each cartography.
        {"leisure": "park"},
        {"leisure": "garden"},
        {"leisure": "playground"},
        {"amenity": "parking"},
        {"shop": "hairdresser"},
        {"building": "yes"},
        {},
    ])
    def test_noise_tags_are_dropped(self, tags):
        assert classify(tags) is None

    def test_wiki_tag_promotes_a_notable_business(self):
        plain = classify({"amenity": "restaurant"})
        notable = classify({"amenity": "restaurant", "wikidata": "Q1067278"})
        assert notable[RANK] > plain[RANK]

    def test_more_specific_tag_wins_when_a_feature_carries_several(self):
        # A hotel with a restaurant in it is a hotel: `tourism` is consulted
        # before `amenity`.
        result = classify({"tourism": "hotel", "amenity": "restaurant"})
        assert (result[CAT], result[ICON]) == ("retail", "hotel")


class TestSubwayStations:
    """496 of the 561 stations in the NYC bbox are `station=subway`, sharing
    Grand Central's `railway=station` tag. Getting their weight right needed two
    corrections in opposite directions, so both are pinned here."""

    def test_demoted_below_a_mainline_terminal(self):
        mainline = classify({"railway": "station"})
        subway = classify({"railway": "station", "station": "subway"})
        assert subway[RANK] < mainline[RANK]
        assert subway[FLOOR] > mainline[FLOOR]

    def test_demotion_survives_a_wikidata_tag(self):
        # Nearly every NYC stop has one; if the notability bonus applied here it
        # would push all 496 back above the terminals this entry separates them
        # from.
        assert classify({"railway": "station", "station": "subway",
                         "wikidata": "Q1234"}) == SUBWAY_STATION

    def test_stays_out_of_the_city_scale_view(self):
        # At floor 13 the 496 stops carpeted the city view, and since most are
        # named after the street above them they read as duplicate street labels.
        assert SUBWAY_STATION[FLOOR] >= 14

    @pytest.mark.parametrize("competitor", [
        {"amenity": "hospital"}, {"tourism": "museum"}, {"amenity": "library"},
        {"amenity": "theatre"}, {"tourism": "hotel"}, {"amenity": "college"},
        {"shop": "mall"}, {"amenity": "townhall"},
    ])
    def test_outranks_the_kinds_that_once_buried_it(self, competitor):
        # Regression: demoting to rank 64 put stops near the BOTTOM of
        # everything eligible at z15, so every grid cell went to a museum or a
        # hospital first and stops did not surface until z17-19 — for practical
        # purposes they had vanished. A stop must win its cell once eligible.
        assert SUBWAY_STATION[RANK] > classify(competitor)[RANK]

    def test_gets_its_own_icon(self):
        # The mark is what lets a stop named "5th Avenue" sit beside the
        # basemap's "5th Avenue" street label without reading as a bug.
        assert SUBWAY_STATION[ICON] == "subway"
        assert classify({"railway": "station"})[ICON] != "subway"


class TestIconCoverage:
    def test_every_kind_has_a_glyph_in_the_client(self):
        # The tileset ships `kind` as a bare string; a slug with no matching
        # glyph renders as the fallback dot with no error anywhere.
        src = (pathlib.Path(__file__).parents[2].parent / "client-react" / "src"
               / "components" / "MapLibreBackground" / "placeIcons.ts").read_text()
        # Top-level keys of the ICONS record, e.g. "  subway: {".
        available = set(re.findall(r"^  (\w+): \{$", src, re.M))
        assert available, "could not parse icon names out of placeIcons.ts"
        used = {entry[ICON] for entry in KINDS.values()} | {SUBWAY_STATION[ICON]}
        assert used <= available, f"kinds with no glyph: {sorted(used - available)}"


class TestNormalizeName:
    def test_folds_case_accents_and_punctuation(self):
        assert normalize_name("Joe's Café") == normalize_name("JOES CAFE")

    def test_keeps_distinct_names_distinct(self):
        assert normalize_name("14 St") != normalize_name("14 Ave")


class TestDedup:
    def test_collapses_a_station_mapped_once_per_line(self):
        # One NYC complex, five `railway=station` nodes within a block.
        pois = [Poi(40.7370 + i * 0.0002, -73.9900, "14 St", "transit", "subway", 86, 14)
                for i in range(5)]
        assert len(dedup(pois)) == 1

    def test_keeps_the_highest_ranked_of_a_cluster(self):
        pois = [
            Poi(40.7370, -73.9900, "14 St", "transit", "subway", 86, 14),
            Poi(40.7371, -73.9900, "14 St", "transit", "rail", 90, 13),
        ]
        kept = dedup(pois)
        assert len(kept) == 1
        assert kept[0].rank == 90

    def test_same_name_far_apart_is_two_real_places(self):
        # Every borough has a "Post Office"; they are not duplicates.
        pois = [
            Poi(40.7370, -73.9900, "Post Office", "civic", "post", 52, 17),
            Poi(40.6800, -73.9500, "Post Office", "civic", "post", 52, 17),
        ]
        assert len(dedup(pois)) == 2


class TestAssignZooms:
    def test_never_reveals_a_poi_before_its_floor(self):
        pois = [Poi(40.75 + i * 0.05, -73.9 - i * 0.05, f"P{i}",
                    "retail", "restaurant", 30, 18) for i in range(4)]
        assign_zooms(pois)
        assert all(p.minz >= 18 for p in pois)

    def test_highest_ranked_wins_a_contested_cell(self):
        # Two POIs on top of each other: only one can hold the spot first.
        big = Poi(40.7500, -73.9900, "Museum", "culture", "museum", 85, 13)
        small = Poi(40.7500, -73.9901, "Deli", "retail", "restaurant", 30, 13)
        assign_zooms([small, big])
        assert big.minz < small.minz

    def test_a_stop_is_never_displaced_by_another_category(self):
        # Transit thins on its own finer grid, so a co-located museum cannot
        # cost a station its reveal zoom. This is the end-to-end form of the
        # "stations vanished" regression: on the shared grid the six stops
        # around the East Village lost their cells to whatever else was nearby.
        stop = Poi(40.7500, -73.9900, "14 St", "transit", "subway", 86, 14)
        museum = Poi(40.7500, -73.9901, "Museum", "culture", "museum", 85, 14)
        assign_zooms([museum, stop])
        assert stop.minz == 14

    def test_stops_still_compete_with_each_other(self):
        # The finer grid is a bigger budget, not an exemption — two stations on
        # the same corner must not both claim the same zoom.
        a = Poi(40.7500, -73.9900, "A", "transit", "subway", 86, 14)
        b = Poi(40.75001, -73.99001, "B", "transit", "subway", 86, 14)
        assign_zooms([a, b])
        assert a.minz != b.minz

    def test_nearby_stops_survive_where_a_shared_grid_would_drop_them(self):
        # Four stops ~500m apart, the East Village spacing. On the shared
        # 256px-cell grid at z16 they land in one or two cells; on transit's
        # finer grid each gets its own.
        stops = [Poi(40.7300 + i * 0.0045, -73.9880, f"Stop {i}",
                     "transit", "subway", 86, 14) for i in range(4)]
        assign_zooms(stops)
        assert max(p.minz for p in stops) <= 16

    def test_spread_out_pois_all_reveal_at_their_floor(self):
        pois = [Poi(40.5 + i * 0.1, -74.0 + i * 0.1, f"Venue{i}",
                    "culture", "stadium", 88, 13) for i in range(4)]
        assign_zooms(pois)
        assert all(p.minz == 13 for p in pois)

    def test_every_poi_gets_a_reveal_zoom(self):
        # Even a POI whose cell is taken at every single zoom must end up
        # visible somewhere rather than silently vanishing from the tileset.
        pois = [Poi(40.75, -73.99, f"Stacked{i}", "retail", "restaurant", 30, 18)
                for i in range(30)]
        assign_zooms(pois)
        assert all(1 <= p.minz <= MAX_LABEL_ZOOM for p in pois)
