# Produzione su Windows Server (senza Docker/WSL2, senza GPU)

Guida operativa per installare e gestire l'app su un server Windows Server
2022 (VM, CPU-only — nessuna GPU), dove Docker/WSL2 non sono disponibili.
La macchina ATS con GPU continua a funzionare normalmente con Docker: le
due modalità convivono (`PIPELINE_RUNNER`, vedi sotto), nessuna delle due
esclude l'altra.

**Stato**: scritto e verificato dove possibile (sintassi degli script,
test funzionali mirati dei singoli meccanismi — vedi i commit sul branch
`deploy/windows-server`), ma **mai eseguito su un vero Windows Server**.
Il primo giro reale va fatto con attenzione, passo per passo, non "a
occhi chiusi" — segnala qualunque scostamento da quanto descritto qui.

## Indice
1. [Prerequisiti](#1-prerequisiti)
2. [Layout delle cartelle](#2-layout-delle-cartelle)
3. [Installazione](#3-installazione)
4. [Configurazione IIS e certificato](#4-configurazione-iis-e-certificato-manuale)
5. [Verifica](#5-verifica)
6. [Aggiornamento](#6-aggiornamento)
7. [Backup e ripristino](#7-backup-e-ripristino)
8. [Rollback](#8-rollback)
9. [Dopo un riavvio del server](#9-dopo-un-riavvio-del-server)
10. [Risoluzione problemi rapida](#10-risoluzione-problemi-rapida)

---

## 1. Prerequisiti

Installati **a mano** prima di eseguire qualunque script (nessuno di questi
è automatizzato da `install.ps1`):

- Git
- Python 3.11 (per tutti gli utenti)
- [uv](https://docs.astral.sh/uv/) (gestione dipendenze/ambienti Python)
- Visual C++ Redistributable 2013 e 2015-2022
- **Tesseract-OCR**, installato in `C:\Program Files\Tesseract-OCR` — **non
  era nella lista originale dei prerequisiti**: serve davvero, è usato come
  fallback quando un barcode non viene letto (`front_detector.py`, chiamato
  da `phase1_preprocess.py`). Scaricalo dall'installer Windows ufficiale
  (UB-Mannheim) e verifica che `tesseract.exe` finisca in quel percorso o
  nel PATH.
- PostgreSQL 16, come servizio Windows, in ascolto solo su `localhost`
- Ollama standalone (eseguibile, non ancora registrato come servizio
  Windows — ci pensa `install.ps1`, vedi sotto)
- IIS con il modulo reverse proxy (Application Request Routing + URL
  Rewrite) — configurazione manuale, vedi [sezione 4](#4-configurazione-iis-e-certificato-manuale)
- Un utente locale dedicato per i servizi (qui chiamato `svc_ocr`), con il
  diritto **"Log on as a service"** già concesso (Criteri di sicurezza
  locali → Criteri locali → Assegnazione dei diritti utente → "Accesso
  come servizio"). `install.ps1` non lo concede da solo.
- Accesso amministratore locale sulla macchina

Verifica rapida di tutto quanto sopra (tranne IIS/IIS cert, cosa a parte):

```powershell
git --version; python --version; uv --version
Test-Path "C:\Program Files\Tesseract-OCR\tesseract.exe"
Get-Service postgresql* | Select Name, Status
ollama --version
```

## 2. Layout delle cartelle

```
E:\infra\
  ollama\                  dati/modelli del servizio Ollama (OLLAMA_MODELS)
  winsw\                   WinSW.exe + XML dei servizi generati da install.ps1

E:\apps\ocr-cannabis\      checkout git di questo repository
  .venv\                   ambiente Python della WEBAPP (uv sync, radice)
  docker\.venv\            ambiente Python della PIPELINE (uv sync, docker\ — separato)
  data\
    storage\                STORAGE_LOCAL_DIR (file permanenti: PDF/PNG/Excel/JSON)
    models\easyocr\         modelli EasyOCR pre-scaricati (EASYOCR_MODEL_DIR)
  logs\                     log della webapp a rotazione (LOG_DIR) + log WinSW
  backup\                   dump Postgres + copie di data\ (backup.ps1)
  deploy\windows\           gli script di questa guida
  .env                      configurazione (da .env.example, MAI committato)
```

`WinSW.exe` **non è scaricato automaticamente** da `install.ps1` — prendilo
da <https://github.com/winsw/winsw/releases> (una release `WinSW-x64.exe`,
rinominata `WinSW.exe`) e mettilo in `E:\infra\winsw\` prima di lanciare
`install.ps1`.

## 3. Installazione

```powershell
cd E:\apps
git clone <url-del-repo> ocr-cannabis
cd ocr-cannabis
git checkout deploy/windows-server   # o il branch/tag di produzione scelto

# Prima esecuzione: crea .env da .env.example e si ferma qui apposta
.\deploy\windows\install.ps1
```

A questo punto **apri `E:\apps\ocr-cannabis\.env` e compilalo**, almeno:

| Variabile | Valore |
|---|---|
| `APP_ENV` | `production` |
| `POSTGRES_PASSWORD` | password del ruolo che verrà creato |
| `SESSION_SECRET_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `PIPELINE_RUNNER` | `local` |
| `PIPELINE_PYTHON` | `E:\apps\ocr-cannabis\docker\.venv\Scripts\python.exe` |
| `PIPELINE_DIR` | `E:\apps\ocr-cannabis` |
| `STORAGE_BACKEND` | `local` |
| `STORAGE_LOCAL_DIR` | `E:\apps\ocr-cannabis\data\storage` |
| `EASYOCR_MODEL_DIR` | `E:\apps\ocr-cannabis\data\models\easyocr` |
| `LOG_DIR` | `E:\apps\ocr-cannabis\logs` |
| `APP_PORT` | `8000` (default, da cambiare solo se quella porta è già in uso) |
| `ROOT_PATH` | vuota se il sito IIS serve l'app alla radice, altrimenti il prefisso (es. `/cannabis`) — vedi sezione 4 |

Poi rilancia (idempotente, salta quanto già fatto):

```powershell
.\deploy\windows\install.ps1
```

Questo secondo giro: sincronizza gli ambienti `uv` (webapp + pipeline,
separati), scarica i modelli Ollama (`qwen3-vl:30b-a3b-instruct`,
`qwen3-vl:8b-instruct`) e i modelli EasyOCR, crea database/utente Postgres
(encoding UTF8 da `template0`), applica le migrazioni Alembic, chiede la
password del primo admin (richiesta in modo sicuro, non salvata in
chiaro), registra i servizi Windows (WinSW) e l'attività pianificata di
backup giornaliero.

Al termine, imposta **a mano** l'account di esecuzione dei due servizi
(`ATS-Ollama`, `ATS-Webapp`) su `svc_ocr`:

```powershell
# da services.msc: scheda "Accesso" di ciascun servizio, oppure:
sc.exe config ATS-Ollama obj= ".\svc_ocr" password= "<password>"
sc.exe config ATS-Webapp obj= ".\svc_ocr" password= "<password>"
```

Poi avvia e verifica:

```powershell
Start-Service ATS-Ollama
Start-Service ATS-Webapp
.\deploy\windows\status.ps1
```

Opzioni utili di `install.ps1`:
- `-SaltaModelli` — rilancia senza riscaricare GB di modelli già presenti
- `-SaltaServizi` — prepara tutto ma non registra i servizi Windows, per
  un primo giro di verifica manuale (`& .venv\Scripts\python.exe -m uvicorn
  app.main:app` a mano, in primo piano, per vedere subito eventuali errori)

## 4. Configurazione IIS e certificato (manuale)

**Non scriptata apposta** — questi script non toccano IIS, certificati o
firewall. Passi (una tantum):

1. Installa il modulo **Application Request Routing (ARR)** e **URL
   Rewrite** per IIS, se non già presenti.
2. In IIS Manager, abilita il proxy ARR (Server → Application Request
   Routing Cache → Server Proxy Settings → "Enable proxy").
3. Crea (o usa) un sito IIS con binding HTTPS sul certificato del server.
4. Aggiungi una regola di URL Rewrite di tipo "Reverse Proxy" verso
   `http://localhost:8000` (la webapp ascolta solo su `127.0.0.1:8000`,
   mai esposta direttamente).
5. Se il sito IIS serve l'app sotto un percorso diverso dalla radice (es.
   `https://server/cannabis/` invece di `https://server/`, col prefisso
   TOLTO da IIS prima di passare la richiesta a uvicorn), imposta
   `ROOT_PATH` nel `.env` (es. `ROOT_PATH=/cannabis`) e rilancia
   `install.ps1` (o `update.ps1`) per rigenerare il comando del servizio
   con `--root-path /cannabis`. Oltre al flag passato a uvicorn, l'app
   stessa usa la stessa variabile per anteporre il prefisso a redirect,
   link nei template e chiamate `fetch()` in JS (nessuna di queste route
   usa `url_for`, quindi il meccanismo nativo di Starlette da solo non
   basterebbe — vedi `app/root_path.py`): imposta lo stesso valore su
   entrambi i lati (IIS e `.env`), altrimenti i link generati dall'app
   non corrisponderanno al percorso esterno reale.
6. Apri la porta 443 (o quella usata) sul firewall di Windows, se non già
   aperta.

La webapp si fida degli header `X-Forwarded-*` **solo da 127.0.0.1**
(`--forwarded-allow-ips=127.0.0.1`, impostato nel comando del servizio
`ATS-Webapp`) — se IIS non gira sulla stessa macchina, questo valore va
adattato.

## 5. Verifica

```powershell
.\deploy\windows\status.ps1
```

Controlla: stato dei 3 servizi (Ollama, Webapp, Postgres), risposta di
`/health`, spazio libero su `E:`, eventuali errori nelle ultime 24 ore nel
log della webapp, età dell'ultimo backup.

Poi da browser, sulla macchina o da un client in rete: apri l'URL HTTPS
configurato in IIS, fai login con l'utente admin creato durante
l'installazione, e **carica almeno una ricetta di prova** per verificare
l'intera pipeline (preprocessing → OCR → difformità → Excel) prima di
considerare l'installazione pronta per l'uso reale.

## 6. Aggiornamento

```powershell
.\deploy\windows\update.ps1
```

`git pull` sul branch corrente (o `-Branch <nome>` per cambiarlo), `uv
sync` su entrambi gli ambienti, `alembic upgrade head`, riavvio del
servizio `ATS-Webapp`, verifica finale su `/health`. **Si ferma al primo
errore**: se le migrazioni falliscono, i servizi non vengono riavviati con
uno schema a metà strada — risolvi il problema prima di ripetere.

Si rifiuta di partire se ci sono modifiche non committate nel repository
(evita che `git pull` fallisca a metà in un modo confuso).

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

## 7. Backup e ripristino

Automatico ogni notte alle 3:00 (attività pianificata creata da
`install.ps1`), oppure a mano:

```powershell
.\deploy\windows\backup.ps1                    # rotazione default 14 giorni
.\deploy\windows\backup.ps1 -GiorniRotazione 30
```

Produce `pg_dump` (formato custom) + copia di `data\` in
`E:\apps\ocr-cannabis\backup\<timestamp>\`.

Ripristino (**distruttivo**, chiede conferma esplicita):

```powershell
.\deploy\windows\restore.ps1 -CartellaBackup "E:\apps\ocr-cannabis\backup\2026-10-29_030000"
.\deploy\windows\restore.ps1 -CartellaBackup ... -SoloDatabase   # lascia invariata data\
```

Verifica periodicamente (non solo alla prima installazione) che l'attività
pianificata abbia davvero le credenziali di `svc_ocr` salvate — vedi
l'avviso che `install.ps1` stampa in merito, è un passo che Windows non fa
automaticamente da riga di comando.

## 8. Rollback

Se un aggiornamento introduce un problema serio:

```powershell
cd E:\apps\ocr-cannabis
git log --oneline -10                  # trova il commit/tag buono precedente
git checkout <commit-o-tag-precedente>
uv sync
Push-Location docker; uv sync; Pop-Location
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
Restart-Service ATS-Webapp
.\deploy\windows\status.ps1
```

## 9. Dopo un riavvio del server

Il server si riavvia da solo con gli aggiornamenti Windows — tutto deve
ripartire in automatico. I servizi WinSW sono configurati con avvio
automatico **ritardato** (`delayedAutoStart`, lasciano partire prima i
servizi di sistema critici) e con riavvio automatico in caso di errore
(`onfailure`, due tentativi con backoff prima di arrendersi).

Dopo ogni riavvio del server, controlla comunque:

```powershell
.\deploy\windows\status.ps1
```

Se Postgres non fosse ancora pronto quando `ATS-Webapp` prova a partire
(ordine di avvio dei servizi, raro ma possibile su un riavvio lento), il
servizio webapp fallirà il primo avvio e **WinSW lo riprova da solo** dopo
10 secondi — se dopo un paio di minuti `status.ps1` mostra ancora
`ATS-Webapp` fermo, riavvialo a mano (`Restart-Service ATS-Webapp`) dopo
aver verificato che Postgres sia `Running`.

## 10. Risoluzione problemi rapida

| Sintomo | Dove guardare |
|---|---|
| `/health` non risponde | `status.ps1`; log in `E:\apps\ocr-cannabis\logs\ats-webapp.log`; `Get-Service ATS-Webapp` |
| L'app parte e si ferma subito | Log WinSW in `E:\apps\ocr-cannabis\logs\winsw\` (stdout/stderr del processo) — spesso `SESSION_SECRET_KEY` mancante o `STORAGE_LOCAL_DIR` non valido, l'app si rifiuta di partire con un errore esplicito in quei casi |
| Un lotto resta bloccato "in corso" dopo un riavvio | Normale al primo avvio successivo: `recupera_elaborazioni_orfane()` lo rimette in stato di errore automaticamente — controlla il lotto in UI |
| Pipeline lenta | `benchmark.py` per calibrare `OLLAMA_NUM_THREAD_PESANTE`/`_LEGGERO` e `PIPELINE_PARALLELISMO` su questo hardware specifico |
| "database is locked" o simili errori Postgres | Verifica `Get-Service postgresql*`; controlla che `POSTGRES_HOST/PORT` nel `.env` puntino davvero a `localhost` |
| Upload rifiutato con 413 | `MAX_UPLOAD_SIZE_MB` nel `.env` (default 200) — alzalo se servono PDF più grandi |
| File scritti nel posto sbagliato / mancanti dopo un lotto | `STORAGE_BACKEND`/`STORAGE_LOCAL_DIR` nel `.env` — in produzione l'app si rifiuta di partire se questo è configurato male, ma verificalo comunque se qualcosa sembra finito nel posto sbagliato |

Per qualunque problema non coperto qui: i log della webapp
(`logs\ats-webapp.log`, con rotazione) non contengono mai dati di
prescrizione (nomi, codici fiscali, ecc.) — quel canale resta solo nel
database (tabella `log_elaborazioni`, visibile nel dettaglio di ogni
lotto in UI) e in console, per progettazione (vedi commenti in
`app/main.py`).
