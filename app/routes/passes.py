from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
import jinja2
from fastapi.templating import Jinja2Templates

from shared.tle import predict_passes

router = APIRouter()
_tmpl_dir = str(Path(__file__).parent.parent / "templates")
templates = Jinja2Templates(env=jinja2.Environment(
    loader=jinja2.FileSystemLoader(_tmpl_dir), autoescape=True, cache_size=0
))

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
