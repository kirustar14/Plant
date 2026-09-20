"""Build-order step 1 gate: prove the confirmed data sources return live data.

Run from the repo root:  .venv/bin/python scripts/verify_sources.py

Exits non-zero if a source is unusable, so this doubles as a pre-demo check. It
reports what it actually got — station names, road counts, timestamps — rather than a
bare pass/fail, because "it returned 200" and "it returned usable current data" are
different claims.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import config
from backend.app.sources import openaq, overpass
from backend.app.sources.http_cache import UpstreamError
from backend.app.zones import ZONES

logging.basicConfig(level=logging.INFO, format="  %(levelname)-7s %(name)s: %(message)s")

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def header(text: str) -> None:
    print(f"\n{'=' * 72}\n{text}\n{'=' * 72}")


def check_openaq() -> bool:
    header("OpenAQ v3 — live Delhi air quality")

    if not config.OPENAQ_API_KEY:
        print(f"{RED}FAIL{RESET}  OPENAQ_API_KEY is not set in .env")
        print(f"{DIM}      Get a free key: https://explore.openaq.org/register{RESET}")
        print(f"{DIM}      Then add it to .env (already gitignored).{RESET}")
        return False

    try:
        stations = openaq.fetch_delhi_air_quality()
    except UpstreamError as exc:
        print(f"{RED}FAIL{RESET}  {exc}")
        return False

    core = [s for s in stations if s.is_core]
    print(f"\n  Core stations (the brief's pull-tested set, pinned by ID):")
    print(f"  {'Station':<34} {'AQI':>6} {'Dominant':<8} {'PM2.5':>8}  Measured (UTC)")
    print(f"  {'-' * 34} {'-' * 6} {'-' * 8} {'-' * 8}  {'-' * 16}")
    for s in core:
        pm25 = s.value_of("pm25")
        latest = max((r.measured_at for r in s.readings), default=None)
        print(
            f"  {s.name[:34]:<34} {s.aqi or 0:>6.0f} {s.dominant_pollutant or '-':<8} "
            f"{pm25 if pm25 is not None else float('nan'):>8.1f}  "
            f"{latest.strftime('%Y-%m-%d %H:%M') if latest else '-'}"
        )

    baseline = openaq.city_baseline(stations)
    print(
        f"\n  City baseline: AQI {baseline['aqi']} ({baseline['aqi_band']}) "
        f"via {baseline['aqi_standard']}"
    )
    print(
        f"  Live reference-grade stations: {baseline['station_count']} "
        f"(core set: {baseline['core_stations_live']}/{baseline['core_stations_expected']})"
    )

    if baseline["core_stations_live"] < baseline["core_stations_expected"]:
        print(f"{YELLOW}  WARN{RESET}  a core station is not reporting — see warnings above")

    # Per-zone attribution is the point of using the wider station set.
    print(f"\n  Per-zone nearest monitor:")
    print(f"  {'Zone':<26} {'AQI':>6}  Attribution")
    print(f"  {'-' * 26} {'-' * 6}  {'-' * 40}")
    for zone in ZONES:
        zq = openaq.zone_air_quality(zone.latitude, zone.longitude, stations, baseline)
        print(f"  {zone.name[:26]:<26} {zq['aqi'] or 0:>6.0f}  {zq['attribution'][:46]}")

    print(f"\n{GREEN}PASS{RESET}  OpenAQ returning fresh data for {len(stations)} station(s)")
    return True


def check_overpass() -> bool:
    header("Overpass (OpenStreetMap) — road density + building floor-space")

    # Two zones is enough to prove the source works without hammering a flaky API;
    # the full set is fetched and cached when Model A first runs.
    sample = [ZONES[0], ZONES[3]]
    ok = True

    print(f"\n  {'Zone':<26} {'Roads':>6} {'Road km':>8} {'Bldgs':>6} {'FAR':>6} {'Traffic':>8}")
    print(f"  {'-' * 26} {'-' * 6} {'-' * 8} {'-' * 6} {'-' * 6} {'-' * 8}")

    for zone in sample:
        try:
            stats = overpass.fetch_zone_osm(zone.zone_id, *zone.bbox)
        except UpstreamError as exc:
            print(f"  {zone.name:<26} {RED}FAIL{RESET}  {exc}")
            ok = False
            continue

        flag = f" {DIM}[cached]{RESET}" if stats.from_cache else ""
        print(
            f"  {zone.name:<26} {stats.road_way_count:>6} {stats.road_length_km:>8.2f} "
            f"{stats.building_count:>6} {stats.floor_area_ratio:>6.2f} "
            f"{stats.traffic_proxy:>8.1f}{flag}"
        )
        if stats.road_way_count == 0 and stats.building_count == 0:
            print(f"{YELLOW}  WARN{RESET}  {zone.name} returned no OSM features — check the bbox")
            ok = False

    if ok:
        print(f"\n{GREEN}PASS{RESET}  Overpass returning real road + building geometry")
        print(f"{DIM}      (User-Agent header set — the default one gets 406'd){RESET}")
    return ok


def main() -> int:
    print(f"\n{'VeggieDelhi — data source verification':^72}")
    print(f"{DIM}{'build order step 1: confirm live data before building on top':^72}{RESET}")

    missing = config.missing_keys()
    if missing:
        print(f"\n{YELLOW}Keys not set:{RESET} {', '.join(missing)}")

    results = {"OpenAQ": check_openaq(), "Overpass": check_overpass()}

    header("Summary")
    for name, passed in results.items():
        print(f"  {GREEN + 'PASS' + RESET if passed else RED + 'FAIL' + RESET}  {name}")

    if all(results.values()):
        print(f"\n{GREEN}Both confirmed sources are live. Clear to build Model B.{RESET}\n")
        return 0
    print(f"\n{RED}Fix the failures above before building on top of these sources.{RESET}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
