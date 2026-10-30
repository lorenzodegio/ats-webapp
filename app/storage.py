"""
File da mostrare nel browser: sempre sulla macchina dove gira uvicorn,
in ./media — non dentro i volumi Docker /dati.

Docker (se usato) scrive ancora in ./dati per la pipeline OCR; dopo ogni
fase copiamo PDF/PNG in media/lotti/<id>/pagine/ e il client li prende
solo da lì (route autenticata, non cartella pubblica).
"""
import os
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

APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()

# Backend dello storage permanente ("onedrive": comportamento storico,
# cartella OneDrive for Business sincronizzata; "local": cartella Windows
# fissa, pensata per il server di produzione senza sincronizzazione cloud).
# Default "local" in produzione (niente OneDrive sul server Windows),
# "onedrive" altrove — invariato per lo sviluppo su questa macchina.
STORAGE_BACKEND = os.environ.get(
    "STORAGE_BACKEND", "local" if APP_ENV == "production" else "onedrive"
).strip().lower()
# Cartella radice per il backend "local" — nessun default: va configurata
# esplicitamente, non indovinata.
STORAGE_LOCAL_DIR = os.environ.get("STORAGE_LOCAL_DIR", "").strip() or None

_CACHE_RADICE_SHAREPOINT = {}


class ErroreStorageNonValido(RuntimeError):
    """Il percorso di storage configurato non esiste o non e' accettabile.
    In produzione questo deve fermare l'operazione (o l'avvio dell'app),
    mai ricadere in silenzio su un'altra cartella — vedi il bug reale
    descritto sotto _risolvi_backend_onedrive()."""


def _risolvi_backend_onedrive() -> Path:
    """
    Backend "onedrive" (comportamento storico): in produzione la cartella
    OneDrive for Business sincronizzata sulla macchina ATS
    (Configurazione.sharepoint_base_path), altrimenti SHAREPOINT_FINTO
    (sviluppo, macchine senza OneDrive).

    Il percorso risolto viene messo in cache di processo SOLO una volta
    trovato valido: a quel punto non ha senso rileggerlo ad ogni chiamata
    (tab "Sistema" di Impostazioni e' sola lettura, quindi non cambia a
    server in esecuzione). Fuori produzione, se al momento della
    risoluzione la cartella OneDrive non e' ancora disponibile, si ricade
    su SHAREPOINT_FINTO senza bloccare (comportamento invariato, utile in
    sviluppo). In produzione (APP_ENV=production) questo fallback e'
    DISABILITATO apposta: solleva ErroreStorageNonValido invece di
    scrivere silenziosamente in locale — e' il meccanismo dietro un
    incidente reale gia' documentato (lotto DICEMBRE scritto per intero in
    sharepoint_finto/ perche' il server era partito prima che OneDrive
    montasse la cartella, scoperto solo a lotto finito).
    """
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

    if percorso is not None:
        _CACHE_RADICE_SHAREPOINT["percorso"] = percorso
        return percorso

    if APP_ENV == "production":
        raise ErroreStorageNonValido(
            "Backend storage 'onedrive': Configurazione.sharepoint_base_path "
            "non e' impostato o non punta a una cartella esistente. In "
            "produzione l'operazione si ferma qui invece di scrivere in "
            "sharepoint_finto/ — correggi il percorso in Impostazioni o "
            "passa a STORAGE_BACKEND=local."
        )
    return _CACHE_RADICE_SHAREPOINT.get("percorso") or SHAREPOINT_FINTO


def _risolvi_backend_locale() -> Path:
    """
    Backend "local": una cartella Windows fissa (STORAGE_LOCAL_DIR), senza
    nessuna dipendenza da OneDrive/SharePoint — il backend di default in
    produzione sul server Windows (niente sincronizzazione cloud li').
    """
    if not STORAGE_LOCAL_DIR:
        raise ErroreStorageNonValido(
            "STORAGE_BACKEND=local ma STORAGE_LOCAL_DIR non e' impostato nel .env."
        )
    percorso = Path(STORAGE_LOCAL_DIR)
    if not percorso.is_dir():
        if APP_ENV == "production":
            raise ErroreStorageNonValido(
                f"Backend storage 'local': la cartella configurata non esiste: {percorso}"
            )
        # Fuori produzione, comodita' per chi prova il backend 'local' in
        # sviluppo: la crea invece di bloccare.
        percorso.mkdir(parents=True, exist_ok=True)
    return percorso


def radice_sharepoint() -> Path:
    """
    Radice dello storage permanente dei file (PDF/PNG/Excel/JSON) — il nome
    resta quello storico per non dover toccare le decine di chiamate gia'
    esistenti in tutto il backend; delega al backend attivo (STORAGE_BACKEND):
      - "onedrive" (default fuori produzione): vedi _risolvi_backend_onedrive.
      - "local" (default in produzione): vedi _risolvi_backend_locale.
    Un futuro backend per Microsoft Graph API (OneDrive senza
    sincronizzazione locale) si aggiungerebbe qui con la stessa interfaccia
    (una funzione che ritorna/accetta percorsi, senza toccare i chiamanti).
    """
    if STORAGE_BACKEND == "local":
        return _risolvi_backend_locale()
    return _risolvi_backend_onedrive()


def verifica_storage_produzione() -> None:
    """
    Chiamata una sola volta all'avvio (app/main.py) quando APP_ENV=production:
    l'app si RIFIUTA DI PARTIRE se lo storage configurato non e' valido o
    punta a sharepoint_finto — invece di scoprirlo a meta' di un lotto reale
    (vedi il bug storico citato sopra). No-op fuori produzione.
    """
    if APP_ENV != "production":
        return
    percorso = radice_sharepoint().resolve()  # solleva ErroreStorageNonValido se non valido
    if percorso == SHAREPOINT_FINTO.resolve():
        raise ErroreStorageNonValido(
            "APP_ENV=production ma lo storage risolto punta a sharepoint_finto/ "
            "— controlla STORAGE_BACKEND/STORAGE_LOCAL_DIR o "
            "Configurazione.sharepoint_base_path prima di avviare l'app."
        )


def nome_file_sicuro(testo: str, default: str = "output") -> str:
    """Nome di file/cartella sicuro da un testo libero (es. lotto.nome)."""
    pulito = re.sub(r"[^a-zA-Z0-9_\-\s]", "", testo or "").strip().replace(" ", "_")
    return pulito or default


def nome_cartella_farmacia(nome_farmacia) -> str:
    """
    Nome di cartella sicuro per raggruppare le prescrizioni per farmacia
    (export ZIP, PRESCRIZIONI su SharePoint). Valori vuoti o gia'
    "FARMACIA_SCONOSCIUTA" (sentinella di merge_regione.py quando
    FARMACIA_ID non ha match in Regione, vedi DatiOcr.nome_farmacia)
    finiscono tutti nella stessa cartella "FARMACIA_SCONOSCIUTA".
    "OCR_INCERTO" e' qui solo per compatibilita' con JSON scritti da
    versioni precedenti di merge_regione.py, prima che il nome del
    sentinel fosse corretto (l'OCR non ha mai avuto un ruolo nella scelta
    del nome farmacia, "OCR_INCERTO" era un nome fuorviante ereditato dal
    pattern usato per le date).
    """
    if not nome_farmacia or str(nome_farmacia).strip() in ("FARMACIA_SCONOSCIUTA", "OCR_INCERTO"):
        return "FARMACIA_SCONOSCIUTA"
    return nome_file_sicuro(nome_farmacia, default="FARMACIA_SCONOSCIUTA")


def cartella_originale_lotto(lotto_id) -> Path:
    percorso = MEDIA_ROOT / "lotti" / str(lotto_id) / "originale"
    percorso.mkdir(parents=True, exist_ok=True)
    return percorso


def cartella_pagine_lotto(lotto_id) -> Path:
    percorso = MEDIA_ROOT / "lotti" / str(lotto_id) / "pagine"
    percorso.mkdir(parents=True, exist_ok=True)
    return percorso


def cartella_profilo_layout(farmacia_id) -> Path:
    """PDF di esempio + anteprima caricati in Impostazioni -> Prompt farmacie,
    usati solo per disegnare i rettangoli — stesso principio di
    cartella_originale_lotto(), non sincronizzati su SharePoint."""
    percorso = MEDIA_ROOT / "profili_layout" / str(farmacia_id)
    percorso.mkdir(parents=True, exist_ok=True)
    return percorso


def relativo_a_radice(percorso: Path) -> str:
    risolto = percorso.resolve()
    try:
        return str(risolto.relative_to(RADICE_PROGETTO.resolve())).replace("\\", "/")
    except ValueError:
        return str(risolto).replace("\\", "/")


def salva_pdf_caricato(lotto_id, nome_file: str, contenuto: bytes) -> str:
    """
    Salva un PDF caricato in originale/. Nome univoco garantito (aggiunge
    un suffisso numerico in caso di collisione): con il caricamento a
    cartella si possono ricevere più file in sequenza, anche con nomi
    ripetuti (es. due sottocartelle diverse con lo stesso "scan1.pdf") —
    senza questo, il secondo sovrascriverebbe in silenzio il primo.
    """
    cartella = cartella_originale_lotto(lotto_id)
    nome_pulito = Path(nome_file).name
    dest = cartella / nome_pulito
    contatore = 1
    while dest.exists():
        contatore += 1
        dest = cartella / f"{Path(nome_pulito).stem}_{contatore}{Path(nome_pulito).suffix}"
    dest.write_bytes(contenuto)
    return relativo_a_radice(dest)


def pdf_originali_lotto(lotto) -> list:
    """
    Tutti i PDF caricati come input originale del lotto — uno solo (PDF
    combinato multi-pagina) o molti (cartella di PDF già separati): il
    preprocessing (phase1_preprocess.py) itera gia' tutti i file trovati
    nella cartella di input, quindi non fa differenza per la pipeline.
    Cerca prima nella cartella media locale, poi come fallback nella
    cartella PRESCRIZIONI dello storage permanente.
    """
    cartella = MEDIA_ROOT / "lotti" / str(lotto.id) / "originale"
    if cartella.is_dir():
        trovati = sorted(cartella.glob("*.pdf"))
        if trovati:
            return trovati
    if lotto.sp_prescrizioni_path:
        sp = radice_sharepoint() / lotto.sp_prescrizioni_path
        if sp.is_dir():
            trovati = sorted(sp.glob("*.pdf"))
            if trovati:
                return trovati
    return []


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
