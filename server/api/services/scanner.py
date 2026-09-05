"""Keeps the vulnerability report current on its own, running on a schedule
(once shortly after startup, then every SCAN_INTERVAL_SECONDS).
"""

import threading
import time
from datetime import datetime, timezone

from api.models import CPEMatch, SoftwareInventory
from api.services.cpe_matcher import normalize_name, resolve_cpe
from api.services.cve_lookup import scan as scan_cves
from api.services.lifecycle import lookup_lifecycle
from config import (
    SCAN_ENABLED,
    SCAN_INTERVAL_SECONDS,
    SCAN_MAX_CVE_PER_CYCLE,
    SCAN_MAX_RESOLVE_PER_CYCLE,
    SCAN_STARTUP_DELAY_SECONDS,
)
from database import SessionLocal
from sqlalchemy.orm import Session


IGNORE_KEYWORDS = (
    "core interpreter", "development libraries", "documentation",
    "executables", "pip bootstrap", "standard library",
    "tcl/tk support", "test suite", "launcher", "add to path",
)

_scan_lock = threading.Lock()

_status: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "last_result": None,
    "last_error": None,
}
_status_lock = threading.Lock()


def _set_status(**fields) -> None:
    with _status_lock:
        _status.update(fields)


def status() -> dict:
    with _status_lock:
        return dict(_status)


def _is_noise(name: str) -> bool:
    lowered = name.lower()
    return any(keyword in lowered for keyword in IGNORE_KEYWORDS)


def resolve_inventory(db: Session, limit: int | None = None) -> dict:
    """Gives every piece of reported software an NVD identity.
    """
    pairs = db.query(SoftwareInventory.name, SoftwareInventory.version).distinct().all()
    cached = {m.software_key: m for m in db.query(CPEMatch).all()}

    pending = []
    for name, version in pairs:
        if not name or _is_noise(name):
            continue
        key = normalize_name(name)
        if not key:
            continue
        match = cached.get(key)
        known = match is not None and match.resolved_at is not None
        pending.append((key, name, version, known))

    pending.sort(key=lambda item: item[3])

    seen: set[str] = set()
    looked_up = 0
    identified = 0
    newly_identified = 0

    for key, name, version, _known in pending:
        if key in seen:
            continue
        seen.add(key)

        if limit is not None and looked_up >= limit:
            break

        previous = cached.get(key)
        previous_resolved_at = previous.resolved_at if previous is not None else None

        match = resolve_cpe(db, name, version)

        if match.resolved_at != previous_resolved_at:
            looked_up += 1
            if match.product:
                newly_identified += 1
        if match.product:
            identified += 1

    return {
        "names_examined": len(seen),
        "names_looked_up": looked_up,
        "identified": identified,
        "newly_identified": newly_identified,
    }


def warm_lifecycle(db: Session) -> int:
    """Pre-fetch endoflife.date data for the products the fleet actually runs.
    """
    rows = (
        db.query(CPEMatch.product, SoftwareInventory.version)
        .join(SoftwareInventory, SoftwareInventory.name == CPEMatch.raw_name)
        .filter(CPEMatch.product.isnot(None))
        .distinct()
        .all()
    )
    for product, version in rows:
        lookup_lifecycle(db, product, version)
    return len(rows)


def run_once(db: Session | None = None) -> dict:
    """One cycle: identify software, look up its CVEs, warm lifecycle.
    """
    if not _scan_lock.acquire(blocking=False):
        return {"skipped": "a scan is already running"}

    owns_session = db is None
    session = db or SessionLocal()
    started = datetime.now(timezone.utc)
    _set_status(running=True, started_at=started, last_error=None)

    try:
        resolution = resolve_inventory(session, limit=SCAN_MAX_RESOLVE_PER_CYCLE)
        cves = scan_cves(session, only_unscanned=True, limit=SCAN_MAX_CVE_PER_CYCLE)
        lifecycle_products = warm_lifecycle(session)

        result = {
            **resolution,
            **cves,
            "lifecycle_products": lifecycle_products,
            "duration_seconds": round(
                (datetime.now(timezone.utc) - started).total_seconds(), 1
            ),
        }
        _set_status(running=False, finished_at=datetime.now(timezone.utc), last_result=result)

        from api.routers.dashboard import invalidate_rollup

        invalidate_rollup()
        return result

    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        _set_status(running=False, finished_at=datetime.now(timezone.utc), last_error=message)
        return {"error": message}

    finally:
        if owns_session:
            session.close()
        _scan_lock.release()


def _loop() -> None:
    time.sleep(SCAN_STARTUP_DELAY_SECONDS)
    while True:
        result = run_once()
        if result.get("error"):
            print(f"[!] Vulnerability scan failed: {result['error']}", flush=True)
        elif not result.get("skipped") and (result["names_looked_up"] or result["products_scanned"]):
            print(
                f"[*] Vulnerability scan: looked up {result['names_looked_up']} name(s) "
                f"({result['newly_identified']} newly identified), scanned "
                f"{result['products_scanned']} product(s), found "
                f"{result['cves_found']} CVE(s) in {result['duration_seconds']}s",
                flush=True,
            )
        time.sleep(SCAN_INTERVAL_SECONDS)


def start_background_scanner() -> bool:
    """Starts the scan loop and returns the status."""
    if not SCAN_ENABLED or SCAN_INTERVAL_SECONDS <= 0:
        return False
    threading.Thread(target=_loop, name="openpatch-scanner", daemon=True).start()
    return True
