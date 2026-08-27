"""
Pipeline finta — NON PIU' USATA a runtime.

Le rotte lotti avviano solo app.real_pipeline (Docker + Ollama).
Queste funzioni restano come guardrail: se qualcosa le richiama, falliscono
in modo esplicito invece di simulare OCR o eccezioni casuali.
"""


def avvia_preprocessing_fake(lotto_id) -> None:
    raise RuntimeError("Pipeline finta disattivata: e' in uso solo Docker/Ollama (real_pipeline).")


def avvia_ocr_fake(lotto_id) -> None:
    raise RuntimeError("Pipeline finta disattivata: e' in uso solo Docker/Ollama (real_pipeline).")


def avvia_difformita_fake(lotto_id) -> None:
    raise RuntimeError("Pipeline finta disattivata: e' in uso solo Docker/Ollama (real_pipeline).")
