import os
from contextlib import asynccontextmanager

from api.routers import agent, dashboard, tasks
from api.services.admin_auth import describe_admin_key
from api.services.fleet_secrets import (
    describe_enrollment_secret,
    describe_task_signing_secret,
)
from api.services.maintenance import start_background_maintenance
from api.services.scanner import start_background_scanner
from config import MAINTENANCE_INTERVAL_SECONDS, SCAN_INTERVAL_SECONDS
from dashboard_auth import describe_dashboard_password
from fastapi import FastAPI

# Schema is managed by Alembic migrations (server/alembic).
# Run `alembic upgrade head` before starting the server.


def warn_if_started_directly() -> str:
    """Warns a developer if it has started the server directly.
    """
    if os.environ.get("OPENPATCH_ENTRYPOINT"):
        return ""
    return (
        "[!] This app was imported directly (uvicorn main:app), not started "
        "through run.py.\n"
        "    No migrations have run, and no TLS certificate was issued or "
        "served - so\n"
        "    the API is plain HTTP and the dashboard has no CA to give "
        "endpoints.\n"
        "    Start it with `python run.py`; in an IDE, point the run "
        "configuration at\n"
        "    server/run.py rather than at uvicorn."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup banner and background loops.
    """
    bypassed = warn_if_started_directly()
    if bypassed:
        print(bypassed, flush=True)

    print(describe_admin_key(), flush=True)
    print(describe_dashboard_password(), flush=True)
    print(describe_enrollment_secret(), flush=True)
    print(describe_task_signing_secret(), flush=True)

    if start_background_maintenance():
        print(
            f"[*] Retention: pruning telemetry and finished tasks every "
            f"{MAINTENANCE_INTERVAL_SECONDS}s",
            flush=True,
        )
    else:
        print(
            "[!] Retention: disabled (OPENPATCH_MAINTENANCE_INTERVAL=0). Telemetry "
            "history will grow without bound unless something else prunes it.",
            flush=True,
        )

    if start_background_scanner():
        print(
            f"[*] Vulnerability scanning: automatic, every {SCAN_INTERVAL_SECONDS}s "
            "(first run shortly after startup)",
            flush=True,
        )
    else:
        print(
            "[!] Vulnerability scanning: disabled (OPENPATCH_SCAN_ENABLED=0). The "
            "dashboard will report whatever was last scanned and nothing newer.",
            flush=True,
        )

    yield


app = FastAPI(title="OpenPatch Orchestrator", lifespan=lifespan)

app.include_router(agent.router)
app.include_router(dashboard.router)
app.include_router(tasks.router)


@app.get("/")
def health_check():
    return {"status": "Server is running"}
