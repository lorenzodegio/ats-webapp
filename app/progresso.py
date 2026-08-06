"""
Helper condiviso per rappresentare l'avanzamento di un Lotto Mensile
attraverso il suo ciclo di vita (Sezione "Flusso caricamento -> elaborazione
-> archiviazione" dello schema). Usato da dashboard e dettaglio lotto.
"""
from app.models import StatoLotto

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
