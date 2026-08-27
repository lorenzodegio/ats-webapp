"""Helper per leggere valori dalla tabella Configurazione."""
from sqlalchemy.orm import Session

from app.models import Configurazione


def leggi_configurazione(db: Session, chiave: str, default: str = "") -> str:
    riga = db.query(Configurazione).filter(Configurazione.chiave == chiave).first()
    return riga.valore if riga else default


def backend_pipeline_e_reale(db: Session) -> bool:
    """La webapp usa solo la pipeline Docker/Ollama reale."""
    return True


def assicura_pipeline_reale(db: Session) -> None:
    """Allinea Configurazione.pipeline_backend a 'reale' (DB vecchi avevano 'finto')."""
    riga = db.query(Configurazione).filter(Configurazione.chiave == "pipeline_backend").first()
    if riga is None:
        db.add(Configurazione(
            chiave="pipeline_backend",
            valore="reale",
            descrizione="Pipeline OCR Docker reale (Ollama/Qwen)",
        ))
        db.commit()
        return
    if riga.valore != "reale":
        riga.valore = "reale"
        riga.descrizione = "Pipeline OCR Docker reale (Ollama/Qwen)"
        db.commit()
