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
│   ├── archivio.py
│   └── impostazioni.py        # Impostazioni admin: censimento farmacie
├── templates/                 # tutte estendono base.html
│   ├── base.html
│   ├── login.html
│   ├── dashboard.html
│   ├── lotti.html
│   ├── nuovo_lotto.html       # wizard 4 step: Caricamento→Modalità→Conferma→Monitoraggio
│   ├── lotto_detail.html
│   ├── archivio.html
│   ├── impostazioni.html
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
gate di revisione manuale tra fasi automatiche. Altri modelli: `Utente`, `Farmacia`, `Prescrizione`, `Difformita` (19 codici di non conformità).

## Endpoint (contratto fisso col backend — NON rinominare)
`/login`, `/jobs/nuovo`, `/jobs`, `/archivio`, `/difformita`

## Pipeline: fake vs real
Flag `pipeline_backend` nella tabella `Configurazione`:
- `fake_pipeline.py` (default): simula le 4 fasi con stati/tempi realistici,
  nessuna dipendenza da Docker/GPU. Usare SEMPRE per sviluppo frontend/test
  su questa macchina (niente Docker/WSL disponibili qui).
- `real_pipeline.py`: Docker reale + Ollama/Qwen2.5-VL, integra gli script
  di Francesco (`ocr_cannabis.py`, `phase1_preprocess.py`, `phase4_excel.py`,
  `phase5_difformita.py`, `pipeline.py`). Sulla macchina ATS con GPU (Docker
  Engine su WSL2), o su un server senza Docker tramite `PIPELINE_RUNNER=local`
  (vedi sezione sotto) — le due modalità convivono, stessa logica.

## Produzione su Windows Server (senza Docker/WSL2, senza GPU)
Branch `deploy/windows-server` (da `dev`). Pensato per un server Windows
Server 2022 (VM, CPU-only, es. 40 vCPU) dove Docker/WSL2 non sono
disponibili — `real_pipeline.py` lancia la pipeline come **processo Python
nativo** invece che in container, riusando `docker/run_fase.py` così com'è.
La macchina ATS con GPU continua a usare Docker normalmente: è una scelta
per MACCHINA (`PIPELINE_RUNNER`), non per lotto.

Sul server, `app` (questa repo) e `pipeline` (repo SEPARATA
`ocr-cannabis-ats`) sono due checkout git **affiancati**, non uno dentro
l'altro (`E:\apps\ocr-cannabis\app\` e `E:\apps\ocr-cannabis\pipeline\`) —
`farmacie_dizionario.py`/`profilo_layout_etichetta.py` qui in questa repo
sono vendorizzati solo per comodità di sviluppo su questa macchina, vanno
allineati con la repo pipeline vera quando si tocca la logica di lookup
farmacie/profili (vedi i commit relativi sul branch `deploy/windows-server`).
Nessun utente di servizio dedicato: i servizi girano con l'utenza unica
del progetto (account amministratore esistente), password chiesta
all'installazione via `Read-Host -AsSecureString` (mai da Claude Code, che
non può rispondere a prompt interattivi).

Riepilogo delle differenze rispetto allo sviluppo locale (dettagli completi
in `DEPLOY_WINDOWS.md`):
- `PIPELINE_RUNNER=docker|local` — in locale lancia `PIPELINE_PYTHON
  <questa-repo>/docker/run_fase.py <fase>` (lo script resta nella repo
  webapp, lanciato da qui indipendentemente da dove vive `PIPELINE_DIR`),
  con `PIPELINE_DIR` (repo pipeline separata) in `sys.path` per gli import;
  pausa/ripresa/annullamento con `psutil` (suspend/resume/terminate
  sull'intero albero di processi) invece di `docker pause/unpause/kill`.
- `STORAGE_BACKEND=onedrive|local` (`app/storage.py`) — `local` è il default
  in produzione (cartella Windows fissa, `STORAGE_LOCAL_DIR`), niente
  OneDrive sul server. Con `APP_ENV=production` il fallback silenzioso su
  `sharepoint_finto/` è disattivato: un percorso non valido blocca
  l'operazione (o l'avvio dell'app) invece di scrivere altrove in silenzio
  — vale anche per la correzione manuale del barcode (`correggi_barcode_
  reale`), non solo per le fasi automatiche.
- Schema DB gestito con **Alembic** (`alembic/`, baseline nella prima
  revisione) quando `APP_ENV=production` — `Base.metadata.create_all()`
  resta attivo solo fuori produzione. Un DB esistente creato con
  `create_all()` va allineato con `alembic stamp head`, non `upgrade head`.
- `SESSION_SECRET_KEY` obbligatoria in produzione (l'app non parte senza).
  `seed_admin.py` accetta `ADMIN_INITIAL_PASSWORD` per l'installer, niente
  prompt interattivo e nessun default.
- `ROOT_PATH` (`.env` + flag `--root-path` a uvicorn) per far funzionare
  l'app sotto un prefisso IIS (es. `/cannabis`): nessuna route usa
  `url_for()`, quindi redirect/template/JS usano tutti `app/root_path.py`
  (stesso nome `RedirectResponse`, basta cambiare l'import) per anteporre
  il prefisso — vedi `app/root_path.py` prima di aggiungere un nuovo
  redirect o un nuovo link con percorso assoluto scritto a mano.
- Dipendenze gestite con `uv`: `pyproject.toml`/`uv.lock` alla radice per la
  webapp, `docker/pyproject.toml`/`docker/uv.lock` per la pipeline (ambiente
  Python **separato**, torch dalla build CPU-only, `PyMuPDF<2.0` per
  evitare che la 2.0 porti dentro `onnxruntime` solo per renderizzare una
  pagina PDF). `requirements.txt` e `docker/requirements.txt` restano le
  fonti per le immagini Docker (macchina GPU), invariate.
- EasyOCR forzato `gpu=False` quando CUDA non è disponibile (prima leggeva
  per sbaglio `NUM_GPU`, una manopola specifica di Ollama). `num_thread`
  configurabile per modello pesante/leggero via `.env`
  (`OLLAMA_NUM_THREAD_PESANTE`/`_LEGGERO`), `NUM_CTX` ora letto da env.
- `/health` copre database, Ollama (richiesta HTTP diretta) e storage
  configurato — solo il DB giù porta lo status a 503 (riavvio via WinSW);
  limite upload coerente con IIS (500 MB, errore leggibile nel wizard, non
  un 413 nudo); cookie `Secure` in produzione; log della webapp con
  rotazione su file (`LOG_DIR`) — il canale che inoltra l'output grezzo
  della pipeline (può contenere dati di prescrizione) resta **solo** nel
  DB e in console, mai nel file di log su disco.
- Script operativi in `deploy/windows/` (install/update/backup/restore/
  status/uninstall/benchmark) — nessun utente/servizio Ollama registrato
  da `install.ps1` (è infrastruttura condivisa pre-esistente, `infra-ollama`,
  modelli già scaricati): solo verificato, mai installato.

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
- Cambio di modello richiede SEMPRE `Remove-Item ats_cannabis.db` +
  `reset_db.py` + `seed_admin.py` (SQLite non ha migrazioni) — se vedi
  `OperationalError: no such column`, è sempre questo, non un bug di codice
- Virtual env NON è portabile tra macchine/utenti (contiene path assoluti
  hardcoded) — ricrealo sempre in locale con `python -m venv .venv`, mai
  copiarlo o condividerlo via git/zip

## Feature aggiunte da Khalil (merge del 26/08, branch frontend→dev)
Nello step "Monitoraggio"/dettaglio lotto sono già presenti (non
reimplementarle):
- Barcode linkati al PDF: `/lotti/{lotto_id}/prescrizioni/{prescrizione_id}/pdf`,
  presenti in tutte le tabelle di prescrizioni/difformità
- Tasto "Riprova" su elaborazioni in eccezione: `POST /lotti/{lotto_id}/riprova`,
  rilancia la fase fallita pulendo i dati parziali — rispetta il pattern di
  controllo cooperativo pausa/annullamento già esistente
- Link file Excel di output: `GET /lotti/{lotto_id}/output-excel`
- Visualizzazione errore migliorata: classe `errore-form--lotto` con
  titolo/corpo/timestamp
- Archivio: raggruppamento prescrizioni per lotto (`<details>` collassabili)
  e ricerca sia per barcode che per nome lotto

## File legacy da ignorare (untracked, non cancellare per ora ma non toccare)
- `ats-webapp/ats-webapp/` — copia duplicata del progetto da uno zip
  estratto per errore
- `app/static/index.html`, `app/static/js/api.js`, `app/static/js/ui.js`,
  `app/static/README.md` — prototipo standalone precedente, sistema a
  "batch code", non integrato con l'architettura attuale

## Step "Monitoraggio" del wizard — decisione architetturale (26/08)
Khalil ha implementato il wizard con solo 3 step (Dati lotto → Caricamento
→ Conferma), con redirect finale a `/lotti/{id}` (`lotto_detail.html`)
invece del 4° step "Monitoraggio" previsto dal documento di progetto
originale. NON è la soluzione voluta: va rifatto come 4° step vero e
proprio, integrato nello stepper di `nuovo_lotto.html`, non come pagina
separata raggiunta via redirect.

Decisione presa con Lorenzo: il Monitoraggio deve apparire come step 4
visivo nello stesso stepper (Dati lotto → Caricamento → Conferma →
**Monitoraggio**), dentro `nuovo_lotto.html`. La logica/i dati possono
riusare quanto già presente in `lotto_detail.html`/`lotti.py` (non
buttare via il lavoro di Khalil, quello resta valido come base per la
pagina di dettaglio archiviato di un lotto già chiuso), ma l'esperienza
del wizard per un lotto appena creato deve restare dentro un unico flusso
a 4 step, senza redirect verso un'altra pagina percepita come "diversa".

Stato reale delle funzionalità sottostanti (verificato via inventario in
`lotto_detail.html`/`lotti.py`, riusabili per costruire lo step 4):

1. **Vista d'insieme**: 🟡 metà fatto — 4 kpi-card esistono (totali,
   barcode da rivedere, score medio, difformità); manca la card
   pulite/con-difformità (il dato `n_prescrizioni_con_diffollo` esiste nel
   modello ma non è mostrato)
2. **Filtro bloccanti/minori**: ❌ da fare, nessuna base esistente
3. **Confronto OCR grezzo vs corretto**: ❌ da fare. NON esiste un
   "valore atteso" esterno con cui confrontare (l'Excel regionale non
   viene letto riga per riga, `barcode_in_excel` è solo un booleano di
   match). Il confronto reale è tra `DatiOcr.json_vllm_raw` (output
   grezzo) e `DatiOcr.json_corretto` (dopo intervento) — entrambi i
   campi esistono già nel modello, nessuna migrazione necessaria
4. **Correzione per riga**: 🟡 metà fatto — conferma/esclusione
   difformità già in `lotti.py:318`; manca l'editing del campo OCR
   (usa `json_corretto`, `corretto_da_id`, `corretto_at`, già in
   `models.py`)
5. **Chiusura lotto**: ✅ già fatto — `POST /lotti/{id}/completa` quando
   tutte le difformità sono gestite
6. **Export ZIP per farmacia**: 🟡 parziale — esiste
   `GET /lotti/{id}/output-excel` ma è un export singolo, non uno zip
   multi-cartella per farmacia
7. **PDF originale**: ✅ già fatto — route `lotti.py:260`, linkato in
   tutte e 5 le tabelle di `lotto_detail.html`

Lavoro reale rimanente per il subagent:
0. **Integrare come 4° step visivo del wizard** (nuovo, priorità
   architetturale prima di tutto il resto — vedi decisione sopra)
1 (completare KPI), 2 (nuovo), 3 (nuovo, ridefinito come sopra),
4 (completare), 6 (estendere a multi-farmacia).

## Censimento farmacie
CRUD in Impostazioni (solo admin). Campi: codice, nome, codice_regionale
(FARMACIA_ID Excel Regione), indirizzo, comune, provincia, telefono, email,
note, attiva — senza nome_breve.

L'elenco attivo va all'IA in due modi (entrambi):
- **A)** `dati/farmacie.json` iniettato nel prompt OCR (`ocr_cannabis.py`)
- **B)** matching fuzzy deterministico (`farmacie_dizionario.py`) in OCR e in
  `phase5_difformita.py`

La lista hardcoded nei prompt e' il fallback se il JSON non e' presente.
La repo pipeline di Francesco va allineata quando si ricostruisce l'immagine
Docker (`farmacie_dizionario.py` e' nel Dockerfile).

## Dashboard analytics stile "Power BI" (nuova feature, separata dal wizard)
Visualizzazione Python con filtri per esplorare storicamente tutte le
elaborazioni (non il singolo lotto — quella è il Monitoraggio sopra).
Fonti dati: DB (SQLite) + file Excel già salvati su SharePoint per ogni
lotto. È una superficie distinta dal wizard, va trattata come gap a sé,
non infilata dentro lo step Monitoraggio.

## Gap aperti (priorità pre-31 agosto)
1. Step "Monitoraggio" del wizard (vedi sopra) — gap più grande e più
   urgente. Khalil ha implementato solo 3 step con redirect a
   lotto_detail.html invece del 4° step integrato: va rifatto come step
   visivo nello stepper, riusando la logica sottostante già scritta
2. Pagina Impostazioni: CRUD farmacie fatto; tab Sistema in sola lettura
   (modifica config da UI ancora da fare)
3. Dashboard analytics stile Power BI (vedi sopra)
4. Allineare l'immagine Docker/pipeline ATS con `farmacie_dizionario.py`
   (gia' nel Dockerfile di questa repo)
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
