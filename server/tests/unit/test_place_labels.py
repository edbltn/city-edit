"""Curation rules for the place-label tileset (build_place_labels.py).

These cover the two things that decide whether the label layer reads as a map
or as a directory dump: WHICH features survive the allowlist, and WHEN each one
is allowed to appear. Both are easy to regress silently — a bad allowlist entry
just quietly puts 15,000 subway platforms back on the map.
"""

import pytest

from build_place_labels import (
    MAX_LABEL_ZOOM,
    Poi,
    assign_zooms,
    classify,
    dedup,
    normalize_name,
)


class TestClassify:
    def test_allowlisted_kinds_are_kept_with_their_tier(self):
        assert classify({"amenity": "library"}) == ("civic", 68, 15)
        assert classify({"leisure": "stadium"}) == ("culture", 88, 13)
        assert classify({"amenity": "restaurant"}) == ("retail", 30, 18)

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
        assert notable[1] > plain[1]

    def test_subway_stop_is_demoted_below_a_mainline_terminal(self):
        mainline = classify({"railway": "station", "name": "Grand Central"})
        subway = classify({"railway": "station", "station": "subway"})
        # Same OSM tag, very different cartographic weight.
        assert subway[1] < mainline[1]
        assert subway[2] > mainline[2]

    def test_subway_demotion_survives_a_wikidata_tag(self):
        # Nearly every NYC subway stop has one; if the notability bonus applied
        # here it would hand back exactly the rank the demotion removed.
        assert classify({"railway": "station", "station": "subway",
                         "wikidata": "Q1234"}) == classify(
            {"railway": "station", "station": "subway"})

    def test_more_specific_tag_wins_when_a_feature_carries_several(self):
        # A hotel with a restaurant in it is a hotel: `tourism` is consulted
        # before `amenity`.
        assert classify({"tourism": "hotel", "amenity": "restaurant"})[0:2] \
            == ("retail", 56)


class TestNormalizeName:
    def test_folds_case_accents_and_punctuation(self):
        assert normalize_name("Joe's Café") == normalize_name("JOES CAFE")

    def test_keeps_distinct_names_distinct(self):
        assert normalize_name("14 St") != normalize_name("14 Ave")


class TestDedup:
    def test_collapses_a_station_mapped_once_per_line(self):
        # One NYC complex, five `railway=station` nodes within a block.
        pois = [Poi(40.7370 + i * 0.0002, -73.9900, "14 St", "transit", 64, 15)
                for i in range(5)]
        assert len(dedup(pois)) == 1

    def test_keeps_the_highest_ranked_of_a_cluster(self):
        pois = [
            Poi(40.7370, -73.9900, "14 St", "transit", 64, 15),
            Poi(40.7371, -73.9900, "14 St", "transit", 90, 13),
        ]
        kept = dedup(pois)
        assert len(kept) == 1
        assert kept[0].rank == 90

    def test_same_name_far_apart_is_two_real_places(self):
        # Every borough has a "Post Office"; they are not duplicates.
        pois = [
            Poi(40.7370, -73.9900, "Post Office", "civic", 52, 17),
            Poi(40.6800, -73.9500, "Post Office", "civic", 52, 17),
        ]
        assert len(dedup(pois)) == 2


class TestAssignZooms:
    def test_never_reveals_a_poi_before_its_floor(self):
        pois = [Poi(40.75 + i * 0.05, -73.9 - i * 0.05, f"P{i}",
                    "retail", 30, 18) for i in range(4)]
        assign_zooms(pois)
        assert all(p.minz >= 18 for p in pois)

    def test_highest_ranked_wins_a_contested_cell(self):
        # Two POIs on top of each other: only one can hold the spot first.
        big = Poi(40.7500, -73.9900, "Museum", "culture", 85, 13)
        small = Poi(40.7500, -73.9901, "Deli", "retail", 30, 13)
        assign_zooms([small, big])
        assert big.minz < small.minz

    def test_spread_out_pois_all_reveal_at_their_floor(self):
        pois = [Poi(40.5 + i * 0.1, -74.0 + i * 0.1, f"Venue{i}",
                    "culture", 88, 13) for i in range(4)]
        assign_zooms(pois)
        assert all(p.minz == 13 for p in pois)

    def test_every_poi_gets_a_reveal_zoom(self):
        # Even a POI whose cell is taken at every single zoom must end up
        # visible somewhere rather than silently vanishing from the tileset.
        pois = [Poi(40.75, -73.99, f"Stacked{i}", "retail", 30, 18)
                for i in range(30)]
        assign_zooms(pois)
        assert all(1 <= p.minz <= MAX_LABEL_ZOOM for p in pois)
