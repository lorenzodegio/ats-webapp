"""
Configurazione SQLAlchemy (SQLite).
Migrabile a PostgreSQL cambiando solo DATABASE_URL.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./ats_cannabis.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency FastAPI: fornisce una sessione DB per request e la chiude sempre."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
