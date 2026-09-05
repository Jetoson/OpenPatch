"""Looks up CVEs for matched products against the NVD CVE API.
"""

from datetime import datetime, timedelta, timezone

from api.models import CPEMatch, CVEFinding
from api.services.nvd_client import NVDUnavailable, get_json
from config import CVE_CACHE_TTL_DAYS
from sqlalchemy import case, or_
from sqlalchemy.orm import Session

NVD_CVE_API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"


CACHE_TTL = timedelta(days=CVE_CACHE_TTL_DAYS)

SEVERITY_ORDER = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}


def _severity_of(cve: dict):
    """Preferring CVSS v3.1 over v3.0 over v2.
    """
    metrics = cve.get("metrics", {})
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        entries = metrics.get(key)
        if not entries:
            continue
        data = entries[0].get("cvssData", {})
        severity = data.get("baseSeverity") or entries[0].get("baseSeverity")
        return (severity or "").upper() or None, data.get("baseScore")
    return None, None


def _query(params: dict) -> list[dict]:
    """Raises NVDUnavailable when NVD never answered.
    """
    payload = get_json(NVD_CVE_API_URL, params={**params, "resultsPerPage": 50}, timeout=30)
    return payload.get("vulnerabilities", [])


def fetch_for_match(match: CPEMatch) -> list[dict]:
    """CVEs for one matched product, as plain dicts ready to persist."""
    if not match.cpe23_uri or not match.product:
        return []

    if match.confidence == "exact_version":
        mode = "version"
        raw = _query({"cpeName": match.cpe23_uri})
    else:
        parts = match.cpe23_uri.split(":")
        if len(parts) < 5:
            return []
        mode = "product"
        raw = _query({"virtualMatchString": ":".join(parts[:5])})

    findings = []
    for entry in raw:
        cve = entry.get("cve", {})
        cve_id = cve.get("id")
        if not cve_id:
            continue
        severity, score = _severity_of(cve)
        description = next(
            (d.get("value") for d in cve.get("descriptions", []) if d.get("lang") == "en"),
            "",
        )
        findings.append({
            "cve_id": cve_id,
            "severity": severity,
            "score": score,
            "published": cve.get("published"),
            "summary": (description or "")[:500],
            "match_mode": mode,
        })
    return findings


def scan(db: Session, only_unscanned: bool = True, limit: int | None = None) -> dict:
    """Refreshes CVE data for every resolved product.
    """
    query = db.query(CPEMatch).filter(CPEMatch.cpe23_uri.isnot(None))
    if only_unscanned:
        cutoff = datetime.now(timezone.utc) - CACHE_TTL
        query = query.filter(
            or_(CPEMatch.cve_scanned_at.is_(None), CPEMatch.cve_scanned_at < cutoff)
        )

    query = query.order_by(
        case((CPEMatch.cve_scanned_at.is_(None), 0), else_=1),
        CPEMatch.cve_scanned_at.asc(),
    )
    if limit is not None:
        query = query.limit(limit)

    matches = query.all()

    scanned = 0
    total_findings = 0
    unavailable = 0
    for match in matches:
        try:
            findings = fetch_for_match(match)
        except NVDUnavailable:

            unavailable += 1
            continue

        db.query(CVEFinding).filter(CVEFinding.software_key == match.software_key).delete()
        for item in findings:
            db.add(CVEFinding(software_key=match.software_key, **item))

        match.cve_scanned_at = datetime.now(timezone.utc)
        db.commit()

        scanned += 1
        total_findings += len(findings)

    return {
        "products_scanned": scanned,
        "cves_found": total_findings,
        "products_unavailable": unavailable,
    }


def stale_product_count(db: Session) -> int:
    """Returns the number of resolved products the cache can no longer answer for.
    """
    cutoff = datetime.now(timezone.utc) - CACHE_TTL
    return (
        db.query(CPEMatch)
        .filter(CPEMatch.cpe23_uri.isnot(None))
        .filter(or_(CPEMatch.cve_scanned_at.is_(None), CPEMatch.cve_scanned_at < cutoff))
        .count()
    )
