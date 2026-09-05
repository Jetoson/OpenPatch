import re
from datetime import datetime, timedelta, timezone

from api.models import CPEMatch
from api.services.nvd_client import NVDUnavailable, get_json
from config import CPE_CACHE_TTL_DAYS, CPE_MISS_CACHE_TTL_DAYS
from sqlalchemy.orm import Session

NVD_CPE_API_URL = "https://services.nvd.nist.gov/rest/json/cpes/2.0"

# How long a resolved match is trusted before we ask NVD again.
CACHE_TTL = timedelta(days=CPE_CACHE_TTL_DAYS)

# A miss expires sooner than a hit.
MISS_CACHE_TTL = timedelta(days=CPE_MISS_CACHE_TTL_DAYS)

_ARCH_NOISE_PATTERNS = [
    re.compile(r"\((?:x86|x64|32-bit|64-bit)\)", re.I),
    re.compile(r"\b(?:32|64)-bit\b", re.I),
]
_VERSION_PATTERN = re.compile(r"\bv?\d+(?:\.\d+){1,3}\b")
_PUNCTUATION_PATTERN = re.compile(r"[^\w\s]")


def _strip_arch_and_version(name: str) -> str:
    cleaned = name
    for pattern in _ARCH_NOISE_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = _VERSION_PATTERN.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def normalize_name(name: str) -> str:
    """Canonical form used for scoring and as the local cache key.
    """
    cleaned = _PUNCTUATION_PATTERN.sub(" ", _strip_arch_and_version(name))
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def _search_keyword(name: str) -> str:
    """Form sent to NVD's keywordSearch.
    """
    return _strip_arch_and_version(name)


def _query_nvd(keyword: str) -> list[dict]:
    """Raises NVDUnavailable when NVD never answered.
    """
    payload = get_json(
        NVD_CPE_API_URL,
        # A large page, ranked client-side: NVD's keywordSearch is a
        # substring match, not a relevance ranker.
        params={"keywordSearch": keyword, "resultsPerPage": 5000},
        timeout=20,
    )
    return payload.get("products", [])


def _parse_cpe_uri(cpe23_uri: str) -> dict | None:
    # cpe:2.3:part:vendor:product:version:update:edition:language:...
    parts = cpe23_uri.split(":")
    if len(parts) < 6:
        return None
    return {"vendor": parts[3], "product": parts[4], "version": parts[5]}


# Below this Jaccard score.
MIN_MATCH_SCORE = 0.5


def _score_product(product: str, normalized_target: str) -> float:
    # CPE product fields use underscores as word separators (e.g. "python_driver").
    product_normalized = normalize_name(product.replace("_", " "))

    collapsed_product = product_normalized.replace(" ", "")
    collapsed_target = normalized_target.replace(" ", "")
    if collapsed_product and collapsed_product == collapsed_target:
        return 1.0

    product_tokens = set(product_normalized.split())
    target_tokens = set(normalized_target.split())
    if not product_tokens or not target_tokens:
        return 0.0
    overlap = len(product_tokens & target_tokens)
    union = len(product_tokens | target_tokens)
    return overlap / union if union else 0.0


def _best_match(products: list[dict], normalized_name: str, installed_version: str) -> dict | None:
    candidates = []
    for entry in products:
        cpe = entry.get("cpe", {})
        if cpe.get("deprecated"):
            continue
        cpe23_uri = cpe.get("cpeName")
        parsed = _parse_cpe_uri(cpe23_uri) if cpe23_uri else None
        if not parsed:
            continue

        score = _score_product(parsed["product"], normalized_name)
        if score < MIN_MATCH_SCORE:
            continue

        candidates.append({**parsed, "cpe23_uri": cpe23_uri, "score": score})

    if not candidates:
        return None

    candidates.sort(key=lambda c: c["score"], reverse=True)
    best_score = candidates[0]["score"]
    top = [c for c in candidates if c["score"] == best_score]

    for c in top:
        if installed_version and c["version"] == installed_version:
            c["confidence"] = "exact_version"
            return c

    top[0]["confidence"] = "product_only"
    return top[0]


def resolve_cpe(db: Session, name: str, version: str) -> CPEMatch:
    """Resolves a software inventory entry to an NVD CPE (vendor:product[:version]).
    """
    normalized = normalize_name(name)

    cached = db.query(CPEMatch).filter(CPEMatch.software_key == normalized).first()
    if cached and cached.resolved_at:
        resolved_at = cached.resolved_at
        if resolved_at.tzinfo is None:
            resolved_at = resolved_at.replace(tzinfo=timezone.utc)
        ttl = MISS_CACHE_TTL if cached.confidence == "none" else CACHE_TTL
        if datetime.now(timezone.utc) - resolved_at < ttl:
            return cached

    try:
        products = _query_nvd(_search_keyword(name))
    except NVDUnavailable:
        if cached is not None:
            return cached
        placeholder = CPEMatch(software_key=normalized, raw_name=name, confidence="unavailable")
        return placeholder

    match = _best_match(products, normalized, version)

    if cached is None:
        cached = CPEMatch(software_key=normalized)
        db.add(cached)

    cached.raw_name = name
    cached.resolved_at = datetime.now(timezone.utc)

    if match:
        cached.cpe23_uri = match["cpe23_uri"]
        cached.vendor = match["vendor"]
        cached.product = match["product"]
        cached.matched_version = match["version"]
        cached.confidence = match["confidence"]
    else:
        cached.cpe23_uri = None
        cached.vendor = None
        cached.product = None
        cached.matched_version = None
        cached.confidence = "none"

    db.commit()
    db.refresh(cached)
    return cached
