"""Router autenticazione: /login /logout"""
from typing import Optional

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import RuoloUtente, Utente
from app.auth import hash_password, verifica_password, get_utente_opzionale

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
def form_login(request: Request, errore: Optional[str] = None, db: Session = Depends(get_db)):
    if get_utente_opzionale(request, db):
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

    request.session["utente_id"] = str(utente.id)
    request.session["nome_completo"] = utente.nome_completo
    request.session["ruolo"] = utente.ruolo.value
    return RedirectResponse(url="/", status_code=302)


@router.get("/register", response_class=HTMLResponse)
def form_registrazione(request: Request, errore: Optional[str] = None, db: Session = Depends(get_db)):
    if get_utente_opzionale(request, db):
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(
        "register.html", {"request": request, "errore": errore}
    )


@router.post("/register")
def esegui_registrazione(
    request: Request,
    nome: str = Form(...),
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    conferma_password: str = Form(...),
    db: Session = Depends(get_db),
):
    nome = nome.strip()
    username = username.strip()
    email = email.strip()

    def errore_form(messaggio: str):
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "errore": messaggio, "nome": nome, "username": username, "email": email},
            status_code=400,
        )

    if not nome or not username or not email:
        return errore_form("Compila tutti i campi.")
    if password != conferma_password:
        return errore_form("Le due password non coincidono.")
    if len(password) < 8:
        return errore_form("La password deve essere lunga almeno 8 caratteri.")
    if db.query(Utente).filter(Utente.username == username).first():
        return errore_form("Nome utente già in uso.")
    if db.query(Utente).filter(Utente.email == email).first():
        return errore_form("Email già in uso.")

    utente = Utente(
        nome=nome,
        username=username,
        email=email,
        password_hash=hash_password(password),
        ruolo=RuoloUtente.operatore,
    )
    db.add(utente)
    db.commit()

    return RedirectResponse(url="/login?registrato=1", status_code=302)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)
