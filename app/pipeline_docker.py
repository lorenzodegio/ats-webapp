"""
pipeline_docker.py
--------------------
Due responsabilità, corrispondenti ai punti 1 e 2 del filone backend
(documento, Sezione 7.1):

1. avvia_pipeline_per_job() — orchestra le 4 fasi come container Docker
   Compose in sequenza, aggiornando Job.stato man mano che procedono
   (sostituisce il TODO che c'era in jobs.py).

2. popola_prescrizioni_da_output() / popola_difformita_da_output() —
   leggono i JSON prodotti dalle fasi OCR e Difformità e creano le
   righe Prescrizione/Difformita nel database, invece di lasciare
   /archivio e /difformita sui dati statici attuali.

Nomi dei servizi Docker, corrispondenti a quelli definiti in
docker-compose.yml (cartella radice del progetto) e docker/Dockerfile:
"fase1-preprocessing", "fase2-ocr", "fase3-difformita", "fase4-excel"
— NOTA l'ordine (difformità PRIMA di excel, il contrario del testo
del documento Sezione 1.2): vedi models.py, docstring di StatoJob,
per la spiegazione completa.

Ogni servizio ha il proprio comando già fissato in docker-compose.yml
(non serve passare argomenti da qui) — tutti i percorsi sono cartelle
condivise via volume Docker (./dati sull'host, /dati nei container),
vedi docker/run_fase.py.
"""

import json
import logging
import subprocess
from pathlib import Path

from database import SessionLocal
from models import Job, Prescrizione, Difformita, StatoJob

log = logging.getLogger("PipelineDocker")

# Nomi dei file JSON prodotti dalla pipeline che NON sono singole
# prescrizioni (riepiloghi aggregati) — da escludere quando si legge
# la cartella di output
FILE_JSON_DA_ESCLUDERE = {"riepilogo.json", "riepilogo_difformita.json"}


def _esegui_container(nome_servizio: str, argomenti: list[str]) -> None:
    """
    Esegue UN container Docker Compose e aspetta che finisca. Solleva
    un'eccezione se il container termina con codice di errore, così il
    chiamante può marcare il Job come StatoJob.errore invece di
    proseguire alla fase successiva su dati incompleti.
    """
    comando = ["docker", "compose", "run", "--rm", nome_servizio, *argomenti]
    log.info(f"Eseguo: {' '.join(comando)}")
    risultato = subprocess.run(comando, capture_output=True, text=True)

    if risultato.returncode != 0:
        raise RuntimeError(
            f"Container '{nome_servizio}' terminato con errore "
            f"(codice {risultato.returncode}): {risultato.stderr[-500:]}"
        )
    log.info(f"Container '{nome_servizio}' completato con successo.")


def popola_prescrizioni_da_output(db, job: Job, cartella_output: Path) -> int:
    """
    Legge tutti i JSON prodotti dalla fase 2 (OCR) per questo job, e
    crea (o aggiorna, se già presente) una riga Prescrizione per
    ciascuno. Restituisce il numero di prescrizioni create/aggiornate.
    """
    contatore = 0
    for json_path in cartella_output.glob("*.json"):
        if json_path.name in FILE_JSON_DA_ESCLUDERE:
            continue
        try:
            dati = json.loads(json_path.read_text(encoding="utf-8-sig"))
        except Exception as e:
            log.error(f"Errore lettura {json_path.name}: {e}")
            continue

        barcode = dati.get("barcode", json_path.stem)
        esistente = (
            db.query(Prescrizione)
            .filter_by(job_id=job.id, barcode=barcode)
            .first()
        )

        if esistente:
            esistente.dati_estratti_json = json.dumps(dati, ensure_ascii=False)
        else:
            db.add(Prescrizione(
                barcode=barcode,
                dati_estratti_json=json.dumps(dati, ensure_ascii=False),
                excel_scritto=False,
                job=job,
            ))
        contatore += 1

    db.commit()
    log.info(f"Job {job.id}: {contatore} prescrizioni popolate da fase 2.")
    return contatore


def segna_excel_scritto(db, job: Job) -> None:
    """Marca tutte le prescrizioni di questo job come 'excel_scritto', dopo la fase 3."""
    db.query(Prescrizione).filter_by(job_id=job.id).update({"excel_scritto": True})
    db.commit()


def popola_difformita_da_output(db, job: Job, cartella_output: Path) -> int:
    """
    Rilegge gli stessi JSON (ora arricchiti con difformita_codici/
    difformita_descrizioni dalla fase 4) e crea le righe Difformita
    collegate alla Prescrizione corrispondente. Se richiamata più
    volte per lo stesso job, sostituisce le difformità precedenti
    invece di duplicarle.
    """
    contatore = 0
    for json_path in cartella_output.glob("*.json"):
        if json_path.name in FILE_JSON_DA_ESCLUDERE:
            continue
        try:
            dati = json.loads(json_path.read_text(encoding="utf-8-sig"))
        except Exception as e:
            log.error(f"Errore lettura {json_path.name}: {e}")
            continue

        barcode = dati.get("barcode", json_path.stem)
        codici = dati.get("difformita_codici", [])
        descrizioni = dati.get("difformita_descrizioni", [])

        presc = (
            db.query(Prescrizione)
            .filter_by(job_id=job.id, barcode=barcode)
            .first()
        )
        if presc is None:
            log.warning(f"Prescrizione con barcode={barcode} non trovata per il job {job.id} — salto le sue difformità.")
            continue

        # Evita duplicati se questa funzione gira più di una volta sullo stesso job
        db.query(Difformita).filter_by(prescrizione_id=presc.id).delete()

        for codice, descrizione in zip(codici, descrizioni):
            db.add(Difformita(tipo=codice, descrizione=descrizione, gravita="media", prescrizione=presc))
            contatore += 1

    db.commit()
    log.info(f"Job {job.id}: {contatore} difformità popolate da fase 4.")
    return contatore


def esiste_job_in_esecuzione(db) -> bool:
    """
    Verifica se esiste già un Job in una delle fasi attive (non ancora
    completato né terminato in errore). Usata per impedire che due
    job partano in parallelo — condividerebbero la stessa cartella
    dati/ e gli stessi container Docker, con rischio concreto di
    mescolare i file di due elaborazioni diverse.
    """
    stati_attivi = (
        StatoJob.in_coda, StatoJob.fase1_preprocessing, StatoJob.fase2_ocr,
        StatoJob.fase3_difformita, StatoJob.fase4_excel,
    )
    return db.query(Job).filter(Job.stato.in_(stati_attivi)).first() is not None


def avvia_pipeline_per_job(job_id: int, cartella_output: Path) -> None:
    """
    Punto 1: orchestra le 4 fasi in sequenza, aggiornando Job.stato ad
    ogni passaggio, e richiama il punto 2 (popolamento DB) nei momenti
    giusti (dopo fase 2 per le Prescrizioni, dopo fase 3 per le
    Difformità, dopo fase 4 per la conferma di scrittura Excel).

    NOTA sull'ordine: qui la difformità (fase 3) viene calcolata PRIMA
    della scrittura Excel (fase 4) — l'ordine inverso rispetto al testo
    del documento (Sezione 1.2), perché l'Excel finale deve includere
    le colonne di difformità già calcolate, non può scriverle prima
    che esistano. Vedi models.py, docstring di StatoJob.

    Pensata per girare in un thread separato (stesso principio già
    usato nel job_manager.py della pipeline locale), così la richiesta
    HTTP che crea il Job può rispondere subito senza aspettare che
    l'intera pipeline finisca.
    """
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job is None:
            log.error(f"Job {job_id} non trovato — impossibile avviare la pipeline.")
            return

        job.stato = StatoJob.fase1_preprocessing
        db.commit()
        _esegui_container("fase1-preprocessing", [])

        job.stato = StatoJob.fase2_ocr
        db.commit()
        _esegui_container("fase2-ocr", [])
        popola_prescrizioni_da_output(db, job, cartella_output)

        job.stato = StatoJob.fase3_difformita
        db.commit()
        _esegui_container("fase3-difformita", [])
        popola_difformita_da_output(db, job, cartella_output)

        job.stato = StatoJob.fase4_excel
        db.commit()
        _esegui_container("fase4-excel", [])
        segna_excel_scritto(db, job)

        job.stato = StatoJob.completato
        db.commit()
        log.info(f"Job {job_id}: pipeline completata con successo.")

    except Exception as e:
        log.error(f"Job {job_id}: pipeline interrotta — {e}")
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.stato = StatoJob.errore
            db.commit()
    finally:
        db.close()
