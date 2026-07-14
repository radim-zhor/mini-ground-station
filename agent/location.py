"""
location.py — auto-detection of the (mobile) ground-station position.

The station moves, so the observer position is detected from IP geolocation
(ipinfo.io — HTTPS, no API key) instead of a fixed env value. Accuracy is
city-level (a few km), which is more than enough for pass prediction: the
NOAA footprint is ~3000 km wide and a few km shift changes AOS by seconds.

Modes (env OBSERVER_MODE):
    auto    (default) — IP geolocation, falling back to OBSERVER_LAT/LON
    manual            — always use OBSERVER_LAT/LON (old fixed-station mode)

Caveat: a VPN makes IP geolocation report the VPN exit. Use manual mode then.
"""
import logging
import os
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

GEO_URL = "https://ipinfo.io/json"
CACHE_TTL = 900  # re-detect at most every 15 min

_cache: dict = {"pos": None, "source": None, "ts": 0.0}


def detect_location(ttl: int = CACHE_TTL) -> tuple:
    """
    Return (lat, lon, source) of the station.

    source: "ip" (geolocated), "manual" (OBSERVER_MODE=manual),
    or "fallback" (detection failed → env/default values).
    Results are cached for `ttl` seconds to avoid hammering the API.
    """
    if os.getenv("OBSERVER_MODE", "auto").lower() == "manual":
        lat, lon = _env_fallback()
        return (lat, lon, "manual")

    now = time.time()
    if _cache["pos"] is not None and now - _cache["ts"] < ttl:
        return (*_cache["pos"], _cache["source"])

    pos = _ip_geolocate()
    if pos is not None:
        _cache.update(pos=pos, source="ip", ts=now)
        return (*pos, "ip")

    lat, lon = _env_fallback()
    log.warning("IP geolocation failed — falling back to %.4f, %.4f", lat, lon)
    # Cache the fallback too, so a dead network doesn't retry every call.
    _cache.update(pos=(lat, lon), source="fallback", ts=now)
    return (lat, lon, "fallback")


def _ip_geolocate() -> Optional[tuple]:
    try:
        resp = requests.get(GEO_URL, timeout=10)
        resp.raise_for_status()
        loc = resp.json().get("loc", "")
        lat_s, lon_s = loc.split(",")
        return (float(lat_s), float(lon_s))
    except Exception as e:
        log.debug("ipinfo.io lookup failed: %s", e)
        return None


def _env_fallback() -> tuple:
    return (
        float(os.getenv("OBSERVER_LAT", "50.08")),
        float(os.getenv("OBSERVER_LON", "14.44")),
    )
