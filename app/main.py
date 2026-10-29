"""
Entry point FastAPI: sessione, routing, mount static.
Avvio: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""
from dotenv import load_dotenv
load_dotenv()

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Configurato PRIMA di qualunque altro import dell'app: finora non esisteva
# nessuna configurazione di logging nella webapp (nessun basicConfig), quindi
# i log.info()/log.warning() sparsi in app/* non finivano da nessuna parte
# (root logger senza handler). LOG_DIR default alla cartella locale del
# progetto per lo sviluppo, sovrascritta in produzione (E:\apps\ocr-cannabis\logs).
LOG_DIR = Path(os.environ.get("LOG_DIR", str(Path(__file__).resolve().parent.parent / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)

_formato_log = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_handler_file = RotatingFileHandler(
    LOG_DIR / "ats-webapp.log", maxBytes=10 * 1024 * 1024, backupCount=10, encoding="utf-8",
)
_handler_file.setFormatter(_formato_log)
_handler_console = logging.StreamHandler()
_handler_console.setFormatter(_formato_log)
logging.basicConfig(level=logging.INFO, handlers=[_handler_file, _handler_console])

# "RealPipeline" (app/real_pipeline.py) inoltra anche l'output grezzo della
# pipeline OCR riga per riga (vedi log_elaborazione), che puo' contenere dati
# della prescrizione (nomi, codici fiscali, importi...) — quel canale resta
# SOLO nel DB (tabella log_elaborazioni, gia' visibile nell'interfaccia
# dell'elaborazione) e in console per il debug dal vivo: niente dati di
# prescrizione nel file di log su disco, per questo qui sotto NON eredita
# l'handler su file configurato sopra (propagate=False).
_handler_console_pipeline = logging.StreamHandler()
_handler_console_pipeline.setFormatter(_formato_log)
_log_pipeline = logging.getLogger("RealPipeline")
_log_pipeline.addHandler(_handler_console_pipeline)
_log_pipeline.propagate = False

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import PlainTextResponse

from app.database import Base, engine, SessionLocal
from app.config_helper import assicura_pipeline_reale
from app.auth import RedirectLogin
from app.real_pipeline import recupera_elaborazioni_orfane
from app.storage import verifica_storage_produzione, APP_ENV
from app.routers import auth_router, dashboard, lotti, archivio, impostazioni

# APP_ENV=production: l'app si rifiuta di partire se lo storage configurato
# non e' valido o punta ancora alla cartella finta di sviluppo — vedi
# app/storage.py::verifica_storage_produzione per il motivo (bug storico
# gia' capitato: scrittura silenziosa in locale invece che su SharePoint).
# No-op fuori produzione (comportamento di sviluppo invariato).
verifica_storage_produzione()

# Crea le tabelle se non esistono — SOLO fuori produzione. In produzione lo
# schema e' gestito da Alembic (cartella alembic/, baseline nella prima
# revisione): create_all() e' solo additivo e non saprebbe mai applicare
# una modifica a una colonna esistente, quindi va disattivato una volta che
# il DB ha dati reali — vedi DEPLOY_WINDOWS.md per "alembic upgrade head".
if APP_ENV != "production":
    Base.metadata.create_all(bind=engine)
_db_avvio = SessionLocal()
try:
    assicura_pipeline_reale(_db_avvio)
    # Un riavvio del server (volontario, crash, o --reload durante lo
    # sviluppo) uccide qualunque thread Python stesse seguendo
    # un'elaborazione reale: senza questo, il lotto resta bloccato "in
    # corso" per sempre, senza nessuna via di recupero dall'interfaccia.
    recupera_elaborazioni_orfane(_db_avvio)
finally:
    _db_avvio.close()

app = FastAPI(title="ATS Gestione Prescrizioni Cannabis")

SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "").strip()
if not SECRET_KEY:
    if APP_ENV == "production":
        raise RuntimeError(
            "SESSION_SECRET_KEY non impostata: obbligatoria in produzione "
            "(APP_ENV=production), l'app non parte senza — genera una chiave "
            "casuale e mettila nel .env, vedi DEPLOY_WINDOWS.md."
        )
    # Fuori produzione un default fisso va benissimo: comodo per lo
    # sviluppo locale (sessioni stabili tra un riavvio e l'altro di
    # uvicorn), nessun rischio perche' non e' un ambiente esposto.
    SECRET_KEY = "cambia-questa-chiave-in-produzione"
# max_age=None (invece del default Starlette di 14 giorni): il cookie di
# sessione non ha piu' un attributo di scadenza, quindi il browser lo
# scarta alla chiusura completa (non alla chiusura di una singola scheda)
# — senza login persiste per 14 giorni anche chiudendo l'app, con questo
# richiede l'accesso ogni volta che il browser viene chiuso del tutto.
# https_only aggiunge il flag Secure al cookie (mai inviato su HTTP in
# chiaro) — attivo solo in produzione, dove IIS fa da reverse proxy HTTPS
# davanti a uvicorn; in sviluppo locale (HTTP semplice) lo spegnerebbe del
# tutto. HttpOnly e' gia' sempre impostato da SessionMiddleware di suo,
# non c'e' un parametro per disattivarlo.
app.add_middleware(
    SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", max_age=None,
    https_only=(APP_ENV == "production"),
)

# Limite dimensione upload — difesa in piu' oltre a qualunque limite gia'
# impostato in IIS: senza questo un upload enorme (o un client malevolo)
# potrebbe tenere occupata la richiesta a lungo prima di essere scartata a
# valle. Controllato su Content-Length (dichiarato dal client): non è una
# garanzia assoluta contro un client che mente sulla dimensione, ma copre
# il caso normale (upload PDF dal wizard) senza dover bufferizzare il
# corpo per contarlo qui.
MAX_UPLOAD_SIZE_BYTES = int(os.environ.get("MAX_UPLOAD_SIZE_MB", "200")) * 1024 * 1024


@app.middleware("http")
async def _limita_dimensione_upload(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_UPLOAD_SIZE_BYTES:
                return PlainTextResponse(
                    f"Corpo della richiesta troppo grande (limite {MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB).",
                    status_code=413,
                )
        except ValueError:
            pass
    return await call_next(request)


@app.get("/health")
def health_check():
    """
    Endpoint per WinSW (riavvio automatico in caso di errore) e per
    status.ps1 — verifica che l'app risponda E che il DB sia raggiungibile
    (un 200 che nasconde un DB giu' sarebbe inutile per un controllo di
    salute). Nessun dato sensibile nella risposta.
    """
    from sqlalchemy import text
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except Exception as exc:
        return JSONResponse({"status": "errore", "dettaglio": "database non raggiungibile"}, status_code=503)
    return {"status": "ok"}


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
