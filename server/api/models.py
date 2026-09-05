
from database import Base
from datetime import datetime, timezone
from sqlalchemy.orm import relationship
from config import DEFAULT_DEPLOYMENT_RING
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)



class Endpoint(Base):
    __tablename__ = "endpoints"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, unique=True, index=True)
    hostname = Column(String)
    os_version = Column(String)

    # Human label OS version
    os_name = Column(String, nullable=True)

    reboot_required = Column(Boolean, default=False)
    reboot_reasons = Column(String, nullable=True)

    # Always admin-assigned from the dashboard
    deployment_ring = Column(String, default=DEFAULT_DEPLOYMENT_RING, index=True)

    # Set by the admin during registration
    department = Column(String, nullable=True, index=True)

    verify_command = Column(String, nullable=True)
    critical_programs = Column(String, nullable=True)

    # SHA-256 hash of the enrollment token
    token_hash = Column(String, unique=True, index=True, nullable=True)

    # Most recent telemetry snapshot.
    cpu_usage = Column(Float, default=0.0)
    ram_usage = Column(Float, default=0.0)
    last_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    telemetry_recorded_at = Column(DateTime, nullable=True)

    status = Column(String, default="ONLINE")

    tasks = relationship("TaskQueue", back_populates="endpoint")
    telemetry_history = relationship("TelemetryHistory", back_populates="endpoint")


class TelemetryHistory(Base):
    __tablename__ = "telemetry_history"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, ForeignKey("endpoints.device_id"), index=True)
    cpu_usage = Column(Float)
    ram_usage = Column(Float)
    recorded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    endpoint = relationship("Endpoint", back_populates="telemetry_history")

    __table_args__ = (
        Index("ix_telemetry_device_recorded", "device_id", "recorded_at"),
    )


# Task lifecycle
TASK_PENDING = "PENDING"
TASK_CANCELLED = "CANCELLED"


TERMINAL_TASK_STATUSES = (
    "SUCCESS",
    "SUCCESS_VERIFIED",
    "SUCCESS_WORKFLOW_FAILED",
    "FAILED",
    "FAILED_AUTO_ROLLED_BACK",
    "FAILED_ROLLBACK_FAILED",
    TASK_CANCELLED,
)


class TaskQueue(Base):
    __tablename__ = "task_queue"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, ForeignKey("endpoints.device_id"), index=True)
    action = Column(String)
    target = Column(String, nullable=True)
    status = Column(String, default=TASK_PENDING, index=True)
    output = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    endpoint = relationship("Endpoint", back_populates="tasks")

    __table_args__ = (
        Index("ix_task_queue_device_status", "device_id", "status"),
    )

class SoftwareInventory(Base):
    __tablename__ = "software_inventory"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, ForeignKey("endpoints.device_id"), index=True)
    name = Column(String, index=True)
    version = Column(String)
    publisher = Column(String, nullable=True)

    __table_args__ = (
        Index("ix_software_inventory_name_version", "name", "version"),
    )


class PendingUpdate(Base):
    """An update the endpoint has available but not yet installed.
    """
    __tablename__ = "pending_updates"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, ForeignKey("endpoints.device_id"), index=True)
    source = Column(String, index=True)
    name = Column(String)
    kb = Column(String, nullable=True)
    severity = Column(String, nullable=True)
    current_version = Column(String, nullable=True)
    available_version = Column(String, nullable=True)
    collected_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class CPEMatch(Base):
    """Cache of software-name"""
    __tablename__ = "cpe_matches"

    id = Column(Integer, primary_key=True, index=True)
    software_key = Column(String, unique=True, index=True)
    raw_name = Column(String)
    cpe23_uri = Column(String, nullable=True)
    vendor = Column(String, nullable=True)
    product = Column(String, nullable=True, index=True)
    matched_version = Column(String, nullable=True)
    confidence = Column(String, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    cve_scanned_at = Column(DateTime, nullable=True)


class CVEFinding(Base):
    """A CVE affecting one matched product, cached from the NVD CVE API.
    """
    __tablename__ = "cve_findings"

    id = Column(Integer, primary_key=True, index=True)
    software_key = Column(String, index=True)
    cve_id = Column(String, index=True)
    severity = Column(String, nullable=True)   # CRITICAL / HIGH / MEDIUM / LOW
    score = Column(Float, nullable=True)       # CVSS base score
    published = Column(String, nullable=True)
    summary = Column(Text, nullable=True)
    match_mode = Column(String, nullable=True)
    fetched_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class ExternalCache(Base):
    """Persisted responses from third-party services OpenPatch depends on
    (endoflife.date; NVD has its own tables).
    """
    __tablename__ = "external_cache"

    id = Column(Integer, primary_key=True, index=True)
    cache_key = Column(String, unique=True, index=True)
    payload = Column(Text, nullable=True)
    ok = Column(Boolean, default=True)
    fetched_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
