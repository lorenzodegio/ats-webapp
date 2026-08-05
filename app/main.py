"""
main.py
-------
Entry point FastAPI. Configura la sessione (cookie firmato), il
redirect automatico a /login quando manca autenticazione, monta i
file statici, e include tutti i router per area funzionale.

Avvio: uvicorn main:app --reload
"""

import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from database import init_db
from auth import NonAutenticato
from routers import auth_router, dashboard, jobs, archivio

BASE_DIR = Path(__file__).parent

app = FastAPI(title="ATS Cannabis OCR — Pipeline Manager")

# Chiave di firma della sessione — in produzione va letta da variabile
# d'ambiente (es. os.environ["SESSION_SECRET"]), non generata ad ogni
# avvio: altrimenti tutte le sessioni attive si invalidano ad ogni
# riavvio del server. Per lo sviluppo locale, una chiave casuale ad
# ogni avvio va bene.
app.add_middleware(SessionMiddleware, secret_key=secrets.token_hex(32))

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.exception_handler(NonAutenticato)
async def gestisci_non_autenticato(request: Request, exc: NonAutenticato):
    return RedirectResponse(url="/login", status_code=303)


@app.on_event("startup")
def all_avvio():
    init_db()


app.include_router(auth_router.router)
app.include_router(dashboard.router)
app.include_router(jobs.router)
app.include_router(archivio.router)
