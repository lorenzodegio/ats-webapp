"""
Router dashboard: / — cruscotto iniziale.

1. "In corso ora": lotti non ancora completati/archiviati, con fase
   attiva e stato aggiornati via polling.
2. "Panoramica generale": KPI e difformita per codice, filtrabili per
   anno, mese e stato.
"""
from collections import Counter
from typing import Optional

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import get_utente_corrente
from app.models import LottoMensile, StatoLotto, Prescrizione, Difformita, Utente
from app.progresso import percentuale_avanzamento, etichetta_stato

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")

STATI_CONCLUSI = [StatoLotto.completato, StatoLotto.archiviato]


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    anno: Optional[int] = None,
    mese: Optional[int] = None,
    stato: Optional[str] = None,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    # ---------- Pannello "in corso ora" ----------
    lotti_attivi = (
        db.query(LottoMensile)
        .options(joinedload(LottoMensile.elaborazioni))
        .filter(LottoMensile.stato.notin_(STATI_CONCLUSI))
        .order_by(LottoMensile.updated_at.desc())
        .all()
    )
    lotti_attivi_vista = [
        {
            "lotto": l,
            "percentuale": percentuale_avanzamento(l.stato),
            "etichetta_stato": etichetta_stato(l.stato),
            "elaborazione_attiva": l.elaborazione_attiva,
        }
        for l in lotti_attivi
    ]

    # ---------- Panoramica generale (filtrabile) ----------
    query_lotti = db.query(LottoMensile)
    if anno:
        query_lotti = query_lotti.filter(LottoMensile.anno == anno)
    if mese:
        query_lotti = query_lotti.filter(LottoMensile.mese == mese)
    if stato and stato in StatoLotto.__members__:
        query_lotti = query_lotti.filter(LottoMensile.stato == StatoLotto(stato))

    lotti_filtrati = query_lotti.all()
    lotti_filtrati_ids = [l.id for l in lotti_filtrati]

    lotti_totali = len(lotti_filtrati_ids)
    prescrizioni_totali = sum(l.n_prescrizioni_totali or 0 for l in lotti_filtrati)
    difformita_totali = sum(l.n_difformita_totali or 0 for l in lotti_filtrati)

    difformita_query = (
        db.query(Difformita)
        .join(Prescrizione)
        .filter(Prescrizione.lotto_id.in_(lotti_filtrati_ids or [None]))
    )
    difformita_per_codice = Counter(codice for (codice,) in difformita_query.with_entities(Difformita.codice).all())
    difformita_per_codice = dict(
        sorted(difformita_per_codice.items(), key=lambda kv: kv[1], reverse=True)[:6]
    )
    max_difformita = max(difformita_per_codice.values()) if difformita_per_codice else 1

    anni_disponibili = sorted({row[0] for row in db.query(LottoMensile.anno).distinct().all()}, reverse=True)

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "utente": utente,
            "voce_attiva": "dashboard",
            "lotti_attivi_vista": lotti_attivi_vista,
            "lotti_totali": lotti_totali,
            "prescrizioni_totali": prescrizioni_totali,
            "difformita_totali": difformita_totali,
            "difformita_per_codice": difformita_per_codice,
            "max_difformita": max_difformita,
            "filtro_anno": anno or "",
            "filtro_mese": mese or "",
            "filtro_stato": stato or "",
            "stati_disponibili": list(StatoLotto),
            "anni_disponibili": anni_disponibili,
            "MESI_IT": ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
                        "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"],
        },
    )
