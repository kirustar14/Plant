"""Precompute every crossbreed pairing the demo can surface.

WHY THIS EXISTS. The drag interaction in the popup must be an instant lookup of an
already-computed result — never a live API call. Scoring one pairing touches NCBI,
TimeTree and GBIF, which is seconds of latency on a cold cache and a hard dependency
on three third-party services being up at the exact moment someone drags two plants
together on stage. So every pairing for every zone is computed ahead of time and
written to disk, and the API serves that file.

WHAT IS STORED. Both tiers per zone: the ambitious set (10 pairings among five
distinct species) and the safer set (within-species cultivar pairs). Each entry keeps
the confidence, its uncertainty band, the plain-language explanation, every scoring
term, and the citations — so the popup never has to recompute or re-justify anything.

IMAGES. Each pairing carries an `image` block that is currently `{"status":
"pending"}`. Generation is a separate, paid step; the schema is here so adding images
later is a field update rather than a rewrite. GBIF reference photographs ARE fetched
now (keyless, free) and stored per species, because those are what ground the eventual
generation prompt — the brief is explicit that generation must not rely on the model's
own guess at what an uncommon cultivar looks like.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..app.config import STATIC_DIR
from ..app.schemas import ZoneProfile
from ..app.sources import gbif
from ..models import model_a_zone_scoring as model_a
from ..models import model_b_genomic as model_b

log = logging.getLogger(__name__)

STORE_PATH = STATIC_DIR / "crossbreeds.json"
SCHEMA_VERSION = 1

# Reference photographs per species, used to ground image generation later.
REFERENCE_IMAGES_PER_SPECIES = 3


def _pair_id(a_id: str, b_id: str) -> str:
    """Order-independent identifier so a drag in either direction hits one entry."""
    lo, hi = sorted((a_id, b_id))
    return f"{lo}__{hi}"


def _serialise_pairing(result, zone_id: str) -> dict[str, Any]:
    a = model_b.CATALOG_BY_ID[result.species_a_id]
    b = model_b.CATALOG_BY_ID[result.species_b_id]
    return {
        "pair_id": _pair_id(result.species_a_id, result.species_b_id),
        "zone_id": zone_id,
        "species_a": {
            "id": a.species_id,
            "name": a.display_name,
            "scientific_name": a.scientific_name,
            "is_cultivar": a.is_cultivar,
        },
        "species_b": {
            "id": b.species_id,
            "name": b.display_name,
            "scientific_name": b.scientific_name,
            "is_cultivar": b.is_cultivar,
        },
        "confidence": result.confidence,
        "confidence_low": result.confidence_low,
        "confidence_high": result.confidence_high,
        "band": result.band,
        "explanation": result.explanation,
        "terms": {
            "crossability": result.crossability,
            "hybrid_evidence": result.hybrid_evidence_strength,
            "parental_merit": result.parental_merit,
            "complementarity": result.complementarity,
            "ploidy_factor": result.ploidy_factor,
        },
        "divergence_mya": result.divergence_mya,
        "divergence_ci": list(result.divergence_ci) if result.divergence_ci else None,
        "divergence_studies": result.divergence_studies,
        "is_conspecific": result.is_conspecific,
        "crossability_from_rank": result.crossability_from_rank,
        "citations": result.citations,
        "data_gaps": result.data_gaps,
        # Filled by the (paid, separate) image generation step.
        "image": {"status": "pending", "path": None, "prompt": None, "generated_at": None},
    }


def collect_reference_images(species_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    """Openly licensed GBIF photographs per species, for grounding generation.

    Keyless and free, so this runs now even though generation is deferred. A species
    with no open-licence imagery is recorded as an empty list rather than skipped —
    that is a real gap the generation step needs to know about, because it means the
    prompt for that species would be ungrounded.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for sid in sorted(species_ids):
        species = model_b.CATALOG_BY_ID[sid]
        # Cultivars have no GBIF record of their own; ground them on the parent
        # species and say so, rather than silently returning nothing.
        lookup_name = species.scientific_name
        taxon = gbif.match_taxon(lookup_name)
        if taxon is None:
            log.warning("GBIF: no taxon for %s — generation would be ungrounded", lookup_name)
            out[sid] = []
            continue

        images = gbif.reference_images(taxon.usage_key, limit=REFERENCE_IMAGES_PER_SPECIES)
        out[sid] = [
            {
                "url": im.url,
                "licence": im.licence,
                "publisher": im.publisher,
                "occurrence_key": im.occurrence_key,
                "grounded_on": lookup_name,
                "is_parent_species": species.is_cultivar,
            }
            for im in images
        ]
        if not images:
            log.warning("GBIF: no open-licence imagery for %s", lookup_name)
    return out


def _existing_images(path: Path = STORE_PATH) -> dict[tuple[str, str], dict[str, Any]]:
    """Image blocks from a previous store, keyed by (zone_id, pair_id).

    Rebuilding the scores must not throw away the images. Scoring is free and gets
    re-run whenever the catalogue changes; generation is the only paid step in the
    project, so a rebuild that silently reset 128 pairings to `pending` would quietly
    discard real spend. Files on disk would survive, but the store's references to
    them would not, which is the same thing from the UI's point of view.
    """
    prior = load(path)
    if prior is None:
        return {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for zone in prior.get("zones", {}).values():
        for pairing in zone.get("ambitious", []) + zone.get("safer", []):
            image = pairing.get("image") or {}
            if image.get("status") in ("ready", "failed"):
                out[(zone["zone_id"], pairing["pair_id"])] = image
    return out


def build(profiles: list[ZoneProfile] | None = None) -> dict[str, Any]:
    """Score every pairing for every zone and return the full store.

    Carries forward any images already generated, so re-scoring is free to run.
    """
    carried = _existing_images()
    if carried:
        log.info("carrying forward %d existing image records", len(carried))

    if profiles is None:
        profiles, _, _ = model_a.score_all_zones()

    zones: dict[str, Any] = {}
    species_seen: set[str] = set()
    started = time.time()

    for profile in profiles:
        if profile.placement is None:
            log.warning("skipping %s — no placement (Model A did not score it)", profile.zone_id)
            continue

        rec = model_b.recommend_for_zone(profile)

        shortlist = [
            {
                "species_id": s.species.species_id,
                "name": s.species.display_name,
                "scientific_name": s.species.scientific_name,
                "is_cultivar": s.species.is_cultivar,
                "fit_score": s.fit_score,
                "key_qualities": s.key_qualities,
                "concerns": s.concerns,
            }
            for s in rec.shortlist
        ]
        species_seen.update(s["species_id"] for s in shortlist)

        ambitious = [_serialise_pairing(r, profile.zone_id) for r in rec.novel_pairings]
        safer = [_serialise_pairing(r, profile.zone_id) for r in rec.safer_pairings]
        for entry in ambitious + safer:
            species_seen.add(entry["species_a"]["id"])
            species_seen.add(entry["species_b"]["id"])
            prior_image = carried.get((profile.zone_id, entry["pair_id"]))
            if prior_image is not None:
                entry["image"] = prior_image

        zones[profile.zone_id] = {
            "zone_id": profile.zone_id,
            "name": profile.name,
            "placement": profile.placement.value,
            "shortlist": shortlist,
            "ambitious": ambitious,
            "safer": safer,
        }
        log.info(
            "precomputed %s: %d ambitious + %d safer pairings",
            profile.zone_id, len(ambitious), len(safer),
        )

    log.info("collecting GBIF reference imagery for %d species...", len(species_seen))
    references = collect_reference_images(species_seen)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.time(),
        "build_seconds": round(time.time() - started, 1),
        "zone_count": len(zones),
        "pairing_count": sum(
            len(z["ambitious"]) + len(z["safer"]) for z in zones.values()
        ),
        "images_generated": sum(
            1 for z in zones.values()
            for p in z["ambitious"] + z["safer"]
            if p["image"]["status"] == "ready"
        ),
        "images_failed": sum(
            1 for z in zones.values()
            for p in z["ambitious"] + z["safer"]
            if p["image"]["status"] == "failed"
        ),
        "zones": zones,
        "reference_images": references,
    }


def save(store: dict[str, Any], path: Path = STORE_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2))
    tmp.replace(path)
    return path


def load(path: Path = STORE_PATH) -> dict[str, Any] | None:
    """Read the precomputed store, or None if it is absent or unreadable.

    None is a signal to the API to fall back to live scoring with a loud warning —
    a demo running off live calls should be visibly degraded, not silently so.
    """
    if not path.exists():
        return None
    try:
        store = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.error("precomputed store at %s is unreadable: %s", path, exc)
        return None

    if store.get("schema_version") != SCHEMA_VERSION:
        log.warning(
            "precomputed store schema v%s != expected v%s — regenerate it",
            store.get("schema_version"), SCHEMA_VERSION,
        )
    return store
