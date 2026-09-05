"""One rate-limited, retrying HTTP client shared by every NVD caller.
"""

import random
import threading
import time

import requests
from config import NVD_API_KEY


MIN_REQUEST_INTERVAL = 0.7 if NVD_API_KEY else 6.5

MAX_ATTEMPTS = 4
MAX_BACKOFF_SECONDS = 60.0

_gate = threading.Lock()
_last_request_at = 0.0


class NVDUnavailable(RuntimeError):
    """NVD could not be reached, or refused to answer, after retries.
    """


def _throttle() -> None:
    global _last_request_at
    with _gate:
        wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _retry_after(response: requests.Response, attempt: int) -> float:
    """How long to wait before retrying, preferring the server's own answer.
    """
    header = response.headers.get("Retry-After")
    if header:
        try:
            return min(float(header), MAX_BACKOFF_SECONDS)
        except ValueError:
            pass
    return min(MIN_REQUEST_INTERVAL * (2 ** attempt), MAX_BACKOFF_SECONDS) + random.uniform(0, 1)


def get_json(url: str, params: dict, timeout: int = 30) -> dict:
    """A throttled, retried GET returning parsed JSON.
    """
    headers = {"apiKey": NVD_API_KEY} if NVD_API_KEY else {}
    last_error = "unknown error"

    for attempt in range(MAX_ATTEMPTS):
        _throttle()
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(min(MIN_REQUEST_INTERVAL * (2 ** attempt), MAX_BACKOFF_SECONDS))
            continue

        if response.status_code in (429, 403, 503) or response.status_code >= 500:
            last_error = f"HTTP {response.status_code}"
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(_retry_after(response, attempt))
                continue
            break

        try:
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            break

    raise NVDUnavailable(f"NVD request failed after {MAX_ATTEMPTS} attempts: {last_error}")
