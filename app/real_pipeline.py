"""
BACKEND REALE — orchestra i 4 container Docker della pipeline OCR vera
(integrazione portata dal lavoro di Francesco sul branch backend,
riscritta per il modello dati lotti_mensili/UUID invece del vecchio
Job/Integer).

Differenza di fondo rispetto a fake_pipeline.py: qui i file DEVONO
transitare per la cartella condivisa ./dati (montata nei container via
docker-compose.yml) perche' e' li' che i container Docker leggono e
scrivono. Il principio "SharePoint e' il filesystem" resta vero per lo
STORAGE permanente (sharepoint_finto/ in sviluppo, il vero SharePoint
in produzione): prima di ogni fase i file del lotto vengono copiati da
li' a ./dati, dopo ogni fase i risultati vengono raccolti da ./dati e
salvati nella posizione permanente del lotto.

Vincolo importante: un solo lotto alla volta puo' essere "in corso" —
dalla creazione fino a completato/archiviato/eccezione — non solo mentre
ha un container Docker realmente in esecuzione (vedi esiste_lotto_in_esecuzione
piu' sotto, ereditato da esiste_job_in_esecuzione di Francesco). Le fasi
di revisione manuale (barcode/qualita'/difformita') NON hanno un
container attivo, ma NON possono comunque coesistere con un altro
lotto: i JSON prodotti da OCR/difformita' restano nella cartella
condivisa ./dati/output finche' la fase Excel finale non li legge, e
quella cartella viene svuotata dal PROSSIMO preprocessing di
QUALUNQUE lotto — se un secondo lotto partisse mentre il primo aspetta
solo una revisione manuale, i suoi JSON sparirebbero in silenzio,
scoperto solo quando quel lotto arriva a "Completa" e non trova piu'
nulla (bug reale, gia' capitato — vedi STATI_LOTTO_OCCUPATO piu' sotto).

NON TESTATO END-TO-END: scritto senza un ambiente Docker/Ollama
disponibile. La logica di orchestrazione e il parsing dei JSON sono
basati sulla lettura diretta del codice reale (ocr_cannabis.py,
phase1_preprocess.py, phase5_difformita.py, run_fase.py), ma vanno
verificati su una macchina con Docker Desktop + Ollama configurati
prima di considerarli affidabili in produzione. Vedi il messaggio di
consegna per l'elenco preciso dei punti da verificare.
"""
import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, date
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.storage import (
    DATI_DIR,
    RADICE_PROGETTO,
    RICETTE,
    RICETTE_RAW,
    RICETTE_STAGING_IMAGES,
    RICETTE_STAGING_PDFS,
    cartella_pagine_lotto,
    nome_file_sicuro,
    pdf_originali_lotto,
    percorso_pdf_prescrizione,
    pubblica_file,
    pubblica_output_preprocessing,
    radice_sharepoint,
    relativo_a_radice,
    rinomina_pagina_media,
)
from app.models import (
    LottoMensile, StatoLotto, Elaborazione, FaseElaborazione, StatoElaborazione,
    LogElaborazione, LivelloLog, Prescrizione, StatoBarcode, DatiOcr,
    Difformita, StatoDifformita,
)

log = logging.getLogger("RealPipeline")

EXCEL_REGIONE_DIR = DATI_DIR / "excel_regione"
OUTPUT_DIR = DATI_DIR / "output"

FILE_JSON_DA_ESCLUDERE = {"riepilogo.json"}

# Barre di progresso stile tqdm (es. "Progress: |████---| 97.8% Complete"):
# vanno rilevate per aggiornare la stessa riga di log invece di accumularne
# centinaia quasi identiche (vedi _esegui_container).
PATTERN_BARRA_PROGRESSO = re.compile(r"Progress:\s*\|.*\|\s*[\d.]+%\s*Complete", re.IGNORECASE)

# Mappa dei campi realmente prodotti da ocr_cannabis.py verso i nomi
# colonna di DatiOcr. Le chiavi identiche non sono elencate (mappate 1:1).
# ATTENZIONE — due mapping non ovvi, da confermare con Francesco:
#   - "nome_cognome_assistito" (reale) -> "cognome_nome_assistito" (schema):
#     stesso dato, ordine delle parole diverso nel nome del campo.
#   - "data_emissione" (reale) non esiste nello schema DatiOcr, che ha
#     "data_invio": li ho considerati equivalenti (data di invio/emissione
#     della prescrizione), ma e' un'assunzione mia, non confermata.
#   - "THC" (reale, maiuscolo) -> "etichetta_thc" (schema).
MAPPA_CAMPI_OCR = {
    "nome_cognome_assistito": "cognome_nome_assistito",
    "data_emissione": "data_invio",
    "THC": "etichetta_thc",
}

# Inversa di MAPPA_CAMPI_OCR: dal nome colonna DatiOcr alla chiave grezza
# che i file JSON su disco (letti da fase3-difformita e fase4-excel) si
# aspettano — serve per riportare le correzioni manuali dell'operatore
# (vedi _applica_correzioni_ocr_su_json) sugli stessi nomi campo con cui
# la pipeline li ha scritti originariamente.
MAPPA_CAMPI_OCR_INVERSA = {v: k for k, v in MAPPA_CAMPI_OCR.items()}

CAMPI_DATA = {
    "data_prescrizione", "data_etichetta_preparazione", "data_invio", "etichetta_data_scadenza",
}
CAMPI_BOOLEANI = {"timbro_medico", "firma_medico"}
CAMPI_PREZZO = {
    "etichetta_prezzo_sost", "etichetta_prezzo_on", "etichetta_prezzo_rec",
    "etichetta_prezzo_iva", "etichetta_prezzo_tot", "totale_prescrizione",
}

COLONNE_DATI_OCR = {
    "cognome_nome_assistito", "codice_fiscale", "codice_esenzione", "codice_atc",
    "testo_prescrizione", "metodo_estrattivo_olio", "forma_farmaceutica",
    "data_prescrizione", "data_etichetta_preparazione", "data_invio",
    "etichetta_data_scadenza", "timbro_medico", "firma_medico",
    "etichetta_nome_cognome_medico", "etichetta_nome_cognome_paziente",
    "etichetta_prezzo_sost", "etichetta_prezzo_on", "etichetta_prezzo_rec",
    "etichetta_prezzo_iva", "etichetta_prezzo_tot", "totale_prescrizione",
    "etichetta_thc", "nome_farmacia", "etichetta_avvertenze",
}

# data_prescrizione e data_invio (= data_emissione) vengono presi SEMPRE
# dall'Excel Regione, mai dalla lettura OCR del PDF (vedi
# merge_regione.py:sostituisci_data_con_regione) — la loro presenza/assenza
# non dice nulla sulla qualita' della lettura OCR, quindi vanno esclusi sia
# dallo score OCR sia dal conteggio "campi compilati" (24 - 2 = 22).
CAMPI_ORIGINE_REGIONE = {"data_prescrizione", "data_invio"}
CAMPI_SCORE_OCR = COLONNE_DATI_OCR - CAMPI_ORIGINE_REGIONE


class LottoGiaInEsecuzione(Exception):
    pass


class ElaborazioneAnnullata(Exception):
    """Sollevata quando l'operatore annulla un'elaborazione reale in corso."""


def pulisci_dati_parziali_fase(db: Session, lotto: LottoMensile, fase: FaseElaborazione) -> None:
    """
    Ripulisce i dati scritti a meta' da un tentativo di questa fase
    interrotto (fallito o annullato): una fase puo' fermarsi a meta' ciclo,
    con alcune prescrizioni gia' elaborate e altre no. Senza questa
    pulizia, sia "Riprova" sia una nuova elaborazione dopo "Annulla"
    rischierebbero di lavorare su dati incoerenti o creare doppioni.

    Condivisa tra _gestisci_annullamento qui sotto (annulla e basta,
    lascia il lotto in eccezione) e /lotti/{id}/riprova in lotti.py
    (pulisce e rilancia subito la stessa fase) — stessa pulizia, due
    momenti diversi in cui serve.
    """
    if fase == FaseElaborazione.preprocessing:
        for p in list(lotto.prescrizioni):
            db.delete(p)
        lotto.n_prescrizioni_totali = 0
        lotto.n_barcode_letti = 0
        lotto.n_barcode_undefined = 0
    elif fase == FaseElaborazione.vllm:
        for p in lotto.prescrizioni:
            if p.dati_ocr:
                db.delete(p.dati_ocr)
            p.score_ocr = None
            p.n_campi_compilati = None
            p.barcode_in_excel = None
            p.riga_excel = None
            p.sp_json_path = None
        lotto.score_ocr_medio = None
        lotto.n_match_excel = 0
    elif fase == FaseElaborazione.difformita:
        for p in lotto.prescrizioni:
            for d in list(p.difformita):
                db.delete(d)
            p.decisione_etichetta_mancante = None
        lotto.n_difformita_totali = 0
    elif fase == FaseElaborazione.completa:
        lotto.excel_output_filename = None


def _gestisci_annullamento(db: Session, lotto_id, elaborazione: Elaborazione) -> None:
    """Marca elaborazione e lotto come annullati dall'operatore (non un errore vero),
    e ripulisce i dati parziali scritti dalla fase interrotta (vedi pulisci_dati_parziali_fase)."""
    messaggio = "Esecuzione annullata dall'operatore"
    log_elaborazione(db, elaborazione, messaggio)
    elaborazione.stato = StatoElaborazione.annullata
    elaborazione.richiesta_controllo = None
    elaborazione.finished_at = datetime.utcnow()
    lotto = db.query(LottoMensile).filter(LottoMensile.id == lotto_id).first()
    if lotto:
        pulisci_dati_parziali_fase(db, lotto, elaborazione.fase)
        lotto.stato = StatoLotto.eccezione
        lotto.note = ((lotto.note or "") + f"\n[{datetime.utcnow():%d/%m %H:%M}] {messaggio}").strip()
        lotto.updated_at = datetime.utcnow()
    db.commit()


def recupera_elaborazioni_orfane(db: Session) -> int:
    """
    Chiamata una volta all'avvio del processo (main.py). Qualunque
    Elaborazione ancora "in_coda" o "in_corso" a questo punto e' per forza
    orfana: se il thread Python che la seguiva fosse ancora vivo, questo
    stesso processo non starebbe ripartendo da zero. Capita quando uvicorn
    viene riavviato mentre un'elaborazione reale e' in corso — il vecchio
    thread muore con il processo, senza mai passare dal proprio gestore
    di errori/annullamento.

    Senza questo, il lotto resta bloccato "in corso" per sempre, senza
    nessuna via di recupero funzionante dall'interfaccia: ne' "Annulla"
    (imposta solo un flag che nessun ciclo vivo legge piu') ne' "Riprova"
    (visibile solo per lotti gia' in stato "eccezione") hanno effetto.
    Qui vengono marcate esplicitamente come "errore" cosi' il lotto passa
    a eccezione e "Riprova"/"Elimina" tornano disponibili.
    """
    orfane = (
        db.query(Elaborazione)
        .filter(Elaborazione.stato.in_([StatoElaborazione.in_coda, StatoElaborazione.in_corso]))
        .all()
    )
    for elaborazione in orfane:
        messaggio = "Elaborazione interrotta da un riavvio del server, segnata come fallita"
        log_elaborazione(db, elaborazione, messaggio, LivelloLog.error)
        elaborazione.stato = StatoElaborazione.errore
        elaborazione.richiesta_controllo = None
        elaborazione.finished_at = datetime.utcnow()
        lotto = db.query(LottoMensile).filter(LottoMensile.id == elaborazione.lotto_id).first()
        if lotto:
            pulisci_dati_parziali_fase(db, lotto, elaborazione.fase)
            lotto.stato = StatoLotto.eccezione
            lotto.note = ((lotto.note or "") + f"\n[{datetime.utcnow():%d/%m %H:%M}] {messaggio}").strip()
            lotto.updated_at = datetime.utcnow()
    db.commit()
    if orfane:
        log.warning(f"Recuperate {len(orfane)} elaborazioni orfane all'avvio: {[str(e.id) for e in orfane]}")
    return len(orfane)


def metti_in_pausa_container(nome_container: str) -> bool:
    """
    Comando diretto (docker pause), chiamato dalla route non appena
    l'operatore clicca "Metti in pausa" — non c'e' bisogno che il ciclo
    di lettura dell'output se ne accorga: il container si congela a
    livello di sistema operativo (cgroup freeze), la lettura dello
    stdout in real_pipeline.py restera' semplicemente in attesa di
    nuove righe finche' non viene ripreso.
    """
    try:
        subprocess.run(["docker", "pause", nome_container], capture_output=True, text=True, timeout=10)
        return True
    except Exception as exc:
        log.warning(f"Impossibile mettere in pausa il container {nome_container}: {exc}")
        return False


def riprendi_container(nome_container: str) -> bool:
    try:
        subprocess.run(["docker", "unpause", nome_container], capture_output=True, text=True, timeout=10)
        return True
    except Exception as exc:
        log.warning(f"Impossibile riprendere il container {nome_container}: {exc}")
        return False


def annulla_container(nome_container: str) -> bool:
    try:
        subprocess.run(["docker", "kill", nome_container], capture_output=True, text=True, timeout=10)
        return True
    except Exception as exc:
        log.warning(f"Impossibile terminare il container {nome_container}: {exc}")
        return False


# Tutti gli stati tranne quelli conclusi: un secondo lotto NON puo' iniziare
# finche' il primo non arriva a completato/archiviato/eccezione — non solo
# mentre un container e' realmente in esecuzione. Include anche le fasi di
# revisione manuale (nessun container attivo, ma i JSON di OCR/difformita'
# di QUESTO lotto restano nella cartella condivisa ./dati/output finche' la
# fase Excel finale non li consuma: un secondo lotto che avviasse il
# preprocessing in quella finestra la svuoterebbe, perdendo quei JSON in
# silenzio — vedi il commento in cima al modulo).
STATI_LOTTO_OCCUPATO = (
    StatoLotto.bozza, StatoLotto.caricamento, StatoLotto.preprocessing,
    StatoLotto.revisione_barcode, StatoLotto.elaborazione_ocr,
    StatoLotto.revisione_qualita, StatoLotto.analisi_difformita,
    StatoLotto.revisione_difformita,
)


def lotto_in_esecuzione(db: Session, escludi_lotto_id=None):
    """
    Il lotto (se presente) che occupa attualmente "lo slot" — dalla creazione
    fino a completato/archiviato/eccezione, non solo mentre un container Docker e'
    realmente in esecuzione (vedi STATI_LOTTO_OCCUPATO). Usato sia per bloccare
    l'avvio di un nuovo lotto sia per dire QUALE lotto e' occupato.

    escludi_lotto_id: il lotto che sta chiedendo "posso partire?" deve
    escludere se stesso dalla ricerca — a quel punto e' gia' stato creato
    con uno stato "occupato" (caricamento), quindi senza questo la query
    troverebbe SE STESSO e si bloccherebbe da solo, sempre, anche a
    database vuoto (bug reale, gia' capitato).
    """
    query = db.query(LottoMensile).filter(LottoMensile.stato.in_(STATI_LOTTO_OCCUPATO))
    if escludi_lotto_id is not None:
        query = query.filter(LottoMensile.id != escludi_lotto_id)
    return query.first()


def esiste_lotto_in_esecuzione(db: Session, escludi_lotto_id=None) -> bool:
    return lotto_in_esecuzione(db, escludi_lotto_id) is not None


def _pulisci_cartella_dati():
    """Svuota ./dati prima di ogni lotto: i container non devono vedere file del lotto precedente."""
    if DATI_DIR.exists():
        shutil.rmtree(DATI_DIR)
    for cartella in (RICETTE_RAW, RICETTE_STAGING_IMAGES, RICETTE_STAGING_PDFS, RICETTE, EXCEL_REGIONE_DIR, OUTPUT_DIR):
        cartella.mkdir(parents=True, exist_ok=True)


def _scrivi_farmacie_per_pipeline(db: Session) -> None:
    """Esporta le farmacie attive in ./dati/farmacie.json per prompt + matching fuzzy nel container."""
    from app.farmacie_io import scrivi_json_pipeline
    try:
        percorso = scrivi_json_pipeline(db, DATI_DIR / "farmacie.json")
        log.info(f"Elenco farmacie scritto in {percorso}")
    except Exception as exc:
        log.warning(f"Impossibile scrivere farmacie.json per la pipeline: {exc}")


def log_elaborazione(db: Session, elaborazione: Elaborazione, messaggio: str, livello: LivelloLog = LivelloLog.info) -> None:
    db.add(LogElaborazione(elaborazione_id=elaborazione.id, messaggio=messaggio, livello=livello))
    db.commit()
    getattr(log, livello.value if livello != LivelloLog.warning else "warning")(messaggio)


def _esegui_container(nome_servizio: str, db: Session, elaborazione: Elaborazione) -> None:
    """
    Esegue un container Docker Compose e ne trasmette l'output riga per
    riga nel log dell'elaborazione MAN MANO che viene prodotto (non alla
    fine): pipeline.py logga gia' da solo il progresso in formato
    "[i/N] nome_file.pdf" durante l'estrazione OCR — usiamo lo stesso
    formato anche nel backend finto (fake_pipeline.py) cosi' l'interfaccia
    puo' mostrare "X di Y" a prescindere da quale backend sia attivo.

    Il container riceve un nome univoco (--name) e viene salvato su
    elaborazione.nome_container: e' quello che le route di pausa/ripresa/
    annullamento useranno per agire direttamente sul container con
    "docker pause/unpause/kill", senza dover coordinarsi con questo
    ciclo di lettura (che semplicemente si blocca in attesa di nuove
    righe mentre il container e' in pausa, e termina quando viene ucciso).

    Presuppone: rete Docker esterna "ats-pipeline_default" gia' creata,
    container "ats-ollama" gia' in esecuzione su quella rete (vedi
    docker-compose.ollama.yml), e comando "docker" disponibile nel PATH
    di chi esegue l'app web (Docker Desktop + WSL2 integration attiva).
    """
    nome_container = f"ats-{nome_servizio}-{str(elaborazione.id)[:8]}"
    elaborazione.nome_container = nome_container
    db.commit()

    comando = [
        "docker", "compose", "-p", "ats-webapp",
        "run", "--name", nome_container, "--rm", nome_servizio,
    ]
    log.info(f"Eseguo: {' '.join(comando)}")

    processo = subprocess.Popen(
        comando, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        universal_newlines=True, cwd=str(RADICE_PROGETTO),
    )

    ultima_riga_progresso = None
    for riga in processo.stdout:
        riga = riga.rstrip()
        if not riga:
            continue

        if PATTERN_BARRA_PROGRESSO.search(riga):
            # Barra di progresso stile tqdm (usa \r per riscriversi sul
            # posto in un terminale vero): aggiorno l'ultima riga invece
            # di accumularne centinaia quasi identiche.
            if ultima_riga_progresso is not None:
                ultima_riga_progresso.messaggio = riga
                ultima_riga_progresso.timestamp = datetime.utcnow()
                db.commit()
            else:
                ultima_riga_progresso = LogElaborazione(
                    elaborazione_id=elaborazione.id, messaggio=riga, livello=LivelloLog.info
                )
                db.add(ultima_riga_progresso)
                db.commit()
            continue

        # Una riga "vera" (non barra di progresso) chiude la barra corrente:
        # se ne arriva un'altra piu' avanti, sara' una barra nuova.
        ultima_riga_progresso = None

        livello = LivelloLog.info
        if "[ERROR]" in riga or "ERRORE" in riga.upper():
            livello = LivelloLog.error
        elif "[WARNING]" in riga or "ATTENZIONE" in riga.upper():
            livello = LivelloLog.warning
        log_elaborazione(db, elaborazione, riga, livello)

    codice_uscita = processo.wait()
    if codice_uscita != 0:
        db.refresh(elaborazione)
        if elaborazione.richiesta_controllo == "annulla":
            raise ElaborazioneAnnullata()
        raise RuntimeError(f"Container '{nome_servizio}' terminato con errore (codice {codice_uscita})")


def _parse_data_italiana(valore):
    """
    I campi data prodotti dall'OCR sono stringhe in formato non
    garantito al 100% (date_corrector.py esiste apposta per
    normalizzarle) — provo i formati piu' comuni, altrimenti lascio
    vuoto invece di far fallire l'intero import.

    data_etichetta_preparazione e etichetta_data_scadenza in particolare
    arrivano da date_corrector.py con un prompt che chiede l'anno a 4
    cifre, ma il modello a volte lo scrive comunque a 2 cifre (es.
    "04/12/25" invece di "04/12/2025") — senza i formati %y qui sotto
    quel valore falliva silenziosamente tutti e 3 i tentativi e veniva
    scartato (None), anche se l'OCR aveva letto la data correttamente.
    """
    if not valore or not isinstance(valore, str):
        return None
    valore = valore.strip()
    for formato in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(valore, formato).date()
        except ValueError:
            continue
    return None


def _parse_booleano(valore):
    """
    timbro_medico/firma_medico dovrebbero arrivare dal JSON dell'OCR come
    bool nativi (true/false), ma il modello a volte li scrive come stringa
    ("false", "False"...) — usare bool(valore) direttamente e' un bug
    subdolo perche' bool("false") vale True in Python (qualunque stringa
    non vuota e' truthy). Qui il valore finale rispetta sempre il dato
    booleano vero letto dal JSON, non la sua "truthiness" come stringa.
    """
    if isinstance(valore, bool):
        return valore
    if valore in (None, ""):
        return None
    if isinstance(valore, (int, float)):
        return bool(valore)
    testo = str(valore).strip().lower()
    if testo in ("true", "1", "vero", "si", "sì", "yes"):
        return True
    if testo in ("false", "0", "falso", "no"):
        return False
    return None


def _parse_prezzo(valore):
    if not valore:
        return None
    try:
        return float(str(valore).replace(",", ".").replace("€", "").strip())
    except ValueError:
        return None


def _normalizza_dati_ocr(dati_grezzi: dict) -> dict:
    """Applica la mappa dei nomi campo e le conversioni di tipo, pronto per DatiOcr(**...)."""
    normalizzato = {}
    for chiave_reale, valore in dati_grezzi.items():
        chiave = MAPPA_CAMPI_OCR.get(chiave_reale, chiave_reale)
        if chiave not in COLONNE_DATI_OCR:
            continue  # campo non mappato nello schema (es. barcode, _etichetta_rilevata_*)
        if chiave in CAMPI_DATA:
            normalizzato[chiave] = _parse_data_italiana(valore)
        elif chiave in CAMPI_BOOLEANI:
            normalizzato[chiave] = _parse_booleano(valore)
        elif chiave in CAMPI_PREZZO:
            normalizzato[chiave] = _parse_prezzo(valore)
        else:
            normalizzato[chiave] = valore or None
    return _scarta_valori_troppo_lunghi(normalizzato)


def _scarta_valori_troppo_lunghi(campi: dict) -> dict:
    """
    Un valore letto dall'OCR che supera la lunghezza massima della sua
    colonna (es. codice_fiscale > 16 caratteri — un CF valido e' sempre
    esattamente 16, quindi oltre e' quasi certamente un errore di lettura)
    farebbe fallire l'inserimento non solo di questa ricetta ma
    dell'INTERO lotto di ricette scritte insieme in una sola transazione
    (vedi avvia_ocr_reale). Meglio azzerare il singolo campo e proseguire:
    l'assenza verra' comunque segnalata dai controlli di difformita' gia'
    esistenti (es. check_18 sul codice fiscale), invece di perdere tutte
    le altre ricette del lotto per l'errore di lettura di una sola.
    """
    lunghezze_colonne = {
        col.name: col.type.length
        for col in DatiOcr.__table__.columns
        if getattr(col.type, "length", None)
    }
    for chiave, valore in list(campi.items()):
        limite = lunghezze_colonne.get(chiave)
        if limite and isinstance(valore, str) and len(valore) > limite:
            log.warning(
                f"  Campo '{chiave}' scartato: {len(valore)} caratteri, oltre il limite "
                f"di {limite} (valore: '{valore[:40]}...')"
            )
            campi[chiave] = None
    return campi


def _copia_lotto_verso_dati(lotto: LottoMensile) -> None:
    """Copia i PDF (uno o piu': PDF combinato oppure cartella di PDF separati)
    e l'Excel Regione del lotto dallo storage permanente a ./dati."""
    _pulisci_cartella_dati()

    for originale in pdf_originali_lotto(lotto):
        shutil.copy2(originale, RICETTE_RAW / originale.name)

    if lotto.excel_input_filename and lotto.sp_lavoro_path:
        cartella_lavoro = radice_sharepoint() / lotto.sp_lavoro_path
        excel_path = cartella_lavoro / lotto.excel_input_filename
        if excel_path.exists():
            shutil.copy2(excel_path, EXCEL_REGIONE_DIR / excel_path.name)


def _pubblica_prescrizioni_su_sharepoint(lotto: LottoMensile) -> int:
    """
    Copia le ricette gia' estratte dal preprocessing (una per barcode,
    RICETTE = /dati/ricette dentro il container) nella cartella
    PRESCRIZIONI dello storage permanente del lotto — NON il PDF combinato
    caricato in origine (quello serve solo come input alla pipeline).
    Un operatore/farmacia che apre PRESCRIZIONI deve trovare le singole
    ricette, non un unico PDF multi-pagina da scorrere a mano.
    """
    if not lotto.sp_prescrizioni_path:
        return 0
    cartella = radice_sharepoint() / lotto.sp_prescrizioni_path
    cartella.mkdir(parents=True, exist_ok=True)
    n = 0
    for pdf in RICETTE.glob("*.pdf"):
        shutil.copy2(pdf, cartella / pdf.name)
        n += 1
    return n


def avvia_preprocessing_reale(lotto_id) -> None:
    db: Session = SessionLocal()
    elaborazione = None  # puo' non esistere ancora se si esce prima di crearla (vedi except sotto)
    try:
        lotto = db.query(LottoMensile).filter(LottoMensile.id == lotto_id).first()
        if lotto is None:
            return
        if esiste_lotto_in_esecuzione(db, escludi_lotto_id=lotto.id):
            raise LottoGiaInEsecuzione(
                "Un altro lotto e' ancora in lavorazione (nemmeno terminato un container "
                "puo' bastare: le fasi di revisione manuale occupano lo stesso slot, vedi "
                "STATI_LOTTO_OCCUPATO) — completalo prima di poter avviare questo."
            )

        elaborazione = Elaborazione(
            lotto_id=lotto.id, fase=FaseElaborazione.preprocessing,
            stato=StatoElaborazione.in_corso,
            comando_docker="docker compose run --rm fase1-preprocessing",
            started_at=datetime.utcnow(),
        )
        db.add(elaborazione)
        lotto.stato = StatoLotto.preprocessing
        db.commit()

        log_elaborazione(db, elaborazione, f"Copio i file del lotto '{lotto.nome}' in ./dati")
        _copia_lotto_verso_dati(lotto)
        _scrivi_farmacie_per_pipeline(db)

        log_elaborazione(db, elaborazione, "Avvio container fase1-preprocessing")
        _esegui_container("fase1-preprocessing", db, elaborazione)
        pubblica_output_preprocessing(lotto.id)
        log_elaborazione(db, elaborazione, "Pagine copiate in media/ per l'interfaccia (indipendente da Docker)")
        n_su_sharepoint = _pubblica_prescrizioni_su_sharepoint(lotto)
        log_elaborazione(db, elaborazione, f"{n_su_sharepoint} ricette copiate in PRESCRIZIONI su SharePoint")

        # Nessun manifest JSON scritto dal container: ricostruisco
        # l'elenco dalle cartelle di output, come fa run_fase.py stesso.
        n_letti, n_undefined = 0, 0
        for pdf in RICETTE.glob("*.pdf"):
            barcode = pdf.stem
            png_src = RICETTE_STAGING_IMAGES / f"{barcode}.png"
            db.add(Prescrizione(
                lotto_id=lotto.id, barcode=barcode, stato_barcode=StatoBarcode.letto,
                sp_pdf_path=pubblica_file(lotto.id, pdf),
                sp_png_path=pubblica_file(lotto.id, png_src) if png_src.is_file() else None,
            ))
            n_letti += 1

        for png in RICETTE_STAGING_IMAGES.glob("undefined_*.png"):
            pdf_src = RICETTE_STAGING_PDFS / f"{png.stem}.pdf"
            db.add(Prescrizione(
                lotto_id=lotto.id, barcode=None, stato_barcode=StatoBarcode.undefined,
                sp_pdf_path=pubblica_file(lotto.id, pdf_src) if pdf_src.is_file() else pubblica_file(lotto.id, png),
                sp_png_path=pubblica_file(lotto.id, png),
            ))
            n_undefined += 1

        db.commit()

        lotto.n_pdf_caricati = len(list(RICETTE_RAW.glob("*.pdf")))
        lotto.n_prescrizioni_totali = n_letti + n_undefined
        lotto.n_barcode_letti = n_letti
        lotto.n_barcode_undefined = n_undefined
        lotto.updated_at = datetime.utcnow()

        elaborazione.stato = StatoElaborazione.completata
        elaborazione.finished_at = datetime.utcnow()
        elaborazione.exit_code = 0
        elaborazione.n_processati = n_letti + n_undefined

        lotto.stato = StatoLotto.revisione_barcode
        log_elaborazione(db, elaborazione, f"Preprocessing completato: {n_letti} barcode letti, {n_undefined} da rivedere")
        db.commit()

    except ElaborazioneAnnullata:
        _gestisci_annullamento(db, lotto_id, elaborazione)
    except Exception as exc:
        log.exception("Errore in avvia_preprocessing_reale")
        db.rollback()
        if elaborazione is not None:
            elaborazione.stato = StatoElaborazione.errore
            elaborazione.finished_at = datetime.utcnow()
        lotto = db.query(LottoMensile).filter(LottoMensile.id == lotto_id).first()
        if lotto:
            lotto.stato = StatoLotto.eccezione
            lotto.note = ((lotto.note or "") + f"\n[{datetime.utcnow():%d/%m %H:%M}] Preprocessing: {exc}").strip()
        db.commit()
    finally:
        db.close()


def correggi_barcode_reale(prescrizione_id, nuovo_barcode: str) -> None:
    """
    Oltre ad aggiornare il DB, sposta per davvero il file dalla cartella
    di staging (ricette_staging/pdfs/undefined_*.pdf) a ./dati/ricette/,
    rinominandolo col barcode corretto — altrimenti la fase OCR
    successiva (che legge da ricette_dir) non lo troverebbe.
    """
    db: Session = SessionLocal()
    try:
        presc = db.query(Prescrizione).filter(Prescrizione.id == prescrizione_id).first()
        if presc is None or presc.stato_barcode != StatoBarcode.undefined:
            return

        vecchio_stem = Path(presc.sp_pdf_path).stem if presc.sp_pdf_path else None
        if vecchio_stem:
            origine = RICETTE_STAGING_PDFS / f"{vecchio_stem}.pdf"
            destinazione = RICETTE / f"{nuovo_barcode}.pdf"
            RICETTE.mkdir(parents=True, exist_ok=True)
            if origine.exists():
                shutil.move(str(origine), str(destinazione))
            rinomina_pagina_media(presc.lotto_id, vecchio_stem, nuovo_barcode)
            pdf_media = cartella_pagine_lotto(presc.lotto_id) / f"{nuovo_barcode}.pdf"
            png_media = cartella_pagine_lotto(presc.lotto_id) / f"{nuovo_barcode}.png"
            if pdf_media.is_file() and not destinazione.exists():
                shutil.copy2(pdf_media, destinazione)
            if pdf_media.is_file():
                presc.sp_pdf_path = relativo_a_radice(pdf_media)
            elif destinazione.exists():
                presc.sp_pdf_path = pubblica_file(presc.lotto_id, destinazione)
            if png_media.is_file():
                presc.sp_png_path = relativo_a_radice(png_media)

        presc.barcode = nuovo_barcode
        presc.stato_barcode = StatoBarcode.corretto_manuale
        db.commit()
    finally:
        db.close()


def avvia_ocr_reale(lotto_id) -> None:
    db: Session = SessionLocal()
    elaborazione = None  # puo' non esistere ancora se si esce prima di crearla (vedi except sotto)
    try:
        lotto = db.query(LottoMensile).filter(LottoMensile.id == lotto_id).first()
        if lotto is None:
            return

        elaborazione = Elaborazione(
            lotto_id=lotto.id, fase=FaseElaborazione.vllm,
            stato=StatoElaborazione.in_corso,
            comando_docker="docker compose run --rm fase2-ocr",
            started_at=datetime.utcnow(),
        )
        db.add(elaborazione)
        lotto.stato = StatoLotto.elaborazione_ocr
        db.commit()

        _scrivi_farmacie_per_pipeline(db)
        log_elaborazione(db, elaborazione, "Avvio container fase2-ocr (estrazione VLLM + arricchimento Regione)")
        _esegui_container("fase2-ocr", db, elaborazione)

        n_processati, n_match = 0, 0
        for json_path in OUTPUT_DIR.glob("*.json"):
            if json_path.name in FILE_JSON_DA_ESCLUDERE:
                continue
            dati_grezzi = json.loads(json_path.read_text(encoding="utf-8-sig"))
            barcode = dati_grezzi.get("barcode") or json_path.stem

            presc = db.query(Prescrizione).filter(
                Prescrizione.lotto_id == lotto.id, Prescrizione.barcode == barcode
            ).first()
            if presc is None:
                log_elaborazione(db, elaborazione, f"Nessuna prescrizione trovata per barcode {barcode}, salto", LivelloLog.warning)
                continue

            campi_normalizzati = _normalizza_dati_ocr(dati_grezzi)
            db.add(DatiOcr(
                prescrizione_id=presc.id,
                json_vllm_raw=dati_grezzi,
                extracted_at=datetime.utcnow(),
                **campi_normalizzati,
            ))

            # Score OCR = percentuale di campi compilati sui 22 che dipendono
            # davvero dalla lettura OCR (esclusi i 2 presi dall'Excel Regione,
            # vedi CAMPI_SCORE_OCR sopra). "Campi compilati" usa la stessa
            # base 22, cosi' le due colonne mostrate in lotto_detail.html
            # restano coerenti tra loro.
            n_compilati = sum(
                1 for chiave in CAMPI_SCORE_OCR
                if campi_normalizzati.get(chiave) not in (None, "")
            )
            presc.n_campi_compilati = n_compilati
            presc.n_campi_totali = len(CAMPI_SCORE_OCR)
            presc.score_ocr = round(100 * n_compilati / len(CAMPI_SCORE_OCR), 2)
            match_regione = str(dati_grezzi.get("CONTROLLO_CODICE_PRESCRIZIONE", "")).lower() == "true" \
                or bool(dati_grezzi.get("LORDO_PRESC"))
            presc.barcode_in_excel = match_regione
            if match_regione:
                n_match += 1
            n_processati += 1

        db.commit()

        lotto.n_match_excel = n_match
        scores = [float(p.score_ocr) for p in lotto.prescrizioni if p.score_ocr is not None]
        lotto.score_ocr_medio = round(sum(scores) / len(scores), 2) if scores else None
        lotto.updated_at = datetime.utcnow()

        elaborazione.stato = StatoElaborazione.completata
        elaborazione.finished_at = datetime.utcnow()
        elaborazione.exit_code = 0
        elaborazione.n_processati = n_processati

        lotto.stato = StatoLotto.revisione_qualita
        log_elaborazione(db, elaborazione, f"OCR completato: {n_processati} prescrizioni elaborate, {n_match} con match Regione")
        db.commit()

    except ElaborazioneAnnullata:
        _gestisci_annullamento(db, lotto_id, elaborazione)
    except Exception as exc:
        log.exception("Errore in avvia_ocr_reale")
        db.rollback()
        if elaborazione is not None:
            elaborazione.stato = StatoElaborazione.errore
            elaborazione.finished_at = datetime.utcnow()
        lotto = db.query(LottoMensile).filter(LottoMensile.id == lotto_id).first()
        if lotto:
            lotto.stato = StatoLotto.eccezione
            lotto.note = ((lotto.note or "") + f"\n[{datetime.utcnow():%d/%m %H:%M}] OCR: {exc}").strip()
        db.commit()
    finally:
        db.close()


def _valori_uguali(a, b) -> bool:
    """
    Confronto tollerante ai tipi: i valori numerici (es. Decimal dalla
    colonna Numeric del DB vs float da _parse_prezzo) possono differire
    per la sola rappresentazione binaria pur essendo lo stesso numero
    (es. Decimal('82.20') == 82.2 vale False in Python) — qui si
    arrotondano entrambi a 2 decimali prima di confrontare. Per tutto
    il resto (date, booleani, stringhe, None) e' un confronto normale.
    """
    try:
        return round(float(a), 2) == round(float(b), 2)
    except (TypeError, ValueError):
        return a == b


def _applica_correzioni_ocr_su_json(lotto: LottoMensile) -> int:
    """
    Riporta sui file JSON grezzi ancora su ./dati/output/ (quelli che
    leggono sia fase3-difformita sia fase4-excel) le correzioni manuali
    fatte dall'operatore in "Revisione qualita' OCR"
    (POST /lotti/{id}/prescrizioni/{id}/ocr, salvate finora solo in
    dati_ocr.json_corretto) — senza questo passaggio la correzione non
    aveva alcun effetto a valle: l'analisi difformita' e l'Excel finale
    leggevano comunque il valore OCR originale, mai quello corretto.

    Chiamata prima di entrambi i container (fase3 e fase4): un secondo
    passaggio e' innocuo (idempotente) e copre eventuali correzioni
    fatte tra le due fasi.
    """
    n = 0
    for presc in lotto.prescrizioni:
        dati_ocr = presc.dati_ocr
        if not dati_ocr or not dati_ocr.json_corretto or not presc.barcode:
            continue
        json_path = OUTPUT_DIR / f"{presc.barcode}.json"
        if not json_path.is_file():
            continue
        try:
            dati_grezzi = json.loads(json_path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            continue
        for campo, valore in dati_ocr.json_corretto.items():
            chiave_raw = MAPPA_CAMPI_OCR_INVERSA.get(campo, campo)
            dati_grezzi[chiave_raw] = valore
        json_path.write_text(json.dumps(dati_grezzi, ensure_ascii=False, indent=2), encoding="utf-8")
        n += 1
    return n


def _evidenzia_campi_corretti(destinazione: Path, lotto: LottoMensile) -> None:
    """
    Evidenzia in giallo, nell'Excel finale, le celle dei campi OCR che
    l'operatore ha aggiunto o modificato manualmente in "Revisione
    qualita' OCR" rispetto al valore letto originariamente dal modello
    (json_vllm_raw) — confronto sul valore GIA' NORMALIZZATO (stessa
    logica di _normalizza_dati_ocr, tramite _parse_data_italiana/
    _parse_booleano/_parse_prezzo) per non confondere una semplice
    differenza di formato con una correzione vera.
    """
    try:
        wb = openpyxl.load_workbook(destinazione)
        ws = wb.active
        intestazione = {}
        for col_idx in range(1, ws.max_column + 1):
            nome = ws.cell(row=1, column=col_idx).value
            if nome:
                intestazione[nome] = col_idx
        col_barcode = intestazione.get("BARCODE")
        if not col_barcode:
            return

        riga_per_barcode = {}
        for riga in range(2, ws.max_row + 1):
            valore = ws.cell(row=riga, column=col_barcode).value
            if valore:
                riga_per_barcode[re.sub(r"[^0-9A-Za-z]", "", str(valore))] = riga

        sfondo_corretto = PatternFill(fgColor="FFFFF3B0", fill_type="solid")

        evidenziate = 0
        for presc in lotto.prescrizioni:
            dati_ocr = presc.dati_ocr
            if not dati_ocr or not dati_ocr.corretto_at or not presc.barcode:
                continue
            raw = dati_ocr.json_vllm_raw or {}
            riga = riga_per_barcode.get(re.sub(r"[^0-9A-Za-z]", "", presc.barcode))
            if not riga:
                continue
            for campo in CAMPI_SCORE_OCR:
                chiave_raw = MAPPA_CAMPI_OCR_INVERSA.get(campo, campo)
                valore_raw_grezzo = raw.get(chiave_raw)
                if campo in CAMPI_DATA:
                    valore_raw_normalizzato = _parse_data_italiana(valore_raw_grezzo)
                elif campo in CAMPI_BOOLEANI:
                    valore_raw_normalizzato = _parse_booleano(valore_raw_grezzo)
                elif campo in CAMPI_PREZZO:
                    valore_raw_normalizzato = _parse_prezzo(valore_raw_grezzo)
                else:
                    valore_raw_normalizzato = valore_raw_grezzo or None

                valore_attuale = getattr(dati_ocr, campo)
                if _valori_uguali(valore_attuale, valore_raw_normalizzato):
                    continue

                col_excel = "etichetta_THC" if campo == "etichetta_thc" else campo
                col_idx = intestazione.get(col_excel)
                if not col_idx:
                    continue
                ws.cell(row=riga, column=col_idx).fill = sfondo_corretto
                evidenziate += 1
        if evidenziate:
            wb.save(destinazione)
            log.info(f"Evidenziati {evidenziate} campi corretti manualmente nell'Excel finale")
    except Exception:
        log.exception("Impossibile evidenziare i campi corretti manualmente nell'Excel finale (il file resta comunque salvato)")


def avvia_difformita_reale(lotto_id) -> None:
    db: Session = SessionLocal()
    elaborazione = None  # puo' non esistere ancora se si esce prima di crearla (vedi except sotto)
    try:
        lotto = db.query(LottoMensile).filter(LottoMensile.id == lotto_id).first()
        if lotto is None:
            return

        elaborazione = Elaborazione(
            lotto_id=lotto.id, fase=FaseElaborazione.difformita,
            stato=StatoElaborazione.in_corso,
            comando_docker="docker compose run --rm fase3-difformita",
            started_at=datetime.utcnow(),
        )
        db.add(elaborazione)
        lotto.stato = StatoLotto.analisi_difformita
        db.commit()

        _scrivi_farmacie_per_pipeline(db)
        n_corretti = _applica_correzioni_ocr_su_json(lotto)
        if n_corretti:
            log_elaborazione(db, elaborazione, f"Applicate {n_corretti} correzioni OCR manuali ai dati prima dell'analisi")
        log_elaborazione(db, elaborazione, "Avvio container fase3-difformita")
        _esegui_container("fase3-difformita", db, elaborazione)

        n_con_difformita, n_difformita_totali = 0, 0
        for json_path in OUTPUT_DIR.glob("*.json"):
            if json_path.name in FILE_JSON_DA_ESCLUDERE:
                continue
            dati = json.loads(json_path.read_text(encoding="utf-8-sig"))
            barcode = dati.get("barcode") or json_path.stem
            codici = dati.get("difformita_codici", [])
            descrizioni = dati.get("difformita_descrizioni", [])
            if not codici:
                continue

            presc = db.query(Prescrizione).filter(
                Prescrizione.lotto_id == lotto.id, Prescrizione.barcode == barcode
            ).first()
            if presc is None:
                continue

            # Evita duplicati se questa fase viene rilanciata sullo stesso lotto
            db.query(Difformita).filter(Difformita.prescrizione_id == presc.id).delete()

            for codice, descrizione in zip(codici, descrizioni):
                db.add(Difformita(
                    prescrizione_id=presc.id, codice=codice, descrizione=descrizione,
                    stato=StatoDifformita.rilevata,
                ))
                n_difformita_totali += 1
            n_con_difformita += 1

        db.commit()

        lotto.n_difformita_totali = n_difformita_totali
        lotto.n_prescrizioni_con_difformita = n_con_difformita
        lotto.updated_at = datetime.utcnow()

        elaborazione.stato = StatoElaborazione.completata
        elaborazione.finished_at = datetime.utcnow()
        elaborazione.exit_code = 0
        elaborazione.n_processati = n_con_difformita

        lotto.stato = StatoLotto.revisione_difformita
        log_elaborazione(db, elaborazione, f"Analisi completata: {n_difformita_totali} difformita su {n_con_difformita} prescrizioni")
        db.commit()

    except ElaborazioneAnnullata:
        _gestisci_annullamento(db, lotto_id, elaborazione)
    except Exception as exc:
        log.exception("Errore in avvia_difformita_reale")
        db.rollback()
        if elaborazione is not None:
            elaborazione.stato = StatoElaborazione.errore
            elaborazione.finished_at = datetime.utcnow()
        lotto = db.query(LottoMensile).filter(LottoMensile.id == lotto_id).first()
        if lotto:
            lotto.stato = StatoLotto.eccezione
            lotto.note = ((lotto.note or "") + f"\n[{datetime.utcnow():%d/%m %H:%M}] Difformita: {exc}").strip()
        db.commit()
    finally:
        db.close()


def _correggi_link_pdf_excel(destinazione: Path, lotto: LottoMensile) -> None:
    """
    phase4_excel.py (dentro Docker) scrive nella colonna LINK un
    hyperlink assoluto al PDF dentro il container (/dati/ricette/...):
    un percorso che non esiste piu' fuori da li' (./dati viene svuotato
    prima del lotto successivo) e che comunque non sarebbe portabile tra
    le macchine dei diversi operatori — ognuno ha il proprio OneDrive
    sincronizzato sotto un percorso utente diverso (C:\\Users\\mario\\...
    vs C:\\Users\\luigi\\...). Qui riscriviamo i link con percorsi
    RELATIVI alla cartella PRESCRIZIONI (sorella di OUTPUT sotto lo
    stesso lotto): OneDrive sincronizza la stessa struttura relativa per
    chiunque abbia accesso al sito, quindi un link relativo funziona per
    tutti indipendentemente dal proprio percorso assoluto locale.
    """
    if not lotto.sp_prescrizioni_path:
        return
    try:
        wb = openpyxl.load_workbook(destinazione)
        ws = wb.active
        intestazione = {}
        for col_idx in range(1, ws.max_column + 1):
            nome = ws.cell(row=1, column=col_idx).value
            if nome:
                intestazione[str(nome).strip().upper()] = col_idx
        col_link = intestazione.get("LINK")
        col_barcode = intestazione.get("BARCODE")
        if not col_link or not col_barcode:
            return

        cartella_prescrizioni = radice_sharepoint() / lotto.sp_prescrizioni_path
        relativo = os.path.relpath(cartella_prescrizioni, destinazione.parent).replace("\\", "/")

        corretti = 0
        for riga in range(2, ws.max_row + 1):
            barcode = ws.cell(row=riga, column=col_barcode).value
            if not barcode:
                continue
            nome_file = f"{barcode}.pdf"
            cella = ws.cell(row=riga, column=col_link)
            cella.value = nome_file
            cella.hyperlink = f"{relativo}/{nome_file}"
            cella.style = "Hyperlink"
            corretti += 1
        wb.save(destinazione)
        log.info(f"Corretti {corretti} link PDF nell'Excel finale ({relativo}/)")
    except Exception:
        log.exception("Impossibile correggere i link PDF nell'Excel finale (il file resta comunque salvato)")


def _evidenzia_difformita_escluse(destinazione: Path, db: Session, lotto: LottoMensile) -> None:
    """
    L'Excel finale viene scritto da phase4_excel.py leggendo i JSON
    prodotti in fase 3 (analisi difformita') — quei JSON non sanno nulla
    delle esclusioni che l'operatore fa DOPO, durante la revisione
    (POST /lotti/{id}/difformita/{id}/escludi, che tocca solo il
    database). Qui, dopo che l'Excel e' stato scritto, si riscrivono le
    celle delle difformita' che risultano escluse nel database: testo
    "{descrizione}-esclusa" ed evidenziate in rosso, cosi' chi legge il
    file vede subito quali difformita' l'operatore ha deciso di non
    considerare valide.

    Copre solo le esclusioni fatte PRIMA di "Completa" (il caso normale:
    la revisione difformita' avviene prima, nel flusso del wizard). Un'
    esclusione fatta su un lotto gia' completato non aggiorna un Excel
    gia' scritto — gap noto, da coprire in futuro con un modo per
    rigenerare l'output.
    """
    escluse = (
        db.query(Difformita)
        .join(Prescrizione)
        .filter(Prescrizione.lotto_id == lotto.id, Difformita.stato == StatoDifformita.esclusa)
        .all()
    )
    if not escluse:
        return
    try:
        wb = openpyxl.load_workbook(destinazione)
        ws = wb.active
        intestazione = {}
        for col_idx in range(1, ws.max_column + 1):
            nome = ws.cell(row=1, column=col_idx).value
            if nome:
                intestazione[nome] = col_idx
        col_barcode = intestazione.get("BARCODE")
        if not col_barcode:
            return

        riga_per_barcode = {}
        for riga in range(2, ws.max_row + 1):
            valore = ws.cell(row=riga, column=col_barcode).value
            if valore:
                riga_per_barcode[re.sub(r"[^0-9A-Za-z]", "", str(valore))] = riga

        font_esclusa = Font(color="FFCC0000", bold=True)
        sfondo_esclusa = PatternFill(fgColor="FFFCE4E4", fill_type="solid")

        evidenziate = 0
        for d in escluse:
            barcode_norm = re.sub(r"[^0-9A-Za-z]", "", (d.prescrizione.barcode or ""))
            riga = riga_per_barcode.get(barcode_norm)
            col_idx = intestazione.get(f"R{d.codice}")
            if not riga or not col_idx:
                continue  # barcode non presente in questo Excel, o codice senza colonna nel template (es. 17, 19M)
            cella = ws.cell(row=riga, column=col_idx)
            testo_base = str(cella.value or d.descrizione or "").strip()
            if not testo_base.endswith("-esclusa"):
                cella.value = f"{testo_base}-esclusa" if testo_base else "esclusa"
            cella.font = font_esclusa
            cella.fill = sfondo_esclusa
            evidenziate += 1
        wb.save(destinazione)
        log.info(f"Evidenziate {evidenziate} difformita' escluse nell'Excel finale")
    except Exception:
        log.exception("Impossibile evidenziare le difformita' escluse nell'Excel finale (il file resta comunque salvato)")


def _pubblica_cfa_su_sharepoint(db: Session, lotto: LottoMensile) -> int:
    """
    Al completamento del lotto, copia in una cartella "CFA" dentro la
    cartella del lotto (Macchina Locale/Archivio/{anno}/{mese}/{lotto}/CFA)
    tutte le ricette che hanno almeno una difformita' CONFERMATA — senza
    distinzione di farmacia (a differenza dello ZIP di export, che le
    raggruppa per farmacia): qui e' un unico raccoglitore piatto, pensato
    per chi deve rivedere solo le ricette con difformita' confermate di
    questo lotto, indipendentemente da quale farmacia le ha spedite.
    """
    if not lotto.sp_lavoro_path:
        return 0
    prescrizioni_con_confermate = (
        db.query(Prescrizione)
        .join(Difformita)
        .filter(Prescrizione.lotto_id == lotto.id, Difformita.stato == StatoDifformita.confermata)
        .distinct()
        .all()
    )
    if not prescrizioni_con_confermate:
        return 0
    cartella_cfa = radice_sharepoint() / lotto.sp_lavoro_path / "CFA"
    cartella_cfa.mkdir(parents=True, exist_ok=True)
    n = 0
    for presc in prescrizioni_con_confermate:
        percorso_pdf = percorso_pdf_prescrizione(presc)
        if not percorso_pdf:
            continue
        origine = Path(percorso_pdf)
        if not origine.is_file():
            continue
        shutil.copy2(origine, cartella_cfa / origine.name)
        n += 1
    return n


def scrivi_excel_finale_reale(lotto_id) -> bool:
    """
    Chiamata sincrona (non e' un BackgroundTask separato) dalla route
    POST /lotti/{id}/completa, dopo la revisione difformita. Esegue il
    container fase4-excel e copia il risultato nello storage permanente
    del lotto. Ritorna False (senza sollevare eccezioni) se qualcosa
    va storto, cosi' la route puo' decidere se bloccare il completamento.
    """
    db: Session = SessionLocal()
    elaborazione = None  # puo' non esistere ancora se si esce prima di crearla (vedi except sotto)
    try:
        lotto = db.query(LottoMensile).filter(LottoMensile.id == lotto_id).first()
        if lotto is None:
            return False

        elaborazione = Elaborazione(
            lotto_id=lotto.id, fase=FaseElaborazione.completa,
            stato=StatoElaborazione.in_corso,
            comando_docker="docker compose run --rm fase4-excel",
            started_at=datetime.utcnow(),
        )
        db.add(elaborazione)
        db.commit()

        n_corretti = _applica_correzioni_ocr_su_json(lotto)
        if n_corretti:
            log_elaborazione(db, elaborazione, f"Applicate {n_corretti} correzioni OCR manuali ai dati prima della scrittura Excel")
        log_elaborazione(db, elaborazione, "Avvio container fase4-excel")
        _esegui_container("fase4-excel", db, elaborazione)

        file_excel_finale = next(OUTPUT_DIR.glob("*.xlsx"), None)
        if file_excel_finale is None:
            raise RuntimeError("Il container fase4-excel non ha prodotto nessun file .xlsx in /dati/output")

        # sp_output_path e' ora una sottocartella del lotto stesso (non piu'
        # una cartella LAVORO/OUTPUT condivisa da tutti i lotti): senza
        # questo, ogni nuovo lotto sovrascriveva l'Excel del precedente,
        # perche' phase4_excel.py salva sempre con lo stesso nome di
        # template. Il nome file qui e' invece specifico del lotto.
        cartella_output_permanente = radice_sharepoint() / lotto.sp_output_path
        cartella_output_permanente.mkdir(parents=True, exist_ok=True)
        nome_file_output = f"{nome_file_sicuro(lotto.nome)}.xlsx"
        destinazione = cartella_output_permanente / nome_file_output
        shutil.copy2(file_excel_finale, destinazione)
        _correggi_link_pdf_excel(destinazione, lotto)
        _evidenzia_difformita_escluse(destinazione, db, lotto)
        _evidenzia_campi_corretti(destinazione, lotto)
        pubblica_file(lotto.id, destinazione)
        n_cfa = _pubblica_cfa_su_sharepoint(db, lotto)

        lotto.excel_output_filename = nome_file_output

        elaborazione.stato = StatoElaborazione.completata
        elaborazione.finished_at = datetime.utcnow()
        elaborazione.exit_code = 0
        db.commit()

        log_elaborazione(db, elaborazione, f"Excel finale scritto: {destinazione}")
        if n_cfa:
            log_elaborazione(db, elaborazione, f"{n_cfa} ricette con difformita' confermate pubblicate in CFA")
        return True

    except ElaborazioneAnnullata:
        _gestisci_annullamento(db, lotto_id, elaborazione)
        return False
    except Exception as exc:
        log.exception("Errore in scrivi_excel_finale_reale")
        db.rollback()
        if elaborazione is not None:
            elaborazione.stato = StatoElaborazione.errore
            elaborazione.finished_at = datetime.utcnow()
        lotto = db.query(LottoMensile).filter(LottoMensile.id == lotto_id).first()
        if lotto:
            # Come le altre 3 fasi: porta il lotto in eccezione invece di
            # lasciarlo a revisione_difformita senza nessun bottone
            # utilizzabile ("Riprova"/"Elimina" compaiono solo su
            # eccezione, il pannello "Annulla" solo su elaborazione
            # in_corso — nessuno dei due era piu' vero a questo punto).
            lotto.stato = StatoLotto.eccezione
            lotto.note = ((lotto.note or "") + f"\n[{datetime.utcnow():%d/%m %H:%M}] Scrittura Excel: {exc}").strip()
        db.commit()
        return False
    finally:
        db.close()
