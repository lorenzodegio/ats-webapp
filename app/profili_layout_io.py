"""Serializza i profili di layout etichetta per la pipeline OCR (file JSON).

Stesso principio di farmacie_io.py: la webapp scrive il JSON in ./dati,
montato come /dati nel container Docker, dove profilo_layout_etichetta.py
lo legge (vedi _carica_profili_da_json() in quel file).
"""
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import ProfiloLayoutEtichetta, CampoProfiloLayout


def _rettangolo_payload(x1, y1, x2, y2):
    if x1 is None or y1 is None or x2 is None or y2 is None:
        return None
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}


def profili_payload(db: Session):
    profili = db.query(ProfiloLayoutEtichetta).all()
    elenco = []
    for p in profili:
        if not p.farmacia or not p.farmacia.attiva:
            continue
        elenco.append({
            "nome_farmacia": p.farmacia.nome,
            "parole_guida_presenza": p.parole_guida_presenza,
            "rettangolo_etichetta": _rettangolo_payload(
                p.rettangolo_etichetta_x1, p.rettangolo_etichetta_y1,
                p.rettangolo_etichetta_x2, p.rettangolo_etichetta_y2,
            ),
            "campi": [
                {
                    "nome_campo": c.nome_campo,
                    "parola_guida": c.parola_guida,
                    "rettangolo_fallback": _rettangolo_payload(
                        c.rettangolo_x1, c.rettangolo_y1, c.rettangolo_x2, c.rettangolo_y2,
                    ),
                }
                for c in sorted(p.campi, key=lambda c: c.ordine)
            ],
        })
    return {"profili": elenco}


def scrivi_json_pipeline(db: Session, destinazione: Path) -> Path:
    import json

    destinazione.parent.mkdir(parents=True, exist_ok=True)
    payload = profili_payload(db)
    destinazione.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return destinazione
