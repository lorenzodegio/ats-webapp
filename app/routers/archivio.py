"""Router archivio: /archivio /difformita"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import get_utente_corrente
from app.models import Prescrizione, Utente

router = APIRouter(tags=["archivio"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/archivio", response_class=HTMLResponse)
def archivio(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    query = db.query(Prescrizione).options(joinedload(Prescrizione.job))
    if q:
        query = query.filter(Prescrizione.barcode.ilike(f"%{q}%"))
    prescrizioni = query.order_by(Prescrizione.creato_il.desc()).limit(200).all()

    return templates.TemplateResponse(
        "archivio.html",
        {
            "request": request,
            "utente": utente,
            "voce_attiva": "archivio",
            "prescrizioni": prescrizioni,
            "q": q,
        },
    )

