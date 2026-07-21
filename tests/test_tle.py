"""Tests for shared.tle — TLE parsing, pass prediction, geometry."""
import json
import math
from pathlib import Path

from shared import tle

# Derived from the fixture rather than hard-coded, so adding a satellite to
# SATELLITE_NORAD_IDS doesn't mean editing counts in three test files.
_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "satellites_tle.json").read_text()
)
TRACKED = {e["tle0"].lstrip("0 ") for e in _FIXTURE}


def test_load_satellites_parses_tracked_birds():
    sats = tle.load_satellites()
    assert {s.name for s in sats} == TRACKED


def test_tracked_set_covers_meteor_orbcomm_and_iss():
    # The station tracks three families; losing one silently would gut the
    # pass schedule, so assert the mix explicitly.
    assert {"METEOR M2-3", "METEOR M2-4"} <= TRACKED
    assert sum(n.startswith("ORBCOMM") for n in TRACKED) >= 10
    assert any(n.startswith("ISS") for n in TRACKED)


def test_load_satellites_strips_leading_zero():
    # tle0 in the SatNOGS feed is prefixed with "0 " — must not leak into name.
    sats = tle.load_satellites()
    assert all(not s.name.startswith("0") for s in sats)


def test_footprint_radius_monotonic_and_bounded():
    # Higher orbit → larger footprint; radius stays below a quarter Earth circumference.
    low = tle._footprint_radius_km(500)
    high = tle._footprint_radius_km(900)
    assert 0 < low < high < 5000


def test_footprint_radius_formula():
    # Spot-check against the closed form for a 850 km orbit.
    R = 6371.0
    alt = 850.0
    expected = round(R * math.acos(R / (R + alt)), 0)
    assert tle._footprint_radius_km(alt) == expected


def test_predict_passes_invariants():
    passes = tle.predict_passes(hours=48)
    # Over 48 h the tracked birds always produce at least one pass from mid-lat.
    assert passes, "expected at least one pass in 48 h"
    for p in passes:
        assert p.satellite in TRACKED
        assert p.los > p.aos
        assert p.duration_s > 0
        assert -90 <= p.max_elevation <= 90
        assert 0 <= p.az_at_max <= 360
    # Result is sorted by AOS.
    assert passes == sorted(passes, key=lambda p: p.aos)


def test_get_cached_passes_reuses_result(monkeypatch):
    calls = {"n": 0}
    real = tle.predict_passes

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(tle, "predict_passes", counting)
    tle.get_cached_passes()
    tle.get_cached_passes()
    assert calls["n"] == 1, "second call should hit the cache"


def test_set_observer_overrides_env(monkeypatch):
    monkeypatch.setenv("OBSERVER_LAT", "50.08")
    monkeypatch.setenv("OBSERVER_LON", "14.44")
    assert tle.set_observer(48.15, 17.11) is True
    assert tle.get_observer_latlon() == (48.15, 17.11)


def test_set_observer_reports_change_and_invalidates_cache():
    tle.get_cached_passes()
    assert tle._passes_cache["data"] is not None

    changed = tle.set_observer(45.0, 10.0)
    assert changed is True
    assert tle._passes_cache["data"] is None  # cache dropped → recompute

    # Same position again → no change, no invalidation churn.
    tle.get_cached_passes()
    assert tle.set_observer(45.0, 10.0) is False
    assert tle._passes_cache["data"] is not None


def test_current_positions_shape():
    positions = tle.current_positions()
    assert len(positions) == len(TRACKED)
    for pos in positions:
        assert -90 <= pos.lat <= 90
        assert -180 <= pos.lon <= 180
        assert pos.alt_km > 0
        assert pos.footprint_radius_km > 0
        # Ground track is a 90-min path sampled every minute.
        assert len(pos.ground_track) == 91
        for lat, lon in pos.ground_track:
            assert -90 <= lat <= 90
            assert -180 <= lon <= 180
