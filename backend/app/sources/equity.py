"""Income-equity proxy per zone — measured from housing-type records, not income.

THE HONEST PROBLEM
    Delhi has no fine-grained public income dataset at neighbourhood scale. Inventing
    one would be the single least defensible thing this project could do, so nothing
    here estimates income.

WHAT IS MEASURED INSTEAD
    A spatial join against the Delhi Urban Shelter Improvement Board's published list
    of 675 J.J. bastis with geo-coordinates. For each zone this counts how many
    recorded informal-settlement clusters fall inside the zone, and how many lie
    within a short walk of it. That is a measurement of *housing type on the ground*,
    from a government record, at coordinates the record itself publishes.

    It is a proxy for "this area likely includes lower-income households". It is not
    an income figure, and every payload says so in a label the UI is required to show.

THE THREE LIMITS THAT MATTER
    1. Each basti is published as ONE representative point, not a boundary. A cluster
       whose point sits just outside a zone may still extend into it, so in-zone
       counts are a lower bound. Proximity is therefore reported alongside them and is
       the more informative of the two.
    2. JJ bastis are one settlement type among several. Unauthorised colonies (the DDA
       PM-UDAY list of 1,731), resettlement colonies and urban villages are separate
       records not joined here. Absence of a basti is NOT evidence of affluence.
    3. The list was published in 2022. Clusters are demolished and resettled, so the
       record lags the ground.

WHAT THIS IS FOR
    A transparency check on the priority ranking — whether the zones the model pushes
    to the top are drawn from across the housing-type spectrum or only one end of it.
    It is deliberately NOT an input to the priority score: silently reweighting the
    ranking by a proxy this coarse would be a worse error than not using it at all.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from math import cos, radians, sqrt
from pathlib import Path

log = logging.getLogger(__name__)

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "static" / "dusib_jj_bastis.json"

# Metres per degree at Delhi's latitude — the same flat-earth approximation the zone
# bounding boxes use, which is accurate to well under a metre over a few kilometres.
_M_PER_DEG_LAT = 111_320.0
_M_PER_DEG_LON = 111_320.0 * cos(radians(28.61))

# How far from a zone centre a cluster still counts as "serving this area". 1.5 km is
# roughly a 15-20 minute walk — the range over which a planting scheme's shade and air
# benefits plausibly reach the people living in it.
PROXIMITY_M = 1500
# Pre-formatted for prose: integer division renders 1500 m as "1 km", which understates
# the range every summary sentence quotes.
PROXIMITY_LABEL = f"{PROXIMITY_M / 1000:g} km"

# Tier thresholds on the proximity count. Stated here, surfaced in the payload, and
# shown in the UI, so the banding is inspectable rather than a black box.
TIER_SUBSTANTIAL = 10   # clusters within PROXIMITY_M
TIER_SOME = 1

TIER_LABEL = {
    "substantial": "Substantial recorded informal settlement",
    "some": "Some recorded informal settlement",
    "none": "No recorded informal settlement nearby",
}

PROXY_LABEL = (
    "Proxy based on housing-type records, not income data"
)


@dataclass
class EquitySignal:
    """Housing-type proxy for one zone, with the measurement behind it."""
    zone_id: str
    clusters_in_zone: int
    clusters_within_range: int
    range_m: int
    tier: str                       # "substantial" | "some" | "none"
    tier_label: str
    nearest_name: str | None
    nearest_distance_m: float | None
    # Named examples, so the number is checkable against something real.
    examples: list[str] = field(default_factory=list)
    proxy_label: str = PROXY_LABEL
    caveats: list[str] = field(default_factory=list)
    source: str = ""
    published: str | None = None


_store: dict | None = None


def _load() -> dict:
    global _store
    if _store is None:
        try:
            _store = json.loads(DATA_PATH.read_text())
        except FileNotFoundError:
            log.warning(
                "No DUSIB JJ-basti dataset at %s — equity signal will be unavailable. "
                "Run: .venv/bin/python scripts/fetch_jj_bastis.py",
                DATA_PATH,
            )
            _store = {"clusters": []}
    return _store


def available() -> bool:
    return bool(_load().get("clusters"))


def _tier(in_zone: int, within: int) -> str:
    if in_zone > 0 or within >= TIER_SUBSTANTIAL:
        return "substantial"
    if within >= TIER_SOME:
        return "some"
    return "none"


def for_zone(zone) -> EquitySignal:
    """Count recorded JJ bastis inside and near one zone.

    `zone` is a zones.Zone — taken whole rather than as loose coordinates so the join
    uses the same bounding box the rest of Model A measures against.
    """
    store = _load()
    clusters = store.get("clusters", [])
    south, west, north, east = zone.bbox

    in_zone = [
        c for c in clusters
        if south <= c["latitude"] <= north and west <= c["longitude"] <= east
    ]

    near: list[tuple[float, dict]] = []
    for c in clusters:
        dy = (c["latitude"] - zone.latitude) * _M_PER_DEG_LAT
        dx = (c["longitude"] - zone.longitude) * _M_PER_DEG_LON
        d = sqrt(dx * dx + dy * dy)
        if d <= PROXIMITY_M:
            near.append((d, c))
    near.sort(key=lambda t: t[0])

    tier = _tier(len(in_zone), len(near))

    caveats = [
        "Housing-type record, not income data — DUSIB's list of J.J. bastis, used as "
        "a proxy for where lower-income households live.",
        store.get(
            "resolution_note",
            "Each basti is published as a single point, not a boundary, so in-zone "
            "counts are a lower bound.",
        ),
        store.get(
            "coverage_note",
            "J.J. bastis only — unauthorised colonies, resettlement colonies and "
            "urban villages are not in this record.",
        ),
    ]
    if store.get("published"):
        caveats.append(
            f"List published {store['published']}; clusters are demolished and "
            f"resettled over time, so the record lags the ground."
        )
    if tier == "none":
        caveats.append(
            "No basti recorded nearby is not evidence that this area is affluent — "
            "other lower-income settlement types are not covered by this record."
        )

    return EquitySignal(
        zone_id=zone.zone_id,
        clusters_in_zone=len(in_zone),
        clusters_within_range=len(near),
        range_m=PROXIMITY_M,
        tier=tier,
        tier_label=TIER_LABEL[tier],
        nearest_name=near[0][1]["name"] if near else None,
        nearest_distance_m=round(near[0][0], 0) if near else None,
        examples=[c["name"] for _, c in near[:3]],
        caveats=caveats,
        source=store.get("source", ""),
        published=store.get("published"),
    )


def balance_check(signals: list[EquitySignal]) -> dict:
    """Is a set of zones drawn from across the housing-type spectrum, or one end?

    Framed as a transparency check on the ranking, never as a correction to it. The
    verdict describes the *selection*, not the zones — a skew here is a prompt to look
    again at what the priority score is rewarding, not a claim that any zone is
    wrongly ranked.
    """
    if not signals:
        return {
            "zone_count": 0,
            "counts": {"substantial": 0, "some": 0, "none": 0},
            "verdict": "no_zones",
            "summary": "No zones selected.",
            "proxy_label": PROXY_LABEL,
        }

    counts = {t: sum(1 for s in signals if s.tier == t) for t in TIER_LABEL}
    n = len(signals)
    with_settlement = counts["substantial"] + counts["some"]

    if n == 1:
        verdict = "single_zone"
        summary = (
            "One zone selected — too few to say anything about balance across the "
            "housing-type spectrum."
        )
    elif with_settlement == 0:
        verdict = "skewed_away"
        summary = (
            f"None of these {n} zones has a recorded J.J. basti within "
            f"{PROXIMITY_LABEL}. This selection is drawn entirely from areas "
            f"the housing record does not flag as informal settlement."
        )
    elif with_settlement == n:
        verdict = "skewed_toward"
        summary = (
            f"All {n} of these zones have recorded informal settlement nearby. "
            f"The selection covers lower-income areas but no higher-income comparison."
        )
    else:
        without = n - with_settlement
        summary = (
            f"{with_settlement} of {n} zones have recorded informal settlement "
            f"nearby, {without} {'does' if without == 1 else 'do'} not — the "
            f"selection spans both ends of the housing record."
        )
        verdict = "mixed"

    return {
        "zone_count": n,
        "counts": counts,
        "verdict": verdict,
        "summary": summary,
        "proxy_label": PROXY_LABEL,
        "range_m": PROXIMITY_M,
    }


def ranking_check(ranked: list[EquitySignal], top_n: int = 5) -> dict:
    """Does the priority ranking concentrate on one end of the housing record?

    Takes the zones in priority order and compares the top of the ranking against the
    rest. This is the question the equity signal exists to answer: not "is this zone
    poor" but "is the model sending planting only to one kind of neighbourhood".

    Reported, never applied. The priority score is unchanged by anything here.
    """
    if len(ranked) < 2:
        return {
            "applicable": False,
            "summary": "Too few zones to check the ranking for skew.",
            "proxy_label": PROXY_LABEL,
        }

    top_n = min(top_n, len(ranked) - 1)
    top, rest = ranked[:top_n], ranked[top_n:]
    top_with = sum(1 for s in top if s.tier != "none")
    rest_with = sum(1 for s in rest if s.tier != "none")

    top_share = top_with / len(top)
    rest_share = rest_with / len(rest)

    if top_share > rest_share:
        verdict = "favours_lower_income"
        reading = (
            "The ranking is weighted toward areas with recorded informal settlement."
        )
    elif top_share < rest_share:
        verdict = "favours_higher_income"
        reading = (
            "The ranking is weighted away from areas with recorded informal "
            "settlement — worth checking what the priority score is rewarding."
        )
    else:
        verdict = "even"
        reading = (
            "The ranking is not concentrated at either end of the housing record."
        )

    return {
        "applicable": True,
        "top_n": len(top),
        "top_with_settlement": top_with,
        "rest_count": len(rest),
        "rest_with_settlement": rest_with,
        "verdict": verdict,
        "summary": (
            f"{top_with} of the {len(top)} highest-priority zones have recorded "
            f"informal settlement within {PROXIMITY_LABEL}, against "
            f"{rest_with} of the remaining {len(rest)}. {reading}"
        ),
        "note": (
            "A transparency check on the ranking, not an input to it — the priority "
            "score is computed without this signal."
        ),
        "proxy_label": PROXY_LABEL,
        "range_m": PROXIMITY_M,
    }


def dataset_note() -> dict:
    """Provenance for the health endpoint and the UI's sources list."""
    store = _load()
    return {
        "source": store.get("source", ""),
        "source_url": store.get("source_url", ""),
        "published": store.get("published"),
        "cluster_count": store.get("cluster_count", 0),
        "unparsed_serials": store.get("unparsed_serials", []),
        "proxy_label": PROXY_LABEL,
    }
