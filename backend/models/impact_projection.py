"""Projected environmental impact of planting a zone.

METHOD, AND ITS LIMITS

    projected impact = planting capacity x published per-plant coefficients

Capacity comes from each zone's own measured surfaces (OSM footprint, road surface,
plantable ground). Coefficients are per-individual published rates following i-Tree
Eco methodology, carried on each species in the catalogue.

This is a **first-order estimate, not a simulation**, and three things it ignores are
large enough that the UI states them rather than burying them here:

1. **It is linear.** Real benefit has diminishing returns — the tenth tree on a street
   removes less marginal PM than the first, because they compete for the same air
   parcel, and canopies shade each other. Nothing here models that.
2. **It is at maturity.** The coefficients describe a grown plant. A newly planted
   tree delivers a small fraction of this for its first decade, and the maturity
   horizon differs per species.
3. **It assumes survival.** Urban planting mortality in Delhi conditions is
   substantial. No survival discount is applied, so treat the figure as a ceiling on
   a successful planting rather than an expected value.

Every returned projection carries these as `caveats`, so a number cannot travel
through the API into the UI without them.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from ..app.schemas import PlacementCategory, ZoneProfile
from .model_b_genomic.species_catalog import Species

log = logging.getLogger(__name__)

# --- Capacity assumptions ----------------------------------------------------
# Each is an explicit, inspectable judgement. They are conservative on purpose:
# over-stating capacity is how greening proposals lose credibility.

# Fraction of "unbuilt and unpaved" land that is realistically plantable. The rest is
# private land, in active use, too narrow to root a tree, or occupied by services.
GROUND_UTILISATION = 0.25

# Fraction of facade area that can carry a green wall. Windows, doors, entrances,
# signage, ownership boundaries and access requirements consume most of it.
WALL_UTILISATION = 0.15
# Assumed storey height for converting building levels into facade height.
STOREY_HEIGHT_M = 3.2

# NOTE ON WALL COUNTING. An earlier version multiplied per-mature-individual
# coefficients by green-wall module density (~9 plants/m2) and produced 17,000
# plants and 129 t CO2/yr for Connaught Place — more than every ground zone
# combined. That was a unit error, not a finding: a wall module holds a cutting,
# while the catalogue coefficients describe a mature free-standing specimen.
# Wall capacity is now counted in MATURE-EQUIVALENTS — how many fully grown plants
# the usable facade area could support — which is dimensionally consistent with the
# coefficients being applied to it.

# Containers restrict root volume, so a potted specimen never reaches the biomass of
# the same species in open ground. Applied to its coefficients rather than pretending
# a planter and a street tree are equivalent.
POTTED_BIOMASS_FRACTION = 0.35

# Planting into land that is already vegetated adds little: the canopy is there. NDVI
# above this is treated as already-green and excluded from plantable capacity.
NDVI_ALREADY_GREEN = 0.30
# NDVI at which a surface is effectively fully vegetated, for scaling between them.
NDVI_FULLY_GREEN = 0.60

# Containers per 100 m2 of hard surface. Deliberately low: containers need
# maintenance access and cannot obstruct footfall.
POTTED_PER_100M2 = 1.2
# Share of a zone's sealed surface that could host containers without blocking use.
POTTED_SURFACE_SHARE = 0.08

# Spacing floor so a small shrub is not assumed to be planted at unrealistic density.
MIN_AREA_PER_PLANT_M2 = 2.0

# Years to the maturity the coefficients describe, by growth habit.
MATURITY_YEARS = {
    "large_tree": 20,
    "medium_tree": 15,
    "small_tree": 10,
    "shrub": 5,
    "climber": 4,
    "herb": 2,
}


@dataclass
class ZoneImpact:
    """Projected annual impact for one zone at plant maturity."""
    zone_id: str
    plant_count: int
    species_id: str
    species_name: str
    placement: str

    co2_kg_yr: float
    pm_deposition_kg_yr: float
    cooling_kwh_yr: float

    maturity_years: int
    capacity_basis: str
    caveats: list[str] = field(default_factory=list)

    @property
    def co2_tonnes_yr(self) -> float:
        return round(self.co2_kg_yr / 1000.0, 2)

    # Rough equivalence for the dashboard: an average Indian passenger car emits
    # about 2 tonnes CO2/yr. Useful for scale, not a claim about offsetting.
    @property
    def cars_equivalent(self) -> float:
        return round(self.co2_kg_yr / 2000.0, 1)


BASE_CAVEATS = [
    "First-order estimate: impact is modelled linearly and ignores diminishing "
    "returns as plantings compete for the same air and light.",
    "Figures are at plant maturity — a newly planted specimen delivers a fraction "
    "of this for its first years.",
    "No survival discount applied; urban planting mortality is significant, so treat "
    "this as a ceiling rather than an expected value.",
]


def _vacant_fraction(ndvi: float | None) -> tuple[float, str]:
    """How much of the unbuilt ground is NOT already vegetated.

    Without this, a zone like Lodhi Garden — 74% unbuilt but the greenest site in
    the set — is credited with capacity for over a thousand new trees on land that
    already carries mature canopy. Planting there adds almost nothing, and the
    projection has to say so.
    """
    if ndvi is None:
        return 1.0, "no NDVI measurement, so no allowance for existing vegetation"
    if ndvi <= NDVI_ALREADY_GREEN:
        return 1.0, ""
    green = min(
        1.0, (ndvi - NDVI_ALREADY_GREEN) / (NDVI_FULLY_GREEN - NDVI_ALREADY_GREEN)
    )
    vacant = max(0.0, 1.0 - green)
    return vacant, (
        f"NDVI {ndvi:.2f} indicates roughly {green:.0%} of the open ground is already "
        f"vegetated, so only {vacant:.0%} is counted as available"
    )


def _ground_capacity(profile: ZoneProfile, species: Species, zone_area_m2: float) -> tuple[int, str]:
    plantable_ratio = profile.plantable_ground_ratio
    if plantable_ratio is None:
        return 0, "no plantable-ground measurement available"

    vacant, vacancy_note = _vacant_fraction(profile.ndvi)
    plantable_m2 = zone_area_m2 * plantable_ratio * GROUND_UTILISATION * vacant
    # One plant per canopy footprint — spacing closer than the mature canopy means
    # the canopy figures in the coefficients would not be reached.
    area_each = max(MIN_AREA_PER_PLANT_M2, math.pi * (species.canopy_spread_m / 2) ** 2)
    count = int(plantable_m2 / area_each)
    basis = (
        f"{plantable_ratio:.0%} of the zone is unbuilt and unpaved; assuming "
        f"{GROUND_UTILISATION:.0%} of that is realistically plantable"
    )
    if vacancy_note:
        basis += f"; {vacancy_note}"
    basis += (
        f" — {plantable_m2:,.0f} m² at one plant per {area_each:.0f} m² "
        f"({species.canopy_spread_m:.0f} m mature canopy)"
    )
    return count, basis


def _wall_capacity(
    profile: ZoneProfile, species: Species, zone_area_m2: float
) -> tuple[int, str]:
    footprint_ratio = profile.building_footprint_ratio
    far = profile.floor_area_ratio
    if footprint_ratio is None or far is None or footprint_ratio <= 0:
        return 0, "no building measurement available"

    footprint_m2 = zone_area_m2 * footprint_ratio
    # Mean storeys implied by FAR over the built footprint.
    storeys = max(1.0, far / footprint_ratio)
    height_m = storeys * STOREY_HEIGHT_M
    # Perimeter of an equivalent square footprint — a deliberate under-estimate,
    # since real urban blocks are more elongated and have more facade per unit area.
    perimeter_m = 4 * math.sqrt(footprint_m2)
    facade_m2 = perimeter_m * height_m
    usable_m2 = facade_m2 * WALL_UTILISATION

    # Counted in mature-equivalents: the wall area one fully grown specimen of this
    # species would cover. This keeps the count dimensionally consistent with the
    # per-individual coefficients that get applied to it.
    coverage_each = max(
        MIN_AREA_PER_PLANT_M2, math.pi * (species.canopy_spread_m / 2) ** 2
    )
    count = int(usable_m2 / coverage_each)
    basis = (
        f"{footprint_m2:,.0f} m² of building footprint at ~{storeys:.1f} storeys "
        f"gives roughly {facade_m2:,.0f} m² of facade; assuming "
        f"{WALL_UTILISATION:.0%} is plantable ({usable_m2:,.0f} m²), counted as "
        f"mature-equivalent plants each covering {coverage_each:.0f} m²"
    )
    return count, basis


def _potted_capacity(profile: ZoneProfile, zone_area_m2: float) -> tuple[int, str]:
    footprint = profile.building_footprint_ratio or 0.0
    road = profile.road_surface_ratio or 0.0
    sealed_ratio = min(1.0, footprint + road)
    if sealed_ratio <= 0:
        return 0, "no sealed-surface measurement available"

    sealed_m2 = zone_area_m2 * sealed_ratio * POTTED_SURFACE_SHARE
    count = int(sealed_m2 / 100.0 * POTTED_PER_100M2)
    basis = (
        f"{sealed_ratio:.0%} of the zone is sealed surface; assuming "
        f"{POTTED_SURFACE_SHARE:.0%} of it can host containers "
        f"({sealed_m2:,.0f} m²) at {POTTED_PER_100M2} containers per 100 m² "
        f"without obstructing use"
    )
    return count, basis


def project_zone(
    profile: ZoneProfile,
    species: Species,
    zone_area_m2: float,
) -> ZoneImpact:
    """Projected annual impact for one zone planted with one species.

    The species is normally the zone's top-ranked shortlist entry — the projection
    answers "if this recommendation were followed", not "if a perfect mix were
    planted", which keeps it traceable to something the tool actually recommended.
    """
    placement = profile.placement or PlacementCategory.GROUND
    caveats = list(BASE_CAVEATS)

    # Scales the per-individual coefficients when the growing condition means a
    # plant will not reach the biomass those coefficients describe.
    vigour = 1.0

    if placement == PlacementCategory.GROUND:
        count, basis = _ground_capacity(profile, species, zone_area_m2)
    elif placement == PlacementCategory.WALL_HANGING:
        count, basis = _wall_capacity(profile, species, zone_area_m2)
        caveats.append(
            "Facade area is inferred from building footprint and floor-area ratio, "
            "not measured directly."
        )
        caveats.append(
            "Counted in mature-equivalent plants, not green-wall modules — a module "
            "holds a cutting, while these coefficients describe a grown specimen."
        )
    else:
        count, basis = _potted_capacity(profile, zone_area_m2)
        vigour = POTTED_BIOMASS_FRACTION
        caveats.append(
            f"Container planting is scaled to {POTTED_BIOMASS_FRACTION:.0%} of "
            f"open-ground performance, because restricted root volume limits the "
            f"biomass a potted specimen reaches."
        )
        caveats.append(
            "Container planting depends entirely on irrigation and maintenance being "
            "funded; without both, survival is very low."
        )

    if count == 0:
        caveats.append("Capacity could not be estimated from available measurements.")

    # Leaf area drives particulate capture, so PM is scaled by canopy area per plant
    # rather than applied per stem.
    leaf_area_each = math.pi * (species.canopy_spread_m / 2) ** 2
    pm_kg = count * species.pm_deposition_g_m2_yr * leaf_area_each * vigour / 1000.0

    return ZoneImpact(
        zone_id=profile.zone_id,
        plant_count=count,
        species_id=species.species_id,
        species_name=species.display_name,
        placement=placement.value,
        co2_kg_yr=round(count * species.co2_sequestration_kg_yr * vigour, 1),
        pm_deposition_kg_yr=round(pm_kg, 2),
        cooling_kwh_yr=round(
            count * species.transpiration_cooling_kwh_yr * vigour, 1
        ),
        maturity_years=MATURITY_YEARS.get(species.habit.value, 10),
        capacity_basis=basis,
        caveats=caveats,
    )


def aggregate(impacts: list[ZoneImpact]) -> dict:
    """Sum projections across selected zones.

    Summing is itself linear and therefore optimistic — two adjacent zones planted
    together do not deliver the arithmetic sum of their separate effects. Said
    plainly in the returned payload rather than left implicit.
    """
    if not impacts:
        return {
            "zone_count": 0,
            "plant_count": 0,
            "co2_kg_yr": 0.0,
            "co2_tonnes_yr": 0.0,
            "pm_deposition_kg_yr": 0.0,
            "cooling_kwh_yr": 0.0,
            "cars_equivalent": 0.0,
            "caveats": [],
        }

    co2 = sum(i.co2_kg_yr for i in impacts)
    return {
        "zone_count": len(impacts),
        "plant_count": sum(i.plant_count for i in impacts),
        "co2_kg_yr": round(co2, 1),
        "co2_tonnes_yr": round(co2 / 1000.0, 2),
        "pm_deposition_kg_yr": round(sum(i.pm_deposition_kg_yr for i in impacts), 2),
        "cooling_kwh_yr": round(sum(i.cooling_kwh_yr for i in impacts), 1),
        "cars_equivalent": round(co2 / 2000.0, 1),
        "max_maturity_years": max(i.maturity_years for i in impacts),
        "caveats": BASE_CAVEATS + [
            "Totals are summed across zones, which assumes zone effects are "
            "independent — adjacent plantings overlap in practice."
        ],
    }
