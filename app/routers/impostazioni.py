"""Router Impostazioni (solo admin): censimento farmacie, prompt farmacie."""
import csv
import io
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.auth import get_utente_corrente
from app.database import get_db
from app.models import CampoProfiloLayout, Configurazione, DatiOcr, Farmacia, ProfiloLayoutEtichetta, Utente
from app.storage import cartella_profilo_layout

router = APIRouter(tags=["impostazioni"])
templates = Jinja2Templates(directory="app/templates")
log = logging.getLogger(__name__)


def _richiede_admin(utente: Utente) -> Optional[RedirectResponse]:
    if not utente.is_admin:
        return RedirectResponse(url="/", status_code=302)
    return None


def _conta_prescrizioni(db: Session):
    righe = db.query(DatiOcr.nome_farmacia).filter(DatiOcr.nome_farmacia.isnot(None)).all()
    contatori = {}
    for (nome,) in righe:
        chiave = (nome or "").strip()
        if not chiave:
            continue
        contatori[chiave] = contatori.get(chiave, 0) + 1
    return contatori


def _dati_form(
    codice: str = "",
    nome: str = "",
    codice_regionale: str = "",
    indirizzo: str = "",
    comune: str = "",
    provincia: str = "",
    telefono: str = "",
    email: str = "",
    note: str = "",
    attiva: bool = True,
):
    return {
        "codice": codice,
        "nome": nome,
        "codice_regionale": codice_regionale,
        "indirizzo": indirizzo,
        "comune": comune,
        "provincia": provincia,
        "telefono": telefono,
        "email": email,
        "note": note,
        "attiva": attiva,
    }


@router.get("/impostazioni", response_class=HTMLResponse)
def pagina_impostazioni(
    request: Request,
    tab: str = "farmacie",
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco

    farmacie = db.query(Farmacia).order_by(Farmacia.attiva.desc(), Farmacia.nome.asc()).all()
    configurazioni = db.query(Configurazione).order_by(Configurazione.chiave.asc()).all()

    profili_per_farmacia = {}
    if tab == "prompt_farmacie":
        profili = db.query(ProfiloLayoutEtichetta).options(joinedload(ProfiloLayoutEtichetta.campi)).all()
        profili_per_farmacia = {str(p.farmacia_id): p for p in profili}

    return templates.TemplateResponse(
        "impostazioni.html",
        {
            "request": request,
            "utente": utente,
            "voce_attiva": "impostazioni",
            "tab": tab if tab in ("farmacie", "sistema", "prompt_farmacie") else "farmacie",
            "farmacie": farmacie,
            "contatori_prescrizioni": _conta_prescrizioni(db),
            "configurazioni": configurazioni,
            "profili_per_farmacia": profili_per_farmacia,
            "form_farmacia": _dati_form(),
            "farmacia_in_modifica": None,
            "errore": request.query_params.get("errore"),
            "ok": request.query_params.get("ok"),
        },
    )


@router.get("/impostazioni/farmacie/{farmacia_id}/modifica", response_class=HTMLResponse)
def form_modifica_farmacia(
    farmacia_id: str,
    request: Request,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco
    farmacia = db.query(Farmacia).filter(Farmacia.id == farmacia_id).first()
    if farmacia is None:
        return RedirectResponse(url="/impostazioni?errore=Farmacia+non+trovata", status_code=302)

    farmacie = db.query(Farmacia).order_by(Farmacia.attiva.desc(), Farmacia.nome.asc()).all()
    configurazioni = db.query(Configurazione).order_by(Configurazione.chiave.asc()).all()
    return templates.TemplateResponse(
        "impostazioni.html",
        {
            "request": request,
            "utente": utente,
            "voce_attiva": "impostazioni",
            "tab": "farmacie",
            "farmacie": farmacie,
            "contatori_prescrizioni": _conta_prescrizioni(db),
            "configurazioni": configurazioni,
            "form_farmacia": _dati_form(
                codice=farmacia.codice,
                nome=farmacia.nome,
                codice_regionale=farmacia.codice_regionale or "",
                indirizzo=farmacia.indirizzo or "",
                comune=farmacia.comune or "",
                provincia=farmacia.provincia or "",
                telefono=farmacia.telefono or "",
                email=farmacia.email or "",
                note=farmacia.note or "",
                attiva=farmacia.attiva,
            ),
            "farmacia_in_modifica": farmacia,
            "errore": None,
            "ok": None,
        },
    )


def _valida(codice: str, nome: str, provincia: str):
    codice = (codice or "").strip().upper()
    nome = (nome or "").strip()
    provincia = (provincia or "").strip().upper()
    if not codice:
        return None, "Il codice e' obbligatorio."
    if not nome:
        return None, "Il nome e' obbligatorio."
    if provincia and (len(provincia) < 2 or len(provincia) > 5):
        return None, "La provincia deve essere una sigla (es. VA, CO)."
    return {"codice": codice, "nome": nome, "provincia": provincia or None}, None


@router.post("/impostazioni/farmacie/{farmacia_id}/modifica")
def salva_farmacia(
    farmacia_id: str,
    codice: str = Form(""),
    nome: str = Form(""),
    codice_regionale: str = Form(""),
    indirizzo: str = Form(""),
    comune: str = Form(""),
    provincia: str = Form(""),
    telefono: str = Form(""),
    email: str = Form(""),
    note: str = Form(""),
    attiva: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco
    farmacia = db.query(Farmacia).filter(Farmacia.id == farmacia_id).first()
    if farmacia is None:
        return RedirectResponse(url="/impostazioni?errore=Farmacia+non+trovata", status_code=302)

    puliti, errore = _valida(codice, nome, provincia)
    if errore:
        return RedirectResponse(
            url=f"/impostazioni/farmacie/{farmacia_id}/modifica?errore={errore}",
            status_code=302,
        )
    doppione = (
        db.query(Farmacia)
        .filter(Farmacia.codice == puliti["codice"], Farmacia.id != farmacia.id)
        .first()
    )
    if doppione:
        return RedirectResponse(url="/impostazioni?errore=Codice+gia+in+uso", status_code=302)

    farmacia.codice = puliti["codice"]
    farmacia.nome = puliti["nome"]
    farmacia.codice_regionale = (codice_regionale or "").strip() or None
    farmacia.indirizzo = (indirizzo or "").strip() or None
    farmacia.comune = (comune or "").strip() or None
    farmacia.provincia = puliti["provincia"]
    farmacia.telefono = (telefono or "").strip() or None
    farmacia.email = (email or "").strip() or None
    farmacia.note = (note or "").strip() or None
    farmacia.attiva = attiva is not None
    farmacia.updated_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(url="/impostazioni?ok=modificata", status_code=302)


@router.post("/impostazioni/farmacie/{farmacia_id}/disattiva")
def disattiva_farmacia(
    farmacia_id: str,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco
    farmacia = db.query(Farmacia).filter(Farmacia.id == farmacia_id).first()
    if farmacia:
        farmacia.attiva = False
        farmacia.updated_at = datetime.utcnow()
        db.commit()
    return RedirectResponse(url="/impostazioni?ok=disattivata", status_code=302)


@router.post("/impostazioni/farmacie/{farmacia_id}/riattiva")
def riattiva_farmacia(
    farmacia_id: str,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco
    farmacia = db.query(Farmacia).filter(Farmacia.id == farmacia_id).first()
    if farmacia:
        farmacia.attiva = True
        farmacia.updated_at = datetime.utcnow()
        db.commit()
    return RedirectResponse(url="/impostazioni?ok=riattivata", status_code=302)


@router.get("/impostazioni/farmacie/export-csv")
def export_csv_farmacie(
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco
    farmacie = db.query(Farmacia).order_by(Farmacia.nome.asc()).all()
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([
        "codice", "nome", "codice_regionale", "indirizzo", "comune",
        "provincia", "telefono", "email", "attiva", "note",
    ])
    for f in farmacie:
        writer.writerow([
            f.codice, f.nome, f.codice_regionale or "", f.indirizzo or "",
            f.comune or "", f.provincia or "", f.telefono or "", f.email or "",
            "si" if f.attiva else "no", f.note or "",
        ])
    contenuto = buffer.getvalue().encode("utf-8-sig")
    return Response(
        content=contenuto,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="farmacie_ats.csv"'},
    )


# ---------------------------------------------------------------------------
# Prompt farmacie: profilo di layout etichetta per farmacia (dove si trova
# l'etichetta e i singoli campi, disegnati dall'operatore su un PDF di
# esempio — vedi profilo_layout_etichetta.py per come la pipeline li usa).
# ---------------------------------------------------------------------------

ESTENSIONI_PDF_VALIDE = {".pdf"}


def _esporta_profili_json(db: Session) -> None:
    """Riscrive dati/profili_layout.json subito dopo ogni modifica, cosi'
    e' sempre visibile per un test manuale — oltre alla scrittura che gia'
    avviene ad ogni avvio di lotto (vedi _scrivi_farmacie_per_pipeline in
    app/real_pipeline.py, stesso file di destinazione)."""
    from app.profili_layout_io import scrivi_json_pipeline
    from app.storage import DATI_DIR
    try:
        scrivi_json_pipeline(db, DATI_DIR / "profili_layout.json")
    except Exception as exc:
        log.warning(f"Impossibile riscrivere profili_layout.json: {exc}")


def _rendi_anteprima_pdf(pdf_path: Path, dest_png: Path, zoom: float = 2.0) -> None:
    """Renderizza la prima pagina del PDF di esempio in PNG per il disegno
    dei rettangoli — solo per l'anteprima in Impostazioni, zoom indipendente
    da quello usato dalla pipeline OCR vera (IMAGE_ZOOM in date_corrector.py):
    le coordinate salvate sono relative alla pagina (0.0-1.0), non dipendono
    da quale zoom e' stato usato per mostrarla."""
    import pymupdf
    doc = pymupdf.open(str(pdf_path))
    try:
        pagina = doc[0]
        pix = pagina.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        pix.save(str(dest_png))
    finally:
        doc.close()


@router.get("/impostazioni/prompt-farmacie/{farmacia_id}", response_class=HTMLResponse)
def pagina_prompt_farmacia(
    farmacia_id: str,
    request: Request,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco
    farmacia = db.query(Farmacia).filter(Farmacia.id == farmacia_id).first()
    if farmacia is None:
        return RedirectResponse(url="/impostazioni?tab=prompt_farmacie&errore=Farmacia+non+trovata", status_code=302)

    profilo = (
        db.query(ProfiloLayoutEtichetta)
        .options(joinedload(ProfiloLayoutEtichetta.campi))
        .filter(ProfiloLayoutEtichetta.farmacia_id == farmacia_id)
        .first()
    )
    campi_esistenti = [
        {
            "nome_campo": c.nome_campo,
            "parola_guida": c.parola_guida or "",
            "x1": c.rettangolo_x1, "y1": c.rettangolo_y1,
            "x2": c.rettangolo_x2, "y2": c.rettangolo_y2,
        }
        for c in (profilo.campi if profilo else [])
    ]
    return templates.TemplateResponse(
        "impostazioni_prompt_farmacia.html",
        {
            "request": request,
            "utente": utente,
            "voce_attiva": "impostazioni",
            "farmacia": farmacia,
            "profilo": profilo,
            "campi_esistenti": campi_esistenti,
            "ha_anteprima": bool(profilo and profilo.pdf_esempio_path),
            "errore": request.query_params.get("errore"),
            "ok": request.query_params.get("ok"),
        },
    )


@router.post("/impostazioni/prompt-farmacie/{farmacia_id}/carica-esempio")
def carica_esempio_prompt_farmacia(
    farmacia_id: str,
    file_pdf: UploadFile = File(...),
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco
    farmacia = db.query(Farmacia).filter(Farmacia.id == farmacia_id).first()
    if farmacia is None:
        return RedirectResponse(url="/impostazioni?tab=prompt_farmacie&errore=Farmacia+non+trovata", status_code=302)

    _, ext = os.path.splitext(file_pdf.filename or "")
    if ext.lower() not in ESTENSIONI_PDF_VALIDE:
        return RedirectResponse(
            url=f"/impostazioni/prompt-farmacie/{farmacia_id}?errore=Solo+file+PDF",
            status_code=302,
        )

    cartella = cartella_profilo_layout(farmacia_id)
    percorso_pdf = cartella / "esempio.pdf"
    percorso_pdf.write_bytes(file_pdf.file.read())

    percorso_png = cartella / "anteprima.png"
    try:
        _rendi_anteprima_pdf(percorso_pdf, percorso_png)
    except Exception as e:
        log.error(f"Errore rendering anteprima PDF profilo ({farmacia_id}): {e}")
        return RedirectResponse(
            url=f"/impostazioni/prompt-farmacie/{farmacia_id}?errore=PDF+non+leggibile",
            status_code=302,
        )

    profilo = db.query(ProfiloLayoutEtichetta).filter(ProfiloLayoutEtichetta.farmacia_id == farmacia_id).first()
    if profilo is None:
        profilo = ProfiloLayoutEtichetta(farmacia_id=farmacia_id)
        db.add(profilo)
    profilo.pdf_esempio_path = str(percorso_pdf)
    profilo.updated_at = datetime.utcnow()
    db.commit()

    return RedirectResponse(url=f"/impostazioni/prompt-farmacie/{farmacia_id}?ok=esempio+caricato", status_code=302)


@router.get("/impostazioni/prompt-farmacie/{farmacia_id}/anteprima.png")
def anteprima_prompt_farmacia(
    farmacia_id: str,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco
    percorso_png = cartella_profilo_layout(farmacia_id) / "anteprima.png"
    if not percorso_png.is_file():
        return Response(status_code=404)
    return FileResponse(percorso_png, media_type="image/png")


@router.post("/impostazioni/prompt-farmacie/{farmacia_id}/salva")
def salva_prompt_farmacia(
    farmacia_id: str,
    parole_guida_presenza: str = Form(""),
    rettangolo_etichetta_x1: str = Form(""),
    rettangolo_etichetta_y1: str = Form(""),
    rettangolo_etichetta_x2: str = Form(""),
    rettangolo_etichetta_y2: str = Form(""),
    campo_nome: List[str] = Form([]),
    campo_parola_guida: List[str] = Form([]),
    campo_x1: List[str] = Form([]),
    campo_y1: List[str] = Form([]),
    campo_x2: List[str] = Form([]),
    campo_y2: List[str] = Form([]),
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco
    farmacia = db.query(Farmacia).filter(Farmacia.id == farmacia_id).first()
    if farmacia is None:
        return RedirectResponse(url="/impostazioni?tab=prompt_farmacie&errore=Farmacia+non+trovata", status_code=302)

    def _float_o_none(v: str) -> Optional[float]:
        v = (v or "").strip()
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    rett_et = (
        _float_o_none(rettangolo_etichetta_x1), _float_o_none(rettangolo_etichetta_y1),
        _float_o_none(rettangolo_etichetta_x2), _float_o_none(rettangolo_etichetta_y2),
    )
    if any(v is None for v in rett_et):
        rett_et = (None, None, None, None)

    righe_campi = []
    for i, nome_campo in enumerate(campo_nome):
        nome_campo = (nome_campo or "").strip()
        if not nome_campo:
            continue
        parola = (campo_parola_guida[i] if i < len(campo_parola_guida) else "").strip() or None
        x1 = _float_o_none(campo_x1[i] if i < len(campo_x1) else "")
        y1 = _float_o_none(campo_y1[i] if i < len(campo_y1) else "")
        x2 = _float_o_none(campo_x2[i] if i < len(campo_x2) else "")
        y2 = _float_o_none(campo_y2[i] if i < len(campo_y2) else "")
        rettangolo_completo = all(v is not None for v in (x1, y1, x2, y2))
        if not parola and not rettangolo_completo:
            return RedirectResponse(
                url=f"/impostazioni/prompt-farmacie/{farmacia_id}"
                    f"?errore=Il+campo+'{nome_campo}'+ha+bisogno+di+una+parola-guida+o+di+un+rettangolo+disegnato",
                status_code=302,
            )
        righe_campi.append({
            "ordine": i, "nome_campo": nome_campo, "parola_guida": parola,
            "rettangolo_x1": x1 if rettangolo_completo else None, "rettangolo_y1": y1 if rettangolo_completo else None,
            "rettangolo_x2": x2 if rettangolo_completo else None, "rettangolo_y2": y2 if rettangolo_completo else None,
        })

    parole = [p.strip() for p in (parole_guida_presenza or "").split(",") if p.strip()]

    profilo = db.query(ProfiloLayoutEtichetta).filter(ProfiloLayoutEtichetta.farmacia_id == farmacia_id).first()
    if profilo is None:
        profilo = ProfiloLayoutEtichetta(farmacia_id=farmacia_id)
        db.add(profilo)

    profilo.parole_guida_presenza = parole or None
    (
        profilo.rettangolo_etichetta_x1, profilo.rettangolo_etichetta_y1,
        profilo.rettangolo_etichetta_x2, profilo.rettangolo_etichetta_y2,
    ) = rett_et
    profilo.updated_at = datetime.utcnow()
    db.flush()

    # Sostituzione completa dei campi: piu' semplice e meno incline a errori
    # di un merge riga-per-riga, dato che la form reinvia sempre l'elenco
    # completo (niente edit parziale lato client in questa prima versione).
    db.query(CampoProfiloLayout).filter(CampoProfiloLayout.profilo_id == profilo.id).delete()
    for riga in righe_campi:
        db.add(CampoProfiloLayout(profilo_id=profilo.id, **riga))

    db.commit()
    _esporta_profili_json(db)

    return RedirectResponse(url=f"/impostazioni/prompt-farmacie/{farmacia_id}?ok=profilo+salvato", status_code=302)


@router.post("/impostazioni/prompt-farmacie/{farmacia_id}/elimina")
def elimina_prompt_farmacia(
    farmacia_id: str,
    db: Session = Depends(get_db),
    utente: Utente = Depends(get_utente_corrente),
):
    blocco = _richiede_admin(utente)
    if blocco:
        return blocco
    profilo = db.query(ProfiloLayoutEtichetta).filter(ProfiloLayoutEtichetta.farmacia_id == farmacia_id).first()
    if profilo:
        db.delete(profilo)
        db.commit()
        _esporta_profili_json(db)
    return RedirectResponse(url="/impostazioni?tab=prompt_farmacie&ok=profilo+eliminato", status_code=302)
