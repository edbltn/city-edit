"""
Build per-city place-label PMTiles (business and landmark names) from the OSM
PBF each city already ships.

The basemap's CARTO label tiles carry streets, neighborhoods and water — but no
businesses or civic landmarks at any zoom. This builder fills that gap from
`osm_data/<city>/source.osm.pbf`, the same extract the walk graph and OSRM are
built from, so a label and the street it sits on always come from one snapshot.

Output: osm_data/<city>/places.pmtiles, served by the existing
/api/tiles/<city>/*.pmtiles route (nginx caches it like the other archives).

    python build_place_labels.py --all
    python build_place_labels.py --city nyc --force

## Why a curated allowlist

Dumping every named OSM feature is what makes a label layer feel broken. The
five most common named POI tags in the NYC extract are, in order:

    15688  public_transport=platform     every subway platform, one per track
    14825  amenity=restaurant
    10675  railway=rail                  named track segments, dozens per line
     8613  amenity=place_of_worship
     4950  railway=abandoned             track that no longer exists

Four of those five are noise. KINDS below is an explicit allowlist — anything
not in it is dropped, so new junk tags can never leak in.

## Why labels appear when they do

Every kind gets a `rank` (who wins a collision) and a floor zoom. The zoom a
label ACTUALLY appears at is then computed by grid thinning (see
`assign_zooms`): at each zoom the map is divided into cells and only the
highest-ranked unplaced POI in a cell earns that zoom. Labels already placed
re-claim their cell first, so zooming in adds detail between existing labels
instead of piling text on top of it. This is what keeps a dense commercial
strip from turning into a wall of text — the cap is geometric, so it holds for
Midtown and for Canarsie alike without any per-neighborhood tuning.

## Zoom units

`minz` on each feature is a LEAFLET zoom, matching CONFIG.maxZoom, map URLs and
every other zoom number in this codebase. MapLibre drives its camera one level
lower (512px vs 256px tile convention), so the client filter compares against
`zoom + 1`. Tile z/x/y addresses below are MapLibre/mercantile zooms.
"""

import argparse
import json
import logging
import math
import os
import sys
import unicodedata

import mapbox_vector_tile
import mercantile
import osmium
from pmtiles.tile import zxy_to_tileid, TileType, Compression
from pmtiles.writer import Writer

from cities import CITIES, City, get_city

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Tile zoom band (mercantile/MapLibre zooms). The client overzooms past the top
# of the band, so the maxzoom tile carries every label regardless of its minz.
MIN_TILE_ZOOM = 11
MAX_TILE_ZOOM = 15

# Leaflet zoom band the thinning runs over. 11 is roughly "whole city on
# screen"; 21 is CONFIG.maxZoom. Running all the way to the app's real maximum
# matters: every POI the grid never places falls back to the last zoom, so a
# ceiling of 19 dumped 20k labels — 60% of the set — into one ungoverned band.
MIN_LABEL_ZOOM = 11
MAX_LABEL_ZOOM = 21

# Thinning cell size, as tile-zoom levels below the label zoom. 0 means one cell
# per tile — about 256 CSS px on screen — so a 1568x774 viewport admits roughly
# 18 newly-revealed labels per zoom level, plus the handful inherited from
# coarser zooms. At 1 (quarter-tile cells) the city-scale view came out at ~50
# labels and read as a directory, not a map.
GRID_OFFSET = 0

# Two same-named POIs closer than this are the same place mapped twice (NYC
# subway complexes carry a `railway=station` node per line, so "14 St" alone
# appears five times within a block). Keep the highest-ranked one.
DEDUP_METERS = 400.0

# Categories drive label COLOR in the client (see POI_COLORS in mapStyles.ts).
# Deliberately few: colors have to be learnable at a glance, and every extra hue
# is one more thing competing with the vote heat.
#
# There is no green-space category, and that is a division of labor, not an
# oversight. The basemap's CARTO label raster already draws parks, gardens and
# squares in green caps, and it is drawn UNDER this layer — so labelling them
# here too printed "Grand Street Garden" twice, once in each cartography, which
# is exactly the kind of thing that reads as a broken map. Green space belongs
# to the basemap; buildings and businesses belong here. Verified against CARTO's
# own tiles: it labels no stadium, hospital or university, so everything below
# is genuinely unclaimed.
CAT_TRANSIT = "transit"
CAT_CIVIC = "civic"
CAT_CULTURE = "culture"
CAT_RETAIL = "retail"

# (osm_key, osm_value) -> (category, rank, floor zoom in LEAFLET zoom units).
# rank breaks collisions: higher wins the cell and the pixel.
KINDS: dict[tuple[str, str], tuple[str, int, int]] = {
    # ── Transit ────────────────────────────────────────────────────────────
    # Stations only. Platforms and stop positions are per-track duplicates of a
    # station that is already labelled, and named track segments are not places.
    ("aeroway", "aerodrome"): (CAT_TRANSIT, 98, 11),
    # Mainline terminals and commuter-rail stops: Penn, Grand Central, the LIRR
    # and Metro-North. Genuinely city-scale landmarks.
    ("railway", "station"): (CAT_TRANSIT, 90, 13),
    ("railway", "halt"): (CAT_TRANSIT, 70, 15),
    ("amenity", "ferry_terminal"): (CAT_TRANSIT, 85, 14),
    ("amenity", "bus_station"): (CAT_TRANSIT, 80, 14),
    ("public_transport", "station"): (CAT_TRANSIT, 74, 14),

    # ── Venues ─────────────────────────────────────────────────────────────
    # What's left of `leisure` once green space goes to the basemap: these are
    # buildings you go into, not open space, so they carry a venue color rather
    # than a park one.
    ("leisure", "stadium"): (CAT_CULTURE, 88, 13),
    ("leisure", "sports_centre"): (CAT_RETAIL, 58, 16),
    ("leisure", "fitness_centre"): (CAT_RETAIL, 45, 17),

    # ── Civic & education ──────────────────────────────────────────────────
    ("amenity", "university"): (CAT_CIVIC, 92, 13),
    ("amenity", "college"): (CAT_CIVIC, 80, 14),
    ("amenity", "hospital"): (CAT_CIVIC, 84, 14),
    ("amenity", "townhall"): (CAT_CIVIC, 76, 15),
    ("amenity", "courthouse"): (CAT_CIVIC, 72, 15),
    ("amenity", "library"): (CAT_CIVIC, 68, 15),
    ("amenity", "community_centre"): (CAT_CIVIC, 56, 16),
    ("amenity", "school"): (CAT_CIVIC, 54, 16),
    ("amenity", "place_of_worship"): (CAT_CIVIC, 48, 16),
    ("amenity", "post_office"): (CAT_CIVIC, 52, 17),
    ("amenity", "fire_station"): (CAT_CIVIC, 50, 17),
    ("amenity", "police"): (CAT_CIVIC, 50, 17),
    ("office", "government"): (CAT_CIVIC, 44, 17),

    # ── Culture ────────────────────────────────────────────────────────────
    ("tourism", "zoo"): (CAT_CULTURE, 90, 13),
    ("tourism", "aquarium"): (CAT_CULTURE, 90, 13),
    ("tourism", "theme_park"): (CAT_CULTURE, 88, 13),
    ("tourism", "museum"): (CAT_CULTURE, 85, 14),
    ("tourism", "attraction"): (CAT_CULTURE, 80, 14),
    ("amenity", "theatre"): (CAT_CULTURE, 70, 15),
    ("tourism", "gallery"): (CAT_CULTURE, 58, 16),
    ("amenity", "cinema"): (CAT_CULTURE, 60, 16),
    ("amenity", "arts_centre"): (CAT_CULTURE, 60, 16),
    ("historic", "monument"): (CAT_CULTURE, 62, 16),
    ("historic", "memorial"): (CAT_CULTURE, 44, 17),
    ("tourism", "artwork"): (CAT_CULTURE, 38, 18),

    # ── Retail, food & lodging ─────────────────────────────────────────────
    # The floor zooms here are deep on purpose: these are the labels that tell
    # you which block you're on, not which neighborhood, and they only earn
    # their space once the heat is already resolved to individual streets.
    ("shop", "mall"): (CAT_RETAIL, 72, 15),
    ("shop", "department_store"): (CAT_RETAIL, 66, 16),
    ("amenity", "marketplace"): (CAT_RETAIL, 62, 16),
    ("shop", "supermarket"): (CAT_RETAIL, 58, 17),
    ("tourism", "hotel"): (CAT_RETAIL, 56, 17),
    ("amenity", "pharmacy"): (CAT_RETAIL, 38, 18),
    ("amenity", "bank"): (CAT_RETAIL, 34, 18),
    ("shop", "books"): (CAT_RETAIL, 32, 18),
    ("amenity", "restaurant"): (CAT_RETAIL, 30, 18),
    ("amenity", "cafe"): (CAT_RETAIL, 30, 18),
    ("amenity", "bar"): (CAT_RETAIL, 30, 18),
    ("amenity", "pub"): (CAT_RETAIL, 30, 18),
    ("shop", "bakery"): (CAT_RETAIL, 28, 18),
    ("amenity", "fast_food"): (CAT_RETAIL, 22, 18),
    ("amenity", "ice_cream"): (CAT_RETAIL, 22, 18),
}

# Tag keys worth consulting, in priority order — the first one that resolves to
# a KINDS entry wins. Ordered so a feature tagged both `tourism=hotel` and
# `amenity=restaurant` lands on the more specific/important of the two.
TAG_KEYS = ("aeroway", "railway", "public_transport", "leisure", "tourism",
            "historic", "amenity", "shop", "office")

# A place the wider world has heard of outranks its generic peers, which is what
# lifts Katz's above the deli next door without a hand-maintained landmark list.
WIKI_RANK_BONUS = 25

# A subway stop is tagged `railway=station` exactly like Grand Central, but NYC
# has 470-odd of them and most are named after the street above ("5th Avenue",
# "72nd Street"). At mainline rank they carpeted the city-scale view, and a blue
# "5th Avenue" sitting beside the basemap's own "5th Avenue" street label reads
# as a rendering bug rather than a station. Demoted to a neighborhood-scale
# label, which is the zoom at which knowing your nearest stop actually helps.
SUBWAY_STATION = (CAT_TRANSIT, 64, 15)
SUBWAY_STATION_VALUES = {"subway", "light_rail", "monorail"}


class Poi:
    __slots__ = ("lat", "lon", "name", "cat", "rank", "floor", "minz")

    def __init__(self, lat, lon, name, cat, rank, floor):
        self.lat = lat
        self.lon = lon
        self.name = name
        self.cat = cat
        self.rank = rank
        self.floor = floor
        self.minz = 0


def classify(tags) -> tuple[str, int, int] | None:
    """(category, rank, floor zoom) for a feature's tags, or None to drop it."""
    for key in TAG_KEYS:
        value = tags.get(key)
        if value is None:
            continue
        entry = KINDS.get((key, value))
        if entry is None:
            continue
        if key == "railway" and value == "station" and \
                tags.get("station") in SUBWAY_STATION_VALUES:
            # Returned as-is: nearly every NYC subway stop carries a wikidata
            # tag, so letting the notability bonus apply here would hand back
            # the mainline rank the demotion just took away.
            return SUBWAY_STATION
        cat, rank, floor = entry
        if "wikidata" in tags or "wikipedia" in tags:
            rank += WIKI_RANK_BONUS
        return cat, rank, floor
    return None


def normalize_name(name: str) -> str:
    """Fold a name for dedup: case, accents and punctuation don't distinguish
    two mappings of the same station."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return "".join(ch for ch in folded.lower() if ch.isalnum())


class PoiCollector(osmium.SimpleHandler):
    """Collects allowlisted named POIs. Nodes use their own location; closed
    ways use the mean of their nodes, which is close enough for a label anchor
    on a park or campus. Multipolygon relations are skipped — assembling areas
    costs a second full pass over a 500MB extract, and in practice the features
    that matter are mapped as ways."""

    def __init__(self, bbox):
        super().__init__()
        self.south, self.west, self.north, self.east = bbox
        self.pois: list[Poi] = []
        self.skipped_unnamed = 0

    def _add(self, tags, lat, lon):
        if not (self.south <= lat <= self.north and self.west <= lon <= self.east):
            return
        name = tags.get("name")
        if not name or len(name) > 60:
            return
        entry = classify(tags)
        if entry is None:
            return
        cat, rank, floor = entry
        self.pois.append(Poi(lat, lon, name, cat, rank, floor))

    def node(self, n):
        if not n.tags:
            return
        self._add(n.tags, n.location.lat, n.location.lon)

    def way(self, w):
        if not w.tags or len(w.nodes) < 2:
            return
        if classify(w.tags) is None:
            return
        lat = lon = 0.0
        count = 0
        for node in w.nodes:
            if not node.location.valid():
                continue
            lat += node.location.lat
            lon += node.location.lon
            count += 1
        if count:
            self._add(w.tags, lat / count, lon / count)


def dedup(pois: list[Poi]) -> list[Poi]:
    """Drop same-named POIs within DEDUP_METERS of a higher-ranked twin."""
    # Highest rank first so the survivor of each cluster is the best-ranked one.
    pois.sort(key=lambda p: (-p.rank, p.name, p.lat, p.lon))
    # Cell size ~ the dedup radius, so a twin is in this cell or one adjacent.
    cell_deg = DEDUP_METERS / 111_320.0
    kept: list[Poi] = []
    index: dict[tuple[str, int, int], list[Poi]] = {}
    for poi in pois:
        key_name = normalize_name(poi.name)
        cy, cx = int(poi.lat / cell_deg), int(poi.lon / cell_deg)
        clash = False
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for other in index.get((key_name, cy + dy, cx + dx), ()):
                    if haversine_m(poi.lat, poi.lon, other.lat, other.lon) < DEDUP_METERS:
                        clash = True
                        break
                if clash:
                    break
            if clash:
                break
        if clash:
            continue
        index.setdefault((key_name, cy, cx), []).append(poi)
        kept.append(poi)
    return kept


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 6_371_000 * 2 * math.asin(math.sqrt(a))


def assign_zooms(pois: list[Poi]) -> None:
    """Set each POI's `minz` by grid thinning — see the module docstring.

    At every zoom, POIs already placed re-claim their cell before any newcomer
    is considered. Without that, a z17 winner could land on the exact spot of a
    z14 landmark and the two would fight for the same pixels forever.
    """
    pois.sort(key=lambda p: (-p.rank, p.name, p.lat, p.lon))
    placed: list[Poi] = []
    for zoom in range(MIN_LABEL_ZOOM, MAX_LABEL_ZOOM + 1):
        grid_zoom = zoom + GRID_OFFSET
        occupied = {_cell(p, grid_zoom) for p in placed}
        for poi in pois:
            if poi.minz or poi.floor > zoom:
                continue
            cell = _cell(poi, grid_zoom)
            if cell in occupied:
                continue
            occupied.add(cell)
            poi.minz = zoom
            placed.append(poi)
    # Anything never placed (its cell was busy at every zoom) still deserves to
    # exist at the deepest zoom rather than vanish from the map entirely.
    for poi in pois:
        if not poi.minz:
            poi.minz = MAX_LABEL_ZOOM


def _cell(poi: Poi, zoom: int) -> tuple[int, int]:
    tile = mercantile.tile(poi.lon, poi.lat, zoom)
    return tile.x, tile.y


def build_city_place_labels(city: City, output_path: str | None = None) -> str:
    """Build one city's places.pmtiles. Returns the output path."""
    output_path = output_path or _output_path_for(city)
    pbf_path = os.path.join(city.data_dir, "source.osm.pbf")
    if not os.path.exists(pbf_path):
        raise FileNotFoundError(f"no source.osm.pbf for {city.id} at {pbf_path}")

    logger.info(f"[{city.id}] reading POIs from {pbf_path}")
    collector = PoiCollector(city.bbox)
    # locations=True back-fills node coordinates onto ways so closed ways get a
    # centroid; it is what makes parks and campuses labellable.
    collector.apply_file(pbf_path, locations=True, idx="flex_mem")
    pois = collector.pois
    logger.info(f"[{city.id}] {len(pois)} allowlisted named POIs in bbox")

    pois = dedup(pois)
    logger.info(f"[{city.id}] {len(pois)} after same-name dedup")

    assign_zooms(pois)
    by_zoom: dict[int, int] = {}
    for poi in pois:
        by_zoom[poi.minz] = by_zoom.get(poi.minz, 0) + 1
    logger.info(f"[{city.id}] labels per reveal zoom: "
                + ", ".join(f"z{z}={by_zoom[z]}" for z in sorted(by_zoom)))

    tiles = _collect_tiles(pois)
    logger.info(f"[{city.id}] encoding {len(tiles)} tiles")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp_path = output_path + ".tmp"
    south, west, north, east = city.bbox
    with open(tmp_path, "wb") as f:
        writer = Writer(f)
        for (z, x, y), features in sorted(tiles.items()):
            bounds = mercantile.bounds(mercantile.Tile(x, y, z))
            quantize = (bounds.west, bounds.south, bounds.east, bounds.north)
            data = mapbox_vector_tile.encode(
                [{"name": "places", "features": features}],
                quantize_bounds=quantize,
            )
            writer.write_tile(zxy_to_tileid(z, x, y), data)
        writer.finalize(
            header={
                "tile_type": TileType.MVT,
                "tile_compression": Compression.NONE,
                "min_zoom": MIN_TILE_ZOOM,
                "max_zoom": MAX_TILE_ZOOM,
                "min_lon_e7": int(west * 1e7),
                "min_lat_e7": int(south * 1e7),
                "max_lon_e7": int(east * 1e7),
                "max_lat_e7": int(north * 1e7),
            },
            metadata={
                "name": f"city-edit-places-{city.id}",
                "description": f"Named places and businesses for {city.name}",
                "format": "pbf",
                "type": "overlay",
                "minzoom": str(MIN_TILE_ZOOM),
                "maxzoom": str(MAX_TILE_ZOOM),
                "bounds": ",".join(str(b) for b in (west, south, east, north)),
                "vector_layers": json.dumps([{
                    "id": "places",
                    "fields": {"name": "String", "cat": "String", "minz": "Number"},
                }]),
            },
        )

    os.replace(tmp_path, output_path)
    size_mb = os.path.getsize(output_path) / 1024 / 1024
    logger.info(f"[{city.id}] place labels built: {output_path} ({size_mb:.1f} MB)")
    return output_path


def _collect_tiles(pois: list[Poi]) -> dict[tuple[int, int, int], list[dict]]:
    """Bucket POIs into MVT tiles, one point feature each.

    A point label is never clipped by a tile edge — MapLibre draws glyphs that
    overflow the tile its anchor sits in — so each POI goes into exactly ONE
    tile per zoom and no buffer is needed. Stable feature ids let MapLibre's
    cross-tile index recognise the same label across adjacent tiles instead of
    drawing it twice.
    """
    tiles: dict[tuple[int, int, int], list[dict]] = {}
    for index, poi in enumerate(pois):
        for zoom in range(MIN_TILE_ZOOM, MAX_TILE_ZOOM + 1):
            # A tile at MapLibre zoom Z is on screen at Leaflet zoom Z+1. The
            # top tile is overzoomed for everything deeper, so it carries the
            # whole set.
            if zoom < MAX_TILE_ZOOM and poi.minz > zoom + 1:
                continue
            tile = mercantile.tile(poi.lon, poi.lat, zoom)
            tiles.setdefault((zoom, tile.x, tile.y), []).append({
                "geometry": {"type": "Point", "coordinates": [poi.lon, poi.lat]},
                "properties": {"name": poi.name, "cat": poi.cat, "minz": poi.minz},
                "id": index,
            })
    return tiles


def _output_path_for(city: City) -> str:
    return os.path.join(city.data_dir, "places.pmtiles")


def _is_up_to_date(city: City, output_path: str) -> bool:
    pbf_path = os.path.join(city.data_dir, "source.osm.pbf")
    if not os.path.exists(output_path) or not os.path.exists(pbf_path):
        return False
    return os.path.getmtime(output_path) >= os.path.getmtime(pbf_path)


def main():
    parser = argparse.ArgumentParser(description="Build per-city place-label PMTiles")
    parser.add_argument("--city", help="City id to build (default: nyc unless --all)")
    parser.add_argument("--all", action="store_true", help="Build every registered city")
    parser.add_argument("--force", action="store_true", help="Rebuild even if up to date")
    parser.add_argument("-o", "--output", help="Explicit output path (single city only)")
    args = parser.parse_args()

    if args.all:
        targets = list(CITIES.values())
    else:
        city = get_city(args.city or "nyc")
        if city is None:
            logger.error(f"Unknown city '{args.city}'. Known: {list(CITIES)}")
            sys.exit(1)
        targets = [city]

    if args.output and len(targets) != 1:
        logger.error("--output can only be used when building a single city")
        sys.exit(1)

    for city in targets:
        out = args.output or _output_path_for(city)
        if not args.force and _is_up_to_date(city, out):
            logger.info(f"[{city.id}] place labels up to date, skipping (--force to rebuild)")
            continue
        try:
            build_city_place_labels(city, out)
        except Exception as e:
            logger.error(f"[{city.id}] place-label build failed: {e}")
            if not args.all:
                sys.exit(1)


if __name__ == "__main__":
    main()
