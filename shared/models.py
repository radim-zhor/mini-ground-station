from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Integer, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Contact(Base):
    """One recorded satellite pass."""

    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True)
    satellite = Column(String, nullable=False)
    aos = Column(DateTime(timezone=True), nullable=False)
    los = Column(DateTime(timezone=True), nullable=False)
    duration_s = Column(Integer, nullable=False)
    max_elevation = Column(Float, nullable=False)
    snr = Column(Float, nullable=True)
    image_filename = Column(String, nullable=True)   # PNG stored in app/static/images/
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
