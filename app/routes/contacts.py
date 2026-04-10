import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
import jinja2
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo

from app.database import get_db
from shared.models import Contact

router = APIRouter()
_tmpl_dir = str(Path(__file__).parent.parent / "templates")
templates = Jinja2Templates(env=jinja2.Environment(
    loader=jinja2.FileSystemLoader(_tmpl_dir), autoescape=True, cache_size=0
))

IMAGES_DIR = Path(__file__).parent.parent / "static" / "images"
_TZ = ZoneInfo("Europe/Prague")


@router.post("/contacts", status_code=201)
async def create_contact(
    request: Request,
    satellite: str = Form(...),
    aos: str = Form(...),
    los: str = Form(...),
    duration_s: int = Form(...),
    max_elevation: float = Form(...),
    snr: float = Form(0.0),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    _require_auth(request)

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
        aos=datetime.fromisoformat(aos),
        los=datetime.fromisoformat(los),
        duration_s=duration_s,
        max_elevation=max_elevation,
        snr=snr,
        image_filename=image_filename,
        created_at=datetime.now(timezone.utc),
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return {"id": contact.id}


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    contacts = db.query(Contact).order_by(Contact.aos.desc()).limit(50).all()
    return templates.TemplateResponse(request, "dashboard.html", {
        "contacts": contacts,
        "tz": _TZ,
    })


def _require_auth(request: Request) -> None:
    secret = os.getenv("AGENT_SECRET", "")
    auth = request.headers.get("Authorization", "")
    if not secret or auth != f"Bearer {secret}":
        raise HTTPException(status_code=401, detail="Unauthorized")
