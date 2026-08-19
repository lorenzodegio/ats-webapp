"""
main.py
-------
Entry point FastAPI. Configura la sessione (cookie firmato), il
redirect automatico a /login quando manca autenticazione, monta i
file statici, e include tutti i router per area funzionale.

Avvio: uvicorn main:app --reload
Richiede un file .env con le credenziali PostgreSQL (vedi .env.example).
"""

import logging
import secrets
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()  # DEVE avvenire prima di "from database import init_db",
                # che legge le variabili d'ambiente al momento dell'import

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from database import init_db
from auth import NonAutenticato
from routers import auth_router, dashboard, jobs, archivio

# Configurazione esplicita del logging — prima non c'era, e senza
# questa i log degli altri moduli (pipeline_docker.py, ecc.) potevano
# non comparire formattati correttamente a seconda di come uvicorn
# viene avviato. Ora tutti i moduli condividono lo stesso formato.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("Server")

BASE_DIR = Path(__file__).parent

app = FastAPI(title="ATS Cannabis OCR — Pipeline Manager")

@app.middleware("http")
async def logga_richieste(request: Request, chiamata_successiva):
    """
    Logga ogni richiesta HTTP: metodo, percorso, utente (se già
    autenticato), stato della risposta e durata — prima non c'era
    nessuna traccia di "chi ha fatto cosa e quando" nei log, solo i
    log interni delle singole route.

    IMPORTANTE sull'ordine: questo middleware deve essere registrato
    PRIMA di app.add_middleware(SessionMiddleware, ...) qui sotto nel
    codice — Starlette esegue per primo l'ULTIMO middleware registrato,
    quindi registrando SessionMiddleware dopo, diventa lo strato più
    esterno ed esegue per primo, rendendo request.session già
    disponibile quando questo middleware lo legge. Con l'ordine
    invertito, request.session solleva un errore perché la sessione
    non è ancora stata agganciata alla richiesta.
    """
    inizio = time.monotonic()
    username = request.session.get("username", "non autenticato")

    risposta = await chiamata_successiva(request)

    durata = time.monotonic() - inizio
    log.info(
        f"{request.method} {request.url.path} — utente={username} "
        f"stato={risposta.status_code} durata={durata*1000:.0f}ms"
    )
    return risposta


# Chiave di firma della sessione — in produzione va letta da variabile
# d'ambiente (es. os.environ["SESSION_SECRET"]), non generata ad ogni
# avvio: altrimenti tutte le sessioni attive si invalidano ad ogni
# riavvio del server. Per lo sviluppo locale, una chiave casuale ad
# ogni avvio va bene.
#
# Registrato DOPO logga_richieste — vedi spiegazione sopra sull'ordine.
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
