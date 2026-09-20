"""GBIF — taxonomy, known hybrid taxa, and CC-licensed reference photographs.

GBIF covers two distinct needs.

**Hybridization records.** The brief specified USDA GRIN-Global, but GRIN publishes no
JSON API — every documented `api/` path returns 404 and only scrapeable HTML remains.
GBIF exposes the same class of evidence in machine-readable form: hybrid taxa carrying
`nameType=HYBRID`, many stated as explicit parent formulas ("Citrus limon x Citrus
paradisi"), plus named nothospecies among a genus's children ("Bougainvillea
x buttiana"). Any user-facing text must cite this as GBIF named hybrid taxa, never as
GRIN-Global.

A caveat carried through to the score: a named hybrid taxon is evidence that a cross
*has been recorded*, not that it is routine or fertile. It raises confidence; it never
by itself makes a pairing certain.

**Reference imagery.** Occurrence records with StillImage media give real photographs
of the actual species, used to ground image generation instead of trusting the model's
own guess at what an uncommon cultivar looks like.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .http_cache import UpstreamError, fetch_json

log = logging.getLogger(__name__)

API_ROOT = "https://api.gbif.org/v1"
TAXONOMY_CACHE_AGE = 90 * 86_400
HYBRID_CACHE_AGE = 30 * 86_400
IMAGE_CACHE_AGE = 30 * 86_400

# Licences permitting reuse with attribution. GBIF also carries all-rights-reserved
# media, which is excluded — reference images are only pulled under an open licence.
OPEN_LICENCE_PAT = re.compile(r"creativecommons\.org/(licenses|publicdomain)/", re.I)

# Matches "A x B" / "A × B" hybrid formulas naming both parents.
_FORMULA_PAT = re.compile(
    r"^\s*([A-Z][a-z]+\s+[a-z][a-z-]+)\s*[×x]\s*([A-Z][a-z]+\s+[a-z][a-z-]+)", re.UNICODE
)


@dataclass(frozen=True)
class Taxon:
    usage_key: int
    scientific_name: str
    canonical_name: str
    rank: str
    genus: str | None
    genus_key: int | None
    family: str | None
    order: str | None


@dataclass
class HybridEvidence:
    """What GBIF knows about hybridization for a species pair."""
    direct_formula: str | None = None       # a hybrid naming exactly these two parents
    genus_hybrid_count: int = 0             # named hybrids anywhere in the shared genus
    genus_hybrid_examples: list[str] = field(default_factory=list)
    same_genus: bool = False

    @property
    def strength(self) -> float:
        """Evidence strength in 0-1, fed to the compatibility model as a prior.

        0.95  a recorded hybrid names exactly this pair
        0.30-0.70  the shared genus contains named hybrids (scaled by how many)
        0.10  same genus but no recorded hybrids anywhere in it
        0.0   different genera, no record
        """
        if self.direct_formula:
            return 0.95
        if self.genus_hybrid_count > 0:
            return min(0.70, 0.30 + 0.04 * self.genus_hybrid_count)
        return 0.10 if self.same_genus else 0.0

    @property
    def citation(self) -> str:
        if self.direct_formula:
            return f"GBIF named hybrid taxon: {self.direct_formula}"
        if self.genus_hybrid_count:
            shown = ", ".join(self.genus_hybrid_examples[:2])
            return (
                f"GBIF records {self.genus_hybrid_count} named hybrid taxa in this "
                f"genus (e.g. {shown})"
            )
        if self.same_genus:
            return "Same genus, but GBIF records no named hybrid taxa for it"
        return "GBIF records no named hybrid taxa linking these genera"


def match_taxon(scientific_name: str) -> Taxon | None:
    """Resolve a name against the GBIF backbone taxonomy."""
    try:
        payload, _ = fetch_json(
            "GET",
            f"{API_ROOT}/species/match",
            namespace="gbif_match",
            cache_key=f"name:{scientific_name.casefold()}",
            max_age_s=TAXONOMY_CACHE_AGE,
            params={"name": scientific_name, "strict": "false"},
            timeout=30.0,
        )
    except UpstreamError as exc:
        log.warning("GBIF: match failed for %r: %s", scientific_name, exc)
        return None

    if payload.get("matchType") == "NONE" or not payload.get("usageKey"):
        log.warning("GBIF: no backbone match for %r", scientific_name)
        return None

    return Taxon(
        usage_key=payload["usageKey"],
        scientific_name=payload.get("scientificName", scientific_name),
        canonical_name=payload.get("canonicalName", scientific_name),
        rank=payload.get("rank", ""),
        genus=payload.get("genus"),
        genus_key=payload.get("genusKey"),
        family=payload.get("family"),
        order=payload.get("order"),
    )


def genus_hybrids(genus: str, genus_key: int | None) -> list[str]:
    """Named hybrid taxa recorded in a genus.

    Combines two GBIF views, because neither alone is complete: the hybrid-name search
    surfaces parent formulas, while genus children surface accepted nothospecies such
    as "Bougainvillea x buttiana".
    """
    names: list[str] = []

    try:
        payload, _ = fetch_json(
            "GET",
            f"{API_ROOT}/species/search",
            namespace="gbif_hybrid_search",
            cache_key=f"genus:{genus.casefold()}",
            max_age_s=HYBRID_CACHE_AGE,
            params={"q": genus, "nameType": "HYBRID", "limit": 200},
            timeout=45.0,
        )
        for r in payload.get("results", []) or []:
            name = r.get("scientificName") or ""
            # The query is a fuzzy text search; keep only genuine genus members.
            if name and name.split()[0].strip("×x ").casefold() == genus.casefold():
                names.append(name)
    except UpstreamError as exc:
        log.warning("GBIF: hybrid search failed for %s: %s", genus, exc)

    if genus_key:
        try:
            payload, _ = fetch_json(
                "GET",
                f"{API_ROOT}/species/{genus_key}/children",
                namespace="gbif_genus_children",
                cache_key=f"key:{genus_key}",
                max_age_s=HYBRID_CACHE_AGE,
                params={"limit": 500},
                timeout=45.0,
            )
            for r in payload.get("results", []) or []:
                name = r.get("scientificName") or ""
                if "×" in name or r.get("nameType") == "HYBRID":
                    names.append(name)
        except UpstreamError as exc:
            log.warning("GBIF: genus children failed for %s: %s", genus, exc)

    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique = []
    for n in names:
        k = n.casefold()
        if k not in seen:
            seen.add(k)
            unique.append(n)
    return unique


def _formula_names(hybrid_name: str) -> tuple[str, str] | None:
    m = _FORMULA_PAT.match(hybrid_name.replace("×", "×"))
    if not m:
        return None
    return m.group(1).strip(), m.group(2).strip()


def hybrid_evidence(taxon_a: Taxon, taxon_b: Taxon) -> HybridEvidence:
    """Search GBIF for recorded hybridization between two species."""
    same_genus = bool(
        taxon_a.genus and taxon_b.genus
        and taxon_a.genus.casefold() == taxon_b.genus.casefold()
    )
    ev = HybridEvidence(same_genus=same_genus)

    # Only search within a genus — a cross-genus hybrid is rare enough that the
    # absence of a record is the expected, and correct, answer.
    if not same_genus or not taxon_a.genus:
        return ev

    names = genus_hybrids(taxon_a.genus, taxon_a.genus_key)
    ev.genus_hybrid_count = len(names)
    ev.genus_hybrid_examples = names[:5]

    a_can = taxon_a.canonical_name.casefold()
    b_can = taxon_b.canonical_name.casefold()
    for name in names:
        parents = _formula_names(name)
        if not parents:
            continue
        p1, p2 = parents[0].casefold(), parents[1].casefold()
        if {p1, p2} == {a_can, b_can}:
            ev.direct_formula = name
            break

    return ev


@dataclass(frozen=True)
class ReferenceImage:
    url: str
    licence: str
    publisher: str | None
    occurrence_key: int | None


def reference_images(taxon_key: int, limit: int = 3) -> list[ReferenceImage]:
    """Openly licensed photographs of a species, for grounding image generation."""
    try:
        payload, _ = fetch_json(
            "GET",
            f"{API_ROOT}/occurrence/search",
            namespace="gbif_images",
            cache_key=f"taxon:{taxon_key}:{limit}",
            max_age_s=IMAGE_CACHE_AGE,
            params={
                "taxonKey": taxon_key,
                "mediaType": "StillImage",
                "limit": max(limit * 6, 20),  # over-fetch: many rows fail the licence filter
            },
            timeout=45.0,
        )
    except UpstreamError as exc:
        log.warning("GBIF: image search failed for taxon %s: %s", taxon_key, exc)
        return []

    images: list[ReferenceImage] = []
    for record in payload.get("results", []) or []:
        for media in record.get("media", []) or []:
            url = media.get("identifier")
            licence = media.get("license") or ""
            if not url or media.get("type") != "StillImage":
                continue
            if not OPEN_LICENCE_PAT.search(licence):
                continue
            images.append(
                ReferenceImage(
                    url=url,
                    licence=licence,
                    publisher=media.get("publisher") or record.get("publishingOrgKey"),
                    occurrence_key=record.get("key"),
                )
            )
            if len(images) >= limit:
                return images
    if not images:
        log.warning("GBIF: no openly licensed images for taxon %s", taxon_key)
    return images
