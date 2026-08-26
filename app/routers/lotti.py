"""
Router lotti mensili: /lotti/nuovo /lotti /lotti/{id} + azioni del ciclo
di vita (revisione barcode, avvio fasi successive, gestione difformita,
completamento, archiviazione).
"""
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import get_utente_corrente
from app.models import (
    LottoMensile, StatoLotto, CaricamentoFile, TipoCaricamento, StatoCaricamento,
    Prescrizione, StatoBarcode, Difformita, StatoDifformita, Elaborazione,
    StatoElaborazione, FaseElaborazione, Utente,
)
from app.fake_pipeline import (
    avvia_preprocessing_fake, avvia_ocr_fake, avvia_difformita_fake,
    FAKE_SP_ROOT, PERCORSO_CARTELLA_OUTPUT_RECENTI,
)
from app.real_pipeline import (
    avvia_preprocessing_reale, avvia_ocr_reale, avvia_difformita_reale,
    correggi_barcode_reale, scrivi_excel_finale_reale, esiste_lotto_in_esecuzione,
    metti_in_pausa_container, riprendi_container, annulla_container,
)
from app.config_helper import backend_pipeline_e_reale
from app.progresso import percentuale_avanzamento, etichetta_stato, ETICHETTE_STATO, estrai_progresso_da_log

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

    if backend_pipeline_e_reale(db) and esiste_lotto_in_esecuzione(db):
        return RedirectResponse(
            url="/lotti/nuovo?errore=Un%27altra+elaborazione+Docker+e%27+gia%27+in+corso,+attendi+che+finisca",
            status_code=302,
        )

    lotto = LottoMensile(
        mese=mese, anno=anno, nome=nome,
        stato=StatoLotto.caricamento,
        operatore_id=utente.id,
    )
    db.add(lotto)
    db.commit()
    db.refresh(lotto)

    # Cartella univoca per lotto: mese/anno da soli non bastano piu' a
    # distinguere elaborazioni diverse dello stesso periodo. Il timestamp
    # di creazione + le prime 8 cifre dell'UUID garantiscono unicita' anche
    # in caso di doppio invio nello stesso secondo.
    cartella = f"{_nome_cartella(mese, anno)}_{lotto.created_at:%Y%m%d%H%M%S}_{str(lotto.id)[:8]}"
    lotto.sp_lavoro_path = f"LAVORO/MESE DI LAVORAZIONE/{cartella}"
    lotto.sp_prescrizioni_path = f"LAVORO/MESE DI LAVORAZIONE/{cartella}/PRESCRIZIONI"
    lotto.sp_output_path = "LAVORO/OUTPUT"
    lotto.sp_archivio_path = f"ARCHIVIO/ELABORAZIONI RECENTI/{cartella}"
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
    if backend_pipeline_e_reale(db):
        background_tasks.add_task(avvia_preprocessing_reale, lotto.id)
    else:
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
    progresso_item = None
    if elaborazione_attiva:
        log_recenti = sorted(elaborazione_attiva.log, key=lambda l: l.timestamp)[-20:]
        progresso_item = estrai_progresso_da_log(elaborazione_attiva.log)

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
            "progresso_item": progresso_item,
            "in_pausa": elaborazione_attiva.richiesta_controllo == "pausa" if elaborazione_attiva else False,
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
    nuovo_barcode_pulito = nuovo_barcode.strip()

    if backend_pipeline_e_reale(db) and era_undefined:
        # Sposta per davvero il file da staging a ./dati/ricette/{barcode}.pdf,
        # altrimenti la fase OCR successiva non lo troverebbe.
        correggi_barcode_reale(presc.id, nuovo_barcode_pulito)
        db.refresh(presc)
    else:
        presc.barcode = nuovo_barcode_pulito
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


@router.get("/lotti/{lotto_id}/prescrizioni/{prescrizione_id}/pdf")
def visualizza_pdf_prescrizione(
    lotto_id: str, prescrizione_id: str,
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    """
    Apre nel browser il PDF della singola prescrizione (link cliccabile
    sul barcode nel dettaglio lotto). Oggi i file vivono nella cartella
    finta che simula SharePoint (app/fake_pipeline.FAKE_SP_ROOT); quando
    sara' collegato lo storage reale, solo la risoluzione del percorso
    qui sotto andra' aggiornata (lettura dal vero SharePoint via
    Configurazione.sharepoint_base_path) — la route e il link nel
    template restano identici.
    """
    presc = (
        db.query(Prescrizione)
        .filter(Prescrizione.id == uuid.UUID(prescrizione_id), Prescrizione.lotto_id == uuid.UUID(lotto_id))
        .first()
    )
    if presc is None or not presc.sp_pdf_path:
        return JSONResponse({"errore": "Prescrizione o file non trovati"}, status_code=404)

    radice = os.path.normpath(FAKE_SP_ROOT)
    percorso = os.path.normpath(os.path.join(FAKE_SP_ROOT, presc.sp_pdf_path))
    if not percorso.startswith(radice + os.sep) or not os.path.isfile(percorso):
        return JSONResponse({"errore": "File non accessibile"}, status_code=404)

    return FileResponse(percorso, media_type="application/pdf", filename=os.path.basename(percorso))


@router.post("/lotti/{lotto_id}/avvia-ocr")
def avvia_ocr(
    lotto_id: str, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto and lotto.stato == StatoLotto.revisione_barcode and (lotto.n_barcode_undefined or 0) == 0:
        if backend_pipeline_e_reale(db):
            background_tasks.add_task(avvia_ocr_reale, lotto.id)
        else:
            background_tasks.add_task(avvia_ocr_fake, lotto.id)
    return RedirectResponse(url=f"/lotti/{lotto_id}", status_code=302)


@router.post("/lotti/{lotto_id}/avvia-difformita")
def avvia_difformita(
    lotto_id: str, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto and lotto.stato == StatoLotto.revisione_qualita:
        if backend_pipeline_e_reale(db):
            background_tasks.add_task(avvia_difformita_reale, lotto.id)
        else:
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
        if backend_pipeline_e_reale(db):
            successo = scrivi_excel_finale_reale(lotto.id)
            if not successo:
                return RedirectResponse(
                    url=f"/lotti/{lotto_id}?errore=Scrittura+Excel+finale+fallita,+vedi+i+log", status_code=302
                )
            db.refresh(lotto)
        lotto.stato = StatoLotto.completato
        lotto.completato_at = datetime.utcnow()
        db.commit()
    return RedirectResponse(url=f"/lotti/{lotto_id}", status_code=302)


@router.get("/lotti/{lotto_id}/output-excel")
def visualizza_output_excel(
    lotto_id: str, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    """
    Apre/scarica il foglio di output finale del lotto (bottone "Apri
    file Excel" nel dettaglio lotto). Stessa cartella fissa per tutti
    i lotti (PERCORSO_CARTELLA_OUTPUT_RECENTI, vedi fake_pipeline.py) —
    a differenza dei PDF per prescrizione, qui non c'e' bisogno del
    fallback "genera al volo" perche' il file nasce sempre insieme al
    completamento del lotto (completa_lotto chiama genera_excel_output_fake
    prima di cambiare stato): se manca, il lotto non e' davvero completato.
    """
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto is None or not lotto.excel_output_filename:
        return JSONResponse({"errore": "Excel di output non ancora disponibile per questo lotto"}, status_code=404)

    radice = os.path.normpath(FAKE_SP_ROOT)
    percorso = os.path.normpath(os.path.join(FAKE_SP_ROOT, PERCORSO_CARTELLA_OUTPUT_RECENTI, lotto.excel_output_filename))
    if not percorso.startswith(radice + os.sep) or not os.path.isfile(percorso):
        return JSONResponse({"errore": "File Excel non trovato"}, status_code=404)

    return FileResponse(
        percorso,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=lotto.excel_output_filename
    )


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
# Controllo dell'elaborazione in corso (pausa / riprendi / annulla)
# ============================================================

@router.post("/lotti/{lotto_id}/elaborazione/pausa")
def metti_in_pausa(
    lotto_id: str, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    elaborazione = lotto.elaborazione_attiva if lotto else None
    if elaborazione and elaborazione.stato == StatoElaborazione.in_corso:
        elaborazione.richiesta_controllo = "pausa"
        db.commit()
        if backend_pipeline_e_reale(db) and elaborazione.nome_container:
            metti_in_pausa_container(elaborazione.nome_container)
    return RedirectResponse(url=f"/lotti/{lotto_id}", status_code=302)


@router.post("/lotti/{lotto_id}/elaborazione/riprendi")
def riprendi(
    lotto_id: str, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    elaborazione = lotto.elaborazione_attiva if lotto else None
    if elaborazione and elaborazione.richiesta_controllo == "pausa":
        if backend_pipeline_e_reale(db) and elaborazione.nome_container:
            riprendi_container(elaborazione.nome_container)
        elaborazione.richiesta_controllo = None
        db.commit()
    return RedirectResponse(url=f"/lotti/{lotto_id}", status_code=302)


@router.post("/lotti/{lotto_id}/elaborazione/annulla")
def annulla(
    lotto_id: str, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    elaborazione = lotto.elaborazione_attiva if lotto else None
    if elaborazione and elaborazione.stato == StatoElaborazione.in_corso:
        elaborazione.richiesta_controllo = "annulla"
        db.commit()
        if backend_pipeline_e_reale(db) and elaborazione.nome_container:
            # Se era in pausa, un container congelato non riceve il kill finche'
            # non viene ripreso: lo riprendo un istante prima di ucciderlo.
            riprendi_container(elaborazione.nome_container)
            annulla_container(elaborazione.nome_container)
    return RedirectResponse(url=f"/lotti/{lotto_id}", status_code=302)


@router.post("/lotti/{lotto_id}/riprova")
def riprova_lotto(
    lotto_id: str, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    """
    Rilancia la fase che ha interrotto il lotto (bottone "Riprova" sul
    banner di eccezione). La fase da riavviare si ricava dall'ultima
    Elaborazione in stato "errore" per questo lotto — non serve un
    campo dedicato sul lotto, e' gia' tutto tracciato li'.

    Prima di rilanciare, ripulisce gli eventuali dati parziali scritti
    dal tentativo fallito (una fase puo' fallire a meta' ciclo, con
    alcune prescrizioni gia' elaborate e altre no): senza questa
    pulizia, un retry rischierebbe di creare doppioni.
    """
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto is None or lotto.stato != StatoLotto.eccezione:
        return RedirectResponse(url=f"/lotti/{lotto_id}", status_code=302)

    ultima_fallita = (
        db.query(Elaborazione)
        .filter(Elaborazione.lotto_id == lotto.id, Elaborazione.stato == StatoElaborazione.errore)
        .order_by(Elaborazione.started_at.desc())
        .first()
    )
    fase = ultima_fallita.fase if ultima_fallita else FaseElaborazione.preprocessing

    lotto.note = None

    if fase == FaseElaborazione.preprocessing:
        for p in list(lotto.prescrizioni):
            db.delete(p)
        lotto.stato = StatoLotto.caricamento
        lotto.n_prescrizioni_totali = 0
        lotto.n_barcode_letti = 0
        lotto.n_barcode_undefined = 0
        db.commit()
        background_tasks.add_task(avvia_preprocessing_fake, lotto.id)

    elif fase == FaseElaborazione.vllm:
        for p in lotto.prescrizioni:
            if p.dati_ocr:
                db.delete(p.dati_ocr)
            p.score_ocr = None
            p.n_campi_compilati = None
            p.barcode_in_excel = None
            p.riga_excel = None
            p.sp_json_path = None
        lotto.stato = StatoLotto.revisione_barcode
        lotto.score_ocr_medio = None
        lotto.n_match_excel = 0
        db.commit()
        background_tasks.add_task(avvia_ocr_fake, lotto.id)

    elif fase == FaseElaborazione.difformita:
        for d in list(lotto.difformita):
            db.delete(d)
        lotto.stato = StatoLotto.revisione_qualita
        lotto.n_difformita_totali = 0
        db.commit()
        background_tasks.add_task(avvia_difformita_fake, lotto.id)

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
    progresso_item = None
    if elaborazione_attiva:
        log_ordinato = sorted(elaborazione_attiva.log, key=lambda l: l.timestamp)
        log_tail = [
            {"livello": l.livello.value, "messaggio": l.messaggio, "timestamp": l.timestamp.strftime("%H:%M:%S")}
            for l in log_ordinato[-10:]
        ]
        progresso_item = estrai_progresso_da_log(log_ordinato)

    return {
        "id": str(lotto.id),
        "nome": lotto.nome,
        "stato": lotto.stato.value,
        "etichetta_stato": etichetta_stato(lotto.stato),
        "percentuale": percentuale_avanzamento(lotto.stato),
        "fase_attiva": elaborazione_attiva.fase.value if elaborazione_attiva else None,
        "richiesta_controllo": elaborazione_attiva.richiesta_controllo if elaborazione_attiva else None,
        "progresso_item": progresso_item,
        "log_recenti": log_tail,
        "n_prescrizioni_totali": lotto.n_prescrizioni_totali,
        "n_barcode_undefined": lotto.n_barcode_undefined,
        "n_difformita_totali": lotto.n_difformita_totali,
    }
