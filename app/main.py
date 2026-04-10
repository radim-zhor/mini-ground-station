from dotenv import load_dotenv
load_dotenv()

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.routes import contacts, map, passes

app = FastAPI(title="Mini Ground Station")

# Create DB tables on startup
init_db()

# Static files (APT images uploaded by agent)
_static_dir = Path(__file__).parent / "static"
_static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

app.include_router(passes.router)
app.include_router(map.router)
app.include_router(contacts.router)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/dashboard")
