import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.models import Base

_DEFAULT_DB = f"sqlite:///{Path(__file__).parent.parent / 'ground_station.db'}"
DATABASE_URL = os.getenv("DATABASE_URL", _DEFAULT_DB)

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
