"""
Shared pytest fixtures.

Sets an isolated SQLite DB and a known agent secret *before* the app is
imported (env is read at import time in app.database / routes), then provides
a hermetic TLE cache so no test hits the SatNOGS network.
"""
import os
import shutil
import tempfile
from pathlib import Path

import pytest

# ── Must run before app modules are imported ────────────────────────────────
# Per-process filename so two concurrent pytest runs don't share one SQLite file.
_TMP_DB = Path(tempfile.gettempdir()) / f"ground_station_test_{os.getpid()}.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ["AGENT_SECRET"] = "test-secret"
os.environ.setdefault("OBSERVER_LAT", "49.20")
os.environ.setdefault("OBSERVER_LON", "16.82")

_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def hermetic_tle(monkeypatch, tmp_path):
    """Point shared.tle at a fresh copy of the fixture TLE cache.

    The freshly written file is always within the 12 h TTL, so
    load_noaa_satellites never falls through to the network.
    """
    import shared.tle as tle

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    shutil.copy(_FIXTURES / "noaa_tle.json", cache_dir / "noaa_tle.json")

    monkeypatch.setattr(tle, "CACHE_DIR", cache_dir)
    # Reset in-process state so each test starts cleanly.
    monkeypatch.setattr(tle, "_passes_cache", {"data": None, "updated": 0.0})
    monkeypatch.setattr(tle, "_observer_override", None)

    from app.routes import map as map_routes
    monkeypatch.setattr(map_routes, "_observer_meta", {"source": None, "updated_at": None})
    return cache_dir


@pytest.fixture(autouse=True)
def clean_db():
    """Reset the contacts table before each test so rows don't leak across tests."""
    from app.database import engine, init_db
    from shared.models import Base

    Base.metadata.drop_all(engine)
    init_db()
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-secret"}
