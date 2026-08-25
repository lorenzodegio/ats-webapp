# ATS Cannabis Webapp — Contesto di Progetto

## Cosa fa questo progetto
Applicazione web per ATS Insubria che automatizza l'elaborazione delle
prescrizioni di cannabis terapeutica tramite una pipeline OCR basata su
Qwen2.5-VL. Progetto di tesi + collaborazione reale con l'ente. Scadenza:
31 agosto.

Repo pipeline OCR (separata): https://github.com/lorenzodegio/ocr-cannabis-ats
Questa repo (`ats-webapp`) contiene backend FastAPI + frontend Jinja2/JS
che espone la pipeline come servizio agli operatori ATS.

## Team e branch strategy
- Frontend: Lorenzo + Khalil
- Backend: Alessandro + Francesco
- Branch: `master ← dev ← test ← (backend, frontend) ← branch personali`
- Worktree per gli agent Claude Code: vedi sezione "Worktree" sotto

## Stack
- FastAPI 0.115, SQLAlchemy 2.0, SQLite, Jinja2, bcrypt, python-multipart
- Frontend: HTML + Jinja2 templates, JS vanilla (niente framework), CSS puro
- Niente build step frontend: le pagine sono server-rendered

## Struttura del codice
```
app/
├── main.py                  # entrypoint FastAPI
├── auth.py                  # login a sessione, bcrypt
├── database.py               # setup SQLAlchemy
├── models.py                 # modelli ORM
├── db_types.py
├── config_helper.py           # legge Configurazione (incl. flag pipeline_backend)
├── fake_pipeline.py           # mock pipeline, NON TOCCARE senza avvisare (vedi sotto)
├── real_pipeline.py           # integra Docker + script Francesco
├── generatore_finto.py
├── progresso.py
├── routers/
│   ├── auth_router.py
│   ├── dashboard.py
│   ├── lotti.py               # rotte principali: nuovo lotto, elaborazioni, dettaglio
│   └── archivio.py
├── templates/                 # tutte estendono base.html
│   ├── base.html
│   ├── login.html
│   ├── dashboard.html
│   ├── lotti.html
│   ├── nuovo_lotto.html       # wizard 4 step: Caricamento→Modalità→Conferma→Monitoraggio
│   ├── lotto_detail.html
│   ├── archivio.html
│   └── _badge_stato.html
└── static/
    ├── css/style.css          # design system, UN SOLO file, vedi sotto
    └── js/
        ├── app.js
        ├── wizard-nuovo-lotto.js
        ├── dashboard-progresso.js
        └── polling-lotto-detail.js

docker/
├── Dockerfile
├── requirements.txt            # dipendenze pipeline (PyMuPDF, pyzbar, openpyxl...)
└── run_fase.py                  # bug noto risolto: esegui_merge_regione() non
                                  # veniva chiamata prima delle check 14/17/18
```

## Come avviare in locale (fake pipeline, no Docker richiesto)
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python seed_admin.py
python -m uvicorn app.main:app --reload --port 8000
```
Utenti demo: admin/admin123 (admin), operatore/operatore123 (operatore).
DB pulito: cancella `ats_cannabis.db` e rilancia `seed_admin.py`.

## Modello dati
Core: `LottoMensile` (batch mensile, UUID PK), lifecycle a 10 stati con
gate di revisione manuale tra fasi automatiche. Altri modelli: `Utente`,
`Job`, `Prescrizione`, `Difformita` (19 codici di non conformità).

## Endpoint (contratto fisso col backend — NON rinominare)
`/login`, `/jobs/nuovo`, `/jobs`, `/archivio`, `/difformita`

## Pipeline: fake vs real
Flag `pipeline_backend` nella tabella `Configurazione`:
- `fake_pipeline.py` (default): simula le 4 fasi con stati/tempi realistici,
  nessuna dipendenza da Docker/GPU. Usare SEMPRE per sviluppo frontend/test
  su questa macchina (niente Docker/WSL disponibili qui).
- `real_pipeline.py`: Docker reale + Ollama/Qwen2.5-VL, integra gli script
  di Francesco (`ocr_cannabis.py`, `phase1_preprocess.py`, `phase4_excel.py`,
  `phase5_difformita.py`, `pipeline.py`). Solo sulla macchina ATS (GPU NVIDIA
  5060 Ti, Docker Engine su WSL2).

## Design system (rispettare sempre, non inventare nuove classi)
- Palette: forest #2B3B35, sage #6B9080, sageLight #A4C3B2, terracotta #C9895B
- Font: Inter + IBM Plex Mono
- Card: radius 16px, controlli: radius 8px
- Classi già pronte in style.css: `.card`, `.btn`, `.badge`, `.tabella`
- Un solo file CSS (`app/static/css/style.css`) — non crearne altri

## Cosa NON toccare senza avvisare
- `app/fake_pipeline.py`: se cambi la forma dei dati restituiti (stati,
  campi Job/Prescrizione/Difformita) disallinei il contratto col backend
  reale di Alessandro/Francesco
- Nomi degli endpoint (`/jobs/nuovo`, `/jobs`, `/archivio`, `/difformita`)

## Bug noti già risolti (non reintrodurli)
- Loop di redirect su login dopo migrazioni schema → risolto con validazione
  UUID in `get_utente_opzionale`
- `str | None` non compatibile Python 3.8 → usare `Optional[str]`
- `UnicodeDecodeError` da output subprocess Docker → `encoding="utf-8", errors="replace"`
- Cambio di modello richiede sempre `reset_db.py` (SQLite non ha migrazioni)

## Step "Monitoraggio" del wizard — requisiti funzionali
Confermato via grep su `nuovo_lotto.html`: oggi è uno scheletro, va
costruito da zero. È il punto in cui l'operatore ATS decide se il lotto
può essere chiuso o serve intervento. Deve includere:

1. **Vista d'insieme del lotto**: totale prescrizioni, quante pulite,
   quante con difformità (colpo d'occhio prima del dettaglio)
2. **Lista prescrizioni con difformità, filtrabile**: distinguere
   difformità bloccanti da minori (i 19 codici non hanno tutti lo stesso
   peso)
3. **Confronto per ogni prescrizione**: dato letto dall'OCR vs atteso —
   serve per distinguere falso positivo (errore OCR) da vero positivo
   (prescrizione realmente non conforme)
4. **Azione di correzione per riga**: operatore può correggere un campo
   letto male, oppure confermare la difformità e decidere l'esito
5. **Chiusura lotto**: azione finale che valida il lotto = gate di
   revisione manuale nel lifecycle a 10 stati del `LottoMensile`
6. **Export ZIP per farmacia**: alla validazione, generare uno zip
   scaricabile con N cartelle (una per farmacia) contenenti le
   prescrizioni con difformità di competenza di quella farmacia, pronte
   da inviare
7. **Accesso al PDF originale** della prescrizione per verifica visiva
   quando il dato estratto è dubbio

## Censimento farmacie (nuova feature, per il punto 6 sopra)
Serve un CRUD farmacie (una decina in totale) in una nuova pagina
Impostazioni (solo admin) — vedi anche gap "Pagina Impostazioni" sotto.
Attenzione: questo si divide in due parti separate, NON sovrapporle:
- **CRUD farmacie**: puro frontend/backend webapp, nessun impatto sulla
  pipeline OCR — la fa chi lavora sul frontend
- **Fuzzy matching nome farmacia** (l'OCR confronta il nome letto con
  l'elenco censito e scrive il nome standardizzato più simile): questo
  tocca `ocr_cannabis.py` / `phase5_difformita.py` nella repo pipeline
  separata — è territorio di Francesco, NON bloccare lo step
  Monitoraggio in attesa di questo pezzo

## Dashboard analytics stile "Power BI" (nuova feature, separata dal wizard)
Visualizzazione Python con filtri per esplorare storicamente tutte le
elaborazioni (non il singolo lotto — quella è il Monitoraggio sopra).
Fonti dati: DB (SQLite) + file Excel già salvati su SharePoint per ogni
lotto. È una superficie distinta dal wizard, va trattata come gap a sé,
non infilata dentro lo step Monitoraggio.

## Gap aperti (priorità pre-31 agosto)
1. Step "Monitoraggio" del wizard (vedi sopra) — gap più grande e più
   urgente, oggi è uno scheletro vuoto
2. Pagina "Impostazioni" (solo admin): non esiste ancora né route né
   template. Contiene sia il censimento farmacie sia altre config.
   Seguire lo schema di `app/routers/archivio.py` come esempio di router
   protetto più semplice.
3. Dashboard analytics stile Power BI (vedi sopra)
4. Fuzzy matching farmacie nell'OCR — dipende da Francesco, repo pipeline
5. Validazioni upload più robuste nel wizard: oggi solo estensione .pdf
   lato client, manca feedback su file troppo grandi
6. Stati vuoti / micro-interazioni: skeleton loading, animazioni badge
   su cambio stato via polling
7. Responsive/mobile: UI pensata solo per desktop/LAN ufficio finora
8. Retry mechanism per stati di eccezione nel lifecycle del LottoMensile
9. Validazione end-to-end con PDF reali (da fare su macchina ATS, non qui)
10. Hardening sicurezza: secret key hardcoded, password di default

## Worktree per gli agent
- `agent/frontend-wizard` (da branch `frontend`)
- `agent/e2e-tests` (da branch `test`)
- `agent/docs` (da branch `dev`)
- `agent/security-hardening` (da branch `dev`)

## Workflow di sviluppo
modifica su dev → verifica avvio pulito del server → merge su test →
test interfaccia completa su macchina ATS → log problemi → ripeti
