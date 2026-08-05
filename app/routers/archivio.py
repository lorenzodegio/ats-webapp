"""
routers/archivio.py
---------------------
/archivio (storico prescrizioni, ricerca per barcode), /difformita (elenco).
"""

from pathlib import Path

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from models import Prescrizione, Difformita, Utente
from auth import get_utente_corrente

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/archivio", response_class=HTMLResponse)
def archivio(
    request: Request,
    barcode: str = None,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    query = db.query(Prescrizione)
    if barcode:
        query = query.filter(Prescrizione.barcode.contains(barcode))
    prescrizioni = query.order_by(Prescrizione.id.desc()).all()

    return templates.TemplateResponse(
        request, "archivio.html",
        {
            "utente": utente, "voce_attiva": "archivio",
            "prescrizioni": prescrizioni, "barcode_cercato": barcode or "",
        },
    )


@router.get("/difformita", response_class=HTMLResponse)
def difformita(
    request: Request,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    tutte = db.query(Difformita).order_by(Difformita.id.desc()).all()
    return templates.TemplateResponse(
        request, "difformita.html",
        {"utente": utente, "voce_attiva": "difformita", "difformita": tutte},
    )
