"""Implementation cost for planting a zone, from published municipal unit costs.

METHOD, AND ITS LIMITS

    estimated cost = plants projected x published per-sapling unit cost

The unit cost is the Government of NCT of Delhi Department of Forests & Wildlife's
compensatory-plantation norm: Rs 5,950 per sapling covering plantation and ten years
of maintenance, of which Rs 1,000 is administrative and contingency charges over that
period. It is a real published rate that Delhi actually charges against, not a market
guess.

This is a **first-order estimate from a published unit rate, not a procurement
quote**, and it is presented through the same "How this estimate works" disclosure the
CO2 and PM projections use, because it deserves no more confidence than they get:

1. **It inherits the plant count's error.** The count is itself a first-order capacity
   estimate. Multiplying two estimates compounds them, so the cost is no more precise
   than the capacity figure it is derived from.
2. **Real execution cost varies.** Contractor rates, site access, soil condition and
   planting season all move the real number, in both directions.
3. **The norm describes ground sapling plantation.** Container and facade planting
   carry different cost structures — the container or mounting system instead of a
   tree guard, and more intensive irrigation. No published Delhi unit rate was found
   for either, so those zones report the ground norm with that mismatch stated rather
   than a silently invented rate.
4. **It excludes land and water infrastructure.** No land acquisition, no new water
   connection, no enabling civil works are in the norm.

Every returned estimate carries these as `caveats`, so the number cannot reach the UI
without them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..app.schemas import PlacementCategory

# --- Published unit cost -----------------------------------------------------
# GNCTD Department of Forests & Wildlife, existing norms of Compensatory Plantation.
SOURCE = (
    "GNCTD Department of Forests & Wildlife — compensatory plantation norms: "
    "Rs 5,950 per sapling for plantation and 10 years maintenance, including "
    "Rs 1,000 per sapling administrative and contingency charges"
)
PER_SAPLING_INR = 5_950
ADMIN_CONTINGENCY_INR = 1_000
FIELD_COST_INR = PER_SAPLING_INR - ADMIN_CONTINGENCY_INR
MAINTENANCE_YEARS = 10

# The same norm's planting density, carried so the per-hectare figure the department
# works in stays recoverable from what this module reports.
SAPLINGS_PER_HECTARE = 1_000

BASE_CAVEATS = [
    "First-order estimate from a published unit rate, not a procurement quote — real "
    "cost varies with contractor, site access, soil condition and planting season.",
    "Derived by multiplying the projected plant count, which is itself a first-order "
    "estimate, so the two sources of error compound.",
    "Excludes land acquisition, new water connections and enabling civil works — the "
    "norm covers planting and maintenance only.",
]

# Stated where it applies rather than folded into the number.
PLACEMENT_CAVEAT = (
    "The published norm describes ground sapling plantation. {placement} planting "
    "carries a different cost structure — {difference} — and no published Delhi unit "
    "rate was found for it, so this figure applies the ground norm rather than "
    "substituting an invented one."
)

_PLACEMENT_DIFFERENCE = {
    PlacementCategory.POTTED: (
        "the container and its soil volume instead of a tree guard, and irrigation "
        "that cannot lapse"
    ),
    PlacementCategory.WALL_HANGING: (
        "a mounting system and fed irrigation instead of a tree guard and ground "
        "establishment"
    ),
}


@dataclass
class ZoneCost:
    """Estimated implementation cost for one zone."""
    zone_id: str
    plant_count: int
    per_sapling_inr: int
    total_inr: int
    # Split out so the disclosure can show what the rate actually buys.
    field_cost_inr: int
    admin_contingency_inr: int
    maintenance_years: int
    # False when the zone's placement is not what the published norm describes.
    basis_matches_placement: bool
    basis: str
    source: str = SOURCE
    caveats: list[str] = field(default_factory=list)


def estimate_zone(
    zone_id: str,
    plant_count: int,
    placement: PlacementCategory | None,
) -> ZoneCost:
    """Cost of planting one zone at its projected capacity."""
    placement = placement or PlacementCategory.GROUND
    caveats = list(BASE_CAVEATS)

    matches = placement == PlacementCategory.GROUND
    if not matches:
        caveats.insert(
            0,
            PLACEMENT_CAVEAT.format(
                placement=placement.value.replace("_", "-").capitalize(),
                difference=_PLACEMENT_DIFFERENCE[placement],
            ),
        )

    if plant_count == 0:
        caveats.append(
            "No planting capacity was estimated for this zone, so there is no cost to "
            "estimate."
        )

    basis = (
        f"{plant_count:,} plants at the published Rs {PER_SAPLING_INR:,} per-sapling "
        f"norm (Rs {FIELD_COST_INR:,} plantation and {MAINTENANCE_YEARS}-year "
        f"maintenance, plus Rs {ADMIN_CONTINGENCY_INR:,} administrative and "
        f"contingency)"
    )

    return ZoneCost(
        zone_id=zone_id,
        plant_count=plant_count,
        per_sapling_inr=PER_SAPLING_INR,
        total_inr=plant_count * PER_SAPLING_INR,
        field_cost_inr=plant_count * FIELD_COST_INR,
        admin_contingency_inr=plant_count * ADMIN_CONTINGENCY_INR,
        maintenance_years=MAINTENANCE_YEARS,
        basis_matches_placement=matches,
        basis=basis,
        caveats=caveats,
    )
