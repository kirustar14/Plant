"""OpenStreetMap via Overpass — road density (traffic proxy) and building floor-space.

Two hard-won operational details:

1. Overpass rejects the default HTTP client User-Agent with 406. config.USER_AGENT is
   mandatory, not cosmetic.
2. The main endpoint is genuinely unreliable under load — measured here returning 504
   and then serving the identical query in 2.7s moments later. Requests therefore go
   through http_cache with retry, mirror fallback, and a disk cache, so a demo never
   depends on Overpass being healthy at that exact second.

Road density is a *proxy* for traffic volume, not a measurement of it: OSM records
where roads are and how they are classified, not how many vehicles use them. Weighting
by road class (motorway > residential) is what makes it a defensible proxy, and the
output is named accordingly.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Iterable

from .http_cache import fetch_json

log = logging.getLogger(__name__)

PRIMARY_ENDPOINT = "https://overpass-api.de/api/interpreter"
# Measured: kumi timed out past 100s during testing, so it is a fallback, never first.
FALLBACK_ENDPOINTS = (
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
)

# Traffic-load weights by OSM highway class. A motorway carries far more vehicle
# throughput per km than a residential street, so raw km-of-road would understate
# arterial corridors. Weights are ordinal judgement, and the unweighted km figure is
# reported alongside so the raw measurement stays visible.
HIGHWAY_WEIGHTS: dict[str, float] = {
    "motorway": 5.0, "motorway_link": 3.5,
    "trunk": 4.5, "trunk_link": 3.0,
    "primary": 3.5, "primary_link": 2.5,
    "secondary": 2.5, "secondary_link": 1.8,
    "tertiary": 1.5, "tertiary_link": 1.2,
    "residential": 1.0,
    "unclassified": 0.8,
    "living_street": 0.5,
    "service": 0.3,
}

# Mean storeys assumed when a building carries no building:levels tag, by building
# type. OSM level tagging is sparse in Delhi, so this is an explicit, inspectable
# assumption rather than a silent default of 1.
DEFAULT_LEVELS: dict[str, float] = {
    "apartments": 4.0, "residential": 2.0, "house": 1.5, "detached": 2.0,
    "commercial": 3.0, "retail": 2.0, "office": 5.0, "industrial": 1.5,
    "warehouse": 1.0, "school": 2.0, "hospital": 4.0, "hotel": 5.0,
    "yes": 2.0,
}
FALLBACK_LEVELS = 2.0

# Carriageway width by road class, metres, including shoulders and adjacent
# pavement. Used to estimate how much of a zone is sealed road surface.
#
# This matters for placement: subtracting only building footprint from zone area
# treats every road, pavement and plaza as plantable soil, which put all ten demo
# zones in the "ground" category including Connaught Place's colonnaded core. Roads
# are the dominant non-building surface in dense Delhi, so they have to be counted.
# OSM rarely tags `width`, so these are class-typical Indian urban values — an
# explicit assumption, and the derived figure is labelled an estimate.
ROAD_WIDTHS_M: dict[str, float] = {
    "motorway": 22.0, "motorway_link": 10.0,
    "trunk": 18.0, "trunk_link": 9.0,
    "primary": 15.0, "primary_link": 8.0,
    "secondary": 12.0, "secondary_link": 7.0,
    "tertiary": 9.0, "tertiary_link": 6.0,
    "residential": 7.0,
    "unclassified": 6.0,
    "living_street": 5.0,
    "pedestrian": 6.0,
    "service": 4.0,
    "footway": 2.0,
    "path": 1.5,
    "cycleway": 2.0,
    "steps": 1.5,
    "track": 3.0,
}
FALLBACK_ROAD_WIDTH_M = 5.0

EARTH_RADIUS_M = 6_371_000.0


@dataclass
class ZoneOSMStats:
    """Road and building measurements for one zone, plus the derived proxies."""
    zone_id: str
    area_km2: float
    road_way_count: int
    road_length_km: float
    weighted_road_length_km: float
    road_surface_m2: float
    building_count: int
    building_footprint_m2: float
    estimated_floor_space_m2: float
    buildings_with_level_tags: int
    from_cache: bool

    @property
    def road_density_km_per_km2(self) -> float:
        return self.road_length_km / self.area_km2 if self.area_km2 else 0.0

    @property
    def traffic_proxy(self) -> float:
        """Class-weighted road km per km2 — the Model A traffic input."""
        return self.weighted_road_length_km / self.area_km2 if self.area_km2 else 0.0

    @property
    def footprint_ratio(self) -> float:
        """Fraction of zone area covered by building footprint (0-1, clamped)."""
        if not self.area_km2:
            return 0.0
        return min(1.0, self.building_footprint_m2 / (self.area_km2 * 1e6))

    @property
    def road_surface_ratio(self) -> float:
        """Estimated fraction of zone area that is sealed road surface (0-1)."""
        if not self.area_km2:
            return 0.0
        return min(1.0, self.road_surface_m2 / (self.area_km2 * 1e6))

    @property
    def plantable_ground_ratio(self) -> float:
        """Estimated fraction of the zone that could take ground planting.

        Zone area minus buildings minus road surface. Still an over-estimate — it
        counts private courtyards, rail land, and paved plazas that carry no
        highway tag — so it is an upper bound on plantable ground, and the
        placement thresholds are set with that in mind.
        """
        return max(0.0, 1.0 - self.footprint_ratio - self.road_surface_ratio)

    @property
    def floor_area_ratio(self) -> float:
        """FAR: total floor space / land area. Drives potted-vs-ground placement."""
        if not self.area_km2:
            return 0.0
        return self.estimated_floor_space_m2 / (self.area_km2 * 1e6)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _way_length_m(geometry: Iterable[dict[str, float]]) -> float:
    pts = [(p["lat"], p["lon"]) for p in geometry if "lat" in p and "lon" in p]
    return sum(
        haversine_m(*pts[i], *pts[i + 1]) for i in range(len(pts) - 1)
    )


def _polygon_area_m2(geometry: list[dict[str, float]]) -> float:
    """Shoelace area on a local equirectangular projection.

    Accurate to well under a percent at building scale, and avoids pulling in a
    projection dependency for what is a few lines of trigonometry.
    """
    pts = [(p["lat"], p["lon"]) for p in geometry if "lat" in p and "lon" in p]
    if len(pts) < 3:
        return 0.0
    lat0 = sum(p[0] for p in pts) / len(pts)
    cos_lat0 = math.cos(math.radians(lat0))
    xy = [
        (math.radians(lon) * EARTH_RADIUS_M * cos_lat0, math.radians(lat) * EARTH_RADIUS_M)
        for lat, lon in pts
    ]
    total = 0.0
    for i in range(len(xy)):
        x1, y1 = xy[i]
        x2, y2 = xy[(i + 1) % len(xy)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _parse_levels(tags: dict[str, Any]) -> tuple[float, bool]:
    """Storey count for a building. Returns (levels, was_explicitly_tagged)."""
    raw = tags.get("building:levels")
    if raw is not None:
        try:
            levels = float(str(raw).split(";")[0].split(",")[0].strip())
            if 0 < levels < 200:  # guard against obvious tagging errors
                return levels, True
        except ValueError:
            pass
    return DEFAULT_LEVELS.get(str(tags.get("building", "")).lower(), FALLBACK_LEVELS), False


def bbox_area_km2(south: float, west: float, north: float, east: float) -> float:
    height_m = haversine_m(south, west, north, west)
    mid_lat = (south + north) / 2
    width_m = haversine_m(mid_lat, west, mid_lat, east)
    return (height_m * width_m) / 1e6


def fetch_zone_osm(
    zone_id: str,
    south: float,
    west: float,
    north: float,
    east: float,
    *,
    max_age_s: float = 7 * 86_400,
) -> ZoneOSMStats:
    """Roads + buildings for one zone bbox, in a single Overpass query.

    OSM geometry is effectively static week to week, so the cache window is long —
    this is also what makes repeated demo runs instant and Overpass-independent.
    """
    bbox = f"{south},{west},{north},{east}"
    query = (
        "[out:json][timeout:90];"
        f'(way["highway"]({bbox});way["building"]({bbox}););'
        "out geom tags;"
    )

    payload, from_cache = fetch_json(
        "POST",
        PRIMARY_ENDPOINT,
        namespace="overpass_zone",
        cache_key=f"{zone_id}|{bbox}|roads_buildings_v1",
        max_age_s=max_age_s,
        fallback_urls=FALLBACK_ENDPOINTS,
        data={"data": query},
        timeout=120.0,
    )

    road_ways = 0
    road_len = 0.0
    weighted_len = 0.0
    road_area = 0.0
    bld_count = 0
    bld_area = 0.0
    floor_space = 0.0
    tagged_levels = 0

    for el in payload.get("elements", []) or []:
        tags = el.get("tags") or {}
        geom = el.get("geometry") or []

        if "highway" in tags:
            length = _way_length_m(geom)
            if length <= 0:
                continue
            klass = tags["highway"]
            road_ways += 1
            road_len += length
            weighted_len += length * HIGHWAY_WEIGHTS.get(klass, 0.5)
            # Prefer an explicit OSM width tag when present; fall back to class-typical.
            width = ROAD_WIDTHS_M.get(klass, FALLBACK_ROAD_WIDTH_M)
            raw_width = tags.get("width")
            if raw_width:
                try:
                    tagged = float(str(raw_width).split()[0].replace(",", "."))
                    if 0.5 < tagged < 60:
                        width = tagged
                except ValueError:
                    pass
            road_area += length * width

        elif "building" in tags:
            area = _polygon_area_m2(geom)
            if area <= 0:
                continue
            levels, explicit = _parse_levels(tags)
            bld_count += 1
            bld_area += area
            floor_space += area * levels
            tagged_levels += int(explicit)

    stats = ZoneOSMStats(
        zone_id=zone_id,
        area_km2=bbox_area_km2(south, west, north, east),
        road_way_count=road_ways,
        road_length_km=road_len / 1000.0,
        weighted_road_length_km=weighted_len / 1000.0,
        road_surface_m2=road_area,
        building_count=bld_count,
        building_footprint_m2=bld_area,
        estimated_floor_space_m2=floor_space,
        buildings_with_level_tags=tagged_levels,
        from_cache=from_cache,
    )

    log.info(
        "Overpass %s: %d roads (%.1f km, %.0f%% surface), %d buildings "
        "(%.0f%% footprint, FAR %.2f, %d/%d level-tagged) -> %.0f%% plantable%s",
        zone_id, road_ways, stats.road_length_km, stats.road_surface_ratio * 100,
        bld_count, stats.footprint_ratio * 100, stats.floor_area_ratio,
        tagged_levels, bld_count, stats.plantable_ground_ratio * 100,
        " [cached]" if from_cache else "",
    )
    return stats
