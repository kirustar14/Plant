"""Model A core — priority score and placement category per zone.

PRIORITY SCORE combines five need signals, each normalised to 0-1 and each meaning
"how much would planting here help":

    pollution burden    measured AQI at the zone's nearest live monitor (CPCB scale)
    traffic load        class-weighted OSM road density (a proxy, not a count)
    heat load           zone LST minus the city median (relative heat island)
    vegetation deficit  1 - NDVI, so bare ground scores high and parks score low
    exposure            built floor-area ratio — how many people benefit per m2 planted

MISSING DATA IS NOT ZERO. If GEE is unavailable there is no NDVI or LST, and scoring
on a zero for those would rank every zone as maximally bare and maximally cool at the
same time — confidently wrong in both directions. Instead the weights of the
available signals are renormalised, the score records which inputs it used, and
`confidence` drops in proportion to what is missing. A zone scored without satellite
data says so.

PLACEMENT CATEGORY is a physical-feasibility decision, not a preference. It asks what
can actually be established given how much of the zone is built on and how tall the
built fabric is:

    ground          enough unbuilt, unpaved area to root a tree
    wall_hanging    little open ground, but multi-storey facades to plant against
    potted          neither — containers on hard surfaces are the only option

Model A does not import Model B. It emits a ZoneProfile (app.schemas); Model B
consumes one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ...app.schemas import PlacementCategory, ZoneProfile

log = logging.getLogger(__name__)

# --- Priority weights --------------------------------------------------------
# Ordered by how directly each signal ties to the benefit of planting. Pollution and
# heat are what trees measurably mitigate, so they lead. Vegetation deficit matters
# because planting where canopy already exists adds least. Exposure is a multiplier
# on who benefits, weighted lowest because a high-FAR zone is also the hardest to
# plant in — it is a benefit signal, not a feasibility one.
WEIGHTS: dict[str, float] = {
    "pollution_burden": 0.30,
    "heat_load": 0.22,
    "vegetation_deficit": 0.22,
    "traffic_load": 0.16,
    "exposure": 0.10,
}

# --- Normalisation reference points -----------------------------------------
# CPCB AQI 400+ is "Severe"; anchoring the top of the scale there keeps Delhi's
# routinely-poor air from saturating every zone at 1.0.
AQI_REFERENCE = 400.0
# Class-weighted road km per km2. Measured range across the demo zones ran roughly
# 30-50, so 70 leaves headroom without compressing the observed spread.
TRAFFIC_REFERENCE = 70.0
# Degrees C above the city median that counts as a maximal heat island.
HEAT_DELTA_REFERENCE = 6.0
# Floor-area ratio treated as maximal exposure.
FAR_REFERENCE = 3.0

# --- Placement thresholds ----------------------------------------------------
# Minimum plantable-ground fraction (zone area minus buildings minus road surface)
# to establish trees at root depth.
#
# Calibrated against a real failure: an earlier version tested `1 - footprint` and
# put all ten demo zones in "ground", including Connaught Place's colonnaded
# commercial core — because it counted every road, pavement and plaza as soil. With
# road surface subtracted, 0.35 separates zones with genuine open ground from those
# where the unbuilt area is essentially all carriageway.
#
# Because plantable_ground_ratio is an upper bound (untagged paving and private land
# inflate it), this threshold is deliberately stricter than the raw number suggests.
GROUND_AVAILABILITY_THRESHOLD = 0.35
# Floor-area ratio implying facades tall enough to be worth planting against.
WALL_FAR_THRESHOLD = 0.60


@dataclass
class ZoneScore:
    """Model A's output for one zone, with every contributing signal inspectable."""
    zone_id: str
    priority_score: float
    placement: PlacementCategory
    placement_rationale: str

    signals: dict[str, float] = field(default_factory=dict)
    weights_used: dict[str, float] = field(default_factory=dict)
    missing_signals: list[str] = field(default_factory=list)
    confidence: float = 1.0

    @property
    def priority_band(self) -> str:
        if self.priority_score >= 0.70:
            return "Critical"
        if self.priority_score >= 0.50:
            return "High"
        if self.priority_score >= 0.30:
            return "Moderate"
        return "Low"


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _pollution_burden(aqi: float | None) -> float | None:
    if aqi is None:
        return None
    return _clamp01(aqi / AQI_REFERENCE)


def _traffic_load(traffic_proxy: float | None) -> float | None:
    if traffic_proxy is None:
        return None
    return _clamp01(traffic_proxy / TRAFFIC_REFERENCE)


def _heat_load(heat_delta_c: float | None) -> float | None:
    """Relative heat island, rescaled so the city median sits mid-scale.

    A zone at the median scores 0.5, not 0 — being averagely hot in Delhi is still
    hot. Cooler-than-median zones fall below 0.5, hotter ones above.
    """
    if heat_delta_c is None:
        return None
    return _clamp01(0.5 + heat_delta_c / (2 * HEAT_DELTA_REFERENCE))


def _vegetation_deficit(ndvi: float | None) -> float | None:
    """How much the zone lacks vegetation.

    NDVI runs -1 to 1, but built and vegetated urban surfaces occupy roughly 0 to
    0.6, so that is the span rescaled. Water and cloud can push NDVI negative;
    clamping handles it without distorting the useful range.
    """
    if ndvi is None:
        return None
    return _clamp01((0.6 - ndvi) / 0.6)


def _exposure(floor_area_ratio: float | None) -> float | None:
    if floor_area_ratio is None:
        return None
    return _clamp01(floor_area_ratio / FAR_REFERENCE)


def decide_placement(
    plantable_ground_ratio: float | None,
    floor_area_ratio: float | None,
    footprint_ratio: float | None = None,
    road_surface_ratio: float | None = None,
) -> tuple[PlacementCategory, str]:
    """Choose how planting can physically happen, with a stated reason.

    Falls back to ground when OSM data is missing, because ground planting is the
    least-constrained option — and the rationale says the decision was made without
    building data rather than implying it was measured.
    """
    if plantable_ground_ratio is None and floor_area_ratio is None:
        return (
            PlacementCategory.GROUND,
            "No building or road data available — defaulted to ground planting; "
            "verify on site before committing.",
        )

    plantable = plantable_ground_ratio if plantable_ground_ratio is not None else 1.0
    far = floor_area_ratio if floor_area_ratio is not None else 0.0

    # Describe the surface breakdown when we have it — this is the evidence for the
    # decision, and it belongs in front of the user rather than only in the score.
    breakdown = ""
    if footprint_ratio is not None and road_surface_ratio is not None:
        breakdown = (
            f" ({footprint_ratio:.0%} built, {road_surface_ratio:.0%} road surface)"
        )

    if plantable >= GROUND_AVAILABILITY_THRESHOLD:
        return (
            PlacementCategory.GROUND,
            f"About {plantable:.0%} of the zone is unbuilt and unpaved{breakdown} — "
            f"enough open ground to establish trees at root depth.",
        )

    if far >= WALL_FAR_THRESHOLD:
        return (
            PlacementCategory.WALL_HANGING,
            f"Only about {plantable:.0%} of the zone is plantable ground{breakdown}, "
            f"but a floor-area ratio of {far:.2f} means multi-storey facades are "
            f"available to plant against.",
        )

    return (
        PlacementCategory.POTTED,
        f"Only about {plantable:.0%} of the zone is plantable ground{breakdown} and "
        f"the fabric is low-rise (FAR {far:.2f}) — containers on hard surfaces are "
        f"the realistic option.",
    )


def score_zone(profile: ZoneProfile) -> ZoneScore:
    """Compute priority score and placement for one zone from its measurements."""
    raw: dict[str, float | None] = {
        "pollution_burden": _pollution_burden(profile.aqi),
        "traffic_load": _traffic_load(profile.traffic_proxy),
        "heat_load": _heat_load(profile.heat_island_delta_c),
        "vegetation_deficit": _vegetation_deficit(profile.ndvi),
        "exposure": _exposure(profile.floor_area_ratio),
    }

    available = {k: v for k, v in raw.items() if v is not None}
    missing = sorted(k for k, v in raw.items() if v is None)

    if not available:
        raise ValueError(
            f"zone {profile.zone_id} has no usable measurements — refusing to "
            f"produce a priority score from nothing"
        )

    # Renormalise over what we actually have, so a missing signal neither counts as
    # zero nor silently shrinks the score.
    total_weight = sum(WEIGHTS[k] for k in available)
    weights_used = {k: WEIGHTS[k] / total_weight for k in available}
    score = sum(available[k] * weights_used[k] for k in available)

    # Confidence is the share of the intended weight that was actually measured.
    confidence = total_weight / sum(WEIGHTS.values())

    placement, rationale = decide_placement(
        profile.plantable_ground_ratio,
        profile.floor_area_ratio,
        profile.building_footprint_ratio,
        profile.road_surface_ratio,
    )

    if missing:
        log.info(
            "Model A %s: scored on %d/%d signals (missing: %s), confidence %.2f",
            profile.zone_id, len(available), len(raw), ", ".join(missing), confidence,
        )

    return ZoneScore(
        zone_id=profile.zone_id,
        priority_score=round(score, 4),
        placement=placement,
        placement_rationale=rationale,
        signals={k: round(v, 4) for k, v in available.items()},
        weights_used={k: round(v, 4) for k, v in weights_used.items()},
        missing_signals=missing,
        confidence=round(confidence, 3),
    )


def apply_score(profile: ZoneProfile, score: ZoneScore) -> ZoneProfile:
    """Write Model A's outputs back onto the profile Model B will consume."""
    profile.priority_score = score.priority_score
    profile.placement = score.placement
    if score.missing_signals:
        for signal in score.missing_signals:
            gap = f"Model A: {signal} unavailable"
            if gap not in profile.data_gaps:
                profile.data_gaps.append(gap)
    return profile
