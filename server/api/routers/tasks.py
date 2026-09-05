"""Bulk task orchestration across deployment rings.
"""

from api.models import TASK_CANCELLED, TASK_PENDING, Endpoint, TaskQueue
from api.schemas import (
    RingRemediationPayload,
    RingRemediationResponse,
    RingRevertPayload,
    TaskCancelPayload,
    TaskCancelResponse,
)
from api.services.admin_auth import require_admin
from config import DEPLOYMENT_RINGS
from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

router = APIRouter(
    prefix="/api/v1/tasks", tags=["Tasks"], dependencies=[Depends(require_admin)]
)

RING_REMEDIATION_ACTION = "UPDATE_WINGET"
RING_REVERT_ACTION = "ROLLBACK"


@router.post("/remediate/ring", response_model=RingRemediationResponse)
def remediate_ring(payload: RingRemediationPayload, db: Session = Depends(get_db)):
    """Queues a winget update for every endpoint in one deployment ring.
    """
    if payload.ring_name not in DEPLOYMENT_RINGS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown ring {payload.ring_name!r}. Valid rings: {DEPLOYMENT_RINGS}",
        )

    endpoints = db.query(Endpoint).filter(Endpoint.deployment_ring == payload.ring_name).all()
    if not endpoints:
        raise HTTPException(
            status_code=404, detail=f"No endpoints are assigned to {payload.ring_name}"
        )

    tasks = [
        TaskQueue(
            device_id=e.device_id,
            action=RING_REMEDIATION_ACTION,
            target=payload.software_name,
            status="PENDING",
        )
        for e in endpoints
    ]
    db.add_all(tasks)
    db.commit()

    return RingRemediationResponse(
        ring_name=payload.ring_name,
        software_name=payload.software_name,
        action=RING_REMEDIATION_ACTION,
        endpoints_targeted=len(tasks),
        task_ids=[t.id for t in tasks],
    )


@router.post("/cancel", response_model=TaskCancelResponse)
def cancel_tasks(payload: TaskCancelPayload, db: Session = Depends(get_db)):
    """Dequeues tasks that have not run yet.
    """
    query = db.query(TaskQueue)

    if payload.task_ids is not None:
        query = query.filter(TaskQueue.id.in_(payload.task_ids))
    elif payload.device_id is not None:
        query = query.filter(TaskQueue.device_id == payload.device_id)
    elif payload.ring_name is not None:
        if payload.ring_name not in DEPLOYMENT_RINGS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown ring {payload.ring_name!r}. Valid rings: {DEPLOYMENT_RINGS}",
            )
        ring_devices = db.query(Endpoint.device_id).filter(
            Endpoint.deployment_ring == payload.ring_name
        )
        query = query.filter(TaskQueue.device_id.in_(ring_devices))

    matched = query.all()
    pending = [t for t in matched if t.status == TASK_PENDING]

    for task in pending:
        task.status = TASK_CANCELLED

        task.output = "Cancelled from the dashboard before the agent ran it."

    db.commit()

    return TaskCancelResponse(
        cancelled=len(pending),
        task_ids=[t.id for t in pending],
        skipped_not_pending=len(matched) - len(pending),
    )


@router.post("/revert/ring", response_model=RingRemediationResponse)
def revert_ring(payload: RingRevertPayload, db: Session = Depends(get_db)):
    """Reverts every endpoint in one ring to its pre-update checkpoint.
    """
    if payload.ring_name not in DEPLOYMENT_RINGS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown ring {payload.ring_name!r}. Valid rings: {DEPLOYMENT_RINGS}",
        )
    if payload.max_age_hours < 1:
        raise HTTPException(status_code=400, detail="max_age_hours must be at least 1")

    endpoints = db.query(Endpoint).filter(Endpoint.deployment_ring == payload.ring_name).all()
    if not endpoints:
        raise HTTPException(
            status_code=404, detail=f"No endpoints are assigned to {payload.ring_name}"
        )

    tasks = [
        TaskQueue(
            device_id=e.device_id,
            action=RING_REVERT_ACTION,
            target=str(payload.max_age_hours),
            status=TASK_PENDING,
        )
        for e in endpoints
    ]
    db.add_all(tasks)
    db.commit()

    return RingRemediationResponse(
        ring_name=payload.ring_name,
        software_name=f"restore point within {payload.max_age_hours}h",
        action=RING_REVERT_ACTION,
        endpoints_targeted=len(tasks),
        task_ids=[t.id for t in tasks],
    )
