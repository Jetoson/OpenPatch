from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from api.schemas import HeartbeatPayload, TaskResultPayload
from api.models import Endpoint, TaskQueue
from database import get_db

router = APIRouter(prefix="/api/v1/agent", tags=["Agent"])

@router.post("/heartbeat")
def receive_heartbeat(payload: HeartbeatPayload, db: Session = Depends(get_db)):
    endpoint = db.query(Endpoint).filter(Endpoint.device_id == payload.device_id).first()

    if not endpoint:
        endpoint = Endpoint(
            device_id=payload.device_id,
            hostname=payload.hostname,
            os_version=payload.os_version,
            cpu_usage_percent=payload.cpu_usage_percent,
            ram_usage_percent=payload.ram_usage_percent
        )
        db.add(endpoint)
    else:
        endpoint.hostname = payload.hostname
        endpoint.os_version = payload.os_version
        endpoint.cpu_usage_percent = payload.cpu_usage_percent
        endpoint.ram_usage_percent = payload.ram_usage_percent
        endpoint.last_seen = datetime.now(timezone.utc)
        endpoint.status = "ONLINE"

    db.commit()

    pending_tasks = db.query(TaskQueue).filter(
        TaskQueue.device_id == payload.device_id,
        TaskQueue.status == "PENDING"
    ).all()

    tasks_to_send = [{"task_id": t.id, "action": t.action} for t in pending_tasks]

    return {"status": "acknowledged", "pending_tasks": tasks_to_send}


@router.post("/{device_id}/queue_task")
def queue_task(device_id: str, action: str, db: Session = Depends(get_db)):
    endpoint = db.query(Endpoint).filter(Endpoint.device_id == device_id).first()
    if not endpoint:
        return {"error": "Device not found"}

    new_task = TaskQueue(device_id=device_id, action=action, status="PENDING")
    db.add(new_task)
    db.commit()
    return {"status": "Task Queued", "task_id": new_task.id}


@router.post("/task/result")
def report_task_result(payload: TaskResultPayload, db: Session = Depends(get_db)):
    task = db.query(TaskQueue).filter(TaskQueue.id == payload.task_id).first()

    if task:
        task.status = payload.status
        db.commit()
        return {"status": "Task state updated"}

    return {"error": "Task not found"}
