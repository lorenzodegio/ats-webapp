"""
routers/jobs.py
-----------------
/jobs/nuovo (form + upload), /jobs (lista).

La creazione del Job avvia subito la pipeline Docker vera (fasi 1-4)
in un thread separato — vedi pipeline_docker.py — così la richiesta
HTTP risponde immediatamente (redirect a /jobs) senza aspettare che
l'intera elaborazione finisca.
"""

import threading
from pathlib import Path

from fastapi import APIRouter, Request, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from database import get_db
from models import Job, Utente, StatoJob
from auth import get_utente_corrente
from pipeline_docker import avvia_pipeline_per_job, esiste_job_in_esecuzione

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")

CARTELLA_UPLOAD = Path(__file__).parent.parent / "uploads"
CARTELLA_OUTPUT = Path(__file__).parent.parent / "output"  # cartella condivisa coi container Docker


@router.get("/jobs/nuovo", response_class=HTMLResponse)
def form_nuova_elaborazione(
    request: Request,
    utente: Utente = Depends(get_utente_corrente),
):
    return templates.TemplateResponse(
        request, "nuova_elaborazione.html",
        {"utente": utente, "voce_attiva": "nuova"},
    )


@router.post("/jobs/nuovo")
def crea_job(
    request: Request,
    file: UploadFile = File(...),
    modalita: str = Form("full"),
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    # Controllo PRIMA di creare il job: un'altra elaborazione già in
    # corso condividerebbe la stessa cartella dati/ e gli stessi
    # container — non si può avviarne una seconda in parallelo.
    if esiste_job_in_esecuzione(db):
        raise HTTPException(
            status_code=409,
            detail="Un'altra elaborazione è già in corso. Attendi che finisca prima di avviarne una nuova."
        )

    CARTELLA_UPLOAD.mkdir(parents=True, exist_ok=True)
    destinazione = CARTELLA_UPLOAD / file.filename
    with open(destinazione, "wb") as out:
        out.write(file.file.read())

    job = Job(
        nome_file_origine=file.filename,
        stato=StatoJob.in_coda,
        modalita=modalita,
        creato_da=utente,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Avvia la pipeline vera in un thread separato — non blocca questa
    # richiesta HTTP, che risponde subito col redirect. Lo stato del
    # Job avanza in background (in_coda -> fase1... -> completato/errore),
    # visibile ricaricando /jobs.
    thread = threading.Thread(
        target=avvia_pipeline_per_job,
        args=(job.id, CARTELLA_OUTPUT),
        daemon=True,
    )
    thread.start()

    return RedirectResponse(url="/jobs", status_code=303)


@router.get("/jobs", response_class=HTMLResponse)
def lista_jobs(
    request: Request,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    jobs = db.query(Job).order_by(Job.creato_il.desc()).all()
    return templates.TemplateResponse(
        request, "elaborazioni.html",
        {"utente": utente, "voce_attiva": "elaborazioni", "jobs": jobs},
    )
