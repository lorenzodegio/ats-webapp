"""
Entry point FastAPI: sessione, routing, mount static.
Avvio: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""
from dotenv import load_dotenv
load_dotenv()

import os

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.database import Base, engine, SessionLocal
from app.config_helper import assicura_pipeline_reale
from app.auth import RedirectLogin
from app.routers import auth_router, dashboard, lotti, archivio, impostazioni

# Crea le tabelle se non esistono (per dev; in produzione si userebbe Alembic)
Base.metadata.create_all(bind=engine)
_db_avvio = SessionLocal()
try:
    assicura_pipeline_reale(_db_avvio)
finally:
    _db_avvio.close()

app = FastAPI(title="ATS Gestione Prescrizioni Cannabis")

SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "cambia-questa-chiave-in-produzione")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax")


@app.middleware("http")
async def _disabilita_cache_pagine_autenticate(request: Request, call_next):
    """
    Senza questo, dopo il logout il tasto "Indietro" del browser puo'
    mostrare l'ultima pagina autenticata presa dalla cache locale (bfcache
    di Chrome/Firefox, o la normale cache HTTP) invece di rifare la
    richiesta al server: la sessione e' gia' invalidata lato server, ma
    l'utente continua a vedere il contenuto vecchio finche' non interagisce
    con la pagina. "no-store" impedisce al browser di salvare la risposta
    ed esclude esplicitamente la pagina dalla bfcache (comportamento
    documentato di Chrome/Firefox) — "Indietro" deve sempre rifare la
    richiesta e ripassare dal controllo sessione in get_utente_opzionale.
    I file statici (CSS/JS/immagini) restano cacheabili normalmente:
    applicare no-store anche a loro li rallenterebbe senza nessun
    beneficio di sicurezza (non contengono dati per-utente).
    """
    risposta = await call_next(request)
    if not request.url.path.startswith("/static/"):
        risposta.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        risposta.headers["Pragma"] = "no-cache"
        risposta.headers["Expires"] = "0"
    return risposta


app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(auth_router.router)
app.include_router(dashboard.router)
app.include_router(lotti.router)
app.include_router(archivio.router)
app.include_router(impostazioni.router)


@app.exception_handler(RedirectLogin)
def redirect_login_handler(request: Request, exc: RedirectLogin):
    return RedirectResponse(url="/login", status_code=302)
