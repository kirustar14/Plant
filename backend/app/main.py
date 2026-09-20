"""FastAPI application — the boundary between the models and the frontend.

Responsibilities are deliberately thin: this layer fetches, serialises, and caches.
It contains no scoring logic. Model A decides priority and placement, Model B decides
species and compatibility, and neither is reimplemented here.

Every response carries provenance. A number without a source is a bug, so zone
payloads include `sources` and `data_gaps`, and the health endpoint reports which
upstreams are actually reachable.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config
from .schemas import ZoneProfile
from .sources import equity, gee, groundwater, streetview
from .sources.http_cache import UpstreamError
from .zones import ZONES, ZONES_BY_ID
from ..models import cost_estimate, impact_projection
from ..models import model_a_zone_scoring as model_a
from ..models import model_b_genomic as model_b
from ..precompute import crossbreeds

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("plan.api")

# Model A over 10 zones means 10 Overpass queries, each pulling every road and
# building in a 600m box — slow on a cold cache. Computed once at startup and held,
# so the UI is instant and a demo never waits on Overpass.
_state: dict[str, Any] = {
    "profiles": [],
    "scores": [],
    "baseline": {},
    "recommendations": {},
    "impacts": {},
    "street_view": {},
    "crossbreeds": None,   # the precomputed store; None means live fallback
    "ready": False,
    "error": None,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = config.missing_keys()
    if missing:
        log.warning("Missing API keys: %s", ", ".join(missing))
    # Load the precomputed crossbreed store first — the popup depends on it, and a
    # missing store is something the operator must know about before a demo, not
    # after someone drags two species together on stage.
    store = crossbreeds.load()
    if store is None:
        log.warning(
            "No precomputed crossbreed store at %s — pairings will be computed LIVE, "
            "which is slow and depends on NCBI/TimeTree/GBIF being up. "
            "Run: .venv/bin/python scripts/precompute_crossbreeds.py",
            crossbreeds.STORE_PATH,
        )
    else:
        _state["crossbreeds"] = store
        log.info(
            "Loaded %d precomputed pairings across %d zones (%d with images)",
            store.get("pairing_count", 0),
            store.get("zone_count", 0),
            store.get("images_generated", 0),
        )

    log.info("Warming zone data (Model A over %d zones)...", len(ZONES))
    try:
        _refresh()
        log.info("Ready.")
    except Exception as exc:
        # Never let a cold upstream prevent the server from starting; /api/health
        # reports the failure and the UI can show it rather than hanging.
        _state["error"] = str(exc)
        log.error("Warm-up failed: %s", exc)
    yield


app = FastAPI(title="VeggieDelhi API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # Vite falls forward to 5174 when 5173 is already taken, so both are allowed.
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Street View photographs are served unmodified so the Google attribution burnt
# into each image stays intact, as the API terms require.
app.mount(
    "/media/streetview",
    StaticFiles(directory=streetview.IMAGE_DIR),
    name="streetview",
)

# Generated pairing images, same mechanism and therefore the same guarantee: the
# popup reads a file off disk, with no outbound call at demo time.
app.mount(
    "/media/pairings",
    StaticFiles(directory=config.GENERATED_IMAGES_DIR),
    name="pairings",
)


def _refresh() -> None:
    profiles, scores, baseline = model_a.score_all_zones()
    _state.update(
        profiles=profiles,
        scores=scores,
        baseline=baseline,
        recommendations={},
        impacts={},
        street_view={},
        ready=True,
        error=None,
    )


def _profile(zone_id: str) -> ZoneProfile:
    for p in _state["profiles"]:
        if p.zone_id == zone_id:
            return p
    raise HTTPException(404, f"unknown zone: {zone_id}")


def _score(zone_id: str):
    for s in _state["scores"]:
        if s.zone_id == zone_id:
            return s
    raise HTTPException(404, f"no score for zone: {zone_id}")


def _serialise_species(scored) -> dict[str, Any]:
    s = scored.species
    return {
        "species_id": s.species_id,
        "scientific_name": s.scientific_name,
        "common_name": s.common_name,
        "display_name": s.display_name,
        "family": s.family,
        "habit": s.habit.value,
        "is_cultivar": s.is_cultivar,
        "cultivar_epithet": s.cultivar_epithet,
        "fit_score": scored.fit_score,
        "key_qualities": scored.key_qualities,
        "concerns": scored.concerns,
        "apti": s.apti,
        "apti_band": s.apti_band,
        "mature_height_m": s.mature_height_m,
        "co2_sequestration_kg_yr": s.co2_sequestration_kg_yr,
        "evidence": s.evidence,
    }


def _serialise_pairing(r) -> dict[str, Any]:
    a = model_b.CATALOG_BY_ID[r.species_a_id]
    b = model_b.CATALOG_BY_ID[r.species_b_id]
    return {
        "pair_id": f"{r.species_a_id}__{r.species_b_id}",
        "species_a": {"id": a.species_id, "name": a.display_name,
                      "scientific_name": a.scientific_name},
        "species_b": {"id": b.species_id, "name": b.display_name,
                      "scientific_name": b.scientific_name},
        "confidence": r.confidence,
        "confidence_low": r.confidence_low,
        "confidence_high": r.confidence_high,
        "band": r.band,
        "explanation": r.explanation,
        "terms": {
            "crossability": r.crossability,
            "hybrid_evidence": r.hybrid_evidence_strength,
            "parental_merit": r.parental_merit,
            "complementarity": r.complementarity,
            "ploidy_factor": r.ploidy_factor,
        },
        "divergence_mya": r.divergence_mya,
        "divergence_ci": r.divergence_ci,
        "divergence_studies": r.divergence_studies,
        "is_conspecific": r.is_conspecific,
        "crossability_from_rank": r.crossability_from_rank,
        "citations": r.citations,
        "data_gaps": r.data_gaps,
    }


def _serialise_zone(profile: ZoneProfile, score, detail: bool = False) -> dict[str, Any]:
    zone = ZONES_BY_ID[profile.zone_id]
    payload: dict[str, Any] = {
        "zone_id": profile.zone_id,
        "name": profile.name,
        "locality": zone.locality,
        "latitude": profile.latitude,
        "longitude": profile.longitude,
        "priority_score": score.priority_score,
        "priority_band": score.priority_band,
        "placement": score.placement.value,
        "placement_rationale": score.placement_rationale,
        "confidence": score.confidence,
        "measurements": {
            "aqi": profile.aqi,
            "aqi_attribution": profile.aqi_attribution,
            "pm25_ugm3": profile.pm25_ugm3,
            "traffic_proxy": profile.traffic_proxy,
            "road_density_km_per_km2": profile.road_density_km_per_km2,
            "floor_area_ratio": profile.floor_area_ratio,
            "building_footprint_ratio": profile.building_footprint_ratio,
            "road_surface_ratio": profile.road_surface_ratio,
            "plantable_ground_ratio": profile.plantable_ground_ratio,
            "ndvi": profile.ndvi,
            "land_surface_temp_c": profile.land_surface_temp_c,
            "heat_island_delta_c": profile.heat_island_delta_c,
        },
        "vegetation": {
            "has_existing_vegetation": profile.has_existing_vegetation,
            "label": (
                "vegetation signal unavailable" if profile.has_existing_vegetation is None
                else "has existing vegetation" if profile.has_existing_vegetation
                else "bare"
            ),
            "ndvi_threshold": gee.NDVI_VEGETATED_THRESHOLD,
        },
        "signals": score.signals,
        "missing_signals": score.missing_signals,
        "sources": profile.sources,
        "data_gaps": profile.data_gaps,
        "selection_rationale": zone.selection_rationale,
    }

    # Impact ships with every zone in the list response so the top bar can update
    # instantly on selection — summing client-side, with no request per click.
    photo = _street_view(profile)
    payload["street_view"] = {
        "available": photo.available,
        "status": photo.status,
        "path": photo.path,
        "captured": photo.captured,
        "attribution": photo.attribution,
        "heading": photo.heading,
        "pano_distance_m": photo.pano_distance_m,
        "detail": photo.detail,
    }

    impact = _zone_impact(profile)
    payload["projected_impact"] = (
        {
            "plant_count": impact.plant_count,
            "species_id": impact.species_id,
            "species_name": impact.species_name,
            "co2_kg_yr": impact.co2_kg_yr,
            "co2_tonnes_yr": impact.co2_tonnes_yr,
            "pm_deposition_kg_yr": impact.pm_deposition_kg_yr,
            "cooling_kwh_yr": impact.cooling_kwh_yr,
            "cars_equivalent": impact.cars_equivalent,
            "maturity_years": impact.maturity_years,
            "capacity_basis": impact.capacity_basis,
            "caveats": impact.caveats,
        }
        if impact
        else None
    )

    # Cost rides with the impact block because it is the same projection expressed in
    # rupees — it multiplies the very plant count shown above, and is disclosed the
    # same way rather than in a more confident-looking style of its own.
    cost = cost_estimate.estimate_zone(
        profile.zone_id, impact.plant_count if impact else 0, score.placement
    )
    payload["implementation_cost"] = {
        "plant_count": cost.plant_count,
        "per_sapling_inr": cost.per_sapling_inr,
        "total_inr": cost.total_inr,
        "field_cost_inr": cost.field_cost_inr,
        "admin_contingency_inr": cost.admin_contingency_inr,
        "maintenance_years": cost.maintenance_years,
        "basis_matches_placement": cost.basis_matches_placement,
        "basis": cost.basis,
        "source": cost.source,
        "caveats": cost.caveats,
    }

    # Block-level, not zone-level. The payload carries the tehsil name and resolution
    # note so the UI cannot render this as a zone-specific reading.
    water = groundwater.for_zone(profile.zone_id)
    payload["water"] = {
        "available": water.available,
        "tehsil": water.tehsil,
        "district": water.district,
        "category": water.category,
        "category_label": water.category_label,
        "severity": water.severity,
        "match": water.match,
        "implication": water.implication,
        "resolution_note": water.resolution_note,
        "source": water.source,
        "assessment_year": water.assessment_year,
        "caveats": water.caveats,
    }

    # A housing-type proxy, never an income figure. `proxy_label` travels with it so
    # the qualifier is available everywhere the signal is rendered.
    eq = equity.for_zone(zone)
    payload["equity"] = {
        "clusters_in_zone": eq.clusters_in_zone,
        "clusters_within_range": eq.clusters_within_range,
        "range_m": eq.range_m,
        "tier": eq.tier,
        "tier_label": eq.tier_label,
        "nearest_name": eq.nearest_name,
        "nearest_distance_m": eq.nearest_distance_m,
        "examples": eq.examples,
        "proxy_label": eq.proxy_label,
        "source": eq.source,
        "published": eq.published,
        "caveats": eq.caveats,
    }

    if detail:
        payload["species"] = [
            _serialise_species(s) for s in _recommendation(profile).shortlist
        ]
    return payload


def _recommendation(profile: ZoneProfile):
    """Model B output for a zone, computed once then held."""
    cached = _state["recommendations"].get(profile.zone_id)
    if cached is None:
        cached = model_b.recommend_for_zone(profile)
        _state["recommendations"][profile.zone_id] = cached
    return cached


def _top_species(profile: ZoneProfile):
    """The zone's highest-ranked species, preferring the precomputed shortlist.

    Falls back to running Model B only when the store is absent, so a normal
    startup never pays for scoring it a second time.
    """
    store = _state["crossbreeds"]
    if store is not None:
        zone = store.get("zones", {}).get(profile.zone_id)
        if zone and zone.get("shortlist"):
            return model_b.CATALOG_BY_ID[zone["shortlist"][0]["species_id"]]
    shortlist = _recommendation(profile).shortlist
    return shortlist[0].species if shortlist else None


def _street_view(profile: ZoneProfile):
    """Before photograph for a zone, fetched once then held.

    Cached in process as well as on disk so a REQUEST_DENIED does not retry per
    request while the API is being enabled.
    """
    cached = _state["street_view"].get(profile.zone_id)
    if cached is None:
        cached = streetview.fetch_photo(
            profile.zone_id, profile.latitude, profile.longitude
        )
        _state["street_view"][profile.zone_id] = cached
    return cached


def _zone_impact(profile: ZoneProfile):
    """Projected impact for a zone, computed once then held."""
    cached = _state["impacts"].get(profile.zone_id)
    if cached is not None:
        return cached

    species = _top_species(profile)
    if species is None:
        return None

    zone = ZONES_BY_ID[profile.zone_id]
    area_m2 = float(zone.size_m) ** 2
    impact = impact_projection.project_zone(profile, species, area_m2)
    _state["impacts"][profile.zone_id] = impact
    return impact


# ---------------------------------------------------------------- endpoints
@app.get("/api/health")
def health() -> dict[str, Any]:
    """What is actually reachable right now. Used by the UI to show honest state."""
    store = _state["crossbreeds"]
    return {
        "ready": _state["ready"],
        "error": _state["error"],
        "zones_scored": len(_state["scores"]),
        "zones_total": len(ZONES),
        "missing_api_keys": config.missing_keys(),
        "earth_engine_available": gee.initialize(),
        "air_quality_available": bool(_state["baseline"]),
        # The popup's demo-reliability guarantee: true means every drag is a
        # dictionary lookup with no outbound call.
        "crossbreeds_precomputed": store is not None,
        "precomputed_pairings": store.get("pairing_count", 0) if store else 0,
        "precomputed_images": store.get("images_generated", 0) if store else 0,
        # Static published datasets rather than live upstreams, but the operator still
        # needs to know whether they loaded before demoing the signals that use them.
        "groundwater_dataset": groundwater.dataset_note(),
        "equity_dataset": equity.dataset_note(),
        "cost_basis": cost_estimate.SOURCE,
    }


@app.get("/api/city/delhi")
def city() -> dict[str, Any]:
    """City-wide baseline for the dashboard top bar."""
    if not _state["ready"]:
        raise HTTPException(503, _state["error"] or "still warming up")
    baseline = dict(_state["baseline"])
    scores = _state["scores"]
    baseline["zone_count"] = len(scores)
    baseline["mean_priority"] = (
        round(sum(s.priority_score for s in scores) / len(scores), 4) if scores else None
    )
    return baseline


@app.get("/api/zones")
def zones() -> dict[str, Any]:
    """All zones with Model A outputs, priority-ranked."""
    if not _state["ready"]:
        raise HTTPException(503, _state["error"] or "still warming up")
    by_id = {p.zone_id: p for p in _state["profiles"]}
    return {
        "zones": [
            _serialise_zone(by_id[s.zone_id], s) for s in _state["scores"]
        ],
        # A transparency check on the ranking as a whole, not a correction to it: does
        # the priority score push zones from across the housing-type spectrum to the
        # top, or only one end of it? Computed over every zone so the statement is
        # about the model's ranking rather than about whatever the user has clicked.
        "equity_check": equity.ranking_check(
            [equity.for_zone(ZONES_BY_ID[s.zone_id]) for s in _state["scores"]]
        ),
    }


@app.get("/api/zones/{zone_id}")
def zone_detail(zone_id: str) -> dict[str, Any]:
    """One zone including its Model B species shortlist."""
    if not _state["ready"]:
        raise HTTPException(503, _state["error"] or "still warming up")
    return _serialise_zone(_profile(zone_id), _score(zone_id), detail=True)


AMBITIOUS_NOTE = (
    "Five genetically distinct species. Most pairings between them are "
    "biologically unlikely, and the scores say so."
)
SAFER_NOTE = (
    "Cultivars of one species. No reproductive barrier, so these cross "
    "reliably — but they add less that neither parent already has."
)


@app.get("/api/zones/{zone_id}/pairings")
def zone_pairings(zone_id: str) -> dict[str, Any]:
    """Both pairing tiers for a zone — served from the precomputed store.

    This endpoint backs the drag-and-drop popup, so it must be a lookup. When the
    precomputed store is present every response is a dictionary read with no
    outbound calls. The live path exists only as a fallback and marks itself
    `"source": "live"` so a degraded demo is visible rather than merely slow.

    `ambitious` is the 10 pairings among five distinct species, scored honestly.
    `safer` is within-species cultivar pairs. Same scorer produced both.
    """
    store = _state["crossbreeds"]
    if store is not None:
        zone = store.get("zones", {}).get(zone_id)
        if zone is None:
            raise HTTPException(
                404,
                f"zone {zone_id} is not in the precomputed store — regenerate it with "
                f"scripts/precompute_crossbreeds.py",
            )
        return {
            "zone_id": zone_id,
            "source": "precomputed",
            "generated_at": store.get("generated_at"),
            "ambitious": {
                "label": "Novel crosses — distinct species",
                "note": AMBITIOUS_NOTE,
                "pairings": zone["ambitious"],
            },
            "safer": {
                "label": "Higher-confidence alternatives — same-species cultivars",
                "note": SAFER_NOTE,
                "pairings": zone["safer"],
            },
        }

    # Fallback: no store on disk. Correct, but slow and network-dependent.
    log.warning("serving pairings for %s LIVE — precomputed store missing", zone_id)
    if not _state["ready"]:
        raise HTTPException(503, _state["error"] or "still warming up")
    rec = _recommendation(_profile(zone_id))
    return {
        "zone_id": zone_id,
        "source": "live",
        "generated_at": None,
        "ambitious": {
            "label": "Novel crosses — distinct species",
            "note": AMBITIOUS_NOTE,
            "pairings": [_serialise_pairing(r) for r in rec.novel_pairings],
        },
        "safer": {
            "label": "Higher-confidence alternatives — same-species cultivars",
            "note": SAFER_NOTE,
            "pairings": [_serialise_pairing(r) for r in rec.safer_pairings],
        },
    }


@app.get("/api/species")
def species_catalog() -> dict[str, Any]:
    """The full trait catalogue, for transparency about what the model drew on."""
    return {
        "species": [
            {
                "species_id": s.species_id,
                "scientific_name": s.scientific_name,
                "common_name": s.common_name,
                "display_name": s.display_name,
                "family": s.family,
                "habit": s.habit.value,
                "is_cultivar": s.is_cultivar,
                "placements": [p.value for p in s.placements],
                "apti": s.apti,
                "apti_band": s.apti_band,
                "chromosome_2n": s.chromosome_2n,
                "co2_sequestration_kg_yr": s.co2_sequestration_kg_yr,
                "native_to_region": s.native_to_region,
                "evidence": s.evidence,
                "notes": s.notes,
            }
            for s in model_b.ALL_TAXA
        ]
    }
