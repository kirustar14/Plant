"""OpenAQ v3 — live air quality for Delhi.

Three things make this source usable rather than misleading:

1. Station liveness is decided by each station's own `datetimeLast`, never by name.
   OpenAQ returns 96 stations for the Delhi radius, 60 of them currently live, and it
   carries duplicate names under different IDs where one is live and the other died
   years ago (see config.CORE_STATION_IDS). Name matching picks dead duplicates;
   timestamps do not. Individual readings are re-checked against the same cutoff, so
   a station that goes quiet mid-demo drops out instead of freezing an old number.

2. Community low-cost sensors are labelled and kept out of scoring. Delhi has many,
   they report more often than the regulatory network, and they are less accurate —
   mixing them into a headline AQI silently would trade accuracy for freshness.

3. Raw concentrations become AQI via the CPCB National Air Quality Index breakpoints
   (CPCB, 2014) — the official Indian standard — not the US EPA scale, which would
   report a materially different number for the same Delhi air.

Note on CO2: OpenAQ measures CO (carbon monoxide), not CO2. No CO2 concentration is
read here; the CO2 figure elsewhere in the app is a *projected sequestration*
estimate from i-Tree coefficients, and is labelled as such.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import (
    CORE_STATION_IDS,
    DELHI_CENTER,
    MAX_READING_AGE_DAYS,
    MAX_ZONE_STATION_DISTANCE_KM,
    OPENAQ_API_KEY,
    REFERENCE_NETWORKS,
)
from .http_cache import UpstreamError, fetch_json

log = logging.getLogger(__name__)

API_ROOT = "https://api.openaq.org/v3"
SEARCH_RADIUS_M = 25_000

# CPCB National AQI sub-index breakpoints.
# parameter -> [(conc_low, conc_high, aqi_low, aqi_high), ...]
# Units: pm25/pm10/no2/so2/o3 in ug/m3, co in mg/m3.
CPCB_BREAKPOINTS: dict[str, list[tuple[float, float, float, float]]] = {
    "pm25": [(0, 30, 0, 50), (30, 60, 51, 100), (60, 90, 101, 200),
             (90, 120, 201, 300), (120, 250, 301, 400), (250, 1000, 401, 500)],
    "pm10": [(0, 50, 0, 50), (50, 100, 51, 100), (100, 250, 101, 200),
             (250, 350, 201, 300), (350, 430, 301, 400), (430, 1000, 401, 500)],
    "no2":  [(0, 40, 0, 50), (40, 80, 51, 100), (80, 180, 101, 200),
             (180, 280, 201, 300), (280, 400, 301, 400), (400, 1000, 401, 500)],
    "so2":  [(0, 40, 0, 50), (40, 80, 51, 100), (80, 380, 101, 200),
             (380, 800, 201, 300), (800, 1600, 301, 400), (1600, 3000, 401, 500)],
    "o3":   [(0, 50, 0, 50), (50, 100, 51, 100), (100, 168, 101, 200),
             (168, 208, 201, 300), (208, 748, 301, 400), (748, 1500, 401, 500)],
    "co":   [(0, 1.0, 0, 50), (1.0, 2.0, 51, 100), (2.0, 10, 101, 200),
             (10, 17, 201, 300), (17, 34, 301, 400), (34, 60, 401, 500)],
}

AQI_BANDS = [
    (50, "Good"), (100, "Satisfactory"), (200, "Moderate"),
    (300, "Poor"), (400, "Very Poor"), (500, "Severe"),
]


def cpcb_sub_index(parameter: str, value: float) -> float | None:
    """CPCB sub-index for one pollutant, or None if we have no breakpoints for it."""
    table = CPCB_BREAKPOINTS.get(parameter)
    if table is None or value < 0:
        return None
    for c_lo, c_hi, i_lo, i_hi in table:
        if c_lo <= value <= c_hi:
            # Linear interpolation within the band, per CPCB method.
            return i_lo + (i_hi - i_lo) * (value - c_lo) / (c_hi - c_lo)
    return 500.0  # above the top band


def aqi_band(aqi: float) -> str:
    for ceiling, label in AQI_BANDS:
        if aqi <= ceiling:
            return label
    return "Severe"


@dataclass
class Reading:
    parameter: str
    value: float
    unit: str
    measured_at: datetime
    sub_index: float | None = None


@dataclass
class Station:
    station_id: int
    name: str
    latitude: float
    longitude: float
    readings: list[Reading] = field(default_factory=list)
    is_reference_grade: bool = True
    is_core: bool = False

    @property
    def aqi(self) -> float | None:
        """CPCB AQI = the maximum sub-index across measured pollutants."""
        subs = [r.sub_index for r in self.readings if r.sub_index is not None]
        return max(subs) if subs else None

    @property
    def dominant_pollutant(self) -> str | None:
        scored = [r for r in self.readings if r.sub_index is not None]
        return max(scored, key=lambda r: r.sub_index).parameter if scored else None

    def value_of(self, parameter: str) -> float | None:
        for r in self.readings:
            if r.parameter == parameter:
                return r.value
        return None


def _auth_headers() -> dict[str, str]:
    if not OPENAQ_API_KEY:
        raise UpstreamError(
            "OPENAQ_API_KEY is not set. Add it to .env (free key: "
            "https://explore.openaq.org/register). Refusing to proceed without it "
            "rather than substituting placeholder air quality data."
        )
    return {"X-API-Key": OPENAQ_API_KEY}


def _parse_dt(raw: Any) -> datetime | None:
    """OpenAQ datetimes arrive as {'utc': ..., 'local': ...} or a bare string."""
    if isinstance(raw, dict):
        raw = raw.get("utc") or raw.get("local")
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _is_reference_grade(name: str) -> bool:
    """True for government regulatory monitors, False for community low-cost sensors.

    Regulatory stations are suffixed with their operating network ("… - DPCC").
    Community sensors carry free-form names ("GK1 (Oberoi Terrace)", "Air Check").
    """
    return any(f"- {net}" in name or name.endswith(net) for net in REFERENCE_NETWORKS)


def discover_stations(
    max_age_s: float = 3_600,
    reference_only: bool = True,
) -> list[dict[str, Any]]:
    """Live stations near Delhi centre, selected on `datetimeLast`, not on name.

    Cached for an hour: the liveness timestamp is the thing being read, so this must
    not be cached for a day the way static metadata could be.
    """
    lat, lon = DELHI_CENTER
    payload, from_cache = fetch_json(
        "GET",
        f"{API_ROOT}/locations",
        namespace="openaq_locations",
        cache_key=f"delhi_{lat}_{lon}_{SEARCH_RADIUS_M}",
        max_age_s=max_age_s,
        headers=_auth_headers(),
        params={
            "coordinates": f"{lat},{lon}",
            "radius": SEARCH_RADIUS_M,
            "limit": 1000,
        },
        timeout=30.0,
    )

    results = payload.get("results", []) or []
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_READING_AGE_DAYS)

    live, stale, low_cost = [], 0, 0
    for loc in results:
        last = _parse_dt(loc.get("datetimeLast"))
        if last is None or last < cutoff:
            stale += 1
            continue
        name = str(loc.get("name", ""))
        if reference_only and not _is_reference_grade(name):
            low_cost += 1
            continue
        live.append(loc)

    log.info(
        "OpenAQ: %d stations in radius — %d live%s, %d stale/dead, %d low-cost excluded%s",
        len(results), len(live), " reference-grade" if reference_only else "",
        stale, low_cost, " (cached)" if from_cache else "",
    )

    # The brief's pull-tested core set should always be present; warn if one drops.
    live_ids = {loc.get("id") for loc in live}
    for sid, name in CORE_STATION_IDS.items():
        if sid not in live_ids:
            log.warning("OpenAQ: core station %s (id %s) is not currently live", name, sid)

    if not live:
        raise UpstreamError(
            "OpenAQ returned no station with a reading inside the freshness window."
        )
    return live


def fetch_station_latest(location: dict[str, Any], max_age_s: float = 900) -> Station | None:
    """Latest readings for one station, with stale readings discarded.

    Returns None if the station has no reading inside MAX_READING_AGE_DAYS — the
    caller then simply has fewer stations, rather than a current-looking old value.
    """
    loc_id = location.get("id")
    coords = location.get("coordinates") or {}
    name = location.get("name", f"station-{loc_id}")
    station = Station(
        station_id=loc_id,
        name=name,
        latitude=coords.get("latitude"),
        longitude=coords.get("longitude"),
        is_reference_grade=_is_reference_grade(name),
        is_core=loc_id in CORE_STATION_IDS,
    )

    # sensor id -> parameter name, from the station's own metadata.
    sensor_meta: dict[int, tuple[str, str]] = {}
    for sensor in location.get("sensors", []) or []:
        param = (sensor.get("parameter") or {})
        name = param.get("name")
        if sensor.get("id") is not None and name:
            sensor_meta[sensor["id"]] = (name, param.get("units", ""))

    try:
        payload, _ = fetch_json(
            "GET",
            f"{API_ROOT}/locations/{loc_id}/latest",
            namespace="openaq_latest",
            cache_key=f"loc_{loc_id}",
            max_age_s=max_age_s,
            headers=_auth_headers(),
            timeout=30.0,
        )
    except UpstreamError as exc:
        log.warning("OpenAQ: no latest data for %s: %s", station.name, exc)
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_READING_AGE_DAYS)
    stale_count = 0

    for row in payload.get("results", []) or []:
        measured_at = _parse_dt(row.get("datetime"))
        value = row.get("value")
        if measured_at is None or value is None:
            continue
        if measured_at < cutoff:
            stale_count += 1
            continue

        param, unit = sensor_meta.get(row.get("sensorsId"), (None, ""))
        if not param:
            continue

        station.readings.append(
            Reading(
                parameter=param,
                value=float(value),
                unit=unit,
                measured_at=measured_at,
                sub_index=cpcb_sub_index(param, float(value)),
            )
        )

    if stale_count:
        log.info("OpenAQ: dropped %d stale reading(s) for %s", stale_count, station.name)
    if not station.readings:
        log.warning(
            "OpenAQ: %s has no reading newer than %d days — excluding it",
            station.name, MAX_READING_AGE_DAYS,
        )
        return None
    return station


def fetch_delhi_air_quality() -> list[Station]:
    """Every confirmed-live Delhi station that has a fresh reading right now."""
    stations = []
    for loc in discover_stations():
        station = fetch_station_latest(loc)
        if station is not None:
            stations.append(station)
    if not stations:
        raise UpstreamError(
            "No Delhi station returned a reading inside the freshness window. "
            "Not falling back to synthetic values."
        )
    log.info("OpenAQ: %d station(s) reporting fresh data", len(stations))
    return stations


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import math
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def nearest_station(
    latitude: float,
    longitude: float,
    stations: list[Station],
) -> tuple[Station, float] | None:
    """Closest live monitor to a point, with its distance in km.

    Returns None when nothing is inside MAX_ZONE_STATION_DISTANCE_KM — a monitor
    20km away says nothing useful about a 600m zone, and the caller is expected to
    fall back to the city baseline and label it as such.
    """
    candidates = [
        (s, haversine_km(latitude, longitude, s.latitude, s.longitude))
        for s in stations
        if s.latitude is not None and s.longitude is not None and s.aqi is not None
    ]
    if not candidates:
        return None
    station, distance = min(candidates, key=lambda pair: pair[1])
    if distance > MAX_ZONE_STATION_DISTANCE_KM:
        log.info(
            "No monitor within %.1f km of %.4f,%.4f (closest %s at %.1f km)",
            MAX_ZONE_STATION_DISTANCE_KM, latitude, longitude, station.name, distance,
        )
        return None
    return station, distance


def zone_air_quality(
    latitude: float,
    longitude: float,
    stations: list[Station],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    """Air quality attributed to one zone, from its nearest live monitor.

    Always states its own provenance — which monitor, how far away, or that it fell
    back to the city mean — so a zone-level number is never mistaken for an on-site
    measurement.
    """
    found = nearest_station(latitude, longitude, stations)
    if found is None:
        return {
            "aqi": baseline.get("aqi"),
            "aqi_band": baseline.get("aqi_band"),
            "pm25_ugm3": baseline.get("pm25_ugm3"),
            "source_station": None,
            "station_distance_km": None,
            "attribution": "city-wide mean (no monitor within range)",
        }

    station, distance = found
    return {
        "aqi": round(station.aqi, 1) if station.aqi is not None else None,
        "aqi_band": aqi_band(station.aqi) if station.aqi is not None else None,
        "pm25_ugm3": round(v, 1) if (v := station.value_of("pm25")) is not None else None,
        "pm10_ugm3": round(v, 1) if (v := station.value_of("pm10")) is not None else None,
        "no2_ugm3": round(v, 1) if (v := station.value_of("no2")) is not None else None,
        "dominant_pollutant": station.dominant_pollutant,
        "source_station": station.name,
        "station_distance_km": round(distance, 2),
        "attribution": f"nearest monitor: {station.name} ({distance:.1f} km)",
    }


def city_baseline(stations: list[Station]) -> dict[str, Any]:
    """City-wide baseline for the dashboard top bar, averaged over live stations."""
    aqis = [s.aqi for s in stations if s.aqi is not None]
    city_aqi = sum(aqis) / len(aqis) if aqis else None

    def mean_of(param: str) -> float | None:
        vals = [v for v in (s.value_of(param) for s in stations) if v is not None]
        return sum(vals) / len(vals) if vals else None

    latest = max(
        (r.measured_at for s in stations for r in s.readings),
        default=None,
    )

    return {
        "aqi": round(city_aqi, 1) if city_aqi is not None else None,
        "aqi_band": aqi_band(city_aqi) if city_aqi is not None else None,
        "aqi_standard": "CPCB National AQI (India)",
        "pm25_ugm3": round(v, 1) if (v := mean_of("pm25")) is not None else None,
        "pm10_ugm3": round(v, 1) if (v := mean_of("pm10")) is not None else None,
        "no2_ugm3": round(v, 1) if (v := mean_of("no2")) is not None else None,
        "co_mgm3": round(v, 2) if (v := mean_of("co")) is not None else None,
        "station_count": len(stations),
        "stations": [s.name for s in stations],
        "core_stations_live": sum(1 for s in stations if s.is_core),
        "core_stations_expected": len(CORE_STATION_IDS),
        "reference_grade_only": all(s.is_reference_grade for s in stations),
        "measured_at": latest.isoformat() if latest else None,
        "source": "OpenAQ v3",
    }
