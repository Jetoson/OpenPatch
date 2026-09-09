"""The shared, rate-limited NVD client.
"""

import time

import api.services.cpe_matcher as cpe_matcher
import api.services.cve_lookup as cve_lookup
import api.services.nvd_client as nvd
import pytest
import requests


@pytest.fixture
def fast(monkeypatch):
    """Collapse the pacing so the retry logic can be tested in milliseconds."""
    monkeypatch.setattr(nvd, "MIN_REQUEST_INTERVAL", 0.01)
    monkeypatch.setattr(nvd, "MAX_BACKOFF_SECONDS", 0.05)


class Reply:
    def __init__(self, status_code, payload=None, retry_after=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = {"Retry-After": retry_after} if retry_after else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture
def nvd_replies(monkeypatch):
    """Script a sequence of responses and count the requests made."""
    state = {"queue": [], "calls": 0}

    def fake_get(url, params=None, headers=None, timeout=None):
        state["calls"] += 1
        return state["queue"].pop(0)

    monkeypatch.setattr(nvd.requests, "get", fake_get)
    return state


def test_a_successful_call_returns_the_payload(fast, nvd_replies):
    nvd_replies["queue"] = [Reply(200, {"products": [1, 2]})]
    assert nvd.get_json("u", {}) == {"products": [1, 2]}
    assert nvd_replies["calls"] == 1


@pytest.mark.parametrize("status", [429, 403, 503, 500])
def test_a_throttled_or_broken_response_is_retried(fast, nvd_replies, status):
    nvd_replies["queue"] = [Reply(status), Reply(200, {"ok": True})]

    assert nvd.get_json("u", {}) == {"ok": True}
    assert nvd_replies["calls"] == 2


def test_retry_after_is_honoured(fast, nvd_replies, monkeypatch):
    slept = []
    monkeypatch.setattr(nvd.time, "sleep", lambda s: slept.append(s))
    nvd_replies["queue"] = [Reply(429, retry_after="0.03"), Reply(200, {})]

    nvd.get_json("u", {})

    assert any(abs(s - 0.03) < 1e-9 for s in slept)


def test_giving_up_raises_rather_than_returning_empty(fast, nvd_replies):
    """An outage must never be cached as "this product has no CVEs"."""
    nvd_replies["queue"] = [Reply(503) for _ in range(nvd.MAX_ATTEMPTS)]

    with pytest.raises(nvd.NVDUnavailable):
        nvd.get_json("u", {})

    assert nvd_replies["calls"] == nvd.MAX_ATTEMPTS


def test_a_connection_error_is_retried(fast, monkeypatch):
    calls = {"n": 0}

    def flaky(url, params=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 2:
            raise requests.ConnectionError("down")
        return Reply(200, {"recovered": True})

    monkeypatch.setattr(nvd.requests, "get", flaky)
    assert nvd.get_json("u", {}) == {"recovered": True}


def test_consecutive_calls_are_paced(monkeypatch, nvd_replies):
    """The pacing is the whole point; without it NVD throttles the scan."""
    monkeypatch.setattr(nvd, "MIN_REQUEST_INTERVAL", 0.15)
    nvd_replies["queue"] = [Reply(200, {}), Reply(200, {})]

    start = time.monotonic()
    nvd.get_json("u", {})
    nvd.get_json("u", {})

    assert time.monotonic() - start >= 0.15


def test_both_nvd_callers_share_one_budget():
    assert cpe_matcher.get_json is nvd.get_json
    assert cve_lookup.get_json is nvd.get_json


def test_an_outage_does_not_overwrite_a_cached_match(db, fast, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from api.models import CPEMatch

    old = datetime.now(timezone.utc) - timedelta(days=999)
    db.add(CPEMatch(software_key="acme reader", raw_name="Acme Reader", product="reader",
                    vendor="acme", cpe23_uri="cpe:2.3:a:acme:reader:1:*:*:*:*:*:*:*",
                    confidence="exact_version", resolved_at=old))
    db.commit()

    def unavailable(*args, **kwargs):
        raise nvd.NVDUnavailable("down")

    monkeypatch.setattr(cpe_matcher, "get_json", unavailable)

    match = cpe_matcher.resolve_cpe(db, "Acme Reader", "1.0")

    assert match.product == "reader", "the cached match must survive the outage"


def test_an_outage_leaves_a_product_unscanned_rather_than_clean(db, fast, monkeypatch):
    """Marking it scanned would cache the outage as a clean bill of health."""
    from datetime import datetime, timezone

    from api.models import CPEMatch

    db.add(CPEMatch(software_key="acme", raw_name="Acme", product="acme", vendor="acme",
                    cpe23_uri="cpe:2.3:a:acme:acme:1:*:*:*:*:*:*:*",
                    confidence="exact_version", resolved_at=datetime.now(timezone.utc)))
    db.commit()

    def unavailable(*args, **kwargs):
        raise nvd.NVDUnavailable("down")

    monkeypatch.setattr(cve_lookup, "get_json", unavailable)

    result = cve_lookup.scan(db, only_unscanned=True)

    assert result["products_unavailable"] == 1
    assert result["products_scanned"] == 0
    assert db.query(CPEMatch).filter_by(software_key="acme").one().cve_scanned_at is None
