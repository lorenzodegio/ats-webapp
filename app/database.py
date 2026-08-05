"""
database.py
------------
Configurazione SQLAlchemy per SQLite (migrabile a PostgreSQL in futuro
senza riscrivere le query — vedi documento di progetto, sezione 2.2).
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

SQLALCHEMY_DATABASE_URL = "sqlite:///./ats_webapp.db"

# check_same_thread=False: necessario per SQLite quando la stessa
# connessione può essere usata da thread diversi (es. i job in
# background) — con PostgreSQL in futuro questo argomento andrà rimosso.
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency FastAPI: apre una sessione per la richiesta, la chiude sempre a fine richiesta."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Crea le tabelle se non esistono già — da chiamare una volta all'avvio dell'app."""
    import models  # noqa: F401 — importa i modelli così sono registrati su Base prima di create_all
    Base.metadata.create_all(bind=engine)
