"""Tests for the agent scheduler — ntfy pass notifications."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import agent.scheduler as scheduler


def _fake_pass():
    return SimpleNamespace(
        satellite="NOAA 18",
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
    assert b"NOAA 18" in captured["data"]
