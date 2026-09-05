from pydantic import BaseModel, model_validator


class RegisterPayload(BaseModel):
    device_id: str
    hostname: str | None = None
    os_version: str | None = None
    os_name: str | None = None
    department: str | None = None
    enrollment_secret: str | None = None

class RegisterResponse(BaseModel):
    device_id: str
    token: str

class HeartbeatPayload(BaseModel):
    device_id: str
    cpu_usage: float
    ram_usage: float

    hostname: str | None = None
    os_version: str | None = None
    os_name: str | None = None
    reboot_required: bool | None = None
    reboot_reasons: str | None = None
    ip_address: str | None = None

class TaskResultPayload(BaseModel):
    device_id: str
    task_id: int
    status: str
    output: str

class SoftwareItem(BaseModel):
    name: str
    version: str
    publisher: str | None = None

class SoftwareInventoryPayload(BaseModel):
    device_id: str
    software_list: list[SoftwareItem]

class PendingUpdateItem(BaseModel):
    source: str
    name: str
    kb: str | None = None
    severity: str | None = None
    current_version: str | None = None
    available_version: str | None = None

class PendingUpdatesPayload(BaseModel):
    device_id: str
    updates: list[PendingUpdateItem]

class RingUpdatePayload(BaseModel):
    """Admin reassigns an endpoint to a different deployment ring."""
    deployment_ring: str

class VerificationSettingsPayload(BaseModel):
    """Admin sets what verify workflow checks on this endpoint after a
    patch.
    """
    verify_command: str | None = None
    critical_programs: str | None = None

class RingRemediationPayload(BaseModel):
    """Queue a patch task across every endpoint in one ring."""
    ring_name: str
    software_name: str

class RingRevertPayload(BaseModel):
    """Revert every endpoint in one ring to its pre-update checkpoint."""
    ring_name: str
    max_age_hours: int = 6


class RingRemediationResponse(BaseModel):
    ring_name: str
    software_name: str
    action: str
    endpoints_targeted: int
    task_ids: list[int]

class TaskCancelPayload(BaseModel):
    """Which queued tasks to dequeue.
    """
    task_ids: list[int] | None = None
    device_id: str | None = None
    ring_name: str | None = None
    all_pending: bool = False

    @model_validator(mode="after")
    def _exactly_one_selector(self):
        chosen = [
            name for name, value in (
                ("task_ids", self.task_ids),
                ("device_id", self.device_id),
                ("ring_name", self.ring_name),
                ("all_pending", self.all_pending or None),
            ) if value
        ]
        if len(chosen) != 1:
            raise ValueError(
                "Pass exactly one of task_ids, device_id, ring_name or all_pending "
                f"(got: {chosen or 'none'})"
            )
        return self


class TaskCancelResponse(BaseModel):
    cancelled: int
    task_ids: list[int]
    skipped_not_pending: int
