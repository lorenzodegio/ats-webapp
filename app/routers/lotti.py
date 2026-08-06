"""
Router lotti mensili: /lotti/nuovo /lotti /lotti/{id} + azioni del ciclo
di vita (revisione barcode, avvio fasi successive, gestione difformita,
completamento, archiviazione).
"""
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import get_utente_corrente
from app.models import (
    LottoMensile, StatoLotto, CaricamentoFile, TipoCaricamento, StatoCaricamento,
    Prescrizione, StatoBarcode, Difformita, StatoDifformita, Elaborazione,
    StatoElaborazione, Utente,
)
from app.fake_pipeline import avvia_preprocessing_fake, avvia_ocr_fake, avvia_difformita_fake, FAKE_SP_ROOT
from app.progresso import percentuale_avanzamento, etichetta_stato, ETICHETTE_STATO

router = APIRouter(tags=["lotti"])
templates = Jinja2Templates(directory="app/templates")

UPLOAD_DIR_TEMP = "uploads"
ESTENSIONI_PDF_VALIDE = {".pdf"}
ESTENSIONI_EXCEL_VALIDE = {".xlsx", ".xls"}
NUMERO_LOTTI_RECENTI = 10

MESI_IT = ["", "GENNAIO", "FEBBRAIO", "MARZO", "APRILE", "MAGGIO", "GIUGNO",
           "LUGLIO", "AGOSTO", "SETTEMBRE", "OTTOBRE", "NOVEMBRE", "DICEMBRE"]


def _nome_cartella(mese: int, anno: int) -> str:
    return f"{MESI_IT[mese]}_{anno}"


# ============================================================
# Creazione nuovo lotto
# ============================================================

@router.get("/lotti/nuovo", response_class=HTMLResponse)
def form_nuovo_lotto(request: Request, utente: Utente = Depends(get_utente_corrente)):
    oggi = datetime.utcnow()
    return templates.TemplateResponse(
        "nuovo_lotto.html",
        {"request": request, "utente": utente, "voce_attiva": "nuova",
         "mese_corrente": oggi.month, "anno_corrente": oggi.year},
    )


@router.post("/lotti/nuovo")
def crea_lotto(
    request: Request,
    background_tasks: BackgroundTasks,
    nome: str = Form(""),
    mese: int = Form(...),
    anno: int = Form(...),
    file_pdf: UploadFile = File(...),
    file_excel: UploadFile = File(None),
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    nome = nome.strip()
    if not nome:
        return RedirectResponse(url="/lotti/nuovo?errore=Il+nome+del+lotto+e%27+obbligatorio", status_code=302)

    _, ext_pdf = os.path.splitext(file_pdf.filename or "")
    if ext_pdf.lower() not in ESTENSIONI_PDF_VALIDE:
        return RedirectResponse(url="/lotti/nuovo?errore=Il+file+prescrizioni+deve+essere+un+PDF", status_code=302)

    lotto_esistente = db.query(LottoMensile).filter(LottoMensile.mese == mese, LottoMensile.anno == anno).first()
    if lotto_esistente:
        return RedirectResponse(
            url=f"/lotti/nuovo?errore=Esiste+gia%27+un+lotto+per+{MESI_IT[mese]}+{anno}", status_code=302
        )

    cartella = _nome_cartella(mese, anno)
    lotto = LottoMensile(
        mese=mese, anno=anno, nome=nome,
        stato=StatoLotto.caricamento,
        operatore_id=utente.id,
        sp_lavoro_path=f"LAVORO/MESE DI LAVORAZIONE/{cartella}",
        sp_prescrizioni_path=f"LAVORO/MESE DI LAVORAZIONE/{cartella}/PRESCRIZIONI",
        sp_output_path="LAVORO/OUTPUT",
        sp_archivio_path=f"ARCHIVIO/ELABORAZIONI RECENTI/{cartella}",
    )
    db.add(lotto)
    db.commit()
    db.refresh(lotto)

    # Caricamento PDF combinato: salva per davvero nella cartella finta che
    # simula SharePoint, e traccia il caricamento in CaricamentoFile.
    os.makedirs(UPLOAD_DIR_TEMP, exist_ok=True)
    contenuto_pdf = file_pdf.file.read()
    percorso_relativo_pdf = f"{lotto.sp_prescrizioni_path}/{file_pdf.filename}"
    percorso_assoluto_pdf = os.path.join(FAKE_SP_ROOT, percorso_relativo_pdf)
    os.makedirs(os.path.dirname(percorso_assoluto_pdf), exist_ok=True)
    with open(percorso_assoluto_pdf, "wb") as f:
        f.write(contenuto_pdf)

    db.add(CaricamentoFile(
        lotto_id=lotto.id, tipo=TipoCaricamento.pdf_combined,
        nome_file_locale=file_pdf.filename, nome_file_sp=file_pdf.filename,
        percorso_sp=percorso_relativo_pdf, dimensione_bytes=len(contenuto_pdf),
        stato=StatoCaricamento.completato, caricato_da_id=utente.id,
        started_at=datetime.utcnow(), completed_at=datetime.utcnow(),
    ))

    if file_excel is not None and file_excel.filename:
        _, ext_excel = os.path.splitext(file_excel.filename)
        if ext_excel.lower() in ESTENSIONI_EXCEL_VALIDE:
            contenuto_excel = file_excel.file.read()
            percorso_relativo_excel = f"{lotto.sp_lavoro_path}/{file_excel.filename}"
            percorso_assoluto_excel = os.path.join(FAKE_SP_ROOT, percorso_relativo_excel)
            os.makedirs(os.path.dirname(percorso_assoluto_excel), exist_ok=True)
            with open(percorso_assoluto_excel, "wb") as f:
                f.write(contenuto_excel)
            lotto.excel_input_filename = file_excel.filename
            db.add(CaricamentoFile(
                lotto_id=lotto.id, tipo=TipoCaricamento.excel_regione,
                nome_file_locale=file_excel.filename, nome_file_sp=file_excel.filename,
                percorso_sp=percorso_relativo_excel, dimensione_bytes=len(contenuto_excel),
                stato=StatoCaricamento.completato, caricato_da_id=utente.id,
                started_at=datetime.utcnow(), completed_at=datetime.utcnow(),
            ))

    db.commit()

    # "attesa sync OneDrive" simulata: passiamo subito a preprocessing
    background_tasks.add_task(avvia_preprocessing_fake, lotto.id)

    return RedirectResponse(url=f"/lotti/{lotto.id}", status_code=302)


# ============================================================
# Lista e dettaglio
# ============================================================

@router.get("/lotti", response_class=HTMLResponse)
def lista_lotti(request: Request, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente)):
    lotti = (
        db.query(LottoMensile)
        .order_by(LottoMensile.created_at.desc())
        .limit(NUMERO_LOTTI_RECENTI)
        .all()
    )
    return templates.TemplateResponse(
        "lotti.html",
        {"request": request, "utente": utente, "voce_attiva": "elaborazioni",
         "lotti": lotti, "numero_recenti": NUMERO_LOTTI_RECENTI},
    )


@router.get("/lotti/{lotto_id}", response_class=HTMLResponse)
def dettaglio_lotto(
    request: Request, lotto_id: str,
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = (
        db.query(LottoMensile)
        .options(joinedload(LottoMensile.prescrizioni).joinedload(Prescrizione.dati_ocr))
        .options(joinedload(LottoMensile.prescrizioni).joinedload(Prescrizione.difformita))
        .options(joinedload(LottoMensile.elaborazioni))
        .filter(LottoMensile.id == uuid.UUID(lotto_id))
        .first()
    )
    if lotto is None:
        return RedirectResponse(url="/lotti", status_code=302)

    prescrizioni_undefined = [p for p in lotto.prescrizioni if p.stato_barcode == StatoBarcode.undefined]
    difformita_lotto = [d for p in lotto.prescrizioni for d in p.difformita]
    difformita_da_gestire = [d for d in difformita_lotto if d.stato == StatoDifformita.rilevata]

    elaborazione_attiva = lotto.elaborazione_attiva
    log_recenti = []
    if elaborazione_attiva:
        log_recenti = sorted(elaborazione_attiva.log, key=lambda l: l.timestamp)[-20:]

    return templates.TemplateResponse(
        "lotto_detail.html",
        {
            "request": request, "utente": utente, "voce_attiva": "elaborazioni",
            "lotto": lotto,
            "percentuale": percentuale_avanzamento(lotto.stato),
            "etichetta_stato_corrente": etichetta_stato(lotto.stato),
            "prescrizioni_undefined": prescrizioni_undefined,
            "difformita_lotto": difformita_lotto,
            "difformita_da_gestire": difformita_da_gestire,
            "elaborazione_attiva": elaborazione_attiva,
            "log_recenti": log_recenti,
        },
    )


# ============================================================
# Azioni del ciclo di vita
# ============================================================

@router.post("/lotti/{lotto_id}/barcode/{prescrizione_id}")
def correggi_barcode(
    lotto_id: str, prescrizione_id: str,
    nuovo_barcode: str = Form(...),
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    presc = db.query(Prescrizione).filter(Prescrizione.id == uuid.UUID(prescrizione_id)).first()
    if presc is None:
        return RedirectResponse(url=f"/lotti/{lotto_id}", status_code=302)

    era_undefined = presc.stato_barcode == StatoBarcode.undefined
    presc.barcode = nuovo_barcode.strip()
    presc.stato_barcode = StatoBarcode.corretto_manuale
    presc.barcode_corretto_da_id = utente.id
    presc.barcode_corretto_at = datetime.utcnow()

    if era_undefined:
        lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
        if lotto:
            lotto.n_barcode_undefined = max(0, (lotto.n_barcode_undefined or 0) - 1)
            lotto.n_barcode_letti = (lotto.n_barcode_letti or 0) + 1
    db.commit()
    return RedirectResponse(url=f"/lotti/{lotto_id}#revisione-barcode", status_code=302)


@router.post("/lotti/{lotto_id}/avvia-ocr")
def avvia_ocr(
    lotto_id: str, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto and lotto.stato == StatoLotto.revisione_barcode and (lotto.n_barcode_undefined or 0) == 0:
        background_tasks.add_task(avvia_ocr_fake, lotto.id)
    return RedirectResponse(url=f"/lotti/{lotto_id}", status_code=302)


@router.post("/lotti/{lotto_id}/avvia-difformita")
def avvia_difformita(
    lotto_id: str, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto and lotto.stato == StatoLotto.revisione_qualita:
        background_tasks.add_task(avvia_difformita_fake, lotto.id)
    return RedirectResponse(url=f"/lotti/{lotto_id}", status_code=302)


@router.post("/lotti/{lotto_id}/difformita/{difformita_id}/{azione}")
def gestisci_difformita(
    lotto_id: str, difformita_id: str, azione: str,
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    if azione not in ("conferma", "escludi"):
        return RedirectResponse(url=f"/lotti/{lotto_id}", status_code=302)

    d = db.query(Difformita).filter(Difformita.id == uuid.UUID(difformita_id)).first()
    if d:
        d.stato = StatoDifformita.confermata if azione == "conferma" else StatoDifformita.esclusa
        d.gestita_da_id = utente.id
        d.gestita_at = datetime.utcnow()
        db.commit()
    return RedirectResponse(url=f"/lotti/{lotto_id}#revisione-difformita", status_code=302)


@router.post("/lotti/{lotto_id}/completa")
def completa_lotto(
    lotto_id: str, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto and lotto.stato == StatoLotto.revisione_difformita:
        lotto.stato = StatoLotto.completato
        lotto.completato_at = datetime.utcnow()
        db.commit()
    return RedirectResponse(url=f"/lotti/{lotto_id}", status_code=302)


@router.post("/lotti/{lotto_id}/archivia")
def archivia_lotto(
    lotto_id: str, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto and lotto.stato == StatoLotto.completato:
        lotto.stato = StatoLotto.archiviato
        lotto.archiviato_at = datetime.utcnow()
        lotto.sp_archivio_path = lotto.sp_archivio_path.replace(
            "ARCHIVIO/ELABORAZIONI RECENTI", "ARCHIVIO/ELABORAZIONI PASSATE"
        )
        db.commit()
    return RedirectResponse(url=f"/lotti/{lotto_id}", status_code=302)


# ============================================================
# Polling JSON
# ============================================================

@router.get("/lotti/{lotto_id}/stato")
def stato_lotto(lotto_id: str, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente)):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto is None:
        return JSONResponse({"errore": "lotto non trovato"}, status_code=404)

    elaborazione_attiva = lotto.elaborazione_attiva
    log_tail = []
    if elaborazione_attiva:
        log_tail = [
            {"livello": l.livello.value, "messaggio": l.messaggio, "timestamp": l.timestamp.strftime("%H:%M:%S")}
            for l in sorted(elaborazione_attiva.log, key=lambda l: l.timestamp)[-10:]
        ]

    return {
        "id": str(lotto.id),
        "nome": lotto.nome,
        "stato": lotto.stato.value,
        "etichetta_stato": etichetta_stato(lotto.stato),
        "percentuale": percentuale_avanzamento(lotto.stato),
        "fase_attiva": elaborazione_attiva.fase.value if elaborazione_attiva else None,
        "log_recenti": log_tail,
        "n_prescrizioni_totali": lotto.n_prescrizioni_totali,
        "n_barcode_undefined": lotto.n_barcode_undefined,
        "n_difformita_totali": lotto.n_difformita_totali,
    }
