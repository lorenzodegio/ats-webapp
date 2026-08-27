"""Serializza l'anagrafica farmacie per la pipeline OCR (file JSON)."""
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Farmacia


def elenco_attive_payload(db: Session):
    farmacie = (
        db.query(Farmacia)
        .filter(Farmacia.attiva.is_(True))
        .order_by(Farmacia.nome.asc())
        .all()
    )
    return {
        "farmacie": [
            {
                "codice": f.codice,
                "nome": f.nome,
                "codice_regionale": f.codice_regionale or "",
                "comune": f.comune or "",
                "provincia": f.provincia or "",
            }
            for f in farmacie
        ]
    }


def scrivi_json_pipeline(db: Session, destinazione: Path) -> Path:
    import json

    destinazione.parent.mkdir(parents=True, exist_ok=True)
    payload = elenco_attive_payload(db)
    destinazione.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return destinazione
