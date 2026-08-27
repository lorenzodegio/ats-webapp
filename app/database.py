"""
Configurazione SQLAlchemy.

Default: SQLite (ats_cannabis.db), adatto a sviluppo e primo avvio ATS.
PostgreSQL se sono impostate POSTGRES_DB, POSTGRES_USER e POSTGRES_PASSWORD
(vedi .env.example e docker-compose.postgres.yml).
DATABASE_URL, se presente, ha priorità su entrambe le modalità.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    postgres_db = os.getenv("POSTGRES_DB")
    postgres_user = os.getenv("POSTGRES_USER")
    postgres_password = os.getenv("POSTGRES_PASSWORD")
    if postgres_db and postgres_user and postgres_password:
        postgres_host = os.getenv("POSTGRES_HOST", "localhost")
        postgres_port = os.getenv("POSTGRES_PORT", "5432")
        DATABASE_URL = (
            f"postgresql://{postgres_user}:{postgres_password}"
            f"@{postgres_host}:{postgres_port}/{postgres_db}"
        )
    else:
        DATABASE_URL = "sqlite:///./ats_cannabis.db"

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
