"""Pass timeline (M3.4) and station health (M3.5)."""
import json

import pytest

from agent.events import PassLog
from app.routes import station


@pytest.fixture(autouse=True)
def clean_live():
    station.reset_live()
    yield
    station.reset_live()


def _pass_log(reporter=None):
    from datetime import datetime, timedelta, timezone

    aos = datetime(2026, 7, 22, 20, 0, tzinfo=timezone.utc)
    return PassLog("METEOR M2-4", aos, aos + timedelta(minutes=12), reporter=reporter)


# ── Agent side: building the timeline ─────────────────────────────────────────

def test_events_are_ordered_and_timestamped():
    log = _pass_log()
    log.add("pass_start", "137.9000 MHz")
    log.add("recording_started", "600 s to LOS")

    assert [e["kind"] for e in log.events] == ["pass_start", "recording_started"]
    assert log.events[0]["at"] <= log.events[1]["at"]
    assert log.events[0]["detail"] == "137.9000 MHz"


def test_each_event_is_reported_as_it_happens():
    reported = []
    log = _pass_log(reporter=lambda **kw: reported.append(kw))
    log.add("recording_started", "600 s")

    assert reported[0]["kind"] == "recording_started"
    assert reported[0]["satellite"] == "METEOR M2-4"


def test_a_broken_reporter_never_interrupts_the_pass():
    def boom(**kw):
        raise OSError("network down")

    log = _pass_log(reporter=boom)
    log.add("recording_started")          # must not raise
    assert len(log.events) == 1


def test_signal_acquired_and_lost_come_from_the_snr_stream():
    log = _pass_log()

    log.observe_signal(0, 2.0)            # noise: nothing to say
    log.observe_signal(10, 12.0)          # signal appears
    log.observe_signal(20, 15.0)          # still there — no second event
    log.observe_signal(25, 1.0)           # quiet, but not for long enough
    log.observe_signal(60, 1.0)           # gone for 40 s → lost

    assert [e["kind"] for e in log.events] == ["signal_acquired", "signal_lost"]
    assert "12.0 dB" in log.events[0]["detail"]


def test_signal_can_come_back():
    log = _pass_log()
    log.observe_signal(10, 12.0)
    log.observe_signal(60, 1.0)
    log.observe_signal(70, 14.0)

    assert [e["kind"] for e in log.events] == [
        "signal_acquired", "signal_lost", "signal_acquired"
    ]


# ── App side: live timeline ───────────────────────────────────────────────────

def test_events_reach_the_console(client, auth_headers):
    client.post("/station/event", data={
        "kind": "recording_started", "detail": "600 s to LOS",
        "satellite": "METEOR M2-4", "aos": "2026-07-22T20:00:00+00:00",
    }, headers=auth_headers)

    panel = client.get("/pass/panel").text
    assert "recording_started" in panel
    assert "600 s to LOS" in panel


def test_event_reporting_requires_auth(client):
    assert client.post("/station/event", data={"kind": "x"}).status_code == 401


def test_a_new_pass_starts_a_new_timeline(client, auth_headers):
    for aos in ("2026-07-22T20:00:00+00:00", "2026-07-22T22:00:00+00:00"):
        client.post("/station/event", data={
            "kind": "pass_start", "detail": aos, "satellite": "METEOR M2-4", "aos": aos,
        }, headers=auth_headers)

    assert len(station._live["events"]) == 1
    assert station._live["events"][0]["detail"].startswith("2026-07-22T22")


def test_timeline_is_kept_with_the_contact(client, auth_headers):
    from app.database import SessionLocal
    from shared.models import Contact

    events = [
        {"at": "2026-07-22T20:00:00+00:00", "kind": "pass_start", "detail": "137.9 MHz"},
        {"at": "2026-07-22T20:10:00+00:00", "kind": "decode_failed", "detail": "no image"},
    ]
    client.post("/contacts", data={
        "satellite": "METEOR M2-4", "aos": "2026-07-22T20:00:00+00:00",
        "los": "2026-07-22T20:12:00+00:00", "duration_s": "720",
        "max_elevation": "62", "snr": "9", "events": json.dumps(events),
    }, headers=auth_headers)

    with SessionLocal() as db:
        stored = db.query(Contact).one().events
    assert [e["kind"] for e in stored] == ["pass_start", "decode_failed"]

    # ... and can be read back on the dashboard months later.
    page = client.get("/dashboard").text
    assert "Průběh přeletu" in page
    assert "decode_failed" in page and "no image" in page


def test_malformed_events_are_a_client_error(client, auth_headers):
    resp = client.post("/contacts", data={
        "satellite": "METEOR M2-4", "aos": "2026-07-22T20:00:00+00:00",
        "los": "2026-07-22T20:12:00+00:00", "duration_s": "720",
        "max_elevation": "62", "snr": "9", "events": '{"not": "a list"}',
    }, headers=auth_headers)
    assert resp.status_code == 422


# ── M3.5: station health ──────────────────────────────────────────────────────

def _report_health(client, auth_headers, **health):
    base = {"sdr": "ok", "bias_tee": True, "lna": True, "gain": "20.7",
            "agc": False, "mock": False, "disk_used_gb": 1.0, "disk_cap_gb": 20.0}
    base.update(health)
    return client.post("/station/live", data={"state": "idle", "health": json.dumps(base)},
                       headers=auth_headers)


def test_healthy_station_has_no_warnings(client, auth_headers):
    _report_health(client, auth_headers)
    panel = client.get("/pass/panel").text

    assert "Vše v pořádku" in panel
    assert "20.7" in panel


def test_unpowered_lna_is_the_warning_that_cost_us_july(client, auth_headers):
    _report_health(client, auth_headers, bias_tee=False)
    panel = client.get("/pass/panel").text

    assert "Bias tee je vypnutý" in panel
    assert "14 dB" in panel


def test_no_warning_when_there_is_no_lna_to_power(client, auth_headers):
    _report_health(client, auth_headers, bias_tee=False, lna=False)
    assert "Bias tee je vypnutý" not in client.get("/pass/panel").text


def test_a_dongle_held_by_another_process_is_called_out(client, auth_headers):
    _report_health(client, auth_headers, sdr="busy")
    panel = client.get("/pass/panel").text
    assert "SatDump" in panel


def test_a_missing_dongle_is_visible_within_the_offline_window(client, auth_headers):
    _report_health(client, auth_headers, sdr="missing")
    assert "SDR není připojený" in client.get("/pass/panel").text


def test_agc_and_mock_mode_are_flagged(client, auth_headers):
    _report_health(client, auth_headers, agc=True, gain="auto", mock=True)
    panel = client.get("/pass/panel").text
    assert "AGC" in panel
    assert "MOCK" in panel


def test_a_filling_disk_warns_before_it_bites(client, auth_headers):
    _report_health(client, auth_headers, disk_used_gb=19.0, disk_cap_gb=20.0)
    assert "úklid" in client.get("/pass/panel").text


def test_a_silent_agent_is_the_first_warning(client):
    # No report at all: the panel still renders and says the obvious thing.
    panel = client.get("/pass/panel").text
    assert "Agent se neozývá" in panel
    assert "Agent zatím nic nehlásil" in panel


def test_health_panel_works_with_no_pass_in_progress(client, auth_headers):
    _report_health(client, auth_headers, sdr="ok")
    panel = client.get("/pass/panel").text
    assert "Stav stanice" in panel
    assert "Nenahrává se." in panel
