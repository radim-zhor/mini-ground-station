"""Tests for the agent scheduler — pass notifications, location, capture rates."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import agent.scheduler as scheduler


def _fake_pass():
    return SimpleNamespace(
        satellite="METEOR M2-4",
        aos=datetime.now(timezone.utc) + timedelta(minutes=9),
        max_elevation=62.0,
        duration_s=720,
    )


def test_notify_upcoming_noop_without_topic(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    called = []
    monkeypatch.setattr(scheduler.requests, "post", lambda *a, **k: called.append(1))
    scheduler.notify_upcoming(_fake_pass())
    assert called == []


def test_refresh_location_reports_on_change(monkeypatch):
    import shared.tle as tle

    monkeypatch.setattr(tle, "_observer_override", None)
    monkeypatch.setattr(scheduler, "detect_location", lambda: (49.5, 17.5, "ip"))
    reported = []
    monkeypatch.setattr(scheduler, "post_observer", lambda *a: reported.append(a))

    scheduler._refresh_location()
    assert reported == [(49.5, 17.5, "ip")]
    assert tle.get_observer_latlon() == (49.5, 17.5)

    # Unchanged position → no re-report (unless forced).
    scheduler._refresh_location()
    assert len(reported) == 1
    scheduler._refresh_location(force_report=True)
    assert len(reported) == 2


def test_notify_upcoming_posts_when_topic_set(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")
    captured = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured.update(url=url, data=data)
        return SimpleNamespace()

    monkeypatch.setattr(scheduler.requests, "post", fake_post)
    scheduler.notify_upcoming(_fake_pass())

    assert captured["url"] == "https://ntfy.sh/test-topic"
    assert b"METEOR M2-4" in captured["data"]


def test_sample_rate_per_satellite_family():
    # Orbcomm must stay at 256 × 4800 baud — the upstream decoder assumes it.
    assert scheduler.sample_rate_for("ORBCOMM FM 118") == 1_228_800
    assert scheduler.sample_rate_for("METEOR M2-4") == 1_000_000
    assert scheduler.sample_rate_for("ISS (ZARYA)") == 250_000
    assert scheduler.sample_rate_for("SOMETHING NEW") == scheduler.DEFAULT_SAMPLE_RATE


def test_every_scheduled_satellite_has_a_capture_rate():
    for name in scheduler.FREQUENCIES:
        assert scheduler.sample_rate_for(name) > 0


def test_progress_logger_throttles_to_the_log_interval(caplog):
    report = scheduler._progress_logger("METEOR M2-4")
    with caplog.at_level("INFO"):
        for elapsed in range(0, 61):  # 61 seconds of 1 Hz updates
            report(float(elapsed), 60.0, 12.3)

    # 0 s, 30 s, 60 s — not 61 lines of noise.
    assert caplog.text.count("METEOR M2-4") == 3
