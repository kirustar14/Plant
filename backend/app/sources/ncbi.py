"""NCBI Taxonomy — resolve scientific names to numeric taxonomy IDs.

Exists for one reason: TimeTree's pairwise API rejects scientific names outright
("resolve to multiple nodes — please use numeric NCBI taxonomy node IDs") and only
accepts NCBI taxids. This module is the bridge.

NCBI E-utilities is keyless but rate-limited to 3 requests/sec without an API key, so
calls are throttled and cached aggressively — taxids never change.
"""
from __future__ import annotations

import logging
import threading
import time

from .http_cache import UpstreamError, fetch_json

log = logging.getLogger(__name__)

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TAXID_CACHE_AGE = 365 * 86_400  # a name->taxid mapping is effectively permanent

_MIN_INTERVAL_S = 0.35  # stay under NCBI's 3 req/s guidance
_last_call = 0.0
_lock = threading.Lock()


def _throttle() -> None:
    global _last_call
    with _lock:
        delta = time.monotonic() - _last_call
        if delta < _MIN_INTERVAL_S:
            time.sleep(_MIN_INTERVAL_S - delta)
        _last_call = time.monotonic()


def resolve_taxid(scientific_name: str) -> int | None:
    """NCBI taxonomy ID for a scientific name, or None if unresolvable.

    Returns None rather than raising: a species missing from NCBI should drop out of
    genetic-distance scoring and be handled explicitly downstream, not abort the run.
    """
    name = scientific_name.strip()
    if not name:
        return None

    _throttle()
    try:
        payload, _ = fetch_json(
            "GET",
            f"{EUTILS}/esearch.fcgi",
            namespace="ncbi_taxid",
            cache_key=f"name:{name.casefold()}",
            max_age_s=TAXID_CACHE_AGE,
            params={"db": "taxonomy", "term": name, "retmode": "json"},
            timeout=30.0,
            attempts=2,
        )
    except UpstreamError as exc:
        log.warning("NCBI: could not resolve %r: %s", name, exc)
        return None

    idlist = (payload.get("esearchresult") or {}).get("idlist") or []
    if not idlist:
        log.warning("NCBI: no taxonomy match for %r", name)
        return None

    if len(idlist) > 1:
        # Ambiguous names would silently pick a wrong lineage; log so it is visible.
        log.info("NCBI: %r matched %d taxa, using first (%s)", name, len(idlist), idlist[0])

    try:
        return int(idlist[0])
    except (TypeError, ValueError):
        return None
