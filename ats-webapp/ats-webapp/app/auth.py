"""
Hashing password (bcrypt) e dependency di sessione.
Sessione server-side gestita da Starlette SessionMiddleware (cookie firmato).
"""
from typing import Optional

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
    """Ritorna l'utente loggato se presente in sessione, altrimenti None. Non reindirizza."""
    utente_id = request.session.get("utente_id")
    if not utente_id:
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
