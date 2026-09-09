"""The fleet-wide vulnerability and lifecycle rollup.
"""

from datetime import datetime, timezone
import api.routers.dashboard as dashboard
import pytest
from api.models import CPEMatch, CVEFinding, Endpoint, SoftwareInventory

NOW = datetime.now(timezone.utc)

CYCLES = {
    "python": [
        {"cycle": "3.13", "eol": False, "support": "2029-10-01"},
        {"cycle": "3.7", "eol": "2023-06-27", "support": "2020-06-27"},
    ],
    "nodejs": [{"cycle": "18", "eol": "2099-04-30", "support": "2099-01-01"}],
}


@pytest.fixture
def eol(monkeypatch):
    """Serve endoflife.date from a fixture instead of the network."""
    import api.services.lifecycle as lifecycle

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        if url.endswith("all.json"):
            return Response(list(CYCLES))
        slug = url.rsplit("/", 1)[-1].replace(".json", "")
        return Response(CYCLES.get(slug, []))

    monkeypatch.setattr(lifecycle.requests, "get", fake_get)
    return calls


@pytest.fixture
def fleet(db):
    """Two machines. dev1 runs an end-of-life Python and a supported Node;
    dev2 runs the same old Python plus a current one."""
    db.add_all([
        Endpoint(device_id="dev1", hostname="PC-1", last_seen=NOW),
        Endpoint(device_id="dev2", hostname="PC-2", last_seen=NOW),
        SoftwareInventory(device_id="dev1", name="Python 3.7.9 (64-bit)", version="3.7.9"),
        SoftwareInventory(device_id="dev1", name="Node.js", version="18.19.0"),
        SoftwareInventory(device_id="dev2", name="Python 3.7.9 (64-bit)", version="3.7.9"),
        SoftwareInventory(device_id="dev2", name="Python 3.13.1", version="3.13.1"),
        SoftwareInventory(device_id="dev2", name="Python Launcher", version="3.13.1"),
        CPEMatch(software_key="python", raw_name="Python 3.7.9 (64-bit)", vendor="python",
                 product="python", cpe23_uri="cpe:2.3:a:python:python:3.7.9:*:*:*:*:*:*:*",
                 confidence="exact_version", resolved_at=NOW, cve_scanned_at=NOW),
        # A second inventory name resolving to the same product.
        CPEMatch(software_key="python launcher", raw_name="Python Launcher", vendor="python",
                 product="python", cpe23_uri="cpe:2.3:a:python:python:3.13.1:*:*:*:*:*:*:*",
                 confidence="exact_version", resolved_at=NOW, cve_scanned_at=NOW),
        CPEMatch(software_key="node js", raw_name="Node.js", vendor="nodejs",
                 product="node.js", cpe23_uri="cpe:2.3:a:nodejs:node.js:18.19.0:*:*:*:*:*:*:*",
                 confidence="exact_version", resolved_at=NOW, cve_scanned_at=NOW),
        CVEFinding(software_key="python", cve_id="CVE-2023-1", severity="CRITICAL",
                   score=9.8, match_mode="version"),
        CVEFinding(software_key="python", cve_id="CVE-2023-2", severity="LOW",
                   score=2.0, match_mode="version"),
        # Deliberately duplicated across keys: it must be counted once.
        CVEFinding(software_key="python launcher", cve_id="CVE-2023-2", severity="LOW",
                   score=2.0, match_mode="version"),
        CVEFinding(software_key="python launcher", cve_id="CVE-2024-9", severity="HIGH",
                   score=7.5, match_mode="version"),
    ])
    db.commit()


@pytest.fixture
def products(db, fleet, eol):
    return {p["matched_product"]: p for p in dashboard.get_findings(db)}


class TestAggregation:
    def test_one_row_per_product(self, products):
        assert set(products) == {"python", "node.js"}

    def test_a_machine_carrying_two_builds_is_counted_once(self, products):
        assert products["python"]["endpoints"] == 2

    def test_installed_versions_are_listed(self, products):
        assert products["python"]["installed"] == "3.13.1, 3.7.9"

    def test_the_product_label_is_stable(self, products):
        """Alphabetically first, so it does not depend on row order."""
        assert products["python"]["software"] == "Python 3.13.1"


class TestLifecycle:
    def test_a_product_reports_its_worst_installed_build(self, products):
        """A supported build must not mask an unsupported one on the same
        product."""
        assert products["python"]["is_eol"] == "2023-06-27"
        assert products["python"]["lifecycle_cycle"] == "3.7"

    def test_a_scheduled_end_of_support_is_reported_as_such(self, products):
        assert products["node.js"]["is_eol"] == "2099-04-30"


class TestVulnerabilities:
    def test_cves_are_aggregated_across_every_name_for_a_product(self, products):
        assert products["python"]["cve_count"] == 3

    def test_duplicates_across_names_are_counted_once(self, products):
        assert "CVE-2023-2" in products["python"]["top_cves"]
        assert products["python"]["top_cves"].count("CVE-2023-2") == 1

    def test_the_worst_severity_wins(self, products):
        assert products["python"]["max_severity"] == "CRITICAL"
        assert products["python"]["max_score"] == 9.8

    def test_worst_cves_are_listed_first(self, products):
        assert products["python"]["top_cves"] == "CVE-2023-1, CVE-2024-9, CVE-2023-2"

    def test_a_scanned_product_with_no_findings_is_not_unscanned(self, products):
        assert products["node.js"]["cve_scanned"] is True
        assert products["node.js"]["cve_count"] == 0


class TestSummary:
    def test_endpoints_at_risk_are_counted_by_machine(self, client, admin, fleet, eol):
        body = client.get("/api/v1/dashboard/summary", headers=admin).json()

        assert body["eol_endpoints"] == 2
        assert body["critical_endpoints"] == 2

    def test_online_is_derived_from_recency_not_a_stored_flag(self, client, admin, fleet, eol):
        """Endpoint.status is set on heartbeat and never flipped back."""
        body = client.get("/api/v1/dashboard/summary", headers=admin).json()
        assert body["total_endpoints"] == 2 and body["online"] == 2

    def test_an_empty_fleet_does_not_fall_over(self, client, admin, eol):
        body = client.get("/api/v1/dashboard/summary", headers=admin).json()
        assert body["total_endpoints"] == 0 and body["eol_endpoints"] == 0


class TestCaching:
    def test_the_rollup_is_reused_between_calls(self, db, fleet, eol):
        dashboard.get_rollup(db)
        before = len(eol)
        dashboard.get_rollup(db)
        assert len(eol) == before

    def test_it_can_be_invalidated(self, db, fleet, eol):
        first = dashboard.get_rollup(db)
        dashboard.invalidate_rollup()
        assert dashboard.get_rollup(db) is not first


class TestWhoIsAtRisk:
    """The summary counts endpoints
    """

    def test_it_names_the_endpoints_behind_the_count(self, client, admin, fleet, eol):
        body = client.get(
            "/api/v1/dashboard/at-risk", params={"level": "eol"}, headers=admin
        ).json()

        assert body["device_ids"] == ["dev1", "dev2"]

    def test_the_list_and_the_count_agree(self, client, admin, fleet, eol):
        summary = client.get("/api/v1/dashboard/summary", headers=admin).json()

        for level, count in (("eol", "eol_endpoints"), ("critical", "critical_endpoints")):
            listed = client.get(
                "/api/v1/dashboard/at-risk", params={"level": level}, headers=admin
            ).json()["device_ids"]
            assert len(listed) == summary[count], level

    def test_an_endpoint_appears_once_however_many_bad_builds_it_carries(
        self, client, admin, fleet, eol, db
    ):
        """dev2 carries the end-of-life Python under two inventory names."""
        body = client.get(
            "/api/v1/dashboard/at-risk", params={"level": "eol"}, headers=admin
        ).json()

        assert body["device_ids"].count("dev2") == 1

    def test_an_empty_fleet_answers_with_an_empty_list(self, client, admin, eol):
        body = client.get(
            "/api/v1/dashboard/at-risk", params={"level": "critical"}, headers=admin
        ).json()

        assert body["device_ids"] == []

    def test_only_the_two_levels_are_accepted(self, client, admin, eol):
        assert client.get(
            "/api/v1/dashboard/at-risk", params={"level": "everything"}, headers=admin
        ).status_code == 422

    def test_it_is_an_operator_route(self, client, enrol):
        """It names machines and what is wrong with them."""
        _, auth = enrol("dev-1")

        assert client.get(
            "/api/v1/dashboard/at-risk", params={"level": "eol"}
        ).status_code == 401
        assert client.get(
            "/api/v1/dashboard/at-risk", params={"level": "eol"}, headers=auth
        ).status_code == 403
