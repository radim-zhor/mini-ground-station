"""The live pass console: agent feed (M3.1) and the /pass page (M3.2, M3.3)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.routes import station


@pytest.fixture(autouse=True)
def clean_live():
    station.reset_live()
    yield
    station.reset_live()


def _live(**overrides):
    data = {"state": "recording", "satellite": "METEOR M2-4",
            "aos": "2026-07-22T20:00:00+00:00", "los": "2026-07-22T20:12:00+00:00",
            "elapsed_s": "120", "total_s": "720", "snr": "11.5"}
    data.update(overrides)
    return data


# ── M3.1: the agent feed ──────────────────────────────────────────────────────

def test_live_report_requires_auth(client):
    assert client.post("/station/live", data=_live()).status_code == 401


def test_live_report_rejects_an_unknown_state(client, auth_headers):
    resp = client.post("/station/live", data=_live(state="dancing"), headers=auth_headers)
    assert resp.status_code == 422


def test_live_report_is_read_back(client, auth_headers):
    client.post("/station/live", data=_live(), headers=auth_headers)
    body = client.get("/station/live").json()

    assert body["state"] == "recording"
    assert body["satellite"] == "METEOR M2-4"
    assert body["elapsed_s"] == 120
    assert body["online"] is True
    assert body["last_seen_s"] < 5


def test_no_report_yet_reads_as_offline(client):
    body = client.get("/station/live").json()
    assert body["state"] == "offline"
    assert body["online"] is False


def test_a_silent_agent_goes_offline(client, auth_headers):
    client.post("/station/live", data=_live(), headers=auth_headers)
    # Pretend the last report arrived just over the threshold ago.
    station._live["received_at"] = datetime.now(timezone.utc) - timedelta(
        seconds=station.OFFLINE_AFTER_S + 1
    )

    body = client.get("/station/live").json()
    assert body["state"] == "offline"
    assert body["online"] is False
    assert body["last_state"] == "recording"  # context kept, claim dropped


def test_state_does_not_survive_a_restart(client, auth_headers):
    client.post("/station/live", data=_live(), headers=auth_headers)
    # The live state is in memory on purpose: after a restart the honest answer
    # is "I don't know", and the agent's next heartbeat is seconds away.
    station.reset_live()

    assert client.get("/station/live").json()["state"] == "offline"


def test_heartbeat_without_a_pass_is_accepted(client, auth_headers):
    resp = client.post("/station/live", data={"state": "idle", "note": "no passes in 24 h"},
                       headers=auth_headers)
    assert resp.status_code == 200

    body = client.get("/station/live").json()
    assert body["state"] == "idle"
    assert body["online"] is True
    assert body["satellite"] is None


def test_snr_series_accumulates_and_resets_between_passes(client, auth_headers):
    for i, snr in enumerate([3.0, 7.0, 12.0]):
        client.post("/station/live", data=_live(elapsed_s=str(i * 2), snr=str(snr)),
                    headers=auth_headers)
    assert station._live["snr_series"] == [(0.0, 3.0), (2.0, 7.0), (4.0, 12.0)]

    # A different pass starts a fresh curve.
    client.post("/station/live", data=_live(aos="2026-07-22T22:00:00+00:00", snr="4.0"),
                headers=auth_headers)
    assert station._live["snr_series"] == [(120.0, 4.0)]


# ── M3.2 / M3.3: the page ─────────────────────────────────────────────────────

def test_pass_page_renders_without_an_agent(client):
    resp = client.get("/pass")
    assert resp.status_code == 200
    assert "OFFLINE" in resp.text
    # It still shows the next predicted pass — that works with no hardware.
    assert "AOS" in resp.text


def test_pass_page_shows_the_sky_arc_for_the_next_pass(client):
    page = client.get("/pass").text
    assert "polyline" in page          # the az/el arc
    assert "TCA" in page               # ... with its markers
    assert 'class="sky"' in page


def test_panel_shows_progress_and_snr_while_recording(client, auth_headers):
    from shared.tle import get_cached_passes

    nxt = sorted(get_cached_passes(), key=lambda p: p.aos)[0]
    for i, snr in enumerate([4.0, 9.0, 14.0]):
        client.post("/station/live", data={
            "state": "recording", "satellite": nxt.satellite,
            "aos": nxt.aos.isoformat(), "los": nxt.los.isoformat(),
            "elapsed_s": str(i * 30), "total_s": "600", "snr": str(snr),
        }, headers=auth_headers)

    panel = client.get("/pass/panel").text
    assert "RECORDING" in panel
    assert "agent online" in panel
    assert "%" in panel and "600" in panel   # progress bar and its numbers
    assert "14.0 dB" in panel                # latest SNR reading
    assert nxt.satellite in panel


def test_panel_says_so_when_nothing_is_recording(client):
    panel = client.get("/pass/panel").text
    assert "Nenahrává se." in panel


def test_progress_is_clamped_to_the_pass(client):
    assert station._progress({"elapsed_s": 700, "total_s": 600})["pct"] == 100.0
    assert station._progress({"elapsed_s": 0, "total_s": 600})["pct"] == 0.0
    assert station._progress({"elapsed_s": 300, "total_s": 600})["remaining_s"] == 300
    assert station._progress({"elapsed_s": 5, "total_s": None}) is None


def test_sky_plot_places_zenith_at_the_centre_and_horizon_at_the_rim():
    centre = station.SKY_SIZE / 2
    assert station._polar_xy(0, 90) == (centre, centre)
    # Due north on the horizon is straight up on the plot.
    assert station._polar_xy(0, 0) == (centre, centre - station.SKY_R)
    # Due east on the horizon is to the right.
    assert station._polar_xy(90, 0) == (centre + station.SKY_R, centre)


def test_sky_plot_needs_a_visible_arc():
    assert station._sky_plot([], None) is None
    assert station._sky_plot([{"az": 10, "el": -5}, {"az": 20, "el": -3}], None) is None

    track = [{"az": 10, "el": 0}, {"az": 90, "el": 45}, {"az": 170, "el": 0}]
    sky = station._sky_plot(track, {"az": 90, "el": 45})
    assert sky["tca"]["el"] == 45
    assert sky["live"] is not None
    assert len(sky["polyline"].split()) == 3


def test_no_live_dot_for_a_satellite_below_the_horizon():
    # _polar_xy clamps a negative elevation onto the rim, which would put a
    # confident dot on the sky for a satellite that has not risen yet.
    track = [{"az": 10, "el": 0}, {"az": 90, "el": 45}, {"az": 170, "el": 0}]
    sky = station._sky_plot(track, {"az": 230, "el": -23.3})
    assert sky["live"] is None
