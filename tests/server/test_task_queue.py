"""Dequeuing work, and keeping the queue readable afterwards."""

import pytest
from api.models import TERMINAL_TASK_STATUSES, TaskQueue


@pytest.fixture
def fleet(enrol):
    enrol("t1", ring="Test-Ring")
    enrol("t2", ring="Test-Ring")
    enrol("p1", ring="Production-Ring")
    return ["t1", "t2", "p1"]


def cancel(client, admin, **selector):
    return client.post("/api/v1/tasks/cancel", json=selector, headers=admin)


class TestSelectors:
    def test_no_selector_is_refused(self, client, admin):
        assert cancel(client, admin).status_code == 422

    def test_two_selectors_are_refused(self, client, admin):
        assert cancel(client, admin, device_id="t1", all_pending=True).status_code == 422

    def test_an_unknown_ring_is_refused(self, client, admin):
        assert cancel(client, admin, ring_name="Nope").status_code == 400

    def test_by_id(self, client, admin, fleet, queue_task, db):
        task_id = queue_task("t1")
        assert cancel(client, admin, task_ids=[task_id]).json()["cancelled"] == 1
        assert db.query(TaskQueue).filter_by(id=task_id).one().status == "CANCELLED"

    def test_by_device(self, client, admin, fleet, queue_task, db):
        queue_task("t1")
        queue_task("t1")
        kept = queue_task("t2")

        assert cancel(client, admin, device_id="t1").json()["cancelled"] == 2
        assert db.query(TaskQueue).filter_by(id=kept).one().status == "PENDING"

    def test_by_ring(self, client, admin, fleet, queue_task, db):
        queue_task("t1")
        kept = queue_task("p1")

        assert cancel(client, admin, ring_name="Test-Ring").json()["cancelled"] == 1
        assert db.query(TaskQueue).filter_by(id=kept).one().status == "PENDING"

    def test_the_whole_queue(self, client, admin, fleet, queue_task):
        for device in fleet:
            queue_task(device)
        assert cancel(client, admin, all_pending=True).json()["cancelled"] == 3


class TestWhatCanBeCancelled:
    def test_a_task_that_already_ran_is_counted_as_skipped(
        self, client, admin, fleet, queue_task, enrol
    ):
        """"Cancelled 3 of 5" is the answer an operator needs"""
        _, auth = enrol("t1")
        task_id = queue_task("t1")
        client.post("/api/v1/agent/task/result", headers=auth, json={
            "device_id": "t1", "task_id": task_id, "status": "SUCCESS", "output": "done",
        })

        body = cancel(client, admin, task_ids=[task_id]).json()

        assert (body["cancelled"], body["skipped_not_pending"]) == (0, 1)

    def test_a_mixed_selection_reports_both_counts(self, client, admin, fleet, queue_task, enrol):
        _, auth = enrol("t1")
        done = queue_task("t1")
        client.post("/api/v1/agent/task/result", headers=auth, json={
            "device_id": "t1", "task_id": done, "status": "SUCCESS", "output": "x",
        })
        pending = [queue_task("t1"), queue_task("t1")]

        body = cancel(client, admin, task_ids=[done] + pending).json()

        assert (body["cancelled"], body["skipped_not_pending"]) == (2, 1)

    def test_cancelling_twice_is_a_no_op_not_a_lie(self, client, admin, fleet, queue_task):
        task_id = queue_task("t1")
        cancel(client, admin, task_ids=[task_id])

        body = cancel(client, admin, task_ids=[task_id]).json()

        assert (body["cancelled"], body["skipped_not_pending"]) == (0, 1)

    def test_the_reason_survives_in_the_history(self, client, admin, fleet, queue_task, db):
        task_id = queue_task("t1")
        cancel(client, admin, task_ids=[task_id])

        assert "Cancelled" in db.query(TaskQueue).filter_by(id=task_id).one().output

    def test_cancelled_work_is_never_handed_out(self, client, admin, fleet, queue_task,
                                                heartbeat, enrol):
        _, auth = enrol("t1")
        task_id = queue_task("t1")
        cancel(client, admin, task_ids=[task_id])

        assert heartbeat("t1", auth).json()["pending_tasks"] == []


def test_a_result_for_a_cancelled_task_is_still_recorded(
    client, admin, enrol, queue_task, heartbeat, db
):
    device_id, auth = enrol("t1")
    task_id = queue_task(device_id)
    heartbeat(device_id, auth)                      # delivered
    cancel(client, admin, task_ids=[task_id])       # too late

    response = client.post("/api/v1/agent/task/result", headers=auth, json={
        "device_id": device_id, "task_id": task_id, "status": "SUCCESS", "output": "patched",
    })

    assert response.json()["cancelled_too_late"] is True
    task = db.query(TaskQueue).filter_by(id=task_id).one()
    assert task.status == "SUCCESS"
    assert "cancelled" in task.output and "patched" in task.output


def test_a_cancelled_task_is_prunable_but_a_pending_one_never_is():
    assert "CANCELLED" in TERMINAL_TASK_STATUSES
    assert "PENDING" not in TERMINAL_TASK_STATUSES


class TestHidingCancelledTasks:

    def test_they_are_excluded_from_the_queue_by_default(self, client, admin, fleet, queue_task):
        live = [queue_task("t1") for _ in range(3)]
        doomed = [queue_task("t1") for _ in range(4)]
        cancel(client, admin, task_ids=doomed)

        body = client.get("/api/v1/dashboard/tasks", headers=admin).json()

        assert {t["id"] for t in body["items"]} == set(live)
        assert body["total"] == 3

    def test_hidden_is_not_the_same_as_invisible(self, client, admin, fleet, queue_task):
        cancel(client, admin, task_ids=[queue_task("t1"), queue_task("t1")])

        assert client.get(
            "/api/v1/dashboard/tasks", headers=admin
        ).json()["cancelled_hidden"] == 2

    def test_they_can_be_asked_for(self, client, admin, fleet, queue_task):
        cancel(client, admin, task_ids=[queue_task("t1"), queue_task("t1")])

        body = client.get(
            "/api/v1/dashboard/tasks", params={"include_cancelled": True}, headers=admin
        ).json()

        assert len(body["items"]) == 2 and body["cancelled_hidden"] == 0

    def test_asking_by_status_implies_including_them(self, client, admin, fleet, queue_task):
        cancel(client, admin, task_ids=[queue_task("t1"), queue_task("t1")])

        body = client.get(
            "/api/v1/dashboard/tasks", params={"status": "CANCELLED"}, headers=admin
        ).json()

        assert len(body["items"]) == 2

    def test_a_page_is_not_crowded_out_by_cancelled_rows(self, client, admin, fleet, queue_task):
        live = [queue_task("t1") for _ in range(3)]
        for _ in range(12):
            cancel(client, admin, task_ids=[queue_task("t1")])

        body = client.get("/api/v1/dashboard/tasks", params={"limit": 5}, headers=admin).json()

        assert {t["id"] for t in body["items"]} == set(live)

    def test_the_per_device_history_hides_them_too(self, client, admin, fleet, queue_task):
        queue_task("t1")
        cancel(client, admin, task_ids=[queue_task("t1")])

        visible = client.get("/api/v1/dashboard/endpoints/t1/tasks", headers=admin).json()
        everything = client.get(
            "/api/v1/dashboard/endpoints/t1/tasks",
            params={"include_cancelled": True}, headers=admin,
        ).json()

        assert len(visible) == 1 and len(everything) == 2

    def test_the_rows_are_kept_not_deleted(self, client, admin, fleet, queue_task, db):
        cancel(client, admin, task_ids=[queue_task("t1")])
        assert db.query(TaskQueue).count() == 1

    def test_the_pending_count_is_unaffected(self, client, admin, fleet, queue_task):
        queue_task("t1")
        cancel(client, admin, task_ids=[queue_task("t1")])

        assert client.get("/api/v1/dashboard/summary", headers=admin).json()["pending_tasks"] == 1


class TestFleetTaskList:
    def test_it_serves_the_whole_fleet_in_one_call(self, client, admin, fleet, queue_task):
        for device in fleet:
            queue_task(device)

        body = client.get("/api/v1/dashboard/tasks", headers=admin).json()

        assert body["total"] == 3
        assert {t["device_id"] for t in body["items"]} == set(fleet)

    def test_each_row_carries_its_hostname(self, client, admin, fleet, queue_task):
        """Which is the only reason the page was iterating endpoints."""
        queue_task("t1")
        row = client.get("/api/v1/dashboard/tasks", headers=admin).json()["items"][0]
        assert row["hostname"] == "T1"

    def test_it_can_be_filtered_to_one_device(self, client, admin, fleet, queue_task):
        queue_task("t1")
        queue_task("p1")

        body = client.get(
            "/api/v1/dashboard/tasks", params={"device_id": "t1"}, headers=admin
        ).json()

        assert body["total"] == 1

    def test_it_is_paged(self, client, admin, fleet, queue_task):
        for _ in range(5):
            queue_task("t1")

        body = client.get("/api/v1/dashboard/tasks", params={"limit": 2}, headers=admin).json()

        assert len(body["items"]) == 2 and body["total"] == 5
