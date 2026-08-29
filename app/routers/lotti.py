"""
Router lotti mensili: /lotti/nuovo /lotti /lotti/{id} + azioni del ciclo
di vita (revisione barcode, avvio fasi successive, gestione difformita,
completamento, archiviazione).
"""
import os
import shutil
import uuid
import io
import zipfile
import re
from datetime import datetime, date
from pathlib import Path
from typing import List

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import get_utente_corrente
from app.models import (
    LottoMensile, StatoLotto, CaricamentoFile, TipoCaricamento, StatoCaricamento,
    Prescrizione, StatoBarcode, Difformita, StatoDifformita, Elaborazione,
    StatoElaborazione, FaseElaborazione, Utente, StatoRevisionePrescrizione, DatiOcr,
    GravitaDifformita, LogElaborazione,
)
from app.storage import (
    MEDIA_ROOT,
    percorso_pdf_prescrizione as _percorso_pdf_prescrizione,
    radice_sharepoint,
    risolvi_anteprima as _risolvi_anteprima,
    salva_pdf_caricato,
)
from app.real_pipeline import (
    avvia_preprocessing_reale, avvia_ocr_reale, avvia_difformita_reale,
    correggi_barcode_reale, scrivi_excel_finale_reale, esiste_lotto_in_esecuzione,
    lotto_in_esecuzione, metti_in_pausa_container, riprendi_container, annulla_container,
    pulisci_dati_parziali_fase, log_elaborazione,
)
from app.progresso import (
    percentuale_avanzamento, etichetta_stato, ETICHETTE_STATO,
    estrai_progresso_da_log, messaggio_fase_operatore,
    indice_fase_wizard, FASI_WIZARD_LOTTO, formatta_log_righe, formatta_ora_locale,
)

router = APIRouter(tags=["lotti"])
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["ora_locale"] = formatta_ora_locale

UPLOAD_DIR_TEMP = "uploads"
ESTENSIONI_PDF_VALIDE = {".pdf"}
ESTENSIONI_EXCEL_VALIDE = {".xlsx", ".xls"}
NUMERO_LOTTI_RECENTI = 10

NUMERO_RIGHE_LOG = 300

BADGE_LIVELLO_LOG = {"error": "badge--errore", "warning": "badge--attesa", "info": "badge--in-coda"}


def _log_lotto(db: Session, lotto_id, limite: int = NUMERO_RIGHE_LOG):
    """
    Ultime `limite` righe di log di TUTTE le elaborazioni del lotto (non solo
    quella attiva): passando da una fase alla successiva viene creata una
    nuova Elaborazione, e il log della fase precedente deve restare visibile
    nel pannello invece di sparire. Query diretta e limitata (non
    elaborazione.log): quel campo puo' avere migliaia di righe.
    """
    righe = (
        db.query(LogElaborazione)
        .join(Elaborazione, LogElaborazione.elaborazione_id == Elaborazione.id)
        .filter(Elaborazione.lotto_id == lotto_id)
        .order_by(LogElaborazione.timestamp.desc())
        .limit(limite)
        .all()
    )
    righe.reverse()
    return righe


MESI_IT = ["", "GENNAIO", "FEBBRAIO", "MARZO", "APRILE", "MAGGIO", "GIUGNO",
           "LUGLIO", "AGOSTO", "SETTEMBRE", "OTTOBRE", "NOVEMBRE", "DICEMBRE"]


def _url_dettaglio_lotto(lotto_id, lotto=None, fase=None) -> str:
    if fase is None and lotto is not None:
        fase = indice_fase_wizard(lotto.stato, lotto)
    if fase is None:
        fase = 3
    return f"/lotti/{lotto_id}?fase={fase}"


def _fase_vista_da_query(request: Request, lotto: LottoMensile) -> int:
    corrente = indice_fase_wizard(lotto.stato, lotto)
    grezzo = request.query_params.get("fase")
    if grezzo is None:
        return corrente
    try:
        richiesta = int(grezzo)
    except ValueError:
        return corrente
    return max(1, min(corrente, richiesta))


def _prescrizione_del_lotto(db: Session, lotto_id: str, prescrizione_id: str):
    return (
        db.query(Prescrizione)
        .options(joinedload(Prescrizione.dati_ocr), joinedload(Prescrizione.difformita))
        .filter(
            Prescrizione.id == uuid.UUID(prescrizione_id),
            Prescrizione.lotto_id == uuid.UUID(lotto_id),
        )
        .first()
    )


# ============================================================
# Creazione nuovo lotto
# ============================================================

@router.get("/lotti/nuovo", response_class=HTMLResponse)
def form_nuovo_lotto(
    request: Request, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    oggi = datetime.utcnow()
    lotto_bloccante = lotto_in_esecuzione(db)
    return templates.TemplateResponse(
        "nuovo_lotto.html",
        {"request": request, "utente": utente, "voce_attiva": "nuova",
         "mese_corrente": oggi.month, "anno_corrente": oggi.year,
         "fasi_wizard": FASI_WIZARD_LOTTO,
         "lotto_bloccante": lotto_bloccante,
         "etichetta_stato_bloccante": etichetta_stato(lotto_bloccante.stato) if lotto_bloccante else None},
    )


@router.post("/lotti/nuovo")
def crea_lotto(
    request: Request,
    background_tasks: BackgroundTasks,
    nome: str = Form(""),
    mese: int = Form(...),
    anno: int = Form(...),
    file_pdf: UploadFile = File(None),
    files_cartella: List[UploadFile] = File(None),
    file_excel: UploadFile = File(...),
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    nome = nome.strip()
    if not nome:
        return RedirectResponse(url="/lotti/nuovo?errore=Il+nome+del+lotto+e%27+obbligatorio", status_code=302)

    # Confronto case-insensitive: "Agosto 2026" e "agosto 2026" sono lo
    # stesso nome per un operatore che sfoglia l'elenco, non vanno
    # trattati come due lotti distinti solo per la differenza di maiuscole.
    nome_gia_usato = (
        db.query(LottoMensile)
        .filter(func.lower(LottoMensile.nome) == nome.lower())
        .first()
    )
    if nome_gia_usato:
        return RedirectResponse(
            url="/lotti/nuovo?errore=Esiste+gia%27+un+lotto+con+questo+nome,+scegline+uno+diverso",
            status_code=302,
        )

    # Due modalita' alternative per le prescrizioni: un PDF combinato
    # multi-pagina (caricamento storico), oppure una cartella con piu' PDF
    # gia' separati (uno per ricetta) — il preprocessing itera comunque
    # tutti i file trovati, quindi il resto della pipeline non fa differenza.
    file_pdf_valido = file_pdf is not None and bool(file_pdf.filename)
    file_cartella_validi = [
        f for f in (files_cartella or [])
        if f.filename and os.path.splitext(f.filename)[1].lower() in ESTENSIONI_PDF_VALIDE
    ]

    if not file_pdf_valido and not file_cartella_validi:
        return RedirectResponse(
            url="/lotti/nuovo?errore=Carica+un+PDF+combinato+oppure+una+cartella+di+PDF",
            status_code=302,
        )

    if file_pdf_valido:
        _, ext_pdf = os.path.splitext(file_pdf.filename or "")
        if ext_pdf.lower() not in ESTENSIONI_PDF_VALIDE:
            return RedirectResponse(url="/lotti/nuovo?errore=Il+file+prescrizioni+deve+essere+un+PDF", status_code=302)

    # L'Excel Regione e' obbligatorio: senza, pipeline.esegui_merge_regione
    # viene saltato (vedi pipeline.py) e i check 14/17/18 in fase difformita
    # segnalano "dati mancanti" per OGNI prescrizione — un lotto senza
    # Excel Regione non produce risultati utilizzabili, quindi non deve
    # nemmeno poter partire.
    _, ext_excel = os.path.splitext(file_excel.filename or "")
    if not file_excel.filename or ext_excel.lower() not in ESTENSIONI_EXCEL_VALIDE:
        return RedirectResponse(
            url="/lotti/nuovo?errore=L%27Excel+Regione+e%27+obbligatorio+(.xlsx+o+.xls)", status_code=302
        )

    if esiste_lotto_in_esecuzione(db):
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

    # Percorso unico e stabile per tutta la vita del lotto (in corso ->
    # completato -> archiviato): niente piu' spostamento da una cartella
    # "di lavoro" a una "di archivio", il lotto nasce gia' nella sua
    # posizione definitiva sotto Macchina Locale/Archivio/{anno}/{mese}/.
    # Il timestamp di creazione + le prime 8 cifre dell'UUID garantiscono
    # unicita' anche in caso di doppio invio nello stesso secondo/mese.
    cartella_mese = f"{mese:02d} - {MESI_IT[mese].capitalize()}"
    cartella = f"{lotto.created_at:%Y%m%d%H%M%S}_{str(lotto.id)[:8]}"
    base_lotto = f"Macchina Locale/Archivio/{anno}/{cartella_mese}/{cartella}"
    lotto.sp_lavoro_path = base_lotto
    lotto.sp_prescrizioni_path = f"{base_lotto}/PRESCRIZIONI"
    lotto.sp_output_path = f"{base_lotto}/OUTPUT"
    lotto.sp_archivio_path = base_lotto
    db.commit()
    db.refresh(lotto)

    # PDF: salvati SOLO come input locale per la pipeline (media/lotti/{id}/
    # originale/, letto da _copia_lotto_verso_dati in real_pipeline.py) —
    # non copiati anche su SharePoint in PRESCRIZIONI. Quella cartella deve
    # contenere le ricette gia' divise per singola prescrizione, non i PDF
    # originali: avvia_preprocessing_reale li pubblica li' a fine
    # preprocessing (vedi _pubblica_prescrizioni_su_sharepoint).
    os.makedirs(UPLOAD_DIR_TEMP, exist_ok=True)

    file_pdf_da_salvare = [file_pdf] if file_pdf_valido else file_cartella_validi
    for f in file_pdf_da_salvare:
        contenuto_pdf = f.file.read()
        percorso_locale_pdf = salva_pdf_caricato(lotto.id, f.filename, contenuto_pdf)
        db.add(CaricamentoFile(
            lotto_id=lotto.id, tipo=TipoCaricamento.pdf_combined,
            nome_file_locale=f.filename, nome_file_sp=f.filename,
            percorso_sp=percorso_locale_pdf, dimensione_bytes=len(contenuto_pdf),
            stato=StatoCaricamento.completato, caricato_da_id=utente.id,
            started_at=datetime.utcnow(), completed_at=datetime.utcnow(),
        ))

    if file_excel is not None and file_excel.filename:
        _, ext_excel = os.path.splitext(file_excel.filename)
        if ext_excel.lower() in ESTENSIONI_EXCEL_VALIDE:
            contenuto_excel = file_excel.file.read()
            percorso_relativo_excel = f"{lotto.sp_lavoro_path}/{file_excel.filename}"
            percorso_assoluto_excel = os.path.join(str(radice_sharepoint()), percorso_relativo_excel)
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

    background_tasks.add_task(avvia_preprocessing_reale, lotto.id)

    return RedirectResponse(url=_url_dettaglio_lotto(lotto.id, fase=3), status_code=302)


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
    progresso_item = None
    in_pausa = False
    if elaborazione_attiva:
        progresso_item = estrai_progresso_da_log(elaborazione_attiva.log)
        in_pausa = elaborazione_attiva.richiesta_controllo == "pausa"

    log_righe = formatta_log_righe(_log_lotto(db, lotto.id))

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
            "progresso_item": progresso_item,
            "in_pausa": in_pausa,
            "log_righe": log_righe,
            "badge_livello_log": BADGE_LIVELLO_LOG,
            "messaggio_operatore": messaggio_fase_operatore(
                elaborazione_attiva.fase if elaborazione_attiva else None,
                in_pausa,
            ),
            "fase_corrente": indice_fase_wizard(lotto.stato, lotto),
            "fase_wizard": _fase_vista_da_query(request, lotto),
            "fasi_wizard": FASI_WIZARD_LOTTO,
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

    if era_undefined:
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
    return RedirectResponse(url=f"/lotti/{lotto_id}?fase=3", status_code=302)


@router.post("/lotti/{lotto_id}/barcode/{prescrizione_id}/escludi")
def escludi_pagina_non_fronte(
    lotto_id: str, prescrizione_id: str,
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    presc = db.query(Prescrizione).filter(Prescrizione.id == uuid.UUID(prescrizione_id)).first()
    if presc is None:
        return RedirectResponse(url=f"/lotti/{lotto_id}", status_code=302)
    if presc.stato_barcode != StatoBarcode.undefined:
        return RedirectResponse(url=f"/lotti/{lotto_id}", status_code=302)

    presc.stato_barcode = StatoBarcode.escluso
    presc.barcode = None
    presc.barcode_corretto_da_id = utente.id
    presc.barcode_corretto_at = datetime.utcnow()

    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto:
        lotto.n_barcode_undefined = max(0, (lotto.n_barcode_undefined or 0) - 1)
        lotto.n_prescrizioni_totali = max(0, (lotto.n_prescrizioni_totali or 0) - 1)
    db.commit()
    return RedirectResponse(url=f"/lotti/{lotto_id}?fase=3", status_code=302)


@router.get("/lotti/{lotto_id}/prescrizioni/{prescrizione_id}/pdf")
def visualizza_pdf_prescrizione(
    lotto_id: str, prescrizione_id: str,
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    presc = (
        db.query(Prescrizione)
        .filter(Prescrizione.id == uuid.UUID(prescrizione_id), Prescrizione.lotto_id == uuid.UUID(lotto_id))
        .first()
    )
    if presc is None:
        return JSONResponse({"errore": "Prescrizione o file non trovati"}, status_code=404)

    percorso, tipo = _risolvi_anteprima(presc)
    if percorso is None:
        return JSONResponse({"errore": "File non accessibile"}, status_code=404)

    mime = {
        "pdf": "application/pdf",
        "png": "image/png",
        "jpeg": "image/jpeg",
    }.get(tipo, "application/octet-stream")
    return FileResponse(
        percorso,
        media_type=mime,
        headers={"Content-Disposition": "inline"},
    )


ETICHETTE_CAMPO_OCR = {
    "cognome_nome_assistito": "Cognome / Nome assistito",
    "codice_fiscale": "Codice fiscale",
    "codice_esenzione": "Codice esenzione",
    "codice_atc": "Codice ATC",
    "testo_prescrizione": "Testo prescrizione",
    "metodo_estrattivo_olio": "Metodo estrattivo olio",
    "forma_farmaceutica": "Forma farmaceutica",
    "data_prescrizione": "Data prescrizione",
    "data_etichetta_preparazione": "Data preparazione etichetta",
    "data_invio": "Data invio / emissione",
    "etichetta_data_scadenza": "Data scadenza etichetta",
    "timbro_medico": "Timbro medico",
    "firma_medico": "Firma medico",
    "etichetta_nome_cognome_medico": "Nome medico (etichetta)",
    "etichetta_nome_cognome_paziente": "Nome paziente (etichetta)",
    "etichetta_prezzo_sost": "Prezzo sost. (etichetta)",
    "etichetta_prezzo_on": "Prezzo on. (etichetta)",
    "etichetta_prezzo_rec": "Prezzo rec. (etichetta)",
    "etichetta_prezzo_iva": "IVA (etichetta)",
    "etichetta_prezzo_tot": "Totale (etichetta)",
    "totale_prescrizione": "Totale prescrizione",
    "etichetta_thc": "THC (etichetta)",
    "nome_farmacia": "Nome farmacia",
    "etichetta_avvertenze": "Avvertenze (etichetta)",
}


def _ctx_revisione(request, utente, lotto, presc, modo):
    return {
        "request": request, "utente": utente, "voce_attiva": "elaborazioni",
        "lotto": lotto, "presc": presc, "modo": modo,
        "fase_corrente": indice_fase_wizard(lotto.stato, lotto),
        "fase_wizard": {"barcode": 3, "ocr": 4, "difformita": 5}.get(modo, indice_fase_wizard(lotto.stato, lotto)),
        "fasi_wizard": FASI_WIZARD_LOTTO,
        "etichette_ocr": ETICHETTE_CAMPO_OCR,
        "campi_booleani": ("timbro_medico", "firma_medico"),
        "campi_area": ("testo_prescrizione", "etichetta_avvertenze"),
        "valori_ocr": {},
        "da_gestire": [],
        "tipo_anteprima": _risolvi_anteprima(presc)[1],
    }


@router.get("/lotti/{lotto_id}/prescrizioni/{prescrizione_id}/revisione-barcode", response_class=HTMLResponse)
def pagina_revisione_barcode(
    request: Request, lotto_id: str, prescrizione_id: str,
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    presc = _prescrizione_del_lotto(db, lotto_id, prescrizione_id)
    if lotto is None or presc is None:
        return RedirectResponse(url="/lotti", status_code=302)
    return templates.TemplateResponse(
        "revisione_dettaglio.html",
        _ctx_revisione(request, utente, lotto, presc, "barcode"),
    )


@router.get("/lotti/{lotto_id}/prescrizioni/{prescrizione_id}/revisione-ocr", response_class=HTMLResponse)
def pagina_revisione_ocr(
    request: Request, lotto_id: str, prescrizione_id: str,
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    presc = _prescrizione_del_lotto(db, lotto_id, prescrizione_id)
    if lotto is None or presc is None:
        return RedirectResponse(url="/lotti", status_code=302)
    valori_ocr = {}
    if presc.dati_ocr:
        if presc.dati_ocr.json_corretto:
            valori_ocr = dict(presc.dati_ocr.json_corretto)
        else:
            for col in COLONNE_DATI_OCR:
                v = getattr(presc.dati_ocr, col, None)
                if isinstance(v, (date, datetime)):
                    valori_ocr[col] = v.isoformat()
                elif v is not None:
                    valori_ocr[col] = v
    ctx = _ctx_revisione(request, utente, lotto, presc, "ocr")
    ctx["valori_ocr"] = valori_ocr
    return templates.TemplateResponse("revisione_dettaglio.html", ctx)


@router.get("/lotti/{lotto_id}/prescrizioni/{prescrizione_id}/revisione-difformita", response_class=HTMLResponse)
def pagina_revisione_difformita(
    request: Request, lotto_id: str, prescrizione_id: str,
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    presc = _prescrizione_del_lotto(db, lotto_id, prescrizione_id)
    if lotto is None or presc is None:
        return RedirectResponse(url="/lotti", status_code=302)
    ctx = _ctx_revisione(request, utente, lotto, presc, "difformita")
    ctx["da_gestire"] = [d for d in presc.difformita if d.stato == StatoDifformita.rilevata]
    return templates.TemplateResponse("revisione_dettaglio.html", ctx)


@router.post("/lotti/{lotto_id}/avvia-ocr")
def avvia_ocr(
    lotto_id: str, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto and lotto.stato == StatoLotto.revisione_barcode and (lotto.n_barcode_undefined or 0) == 0:
        lotto.stato = StatoLotto.elaborazione_ocr
        db.commit()
        background_tasks.add_task(avvia_ocr_reale, lotto.id)
        return RedirectResponse(url=_url_dettaglio_lotto(lotto_id, fase=4), status_code=302)
    return RedirectResponse(url=_url_dettaglio_lotto(lotto_id, lotto), status_code=302)


@router.post("/lotti/{lotto_id}/avvia-difformita")
def avvia_difformita(
    lotto_id: str, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto and lotto.stato == StatoLotto.revisione_qualita:
        lotto.stato = StatoLotto.analisi_difformita
        db.commit()
        background_tasks.add_task(avvia_difformita_reale, lotto.id)
        return RedirectResponse(url=_url_dettaglio_lotto(lotto_id, fase=5), status_code=302)
    return RedirectResponse(url=_url_dettaglio_lotto(lotto_id, lotto), status_code=302)


@router.post("/lotti/{lotto_id}/difformita/{difformita_id}/{azione}")
def gestisci_difformita(
    lotto_id: str, difformita_id: str, azione: str,
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
    next: str = Form(""),
):
    if azione not in ("conferma", "escludi"):
        return RedirectResponse(url=f"/lotti/{lotto_id}", status_code=302)

    d = db.query(Difformita).filter(Difformita.id == uuid.UUID(difformita_id)).first()
    if d:
        d.stato = StatoDifformita.confermata if azione == "conferma" else StatoDifformita.esclusa
        d.gestita_da_id = utente.id
        d.gestita_at = datetime.utcnow()
        db.commit()
    if next.startswith(f"/lotti/{lotto_id}/"):
        return RedirectResponse(url=next, status_code=302)
    return RedirectResponse(url=_url_dettaglio_lotto(lotto_id, fase=5), status_code=302)


COLONNE_DATI_OCR = [
    "cognome_nome_assistito", "codice_fiscale", "codice_esenzione", "codice_atc",
    "testo_prescrizione", "metodo_estrattivo_olio", "forma_farmaceutica",
    "data_prescrizione", "data_etichetta_preparazione", "data_invio",
    "etichetta_data_scadenza", "timbro_medico", "firma_medico",
    "etichetta_nome_cognome_medico", "etichetta_nome_cognome_paziente",
    "etichetta_prezzo_sost", "etichetta_prezzo_on", "etichetta_prezzo_rec",
    "etichetta_prezzo_iva", "etichetta_prezzo_tot", "totale_prescrizione",
    "etichetta_thc", "nome_farmacia", "etichetta_avvertenze",
]

def parse_date_only(val: str):
    if not val or val.strip().upper() in ("", "NONE", "NAN", "NAT", "OCR_INCERTO"):
        return None
    try:
        return datetime.strptime(val.strip(), "%Y-%m-%d").date()
    except ValueError:
        try:
            return datetime.strptime(val.strip(), "%d/%m/%Y").date()
        except ValueError:
            return None

def parse_numeric(val: str):
    if not val or val.strip().upper() in ("", "NONE", "NAN"):
        return None
    try:
        return float(val.replace(",", "."))
    except ValueError:
        return None

def parse_boolean(val: str):
    if val is None:
        return False
    return str(val).strip().lower() in ("true", "1", "on", "yes", "sì", "si")


@router.post("/lotti/{lotto_id}/completa")
def completa_lotto(
    lotto_id: str, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto and lotto.stato == StatoLotto.revisione_difformita:
        successo = scrivi_excel_finale_reale(lotto.id)
        if not successo:
            return RedirectResponse(
                url=f"/lotti/{lotto_id}?errore=Scrittura+Excel+finale+fallita,+vedi+i+log", status_code=302
            )
        db.refresh(lotto)
        lotto.stato = StatoLotto.completato
        lotto.completato_at = datetime.utcnow()
        db.commit()
    return RedirectResponse(url=_url_dettaglio_lotto(lotto_id, fase=6), status_code=302)


@router.get("/lotti/{lotto_id}/export-zip")
def export_zip(
    lotto_id: str,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto is None:
        return JSONResponse({"errore": "Lotto non trovato"}, status_code=404)
    if lotto.stato != StatoLotto.completato and lotto.stato != StatoLotto.archiviato:
        return JSONResponse({"errore": "Il lotto deve essere completato per esportare lo ZIP"}, status_code=400)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        prescrizioni = lotto.prescrizioni
        da_includere = []
        for p in prescrizioni:
            if p.stato_barcode == StatoBarcode.escluso:
                continue
            confermate = [d for d in p.difformita if d.stato == StatoDifformita.confermata]
            da_includere.append((p, confermate))
        if not any(c for _, c in da_includere):
            da_includere = [(p, True) for p in prescrizioni if p.stato_barcode != StatoBarcode.escluso]
        else:
            da_includere = [(p, c) for p, c in da_includere if c]
        gruppi_farmacia = {}
        for p, _conf in da_includere:
            nome_farmacia = "FARMACIA_SCONOSCIUTA"
            if p.dati_ocr and p.dati_ocr.nome_farmacia:
                nome_farmacia = p.dati_ocr.nome_farmacia.strip()
            nome_cartella = re.sub(r'[^a-zA-Z0-9_\-\s]', '', nome_farmacia).strip().replace(" ", "_")
            if not nome_cartella:
                nome_cartella = "FARMACIA_SCONOSCIUTA"
            if nome_cartella not in gruppi_farmacia:
                gruppi_farmacia[nome_cartella] = []
            gruppi_farmacia[nome_cartella].append(p)

        for nome_cartella, prescs in gruppi_farmacia.items():
            for p in prescs:
                percorso_pdf = _percorso_pdf_prescrizione(p)
                if percorso_pdf:
                    nome_pdf = os.path.basename(p.sp_pdf_path or percorso_pdf)
                    zip_file.write(percorso_pdf, arcname=f"{nome_cartella}/{nome_pdf}")

    zip_buffer.seek(0)
    nome_zip = f"ESPORTAZIONE_FARMACIE_{lotto.nome}.zip"
    return Response(
        zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={nome_zip}"}
    )


@router.get("/lotti/{lotto_id}/prescrizioni/{prescrizione_id}/ocr")
def get_ocr_data(
    lotto_id: str, prescrizione_id: str,
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente)
):
    presc = db.query(Prescrizione).filter(Prescrizione.id == uuid.UUID(prescrizione_id)).first()
    if not presc or not presc.dati_ocr:
        return JSONResponse({"errore": "Dati OCR non trovati"}, status_code=404)
    
    dati = presc.dati_ocr
    raw = dati.json_vllm_raw or {}
    
    corretto = dati.json_corretto or {}
    if not corretto:
        corretto = {col: getattr(dati, col) for col in COLONNE_DATI_OCR}
        for k, v in corretto.items():
            if isinstance(v, (date, datetime)):
                corretto[k] = v.isoformat()
            elif isinstance(v, (int, float)):
                corretto[k] = float(v)
            elif v is not None:
                corretto[k] = str(v)
                
    return {
        "barcode": presc.barcode,
        "raw": raw,
        "corretto": corretto
    }


@router.post("/lotti/{lotto_id}/prescrizioni/{prescrizione_id}/ocr")
async def correggi_ocr(
    lotto_id: str,
    prescrizione_id: str,
    request: Request,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    presc = db.query(Prescrizione).filter(Prescrizione.id == uuid.UUID(prescrizione_id)).first()
    if not presc or not presc.dati_ocr:
        return JSONResponse({"errore": "Dati OCR o prescrizione non trovati"}, status_code=404)
        
    dati_ocr = presc.dati_ocr
    form_data = await request.form()
    
    CAMPI_BOOLEANI = {"timbro_medico", "firma_medico"}
    CAMPI_PREZZO = {
        "etichetta_prezzo_sost", "etichetta_prezzo_on", "etichetta_prezzo_rec",
        "etichetta_prezzo_iva", "etichetta_prezzo_tot", "totale_prescrizione",
    }
    CAMPI_DATA = {
        "data_prescrizione", "data_etichetta_preparazione", "data_invio", "etichetta_data_scadenza",
    }
    
    for col in COLONNE_DATI_OCR:
        if col in form_data:
            val_raw = form_data.get(col)
            if col in CAMPI_DATA:
                setattr(dati_ocr, col, parse_date_only(val_raw))
            elif col in CAMPI_PREZZO:
                setattr(dati_ocr, col, parse_numeric(val_raw))
            elif col in CAMPI_BOOLEANI:
                setattr(dati_ocr, col, parse_boolean(val_raw))
            else:
                setattr(dati_ocr, col, val_raw if val_raw != "" else None)
        else:
            if col in CAMPI_BOOLEANI:
                setattr(dati_ocr, col, False)

    json_data = {}
    for col in COLONNE_DATI_OCR:
        val = getattr(dati_ocr, col)
        if isinstance(val, (date, datetime)):
            json_data[col] = val.isoformat()
        else:
            json_data[col] = val
            
    dati_ocr.json_corretto = json_data
    dati_ocr.corretto_da_id = utente.id
    dati_ocr.corretto_at = datetime.utcnow()
    presc.stato_revisione = StatoRevisionePrescrizione.corretto
    
    db.commit()
    torna = form_data.get("torna_elenco")
    if torna:
        return RedirectResponse(url=f"/lotti/{lotto_id}?fase=4", status_code=302)
    return {"stato": "ok"}


@router.get("/lotti/{lotto_id}/output-excel")
def visualizza_output_excel(
    lotto_id: str, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto is None or not lotto.excel_output_filename:
        return JSONResponse({"errore": "Excel di output non ancora disponibile per questo lotto"}, status_code=404)

    radice = radice_sharepoint()
    candidati = []
    if lotto.sp_output_path:
        candidati.append(radice / lotto.sp_output_path / lotto.excel_output_filename)
    candidati.append(MEDIA_ROOT / "lotti" / str(lotto.id) / "pagine" / lotto.excel_output_filename)
    percorso = None
    radici = (radice.resolve(), MEDIA_ROOT.resolve())
    for candidato in candidati:
        try:
            risolto = candidato.resolve()
            if not any(True for r in radici if str(risolto).startswith(str(r))):
                continue
        except (ValueError, OSError):
            continue
        if risolto.is_file():
            percorso = risolto
            break
    if percorso is None:
        return JSONResponse({"errore": "File Excel non trovato"}, status_code=404)

    return FileResponse(
        str(percorso),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=lotto.excel_output_filename
    )


@router.post("/lotti/{lotto_id}/archivia")
def archivia_lotto(
    lotto_id: str, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto and lotto.stato == StatoLotto.completato:
        # Il lotto vive gia' nella sua posizione definitiva su SharePoint
        # fin dalla creazione (Macchina Locale/Archivio/{anno}/{mese}/...):
        # "archivia" e' solo un cambio di stato, nessun file da spostare.
        lotto.stato = StatoLotto.archiviato
        lotto.archiviato_at = datetime.utcnow()
        db.commit()
    return RedirectResponse(url=_url_dettaglio_lotto(lotto_id, fase=6), status_code=302)


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
        log_elaborazione(db, elaborazione, "Esecuzione messa in pausa dall'operatore")
        db.commit()
        if elaborazione.nome_container:
            metti_in_pausa_container(elaborazione.nome_container)
    return RedirectResponse(url=_url_dettaglio_lotto(lotto_id, lotto), status_code=302)


@router.post("/lotti/{lotto_id}/elaborazione/riprendi")
def riprendi(
    lotto_id: str, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    elaborazione = lotto.elaborazione_attiva if lotto else None
    if elaborazione and elaborazione.richiesta_controllo == "pausa":
        if elaborazione.nome_container:
            riprendi_container(elaborazione.nome_container)
        elaborazione.richiesta_controllo = None
        log_elaborazione(db, elaborazione, "Esecuzione ripresa dall'operatore")
        db.commit()
    return RedirectResponse(url=_url_dettaglio_lotto(lotto_id, lotto), status_code=302)


@router.post("/lotti/{lotto_id}/elaborazione/annulla")
def annulla(
    lotto_id: str, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    elaborazione = lotto.elaborazione_attiva if lotto else None
    if elaborazione and elaborazione.stato == StatoElaborazione.in_corso:
        elaborazione.richiesta_controllo = "annulla"
        db.commit()
        if elaborazione.nome_container:
            riprendi_container(elaborazione.nome_container)
            annulla_container(elaborazione.nome_container)
    return RedirectResponse(url=_url_dettaglio_lotto(lotto_id, lotto), status_code=302)


@router.post("/lotti/{lotto_id}/riprova")
def riprova_lotto(
    lotto_id: str, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    """
    Rilancia la fase che ha interrotto il lotto (bottone "Riprova" sul
    banner di eccezione). La fase da riavviare si ricava dall'ultima
    Elaborazione in stato "errore" o "annullata" per questo lotto — non
    serve un campo dedicato sul lotto, e' gia' tutto tracciato li'.

    Prima di rilanciare, ripulisce gli eventuali dati parziali scritti dal
    tentativo interrotto (vedi pulisci_dati_parziali_fase in real_pipeline.py,
    condivisa con "Annulla"): senza questa pulizia, un retry rischierebbe
    di creare doppioni.
    """
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto is None or lotto.stato != StatoLotto.eccezione:
        return RedirectResponse(url=_url_dettaglio_lotto(lotto_id, lotto), status_code=302)

    ultima_interrotta = (
        db.query(Elaborazione)
        .filter(
            Elaborazione.lotto_id == lotto.id,
            Elaborazione.stato.in_([StatoElaborazione.errore, StatoElaborazione.annullata]),
        )
        .order_by(Elaborazione.started_at.desc())
        .first()
    )
    fase = ultima_interrotta.fase if ultima_interrotta else FaseElaborazione.preprocessing
    if ultima_interrotta:
        log_elaborazione(db, ultima_interrotta, "Esecuzione riavviata dall'operatore")

    lotto.note = None
    pulisci_dati_parziali_fase(db, lotto, fase)

    if fase == FaseElaborazione.preprocessing:
        lotto.stato = StatoLotto.preprocessing
        db.commit()
        background_tasks.add_task(avvia_preprocessing_reale, lotto.id)
        return RedirectResponse(url=_url_dettaglio_lotto(lotto_id, fase=3), status_code=302)

    if fase == FaseElaborazione.vllm:
        lotto.stato = StatoLotto.elaborazione_ocr
        db.commit()
        background_tasks.add_task(avvia_ocr_reale, lotto.id)
        return RedirectResponse(url=_url_dettaglio_lotto(lotto_id, fase=4), status_code=302)

    if fase == FaseElaborazione.difformita:
        lotto.stato = StatoLotto.analisi_difformita
        db.commit()
        background_tasks.add_task(avvia_difformita_reale, lotto.id)
        return RedirectResponse(url=_url_dettaglio_lotto(lotto_id, fase=5), status_code=302)

    return RedirectResponse(url=_url_dettaglio_lotto(lotto_id, lotto), status_code=302)


@router.post("/lotti/{lotto_id}/elimina")
def elimina_lotto(
    lotto_id: str, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
    """
    Bottone "Elimina" sul banner di eccezione, alternativa a "Riprova":
    invece di ripartire dalla stessa fase, butta via il lotto per intero
    (record DB con cascade su prescrizioni/elaborazioni/log/difformita,
    piu' i file media/SharePoint associati). Solo per lotti in eccezione —
    non e' un'azione da poter fare su un lotto sano o gia' completato.
    """
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto is None or lotto.stato != StatoLotto.eccezione:
        return RedirectResponse(url=_url_dettaglio_lotto(lotto_id, lotto), status_code=302)

    cartella_media = MEDIA_ROOT / "lotti" / str(lotto.id)
    if cartella_media.is_dir():
        try:
            shutil.rmtree(cartella_media)
        except OSError:
            pass  # il record DB viene comunque cancellato, i file restano da ripulire a mano

    if lotto.sp_lavoro_path:
        cartella_sp = radice_sharepoint() / lotto.sp_lavoro_path
        if cartella_sp.is_dir():
            try:
                shutil.rmtree(cartella_sp)
            except OSError:
                pass  # probabile lock di OneDrive in sync, non blocca l'eliminazione del record

    db.delete(lotto)
    db.commit()
    return RedirectResponse(url="/lotti", status_code=302)


# ============================================================
# Polling JSON
# ============================================================

@router.get("/lotti/{lotto_id}/stato")
def stato_lotto(lotto_id: str, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente)):
    lotto = db.query(LottoMensile).filter(LottoMensile.id == uuid.UUID(lotto_id)).first()
    if lotto is None:
        return JSONResponse({"errore": "lotto non trovato"}, status_code=404)

    elaborazione_attiva = lotto.elaborazione_attiva
    progresso_item = None
    in_pausa = False
    if elaborazione_attiva:
        log_ordinato = sorted(elaborazione_attiva.log, key=lambda l: l.timestamp)
        progresso_item = estrai_progresso_da_log(log_ordinato)
        in_pausa = elaborazione_attiva.richiesta_controllo == "pausa"

    log_righe = formatta_log_righe(_log_lotto(db, lotto.id))

    return {
        "id": str(lotto.id),
        "nome": lotto.nome,
        "stato": lotto.stato.value,
        "etichetta_stato": etichetta_stato(lotto.stato),
        "percentuale": percentuale_avanzamento(lotto.stato),
        "fase_attiva": elaborazione_attiva.fase.value if elaborazione_attiva else None,
        "messaggio_operatore": messaggio_fase_operatore(
            elaborazione_attiva.fase if elaborazione_attiva else None,
            in_pausa,
        ),
        "richiesta_controllo": elaborazione_attiva.richiesta_controllo if elaborazione_attiva else None,
        "progresso_item": progresso_item,
        "n_prescrizioni_totali": lotto.n_prescrizioni_totali,
        "n_barcode_undefined": lotto.n_barcode_undefined,
        "n_difformita_totali": lotto.n_difformita_totali,
        "log_righe": log_righe,
    }
