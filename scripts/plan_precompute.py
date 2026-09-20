"""Cost/scope analysis for the crossbreed precompute, before spending anything.

Scores are effectively free (TimeTree + GBIF, already cached). Images are not, so
this counts exactly how many generations each strategy needs before any are made.

    .venv/bin/python scripts/plan_precompute.py
"""
from __future__ import annotations

import itertools
import logging
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.models import model_a_zone_scoring as ma
from backend.models import model_b_genomic as mb

logging.basicConfig(level=logging.ERROR)
BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"

# gpt-image-1 pricing, 1024x1024. Quality tiers differ ~5x, so the choice matters
# more than the count does.
COST_LOW = 0.011
COST_MEDIUM = 0.042
COST_HIGH = 0.167
SECONDS_PER_IMAGE = 15


def main() -> int:
    print(f"\n{BOLD}Gathering zone profiles (cached){RESET}")
    profiles, scores, _ = ma.score_all_zones()
    by_id = {p.zone_id: p for p in profiles}

    zone_pairs: dict[str, list[tuple[str, str]]] = {}
    zone_placement: dict[str, str] = {}
    all_species: set[str] = set()

    for s in scores:
        profile = by_id[s.zone_id]
        shortlist = mb.shortlist_for_zone(profile)
        species_ids = [x.species.species_id for x in shortlist]
        all_species.update(species_ids)

        pairs = [tuple(sorted(p)) for p in itertools.combinations(species_ids, 2)]

        # Safer tier: within-species cultivar pairs.
        for a, b in mb.cultivar_alternatives_for_zone(profile):
            pairs.append(tuple(sorted((a.species_id, b.species_id))))
            all_species.update({a.species_id, b.species_id})

        zone_pairs[s.zone_id] = pairs
        zone_placement[s.zone_id] = s.placement.value

    total_zone_pairs = sum(len(v) for v in zone_pairs.values())
    unique_pairs = {p for v in zone_pairs.values() for p in v}

    by_placement: dict[tuple, set] = defaultdict(set)
    for zid, pairs in zone_pairs.items():
        for p in pairs:
            by_placement[(zone_placement[zid], p)].add(zid)

    print(f"\n{BOLD}Counts{RESET}\n{'='*70}")
    print(f"  zones                              {len(zone_pairs)}")
    print(f"  species involved                   {len(all_species)}")
    print(f"  (zone x pair) combinations         {total_zone_pairs}")
    print(f"  unique species pairs               {len(unique_pairs)}")
    print(f"  unique (placement x pair)          {len(by_placement)}")

    print(f"\n  {DIM}per-zone breakdown:{RESET}")
    for s in scores:
        n = len(zone_pairs[s.zone_id])
        print(f"    {by_id[s.zone_id].name[:34]:<36} {zone_placement[s.zone_id]:<13} {n} pairs")

    print(f"\n{BOLD}Image generation strategies{RESET}\n{'='*70}")
    strategies = [
        ("A. per (zone x pair) — brief-literal", total_zone_pairs),
        ("B. per (placement x pair)", len(by_placement)),
        ("C. per unique pair only", len(unique_pairs)),
    ]
    print(f"  {'strategy':<40}{'images':>8}{'low':>9}{'med':>9}{'time':>9}")
    print(f"  {'-'*40}{'-'*8}{'-'*9}{'-'*9}{'-'*9}")
    for name, n in strategies:
        mins = n * SECONDS_PER_IMAGE / 60
        print(f"  {name:<40}{n:>8}{'$'+format(n*COST_LOW,'.2f'):>9}"
              f"{'$'+format(n*COST_MEDIUM,'.2f'):>9}{format(mins,'.0f')+'m':>9}")

    print(f"\n{BOLD}Pairs shared across zones (why dedupe helps){RESET}\n{'='*70}")
    counts = defaultdict(list)
    for zid, pairs in zone_pairs.items():
        for p in pairs:
            counts[p].append(zid)
    shared = sorted(counts.items(), key=lambda kv: -len(kv[1]))[:6]
    for pair, zids in shared:
        a = mb.CATALOG_BY_ID[pair[0]].display_name
        b = mb.CATALOG_BY_ID[pair[1]].display_name
        print(f"  {f'{a} x {b}'[:50]:<52} appears in {len(zids)} zones")

    print(f"\n{BOLD}Score-only precompute (free){RESET}\n{'='*70}")
    print(f"  {total_zone_pairs} scored pairings — TimeTree/GBIF already cached, no spend")
    return 0


if __name__ == "__main__":
    sys.exit(main())
