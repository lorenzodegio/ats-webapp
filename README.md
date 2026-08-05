# ATS Cannabis OCR — Pipeline Manager (Web App)

Applicazione web interna per la gestione delle prescrizioni di cannabis
terapeutica, sviluppata in collaborazione con ATS Insubria.

> Vedi il documento di progetto per il contesto completo (architettura,
> modello dati, API, design system, organizzazione del team).

## Avvio rapido (sviluppo locale)

```bash
python -m venv venv
source venv/bin/activate      # su Windows: venv\Scripts\activate
pip install -r requirements.txt

python seed_admin.py          # crea utente admin + dati demo
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Poi apri http://localhost:8000 — utenti demo creati da `seed_admin.py`:

| Utente     | Password       | Ruolo      |
|------------|----------------|------------|
| admin      | (scelta a run) | admin      |
| operatore  | operatore123   | operatore  |

## Backend finto (mock)

Le 4 fasi della pipeline OCR **non** vengono richiamate realmente in questa
versione: `app/fake_pipeline.py` simula l'avanzamento di stato di un Job
(`in_coda → fase1 → fase2 → fase3 → fase4 → completato`) e genera
prescrizioni/difformita finte, rispettando lo stesso contratto di dati che
usera' l'integrazione reale (Alessandro, Francesco — Sezione 7.1 del
documento di progetto).

Quando il backend reale sara' pronto, sara' sufficiente sostituire la
chiamata a `elabora_job_fake(...)` in `app/routers/jobs.py` con l'avvio
reale dei container Docker: nessuna modifica necessaria a route, template
o modelli.

## Struttura

```
ats-webapp/
|-- requirements.txt
|-- seed_admin.py
`-- app/
    |-- main.py
    |-- database.py
    |-- models.py
    |-- auth.py
    |-- fake_pipeline.py       # backend finto, da sostituire con l'integrazione reale
    |-- routers/
    |   |-- auth_router.py
    |   |-- dashboard.py
    |   |-- jobs.py
    |   `-- archivio.py
    |-- templates/
    `-- static/
        |-- css/style.css      # design system
        `-- js/
```
