"""
database.py
------------
Configurazione SQLAlchemy per PostgreSQL, containerizzato (vedi
docker-compose.postgres.yml) — prima qui c'era SQLite, nonostante
main.py e seed_admin.py chiamassero già load_dotenv() apposta per
questo file (commenti: "DEVE avvenire prima di from database import
init_db, che legge le variabili d'ambiente"). Il collegamento vero e
proprio a PostgreSQL non era mai stato fatto.

Richiede un file .env nella radice del progetto (copia .env.example e
personalizza) con POSTGRES_HOST/PORT/DB/USER/PASSWORD, e il container
Postgres avviato:
    docker compose -f docker-compose.postgres.yml up -d

Richiede anche il driver psycopg2 nel venv (non serve dentro i
container Docker delle 4 fasi, che non toccano il database):
    pip install psycopg2-binary
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB")
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")

# Fallisce SUBITO e con un messaggio chiaro se manca qualcosa, invece di
# scoprirlo più tardi con un errore di connessione criptico (o peggio,
# invece di cadere silenziosamente su un default sbagliato) — lo stesso
# tipo di ambiguità SQLite-vs-Postgres che questo cambio doveva risolvere.
if not all([POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD]):
    raise RuntimeError(
        "Variabili d'ambiente PostgreSQL mancanti (POSTGRES_DB / "
        "POSTGRES_USER / POSTGRES_PASSWORD). Copia .env.example in .env "
        "nella radice del progetto e personalizza i valori prima di "
        "avviare il server."
    )

SQLALCHEMY_DATABASE_URL = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

# Niente più connect_args={"check_same_thread": False}: serviva solo per
# SQLite, che non gestisce nativamente connessioni da thread diversi
# (es. i job in background avviati da jobs.py). PostgreSQL/psycopg2
# gestiscono la concorrenza a livello di pool di connessioni in modo
# nativo — quell'argomento non è nemmeno valido per psycopg2 e
# romperebbe la connessione se lasciato.
engine = create_engine(SQLALCHEMY_DATABASE_URL)
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
