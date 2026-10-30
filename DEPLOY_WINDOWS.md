# Produzione su Windows Server (senza Docker/WSL2, senza GPU)

Guida operativa per installare e gestire l'app sul server Windows Server
2022 `ATSVaUninsubria.aslva.it` (VM VMware, 40 vCPU Xeon Gold 6140 con
AVX-512, 192 GB RAM, nessuna GPU, PowerShell 5.1), dove Docker/WSL2 non
sono disponibili. La macchina ATS con GPU continua a funzionare normalmente
con Docker: le due modalità convivono (`PIPELINE_RUNNER`, vedi sotto),
nessuna delle due esclude l'altra.

**Stato**: scritto e verificato dove possibile (sintassi degli script,
test funzionali mirati dei singoli meccanismi — vedi i commit sul branch
`deploy/windows-server`), ma **mai eseguito su un vero Windows Server**.
Il primo giro reale va fatto con attenzione, passo per passo, non "a
occhi chiusi" — segnala qualunque scostamento da quanto descritto qui.
L'installazione la esegue una persona in PowerShell come amministratore;
può essere assistita da Claude Code, che però non può rispondere a prompt
interattivi (`Read-Host`) — le password vanno sempre inserite di persona.

## Indice
1. [Prerequisiti (già predisposti sul server)](#1-prerequisiti-già-predisposti-sul-server)
2. [Layout delle cartelle](#2-layout-delle-cartelle)
3. [Installazione](#3-installazione)
4. [Configurazione IIS e certificato](#4-configurazione-iis-e-certificato-già-fatta)
5. [Verifica](#5-verifica)
6. [Benchmark (calibrazione CPU)](#6-benchmark-calibrazione-cpu)
7. [Aggiornamento](#7-aggiornamento)
8. [Backup e ripristino](#8-backup-e-ripristino)
9. [Rollback](#9-rollback)
10. [Disinstallazione](#10-disinstallazione)
11. [Dopo un riavvio del server](#11-dopo-un-riavvio-del-server)
12. [Risoluzione problemi rapida](#12-risoluzione-problemi-rapida)

---

## 1. Prerequisiti (già predisposti sul server)

Il server è già stato preparato da uno script di setup — **nessuno di
questi va reinstallato o riconfigurato da `install.ps1`**, che si limita a
verificarne la presenza (vedi `-1. Prerequisiti-` in `install.ps1`):

- Git, Python 3.11 gestito da **uv** (`UV_PYTHON_INSTALL_DIR=E:\infra\python`,
  `UV_CACHE_DIR=E:\infra\cache\uv`), `uv` in `E:\infra\tools\uv` (nel PATH).
  Nessun Python di sistema installato a parte.
- Visual C++ Redistributable 2013 e 2015-2022.
- **Tesseract-OCR** in `C:\Program Files\Tesseract-OCR` (nel PATH), con
  lingua `ita` — usato come fallback quando un barcode non viene letto
  (`front_detector.py`, chiamato da `phase1_preprocess.py`).
- **PostgreSQL 16**, servizio `postgresql-x64-16`, in ascolto solo su
  `localhost:5432`, strumenti a riga di comando (`psql`, `pg_dump`, ecc.)
  nel PATH.
- **Ollama condiviso**, servizio `infra-ollama`, su `127.0.0.1:11434`, con
  `OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_MAX_LOADED_MODELS=2`,
  `OLLAMA_NUM_PARALLEL=1`. Modelli **già scaricati**:
  `qwen3-vl:30b-a3b-instruct` e `qwen3-vl:8b-instruct`.
- `WinSW-x64.exe` in `E:\infra\tools\winsw\`.
- IIS già configurato come reverse proxy HTTPS — vedi
  [sezione 4](#4-configurazione-iis-e-certificato-già-fatta).
- **Nessun utente di servizio dedicato**: i servizi girano con l'utenza
  unica del progetto (l'account amministratore della macchina), la cui
  password viene chiesta a più riprese durante `install.ps1` (sempre con
  `Read-Host -AsSecureString`, mai salvata in chiaro da questi script).

Verifica rapida (quello che `install.ps1` controlla comunque da solo al
primo passo):

```powershell
git --version; uv --version; tesseract --version
tesseract --list-langs                        # deve contenere "ita"
Get-Service postgresql-x64-16, infra-ollama | Select Name, Status
ollama list                                    # deve contenere i due modelli qwen3-vl
```

## 2. Layout delle cartelle

```
E:\infra\                        (già predisposto, non toccato da questi script)
  tools\uv\                      uv
  tools\winsw\WinSW-x64.exe
  python\                        Python gestito da uv (UV_PYTHON_INSTALL_DIR)
  cache\uv\                      cache di uv (UV_CACHE_DIR)

E:\apps\ocr-cannabis\
  app\                           checkout git di QUESTA repository (ats-webapp)
    .venv\                       ambiente Python della WEBAPP (uv sync)
    deploy\windows\               gli script di questa guida
    .env                          configurazione (da .env.example, MAI committato)
  pipeline\                      checkout git della repo SEPARATA ocr-cannabis-ats
    .venv\                       ambiente Python della PIPELINE (uv sync, separato)
  data\
    storage\                      STORAGE_LOCAL_DIR (file permanenti: PDF/PNG/Excel/JSON)
    models\easyocr\               modelli EasyOCR pre-scaricati (EASYOCR_MODEL_DIR)
  logs\                          log della webapp a rotazione (LOG_DIR) + log WinSW
  backup\                        dump Postgres + copie di data\ (backup.ps1)
```

`app` e `pipeline` sono **due repository git separate** clonate l'una a
fianco dell'altra, non una dentro l'altra — `PIPELINE_DIR` nel `.env` punta
a `pipeline\`, non a una sottocartella di `app\`. Se la repo pipeline non
è ancora clonata lì, `install.ps1` si ferma con un errore chiaro al primo
passo.

## 3. Installazione

```powershell
cd E:\apps\ocr-cannabis
git clone <url-ats-webapp> app
git clone <url-ocr-cannabis-ats> pipeline
cd app
git checkout deploy/windows-server   # o il branch/tag di produzione scelto

# Prima esecuzione: crea .env da .env.example e si ferma qui apposta
.\deploy\windows\install.ps1
```

A questo punto **apri `E:\apps\ocr-cannabis\app\.env` e compilalo**,
almeno:

| Variabile | Valore |
|---|---|
| `APP_ENV` | `production` |
| `POSTGRES_DB` | `ocr_cannabis` |
| `POSTGRES_USER` | `ocr_cannabis` |
| `POSTGRES_PASSWORD` | lascia vuota — `install.ps1` ne genera una casuale e la scrive qui da solo |
| `SESSION_SECRET_KEY` | lascia vuota — `install.ps1` ne genera una da solo se assente |
| `PIPELINE_RUNNER` | `local` |
| `PIPELINE_PYTHON` | `E:\apps\ocr-cannabis\pipeline\.venv\Scripts\python.exe` |
| `PIPELINE_DIR` | `E:\apps\ocr-cannabis\pipeline` |
| `STORAGE_BACKEND` | `local` |
| `STORAGE_LOCAL_DIR` | `E:\apps\ocr-cannabis\data\storage` |
| `EASYOCR_MODEL_DIR` | `E:\apps\ocr-cannabis\data\models\easyocr` |
| `LOG_DIR` | `E:\apps\ocr-cannabis\logs` |
| `APP_PORT` | `8000` (default, da cambiare solo se quella porta è già in uso) |
| `ROOT_PATH` | `/cannabis` se IIS serve l'app sotto quel percorso (vedi sezione 4), vuota se alla radice |

**Quali sono segrete**: `POSTGRES_PASSWORD` e `SESSION_SECRET_KEY` (generate
automaticamente, non vanno mai committate né condivise — il `.env` resta
fuori da git). Le altre sono configurazione, non segreti.

Poi rilancia (idempotente, salta quanto già fatto):

```powershell
.\deploy\windows\install.ps1
```

Questo giro, nell'ordine: verifica i prerequisiti → `uv sync` su webapp e
pipeline (ambienti separati) → smoke test dell'ambiente pipeline (import,
lettura di un barcode di prova, Tesseract `ita`) → crea ruolo/database
Postgres (chiede la password del superuser `postgres`, genera e scrive in
`.env` una password casuale per il ruolo dell'app) → genera
`SESSION_SECRET_KEY` se assente → applica le migrazioni Alembic → crea il
primo utente admin (chiede la password, **di persona**, non tramite
Claude Code) → scarica i modelli EasyOCR → registra il servizio Windows
`ocr-cannabis-web` (WinSW, chiede la password dell'account di servizio) →
crea l'attività pianificata di backup giornaliero (chiede di nuovo la
password dello stesso account, necessaria per salvarla nell'attività) →
avvia il servizio e verifica `/health`.

Opzioni utili di `install.ps1`:
- `-SaltaServizi` — prepara tutto (ambienti, DB, migrazioni, admin) ma non
  registra il servizio Windows, per un primo giro di verifica manuale
  (avvio di uvicorn a mano, in primo piano, per vedere subito eventuali
  errori — il comando esatto viene stampato a schermo).

## 4. Configurazione IIS e certificato (già fatta)

**Già configurata sul server** (nessuno di questi passi è automatizzato da
questi script, e non serve ripeterli): sito IIS con Application Request
Routing + URL Rewrite come reverse proxy HTTPS verso
`http://127.0.0.1:8000/`, prefisso `/cannabis/` **tolto** prima di passare
la richiesta a uvicorn, header `X-Forwarded-Proto: https` e
`X-Forwarded-Prefix: /cannabis` impostati, upload fino a 500 MB, timeout
10 minuti.

Perché i link generati dall'app corrispondano davvero al percorso esterno
`https://.../cannabis/...`, imposta `ROOT_PATH=/cannabis` nel `.env` (vedi
sezione 3) — oltre al flag `--root-path` passato a uvicorn (gestito da
`install.ps1`/`update.ps1`), l'app stessa usa la stessa variabile per
anteporre il prefisso a redirect, link nei template e chiamate `fetch()`
in JS: nessuna di queste route usa `url_for()`, quindi il meccanismo nativo
di Starlette legato a `--root-path` da solo non basterebbe (vedi
`app/root_path.py`). Il valore deve essere **identico** su entrambi i lati
(IIS e `.env`) — se in futuro cambia il percorso lato IIS, aggiorna anche
il `.env` e rilancia `update.ps1`.

La webapp si fida degli header `X-Forwarded-*` **solo da 127.0.0.1**
(`--forwarded-allow-ips=127.0.0.1`, impostato nel comando del servizio
`ocr-cannabis-web`) — se IIS girasse su un'altra macchina, questo valore
andrebbe adattato nello script.

## 5. Verifica

```powershell
.\deploy\windows\status.ps1
```

Controlla: stato del servizio `ocr-cannabis-web`, stato dell'infrastruttura
condivisa (`infra-ollama`, `postgresql-x64-16` — solo in lettura, questi
script non li gestiscono), risposta di `/health` (copre database, Ollama e
percorso di storage — vedi `app/main.py`), spazio libero su `E:`, eventuali
errori nelle ultime 24 ore nel log della webapp, età dell'ultimo backup.

Poi da browser, sulla macchina o da un client in rete: apri
`https://ATSVaUninsubria.aslva.it/cannabis/` (o il percorso configurato),
fai login con l'utente admin creato durante l'installazione, e **carica
almeno una ricetta di prova** per verificare l'intera pipeline
(preprocessing → OCR → difformità → Excel) prima di considerare
l'installazione pronta per l'uso reale.

## 6. Benchmark (calibrazione CPU)

Sui 40 vCPU del server, `num_thread` (per chiamata Ollama) e
`PIPELINE_PARALLELISMO` (documenti elaborati insieme) vanno calibrati
prima di passare a ricette reali:

```powershell
.\deploy\windows\benchmark.ps1 -Ricette "E:\apps\ocr-cannabis\data\pdf_test_anonimizzati"
```

Usa **solo PDF anonimizzati o di prova**, mai ricette reali. Prova di
default le combinazioni `num_thread` 20/30/38 × `parallelismo` 1/2 (`
-Thread`/`-Parallelismo` per cambiarle), misura tempo totale, tempo medio
per documento e picco di RAM per ciascuna, e produce un CSV + un report
Markdown sotto `E:\apps\ocr-cannabis\backup\benchmark\` — nessuno dei due
stampa mai il contenuto dei documenti. Riporta la combinazione migliore in
`.env` come `OLLAMA_NUM_THREAD_PESANTE`/`OLLAMA_NUM_THREAD_LEGGERO` e
`PIPELINE_PARALLELISMO`, poi `update.ps1` per applicarla (riavvia il
servizio, che rilegge il `.env`).

## 7. Aggiornamento

```powershell
.\deploy\windows\update.ps1
```

`git pull` sul branch corrente (o `-Branch <nome>` per cambiarlo), `uv
sync` su entrambi gli ambienti (webapp + pipeline, repo separate),
`alembic upgrade head`, riavvio del servizio `ocr-cannabis-web`, verifica
finale su `/health`. **Si ferma al primo errore**: se le migrazioni
falliscono, il servizio non viene riavviato con uno schema a metà strada —
risolvi il problema prima di ripetere.

Si rifiuta di partire se ci sono modifiche non committate nel repository
(evita che `git pull` fallisca a metà in un modo confuso). Non tocca la
repo pipeline (`ocr-cannabis-ats`) oltre a `uv sync` — un suo aggiornamento
di codice si coordina separatamente con chi la mantiene.

### Creare una nuova migrazione

Quando si cambia `app/models.py` (nuovo campo, nuova tabella), serve una
nuova revisione Alembic — `create_all()` è attivo solo fuori produzione
(`APP_ENV` diverso da `production`), quindi in produzione lo schema si
aggiorna SOLO con `alembic upgrade head`, mai da solo.

Dalla macchina di sviluppo (non sul server), con un database Postgres di
prova aggiornato all'ultima revisione esistente:

```powershell
# dalla radice del repository, ambiente della webapp attivo
alembic revision --autogenerate -m "descrizione breve della modifica"
```

Poi, SEMPRE a mano:
1. Apri il file generato in `alembic/versions/` e controlla `upgrade()`/
   `downgrade()` — l'autogenerate può perdere dettagli (default lato
   server, vincoli CHECK, rinomina di una colonna vista come
   drop+add — vedi la nota nel file della baseline per un bug reale già
   capitato: import mancante di `app.db_types`, necessario per il tipo
   `GUID()` usato in quasi tutte le tabelle).
2. Testa `alembic upgrade head` E `alembic downgrade -1` su un database
   Postgres vuoto o di prova — mai direttamente sul database di
   produzione.
3. Committa il file di migrazione insieme alla modifica di
   `app/models.py` che la richiede, nello stesso commit.

Sul server, `update.ps1` applica la nuova revisione con `alembic upgrade
head` durante il normale aggiornamento (vedi sopra) — nessun passo
manuale aggiuntivo, a patto che la migrazione sia stata creata e
verificata come sopra.

## 8. Backup e ripristino

Automatico ogni notte alle 3:00 (attività pianificata `ocr-cannabis-backup`
creata da `install.ps1`), oppure a mano:

```powershell
.\deploy\windows\backup.ps1                    # rotazione default 14 giorni
.\deploy\windows\backup.ps1 -GiorniRotazione 30
```

Produce `pg_dump` (formato custom, solo database `ocr_cannabis`) + copia
di `data\` in `E:\apps\ocr-cannabis\backup\<timestamp>\`.

Ripristino (**distruttivo**, chiede conferma esplicita):

```powershell
.\deploy\windows\restore.ps1 -CartellaBackup "E:\apps\ocr-cannabis\backup\2026-10-29_030000"
.\deploy\windows\restore.ps1 -CartellaBackup ... -SoloDatabase   # lascia invariata data\
```

Verifica periodicamente (non solo alla prima installazione) che l'attività
pianificata abbia davvero una password salvata — se mai dovesse fallire
silenziosamente, ricreala rilanciando `install.ps1` (è idempotente: la
attività esistente non viene toccata, cancellala prima a mano con
`Unregister-ScheduledTask -TaskName ocr-cannabis-backup` se la vuoi
rigenerare con una password diversa).

## 9. Rollback

Se un aggiornamento introduce un problema serio:

```powershell
cd E:\apps\ocr-cannabis\app
git log --oneline -10                  # trova il commit/tag buono precedente
git checkout <commit-o-tag-precedente>
uv sync
Push-Location ..\pipeline; uv sync; Pop-Location
```

Se la revisione precedente aveva anche uno schema DB diverso (raro, solo
se il problema è arrivato con una migrazione Alembic):

```powershell
.venv\Scripts\python.exe -m alembic downgrade -1   # UNA revisione indietro
```

**Prima di un downgrade dello schema**, fai un backup (`backup.ps1`) — un
`downgrade` può eliminare colonne/tabelle con dati, non solo tornare
indietro nel codice. Poi riavvia il servizio:

```powershell
Restart-Service ocr-cannabis-web
.\deploy\windows\status.ps1
```

## 10. Disinstallazione

```powershell
.\deploy\windows\uninstall.ps1
```

Ferma e rimuove il servizio `ocr-cannabis-web` (ed eventuali altri con
prefisso `ocr-cannabis-`) e l'attività pianificata di backup. **Non tocca**
dati, database, log, backup, né l'infrastruttura condivisa (`infra-ollama`,
`postgresql-x64-16`) — rimuovili a mano, deliberatamente, se non servono
più. `-MantieniAttivitaBackup` lascia attiva l'attività pianificata (utile
se il database resta al suo posto e si vuole continuare a fare backup).

## 11. Dopo un riavvio del server

Il server si riavvia da solo con gli aggiornamenti Windows — tutto deve
ripartire in automatico. Il servizio WinSW è configurato con avvio
automatico **ritardato** (`delayedAutoStart`, lascia partire prima i
servizi di sistema critici) e con riavvio automatico in caso di errore
(`onfailure`, due tentativi con backoff prima di arrendersi).

Dopo ogni riavvio del server, controlla comunque:

```powershell
.\deploy\windows\status.ps1
```

Se Postgres o Ollama non fossero ancora pronti quando `ocr-cannabis-web`
prova a partire (ordine di avvio dei servizi, raro ma possibile su un
riavvio lento), il servizio fallirà il primo avvio e **WinSW lo riprova da
solo** dopo 10 secondi — se dopo un paio di minuti `status.ps1` mostra
ancora `ocr-cannabis-web` fermo, verifica prima che `infra-ollama` e
`postgresql-x64-16` siano `Running`, poi riavvialo a mano
(`Restart-Service ocr-cannabis-web`).

## 12. Risoluzione problemi rapida

| Sintomo | Dove guardare |
|---|---|
| `/health` non risponde | `status.ps1`; log in `E:\apps\ocr-cannabis\logs\ats-webapp.log`; `Get-Service ocr-cannabis-web` |
| `/health` risponde ma segnala Ollama o storage in errore | Lo stesso 200 non vuol dire che tutto funzioni: controlla `infra-ollama` (`Get-Service`) e `STORAGE_BACKEND`/`STORAGE_LOCAL_DIR` nel `.env` — solo il database giù porta `/health` a 503, gli altri due restano segnalati senza bloccare login/archivio |
| Il servizio parte e si ferma subito | Log WinSW in `E:\apps\ocr-cannabis\logs\winsw\` (stdout/stderr del processo) — spesso `SESSION_SECRET_KEY` mancante o `STORAGE_LOCAL_DIR` non valido, l'app si rifiuta di partire con un errore esplicito in quei casi |
| Un lotto resta bloccato "in corso" dopo un riavvio | Normale al primo avvio successivo: `recupera_elaborazioni_orfane()` lo rimette in stato di errore automaticamente — controlla il lotto in UI |
| Pipeline lenta | `benchmark.ps1` (sezione 6) per calibrare `OLLAMA_NUM_THREAD_PESANTE`/`_LEGGERO` e `PIPELINE_PARALLELISMO` su questo hardware |
| Login/pagine rotti, link che puntano alla radice invece che a `/cannabis/...` | `ROOT_PATH` nel `.env` non corrisponde al percorso configurato in IIS — vedi sezione 4 |
| Errore di autenticazione Postgres durante `install.ps1` | Password del superuser `postgres` sbagliata (chiesta a ogni giro, non salvata) — verifica anche `Get-Service postgresql-x64-16` |
| Upload rifiutato con 413 | `MAX_UPLOAD_SIZE_MB` nel `.env` (default 500, coerente col limite IIS) — un upload troppo grande dal wizard mostra comunque un errore leggibile sulla stessa pagina, non un 413 nudo |
| File scritti nel posto sbagliato / mancanti dopo un lotto | `STORAGE_BACKEND`/`STORAGE_LOCAL_DIR` nel `.env` — in produzione l'app si rifiuta di partire se questo è configurato male, ma verificalo comunque se qualcosa sembra finito nel posto sbagliato |

Per qualunque problema non coperto qui: i log della webapp
(`logs\ats-webapp.log`, con rotazione) non contengono mai dati di
prescrizione (nomi, codici fiscali, ecc.) — quel canale resta solo nel
database (tabella `log_elaborazioni`, visibile nel dettaglio di ogni
lotto in UI) e in console, per progettazione (vedi commenti in
`app/main.py`).
