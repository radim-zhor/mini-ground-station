"""Tests for agent.location — auto-detection of the mobile station position."""
from types import SimpleNamespace

import pytest

import agent.location as location


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(location, "_cache", {"pos": None, "source": None, "ts": 0.0})
    monkeypatch.delenv("OBSERVER_MODE", raising=False)


def _fake_response(loc="49.1234,16.5678"):
    return SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"loc": loc, "city": "Brno"},
    )


def test_detect_uses_ip_geolocation(monkeypatch):
    monkeypatch.setattr(location.requests, "get", lambda *a, **k: _fake_response())
    lat, lon, source = location.detect_location()
    assert (lat, lon) == (49.1234, 16.5678)
    assert source == "ip"


def test_detect_caches_result(monkeypatch):
    calls = {"n": 0}

    def counting_get(*a, **k):
        calls["n"] += 1
        return _fake_response()

    monkeypatch.setattr(location.requests, "get", counting_get)
    location.detect_location()
    location.detect_location()
    assert calls["n"] == 1


def test_detect_falls_back_to_env_on_failure(monkeypatch):
    def failing_get(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(location.requests, "get", failing_get)
    monkeypatch.setenv("OBSERVER_LAT", "49.20")
    monkeypatch.setenv("OBSERVER_LON", "16.82")
    lat, lon, source = location.detect_location()
    assert (lat, lon) == (49.20, 16.82)
    assert source == "fallback"


def test_manual_mode_skips_geolocation(monkeypatch):
    called = []
    monkeypatch.setattr(location.requests, "get", lambda *a, **k: called.append(1))
    monkeypatch.setenv("OBSERVER_MODE", "manual")
    monkeypatch.setenv("OBSERVER_LAT", "49.20")
    monkeypatch.setenv("OBSERVER_LON", "16.82")
    lat, lon, source = location.detect_location()
    assert (lat, lon, source) == (49.20, 16.82, "manual")
    assert called == []
