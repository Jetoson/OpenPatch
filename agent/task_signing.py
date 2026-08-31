"""Verification of the HMAC-SHA256 signature the server puts on task lists.
Verifies whether the tasks really come from the correct server.
"""

import hmac
import json
import time
import hashlib


SIGNATURE_HEADER = "X-Task-Signature"
TIMESTAMP_HEADER = "X-Task-Timestamp"

# How far the server's signing timestamp may be from our clock.
MAX_SIGNATURE_AGE_SECONDS = 300

def canonical_payload(device_id: str, timestamp: str, tasks: list) -> bytes:
    """Returns a canonical payload from the agent's side."""
    body = json.dumps(tasks, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{device_id}\n{timestamp}\n{body}".encode()


def compute_signature(device_id: str, timestamp: str, tasks: list, secret: str) -> str:
    """Computes and returns the agent's signature."""
    return hmac.new(
        secret.encode("utf-8"),
        canonical_payload(device_id, timestamp, tasks),
        hashlib.sha256,
    ).hexdigest()


def verify(device_id: str, tasks: list, headers, secret: str) -> tuple[bool, str]:
    """Returns whether the agent's signature matches the claiming server's signature."""
    received_signature = headers.get(SIGNATURE_HEADER)
    received_timestamp = headers.get(TIMESTAMP_HEADER)

    if not received_signature or not received_timestamp:
        return False, "Response carried no signature headers"

    try:
        age = abs(time.time() - int(received_timestamp))
    except (TypeError, ValueError):
        return False, f"Unparseable {TIMESTAMP_HEADER}: {received_timestamp!r}"

    if age > MAX_SIGNATURE_AGE_SECONDS:
        return False, (
            f"Signature is {int(age)}s old (limit {MAX_SIGNATURE_AGE_SECONDS}s) - "
        )

    expected = compute_signature(device_id, received_timestamp, tasks, secret)
    if not hmac.compare_digest(expected, received_signature):
        return False, "signature mismatch"

    return True, "ok"
