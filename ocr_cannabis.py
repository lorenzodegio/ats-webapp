"""
OCR Cannabis ATS Insubria — Pipeline Qwen2.5-VL
Autori: Alessandro Marchinu, Francesco Milani
Versione: 3.0

Pipeline:
1. PDF -> immagine ad alta risoluzione
2. Qwen2.5-VL -> estrazione dati in JSON
3. Sanity check e normalizzazione
4. Output JSON compatibile con Phase 4 del tutor

Requisiti:
    pip install pymupdf pillow ollama openpyxl pandas

Modello richiesto:
    ollama pull qwen2.5vl:7b

Utilizzo:
    python ocr_cannabis.py                    # elabora tutte le ricette in ./ricette/
    python ocr_cannabis.py --input /path/pdf  # cartella personalizzata
    python ocr_cannabis.py --test             # test su prima ricetta trovata

CHANGELOG v3.0:
    - Fix data_emissione: ricerca ristretta al solo timbro DATA SPEDIZIONE,
      esclude esplicitamente Cod.Reg. e altri numeri vicini al timbro medico
    - Fix data_etichetta_preparazione: istruzioni rinforzate per scartare
      il numero progressivo prima della data
"""

import ollama
import fitz
import base64
import json
import re
import sys
import argparse
import logging
import cv2
import numpy as np
from pathlib import Path
from datetime import datetime

from etichetta_presence import detect_etichetta_from_images

# ─── Configurazione ────────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).parent
RICETTE_DIR = BASE_DIR / "ricette"
OUTPUT_DIR  = BASE_DIR / "output"
LOG_DIR     = BASE_DIR / "logs"

# Percorso del file Excel Regione — usato SOLO per recuperare FARMACIA_ID
# via barcode quando nome_farmacia non e' stato letto dall'OCR (timbro
# assente/illeggibile). None di default: se non impostato (es. lanciando
# ocr_cannabis.py da solo, senza passare da pipeline.py), il recupero da
# Regione viene semplicemente saltato.
PERCORSO_REGIONE = None
_cache_barcode_a_farmacia_id = None  # {barcode: "CO0310 - TILI & C."}, caricata una sola volta

OLLAMA_MODEL = "qwen2.5vl:32b"
_date_corrector = None
IMAGE_ZOOM   = 3.0
NUM_GPU      = 1
NUM_CTX      = 16384

# Template per il rilevamento automatico presenza/assenza etichetta (crop del
# modulo CODICE/NUMERO vuoto, generato dalla stessa pipeline zoom-3x — vedi
# batch_test_etichetta.py per come generarlo/validarlo su un campione).
TEMPLATE_ETICHETTA_PATH = BASE_DIR / "crop_vuoto_v2.png"
_template_etichetta_gray = None

# Fallback OCR per la zona grigia del rilevamento etichetta (SSIM ambiguo):
# cerca pattern strutturati (prezzo/THC/barcode) invece di basarsi solo sul
# testo generico. Disattivabile se EasyOCR non è installato o pesa troppo
# in produzione — in quel caso i casi ambigui restano confidenza "bassa" e
# il prompt resta quello base, senza istruzione dinamica.
USA_FALLBACK_OCR_ETICHETTA = True
_easyocr_reader = None

# ─── Logging ───────────────────────────────────────────────────────────────────

LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_DIR / f"ocr_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
            encoding="utf-8"
        )
    ]
)
log = logging.getLogger(__name__)

# Import DateCorrector — opzionale
try:
    from date_corrector import DateCorrector
    DATE_CORRECTOR_DISPONIBILE = True
except ImportError:
    DATE_CORRECTOR_DISPONIBILE = False
    log.warning('date_corrector.py non trovato — correzione TrOCR disabilitata')

# ─── Prompt ────────────────────────────────────────────────────────────────────

PROMPT = """You are an expert medical document data extractor.
Your task: extract structured data from an Italian medical prescription image and return ONLY a JSON object.

CRITICAL OUTPUT RULE:
Return ONLY the raw JSON object.
NO markdown. NO backticks. NO ```json. NO explanations. NO comments.
Start your response with { and end with }.

DOCUMENT LAYOUT:
- TOP LEFT: patient name (COGNOME E NOME DELL'ASSISTITO)
- TOP RIGHT: two barcodes, then tax code grid (CODICE FISCALE)
- LEFT AREA: exemption code (CODICE ESENZIONE) often handwritten — may be
  inside the printed box OR handwritten ANYWHERE in the upper-left region,
  including just below "REGIONE LOMBARDIA"
- CENTER: prescription text (PRESCRIZIONE)
- BOTTOM CENTER: DATE field (6 digits GGMMAA)
- BOTTOM LEFT: pharmacy label with prices and preparation date
- BOTTOM RIGHT: doctor stamp box ABOVE the pharmacy stamp box
  (Cod., pharmacy name, DATA SPEDIZIONE, progressive number)

PHARMACY LABEL — TWO POSSIBLE LAYOUTS:
The pharmacy label can appear in TWO different formats. Identify which
one is present before extracting label fields:

LAYOUT A — Standard white label (most pharmacies):
  Row 1: "Dott. [doctor]    Sig. [patient]"
  Drug details, quantities
  "Prep. [progressive] [date]" and "UTILIZZARE ENTRO [date]"
  Price column on the right: S / O / AS / R / IV / EUR rows
  Warning text about doping and driving

LAYOUT B — Blue-bordered label (Farmacia Comunale 2 / CMS):
  Header: "Farmacia Comunale 2, Via Verdi 40 - Cassano Magnago(VA)"
  TWO separate blue boxes side by side:
  - LEFT box: "PREP N°[num]/[yy] SCAD. [date]" on the same row,
    drug description, "SI ASSUME ... mg THC e ... mg CBD PRO/DIE",
    "PREZZO [substance price] O.P. [x]EUR S [x]EUR C [x]EUR IVA 10% [x]",
    "TOT. [total]",
    "PZ. [patient initials/code]   DR. [doctor name]"
  - RIGHT box: "Avvertenze:" with colored warning text lines
    ("Conservare lontano da fonti di luce e calore",
     "Tenere fuori dalla portata dei bambini",
     "Soggetto alla disciplina del DPR 309/90",
     "Contiene sostanze dopanti L.376/00 S8 Cannabinoidi")

FIELD EXTRACTION RULES:

nome_cognome_assistito:
  Source: top left field "COGNOME E NOME DELL'ASSISTITO".
  May be a full name OR initials/alphanumeric code (e.g. "-485TDBA",
  "485TDBA") when privacy shortening is used. Extract as written,
  removing any leading dash.
  If completely empty -> return ""

barcode:
  Source: top right, TWO separate barcodes.
  First starts with J (remove J). Second starts with Y (remove Y).
  Concatenate both without spaces.
  Example: "J03024" + "Y0055946024" -> "030240055946024"

codice_fiscale:
  Source: top right area, below barcodes.
  May be INSIDE the printed grid boxes OR printed as a plain string
  just above/below the grid (e.g. "_NLIDNL72A12Z129C" — remove any
  leading underscore or dash).
  Structure: EXACTLY 16 chars LLLLLLNNLNNLNNNL (L=letter N=number).
  Remove all spaces, underscores and dashes.
  Positional disambiguation:
    Pos 1-6=letters, 7-8=numbers, 9=letter, 10-11=numbers, 12=letter, 13-15=numbers, 16=letter
    If letter position has digit: 1->I/L, 0->O/D, 8->B, 6->G/C, 5->S, 2->Z, 4->A
    If number position has letter: I/L->1, O/D->0, B->8, G/C->6, S->5, Z->2, A->4
    Position 9 (birth month) allowed ONLY: A B C D E H L M P R S T

codice_esenzione:
  =====================================================================
  EXTENDED SEARCH — the code is often HANDWRITTEN in LARGE letters
  and is NOT reliably inside its printed grid box. Search THE ENTIRE
  TOP HALF of the document, not just the upper-left quadrant:
  1. Inside the printed "CODICE ESENZIONE" box on the left (its usual
     position, but often it is NOT here)
  2. Handwritten ABOVE or BELOW that box
  3. Handwritten below "REGIONE LOMBARDIA" text (large spaced letters)
  4. Near "NON ESENTE" text
  5. Overlapping the printed form lines
  6. Anywhere else in the header area: near the patient name field,
     near the barcodes, near the tax code grid, in the margins —
     doctors write it wherever there is free space
  7. It may also appear alone, away from any of the printed labels
     above, as a standalone handwritten 2-3 letter/digit group

  SCAN CAREFULLY: check EVERY handwritten group of 2-3 characters
  anywhere in the top half of the page, not only near the printed box.
  TDL is often written in large letters with spaces: "T D L" -> return "TDL"

  Valid values ONLY: 048, 046, 019, 005, 020, TDL, L99, E30
  Corrections: TDI->TDL, 48->048
  TDL handwritten variants: T looks like 1/I/F, D looks like O/0/B,
  L looks like I/1/7
  If a 2-3 letter handwritten group resembles TDL even approximately,
  REGARDLESS OF WHERE ON THE PAGE it appears -> return "TDL"
  If nothing valid found -> return ""
  =====================================================================

codice_atc:
  Source: prescription text, after the word "ATC". Expected: N02BG10.
  OCR tolerance: N02BG10R or similar (1-2 different chars) -> return "N02BG10"
  If "ATC" not present but "N02BG10" string appears standalone -> return "N02BG10"
  If not found -> return ""

testo_prescrizione:
  Source: PRESCRIZIONE area center-left. Extract all text as written.

metodo_estrattivo_olio:
  Source: prescription text.
  Extract ONLY if one of these proper names appears:
    Ramella, Calvi, SIFAP, SICAM, Romano, Hazecamp, Cannazza
  Generic descriptions (e.g. "estrazione oleosa") -> return ""

forma_farmaceutica:
  Map to exactly one of these strings:
    "olio in flacone" if text contains: olio, MCT, ml, gocce, estrazione oleosa,
                       contagocce, estratto vegetale liquido
    "capsule" if text contains: cps, capsule, opercoli
    "cartine" if text contains: cartine, bustine, filtri, vapo
  No match -> return ""

data_prescrizione:
  Source: field "DATA" bottom center, 6 digits GGMMAA.
  ATTENTION: the field may contain a leading dash/hyphen before the
  digits (e.g. "-XXXXXX"). IGNORE any leading dash and extract only
  the 6 digits.
  Convert: first 2=day, digits 3-4=month, last 2=year (add "20").
  Format transformation ONLY (placeholder, NOT a real value to copy):
  "DDMMYY" -> "DD/MM/20YY"
  Format: GG/MM/AAAA.
  =====================================================================
  ANTI-HALLUCINATION RULE: read the ACTUAL digits printed/stamped on
  THIS specific document, one by one. Do NOT default to any date you
  may have seen as an example or in a previous document. Never output
  the literal placeholder text "DDMMYY", "XXXXXX", or similar — these
  are format templates shown for illustration only, not real values.
  If the digits are not clearly readable -> return "OCR_INCERTO"
  instead of guessing or echoing the placeholder.
  Guessing a plausible-looking date is a SEVERE ERROR.
  =====================================================================
  If unclear -> "OCR_INCERTO"

data_emissione:
  =====================================================================
  CRITICAL FIELD
  =====================================================================
  Source: ONLY the 6-digit number printed or stamped INSIDE the box
  labeled "DATA SPEDIZIONE" or "TIMBRO STRUTTURA EROGANTE".
  This box is in the BOTTOM-RIGHT area, BELOW the doctor stamp box.
  The 6 digits may be printed in dot-matrix/stamp style, sometimes
  inside small boxes, sometimes followed by the progressive number
  on the next line (e.g. "DDMMYY" then a separate progressive number
  below — take ONLY the date, NOT the progressive).
  NOTE: different pharmacies use different stamp fonts — read the
  digits carefully one by one, do not assume based on similar dates
  seen elsewhere in the document.
  Format transformation ONLY (placeholder, NOT a real value to copy):
  "DDMMYY" -> "DD/MM/20YY"

  ANTI-HALLUCINATION RULE: read the ACTUAL digits stamped on THIS
  specific document. Never output the literal placeholder text
  "DDMMYY", "XXXXXX", or similar — these are format templates, not
  real values. Do NOT default to any date seen as an example,
  in a previous document, or elsewhere on this same page (e.g. do not
  reuse the data_prescrizione value here). If not clearly readable ->
  "OCR_INCERTO". Guessing is a SEVERE ERROR.

  DO NOT use:
  - "Cod.Reg." numbers or "Cod.Fisc" from the doctor stamp
  - Phone numbers
  - The pharmacy progressive number (separate line/field)
  - Pharmacy code (e.g. "Cod. 506")
  If not clearly readable in this specific box -> "OCR_INCERTO"
  =====================================================================

timbro_medico and firma_medico:
  =====================================================================
  MAXIMALLY SIMPLE RULE — you do NOT need to identify WHAT the text is,
  WHO it belongs to, or WHETHER it looks like a proper doctor stamp.
  Look at the right-hand "TIMBRO E FIRMA DEL MEDICO" area as a whole:

  If this area contains ANY printed text, handwritten text, ink mark,
  stamp, scribble, or overlapping content of ANY kind — even if it is
  unclear, partial, looks like it might belong to the pharmacy stamp
  instead of the doctor, doesn't resemble a name, or doesn't match any
  expected format — treat it as timbro_medico = true. You do not need
  to read or understand what it says; its mere presence is enough.

  Only return false if this area is COMPLETELY BLANK white space, with
  absolutely nothing printed, written, or stamped in it.

  firma_medico ALWAYS equals timbro_medico.



data_etichetta_preparazione:
  =====================================================================
  MANDATORY ANCHOR CHECK — do this BEFORE answering. Verify that the
  literal text "Prep." / "Preparazione" (or, for the blue layout,
  "PREP N°") is ACTUALLY VISIBLE somewhere on this page. This is not
  optional: an empty pre-printed CODICE/NUMERO grid can visually
  resemble "a label area" without an actual label pasted on it — do
  not treat that resemblance as evidence the field exists.

  If you cannot find this literal anchor text anywhere -> return "".
  This is the CORRECT answer when no label is pasted on the page, and
  is common on some prescriptions. Do NOT substitute another date from
  elsewhere on the page as a fallback (data_prescrizione's center
  "DATA" box, or data_emissione's "DATA SPEDIZIONE" pharmacy stamp are
  DIFFERENT fields in DIFFERENT areas — never borrow either of them
  here, even when you cannot find "Prep." text).

  If the anchor text IS found but the date itself is only partially
  readable (e.g. smudged year), prefer "OCR_INCERTO" over guessing.

  TWO POSSIBLE FORMATS once the anchor above is confirmed present:

  LAYOUT A (white label): field "Prep." or "Prep del"
  Structure: [progressive number] [date]
  Example (format transformation only, NOT a real value to copy):
  "Prep. [progressive]  DD/MM/YY" -> extract "DD/MM/20YY"
  The progressive number (4-6 digits, no separators) must be DISCARDED.

  LAYOUT B (blue label): field "PREP N°[num]/[yy] SCAD. [date]"
  Here "PREP N°36/25" is the preparation NUMBER (36) and YEAR (25),
  NOT a date. In this layout the preparation date may be handwritten
  near the top of the label (e.g. a small handwritten date) — if a
  handwritten date is visible near PREP, use it.
  If no readable preparation date exists in layout B -> "OCR_INCERTO"

  OUTPUT VALIDATION: the output MUST be a COMPLETE date in GG/MM/AAAA
  format (exactly 10 characters). Partial dates like "05/25" or "36/25"
  are INVALID — if you cannot form a complete valid date -> return
  "OCR_INCERTO" instead. Never output the preparation number as a date.
  =====================================================================

etichetta_data_scadenza:
  =====================================================================
  THIS FIELD IS FREQUENTLY LEFT EMPTY BY MISTAKE, especially because
  it sits close to several OTHER date fields on the document (Prep.
  date, data_emissione, data_prescrizione) and gets overlooked. If the
  label is present, actively check for it — do not skip it by default.

  LAYOUT A: field "UTILIZZARE ENTRO [date]" — usually on the same row
  as, or right below, the "Prep. [num] del [date]" field, near the
  price table (S/O/R/U/IV/€ column). Extract the date that follows
  "UTILIZZARE ENTRO", NOT the "Prep. ... del ..." date (that one goes
  in data_etichetta_preparazione instead — they are two DIFFERENT
  dates on the same label, both must be extracted separately).
  LAYOUT B: field "SCAD. [date]" on the PREP row (e.g. "SCAD. DD/MM/YY", format only)
  Format GG/MM/AA -> GG/MM/AAAA.
  Only return "" if the label is genuinely absent or this specific
  date is not present/readable after actively looking for it.
  =====================================================================

etichetta_avvertenze:
  =====================================================================
  THIS FIELD IS FREQUENTLY LEFT EMPTY BY MISTAKE — READ CAREFULLY.
  The warning text is usually SMALL and located near a pictogram
  (e.g. a car/driving icon, a trash bin icon) or in a small dedicated
  box on the label. It is easy to overlook, but if a label is present
  at all, some warning text is USUALLY there too — actively look for
  it, do not default to "" just because it's small or partial.

  LAYOUT A (white label): warning text near the price table or near a
  pictogram — usually touches on THESE THEMES (do NOT copy any exact
  wording below, these are topic hints only, never text to reproduce):
  - driving/vehicle-use warning (contraindicated while driving)
  - doping warning (therapeutic use vs. sports doping test positivity)
  - storage instructions (temperature limit, refrigeration)
  - daily dose reminder near the pictogram (a THC/CBD mg figure)
  - sports-activity warning
  Transcribe the ACTUAL wording printed on THIS specific label for
  whichever of these themes appear — in the label's own words. If NONE
  of these themes appear anywhere near a pictogram/price table on THIS
  image, leave the field empty rather than reconstructing a stock phrase.

  LAYOUT B (blue label): the SEPARATE blue box titled "Avvertenze:" —
  extract the warning lines actually printed there (themes: storage
  away from light/heat, keep out of reach of children, doping
  substance disclosure with a legal reference code). Transcribe the
  REAL wording on THIS label, do not reconstruct a stock phrase. Do
  NOT skip layout B warnings
  just because they are on colored background — they are valid warnings.

  Only return "" if, after actively searching near the pictogram/price
  area, there is truly no warning text visible (or the label itself is
  absent).
  =====================================================================

etichetta_nome_cognome_paziente:
  LAYOUT A: after "Sig." or "Sig.ra" in the white label.
  LAYOUT B: after "PZ." in the blue label (e.g. "PZ. 485TDBA" ->
  "485TDBA"). The patient may be identified by initials/alphanumeric
  code instead of full name — extract it as written, removing dots.
  Do NOT use patient name from top of prescription.
  Absent in label -> ""

etichetta_nome_cognome_medico:
  LAYOUT A: preceded by "Dott." or "Dr." or "Dott.ssa" in the white label.
  LAYOUT B: after "DR." at the bottom of the blue label.
  If the label field is absent or unreadable, use the doctor stamp name
  ONLY as last resort.
  Extract full name including title when present.
  Absent everywhere -> ""

PRICE EXTRACTION — TWO LAYOUTS:

  LAYOUT A (white label, price column on the right):
    S or Sost -> etichetta_prezzo_sost  (HIGHEST value, usually ~48)
    O or On   -> etichetta_prezzo_on   (usually ~33)
    AS        -> IGNORE
    R or Rec  -> etichetta_prezzo_rec  (small ~1.53)
    U         -> IGNORE
    IV or IVA -> etichetta_prezzo_iva  (~8)
    EUR or T or Tot -> etichetta_prezzo_tot (~97)
    FARMACIA RAMELLA only: sum OP + OI + OS -> etichetta_prezzo_on

  LAYOUT B (blue label, prices in a single row):
    "PREZZO [x] O.P. [x]EUR S [x]EUR C [x]EUR IVA 10% [x]" and "TOT. [x]"
    Mapping:
    - PREZZO value (first number after PREZZO) -> etichetta_prezzo_sost
    - O.P. value -> etichetta_prezzo_on
    - S value -> IGNORE (stampa/spese)
    - C value -> etichetta_prezzo_rec
    - IVA value -> etichetta_prezzo_iva
    - TOT. value -> etichetta_prezzo_tot

  VALIDATION: etichetta_prezzo_sost MUST be the highest among
  sost/on/rec/iva. If not -> re-read and reassign.
  Decimal: always period not comma. "48,48" -> "48.48". Remove EUR.

  =====================================================================
  CRITICAL ANTI-HALLUCINATION RULE (applies especially to LAYOUT B):
  In layout B prices are often HANDWRITTEN and difficult to read.
  NEVER return the typical printed values of other pharmacies
  (48.48, 33.74, 5.00, 1.53, 8.88, 97.63) unless you can CLEARLY
  and DISTINCTLY see those exact digits written on THIS label.
  Copying typical values from memory is a SEVERE ERROR.
  If a price is handwritten and not clearly readable -> "OCR_INCERTO"
  It is ALWAYS better to return "OCR_INCERTO" than to guess.
  =====================================================================
  Label absent -> ""

totale_prescrizione:
  Source: handwritten value bottom right, in field GALEN/DIR.CHAR/ALTRO.
  This is DIFFERENT from etichetta_prezzo_tot (different area).
  Use period as decimal separator. Unclear -> "OCR_INCERTO"

THC:
  Source: prescription text ONLY.
  Extract NUMBER ONLY in mg: "10mg di THC" -> "10", "SI ASSUME 10 mg di THC" -> "10"
  Note: "TDH" in prescription text is an OCR error for "THC".
  Do NOT extract percentages (e.g. 15%, 22%).
  Do NOT extract mg/ml concentrations from the drug description line
  (e.g. "10 mg/ml THC") — prefer the daily dose if both are present
  (e.g. "SI ASSUME 10 mg di THC ... pro/die" -> "10").
  Do NOT extract CBD values.
  Not found -> ""

nome_farmacia:
  Source: pharmacy stamp bottom right or pharmacy label header.
  Map to closest from this list:
    Farmacia Tili Snc
    Farmacia Di Lora Srl
    Farmacia Pomi di dr. Collivasone A. & C. Snc
    Farmacia Ramella dott.ri G. e A. Sas
    Farmacia Mazzucchelli F. & C. Snc
    Farmacia Peroni dr Antonio E. & C. Sas
    Farmacia Comunale N.2
    Farmacia Stefini & C Sas
    Farmacia Introini dr. Paolo & C. Sas
    Farmacia Di Crenna
    Farmacia Ponti
  Specific corrections:
    "FARMACIA POMI SNC DI AVIGNO" or "FARMACIA DI AVIGNO" -> "Farmacia Pomi di dr. Collivasone A. & C. Snc"
    "FARMACIA RAMELLA" -> "Farmacia Ramella dott.ri G. e A. Sas"
    "FARMACIA INTROINI" -> "Farmacia Introini dr. Paolo & C. Sas"
    "FARMACIA MAZZUCCHELLI" -> "Farmacia Mazzucchelli F. & C. Snc"
    "FARMACIA PERONI" -> "Farmacia Peroni dr Antonio E. & C. Sas"
    "Farmacia Comunale 2" or "FARMACIA COMUNALE 2" or "M.S. SpA - Farmacia Comunale" -> "Farmacia Comunale N.2"
    "FARMACIA PILI" or "FARMACIA MILI" -> "Farmacia Tili Snc"
  Not mappable -> return name as read. Not present -> "FARMACIA NON RICONOSCIUTA"

{
  "nome_cognome_assistito": "",
  "barcode": "",
  "codice_fiscale": "",
  "codice_esenzione": "",
  "codice_atc": "",
  "testo_prescrizione": "",
  "metodo_estrattivo_olio": "",
  "forma_farmaceutica": "",
  "data_emissione": "",
  "timbro_medico": false,
  "firma_medico": false,
  "data_etichetta_preparazione": "",
  "data_prescrizione": "",
  "etichetta_avvertenze": "",
  "etichetta_data_scadenza": "",
  "etichetta_nome_cognome_medico": "",
  "etichetta_nome_cognome_paziente": "",
  "etichetta_prezzo_sost": "",
  "etichetta_prezzo_on": "",
  "etichetta_prezzo_rec": "",
  "etichetta_prezzo_iva": "",
  "etichetta_prezzo_tot": "",
  "totale_prescrizione": "",
  "THC": "",
  "nome_farmacia": ""
}"""

# ─── Prompt suddivisi in gruppi ────────────────────────────────────────────────
# L'estrazione è divisa in 4 chiamate più piccole invece di una sola richiesta
# con tutti i 25 campi: meno campi per chiamata riduce il rischio che il
# modello ne "salti" qualcuno (osservato empiricamente con il prompt unico).
# NON velocizza (ogni chiamata rielabora comunque l'immagine intera), è un
# cambiamento pensato per la qualità dell'estrazione, non per i tempi.

CONTESTO_CONDIVISO = """You are an expert medical document data extractor.
Your task: extract structured data from an Italian medical prescription image and return ONLY a JSON object.

CRITICAL OUTPUT RULE:
Return ONLY the raw JSON object.
NO markdown. NO backticks. NO ```json. NO explanations. NO comments.
Start your response with { and end with }.

DOCUMENT LAYOUT:
- TOP LEFT: patient name (COGNOME E NOME DELL'ASSISTITO)
- TOP RIGHT: two barcodes, then tax code grid (CODICE FISCALE)
- LEFT AREA: exemption code (CODICE ESENZIONE) often handwritten — may be
  inside the printed box OR handwritten ANYWHERE in the upper-left region,
  including just below "REGIONE LOMBARDIA"
- CENTER: prescription text (PRESCRIZIONE)
- BOTTOM CENTER: DATE field (6 digits GGMMAA)
- BOTTOM LEFT: pharmacy label with prices and preparation date
- BOTTOM RIGHT: doctor stamp box ABOVE the pharmacy stamp box
  (Cod., pharmacy name, DATA SPEDIZIONE, progressive number)

PHARMACY LABEL — TWO POSSIBLE LAYOUTS:
The pharmacy label can appear in TWO different formats. Identify which
one is present before extracting label fields:

LAYOUT A — Standard white label (most pharmacies):
  Row 1: "Dott. [doctor]    Sig. [patient]"
  Drug details, quantities
  "Prep. [progressive] [date]" and "UTILIZZARE ENTRO [date]"
  Price column on the right: S / O / AS / R / IV / EUR rows
  Warning text about doping and driving

LAYOUT B — Blue-bordered label (Farmacia Comunale 2 / CMS):
  Header: "Farmacia Comunale 2, Via Verdi 40 - Cassano Magnago(VA)"
  TWO separate blue boxes side by side:
  - LEFT box: "PREP N°[num]/[yy] SCAD. [date]" on the same row,
    drug description, "SI ASSUME ... mg THC e ... mg CBD PRO/DIE",
    "PREZZO [substance price] O.P. [x]EUR S [x]EUR C [x]EUR IVA 10% [x]",
    "TOT. [total]",
    "PZ. [patient initials/code]   DR. [doctor name]"
  - RIGHT box: "Avvertenze:" with colored warning text lines
    ("Conservare lontano da fonti di luce e calore",
     "Tenere fuori dalla portata dei bambini",
     "Soggetto alla disciplina del DPR 309/90",
     "Contiene sostanze dopanti L.376/00 S8 Cannabinoidi")

DATE FORMAT REMINDER: all dates on this document are Italian format,
DAY FIRST (GG/MM/AAAA or GGMMAA), never month-first like US format.
When converting digits to a date, the FIRST two digits are always the
DAY, the NEXT two are always the MONTH — do not swap them even if the
resulting day/month combination would also look valid the other way
around (e.g. digits "0408" must become day=04, month=08 -> "04/08",
NOT day=08, month=04 -> "08/04").

YEAR WARNING: this specific document batch is from 2025. There is a
known tendency to misread the year as "2023" even when "2025" is what
is actually printed/stamped — do NOT default to 2023. Read the last
two digits of the year carefully, one at a time, and double-check
before answering: if you find yourself about to write "2023", stop
and re-examine the actual digits on the document instead of trusting
your first impression.

FIELD EXTRACTION RULES — extract ONLY the fields listed below and
return a JSON object with EXACTLY those keys, nothing else.

"""

PROMPT_GRUPPO_CRITICO = CONTESTO_CONDIVISO + """codice_fiscale:
  Source: top right area, below barcodes.
  May be INSIDE the printed grid boxes OR printed as a plain string
  just above/below the grid (e.g. "_NLIDNL72A12Z129C" — remove any
  leading underscore or dash).
  Structure: EXACTLY 16 chars LLLLLLNNLNNLNNNL (L=letter N=number).
  Remove all spaces, underscores and dashes.
  Positional disambiguation:
    Pos 1-6=letters, 7-8=numbers, 9=letter, 10-11=numbers, 12=letter, 13-15=numbers, 16=letter
    If letter position has digit: 1->I/L, 0->O/D, 8->B, 6->G/C, 5->S, 2->Z, 4->A
    If number position has letter: I/L->1, O/D->0, B->8, G/C->6, S->5, Z->2, A->4
    Position 9 (birth month) allowed ONLY: A B C D E H L M P R S T
  READ EVERY ONE OF THE 16 BOXES INDIVIDUALLY, left to right, one at a
  time — do not skip repeated-looking letters (e.g. two "B"s in a row
  are common and both must be read, not collapsed into one).

  MANDATORY SELF-CHECK before answering: count the characters in your
  extracted codice_fiscale. If the count is NOT exactly 16, you have
  made an error — go back to the image, count the physical boxes in
  the grid one by one (there are always 16), and re-read each one
  individually until your extracted string has exactly 16 characters.
  Do not output a string shorter or longer than 16 characters.

codice_esenzione:
  =====================================================================
  EXTENDED SEARCH — the code is often HANDWRITTEN in LARGE letters
  and is NOT reliably inside its printed grid box. Search THE ENTIRE
  TOP HALF of the document, not just the upper-left quadrant:
  1. Inside the printed "CODICE ESENZIONE" box on the left (its usual
     position, but often it is NOT here)
  2. Handwritten ABOVE or BELOW that box
  3. Handwritten below "REGIONE LOMBARDIA" text (large spaced letters)
  4. Near "NON ESENTE" text
  5. Overlapping the printed form lines
  6. Anywhere else in the header area: near the patient name field,
     near the barcodes, near the tax code grid, in the margins —
     doctors write it wherever there is free space
  7. It may also appear alone, away from any of the printed labels
     above, as a standalone handwritten 2-3 letter/digit group

  SCAN CAREFULLY: check EVERY handwritten group of 2-3 characters
  anywhere in the top half of the page, not only near the printed box.
  TDL is often written in large letters with spaces: "T D L" -> return "TDL"

  IMPORTANT — HATCHED/CROSSED-OUT EXTRA BOXES: the printed box for this
  code often has MORE cells than needed (e.g. 6 empty cells for a
  3-letter code). The EXTRA unused cells are frequently marked with
  diagonal hatching, cross-hatch pattern, or "#"-like marks to show
  they are simply not used — this is a NORMAL form convention, NOT an
  indication that the code itself is invalid, crossed out, or should
  be ignored. As soon as you identify ONE of the valid values below
  written or filled in ANYWHERE in the search area, ACCEPT IT AS VALID
  immediately and stop worrying about surrounding hatching, unused
  cells, or other visual noise nearby — do not let that noise talk you
  out of a code you already found.

  Valid values ONLY: 048, 046, 019, 005, 020, TDL, L99, E30
  Corrections: TDI->TDL, 48->048
  TDL handwritten variants: T looks like 1/I/F, D looks like O/0/B,
  L looks like I/1/7
  If a 2-3 letter handwritten group resembles TDL even approximately,
  REGARDLESS OF WHERE ON THE PAGE it appears -> return "TDL"
  If nothing valid found -> return ""
  =====================================================================

data_etichetta_preparazione:
  =====================================================================
  MANDATORY ANCHOR CHECK — do this BEFORE answering. Verify that the
  literal text "Prep." / "Preparazione" (or, for the blue layout,
  "PREP N°") is ACTUALLY VISIBLE somewhere on this page. This is not
  optional: an empty pre-printed CODICE/NUMERO grid can visually
  resemble "a label area" without an actual label pasted on it — do
  not treat that resemblance as evidence the field exists.

  If you cannot find this literal anchor text anywhere -> return "".
  This is the CORRECT answer when no label is pasted on the page, and
  is common on some prescriptions. Do NOT substitute another date from
  elsewhere on the page as a fallback (data_prescrizione's center
  "DATA" box, or data_emissione's "DATA SPEDIZIONE" pharmacy stamp are
  DIFFERENT fields in DIFFERENT areas — never borrow either of them
  here, even when you cannot find "Prep." text).

  If the anchor text IS found but the date itself is only partially
  readable (e.g. smudged year), prefer "OCR_INCERTO" over guessing.

  TWO POSSIBLE FORMATS once the anchor above is confirmed present:

  LAYOUT A (white label): field "Prep." or "Prep del"
  Structure: [progressive number] [date]
  Example (format transformation only, NOT a real value to copy):
  "Prep. [progressive]  DD/MM/YY" -> extract "DD/MM/20YY"
  The progressive number (4-6 digits, no separators) must be DISCARDED.

  LAYOUT B (blue label): field "PREP N°[num]/[yy] SCAD. [date]"
  Here "PREP N°36/25" is the preparation NUMBER (36) and YEAR (25),
  NOT a date. In this layout the preparation date may be handwritten
  near the top of the label (e.g. a small handwritten date) — if a
  handwritten date is visible near PREP, use it.
  If no readable preparation date exists in layout B -> "OCR_INCERTO"

  OUTPUT VALIDATION: the output MUST be a COMPLETE date in GG/MM/AAAA
  format (exactly 10 characters). Partial dates like "05/25" or "36/25"
  are INVALID — if you cannot form a complete valid date -> return
  "OCR_INCERTO" instead. Never output the preparation number as a date.
  =====================================================================

etichetta_data_scadenza:
  =====================================================================
  THIS FIELD IS FREQUENTLY LEFT EMPTY BY MISTAKE, especially because
  it sits close to several OTHER date fields on the document (Prep.
  date, data_emissione, data_prescrizione) and gets overlooked. If the
  label is present, actively check for it — do not skip it by default.

  LAYOUT A: field "UTILIZZARE ENTRO [date]" — usually on the same row
  as, or right below, the "Prep. [num] del [date]" field, near the
  price table (S/O/R/U/IV/€ column). Extract the date that follows
  "UTILIZZARE ENTRO", NOT the "Prep. ... del ..." date (that one goes
  in data_etichetta_preparazione instead — they are two DIFFERENT
  dates on the same label, both must be extracted separately).
  LAYOUT B: field "SCAD. [date]" on the PREP row (e.g. "SCAD. DD/MM/YY", format only)
  Format GG/MM/AA -> GG/MM/AAAA.
  Only return "" if the label is genuinely absent or this specific
  date is not present/readable after actively looking for it.
  =====================================================================

etichetta_nome_cognome_paziente:
  LAYOUT A: after "Sig." or "Sig.ra" in the white label.
  LAYOUT B: after "PZ." in the blue label (e.g. "PZ. 485TDBA" ->
  "485TDBA"). The patient may be identified by initials/alphanumeric
  code instead of full name — extract it as written, removing dots.
  Do NOT use patient name from top of prescription.
  Absent in label -> ""

PRICE EXTRACTION — TWO LAYOUTS:

  LAYOUT A (white label, price column on the right):
    =====================================================================
    READ BY LABEL, NOT BY POSITION. This price column has 5-7 rows,
    each with its OWN letter-label printed to the LEFT of its number
    (S., O., AS, R., U, IV., €/T./TOT.). Do NOT read the numbers
    top-to-bottom and assign them in sequence — if you skip or
    misjudge even ONE row that way, every field after it gets the
    WRONG value (a cascading shift). Instead, for each target field
    below, FIRST locate its exact letter-label in the image, THEN read
    the number printed immediately next to THAT specific label.

    - Find the row labeled "S" or "Sost" -> etichetta_prezzo_sost (HIGHEST value, usually ~48)
    - Find the row labeled "O" or "On"   -> etichetta_prezzo_on   (usually ~33)
    - The row labeled "AS" is a DIFFERENT field from "R"/"Rec", even
      though they are printed close together and can have similar-
      looking values — IGNORE the "AS" row entirely, do not use its
      value for anything, and do not let it shift your reading of the
      rows below it.
    - Find the row labeled "R" or "Rec" -> etichetta_prezzo_rec (small ~1.53) — this is
      the row AFTER "AS", not "AS" itself.
    - The row labeled "U" is also DIFFERENT from "IV"/"IVA" — IGNORE
      the "U" row entirely.
    - Find the row labeled "IV" or "IVA" -> etichetta_prezzo_iva (~8) — this is the
      row AFTER "U", not "U" itself.
    - Find the row labeled "EUR", "T", or "TOT" -> etichetta_prezzo_tot (~97)
    - FARMACIA RAMELLA only: sum OP + OI + OS -> etichetta_prezzo_on.
      OUTPUT ONLY THE FINAL COMPUTED NUMBER (e.g. "33.42"), NEVER the
      arithmetic expression itself (do NOT output "20.30 + 8.12 + 5.00"
      or "20.30+8.12+5.00=33.42" — compute it yourself and return only
      the resulting decimal number).

    SELF-CHECK before answering: you should have looked at 5 DISTINCT
    labels (S, R, IV, plus EUR/T/TOT, plus O or OP+OI+OS) and skipped
    exactly 2 labels (AS and U). If your 5 extracted values came from
    5 rows that are NOT separated by the AS/U rows you were supposed
    to skip, you have shifted — go back and re-match each value to
    its printed label directly.

  LAYOUT B (blue label, prices in a single row):
    "PREZZO [x] O.P. [x]EUR S [x]EUR C [x]EUR IVA 10% [x]" and "TOT. [x]"
    Mapping:
    - PREZZO value (first number after PREZZO) -> etichetta_prezzo_sost
    - O.P. value -> etichetta_prezzo_on
    - S value -> IGNORE (stampa/spese)
    - C value -> etichetta_prezzo_rec
    - IVA value -> etichetta_prezzo_iva
    - TOT. value -> etichetta_prezzo_tot

  VALIDATION: etichetta_prezzo_sost MUST be the highest among
  sost/on/rec/iva. If not -> re-read and reassign.
  Decimal: always period not comma. "48,48" -> "48.48". Remove EUR.
  =====================================================================

  =====================================================================
  CRITICAL ANTI-HALLUCINATION RULE (applies especially to LAYOUT B):
  In layout B prices are often HANDWRITTEN and difficult to read.
  NEVER return the typical printed values of other pharmacies
  (48.48, 33.74, 5.00, 1.53, 8.88, 97.63) unless you can CLEARLY
  and DISTINCTLY see those exact digits written on THIS label.
  Copying typical values from memory is a SEVERE ERROR.
  If a price is handwritten and not clearly readable -> "OCR_INCERTO"
  It is ALWAYS better to return "OCR_INCERTO" than to guess.
  =====================================================================
  Label absent -> ""

totale_prescrizione:
  =====================================================================
  DO NOT CONFUSE THIS WITH THE 5 ETICHETTA PRICE FIELDS YOU JUST
  EXTRACTED (etichetta_prezzo_sost/on/rec/iva/tot). This is a
  COMPLETELY SEPARATE number, in a DIFFERENT physical location on the
  page: the small handwritten box labeled "GALEN." / "DIR.CHIAM." /
  "ALTRO" near the bottom-right corner of the page, OUTSIDE the
  pharmacy label entirely. Do NOT copy, blend, average, or otherwise
  derive this value from etichetta_prezzo_sost/on/rec/iva/tot — go
  back to that specific handwritten box and read ONLY what is written
  there, independently of the label prices.
  Source: handwritten value bottom right, in field GALEN/DIR.CHAR/ALTRO.
  Use period as decimal separator. Unclear -> "OCR_INCERTO"
  =====================================================================

timbro_medico and firma_medico:
  =====================================================================
  MAXIMALLY SIMPLE RULE — you do NOT need to identify WHAT the text is,
  WHO it belongs to, or WHETHER it looks like a proper doctor stamp.
  Look at the right-hand "TIMBRO E FIRMA DEL MEDICO" area as a whole:

  If this area contains ANY printed text, handwritten text, ink mark,
  stamp, scribble, or overlapping content of ANY kind — even if it is
  unclear, partial, looks like it might belong to the pharmacy stamp
  instead of the doctor, doesn't resemble a name, or doesn't match any
  expected format — treat it as timbro_medico = true. You do not need
  to read or understand what it says; its mere presence is enough.

  Only return false if this area is COMPLETELY BLANK white space, with
  absolutely nothing printed, written, or stamped in it.

  firma_medico ALWAYS equals timbro_medico.

{
  "codice_fiscale": "",
  "codice_esenzione": "",
  "data_etichetta_preparazione": "",
  "etichetta_data_scadenza": "",
  "etichetta_nome_cognome_paziente": "",
  "etichetta_prezzo_sost": "",
  "etichetta_prezzo_on": "",
  "etichetta_prezzo_rec": "",
  "etichetta_prezzo_iva": "",
  "etichetta_prezzo_tot": "",
  "totale_prescrizione": "",
  "timbro_medico": false,
  "firma_medico": false
}"""

PROMPT_GRUPPO_RESTO = CONTESTO_CONDIVISO + """nome_cognome_assistito:
  Source: top left field "COGNOME E NOME DELL'ASSISTITO".
  May be a full name OR initials/alphanumeric code (e.g. "-485TDBA",
  "485TDBA") when privacy shortening is used. Extract as written,
  removing any leading dash.
  If completely empty -> return ""

barcode:
  Source: top right, TWO separate barcodes.
  First starts with J (remove J). Second starts with Y (remove Y).
  Concatenate both without spaces.
  Example: "J03024" + "Y0055946024" -> "030240055946024"

codice_atc:
  Source: prescription text, after the word "ATC". Expected: N02BG10.
  OCR tolerance: N02BG10R or similar (1-2 different chars) -> return "N02BG10"
  If "ATC" not present but "N02BG10" string appears standalone -> return "N02BG10"
  If not found -> return ""

testo_prescrizione:
  Source: PRESCRIZIONE area center-left. Extract all text as written.

metodo_estrattivo_olio:
  Source: prescription text.
  Extract ONLY if one of these proper names appears:
    Ramella, Calvi, SIFAP, SICAM, Romano, Hazecamp, Cannazza
  Generic descriptions (e.g. "estrazione oleosa") -> return ""

forma_farmaceutica:
  Map to exactly one of these strings:
    "olio in flacone" if text contains: olio, MCT, ml, gocce, estrazione oleosa,
                       contagocce, estratto vegetale liquido
    "capsule" if text contains: cps, capsule, opercoli
    "cartine" if text contains: cartine, bustine, filtri, vapo
  No match -> return ""

THC:
  Source: prescription text ONLY.
  Extract NUMBER ONLY in mg: "10mg di THC" -> "10", "SI ASSUME 10 mg di THC" -> "10"
  Note: "TDH" in prescription text is an OCR error for "THC".
  Do NOT extract percentages (e.g. 15%, 22%).
  Do NOT extract mg/ml concentrations from the drug description line
  (e.g. "10 mg/ml THC") — prefer the daily dose if both are present
  (e.g. "SI ASSUME 10 mg di THC ... pro/die" -> "10").
  Do NOT extract CBD values.
  Not found -> ""

nome_farmacia:
  Source: pharmacy stamp bottom right or pharmacy label header.
  Map to closest from this list:
    Farmacia Tili Snc
    Farmacia Di Lora Srl
    Farmacia Pomi di dr. Collivasone A. & C. Snc
    Farmacia Ramella dott.ri G. e A. Sas
    Farmacia Mazzucchelli F. & C. Snc
    Farmacia Peroni dr Antonio E. & C. Sas
    Farmacia Comunale N.2
    Farmacia Stefini & C Sas
    Farmacia Introini dr. Paolo & C. Sas
    Farmacia Di Crenna
    Farmacia Ponti
  Specific corrections:
    "FARMACIA POMI SNC DI AVIGNO" or "FARMACIA DI AVIGNO" -> "Farmacia Pomi di dr. Collivasone A. & C. Snc"
    "FARMACIA RAMELLA" -> "Farmacia Ramella dott.ri G. e A. Sas"
    "FARMACIA INTROINI" -> "Farmacia Introini dr. Paolo & C. Sas"
    "FARMACIA MAZZUCCHELLI" -> "Farmacia Mazzucchelli F. & C. Snc"
    "FARMACIA PERONI" -> "Farmacia Peroni dr Antonio E. & C. Sas"
    "Farmacia Comunale 2" or "FARMACIA COMUNALE 2" or "M.S. SpA - Farmacia Comunale" -> "Farmacia Comunale N.2"
    "FARMACIA PILI" or "FARMACIA MILI" -> "Farmacia Tili Snc"
  Not mappable -> return name as read. Not present -> "FARMACIA NON RICONOSCIUTA"

etichetta_avvertenze:
  =====================================================================
  THIS FIELD IS FREQUENTLY LEFT EMPTY BY MISTAKE — READ CAREFULLY.
  The warning text is usually SMALL and located near a pictogram
  (e.g. a car/driving icon, a trash bin icon) or in a small dedicated
  box on the label. It is easy to overlook, but if a label is present
  at all, some warning text is USUALLY there too — actively look for
  it, do not default to "" just because it's small or partial.

  LAYOUT A (white label): warning text near the price table or near a
  pictogram — usually touches on THESE THEMES (do NOT copy any exact
  wording below, these are topic hints only, never text to reproduce):
  - driving/vehicle-use warning (contraindicated while driving)
  - doping warning (therapeutic use vs. sports doping test positivity)
  - storage instructions (temperature limit, refrigeration)
  - daily dose reminder near the pictogram (a THC/CBD mg figure)
  - sports-activity warning
  Transcribe the ACTUAL wording printed on THIS specific label for
  whichever of these themes appear — in the label's own words. If NONE
  of these themes appear anywhere near a pictogram/price table on THIS
  image, leave the field empty rather than reconstructing a stock phrase.

  LAYOUT B (blue label): the SEPARATE blue box titled "Avvertenze:" —
  extract the warning lines actually printed there (themes: storage
  away from light/heat, keep out of reach of children, doping
  substance disclosure with a legal reference code). Transcribe the
  REAL wording on THIS label, do not reconstruct a stock phrase. Do
  NOT skip layout B warnings
  just because they are on colored background — they are valid warnings.

  Only return "" if, after actively searching near the pictogram/price
  area, there is truly no warning text visible (or the label itself is
  absent).
  =====================================================================

etichetta_nome_cognome_medico:
  LAYOUT A: preceded by "Dott." or "Dr." or "Dott.ssa" in the white label.
  LAYOUT B: after "DR." at the bottom of the blue label.
  If the label field is absent or unreadable, use the doctor stamp name
  ONLY as last resort.
  Extract full name including title when present.
  Absent everywhere -> ""

{
  "nome_cognome_assistito": "",
  "barcode": "",
  "codice_atc": "",
  "testo_prescrizione": "",
  "metodo_estrattivo_olio": "",
  "forma_farmaceutica": "",
  "THC": "",
  "nome_farmacia": "",
  "etichetta_avvertenze": "",
  "etichetta_nome_cognome_medico": ""
}"""

# Due gruppi organizzati per CRITICITA' (non per argomento tematico): il
# gruppo CRITICO raccoglie i campi dove i test hanno mostrato più errori
# (CF, esenzione, tutte le date, tutti i prezzi) e usa il modello pesante;
# il RESTO usa il modello leggero. Solo 2 chiamate = un solo cambio di
# modello, minimizzando il costo di caricamento/scaricamento tra i due.
# MODELLO_LEGGERO viene sovrascritto a MODELLO_PESANTE se l'utente passa
# esplicitamente --model da riga di comando (per test A/B con un solo
# modello uniforme su tutti i gruppi).
MODELLO_PESANTE = "qwen2.5vl:32b"
MODELLO_LEGGERO = "qwen2.5vl:7b"

GRUPPI_ESTRAZIONE = [
    ("critico", PROMPT_GRUPPO_CRITICO, True, "pesante"),
    ("resto", PROMPT_GRUPPO_RESTO, True, "leggero"),
]

# ─── Funzioni core ─────────────────────────────────────────────────────────────

def get_template_etichetta():
    """Carica (una sola volta) il template del modulo CODICE/NUMERO vuoto."""
    global _template_etichetta_gray
    if _template_etichetta_gray is None:
        if TEMPLATE_ETICHETTA_PATH.exists():
            tmpl = cv2.imread(str(TEMPLATE_ETICHETTA_PATH))
            if tmpl is not None:
                _template_etichetta_gray = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY)
            else:
                log.warning(f"Template etichetta illeggibile: {TEMPLATE_ETICHETTA_PATH}")
        else:
            log.warning(f"Template etichetta non trovato: {TEMPLATE_ETICHETTA_PATH} — rilevamento disabilitato")
    return _template_etichetta_gray


def get_easyocr_reader():
    """Carica (una sola volta) il reader EasyOCR usato SOLO come fallback
    quando il rilevamento SSIM cade nella zona grigia ambigua."""
    global _easyocr_reader
    if not USA_FALLBACK_OCR_ETICHETTA:
        return None
    if _easyocr_reader is None:
        try:
            import easyocr
            log.info("Caricamento EasyOCR (fallback rilevamento etichetta)...")
            _easyocr_reader = easyocr.Reader(["it"], gpu=(NUM_GPU == 1))
        except Exception as e:
            log.warning(f"EasyOCR non disponibile, fallback disabilitato: {e}")
            _easyocr_reader = False  # sentinella: non ritentare ad ogni ricetta
    return _easyocr_reader or None


def render_pagina(pdf_path: Path):
    """Renderizza la prima pagina del PDF una sola volta.

    Restituisce (base64_png, image_gray_np, image_bgr_np) — il base64 va a
    Qwen-VL, gray viene riusato per l'SSIM del rilevamento etichetta, bgr
    serve solo se scatta il fallback OCR sulla zona grigia ambigua.
    """
    doc = fitz.open(str(pdf_path))
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(IMAGE_ZOOM, IMAGE_ZOOM))
    img_bytes = pix.tobytes("png")
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")

    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    else:
        bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    doc.close()
    return img_b64, gray, bgr


def pdf_to_base64(pdf_path: Path) -> str:
    """Converte prima pagina PDF in PNG base64 ad alta risoluzione.

    Mantenuta per compatibilità con eventuale codice esterno che la importi
    direttamente; internamente estrai_dati() usa render_pagina().
    """
    img_b64, _, _ = render_pagina(pdf_path)
    return img_b64


def pulisci_json(testo: str) -> str:
    """Estrae blocco JSON dalla risposta del modello."""
    testo = testo.strip()
    match = re.search(r'\{[\s\S]*\}', testo)
    return match.group(0).strip() if match else testo


def pulisci_data_preparazione(val: str) -> str:
    """
    Rimuove il numero progressivo da data_etichetta_preparazione
    se il modello non l'ha già fatto correttamente.
    Es: "00710 01/04/25" -> "01/04/2025"
        "00710 01/04/2025" -> "01/04/2025"
    """
    if not val or val == "OCR_INCERTO":
        return val

    val = str(val).strip()

    # Pattern: [numero progressivo 4-6 cifre] [spazio] [data]
    match = re.search(
        r'(?:\d{4,6}\s+)?(\d{1,2})[/\s\-](\d{1,2})[/\s\-](\d{2,4})',
        val
    )
    if match:
        giorno, mese, anno = match.groups()
        giorno = giorno.zfill(2)
        mese = mese.zfill(2)
        if len(anno) == 2:
            anno = "20" + anno
        return f"{giorno}/{mese}/{anno}"

    return val


def _is_empty_campo(val) -> bool:
    """Campo vuoto o OCR_INCERTO — usata dai fallback deterministici in sanity_check()."""
    v = str(val or "").strip()
    return v == "" or v.upper() == "OCR_INCERTO"


def _distanza_levenshtein_semplice(a: str, b: str) -> int:
    """Distanza di Levenshtein minima tra due stringhe (implementazione
    locale, per non dipendere da phase5_difformita.py che e' un file
    separato eseguito in un secondo momento della pipeline)."""
    if a == b:
        return 0
    len_a, len_b = len(a), len(b)
    if len_a == 0:
        return len_b
    if len_b == 0:
        return len_a
    riga_prec = list(range(len_b + 1))
    for i, ca in enumerate(a, 1):
        riga_corr = [i] + [0] * len_b
        for j, cb in enumerate(b, 1):
            costo = 0 if ca == cb else 1
            riga_corr[j] = min(
                riga_corr[j - 1] + 1,
                riga_prec[j] + 1,
                riga_prec[j - 1] + costo
            )
        riga_prec = riga_corr
    return riga_prec[len_b]


def sanity_check(dati: dict) -> dict:
    """
    Normalizza e valida i campi estratti.
    Compatibile con il sanity check del tutor (parser.py).
    """
    # Nome assistito: rimuovi trattino/underscore iniziale
    if dati.get("nome_cognome_assistito"):
        dati["nome_cognome_assistito"] = str(dati["nome_cognome_assistito"]).strip().lstrip("-_").strip()

    # Codice fiscale: rimuovi underscore/trattini iniziali
    if dati.get("codice_fiscale"):
        dati["codice_fiscale"] = str(dati["codice_fiscale"]).strip().lstrip("-_").strip()

    # Barcode: rimuovi J iniziale e spazi
    if dati.get("barcode"):
        bc = str(dati["barcode"]).replace(" ", "")
        if bc.upper().startswith("J"):
            bc = bc[1:]
        dati["barcode"] = bc

    # Codice fiscale: 16 caratteri, maiuscolo, no spazi
    if dati.get("codice_fiscale"):
        cf = str(dati["codice_fiscale"]).replace(" ", "").upper()
        if len(cf) == 16 and re.match(r'^[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]$', cf):
            dati["codice_fiscale"] = cf
        else:
            log.warning(f"CF non valido: {cf}")

    # Codice ATC: c'e' un solo valore valido possibile in questo dominio
    # (N02BG10) — tolleranza fuzzy piu' ampia rispetto al codice esenzione
    # (dove invece c'erano 8 codici diversi tra cui rischiare di scegliere
    # quello sbagliato): qui basta la vera distanza di Levenshtein dall'
    # unico bersaglio, non serve limitarsi al solo prefisso.
    if dati.get("codice_atc"):
        atc = str(dati["codice_atc"]).upper().replace(" ", "")
        if atc != "N02BG10" and _distanza_levenshtein_semplice(atc, "N02BG10") <= 3:
            log.info(f"  codice_atc: '{atc}' corretto fuzzy in 'N02BG10'")
            dati["codice_atc"] = "N02BG10"

    # Codice esenzione: valori ammessi, con tolleranza fuzzy (distanza 1)
    # per refusi di lettura OCR — non solo i due casi "TDI"/"48" gestiti
    # prima a mano, ma qualunque lettura vicina a un valore valido (es.
    # "TOL" letto per "TDL", osservato in un caso reale: la D scambiata
    # per una O e' un errore visivo comune).
    codici_validi = {"048", "046", "019", "005", "020", "TDL", "L99", "E30"}
    ce = str(dati.get("codice_esenzione", "")).upper().strip()
    if ce and ce not in codici_validi:
        migliore_match, migliore_distanza = None, None
        for candidato in codici_validi:
            d = _distanza_levenshtein_semplice(ce, candidato)
            if migliore_distanza is None or d < migliore_distanza:
                migliore_match, migliore_distanza = candidato, d
        if migliore_distanza is not None and migliore_distanza <= 1:
            log.info(f"  codice_esenzione: '{ce}' corretto fuzzy in '{migliore_match}' (distanza {migliore_distanza})")
            ce = migliore_match
    dati["codice_esenzione"] = ce if ce in codici_validi else ""

    # Data etichetta preparazione: pulizia extra del numero progressivo
    if dati.get("data_etichetta_preparazione"):
        dati["data_etichetta_preparazione"] = pulisci_data_preparazione(
            dati["data_etichetta_preparazione"]
        )
        # Valida formato completo GG/MM/AAAA — date parziali diventano OCR_INCERTO
        val = dati["data_etichetta_preparazione"]
        if val and val != "OCR_INCERTO" and not re.match(r'^\d{2}/\d{2}/\d{4}$', val):
            log.warning(f"Data preparazione non valida ({val}) -> OCR_INCERTO")
            dati["data_etichetta_preparazione"] = "OCR_INCERTO"

    # Date: converti anno a 2 cifre in 4 cifre
    campi_data = [
        "data_etichetta_preparazione", "etichetta_data_scadenza"
    ]
    for campo in campi_data:
        val = str(dati.get(campo, "") or "")
        if val and val != "OCR_INCERTO":
            m = re.match(r'^(\d{2})/(\d{2})/(\d{2})$', val)
            if m:
                dati[campo] = f"{m.group(1)}/{m.group(2)}/20{m.group(3)}"

    # Prezzi: virgola -> punto, rimuovi simboli
    campi_prezzo = [
        "etichetta_prezzo_sost", "etichetta_prezzo_on", "etichetta_prezzo_rec",
        "etichetta_prezzo_iva", "etichetta_prezzo_tot", "totale_prescrizione"
    ]
    for campo in campi_prezzo:
        val = str(dati.get(campo, "") or "")
        if val and val != "OCR_INCERTO":
            val_str = val.replace(",", ".").replace("€", "").replace(" ", "")
            try:
                dati[campo] = str(round(float(val_str), 2))
            except ValueError:
                pass

    # Anti-allucinazione prezzi: valori tipici sospetti su Farmacia Comunale
    if "Comunale" in str(dati.get("nome_farmacia", "")):
        valori_tipici = {"48.48", "33.74", "5.0", "5.00", "1.53", "8.88", "97.63"}
        campi_p = ["etichetta_prezzo_sost", "etichetta_prezzo_on",
                   "etichetta_prezzo_rec", "etichetta_prezzo_iva", "etichetta_prezzo_tot"]
        sospetti = sum(1 for c in campi_p if str(dati.get(c, "")) in valori_tipici)
        if sospetti >= 3:
            log.warning("Prezzi tipici su Farmacia Comunale — probabile allucinazione -> OCR_INCERTO")
            for c in campi_p:
                if str(dati.get(c, "")) in valori_tipici:
                    dati[c] = "OCR_INCERTO"

    # THC: estrai solo il numero
    if dati.get("THC"):
        m = re.search(r'(\d+(?:\.\d+)?)', str(dati["THC"]))
        dati["THC"] = m.group(1) if m else ""

    # Timbro/Firma: sempre uguali
    dati["firma_medico"] = dati.get("timbro_medico", False)

    # ─── Fallback deterministico: metodo_estrattivo_olio ──────────────────
    # Se il modello lascia il campo vuoto ma testo_prescrizione contiene
    # comunque uno dei nomi propri noti, lo recuperiamo qui via semplice
    # string matching — testo_prescrizione è quasi sempre estratto bene,
    # è solo il campo dedicato che a volte il modello non compila anche
    # quando il termine è letteralmente presente nel testo.
    METODI_ESTRATTIVI_NOTI_CANON = {
        "ramella": "Ramella", "calvi": "Calvi", "sifap": "SIFAP",
        "sicam": "SICAM", "romano": "Romano",
        "hazecamp": "Hazecamp", "hazekamp": "Hazekamp", "cannazza": "Cannazza",
    }
    if _is_empty_campo(dati.get("metodo_estrattivo_olio", "")):
        testo_lower = str(dati.get("testo_prescrizione", "")).lower()
        trovato = False
        for chiave, canonico in METODI_ESTRATTIVI_NOTI_CANON.items():
            if re.search(rf'\b{chiave}\b', testo_lower):
                dati["metodo_estrattivo_olio"] = canonico
                log.info(f"  metodo_estrattivo_olio recuperato da testo_prescrizione (match esatto): {canonico}")
                trovato = True
                break

        if not trovato:
            # Match esatto fallito: il testo potrebbe contenere un refuso
            # OCR del nome del metodo (es. "Ramells" invece di "Ramella",
            # osservato in casi reali) — confronto fuzzy parola per parola
            # con tolleranza di 1 carattere, invece di richiedere la
            # parola esatta con confine netto.
            parole = re.findall(r"[a-zà-ù]+", testo_lower)
            for parola in parole:
                if len(parola) < 5:
                    continue  # parole troppo corte, rischio di falsi positivi
                for chiave, canonico in METODI_ESTRATTIVI_NOTI_CANON.items():
                    if _distanza_levenshtein_semplice(parola, chiave) <= 1:
                        dati["metodo_estrattivo_olio"] = canonico
                        log.info(
                            f"  metodo_estrattivo_olio recuperato da testo_prescrizione "
                            f"(fuzzy: '{parola}' ~ '{chiave}'): {canonico}"
                        )
                        trovato = True
                        break
                if trovato:
                    break

    # Canonicalizzazione fuzzy di nome_farmacia — critica ora che il
    # sistema a profili farmacia (date_corrector.py) dipende da un match
    # ESATTO su questo campo per scegliere la regola giusta: se il
    # modello scrive una variante leggermente diversa dal nome canonico
    # (spazi, maiuscole, un carattere diverso), senza questa
    # canonicalizzazione il sistema penserebbe "farmacia non riconosciuta"
    # e proverebbe tutti i profili in sequenza invece di usare subito
    # quello giusto. Tolleranza proporzionale alla lunghezza (nomi lunghi
    # tollerano piu' caratteri di differenza rispetto a nomi corti).
    FARMACIE_CANONICHE = [
        "Farmacia Tili Snc", "Farmacia Di Lora Srl",
        "Farmacia Pomi di dr. Collivasone A. & C. Snc",
        "Farmacia Ramella dott.ri G. e A. Sas",
        "Farmacia Mazzucchelli F. & C. Snc",
        "Farmacia Peroni dr Antonio E. & C. Sas",
        "Farmacia Comunale N.2", "Farmacia Stefini & C Sas",
        "Farmacia Introini dr. Paolo & C. Sas",
        "Farmacia Di Crenna", "Farmacia Ponti",
    ]
    nome_farmacia_attuale = str(dati.get("nome_farmacia", "")).strip()
    if nome_farmacia_attuale and nome_farmacia_attuale not in FARMACIE_CANONICHE \
       and nome_farmacia_attuale != "FARMACIA NON RICONOSCIUTA":
        migliore_match, migliore_rapporto = None, None
        for canonica in FARMACIE_CANONICHE:
            d = _distanza_levenshtein_semplice(nome_farmacia_attuale.lower(), canonica.lower())
            rapporto = d / max(len(canonica), 1)  # differenza proporzionale alla lunghezza
            if migliore_rapporto is None or rapporto < migliore_rapporto:
                migliore_match, migliore_rapporto = canonica, rapporto
        if migliore_rapporto is not None and migliore_rapporto <= 0.25:
            log.info(
                f"  nome_farmacia: '{nome_farmacia_attuale}' corretta fuzzy in "
                f"'{migliore_match}' (differenza {migliore_rapporto:.0%})"
            )
            dati["nome_farmacia"] = migliore_match


    # Stessa idea: se il campo è vuoto ma testo_prescrizione contiene le
    # parole chiave già usate nel prompt per la mappatura, la deriviamo
    # qui direttamente invece di fidarci solo del modello.
    if _is_empty_campo(dati.get("forma_farmaceutica", "")):
        testo_lower = str(dati.get("testo_prescrizione", "")).lower()
        if any(k in testo_lower for k in (
            "olio", "mct", " ml", "gocce", "estrazione oleosa",
            "contagocce", "estratto vegetale liquido"
        )):
            dati["forma_farmaceutica"] = "olio in flacone"
            log.info("  forma_farmaceutica recuperata da testo_prescrizione: olio in flacone")
        elif any(k in testo_lower for k in ("cps", "capsule", "opercoli")):
            dati["forma_farmaceutica"] = "capsule"
            log.info("  forma_farmaceutica recuperata da testo_prescrizione: capsule")
        elif any(k in testo_lower for k in ("cartine", "bustine", "filtri", "vapo")):
            dati["forma_farmaceutica"] = "cartine"
            log.info("  forma_farmaceutica recuperata da testo_prescrizione: cartine")
    else:
        # Il campo non e' vuoto, ma potrebbe non combaciare esattamente
        # con uno dei 3 valori canonici (maiuscole diverse, singolare
        # invece di plurale, ecc.) — canonicalizzazione fuzzy invece di
        # lasciarlo passare cosi' com'e' e farlo bocciare dal check R06.
        FORME_CANONICHE = ["olio in flacone", "capsule", "cartine"]
        forma_attuale = str(dati["forma_farmaceutica"]).strip().lower()
        if forma_attuale not in FORME_CANONICHE:
            for canonica in FORME_CANONICHE:
                if _distanza_levenshtein_semplice(forma_attuale, canonica) <= 2:
                    log.info(f"  forma_farmaceutica: '{forma_attuale}' corretta fuzzy in '{canonica}'")
                    dati["forma_farmaceutica"] = canonica
                    break

    return dati


# Mappatura tra il formato FARMACIA_ID del file Regione (es. "CO0310 -
# TILI & C.") e i nomi canonici usati nei profili farmacia di
# date_corrector.py. Basata sui valori reali osservati nel file Regione:
# 11 farmacie totali, di cui 7 hanno un profilo dedicato costruito su un
# esempio reale — le altre (es. "TILI & C.", "PONTI DI DOTT. SESSA")
# restano non mappate: per quelle si usa la regola generica.
MAPPATURA_FARMACIA_ID_A_PROFILO = {
    "PERONI": "Farmacia Peroni dr Antonio E. & C. Sas",
    "DI LORA": "Farmacia Di Lora Srl",
    "POMI": "Farmacia Pomi di dr. Collivasone A. & C. Snc",
    "MAZZUCCHELLI": "Farmacia Mazzucchelli F. & C. Snc",
    "INTROINI": "Farmacia Introini dr. Paolo & C. Sas",
    "RAMELLA": "Farmacia Ramella dott.ri G. e A. Sas",
    "COMUNALE 2": "Farmacia Comunale N.2",
}


def _carica_cache_barcode_a_farmacia_id():
    """
    Carica (una sola volta, cache condivisa) la colonna FARMACIA_ID del
    file Excel Regione, indicizzata per barcode — per non rileggere
    l'intero file ad ogni ricetta. Silenziosa se PERCORSO_REGIONE non e'
    impostato o il file non e' disponibile: in quel caso il recupero da
    Regione viene semplicemente saltato più avanti.
    """
    global _cache_barcode_a_farmacia_id
    if _cache_barcode_a_farmacia_id is not None:
        return _cache_barcode_a_farmacia_id

    _cache_barcode_a_farmacia_id = {}
    if PERCORSO_REGIONE is None or not Path(PERCORSO_REGIONE).exists():
        return _cache_barcode_a_farmacia_id

    try:
        import pandas as pd
        # dtype=str e' essenziale: senza, pandas legge BARCODE come numero
        # e perde lo zero iniziale (es. "030170161280325" -> "30170161280325"),
        # rompendo il confronto — stesso accorgimento gia' usato in
        # merge_regione.py per lo stesso identico motivo.
        df = pd.read_excel(PERCORSO_REGIONE, dtype=str, usecols=lambda c: c.upper() in ("BARCODE", "FARMACIA_ID"))
        col_barcode = next((c for c in df.columns if c.upper() == "BARCODE"), None)
        col_farmacia = next((c for c in df.columns if c.upper() == "FARMACIA_ID"), None)
        if col_barcode and col_farmacia:
            for _, riga in df.iterrows():
                bc = str(riga[col_barcode]).strip().replace(" ", "").lstrip("_-")
                if bc.upper().startswith("J"):
                    bc = bc[1:]
                fid = riga[col_farmacia]
                if bc and bc != "nan" and isinstance(fid, str) and fid.strip():
                    _cache_barcode_a_farmacia_id[bc] = fid.strip()
        log.info(f"Cache FARMACIA_ID da Regione caricata: {len(_cache_barcode_a_farmacia_id)} barcode")
    except Exception as e:
        log.warning(f"Impossibile caricare FARMACIA_ID da Regione: {e}")

    return _cache_barcode_a_farmacia_id


def recupera_profilo_farmacia_da_regione(barcode: str) -> str:
    """
    Se il barcode e' presente nella cache Regione, restituisce il nome
    canonico del profilo farmacia corrispondente (uno dei 7 già definiti),
    o stringa vuota se FARMACIA_ID non e' mappabile a nessuno di essi —
    in quel caso si userà la regola generica, non un profilo indovinato.
    """
    if not barcode:
        return ""
    cache = _carica_cache_barcode_a_farmacia_id()
    farmacia_id = cache.get(str(barcode).strip(), "")
    if not farmacia_id:
        return ""

    farmacia_id_upper = farmacia_id.upper()
    for chiave, profilo in MAPPATURA_FARMACIA_ID_A_PROFILO.items():
        if chiave in farmacia_id_upper:
            return profilo
    return ""


def get_date_corrector():
    global _date_corrector
    if _date_corrector is None and DATE_CORRECTOR_DISPONIBILE:
        log.info('Inizializzazione DateCorrector (crop dedicati: esenzione, totale, testo, CF, nome, etichetta)...')
        try:
            # DateCorrector non gestisce più data_prescrizione/data_emissione
            # affatto (prese esclusivamente da Regione in merge_regione.py) —
            # gestisce solo i crop dedicati per gli altri campi.
            _date_corrector = DateCorrector(qwen_model="qwen2.5vl:32b")
        except Exception as e:
            log.warning(f'DateCorrector non disponibile: {e}')
    return _date_corrector


def costruisci_prompt(etichetta_result, prompt_base: str = PROMPT) -> str:
    """Aggiunge a un prompt di base un'istruzione dinamica sull'esito del
    rilevamento automatico presenza/assenza etichetta, quando la confidenza
    è sufficiente. Con confidenza 'bassa' (caso ambiguo) non si forza nulla:
    resta valida la regola anti-allucinazione generica già presente nel
    prompt base.
    """
    if etichetta_result is None or etichetta_result.confidence == "bassa":
        return prompt_base

    if not etichetta_result.presente:
        nota = f"""

=====================================================================
VERIFIED LABEL STATUS (automatic structural detection, confidence: {etichetta_result.confidence}):
The pharmacy label area has been automatically verified as EMPTY
(structural similarity vs blank template: {etichetta_result.ssim_score:.2f}).
This means NO pharmacy label is physically present on this document.
You MUST return "" for ALL etichetta_* fields (etichetta_prezzo_sost,
etichetta_prezzo_on, etichetta_prezzo_rec, etichetta_prezzo_iva,
etichetta_prezzo_tot, etichetta_avvertenze, etichetta_data_scadenza,
etichetta_nome_cognome_medico, etichetta_nome_cognome_paziente,
data_etichetta_preparazione).
Do NOT invent typical values even if you think you see faint marks or
a stamp — this has been structurally verified as empty.
=====================================================================
"""
    else:
        nota = f"""

=====================================================================
LABEL DETECTION HEURISTIC (automatic structural detection, confidence: {etichetta_result.confidence}):
An automatic heuristic (image similarity vs a blank template) suggests
a pharmacy label is LIKELY present on this page. This is a SIGNAL, not
a verified fact — the heuristic CAN be wrong, especially on documents
where the pre-printed grid alone can visually resemble a label.

Do NOT let this heuristic override what you actually see: for EACH
etichetta_* field, you must still find its own real anchor text/content
on the page before extracting a value (see the per-field anchor
requirements above, e.g. "Prep." for data_etichetta_preparazione). If
this heuristic says "present" but you cannot actually find the anchor
for a specific field, trust your own reading over the heuristic and
return "" for that field — do not force a value just because the
heuristic suggested a label should be there.
=====================================================================
"""
    return prompt_base + nota


def estrai_dati(pdf_path: Path) -> dict:
    """Pipeline completa: PDF -> JSON validato."""
    log.info(f"Elaborando: {pdf_path.name}")

    try:
        img_b64, page_gray, page_bgr = render_pagina(pdf_path)
    except Exception as e:
        log.error(f"Errore conversione PDF: {e}")
        return {}

    # Rilevamento automatico presenza/assenza etichetta (best-effort: se il
    # template non è disponibile o qualcosa fallisce, si procede comunque
    # con il prompt base, senza bloccare l'elaborazione)
    etichetta_result = None
    template_gray = get_template_etichetta()
    if template_gray is not None:
        try:
            reader = get_easyocr_reader()
            etichetta_result = detect_etichetta_from_images(
                page_gray, template_gray, reader=reader, page_bgr=page_bgr
            )
            log.info(
                f"  Rilevamento etichetta: presente={etichetta_result.presente} "
                f"confidenza={etichetta_result.confidence} ssim={etichetta_result.ssim_score:.3f}"
            )
        except Exception as e:
            log.warning(f"  Errore rilevamento etichetta: {e}")

    dati = {}
    almeno_un_gruppo_riuscito = False

    for nome_gruppo, prompt_gruppo, e_gruppo_etichetta, peso in GRUPPI_ESTRAZIONE:
        prompt_finale = prompt_gruppo
        if e_gruppo_etichetta:
            prompt_finale = costruisci_prompt(etichetta_result, prompt_gruppo)

        modello_gruppo = MODELLO_PESANTE if peso == "pesante" else MODELLO_LEGGERO
        log.info(f"  [{nome_gruppo}] uso modello {modello_gruppo} ({peso})")

        try:
            response = ollama.chat(
                model=modello_gruppo,
                messages=[{
                    "role": "user",
                    "content": prompt_finale,
                    "images": [img_b64]
                }],
                options={
                    "num_gpu": NUM_GPU,
                    "num_ctx": NUM_CTX,
                    "temperature": 0.0
                }
            )
            testo = response["message"]["content"]
        except Exception as e:
            log.error(f"  [{nome_gruppo}] Errore Ollama ({modello_gruppo}): {e}")
            continue

        testo_pulito = pulisci_json(testo)
        try:
            parziale = json.loads(testo_pulito)
        except json.JSONDecodeError as e:
            log.error(f"  [{nome_gruppo}] Errore parsing JSON: {e}")
            log.debug(f"  [{nome_gruppo}] Risposta raw:\n{testo[:500]}")
            continue

        dati.update(parziale)
        almeno_un_gruppo_riuscito = True
        log.info(f"  [{nome_gruppo}] estratto: {list(parziale.keys())}")

    if not almeno_un_gruppo_riuscito:
        log.error("  Nessuno dei 4 gruppi di estrazione è riuscito")
        return {}

    dati = sanity_check(dati)

    # ─── Fallback deterministico: timbro_medico/firma_medico ──────────────
    # Contraddizione osservata nei dati reali: il modello a volte estrae
    # correttamente etichetta_nome_cognome_medico (che secondo il prompt va
    # letto dal timbro SOLO come ultima risorsa, quando l'etichetta è
    # assente) ma segna comunque timbro_medico=false nella stessa risposta.
    # Se l'etichetta è confermata assente e quel campo non è vuoto, il nome
    # deve per forza provenire dal timbro — quindi lo deduciamo qui invece
    # di fidarci del campo timbro_medico diretto in questo caso specifico.
    if etichetta_result is not None and not etichetta_result.presente:
        nome_medico_letto = str(dati.get("etichetta_nome_cognome_medico", "")).strip()
        if nome_medico_letto and not dati.get("timbro_medico"):
            log.info(
                f"  timbro_medico=false ma etichetta_nome_cognome_medico='{nome_medico_letto}' "
                f"con etichetta assente -> il nome viene dal timbro, correggo a true"
            )
            dati["timbro_medico"] = True
            dati["firma_medico"] = True

    # Il barcode viene sovrascritto con quello ricavato dal NOME DEL FILE PDF:
    # il nome è assegnato dal sistema sorgente ed è sempre corretto, a
    # differenza della lettura OCR di Qwen-VL che concatenando i due barcode
    # può confondere cifre/lettere (es. inserire cifre spurie a metà stringa).
    barcode_da_filename = re.sub(r'\D', '', pdf_path.stem)
    if barcode_da_filename:
        barcode_ocr = dati.get("barcode", "")
        if barcode_ocr and barcode_ocr != barcode_da_filename:
            log.info(
                f"  Barcode OCR ({barcode_ocr}) diverso da quello nel nome file "
                f"({barcode_da_filename}) — uso quello del filename (più affidabile)"
            )
        dati["barcode"] = barcode_da_filename
    else:
        log.warning(f"  Impossibile ricavare un barcode numerico dal nome file: {pdf_path.name}")

    # NOTA: data_prescrizione/data_emissione vengono prese ESCLUSIVAMENTE
    # da Regione in merge_regione.py — non vengono nemmeno più estratte qui,
    # né da questo blocco né dal prompt principale (rimosse del tutto).
    corrector = get_date_corrector()

    # Guardia: se l'etichetta è CONFERMATA assente (confidenza sufficiente,
    # stessa soglia già usata in costruisci_prompt), nessuno dei meccanismi
    # di correzione sui campi etichetta deve nemmeno tentare la lettura —
    # altrimenti rischiano di leggere per sbaglio un'altra zona della
    # pagina (es. la data_emissione/data spedizione, visibile nella stessa
    # ampia area di ricerca), producendo un falso positivo: un valore
    # presente quando invece l'etichetta — e quindi il dato — non c'è.
    etichetta_confermata_assente = (
        etichetta_result is not None
        and etichetta_result.confidence != "bassa"
        and not etichetta_result.presente
    )

    if corrector:
        # Recupero codice_esenzione con crop dedicato, se il prompt
        # principale lo ha lasciato vuoto — questa parte resta attiva
        try:
            dati = corrector.correggi_esenzione(dati, pdf_path)
        except Exception as e:
            log.warning(f'Errore correzione codice_esenzione: {e}')

        # Recupero totale_prescrizione con crop dedicato, se il prompt
        # principale lo ha lasciato vuoto/incerto — sfrutta il modello
        # pesante ormai libero dalla correzione date (ora presa da Regione)
        try:
            dati = corrector.correggi_totale(dati, pdf_path)
        except Exception as e:
            log.warning(f'Errore correzione totale_prescrizione: {e}')

        # Recupero testo_prescrizione con crop dedicato, se il prompt
        # principale lo ha lasciato vuoto/incerto
        try:
            dati = corrector.correggi_testo_prescrizione(dati, pdf_path)
        except Exception as e:
            log.warning(f'Errore correzione testo_prescrizione: {e}')

        # Recupero codice_fiscale con crop dedicato, se il prompt principale
        # lo ha lasciato vuoto/incerto/non da 16 caratteri
        try:
            dati = corrector.correggi_codice_fiscale(dati, pdf_path)
        except Exception as e:
            log.warning(f'Errore correzione codice_fiscale: {e}')

        # Recupero nome_cognome_assistito con crop dedicato, se il prompt
        # principale lo ha lasciato vuoto
        try:
            dati = corrector.correggi_nome_assistito(dati, pdf_path)
        except Exception as e:
            log.warning(f'Errore correzione nome_cognome_assistito: {e}')

        # Recupero data_etichetta_preparazione con ANCORAGGIO DINAMICO al
        # testo "Prep." — SOLO se l'etichetta non è confermata assente
        # (altrimenti non c'è nessun "Prep." vero da trovare, e il
        # meccanismo rischierebbe di leggere per sbaglio un'altra data
        # visibile nella stessa area di ricerca, es. la data di spedizione)
        if not etichetta_confermata_assente:
            try:
                area_etichetta = etichetta_result.match_location if etichetta_result is not None else None
                dati = corrector.correggi_data_preparazione_dinamico(dati, pdf_path, area_etichetta=area_etichetta)
            except Exception as e:
                log.warning(f'Errore correzione data_etichetta_preparazione: {e}')
        else:
            log.info("  Etichetta confermata assente — salto la ricerca di data_etichetta_preparazione")

        # Rilettura di TUTTI i campi etichetta (date, prezzi, nomi,
        # avvertenze) con la regola specifica della farmacia già
        # riconosciuta — SOLO se l'etichetta non è confermata assente,
        # stesso motivo del punto sopra
        if not etichetta_confermata_assente:
            try:
                nome_farmacia_da_regione = ""
                if not str(dati.get("nome_farmacia", "")).strip():
                    nome_farmacia_da_regione = recupera_profilo_farmacia_da_regione(dati.get("barcode", ""))
                dati = corrector.correggi_etichetta_per_farmacia(
                    dati, pdf_path, nome_farmacia_da_regione=nome_farmacia_da_regione
                )
            except Exception as e:
                log.warning(f'Errore correzione etichetta per farmacia: {e}')
        else:
            log.info("  Etichetta confermata assente — salto la rilettura per profilo farmacia")

    # Rete di sicurezza finale: se l'etichetta è confermata assente, forza
    # VUOTI tutti i campi etichetta indipendentemente da cosa sia successo
    # sopra — copre anche il caso in cui il prompt principale (nonostante
    # l'istruzione esplicita) abbia comunque restituito un valore.
    if etichetta_confermata_assente:
        CAMPI_ETICHETTA = [
            "data_etichetta_preparazione", "etichetta_data_scadenza",
            "etichetta_nome_cognome_paziente", "etichetta_nome_cognome_medico",
            "etichetta_prezzo_sost", "etichetta_prezzo_on", "etichetta_prezzo_rec",
            "etichetta_prezzo_iva", "etichetta_prezzo_tot", "etichetta_avvertenze",
        ]
        for campo in CAMPI_ETICHETTA:
            if dati.get(campo):
                log.info(f"  Etichetta confermata assente: forzo {campo} vuoto (era {dati[campo]!r})")
            dati[campo] = ""

    dati["_source_file"] = pdf_path.name
    dati["_elaborato_il"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    # Tracciabilità: salva l'esito del rilevamento automatico nel JSON, utile
    # sia per il modulo difformità sia per validare il tasso di allucinazione
    # residua su un campione più ampio (vedi hallucination_audit.py)
    if etichetta_result is not None:
        dati["_etichetta_rilevata_presente"] = etichetta_result.presente
        dati["_etichetta_rilevata_confidenza"] = etichetta_result.confidence
        dati["_etichetta_rilevata_ssim"] = round(float(etichetta_result.ssim_score), 4)

    return dati


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    global NUM_GPU, OLLAMA_MODEL, MODELLO_PESANTE, MODELLO_LEGGERO

    ap = argparse.ArgumentParser(description="OCR Cannabis ATS Insubria")
    ap.add_argument("--input", type=Path, default=RICETTE_DIR,
                    help="Cartella con i PDF delle ricette")
    ap.add_argument("--test", action="store_true",
                    help="Elabora solo la prima ricetta trovata")
    ap.add_argument("--gpu", type=int, default=NUM_GPU,
                    help="0=CPU, 1=GPU (default: 1)")
    ap.add_argument("--model", type=str, default=None,
                    help=f"Se specificato, forza LO STESSO modello su tutti e 4 i "
                         f"gruppi di estrazione (utile per test A/B). Se omesso, "
                         f"usa la strategia mista di default: {MODELLO_PESANTE} su "
                         f"identita/etichetta, {MODELLO_LEGGERO} su prescrizione/timbri.")
    args = ap.parse_args()

    NUM_GPU = args.gpu
    if args.model:
        MODELLO_PESANTE = args.model
        MODELLO_LEGGERO = args.model
        OLLAMA_MODEL = args.model
        log.info(f"Modello forzato uniforme su tutti i gruppi: {args.model}")

    input_dir = args.input
    if not input_dir.exists():
        log.error(f"Cartella non trovata: {input_dir}")
        sys.exit(1)

    pdf_files = sorted(input_dir.glob("*.pdf"))
    if not pdf_files:
        log.error(f"Nessun PDF trovato in {input_dir}")
        sys.exit(1)

    if args.test:
        pdf_files = [pdf_files[0]]
        log.info("Modalità TEST — elaboro solo la prima ricetta")

    log.info(f"Modello: {OLLAMA_MODEL} | GPU: {NUM_GPU} | Ricette: {len(pdf_files)}")

    OUTPUT_DIR.mkdir(exist_ok=True)
    risultati, errori = [], []
    inizio = datetime.now()

    for i, pdf in enumerate(pdf_files, 1):
        log.info(f"[{i}/{len(pdf_files)}] {pdf.name}")
        try:
            dati = estrai_dati(pdf)
            if dati:
                risultati.append(dati)
                out = OUTPUT_DIR / f"{pdf.stem}.json"
                out.write_text(
                    json.dumps(dati, indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )
                log.info(f"  -> {out.name}")
            else:
                errori.append(pdf.name)
                log.warning(f"  -> Nessun dato estratto")
        except Exception as e:
            errori.append(pdf.name)
            log.error(f"  -> Errore: {e}")

    durata = (datetime.now() - inizio).total_seconds()
    media = durata / len(pdf_files) if pdf_files else 0

    riepilogo = {
        "totale": len(pdf_files),
        "elaborati": len(risultati),
        "errori": len(errori),
        "durata_sec": round(durata, 1),
        "media_sec_per_ricetta": round(media, 1),
        "file_errore": errori,
        "risultati": risultati
    }
    riepilogo_path = OUTPUT_DIR / "riepilogo.json"
    riepilogo_path.write_text(
        json.dumps(riepilogo, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    log.info(f"\n{'='*50}")
    log.info(f"Completato: {len(risultati)}/{len(pdf_files)} ricette elaborate")
    log.info(f"Durata totale: {durata:.0f}s | Media: {media:.1f}s/ricetta")
    if errori:
        log.warning(f"Errori su: {errori}")
    log.info(f"Output: {OUTPUT_DIR}")

    if args.test and risultati:
        print("\nRisultato finale:")
        print(json.dumps(risultati[0], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()