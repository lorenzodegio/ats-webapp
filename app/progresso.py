"""
Helper condiviso per rappresentare l'avanzamento di un Lotto Mensile
attraverso il suo ciclo di vita (Sezione "Flusso caricamento -> elaborazione
-> archiviazione" dello schema). Usato da dashboard e dettaglio lotto.
"""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.models import FaseElaborazione, StatoLotto

_PATTERN_PROGRESSO = re.compile(r"\[(\d+)/(\d+)\]")

FUSO_ROMA = ZoneInfo("Europe/Rome")


def formatta_ora_locale(dt, formato: str = "%d/%m/%Y %H:%M"):
    """
    Converte un datetime naive (tutti i timestamp del modello sono salvati
    in UTC via datetime.utcnow() come default di colonna) in orario locale
    Europe/Rome per la UI. Filtro Jinja "ora_locale" registrato sui
    Jinja2Templates che ne hanno bisogno (vedi lotti.py, archivio.py).
    """
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(FUSO_ROMA).strftime(formato)

# Il logger della pipeline (logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s"))
# scrive gia' data/livello dentro il testo del messaggio: le togliamo qui perche'
# nel pannello "Log tecnico" Ora/Livello sono gia' colonne separate.
_PATTERN_PREFISSO_LOG = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \[\w+\]\s*")

# Whitelist di righe rilevanti per un operatore non tecnico (il log completo
# di Docker puo' avere migliaia di righe): cambio fase, ricetta in
# lavorazione, fine fase. Gli errori passano a prescindere (vedi
# _riga_log_rilevante), i warning restano sempre esclusi.
_PATTERN_FASE = re.compile(r"^(\[\d+/4\]|\[Fase \d+\])")

_PATTERN_LOG_RILEVANTI = [
    _PATTERN_FASE,                                # avvio/fine fase: 1=preprocessing 2=ocr 3=difformita 4=excel
    re.compile(r"^\[\d+/\d+\]\s+\S+\.pdf\s*$"),   # ricetta i/N in elaborazione
    re.compile(r"^Elaborando:\s"),                # ricetta in elaborazione (per file)
    re.compile(r"pipeline completata", re.IGNORECASE),
    # Azioni dell'operatore sull'elaborazione (pausa/riprendi/annulla/riprova
    # in lotti.py): non vengono dal log grezzo di Docker, le scrive la
    # webapp stessa con log_elaborazione(), ma vanno mostrate comunque nel
    # pannello — l'operatore deve vedere quando ha agito, non solo cosa
    # ha fatto la pipeline.
    re.compile(r"^Esecuzione (messa in pausa|ripresa|annullata|riavviata)\b"),
]


def _pulisci_messaggio_log(messaggio: str) -> str:
    return _PATTERN_PREFISSO_LOG.sub("", messaggio, count=1).strip()


def _tronca_dettaglio_fase(messaggio_pulito: str) -> str:
    """Le righe di avvio fase includono dopo un em-dash dettagli tecnici
    (modello, GPU, nome file) utili nel log Docker ma non nel pannello
    operatore, che deve mostrare solo "[N/4] Nome fase"."""
    if _PATTERN_FASE.match(messaggio_pulito) and " — " in messaggio_pulito:
        return messaggio_pulito.split(" — ", 1)[0].strip()
    return messaggio_pulito


def _riga_log_rilevante(livello: str, messaggio_pulito: str) -> bool:
    if livello == "error":
        return True
    if livello == "warning":
        return False
    return any(p.search(messaggio_pulito) for p in _PATTERN_LOG_RILEVANTI)


def formatta_log_righe(righe) -> list:
    """
    Converte le righe di LogElaborazione (ordinate cronologicamente, gia'
    limitate a poche centinaia) nel formato per il pannello "Log tecnico":
    - ora in fuso Europe/Rome (il timestamp e' salvato in UTC via
      datetime.utcnow() in real_pipeline.py, va convertito per la UI);
    - solo le righe rilevanti (vedi _riga_log_rilevante);
    - messaggio ripulito dal prefisso data/livello gia' duplicato altrove.
    """
    risultato = []
    for riga in righe:
        livello = riga.livello.value if hasattr(riga.livello, "value") else str(riga.livello)
        messaggio_pulito = _pulisci_messaggio_log(riga.messaggio)
        if not _riga_log_rilevante(livello, messaggio_pulito):
            continue
        messaggio_pulito = _tronca_dettaglio_fase(messaggio_pulito)
        ora_utc = riga.timestamp.replace(tzinfo=timezone.utc)
        risultato.append({
            "ora": ora_utc.astimezone(FUSO_ROMA).strftime("%H:%M:%S"),
            "livello": livello,
            "messaggio": messaggio_pulito,
        })
    return risultato


def estrai_progresso_da_log(log_lines) -> dict:
    """
    Cerca nell'ultima riga di log (piu' recente prima) un pattern
    "[i/N]" (formato gia' prodotto sia da pipeline.py nel backend reale,
    sia da fake_pipeline.py nel backend finto — stesso formato per i due
    backend, cosi' il frontend non deve saperne la differenza).
    Ritorna {"attuale": i, "totale": N} oppure None se non trovato.
    """
    for riga in reversed(log_lines):
        messaggio = riga.messaggio if hasattr(riga, "messaggio") else riga.get("messaggio", "")
        m = _PATTERN_PROGRESSO.search(messaggio)
        if m:
            return {"attuale": int(m.group(1)), "totale": int(m.group(2))}
    return None


ORDINE_STATI = [
    StatoLotto.bozza,
    StatoLotto.caricamento,
    StatoLotto.preprocessing,
    StatoLotto.revisione_barcode,
    StatoLotto.elaborazione_ocr,
    StatoLotto.revisione_qualita,
    StatoLotto.analisi_difformita,
    StatoLotto.revisione_difformita,
    StatoLotto.completato,
    StatoLotto.archiviato,
]

ETICHETTE_STATO = {
    StatoLotto.bozza: "Bozza",
    StatoLotto.caricamento: "Caricamento file",
    StatoLotto.preprocessing: "Preprocessing (fase 1)",
    StatoLotto.revisione_barcode: "Revisione barcode",
    StatoLotto.elaborazione_ocr: "Elaborazione OCR (fase 2)",
    StatoLotto.revisione_qualita: "Revisione qualita",
    StatoLotto.analisi_difformita: "Analisi difformita (fase 4)",
    StatoLotto.revisione_difformita: "Revisione difformita",
    StatoLotto.completato: "Completato",
    StatoLotto.archiviato: "Archiviato",
    StatoLotto.eccezione: "Eccezione",
}

# Stati in cui e' richiesta un'azione dell'operatore prima di poter proseguire
STATI_ATTESA_OPERATORE = {
    StatoLotto.revisione_barcode,
    StatoLotto.revisione_qualita,
    StatoLotto.revisione_difformita,
    StatoLotto.completato,
}

# Stati in cui e' in corso un'elaborazione automatica (background task)
STATI_IN_ELABORAZIONE_AUTOMATICA = {
    StatoLotto.preprocessing,
    StatoLotto.elaborazione_ocr,
    StatoLotto.analisi_difformita,
}


FASI_WIZARD_LOTTO = [
    (1, "Dati lotto"),
    (2, "Caricamento"),
    (3, "Preprocessing"),
    (4, "OCR"),
    (5, "Difformità"),
    (6, "Cartelle farmacie"),
]


_FASE_ELAB_TO_WIZARD = {
    FaseElaborazione.preprocessing: 3,
    FaseElaborazione.vllm: 4,
    FaseElaborazione.difformita: 5,
    FaseElaborazione.completa: 6,
}


def _indice_da_elaborazioni(lotto) -> int:
    elabs = list(getattr(lotto, "elaborazioni", None) or [])
    if not elabs:
        return 0
    ultima = max(
        elabs,
        key=lambda e: (e.started_at or e.finished_at or datetime.min, str(getattr(e, "id", ""))),
    )
    return _FASE_ELAB_TO_WIZARD.get(ultima.fase, 0)


def indice_fase_wizard(stato: StatoLotto, lotto=None) -> int:
    """Step visivo 1–6 del lotto in elaborazione (dopo la creazione)."""
    if stato == StatoLotto.eccezione:
        idx = _indice_da_elaborazioni(lotto)
        if idx:
            return idx
        note = (getattr(lotto, "note", None) or "").lower()
        if "ocr" in note or "vllm" in note:
            return 4
        if "difform" in note:
            return 5
        if "excel" in note or "output" in note or "cartell" in note:
            return 6
        return 3
    if stato in (StatoLotto.bozza, StatoLotto.caricamento):
        return 2
    if stato in (StatoLotto.preprocessing, StatoLotto.revisione_barcode):
        return 3
    if stato in (StatoLotto.elaborazione_ocr, StatoLotto.revisione_qualita):
        return 4
    if stato in (StatoLotto.analisi_difformita, StatoLotto.revisione_difformita):
        return 5
    if stato in (StatoLotto.completato, StatoLotto.archiviato):
        return 6
    return 3


def _percentuale_proporzionale(inizio: int, fine: int, progresso_item) -> int:
    """Interpola tra 'inizio' e 'fine' in base a progresso_item
    ({"attuale": i, "totale": N}, da estrai_progresso_da_log) — se non
    ancora disponibile (fase appena avviata, nessuna riga "[i/N]" ancora
    nel log), resta fermo sul valore di inizio dell'intervallo."""
    if not progresso_item or not progresso_item.get("totale"):
        return inizio
    attuale = progresso_item.get("attuale", 0) or 0
    totale = progresso_item["totale"]
    frazione = max(0.0, min(1.0, attuale / totale))
    return round(inizio + frazione * (fine - inizio))


def percentuale_avanzamento(stato: StatoLotto, progresso_item=None) -> int:
    """
    Percentuale mostrata nella barra di avanzamento — a tratti fissi per
    le fasi senza un contatore per-ricetta significativo (preprocessing),
    proporzionale ricetta per ricetta per OCR e analisi difformita' (le
    due fasi piu' lunghe, dove un avanzamento granulare aiuta davvero
    l'operatore a capire quanto manca), tramite progresso_item =
    {"attuale": i, "totale": N} letto dal log via estrai_progresso_da_log.

    Suddivisione:
        0%          prima del preprocessing (bozza/caricamento)
        5%          preprocessing (fisso, in corso o appena concluso)
        5% -> 80%   elaborazione OCR (proporzionale)
        80%         OCR concluso, in attesa dell'avvio analisi difformita'
        80% -> 95%  analisi difformita' (proporzionale)
        95%         difformita' concluse, in attesa di "Completa lotto"
        100%        completato/archiviato
    """
    if stato == StatoLotto.eccezione:
        return 100
    if stato in (StatoLotto.bozza, StatoLotto.caricamento):
        return 0
    if stato in (StatoLotto.preprocessing, StatoLotto.revisione_barcode):
        return 5
    if stato == StatoLotto.elaborazione_ocr:
        return _percentuale_proporzionale(5, 80, progresso_item)
    if stato == StatoLotto.revisione_qualita:
        return 80
    if stato == StatoLotto.analisi_difformita:
        return _percentuale_proporzionale(80, 95, progresso_item)
    if stato == StatoLotto.revisione_difformita:
        return 95
    if stato in (StatoLotto.completato, StatoLotto.archiviato):
        return 100
    return 0


def etichetta_stato(stato: StatoLotto) -> str:
    return ETICHETTE_STATO.get(stato, stato.value if hasattr(stato, "value") else str(stato))


MESSAGGI_FASE_OPERATORE = {
    "preprocessing": "Sto preparando i file delle prescrizioni.",
    "vllm": "Sto leggendo automaticamente le prescrizioni.",
    "difformita": "Sto analizzando le difformità.",
    "completa": "Sto preparando i file di output.",
}


def messaggio_fase_operatore(fase, in_pausa=False) -> str:
    if in_pausa:
        return "Elaborazione in pausa. Puoi riprendere quando sei pronto."
    if fase is None:
        return "Elaborazione in corso."
    chiave = fase.value if hasattr(fase, "value") else str(fase)
    return MESSAGGI_FASE_OPERATORE.get(chiave, "Elaborazione in corso.")
