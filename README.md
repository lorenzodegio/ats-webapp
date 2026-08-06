# ATS Cannabis OCR — Pipeline Manager (Web App)

Applicazione web interna per la gestione delle prescrizioni di cannabis
terapeutica, sviluppata in collaborazione con ATS Insubria.

## Principio architetturale

**SharePoint e' il filesystem, il DB e' il cervello.** I file (PDF, PNG,
Excel, JSON) vivono su SharePoint; il database salva solo percorsi
relativi, stati e metadati — mai dati binari.

Un **lotto mensile** e' l'unita' di lavoro (es. "Agosto 2026"): contiene
piu' **elaborazioni** (run separate della pipeline: preprocessing, OCR,
difformita), attraversa un ciclo di vita a 10 stati con gate manuali di
revisione da parte dell'operatore.

## Avvio rapido (sviluppo locale)

```bash
python -m venv venv
source venv/bin/activate      # su Windows: venv\Scripts\activate
pip install -r requirements.txt

python seed_admin.py          # crea utenti + configurazione + lotti demo
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Poi apri http://localhost:8000. Utenti demo:

| Utente     | Password       | Ruolo          |
|------------|----------------|----------------|
| admin      | admin123       | amministratore |
| operatore  | operatore123   | operatore      |
| revisore   | revisore123    | revisore       |

## Azzerare il database

Ogni volta che cambia `app/models.py` (colonne o tabelle nuove), SQLite
non fa migrazioni automatiche: lo schema va ricreato da zero. Comando
unico:

```bash
python reset_db.py
```

Cancella `ats_cannabis.db` e richiama `seed_admin.py`. Quando si passera'
a PostgreSQL in produzione, questo script andra' sostituito da vere
migrazioni (es. Alembic).

## Backend finto (mock)

Le fasi automatiche della pipeline (preprocessing, OCR, difformita)
**non** chiamano davvero Docker in questa versione: `app/fake_pipeline.py`
simula ogni fase con tempi realistici, scrive log riga per riga
(`LogElaborazione`) e aggiorna lo stato del lotto — stesso contratto
dati che usera' l'integrazione reale.

I file vengono comunque scritti per davvero, ma in una cartella locale
`./sharepoint_finto/` invece che sul vero SharePoint montato, cosi' il
principio "i file vivono sul filesystem" resta vero anche senza
OneDrive sincronizzato sulla macchina di sviluppo.

## Backend finto (mock) vs backend reale (Docker)

Toggle in `Configurazione.pipeline_backend` (seedato a `finto`):

- **`finto`** (default): nessun Docker richiesto, tutto simulato — usalo per sviluppo frontend.
- **`reale`**: esegue davvero i 4 container Docker della pipeline OCR (integrazione portata dal lavoro di Francesco, riscritta per `lotti_mensili`/UUID). Richiede:
  1. Docker Desktop attivo, rete `ats-pipeline_default` creata (`docker network create ats-pipeline_default`)
  2. Container Ollama in esecuzione: `docker compose -f docker-compose.ollama.yml up -d`, poi `docker exec ats-ollama ollama pull qwen2.5vl:7b`
  3. Cambiare il valore in DB: `UPDATE configurazione SET valore='reale' WHERE chiave='pipeline_backend';`

Per cambiare il valore senza toccare SQL a mano:
```python
python -c "from app.database import SessionLocal; from app.models import Configurazione; db = SessionLocal(); c = db.query(Configurazione).filter_by(chiave='pipeline_backend').first(); c.valore='reale'; db.commit()"
```

### ⚠️ Non testato end-to-end

L'integrazione Docker reale (`app/real_pipeline.py`) è stata scritta leggendo
il codice sorgente della pipeline (non eseguendola: qui non c'è Docker/Ollama
disponibile). **Prima di fidarsene in produzione va verificata su una macchina
con Docker configurato.** Punti specifici da controllare:

- Il mapping campo-per-campo tra l'output reale di `ocr_cannabis.py` e le
  colonne di `DatiOcr` (vedi `MAPPA_CAMPI_OCR` in `real_pipeline.py`) — in
  particolare `data_emissione` → `data_invio`, assunto per equivalenza
  semantica ma non confermato con Francesco.
- Il parsing delle date italiane (`_parse_data_italiana`) prova i formati
  `%d/%m/%Y`, `%d-%m-%Y`, `%Y-%m-%d` — se `date_corrector.py` normalizza in
  un formato diverso, va aggiornato.
- **Fix applicato a `docker/run_fase.py`**: la fase OCR ora chiama anche
  `pipeline.esegui_merge_regione()`, che nella versione originale del
  dispatcher non veniva mai invocata nonostante `pipeline.py` la preveda
  come passo necessario prima della difformità. Da confermare con Francesco
  che sia il comportamento corretto.
- `score_ocr` resta sempre vuoto: la pipeline reale non produce una metrica
  di confidenza numerica (il campo esisteva solo nel backend finto).
- Un solo lotto alla volta può avere un container Docker attivo (stessa
  cartella `./dati` condivisa) — i lotti in stato `revisione_*` non ne
  risentono, possono coesistere tranquillamente.



```
bozza -> caricamento -> preprocessing -> revisione_barcode
      -> elaborazione_ocr -> revisione_qualita -> analisi_difformita
      -> revisione_difformita -> completato -> archiviato
                (eccezione possibile in qualsiasi fase automatica)
```

Le fasi `preprocessing`, `elaborazione_ocr` e `analisi_difformita` sono
automatiche (background task); le fasi `revisione_*` richiedono
un'azione esplicita dell'operatore nell'interfaccia.

## Struttura

```
ats-webapp/
|-- requirements.txt
|-- seed_admin.py
|-- reset_db.py
|-- docker-compose.yml           # 4 servizi della pipeline reale (fase1-4)
|-- docker-compose.ollama.yml    # container Ollama separato
|-- docker/
|   |-- Dockerfile
|   |-- requirements.txt         # dipendenze pesanti (torch, easyocr...), solo nel container
|   `-- run_fase.py              # dispatcher eseguito dentro il container
|-- ocr_cannabis.py, phase1_preprocess.py, phase4_excel.py,     # script della pipeline
|   phase5_difformita.py, merge_regione.py, front_detector.py,  # OCR reale, portati
|   etichetta_presence.py, date_corrector.py, pipeline.py       # dal lavoro di Francesco
`-- app/
    |-- main.py
    |-- database.py
    |-- models.py               # schema completo: lotti, elaborazioni, prescrizioni,
    |                            # dati_ocr, difformita, configurazione, ecc.
    |-- db_types.py              # tipo GUID portabile SQLite/PostgreSQL
    |-- auth.py
    |-- progresso.py             # percentuali/etichette del ciclo di vita
    |-- config_helper.py         # lettura Configurazione (incluso toggle finto/reale)
    |-- generatore_finto.py      # dati finti condivisi (seed + backend finto)
    |-- fake_pipeline.py         # backend finto
    |-- real_pipeline.py         # backend reale: orchestra i container Docker veri
    |-- routers/
    |   |-- auth_router.py
    |   |-- dashboard.py
    |   |-- lotti.py             # ciclo di vita completo del lotto mensile
    |   `-- archivio.py
    |-- templates/
    `-- static/
        |-- css/style.css        # design system
        `-- js/
```

## Cosa manca ancora (fuori perimetro in questa versione)

Lo schema dati completo include tabelle gia' pronte ma senza ancora una
pagina dedicata nell'interfaccia:

- **Configurazione**: seedata da `seed_admin.py`, non ancora modificabile da UI (percorso SharePoint, soglia score OCR, ecc.)
- **Audit log**: tabella pronta, nessuna pagina di consultazione
- **Farmacie**: tabella pronta, nessuno storico per farmacia mostrato
- **Annotazioni**: tabella pronta, nessuna UI per note su prescrizioni/difformita
