"""Tests for the agent scheduler — pass notifications, location, capture rates."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import agent.scheduler as scheduler


def _fake_pass():
    aos = datetime.now(timezone.utc) + timedelta(minutes=9)
    return SimpleNamespace(
        satellite="METEOR M2-4",
        aos=aos,
        los=aos + timedelta(seconds=720),
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


def test_progress_reporter_throttles_the_log_and_the_console(caplog, monkeypatch):
    posted = []
    monkeypatch.setattr(scheduler, "post_live", lambda **kw: posted.append(kw))

    report = scheduler._progress_reporter(_fake_pass())
    with caplog.at_level("INFO"):
        for elapsed in range(0, 61):  # 61 seconds of 1 Hz updates
            report(float(elapsed), 60.0, 12.3)

    # The log is for reading afterwards: 0 s, 30 s, 60 s — not 61 lines.
    assert caplog.text.count("METEOR M2-4") == 3
    # The console is for watching now, so it hears from us far more often.
    assert len(posted) == 31
    assert posted[0]["state"] == "recording"
    assert posted[-1]["snr"] == 12.3


def test_heartbeat_keeps_reporting_while_sleeping(monkeypatch):
    posted, slept = [], []
    monkeypatch.setattr(scheduler, "post_live", lambda **kw: posted.append(kw))
    monkeypatch.setattr(scheduler.time, "sleep", lambda s: slept.append(s))

    scheduler._sleep_with_heartbeat(25, "waiting", _fake_pass())

    # 25 s at a 10 s heartbeat: beats at 0, 10, 20 and once more at the end.
    assert sum(slept) == 25
    assert len(posted) == 4
    assert all(p["state"] == "waiting" for p in posted)


def test_heartbeat_reports_even_when_not_sleeping(monkeypatch):
    posted = []
    monkeypatch.setattr(scheduler, "post_live", lambda **kw: posted.append(kw))
    monkeypatch.setattr(scheduler.time, "sleep", lambda s: None)

    scheduler._sleep_with_heartbeat(0, "idle", note="no passes in 24 h")

    assert len(posted) == 1
    assert posted[0]["note"] == "no passes in 24 h"
