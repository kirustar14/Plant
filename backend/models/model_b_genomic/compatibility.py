"""Model B core — predicted compatibility for a candidate species pairing.

METHODOLOGY

This translates the approach used by the `gpcp` and `predhy` R packages (genomic
prediction of cross performance / hybrid performance) into the data actually
available for urban tree species. Those packages predict the performance of an
unobserved cross from (a) parental merit, estimated as genomic breeding values, and
(b) the genomic relationship between the parents, since heterosis depends on parental
divergence. We keep that structure and substitute the best available proxies:

    gpcp/predhy                     ->  here
    ---------------------------------------------------------------------------
    parental GEBV for target trait  ->  per-parent trait fitness for the zone
    genomic relationship matrix     ->  TimeTree divergence time (published, live)
    training-population validation  ->  GBIF named hybrid taxa (recorded crosses)
    (assumed within breeding pool)  ->  explicit crossability + ploidy screening

The last row is the important addition. GBLUP-family methods assume the parents are
already within a crossable breeding pool — a safe assumption for maize inbreds, and
an unsafe one across urban tree species. So crossability is applied as a multiplicative
gate: if two species almost certainly cannot cross, no amount of trait complementarity
rescues the score. A pair of Ficus species diverged ~48 Mya scores low even though both
are excellent urban trees, and that is the scientifically correct answer.

SCOPE AND HONESTY

Output is "predicted compatibility" — a prior on whether a cross is worth attempting,
derived from divergence and documented hybridization. It is NOT a claim that a cross
has been performed, will succeed, or will be fertile. Nothing here is a certainty
claim, and every score carries an explicit uncertainty band derived from TimeTree's
own confidence interval and study count.

Cross-kingdom or cross-family "crossbreeding" is not modelled, because it is not a
real thing. Those pairings score near zero with a plain-language explanation.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from functools import lru_cache

from ...app.sources import gbif, ncbi, timetree
from ...app.sources.http_cache import UpstreamError
from .species_catalog import ALL_TAXA, Placement, Species

log = logging.getLogger(__name__)

# --- Crossability calibration -------------------------------------------------
# Reproductive isolation in plants accumulates with divergence, but far more slowly
# than in animals — viable interspecific hybrids are well documented across several
# million years of divergence, and become progressively rarer beyond that (Levin 2012
# on plant hybridization; Moyle et al. on the accumulation of postzygotic isolation).
# An exponential decay with tau = 8 Mya reproduces that shape: near-certain crossing
# within a genus of recent origin, roughly even odds around 5-6 Mya, and negligible
# beyond ~25 Mya. This is a calibrated prior, not a fitted model, and is stated as such.
CROSSABILITY_TAU_MYA = 8.0

# Contribution weights, applied AFTER the crossability gate.
W_HYBRID_EVIDENCE = 0.40
W_PARENTAL_MERIT = 0.35
W_COMPLEMENTARITY = 0.25

# Different chromosome counts do not prevent hybridisation, but commonly yield
# sterile or low-fertility offspring (odd ploidy at meiosis). A partial penalty
# reflects "possible but compromised" rather than a hard block.
PLOIDY_MISMATCH_FACTOR = 0.45


@dataclass
class CompatibilityResult:
    """A scored pairing, with every contributing term kept inspectable."""
    species_a_id: str
    species_b_id: str

    confidence: float               # 0-1 predicted compatibility
    confidence_low: float           # uncertainty band
    confidence_high: float

    crossability: float
    hybrid_evidence_strength: float
    parental_merit: float
    complementarity: float
    ploidy_factor: float

    divergence_mya: float | None
    divergence_ci: tuple[float, float] | None
    divergence_studies: int
    same_genus: bool
    same_family: bool

    explanation: str
    citations: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    # True when TimeTree had no estimate and crossability came from rank instead.
    crossability_from_rank: bool = False
    # True for two cultivars of one species — a within-species cross.
    is_conspecific: bool = False

    @property
    def band(self) -> str:
        """Qualitative class — what the UI should lead with, not the decimal.

        Thresholds are calibrated to the score range this model can actually
        produce, which is roughly 0.00-0.75 rather than the full 0-1. The ceiling is
        structural, not a data shortfall: the quality term blends hybrid evidence,
        parental merit, and complementarity, and the last two pull against each
        other. High complementarity requires genetically distant parents, which
        drives crossability — and therefore the whole score — down. A pairing that
        maxed every term simultaneously is not biologically possible.

        Bands describe the score's meaning, so they are set against the reachable
        range. Relative ordering is untouched: this renames regions of the scale, it
        does not move any pairing past another.
        """
        if self.confidence >= 0.50:
            return "Promising"
        if self.confidence >= 0.30:
            return "Plausible"
        if self.confidence >= 0.12:
            return "Unlikely"
        return "Not viable"


def crossability_from_divergence(divergence_mya: float | None) -> float:
    """Prior probability that two lineages can cross, from divergence time."""
    if divergence_mya is None:
        return 0.0
    if divergence_mya <= 0:
        return 1.0
    return math.exp(-divergence_mya / CROSSABILITY_TAU_MYA)


def crossability_from_taxonomy(same_genus: bool, same_family: bool) -> float:
    """Fallback prior when TimeTree publishes no estimate for a pair.

    Necessary because missing data is not evidence of incompatibility. TimeTree has
    no divergence estimate for Bougainvillea glabra x B. peruviana, yet GBIF records
    named hybrids in the genus — treating "unknown" as zero would score a documented
    cross as impossible, which is exactly backwards.

    Rank is a weak proxy for divergence, so these values are deliberately lower than
    a measured recent divergence would give, and the caller records a data gap that
    widens the uncertainty band.
    """
    if same_genus:
        return 0.45   # congeners cross often enough to be worth attempting
    if same_family:
        return 0.08   # intergeneric crosses are rare but documented in some families
    return 0.01       # across families, effectively never


def ploidy_factor(a: Species, b: Species) -> tuple[float, str | None]:
    """Penalty for mismatched chromosome counts. Returns (factor, caveat)."""
    if a.chromosome_2n is None or b.chromosome_2n is None:
        unknown = a.scientific_name if a.chromosome_2n is None else b.scientific_name
        return 1.0, f"chromosome count unpublished for {unknown} — ploidy not screened"
    if a.chromosome_2n == b.chromosome_2n:
        return 1.0, None
    return (
        PLOIDY_MISMATCH_FACTOR,
        f"chromosome counts differ (2n={a.chromosome_2n} vs 2n={b.chromosome_2n}); "
        f"hybrids of mismatched ploidy are frequently sterile",
    )


def parental_merit(
    a: Species,
    b: Species,
    zone_weights: dict[str, float],
    placement: Placement | None = None,
) -> float:
    """Mean zone-fitness of the two parents — the GEBV analogue.

    zone_weights maps a trait name to how much this zone needs it (0-1), e.g.
    {"pollution_tolerance": 0.9, "drought_tolerance": 0.6}.
    """
    if not zone_weights:
        return 0.5

    def fitness(s: Species) -> float:
        total = weight_sum = 0.0
        for trait, weight in zone_weights.items():
            value = _trait_value(s, trait, placement)
            if value is None:
                continue
            total += value * weight
            weight_sum += weight
        return total / weight_sum if weight_sum else 0.5

    return (fitness(a) + fitness(b)) / 2


def complementarity(
    a: Species,
    b: Species,
    zone_weights: dict[str, float],
    placement: Placement | None = None,
) -> float:
    """Reward pairs whose strengths cover each other's weaknesses.

    This is the heterosis intuition in predhy: a cross is most interesting when the
    parents differ in useful ways. Measured as the mean, over weighted traits, of how
    much the better parent exceeds the pair average.
    """
    if not zone_weights:
        return 0.0

    gains = weights = 0.0
    for trait, weight in zone_weights.items():
        va, vb = _trait_value(a, trait, placement), _trait_value(b, trait, placement)
        if va is None or vb is None:
            continue
        best, avg = max(va, vb), (va + vb) / 2
        gains += (best - avg) * weight
        weights += weight

    if not weights:
        return 0.0
    # (best - avg) maxes out at 0.5 for traits scaled 0-1; rescale to 0-1.
    return min(1.0, (gains / weights) * 2)


@lru_cache(maxsize=8)
def _placement_maxima(placement: Placement | None) -> tuple[float, float]:
    """Best achievable CO2 capture and cooling among taxa feasible for a placement.

    Absolute-scale traits have to be normalised against what is actually possible in
    the zone, not against the catalogue's largest tree. Scoring a planter candidate
    against a banyan's 58 kg CO2/yr rates every option near zero on a trait no
    potted plant can win, which silently drags down merit for every non-ground zone
    and makes those zones look worse than the alternatives they actually have.

    Normalising within the placement asks the question that matters: of what can be
    established here, how good is this? Absolute figures are still reported to the
    user unchanged — this rescaling affects ranking only.
    """
    pool = [s for s in ALL_TAXA if placement is None or placement in s.placements]
    if not pool:
        pool = list(ALL_TAXA)
    co2 = max((s.co2_sequestration_kg_yr for s in pool), default=1.0) or 1.0
    cooling = max((s.transpiration_cooling_kwh_yr for s in pool), default=1.0) or 1.0
    return co2, cooling


def _trait_value(
    s: Species, trait: str, placement: Placement | None = None
) -> float | None:
    """Normalised 0-1 trait lookup.

    `placement` scales the absolute-magnitude traits (carbon, cooling) against what
    is achievable in that placement. Omitting it falls back to catalogue-wide
    normalisation, which is only meaningful when comparing across all placements.
    """
    if trait == "pollution_tolerance":
        # APTI runs roughly 5-20 in the literature; map that span onto 0-1.
        return max(0.0, min(1.0, (s.apti - 5.0) / 15.0))
    if trait == "drought_tolerance":
        return s.drought_tolerance
    if trait == "heat_tolerance":
        return s.heat_tolerance
    if trait == "shade_tolerance":
        return s.shade_tolerance
    if trait == "low_water":
        return 1.0 - s.water_requirement
    if trait == "growth_rate":
        return s.growth_rate
    if trait == "low_root_risk":
        return 1.0 - s.root_aggressiveness
    if trait == "canopy_cooling":
        _, cooling_max = _placement_maxima(placement)
        return min(1.0, s.transpiration_cooling_kwh_yr / cooling_max)
    if trait == "carbon_capture":
        co2_max, _ = _placement_maxima(placement)
        return min(1.0, s.co2_sequestration_kg_yr / co2_max)
    if trait == "native":
        return 1.0 if s.native_to_region else 0.0
    return None


def _build_explanation(r: CompatibilityResult, a: Species, b: Species) -> str:
    """Plain-language account of the score, for the crossbreed popup."""
    parts: list[str] = []

    if r.divergence_mya is None:
        if r.same_genus:
            parts.append(
                f"TimeTree has no published divergence estimate for {a.common_name} "
                f"and {b.common_name}, so this score leans on the fact that they share "
                f"a genus — close relatives that often cross — rather than on a "
                f"measured divergence. Treat it as a weaker estimate."
            )
        elif r.same_family:
            parts.append(
                f"TimeTree has no divergence estimate for this pair. They share a "
                f"family but not a genus, and crossing between genera is rare."
            )
        else:
            parts.append(
                f"TimeTree has no divergence estimate for {a.common_name} and "
                f"{b.common_name}, and they belong to different families — a cross "
                f"is not realistic."
            )
    elif r.divergence_mya < 0.5:
        parts.append(
            f"TimeTree places {a.common_name} and {b.common_name} as very recently "
            f"diverged — close enough to be treated as part of one breeding group, "
            f"so crossing should be straightforward."
        )
    elif r.crossability < 0.05:
        parts.append(
            f"{a.common_name} and {b.common_name} diverged roughly "
            f"{r.divergence_mya:.0f} million years ago — far past the range where "
            f"plant hybridisation is realistic. This pairing is not biologically viable."
        )
    elif r.crossability < 0.35:
        parts.append(
            f"These two diverged about {r.divergence_mya:.1f} million years ago. "
            f"Crossing is not impossible at that distance, but it is uncommon and "
            f"would likely need intervention."
        )
    else:
        parts.append(
            f"{a.common_name} and {b.common_name} diverged only about "
            f"{r.divergence_mya:.1f} million years ago, close enough that crossing is "
            f"realistic."
        )

    if r.hybrid_evidence_strength >= 0.9:
        parts.append("A hybrid between exactly these two is already recorded by name.")
    elif r.hybrid_evidence_strength >= 0.3:
        parts.append("Their genus contains documented hybrids, so the group crosses.")
    elif r.same_genus:
        parts.append("They share a genus, but no hybrid in it has been formally named.")

    if r.ploidy_factor < 1.0:
        parts.append(
            f"Chromosome counts differ (2n={a.chromosome_2n} vs {b.chromosome_2n}), "
            f"so even a successful cross would probably be sterile."
        )

    if r.crossability >= 0.35:
        if r.complementarity >= 0.25:
            parts.append(
                "Their strengths differ usefully for this site, which is what makes a "
                "cross worth attempting rather than just planting the better parent."
            )
        elif r.parental_merit >= 0.6:
            parts.append(
                "Both suit this site well, though they are similar enough that a cross "
                "would add little over planting either one."
            )

    parts.append(
        "This is a predicted compatibility from divergence data and recorded "
        "hybrids — not evidence that this cross has been made or would succeed."
    )
    return " ".join(parts)


def _is_conspecific(a: Species, b: Species) -> bool:
    """True when both taxa are the same biological species (cultivars included)."""
    if a.species_id == b.species_id:
        return False  # a taxon is not a pairing with itself
    return a.scientific_name.casefold() == b.scientific_name.casefold()


def _score_conspecific(
    a: Species,
    b: Species,
    zone_weights: dict[str, float],
    placement: Placement | None = None,
) -> CompatibilityResult:
    """Score a within-species cultivar cross — the 'safer bet' tier.

    Scored by the same formula as any other pairing; only the biological distance
    differs. Crossability is 1.0 because the parents are one species, which is a
    definitional fact rather than a measured one — recorded as a data gap so the
    claim is never dressed up as an empirical result.

    The honest tradeoff this surfaces: cultivar crosses are reliable but add little
    novelty, because selections within one species differ in habit and foliage far
    more than in the physiological traits a site actually needs. Complementarity is
    therefore usually low, and the score reflects reliability, not ambition.
    """
    merit = parental_merit(a, b, zone_weights, placement)
    comp = complementarity(a, b, zone_weights, placement)
    ploidy, ploidy_caveat = ploidy_factor(a, b)

    gaps = [
        "cultivar-level cross: crossability is definitional (same species), not "
        "measured from divergence data",
        "cultivar traits come from horticultural trade descriptions, which are "
        "weaker evidence than peer-reviewed measurements",
    ]
    if ploidy_caveat:
        gaps.append(ploidy_caveat)

    # Conspecific crossing is routine, so evidence strength is high on definitional
    # grounds. It is capped below 1.0 because individual cultivar combinations still
    # vary in fertility and outcome.
    evidence_strength = 0.90

    quality = (
        W_HYBRID_EVIDENCE * evidence_strength
        + W_PARENTAL_MERIT * merit
        + W_COMPLEMENTARITY * comp
    )
    confidence = 1.0 * ploidy * quality
    spread = min(0.20, 0.08 + 0.04 * len(gaps))

    result = CompatibilityResult(
        species_a_id=a.species_id,
        species_b_id=b.species_id,
        confidence=round(confidence, 3),
        confidence_low=round(max(0.0, confidence - spread), 3),
        confidence_high=round(min(1.0, confidence + spread), 3),
        crossability=1.0,
        hybrid_evidence_strength=evidence_strength,
        parental_merit=round(merit, 3),
        complementarity=round(comp, 3),
        ploidy_factor=ploidy,
        divergence_mya=0.0,
        divergence_ci=None,
        divergence_studies=0,
        same_genus=True,
        same_family=True,
        explanation="",
        citations=[
            f"Both are selections of {a.scientific_name} — a within-species cross",
            "Cultivar traits: horticultural trade/selection literature",
        ],
        data_gaps=gaps,
        is_conspecific=True,
    )

    name_a = a.display_name
    name_b = b.display_name
    parts = [
        f"{name_a} and {name_b} are two cultivars of the same species "
        f"({a.scientific_name}), so there is no reproductive barrier between them — "
        f"this kind of cross is routine in horticulture."
    ]
    if comp >= 0.15:
        parts.append(
            "They also differ usefully for this site, so the cross is worth making "
            "rather than simply planting the stronger of the two."
        )
    else:
        parts.append(
            "Because they are the same species, they are physiologically very "
            "similar — this is a reliable cross rather than an adventurous one, and "
            "it will not unlock traits neither parent already has."
        )
    if ploidy_caveat:
        parts.append(f"Caveat: {ploidy_caveat}.")
    parts.append(
        "Predicted compatibility, not a guarantee: individual cultivar combinations "
        "still vary in fertility and in what the offspring inherit."
    )
    result.explanation = " ".join(parts)
    return result


def score_pairing(
    a: Species,
    b: Species,
    zone_weights: dict[str, float] | None = None,
    placement: Placement | None = None,
) -> CompatibilityResult:
    """Predicted compatibility for one species pair, using live TimeTree + GBIF.

    `placement` scales absolute-magnitude traits against what the zone can actually
    support; see _placement_maxima.
    """
    zone_weights = zone_weights or {}
    citations: list[str] = []
    gaps: list[str] = []

    # --- Conspecific short-circuit (two cultivars of one species) ------------
    # Same species means no reproductive barrier to establish: crossability is 1.0
    # by definition, not by measurement. Routing these through TimeTree would be
    # meaningless — cultivars share one taxid and have no divergence estimate.
    if _is_conspecific(a, b):
        return _score_conspecific(a, b, zone_weights, placement)

    # --- Taxonomy (GBIF) ------------------------------------------------------
    tax_a = gbif.match_taxon(a.scientific_name)
    tax_b = gbif.match_taxon(b.scientific_name)
    same_genus = bool(tax_a and tax_b and tax_a.genus and tax_b.genus
                      and tax_a.genus.casefold() == tax_b.genus.casefold())
    same_family = bool(tax_a and tax_b and tax_a.family and tax_b.family
                       and tax_a.family.casefold() == tax_b.family.casefold())
    if tax_a and tax_b:
        citations.append("GBIF Backbone Taxonomy (family/genus placement)")
    else:
        gaps.append("GBIF could not resolve one or both names")

    # --- Divergence (TimeTree via NCBI taxids) -------------------------------
    divergence = None
    taxid_a = ncbi.resolve_taxid(a.scientific_name)
    taxid_b = ncbi.resolve_taxid(b.scientific_name)
    if taxid_a and taxid_b:
        try:
            divergence = timetree.fetch_divergence(taxid_a, taxid_b)
        except UpstreamError as exc:
            log.warning("TimeTree unavailable for %s x %s: %s",
                        a.species_id, b.species_id, exc)
            gaps.append("TimeTree unreachable — divergence unknown")
    else:
        gaps.append("NCBI could not resolve a taxonomy ID for one or both species")

    if divergence:
        citations.append(
            f"TimeTree: {divergence.best_estimate_mya:.1f} Mya divergence "
            f"({divergence.study_count} studies)"
        )
        div_mya = divergence.best_estimate_mya
        div_ci = (
            (divergence.ci_low_mya, divergence.ci_high_mya)
            if divergence.ci_low_mya is not None and divergence.ci_high_mya is not None
            else None
        )
    else:
        div_mya, div_ci = None, None
        if "TimeTree unreachable — divergence unknown" not in gaps:
            gaps.append("No published divergence estimate for this pair")

    if div_mya is not None:
        cross = crossability_from_divergence(div_mya)
        used_taxonomy_fallback = False
    else:
        # Missing divergence is not evidence of incompatibility — fall back to rank.
        cross = crossability_from_taxonomy(same_genus, same_family)
        used_taxonomy_fallback = True
        gaps.append("crossability estimated from taxonomic rank, not divergence data")

    # --- Recorded hybridization (GBIF named hybrid taxa) ---------------------
    if tax_a and tax_b:
        evidence = gbif.hybrid_evidence(tax_a, tax_b)
        citations.append(evidence.citation)
    else:
        evidence = gbif.HybridEvidence(same_genus=same_genus)
        gaps.append("Hybrid records not searched (taxonomy unresolved)")

    # --- Ploidy ---------------------------------------------------------------
    ploidy, ploidy_caveat = ploidy_factor(a, b)
    if ploidy_caveat:
        gaps.append(ploidy_caveat)

    # --- Combine --------------------------------------------------------------
    merit = parental_merit(a, b, zone_weights)
    comp = complementarity(a, b, zone_weights)

    # Documented hybridisation is direct evidence of crossability and can lift a
    # divergence-based prior that molecular dating alone would call marginal.
    effective_cross = max(cross, evidence.strength * 0.85)

    quality = (
        W_HYBRID_EVIDENCE * evidence.strength
        + W_PARENTAL_MERIT * merit
        + W_COMPLEMENTARITY * comp
    )
    confidence = effective_cross * ploidy * quality

    # --- Uncertainty ----------------------------------------------------------
    # Widen the band using TimeTree's own CI and thin study support; widen further
    # when a data gap means part of the model could not run at all.
    rel_unc = divergence.relative_uncertainty if divergence else 1.0
    study_penalty = 0.0 if not divergence else max(0.0, (3 - divergence.study_count) * 0.05)
    spread = min(0.35, 0.10 + 0.12 * rel_unc + study_penalty + 0.05 * len(gaps))

    result = CompatibilityResult(
        species_a_id=a.species_id,
        species_b_id=b.species_id,
        confidence=round(confidence, 3),
        confidence_low=round(max(0.0, confidence - spread), 3),
        confidence_high=round(min(1.0, confidence + spread), 3),
        crossability=round(effective_cross, 3),
        hybrid_evidence_strength=round(evidence.strength, 3),
        parental_merit=round(merit, 3),
        complementarity=round(comp, 3),
        ploidy_factor=ploidy,
        divergence_mya=round(div_mya, 2) if div_mya is not None else None,
        divergence_ci=div_ci,
        divergence_studies=divergence.study_count if divergence else 0,
        same_genus=same_genus,
        same_family=same_family,
        crossability_from_rank=used_taxonomy_fallback,
        explanation="",
        citations=citations,
        data_gaps=gaps,
    )
    result.explanation = _build_explanation(result, a, b)
    return result
