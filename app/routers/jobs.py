"""
Router jobs: /jobs/nuovo /jobs

Nota: l'avvio reale dei container Docker (Sezione 7.1, a carico di
Alessandro e Francesco) e' oggi sostituito da `elabora_job_fake`
(vedi app/fake_pipeline.py), che rispetta lo stesso contratto di stati.
"""
import os
import random
import uuid

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import get_utente_corrente
from app.models import Job, StatoJob, ModalitaJob, Utente
from app.fake_pipeline import elabora_job_fake

router = APIRouter(tags=["jobs"])
templates = Jinja2Templates(directory="app/templates")

UPLOAD_DIR = "uploads"
ESTENSIONI_VALIDE = {".pdf"}


@router.get("/jobs/nuovo", response_class=HTMLResponse)
def form_nuova_elaborazione(request: Request, utente: Utente = Depends(get_utente_corrente)):
    return templates.TemplateResponse(
        "nuova_elaborazione.html",
        {"request": request, "utente": utente, "voce_attiva": "nuova"},
    )


@router.post("/jobs/nuovo")
def crea_job(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    modalita: str = Form("full"),
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    _, ext = os.path.splitext(file.filename or "")
    if ext.lower() not in ESTENSIONI_VALIDE:
        return RedirectResponse(
            url="/jobs/nuovo?errore=Formato+non+valido,+atteso+PDF", status_code=302
        )

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    percorso_salvato = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}_{file.filename}")
    with open(percorso_salvato, "wb") as f:
        f.write(file.file.read())

    job = Job(
        nome_file_origine=file.filename,
        stato=StatoJob.in_coda,
        modalita=ModalitaJob(modalita) if modalita in ModalitaJob.__members__ else ModalitaJob.full,
        creato_da_id=utente.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # BACKEND FINTO: numero di prescrizioni simulato per il PDF caricato.
    # Il backend reale lo determinera' contando i barcode individuati in fase 1.
    numero_prescrizioni_simulate = random.randint(1, 6)
    background_tasks.add_task(elabora_job_fake, job.id, numero_prescrizioni_simulate)

    return RedirectResponse(url=f"/jobs?nuovo={job.id}", status_code=302)


@router.get("/jobs", response_class=HTMLResponse)
def lista_jobs(request: Request, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente)):
    jobs = (
        db.query(Job)
        .options(joinedload(Job.prescrizioni))
        .order_by(Job.creato_il.desc())
        .all()
    )
    return templates.TemplateResponse(
        "elaborazioni.html",
        {"request": request, "utente": utente, "voce_attiva": "elaborazioni", "jobs": jobs},
    )


@router.get("/jobs/{job_id}/stato")
def stato_job(job_id: int, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente)):
    """Endpoint JSON usato dal polling JS in elaborazioni.html (sostituira' un futuro WebSocket)."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if job is None:
        return JSONResponse({"errore": "job non trovato"}, status_code=404)
    return {
        "id": job.id,
        "stato": job.stato.value,
        "numero_prescrizioni": job.numero_prescrizioni,
        "numero_difformita": job.numero_difformita,
        "messaggio_errore": job.messaggio_errore,
    }
