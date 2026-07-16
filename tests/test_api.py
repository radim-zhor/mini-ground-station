"""Tests for the FastAPI web app routes."""


def _valid_contact():
    return {
        "satellite": "NOAA 18",
        "aos": "2026-07-02T20:00:00+00:00",
        "los": "2026-07-02T20:12:00+00:00",
        "duration_s": "720",
        "max_elevation": "62.5",
        "snr": "8.3",
    }


def test_post_contact_requires_auth(client):
    resp = client.post("/contacts", data=_valid_contact())
    assert resp.status_code == 401


def test_post_contact_rejects_wrong_token(client):
    resp = client.post(
        "/contacts", data=_valid_contact(), headers={"Authorization": "Bearer nope"}
    )
    assert resp.status_code == 401


def test_post_contact_ok(client, auth_headers):
    resp = client.post("/contacts", data=_valid_contact(), headers=auth_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert isinstance(body["id"], int)


def test_post_contact_then_appears_on_dashboard(client, auth_headers):
    data = _valid_contact()
    data["satellite"] = "NOAA 15"
    client.post("/contacts", data=data, headers=auth_headers)
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "NOAA 15" in resp.text


def test_post_contact_is_idempotent(client, auth_headers):
    # Agent retries a pending contact → same (satellite, aos) must not duplicate (A4).
    data = _valid_contact()
    data["satellite"] = "NOAA 19"
    data["aos"] = "2026-07-02T21:30:00+00:00"
    first = client.post("/contacts", data=data, headers=auth_headers)
    second = client.post("/contacts", data=data, headers=auth_headers)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json().get("duplicate") is True


def test_post_contact_invalid_date_is_client_error(client, auth_headers):
    data = _valid_contact()
    data["aos"] = "not-a-date"
    resp = client.post("/contacts", data=data, headers=auth_headers)
    # Bad input from the agent is a 422, not a 500 (A6).
    assert resp.status_code == 422


def test_passes_page_renders(client):
    resp = client.get("/passes")
    assert resp.status_code == 200
    assert "NOAA" in resp.text


def test_satellite_position_json(client):
    resp = client.get("/satellite/position")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["satellites"]) == 3
    assert "observer" in body
    sat = body["satellites"][0]
    assert {"name", "lat", "lon", "alt_km", "footprint_radius_km", "ground_track"} <= sat.keys()


def test_root_redirects_to_dashboard(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"].endswith("/dashboard")


def _post(client, headers, **overrides):
    data = _valid_contact()
    data.update({k: str(v) for k, v in overrides.items()})
    return client.post("/contacts", data=data, headers=headers)


def _csv_rows(client):
    resp = client.get("/contacts/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    import csv
    import io
    return list(csv.DictReader(io.StringIO(resp.text)))


def test_quality_derived_from_snr(client, auth_headers):
    _post(client, auth_headers, satellite="NOAA 18", aos="2026-07-03T01:00:00+00:00", snr=14)
    _post(client, auth_headers, satellite="NOAA 15", aos="2026-07-03T02:00:00+00:00", snr=5)
    _post(client, auth_headers, satellite="NOAA 19", aos="2026-07-03T03:00:00+00:00", snr=0.5)
    rows = {r["satellite"]: r for r in _csv_rows(client)}
    assert rows["NOAA 18"]["quality"] == "ok"
    assert rows["NOAA 15"]["quality"] == "degraded"
    assert rows["NOAA 19"]["quality"] == "lost"


def test_avg_snr_stored(client, auth_headers):
    _post(client, auth_headers, satellite="NOAA 18", aos="2026-07-03T04:00:00+00:00",
          snr=12, avg_snr=7.7)
    rows = {r["satellite"]: r for r in _csv_rows(client)}
    assert rows["NOAA 18"]["avg_snr"] == "7.7"


def test_export_csv_has_header_and_rows(client, auth_headers):
    _post(client, auth_headers, satellite="NOAA 15", aos="2026-07-03T05:00:00+00:00")
    resp = client.get("/contacts/export.csv")
    assert resp.status_code == 200
    assert "satellite,aos,los" in resp.text
    assert "NOAA 15" in resp.text


def test_dashboard_filter_by_satellite(client, auth_headers):
    _post(client, auth_headers, satellite="NOAA 15", aos="2026-07-03T06:00:00+00:00",
          max_elevation=11.1)
    _post(client, auth_headers, satellite="NOAA 18", aos="2026-07-03T07:00:00+00:00",
          max_elevation=62.5)
    resp = client.get("/dashboard", params={"sat": "NOAA 18"})
    assert resp.status_code == 200
    # Elevation only appears inside a contact card, so it reflects filtering
    # (satellite names also appear in the filter dropdown and can't be used here).
    assert "62.5" in resp.text
    assert "11.1" not in resp.text


def test_observer_report_requires_auth(client):
    resp = client.post("/observer", data={"lat": "49.2", "lon": "16.8", "source": "ip"})
    assert resp.status_code == 401


def test_observer_report_rejects_bad_coords(client, auth_headers):
    resp = client.post(
        "/observer", data={"lat": "123.0", "lon": "16.8", "source": "ip"}, headers=auth_headers
    )
    assert resp.status_code == 422


def test_observer_report_moves_station(client, auth_headers):
    resp = client.post(
        "/observer",
        data={"lat": "48.1486", "lon": "17.1077", "source": "ip"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "changed": True}

    pos = client.get("/satellite/position").json()
    assert pos["observer"]["lat"] == 48.1486
    assert pos["observer"]["lon"] == 17.1077
    assert pos["observer"]["source"] == "ip"
    assert pos["observer"]["updated"] is not None


def test_observer_report_persists_across_restart(client, auth_headers):
    client.post(
        "/observer",
        data={"lat": "50.7663", "lon": "15.0543", "source": "ip"},
        headers=auth_headers,
    )

    # Simulate an app restart: clear runtime state, reload from DB.
    import shared.tle as tle
    from app.routes import map as map_routes

    tle._observer_override = None
    map_routes._observer_meta.update(source=None, updated_at=None)
    map_routes.load_station_from_db()

    assert tle.get_observer_latlon() == (50.7663, 15.0543)
    assert map_routes._observer_meta["source"] == "ip"


def test_position_preview_override(client):
    resp = client.get("/satellite/position", params={"lat": 40.0, "lon": -74.0})
    assert resp.status_code == 200
    obs = resp.json()["observer"]
    assert obs["lat"] == 40.0
    assert obs["lon"] == -74.0
    assert obs["source"] == "preview"
    assert obs["updated"] is None
    assert len(resp.json()["satellites"]) == 3


def test_position_preview_rejects_bad_coords(client):
    resp = client.get("/satellite/position", params={"lat": 200, "lon": 0})
    assert resp.status_code == 422


def test_position_preview_does_not_pollute_real_observer(client, auth_headers):
    client.post(
        "/observer",
        data={"lat": "49.2", "lon": "16.8", "source": "ip"},
        headers=auth_headers,
    )
    # A preview request must not change the persisted station position.
    client.get("/satellite/position", params={"lat": 10.0, "lon": 20.0})
    obs = client.get("/satellite/position").json()["observer"]
    assert obs["lat"] == 49.2
    assert obs["source"] == "ip"


def test_dashboard_pagination(client, auth_headers):
    for i in range(12):
        _post(client, auth_headers, satellite="NOAA 19",
              aos=f"2026-07-04T{i:02d}:00:00+00:00")
    page1 = client.get("/dashboard")
    page2 = client.get("/dashboard", params={"page": 2})
    assert page1.text.count('class="contact-card"') == 10
    assert page2.text.count('class="contact-card"') == 2
