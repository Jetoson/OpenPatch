from pydantic import BaseModel
from typing import Optional

class HeartbeatPayload(BaseModel):
    device_id: str
    hostname: str
    os_version: str
    cpu_usage_percent: float
    ram_usage_percent: float

class TaskResultPayload(BaseModel):
    task_id: int
    status: str
    log_output: Optional[str] = None