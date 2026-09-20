"""Water availability per zone — CGWB groundwater stress, at block resolution.

WHAT THIS IS
    The Central Ground Water Board categorises groundwater by administrative
    assessment unit. For Delhi that unit is the tehsil, so the finest honest answer to
    "is there water here" is a tehsil-wide category: safe, semi-critical, critical, or
    over-exploited.

WHAT THIS DELIBERATELY IS NOT
    A zone-level measurement. The demo zones are 600 m cells; a tehsil is tens of
    square kilometres. Nothing here interpolates a per-zone water figure, because the
    source has no such figure to interpolate from — every payload carries the tehsil
    name and a resolution note so the UI cannot present it as zone-specific.

    Two further gaps are reported rather than smoothed over:
      - a zone whose tehsil is not in the sourced table gets no category, rather than
        borrowing a neighbouring tehsil's;
      - a zone whose containing tehsil was not verified against a boundary dataset is
        marked `approximate`, and says which way the ambiguity cuts.

Unlike NDVI or air quality this is a static published assessment, not a live read, so
it loads from disk once rather than going out over the network.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "static" / "cgwb_groundwater.json"

# Ordered worst-first — used to rank zones and to drive the UI's colour ramp.
SEVERITY: dict[str, int] = {
    "over_exploited": 3,
    "critical": 2,
    "semi_critical": 1,
    "safe": 0,
}

CATEGORY_LABEL: dict[str, str] = {
    "over_exploited": "Over-exploited",
    "critical": "Critical",
    "semi_critical": "Semi-critical",
    "safe": "Safe",
}

# What the category means for the thing this app actually recommends: establishing
# new planting. Phrased as an irrigation constraint, which is what a planner does
# something about, rather than as a restatement of the hydrology.
PLANTING_IMPLICATION: dict[str, str] = {
    "over_exploited": (
        "Extraction already exceeds recharge across this block, so new planting "
        "should not assume borewell irrigation during establishment."
    ),
    "critical": (
        "Groundwater is at or near its replenishable limit across this block; "
        "establishment-phase irrigation needs a non-groundwater source."
    ),
    "semi_critical": (
        "Extraction is approaching the replenishable limit across this block; "
        "irrigation demand should be planned rather than assumed."
    ),
    "safe": (
        "Extraction is below the replenishable limit across this block, so "
        "establishment-phase irrigation is less constrained here."
    ),
}


@dataclass
class WaterAvailability:
    """CGWB groundwater status for the block containing a zone."""
    zone_id: str
    tehsil: str | None
    district: str | None
    category: str | None            # None when the tehsil has no published category
    category_label: str | None
    severity: int | None
    match: str                      # "confirmed" | "approximate" | "unmapped"
    implication: str | None
    # Why this is a block reading and not a zone reading. Always populated.
    resolution_note: str = ""
    caveats: list[str] = field(default_factory=list)
    source: str = ""
    assessment_year: int | None = None

    @property
    def available(self) -> bool:
        return self.category is not None


_store: dict | None = None


def _load() -> dict:
    global _store
    if _store is None:
        try:
            _store = json.loads(DATA_PATH.read_text())
        except FileNotFoundError:
            log.warning("No CGWB groundwater dataset at %s", DATA_PATH)
            _store = {"tehsils": {}, "zone_tehsil": {}}
    return _store


def for_zone(zone_id: str) -> WaterAvailability:
    """Groundwater status for the block containing `zone_id`.

    Returns a populated object even when nothing is known, so the caller always has a
    resolution note and a reason to show rather than a bare null.
    """
    store = _load()
    mapping = store.get("zone_tehsil", {}).get(zone_id)
    source = store.get("source", "")
    year = store.get("assessment_year")
    resolution = store.get("resolution_note", "")

    if mapping is None:
        return WaterAvailability(
            zone_id=zone_id, tehsil=None, district=None, category=None,
            category_label=None, severity=None, match="unmapped", implication=None,
            resolution_note=resolution, source=source, assessment_year=year,
            caveats=["This zone has not been mapped to a CGWB assessment block."],
        )

    tehsil = mapping["tehsil"]
    entry = store.get("tehsils", {}).get(tehsil)
    category = entry["category"] if entry else None
    district = entry["district"] if entry else None

    caveats: list[str] = []
    if category is None:
        caveats.append(
            f"{tehsil} does not appear in the sourced CGWB table, so no category is "
            f"reported. A neighbouring tehsil's category is not substituted."
        )
    if mapping["match"] == "approximate":
        caveats.append(f"Block assignment is approximate — {mapping['note']}")
    if category is not None:
        caveats.append(
            f"Category describes the whole of {tehsil} tehsil, not this 600 m zone."
        )

    return WaterAvailability(
        zone_id=zone_id,
        tehsil=tehsil,
        district=district,
        category=category,
        category_label=CATEGORY_LABEL.get(category) if category else None,
        severity=SEVERITY.get(category) if category else None,
        match=mapping["match"],
        implication=PLANTING_IMPLICATION.get(category) if category else None,
        resolution_note=resolution,
        caveats=caveats,
        source=source,
        assessment_year=year,
    )


def dataset_note() -> dict:
    """Provenance for the health endpoint and the UI's sources list."""
    store = _load()
    return {
        "source": store.get("source", ""),
        "assessment_year": store.get("assessment_year"),
        "tehsils_known": len(store.get("tehsils", {})),
        "zones_mapped": len(store.get("zone_tehsil", {})),
        "newer_assessment_note": store.get("newer_assessment_note", ""),
    }
