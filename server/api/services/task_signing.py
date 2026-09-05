"""HMAC-SHA256 signing of the task list the server hands to agents.

Three values go into the signed material, not just the task list:
- device_id,
- timestamp, and
- the task list itself, serialised canonically.
"""

import hmac
import json
import time
import hashlib

from api.services import fleet_secrets

TASK_SIGNING_SECRET = fleet_secrets.task_signing_secret()

SIGNATURE_HEADER = "X-Task-Signature"
TIMESTAMP_HEADER = "X-Task-Timestamp"


def canonical_payload(device_id: str, timestamp: str, tasks: list[dict]) -> bytes:
    """The exact bytes both sides sign.
    """
    body = json.dumps(tasks, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{device_id}\n{timestamp}\n{body}".encode()


def compute_signature(device_id: str, timestamp: str, tasks: list[dict], secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        canonical_payload(device_id, timestamp, tasks),
        hashlib.sha256,
    ).hexdigest()


def attach_task_signature(response, device_id: str, tasks: list[dict]) -> None:
    """Adds the signature headers to an outgoing response.
    """
    if not TASK_SIGNING_SECRET:
        return

    timestamp = str(int(time.time()))
    response.headers[TIMESTAMP_HEADER] = timestamp
    response.headers[SIGNATURE_HEADER] = compute_signature(
        device_id, timestamp, tasks, TASK_SIGNING_SECRET
    )
