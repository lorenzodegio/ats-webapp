"""Router Impostazioni (solo admin): censimento farmacie."""
import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_utente_corrente
from app.database import get_db
from app.models import Configurazione, DatiOcr, Farmacia, Utente

router = APIRouter(tags=["impostazioni"])
templates = Jinja2Templates(directory="app/templates")


def _richiede_admin(utente: Utente) -> Optional[RedirectResponse]:
    if not utente.is_admin:
        return RedirectResponse(url="/", status_code=302)
    return None


def _conta_prescrizioni(db: Session):
    righe = db.query(DatiOcr.nome_farmacia).filter(DatiOcr.nome_farmacia.isnot(None)).all()
    contatori = {}
    for (nome,) in righe:
        chiave = (nome or "").strip()
        if not chiave:
            continue
        contatori[chiave] = contatori.get(chiave, 0) + 1
    return contatori


def _dati_form(
    codice: str = "",
    nome: str = "",
    codice_regionale: str = "",
    indirizzo: str = "",
    comune: str = "",
    provincia: str = "",
    telefono: str = "",
    email: str = "",
    note: str = "",
    attiva: bool = True,
):
    return {
        "codice": codice,
        "nome": nome,
        "codice_regionale": codice_regionale,
        "indirizzo": indirizzo,
        "comune": comune,
        "provincia": provincia,
        "telefono": telefono,
        "email": email,
        "note": note,
        "attiva": attiva,
    }


@router.get("/impostazioni", response_class=HTMLResponse)
def pagina_impostazioni(
    request: Request,
    tab: str = "farmacie",
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco

    farmacie = db.query(Farmacia).order_by(Farmacia.attiva.desc(), Farmacia.nome.asc()).all()
    configurazioni = db.query(Configurazione).order_by(Configurazione.chiave.asc()).all()
    return templates.TemplateResponse(
        "impostazioni.html",
        {
            "request": request,
            "utente": utente,
            "voce_attiva": "impostazioni",
            "tab": tab if tab in ("farmacie", "sistema") else "farmacie",
            "farmacie": farmacie,
            "contatori_prescrizioni": _conta_prescrizioni(db),
            "configurazioni": configurazioni,
            "form_farmacia": _dati_form(),
            "farmacia_in_modifica": None,
            "errore": request.query_params.get("errore"),
            "ok": request.query_params.get("ok"),
        },
    )


@router.get("/impostazioni/farmacie/{farmacia_id}/modifica", response_class=HTMLResponse)
def form_modifica_farmacia(
    farmacia_id: str,
    request: Request,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco
    farmacia = db.query(Farmacia).filter(Farmacia.id == farmacia_id).first()
    if farmacia is None:
        return RedirectResponse(url="/impostazioni?errore=Farmacia+non+trovata", status_code=302)

    farmacie = db.query(Farmacia).order_by(Farmacia.attiva.desc(), Farmacia.nome.asc()).all()
    configurazioni = db.query(Configurazione).order_by(Configurazione.chiave.asc()).all()
    return templates.TemplateResponse(
        "impostazioni.html",
        {
            "request": request,
            "utente": utente,
            "voce_attiva": "impostazioni",
            "tab": "farmacie",
            "farmacie": farmacie,
            "contatori_prescrizioni": _conta_prescrizioni(db),
            "configurazioni": configurazioni,
            "form_farmacia": _dati_form(
                codice=farmacia.codice,
                nome=farmacia.nome,
                codice_regionale=farmacia.codice_regionale or "",
                indirizzo=farmacia.indirizzo or "",
                comune=farmacia.comune or "",
                provincia=farmacia.provincia or "",
                telefono=farmacia.telefono or "",
                email=farmacia.email or "",
                note=farmacia.note or "",
                attiva=farmacia.attiva,
            ),
            "farmacia_in_modifica": farmacia,
            "errore": None,
            "ok": None,
        },
    )


def _valida(codice: str, nome: str, provincia: str):
    codice = (codice or "").strip().upper()
    nome = (nome or "").strip()
    provincia = (provincia or "").strip().upper()
    if not codice:
        return None, "Il codice e' obbligatorio."
    if not nome:
        return None, "Il nome e' obbligatorio."
    if provincia and (len(provincia) < 2 or len(provincia) > 5):
        return None, "La provincia deve essere una sigla (es. VA, CO)."
    return {"codice": codice, "nome": nome, "provincia": provincia or None}, None


@router.post("/impostazioni/farmacie")
def crea_farmacia(
    codice: str = Form(""),
    nome: str = Form(""),
    codice_regionale: str = Form(""),
    indirizzo: str = Form(""),
    comune: str = Form(""),
    provincia: str = Form(""),
    telefono: str = Form(""),
    email: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco
    puliti, errore = _valida(codice, nome, provincia)
    if errore:
        return RedirectResponse(url=f"/impostazioni?errore={errore}", status_code=302)
    if db.query(Farmacia).filter(Farmacia.codice == puliti["codice"]).first():
        return RedirectResponse(url="/impostazioni?errore=Codice+gia+in+uso", status_code=302)

    db.add(Farmacia(
        codice=puliti["codice"],
        nome=puliti["nome"],
        codice_regionale=(codice_regionale or "").strip() or None,
        indirizzo=(indirizzo or "").strip() or None,
        comune=(comune or "").strip() or None,
        provincia=puliti["provincia"],
        telefono=(telefono or "").strip() or None,
        email=(email or "").strip() or None,
        note=(note or "").strip() or None,
        attiva=True,
    ))
    db.commit()
    return RedirectResponse(url="/impostazioni?ok=creata", status_code=302)


@router.post("/impostazioni/farmacie/{farmacia_id}/modifica")
def salva_farmacia(
    farmacia_id: str,
    codice: str = Form(""),
    nome: str = Form(""),
    codice_regionale: str = Form(""),
    indirizzo: str = Form(""),
    comune: str = Form(""),
    provincia: str = Form(""),
    telefono: str = Form(""),
    email: str = Form(""),
    note: str = Form(""),
    attiva: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco
    farmacia = db.query(Farmacia).filter(Farmacia.id == farmacia_id).first()
    if farmacia is None:
        return RedirectResponse(url="/impostazioni?errore=Farmacia+non+trovata", status_code=302)

    puliti, errore = _valida(codice, nome, provincia)
    if errore:
        return RedirectResponse(
            url=f"/impostazioni/farmacie/{farmacia_id}/modifica?errore={errore}",
            status_code=302,
        )
    doppione = (
        db.query(Farmacia)
        .filter(Farmacia.codice == puliti["codice"], Farmacia.id != farmacia.id)
        .first()
    )
    if doppione:
        return RedirectResponse(url="/impostazioni?errore=Codice+gia+in+uso", status_code=302)

    farmacia.codice = puliti["codice"]
    farmacia.nome = puliti["nome"]
    farmacia.codice_regionale = (codice_regionale or "").strip() or None
    farmacia.indirizzo = (indirizzo or "").strip() or None
    farmacia.comune = (comune or "").strip() or None
    farmacia.provincia = puliti["provincia"]
    farmacia.telefono = (telefono or "").strip() or None
    farmacia.email = (email or "").strip() or None
    farmacia.note = (note or "").strip() or None
    farmacia.attiva = attiva is not None
    farmacia.updated_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(url="/impostazioni?ok=modificata", status_code=302)


@router.post("/impostazioni/farmacie/{farmacia_id}/disattiva")
def disattiva_farmacia(
    farmacia_id: str,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco
    farmacia = db.query(Farmacia).filter(Farmacia.id == farmacia_id).first()
    if farmacia:
        farmacia.attiva = False
        farmacia.updated_at = datetime.utcnow()
        db.commit()
    return RedirectResponse(url="/impostazioni?ok=disattivata", status_code=302)


@router.post("/impostazioni/farmacie/{farmacia_id}/riattiva")
def riattiva_farmacia(
    farmacia_id: str,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco
    farmacia = db.query(Farmacia).filter(Farmacia.id == farmacia_id).first()
    if farmacia:
        farmacia.attiva = True
        farmacia.updated_at = datetime.utcnow()
        db.commit()
    return RedirectResponse(url="/impostazioni?ok=riattivata", status_code=302)


@router.post("/impostazioni/farmacie/{farmacia_id}/elimina")
def elimina_farmacia(
    farmacia_id: str,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco
    farmacia = db.query(Farmacia).filter(Farmacia.id == farmacia_id).first()
    if farmacia:
        db.delete(farmacia)
        db.commit()
    return RedirectResponse(url="/impostazioni?ok=eliminata", status_code=302)


@router.get("/impostazioni/farmacie/export-csv")
def export_csv_farmacie(
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco
    farmacie = db.query(Farmacia).order_by(Farmacia.nome.asc()).all()
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([
        "codice", "nome", "codice_regionale", "indirizzo", "comune",
        "provincia", "telefono", "email", "attiva", "note",
    ])
    for f in farmacie:
        writer.writerow([
            f.codice, f.nome, f.codice_regionale or "", f.indirizzo or "",
            f.comune or "", f.provincia or "", f.telefono or "", f.email or "",
            "si" if f.attiva else "no", f.note or "",
        ])
    contenuto = buffer.getvalue().encode("utf-8-sig")
    return Response(
        content=contenuto,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="farmacie_ats.csv"'},
    )
