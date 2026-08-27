"""
Entry point FastAPI: sessione, routing, mount static.
Avvio: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""
import os

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.database import Base, engine
from app.auth import RedirectLogin
from app.routers import auth_router, dashboard, lotti, archivio, impostazioni

# Crea le tabelle se non esistono (per dev; in produzione si userebbe Alembic)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="ATS Gestione Prescrizioni Cannabis")

SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "cambia-questa-chiave-in-produzione")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(auth_router.router)
app.include_router(dashboard.router)
app.include_router(lotti.router)
app.include_router(archivio.router)
app.include_router(impostazioni.router)


@app.exception_handler(RedirectLogin)
def redirect_login_handler(request: Request, exc: RedirectLogin):
    return RedirectResponse(url="/login", status_code=302)
