"""Is ~0.55 a real structural ceiling, or a formula bug?

Runs a textbook-easy same-species cross (Hibiscus rosa-sinensis cultivars — the
canonical example of routine cultivar hybridisation, with thousands of named
cultivars produced by exactly this cross) and decomposes the score term by term
against the formula's algebraic maximum.

If the binding constraint is complementarity, the ceiling is real: cultivars of one
species genuinely are near-identical, and the model is right to say a reliable cross
adds little. If something else binds, that is a bug and the band labels should not
be locked.

    .venv/bin/python scripts/check_score_ceiling.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.schemas import PlacementCategory, ZoneProfile
from backend.models import model_b_genomic as mb
from backend.models.model_b_genomic.compatibility import (
    W_COMPLEMENTARITY,
    W_HYBRID_EVIDENCE,
    W_PARENTAL_MERIT,
    _trait_value,
    complementarity,
    parental_merit,
)

logging.basicConfig(level=logging.ERROR)
BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def rule(t: str) -> None:
    print(f"\n{BOLD}{t}{RESET}\n{'=' * 76}")


def main() -> int:
    # A zone that genuinely suits Hibiscus: potted placement, dense built fabric,
    # high pollution. Irrigation is available in a planter, so its moderate water
    # need is not disqualifying.
    zone = ZoneProfile(
        zone_id="ceiling-test", name="Connaught Place planters",
        latitude=28.6315, longitude=77.2167,
        aqi=237.0, traffic_proxy=48.7, floor_area_ratio=2.2,
        has_existing_vegetation=False,
        placement=PlacementCategory.POTTED,
    )
    weights = zone.trait_weights()

    a = mb.get_species("hibiscus_rosa_sinensis_cooperi")
    b = mb.get_species("hibiscus_rosa_sinensis_brilliant")

    rule("Textbook-easy cross: Hibiscus rosa-sinensis cultivars")
    print(f"  {a.display_name}  ×  {b.display_name}")
    print(f"  {DIM}both are selections of {a.scientific_name}{RESET}")

    placement = mb.Placement(zone.placement.value)
    r = mb.score_pairing(a, b, weights, placement)
    print(f"\n  score {BOLD}{r.confidence:.3f}{RESET} → {r.band}   "
          f"(band {r.confidence_low:.2f}–{r.confidence_high:.2f})")
    print(f"  conspecific={r.is_conspecific}  crossability={r.crossability:.2f}  "
          f"ploidy={r.ploidy_factor:.2f}")

    rule("Term-by-term decomposition")
    merit = parental_merit(a, b, weights, placement)
    comp = complementarity(a, b, weights, placement)
    ev = r.hybrid_evidence_strength

    rows = [
        ("hybrid evidence", ev, W_HYBRID_EVIDENCE),
        ("parental merit", merit, W_PARENTAL_MERIT),
        ("complementarity", comp, W_COMPLEMENTARITY),
    ]
    print(f"  {'term':<20} {'value':>7} {'weight':>7} {'contrib':>8} {'max contrib':>12}")
    print(f"  {'-'*20} {'-'*7} {'-'*7} {'-'*8} {'-'*12}")
    quality = 0.0
    for name, val, w in rows:
        quality += val * w
        print(f"  {name:<20} {val:>7.3f} {w:>7.2f} {val*w:>8.3f} {w:>12.2f}")
    print(f"  {'-'*20} {'-'*7} {'-'*7} {'-'*8} {'-'*12}")
    print(f"  {'quality':<20} {'':>7} {'':>7} {quality:>8.3f} {1.00:>12.2f}")
    print(f"\n  confidence = crossability({r.crossability:.2f}) x "
          f"ploidy({r.ploidy_factor:.2f}) x quality({quality:.3f}) = {r.confidence:.3f}")

    rule("Where is merit being lost?")
    print(f"  {'trait':<22} {'weight':>7} {a.common_name[:10]:>11} {b.common_name[:10]:>11}")
    print(f"  {'-'*22} {'-'*7} {'-'*11} {'-'*11}")
    print(f"  {DIM}(normalised within placement '{placement.value}'){RESET}")
    for trait, w in sorted(weights.items(), key=lambda kv: -kv[1]):
        va, vb = _trait_value(a, trait, placement), _trait_value(b, trait, placement)
        flag = "  <-- drags merit down" if (va or 0) < 0.3 and w >= 0.5 else ""
        print(f"  {trait:<22} {w:>7.2f} {va if va is not None else -1:>11.3f} "
              f"{vb if vb is not None else -1:>11.3f}{flag}")

    rule("Algebraic maximum of the formula")
    max_q = W_HYBRID_EVIDENCE * 1.0 + W_PARENTAL_MERIT * 1.0 + W_COMPLEMENTARITY * 1.0
    print(f"  absolute max (all terms = 1.0):            {max_q:.3f}")
    cons_max = W_HYBRID_EVIDENCE * 0.90 + W_PARENTAL_MERIT * 1.0 + W_COMPLEMENTARITY * 1.0
    print(f"  conspecific max (evidence capped at 0.90): {cons_max:.3f}")
    real_max = W_HYBRID_EVIDENCE * 0.90 + W_PARENTAL_MERIT * 1.0 + W_COMPLEMENTARITY * 0.02
    print(f"  conspecific + realistic complementarity:   {real_max:.3f}")
    print(f"  {DIM}(cultivars of one species are near-identical, so complementarity")
    print(f"   is ~0.00-0.02 by construction — this is the structural part){RESET}")

    rule("Best achievable in practice, across every feasible placement")
    best: tuple[float, str, str, str] | None = None
    for placement in PlacementCategory:
        z = ZoneProfile(
            zone_id=f"probe-{placement.value}", name="probe",
            latitude=28.63, longitude=77.22,
            aqi=237.0, traffic_proxy=48.7, floor_area_ratio=1.5,
            has_existing_vegetation=False, placement=placement,
        )
        for res in mb.score_cultivar_alternatives(z, max_pairs=8):
            sa = mb.get_species(res.species_a_id)
            sb = mb.get_species(res.species_b_id)
            if best is None or res.confidence > best[0]:
                best = (res.confidence, sa.display_name, sb.display_name, placement.value)

    if best:
        print(f"  highest real cultivar cross: {best[0]:.3f}")
        print(f"    {best[1]} × {best[2]}  ({best[3]})")

    rule("Verdict")
    hibiscus_merit_loss = (1.0 - merit) * W_PARENTAL_MERIT
    comp_loss = (1.0 - comp) * W_COMPLEMENTARITY
    print(f"  lost to imperfect merit:           {hibiscus_merit_loss:.3f}")
    print(f"  lost to near-zero complementarity: {comp_loss:.3f}")
    print()
    if comp_loss >= hibiscus_merit_loss:
        print("  Complementarity is the binding constraint — structural and correct.")
    else:
        print("  Merit is the binding constraint. Worth checking whether the merit")
        print("  term is fairly scaled before locking band labels.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
