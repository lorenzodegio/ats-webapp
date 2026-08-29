"""
Configurazione SQLAlchemy.

PostgreSQL se sono impostate POSTGRES_DB, POSTGRES_USER e POSTGRES_PASSWORD
in .env (vedi docker-compose.postgres.yml) — e' il backend in uso.
Fallback SQLite (ats_cannabis.db) solo se quelle variabili non sono
impostate. DATABASE_URL, se presente, ha priorità su entrambe le modalità.

load_dotenv() e' chiamato QUI, non solo in app/main.py: qualunque script
importi questo modulo (seed_admin.py, reset_db.py, uno script al volo)
deve leggere lo stesso .env in modo affidabile — altrimenti ricade
silenziosamente su SQLite anche con Postgres configurato, che e' esattamente
il bug che ci ha fatto perdere tempo con seed_admin.py.
"""
import os
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, event
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

if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _abilita_wal_sqlite(dbapi_connection, connection_record):
        """
        Il backend reale scrive nel log un commit per riga da un thread
        separato (real_pipeline.py, un log ogni frazione di secondo durante
        l'OCR) mentre il polling /stato legge ogni 2s da un'altra
        connessione: senza WAL i lettori possono trovare il DB
        temporaneamente bloccato dallo scrittore ("database is locked"),
        con la richiesta di polling che fallisce silenziosamente e il
        pannello che sembra fermo finche' non si ricarica la pagina.
        WAL permette letture concorrenti mentre e' in corso una scrittura;
        busy_timeout fa comunque attendere invece di fallire subito nei
        rari casi di contesa residua.
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency FastAPI: fornisce una sessione DB per request e la chiude sempre."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
