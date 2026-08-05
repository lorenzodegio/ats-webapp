"""
auth.py
-------
Hashing password (bcrypt via passlib) e dependency di sessione per
l'autenticazione — sessione server-side "classica" come da documento
di progetto (Sezione 2.2): l'id utente viene salvato in un cookie di
sessione firmato (Starlette SessionMiddleware, configurato in main.py),
non serve un token/JWT separato per uno strumento interno in LAN.
"""

from fastapi import Request, Depends, HTTPException
import bcrypt
from sqlalchemy.orm import Session

from database import get_db
from models import Utente


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verifica_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


class NonAutenticato(Exception):
    """
    Sollevata da get_utente_corrente quando manca una sessione valida.
    Un exception_handler registrato in main.py la trasforma in un
    redirect verso /login, invece di un generico 401 — più adatto a
    un'applicazione con pagine web (non un'API pura).
    """
    pass


def get_utente_corrente(request: Request, db: Session = Depends(get_db)) -> Utente:
    """
    Dependency da usare in ogni route che richiede login. Legge l'id
    utente dalla sessione e restituisce l'oggetto Utente corrispondente.
    """
    utente_id = request.session.get("utente_id")
    if utente_id is None:
        raise NonAutenticato()

    utente = db.query(Utente).filter(Utente.id == utente_id).first()
    if utente is None:
        # L'utente in sessione non esiste più nel DB (es. eliminato) —
        # ripulisco la sessione invece di lasciarla in uno stato invalido
        request.session.clear()
        raise NonAutenticato()

    return utente


def richiedi_admin(utente: Utente = Depends(get_utente_corrente)) -> Utente:
    """Dependency aggiuntiva per le route riservate agli amministratori (es. Impostazioni)."""
    if utente.ruolo != "admin":
        raise HTTPException(status_code=403, detail="Accesso riservato agli amministratori.")
    return utente
