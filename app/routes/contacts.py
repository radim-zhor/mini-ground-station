import csv
import io
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import jinja2
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import require_agent_auth
from app.database import get_db
from shared.models import Contact

router = APIRouter()
_tmpl_dir = str(Path(__file__).parent.parent / "templates")
templates = Jinja2Templates(env=jinja2.Environment(
    loader=jinja2.FileSystemLoader(_tmpl_dir), autoescape=True, cache_size=0
))

IMAGES_DIR = Path(__file__).parent.parent / "static" / "images"
_TZ = ZoneInfo("Europe/Prague")

PAGE_SIZE = 10

# Reception-quality thresholds on peak SNR (dB). Heuristic for NOAA APT — a
# decoded image can never be classed "lost", only "ok" or "degraded".
QUALITY_OK_DB = 10.0
QUALITY_DEGRADED_DB = 3.0


@router.post("/contacts", status_code=201)
async def create_contact(
    request: Request,
    satellite: str = Form(...),
    aos: str = Form(...),
    los: str = Form(...),
    duration_s: int = Form(...),
    max_elevation: float = Form(...),
    snr: float = Form(0.0),
    avg_snr: Optional[float] = Form(None),
    notes: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    require_agent_auth(request)

    aos_dt = _parse_dt(aos, "aos")
    los_dt = _parse_dt(los, "los")

    # Idempotency (A4): the agent retries pending contacts, so the same pass may
    # arrive twice. A pass is uniquely identified by (satellite, aos) — if we
    # already have it, return the existing row instead of inserting a duplicate.
    existing = _find_existing(db, satellite, aos_dt)
    if existing:
        return JSONResponse({"id": existing.id, "duplicate": True}, status_code=200)

    image_filename = None
    if image and image.filename:
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        safe = satellite.replace(" ", "_")
        ts = aos[:19].replace(":", "-").replace("T", "_")
        image_filename = f"{safe}_{ts}.png"
        with open(IMAGES_DIR / image_filename, "wb") as f:
            shutil.copyfileobj(image.file, f)

    contact = Contact(
        satellite=satellite,
        aos=aos_dt,
        los=los_dt,
        duration_s=duration_s,
        max_elevation=max_elevation,
        snr=snr,
        avg_snr=avg_snr,
        quality=_classify_quality(snr, has_image=image_filename is not None),
        notes=notes,
        image_filename=image_filename,
        created_at=datetime.now(timezone.utc),
    )
    db.add(contact)
    try:
        db.commit()
    except IntegrityError:
        # Raced with a concurrent insert (or app-level check missed due to a
        # timezone round-trip) — the DB unique constraint caught it.
        db.rollback()
        existing = _find_existing(db, satellite, aos_dt)
        if existing:
            return JSONResponse({"id": existing.id, "duplicate": True}, status_code=200)
        raise
    db.refresh(contact)
    return {"id": contact.id}


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    sat: Optional[str] = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    page = max(1, page)
    base = db.query(Contact)
    if sat:
        base = base.filter(Contact.satellite == sat)

    total = base.count()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    contacts = (
        base.order_by(Contact.aos.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
        .all()
    )

    satellites = [
        r[0] for r in db.query(Contact.satellite).distinct().order_by(Contact.satellite).all()
    ]
    chart = _snr_chart(_chart_rows(db, sat))

    return templates.TemplateResponse(request, "dashboard.html", {
        "contacts": contacts,
        "tz": _TZ,
        "satellites": satellites,
        "sat": sat,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "chart": chart,
    })


@router.get("/contacts/export.csv")
async def export_csv(db: Session = Depends(get_db)):
    rows = db.query(Contact).order_by(Contact.aos.desc()).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "satellite", "aos", "los", "duration_s", "max_elevation",
        "snr", "avg_snr", "quality", "notes", "image_filename", "created_at",
    ])
    for c in rows:
        writer.writerow([
            c.id, c.satellite, c.aos.isoformat(), c.los.isoformat(),
            c.duration_s, c.max_elevation, c.snr, c.avg_snr, c.quality,
            c.notes or "", c.image_filename or "",
            c.created_at.isoformat() if c.created_at else "",
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=contacts.csv"},
    )


# ── Helpers ─────────────────────────────────────────────────────────────────

def _find_existing(db: Session, satellite: str, aos_dt: datetime) -> Optional[Contact]:
    return (
        db.query(Contact)
        .filter(Contact.satellite == satellite, Contact.aos == aos_dt)
        .first()
    )


def _classify_quality(snr: Optional[float], has_image: bool) -> str:
    peak = snr or 0.0
    if peak >= QUALITY_OK_DB:
        return "ok"
    if peak >= QUALITY_DEGRADED_DB or has_image:
        return "degraded"
    return "lost"


def _chart_rows(db: Session, sat: Optional[str]) -> list[Contact]:
    """Last 30 contacts with a SNR value, oldest first, for the trend chart."""
    q = db.query(Contact).filter(Contact.snr.isnot(None))
    if sat:
        q = q.filter(Contact.satellite == sat)
    rows = q.order_by(Contact.aos.desc()).limit(30).all()
    return list(reversed(rows))


def _snr_chart(rows: list[Contact]) -> Optional[dict]:
    """Build inline-SVG geometry for a SNR-over-time sparkline."""
    pts = [(c.aos, c.snr) for c in rows if c.snr is not None]
    if len(pts) < 2:
        return None

    width, height, pad = 860, 150, 26
    snrs = [s for _, s in pts]
    lo, hi = min(snrs), max(snrs)
    if hi == lo:
        hi = lo + 1
    n = len(pts)

    coords, dots = [], []
    for i, (t, s) in enumerate(pts):
        x = round(pad + (width - 2 * pad) * i / (n - 1), 1)
        y = round(height - pad - (height - 2 * pad) * (s - lo) / (hi - lo), 1)
        coords.append(f"{x},{y}")
        dots.append({"x": x, "y": y, "snr": s, "label": t.astimezone(_TZ).strftime("%m-%d %H:%M")})

    return {
        "width": width,
        "height": height,
        "polyline": " ".join(coords),
        "dots": dots,
        "lo": round(lo, 1),
        "hi": round(hi, 1),
    }


def _parse_dt(value: str, field: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        # Bad input from the agent is a client error (A6), not a 500.
        raise HTTPException(status_code=422, detail=f"Invalid {field} datetime: {value!r}")
