"""
station.py — the live pass console (/pass) and the agent's status feed

The agent sits behind NAT, so the app cannot ask it anything. Same pattern as
POST /observer: the agent pushes, the browser polls.

    agent (Mac)                         app (Render)          browser
    recorder, every chunk:
      ├ SNR, % done       ──POST /station/live──→  holds   ──poll 5 s──→ /pass
      └ satellite, AOS/LOS                       last state

The state is deliberately **in memory**. It describes what the station is doing
right now, and after an app restart the honest answer is "I don't know" — the
agent's next heartbeat says more than a stale row would. The station's
*position* is a different thing and does live in the database.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import jinja2
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import require_agent_auth
from shared.tle import current_azel, get_cached_passes, pass_track

router = APIRouter()
_tmpl_dir = str(Path(__file__).parent.parent / "templates")
templates = Jinja2Templates(env=jinja2.Environment(
    loader=jinja2.FileSystemLoader(_tmpl_dir), autoescape=True, cache_size=0
))

_TZ = ZoneInfo("Europe/Prague")

# How long the agent may stay silent before we call it offline. It heartbeats
# every ~10 s even when idle, so 30 s is three missed beats.
OFFLINE_AFTER_S = 30

VALID_STATES = {"idle", "waiting", "recording", "decoding", "done", "error"}

SNR_HISTORY = 400  # samples kept per pass (a 15 min pass at 2 s ≈ 450)
EVENT_HISTORY = 100

_live: dict = {
    "payload": None,
    "received_at": None,
    "snr_series": [],
    "events": [],
    "health": {},
    "pass_key": None,
}


# ── Agent feed ────────────────────────────────────────────────────────────────

@router.post("/station/live")
async def report_live(
    request: Request,
    state: str = Form(...),
    satellite: Optional[str] = Form(None),
    aos: Optional[str] = Form(None),
    los: Optional[str] = Form(None),
    elapsed_s: Optional[float] = Form(None),
    total_s: Optional[float] = Form(None),
    snr: Optional[float] = Form(None),
    note: Optional[str] = Form(None),
    health: Optional[str] = Form(None),
):
    """Agent reports what it is doing. Called every couple of seconds."""
    require_agent_auth(request)
    if state not in VALID_STATES:
        raise HTTPException(status_code=422, detail=f"Unknown state: {state!r}")
    health_data = _parse_json_object(health, "health")

    payload = {
        "state": state,
        "satellite": satellite,
        "aos": aos,
        "los": los,
        "elapsed_s": elapsed_s,
        "total_s": total_s,
        "snr": snr,
        "note": note,
    }

    _start_new_pass_if_needed(satellite, aos)
    if snr is not None and elapsed_s is not None:
        _live["snr_series"].append((round(elapsed_s, 1), round(snr, 1)))
        del _live["snr_series"][:-SNR_HISTORY]

    if health_data:
        _live["health"] = health_data
    _live["payload"] = payload
    _live["received_at"] = datetime.now(timezone.utc)
    return {"ok": True}


@router.post("/station/event")
async def report_event(
    request: Request,
    kind: str = Form(...),
    detail: str = Form(""),
    satellite: Optional[str] = Form(None),
    aos: Optional[str] = Form(None),
):
    """One entry in the pass timeline, reported as it happens."""
    require_agent_auth(request)
    _start_new_pass_if_needed(satellite, aos)

    _live["events"].append({
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "detail": detail,
    })
    del _live["events"][:-EVENT_HISTORY]
    _live["received_at"] = datetime.now(timezone.utc)
    return {"ok": True}


def _start_new_pass_if_needed(satellite: Optional[str], aos: Optional[str]) -> None:
    """The SNR curve and the timeline belong to one pass; a new pass clears both."""
    key = f"{satellite}@{aos}" if satellite and aos else None
    if key != _live["pass_key"]:
        _live["pass_key"] = key
        _live["snr_series"] = []
        _live["events"] = []


def _parse_json_object(raw: Optional[str], field: str) -> Optional[dict]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid {field} JSON")
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail=f"{field} must be a JSON object")
    return data


@router.get("/station/live")
async def live_status():
    """Last reported state, or `offline` when the agent has gone quiet."""
    return _live_state()


def _live_state() -> dict:
    payload, received = _live["payload"], _live["received_at"]
    if payload is None or received is None:
        return {"state": "offline", "last_seen_s": None, "online": False}

    age = (datetime.now(timezone.utc) - received).total_seconds()
    online = age <= OFFLINE_AFTER_S
    data = dict(payload)
    data["last_seen_s"] = round(age, 1)
    data["online"] = online
    if not online:
        # Keep the reported fields for context, but do not claim it is still
        # recording — nobody has heard from the station in half a minute.
        data["last_state"] = payload["state"]
        data["state"] = "offline"
    return data


def reset_live() -> None:
    """Drop the live state (used by tests; also what a restart looks like)."""
    _live.update(payload=None, received_at=None, snr_series=[], events=[],
                 health={}, pass_key=None)


# ── Station health (M3.5) ─────────────────────────────────────────────────────

def station_health(live: dict) -> dict:
    """
    The health panel: what the hardware is doing, and what looks wrong.

    The warnings are the point. Each one is a failure that cost us a real pass
    in July and was invisible until someone read the right terminal.
    """
    h = dict(_live["health"])
    warnings = []

    if not live.get("online"):
        warnings.append("Agent se neozývá, stanice nenahrává.")

    sdr = h.get("sdr")
    if sdr == "missing":
        warnings.append("SDR není připojený.")
    elif sdr == "busy":
        warnings.append("SDR drží jiný proces (SatDump?), nahrávání selže.")
    elif isinstance(sdr, str) and sdr.startswith("error"):
        warnings.append(f"SDR hlásí chybu: {sdr[7:]}")

    if h.get("lna") and h.get("bias_tee") is False:
        warnings.append("Bias tee je vypnutý, ale LNA je v cestě: přijde o ~14 dB.")

    if h.get("agc"):
        warnings.append("Zapnuté AGC: s LNA před tunerem přebuzuje na vysokém přeletu.")

    if h.get("mock"):
        warnings.append("Agent běží v MOCK režimu, data jsou syntetická.")

    used, cap = h.get("disk_used_gb"), h.get("disk_cap_gb")
    if used is not None and cap:
        h["disk_pct"] = round(100 * used / cap, 1)
        if used >= 0.9 * cap:
            warnings.append(f"Nahrávky zabírají {used:.1f} z {cap:.1f} GB, úklid je za dveřmi.")

    h["warnings"] = warnings
    h["known"] = bool(_live["health"])
    return h


# ── The page ──────────────────────────────────────────────────────────────────

@router.get("/pass", response_class=HTMLResponse)
async def pass_page(request: Request):
    return templates.TemplateResponse(request, "pass.html", _pass_context(request))


@router.get("/pass/panel", response_class=HTMLResponse)
async def pass_panel(request: Request):
    """The polled fragment — same context, no page chrome."""
    return templates.TemplateResponse(request, "pass_panel.html", _pass_context(request))


def _pass_context(request: Request) -> dict:
    live = _live_state()
    p = _subject_pass(live)

    track, sky, live_pos = [], None, None
    if p is not None:
        track = pass_track(p.satellite, p.aos, p.los)
        if live["state"] == "recording" and live.get("satellite") == p.satellite:
            live_pos = current_azel(p.satellite)
        sky = _sky_plot(track, live_pos)

    return {
        "live": live,
        "pass": p,
        "sky": sky,
        "live_pos": live_pos,
        "snr_chart": _snr_chart(_live["snr_series"]),
        "progress": _progress(live),
        "events": list(reversed(_live["events"])),  # newest first
        "health": station_health(live),
        "now": datetime.now(timezone.utc),
        "tz": _TZ,
    }


def _subject_pass(live: dict):
    """
    Which pass the page is about: the one being recorded, else the next one.

    Without an agent (or between passes) the page still has something to show —
    that is the point of predicting passes in the first place.
    """
    passes = get_cached_passes()
    if not passes:
        return None

    sat, aos = live.get("satellite"), live.get("aos")
    if sat and aos:
        for p in passes:
            if p.satellite == sat and p.aos.isoformat() == aos:
                return p

    now = datetime.now(timezone.utc)
    upcoming = sorted((p for p in passes if p.los > now), key=lambda p: p.aos)
    return upcoming[0] if upcoming else None


def _progress(live: dict) -> Optional[dict]:
    elapsed, total = live.get("elapsed_s"), live.get("total_s")
    if not total or elapsed is None:
        return None
    pct = max(0.0, min(100.0, 100.0 * elapsed / total))
    return {
        "pct": round(pct, 1),
        "elapsed_s": int(elapsed),
        "total_s": int(total),
        "remaining_s": max(int(total - elapsed), 0),
    }


# ── Drawing ───────────────────────────────────────────────────────────────────

SKY_SIZE = 320
SKY_R = 140


def _polar_xy(az: float, el: float) -> tuple:
    """Az/el to sky-plot coordinates: zenith at the centre, horizon at the rim."""
    import math

    r = SKY_R * (1 - max(min(el, 90.0), 0.0) / 90.0)
    rad = math.radians(az)
    c = SKY_SIZE / 2
    return (round(c + r * math.sin(rad), 1), round(c - r * math.cos(rad), 1))


def _sky_plot(track: list, live_pos: Optional[dict]) -> Optional[dict]:
    """Geometry for the inline-SVG polar plot of the pass."""
    visible = [pt for pt in track if pt["el"] >= 0]
    if len(visible) < 2:
        return None

    points = [_polar_xy(pt["az"], pt["el"]) for pt in visible]
    tca = max(visible, key=lambda pt: pt["el"])

    return {
        "size": SKY_SIZE,
        "r": SKY_R,
        "polyline": " ".join(f"{x},{y}" for x, y in points),
        "aos": {"xy": points[0], "az": visible[0]["az"]},
        "los": {"xy": points[-1], "az": visible[-1]["az"]},
        "tca": {"xy": _polar_xy(tca["az"], tca["el"]), "az": tca["az"], "el": tca["el"]},
        "rings": [
            {"r": round(SKY_R * (1 - el / 90.0), 1), "label": f"{el}°"}
            for el in (30, 60)
        ],
        # Only when the satellite is actually up: _polar_xy clamps a negative
        # elevation onto the horizon ring, which would draw a confident dot for
        # a satellite that is nowhere near the sky yet.
        "live": (
            _polar_xy(live_pos["az"], live_pos["el"])
            if live_pos and live_pos["el"] >= 0
            else None
        ),
    }


def _snr_chart(series: list) -> Optional[dict]:
    """Live SNR sparkline: signal against time since AOS."""
    if len(series) < 2:
        return None

    width, height, pad = 620, 110, 22
    xs = [t for t, _ in series]
    ys = [s for _, s in series]
    lo, hi = min(ys), max(ys)
    if hi == lo:
        hi = lo + 1
    span = max(xs[-1] - xs[0], 1)

    coords = []
    for t, s in series:
        x = round(pad + (width - 2 * pad) * (t - xs[0]) / span, 1)
        y = round(height - pad - (height - 2 * pad) * (s - lo) / (hi - lo), 1)
        coords.append(f"{x},{y}")

    return {
        "width": width,
        "height": height,
        "polyline": " ".join(coords),
        "lo": round(lo, 1),
        "hi": round(hi, 1),
        "last": ys[-1],
        "points": len(series),
    }
