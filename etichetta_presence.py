"""
etichetta_presence.py

Rileva se l'etichetta della farmacia è presente sopra il modulo
CODICE/NUMERO prestampato, confrontando strutturalmente il crop
con un template di riferimento "vuoto".

Pipeline:
  1. Template matching: trova la posizione del modulo CODICE/NUMERO
     sulla pagina (robusto a disallineamenti di scansione).
  2. SSIM: confronta la regione trovata con il template vuoto.
     - SSIM alto  -> nessuna etichetta (struttura pulita, come il template)
     - SSIM basso -> etichetta presente (testo/tabelle sopra la struttura)
  3. Se SSIM è in una fascia intermedia (ambiguo, es. timbro parziale),
     EasyOCR fa da tie-breaker cercando pattern strutturati (prezzo,
     percentuale THC, barcode).

Richiede: opencv-python, scikit-image, easyocr (solo per il fallback)
    pip install opencv-python scikit-image easyocr --break-system-packages
"""

import re
from dataclasses import dataclass

import cv2
import numpy as np
from skimage.metrics import structural_similarity as ssim

PRICE_PATTERN = re.compile(r"\d+[.,]\d{2}")
THC_PATTERN = re.compile(r"\d{1,2}\s*mg")

# Stessi ancoraggi già usati per l'estrazione dei campi etichetta (vedi
# MANDATORY ANCHOR CHECK in date_corrector.py: PROMPT_ETICHETTA_FARMACIA_TEMPLATE) —
# se l'etichetta è vera, di solito compare almeno uno di questi elementi;
# un modulo prestampato vuoto non ne ha nessuno.
NOME_PATTERN = re.compile(r"\bsig\.?\b|\bdott\.?\b|\bdott\.?ssa\b", re.IGNORECASE)
AVVERTENZE_PATTERN = re.compile(r"avvertenz", re.IGNORECASE)
DATA_PREP_PATTERN = re.compile(r"\bprep\.?\b|preparazion", re.IGNORECASE)
DATA_SCADENZA_PATTERN = re.compile(r"scaden|utilizzare\s*entro", re.IGNORECASE)

# Soglie da calibrare su un campione reale (vedi note in fondo al file)
SSIM_EMPTY_THRESHOLD = 0.85   # sopra questo valore: quasi certamente vuoto
SSIM_PRESENT_THRESHOLD = 0.55  # sotto questo valore: quasi certamente etichetta presente
# tra le due soglie: zona grigia -> serve il fallback OCR


@dataclass
class EtichettaResult:
    presente: bool
    confidence: str  # "alta" | "media" | "bassa"
    ssim_score: float
    match_location: tuple
    dettaglio: str


def find_template_region(page_gray: np.ndarray, template_gray: np.ndarray, search_region_frac=(0.5, 1.0)):
    """Trova la posizione del modulo CODICE/NUMERO tramite template matching.

    search_region_frac: (y_start_frac, y_end_frac) - limita la ricerca a una
    fascia verticale della pagina (default: metà inferiore), per evitare falsi
    match con altri blocchi grafici simili più in alto (es. codice fiscale,
    codice esenzione, sigla provincia/codice ASL).
    """
    page_h = page_gray.shape[0]
    y_start = int(page_h * search_region_frac[0])
    y_end = int(page_h * search_region_frac[1])
    search_area = page_gray[y_start:y_end, :]

    result = cv2.matchTemplate(search_area, template_gray, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    h, w = template_gray.shape
    x, y = max_loc
    y += y_start  # riporta la coordinata y al sistema di riferimento della pagina intera
    return (x, y, w, h), max_val


def ocr_fallback(region_bgr: np.ndarray, reader) -> bool:
    """Usato solo se SSIM è ambiguo/basso. Cerca gli STESSI ancoraggi già
    usati per l'estrazione dei campi etichetta (prezzi, THC, nomi
    paziente/medico, avvertenze, data preparazione, data scadenza) —
    se l'etichetta è vera, di solito compare almeno uno di questi
    elementi; un modulo prestampato vuoto non ne ha nessuno.

    PRICE_PATTERN volutamente ESCLUSO da questo controllo (anche se
    resta definito sopra, per eventuale uso altrove): il totale della
    prescrizione (es. "156,15") è sempre fisicamente vicino al box
    CODICE/NUMERO su OGNI ricetta, etichetta presente o no — verificato
    concretamente su un caso reale (Farmacia Tili, Vismara) dove quel
    numero, non l'etichetta, faceva scattare "presente" per errore.
    Non è un segnale specifico dell'etichetta, quindi non va usato qui.
    """
    results = reader.readtext(region_bgr)
    for _, text, conf in results:
        if conf < 0.4:
            continue
        if (THC_PATTERN.search(text)
                or NOME_PATTERN.search(text) or AVVERTENZE_PATTERN.search(text)
                or DATA_PREP_PATTERN.search(text) or DATA_SCADENZA_PATTERN.search(text)):
            return True
    return False


def detect_etichetta_from_images(page_gray: np.ndarray, template_gray: np.ndarray, reader=None,
                                  search_region_frac=(0.5, 1.0), page_bgr: np.ndarray = None) -> EtichettaResult:
    """Versione core: lavora direttamente su array numpy già in memoria.

    Usata sia da detect_etichetta() (che carica da file) sia da ocr_cannabis.py
    (che passa direttamente l'immagine già renderizzata dal PDF, senza rileggerla da disco).

    page_bgr: immagine a colori originale (stesso frame di page_gray). Necessaria
    SOLO se si vuole il fallback OCR nella zona grigia — se non fornita e la
    confidenza cade nella zona ambigua, il risultato resta "bassa" invece di "media".
    """
    (x, y, w, h), match_score = find_template_region(page_gray, template_gray, search_region_frac)
    region_gray = page_gray[y:y + h, x:x + w]

    # se il template è troppo diverso in dimensioni per un match diretto,
    # ridimensiona la regione trovata alle dimensioni del template
    if region_gray.shape != template_gray.shape:
        region_gray = cv2.resize(region_gray, (template_gray.shape[1], template_gray.shape[0]))

    score = ssim(region_gray, template_gray)

    if score >= SSIM_EMPTY_THRESHOLD:
        return EtichettaResult(False, "alta", score, (x, y, w, h), "struttura combacia col template vuoto")

    if score <= SSIM_PRESENT_THRESHOLD:
        # SSIM decisamente basso di solito significa "etichetta presente",
        # ma non è affidabile al 100%: alcune farmacie hanno un modulo
        # prestampato vuoto che assomiglia strutturalmente poco al template
        # di calibrazione (font/box diversi), producendo SSIM sempre basso
        # anche senza nessuna etichetta vera incollata sopra — osservato
        # sistematicamente su una farmacia specifica (Farmacia Tili: SSIM
        # sempre tra 0.25 e 0.51 su 30/30 ricette, MAI un'etichetta vera).
        # Stessa verifica OCR già usata per la zona grigia, come ulteriore
        # conferma prima di dichiarare "presente" con alta confidenza.
        #
        # Cerca in TUTTA la fascia inferiore della pagina, non solo nel
        # margine attorno al crop stretto — un tentativo precedente aveva
        # ristretto la ricerca a un margine attorno a (x,y,w,h), ma
        # un'etichetta VERA è spesso fisicamente più grande del box
        # CODICE/NUMERO usato come riferimento (viene incollata sopra e
        # intorno) — verificato su tre farmacie CON profilo dedicato
        # (Pomi, Comunale N.2, Ramella) che finivano scartate come
        # "assente" perché il controllo cercava in un'area troppo
        # piccola per contenerle. Il vero problema che aveva motivato la
        # fascia stretta (Vismara/Tili: il totale prescrizione, sempre
        # vicino al box, veniva scambiato per un prezzo etichetta) è
        # già risolto togliendo PRICE_PATTERN dai segnali OCR sopra —
        # non serve più restringere anche la zona di ricerca.
        if reader is not None and page_bgr is not None:
            page_h = page_gray.shape[0]
            y_start = int(page_h * search_region_frac[0])
            y_end = int(page_h * search_region_frac[1])
            region_bgr = page_bgr[y_start:y_end, :]
            ocr_hit = ocr_fallback(region_bgr, reader)
            if ocr_hit:
                return EtichettaResult(True, "alta", score, (x, y, w, h), "struttura molto diversa dal template, confermata da pattern OCR")
            return EtichettaResult(False, "media", score, (x, y, w, h), "struttura diversa dal template ma NESSUN pattern OCR trovato — probabile modulo vuoto di layout diverso dal template di calibrazione, non un'etichetta vera")
        return EtichettaResult(True, "alta", score, (x, y, w, h), "struttura molto diversa dal template (etichetta presente)")

    # zona grigia: serve conferma OCR.
    # Cerca in TUTTA la fascia inferiore della pagina — stesso motivo del
    # ramo sopra: un margine stretto attorno al box CODICE/NUMERO non
    # basta a coprire un'etichetta vera fisicamente più grande di quel
    # box. PRICE_PATTERN già rimosso dai segnali OCR (sopra) evita il
    # falso positivo che aveva motivato la fascia stretta in origine.
    if reader is not None and page_bgr is not None:
        page_h = page_gray.shape[0]
        y_start = int(page_h * search_region_frac[0])
        y_end = int(page_h * search_region_frac[1])
        region_bgr = page_bgr[y_start:y_end, :]
        ocr_hit = ocr_fallback(region_bgr, reader)
        if ocr_hit:
            return EtichettaResult(True, "media", score, (x, y, w, h), "SSIM ambiguo, ma pattern OCR trovati nella fascia inferiore")
        return EtichettaResult(False, "media", score, (x, y, w, h), "SSIM ambiguo, nessun pattern OCR trovato nella fascia inferiore")

    return EtichettaResult(True, "bassa", score, (x, y, w, h), "SSIM ambiguo, nessun fallback OCR disponibile")


def detect_etichetta(page_path: str, template_path: str, reader=None, search_region_frac=(0.5, 1.0)) -> EtichettaResult:
    """Wrapper da riga di comando: carica le immagini da file e richiama la funzione core."""
    page = cv2.imread(page_path)
    template = cv2.imread(template_path)

    page_gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

    return detect_etichetta_from_images(
        page_gray, template_gray, reader=reader,
        search_region_frac=search_region_frac, page_bgr=page,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("page", help="immagine della ricetta (PNG ad alta risoluzione)")
    parser.add_argument("--template", default="template_vuoto.png",
                         help="crop di riferimento senza etichetta")
    parser.add_argument("--use-ocr-fallback", action="store_true")
    parser.add_argument("--search-y-start", type=float, default=0.5,
                         help="inizio fascia di ricerca verticale, come frazione dell'altezza pagina (default 0.5 = metà)")
    parser.add_argument("--search-y-end", type=float, default=1.0,
                         help="fine fascia di ricerca verticale, come frazione dell'altezza pagina (default 1.0 = fondo pagina)")
    args = parser.parse_args()

    reader = None
    if args.use_ocr_fallback:
        import easyocr
        reader = easyocr.Reader(["it"], gpu=True)

    result = detect_etichetta(
        args.page, args.template, reader,
        search_region_frac=(args.search_y_start, args.search_y_end),
    )
    print(result)

# NOTE PER LA CALIBRAZIONE:
# - SSIM_EMPTY_THRESHOLD e SSIM_PRESENT_THRESHOLD vanno tarate su un piccolo
#   campione misto (alcune ricette con etichetta, alcune senza, alcune con
#   solo timbro obliquo) prima di fidarsi in produzione.
# - Il template vuoto deve provenire dalla stessa versione del modulo prestampato:
#   se ATS Insubria usa più formati di modulo, serve un template per formato.
# - Se il match_score del template matching è basso (es. < 0.6), la regione
#   trovata potrebbe essere sbagliata (pagina ruotata/scalata diversamente):
#   in quel caso conviene loggare un warning invece di fidarsi ciecamente dell'SSIM.
