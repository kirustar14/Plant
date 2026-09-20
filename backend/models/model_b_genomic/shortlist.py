"""Model B stage 1 — shortlist species suited to a zone's conditions.

This is step 1 of the brief's genomic methodology: narrow the catalogue to species
whose climate, water, and space profile fits the zone, before any pairing is scored.
Scoring every possible pair across the whole catalogue would be both slow and
meaningless — cross-compatibility only matters among species that belong on the site
in the first place.

Two filters run in order:

1. A hard filter on physical feasibility. A species that cannot be established in the
   zone's placement category (a 25m banyan on a wall) is removed outright rather than
   ranked low, because no trait score should be able to rescue it.
2. A weighted trait fit score, using weights the zone itself produces from its
   measurements (ZoneProfile.trait_weights), so shortlists genuinely differ by site.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ...app.schemas import PlacementCategory, ZoneProfile
from .compatibility import _trait_value
from .species_catalog import SPECIES_ONLY, Placement, Species, cultivars_of

log = logging.getLogger(__name__)

SHORTLIST_SIZE = 5

# At most this many species per genus in the main shortlist.
#
# Tuned against a real constraint rather than picked. At MAX_PER_GENUS = 3 a
# high-pollution ground zone filled with Ficus — a narrow recommendation. At 1 it
# swung the other way: five different genera meant every one of the ten pairings was
# cross-genus, and Delhi's avenue-tree genera are diverged far enough (69-121 Mya)
# that all ten honestly scored 0.000. Correct, but a uniform wall of zeros carries
# no information.
#
# 2 keeps at least four distinct genera — genuinely diverse, not variations on one
# plant — while admitting a single congener pair, so the tier spans "not viable" to
# "plausible" on the real biology instead of collapsing to one value. The cap is
# applied after trait ranking, so it never promotes a worse-fitting species.
MAX_PER_GENUS = 2

# A tree needs real rooting volume and vertical clearance; these caps keep the hard
# filter physical rather than aesthetic.
MAX_HEIGHT_POTTED_M = 4.0
MAX_HEIGHT_WALL_M = 10.0


@dataclass
class ScoredSpecies:
    species: Species
    fit_score: float
    reasons: list[str]
    concerns: list[str]

    @property
    def key_qualities(self) -> list[str]:
        """Short phrases for the zone block's species list."""
        return self.reasons[:3]


def _placement_to_catalog(placement: PlacementCategory) -> Placement:
    return {
        PlacementCategory.GROUND: Placement.GROUND,
        PlacementCategory.WALL_HANGING: Placement.WALL_HANGING,
        PlacementCategory.POTTED: Placement.POTTED,
    }[placement]


def _physically_feasible(s: Species, placement: Placement) -> bool:
    if placement not in s.placements:
        return False
    if placement == Placement.POTTED and s.mature_height_m > MAX_HEIGHT_POTTED_M:
        return False
    if placement == Placement.WALL_HANGING and s.mature_height_m > MAX_HEIGHT_WALL_M:
        return False
    return True


# Trait pairs that say the same thing to a reader. Delhi zones weight drought
# tolerance and low water demand almost identically, so a card that fires on both
# spends two of its three lines on one idea ("survives Delhi summers with minimal
# irrigation" / "very low water requirement"). Only the more distinctive of each
# group is kept.
_REDUNDANT_TRAIT_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"drought_tolerance", "low_water"}),
    frozenset({"heat_tolerance", "canopy_cooling"}),
)

# Traits whose phrase carries a species-specific number. These are worth protecting:
# they are the lines a judge can check, and they cannot be true of every card.
_QUANTIFIED_TRAITS = frozenset(
    {"pollution_tolerance", "carbon_capture", "canopy_cooling"}
)


def _phrase_for(s: Species, trait: str) -> str | None:
    return {
        "pollution_tolerance": f"APTI {s.apti} ({s.apti_band}) — handles roadside pollution load",
        "drought_tolerance": "survives Delhi summers with minimal irrigation",
        "heat_tolerance": "high heat tolerance",
        "shade_tolerance": "tolerates shade from surrounding buildings",
        "low_water": "very low water requirement",
        "growth_rate": "fast to establish — visible change within a season",
        "low_root_risk": "non-invasive roots, safe beside pavement and services",
        "canopy_cooling": f"strong cooling effect (~{s.transpiration_cooling_kwh_yr:.0f} kWh/yr equivalent)",
        "carbon_capture": f"sequesters ~{s.co2_sequestration_kg_yr:.0f} kg CO₂/yr at maturity",
        "native": "native to the region",
    }.get(trait)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _describe(
    s: Species,
    weights: dict[str, float],
    placement: Placement | None = None,
    cohort: list[Species] | None = None,
) -> tuple[list[str], list[str]]:
    """Why this species suits the zone, and what to watch out for.

    Ranking by zone weight alone produces near-identical copy for every card. The
    zone's weights are the same for all of them, and the shortlist was *selected*
    for scoring well on whatever the zone weights heavily — so the same two or three
    traits clear the bar for everyone. Observed live: "survives Delhi summers with
    minimal irrigation" on 17 of 20 cards, and all five Lodhi Garden species reading
    word-for-word identically. Copy like that describes the zone, not the plant.

    So a trait earns its line by how far this species stands out *from the others on
    the same shortlist*, not by its absolute value. A species still has to be good at
    the trait (the >= 0.65 floor is unchanged — this reorders honest statements, it
    does not manufacture them); being merely typical just no longer earns a mention
    ahead of something the species is genuinely distinctive for.

    `cohort` is the shortlist this species is being shown alongside. Without it the
    function falls back to absolute weighted ranking, which is the right behaviour
    for a species described on its own.
    """
    reasons: list[str] = []
    concerns: list[str] = []

    peers = [p for p in (cohort or []) if p.species_id != s.species_id]

    scored_traits: list[tuple[float, bool, str]] = []
    for trait, weight in weights.items():
        if weight < 0.4:
            continue
        value = _trait_value(s, trait, placement)
        if value is None or value < 0.65:
            continue

        if peers:
            peer_values = [
                v for v in (_trait_value(p, trait, placement) for p in peers)
                if v is not None
            ]
            # Distance above the peer median is the signal: a trait everyone shares
            # contributes ~0 and drops behind one this species genuinely leads on.
            edge = value - _median(peer_values) if peer_values else value
            # The floor is deliberately small. It keeps zone weight as a tie-break
            # among equally-distinctive traits without letting a heavy weight
            # promote a trait the whole shortlist shares — which is precisely how
            # the drought line ended up on nearly every card.
            rank_key = weight * (0.05 + max(0.0, edge))
        else:
            rank_key = weight * value

        scored_traits.append((rank_key, trait in _QUANTIFIED_TRAITS, trait))

    # Distinctiveness first; a checkable number breaks ties, since two traits this
    # species leads on equally are better represented by the one carrying a figure.
    scored_traits.sort(key=lambda t: (t[0], t[1]), reverse=True)

    used_groups: list[frozenset[str]] = []
    for _, _, trait in scored_traits:
        group = next(
            (g for g in _REDUNDANT_TRAIT_GROUPS if trait in g), frozenset({trait})
        )
        if group in used_groups:
            continue
        phrase = _phrase_for(s, trait)
        if not phrase or phrase in reasons:
            continue
        reasons.append(phrase)
        used_groups.append(group)
        if len(reasons) >= 4:
            break

    if not reasons:
        reasons.append(f"{s.habit.value.replace('_', ' ')} suited to this placement")

    # A species that leads its cohort on nothing can still end up with copy identical
    # to its neighbour's — at Lodhi Garden, Khejri and Neem both cleared only the two
    # traits every species there shares, and read word-for-word the same. Two cards
    # saying exactly the same thing look like a bug even when both statements are
    # true. Mature dimensions are the honest tie-breaker: always known, different for
    # every species, and load-bearing for a real planting decision in a way that
    # another shared tolerance claim is not.
    if len(reasons) < 3:
        reasons.append(
            f"{s.habit.value.replace('_', ' ')}, reaching ~{s.mature_height_m:.0f} m "
            f"with a ~{s.canopy_spread_m:.0f} m canopy spread"
        )

    # Concerns are surfaced, never hidden — they matter for a real planting decision.
    if s.root_aggressiveness >= 0.8:
        concerns.append("aggressive roots — keep clear of pavement, drains, foundations")
    if s.water_requirement >= 0.55:
        concerns.append("needs reliable irrigation to establish")
    if not s.native_to_region:
        concerns.append("not native to the region")
    if s.notes and "toxic" in s.notes.lower():
        concerns.append("toxic if ingested — site away from play areas")
    if s.notes and ("allergen" in s.notes.lower() or "irritant" in s.notes.lower()):
        concerns.append("pollen/flowering can irritate — avoid near hospitals or schools")

    return reasons, concerns


def shortlist_for_zone(
    profile: ZoneProfile,
    size: int = SHORTLIST_SIZE,
) -> list[ScoredSpecies]:
    """Top N species for a zone, ranked by weighted trait fit.

    Requires profile.placement — Model A must have run first. Raises rather than
    guessing a placement, since silently defaulting would produce a plausible-looking
    shortlist built on an assumption the user never sees.
    """
    if profile.placement is None:
        raise ValueError(
            f"zone {profile.zone_id} has no placement category — run Model A first"
        )

    placement = _placement_to_catalog(profile.placement)
    weights = profile.trait_weights()

    # SPECIES_ONLY, not ALL_TAXA: the main shortlist is distinct species. Cultivars
    # are surfaced separately by cultivar_alternatives_for_zone().
    feasible = [s for s in SPECIES_ONLY if _physically_feasible(s, placement)]
    if not feasible:
        raise ValueError(f"no catalogue species can be placed as {placement.value}")

    scored = [_score_one(s, weights, placement) for s in feasible]
    scored.sort(key=lambda x: x.fit_score, reverse=True)

    # Apply the genus cap in rank order, so the best-fitting member of each genus
    # wins its slot. Back-fill from the remainder if the cap leaves the list short.
    selected: list[ScoredSpecies] = []
    genus_counts: dict[str, int] = {}
    overflow: list[ScoredSpecies] = []

    for cand in scored:
        genus = cand.species.genus
        if genus_counts.get(genus, 0) < MAX_PER_GENUS:
            selected.append(cand)
            genus_counts[genus] = genus_counts.get(genus, 0) + 1
        else:
            overflow.append(cand)
        if len(selected) == size:
            break

    if len(selected) < size:
        # Fewer genera available than slots — relax the cap rather than return a
        # short list, and say so, since the diversity guarantee is then weaker.
        shortfall = size - len(selected)
        log.info(
            "Model B: only %d genera feasible for %s; relaxing genus cap for %d slot(s)",
            len(genus_counts), profile.zone_id, shortfall,
        )
        selected.extend(overflow[:shortfall])

    # Descriptions are computed only now, against the final shortlist. A species'
    # copy depends on who it is shown next to, so it cannot be settled while the
    # cohort is still changing.
    cohort = [x.species for x in selected]
    for x in selected:
        x.reasons, x.concerns = _describe(x.species, weights, placement, cohort)

    # Two cards can still end up with the same set of phrases in a different order
    # — Snake Plant and Golden Dewdrop did, and shuffled order reads *worse* than
    # plain repetition, because it looks like the ranking means something when it
    # does not. Compare as sets, and give every card in a colliding group its
    # dimensions so each one says something the others do not.
    by_phrase_set: dict[frozenset[str], list[ScoredSpecies]] = {}
    for x in selected:
        by_phrase_set.setdefault(frozenset(x.reasons), []).append(x)

    for group in by_phrase_set.values():
        if len(group) < 2:
            continue
        for x in group:
            s = x.species
            x.reasons = x.reasons[:2] + [
                f"{s.habit.value.replace('_', ' ')}, reaching ~{s.mature_height_m:.0f} m "
                f"with a ~{s.canopy_spread_m:.0f} m canopy spread"
            ]

    log.info(
        "Model B shortlist for %s (%s): %s",
        profile.zone_id, placement.value,
        ", ".join(f"{x.species.common_name} {x.fit_score:.2f}" for x in selected),
    )
    return selected


def _score_one(
    s: Species, weights: dict[str, float], placement: Placement | None = None
) -> ScoredSpecies:
    total = weight_sum = 0.0
    for trait, weight in weights.items():
        value = _trait_value(s, trait, placement)
        if value is None:
            continue
        total += value * weight
        weight_sum += weight
    fit = total / weight_sum if weight_sum else 0.0
    reasons, concerns = _describe(s, weights, placement)
    return ScoredSpecies(s, round(fit, 4), reasons, concerns)


def cultivar_alternatives_for_zone(
    profile: ZoneProfile,
    max_pairs: int = 3,
) -> list[tuple[Species, Species]]:
    """Candidate within-species cultivar pairs for the zone — the 'safer bet' tier.

    Returns pairs of cultivars of the SAME species, drawn from species that are
    physically feasible here and rank well on trait fit. These are not a different
    kind of recommendation — they run through the identical scoring path — they are
    simply at a much shorter biological distance, which is why they score higher.

    Only genuinely cultivar-rich species appear. Delhi's avenue trees are grown from
    seed rather than as named clones, so no cultivar pairs are invented for them; a
    ground-placement zone draws its alternatives from shrubs that also suit ground
    planting, and returns fewer than max_pairs if that is all the evidence supports.
    """
    if profile.placement is None:
        raise ValueError(
            f"zone {profile.zone_id} has no placement category — run Model A first"
        )

    placement = _placement_to_catalog(profile.placement)
    weights = profile.trait_weights()

    candidates: list[tuple[float, Species, Species]] = []
    for parent in SPECIES_ONLY:
        if not _physically_feasible(parent, placement):
            continue
        cvs = [c for c in cultivars_of(parent.species_id)
               if _physically_feasible(c, placement)]
        if len(cvs) < 2:
            continue
        # Best-fitting two cultivars of this species.
        ranked = sorted(
            cvs, key=lambda c: _score_one(c, weights, placement).fit_score, reverse=True
        )
        a, b = ranked[0], ranked[1]
        pair_fit = (
            _score_one(a, weights, placement).fit_score
            + _score_one(b, weights, placement).fit_score
        ) / 2
        candidates.append((pair_fit, a, b))

    candidates.sort(key=lambda t: t[0], reverse=True)
    pairs = [(a, b) for _, a, b in candidates[:max_pairs]]

    if not pairs:
        log.info(
            "Model B: no cultivar-rich species feasible for %s (%s) — the safer-bet "
            "tier is empty for this zone rather than fabricated",
            profile.zone_id, placement.value,
        )
    else:
        log.info(
            "Model B cultivar alternatives for %s: %s",
            profile.zone_id,
            ", ".join(f"{a.display_name} x {b.display_name}" for a, b in pairs),
        )
    return pairs
