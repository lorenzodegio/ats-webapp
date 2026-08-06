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

## Ciclo di vita di un lotto

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
`-- app/
    |-- main.py
    |-- database.py
    |-- models.py               # schema completo: lotti, elaborazioni, prescrizioni,
    |                            # dati_ocr, difformita, configurazione, ecc.
    |-- db_types.py              # tipo GUID portabile SQLite/PostgreSQL
    |-- auth.py
    |-- progresso.py             # percentuali/etichette del ciclo di vita
    |-- generatore_finto.py      # dati finti condivisi (seed + backend finto)
    |-- fake_pipeline.py         # backend finto, da sostituire con l'integrazione reale
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
