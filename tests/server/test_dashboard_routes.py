"""The read API behind the dashboard: paging, filtering, retention."""

from datetime import datetime, timedelta, timezone

import pytest
from api.models import (
    Endpoint,
    PendingUpdate,
    SoftwareInventory,
    TaskQueue,
    TelemetryHistory,
)

NOW = datetime.now(timezone.utc)


@pytest.fixture
def fleet(db):
    for i in range(12):
        device = f"dev{i}"
        db.add(Endpoint(
            device_id=device, hostname=f"WS-{i:02d}",
            department="IT" if i % 2 else "Finance",
            deployment_ring="Test-Ring" if i % 3 else "Production-Ring",
            os_version="10.0.26100", os_name="Windows 11 Pro",
            last_seen=NOW if i < 8 else NOW - timedelta(hours=2),
        ))
        db.add(SoftwareInventory(device_id=device, name="Acme Reader", version="9.1"))
        db.add(PendingUpdate(device_id=device, source="winget", name="Acme", kb="Acme.App"))
        db.add(PendingUpdate(device_id=device, source="windows", name="Cumulative", kb="KB1"))
    db.commit()


class TestFleetList:
    def test_it_is_paged(self, client, admin, fleet):
        body = client.get("/api/v1/dashboard/endpoints", params={"limit": 5}, headers=admin).json()

        assert len(body["items"]) == 5
        assert body["total"] == 12

    def test_paging_walks_the_whole_fleet(self, client, admin, fleet):
        seen = []
        for offset in range(0, 12, 5):
            page = client.get("/api/v1/dashboard/endpoints",
                              params={"limit": 5, "offset": offset}, headers=admin).json()
            seen += [row["device_id"] for row in page["items"]]

        assert len(set(seen)) == 12

    def test_counts_are_annotated_per_endpoint(self, client, admin, fleet):
        row = client.get("/api/v1/dashboard/endpoints", headers=admin).json()["items"][0]

        assert row["software_count"] == 1
        assert row["windows_updates"] == 1
        assert row["third_party_updates"] == 1

    def test_the_configured_verification_settings_are_exposed(self, client, admin, enrol):
        device_id, _ = enrol("dev-1")
        client.patch(f"/api/v1/agent/{device_id}/verification", json={
            "verify_command": "Get-Process notepad -ErrorAction Stop",
            "critical_programs": "notepad, chrome",
        }, headers=admin)

        row = client.get("/api/v1/dashboard/endpoints", headers=admin).json()["items"][0]

        assert row["verify_command"] == "Get-Process notepad -ErrorAction Stop"
        assert row["critical_programs"] == "notepad, chrome"

    @pytest.mark.parametrize("field,value,expected", [
        ("department", "IT", 6),
        ("ring", "Test-Ring", 8),
        ("online", True, 8),
        ("online", False, 4),
    ])
    def test_it_can_be_filtered(self, client, admin, fleet, field, value, expected):
        body = client.get(
            "/api/v1/dashboard/endpoints", params={field: value}, headers=admin
        ).json()
        assert body["total"] == expected

    def test_it_can_be_searched_by_hostname(self, client, admin, fleet):
        body = client.get(
            "/api/v1/dashboard/endpoints", params={"search": "WS-03"}, headers=admin
        ).json()
        assert body["total"] == 1

    def test_the_page_size_is_capped(self, client, admin, fleet):
        from config import MAX_PAGE_SIZE

        body = client.get(
            "/api/v1/dashboard/endpoints", params={"limit": MAX_PAGE_SIZE * 10}, headers=admin
        ).json()
        assert body["limit"] == MAX_PAGE_SIZE

    def test_departments_are_listed_without_paging_the_fleet(self, client, admin, fleet):
        body = client.get("/api/v1/dashboard/departments", headers=admin).json()
        assert body["departments"] == ["Finance", "IT"]


class TestOnlineness:
    def test_it_is_derived_from_recency(self, client, admin, fleet):
        body = client.get("/api/v1/dashboard/summary", headers=admin).json()

        assert body["online"] == 8
        assert body["offline"] == 4

    def test_a_row_with_no_last_seen_is_offline(self, client, admin, db):
        from sqlalchemy import text

        db.add(Endpoint(device_id="never", hostname="NEVER"))
        db.commit()
        db.execute(text("UPDATE endpoints SET last_seen = NULL WHERE device_id = 'never'"))
        db.commit()

        assert client.get("/api/v1/dashboard/summary", headers=admin).json()["online"] == 0

    def test_the_recency_check_handles_a_missing_timestamp(self):
        import api.routers.dashboard as dashboard

        assert dashboard._is_online(None) is False


class TestTelemetryHistory:
    def test_samples_come_back_oldest_first_for_charting(self, client, admin, db):
        db.add(Endpoint(device_id="dev1", hostname="WS", last_seen=NOW))
        for minutes in range(5):
            db.add(TelemetryHistory(device_id="dev1", cpu_usage=float(minutes), ram_usage=1.0,
                                    recorded_at=NOW - timedelta(minutes=minutes)))
        db.commit()

        rows = client.get("/api/v1/dashboard/endpoints/dev1/telemetry", headers=admin).json()

        assert [r["cpu_usage"] for r in rows] == [4.0, 3.0, 2.0, 1.0, 0.0]

    def test_only_the_most_recent_are_returned(self, client, admin, db):
        db.add(Endpoint(device_id="dev1", hostname="WS", last_seen=NOW))
        for minutes in range(10):
            db.add(TelemetryHistory(device_id="dev1", cpu_usage=1.0, ram_usage=1.0,
                                    recorded_at=NOW - timedelta(minutes=minutes)))
        db.commit()

        rows = client.get("/api/v1/dashboard/endpoints/dev1/telemetry",
                          params={"limit": 3}, headers=admin).json()

        assert len(rows) == 3


class TestRetention:
    def test_old_telemetry_is_pruned(self, db, monkeypatch):
        import api.services.maintenance as maintenance

        monkeypatch.setattr(maintenance, "TELEMETRY_RETENTION_DAYS", 7)
        db.add(Endpoint(device_id="dev1", hostname="WS", last_seen=NOW))
        db.add(TelemetryHistory(device_id="dev1", cpu_usage=1.0, ram_usage=1.0,
                                recorded_at=NOW - timedelta(days=30)))
        db.add(TelemetryHistory(device_id="dev1", cpu_usage=1.0, ram_usage=1.0, recorded_at=NOW))
        db.commit()

        assert maintenance.prune_telemetry(db) == 1
        assert db.query(TelemetryHistory).count() == 1

    def test_finished_tasks_are_pruned_but_pending_ones_never_are(self, db, monkeypatch):
        import api.services.maintenance as maintenance

        monkeypatch.setattr(maintenance, "TASK_RETENTION_DAYS", 30)
        db.add(Endpoint(device_id="dev1", hostname="WS", last_seen=NOW))
        old = NOW - timedelta(days=90)
        db.add(TaskQueue(device_id="dev1", action="UPDATE_WINGET", status="SUCCESS",
                         created_at=old))
        db.add(TaskQueue(device_id="dev1", action="UPDATE_WINGET", status="CANCELLED",
                         created_at=old))
        db.add(TaskQueue(device_id="dev1", action="UPDATE_WINGET", status="PENDING",
                         created_at=old))
        db.commit()

        assert maintenance.prune_tasks(db) == 2
        assert db.query(TaskQueue).one().status == "PENDING"
