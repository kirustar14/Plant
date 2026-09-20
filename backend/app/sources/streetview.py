"""Google Street View Static API — real "before" photographs of each zone.

These are photographs, not renderings. The brief is explicit that the before-image
must be a real photo, so nothing here falls back to a generated or stock picture: a
zone with no imagery reports that it has none, and the UI shows an empty state.

TWO THINGS THAT CONTROL COST AND CORRECTNESS

1. **Metadata first.** `/streetview/metadata` is free and unmetered; `/streetview` is
   billed per request. Every fetch checks metadata first, so a zone with no coverage
   never triggers a billable call, and a misconfigured key surfaces as a clear
   REQUEST_DENIED instead of a bill for broken images.

2. **Heading is computed, not guessed.** The panorama is rarely at the exact zone
   centroid — it sits on the nearest road. Pointing the camera with a default heading
   of 0 (due north) would frame whatever happens to be north of the car. Instead the
   bearing from the panorama's actual position toward the zone centroid is computed,
   so the photograph looks at the place being scored.

Images are cached to disk. Street View terms require the Google attribution that is
burnt into the returned image, so images are stored and served unmodified — never
cropped in a way that would remove it.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import DATA_DIR, GOOGLE_API_KEY, USER_AGENT
from .http_cache import read_cache, write_cache

log = logging.getLogger(__name__)

METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
IMAGE_URL = "https://maps.googleapis.com/maps/api/streetview"

IMAGE_DIR = DATA_DIR / "streetview"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

# 640x400 is the largest free-tier size without the premium "size" surcharge, and
# matches the 16:10 slot in the zone block.
IMAGE_SIZE = "640x400"
FOV = 80          # slightly wide, to show streetscape context rather than one facade
PITCH = 4         # a touch upward: shows canopy space and building height
METADATA_CACHE_AGE = 30 * 86_400


@dataclass(frozen=True)
class StreetViewPhoto:
    """A zone's before photograph, or an explanation of why there isn't one."""
    zone_id: str
    available: bool
    status: str
    path: str | None = None        # public URL path, e.g. /media/streetview/x.jpg
    pano_id: str | None = None
    captured: str | None = None    # YYYY-MM as returned by the API
    copyright: str | None = None
    heading: float | None = None
    pano_distance_m: float | None = None
    detail: str | None = None

    @property
    def attribution(self) -> str:
        return self.copyright or "© Google"


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing from point 1 to point 2, in degrees."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def fetch_metadata(latitude: float, longitude: float, radius_m: int = 120) -> dict:
    """Free availability check. Cached — panorama coverage changes slowly."""
    key = f"{latitude:.5f},{longitude:.5f}|{radius_m}"
    cached = read_cache("streetview_meta", key, max_age_s=METADATA_CACHE_AGE)
    if cached is not None:
        return cached

    if not GOOGLE_API_KEY:
        return {"status": "NO_API_KEY"}

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                METADATA_URL,
                headers={"User-Agent": USER_AGENT},
                params={
                    "location": f"{latitude},{longitude}",
                    "radius": radius_m,
                    "key": GOOGLE_API_KEY,
                },
            )
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Street View metadata failed for %.4f,%.4f: %s", latitude, longitude, exc)
        return {"status": "FETCH_ERROR", "error_message": str(exc)}

    # Only cache definitive answers. Caching REQUEST_DENIED would hide the fix
    # after the API is enabled.
    if payload.get("status") in ("OK", "ZERO_RESULTS"):
        write_cache("streetview_meta", key, payload)
    return payload


def fetch_photo(
    zone_id: str,
    latitude: float,
    longitude: float,
    force: bool = False,
) -> StreetViewPhoto:
    """Download a zone's before photograph, or report why it is unavailable."""
    out_path = IMAGE_DIR / f"{zone_id}.jpg"
    meta = fetch_metadata(latitude, longitude)
    status = meta.get("status", "UNKNOWN")

    if status != "OK":
        detail = {
            "ZERO_RESULTS": "No Street View coverage near this location.",
            "NO_API_KEY": "GOOGLE_API_KEY is not set.",
            "REQUEST_DENIED": (
                "Street View Static API is not enabled on the API project, or the key "
                "is restricted. Enable street-view-image-backend.googleapis.com."
            ),
            "OVER_QUERY_LIMIT": "Street View quota exhausted.",
        }.get(status, meta.get("error_message") or f"Street View returned {status}.")
        log.warning("Street View unavailable for %s: %s (%s)", zone_id, status, detail)
        return StreetViewPhoto(zone_id=zone_id, available=False, status=status, detail=detail)

    pano_id = meta.get("pano_id")
    pano_loc = meta.get("location") or {}
    pano_lat, pano_lng = pano_loc.get("lat"), pano_loc.get("lng")
    heading = (
        _bearing(pano_lat, pano_lng, latitude, longitude)
        if pano_lat is not None and pano_lng is not None
        else 0.0
    )
    distance = (
        _distance_m(pano_lat, pano_lng, latitude, longitude)
        if pano_lat is not None
        else None
    )

    # Reuse the cached file unless explicitly refreshed — the billable call is here.
    if out_path.exists() and not force:
        return StreetViewPhoto(
            zone_id=zone_id,
            available=True,
            status="OK",
            path=f"/media/streetview/{out_path.name}",
            pano_id=meta.get("pano_id"),
            captured=meta.get("date"),
            copyright=meta.get("copyright"),
            heading=round(heading, 1),
            pano_distance_m=round(distance, 1) if distance is not None else None,
        )

    # Request by pano id, not by location. The image endpoint runs its own panorama
    # search with a *smaller* default radius than the 120 m used for metadata, so a
    # location request can resolve to a different panorama than the one the heading
    # was computed from — the photo then points somewhere the bearing never meant.
    # Observed live at R K Puram: metadata found a pano 61 m out, the image endpoint
    # returned a different one. Pinning the id keeps image and heading in agreement.
    locator = (
        {"pano": pano_id} if pano_id else {"location": f"{latitude},{longitude}"}
    )
    try:
        with httpx.Client(timeout=45.0) as client:
            resp = client.get(
                IMAGE_URL,
                headers={"User-Agent": USER_AGENT},
                params={
                    "size": IMAGE_SIZE,
                    **locator,
                    "heading": f"{heading:.1f}",
                    "fov": FOV,
                    "pitch": PITCH,
                    "return_error_code": "true",
                    "key": GOOGLE_API_KEY,
                },
            )
        if resp.status_code != 200 or not resp.content:
            raise RuntimeError(f"HTTP {resp.status_code}")
        # Guard against an error page being written out as a .jpg.
        if not resp.headers.get("content-type", "").startswith("image/"):
            raise RuntimeError(f"unexpected content-type {resp.headers.get('content-type')}")
        out_path.write_bytes(resp.content)
    except (httpx.HTTPError, RuntimeError, OSError) as exc:
        log.warning("Street View image failed for %s: %s", zone_id, exc)
        return StreetViewPhoto(
            zone_id=zone_id, available=False, status="IMAGE_ERROR", detail=str(exc)
        )

    log.info(
        "Street View %s: %.0f m from centroid, heading %.0f°, captured %s",
        zone_id, distance or 0, heading, meta.get("date", "?"),
    )
    return StreetViewPhoto(
        zone_id=zone_id,
        available=True,
        status="OK",
        path=f"/media/streetview/{out_path.name}",
        pano_id=meta.get("pano_id"),
        captured=meta.get("date"),
        copyright=meta.get("copyright"),
        heading=round(heading, 1),
        pano_distance_m=round(distance, 1) if distance is not None else None,
    )
