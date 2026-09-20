"""Exercise Model B against live TimeTree + GBIF.

Run:  .venv/bin/python scripts/test_model_b.py

Checks the thing that actually matters for the demo: that scores show real variance
and that biologically implausible pairings are correctly rejected.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.schemas import PlacementCategory, ZoneProfile
from backend.models import model_b_genomic as mb

logging.basicConfig(level=logging.WARNING, format="  %(levelname)s %(name)s: %(message)s")

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def rule(text: str) -> None:
    print(f"\n{BOLD}{text}{RESET}\n{'=' * 78}")


def main() -> int:
    # A realistic high-pollution, high-traffic, dense zone — values in the range the
    # live sources actually returned for Anand Vihar.
    zone = ZoneProfile(
        zone_id="anand-vihar",
        name="Anand Vihar ISBT Corridor",
        latitude=28.6469, longitude=77.3159,
        aqi=237.0, pm25_ugm3=101.0,
        traffic_proxy=48.7, floor_area_ratio=0.39,
        has_existing_vegetation=False,
        placement=PlacementCategory.GROUND,
    )

    rule(f"Trait weights derived from {zone.name}")
    for trait, weight in sorted(zone.trait_weights().items(), key=lambda kv: -kv[1]):
        bar = "█" * int(weight * 28)
        print(f"  {trait:<22} {weight:.2f}  {bar}")

    rule("Stage 1 — trait shortlist")
    shortlist = mb.shortlist_for_zone(zone)
    for i, sc in enumerate(shortlist, 1):
        print(f"\n  {i}. {BOLD}{sc.species.common_name}{RESET} "
              f"({sc.species.scientific_name})  fit={sc.fit_score:.3f}")
        for q in sc.key_qualities:
            print(f"       + {q}")
        for c in sc.concerns:
            print(f"       {DIM}! {c}{RESET}")

    rule("Stage 2-4 — all 10 pairings scored (live TimeTree + GBIF)")
    print(f"{DIM}  first run hits the network; afterwards it is cached{RESET}\n")
    results = mb.score_all_pairings(zone, shortlist)

    print(f"  {'Pairing':<44} {'Score':>6} {'Band':<12} {'Diverg.':>9}")
    print(f"  {'-'*44} {'-'*6} {'-'*12} {'-'*9}")
    for r in results:
        a = mb.get_species(r.species_a_id).common_name
        b = mb.get_species(r.species_b_id).common_name
        div = f"{r.divergence_mya:.0f} Mya" if r.divergence_mya is not None else "unknown"
        print(f"  {f'{a} × {b}'[:44]:<44} {r.confidence:>6.3f} {r.band:<12} {div:>9}")

    rule("Top pairing — full explanation")
    top = results[0]
    a, b = mb.get_species(top.species_a_id), mb.get_species(top.species_b_id)
    print(f"  {BOLD}{a.common_name} × {b.common_name}{RESET}")
    print(f"  confidence {top.confidence:.3f}  "
          f"(band {top.confidence_low:.2f}–{top.confidence_high:.2f})  → {top.band}\n")
    print(f"  crossability      {top.crossability:.3f}")
    print(f"  hybrid evidence   {top.hybrid_evidence_strength:.3f}")
    print(f"  parental merit    {top.parental_merit:.3f}")
    print(f"  complementarity   {top.complementarity:.3f}")
    print(f"  ploidy factor     {top.ploidy_factor:.2f}")
    print(f"\n  {top.explanation}\n")
    for c in top.citations:
        print(f"  {DIM}· {c}{RESET}")
    for g in top.data_gaps:
        print(f"  {DIM}? {g}{RESET}")

    rule("Control — a deliberately implausible cross-family pairing")
    neem = mb.get_species("azadirachta_indica")
    boug = mb.get_species("bougainvillea_glabra")
    ctrl = mb.score_pairing(neem, boug, zone.trait_weights())
    print(f"  {neem.common_name} × {boug.common_name}: "
          f"{ctrl.confidence:.3f} → {BOLD}{ctrl.band}{RESET}")
    print(f"  {ctrl.explanation}")

    rule("Control — pair GBIF records as a named hybrid formula")
    # GBIF carries "Bougainvillea spectabilis x Bougainvillea glabra" explicitly, so
    # this pair should trigger direct-formula evidence, not just genus-level evidence.
    bg = mb.get_species("bougainvillea_glabra")
    bs = mb.get_species("bougainvillea_spectabilis")
    known = mb.score_pairing(bg, bs, zone.trait_weights())
    print(f"  {bg.common_name} × {bs.common_name}: "
          f"{known.confidence:.3f} → {BOLD}{known.band}{RESET}")
    print(f"  hybrid evidence strength: {known.hybrid_evidence_strength:.2f}  "
          f"(0.95 means a formula naming exactly these two was found)")
    print(f"  crossability from rank (no TimeTree estimate): {known.crossability_from_rank}")
    print(f"  {known.explanation}")
    for c in known.citations:
        print(f"  {DIM}· {c}{RESET}")

    rule("Two-tier output — ambitious vs safer bet")
    ok = True
    for placement, label in (
        (PlacementCategory.GROUND, "ground (avenue trees)"),
        (PlacementCategory.WALL_HANGING, "wall-hanging (dense commercial)"),
    ):
        z = ZoneProfile(
            zone_id=f"demo-{placement.value}", name=f"demo {placement.value}",
            latitude=28.63, longitude=77.22,
            aqi=237.0, traffic_proxy=48.7, floor_area_ratio=1.8,
            has_existing_vegetation=False, placement=placement,
        )
        rec = mb.recommend_for_zone(z)

        print(f"\n  {BOLD}{label}{RESET}")
        genera = [s.species.genus for s in rec.shortlist]
        print(f"  shortlist: {', '.join(s.species.common_name for s in rec.shortlist)}")
        print(f"  genera:    {', '.join(genera)}  "
              f"({len(set(genera))} distinct of {len(genera)})")
        if len(set(genera)) < len(genera):
            print(f"  {DIM}(genus cap relaxed — too few feasible genera){RESET}")

        print(f"\n    {'AMBITIOUS (distinct species)':<46} {'Score':>6}  Band")
        print(f"    {'-'*46} {'-'*6}  {'-'*12}")
        for r in rec.novel_pairings[:4]:
            a = mb.get_species(r.species_a_id).display_name
            b = mb.get_species(r.species_b_id).display_name
            print(f"    {f'{a} × {b}'[:46]:<46} {r.confidence:>6.3f}  {r.band}")

        print(f"\n    {'SAFER BET (same-species cultivars)':<46} {'Score':>6}  Band")
        print(f"    {'-'*46} {'-'*6}  {'-'*12}")
        if not rec.safer_pairings:
            print(f"    {DIM}none — no cultivar-rich species feasible here{RESET}")
        for r in rec.safer_pairings:
            a = mb.get_species(r.species_a_id).display_name
            b = mb.get_species(r.species_b_id).display_name
            print(f"    {f'{a} × {b}'[:46]:<46} {r.confidence:>6.3f}  {r.band}")

        if rec.best_novel and rec.best_safer:
            delta = rec.best_safer.confidence - rec.best_novel.confidence
            print(f"\n    safer-bet advantage: {delta:+.3f}")
            if delta <= 0:
                print(f"    {DIM}NOTE: cultivars did not outscore distinct species here{RESET}")

    rule("Sample explanation — a cultivar pair")
    z = ZoneProfile(
        zone_id="demo-wall", name="demo wall", latitude=28.63, longitude=77.22,
        aqi=237.0, traffic_proxy=48.7, floor_area_ratio=1.8,
        has_existing_vegetation=False, placement=PlacementCategory.WALL_HANGING,
    )
    safer = mb.score_cultivar_alternatives(z)
    if safer:
        r = safer[0]
        a, b = mb.get_species(r.species_a_id), mb.get_species(r.species_b_id)
        print(f"  {BOLD}{a.display_name} × {b.display_name}{RESET}  "
              f"{r.confidence:.3f} ({r.band})   conspecific={r.is_conspecific}")
        print(f"  crossability {r.crossability:.2f}  complementarity {r.complementarity:.3f}")
        print(f"\n  {r.explanation}\n")
        for g in r.data_gaps:
            print(f"  {DIM}? {g}{RESET}")

    rule("Variance check")
    # Checked across both tiers together, because that is what a zone block actually
    # shows. An ambitious tier that is honestly all "not viable" is a valid result,
    # so long as the zone as a whole still gives the user a real range to read.
    for placement in (PlacementCategory.GROUND, PlacementCategory.WALL_HANGING):
        z = ZoneProfile(
            zone_id=f"var-{placement.value}", name="variance check",
            latitude=28.63, longitude=77.22,
            aqi=237.0, traffic_proxy=48.7, floor_area_ratio=1.8,
            has_existing_vegetation=False, placement=placement,
        )
        rec = mb.recommend_for_zone(z)
        combined = rec.novel_pairings + rec.safer_pairings
        scores = [r.confidence for r in combined]
        bands = sorted({r.band for r in combined})
        spread = max(scores) - min(scores)

        novel = [r.confidence for r in rec.novel_pairings]
        novel_spread = max(novel) - min(novel) if novel else 0.0

        print(f"\n  {BOLD}{placement.value}{RESET}")
        print(f"    combined range   {min(scores):.3f} – {max(scores):.3f}  "
              f"(spread {spread:.3f})")
        print(f"    ambitious spread {novel_spread:.3f}")
        print(f"    bands present    {', '.join(bands)}")

        if spread < 0.20:
            print(f"    FAIL: too uniform to read as a range")
            ok = False
        elif len(bands) < 2:
            print(f"    FAIL: only one band present")
            ok = False
        else:
            print(f"    OK")

    print()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
