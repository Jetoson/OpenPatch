from sqlalchemy import Column, Integer, String, Float, DateTime, LargeBinary, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from database import Base


class Endpoint(Base):
    __tablename__ = "endpoints"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, unique=True, index=True)
    hostname = Column(String)
    os_version = Column(String)

    # Telemetry
    cpu_usage_percent = Column(Float, default=0.0)
    ram_usage_percent = Column(Float, default=0.0)
    last_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    status = Column(String, default="ONLINE")
    ml_model = Column(LargeBinary, nullable=True)

    tasks = relationship("TaskQueue", back_populates="endpoint")


class TaskQueue(Base):
    __tablename__ = "task_queue"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String, ForeignKey("endpoints.device_id"))
    action = Column(String)
    status = Column(String, default="PENDING")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    endpoint = relationship("Endpoint", back_populates="tasks")