from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from shared.tle import predict_passes

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

_TZ = ZoneInfo("Europe/Prague")


@router.get("/passes", response_class=HTMLResponse)
async def passes_page(request: Request):
    passes = predict_passes(hours=24)
    return templates.TemplateResponse("passes.html", {
        "request": request,
        "passes": passes,
        "now": datetime.now(_TZ),
        "tz": _TZ,
    })
