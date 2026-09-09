"""Staged rollout across a ring, and the undo for it."""

import pytest


@pytest.fixture
def rings(enrol):
    enrol("t1", ring="Test-Ring")
    enrol("t2", ring="Test-Ring")
    enrol("p1", ring="Production-Ring")


def tasks_for(client, admin, device_id):
    return client.get(
        "/api/v1/dashboard/tasks", params={"device_id": device_id}, headers=admin
    ).json()["items"]


class TestRingRemediation:
    def test_it_queues_one_task_per_endpoint_in_the_ring(self, client, admin, rings):
        body = client.post("/api/v1/tasks/remediate/ring", headers=admin,
                           json={"ring_name": "Test-Ring", "software_name": "Acme.App"}).json()

        assert body["endpoints_targeted"] == 2
        assert body["action"] == "UPDATE_WINGET"

    def test_the_other_ring_is_untouched(self, client, admin, rings):
        """The whole point of staging: production must not move until someone
        says so."""
        client.post("/api/v1/tasks/remediate/ring", headers=admin,
                    json={"ring_name": "Test-Ring", "software_name": "Acme.App"})

        assert tasks_for(client, admin, "p1") == []

    def test_the_software_is_recorded_on_the_task(self, client, admin, rings):
        client.post("/api/v1/tasks/remediate/ring", headers=admin,
                    json={"ring_name": "Test-Ring", "software_name": "Acme.App"})

        assert tasks_for(client, admin, "t1")[0]["target"] == "Acme.App"

    def test_an_unknown_ring_is_refused(self, client, admin, rings):
        assert client.post("/api/v1/tasks/remediate/ring", headers=admin,
                           json={"ring_name": "Nope", "software_name": "x"}).status_code == 400

    def test_an_empty_ring_is_a_404_not_a_silent_success(self, client, admin):
        assert client.post("/api/v1/tasks/remediate/ring", headers=admin,
                           json={"ring_name": "Test-Ring", "software_name": "x"}
                           ).status_code == 404


class TestRingRevert:
    def test_it_queues_a_rollback_for_every_endpoint(self, client, admin, rings):
        body = client.post("/api/v1/tasks/revert/ring", headers=admin,
                           json={"ring_name": "Test-Ring", "max_age_hours": 12}).json()

        assert body["endpoints_targeted"] == 2
        assert body["action"] == "ROLLBACK"

    def test_the_restore_window_travels_with_the_task(self, client, admin, rings):
        """Only the operator knows when the bad patch went out."""
        client.post("/api/v1/tasks/revert/ring", headers=admin,
                    json={"ring_name": "Test-Ring", "max_age_hours": 12})

        assert tasks_for(client, admin, "t1")[0]["target"] == "12"

    def test_production_can_be_reverted_too(self, client, admin, rings):
        body = client.post("/api/v1/tasks/revert/ring", headers=admin,
                           json={"ring_name": "Production-Ring", "max_age_hours": 72}).json()

        assert body["endpoints_targeted"] == 1

    def test_only_the_named_ring_is_reverted(self, client, admin, rings):
        client.post("/api/v1/tasks/revert/ring", headers=admin,
                    json={"ring_name": "Test-Ring"})

        assert tasks_for(client, admin, "p1") == []

    def test_a_nonsense_window_is_refused(self, client, admin, rings):
        assert client.post("/api/v1/tasks/revert/ring", headers=admin,
                           json={"ring_name": "Test-Ring", "max_age_hours": 0}
                           ).status_code == 400

    def test_an_unknown_ring_is_refused(self, client, admin, rings):
        assert client.post("/api/v1/tasks/revert/ring", headers=admin,
                           json={"ring_name": "Nope"}).status_code == 400

    def test_a_queued_revert_can_still_be_dequeued(self, client, admin, rings):
        """It is an ordinary task, so the usual escape hatch applies."""
        client.post("/api/v1/tasks/revert/ring", headers=admin, json={"ring_name": "Test-Ring"})
        task_id = tasks_for(client, admin, "t1")[0]["id"]

        body = client.post(
            "/api/v1/tasks/cancel", json={"task_ids": [task_id]}, headers=admin
        ).json()

        assert body["cancelled"] == 1


def test_the_ring_list_is_served_from_one_place(client, admin):
    from config import DEFAULT_DEPLOYMENT_RING, DEPLOYMENT_RINGS

    body = client.get("/api/v1/dashboard/rings", headers=admin).json()

    assert body == {"rings": DEPLOYMENT_RINGS, "default": DEFAULT_DEPLOYMENT_RING}


def test_a_device_starts_in_the_least_risky_ring(client, enrol, admin):
    from config import DEFAULT_DEPLOYMENT_RING

    enrol("dev-1")
    row = client.get("/api/v1/dashboard/endpoints", headers=admin).json()["items"][0]

    assert row["deployment_ring"] == DEFAULT_DEPLOYMENT_RING
