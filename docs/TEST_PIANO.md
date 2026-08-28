# Piano di test — ATS Gestione Prescrizioni Cannabis

Documento per **Francesco** e **Alessandro**: come far partire l’applicazione, cosa verificare prima della consegna ad ATS, domande tipiche in demo, checklist da spuntare.

Data di riferimento: agosto 2026. Branch da usare: `dev` (o `test` dopo il merge).

---

## 1. Cosa stiamo consegnando

Applicazione web interna ATS Insubria che:

1. riceve un PDF combinato di ricette di cannabis terapeutica (e opzionalmente l’Excel regionale);
2. esegue una pipeline Docker (preprocessing → OCR con Ollama/Qwen → analisi difformità → Excel/ZIP per farmacia);
3. chiede all’operatore di rivedere barcode, qualità OCR e difformità tra una fase automatica e l’altra.

Stack: FastAPI, Jinja2, SQLite (default) o PostgreSQL, Docker Compose per le 4 fasi, container Ollama con GPU.

Repo: `ats-webapp`. Pipeline OCR nel container (script in root: `ocr_cannabis.py`, `pipeline.py`, …). Repo correlata: `ocr-cannabis-ats`.

---

## 2. Avvio tecnico

### 2.1 Macchina ATS (Windows + Docker Desktop + WSL2 + GPU) — scenario ufficiale

Questa è la configurazione reale di consegna.

**Prerequisiti**

- Docker Desktop avviato, integrazione WSL2 attiva, GPU visibile (`docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi`).
- Python 3.11+ sull’host (o in WSL) se la webapp gira **fuori** dai container (consigliato: uvicorn sull’host, Docker solo per OCR/Ollama).
- Rete Docker esterna:

```bash
docker network create ats-pipeline_default
```

(Se esiste già, il comando fallisce: va bene.)

**Ollama**

```bash
docker compose -f docker-compose.ollama.yml up -d
docker exec ats-ollama ollama pull qwen2.5vl:7b
docker exec ats-ollama ollama pull qwen2.5vl:32b
```

I modelli in `docker-compose.yml` (`MODELLO_PESANTE` / `MODELLO_LEGGERO`) **devono coincidere** con quelli pullati. Se in compose restano i default `qwen3-vl:…` e in Ollama ci sono solo `qwen2.5vl:…`, l’OCR non parte. Allineare le env del servizio `fase2-ocr` / `fase3-difformita` prima del test.

Fuso orario log: `TZ=Europe/Rome` (già in compose). Dopo un pull di `dev` fare rebuild delle immagini pipeline (`docker compose build`) e ricreare Ollama se i log sono ancora in UTC.

**Webapp sull’host (consigliato)**

Dalla root del repo:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env
python seed_admin.py
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` è obbligatorio: altrimenti l’app risponde solo da `localhost` sulla macchina ATS e **non** dai PC in rete.

- `seed_admin.py` chiede la password admin (Invio = `admin123`). Di default crea **solo** `admin`.
- Per utenti demo: `$env:SEED_DEMO_UTENTI="1"; python seed_admin.py`  
  | Utente | Password | Ruolo |
  |--------|----------|--------|
  | admin | (quella scelta) | amministratore |
  | operatore | operatore123 | operatore |
  | revisore | revisore123 | revisore |

La webapp lancia `docker compose -p ats-webapp run …` dalla **radice del repo**: Docker deve essere nel PATH della stessa sessione di uvicorn.

**Uso da PC personale (consigliato per i test)** — non occupare la postazione ATS.

Se uvicorn è già in ascolto sulla macchina ATS, dal **vostro** computer (stessa LAN / VPN ATS) aprire il browser su:

```
http://10.1.20.20:8000/
```

L’interfaccia è la stessa di `http://localhost:8000` sulla macchina. Docker, GPU e Ollama restano **sulla ATS**: voi usate solo il browser. Due persone possono essere loggate da due PC; resta il vincolo di **un solo job Docker alla volta**.

Se la pagina non si apre: ping `10.1.20.20`, stessa rete, uvicorn con `0.0.0.0`, firewall Windows sulla ATS che consente TCP **8000** in ingresso. Non serve installare Python/Docker sul vostro PC.

**Coda `./dati`**

I container montano `./dati`. Un solo lotto in fase Docker alla volta (vedi FAQ). Non copiare a mano file dentro `dati` mentre gira un job.

**PDF per l’interfaccia**

Le anteprime/PDF in UI usano `./media` sull’host, non il volume del container.

### 2.2 Ubuntu / WSL2 (stesso flusso, comandi Unix)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python seed_admin.py
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Docker: stesso `compose` da Linux. GPU: NVIDIA Container Toolkit in WSL.

Percorso progetto: evitare spazi strani se possibile. Se uvicorn è su Windows e Docker su WSL, `cwd` di `subprocess` deve essere il clone che Docker Desktop vede (stesso tree montato).

### 2.3 Webapp in Docker (opzionale, Docker-fuori-da-Docker)

Servizio `webapp` in `docker-compose.yml`. Serve `HOST_PROJECT_ROOT` nel `.env`: percorso **assoluto** del repo come lo vede Docker Desktop (su WSL2 spesso `/run/desktop/mnt/host/c/Users/...`).

Monta `/var/run/docker.sock`. Usare solo se si vuole tutto containerizzato; per i test ATS è più semplice uvicorn sull’host.

### 2.4 PostgreSQL (opzionale)

Default: SQLite `ats_cannabis.db` nella root.

Per Postgres: compilare `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` nel `.env`, poi:

```bash
docker compose -f docker-compose.postgres.yml up -d
```

Rilanciare seed dopo il cambio DB. SQLite **non ha migrazioni**: dopo un cambio di `models.py` cancellare `ats_cannabis.db` e `python reset_db.py` (o seed). Errore `OperationalError: no such column` = DB vecchio, non bug di logica.

### 2.5 Verifica rapida che lo stack sia vivo

| Controllo | Comando / azione | Esito atteso |
|-----------|------------------|--------------|
| Web (sulla ATS) | `http://localhost:8000/login` | Pagina Accedi |
| Web (dal vostro PC) | `http://10.1.20.20:8000/` | Stesso login, senza occupare la postazione |
| Ollama | `docker exec ats-ollama ollama list` | Modelli VL presenti |
| Rete | `docker network inspect ats-pipeline_default` | `ats-ollama` collegato |
| GPU | `nvidia-smi` durante OCR | Processo Ollama sulla GPU |
| Compose pipeline | `docker compose config` dalla root | Nessun errore YAML |

---

## 3. Ruoli e permessi

| Azione | Admin | Operatore | Revisore |
|--------|:-----:|:---------:|:--------:|
| Login, dashboard, lotti, archivio, wizard | sì | sì | sì |
| Impostazioni (farmacie, tab Sistema) | sì | no (voce nascosta) | no |
| Logout da sidebar | sì | sì | sì |

Non esiste un “cambio utente” senza logout: sessione cookie. Due browser / due profili possono stare loggati insieme.

---

## 4. Flusso operativo da testare end-to-end

Ordine obbligatorio (gate manuali):

```
Nuovo lotto (dati + PDF [+ Excel])
  → Preprocessing (Docker)
  → Revisione barcode (operatore)
  → OCR / VLLM (Docker + Ollama)
  → Revisione qualità OCR (operatore)
  → Analisi difformità (Docker)
  → Revisione difformità (operatore)
  → Completa (Excel finale, Docker fase4)
  → Export ZIP per farmacia / Archivia
```

Stepper in UI: **Dati lotto → Caricamento → Preprocessing → OCR → Difformità → Cartelle farmacie** (6 step). Dopo il submit, il dettaglio lotto continua sullo stesso schema di fasi.

### 4.1 Login / logout

- Credenziali sbagliate → messaggio errore, resti su `/login`.
- Già loggato che apre `/login` → redirect dashboard.
- Click sul **nome utente** in fondo alla sidebar → compare **Esci e cambia utente** → `/logout` → login, poi altro utente.

### 4.2 Nuovo lotto

- Nome obbligatorio; PDF obbligatorio (estensione `.pdf`); Excel `.xlsx`/`.xls` opzionale (si può aggiungere dopo).
- Se un altro lotto è in `preprocessing` / `elaborazione_ocr` / `analisi_difformita`, il POST `/lotti/nuovo` rifiuta con: *Un'altra elaborazione Docker è già in corso*.
- Lotti in revisione o completati **non** bloccano un nuovo avvio.

### 4.3 Preprocessing e barcode

- Log visibili, polling stato.
- Barcode illeggibili: assegnazione / esclusione; PDF collegato.
- Solo con **zero** barcode undefined si può avviare OCR.

### 4.4 OCR

- Tempi lunghi (minuti per ricetta col 32B, poi 7B). Non uccidere il container al primo silenzio.
- EasyOCR può scaricare pesi al primo avvio (CPU nel container OCR; la GPU serve a Ollama).
- Pausa / riprendi / annulla / Riprova su eccezione.

### 4.5 Qualità, difformità, chiusura

- Conferma / esclusione difformità.
- Completa lotto solo da `revisione_difformita` e se l’Excel finale viene scritto.
- `GET /lotti/{id}/output-excel` e `GET /lotti/{id}/export-zip` (ZIP dopo completato/archiviato).
- Archivia da completato.

### 4.6 Archivio e dashboard

- Dashboard: lotti in corso + filtri anno/mese/stato + KPI.
- Archivio: ricerca barcode / nome lotto / farmacia; gruppi `<details>`.

### 4.7 Impostazioni (solo admin)

- CRUD farmacie, disattiva/riattiva/elimina, export CSV.
- Tab Sistema: sola lettura (percorsi, soglia score, `pipeline_backend=reale`).

---

## 5. Domande che ATS (o noi in demo) può farci

Risposte da codice attuale — da confermare in test se il comportamento è quello voluto.

**Posso avviare due elaborazioni contemporaneamente?**  
No, non due pipeline Docker insieme. Il vincolo è su lotti in stato `preprocessing`, `elaborazione_ocr` o `analisi_difformita` (cartella `./dati` condivisa). Due operatori **non** devono lanciare due OCR in parallelo. Lotti in *revisione* possono coesistere (niente container). Durante la scrittura Excel di chiusura lo stato è ancora `revisione_difformita`: in teoria un secondo lotto potrebbe partire in quel breve intervallo — da verificare in test e, se necessario, estendere il lock.

**Due persone possono usare l’app insieme?**  
Sì, sessioni separate. Non due OCR Docker. Stesso utente su due browser: due sessioni.

**Dobbiamo stare fisicamente sulla macchina ATS per testare?**  
No. Con uvicorn già acceso sulla ATS (`--host 0.0.0.0 --port 8000`), dal vostro PC aprite `http://10.1.20.20:8000/`. La postazione ATS resta libera. Serve solo essere in rete con quella macchina.

**Quanto dura un lotto?**  
Dipende dal numero di ricette e dai modelli. Ordine di grandezza: decine di minuti / ore per lotti grandi (es. 9 ricette con 32B+7B). Dire “non è istantaneo” in demo.

**Cosa succede se spengo il PC / Docker in mezzo?**  
Lotto in `eccezione` o bloccato. Tasto Riprova sulla fase fallita. Non promettere recovery perfetto di un container ucciso a metà senza Riprova.

**Posso mettere in pausa?**  
Sì, durante elaborazione in corso: pausa/riprendi/annulla sul container nominato.

**Serve la GPU?**  
Sì per Ollama/Qwen. EasyOCR nel container OCR può andare in CPU (più lento solo sul rilevamento etichetta).

**I file dove stanno?**  
Metadati nel DB. PDF/Excel/output su filesystem (SharePoint montato in produzione; in locale `sharepoint_finto` / `media` / `dati`). Il DB non contiene i binari.

**Chi vede Impostazioni?**  
Solo amministratore.

**Possiamo cambiare password da interfaccia?**  
No in questa versione. Si cambia hash in DB o si ricrea l’utente con seed.

**Excel regionale obbligatorio?**  
No in creazione; serve per il match Regione più avanti. Senza Excel alcune verifiche saranno più povere.

**Barcode cliccabile?**  
Sì, PDF della prescrizione.

**Filtro difformità bloccanti vs minori?**  
Sì, sul dettaglio lotto in revisione difformità: «Filtra per gravità» (Tutte / Bloccanti / Minori).

**Due lotti dello stesso mese?**  
Sì: cartella univoca con timestamp + UUID.

**Login dopo reset DB?**  
Cookie vecchio viene pulito (`get_utente_opzionale`); si torna al login, non loop di redirect.

**Pipeline finta?**  
Non più: `pipeline_backend` è forzato a `reale`. Senza Docker/Ollama i lotti nuovi falliscono in eccezione.

**Orari nei log diversi dall’orologio Windows?**  
UTC vs `Europe/Rome`. Dopo rebuild/recreate devono coincidere.

**Posso usare l’app dal telefono?**  
Pensata per desktop/LAN ufficio. Responsive non è un requisito di consegna.

**Dati personali / GDPR in demo?**  
Usare PDF di prova o dati mascherati; non condividere screenshot con CF visibili fuori ATS.

---

## 6. Ambiente e regressioni note (non reintrodurre)

- `str | None` → usare `Optional[str]` se si tocca codice 3.8 (ATS è 3.11, ma attenzione).
- Output Docker: `encoding="utf-8", errors="replace"`.
- Merge Regione prima dei check 14/17/18 in `docker/run_fase.py`.
- Virtualenv non portabile tra PC: ricrearlo, non copiarlo.
- Non rinominare endpoint `/login`, `/jobs/nuovo` (legacy), rotte `/lotti/...` usate dalla UI.

---

## 7. Come riportare i test

1. Copiare `docs/CHECKLIST_TEST.md` in un file personale o spuntare su GitHub/Notion.
2. Per ogni voce: **OK** / **KO** / **N/A** + nota (browser, lotto id, screenshot).
3. Allegare log container (`docker logs`) se KO su OCR.
4. Un giro **completo** su PDF reali va fatto **sulla macchina ATS**, non sul portatile di sviluppo senza GPU.
