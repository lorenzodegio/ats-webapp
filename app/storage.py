"""
File da mostrare nel browser: sempre sulla macchina dove gira uvicorn,
in ./media — non dentro i volumi Docker /dati.

Docker (se usato) scrive ancora in ./dati per la pipeline OCR; dopo ogni
fase copiamo PDF/PNG in media/lotti/<id>/pagine/ e il client li prende
solo da lì (route autenticata, non cartella pubblica).
"""
import re
import shutil
from pathlib import Path

RADICE_PROGETTO = Path(__file__).resolve().parent.parent
if (Path("/workspace") / "app" / "main.py").is_file():
    RADICE_PROGETTO = Path("/workspace")

MEDIA_ROOT = RADICE_PROGETTO / "media"
DATI_DIR = RADICE_PROGETTO / "dati"
SHAREPOINT_FINTO = RADICE_PROGETTO / "sharepoint_finto"

RICETTE_RAW = DATI_DIR / "ricette_raw"
RICETTE_STAGING_IMAGES = DATI_DIR / "ricette_staging" / "images"
RICETTE_STAGING_PDFS = DATI_DIR / "ricette_staging" / "pdfs"
RICETTE = DATI_DIR / "ricette"

_CACHE_RADICE_SHAREPOINT = {}


def radice_sharepoint() -> Path:
    """
    Radice dello storage "SharePoint": in produzione la cartella OneDrive
    for Business sincronizzata sulla macchina ATS (Configurazione.sharepoint_base_path),
    altrimenti SHAREPOINT_FINTO (sviluppo, macchine senza OneDrive).

    Letta una sola volta e messa in cache di processo: oggi non esiste
    ancora un modo per cambiare sharepoint_base_path mentre il server e'
    in esecuzione (tab "Sistema" di Impostazioni e' sola lettura), quindi
    non c'e' bisogno di rileggerla ad ogni chiamata — solo di non fallire
    se la tabella Configurazione non e' ancora popolata (DB appena creato,
    prima di seed_admin.py).
    """
    if "percorso" not in _CACHE_RADICE_SHAREPOINT:
        percorso = None
        try:
            from app.database import SessionLocal
            from app.models import Configurazione
            db = SessionLocal()
            try:
                riga = db.query(Configurazione).filter(Configurazione.chiave == "sharepoint_base_path").first()
                if riga and riga.valore and riga.valore.strip():
                    candidato = Path(riga.valore.strip())
                    if candidato.is_dir():
                        percorso = candidato
            finally:
                db.close()
        except Exception:
            percorso = None
        _CACHE_RADICE_SHAREPOINT["percorso"] = percorso or SHAREPOINT_FINTO
    return _CACHE_RADICE_SHAREPOINT["percorso"]


def nome_file_sicuro(testo: str, default: str = "output") -> str:
    """Nome di file/cartella sicuro da un testo libero (es. lotto.nome)."""
    pulito = re.sub(r"[^a-zA-Z0-9_\-\s]", "", testo or "").strip().replace(" ", "_")
    return pulito or default


def cartella_originale_lotto(lotto_id) -> Path:
    percorso = MEDIA_ROOT / "lotti" / str(lotto_id) / "originale"
    percorso.mkdir(parents=True, exist_ok=True)
    return percorso


def cartella_pagine_lotto(lotto_id) -> Path:
    percorso = MEDIA_ROOT / "lotti" / str(lotto_id) / "pagine"
    percorso.mkdir(parents=True, exist_ok=True)
    return percorso


def relativo_a_radice(percorso: Path) -> str:
    risolto = percorso.resolve()
    try:
        return str(risolto.relative_to(RADICE_PROGETTO.resolve())).replace("\\", "/")
    except ValueError:
        return str(risolto).replace("\\", "/")


def salva_pdf_caricato(lotto_id, nome_file: str, contenuto: bytes) -> str:
    dest = cartella_originale_lotto(lotto_id) / Path(nome_file).name
    dest.write_bytes(contenuto)
    return relativo_a_radice(dest)


def pdf_originale_lotto(lotto) -> Path:
    cartella = MEDIA_ROOT / "lotti" / str(lotto.id) / "originale"
    if cartella.is_dir():
        trovati = sorted(cartella.glob("*.pdf"))
        if trovati:
            return trovati[0]
    if lotto.sp_prescrizioni_path:
        sp = radice_sharepoint() / lotto.sp_prescrizioni_path
        if sp.is_dir():
            trovati = sorted(sp.glob("*.pdf"))
            if trovati:
                return trovati[0]
    return None


def pubblica_file(lotto_id, sorgente: Path, nome: str = None):
    if sorgente is None or not Path(sorgente).is_file():
        return None
    sorgente = Path(sorgente)
    dest = cartella_pagine_lotto(lotto_id) / (nome or sorgente.name)
    if sorgente.resolve() != dest.resolve():
        shutil.copy2(sorgente, dest)
    return relativo_a_radice(dest)


def rinomina_pagina_media(lotto_id, vecchio_stem: str, nuovo_stem: str) -> None:
    cartella = cartella_pagine_lotto(lotto_id)
    for ext in (".pdf", ".png"):
        origine = cartella / f"{vecchio_stem}{ext}"
        if origine.is_file():
            origine.replace(cartella / f"{nuovo_stem}{ext}")


def pubblica_output_preprocessing(lotto_id) -> None:
    """Copia su media tutto ciò che Docker ha lasciato in ./dati."""
    for cartella in (RICETTE, RICETTE_STAGING_PDFS):
        if not cartella.is_dir():
            continue
        for pdf in cartella.glob("*.pdf"):
            pubblica_file(lotto_id, pdf)
    if RICETTE_STAGING_IMAGES.is_dir():
        for png in RICETTE_STAGING_IMAGES.glob("*.png"):
            pubblica_file(lotto_id, png)


def _radici_consentite():
    return (
        MEDIA_ROOT.resolve(),
        DATI_DIR.resolve(),
        SHAREPOINT_FINTO.resolve(),
        radice_sharepoint().resolve(),
    )


def sotto_cartella_consentita(percorso: Path) -> bool:
    risolto = percorso.resolve()
    for radice in _radici_consentite():
        try:
            risolto.relative_to(radice)
            return True
        except ValueError:
            continue
    return False


def _candidato_esistente(rel_or_abs: str):
    grezzo = (rel_or_abs or "").replace("\\", "/").strip()
    if not grezzo:
        return None
    path = Path(grezzo)
    tentativi = []
    if path.is_absolute():
        tentativi.append(path)
    else:
        tentativi.append(RADICE_PROGETTO / grezzo)
        tentativi.append(Path.cwd() / grezzo)
        tentativi.append(radice_sharepoint() / grezzo)
        tentativi.append(SHAREPOINT_FINTO / grezzo)
    visti = set()
    for candidato in tentativi:
        risolto = candidato.resolve()
        if risolto in visti:
            continue
        visti.add(risolto)
        if risolto.is_file() and sotto_cartella_consentita(risolto):
            return risolto
    return None


def _cerca_in_media(lotto_id, stem: str, estensioni):
    if not lotto_id or not stem:
        return None
    cartella = MEDIA_ROOT / "lotti" / str(lotto_id) / "pagine"
    if not cartella.is_dir():
        return None
    for ext in estensioni:
        candidato = (cartella / f"{stem}{ext}").resolve()
        if candidato.is_file() and sotto_cartella_consentita(candidato):
            return candidato
    return None


def percorso_pdf_prescrizione(presc) -> str:
    stem = None
    if presc.sp_pdf_path:
        stem = Path(presc.sp_pdf_path.replace("\\", "/")).stem
    elif presc.barcode:
        stem = presc.barcode
    trovato = _cerca_in_media(presc.lotto_id, stem, (".pdf",))
    if trovato:
        return str(trovato)

    rels = []
    if presc.sp_pdf_path:
        rel = presc.sp_pdf_path.replace("\\", "/")
        rels.extend([rel, Path(rel).name, f"dati/ricette_staging/pdfs/{Path(rel).stem}.pdf",
                     f"dati/ricette/{Path(rel).stem}.pdf"])
    if presc.sp_png_path:
        png = Path(presc.sp_png_path.replace("\\", "/"))
        rels.extend([str(png.with_suffix(".pdf")), f"dati/ricette_staging/pdfs/{png.stem}.pdf"])
    if presc.barcode:
        rels.extend([f"dati/ricette/{presc.barcode}.pdf", f"dati/ricette_staging/pdfs/{presc.barcode}.pdf"])

    for rel in rels:
        trovato = _candidato_esistente(rel)
        if trovato:
            if presc.lotto_id:
                copiato = pubblica_file(presc.lotto_id, trovato)
                if copiato:
                    return str((RADICE_PROGETTO / copiato).resolve())
            return str(trovato)

    if stem:
        for cartella in (RICETTE_STAGING_PDFS, RICETTE, DATI_DIR / "ricette_staging", DATI_DIR / "ricette"):
            if not cartella.is_dir():
                continue
            for file_pdf in cartella.rglob(f"{stem}.pdf"):
                if file_pdf.is_file() and sotto_cartella_consentita(file_pdf):
                    if presc.lotto_id:
                        copiato = pubblica_file(presc.lotto_id, file_pdf)
                        if copiato:
                            return str((RADICE_PROGETTO / copiato).resolve())
                    return str(file_pdf.resolve())
    return None


def percorso_png_prescrizione(presc) -> str:
    stem = None
    if presc.sp_png_path:
        stem = Path(presc.sp_png_path.replace("\\", "/")).stem
    elif presc.sp_pdf_path:
        stem = Path(presc.sp_pdf_path.replace("\\", "/")).stem
    elif presc.barcode:
        stem = presc.barcode
    trovato = _cerca_in_media(presc.lotto_id, stem, (".png",))
    if trovato:
        return str(trovato)
    if presc.sp_png_path:
        trovato = _candidato_esistente(presc.sp_png_path)
        if trovato:
            if presc.lotto_id:
                copiato = pubblica_file(presc.lotto_id, trovato)
                if copiato:
                    return str((RADICE_PROGETTO / copiato).resolve())
            return str(trovato)
    if stem:
        for cartella in (RICETTE_STAGING_IMAGES, RICETTE):
            candidato = cartella / f"{stem}.png"
            if candidato.is_file():
                if presc.lotto_id:
                    copiato = pubblica_file(presc.lotto_id, candidato)
                    if copiato:
                        return str((RADICE_PROGETTO / copiato).resolve())
                return str(candidato.resolve())
    return None


def sniff_tipo_file(percorso: str):
    try:
        with open(percorso, "rb") as handle:
            testa = handle.read(8)
    except OSError:
        return None
    if testa.startswith(b"%PDF"):
        return "pdf"
    if testa.startswith(b"\x89PNG"):
        return "png"
    if testa[:2] == b"\xff\xd8":
        return "jpeg"
    return "altro"


def risolvi_anteprima(presc):
    pdf = percorso_pdf_prescrizione(presc)
    if pdf:
        tipo = sniff_tipo_file(pdf)
        if tipo == "pdf":
            return pdf, "pdf"
        if tipo in ("png", "jpeg"):
            return pdf, tipo
    png = percorso_png_prescrizione(presc)
    if png:
        tipo = sniff_tipo_file(png) or "png"
        return png, tipo
    return None, None
