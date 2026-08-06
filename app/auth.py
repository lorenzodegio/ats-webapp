"""
Hashing password (bcrypt) e dependency di sessione.
Sessione server-side gestita da Starlette SessionMiddleware (cookie firmato).
"""
from typing import Optional

import uuid

import bcrypt
from fastapi import Request, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Utente


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verifica_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def get_utente_opzionale(request: Request, db: Session = Depends(get_db)) -> Optional[Utente]:
    """
    Ritorna l'utente loggato se presente in sessione, altrimenti None. Non reindirizza.

    Se il cookie di sessione contiene un utente_id non piu' valido come UUID
    (tipico dopo un cambio di schema: vecchie sessioni salvate prima del
    passaggio a UUID contengono ancora un intero), tratta la sessione come
    assente e la ripulisce, invece di far fallire la richiesta con un 500.
    """
    utente_id = request.session.get("utente_id")
    if not utente_id:
        return None
    try:
        uuid.UUID(str(utente_id))
    except (ValueError, AttributeError, TypeError):
        request.session.clear()
        return None
    return db.query(Utente).filter(Utente.id == utente_id).first()


class RedirectLogin(Exception):
    """Segnala che serve un redirect a /login (nessuna sessione valida)."""


def get_utente_corrente(request: Request, db: Session = Depends(get_db)) -> Utente:
    """
    Dependency per le route protette: ritorna l'utente loggato o
    solleva un redirect verso /login gestito dall'exception handler in main.py.
    """
    utente = get_utente_opzionale(request, db)
    if utente is None:
        raise RedirectLogin()
    return utente


def richiede_admin(utente: Utente = Depends(get_utente_corrente)) -> Utente:
    if not utente.is_admin:
        raise RedirectLogin()
    return utente
