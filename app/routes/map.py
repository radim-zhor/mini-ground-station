import os
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
import jinja2
from fastapi.templating import Jinja2Templates

from shared.tle import current_positions

router = APIRouter()
_tmpl_dir = str(Path(__file__).parent.parent / "templates")
templates = Jinja2Templates(env=jinja2.Environment(
    loader=jinja2.FileSystemLoader(_tmpl_dir), autoescape=True, cache_size=0
))

_TZ = ZoneInfo("Europe/Prague")


@router.get("/map", response_class=HTMLResponse)
async def map_page(request: Request):
    observer_lat = float(os.getenv("OBSERVER_LAT", "50.08"))
    observer_lon = float(os.getenv("OBSERVER_LON", "14.44"))
    return templates.TemplateResponse("map.html", {
        "request": request,
        "observer_lat": observer_lat,
        "observer_lon": observer_lon,
    })


@router.get("/satellite/position")
async def satellite_position():
    positions = current_positions()
    observer_lat = float(os.getenv("OBSERVER_LAT", "50.08"))
    observer_lon = float(os.getenv("OBSERVER_LON", "14.44"))

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

    return {
        "satellites": satellites,
        "observer": {"lat": observer_lat, "lon": observer_lon},
    }
