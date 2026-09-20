"""Model A — Zone Scoring Model.

Inputs : AQI/CO2 (OpenAQ), traffic density (OSM road density proxy), building and
         floor-space (OSM footprints), NDVI and land surface temperature (Earth
         Engine), light pollution.
Outputs: a priority score per zone and a placement category (ground / wall-hanging /
         potted), emitted as a ZoneProfile for Model B to consume.

Model A does not import Model B. The only contract between them is app.schemas.
"""
from __future__ import annotations

import logging

from ...app.schemas import PlacementCategory, ZoneProfile
from ...app.sources import gee, openaq, overpass
from ...app.sources.http_cache import UpstreamError
from ...app.zones import ZONES, Zone
from .scoring import ZoneScore, apply_score, decide_placement, score_zone

log = logging.getLogger(__name__)

__all__ = [
    "PlacementCategory",
    "ZoneProfile",
    "ZoneScore",
    "apply_score",
    "build_profiles",
    "decide_placement",
    "score_zone",
    "score_all_zones",
]


def build_profiles(zones: tuple[Zone, ...] = ZONES) -> tuple[list[ZoneProfile], dict]:
    """Gather every live measurement for each zone into ZoneProfiles.

    Returns (profiles, city_baseline). Air quality is fetched once for the city and
    attributed per zone from the nearest live monitor; OSM and Earth Engine are
    fetched per zone. Any source that fails leaves its fields None and records a
    data gap — the pipeline does not abort, and it does not substitute values.
    """
    # --- Air quality (city-wide, then attributed per zone) -------------------
    stations, baseline = [], {}
    try:
        stations = openaq.fetch_delhi_air_quality()
        baseline = openaq.city_baseline(stations)
    except UpstreamError as exc:
        log.warning("Model A: air quality unavailable: %s", exc)

    profiles: list[ZoneProfile] = []
    lst_values: list[float] = []

    for zone in zones:
        profile = ZoneProfile(
            zone_id=zone.zone_id,
            name=zone.name,
            latitude=zone.latitude,
            longitude=zone.longitude,
        )

        # --- Air quality ------------------------------------------------------
        if stations:
            zq = openaq.zone_air_quality(zone.latitude, zone.longitude, stations, baseline)
            profile.aqi = zq["aqi"]
            profile.pm25_ugm3 = zq.get("pm25_ugm3")
            profile.aqi_attribution = zq["attribution"]
            profile.sources["air_quality"] = "OpenAQ v3 (CPCB National AQI)"
        else:
            profile.data_gaps.append("air quality unavailable")

        # --- Roads and buildings ---------------------------------------------
        try:
            osm = overpass.fetch_zone_osm(zone.zone_id, *zone.bbox)
            profile.traffic_proxy = round(osm.traffic_proxy, 3)
            profile.road_density_km_per_km2 = round(osm.road_density_km_per_km2, 3)
            profile.floor_area_ratio = round(osm.floor_area_ratio, 4)
            profile.building_footprint_ratio = round(osm.footprint_ratio, 4)
            profile.road_surface_ratio = round(osm.road_surface_ratio, 4)
            profile.plantable_ground_ratio = round(osm.plantable_ground_ratio, 4)
            profile.sources["traffic_and_buildings"] = "OpenStreetMap via Overpass"
        except UpstreamError as exc:
            log.warning("Model A: OSM unavailable for %s: %s", zone.zone_id, exc)
            profile.data_gaps.append("road and building data unavailable")

        # --- Satellite: NDVI + LST --------------------------------------------
        sat = gee.fetch_zone_vegetation_thermal(
            zone.zone_id, zone.latitude, zone.longitude
        )
        profile.ndvi = sat.ndvi
        profile.land_surface_temp_c = sat.land_surface_temp_c
        profile.has_existing_vegetation = sat.has_existing_vegetation
        if sat.ndvi is not None:
            profile.sources["vegetation_and_thermal"] = sat.source
            if sat.land_surface_temp_c is not None:
                lst_values.append(sat.land_surface_temp_c)
        else:
            profile.data_gaps.append(
                "NDVI/land surface temperature unavailable (Earth Engine)"
            )

        profiles.append(profile)

    # --- Heat island needs the full set, so it is a second pass ---------------
    for profile in profiles:
        profile.heat_island_delta_c = gee.heat_island_delta(
            profile.land_surface_temp_c, lst_values
        )

    return profiles, baseline


def score_all_zones(
    zones: tuple[Zone, ...] = ZONES,
) -> tuple[list[ZoneProfile], list[ZoneScore], dict]:
    """Run Model A end to end: gather measurements, score, and attach results."""
    profiles, baseline = build_profiles(zones)
    scores: list[ZoneScore] = []

    for profile in profiles:
        try:
            score = score_zone(profile)
        except ValueError as exc:
            log.error("Model A: %s", exc)
            continue
        apply_score(profile, score)
        scores.append(score)

    scores.sort(key=lambda s: s.priority_score, reverse=True)
    log.info(
        "Model A: scored %d/%d zones (priority %.3f-%.3f)",
        len(scores), len(profiles),
        scores[-1].priority_score if scores else 0.0,
        scores[0].priority_score if scores else 0.0,
    )
    return profiles, scores, baseline
