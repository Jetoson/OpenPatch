"""Reads API behind the Streamlit dashboard.
Every route requires the admin key - see api.services.admin_auth.
"""

import time
import threading
from datetime import date, datetime, timedelta, timezone

from api.models import (
    TASK_CANCELLED,
    CPEMatch,
    CVEFinding,
    Endpoint,
    PendingUpdate,
    SoftwareInventory,
    TaskQueue,
    TelemetryHistory,
)
from api.services import scanner
from api.services.admin_auth import require_admin
from api.services.cpe_matcher import normalize_name
from api.services.cve_lookup import SEVERITY_ORDER, stale_product_count
from api.services.lifecycle import lookup_lifecycle
from config import (
    DEFAULT_DEPLOYMENT_RING,
    DEFAULT_PAGE_SIZE,
    DEPLOYMENT_RINGS,
    FINDINGS_CACHE_TTL_SECONDS,
    MAX_PAGE_SIZE,
    ONLINE_THRESHOLD_SECONDS,
    SCAN_ENABLED,
    SCAN_INTERVAL_SECONDS,
)
from database import get_db
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session


router = APIRouter(
    prefix="/api/v1/dashboard", tags=["Dashboard"], dependencies=[Depends(require_admin)]
)

ONLINE_THRESHOLD = timedelta(seconds=ONLINE_THRESHOLD_SECONDS)

_MAX_EXACT_PRODUCT_COUNTS = 200

_IN_CHUNK = 500


def _online_cutoff() -> datetime:
    return datetime.now(timezone.utc) - ONLINE_THRESHOLD


def _is_online(last_seen) -> bool:
    if last_seen is None:
        return False
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last_seen < ONLINE_THRESHOLD


def _page(limit: int | None, offset: int) -> tuple[int, int]:
    """Clamp caller-supplied paging"""
    size = DEFAULT_PAGE_SIZE if limit is None else limit
    return max(1, min(size, MAX_PAGE_SIZE)), max(0, offset)


def _chunks(values: list, size: int = _IN_CHUNK):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _eol_severity(is_eol) -> str:
    """Classify an eol value as none, scheduled or critical."""
    if not is_eol:
        return "none"
    if is_eol is True:
        return "critical"
    try:
        eol_date = datetime.strptime(str(is_eol), "%Y-%m-%d").date()
    except ValueError:
        return "scheduled"
    return "critical" if eol_date <= date.today() else "scheduled"


_EOL_RANK = {"none": 0, "scheduled": 1, "critical": 2}


# Fleet-wide rollup
_rollup_lock = threading.Lock()
_rollup_cache: dict = {"at": 0.0, "value": None}


def _device_counts_by_product(db: Session, names_by_product: dict[str, set[str]]) -> dict[str, int]:
    """How many distinct endpoints carry each matched product.
    """
    per_name = dict(
        db.query(SoftwareInventory.name, func.count(func.distinct(SoftwareInventory.device_id)))
        .group_by(SoftwareInventory.name)
        .all()
    )

    counts: dict[str, int] = {}
    multi = [p for p, names in names_by_product.items() if len(names) > 1]

    for product, names in names_by_product.items():
        counts[product] = sum(per_name.get(name, 0) for name in names)

    # Correct the over-count on the few products that need it.
    for product in multi[:_MAX_EXACT_PRODUCT_COUNTS]:
        names = list(names_by_product[product])
        devices: set[str] = set()
        for chunk in _chunks(names):
            devices.update(
                row[0] for row in
                db.query(SoftwareInventory.device_id)
                .filter(SoftwareInventory.name.in_(chunk))
                .distinct()
                .all()
            )
        counts[product] = len(devices)

    return counts


def _build_rollup(db: Session) -> dict:
    """One row per matched product, aggregated across the whole fleet.
    """
    groups = (
        db.query(
            SoftwareInventory.name,
            SoftwareInventory.version,
            func.count(func.distinct(SoftwareInventory.device_id)).label("devices"),
        )
        .group_by(SoftwareInventory.name, SoftwareInventory.version)
        .all()
    )
    if not groups:
        return {"products": [], "eol_pairs": [], "critical_pairs": []}

    cpe_by_key = {m.software_key: m for m in db.query(CPEMatch).all()}
    cve_by_key: dict[str, list] = {}
    for finding in db.query(CVEFinding).all():
        cve_by_key.setdefault(finding.software_key, []).append(finding)

    products: dict[str, dict] = {}
    names_by_product: dict[str, set[str]] = {}
    eol_pairs: set[tuple[str, str]] = set()
    critical_pairs: set[tuple[str, str]] = set()

    for name, version, _devices in groups:
        match = cpe_by_key.get(normalize_name(name))
        if not match or not match.product:
            continue

        entry = products.get(match.product)
        if entry is None:
            entry = {
                "matched_product": match.product,
                "matched_vendor": match.vendor,
                "software": name,
                "match_confidence": match.confidence,
                "versions": set(),
                "is_eol": None,
                "support_until": None,
                "lifecycle_cycle": None,
                "_eol_rank": -1,
                "_keys": set(),
            }
            products[match.product] = entry

        entry["_keys"].add(match.software_key)
        entry["software"] = min(entry["software"], name)

        names_by_product.setdefault(match.product, set()).add(name)
        if version:
            entry["versions"].add(str(version))

        cycle = lookup_lifecycle(db, match.product, version)
        if cycle:
            severity = _eol_severity(cycle.get("eol", False))
            if severity in ("scheduled", "critical"):
                eol_pairs.add((name, version))
            if severity == "critical":
                critical_pairs.add((name, version))

            rank = _EOL_RANK[severity]
            if rank > entry["_eol_rank"]:
                entry["_eol_rank"] = rank
                entry["is_eol"] = cycle.get("eol", False)
                entry["support_until"] = cycle.get("support")
                entry["lifecycle_cycle"] = cycle.get("cycle")

    counts = _device_counts_by_product(db, names_by_product)

    results = []
    for product, entry in products.items():
        versions = sorted(entry.pop("versions"))
        entry.pop("_eol_rank")
        keys = entry.pop("_keys")

        # Deduplicated by CVE id
        cves = {f.cve_id: f for key in keys for f in cve_by_key.get(key, [])}
        ordered = sorted(
            cves.values(),
            key=lambda c: (SEVERITY_ORDER.get(c.severity or "", 0), c.score or 0),
            reverse=True,
        )
        entry["cve_count"] = len(ordered)
        entry["max_severity"] = ordered[0].severity if ordered else None
        entry["max_score"] = ordered[0].score if ordered else None
        entry["cve_match_mode"] = ordered[0].match_mode if ordered else None
        entry["top_cves"] = ", ".join(c.cve_id for c in ordered[:3])

        # Every contributing name must have been scanned
        entry["cve_scanned"] = all(
            (cpe_by_key[key].cve_scanned_at is not None) for key in keys if key in cpe_by_key
        )

        entry["installed"] = ", ".join(versions[:2])
        entry["version_count"] = len(versions)
        entry["endpoints"] = counts.get(product, 0)
        results.append(entry)

    results.sort(
        key=lambda e: (SEVERITY_ORDER.get(e["max_severity"] or "", 0), e["cve_count"]),
        reverse=True,
    )
    return {
        "products": results,
        "eol_pairs": sorted(eol_pairs),
        "critical_pairs": sorted(critical_pairs),
    }


def get_rollup(db: Session) -> dict:
    """The rollup, reused for a short window - /summary and /findings both
    need it on every page render, so this saves computing it twice."""
    now = time.monotonic()
    with _rollup_lock:
        cached = _rollup_cache["value"]
        if cached is not None and now - _rollup_cache["at"] < FINDINGS_CACHE_TTL_SECONDS:
            return cached

    value = _build_rollup(db)

    with _rollup_lock:
        _rollup_cache["at"] = time.monotonic()
        _rollup_cache["value"] = value
    return value


def invalidate_rollup() -> None:
    """Called after anything that changes what the rollup would say."""
    with _rollup_lock:
        _rollup_cache["value"] = None


def _device_ids_carrying(db: Session, pairs: list) -> set:
    """Which endpoints carry at least one of these (name, version) builds.
    """
    if not pairs:
        return set()
    wanted = {(name, version) for name, version in pairs}
    names = sorted({name for name, _ in wanted})

    devices: set[str] = set()
    for chunk in _chunks(names):
        rows = (
            db.query(
                SoftwareInventory.device_id, SoftwareInventory.name, SoftwareInventory.version
            )
            .filter(SoftwareInventory.name.in_(chunk))
            .distinct()
            .all()
        )
        devices.update(device_id for device_id, name, version in rows if (name, version) in wanted)
    return devices


def _devices_carrying(db: Session, pairs: list) -> int:
    return len(_device_ids_carrying(db, pairs))


# Routes

@router.get("/summary")
def get_summary(db: Session = Depends(get_db)):
    rollup = get_rollup(db)
    cutoff = _online_cutoff()

    # Counted in SQL.
    total = db.query(func.count(Endpoint.id)).scalar() or 0
    online = (
        db.query(func.count(Endpoint.id)).filter(Endpoint.last_seen >= cutoff).scalar() or 0
    )

    return {
        "total_endpoints": total,
        "online": online,
        "offline": total - online,
        "eol_endpoints": _devices_carrying(db, rollup["eol_pairs"]),
        "critical_endpoints": _devices_carrying(db, rollup["critical_pairs"]),
        "pending_tasks": db.query(func.count(TaskQueue.id))
        .filter(TaskQueue.status == "PENDING").scalar() or 0,
        "failed_tasks": db.query(func.count(TaskQueue.id))
        .filter(TaskQueue.status == "FAILED").scalar() or 0,
        "total_software_items": db.query(func.count(SoftwareInventory.id)).scalar() or 0,
        "tracked_products": len(rollup["products"]),
    }


@router.get("/at-risk")
def at_risk_endpoints(level: str = Query(pattern="^(eol|critical)$"), db: Session = Depends(get_db)):
    """The device ids behind the EOL and Critical counts on the summary.
    """
    rollup = get_rollup(db)
    pairs = rollup["eol_pairs"] if level == "eol" else rollup["critical_pairs"]
    return {"level": level, "device_ids": sorted(_device_ids_carrying(db, pairs))}


@router.get("/endpoints")
def list_endpoints(
    db: Session = Depends(get_db),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    department: str | None = None,
    ring: str | None = None,
    online: bool | None = None,
    search: str | None = None,
):
    size, skip = _page(limit, offset)

    query = db.query(Endpoint)
    if department:
        query = query.filter(Endpoint.department == department)
    if ring:
        query = query.filter(Endpoint.deployment_ring == ring)
    if online is not None:
        cutoff = _online_cutoff()
        query = query.filter(
            Endpoint.last_seen >= cutoff if online else Endpoint.last_seen < cutoff
        )
    if search:
        pattern = f"%{search}%"
        query = query.filter(Endpoint.hostname.ilike(pattern))

    total = query.with_entities(func.count(Endpoint.id)).scalar() or 0

    endpoints = (
        query.order_by(
            (Endpoint.department == None).asc(), Endpoint.department, Endpoint.hostname  # noqa: E711
        )
        .limit(size)
        .offset(skip)
        .all()
    )

    # Restricted to the devices on this page, not the whole inventory.
    device_ids = [e.device_id for e in endpoints]

    software_counts: dict[str, int] = {}
    update_counts: dict[tuple[str, str], int] = {}
    for chunk in _chunks(device_ids):
        software_counts.update(
            db.query(SoftwareInventory.device_id, func.count(SoftwareInventory.id))
            .filter(SoftwareInventory.device_id.in_(chunk))
            .group_by(SoftwareInventory.device_id)
            .all()
        )
        update_counts.update({
            (device_id, source): count
            for device_id, source, count in
            db.query(PendingUpdate.device_id, PendingUpdate.source, func.count(PendingUpdate.id))
            .filter(PendingUpdate.device_id.in_(chunk))
            .group_by(PendingUpdate.device_id, PendingUpdate.source)
            .all()
        })

    items = [
        {
            "device_id": e.device_id,
            "hostname": e.hostname,
            "department": e.department,
            "os_version": e.os_version,
            "os_name": e.os_name,
            "cpu_usage": e.cpu_usage,
            "ram_usage": e.ram_usage,
            "last_seen": e.last_seen,
            "online": _is_online(e.last_seen),
            "software_count": software_counts.get(e.device_id, 0),
            "deployment_ring": e.deployment_ring or DEFAULT_DEPLOYMENT_RING,
            "verify_command": e.verify_command,
            "critical_programs": e.critical_programs,
            "reboot_required": bool(e.reboot_required),
            "reboot_reasons": e.reboot_reasons,
            "windows_updates": update_counts.get((e.device_id, "windows"), 0),
            "third_party_updates": update_counts.get((e.device_id, "winget"), 0),
        }
        for e in endpoints
    ]

    return {"items": items, "total": total, "limit": size, "offset": skip}


@router.get("/rings")
def list_rings():
    """The canonical ring list."""
    return {"rings": DEPLOYMENT_RINGS, "default": DEFAULT_DEPLOYMENT_RING}


@router.get("/departments")
def list_departments(db: Session = Depends(get_db)):
    """Distinct departments, for filtering the fleet without the dashboard
    having to page through every endpoint to discover them."""
    rows = (
        db.query(Endpoint.department)
        .filter(Endpoint.department.isnot(None))
        .distinct()
        .order_by(Endpoint.department)
        .all()
    )
    return {"departments": [row[0] for row in rows if row[0]]}


@router.get("/scan-status")
def get_scan_status(db: Session = Depends(get_db)):
    """How current the vulnerability report is, shown instead of a manual
    scanning.
    """
    resolved = db.query(func.count(CPEMatch.id)).filter(CPEMatch.cpe23_uri.isnot(None)).scalar() or 0
    scanned = (
        db.query(func.count(CPEMatch.id))
        .filter(CPEMatch.cpe23_uri.isnot(None), CPEMatch.cve_scanned_at.isnot(None))
        .scalar() or 0
    )
    unidentified = (
        db.query(func.count(CPEMatch.id)).filter(CPEMatch.cpe23_uri.is_(None)).scalar() or 0
    )
    newest = db.query(func.max(CPEMatch.cve_scanned_at)).scalar()
    oldest = (
        db.query(func.min(CPEMatch.cve_scanned_at))
        .filter(CPEMatch.cve_scanned_at.isnot(None))
        .scalar()
    )

    return {
        "enabled": SCAN_ENABLED,
        "interval_seconds": SCAN_INTERVAL_SECONDS,
        "products_resolved": resolved,
        "products_scanned": scanned,
        "products_pending": stale_product_count(db),
        "names_unidentified": unidentified,
        "last_scanned_at": newest,
        "oldest_scan_at": oldest,
        **scanner.status(),
    }


@router.get("/endpoints/{device_id}/updates")
def get_endpoint_updates(device_id: str, db: Session = Depends(get_db)):
    rows = (
        db.query(PendingUpdate)
        .filter(PendingUpdate.device_id == device_id)
        .order_by(PendingUpdate.source, PendingUpdate.name)
        .all()
    )
    return [
        {
            "source": r.source,
            "name": r.name,
            "kb": r.kb,
            "severity": r.severity,
            "current_version": r.current_version,
            "available_version": r.available_version,
            "collected_at": r.collected_at,
        }
        for r in rows
    ]


@router.get("/endpoints/{device_id}/telemetry")
def get_endpoint_telemetry(
    device_id: str,
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """Most recent `limit` telemetry samples, oldest first (for charting).
    """
    rows = (
        db.query(TelemetryHistory)
        .filter(TelemetryHistory.device_id == device_id)
        .order_by(TelemetryHistory.recorded_at.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()
    return [
        {"recorded_at": r.recorded_at, "cpu_usage": r.cpu_usage, "ram_usage": r.ram_usage}
        for r in rows
    ]


@router.get("/endpoints/{device_id}/inventory")
def get_endpoint_inventory(
    device_id: str,
    db: Session = Depends(get_db),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
):
    size, skip = _page(limit, offset)
    query = db.query(SoftwareInventory).filter(SoftwareInventory.device_id == device_id)
    total = query.with_entities(func.count(SoftwareInventory.id)).scalar() or 0
    rows = query.order_by(SoftwareInventory.name).limit(size).offset(skip).all()
    return {
        "items": [{"name": r.name, "version": r.version, "publisher": r.publisher} for r in rows],
        "total": total,
        "limit": size,
        "offset": skip,
    }


@router.get("/endpoints/{device_id}/tasks")
def get_endpoint_tasks(
    device_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    include_cancelled: bool = False,
    db: Session = Depends(get_db),
):
    """One endpoint's task history, newest first."""
    query = db.query(TaskQueue).filter(TaskQueue.device_id == device_id)
    if not include_cancelled:
        query = query.filter(TaskQueue.status != TASK_CANCELLED)

    rows = query.order_by(TaskQueue.created_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "action": r.action,
            "target": r.target,
            "status": r.status,
            "output": r.output,
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/tasks")
def list_tasks(
    db: Session = Depends(get_db),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    status: str | None = None,
    device_id: str | None = None,
    include_cancelled: bool = False,
):
    """Recent tasks across the fleet, newest first, one query rather than
    one per endpoint.
    """
    size, skip = _page(limit, offset)

    query = db.query(TaskQueue, Endpoint.hostname).outerjoin(
        Endpoint, Endpoint.device_id == TaskQueue.device_id
    )
    if device_id:
        query = query.filter(TaskQueue.device_id == device_id)

    # Asking for cancelled tasks by status is itself the request to see them.
    if status == TASK_CANCELLED:
        include_cancelled = True

    hidden = 0
    if not include_cancelled:
        hidden = (
            query.with_entities(func.count(TaskQueue.id))
            .filter(TaskQueue.status == TASK_CANCELLED)
            .scalar() or 0
        )
        query = query.filter(TaskQueue.status != TASK_CANCELLED)

    if status:
        query = query.filter(TaskQueue.status == status)

    total = query.with_entities(func.count(TaskQueue.id)).scalar() or 0
    rows = query.order_by(TaskQueue.created_at.desc(), TaskQueue.id.desc()).limit(size).offset(skip).all()

    return {
        "items": [
            {
                "id": task.id,
                "device_id": task.device_id,
                "hostname": hostname,
                "action": task.action,
                "target": task.target,
                "status": task.status,
                "output": task.output,
                "created_at": task.created_at,
            }
            for task, hostname in rows
        ],
        "total": total,
        "limit": size,
        "offset": skip,
        "cancelled_hidden": hidden,
    }


@router.get("/endpoints/{device_id}/findings")
def get_endpoint_findings(device_id: str, db: Session = Depends(get_db)):
    """Lifecycle and CVE status for one device's software."""
    inventory = (
        db.query(SoftwareInventory.name, SoftwareInventory.version)
        .filter(SoftwareInventory.device_id == device_id)
        .distinct()
        .all()
    )
    if not inventory:
        return []

    keys = {normalize_name(name) for name, _ in inventory}
    cpe_by_key = {
        m.software_key: m
        for chunk in _chunks(sorted(keys))
        for m in db.query(CPEMatch).filter(CPEMatch.software_key.in_(chunk)).all()
    }
    cve_counts = dict(
        db.query(CVEFinding.software_key, func.count(CVEFinding.id))
        .filter(CVEFinding.software_key.in_(sorted(keys)))
        .group_by(CVEFinding.software_key)
        .all()
    )

    findings = []
    for name, version in inventory:
        key = normalize_name(name)
        match = cpe_by_key.get(key)
        if not match or not match.product:
            continue

        entry = {
            "software": name,
            "installed_version": version,
            "matched_vendor": match.vendor,
            "matched_product": match.product,
            "cpe": match.cpe23_uri,
            "match_confidence": match.confidence,
            "cve_scanned": match.cve_scanned_at is not None,
            "cve_count": cve_counts.get(key, 0),
        }

        cycle = lookup_lifecycle(db, match.product, version)
        if cycle:
            entry["lifecycle_cycle"] = cycle.get("cycle")
            entry["is_eol"] = cycle.get("eol", False)
            entry["support_until"] = cycle.get("support")

        findings.append(entry)

    return findings


@router.get("/findings")
def get_findings(db: Session = Depends(get_db)):
    """Fleet-wide lifecycle and vulnerability status, one row per product,
    aggregated server-side"""
    return get_rollup(db)["products"]
