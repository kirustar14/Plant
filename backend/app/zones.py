"""The 10 Delhi demo zones.

Which 10 locations appear is a curated demo choice, stated plainly in the UI: real,
recognisable Delhi sites chosen to span the range of conditions the tool reasons about
(arterial-traffic corridors, dense commercial cores, industrial estates, and
established green space). The brief's 3-vegetated / 7-bare split is likewise a
selection decision.

What is NOT curated: every measurement attached to these zones — road density,
building floor-space, NDVI, land surface temperature, air quality — is fetched from
its real source at runtime. The `expected_vegetation` field below records why a zone
was picked; the vegetation signal the app reports comes from measured NDVI, and the
two are compared at runtime so a mismatch shows up rather than being papered over.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import cos, radians

# Metres per degree at Delhi's latitude.
_M_PER_DEG_LAT = 111_320.0
_M_PER_DEG_LON = 111_320.0 * cos(radians(28.61))

ZONE_SIZE_M = 600  # each zone is a ~600m x 600m cell — walkable, street-scale


@dataclass(frozen=True)
class Zone:
    zone_id: str
    name: str
    locality: str
    latitude: float
    longitude: float
    # Why this site was selected for the demo set. Not a measurement.
    selection_rationale: str
    # The vegetation state the site was chosen to represent. Compared against
    # measured NDVI at runtime; never substituted for it.
    expected_vegetation: str  # "vegetated" | "bare"
    size_m: int = ZONE_SIZE_M

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(south, west, north, east) for Overpass queries."""
        dlat = (self.size_m / 2) / _M_PER_DEG_LAT
        dlon = (self.size_m / 2) / _M_PER_DEG_LON
        return (
            self.latitude - dlat,
            self.longitude - dlon,
            self.latitude + dlat,
            self.longitude + dlon,
        )


# --- Sites selected as established green space (3) ---------------------------
_VEGETATED = [
    Zone(
        zone_id="lodhi-garden",
        name="Lodhi Garden Perimeter",
        locality="Lodhi Estate",
        latitude=28.5931,
        longitude=77.2197,
        selection_rationale=(
            "Mature heritage park canopy bordered by arterial road — tests whether the "
            "model correctly deprioritises already-planted land while still registering "
            "roadside pollution load."
        ),
        expected_vegetation="vegetated",
    ),
    Zone(
        zone_id="nehru-park",
        name="Nehru Park, Chanakyapuri",
        locality="Chanakyapuri",
        latitude=28.5885,
        longitude=77.1910,
        selection_rationale=(
            "Large institutional green space in the low-density diplomatic enclave — "
            "high existing canopy, low floor-area ratio."
        ),
        expected_vegetation="vegetated",
    ),
    Zone(
        zone_id="sanjay-van",
        name="Sanjay Van Edge, Vasant Kunj",
        locality="Vasant Kunj",
        latitude=28.5245,
        longitude=77.1855,
        selection_rationale=(
            "Ridge forest remnant at the urban boundary — the highest-NDVI site in the "
            "set, useful as the upper anchor for the vegetation signal."
        ),
        expected_vegetation="vegetated",
    ),
]

# --- Sites selected as bare / hard-surfaced (7) -------------------------------
_BARE = [
    Zone(
        zone_id="anand-vihar",
        name="Anand Vihar ISBT Corridor",
        locality="Anand Vihar",
        latitude=28.6469,
        longitude=77.3159,
        selection_rationale=(
            "Interstate bus terminal and freight corridor, co-located with the Anand "
            "Vihar DPCC monitor — routinely among Delhi's worst PM2.5 readings, with "
            "minimal planted surface."
        ),
        expected_vegetation="bare",
    ),
    Zone(
        zone_id="connaught-place",
        name="Connaught Place Outer Circle",
        locality="Connaught Place",
        latitude=28.6315,
        longitude=77.2167,
        selection_rationale=(
            "Dense colonnaded commercial core — very high floor-area ratio and paved "
            "coverage leave little ground-plantable area, so placement should favour "
            "vertical and container options."
        ),
        expected_vegetation="bare",
    ),
    Zone(
        zone_id="karol-bagh",
        name="Ajmal Khan Road, Karol Bagh",
        locality="Karol Bagh",
        latitude=28.6519,
        longitude=77.1909,
        selection_rationale=(
            "Pedestrianised retail market with continuous multi-storey frontage — a "
            "wall-hanging placement test case."
        ),
        expected_vegetation="bare",
    ),
    Zone(
        zone_id="punjabi-bagh",
        name="Punjabi Bagh Club Road",
        locality="Punjabi Bagh",
        latitude=28.6695,
        longitude=77.1310,
        selection_rationale=(
            "Ring Road junction beside the Punjabi Bagh DPCC monitor — heavy arterial "
            "traffic against low-rise residential fabric."
        ),
        expected_vegetation="bare",
    ),
    Zone(
        zone_id="rk-puram",
        name="R K Puram Sector 12",
        locality="R K Puram",
        latitude=28.5646,
        longitude=77.1795,
        selection_rationale=(
            "Government housing blocks at the R K Puram DPCC monitor — wide setbacks "
            "mean genuine ground-planting capacity despite a bare present state."
        ),
        expected_vegetation="bare",
    ),
    Zone(
        zone_id="okhla-industrial",
        name="Okhla Industrial Area Phase II",
        locality="Okhla",
        latitude=28.5355,
        longitude=77.2730,
        selection_rationale=(
            "Industrial estate with large roof spans and freight movement — expected "
            "heat-island peak and near-zero existing vegetation."
        ),
        expected_vegetation="bare",
    ),
    Zone(
        zone_id="mayapuri",
        name="Mayapuri Industrial Area",
        locality="Mayapuri",
        latitude=28.6280,
        longitude=77.1200,
        selection_rationale=(
            "Scrap-metal and light-industry district — hard-surfaced, high thermal load, "
            "historically overlooked in greening programmes."
        ),
        expected_vegetation="bare",
    ),
]

ZONES: tuple[Zone, ...] = tuple(_VEGETATED + _BARE)

ZONES_BY_ID: dict[str, Zone] = {z.zone_id: z for z in ZONES}


def get_zone(zone_id: str) -> Zone:
    if zone_id not in ZONES_BY_ID:
        raise KeyError(f"unknown zone: {zone_id}")
    return ZONES_BY_ID[zone_id]
