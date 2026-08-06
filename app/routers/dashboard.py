"""
Router dashboard: / — cruscotto iniziale.

Due sezioni:
1. "In corso ora": elaborazioni non ancora completate/in errore, con
   avanzamento live (aggiornato via polling JS).
2. "Panoramica generale": KPI e difformita per tipo, filtrabili per
   intervallo di date, stato e modalita.
"""
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import get_utente_corrente
from app.models import Job, Prescrizione, Difformita, StatoJob, ModalitaJob, Utente
from app.progresso import percentuale_avanzamento, etichetta_fase

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")

STATI_ATTIVI = [
    StatoJob.in_coda,
    StatoJob.fase1_preprocessing,
    StatoJob.fase2_ocr,
    StatoJob.fase3_excel,
    StatoJob.fase4_difformita,
]


def _parse_data(valore: Optional[str]):
    if not valore:
        return None
    try:
        return datetime.strptime(valore, "%Y-%m-%d")
    except ValueError:
        return None


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    da: Optional[str] = None,
    a: Optional[str] = None,
    stato: Optional[str] = None,
    modalita: Optional[str] = None,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    # ---------- Pannello "in corso ora" ----------
    job_attivi = (
        db.query(Job)
        .options(joinedload(Job.prescrizioni))
        .filter(Job.stato.in_(STATI_ATTIVI))
        .order_by(Job.creato_il.desc())
        .all()
    )
    job_attivi_vista = [
        {
            "job": j,
            "percentuale": percentuale_avanzamento(j.stato),
            "etichetta_fase": etichetta_fase(j.stato),
        }
        for j in job_attivi
    ]

    # ---------- Panoramica generale (filtrabile) ----------
    query_job = db.query(Job)

    data_da = _parse_data(da)
    data_a = _parse_data(a)
    if data_da:
        query_job = query_job.filter(Job.creato_il >= data_da)
    if data_a:
        query_job = query_job.filter(Job.creato_il < data_a + timedelta(days=1))
    if stato and stato in StatoJob.__members__:
        query_job = query_job.filter(Job.stato == StatoJob(stato))
    if modalita and modalita in ModalitaJob.__members__:
        query_job = query_job.filter(Job.modalita == ModalitaJob(modalita))

    job_filtrati_ids = [j.id for j in query_job.with_entities(Job.id).all()]

    job_totali = len(job_filtrati_ids)

    prescrizioni_query = db.query(Prescrizione).filter(Prescrizione.job_id.in_(job_filtrati_ids or [-1]))
    prescrizioni_totali = prescrizioni_query.count()

    difformita_query = (
        db.query(Difformita)
        .join(Prescrizione)
        .filter(Prescrizione.job_id.in_(job_filtrati_ids or [-1]))
    )
    difformita_totali = difformita_query.count()

    difformita_per_tipo = Counter(tipo for (tipo,) in difformita_query.with_entities(Difformita.tipo).all())
    difformita_per_tipo = dict(
        sorted(difformita_per_tipo.items(), key=lambda kv: kv[1], reverse=True)[:6]
    )
    max_difformita = max(difformita_per_tipo.values()) if difformita_per_tipo else 1

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "utente": utente,
            "voce_attiva": "dashboard",
            "job_attivi_vista": job_attivi_vista,
            "job_totali": job_totali,
            "prescrizioni_totali": prescrizioni_totali,
            "difformita_totali": difformita_totali,
            "difformita_per_tipo": difformita_per_tipo,
            "max_difformita": max_difformita,
            "filtro_da": da or "",
            "filtro_a": a or "",
            "filtro_stato": stato or "",
            "filtro_modalita": modalita or "",
            "stati_disponibili": list(StatoJob),
            "modalita_disponibili": list(ModalitaJob),
        },
    )
