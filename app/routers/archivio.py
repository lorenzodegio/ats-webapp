"""Router archivio: /archivio — storico prescrizioni ricercabile per barcode o nome lotto, raggruppato per lotto."""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import get_utente_corrente
from app.models import Prescrizione, LottoMensile, Utente, DatiOcr

router = APIRouter(tags=["archivio"])
templates = Jinja2Templates(directory="app/templates")

LIMITE_PRESCRIZIONI = 500


@router.get("/archivio", response_class=HTMLResponse)
def archivio(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    query = (
        db.query(Prescrizione)
        .join(LottoMensile, Prescrizione.lotto_id == LottoMensile.id)
        .outerjoin(DatiOcr, DatiOcr.prescrizione_id == Prescrizione.id)
        .options(joinedload(Prescrizione.lotto), joinedload(Prescrizione.dati_ocr))
    )
    if q:
        query = query.filter(or_(
            Prescrizione.barcode.ilike(f"%{q}%"),
            LottoMensile.nome.ilike(f"%{q}%"),
            DatiOcr.nome_farmacia.ilike(f"%{q}%"),
        ))
    prescrizioni = (
        query.order_by(LottoMensile.created_at.desc(), Prescrizione.barcode.asc())
        .limit(LIMITE_PRESCRIZIONI)
        .all()
    )

    # Raggruppo per lotto mantenendo l'ordine di arrivo (lotto piu'
    # recente per primo, dato l'order_by sopra). Non uso itertools.groupby
    # per non dipendere dal fatto che le righe di uno stesso lotto
    # restino contigue se in futuro cambia l'ordinamento della query.
    gruppi = []
    indice_lotto = {}
    for p in prescrizioni:
        if p.lotto_id not in indice_lotto:
            indice_lotto[p.lotto_id] = {"lotto": p.lotto, "prescrizioni": []}
            gruppi.append(indice_lotto[p.lotto_id])
        indice_lotto[p.lotto_id]["prescrizioni"].append(p)

    return templates.TemplateResponse(
        "archivio.html",
        {
            "request": request,
            "utente": utente,
            "voce_attiva": "archivio",
            "gruppi": gruppi,
            "numero_prescrizioni": len(prescrizioni),
            "q": q,
        },
    )
