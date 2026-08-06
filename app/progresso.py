"""
Helper condiviso per rappresentare l'avanzamento di un Lotto Mensile
attraverso il suo ciclo di vita (Sezione "Flusso caricamento -> elaborazione
-> archiviazione" dello schema). Usato da dashboard e dettaglio lotto.
"""
import re

from app.models import StatoLotto

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
