from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import jinja2
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_agent_auth
from app.database import get_db
from shared.models import StationStatus
from shared.tle import current_positions, get_observer_latlon, set_observer

router = APIRouter()
_tmpl_dir = str(Path(__file__).parent.parent / "templates")
templates = Jinja2Templates(env=jinja2.Environment(
    loader=jinja2.FileSystemLoader(_tmpl_dir), autoescape=True, cache_size=0
))

_TZ = ZoneInfo("Europe/Prague")

# In-process metadata about the last observer report (mirrors station_status).
_observer_meta: dict = {"source": None, "updated_at": None}


@router.get("/map", response_class=HTMLResponse)
async def map_page(request: Request):
    observer_lat, observer_lon = get_observer_latlon()
    return templates.TemplateResponse(request, "map.html", {
        "observer_lat": observer_lat,
        "observer_lon": observer_lon,
    })


@router.post("/observer")
async def report_observer(
    request: Request,
    lat: float = Form(...),
    lon: float = Form(...),
    source: str = Form("manual"),
    db: Session = Depends(get_db),
):
    """Agent reports the mobile station's current position."""
    require_agent_auth(request)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(status_code=422, detail="Invalid coordinates")

    now = datetime.now(timezone.utc)
    row = db.get(StationStatus, 1)
    if row is None:
        row = StationStatus(id=1, lat=lat, lon=lon, source=source, updated_at=now)
        db.add(row)
    else:
        row.lat, row.lon, row.source, row.updated_at = lat, lon, source, now
    db.commit()

    changed = set_observer(lat, lon)  # invalidates the passes cache on move
    _observer_meta.update(source=source, updated_at=now)
    return {"ok": True, "changed": changed}


@router.get("/satellite/position")
async def satellite_position():
    positions = current_positions()
    observer_lat, observer_lon = get_observer_latlon()

    satellites = []
    for pos in positions:
        np = pos.next_pass
        satellites.append({
            "name": pos.name,
            "lat": pos.lat,
            "lon": pos.lon,
            "alt_km": pos.alt_km,
            "footprint_radius_km": pos.footprint_radius_km,
            "ground_track": list(pos.ground_track),
            "next_pass": {
                "minutes_until": np.minutes_until,
                "max_elevation": np.max_elevation,
                "aos": np.aos.astimezone(_TZ).strftime("%H:%M %Z"),
                "los": np.los.astimezone(_TZ).strftime("%H:%M %Z"),
                "duration_s": np.duration_s,
            } if np else None,
        })

    updated_at: Optional[datetime] = _observer_meta["updated_at"]
    if updated_at is not None and updated_at.tzinfo is None:
        # SQLite drops tzinfo on round-trip; stored values are UTC.
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return {
        "satellites": satellites,
        "observer": {
            "lat": observer_lat,
            "lon": observer_lon,
            "source": _observer_meta["source"],
            "updated": updated_at.astimezone(_TZ).strftime("%H:%M %Z") if updated_at else None,
        },
    }


def load_station_from_db() -> None:
    """Restore the last reported station position on app startup."""
    from app.database import SessionLocal

    try:
        with SessionLocal() as db:
            row = db.get(StationStatus, 1)
    except Exception:
        return  # table missing (pre-migration) — env fallback applies
    if row is not None:
        set_observer(row.lat, row.lon)
        _observer_meta.update(source=row.source, updated_at=row.updated_at)
