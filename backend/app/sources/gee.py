"""Google Earth Engine — NDVI (existing vegetation) and land surface temperature.

Supplies two of Model A's inputs from satellite imagery:

* **NDVI** from Sentinel-2 surface reflectance (10m). Normalised difference of NIR
  and red; the standard measure of green biomass. Drives the "has existing
  vegetation" / "bare" signal on each zone block.
* **Land surface temperature** from Landsat 8/9 thermal infrared (~100m, resampled
  to 30m). The heat-island delta is computed as a zone's LST minus the city-wide
  median, so it measures relative thermal load rather than absolute summer heat —
  every Delhi zone is hot in absolute terms, and the useful signal is which ones are
  hotter than the city around them.

AVAILABILITY. Earth Engine needs a registered Cloud project, an enabled API, and a
service account holding serviceusage.serviceUsageConsumer plus an earthengine role.
When any of that is missing, `initialize()` returns False and every fetch returns
None rather than a substituted value. Model A then scores on the inputs it does have
and reports NDVI/LST as unavailable — the zone is honestly described as
"vegetation signal unavailable", never silently defaulted to "bare".
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from ..config import (
    GEE_PROJECT_ID,
    GEE_SERVICE_ACCOUNT_EMAIL,
    GEE_SERVICE_ACCOUNT_JSON,
    REPO_ROOT,
)
from .http_cache import read_cache, write_cache

log = logging.getLogger(__name__)

# Sentinel-2 surface reflectance, cloud-masked via the scene classification band.
S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
LANDSAT_COLLECTION = "LANDSAT/LC09/C02/T1_L2"
LANDSAT8_COLLECTION = "LANDSAT/LC08/C02/T1_L2"

MAX_CLOUD_PCT = 35
COMPOSITE_DAYS = 120  # wide enough to clear monsoon cloud over Delhi
CACHE_AGE_S = 7 * 86_400

# NDVI above this is treated as established vegetation. 0.3 is a conventional
# threshold separating sparse/bare surfaces from vegetated ones; Delhi's parks sit
# well above it and its industrial estates well below.
NDVI_VEGETATED_THRESHOLD = 0.30

_initialized: bool | None = None
_ee = None


@dataclass(frozen=True)
class ZoneVegetationThermal:
    """Satellite-derived measurements for one zone. Fields are None when unavailable."""
    zone_id: str
    ndvi: float | None
    ndvi_scene_count: int
    land_surface_temp_c: float | None
    lst_scene_count: int
    composite_start: str
    composite_end: str
    source: str

    @property
    def has_existing_vegetation(self) -> bool | None:
        if self.ndvi is None:
            return None
        return self.ndvi >= NDVI_VEGETATED_THRESHOLD

    @property
    def vegetation_label(self) -> str:
        if self.ndvi is None:
            return "vegetation signal unavailable"
        return "has existing vegetation" if self.has_existing_vegetation else "bare"


def initialize() -> bool:
    """Authenticate to Earth Engine. Returns False if unavailable, never raises.

    Result is memoised: a missing-permission failure will not resolve inside one
    process, and retrying it per zone would add latency for nothing.
    """
    global _initialized, _ee
    if _initialized is not None:
        return _initialized

    key_path = Path(GEE_SERVICE_ACCOUNT_JSON)
    if not key_path.is_absolute():
        key_path = REPO_ROOT / key_path

    if not key_path.exists():
        log.warning("GEE: service account key not found at %s — NDVI/LST unavailable", key_path)
        _initialized = False
        return False

    try:
        import ee  # imported lazily so the app runs without earthengine-api installed
    except ImportError:
        log.warning("GEE: earthengine-api not installed — NDVI/LST unavailable")
        _initialized = False
        return False

    try:
        info = json.loads(key_path.read_text())
        email = GEE_SERVICE_ACCOUNT_EMAIL or info.get("client_email")
        project = GEE_PROJECT_ID or info.get("project_id")
        creds = ee.ServiceAccountCredentials(email, str(key_path))
        ee.Initialize(creds, project=project)
        # Force a round trip: Initialize alone can succeed while the API is unusable.
        ee.Number(1).getInfo()
    except Exception as exc:  # EEException and friends are not a stable public type
        msg = str(exc)
        log.warning("GEE unavailable — NDVI/LST will be reported as missing: %s", msg[:220])
        if "serviceusage" in msg or "permission" in msg.lower():
            log.warning(
                "GEE: grant the service account roles/serviceusage.serviceUsageConsumer "
                "and an earthengine role, enable earthengine.googleapis.com, and "
                "register the project at https://code.earthengine.google.com/register"
            )
        _initialized = False
        return False

    _ee = ee
    _initialized = True
    log.info("GEE: initialized on project %s", GEE_PROJECT_ID or "(from key)")
    return True


def _mask_s2_clouds(img):
    """Mask cloud and cirrus using the Sentinel-2 scene classification layer."""
    scl = img.select("SCL")
    # 3 shadow, 8 cloud medium, 9 cloud high, 10 cirrus
    bad = scl.eq(3).Or(scl.eq(8)).Or(scl.eq(9)).Or(scl.eq(10))
    return img.updateMask(bad.Not())


def fetch_zone_vegetation_thermal(
    zone_id: str,
    latitude: float,
    longitude: float,
    radius_m: int = 300,
    use_cache: bool = True,
) -> ZoneVegetationThermal:
    """NDVI and LST for one zone. Returns a result with None fields if GEE is down."""
    end = date.today()
    start = end - timedelta(days=COMPOSITE_DAYS)
    cache_key = f"{zone_id}|{latitude:.4f},{longitude:.4f}|{radius_m}|{start}|{end}"

    if use_cache:
        cached = read_cache("gee_zone", cache_key, max_age_s=CACHE_AGE_S)
        if cached is not None:
            return ZoneVegetationThermal(**cached)

    unavailable = ZoneVegetationThermal(
        zone_id=zone_id, ndvi=None, ndvi_scene_count=0,
        land_surface_temp_c=None, lst_scene_count=0,
        composite_start=str(start), composite_end=str(end),
        source="Earth Engine (unavailable)",
    )

    if not initialize():
        return unavailable

    ee = _ee
    try:
        region = ee.Geometry.Point([longitude, latitude]).buffer(radius_m)

        # --- NDVI from Sentinel-2 --------------------------------------------
        s2 = (
            ee.ImageCollection(S2_COLLECTION)
            .filterBounds(region)
            .filterDate(str(start), str(end))
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", MAX_CLOUD_PCT))
            .map(_mask_s2_clouds)
        )
        s2_count = s2.size().getInfo()
        ndvi_val = None
        if s2_count:
            ndvi_img = s2.median().normalizedDifference(["B8", "B4"]).rename("NDVI")
            stats = ndvi_img.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=region, scale=10, maxPixels=1e9
            ).getInfo()
            ndvi_val = stats.get("NDVI")

        # --- LST from Landsat 8/9 thermal ------------------------------------
        landsat = (
            ee.ImageCollection(LANDSAT_COLLECTION)
            .merge(ee.ImageCollection(LANDSAT8_COLLECTION))
            .filterBounds(region)
            .filterDate(str(start), str(end))
            .filter(ee.Filter.lt("CLOUD_COVER", MAX_CLOUD_PCT))
        )
        ls_count = landsat.size().getInfo()
        lst_val = None
        if ls_count:
            # Collection 2 Level-2 ST_B10 scaling: K = DN * 0.00341802 + 149.0
            lst_img = (
                landsat.median()
                .select("ST_B10")
                .multiply(0.00341802)
                .add(149.0)
                .subtract(273.15)  # to Celsius
                .rename("LST")
            )
            stats = lst_img.reduceRegion(
                reducer=ee.Reducer.mean(), geometry=region, scale=30, maxPixels=1e9
            ).getInfo()
            lst_val = stats.get("LST")

    except Exception as exc:
        log.warning("GEE: fetch failed for %s: %s", zone_id, str(exc)[:220])
        return unavailable

    result = ZoneVegetationThermal(
        zone_id=zone_id,
        ndvi=round(ndvi_val, 4) if ndvi_val is not None else None,
        ndvi_scene_count=int(s2_count or 0),
        land_surface_temp_c=round(lst_val, 2) if lst_val is not None else None,
        lst_scene_count=int(ls_count or 0),
        composite_start=str(start),
        composite_end=str(end),
        source=f"Sentinel-2 {S2_COLLECTION} / Landsat 8-9 C02 L2",
    )

    if result.ndvi is None:
        log.warning("GEE: no usable Sentinel-2 pixels for %s", zone_id)
    else:
        log.info(
            "GEE %s: NDVI %.3f (%d scenes), LST %s C (%d scenes)",
            zone_id, result.ndvi, result.ndvi_scene_count,
            f"{result.land_surface_temp_c:.1f}" if result.land_surface_temp_c else "n/a",
            result.lst_scene_count,
        )

    write_cache("gee_zone", cache_key, result.__dict__)
    return result


def heat_island_delta(
    zone_lst_c: float | None, all_zone_lst: list[float]
) -> float | None:
    """A zone's LST minus the median across all measured zones.

    Relative rather than absolute: every Delhi zone is hot, and what matters for
    prioritising is which are hotter than their surroundings. Needs at least three
    measured zones to be meaningful.
    """
    if zone_lst_c is None:
        return None
    valid = sorted(v for v in all_zone_lst if v is not None)
    if len(valid) < 3:
        return None
    mid = len(valid) // 2
    median = valid[mid] if len(valid) % 2 else (valid[mid - 1] + valid[mid]) / 2
    return round(zone_lst_c - median, 2)
