"""EOL lookups, cached in the database rather than in memory.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
from api.models import ExternalCache
from config import EOL_CACHE_TTL_HOURS
from sqlalchemy.orm import Session

ALL_PRODUCTS_URL = "https://endoflife.date/api/all.json"
PRODUCT_CYCLES_URL = "https://endoflife.date/api/{product}.json"

CACHE_TTL = timedelta(hours=EOL_CACHE_TTL_HOURS)

_CATALOG_KEY = "eol:catalog"
_CYCLES_KEY = "eol:cycles:{product}"

# Read-through memo over the database cache
_MEMO_TTL_SECONDS = 300
_memo: dict[str, tuple[float, object]] = {}
_memo_lock = threading.Lock()


def _memo_get(key: str):
    with _memo_lock:
        entry = _memo.get(key)
        if entry and time.monotonic() - entry[0] < _MEMO_TTL_SECONDS:
            return entry[1]
    return None


def _memo_put(key: str, value) -> None:
    with _memo_lock:
        _memo[key] = (time.monotonic(), value)


def _load(db: Session, key: str) -> ExternalCache | None:
    return db.query(ExternalCache).filter(ExternalCache.cache_key == key).first()


def _is_fresh(row: ExternalCache) -> bool:
    fetched_at = row.fetched_at
    if fetched_at is None:
        return False
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched_at < CACHE_TTL


def _store(db: Session, key: str, payload, ok: bool) -> None:
    row = _load(db, key)
    if row is None:
        row = ExternalCache(cache_key=key)
        db.add(row)
    row.payload = json.dumps(payload)
    row.ok = ok
    row.fetched_at = datetime.now(timezone.utc)
    db.commit()


def _decode(row: ExternalCache | None, default):
    if row is None or row.payload is None:
        return default
    try:
        return json.loads(row.payload)
    except ValueError:
        return default


def _fetch(db: Session, key: str, url: str, default):
    """Cache-first fetch with stale-on-error.
    """
    memoized = _memo_get(key)
    if memoized is not None:
        return memoized

    row = _load(db, key)
    if row is not None and _is_fresh(row):
        value = _decode(row, default)
        _memo_put(key, value)
        return value

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):

        stale = _decode(row, None)
        value = stale if stale is not None else default
        _memo_put(key, value)
        return value

    _store(db, key, payload, ok=True)
    _memo_put(key, payload)
    return payload


def get_product_catalog(db: Session) -> set[str]:
    """Returns every EOL product slug.
    """
    return set(_fetch(db, _CATALOG_KEY, ALL_PRODUCTS_URL, []))


def get_cycles(db: Session, product: str) -> list[dict]:
    """Returns release cycles for one product slug.
    """
    key = _CYCLES_KEY.format(product=product)
    cycles = _fetch(db, key, PRODUCT_CYCLES_URL.format(product=product), [])
    return cycles if isinstance(cycles, list) else []


def _resolve_slug(db: Session, product: str) -> str | None:
    """Resolves among NVD CPE product names and endoflife.date slugs.
    """
    catalog = get_product_catalog(db)
    candidates = [
        product,
        product.replace(".", ""),
        product.replace("-", ""),
        product.replace("_", "-"),
        product.replace(".", "").replace("-", ""),
    ]
    for candidate in candidates:
        if candidate in catalog:
            return candidate
    return None


def lookup_lifecycle(db: Session, product: str, installed_version: str) -> dict | None:
    """Searches the release cycle matching the installed version.
    """
    if not installed_version:
        return None

    slug = _resolve_slug(db, product)
    if slug is None:
        return None

    for cycle in get_cycles(db, slug):
        if not isinstance(cycle, dict):
            continue
        cycle_id = str(cycle.get("cycle", ""))
        if not cycle_id:
            continue
        if installed_version == cycle_id or installed_version.startswith(cycle_id + "."):
            return cycle

    return None
