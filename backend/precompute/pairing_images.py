"""Generate the crossbreed pairing images, composited onto each zone's real photo.

WHAT MAKES THESE DIFFERENT FROM STOCK IMAGERY

    base image   = the zone's actual Street View photograph, already fetched
    references   = open-licence GBIF photographs of both parent species
    output       = that same street, with the proposed planting inserted into it

The base image is the point. A generated picture of a shrub against a neutral
background says nothing about whether the planting suits R K Puram Sector 12; a
picture of R K Puram Sector 12 *with the shrub in it* is the claim the tool is
actually making. So the Street View photo is passed as the image being edited, and
the prompt instructs an insertion that matches the scene's own lighting, perspective
and scale rather than a replacement of it.

GROUNDING. The GBIF reference photographs are passed as additional input images, not
merely described. Several of these taxa are uncommon cultivars the model has no
reliable prior for, and letting it guess what 'Kaner Petite Pink' looks like would
make the image a decoration rather than a depiction. Where a cultivar has no GBIF
record of its own, the store already records that its reference is the parent
species, and the prompt says so.

COST AND FAILURE. Generation is the only paid step in this project, billed per image
across 128 pairings, so nothing here runs implicitly: the caller asks for it, and
`estimate()` prices the batch first. Each generation is independent — one content
block or API error marks that pairing `failed` with the reason and the batch
continues, because a partially generated set is useful and a half-written store is
not. `failed` is deliberately distinct from `pending`: attempted-and-failed and
not-yet-attempted are different operational states and the UI shows them differently.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import httpx

from ..app.config import (
    CACHE_DIR,
    GENERATED_IMAGES_DIR,
    OPENAI_API_KEY,
    USER_AGENT,
)
from ..app.sources import streetview
from ..models import model_b_genomic as model_b

log = logging.getLogger(__name__)

EDITS_URL = "https://api.openai.com/v1/images/edits"
MODEL = "gpt-image-1"

# Landscape, to match a streetscape and the 16:10 slot the modal renders into.
SIZE = "1536x1024"

# Published gpt-image-1 per-image output prices at SIZE. Input image tokens are billed
# on top and are small relative to these, so an estimate built from them is a floor,
# not a ceiling — `estimate()` says so rather than implying an exact invoice.
COST_PER_IMAGE = {"low": 0.016, "medium": 0.063, "high": 0.25}
SECONDS_PER_IMAGE = {"low": 12, "medium": 20, "high": 35}

# One reference photograph per parent. More would ground the result better but each
# input image is billed, and two references already fix the two things the model
# actually gets wrong unprompted: leaf form and flower colour.
REFERENCES_PER_PARENT = 1

REFERENCE_CACHE = CACHE_DIR / "gbif_reference_photos"

# iNaturalist serves size variants at the same path, and `original` is the wrong one
# to ask for. Measured on the first run of this batch: originals averaged 2.3 MB and
# ran to 1536x2048, against a 56 KB Street View base — the reference photographs were
# 40x the size of the photograph they exist to support, and input image tokens are
# billed per call. That pushed actual cost to ~$0.096/image against an estimated
# $0.063. `medium` is around 500 px on the long edge, which is ample for conveying
# leaf form and flower colour to the model.
#
# Scoped deliberately: only the iNaturalist open-data bucket publishes these variants.
# Other GBIF publishers (museum CDNs, editedimages) serve opaque keys with no size
# suffix, so their URLs are passed through untouched rather than guessed at.
_INAT_HOST = "inaturalist-open-data.s3.amazonaws.com"
_INAT_ORIGINAL = "/original.jpg"
_INAT_VARIANT = "/medium.jpg"

REQUEST_TIMEOUT = 300.0


@dataclass
class GenerationResult:
    """Outcome for one pairing. `failed` always carries a reason."""
    pair_id: str
    zone_id: str
    status: str                 # "ready" | "failed" | "skipped"
    path: str | None = None     # public URL path, e.g. /media/pairings/<zone>/<pair>.png
    prompt: str | None = None
    detail: str | None = None
    seconds: float = 0.0


# --------------------------------------------------------------------- prompt
def _visual_brief(species_id: str, references: dict[str, list[dict]]) -> str:
    """One clause describing a parent, from catalogue traits plus its grounding."""
    s = model_b.CATALOG_BY_ID[species_id]
    bits = [
        f"{s.display_name} ({s.scientific_name})",
        f"a {s.habit.value.replace('_', ' ')}",
        f"about {s.mature_height_m:.0f} m tall with a {s.canopy_spread_m:.0f} m spread",
    ]
    refs = references.get(species_id) or []
    if refs and refs[0].get("is_parent_species"):
        bits.append(
            f"a cultivar with no photographic record of its own — the reference "
            f"photograph shows the parent species {refs[0]['grounded_on']}"
        )
    elif not refs:
        bits.append("no reference photograph available for this parent")
    return ", ".join(bits)


_PLACEMENT_INSTRUCTION = {
    "ground": (
        "Plant it in the ground, in open unpaved soil at the roadside or verge where "
        "such soil actually exists in this photograph."
    ),
    "potted": (
        "Place it in large street planters standing on the existing paved surface. Do "
        "not dig up or replace the paving."
    ),
    "wall_hanging": (
        "Train it against an existing building facade as a green wall or hanging "
        "planters fixed to the wall. Do not alter the building's structure."
    ),
}


def build_prompt(pairing: dict, placement: str, zone_name: str,
                 references: dict[str, list[dict]]) -> str:
    """The generation instruction for one pairing in one zone."""
    a_id, b_id = pairing["species_a"]["id"], pairing["species_b"]["id"]
    a_name = pairing["species_a"]["name"]
    b_name = pairing["species_b"]["name"]

    placement_line = _PLACEMENT_INSTRUCTION.get(
        placement, _PLACEMENT_INSTRUCTION["ground"]
    )

    return (
        "Edit the first image, which is a real street-level photograph of "
        f"{zone_name} in Delhi. Keep it as the same photograph: do not change the "
        "buildings, road, vehicles, sky, weather, time of day or camera position.\n\n"
        "Insert into this scene a planting of a hypothetical hybrid shrub or tree "
        f"that crosses two parent plants:\n"
        f"  Parent A — {_visual_brief(a_id, references)}.\n"
        f"  Parent B — {_visual_brief(b_id, references)}.\n\n"
        "The remaining reference photographs show these two parents. Give the hybrid "
        "a plausible blend of their leaf shape, growth form and flower colour.\n\n"
        f"{placement_line}\n\n"
        "The result must read as this exact street with the planting added to it. "
        "Match the existing photograph's lighting direction, shadow length, haze, "
        "colour grade, perspective and scale. Mature planting at realistic size for "
        "the space available — do not fill the frame with foliage, do not turn the "
        "street into a park, and do not add people, signage or text. Photographic, "
        "not illustrated."
    )


# ----------------------------------------------------------------- references
def reference_url(url: str) -> str:
    """Prefer a size variant over the full-resolution original, where one exists."""
    if _INAT_HOST in url and url.endswith(_INAT_ORIGINAL):
        return url[: -len(_INAT_ORIGINAL)] + _INAT_VARIANT
    return url


def _reference_photo(species_id: str, references: dict[str, list[dict]]) -> Path | None:
    """Download (once) a GBIF reference photograph for a species.

    Cached on disk because the batch re-uses the same handful of species across all
    128 pairings; re-fetching per pairing would hammer iNaturalist for no benefit.
    """
    refs = (references.get(species_id) or [])[:REFERENCES_PER_PARENT]
    if not refs:
        return None

    REFERENCE_CACHE.mkdir(parents=True, exist_ok=True)
    out = REFERENCE_CACHE / f"{species_id}.jpg"
    if out.exists() and out.stat().st_size > 0:
        return out

    url = reference_url(refs[0]["url"])
    try:
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": USER_AGENT})
        if resp.status_code != 200 or not resp.content:
            raise RuntimeError(f"HTTP {resp.status_code}")
        if not resp.headers.get("content-type", "").startswith("image/"):
            raise RuntimeError(f"content-type {resp.headers.get('content-type')}")
        out.write_bytes(resp.content)
        log.info("cached reference photo for %s (%s)", species_id, refs[0]["publisher"])
        return out
    except (httpx.HTTPError, RuntimeError, OSError) as exc:
        # Not fatal: generation proceeds text-grounded only, and the prompt has
        # already said which parents lack a photograph.
        log.warning("reference photo failed for %s: %s", species_id, exc)
        return None


# ------------------------------------------------------------------ generate
def _zone_photo(zone_id: str) -> Path | None:
    p = streetview.IMAGE_DIR / f"{zone_id}.jpg"
    return p if p.exists() and p.stat().st_size > 0 else None


def output_path(zone_id: str, pair_id: str) -> Path:
    return GENERATED_IMAGES_DIR / zone_id / f"{pair_id}.png"


def public_path(zone_id: str, pair_id: str) -> str:
    return f"/media/pairings/{zone_id}/{pair_id}.png"


def generate_one(
    pairing: dict,
    zone_id: str,
    zone_name: str,
    placement: str,
    references: dict[str, list[dict]],
    quality: str = "medium",
) -> GenerationResult:
    """Generate one pairing image. Never raises — failures come back as a status."""
    pair_id = pairing["pair_id"]
    started = time.time()

    if not OPENAI_API_KEY:
        return GenerationResult(pair_id, zone_id, "failed",
                                detail="OPENAI_API_KEY is not set")

    base = _zone_photo(zone_id)
    if base is None:
        # The whole premise is compositing onto the real photograph. Without it we
        # would be generating a generic plant picture, which the brief rules out.
        return GenerationResult(
            pair_id, zone_id, "failed",
            detail=f"no Street View photograph on disk for {zone_id} — run "
                   f"scripts/fetch_streetview.py first",
        )

    prompt = build_prompt(pairing, placement, zone_name, references)

    files: list[tuple[str, tuple[str, bytes, str]]] = [
        ("image[]", (base.name, base.read_bytes(), "image/jpeg"))
    ]
    for sid in (pairing["species_a"]["id"], pairing["species_b"]["id"]):
        ref = _reference_photo(sid, references)
        if ref is not None:
            files.append(("image[]", (ref.name, ref.read_bytes(), "image/jpeg")))

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.post(
                EDITS_URL,
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                data={
                    "model": MODEL,
                    "prompt": prompt,
                    "size": SIZE,
                    "quality": quality,
                    "n": "1",
                },
                files=files,
            )
    except httpx.HTTPError as exc:
        return GenerationResult(pair_id, zone_id, "failed", prompt=prompt,
                                detail=f"request failed: {exc}",
                                seconds=time.time() - started)

    if resp.status_code != 200:
        # Surface the API's own message — content-policy blocks and quota errors are
        # different problems and the log has to distinguish them.
        detail = f"HTTP {resp.status_code}"
        try:
            err = resp.json().get("error", {})
            detail = f"{detail}: {err.get('code') or ''} {err.get('message') or ''}".strip()
        except (json.JSONDecodeError, ValueError):
            detail = f"{detail}: {resp.text[:200]}"
        return GenerationResult(pair_id, zone_id, "failed", prompt=prompt,
                                detail=detail, seconds=time.time() - started)

    try:
        payload = resp.json()
        b64 = payload["data"][0]["b64_json"]
        blob = base64.b64decode(b64)
    except (json.JSONDecodeError, KeyError, IndexError, ValueError) as exc:
        return GenerationResult(pair_id, zone_id, "failed", prompt=prompt,
                                detail=f"unreadable response: {exc}",
                                seconds=time.time() - started)

    out = output_path(zone_id, pair_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_bytes(blob)
    tmp.replace(out)

    return GenerationResult(
        pair_id, zone_id, "ready",
        path=public_path(zone_id, pair_id),
        prompt=prompt,
        seconds=time.time() - started,
    )


# --------------------------------------------------------------------- batch
def iter_pairings(store: dict) -> Iterator[tuple[dict, dict]]:
    """Every (zone, pairing) in the store, both tiers."""
    for zone in store.get("zones", {}).values():
        for pairing in zone.get("ambitious", []) + zone.get("safer", []):
            yield zone, pairing


def pending_pairings(store: dict, force: bool = False) -> list[tuple[dict, dict]]:
    """Pairings still needing generation.

    A pairing already marked `ready` whose file is missing from disk counts as
    outstanding — the store and the filesystem disagreeing is exactly the case where
    silently trusting the store would leave a broken image in the demo.
    """
    out = []
    for zone, pairing in iter_pairings(store):
        if force:
            out.append((zone, pairing))
            continue
        image = pairing.get("image") or {}
        on_disk = output_path(zone["zone_id"], pairing["pair_id"]).exists()
        if image.get("status") == "ready" and on_disk:
            continue
        out.append((zone, pairing))
    return out


def estimate(count: int, quality: str) -> dict[str, Any]:
    """Price and time a batch before any of it is spent."""
    per = COST_PER_IMAGE[quality]
    secs = SECONDS_PER_IMAGE[quality]
    return {
        "count": count,
        "quality": quality,
        "size": SIZE,
        "cost_per_image_usd": per,
        "total_cost_usd": round(count * per, 2),
        "seconds_per_image": secs,
        "total_minutes": round(count * secs / 60, 1),
        "note": (
            "Output-image pricing only. Each call also sends the Street View "
            "photograph and up to two reference photographs, billed as input image "
            "tokens on top of this, so treat the total as a floor."
        ),
    }


def apply_result(store: dict, result: GenerationResult) -> None:
    """Write one outcome back into the store in place."""
    for zone, pairing in iter_pairings(store):
        if zone["zone_id"] == result.zone_id and pairing["pair_id"] == result.pair_id:
            pairing["image"] = {
                "status": result.status,
                "path": result.path,
                "prompt": result.prompt,
                "detail": result.detail,
                "generated_at": time.time() if result.status == "ready" else None,
            }
            return


def recount(store: dict) -> dict[str, int]:
    """Refresh the store's image counters from the pairings themselves."""
    counts = {"ready": 0, "failed": 0, "pending": 0}
    for _zone, pairing in iter_pairings(store):
        status = (pairing.get("image") or {}).get("status", "pending")
        counts[status] = counts.get(status, 0) + 1
    store["images_generated"] = counts["ready"]
    store["images_failed"] = counts["failed"]
    return counts
