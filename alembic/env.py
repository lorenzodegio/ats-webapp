"""
env.py — configurato per riusare DATABASE_URL e i metadata reali
dell'app (app.database/app.models), invece di duplicare la logica di
costruzione dell'URL qui dentro. Stessa fonte di verita' di app/database.py
(POSTGRES_*/.env), compreso il fallback SQLite per sviluppo locale.
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Popola Base.metadata con TUTTE le tabelle (import di app.models, non solo
# app.database): senza importare esplicitamente app.models qui, le classi
# ORM non sono ancora registrate su Base.metadata quando Alembic confronta
# lo schema per l'autogenerate, e ogni tabella sembrerebbe "da aggiungere".
from app.database import Base, DATABASE_URL
import app.models  # noqa: F401 — side effect: registra tutti i modelli su Base.metadata

config = context.config
# Sovrascrive sqlalchemy.url da alembic.ini con quello vero dell'app (stessa
# risoluzione .env di app/database.py) — alembic.ini non contiene nessuna
# credenziale, e un solo posto decide quale DB usare.
config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
