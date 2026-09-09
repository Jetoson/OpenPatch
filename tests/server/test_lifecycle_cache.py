"""endoflife.date lookups, cached in the database rather than in memory.

"""

from datetime import datetime, timedelta, timezone

import api.services.lifecycle as lifecycle
import pytest
import requests
from api.models import ExternalCache

CYCLES = {
    "python": [
        {"cycle": "3.13", "eol": False, "support": "2029-10-01"},
        {"cycle": "3.7", "eol": "2023-06-27"},
    ],
}


@pytest.fixture
def upstream(monkeypatch):
    calls = []

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def fake_get(url, timeout=None):
        calls.append(url)
        if url.endswith("all.json"):
            return Response(list(CYCLES))
        slug = url.rsplit("/", 1)[-1].replace(".json", "")
        return Response(CYCLES.get(slug, []))

    monkeypatch.setattr(lifecycle.requests, "get", fake_get)
    return calls


def test_responses_are_persisted(db, upstream):
    lifecycle.lookup_lifecycle(db, "python", "3.7.9")

    keys = {row.cache_key for row in db.query(ExternalCache).all()}
    assert "eol:catalog" in keys
    assert "eol:cycles:python" in keys


def test_a_restart_costs_nothing(db, upstream):
    """The dict cache meant a redeploy re-issued the entire catalogue."""
    lifecycle.lookup_lifecycle(db, "python", "3.7.9")
    before = len(upstream)

    lifecycle._memo.clear()          # a fresh process
    lifecycle.lookup_lifecycle(db, "python", "3.7.9")

    assert len(upstream) == before


def test_a_miss_is_cached_too(db, upstream):
    lifecycle.get_cycles(db, "notaproduct")
    before = len(upstream)

    lifecycle._memo.clear()
    lifecycle.get_cycles(db, "notaproduct")

    assert len(upstream) == before


def test_stale_data_is_served_when_upstream_fails(db, upstream, monkeypatch):
    lifecycle.lookup_lifecycle(db, "python", "3.7.9")

    db.query(ExternalCache).filter_by(cache_key="eol:cycles:python").update(
        {"fetched_at": datetime.now(timezone.utc) - timedelta(days=400)}
    )
    db.commit()
    lifecycle._memo.clear()

    def down(url, timeout=None):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(lifecycle.requests, "get", down)

    assert len(lifecycle.get_cycles(db, "python")) == 2


class TestVersionMatching:
    def test_the_cycle_matching_the_installed_build_is_returned(self, db, upstream):
        cycle = lifecycle.lookup_lifecycle(db, "python", "3.7.9")
        assert cycle["cycle"] == "3.7"

    def test_a_supported_build_reports_as_supported(self, db, upstream):
        assert lifecycle.lookup_lifecycle(db, "python", "3.13.1")["eol"] is False

    def test_an_untracked_product_returns_nothing(self, db, upstream):
        assert lifecycle.lookup_lifecycle(db, "notaproduct", "1.0") is None

    def test_no_version_returns_nothing(self, db, upstream):
        assert lifecycle.lookup_lifecycle(db, "python", "") is None


class TestCpeCacheTtls:
    def test_a_miss_expires_sooner_than_a_hit(self):
        import api.services.cpe_matcher as cpe_matcher

        assert cpe_matcher.MISS_CACHE_TTL < cpe_matcher.CACHE_TTL

    def test_a_fresh_match_is_served_without_asking_nvd(self, db, monkeypatch):
        import api.services.cpe_matcher as cpe_matcher
        from api.models import CPEMatch

        db.add(CPEMatch(software_key="acme reader", raw_name="Acme Reader", product="reader",
                        vendor="acme", cpe23_uri="cpe:2.3:a:acme:reader:1:*:*:*:*:*:*:*",
                        confidence="exact_version", resolved_at=datetime.now(timezone.utc)))
        db.commit()

        def must_not_be_called(*args, **kwargs):
            raise AssertionError("NVD should not have been asked")

        monkeypatch.setattr(cpe_matcher, "get_json", must_not_be_called)

        assert cpe_matcher.resolve_cpe(db, "Acme Reader", "1.0").product == "reader"
