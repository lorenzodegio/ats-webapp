"""Router autenticazione: /login /logout"""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Utente
from app.auth import verifica_password

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
def form_login(request: Request, errore: str | None = None):
    if request.session.get("utente_id"):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(
        "login.html", {"request": request, "errore": errore}
    )


@router.post("/login")
def esegui_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    utente = db.query(Utente).filter(Utente.username == username).first()
    if utente is None or not verifica_password(password, utente.password_hash):
        return RedirectResponse(
            url="/login?errore=Credenziali+non+valide", status_code=302
        )

    request.session["utente_id"] = utente.id
    request.session["nome_completo"] = utente.nome_completo
    request.session["ruolo"] = utente.ruolo.value
    return RedirectResponse(url="/", status_code=302)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)
