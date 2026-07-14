from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Contact(Base):
    """One recorded satellite pass."""

    __tablename__ = "contacts"

    # A pass is uniquely identified by which satellite and when it started (A4).
    # Applies to freshly created tables; existing DBs keep the app-level guard.
    __table_args__ = (UniqueConstraint("satellite", "aos", name="uq_contact_pass"),)

    id = Column(Integer, primary_key=True)
    satellite = Column(String, nullable=False)
    aos = Column(DateTime(timezone=True), nullable=False)
    los = Column(DateTime(timezone=True), nullable=False)
    duration_s = Column(Integer, nullable=False)
    max_elevation = Column(Float, nullable=False)
    snr = Column(Float, nullable=True)          # peak SNR over the pass (dB)
    avg_snr = Column(Float, nullable=True)      # mean SNR across 10 s windows (dB)
    quality = Column(String, nullable=True)     # ok / degraded / lost (derived)
    notes = Column(String, nullable=True)       # free text (decode status, errors)
    image_filename = Column(String, nullable=True)   # PNG stored in app/static/images/
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class StationStatus(Base):
    """Current position of the (mobile) ground station, reported by the agent.

    Single-row table (id=1). The agent auto-detects its location and POSTs it
    to /observer; the web app persists it here so the map and pass predictions
    follow the station across restarts.
    """

    __tablename__ = "station_status"

    id = Column(Integer, primary_key=True)      # always 1
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    source = Column(String, nullable=False, default="manual")  # ip / manual / fallback
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
