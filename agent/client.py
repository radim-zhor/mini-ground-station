"""
client.py — posts contact data to the web app after a pass.

On network failure the contact is saved to a local SQLite (pending.db)
and retried at the start of the next pass.
"""
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

_API_URL = os.getenv("APP_URL", "http://localhost:8000")
_SECRET = os.getenv("AGENT_SECRET", "")
_PENDING_DB = Path(__file__).parent.parent / "pending.db"


def post_contact(
    satellite: str,
    aos: datetime,
    los: datetime,
    duration_s: int,
    max_elevation: float,
    snr: float,
    avg_snr: Optional[float] = None,
    notes: Optional[str] = None,
    png_path: Optional[Path] = None,
    contact_type: str = "image",
    telemetry: Optional[dict] = None,
    events: Optional[list] = None,
) -> bool:
    """
    POST a contact to the web app.

    Returns True on success, False if saved to pending queue instead.
    """
    data = {
        "satellite": satellite,
        "aos": aos.isoformat(),
        "los": los.isoformat(),
        "duration_s": str(duration_s),
        "max_elevation": str(max_elevation),
        "snr": str(snr),
        "contact_type": contact_type,
    }
    if avg_snr is not None:
        data["avg_snr"] = str(avg_snr)
    if notes:
        data["notes"] = notes
    if telemetry:
        data["telemetry"] = json.dumps(telemetry)
    if events:
        data["events"] = json.dumps(events)
    files = {}
    if png_path and png_path.exists():
        files["image"] = open(png_path, "rb")

    try:
        resp = requests.post(
            f"{_API_URL}/contacts",
            data=data,
            files=files or None,
            headers={"Authorization": f"Bearer {_SECRET}"},
            timeout=30,
        )
        resp.raise_for_status()
        log.info("Contact posted (id=%s)", resp.json().get("id"))
        return True
    except Exception as e:
        log.warning("POST /contacts failed (%s) — saving to pending queue", e)
        _save_pending(satellite, aos, los, duration_s, max_elevation, snr, avg_snr,
                      notes, png_path, contact_type, telemetry, events)
        return False
    finally:
        for f in files.values():
            f.close()


def post_observer(lat: float, lon: float, source: str) -> bool:
    """
    Report the station's current position to the web app, so the map pin and
    server-side pass predictions follow the mobile station.

    Best-effort: on failure just returns False — the scheduler re-reports on
    its next location refresh, so no pending queue is needed.
    """
    try:
        resp = requests.post(
            f"{_API_URL}/observer",
            data={"lat": str(lat), "lon": str(lon), "source": source},
            headers={"Authorization": f"Bearer {_SECRET}"},
            timeout=15,
        )
        resp.raise_for_status()
        log.info("Observer position reported: %.4f, %.4f (%s)", lat, lon, source)
        return True
    except Exception as e:
        log.warning("POST /observer failed (%s) — will retry on next refresh", e)
        return False


def post_live(
    state: str,
    satellite: Optional[str] = None,
    aos: Optional[datetime] = None,
    los: Optional[datetime] = None,
    elapsed_s: Optional[float] = None,
    total_s: Optional[float] = None,
    snr: Optional[float] = None,
    note: Optional[str] = None,
    health: Optional[dict] = None,
) -> bool:
    """
    Push the station's current state to the web app (the /pass console).

    Fire and forget: this runs inside the recording loop, so it gets a short
    timeout and swallows everything. A dropped status update is invisible —
    a dropped chunk of the pass is not.
    """
    data = {"state": state}
    for key, value in (
        ("satellite", satellite),
        ("aos", aos.isoformat() if aos else None),
        ("los", los.isoformat() if los else None),
        ("elapsed_s", elapsed_s),
        ("total_s", total_s),
        ("snr", snr),
        ("note", note),
        ("health", json.dumps(health) if health else None),
    ):
        if value is not None:
            data[key] = str(value)

    try:
        requests.post(
            f"{_API_URL}/station/live",
            data=data,
            headers={"Authorization": f"Bearer {_SECRET}"},
            timeout=5,
        )
        return True
    except Exception:
        return False


def post_event(kind: str, detail: str = "", satellite: Optional[str] = None,
               aos: Optional[datetime] = None) -> bool:
    """Report one pass-timeline entry. Best effort, like post_live()."""
    data = {"kind": kind, "detail": detail}
    if satellite:
        data["satellite"] = satellite
    if aos:
        data["aos"] = aos.isoformat()

    try:
        requests.post(
            f"{_API_URL}/station/event",
            data=data,
            headers={"Authorization": f"Bearer {_SECRET}"},
            timeout=5,
        )
        return True
    except Exception:
        return False


def retry_pending() -> None:
    """Retry all pending contacts. Call this at agent startup."""
    if not _PENDING_DB.exists():
        return

    conn = _open_pending_db()
    rows = conn.execute(
        "SELECT id, satellite, aos, los, duration_s, max_elevation, snr, avg_snr, notes, "
        "png_path, contact_type, telemetry, events FROM pending"
    ).fetchall()
    if not rows:
        conn.close()
        return

    log.info("Retrying %d pending contact(s)...", len(rows))
    for row in rows:
        (id_, satellite, aos, los, duration_s, max_elevation, snr, avg_snr, notes,
         png_path, contact_type, telemetry, events) = row
        ok = post_contact(
            satellite,
            datetime.fromisoformat(aos),
            datetime.fromisoformat(los),
            duration_s,
            max_elevation,
            snr,
            avg_snr,
            notes,
            Path(png_path) if png_path else None,
            contact_type or "image",
            json.loads(telemetry) if telemetry else None,
            json.loads(events) if events else None,
        )
        if ok:
            conn.execute("DELETE FROM pending WHERE id = ?", (id_,))
            conn.commit()
    conn.close()


def _save_pending(
    satellite, aos, los, duration_s, max_elevation, snr, avg_snr, notes, png_path,
    contact_type="image", telemetry=None, events=None,
) -> None:
    conn = _open_pending_db()
    conn.execute("""
        INSERT INTO pending
            (satellite, aos, los, duration_s, max_elevation, snr, avg_snr, notes,
             png_path, contact_type, telemetry, events, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        satellite,
        aos.isoformat(),
        los.isoformat(),
        duration_s,
        max_elevation,
        snr,
        avg_snr,
        notes,
        str(png_path) if png_path else None,
        contact_type,
        json.dumps(telemetry) if telemetry else None,
        json.dumps(events) if events else None,
        datetime.now(timezone.utc).isoformat(),
    ))
    conn.commit()
    conn.close()


def _open_pending_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_PENDING_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pending (
            id          INTEGER PRIMARY KEY,
            satellite   TEXT NOT NULL,
            aos         TEXT NOT NULL,
            los         TEXT NOT NULL,
            duration_s  INTEGER NOT NULL,
            max_elevation REAL NOT NULL,
            snr         REAL,
            avg_snr     REAL,
            notes       TEXT,
            png_path    TEXT,
            contact_type TEXT,
            telemetry   TEXT,
            events      TEXT,
            created_at  TEXT NOT NULL
        )
    """)
    # A queue written by an older agent lacks the newer columns; adding them
    # here keeps contacts that are already waiting from being lost.
    have = {row[1] for row in conn.execute("PRAGMA table_info(pending)")}
    for column, ddl in (("contact_type", "TEXT"), ("telemetry", "TEXT"), ("events", "TEXT")):
        if column not in have:
            conn.execute(f"ALTER TABLE pending ADD COLUMN {column} {ddl}")
    conn.commit()
    return conn
