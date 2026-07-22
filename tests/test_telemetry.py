"""Telemetry contacts end to end: POST → storage → dashboard → CSV (M2.3)."""
import json


def _orbcomm_stats(per=0.0, packets=98):
    return {
        "packets": packets,
        "per": per,
        "packets_with_errors": 0,
        "packets_corrected": 0,
        "freq_offset_hz": 99.75,
        "packet_types": {"Fill": 64, "Message": 10, "Ephemeris": 1},
        "ephemeris": [{
            "lat": 46.7506, "lon": 16.3988, "alt_km": 702.4, "velocity_ms": 7158.5,
            "satellite_time": "2026-07-21 22:43:42", "ephemeris_diff_km": 22.7,
        }],
        "satellite": "orbcomm fm118",
    }


def _telemetry_contact(**overrides):
    data = {
        "satellite": "ORBCOMM FM 118",
        "aos": "2026-07-22T00:43:00+00:00",
        "los": "2026-07-22T00:52:00+00:00",
        "duration_s": "540",
        "max_elevation": "48.0",
        "snr": "6.1",
        "avg_snr": "4.4",
        "contact_type": "telemetry",
        "telemetry": json.dumps(_orbcomm_stats()),
        "notes": "orbcomm: 98 packets, PER 0.0%, 1 ephemeris frame(s)",
    }
    data.update(overrides)
    return data


def test_telemetry_contact_is_stored(client, auth_headers):
    resp = client.post("/contacts", data=_telemetry_contact(), headers=auth_headers)
    assert resp.status_code == 201


def test_image_contacts_default_to_the_image_type(client, auth_headers):
    from app.database import SessionLocal
    from shared.models import Contact

    client.post("/contacts", data={
        "satellite": "METEOR M2-4",
        "aos": "2026-07-22T10:00:00+00:00",
        "los": "2026-07-22T10:10:00+00:00",
        "duration_s": "600", "max_elevation": "70", "snr": "14",
    }, headers=auth_headers)

    with SessionLocal() as db:
        c = db.query(Contact).filter(Contact.satellite == "METEOR M2-4").one()
        assert c.contact_type == "image"
        assert c.telemetry is None


def test_dashboard_shows_frames_and_per_instead_of_an_image(client, auth_headers):
    client.post("/contacts", data=_telemetry_contact(), headers=auth_headers)
    page = client.get("/dashboard").text

    assert "98" in page and "0.0%" in page          # frames and PER
    assert "Ephemeris" in page                       # packet type breakdown
    assert "702 km" in page and "7158 m/s" in page   # ephemeris the sat reported
    assert "22.7 km" in page                         # ... against the TLE prediction
    assert "snímek nedostupný" not in page           # not an empty image card


def test_undecoded_telemetry_pass_says_so(client, auth_headers):
    client.post("/contacts", data=_telemetry_contact(
        telemetry="", notes="orbcomm decode failed: no TLE available",
    ), headers=auth_headers)
    page = client.get("/dashboard").text

    assert "telemetrie nedekódována" in page
    assert "snímek nedostupný" not in page


def test_quality_comes_from_per_not_snr(client, auth_headers):
    from app.database import SessionLocal
    from shared.models import Contact

    # Low SNR but a clean decode: that is a good pass.
    client.post("/contacts", data=_telemetry_contact(snr="4.0"), headers=auth_headers)
    # Frames arrive but a tenth of them are broken.
    client.post("/contacts", data=_telemetry_contact(
        aos="2026-07-22T02:00:00+00:00",
        telemetry=json.dumps(_orbcomm_stats(per=10.0)),
    ), headers=auth_headers)
    # Nothing decoded at all.
    client.post("/contacts", data=_telemetry_contact(
        aos="2026-07-22T03:00:00+00:00",
        telemetry=json.dumps(_orbcomm_stats(per=None, packets=0)),
    ), headers=auth_headers)

    with SessionLocal() as db:
        rows = {c.aos.hour: c.quality for c in db.query(Contact).all()}
    assert rows[0] == "ok"
    assert rows[2] == "degraded"
    assert rows[3] == "lost"


def test_export_csv_includes_telemetry_contacts(client, auth_headers):
    client.post("/contacts", data=_telemetry_contact(), headers=auth_headers)
    csv_text = client.get("/contacts/export.csv").text

    header, row = csv_text.splitlines()[0], csv_text.splitlines()[1]
    assert "contact_type" in header and "frames" in header and "per" in header
    assert "ORBCOMM FM 118" in row
    assert "telemetry" in row
    assert ",98," in row


def test_malformed_telemetry_is_a_client_error(client, auth_headers):
    resp = client.post(
        "/contacts", data=_telemetry_contact(telemetry="{not json"), headers=auth_headers
    )
    assert resp.status_code == 422

    resp = client.post(
        "/contacts", data=_telemetry_contact(telemetry="[1, 2, 3]"), headers=auth_headers
    )
    assert resp.status_code == 422
