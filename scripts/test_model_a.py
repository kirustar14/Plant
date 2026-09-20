"""Run Model A over all 10 Delhi zones on live data.

    .venv/bin/python scripts/test_model_a.py

Reports which signals were actually measured per zone, so a score computed without
satellite data is visibly distinguishable from a complete one.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.models import model_a_zone_scoring as ma

logging.basicConfig(level=logging.WARNING, format="  %(levelname)s %(name)s: %(message)s")
BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def rule(t: str) -> None:
    print(f"\n{BOLD}{t}{RESET}\n{'=' * 86}")


def main() -> int:
    rule("Model A — gathering live measurements for 10 Delhi zones")
    print(f"{DIM}  OpenAQ + Overpass live; Earth Engine may be unavailable{RESET}")

    profiles, scores, baseline = ma.score_all_zones()
    by_id = {p.zone_id: p for p in profiles}

    if baseline:
        print(f"\n  City baseline: AQI {baseline.get('aqi')} "
              f"({baseline.get('aqi_band')}) from {baseline.get('station_count')} stations")

    rule("Priority ranking")
    print(f"  {'#':<3}{'Zone':<28}{'Score':>7} {'Band':<10}{'Placement':<14}{'Conf':>6}")
    print(f"  {'-'*3}{'-'*28}{'-'*7} {'-'*10}{'-'*14}{'-'*6}")
    for i, s in enumerate(scores, 1):
        p = by_id[s.zone_id]
        print(f"  {i:<3}{p.name[:27]:<28}{s.priority_score:>7.3f} "
              f"{s.priority_band:<10}{s.placement.value:<14}{s.confidence:>6.2f}")

    rule("Measured inputs per zone")
    print(f"  {'Zone':<28}{'AQI':>6}{'Traffic':>9}{'FAR':>7}{'NDVI':>8}{'LST C':>8}{'dHeat':>7}")
    print(f"  {'-'*28}{'-'*6}{'-'*9}{'-'*7}{'-'*8}{'-'*8}{'-'*7}")
    for s in scores:
        p = by_id[s.zone_id]
        def f(v, spec=".2f"):
            return format(v, spec) if v is not None else "—"
        print(f"  {p.name[:27]:<28}{f(p.aqi, '.0f'):>6}{f(p.traffic_proxy, '.1f'):>9}"
              f"{f(p.floor_area_ratio):>7}{f(p.ndvi, '.3f'):>8}"
              f"{f(p.land_surface_temp_c, '.1f'):>8}{f(p.heat_island_delta_c, '+.1f'):>7}")

    rule("Surface composition → placement")
    print(f"  {'Zone':<28}{'Built':>7}{'Road':>7}{'Plantable':>11}{'FAR':>7}  Placement")
    print(f"  {'-'*28}{'-'*7}{'-'*7}{'-'*11}{'-'*7}  {'-'*13}")
    for s in scores:
        p = by_id[s.zone_id]
        def pc(v):
            return f"{v:.0%}" if v is not None else "—"
        print(f"  {p.name[:27]:<28}{pc(p.building_footprint_ratio):>7}"
              f"{pc(p.road_surface_ratio):>7}{pc(p.plantable_ground_ratio):>11}"
              f"{p.floor_area_ratio if p.floor_area_ratio is not None else 0:>7.2f}"
              f"  {s.placement.value}")

    counts: dict[str, int] = {}
    for s in scores:
        counts[s.placement.value] = counts.get(s.placement.value, 0) + 1
    print(f"\n  distribution: {', '.join(f'{k}={v}' for k, v in sorted(counts.items()))}")
    if len(counts) == 1:
        print(f"  {DIM}WARNING: every zone got the same placement — the popup demo needs"
              f" variety across ground/wall/potted{RESET}")

    rule("Placement rationales")
    for s in scores:
        p = by_id[s.zone_id]
        print(f"\n  {BOLD}{p.name}{RESET} → {s.placement.value}")
        print(f"    {s.placement_rationale}")

    rule("Signal coverage")
    all_missing: set[str] = set()
    for s in scores:
        all_missing.update(s.missing_signals)
    complete = [s for s in scores if not s.missing_signals]
    print(f"  zones scored:            {len(scores)}/10")
    print(f"  fully-measured zones:    {len(complete)}/10")
    print(f"  mean confidence:         {sum(s.confidence for s in scores)/len(scores):.2f}")
    if all_missing:
        print(f"\n  {DIM}signals missing somewhere: {', '.join(sorted(all_missing))}{RESET}")
        print(f"  {DIM}scores were renormalised over available signals, not zero-filled{RESET}")

    rule("Vegetation signal vs curated expectation")
    print(f"{DIM}  zone selection was curated; the NDVI reading is measured{RESET}\n")
    from backend.app.zones import ZONES_BY_ID
    agree = disagree = unknown = 0
    for s in scores:
        p = by_id[s.zone_id]
        expected = ZONES_BY_ID[s.zone_id].expected_vegetation
        if p.has_existing_vegetation is None:
            mark, unknown = "unmeasured", unknown + 1
        else:
            actual = "vegetated" if p.has_existing_vegetation else "bare"
            if actual == expected:
                mark, agree = f"agrees ({actual})", agree + 1
            else:
                mark, disagree = f"MISMATCH: expected {expected}, measured {actual}", disagree + 1
        print(f"  {p.name[:30]:<32} {mark}")
    print(f"\n  agree {agree} | mismatch {disagree} | unmeasured {unknown}")

    rule("Verdict")
    spread = scores[0].priority_score - scores[-1].priority_score
    print(f"  priority spread across zones: {spread:.3f}")
    if spread < 0.15:
        print("  WARNING: zones barely differentiate — check normalisation references.")
        return 1
    print("  Zones differentiate meaningfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
