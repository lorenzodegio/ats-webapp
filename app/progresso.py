"""
Helper condiviso per rappresentare l'avanzamento di un Lotto Mensile
attraverso il suo ciclo di vita (Sezione "Flusso caricamento -> elaborazione
-> archiviazione" dello schema). Usato da dashboard e dettaglio lotto.
"""
import re
from datetime import datetime

from app.models import FaseElaborazione, StatoLotto

_PATTERN_PROGRESSO = re.compile(r"\[(\d+)/(\d+)\]")


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


def percentuale_avanzamento(stato: StatoLotto) -> int:
    if stato == StatoLotto.eccezione:
        return 100
    try:
        idx = ORDINE_STATI.index(stato)
    except ValueError:
        idx = 0
    return round(idx / (len(ORDINE_STATI) - 1) * 100)


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
