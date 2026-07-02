import json
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import requests
from skyfield.api import EarthSatellite, load, wgs84

CACHE_DIR = Path(__file__).parent.parent / ".cache"
SATNOGS_TLE_URL = "https://db.satnogs.org/api/tle/?format=json"

# NOAA NORAD IDs: 15, 18, 19 (weather imaging satellites)
NOAA_NORAD_IDS = {25338, 28654, 33591}

CACHE_TTL_SECONDS = 12 * 3600  # refresh TLE every 12 hours
PASSES_CACHE_TTL = 300          # recompute passes every 5 minutes

ts = load.timescale(builtin=True)

_passes_cache: dict = {"data": None, "updated": 0.0}


@dataclass
class PassInfo:
    satellite: str
    aos: datetime       # UTC
    los: datetime       # UTC
    duration_s: int
    max_elevation: float
    az_at_max: float
    minutes_until: int  # minutes from now until AOS (0 or negative = ongoing)


@dataclass
class SatPosition:
    name: str
    lat: float
    lon: float
    alt_km: float
    footprint_radius_km: float
    next_pass: Optional[PassInfo]
    ground_track: list[tuple[float, float]] = field(default_factory=list)


def _cache_path() -> Path:
    return CACHE_DIR / "noaa_tle.json"


def load_noaa_satellites() -> list[EarthSatellite]:
    CACHE_DIR.mkdir(exist_ok=True)
    cache = _cache_path()

    if cache.exists() and (time.time() - cache.stat().st_mtime) < CACHE_TTL_SECONDS:
        data = json.loads(cache.read_text())
    else:
        resp = requests.get(SATNOGS_TLE_URL, timeout=10)
        resp.raise_for_status()
        all_tles = resp.json()
        data = [e for e in all_tles if e.get("norad_cat_id") in NOAA_NORAD_IDS]
        cache.write_text(json.dumps(data))

    return [
        EarthSatellite(e["tle1"], e["tle2"], e["tle0"].lstrip("0 "), ts)
        for e in data
    ]


def observer_location():
    lat = float(os.getenv("OBSERVER_LAT", "50.08"))
    lon = float(os.getenv("OBSERVER_LON", "14.44"))
    return wgs84.latlon(lat, lon)


def _footprint_radius_km(alt_km: float) -> float:
    """Ground radius of full-horizon footprint for satellite at alt_km."""
    R = 6371.0
    rho = math.acos(R / (R + alt_km))
    return round(R * rho, 0)


def predict_passes(hours: int = 24) -> list[PassInfo]:
    satellites = load_noaa_satellites()
    observer = observer_location()

    t0 = ts.now()
    t1 = ts.tt_jd(t0.tt + hours / 24.0)
    now_dt = datetime.now(timezone.utc)

    result = []
    for satellite in satellites:
        try:
            times, events = satellite.find_events(observer, t0, t1, altitude_degrees=10.0)
        except Exception:
            continue

        pass_data: dict = {}
        for t, event in zip(times, events):
            if event == 0:  # AOS
                pass_data = {"aos": t}
            elif event == 1 and "aos" in pass_data:  # TCA
                diff = satellite - observer
                alt, az, _ = diff.at(t).altaz()
                pass_data["tca"] = t
                pass_data["max_el"] = round(alt.degrees, 1)
                pass_data["az"] = round(az.degrees, 1)
            elif event == 2 and "tca" in pass_data:  # LOS
                aos_dt = pass_data["aos"].utc_datetime()
                los_dt = t.utc_datetime()
                duration_s = int((los_dt - aos_dt).total_seconds())
                minutes_until = int((aos_dt - now_dt).total_seconds() / 60)

                result.append(PassInfo(
                    satellite=satellite.name,
                    aos=aos_dt,
                    los=los_dt,
                    duration_s=duration_s,
                    max_elevation=pass_data["max_el"],
                    az_at_max=pass_data["az"],
                    minutes_until=minutes_until,
                ))
                pass_data = {}

    result.sort(key=lambda p: p.aos)
    return result


def get_cached_passes() -> list[PassInfo]:
    """Return predicted passes, recomputing at most every PASSES_CACHE_TTL (A2).

    The absolute AOS/LOS times stay valid between recomputes; only the
    ``minutes_until`` countdown is refreshed on every call so callers
    (the /passes page and the live map) always show an accurate ETA.
    """
    if time.time() - _passes_cache["updated"] > PASSES_CACHE_TTL:
        _passes_cache["data"] = predict_passes()
        _passes_cache["updated"] = time.time()

    passes = _passes_cache["data"] or []
    now_dt = datetime.now(timezone.utc)
    for p in passes:
        p.minutes_until = int((p.aos - now_dt).total_seconds() / 60)
    return passes


def current_positions() -> list[SatPosition]:
    satellites = load_noaa_satellites()
    now = ts.now()

    # Next pass per satellite (from cache; minutes_until refreshed inside).
    passes = get_cached_passes()
    next_pass_map: dict[str, PassInfo] = {}
    for p in passes:
        if p.minutes_until > -(p.duration_s / 60) and p.satellite not in next_pass_map:
            next_pass_map[p.satellite] = p

    result = []
    for satellite in satellites:
        geocentric = satellite.at(now)
        subpoint = wgs84.geographic_position_of(geocentric)
        lat = round(subpoint.latitude.degrees, 4)
        lon = round(subpoint.longitude.degrees, 4)
        alt = round(subpoint.elevation.km, 1)

        next_pass = next_pass_map.get(satellite.name)

        # Ground track: full orbital path for the next 90 minutes, sampled every
        # 60s. Vectorised into a single skyfield evaluation (A3) — the old
        # per-minute loop ran 91 propagations per satellite on every 5s poll.
        steps = 90
        minutes = np.arange(steps + 1)
        t_track = ts.tt_jd(now.tt + minutes / (24 * 60))
        track_sp = wgs84.subpoint_of(satellite.at(t_track))
        track_lats = np.round(track_sp.latitude.degrees, 4).tolist()
        track_lons = np.round(track_sp.longitude.degrees, 4).tolist()
        ground_track: list[tuple[float, float]] = list(zip(track_lats, track_lons))

        result.append(SatPosition(
            name=satellite.name,
            lat=lat,
            lon=lon,
            alt_km=alt,
            footprint_radius_km=_footprint_radius_km(alt),
            next_pass=next_pass,
            ground_track=ground_track,
        ))

    return result
