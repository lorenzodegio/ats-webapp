"""Router dashboard: / (KPI, difformita per tipo, ultime elaborazioni)"""
from collections import Counter

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import get_utente_corrente
from app.models import Job, Prescrizione, Difformita, StatoJob, Utente

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    prescrizioni_totali = db.query(Prescrizione).count()
    difformita_totali = db.query(Difformita).count()
    job_in_coda = db.query(Job).filter(
        Job.stato.notin_([StatoJob.completato, StatoJob.errore])
    ).count()

    difformita_per_tipo = Counter(
        tipo for (tipo,) in db.query(Difformita.tipo).all()
    )
    # top 6 per il grafico a barre della dashboard
    difformita_per_tipo = dict(
        sorted(difformita_per_tipo.items(), key=lambda kv: kv[1], reverse=True)[:6]
    )
    max_difformita = max(difformita_per_tipo.values()) if difformita_per_tipo else 1

    ultime_elaborazioni = (
        db.query(Job)
        .options(joinedload(Job.prescrizioni))
        .order_by(Job.creato_il.desc())
        .limit(8)
        .all()
    )

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "utente": utente,
            "voce_attiva": "dashboard",
            "prescrizioni_totali": prescrizioni_totali,
            "difformita_totali": difformita_totali,
            "job_in_coda": job_in_coda,
            "difformita_per_tipo": difformita_per_tipo,
            "max_difformita": max_difformita,
            "ultime_elaborazioni": ultime_elaborazioni,
        },
    )
