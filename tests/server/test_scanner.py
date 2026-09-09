"""The scheduled scan that keeps the vulnerability report current.
"""

from datetime import datetime, timedelta, timezone

import api.services.cpe_matcher as cpe_matcher
import api.services.cve_lookup as cve_lookup
import api.services.lifecycle as lifecycle
import api.services.nvd_client as nvd
import api.services.scanner as scanner
import pytest
from api.models import CPEMatch, CVEFinding, Endpoint, SoftwareInventory

NOW = datetime.now(timezone.utc)


@pytest.fixture
def upstream(monkeypatch):
    """Fake NVD and endoflife.date, counting the requests each would receive."""
    counts = {"cpe": 0, "cve": 0, "eol": 0}

    def fake_nvd(url, params, timeout=30):
        if "cpes" in url:
            counts["cpe"] += 1
            slug = params["keywordSearch"].lower().strip().replace(" ", "")
            return {"products": [
                {"cpe": {"cpeName": f"cpe:2.3:a:acme:{slug}:1.0:*:*:*:*:*:*:*"}}
            ]}
        counts["cve"] += 1
        return {"vulnerabilities": [{"cve": {
            "id": "CVE-2024-0001", "published": "2024-01-01",
            "descriptions": [{"lang": "en", "value": "test"}],
            "metrics": {"cvssMetricV31": [
                {"cvssData": {"baseSeverity": "HIGH", "baseScore": 7.5}}
            ]},
        }}]}

    class EmptyCycles:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    def fake_eol(url, timeout=None):
        counts["eol"] += 1
        return EmptyCycles()

    monkeypatch.setattr(nvd, "get_json", fake_nvd)
    monkeypatch.setattr(cpe_matcher, "get_json", fake_nvd)
    monkeypatch.setattr(cve_lookup, "get_json", fake_nvd)
    monkeypatch.setattr(lifecycle.requests, "get", fake_eol)
    return counts


@pytest.fixture
def inventory(db):
    db.add(Endpoint(device_id="dev1", hostname="PC-1", last_seen=NOW))
    for i in range(6):
        db.add(SoftwareInventory(device_id="dev1", name=f"Product {i}", version="1.0"))
    for noise in ("Python Documentation", "Python Test Suite", "Node.js Add to PATH"):
        db.add(SoftwareInventory(device_id="dev1", name=noise, version="1.0"))
    db.commit()


class TestNoise:
    @pytest.mark.parametrize("name", [
        "Python Documentation", "Python Test Suite", "Acme Development Libraries",
    ])
    def test_sub_components_are_skipped(self, name):
        assert scanner._is_noise(name)

    def test_a_real_product_is_not(self):
        assert not scanner._is_noise("Google Chrome")


class TestACycle:
    def test_it_identifies_the_fleets_software(self, db, inventory, upstream):
        result = scanner.run_once(db)
        assert result["identified"] == 6
        assert result["names_looked_up"] == 6

    def test_noise_never_reaches_nvd(self, db, inventory, upstream):
        scanner.run_once(db)
        assert upstream["cpe"] == 6

    def test_it_looks_up_cves_for_what_it_identified(self, db, inventory, upstream):
        assert scanner.run_once(db)["products_scanned"] == 6
        assert db.query(CVEFinding).count() == 6

    def test_it_warms_the_lifecycle_cache(self, db, inventory, upstream):
        assert scanner.run_once(db)["lifecycle_products"] > 0

    def test_a_second_cycle_costs_nothing(self, db, inventory, upstream):
        scanner.run_once(db)
        before = dict(upstream)

        result = scanner.run_once(db)

        assert upstream == before, "a quiet cycle must make no external requests"
        assert result["names_looked_up"] == 0

    def test_examined_and_looked_up_are_reported_separately(self, db, inventory, upstream):
        scanner.run_once(db)
        result = scanner.run_once(db)

        assert result["names_examined"] == 6
        assert result["names_looked_up"] == 0


class TestPickingUpChanges:
    def test_new_software_is_resolved_without_anyone_asking(self, db, inventory, upstream):
        scanner.run_once(db)
        db.add(SoftwareInventory(device_id="dev1", name="Newly Installed App", version="2.0"))
        db.commit()

        result = scanner.run_once(db)

        assert result["names_looked_up"] == 1
        assert result["newly_identified"] == 1

    def test_a_stale_product_is_rescanned(self, db, inventory, upstream):
        scanner.run_once(db)
        db.query(CPEMatch).filter_by(software_key="product 0").update(
            {"cve_scanned_at": NOW - timedelta(days=999)}
        )
        db.commit()

        assert scanner.run_once(db)["products_scanned"] == 1


class TestBounds:
    def test_a_cycle_is_capped(self, db, inventory, upstream):
        scanner.run_once(db)
        db.query(CPEMatch).update({"cve_scanned_at": None})
        db.commit()

        assert cve_lookup.scan(db, only_unscanned=True, limit=3)["products_scanned"] == 3

    def test_what_a_capped_cycle_leaves_is_what_the_next_one_starts_with(
        self, db, inventory, upstream
    ):
        scanner.run_once(db)
        db.query(CPEMatch).update({"cve_scanned_at": None})
        db.commit()

        cve_lookup.scan(db, only_unscanned=True, limit=3)

        assert cve_lookup.stale_product_count(db) == 3
        assert cve_lookup.scan(db, only_unscanned=True, limit=100)["products_scanned"] == 3
        assert cve_lookup.stale_product_count(db) == 0

    def test_the_cap_bounds_lookups_not_names_considered(self, db, inventory, upstream):
        scanner.run_once(db)

        result = scanner.resolve_inventory(db, limit=2)

        assert result["names_examined"] == 6 and result["names_looked_up"] == 0


class TestConcurrencyAndFailure:
    def test_a_second_scan_is_skipped_rather_than_queued(self, db, inventory, upstream):
        scanner._scan_lock.acquire()
        try:
            assert "skipped" in scanner.run_once(db)
        finally:
            scanner._scan_lock.release()

    def test_a_failure_is_recorded_not_raised(self, db, inventory, upstream, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("NVD exploded")

        monkeypatch.setattr(scanner, "resolve_inventory", boom)

        result = scanner.run_once(db)

        assert "NVD exploded" in result["error"]
        assert "NVD exploded" in scanner.status()["last_error"]

    def test_the_lock_is_released_after_a_failure(self, db, inventory, upstream, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("x")

        monkeypatch.setattr(scanner, "resolve_inventory", boom)
        scanner.run_once(db)

        assert scanner._scan_lock.acquire(blocking=False)
        scanner._scan_lock.release()


class TestScanStatus:
    def test_nothing_scanned_is_distinguishable_from_nothing_found(self, client, admin):
        body = client.get("/api/v1/dashboard/scan-status", headers=admin).json()

        assert body["products_scanned"] == 0
        assert body["last_scanned_at"] is None

    def test_coverage_is_reported_from_the_database(self, client, admin, db):
        db.add_all([
            CPEMatch(software_key="a", raw_name="A", product="a",
                     cpe23_uri="cpe:2.3:a:v:a:1:*:*:*:*:*:*:*",
                     resolved_at=NOW, cve_scanned_at=NOW),
            CPEMatch(software_key="b", raw_name="B", product="b",
                     cpe23_uri="cpe:2.3:a:v:b:1:*:*:*:*:*:*:*", resolved_at=NOW),
            CPEMatch(software_key="c", raw_name="C", resolved_at=NOW, confidence="none"),
        ])
        db.commit()

        body = client.get("/api/v1/dashboard/scan-status", headers=admin).json()

        assert body["products_resolved"] == 2
        assert body["products_scanned"] == 1
        assert body["products_pending"] == 1
        assert body["names_unidentified"] == 1

    def test_it_reports_whether_scanning_is_even_on(self, client, admin):
        body = client.get("/api/v1/dashboard/scan-status", headers=admin).json()
        assert body["enabled"] is False   # the suite disables it
        assert body["interval_seconds"] > 0


def test_the_manual_scan_routes_are_gone(client, admin):
    assert client.post("/api/v1/dashboard/cve-scan", headers=admin).status_code == 404
    assert client.get("/api/v1/agent/inventory/dev1/eol", headers=admin).status_code == 404
