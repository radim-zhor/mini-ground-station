"""The upcoming-pass queue and the inline decode result on /pass."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.routes import station


@pytest.fixture(autouse=True)
def clean_live():
    station.reset_live()
    yield
    station.reset_live()


NOW = datetime(2026, 7, 22, 20, 0, tzinfo=timezone.utc)


def _pass(satellite, start_min, length_min=10, el=45.0):
    aos = NOW + timedelta(minutes=start_min)
    return SimpleNamespace(
        satellite=satellite,
        aos=aos,
        los=aos + timedelta(minutes=length_min),
        duration_s=length_min * 60,
        max_elevation=el,
        minutes_until=start_min,
    )


# ── The queue ─────────────────────────────────────────────────────────────────

def test_passes_that_do_not_overlap_are_all_recorded():
    passes = [_pass("METEOR M2-4", 10), _pass("ORBCOMM FM 118", 40)]
    queue = station.pass_queue(passes, now=NOW)

    assert [q["plan"] for q in queue] == ["recording", "recording"]


def test_a_pass_inside_another_one_is_lost_not_queued():
    # The station has one dish and one thread: a pass that starts and ends
    # while another is being recorded is never heard at all.
    passes = [_pass("METEOR M2-4", 10, length_min=20), _pass("ORBCOMM FM 118", 15, length_min=5)]
    queue = station.pass_queue(passes, now=NOW)

    assert queue[0]["plan"] == "recording"
    assert queue[1]["plan"] == "missed"
    assert queue[1]["blocked_by"] == "METEOR M2-4"


def test_an_overlapping_pass_that_outlives_the_first_is_recorded_partially():
    passes = [_pass("METEOR M2-4", 10, length_min=20), _pass("ORBCOMM FM 118", 25, length_min=15)]
    queue = station.pass_queue(passes, now=NOW)

    assert queue[1]["plan"] == "partial"
    assert queue[1]["blocked_by"] == "METEOR M2-4"


def test_the_cooldown_after_a_pass_costs_us_the_next_one():
    # The agent settles for two minutes after a pass; a pass ending inside that
    # window is gone even though the dish is technically free.
    passes = [_pass("METEOR M2-4", 10), _pass("ORBCOMM FM 118", 20, length_min=1)]
    queue = station.pass_queue(passes, now=NOW)

    assert queue[1]["plan"] == "missed"


def test_queue_ignores_passes_that_have_already_ended():
    passes = [_pass("METEOR M2-4", -30), _pass("ORBCOMM FM 118", 10)]
    queue = station.pass_queue(passes, now=NOW)

    assert len(queue) == 1
    assert queue[0]["pass"].satellite == "ORBCOMM FM 118"


def test_queue_is_capped():
    passes = [_pass(f"SAT {i}", i * 30) for i in range(10)]
    assert len(station.pass_queue(passes, now=NOW)) == station.QUEUE_LENGTH


def test_queue_renders_on_the_page(client):
    panel = client.get("/pass/panel").text
    assert "Fronta přeletů" in panel
    assert "nahraje se" in panel


# ── The inline result ─────────────────────────────────────────────────────────

def _post_contact(client, auth_headers, **overrides):
    aos = datetime.now(timezone.utc) - timedelta(minutes=20)
    data = {
        "satellite": "ORBCOMM FM 118",
        "aos": aos.isoformat(),
        "los": (aos + timedelta(minutes=9)).isoformat(),
        "duration_s": "540", "max_elevation": "48", "snr": "11.2",
        "contact_type": "telemetry",
        "telemetry": json.dumps({"packets": 98, "per": 0.0,
                                 "ephemeris": [{"lat": 46.7, "lon": 16.4}]}),
        "notes": "orbcomm: 98 packets, PER 0.0%",
    }
    data.update(overrides)
    return client.post("/contacts", data=data, headers=auth_headers)


def test_the_decode_result_waits_on_the_console_after_los(client, auth_headers):
    _post_contact(client, auth_headers)
    panel = client.get("/pass/panel").text

    assert "Výsledek posledního přeletu" in panel
    assert "98" in panel and "0.0%" in panel
    assert "efemeridy" in panel


def test_an_old_contact_is_not_passed_off_as_this_pass(client, auth_headers):
    old = datetime.now(timezone.utc) - timedelta(hours=6)
    _post_contact(client, auth_headers, aos=old.isoformat(),
                  los=(old + timedelta(minutes=9)).isoformat())

    assert "Výsledek posledního přeletu" not in client.get("/pass/panel").text


def test_no_contacts_at_all_is_fine(client):
    assert client.get("/pass/panel").status_code == 200


def test_an_image_pass_shows_its_picture(client, auth_headers):
    aos = datetime.now(timezone.utc) - timedelta(minutes=10)
    _post_contact(
        client, auth_headers, satellite="METEOR M2-4", contact_type="image",
        telemetry="", notes="satdump meteor_m2-x_lrpt: 3 image(s)",
        aos=aos.isoformat(), los=(aos + timedelta(minutes=9)).isoformat(),
    )

    result = station.last_result(_db(), {"satellite": "METEOR M2-4"})
    assert result["contact_type"] == "image"
    assert result["is_current"] is True


def _db():
    from app.database import SessionLocal

    return SessionLocal()
