"""Model B — Genomic Compatibility Model.

Inputs : plant trait data, species divergence (TimeTree), hybridization records (GBIF)
Outputs: a species shortlist per zone, plus cross-compatibility confidence scores for
         every candidate pairing within that shortlist.

Pipeline, matching the brief's four methodology steps:

    1. shortlist_for_zone   trait-based shortlist for the zone's conditions
    2-3. score_pairing      divergence (TimeTree) + recorded hybrids (GBIF), combined
                            in the spirit of gpcp/predhy cross-performance prediction
    4. CompatibilityResult  a confidence score framed as predicted compatibility

Model B never imports Model A. It consumes a ZoneProfile (app.schemas) and nothing
else, so it can be tested and replaced independently.
"""
from __future__ import annotations

import itertools
import logging

from dataclasses import dataclass

from ...app.schemas import ZoneProfile
from .compatibility import CompatibilityResult, score_pairing
from .shortlist import (
    ScoredSpecies,
    cultivar_alternatives_for_zone,
    shortlist_for_zone,
)
from .species_catalog import (
    ALL_TAXA,
    CATALOG_BY_ID,
    CULTIVARS,
    SPECIES_ONLY,
    Placement,
    Species,
    cultivars_of,
    get_species,
)

log = logging.getLogger(__name__)


def _catalog_placement(profile: ZoneProfile) -> Placement | None:
    """Map Model A's placement category onto the catalogue's enum."""
    if profile.placement is None:
        return None
    return Placement(profile.placement.value)


__all__ = [
    "ALL_TAXA",
    "CATALOG_BY_ID",
    "CULTIVARS",
    "CompatibilityResult",
    "Placement",
    "ScoredSpecies",
    "SPECIES_ONLY",
    "Species",
    "ZoneRecommendation",
    "cultivar_alternatives_for_zone",
    "cultivars_of",
    "get_species",
    "recommend_for_zone",
    "score_all_pairings",
    "score_cultivar_alternatives",
    "score_pairing",
    "shortlist_for_zone",
]


def score_all_pairings(
    profile: ZoneProfile,
    shortlist: list[ScoredSpecies] | None = None,
) -> list[CompatibilityResult]:
    """Score every pairing within a zone's shortlist — 5 choose 2 = 10 pairs.

    This is what the precompute step persists so the drag interaction is a pure
    lookup. Results are returned most-compatible first; low scores are kept, not
    filtered, because "these two cannot cross" is a real answer the UI should show.
    """
    shortlist = shortlist or shortlist_for_zone(profile)
    weights = profile.trait_weights()
    placement = _catalog_placement(profile)

    results: list[CompatibilityResult] = []
    for a, b in itertools.combinations([s.species for s in shortlist], 2):
        results.append(score_pairing(a, b, weights, placement))

    results.sort(key=lambda r: r.confidence, reverse=True)
    log.info(
        "Model B: scored %d pairings for %s (range %.2f-%.2f)",
        len(results), profile.zone_id,
        results[-1].confidence if results else 0.0,
        results[0].confidence if results else 0.0,
    )
    return results


def score_cultivar_alternatives(
    profile: ZoneProfile,
    max_pairs: int = 3,
) -> list[CompatibilityResult]:
    """Score the zone's within-species cultivar pairs — the 'safer bet' tier.

    Identical scoring path to score_all_pairings; the only difference is that these
    parents are the same species, so crossability is 1.0 and scores land higher. That
    is a real biological difference, not a thumb on the scale.
    """
    weights = profile.trait_weights()
    placement = _catalog_placement(profile)
    pairs = cultivar_alternatives_for_zone(profile, max_pairs=max_pairs)

    results = [score_pairing(a, b, weights, placement) for a, b in pairs]
    results.sort(key=lambda r: r.confidence, reverse=True)
    return results


@dataclass
class ZoneRecommendation:
    """Model B's complete output for one zone, in the two tiers the UI presents.

    `novel_pairings` is the ambitious set: five distinct species, capped at one per
    genus, scored honestly — most crosses between them are biologically unlikely and
    say so. `safer_pairings` is the reliable set: cultivars of a single species,
    which cross readily. Both tiers come from the same scorer.
    """
    zone_id: str
    shortlist: list[ScoredSpecies]
    novel_pairings: list[CompatibilityResult]
    safer_pairings: list[CompatibilityResult]

    @property
    def best_novel(self) -> CompatibilityResult | None:
        return self.novel_pairings[0] if self.novel_pairings else None

    @property
    def best_safer(self) -> CompatibilityResult | None:
        return self.safer_pairings[0] if self.safer_pairings else None


def recommend_for_zone(profile: ZoneProfile, max_cultivar_pairs: int = 3) -> ZoneRecommendation:
    """Run Model B end to end for one zone: shortlist, then both pairing tiers."""
    shortlist = shortlist_for_zone(profile)
    return ZoneRecommendation(
        zone_id=profile.zone_id,
        shortlist=shortlist,
        novel_pairings=score_all_pairings(profile, shortlist),
        safer_pairings=score_cultivar_alternatives(profile, max_pairs=max_cultivar_pairs),
    )
