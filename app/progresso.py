"""
Helper condiviso per rappresentare l'avanzamento di un Job attraverso le
4 fasi della pipeline. Usato da dashboard (pannello "in corso ora") e
dal dettaglio elaborazione.
"""
from app.models import StatoJob

ORDINE_FASI = [
    StatoJob.in_coda,
    StatoJob.fase1_preprocessing,
    StatoJob.fase2_ocr,
    StatoJob.fase3_excel,
    StatoJob.fase4_difformita,
    StatoJob.completato,
]

ETICHETTE_FASE = {
    StatoJob.in_coda: "In coda",
    StatoJob.fase1_preprocessing: "Fase 1 — Preprocessing",
    StatoJob.fase2_ocr: "Fase 2 — OCR",
    StatoJob.fase3_excel: "Fase 3 — Scrittura Excel",
    StatoJob.fase4_difformita: "Fase 4 — Difformità",
    StatoJob.completato: "Completata",
    StatoJob.errore: "Errore",
}


def percentuale_avanzamento(stato: StatoJob) -> int:
    """Percentuale 0-100 in base alla posizione nella sequenza delle fasi."""
    if stato == StatoJob.errore:
        return 100
    try:
        idx = ORDINE_FASI.index(stato)
    except ValueError:
        idx = 0
    return round(idx / (len(ORDINE_FASI) - 1) * 100)


def etichetta_fase(stato: StatoJob) -> str:
    return ETICHETTE_FASE.get(stato, stato.value if hasattr(stato, "value") else str(stato))
