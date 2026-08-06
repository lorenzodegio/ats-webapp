"""Helper per leggere valori dalla tabella Configurazione."""
from sqlalchemy.orm import Session

from app.models import Configurazione


def leggi_configurazione(db: Session, chiave: str, default: str = "") -> str:
    riga = db.query(Configurazione).filter(Configurazione.chiave == chiave).first()
    return riga.valore if riga else default


def backend_pipeline_e_reale(db: Session) -> bool:
    """
    True se Configurazione.pipeline_backend == 'reale' (container Docker
    veri), False (default) se 'finto' (simulazione, nessun Docker richiesto).
    """
    return leggi_configurazione(db, "pipeline_backend", "finto") == "reale"
