"""
routers/dashboard.py
----------------------
GET / — dashboard con KPI, difformità per tipo, ultime elaborazioni.
Dati reali dal database (non mock statici), come da documento (Sezione
6.3: "Pronta" per Dashboard, a differenza delle altre pagine ancora
"dati statici").
"""

from pathlib import Path

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import Job, Prescrizione, Difformita, Utente, StatoJob
from auth import get_utente_corrente

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    n_prescrizioni = db.query(func.count(Prescrizione.id)).scalar() or 0
    n_difformita = db.query(func.count(Difformita.id)).scalar() or 0
    n_in_coda = db.query(func.count(Job.id)).filter(Job.stato == StatoJob.in_coda).scalar() or 0

    difformita_per_tipo = (
        db.query(Difformita.tipo, func.count(Difformita.id))
        .group_by(Difformita.tipo)
        .order_by(func.count(Difformita.id).desc())
        .all()
    )

    ultime_elaborazioni = (
        db.query(Job).order_by(Job.creato_il.desc()).limit(5).all()
    )

    return templates.TemplateResponse(
        request, "dashboard.html",
        {
            "utente": utente,
            "voce_attiva": "dashboard",
            "n_prescrizioni": n_prescrizioni,
            "n_difformita": n_difformita,
            "n_in_coda": n_in_coda,
            "difformita_per_tipo": difformita_per_tipo,
            "ultime_elaborazioni": ultime_elaborazioni,
        },
    )
