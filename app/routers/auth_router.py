"""
routers/auth_router.py
------------------------
/login (form + verifica credenziali), /logout
"""

from pathlib import Path

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from models import Utente
from auth import verifica_password

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/login", response_class=HTMLResponse)
def form_login(request: Request, errore: str = None):
    return templates.TemplateResponse(
        request, "login.html", {"errore": errore}
    )


@router.post("/login")
def verifica_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    utente = db.query(Utente).filter(Utente.username == username).first()

    if utente is None or not verifica_password(password, utente.password_hash):
        return RedirectResponse(
            url="/login?errore=Credenziali non valide", status_code=303
        )

    request.session["utente_id"] = utente.id
    return RedirectResponse(url="/", status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
