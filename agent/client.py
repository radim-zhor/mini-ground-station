"""
client.py — posts contact data to the web app after a pass.

On network failure the contact is saved to a local SQLite (pending.db)
and retried at the start of the next pass.
"""
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
    png_path: Optional[Path] = None,
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
    }
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
        _save_pending(satellite, aos, los, duration_s, max_elevation, snr, png_path)
        return False
    finally:
        for f in files.values():
            f.close()


def retry_pending() -> None:
    """Retry all pending contacts. Call this at agent startup."""
    if not _PENDING_DB.exists():
        return

    conn = _open_pending_db()
    rows = conn.execute("SELECT id, satellite, aos, los, duration_s, max_elevation, snr, png_path FROM pending").fetchall()
    if not rows:
        conn.close()
        return

    log.info("Retrying %d pending contact(s)...", len(rows))
    for row in rows:
        id_, satellite, aos, los, duration_s, max_elevation, snr, png_path = row
        ok = post_contact(
            satellite,
            datetime.fromisoformat(aos),
            datetime.fromisoformat(los),
            duration_s,
            max_elevation,
            snr,
            Path(png_path) if png_path else None,
        )
        if ok:
            conn.execute("DELETE FROM pending WHERE id = ?", (id_,))
            conn.commit()
    conn.close()


def _save_pending(
    satellite, aos, los, duration_s, max_elevation, snr, png_path
) -> None:
    conn = _open_pending_db()
    conn.execute("""
        INSERT INTO pending (satellite, aos, los, duration_s, max_elevation, snr, png_path, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        satellite,
        aos.isoformat(),
        los.isoformat(),
        duration_s,
        max_elevation,
        snr,
        str(png_path) if png_path else None,
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
            png_path    TEXT,
            created_at  TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn
