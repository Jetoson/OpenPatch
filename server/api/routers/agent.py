import hmac
from datetime import datetime, timezone

from api.models import (
    TASK_CANCELLED,
    TASK_PENDING,
    Endpoint,
    PendingUpdate,
    SoftwareInventory,
    TaskQueue,
    TelemetryHistory,
)
from api.schemas import (
    HeartbeatPayload,
    PendingUpdatesPayload,
    RegisterPayload,
    RegisterResponse,
    RingUpdatePayload,
    SoftwareInventoryPayload,
    TaskResultPayload,
    VerificationSettingsPayload,
)
from api.services import fleet_secrets
from api.services.admin_auth import require_admin
from api.services.auth import generate_token, hash_token
from api.services.task_signing import attach_task_signature
from config import (
    AGENT_POLL_INTERVAL_SECONDS,
    DEPLOYMENT_RINGS,
    TELEMETRY_SAMPLE_INTERVAL_SECONDS,
)
from database import get_db
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/v1/agent", tags=["Agent"])

# Resolved once at import
ENROLLMENT_SECRET = fleet_secrets.enrollment_secret()

# A list of actions what an operator may queue.
ALLOWED_ACTIONS = {
    "UPDATE_WINGET",
    "UPDATE_OS",
    "RESTART",
    "UPDATE_AND_VERIFY",
    "UPDATE_VERIFY_HEAL",
    # Revert to the checkpoint before patching.
    "ROLLBACK",
}

VERIFYING_ACTIONS = ("UPDATE_AND_VERIFY", "UPDATE_VERIFY_HEAL")


def _critical_programs_command(raw: str | None) -> str | None:
    """A PowerShell one-liner that fails if any of these processes are not
    running, generated from the plain & comma-separated list an operator introduces.
    """
    names = [n.strip() for n in (raw or "").split(",") if n.strip()]
    if not names:
        return None
    literals = ", ".join("'" + n.replace("'", "''") + "'" for n in names)
    return (
        "$names = @(" + literals + "); "
        "$missing = @($names | Where-Object { -not (Get-Process -Name $_ -ErrorAction SilentlyContinue) }); "
        "if ($missing.Count) { throw ('Not running: ' + ($missing -join ', ')) }"
    )


_bearer_scheme = HTTPBearer()


def get_current_endpoint(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> Endpoint:
    """Verifies the device's Bearer <token>.
     """
    endpoint = db.query(Endpoint).filter(Endpoint.token_hash == hash_token(credentials.credentials)).first()
    if not endpoint:
        raise HTTPException(status_code=401, detail="Invalid or unrecognized device token")
    return endpoint


def _set_if_reported(endpoint: Endpoint, field: str, value) -> None:
    """Overwrite a stored field only when the agent actually reported it.
    """
    if value is None:
        return
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return
    setattr(endpoint, field, value)


def _should_store_telemetry_sample(endpoint: Endpoint, now: datetime) -> bool:
    """Decides whether this heartbeat's telemetry is worth keeping as history.
    """
    last = endpoint.telemetry_recorded_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now - last).total_seconds() >= TELEMETRY_SAMPLE_INTERVAL_SECONDS


def _require_matching_device(current: Endpoint, device_id: str) -> None:
    """Enforces a given token to authenticate only its own original device id.
    """
    if current.device_id != device_id:
        raise HTTPException(status_code=403, detail="Token does not match device_id")


@router.post("/register", response_model=RegisterResponse)
def register_device(payload: RegisterPayload, db: Session = Depends(get_db)):
    """Enrolls a device and issues its API toke."""
    if ENROLLMENT_SECRET and not hmac.compare_digest(
        payload.enrollment_secret or "", ENROLLMENT_SECRET
    ):

        print(
            f"[!] Enrolment refused for {payload.device_id}: "
            + (
                "no enrolment secret was presented"
                if not payload.enrollment_secret
                else "the enrolment secret presented does not match this server's"
            ),
            flush=True,
        )
        raise HTTPException(status_code=401, detail="Invalid enrollment secret")

    endpoint = db.query(Endpoint).filter(Endpoint.device_id == payload.device_id).first()
    if not endpoint:
        endpoint = Endpoint(device_id=payload.device_id)
        db.add(endpoint)

    token = generate_token()
    endpoint.token_hash = hash_token(token)

    _set_if_reported(endpoint, "hostname", payload.hostname)
    _set_if_reported(endpoint, "os_version", payload.os_version)
    _set_if_reported(endpoint, "os_name", payload.os_name)
    _set_if_reported(endpoint, "department", payload.department)

    db.commit()

    return RegisterResponse(device_id=payload.device_id, token=token)


@router.post("/heartbeat")
def receive_heartbeat(
    payload: HeartbeatPayload,
    response: Response,
    db: Session = Depends(get_db),
    current: Endpoint = Depends(get_current_endpoint),
):
    _require_matching_device(current, payload.device_id)

    _set_if_reported(current, "hostname", payload.hostname)
    _set_if_reported(current, "os_version", payload.os_version)
    _set_if_reported(current, "os_name", payload.os_name)

    if payload.reboot_required is not None:
        current.reboot_required = payload.reboot_required
        current.reboot_reasons = payload.reboot_reasons
    now = datetime.now(timezone.utc)
    current.cpu_usage = payload.cpu_usage
    current.ram_usage = payload.ram_usage
    current.last_seen = now
    current.status = "ONLINE"

    if _should_store_telemetry_sample(current, now):
        db.add(TelemetryHistory(
            device_id=payload.device_id,
            cpu_usage=payload.cpu_usage,
            ram_usage=payload.ram_usage,
            recorded_at=now,
        ))
        current.telemetry_recorded_at = now

    db.commit()

    pending_tasks = db.query(TaskQueue).filter(
        TaskQueue.device_id == payload.device_id,
        TaskQueue.status == TASK_PENDING
    ).all()

    tasks_to_send = [
        {"task_id": t.id, "action": t.action, "target": t.target} for t in pending_tasks
    ]

    attach_task_signature(response, payload.device_id, tasks_to_send)

    return {
        "status": "acknowledged",
        "pending_tasks": tasks_to_send,
        "poll_interval": AGENT_POLL_INTERVAL_SECONDS,
    }


@router.post("/{device_id}/queue_task", dependencies=[Depends(require_admin)])
def queue_task(device_id: str, action: str, target: str | None = None, db: Session = Depends(get_db)):
    """Queues one task. `target` narrows the action to a single subject.
        Admin-authenticated
    """
    if action not in ALLOWED_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action {action!r}. Valid actions: {sorted(ALLOWED_ACTIONS)}",
        )

    endpoint = db.query(Endpoint).filter(Endpoint.device_id == device_id).first()
    if not endpoint:
        return {"error": "Device not found"}

    if action in VERIFYING_ACTIONS and not target:
        target = endpoint.verify_command or _critical_programs_command(endpoint.critical_programs)

    new_task = TaskQueue(device_id=device_id, action=action, target=target, status=TASK_PENDING)
    db.add(new_task)
    db.commit()
    return {"status": "Task Queued", "task_id": new_task.id}


@router.patch("/{device_id}/verification", dependencies=[Depends(require_admin)])
def set_verification_settings(
    device_id: str, payload: VerificationSettingsPayload, db: Session = Depends(get_db)
):
    """Sets what verify workflow checks on this endpoint."""
    endpoint = db.query(Endpoint).filter(Endpoint.device_id == device_id).first()
    if not endpoint:
        raise HTTPException(status_code=404, detail="Device not found")

    endpoint.verify_command = (payload.verify_command or "").strip() or None
    endpoint.critical_programs = (payload.critical_programs or "").strip() or None
    db.commit()
    return {
        "device_id": device_id,
        "verify_command": endpoint.verify_command,
        "critical_programs": endpoint.critical_programs,
    }


@router.patch("/{device_id}/ring", dependencies=[Depends(require_admin)])
def set_deployment_ring(
    device_id: str,
    payload: RingUpdatePayload,
    db: Session = Depends(get_db),
):
    """Moves an endpoint into a different deployment ring. This is an admin route"""
    if payload.deployment_ring not in DEPLOYMENT_RINGS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown ring {payload.deployment_ring!r}. Valid rings: {DEPLOYMENT_RINGS}",
        )

    endpoint = db.query(Endpoint).filter(Endpoint.device_id == device_id).first()
    if not endpoint:
        raise HTTPException(status_code=404, detail="Device not found")

    endpoint.deployment_ring = payload.deployment_ring
    db.commit()
    return {"device_id": device_id, "deployment_ring": endpoint.deployment_ring}


@router.get("/task/{task_id}/status")
def get_task_status(
    task_id: int,
    db: Session = Depends(get_db),
    current: Endpoint = Depends(get_current_endpoint),
):
    """Checks whether a task the agent already holds is still worth running.
    """
    task = db.query(TaskQueue).filter(
        TaskQueue.id == task_id,
        TaskQueue.device_id == current.device_id,
    ).first()

    if not task:
        raise HTTPException(status_code=404, detail="Task not found for this device")

    return {"task_id": task.id, "status": task.status}


@router.post("/task/result")
def report_task_result(
    payload: TaskResultPayload,
    db: Session = Depends(get_db),
    current: Endpoint = Depends(get_current_endpoint),
):
    _require_matching_device(current, payload.device_id)

    task = db.query(TaskQueue).filter(
        TaskQueue.id == payload.task_id,
        TaskQueue.device_id == payload.device_id,
    ).first()

    if task:
        raced = task.status == TASK_CANCELLED
        task.status = payload.status
        task.output = payload.output
        if raced:
            task.output = (
                "NOTE: this task was cancelled, but the agent had already started it "
                "and it ran to completion. The result below is what actually happened."
                "\n\n"
                + (payload.output or "")
            )
        db.commit()
        return {"status": "Task state updated", "cancelled_too_late": raced}

    return {"error": "Task not found"}


@router.post("/inventory")
def receive_software_inventory(
    payload: SoftwareInventoryPayload,
    db: Session = Depends(get_db),
    current: Endpoint = Depends(get_current_endpoint),
):
    """Receives full software inventory from an agent."""
    _require_matching_device(current, payload.device_id)

    db.query(SoftwareInventory).filter(
        SoftwareInventory.device_id == payload.device_id
    ).delete(synchronize_session=False)

    db.bulk_insert_mappings(
        SoftwareInventory,
        [
            {
                "device_id": payload.device_id,
                "name": item.name,
                "version": item.version,
                "publisher": item.publisher,
            }
            for item in payload.software_list
        ],
    )

    db.commit()
    return {"status": "Inventory updated", "count": len(payload.software_list)}


@router.post("/updates")
def receive_pending_updates(
    payload: PendingUpdatesPayload,
    db: Session = Depends(get_db),
    current: Endpoint = Depends(get_current_endpoint),
):
    """Replaces this device's pending-update rows with the latest scan."""
    _require_matching_device(current, payload.device_id)

    db.query(PendingUpdate).filter(
        PendingUpdate.device_id == payload.device_id
    ).delete(synchronize_session=False)

    collected_at = datetime.now(timezone.utc)
    db.bulk_insert_mappings(
        PendingUpdate,
        [
            {
                "device_id": payload.device_id,
                "source": item.source,
                "name": item.name,
                "kb": item.kb,
                "severity": item.severity,
                "current_version": item.current_version,
                "available_version": item.available_version,
                # Set explicitly: the bulk path bypasses the column default,
                # so leaving it out would store NULL on every row.
                "collected_at": collected_at,
            }
            for item in payload.updates
        ],
    )

    db.commit()
    return {"status": "Updates recorded", "count": len(payload.updates)}
