"""Shared data contracts between Model A, Model B, and the API layer.

Lives outside both model packages on purpose. Model A produces a ZoneProfile; Model B
consumes one. Neither imports the other, so the two models stay independently
testable and separately replaceable — the separation the brief asks to be visible in
the architecture, not just described.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PlacementCategory(str, Enum):
    """Model A's output category — how planting must physically happen in a zone."""
    GROUND = "ground"
    WALL_HANGING = "wall_hanging"
    POTTED = "potted"


@dataclass
class ZoneProfile:
    """Everything Model B needs to know about a zone, produced by Model A.

    Deliberately carries no OpenAQ/Overpass/GEE types — just measured values and
    their provenance, so Model B never reaches back into a data source itself.
    """
    zone_id: str
    name: str
    latitude: float
    longitude: float

    # --- Measured environmental inputs ---------------------------------------
    aqi: float | None = None
    pm25_ugm3: float | None = None
    aqi_attribution: str = ""

    traffic_proxy: float | None = None          # class-weighted road km per km2
    road_density_km_per_km2: float | None = None
    floor_area_ratio: float | None = None
    building_footprint_ratio: float | None = None
    road_surface_ratio: float | None = None
    # Zone area minus buildings minus road surface. An upper bound on plantable
    # ground, since untagged paving and private land still count toward it.
    plantable_ground_ratio: float | None = None

    ndvi: float | None = None                   # existing vegetation, -1 to 1
    land_surface_temp_c: float | None = None
    heat_island_delta_c: float | None = None
    light_pollution_mcd_m2: float | None = None

    # --- Model A outputs ------------------------------------------------------
    priority_score: float | None = None
    placement: PlacementCategory | None = None
    has_existing_vegetation: bool | None = None

    # --- Provenance -----------------------------------------------------------
    sources: dict[str, str] = field(default_factory=dict)
    data_gaps: list[str] = field(default_factory=list)

    def trait_weights(self) -> dict[str, float]:
        """Translate measured conditions into how much each plant trait matters here.

        This is the bridge from Model A's environment to Model B's species scoring.
        Weights are derived from the zone's own measurements, so two zones produce
        genuinely different shortlists rather than a single global ranking.
        """
        w: dict[str, float] = {}

        # Air quality drives pollution tolerance. CPCB 'Poor' begins at 200.
        if self.aqi is not None:
            w["pollution_tolerance"] = min(1.0, max(0.2, self.aqi / 250.0))
        else:
            w["pollution_tolerance"] = 0.6

        # Traffic implies roadside stress and particulate load.
        if self.traffic_proxy is not None:
            w["pollution_tolerance"] = max(
                w["pollution_tolerance"], min(1.0, self.traffic_proxy / 60.0)
            )

        # Delhi summers are extreme everywhere; heat island raises it further.
        heat = 0.7
        if self.heat_island_delta_c is not None:
            heat = min(1.0, 0.5 + self.heat_island_delta_c / 8.0)
        w["heat_tolerance"] = heat
        w["canopy_cooling"] = heat

        # Dense built fabric means shade, restricted rooting, and services to protect.
        if self.floor_area_ratio is not None:
            far = self.floor_area_ratio
            w["shade_tolerance"] = min(1.0, far / 2.5)
            w["low_root_risk"] = min(1.0, 0.3 + far / 2.0)
        else:
            w["shade_tolerance"] = 0.3
            w["low_root_risk"] = 0.4

        # Irrigation is the usual failure mode for Delhi plantings.
        w["drought_tolerance"] = 0.75
        w["low_water"] = 0.65

        # Already-vegetated zones gain less from more of the same, so the emphasis
        # shifts toward species that add capability rather than volume.
        if self.has_existing_vegetation:
            w["carbon_capture"] = 0.35
            w["native"] = 0.6
        else:
            w["carbon_capture"] = 0.75
            w["native"] = 0.45

        return w
