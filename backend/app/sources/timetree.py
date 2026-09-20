"""TimeTree — published divergence-time estimates between species pairs.

TimeTree aggregates peer-reviewed molecular clock studies. Its pairwise endpoint
returns CSV with, per pair: the number of contributing studies, a precomputed median
age, a 95% confidence interval, and an adjusted age.

Two details that matter for honest scoring:

* The endpoint accepts ONLY numeric NCBI taxids (see sources/ncbi.py).
* The confidence interval is frequently very wide — Ficus religiosa vs
  F. benghalensis came back 48.3 Mya with a CI of 5.6-68.8 across 6 studies. A model
  that consumed the point estimate alone would project false precision, so CI width
  and study count are both carried through to the compatibility score as an explicit
  uncertainty term rather than discarded.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass

from .http_cache import UpstreamError, read_cache, write_cache
from ..config import USER_AGENT

import httpx

log = logging.getLogger(__name__)

API_ROOT = "http://timetree.org/api/pairwise"
CACHE_AGE = 180 * 86_400  # published divergence estimates change very slowly


@dataclass(frozen=True)
class Divergence:
    """A published divergence estimate for one species pair."""
    taxon_a_id: int
    taxon_b_id: int
    name_a: str
    name_b: str
    study_count: int
    median_mya: float
    ci_low_mya: float | None
    ci_high_mya: float | None
    adjusted_mya: float | None

    @property
    def best_estimate_mya(self) -> float:
        """Adjusted age when TimeTree provides one, else the precomputed median."""
        return self.adjusted_mya if self.adjusted_mya is not None else self.median_mya

    @property
    def ci_width_mya(self) -> float | None:
        if self.ci_low_mya is None or self.ci_high_mya is None:
            return None
        return self.ci_high_mya - self.ci_low_mya

    @property
    def relative_uncertainty(self) -> float:
        """CI width relative to the estimate; 1.0 when unknown (assume worst case).

        Used to widen the confidence band on any score derived from this divergence.
        """
        width = self.ci_width_mya
        est = self.best_estimate_mya
        if width is None or est <= 0:
            return 1.0
        return min(2.0, width / est)


def _to_float(raw: str) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def fetch_divergence(taxid_a: int, taxid_b: int) -> Divergence | None:
    """Divergence between two NCBI taxids, or None when TimeTree has no estimate.

    None is a meaningful result, not a failure: absence of a published divergence
    estimate is itself information, and callers surface it as reduced confidence
    rather than assuming a default distance.
    """
    if taxid_a == taxid_b:
        return None  # same taxon — handled as the conspecific case by the caller

    # Order-independent: TimeTree returns the same pair either way.
    lo, hi = sorted((int(taxid_a), int(taxid_b)))
    cache_key = f"{lo}_{hi}"

    cached = read_cache("timetree_pairwise", cache_key, max_age_s=CACHE_AGE)
    if cached is None:
        url = f"{API_ROOT}/{lo}/{hi}"
        try:
            with httpx.Client(timeout=45.0, follow_redirects=True) as client:
                resp = client.get(url, headers={"User-Agent": USER_AGENT})
        except httpx.HTTPError as exc:
            raise UpstreamError(f"TimeTree unreachable for {lo}/{hi}: {exc}") from exc

        if resp.status_code != 200:
            raise UpstreamError(f"TimeTree HTTP {resp.status_code} for {lo}/{hi}")

        cached = resp.text
        write_cache("timetree_pairwise", cache_key, cached)

    text = (cached or "").strip()
    # TimeTree signals "no data" with an empty body or an HTML error blob.
    if not text or text.lstrip().startswith("<") or "error" in text[:60].lower():
        log.info("TimeTree: no divergence estimate for %s/%s", lo, hi)
        return None

    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return None
    row = rows[0]

    median = _to_float(row.get("precomputed_age", ""))
    if median is None:
        log.info("TimeTree: unparseable age for %s/%s", lo, hi)
        return None

    try:
        study_count = int(float(row.get("all_total") or 0))
    except (TypeError, ValueError):
        study_count = 0

    return Divergence(
        taxon_a_id=lo,
        taxon_b_id=hi,
        name_a=row.get("scientific_name_a", ""),
        name_b=row.get("scientific_name_b", ""),
        study_count=study_count,
        median_mya=median,
        ci_low_mya=_to_float(row.get("precomputed_ci_low", "")),
        ci_high_mya=_to_float(row.get("precomputed_ci_high", "")),
        adjusted_mya=_to_float(row.get("adjusted_age", "")),
    )
