"""
Router lotti mensili: /lotti/nuovo /lotti /lotti/{id} + azioni del ciclo
di vita (revisione barcode, avvio fasi successive, gestione difformita,
completamento, archiviazione).
"""
import os
import uuid
import io
import zipfile
import re
import pandas as pd
from datetime import datetime, date

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.auth import get_utente_corrente
from app.models import (
    LottoMensile, StatoLotto, CaricamentoFile, TipoCaricamento, StatoCaricamento,
    Prescrizione, StatoBarcode, Difformita, StatoDifformita, Elaborazione,
    StatoElaborazione, FaseElaborazione, Utente, StatoRevisionePrescrizione, DatiOcr,
    GravitaDifformita,
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
        if backend_pipeline_e_reale(db):
            successo = scrivi_excel_finale_reale(lotto.id)
            if not successo:
                return RedirectResponse(
                    url=f"/lotti/{lotto_id}?errore=Scrittura+Excel+finale+fallita,+vedi+i+log", status_code=302
                )
            db.refresh(lotto)
        else:
            nome_file = f"OUTPUT_CANNABIS_{lotto.nome}.xlsx"
            lotto.excel_output_filename = nome_file
            
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            
            headers = ["BARCODE", "nome_farmacia", "codice_fiscale", "codice_esenzione", "forma_farmaceutica", "totale_prescrizione"]
            ws.append(headers)
            for p in lotto.prescrizioni:
                nf = p.dati_ocr.nome_farmacia if (p.dati_ocr and p.dati_ocr.nome_farmacia) else "FARMACIA DI PROVA"
                cf = p.dati_ocr.codice_fiscale if (p.dati_ocr and p.dati_ocr.codice_fiscale) else "RSSMRA80A01H501U"
                ce = p.dati_ocr.codice_esenzione if (p.dati_ocr and p.dati_ocr.codice_esenzione) else "048"
                ff = p.dati_ocr.forma_farmaceutica if (p.dati_ocr and p.dati_ocr.forma_farmaceutica) else "olio in flacone"
                tot = p.dati_ocr.totale_prescrizione if (p.dati_ocr and p.dati_ocr.totale_prescrizione) else 100.0
                ws.append([p.barcode, nf, cf, ce, ff, tot])
                
            percorso_dir = os.path.join(FAKE_SP_ROOT, PERCORSO_CARTELLA_OUTPUT_RECENTI)
            os.makedirs(percorso_dir, exist_ok=True)
            wb.save(os.path.join(percorso_dir, nome_file))
            
        lotto.stato = StatoLotto.completato
        lotto.completato_at = datetime.utcnow()
        db.commit()
    return RedirectResponse(url=f"/lotti/{lotto_id}", status_code=302)


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

    percorso_excel = os.path.join(FAKE_SP_ROOT, "LAVORO/OUTPUT", lotto.excel_output_filename)
    if not os.path.exists(percorso_excel):
        return JSONResponse({"errore": f"File Excel principale non trovato a {percorso_excel}"}, status_code=404)

    try:
        df = pd.read_excel(percorso_excel)
    except Exception as e:
        return JSONResponse({"errore": f"Errore nella lettura del file Excel principale: {e}"}, status_code=500)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        prescrizioni = lotto.prescrizioni
        gruppi_farmacia = {}
        for p in prescrizioni:
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
                if p.sp_pdf_path:
                    percorso_pdf = os.path.join(FAKE_SP_ROOT, p.sp_pdf_path)
                    if os.path.exists(percorso_pdf):
                        nome_pdf = os.path.basename(p.sp_pdf_path)
                        zip_file.write(percorso_pdf, arcname=f"{nome_cartella}/{nome_pdf}")
            
            barcodes_gruppo = [p.barcode for p in prescs if p.barcode]
            barcodes_set = set(barcodes_gruppo)
            
            colonna_barcode = None
            for col in df.columns:
                if str(col).upper() == "BARCODE":
                    colonna_barcode = col
                    break
            
            if colonna_barcode is not None:
                df_farmacia = df[df[colonna_barcode].astype(str).str.strip().isin(barcodes_set)]
            else:
                colonna_farmacia = None
                for col in df.columns:
                    if "FARMACIA" in str(col).upper():
                        colonna_farmacia = col
                        break
                if colonna_farmacia is not None:
                    df_farmacia = df[df[colonna_farmacia].astype(str).str.contains(nome_cartella.replace("_", " "), case=False, na=False)]
                else:
                    df_farmacia = df.head(0)

            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                df_farmacia.to_excel(writer, index=False, sheet_name="Prescrizioni")
            
            zip_file.writestr(
                f"{nome_cartella}/Prescrizioni_{nome_cartella}.xlsx",
                excel_buffer.getvalue()
            )

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
    return {"stato": "ok"}


@router.get("/lotti/{lotto_id}/output-excel")
def visualizza_output_excel(
    lotto_id: str, db: Session = Depends(get_db), utente: Utente = Depends(get_utente_corrente),
):
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
