"""Cached, retrying HTTP for flaky public APIs.

Overpass in particular is unreliable under load — a single request can 504 and the
same request succeed seconds later, and community mirrors can hang past 100s. For a
live demo that is unacceptable, so every upstream response is written to disk and
served from there on a repeat request. Nothing here invents data: a cache miss plus a
failed fetch raises, it does not substitute a placeholder value.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import CACHE_DIR, USER_AGENT

log = logging.getLogger(__name__)


class UpstreamError(RuntimeError):
    """An upstream source could not be reached or returned an unusable response."""


def _cache_path(namespace: str, key: str) -> Path:
    digest = hashlib.sha256(key.encode()).hexdigest()[:20]
    d = CACHE_DIR / namespace
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{digest}.json"


def read_cache(namespace: str, key: str, max_age_s: float | None = None) -> Any | None:
    """Return the cached payload, or None on miss/expiry/corruption."""
    p = _cache_path(namespace, key)
    if not p.exists():
        return None
    if max_age_s is not None and (time.time() - p.stat().st_mtime) > max_age_s:
        return None
    try:
        return json.loads(p.read_text())["payload"]
    except (json.JSONDecodeError, KeyError, OSError):
        log.warning("discarding unreadable cache entry %s", p)
        return None


def write_cache(namespace: str, key: str, payload: Any) -> None:
    p = _cache_path(namespace, key)
    body = {"key": key, "fetched_at": time.time(), "payload": payload}
    # Write-then-rename so a crash mid-write cannot leave a truncated cache file.
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(body))
    tmp.replace(p)


def fetch_json(
    method: str,
    url: str,
    *,
    namespace: str,
    cache_key: str,
    max_age_s: float | None = None,
    fallback_urls: tuple[str, ...] = (),
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    timeout: float = 90.0,
    attempts: int = 3,
) -> tuple[Any, bool]:
    """Fetch JSON with retries, endpoint fallback, and a disk cache.

    Returns (payload, from_cache). Raises UpstreamError only when every endpoint
    failed AND no cached copy exists — so callers can distinguish "no data" from
    "stale data", and never have to guess which they got.
    """
    cached = read_cache(namespace, cache_key, max_age_s=max_age_s)
    if cached is not None:
        return cached, True

    merged_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    merged_headers.update(headers or {})

    endpoints = (url, *fallback_urls)
    last_error: str = "no attempt made"

    for endpoint in endpoints:
        for attempt in range(1, attempts + 1):
            try:
                with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                    resp = client.request(
                        method, endpoint, headers=merged_headers, params=params, data=data
                    )
                if resp.status_code == 200:
                    payload = resp.json()
                    write_cache(namespace, cache_key, payload)
                    return payload, False

                last_error = f"HTTP {resp.status_code} from {endpoint}"
                # 429/5xx are transient and worth a retry; 4xx client errors are not.
                if resp.status_code not in (429, 500, 502, 503, 504):
                    log.warning("%s — not retryable, moving on", last_error)
                    break
                log.warning("%s — attempt %d/%d", last_error, attempt, attempts)
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                last_error = f"{type(exc).__name__} from {endpoint}: {exc}"
                log.warning("%s — attempt %d/%d", last_error, attempt, attempts)

            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))  # 1s, 2s

    # Last resort: an expired cache entry beats nothing, but the caller is told.
    stale = read_cache(namespace, cache_key, max_age_s=None)
    if stale is not None:
        log.warning("serving STALE cache for %s/%s after: %s", namespace, cache_key, last_error)
        return stale, True

    raise UpstreamError(f"all endpoints failed for {namespace}/{cache_key}: {last_error}")
