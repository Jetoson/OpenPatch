"""Background retention so that the database does not grow without bound.
"""

import threading
import time
from datetime import datetime, timedelta, timezone

from api.models import TERMINAL_TASK_STATUSES, TaskQueue, TelemetryHistory
from config import (
    MAINTENANCE_INTERVAL_SECONDS,
    TASK_RETENTION_DAYS,
    TELEMETRY_RETENTION_DAYS,
)
from database import SessionLocal
from sqlalchemy import select


_DELETE_BATCH = 5_000

def _delete_in_batches(db, model, condition) -> int:
    """Delete matching rows a batch at a time.
    """
    removed = 0
    while True:
        ids = db.execute(
            select(model.id).where(condition).limit(_DELETE_BATCH)
        ).scalars().all()
        if not ids:
            return removed
        db.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
        removed += len(ids)
        if len(ids) < _DELETE_BATCH:
            return removed


def prune_telemetry(db) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=TELEMETRY_RETENTION_DAYS)
    return _delete_in_batches(db, TelemetryHistory, TelemetryHistory.recorded_at < cutoff)


def prune_tasks(db) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=TASK_RETENTION_DAYS)
    return _delete_in_batches(
        db,
        TaskQueue,
        (TaskQueue.created_at < cutoff) & TaskQueue.status.in_(TERMINAL_TASK_STATUSES),
    )


def run_once() -> dict:
    db = SessionLocal()
    try:
        return {"telemetry_pruned": prune_telemetry(db), "tasks_pruned": prune_tasks(db)}
    finally:
        db.close()


def _loop() -> None:
    while True:
        time.sleep(MAINTENANCE_INTERVAL_SECONDS)
        try:
            result = run_once()
            if result["telemetry_pruned"] or result["tasks_pruned"]:
                print(
                    f"[*] Maintenance: pruned {result['telemetry_pruned']} telemetry "
                    f"and {result['tasks_pruned']} task row(s).",
                    flush=True,
                )
        except Exception as exc:
            print(f"[!] Maintenance run failed: {type(exc).__name__}: {exc}", flush=True)


def start_background_maintenance() -> bool:
    """Starts the retention loop and returns the status.
    """
    if MAINTENANCE_INTERVAL_SECONDS <= 0:
        return False
    threading.Thread(target=_loop, name="openpatch-maintenance", daemon=True).start()
    return True
