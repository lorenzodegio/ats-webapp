"""
pipeline_docker.py
--------------------
Due responsabilità, corrispondenti ai punti 1 e 2 del filone backend
(documento, Sezione 7.1):

1. avvia_pipeline_per_job() — orchestra le 4 fasi come container Docker
   Compose in sequenza, aggiornando Job.stato man mano che procedono
   (sostituisce il TODO che c'era in jobs.py).

2. popola_prescrizioni_da_output() / popola_difformita_da_output() —
   leggono i JSON prodotti dalle fasi OCR e Difformità e creano le
   righe Prescrizione/Difformita nel database, invece di lasciare
   /archivio e /difformita sui dati statici attuali.

Nomi dei servizi Docker, corrispondenti a quelli definiti in
docker-compose.yml (cartella radice del progetto) e docker/Dockerfile:
"fase1-preprocessing", "fase2-ocr", "fase3-difformita", "fase4-excel"
— NOTA l'ordine (difformità PRIMA di excel, il contrario del testo
del documento Sezione 1.2): vedi models.py, docstring di StatoJob,
per la spiegazione completa.

Ogni servizio ha il proprio comando già fissato in docker-compose.yml
(non serve passare argomenti da qui) — tutti i percorsi sono cartelle
condivise via volume Docker (./dati sull'host, /dati nei container),
vedi docker/run_fase.py.
"""

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from database import SessionLocal
from models import Job, Prescrizione, Difformita, StatoJob

log = logging.getLogger("PipelineDocker")

# Riconosce le righe di avanzamento nel formato [N/M] già usato in modo
# consistente in tutto il codice (fasi pipeline: "[1/4] ...", ricette:
# "  [34/96] file.pdf") — usato per filtrare cosa mostrare in tempo
# reale durante job lunghi (vedi _esegui_container), senza dover
# conoscere il formato esatto di ogni singola fase.
PATTERN_PROGRESSO = re.compile(r"\[\d+/\d+\]")

# Radice del progetto (ats-webapp/), dove sta docker-compose.yml — NON
# necessariamente la cartella corrente del processo Python: main.py va
# lanciato da dentro app/ (per i suoi import interni), ma "docker
# compose run" cerca docker-compose.yml nella cartella da cui viene
# eseguito. Senza specificare esplicitamente cwd qui, il comando
# fallirebbe SEMPRE ("no configuration file provided") non appena
# lanciato da un server avviato correttamente da app/.
#
# Containerizzato (HOST_PROJECT_ROOT impostata, vedi docker-compose.yml,
# servizio "webapp") ci sono DUE percorsi diversi da tenere separati,
# non uno solo:
#   1. Dove il file docker-compose.yml è DAVVERO leggibile da QUESTO
#      container (un percorso Linux normale, es. /workspace — il
#      client Docker installato qui dentro non sa interpretare un
#      percorso Windows con backslash come gerarchia di cartelle).
#      "cwd" sotto usa questo, serve solo a TROVARE e LEGGERE il file
#      — anche il contesto di build (context: .) si risolve bene
#      rispetto a questo percorso, perché i file vengono letti e
#      trasmessi al demone, non richiedono un percorso host reale.
#   2. Dove il DEMONE Docker (che gira nativo su Docker Desktop, non
#      dentro questo container) deve risolvere DAVVERO i volumi
#      relativi (./dati) delle 4 fasi — lì serve il percorso Windows
#      vero (es. "C:\Users\...\ats-webapp"), perché il demone conosce
#      solo il filesystem reale dell'host, non quello interno di
#      questo container. Gestito DIRETTAMENTE nel docker-compose.yml
#      (i volumi ./dati usano ${HOST_PROJECT_ROOT} con fallback a "."
#      per l'uso da PowerShell) — non da un flag qui: un tentativo
#      precedente con --project-directory applicava questo stesso
#      percorso Windows ANCHE alla risoluzione del contesto di build,
#      rompendola (un client Linux non riconosce "C:\..." come
#      assoluto, lo trattava come relativo alla cwd, percorso assurdo).
MOUNT_INTERNO_CONTAINER = Path("/workspace")
HOST_PROJECT_ROOT_WINDOWS = os.environ.get("HOST_PROJECT_ROOT")

RADICE_PROGETTO = MOUNT_INTERNO_CONTAINER if HOST_PROJECT_ROOT_WINDOWS \
    else Path(__file__).parent.parent

# Nomi dei file JSON prodotti dalla pipeline che NON sono singole
# prescrizioni (riepiloghi aggregati) — da escludere quando si legge
# la cartella di output
FILE_JSON_DA_ESCLUDERE = {"riepilogo.json", "riepilogo_difformita.json"}


def _esegui_container(nome_servizio: str, argomenti: list[str], job_id: int = None) -> None:
    """
    Esegue UN container Docker Compose e aspetta che finisca, leggendo
    l'output RIGA PER RIGA man mano che viene prodotto (non tutto in
    blocco a fine esecuzione) — necessario per vedere l'avanzamento
    durante job lunghi (ore), non solo un dump finale.

    In console (livello INFO) mostra solo le righe nel formato "[N/M]"
    già usato in modo consistente per il progresso (fase corrente,
    numero ricetta) — il resto (dettaglio per campo, per crop, per
    chiamata Ollama) va a livello DEBUG, quindi non sparisce: resta
    disponibile abbassando il livello di log, e viene comunque
    stampato per intero se il container fallisce, per non perdere
    nulla di utile al debug.

    Solleva un'eccezione se il container termina con codice di errore,
    così il chiamante può marcare il Job come StatoJob.errore invece
    di proseguire alla fase successiva su dati incompleti.
    """
    prefisso = f"[job {job_id}] " if job_id is not None else ""
    # -p (nome progetto) fisso: senza, Compose lo deriva dal nome della
    # cartella corrente ("workspace" da dentro questo container, invece
    # di "ats-webapp" come quando si lancia da PowerShell sull'host) —
    # con un nome diverso, Compose non riconosce le immagini delle 4
    # fasi già costruite e prova a ricostruirle ad ogni singolo job.
    # NIENTE --project-directory qui: sembrava necessario per risolvere
    # ./dati, ma essendo risolto lato CLIENT con le regole dei percorsi
    # Linux, un percorso Windows (con la sua doppia probabile ambiguità
    # di ':' e '\') veniva interpretato come relativo, non assoluto —
    # rompeva anche la risoluzione del contesto di build (context: .),
    # che invece funzionava già bene lasciando che si risolvesse
    # rispetto alla cartella corrente (il progetto vero, montato su
    # /workspace). Il problema reale riguardava SOLO i volumi ./dati
    # (che il DEMONE deve risolvere su un percorso host vero, non
    # quello interno a questo container) — sistemato direttamente nel
    # docker-compose.yml con un percorso assoluto esplicito per quei
    # volumi, non qui con un flag che tocca tutto indistintamente.
    comando = ["docker", "compose", "-p", "ats-webapp", "run", "--rm", nome_servizio, *argomenti]
    log.info(f"{prefisso}Avvio fase: {nome_servizio}")

    inizio = time.monotonic()
    # stderr=STDOUT: unisce i due flussi in ordine cronologico corretto
    # (docker scrive parte dei messaggi su stderr, la pipeline su
    # stdout — separarli avrebbe comunque perso l'ordine relativo).
    # encoding="utf-8" esplicito: senza, su Windows subprocess usa la
    # codifica di default del sistema (cp1252), diversa da quella con
    # cui il container scrive davvero il suo output — risultato:
    # caratteri accentati storpiati nei log (es. "â€”" al posto di "—").
    processo = subprocess.Popen(
        comando, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", cwd=RADICE_PROGETTO, bufsize=1,
    )

    righe_complete = []  # tenute per intero, per il dump completo in caso di errore
    for riga in processo.stdout:
        riga = riga.rstrip("\n")
        righe_complete.append(riga)
        if PATTERN_PROGRESSO.search(riga):
            log.info(f"{prefisso}[{nome_servizio}] {riga.strip()}")
        else:
            log.debug(f"{prefisso}[{nome_servizio}] {riga}")

    codice_ritorno = processo.wait()
    durata = time.monotonic() - inizio

    if codice_ritorno != 0:
        log.error(f"{prefisso}Container '{nome_servizio}' fallito (codice {codice_ritorno}) dopo {durata:.1f}s — output completo:")
        for riga in righe_complete:
            log.error(f"{prefisso}[{nome_servizio}] {riga}")
        raise RuntimeError(
            f"Container '{nome_servizio}' terminato con errore (codice {codice_ritorno}) dopo {durata:.1f}s"
        )

    log.info(f"{prefisso}Fase completata: {nome_servizio} in {durata:.1f}s.")


def popola_prescrizioni_da_output(db, job: Job, cartella_output: Path) -> int:
    """
    Legge tutti i JSON prodotti dalla fase 2 (OCR) per questo job, e
    crea (o aggiorna, se già presente) una riga Prescrizione per
    ciascuno. Restituisce il numero di prescrizioni create/aggiornate.
    """
    contatore = 0
    for json_path in cartella_output.glob("*.json"):
        if json_path.name in FILE_JSON_DA_ESCLUDERE:
            continue
        try:
            dati = json.loads(json_path.read_text(encoding="utf-8-sig"))
        except Exception as e:
            log.error(f"Errore lettura {json_path.name}: {e}")
            continue

        barcode = dati.get("barcode", json_path.stem)
        esistente = (
            db.query(Prescrizione)
            .filter_by(job_id=job.id, barcode=barcode)
            .first()
        )

        if esistente:
            esistente.dati_estratti_json = json.dumps(dati, ensure_ascii=False)
        else:
            db.add(Prescrizione(
                barcode=barcode,
                dati_estratti_json=json.dumps(dati, ensure_ascii=False),
                excel_scritto=False,
                job=job,
            ))
        contatore += 1

    db.commit()
    log.info(f"Job {job.id}: {contatore} prescrizioni popolate da fase 2.")
    return contatore


def segna_excel_scritto(db, job: Job) -> None:
    """Marca tutte le prescrizioni di questo job come 'excel_scritto', dopo la fase 3."""
    db.query(Prescrizione).filter_by(job_id=job.id).update({"excel_scritto": True})
    db.commit()


def popola_difformita_da_output(db, job: Job, cartella_output: Path) -> int:
    """
    Rilegge gli stessi JSON (ora arricchiti con difformita_codici/
    difformita_descrizioni dalla fase 4) e crea le righe Difformita
    collegate alla Prescrizione corrispondente. Se richiamata più
    volte per lo stesso job, sostituisce le difformità precedenti
    invece di duplicarle.
    """
    contatore = 0
    for json_path in cartella_output.glob("*.json"):
        if json_path.name in FILE_JSON_DA_ESCLUDERE:
            continue
        try:
            dati = json.loads(json_path.read_text(encoding="utf-8-sig"))
        except Exception as e:
            log.error(f"Errore lettura {json_path.name}: {e}")
            continue

        barcode = dati.get("barcode", json_path.stem)
        codici = dati.get("difformita_codici", [])
        descrizioni = dati.get("difformita_descrizioni", [])

        presc = (
            db.query(Prescrizione)
            .filter_by(job_id=job.id, barcode=barcode)
            .first()
        )
        if presc is None:
            log.warning(f"Prescrizione con barcode={barcode} non trovata per il job {job.id} — salto le sue difformità.")
            continue

        # Evita duplicati se questa funzione gira più di una volta sullo stesso job
        db.query(Difformita).filter_by(prescrizione_id=presc.id).delete()

        for codice, descrizione in zip(codici, descrizioni):
            db.add(Difformita(tipo=codice, descrizione=descrizione, gravita="media", prescrizione=presc))
            contatore += 1

    db.commit()
    log.info(f"Job {job.id}: {contatore} difformità popolate da fase 4.")
    return contatore


def esiste_job_in_esecuzione(db) -> bool:
    """
    Verifica se esiste già un Job in una delle fasi attive (non ancora
    completato né terminato in errore). Usata per impedire che due
    job partano in parallelo — condividerebbero la stessa cartella
    dati/ e gli stessi container Docker, con rischio concreto di
    mescolare i file di due elaborazioni diverse.
    """
    stati_attivi = (
        StatoJob.in_coda, StatoJob.fase1_preprocessing, StatoJob.fase2_ocr,
        StatoJob.fase3_difformita, StatoJob.fase4_excel,
    )
    return db.query(Job).filter(Job.stato.in_(stati_attivi)).first() is not None


def avvia_pipeline_per_job(job_id: int, cartella_output: Path) -> None:
    """
    Punto 1: orchestra le 4 fasi in sequenza, aggiornando Job.stato ad
    ogni passaggio, e richiama il punto 2 (popolamento DB) nei momenti
    giusti (dopo fase 2 per le Prescrizioni, dopo fase 3 per le
    Difformità, dopo fase 4 per la conferma di scrittura Excel).

    NOTA sull'ordine: qui la difformità (fase 3) viene calcolata PRIMA
    della scrittura Excel (fase 4) — l'ordine inverso rispetto al testo
    del documento (Sezione 1.2), perché l'Excel finale deve includere
    le colonne di difformità già calcolate, non può scriverle prima
    che esistano. Vedi models.py, docstring di StatoJob.

    Pensata per girare in un thread separato (stesso principio già
    usato nel job_manager.py della pipeline locale), così la richiesta
    HTTP che crea il Job può rispondere subito senza aspettare che
    l'intera pipeline finisca.
    """
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job is None:
            log.error(f"Job {job_id} non trovato — impossibile avviare la pipeline.")
            return

        job.stato = StatoJob.fase1_preprocessing
        db.commit()
        _esegui_container("fase1-preprocessing", [], job_id=job.id)

        job.stato = StatoJob.fase2_ocr
        db.commit()
        _esegui_container("fase2-ocr", [], job_id=job.id)
        popola_prescrizioni_da_output(db, job, cartella_output)

        job.stato = StatoJob.fase3_difformita
        db.commit()
        _esegui_container("fase3-difformita", [], job_id=job.id)
        popola_difformita_da_output(db, job, cartella_output)

        job.stato = StatoJob.fase4_excel
        db.commit()
        _esegui_container("fase4-excel", [], job_id=job.id)
        segna_excel_scritto(db, job)

        job.stato = StatoJob.completato
        db.commit()
        log.info(f"Job {job_id}: pipeline completata con successo.")

    except Exception as e:
        log.error(f"Job {job_id}: pipeline interrotta — {e}")
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.stato = StatoJob.errore
            db.commit()
    finally:
        db.close()
