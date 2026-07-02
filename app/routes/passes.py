from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import jinja2
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from shared.tle import get_cached_passes

router = APIRouter()
_tmpl_dir = str(Path(__file__).parent.parent / "templates")
templates = Jinja2Templates(env=jinja2.Environment(
    loader=jinja2.FileSystemLoader(_tmpl_dir), autoescape=True, cache_size=0
))

_TZ = ZoneInfo("Europe/Prague")


@router.get("/passes", response_class=HTMLResponse)
async def passes_page(request: Request):
    passes = get_cached_passes()
    return templates.TemplateResponse(request, "passes.html", {
        "passes": passes,
        "now": datetime.now(_TZ),
        "tz": _TZ,
    })
