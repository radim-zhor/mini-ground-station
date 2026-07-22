"""The agent's pending queue must not drop telemetry when the network is down."""
import sqlite3

import pytest

from agent import client


@pytest.fixture
def offline_queue(tmp_path, monkeypatch):
    """Point the pending queue at a temp file and make every POST fail."""
    monkeypatch.setattr(client, "_PENDING_DB", tmp_path / "pending.db")

    def unreachable(*a, **kw):
        raise OSError("network is down")

    monkeypatch.setattr(client.requests, "post", unreachable)
    return tmp_path / "pending.db"


def _post(**kw):
    from datetime import datetime, timezone

    defaults = dict(
        satellite="ORBCOMM FM 118",
        aos=datetime(2026, 7, 22, 0, 43, tzinfo=timezone.utc),
        los=datetime(2026, 7, 22, 0, 52, tzinfo=timezone.utc),
        duration_s=540,
        max_elevation=48.0,
        snr=6.1,
    )
    defaults.update(kw)
    return client.post_contact(**defaults)


def test_telemetry_survives_a_failed_post(offline_queue):
    stats = {"packets": 98, "per": 0.0, "ephemeris": [{"lat": 46.75}]}

    assert _post(contact_type="telemetry", telemetry=stats) is False

    conn = sqlite3.connect(offline_queue)
    row = conn.execute("SELECT contact_type, telemetry FROM pending").fetchone()
    conn.close()
    assert row[0] == "telemetry"
    assert "98" in row[1]


def test_queued_telemetry_is_replayed_on_retry(offline_queue, monkeypatch):
    stats = {"packets": 98, "per": 0.0}
    _post(contact_type="telemetry", telemetry=stats)

    sent = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": 7}

    def ok(url, data=None, files=None, headers=None, timeout=None):
        sent.update(data)
        return Resp()

    monkeypatch.setattr(client.requests, "post", ok)
    client.retry_pending()

    assert sent["contact_type"] == "telemetry"
    assert '"packets": 98' in sent["telemetry"]

    conn = sqlite3.connect(offline_queue)
    remaining = conn.execute("SELECT COUNT(*) FROM pending").fetchone()[0]
    conn.close()
    assert remaining == 0


def test_a_queue_from_an_older_agent_is_upgraded_in_place(offline_queue):
    # An agent that predates M2.3 left rows behind; adding the columns must not
    # lose them.
    conn = sqlite3.connect(offline_queue)
    conn.execute("""
        CREATE TABLE pending (
            id INTEGER PRIMARY KEY, satellite TEXT NOT NULL, aos TEXT NOT NULL,
            los TEXT NOT NULL, duration_s INTEGER NOT NULL, max_elevation REAL NOT NULL,
            snr REAL, avg_snr REAL, notes TEXT, png_path TEXT, created_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO pending (satellite, aos, los, duration_s, max_elevation, created_at) "
        "VALUES ('METEOR M2-4', '2026-07-01T00:00:00+00:00', '2026-07-01T00:10:00+00:00', "
        "600, 70.0, '2026-07-01T00:11:00+00:00')"
    )
    conn.commit()
    conn.close()

    _post(contact_type="telemetry", telemetry={"packets": 5})

    conn = sqlite3.connect(offline_queue)
    rows = conn.execute("SELECT satellite, contact_type FROM pending ORDER BY id").fetchall()
    conn.close()
    assert rows == [("METEOR M2-4", None), ("ORBCOMM FM 118", "telemetry")]
