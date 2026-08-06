"""
BACKEND FINTO (mock) — sostituisce temporaneamente le chiamate reali ai
container Docker delle 4 fasi (Sezione 2 del documento di progetto).

Obiettivo: permettere lo sviluppo del frontend in autonomia, rispettando
esattamente lo stesso contratto (stati di Job, Prescrizione, Difformita)
che userà l'integrazione reale fatta da Alessandro e Francesco.

Quando il backend reale sarà collegato, basterà sostituire la funzione
`elabora_job_fake` con la chiamata reale ai container, senza toccare le
route ne' i template: il contratto (stati, campi) resta identico.
"""
import json
import random
import time
from datetime import datetime

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Job, Prescrizione, Difformita, StatoJob, Gravita

# Tempi (secondi) di permanenza in ciascuna fase, per simulare una
# elaborazione realistica senza dover aspettare i tempi reali della pipeline.
DURATA_FASE = {
    StatoJob.fase1_preprocessing: 2,
    StatoJob.fase2_ocr: 4,
    StatoJob.fase3_excel: 1,
    StatoJob.fase4_difformita: 2,
}

TIPI_DIFFORMITA = [
    ("TDL", "Dosaggio giornaliero fuori range terapeutico previsto"),
    ("TAL", "Durata terapia non conforme alla prescrizione autorizzata"),
    ("TOL", "Quantita totale prescritta non coerente con la posologia"),
    ("FIRMA_MANCANTE", "Firma del medico prescrittore non rilevata"),
    ("CODICE_ATC_INCOERENTE", "Codice ATC non corrispondente al principio attivo"),
    ("BARCODE_ILLEGGIBILE", "Barcode rilevato con confidenza bassa, verifica manuale consigliata"),
]

NOMI = ["Mario Rossi", "Anna Bianchi", "Luca Verdi", "Giulia Ferrari", "Marco Colombo",
        "Sara Ricci", "Davide Marino", "Elena Greco", "Paolo Conti", "Chiara Villa"]

PRINCIPI_ATTIVI = ["FM2 Cannabis", "Bedrocan", "Bediol", "Pedanios 22/1", "Bedica"]


def _genera_barcode() -> str:
    return f"{random.randint(8000, 8999)}-{random.randint(100, 999)}"


def _genera_dati_estratti() -> dict:
    """Simula i 24 campi strutturati che la fase 2 (Qwen2.5-VL) estrarrebbe davvero."""
    return {
        "paziente": random.choice(NOMI),
        "medico_prescrittore": f"Dr. {random.choice(NOMI)}",
        "principio_attivo": random.choice(PRINCIPI_ATTIVI),
        "dosaggio_giornaliero_mg": random.choice([300, 500, 600, 900]),
        "durata_terapia_giorni": random.choice([30, 60, 90]),
        "quantita_totale_g": round(random.uniform(10, 60), 1),
        "data_prescrizione": datetime.utcnow().strftime("%Y-%m-%d"),
        "codice_ats": f"ATS-{random.randint(1000, 9999)}",
    }


MESSAGGI_ERRORE_FINTI = [
    "Impossibile leggere il barcode su 2 pagine del PDF caricato",
    "Timeout del modello OCR durante l'estrazione dei campi",
    "File Excel regionale di destinazione non raggiungibile (percorso OneDrive)",
]

# Probabilita' (0-1) che, per scopo dimostrativo, un job finto si interrompa
# in errore durante una fase. Nel backend reale l'errore sara' quello vero
# sollevato dal container Docker corrispondente.
PROBABILITA_ERRORE_FINTO = 0.08


def elabora_job_fake(job_id: int, numero_prescrizioni: int) -> None:
    """
    Simula in modo sincrono (girera' su un BackgroundTask di FastAPI) le
    4 fasi della pipeline per un job, avanzando lo stato nel DB passo passo
    cosi' che la pagina "Elaborazioni" possa mostrare il progresso in polling.

    Ad ogni fase completata con successo aggiorna anche
    `ultima_fase_completata`, cosi' se il job si ferma in errore resta
    tracciato "dove" si era arrivato.
    """
    db: Session = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job is None:
            return

        sequenza = [
            StatoJob.fase1_preprocessing,
            StatoJob.fase2_ocr,
            StatoJob.fase3_excel,
            StatoJob.fase4_difformita,
        ]

        for fase in sequenza:
            job.stato = fase
            job.aggiornato_il = datetime.utcnow()
            db.commit()
            time.sleep(DURATA_FASE[fase])

            if fase == StatoJob.fase2_ocr:
                # Fase 2: crea le prescrizioni con i dati estratti finti
                for _ in range(numero_prescrizioni):
                    presc = Prescrizione(
                        job_id=job.id,
                        barcode=_genera_barcode(),
                        dati_estratti_json=json.dumps(_genera_dati_estratti(), ensure_ascii=False),
                        excel_scritto=False,
                    )
                    db.add(presc)
                db.commit()

            if fase == StatoJob.fase3_excel:
                for presc in job.prescrizioni:
                    presc.excel_scritto = True
                db.commit()

            if fase == StatoJob.fase4_difformita:
                for presc in job.prescrizioni:
                    # circa 1 prescrizione su 4 riceve una difformita', come nei dati reali (~95% ok)
                    if random.random() < 0.25:
                        tipo, descrizione = random.choice(TIPI_DIFFORMITA)
                        db.add(Difformita(
                            prescrizione_id=presc.id,
                            tipo=tipo,
                            descrizione=descrizione,
                            gravita=random.choice(list(Gravita)),
                        ))
                db.commit()

            # Simulazione (solo demo): possibilita' di errore dopo una fase
            # completata con successo, per poter testare lo stato "errore".
            if random.random() < PROBABILITA_ERRORE_FINTO:
                job.ultima_fase_completata = fase
                job.stato = StatoJob.errore
                job.messaggio_errore = random.choice(MESSAGGI_ERRORE_FINTI)
                job.aggiornato_il = datetime.utcnow()
                db.commit()
                return

            job.ultima_fase_completata = fase
            db.commit()

        job.stato = StatoJob.completato
        job.aggiornato_il = datetime.utcnow()
        db.commit()

    except Exception as exc:  # pragma: no cover - solo per il mock
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.stato = StatoJob.errore
            job.messaggio_errore = str(exc)
            db.commit()
    finally:
        db.close()
