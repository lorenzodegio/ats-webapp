"""
Finto backend per sviluppare il frontend senza dipendere dal team backend.
Risponde con dati statici agli stessi endpoint che il backend reale esporra
(vedi documento di progetto, Sezione 5). Nessun collegamento al motore OCR.

Avvio: pip install -r requirements.txt && uvicorn app:app --reload --port 8000
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="ATS Cannabis OCR - Mock Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UTENTE_FINTO = {"id": 1, "username": "admin", "nome_completo": "Operatore Demo", "ruolo": "admin"}

JOB_FINTI = [
    {"id": 1, "nome_file_origine": "prescrizione_001.pdf", "stato": "completato", "modalita": "full", "creato_il": "2026-08-01T10:00:00"},
    {"id": 2, "nome_file_origine": "prescrizione_002.pdf", "stato": "fase2_ocr", "modalita": "full", "creato_il": "2026-08-02T09:30:00"},
    {"id": 3, "nome_file_origine": "prescrizione_003.pdf", "stato": "in_coda", "modalita": "solo-preprocess", "creato_il": "2026-08-03T14:15:00"},
]

PRESCRIZIONI_FINTE = [
    {"id": 1, "barcode": "8021000000045", "job_id": 1, "excel_scritto": "regione_2026_08.xlsx"},
    {"id": 2, "barcode": "8021000000046", "job_id": 1, "excel_scritto": "regione_2026_08.xlsx"},
]

DIFFORMITA_FINTE = [
    {"id": 1, "tipo": "TDL", "descrizione": "Durata prescrizione non conforme", "gravita": "media", "prescrizione_id": 1},
    {"id": 2, "tipo": "TAL", "descrizione": "Tipologia farmaco ambigua", "gravita": "alta", "prescrizione_id": 2},
]


@app.get("/")
def dashboard():
    return {
        "tot_prescrizioni": len(PRESCRIZIONI_FINTE),
        "tot_difformita": len(DIFFORMITA_FINTE),
        "in_coda": sum(1 for j in JOB_FINTI if j["stato"] != "completato"),
        "ultimi_job": JOB_FINTI,
    }


@app.post("/login")
def login(username: str, password: str):
    return {"ok": True, "utente": UTENTE_FINTO}


@app.get("/logout")
def logout():
    return {"ok": True}


@app.get("/jobs")
def lista_job():
    return JOB_FINTI


@app.post("/jobs/nuovo")
def crea_job(modalita: str = "full"):
    nuovo = {
        "id": len(JOB_FINTI) + 1,
        "nome_file_origine": "nuovo_caricamento.pdf",
        "stato": "in_coda",
        "modalita": modalita,
        "creato_il": "2026-08-04T12:00:00",
    }
    JOB_FINTI.append(nuovo)
    return nuovo


@app.get("/archivio")
def archivio(q: str | None = None):
    if q:
        return [p for p in PRESCRIZIONI_FINTE if q in p["barcode"]]
    return PRESCRIZIONI_FINTE


@app.get("/difformita")
def difformita():
    return DIFFORMITA_FINTE
