"""
date_corrector.py — Modulo di correzione date con TrOCR
ATS Insubria — Integrazione in ocr_cannabis.py

Carica i modelli TrOCR fine-tunati e corregge i 2 campi data
più critici dopo l'estrazione di Qwen2.5-VL.

Logica di correzione:
- Se Qwen-VL e TrOCR concordano → usa il valore comune
- Se discordano → usa TrOCR (più affidabile per le date)
- Se TrOCR non legge → mantieni il valore di Qwen-VL
- Se Qwen-VL ha OCR_INCERTO o vuoto → usa TrOCR

Utilizzo:
    from date_corrector import DateCorrector

    corrector = DateCorrector()  # carica i modelli una volta sola
    dati = corrector.correggi(dati, pdf_path)
"""

import re
import io
import json
import base64
import logging
from pathlib import Path
from datetime import datetime

import torch
import fitz
import ollama
import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

IMAGE_ZOOM = 3.0

_THINK_BLOCK_RE = re.compile(r'<think>[\s\S]*?</think>', re.IGNORECASE)

# Rete di sicurezza aggiuntiva contro il thinking mode di Qwen3-VL — vedi
# la stessa funzione e spiegazione in ocr_cannabis.py. think=False da solo
# non è sempre rispettato (verificato con log reali), "/no_think" agisce
# a livello di chat template ed è più affidabile. Condizionale alla
# famiglia Qwen3: su Qwen2.5 (nessun thinking mode) sarebbe solo testo
# estraneo nel prompt.
def _suffisso_no_think(modello: str) -> str:
    return "\n\n/no_think" if "qwen3" in modello.lower() else ""


def _contenuto_pulito(response) -> str:
    """Estrae response["message"]["content"] rimuovendo un eventuale blocco
    <think>...</think> residuo.

    Qwen3-VL supporta il thinking mode: tutte le chiamate qui sotto passano
    già think=False esplicitamente, ma questo helper resta come rete di
    sicurezza — queste funzioni fanno parsing rigido (confronti di
    sottostringa tipo "INCERTO" in testo, o json.loads diretto) e non hanno
    la stessa tolleranza di pulisci_json() in ocr_cannabis.py: un residuo di
    ragionamento non rimosso qui romperebbe silenziosamente la lettura.
    """
    testo = response["message"]["content"]
    return _THINK_BLOCK_RE.sub('', testo).strip()

# Valori validi per il codice esenzione — usati sia dal prompt principale
# (in ocr_cannabis.py) sia da questo modulo per il crop dedicato.
VALORI_ESENZIONE_VALIDI = ["TDL", "048", "046", "019", "005", "020", "L99", "E30"]

PROMPT_CROP_ESENZIONE = """This image is a cropped region from the top-left header area of an Italian medical prescription, containing the "CODICE ESENZIONE" (exemption code) field.

The code may be printed inside grid boxes, handwritten in large letters above/below the boxes, or written anywhere in this cropped region.

IMPORTANT: the printed box often has MORE cells than the code needs
(e.g. 6 cells for a 3-letter code like "TDL"). The EXTRA unused cells
are frequently marked with diagonal hatching, cross-hatch pattern, or
"#"-like marks — this is a NORMAL form convention showing those cells
are simply unused. It does NOT mean the code itself (in the earlier
filled cells) is invalid, crossed out, or should be ignored. If you
see a valid code followed by hatched/marked empty cells, still extract
the code normally from the filled cells.

Valid values ONLY: TDL, 048, 046, 019, 005, 020, L99, E30
Corrections: TDI -> TDL, 48 -> 048
TDL handwritten variants: T looks like 1/I/F, D looks like O/0/B, L looks like I/1/7

Return ONLY the code (one of the valid values above), nothing else — no
explanation, no extra text.
If no valid code is visible anywhere in this image, return exactly: NESSUNO
"""

# ATTENZIONE: coordinate STIMATE, non ancora calibrate su una ricetta reale
# come fatto per le date — verificare/aggiustare con lo stesso procedimento
# (debug_crop_date.py) prima di fidarsene in produzione.
ESENZIONE_ROI = {
    "x1": 118, "y1": 282, "x2": 585, "y2": 427,
    "descrizione": "Area di ricerca CODICE ESENZIONE (calibrata su ricetta reale)"
}

PROMPT_CROP_TOTALE = """This image is a cropped region from the bottom-right corner of an Italian medical prescription, containing a small handwritten box labeled "GALEN." / "DIR.CHIAM." / "ALTRO" (or similar), with a handwritten decimal number written inside or next to it.

This number is the "totale_prescrizione" — a price/total value, handwritten,
usually with 2 decimal digits (e.g. "106,00", "162,24", "51,00"), typically
in the range of roughly 50 to 300.

=====================================================================
READ CAREFULLY, ONE DIGIT AT A TIME. This is a small handwritten number
and easy to misread — do not guess based on a first impression. Look at
each digit individually, left to right, including the two decimal
digits after the comma. Pay attention to handwriting styles that can
be ambiguous: a "1" can look like a "7" without the crossbar, a "0"
can look like a "6" if the loop isn't fully closed, a "4" can look
like a "9" if open at the top.
If, after reading carefully, you are not confident in even one digit,
return "INCERTO" rather than guessing — a wrong number is worse than
admitting uncertainty.
=====================================================================

Read ONLY the handwritten number in this specific box. Convert the decimal
separator to a period (e.g. "106,00" -> "106.00").

Return ONLY the number, nothing else — no explanation, no currency symbol.
If the number is not clearly readable, return exactly: INCERTO
"""

TOTALE_ROI = {
    "x1": 1177, "y1": 1110, "x2": 1629, "y2": 1350,
    "descrizione": "Area di ricerca totale_prescrizione (GALEN/DIR.CHIAM/ALTRO, calibrata su ricetta reale)"
}

PROMPT_CROP_TESTO_PRESCRIZIONE = """This image is a cropped region from the center-left area of an Italian medical prescription, containing the free-text "PRESCRIZIONE" section written by the doctor (drug composition, dosage, method, patient notes).

Transcribe ALL the text visible in this image EXACTLY as written, preserving
line breaks where they naturally occur. This is typically several lines of
text about the cannabis-based drug prescribed, its composition (THC/CBD
percentages), extraction method, dosage, and sometimes a note about the
patient's non-responsiveness to standard treatments.

Return ONLY the transcribed text, nothing else — no explanation, no
additional commentary, no JSON formatting.
If the text is not clearly readable, return exactly: INCERTO
"""

TESTO_PRESCRIZIONE_ROI = {
    "x1": 16, "y1": 450, "x2": 1200, "y2": 800,
    "descrizione": "Area di ricerca testo_prescrizione (blocco centrale-sinistro, calibrata su ricetta reale)"
}

PROMPT_CROP_CODICE_FISCALE = """This image is a cropped region from the top-right area of an Italian medical prescription, containing the "CODICE FISCALE" (tax code) grid — a row of 16 boxes, each with one letter or digit.

Structure: EXACTLY 16 characters, pattern LLLLLLNNLNNLNNNL (L=letter, N=number).
Positions: 1-6 letters, 7-8 numbers, 9 letter, 10-11 numbers, 12 letter, 13-15 numbers, 16 letter.
Position 9 (birth month code) can ONLY be one of: A B C D E H L M P R S T

Positional disambiguation (a character in a LETTER position that looks like
a digit, or vice versa, should be corrected using this table):
  If a letter position shows a digit: 1->I/L, 0->O/D, 8->B, 6->G/C, 5->S, 2->Z, 4->A
  If a number position shows a letter: I/L->1, O/D->0, B->8, G/C->6, S->5, Z->2, A->4

READ EVERY ONE OF THE 16 BOXES INDIVIDUALLY, left to right, one at a time —
do not skip repeated-looking letters (e.g. two identical letters in a row
are common and both must be read, not collapsed into one).

MANDATORY SELF-CHECK: count the characters in your answer. If not exactly
16, go back and re-read the boxes one by one until you have exactly 16.

Return ONLY the 16-character code, nothing else — no spaces, no explanation.
If not clearly readable, return exactly: INCERTO
"""

# ATTENZIONE: coordinate STIMATE, non ancora calibrate su una ricetta reale
# — stesso procedimento già usato per le altre ROI: generare una pagina
# intera, individuare a occhio la griglia CODICE FISCALE in alto a destra
# (sotto i due barcode), e correggere qui.
CODICE_FISCALE_ROI = {
    "x1": 900, "y1": 150, "x2": 1650, "y2": 280,
    "descrizione": "Area di ricerca CODICE FISCALE (griglia in alto a destra — coordinate da calibrare)"
}

PROMPT_CROP_NOME_ASSISTITO = """This image is a cropped region from the top-left area of an Italian medical prescription, containing the "COGNOME E NOME DELL'ASSISTITO" (patient name) field.

The name may be a full name, OR initials/an alphanumeric code (e.g. "-485TDBA", "485TDBA") when privacy shortening is used. Transcribe it exactly as written, removing any leading dash.

IMPORTANT — sometimes what is written here is NOT a name at all, but the
patient's full CODICE FISCALE (16 characters, mixing letters and
digits) written by hand as a privacy-preserving identifier instead of
the real name. If what you see looks like a 16-character alphanumeric
code rather than a readable name, read it with the SAME care as a tax
code: go character by character, do not guess based on what "looks
like" a plausible name, and pay attention to easily confused
letter/digit pairs (0/O, 1/I/L, 8/B, 5/S, 2/Z, 6/G).

Return ONLY the name/code as written, nothing else — no explanation.
If completely empty or not readable, return exactly: INCERTO
"""

# ATTENZIONE: coordinate STIMATE, non ancora calibrate su una ricetta reale —
# stesso procedimento delle altre ROI: individuare a occhio il campo
# "COGNOME E NOME DELL'ASSISTITO" in alto a sinistra, e correggere qui.
NOME_ASSISTITO_ROI = {
    "x1": 0, "y1": 0, "x2": 550, "y2": 150,
    "descrizione": "Area di ricerca nome_cognome_assistito (in alto a sinistra — coordinate da calibrare)"
}

# ─── Profili farmacia: regole di lettura specifiche per ogni campo etichetta ──
# Basati sull'osservazione diretta di un'etichetta reale per ciascuna delle
# 6 farmacie attualmente nel flusso. Ogni voce e' un blocco di istruzioni
# testuali (non coordinate pixel, che non abbiamo modo di calibrare senza
# test dal vivo) da iniettare nel prompt della chiamata Qwen dedicata
# all'etichetta — lasciamo al modello il compito di localizzare i campi
# dentro l'area ampia della fascia inferiore, seguendo la regola giusta
# invece di una generica valida per tutte le farmacie indistintamente.
PROFILI_FARMACIA = {
    "Farmacia Introini dr. Paolo & C. Sas": """
LAYOUT: "Prep." on one line with the preparation number, "del [date]"
on the line directly below — this pair is the preparation date.
A SEPARATE box to the right/below shows "Utilizzare entro [date]" —
that is the EXPIRY date, do not confuse it with the preparation date.
Prices: single column S / O / AS / R / IV / EUR (or €), one value each.
Patient name: after "Sig." on the label. Doctor name: after "Dott." on
the label (or the doctor stamp name as last resort if the label field
is unreadable/absent).
Avvertenze: near a pictogram (car/driving or trash-bin icon) on the
label, e.g. wording about doping/driving/storage.
""",

    "Farmacia Mazzucchelli F. & C. Snc": """
LAYOUT — DIFFERENT FROM OTHER PHARMACIES: "Utilizzare entro: [date]"
appears FIRST (to the left), immediately followed on the SAME LINE by
"Preparazione[num] del [date]" (the word "Preparazione" is spelled out
in full and is directly attached to the number, with no space) — the
preparation date is the one after "del" in THIS second part, NOT the
"Utilizzare entro" date that comes first on the line.
WARNING: a separate long paragraph on this label (near the price
table) starts with "Questa preparazione è stata effettuata in area a
contaminazione controllata..." — this ALSO contains the word
"preparazione" but is NOT the Prep./date field, ignore it entirely.
Prices: single column S / O / AS / R / IV, standard layout.
Patient name: after "Sig." — may be a CF-style alphanumeric code
instead of a real name; read it character by character with the same
care as a tax code if so.
Avvertenze wording here tends to start with "Attenersi alle dosi
consigliate dal medico...".
""",

    "Farmacia Comunale N.2": """
LAYOUT — BLUE LABEL, COMPLETELY DIFFERENT FROM THE OTHERS: there are
TWO separate blue-bordered boxes side by side.
LEFT box: header "Farmacia Comunale 2" — the PREPARATION DATE is
HANDWRITTEN separately, near this header at the TOP of the box (e.g.
a small handwritten date like "4.08.25"). Below that, the line
"PREP N°[num] SCAD.[date]" contains ONLY the preparation NUMBER (not
a date) and the EXPIRY date (SCAD, not the preparation date) — do NOT
use anything from this "PREP N°...SCAD..." line as the preparation
date; the real preparation date is the handwritten one near the top.
Prices (still in the LEFT box): a single row "PREZZO [x] O.P. [x]EUR
S [x]EUR C [x]EUR IVA 10% [x]" followed by "TOT. [x]" — map PREZZO to
sost, O.P. to on, C to rec, IVA to iva, TOT to tot (S is stampa/spese,
ignore it).
Patient: after "PZ." at the bottom of the left box (often an
alphanumeric code, not a full name). Doctor: after "DR." at the
bottom of the left box.
RIGHT box: separate box titled "Avvertenze:" with the warning text —
extract it from there.
""",

    "Farmacia Pomi di dr. Collivasone A. & C. Snc": """
LAYOUT: "Prep. [num] [date]" all on ONE line (number and date
directly next to each other, no "del" in between) — this is the
preparation date. "UTILIZZARE ENTRO [date]" on the line below is a
DIFFERENT date (expiry), do not confuse it with the preparation date.
Prices: single column S / O / AS / R / IV, standard layout.
Patient name: after "Sig.". Doctor name: after "Dott.".
""",

    "Farmacia Ramella dott.ri G. e A. Sas": """
LAYOUT: "Prep." on one line with the preparation number, "del [date]"
on the line directly below — this pair is the preparation date.
"Utilizzare entro [date]" nearby is a DIFFERENT date (expiry), do not
confuse it with the preparation date.
PRICES — SPECIAL RULE FOR THIS PHARMACY ONLY: the price column has
EXTRA rows "OP", "OI", "OS" instead of a single "O" row — sum
OP + OI + OS together and use that SUM as etichetta_prezzo_on (output
only the final computed number, never the arithmetic expression).
Patient name: after "Sig.". Doctor name: after "Dott.".
""",

    "Farmacia Peroni dr Antonio E. & C. Sas": """
LAYOUT: "Prep." on one line with the preparation number, "del [date]"
on the line directly below — this pair is the preparation date.
"Utilizzare entro [date]" nearby is a DIFFERENT date (expiry), do not
confuse it with the preparation date.
Prices: single column S / O / AS / R / IV, standard layout.
Patient name: after "Sig.". Doctor name: after "Dott." on the label,
or the doctor stamp name as last resort if the label field is
unreadable/absent.
""",

    "Farmacia Di Lora Srl": """
LAYOUT: "Utilizzare Entro: [date]" appears on ONE line, immediately
followed on the NEXT line below by "Preparazione [num] del [date]"
(word spelled in full, WITH a space before the number) — this second
line's date (after "del") is the preparation date. The "Utilizzare
Entro" date on the line ABOVE it is a DIFFERENT date (expiry) — do
not confuse the two, even though "Utilizzare Entro" appears first
(above) here, opposite order from most other pharmacies.
Prices: standard S / O / AS / R / IV / total, but may be split across
TWO rows on the page instead of one column (e.g. "S. [x] AS [x] IV [x]"
on one row, "O. [x] R. [x] [total]" on the next) — the field meaning
mapping is still the same regardless of the row layout.
Patient name: after "Sig.". Doctor name: after "Dott.".
Avvertenze: near a pictogram, standard doping/sportiva wording — a
posology note (e.g. "POSOLOGIA: 0,57 mg THC...") may appear in the
same text block, that is fine to include as part of the warning text.
""",
}

# Ordine di tentativo quando la farmacia non e' riconosciuta: prova tutti
# i profili finche' uno non produce un risultato plausibile
PROFILO_GENERICO = """
LAYOUT NON RICONOSCIUTO — segui queste indicazioni generali (valide per
la maggior parte delle farmacie viste finora), invece delle regole
specifiche di una farmacia in particolare:
"Prep." (o "Preparazione") seguito da un numero di preparazione, poi una
data — a volte sulla stessa riga, a volte introdotta da "del" sulla riga
sotto. Una "Utilizzare entro"/data di scadenza separata è un campo
DIVERSO (non confondere le due date). I prezzi sono di solito in una
colonna con sigle tipo S / O / AS / R / IV (o un elenco simile),
seguiti da un totale. Il nome del paziente segue di solito "Sig.", il
nome del medico segue di solito "Dott."/"Dott.ssa". Le avvertenze si
trovano di solito vicino a un pittogramma (auto/guida o cestino).
Se non riesci a individuare con sicurezza uno di questi elementi in
QUESTA immagine specifica, lascialo vuoto — non indovinare basandoti su
schemi visti altrove.
"""

PROMPT_ETICHETTA_FARMACIA_TEMPLATE = """This image shows the lower portion of an Italian medical prescription page, containing the pharmacy label with preparation date, expiry date, prices, patient/doctor names and warnings.

You are told this label follows the KNOWN LAYOUT of a SPECIFIC pharmacy. Follow this layout guidance carefully to locate each field correctly:

{regola_farmacia}

=====================================================================
MANDATORY ANCHOR CHECK — READ THIS BEFORE ANSWERING ANY FIELD.
The layout guidance above describes what a label from this pharmacy
LOOKS LIKE WHEN PRESENT — it does NOT mean a label is actually present
on THIS specific image. Some prescriptions have no label pasted at all.

For EACH of these 5 field groups, first locate the literal anchor text
for that group SOMEWHERE in this image. If the anchor is not found,
ALL fields in that group MUST be left empty ("") — do not guess,
infer, or borrow a value from elsewhere on the page (e.g. never use a
date stamp from a different part of the page as a substitute for a
missing "Prep." date; never invent price numbers when no price table
is visible).

1. Anchor "Prep." / "Preparazione" / "del" -> only if found, extract data_etichetta_preparazione
2. Anchor a price-column label (e.g. "S", "O", "AS", "R", "IV", "PREZZO", "TOT" or similar, near numeric values) -> only if found, extract etichetta_prezzo_sost/on/rec/iva/tot
3. Anchor "Scadenza" / "Utilizzare entro" / "SCAD." -> only if found, extract etichetta_data_scadenza
4. Anchor "Avvertenze" (or a doping/driving warning pictogram with text) -> only if found, extract etichetta_avvertenze
5. Anchor "Sig." / "Dott." / "Dott.ssa" -> only if found, extract the corresponding patient/doctor name

If NONE of these anchors are found anywhere in this image, the pharmacy
label is simply NOT PRESENT on this page — return ALL 10 fields as
empty strings. This is common and expected on some prescriptions;
returning everything empty in that case is the CORRECT answer, not a
failure to find something that should be there.
=====================================================================

DATE FORMAT REMINDER: dates are Italian format DD/MM/YY on the label —
convert to GG/MM/AAAA (day first, add "20" to the 2-digit year). This
document batch is from 2025 — do not default to any other year (2021,
2023...) if uncertain; read the actual digits carefully.

Extract these fields, using "" for any field not found/not readable
(never guess or leave a stale value):
- data_etichetta_preparazione (GG/MM/AAAA)
- etichetta_data_scadenza (GG/MM/AAAA)
- etichetta_nome_cognome_paziente
- etichetta_nome_cognome_medico
- etichetta_prezzo_sost
- etichetta_prezzo_on
- etichetta_prezzo_rec
- etichetta_prezzo_iva
- etichetta_prezzo_tot
- etichetta_avvertenze

Return ONLY a raw JSON object with exactly these 10 keys, nothing else
— no markdown, no backticks, no explanation. Decimal prices use a
period, never a comma.
"""

PROMPT_CROP_DATA_PREPARAZIONE = """This image is a cropped region containing the "Prep." field from an Italian pharmacy label.
=====================================================================
CRITICAL — DO NOT CONFUSE THE PREPARATION NUMBER WITH THE DATE.
Right after "Prep." (or "PREP N°") there are usually TWO separate
pieces of information, in this order:
1. A plain PREPARATION NUMBER: 4-5 digits, NO slashes, NO punctuation
   between them. This is NOT a date — ignore it completely, do not use
   any of its digits for your answer.
2. THEN, separately, the actual preparation DATE: always in DD/MM/YY
   format WITH TWO SLASHES. This second group, with the slashes, is
   what you want.

Typical patterns (structure illustration only — these use placeholder
digits, NEVER copy these exact numbers, always read the ACTUAL digits
shown in THIS specific image):
  "Prep. NNNNN  DD/MM/YY"           -> number=NNNNN (ignore), date=DD/MM/YY (use this)
  "Prep. NNNNN" then "del DD/MM/YY" -> number=NNNNN (ignore), date=DD/MM/YY (use this)
  "PREP N°NNNNN ... SCAD. DD/MM/YY" -> "PREP N°" number is NOT the date;
                                        "SCAD." here is a DIFFERENT date
                                        (expiry, not preparation) — ignore it too
Only extract the date that has the DD/MM/YY slash format and is
associated with "Prep." / "del" — never the plain digit group right
after "Prep.", and never a separate "SCAD."/"UTILIZZARE ENTRO" expiry
date if one is also visible in this crop. NEVER output the literal
placeholder characters "NNNNN", "DD/MM/YY" or similar — read the real
digits printed/handwritten on THIS document.

YEAR WARNING: this specific document batch is from 2025. There is a
known tendency to misread the year as some other year (2021, 2023...)
even when "2025" is what is actually printed/stamped — do NOT default
to any year other than what you actually see. Read the last two digits
of the year carefully, one at a time, and double-check before
answering: if you find yourself about to write a year that is not
2025, stop and re-examine the actual digits on the document instead of
trusting your first impression.
=====================================================================

Convert the date found to GG/MM/AAAA format (2-digit day, 2-digit
month, 4-digit year — add "20" to a 2-digit year).

Return ONLY the date in GG/MM/AAAA format, nothing else — no explanation.
If no valid preparation date is visible in this crop, return exactly: INCERTO
"""


def _distanza_levenshtein_semplice(a: str, b: str) -> int:
    """Distanza di Levenshtein minima tra due stringhe (implementazione
    locale, stessa logica gia' usata in ocr_cannabis.py per non
    dipendere da un file esterno)."""
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


def data_plausibile(data_str: str) -> bool:
    """
    Verifica che una data GG/MM/AAAA sia plausibile: non solo formato corretto,
    ma anche giorno/mese/anno che esistono davvero e rientrano in un range
    ragionevole per una ricetta (evita che un artefatto di lettura come
    "35/13/2025" o un anno assurdo venga accettato solo perché 'ha la forma giusta').
    """
    if not data_str or data_str == "OCR_INCERTO":
        return False

    m = re.match(r'^(\d{2})/(\d{2})/(\d{4})$', data_str)
    if not m:
        return False

    gg, mm, aaaa = int(m.group(1)), int(m.group(2)), int(m.group(3))

    if not (1 <= mm <= 12):
        return False

    giorni_nel_mese = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if not (1 <= gg <= giorni_nel_mese[mm - 1]):
        return False

    # Range plausibile per ricette cannabis terapeutica ATS Insubria
    anno_corrente = datetime.now().year
    if not (2020 <= aaaa <= anno_corrente + 1):
        return False

    return True


def estrai_crop(img: Image.Image, roi: dict, padding: int = 10) -> Image.Image:
    """Ritaglia una ROI dall'immagine con padding."""
    x1 = max(0, roi["x1"] - padding)
    y1 = max(0, roi["y1"] - padding)
    x2 = min(img.width, roi["x2"] + padding)
    y2 = min(img.height, roi["y2"] + padding)

    if x2 <= x1 or y2 <= y1:
        log.warning(f"ROI fuori dai limiti: {roi} su immagine {img.width}x{img.height}")
        return None

    return img.crop((x1, y1, x2, y2))


def trova_box_testo(img: Image.Image, reader, pattern: str = r'DATA',
                     escludi_pattern: str = r'SPEDIZ|STRUTTURA|EROGANTE', min_conf: float = 0.15):
    """
    Cerca un testo che CONTENGA 'pattern' (case-insensitive, dopo aver tolto
    caratteri non alfabetici) tramite EasyOCR su tutta l'immagine, escludendo
    i match che contengono 'escludi_pattern' (per non confondere la label
    "DATA" del box prescrizione con quella del timbro "DATA SPEDIZIONE /
    TIMBRO STRUTTURA EROGANTE"). Match per contenuto invece che esatto,
    perché EasyOCR può unire la label ad altro testo vicino sulla stessa riga
    o introdurre piccoli artefatti.

    Restituisce la bounding box (x1, y1, x2, y2) del match con confidenza
    più alta, o None se non trovato.
    """
    arr = np.array(img.convert("L"))
    results = reader.readtext(arr)

    tutti_i_testi = [(text, conf) for _, text, conf in results]
    log.debug(f"  EasyOCR ha letto {len(tutti_i_testi)} blocchi di testo sulla pagina")

    candidati = []
    for bbox, text, conf in results:
        pulito = re.sub(r'[^A-Za-z]', '', text).upper()
        if conf < min_conf:
            continue
        if not re.search(pattern, pulito):
            continue
        if escludi_pattern and re.search(escludi_pattern, pulito):
            continue
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        candidati.append((min(xs), min(ys), max(xs), max(ys), conf, text))

    if not candidati:
        # Diagnostica: mostra ogni testo che contiene "DATA" anche se scartato,
        # per capire se il problema è la soglia di confidenza o l'esclusione
        quasi = [(t, c) for t, c in tutti_i_testi if "DATA" in re.sub(r'[^A-Za-z]', '', t).upper()]
        if quasi:
            log.warning(f"  Nessun candidato valido, ma trovati testi con 'DATA' scartati: {quasi}")
        return None

    candidati.sort(key=lambda c: -c[4])
    x1, y1, x2, y2, conf, testo_trovato = candidati[0]
    log.info(f"  Label trovata: {testo_trovato!r} (confidenza {conf:.2f})")

    # Se il blocco rilevato da EasyOCR include testo PRIMA del pattern
    # cercato (es. EasyOCR unisce in un solo blocco "Utilizzare entro:
    # ... Preparazione... del ...", riga unica osservata su alcuni
    # layout come Farmacia Mazzucchelli), sposto proporzionalmente il
    # bordo sinistro fino a dove inizia il match nel testo, invece di
    # partire dall'inizio dell'intero blocco — altrimenti il crop
    # includerebbe anche il campo estraneo prima del pattern cercato.
    pulito_trovato = re.sub(r'[^A-Za-z]', '', testo_trovato).upper()
    match_in_testo = re.search(pattern, pulito_trovato)
    if match_in_testo and len(pulito_trovato) > 0 and match_in_testo.start() > 0:
        frazione_inizio = match_in_testo.start() / len(pulito_trovato)
        x1_originale = x1
        x1 = x1 + frazione_inizio * (x2 - x1)
        log.info(
            f"  Blocco unito ad altro testo prima del match: bordo sinistro "
            f"spostato da {x1_originale:.0f} a {x1:.0f} (frazione {frazione_inizio:.2f})"
        )

    return (x1, y1, x2, y2)


class DateCorrector:
    """
    Correttore per campi letti da crop dedicati sull'etichetta e su altre
    zone della pagina (codice esenzione, totale prescrizione, testo
    prescrizione, codice fiscale, nome assistito, data etichetta
    preparazione, campi etichetta per farmacia). NON gestisce più
    data_prescrizione/data_emissione: quei due campi vengono presi
    ESCLUSIVAMENTE dal file Excel Regione in merge_regione.py — tutta la
    vecchia pipeline di correzione (TrOCR fine-tuned, voto a tre fonti)
    è stata rimossa perché il suo risultato veniva comunque scartato.
    """

    def __init__(self, qwen_model: str = "qwen3-vl:8b", usa_qwen_su_crop: bool = True):
        """
        Inizializza il correttore.

        Args:
            qwen_model: modello Ollama da usare per la lettura dei crop.
                Il default qui vale solo per uso standalone di questa classe
                (es. test/debug diretti su date_corrector.py) — quando viene
                istanziata da ocr_cannabis.py::get_date_corrector(), riceve
                sempre esplicitamente MODELLO_PESANTE, che ha priorità.
            usa_qwen_su_crop: se False, disattiva tutte le chiamate Qwen
                   sui crop (nessuna correzione viene tentata) — utile
                   solo per test/debug
        """
        self.qwen_model = qwen_model
        self.usa_qwen_su_crop = usa_qwen_su_crop
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self._easyocr_reader = None

    def _get_easyocr_reader(self):
        """Carica (una sola volta) il reader EasyOCR usato per l'ancoraggio dinamico."""
        if self._easyocr_reader is None:
            try:
                import easyocr
                log.info("Caricamento EasyOCR (condiviso da tutti gli ancoraggi dinamici: date, esenzione, Prep.)...")
                self._easyocr_reader = easyocr.Reader(["it"], gpu=(self.device.type == "cuda"))
            except Exception as e:
                log.warning(f"EasyOCR non disponibile per ancoraggio dinamico: {e}")
                self._easyocr_reader = False  # sentinella: non ritentare ogni volta
        return self._easyocr_reader or None

    def _pdf_to_image(self, pdf_path: Path) -> Image.Image:
        """Converte la prima pagina del PDF in immagine PIL."""
        doc  = fitz.open(str(pdf_path))
        page = doc[0]
        pix  = page.get_pixmap(matrix=fitz.Matrix(IMAGE_ZOOM, IMAGE_ZOOM))
        img  = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        doc.close()
        return img

    def _pdf_to_image_alta_risoluzione(self, pdf_path: Path, zoom: float) -> Image.Image:
        """
        Come _pdf_to_image, ma rirenderizza la pagina a uno ZOOM PIÙ ALTO
        di quello standard — usato quando serve più dettaglio su una zona
        piccola con cifre scritte a mano difficili da leggere (es.
        totale_prescrizione). Semplicemente ritagliare la stessa immagine
        già renderizzata a zoom standard non aggiungerebbe informazione
        reale: qui invece si riparte dal PDF vettoriale, quindi il crop
        risultante ha davvero più pixel utili, non solo interpolati.
        """
        doc  = fitz.open(str(pdf_path))
        page = doc[0]
        pix  = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        img  = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        doc.close()
        return img

    def _predici_esenzione_da_crop(self, crop: Image.Image) -> str:
        """
        Legge il codice esenzione da un crop dedicato (area di intestazione
        in alto a sinistra), tramite una chiamata Qwen mirata e indipendente
        da quella principale — usata come recupero quando il prompt
        principale lascia il campo vuoto (es. per via del tratteggio nelle
        caselle inutilizzate che confonde il modello quando deve gestire
        contemporaneamente altri 14 campi diversi).
        Restituisce uno dei valori validi, o stringa vuota se non trovato.
        """
        if not self.usa_qwen_su_crop:
            return ""

        try:
            buf = io.BytesIO()
            crop.convert("RGB").save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

            response = ollama.chat(
                model=self.qwen_model,
                messages=[{
                    "role": "user",
                    "content": PROMPT_CROP_ESENZIONE + _suffisso_no_think(self.qwen_model),
                    "images": [img_b64]
                }],
                think=False,
                options={"temperature": 0.0}
            )
            testo = _contenuto_pulito(response).upper()
            log.info(f"    [codice_esenzione] risposta grezza crop: {testo!r}")

            if "NESSUNO" in testo:
                return ""

            for valore in VALORI_ESENZIONE_VALIDI:
                if valore in testo:
                    return valore

            # Contenimento esatto fallito: prova un confronto fuzzy
            # (distanza 1) tra il testo letto e ciascun valore valido —
            # copre refusi come "TOL" invece di "TDL" (osservato in un
            # caso reale), non solo scambi esatti gia' noti.
            testo_pulito = re.sub(r'[^A-Z0-9]', '', testo)
            for valore in VALORI_ESENZIONE_VALIDI:
                if _distanza_levenshtein_semplice(testo_pulito, valore) <= 1:
                    log.info(f"    [codice_esenzione] '{testo_pulito}' corretto fuzzy in '{valore}'")
                    return valore

            return ""

        except Exception as e:
            log.warning(f"Errore lettura crop esenzione: {e}")
            return ""

    def correggi_esenzione(self, dati: dict, pdf_path: Path) -> dict:
        """
        Prova SEMPRE a leggere codice_esenzione con un crop dedicato e una
        chiamata Qwen isolata e mirata solo su questo campo — se il crop
        produce un valore valido, ha PRIORITÀ e sovrascrive quello del
        gruppo principale (anche se già presente), perché il crop mirato
        si è dimostrato più affidabile su questo campo specifico.
        """
        try:
            img = self._pdf_to_image(pdf_path)
        except Exception as e:
            log.error(f"Errore apertura PDF per correggi_esenzione {pdf_path}: {e}")
            return dati

        crop = estrai_crop(img, ESENZIONE_ROI, padding=10)
        if crop is None:
            log.warning("  codice_esenzione: ROI fuori dai limiti della pagina, skip")
            return dati

        valore = self._predici_esenzione_da_crop(crop)
        if valore:
            dati["codice_esenzione"] = valore
            dati["codice_esenzione_risolto_da_crop"] = True
            log.info(f"  codice_esenzione recuperato da crop dedicato: {valore}")
        else:
            log.info("  codice_esenzione: non trovato nemmeno nel crop dedicato, resta vuoto")

        return dati

    def _predici_totale_da_crop(self, crop: Image.Image) -> str:
        """
        Legge totale_prescrizione da un crop dedicato (area GALEN/DIR.CHIAM/
        ALTRO in basso a destra), tramite una chiamata Qwen isolata e mirata.
        Restituisce il numero come stringa (formato con punto decimale),
        o stringa vuota se non leggibile.
        """
        if not self.usa_qwen_su_crop:
            return ""

        try:
            buf = io.BytesIO()
            crop.convert("RGB").save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

            response = ollama.chat(
                model=self.qwen_model,
                messages=[{
                    "role": "user",
                    "content": PROMPT_CROP_TOTALE + _suffisso_no_think(self.qwen_model),
                    "images": [img_b64]
                }],
                think=False,
                options={"temperature": 0.0}
            )
            testo = _contenuto_pulito(response)
            log.info(f"    [totale_prescrizione] risposta grezza crop: {testo!r}")

            if "INCERTO" in testo.upper():
                return ""

            # Estrae solo la parte numerica (cifre, punto, virgola), poi
            # normalizza la virgola decimale in punto
            numero = re.sub(r"[^0-9.,]", "", testo).replace(",", ".")

            # Più di un punto decimale = lettura ambigua o malformata (es.
            # il modello ha letto/concatenato due numeri diversi) — MEGLIO
            # SCARTARE che indovinare quale tenere. A differenza di quanto
            # si pensava prima, qui NON serve gestire un separatore delle
            # migliaia: i valori reali di questo campo sono sempre ben
            # sotto le poche centinaia di euro, quindi un secondo punto
            # è sempre un segnale di lettura fallita, non di formattazione
            # legittima — concatenare ciecamente produceva numeri assurdi
            # a 7+ cifre (es. "10511105.11", osservato in un caso reale).
            if numero.count(".") > 1:
                log.warning(f"    [totale_prescrizione] più di un punto nella lettura ({numero!r}) — scartato come ambiguo")
                return ""

            if not numero:
                return ""

            # Controllo di plausibilità sul range: i valori reali osservati
            # finora stanno sempre ben sotto le poche centinaia di euro
            try:
                valore_float = float(numero)
            except ValueError:
                return ""
            if not (0 < valore_float <= 1000):
                log.warning(f"    [totale_prescrizione] valore fuori range plausibile ({valore_float}) — scartato")
                return ""

            return numero

        except Exception as e:
            log.warning(f"Errore lettura crop totale_prescrizione: {e}")
            return ""

    def correggi_totale(self, dati: dict, pdf_path: Path) -> dict:
        """
        Prova SEMPRE a leggere totale_prescrizione con un crop dedicato,
        rirenderizzato ad ALTA RISOLUZIONE (zoom raddoppiato rispetto allo
        standard) — le cifre scritte a mano in questa casella sono piccole
        e facili da confondere, quindi più risoluzione aiuta concretamente.
        Se produce un valore valido, ha PRIORITÀ e sovrascrive quello del
        gruppo principale (anche se già presente).
        """
        zoom_alto = IMAGE_ZOOM * 2
        try:
            img = self._pdf_to_image_alta_risoluzione(pdf_path, zoom_alto)
        except Exception as e:
            log.error(f"Errore apertura PDF per correggi_totale {pdf_path}: {e}")
            return dati

        fattore_scala = zoom_alto / IMAGE_ZOOM
        roi_scalato = {
            "x1": int(TOTALE_ROI["x1"] * fattore_scala),
            "y1": int(TOTALE_ROI["y1"] * fattore_scala),
            "x2": int(TOTALE_ROI["x2"] * fattore_scala),
            "y2": int(TOTALE_ROI["y2"] * fattore_scala),
        }

        crop = estrai_crop(img, roi_scalato, padding=20)
        if crop is None:
            log.warning("  totale_prescrizione: ROI fuori dai limiti della pagina, skip")
            return dati

        valore = self._predici_totale_da_crop(crop)
        if valore:
            dati["totale_prescrizione"] = valore
            dati["totale_prescrizione_risolto_da_crop"] = True
            log.info(f"  totale_prescrizione recuperato da crop dedicato (alta risoluzione): {valore}")
        else:
            log.info("  totale_prescrizione: non trovato nemmeno nel crop dedicato, resta invariato")

        return dati

    def _predici_testo_prescrizione_da_crop(self, crop: Image.Image) -> str:
        """
        Legge testo_prescrizione da un crop dedicato (area centrale-sinistra
        con il testo libero della prescrizione), tramite una chiamata Qwen
        isolata e mirata. Restituisce il testo trascritto, o stringa vuota
        se non leggibile.
        """
        if not self.usa_qwen_su_crop:
            return ""

        try:
            buf = io.BytesIO()
            crop.convert("RGB").save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

            response = ollama.chat(
                model=self.qwen_model,
                messages=[{
                    "role": "user",
                    "content": PROMPT_CROP_TESTO_PRESCRIZIONE + _suffisso_no_think(self.qwen_model),
                    "images": [img_b64]
                }],
                think=False,
                options={"temperature": 0.0}
            )
            testo = _contenuto_pulito(response)
            log.info(f"    [testo_prescrizione] risposta grezza crop: {testo!r}")

            if testo.upper() == "INCERTO":
                return ""

            return testo

        except Exception as e:
            log.warning(f"Errore lettura crop testo_prescrizione: {e}")
            return ""

    def correggi_testo_prescrizione(self, dati: dict, pdf_path: Path) -> dict:
        """
        Prova SEMPRE a leggere testo_prescrizione con un crop dedicato — se
        produce un testo non vuoto, ha PRIORITÀ e sovrascrive quello del
        gruppo principale (anche se già presente).
        """
        try:
            img = self._pdf_to_image(pdf_path)
        except Exception as e:
            log.error(f"Errore apertura PDF per correggi_testo_prescrizione {pdf_path}: {e}")
            return dati

        crop = estrai_crop(img, TESTO_PRESCRIZIONE_ROI, padding=10)
        if crop is None:
            log.warning("  testo_prescrizione: ROI fuori dai limiti della pagina, skip")
            return dati

        valore_crop = self._predici_testo_prescrizione_da_crop(crop)

        if not valore_crop:
            log.info("  testo_prescrizione: crop dedicato non ha prodotto nulla, mantengo il valore principale")
            return dati

        dati["testo_prescrizione"] = valore_crop
        dati["testo_prescrizione_risolto_da_crop"] = True
        log.info(f"  testo_prescrizione: usato il crop dedicato ({len(valore_crop)} caratteri)")
        return dati

    def _predici_codice_fiscale_da_crop(self, crop: Image.Image) -> str:
        """Legge codice_fiscale da un crop dedicato sulla griglia CF, tramite chiamata Qwen isolata."""
        if not self.usa_qwen_su_crop:
            return ""
        try:
            buf = io.BytesIO()
            crop.convert("RGB").save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

            response = ollama.chat(
                model=self.qwen_model,
                messages=[{
                    "role": "user",
                    "content": PROMPT_CROP_CODICE_FISCALE + _suffisso_no_think(self.qwen_model),
                    "images": [img_b64]
                }],
                think=False,
                options={"temperature": 0.0}
            )
            testo = _contenuto_pulito(response).upper()
            log.info(f"    [codice_fiscale] risposta grezza crop: {testo!r}")

            if "INCERTO" in testo:
                return ""
            # Tiene solo caratteri alfanumerici, poi valida la lunghezza attesa
            pulito = re.sub(r"[^0-9A-Z]", "", testo)
            return pulito if len(pulito) == 16 else ""

        except Exception as e:
            log.warning(f"Errore lettura crop codice_fiscale: {e}")
            return ""

    def correggi_codice_fiscale(self, dati: dict, pdf_path: Path) -> dict:
        """
        Prova SEMPRE a leggere codice_fiscale con un crop dedicato — se
        produce un valore valido (16 caratteri), ha PRIORITÀ e sovrascrive
        quello del gruppo principale (anche se già presente e già da 16
        caratteri).
        """
        try:
            img = self._pdf_to_image(pdf_path)
        except Exception as e:
            log.error(f"Errore apertura PDF per correggi_codice_fiscale {pdf_path}: {e}")
            return dati

        crop = estrai_crop(img, CODICE_FISCALE_ROI, padding=10)
        if crop is None:
            log.warning("  codice_fiscale: ROI fuori dai limiti della pagina, skip")
            return dati

        valore = self._predici_codice_fiscale_da_crop(crop)
        if valore:
            dati["codice_fiscale"] = valore
            dati["codice_fiscale_risolto_da_crop"] = True
            log.info(f"  codice_fiscale recuperato da crop dedicato: {valore}")
        else:
            log.info("  codice_fiscale: non trovato/non valido nemmeno nel crop dedicato, resta invariato")

        return dati

    def _predici_nome_assistito_da_crop(self, crop: Image.Image) -> str:
        """Legge nome_cognome_assistito da un crop dedicato, tramite chiamata Qwen isolata."""
        if not self.usa_qwen_su_crop:
            return ""
        try:
            buf = io.BytesIO()
            crop.convert("RGB").save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

            response = ollama.chat(
                model=self.qwen_model,
                messages=[{
                    "role": "user",
                    "content": PROMPT_CROP_NOME_ASSISTITO + _suffisso_no_think(self.qwen_model),
                    "images": [img_b64]
                }],
                think=False,
                options={"temperature": 0.0}
            )
            testo = _contenuto_pulito(response)
            log.info(f"    [nome_cognome_assistito] risposta grezza crop: {testo!r}")

            if testo.upper() == "INCERTO":
                return ""
            return testo

        except Exception as e:
            log.warning(f"Errore lettura crop nome_cognome_assistito: {e}")
            return ""

    def correggi_nome_assistito(self, dati: dict, pdf_path: Path) -> dict:
        """
        Se nome_cognome_assistito è vuoto dopo l'estrazione principale,
        prova a recuperarlo con un crop dedicato — stesso principio già
        usato per codice_esenzione/totale_prescrizione. Ha PRIORITÀ e
        sovrascrive quello del gruppo principale (anche se già presente).
        """
        try:
            img = self._pdf_to_image(pdf_path)
        except Exception as e:
            log.error(f"Errore apertura PDF per correggi_nome_assistito {pdf_path}: {e}")
            return dati

        crop = estrai_crop(img, NOME_ASSISTITO_ROI, padding=10)
        if crop is None:
            log.warning("  nome_cognome_assistito: ROI fuori dai limiti della pagina, skip")
            return dati

        valore = self._predici_nome_assistito_da_crop(crop)
        if valore:
            dati["nome_cognome_assistito"] = valore
            dati["nome_cognome_assistito_risolto_da_crop"] = True
            log.info(f"  nome_cognome_assistito recuperato da crop dedicato: {valore}")
        else:
            log.info("  nome_cognome_assistito: non trovato nemmeno nel crop dedicato, resta invariato")

        return dati

    def _localizza_prep_e_crop(self, img: Image.Image, reader, search_region_frac=(0.45, 1.0)) -> Image.Image:
        """
        Cerca dinamicamente l'etichetta "Prep." (o "PREP N°" nel layout
        blu) tramite EasyOCR, limitando la ricerca alla FASCIA INFERIORE
        della pagina (stessa banda già usata da etichetta_presence.py per
        rilevare l'etichetta) invece di scansionare l'intera pagina — più
        veloce e senza rischio di falsi match altrove (es. se "PREP"
        comparisse per caso nel testo della prescrizione). La posizione
        esatta DENTRO quella fascia resta comunque dinamica, non un ROI
        fisso, perché varia troppo da farmacia a farmacia (e da ricetta a
        ricetta, a seconda di quanto testo di prescrizione c'è sopra) per
        un ROI statico. Ritaglia una finestra generosa a destra/intorno al
        testo trovato, dove normalmente segue la data di preparazione.
        """
        page_h = img.height
        y_start = int(page_h * search_region_frac[0])
        y_end = int(page_h * search_region_frac[1])
        fascia_inferiore = img.crop((0, y_start, img.width, y_end))

        box = trova_box_testo(fascia_inferiore, reader, pattern=r'PREP', escludi_pattern=r'QUES|EFFETTU|STATA|CERTIFICAT', min_conf=0.15)
        if box is None:
            return None

        # Le coordinate del box sono relative alla fascia ritagliata: le
        # riporto al sistema di riferimento della pagina intera prima di
        # costruire il ROI finale
        x1, y1, x2, y2 = box
        y1 += y_start
        y2 += y_start

        roi = {
            "x1": int(x1),
            "y1": int(y1) - 15,
            "x2": int(x2) + 400,
            "y2": int(y2) + 60,
        }
        return estrai_crop(img, roi, padding=10)

    def _predici_data_preparazione_da_crop(self, crop: Image.Image) -> str:
        """Legge data_etichetta_preparazione dal crop ancorato dinamicamente al testo 'Prep.'."""
        if not self.usa_qwen_su_crop:
            return ""
        try:
            buf = io.BytesIO()
            crop.convert("RGB").save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

            response = ollama.chat(
                model=self.qwen_model,
                messages=[{
                    "role": "user",
                    "content": PROMPT_CROP_DATA_PREPARAZIONE + _suffisso_no_think(self.qwen_model),
                    "images": [img_b64]
                }],
                think=False,
                options={"temperature": 0.0}
            )
            testo = _contenuto_pulito(response)
            log.info(f"    [data_etichetta_preparazione] risposta grezza crop dinamico: {testo!r}")

            # Rimuove eventuale formattazione markdown (**grassetto**, `code`,
            # _corsivo_) che il modello a volte aggiunge attorno alla
            # risposta pur avendo letto il contenuto giusto — altrimenti
            # la regex di data_plausibile() fallisce per un motivo
            # puramente cosmetico, scartando una lettura corretta.
            testo_pulito = testo.strip("*_`").strip()

            if testo_pulito.upper() == "INCERTO":
                return ""
            return testo_pulito if data_plausibile(testo_pulito) else ""

        except Exception as e:
            log.warning(f"Errore lettura crop data_etichetta_preparazione: {e}")
            return ""

    def _localizza_prep_e_crop_in_area(self, img: Image.Image, reader, area_etichetta: tuple) -> Image.Image:
        """
        Cerca il testo "Prep." in un'area ESPANSA attorno alla posizione
        dell'etichetta già individuata da etichetta_presence.py (il modulo
        CODICE/NUMERO su cui la farmacia incolla fisicamente l'etichetta).
        Più precisa e più veloce della fascia generica, perché è ancorata
        allo stesso punto dove sappiamo già che l'etichetta si trova —
        ma va comunque espansa generosamente, perché match_location è
        solo la posizione del singolo modulo sottostante, mentre
        l'etichetta incollata sopra può estendersi ben oltre in ogni
        direzione (soprattutto verso l'alto e verso sinistra).
        """
        x, y, w, h = area_etichetta
        x1 = max(0, x - 50)
        y1 = max(0, y - 250)
        x2 = min(img.width, x + w + 350)
        y2 = min(img.height, y + h + 150)
        area_espansa = img.crop((x1, y1, x2, y2))

        box = trova_box_testo(area_espansa, reader, pattern=r'PREP', escludi_pattern=r'QUES|EFFETTU|STATA|CERTIFICAT', min_conf=0.15)
        if box is None:
            return None

        bx1, by1, bx2, by2 = box
        bx1 += x1
        bx2 += x1
        by1 += y1
        by2 += y1

        roi = {
            "x1": int(bx1),
            "y1": int(by1) - 15,
            "x2": int(bx2) + 400,
            "y2": int(by2) + 60,
        }
        return estrai_crop(img, roi, padding=10)

    def correggi_data_preparazione_dinamico(self, dati: dict, pdf_path: Path, area_etichetta: tuple = None) -> dict:
        """
        Prova SEMPRE a leggere data_etichetta_preparazione ancorando la
        ricerca al testo "Prep." trovato dinamicamente (non a coordinate
        fisse) — se produce una data plausibile, ha PRIORITÀ e sovrascrive
        quella del gruppo principale (anche se già presente).

        area_etichetta: (x, y, w, h) della posizione già individuata da
        etichetta_presence.py, se disponibile. Se fornita, la ricerca è
        ristretta a un'area espansa attorno a quel punto (più precisa e
        veloce); altrimenti (o se lì "Prep." non viene trovato), si
        ripiega sulla ricerca nella fascia inferiore generica della pagina.
        """
        try:
            img = self._pdf_to_image(pdf_path)
        except Exception as e:
            log.error(f"Errore apertura PDF per correggi_data_preparazione_dinamico {pdf_path}: {e}")
            return dati

        reader = self._get_easyocr_reader()
        if reader is None:
            log.warning("  data_etichetta_preparazione: EasyOCR non disponibile, skip ancoraggio dinamico")
            return dati

        crop = None
        if area_etichetta is not None:
            crop = self._localizza_prep_e_crop_in_area(img, reader, area_etichetta)
            if crop is not None:
                log.info("  data_etichetta_preparazione: 'Prep.' trovato nell'area etichetta già individuata")

        if crop is None:
            crop = self._localizza_prep_e_crop(img, reader)
            if crop is not None:
                log.info("  data_etichetta_preparazione: 'Prep.' trovato nella fascia inferiore generica (ripiego)")

        if crop is None:
            log.info("  data_etichetta_preparazione: etichetta 'Prep.' non trovata sulla pagina, resta invariato")
            return dati

        valore = self._predici_data_preparazione_da_crop(crop)
        if valore:
            dati["data_etichetta_preparazione"] = valore
            dati["data_etichetta_preparazione_risolto_da_crop"] = True
            log.info(f"  data_etichetta_preparazione recuperata da crop dinamico: {valore}")
        else:
            log.info("  data_etichetta_preparazione: crop trovato ma nessuna data plausibile, resta invariato")

        return dati

    def _leggi_etichetta_con_profilo(self, crop: Image.Image, regola: str) -> dict:
        """
        Chiama Qwen su un crop dell'etichetta usando il prompt costruito
        con la regola di lettura fornita (di UNA farmacia specifica, o la
        regola generica se la farmacia non è riconosciuta). Restituisce il
        dizionario risultante (solo le chiavi lette con successo), o {}
        se la chiamata fallisce o il JSON non e' valido.
        """
        if not regola:
            return {}

        prompt_finale = PROMPT_ETICHETTA_FARMACIA_TEMPLATE.format(regola_farmacia=regola)

        try:
            buf = io.BytesIO()
            crop.convert("RGB").save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

            response = ollama.chat(
                model=self.qwen_model,
                messages=[{
                    "role": "user",
                    "content": prompt_finale + _suffisso_no_think(self.qwen_model),
                    "images": [img_b64]
                }],
                think=False,
                options={"temperature": 0.0}
            )
            testo = _contenuto_pulito(response)
            # Rimuove eventuali backtick/markdown attorno al JSON
            testo_pulito = re.sub(r'^```(?:json)?\s*|\s*```$', '', testo, flags=re.IGNORECASE).strip()
            try:
                risultato = json.loads(testo_pulito)
            except json.JSONDecodeError:
                # Fallback difensivo: se resta comunque del testo prima/dopo
                # il JSON (es. thinking non rimosso del tutto da un bug di
                # libreria), prova a isolare il blocco {...} più esterno
                # invece di far fallire l'intera lettura dell'etichetta.
                match = re.search(r'\{[\s\S]*\}', testo_pulito)
                if not match:
                    raise
                risultato = json.loads(match.group(0))
            if not isinstance(risultato, dict):
                return {}
            return risultato

        except Exception as e:
            log.warning(f"    Errore lettura etichetta con questa regola: {e}")
            return {}

    def _risultato_plausibile(self, risultato: dict) -> bool:
        """
        Verifica se il risultato di UN profilo è genuino: richiede che la
        MAGGIORANZA dei 10 campi etichetta sia stata popolata (non vuota,
        non "INCERTO") — non basta che UN SOLO campo sembri plausibile.

        Motivo: se l'etichetta non è realmente presente sulla pagina, è
        impossibile che il modello legga correttamente un solo campo
        mentre tutti gli altri restano vuoti — quel singolo campo
        "plausibile" è quasi certamente un'allucinazione anche lui (es.
        il modello scambia il timbro DATA SPEDIZIONE per la data di
        preparazione, quando non trova "Prep." sull'etichetta perché
        l'etichetta non c'è). La maggioranza dei campi vuoti è il segnale
        più affidabile che l'etichetta non c'è — a quel punto va scartata
        l'INTERA risposta, incluso il campo che sembrava a posto.
        """
        campi_totali = [
            "data_etichetta_preparazione", "etichetta_data_scadenza",
            "etichetta_nome_cognome_paziente", "etichetta_nome_cognome_medico",
            "etichetta_prezzo_sost", "etichetta_prezzo_on", "etichetta_prezzo_rec",
            "etichetta_prezzo_iva", "etichetta_prezzo_tot", "etichetta_avvertenze",
        ]
        popolati = sum(
            1 for campo in campi_totali
            if str(risultato.get(campo, "")).strip() and str(risultato.get(campo, "")).strip().upper() != "INCERTO"
        )
        SOGLIA_MINIMA_CAMPI_POPOLATI = 7  # su 10 totali — non basta "metà", serve la stragrande maggioranza
        e_plausibile = popolati >= SOGLIA_MINIMA_CAMPI_POPOLATI
        if not e_plausibile:
            log.info(f"    Solo {popolati}/{len(campi_totali)} campi popolati — sotto la maggioranza, risposta scartata per intero")
        return e_plausibile

    def correggi_etichetta_per_farmacia(self, dati: dict, pdf_path: Path, nome_farmacia_da_regione: str = "") -> dict:
        """
        Rilegge TUTTI i campi dell'etichetta (date, prezzi, nomi,
        avvertenze) con un prompt costruito sulla regola SPECIFICA della
        farmacia già riconosciuta (campo nome_farmacia, già estratto in
        modo affidabile dal gruppo "resto"). Se nome_farmacia non è stato
        letto dall'OCR (vuoto) ma è disponibile un nome recuperato da
        Regione (nome_farmacia_da_regione), si usa quello al suo posto
        per la scelta del profilo. Se la farmacia (in un modo o nell'altro)
        non è comunque tra quelle con un profilo definito, si usa una
        regola GENERICA con un solo tentativo (non si prova più in
        sequenza tutti i profili delle altre farmacie).

        Ogni campo risultante ha PRIORITÀ e sovrascrive quello del gruppo
        principale, solo se il valore letto qui non è vuoto — stesso
        principio già usato per gli altri campi con crop dedicato.
        """
        try:
            img = self._pdf_to_image(pdf_path)
        except Exception as e:
            log.error(f"Errore apertura PDF per correggi_etichetta_per_farmacia {pdf_path}: {e}")
            return dati

        # Stessa fascia inferiore già usata per la ricerca di "Prep." — è
        # abbastanza ampia da contenere l'intera etichetta in tutti gli
        # esempi osservati, evitando di dover calibrare coordinate precise
        # per ciascuna farmacia senza possibilità di testarle dal vivo.
        page_h = img.height
        y_start = int(page_h * 0.45)
        crop_etichetta = img.crop((0, y_start, img.width, page_h))

        nome_farmacia_ocr = str(dati.get("nome_farmacia", "")).strip()
        nome_farmacia = nome_farmacia_ocr

        if not nome_farmacia and nome_farmacia_da_regione:
            nome_farmacia = nome_farmacia_da_regione.strip()
            log.info(f"  nome_farmacia non letto dall'OCR — uso il valore da Regione: '{nome_farmacia}'")

        if nome_farmacia in PROFILI_FARMACIA:
            regola = PROFILI_FARMACIA[nome_farmacia]
            log.info(f"  Farmacia riconosciuta ('{nome_farmacia}') — uso il profilo dedicato")
        else:
            regola = PROFILO_GENERICO
            log.info(
                f"  Farmacia non riconosciuta ('{nome_farmacia}') — UN SOLO tentativo "
                f"con la regola generica (non si prova più in sequenza tutti i profili "
                f"delle altre farmacie: le loro istruzioni troppo specifiche inducevano "
                f"il modello a 'trovare' campi che in realtà non c'erano)"
            )

        CAMPI_ETICHETTA = [
            "data_etichetta_preparazione", "etichetta_data_scadenza",
            "etichetta_nome_cognome_paziente", "etichetta_nome_cognome_medico",
            "etichetta_prezzo_sost", "etichetta_prezzo_on", "etichetta_prezzo_rec",
            "etichetta_prezzo_iva", "etichetta_prezzo_tot", "etichetta_avvertenze",
        ]

        risultato = self._leggi_etichetta_con_profilo(crop_etichetta, regola)
        if not self._risultato_plausibile(risultato):
            # Non ci si ferma a scartare il NUOVO tentativo: la stessa prova
            # raccolta qui (rilettura dedicata, con ancoraggi espliciti,
            # che non trova quasi nulla) è evidenza forte che l'etichetta
            # non c'è affatto — quindi anche i valori del passaggio
            # "critico" originale per questi stessi campi sono sospetti
            # allo stesso modo (probabilmente la stessa allucinazione,
            # es. il timbro DATA SPEDIZIONE scambiato per data etichetta).
            # Prima restavano intatti perché si scartava solo il nuovo
            # tentativo senza mai riconsiderare il vecchio — qui li
            # forziamo vuoti, coerente con "etichetta confermata assente"
            # già usato altrove nel codice.
            campi_da_svuotare = [c for c in CAMPI_ETICHETTA if str(dati.get(c, "")).strip()]
            if campi_da_svuotare:
                log.info(
                    f"  correggi_etichetta_per_farmacia: nessun risultato plausibile — "
                    f"svuoto anche i campi del passaggio critico ({', '.join(campi_da_svuotare)}), "
                    f"probabile stessa allucinazione"
                )
                for campo in campi_da_svuotare:
                    dati[campo] = ""
            else:
                log.info("  correggi_etichetta_per_farmacia: nessun risultato plausibile, nessun altro tentativo")
            return dati
        etichetta_fonte = nome_farmacia if nome_farmacia in PROFILI_FARMACIA else "generico"
        for campo in CAMPI_ETICHETTA:
            valore = str(risultato.get(campo, "")).strip()
            if valore and valore.upper() != "INCERTO":
                dati[campo] = valore
                dati[f"{campo}_risolto_da_profilo_farmacia"] = etichetta_fonte

        return dati


# ─── Test rapido ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Utilizzo: python date_corrector.py ricette/030140923503386.pdf")
        sys.exit(1)

    pdf = Path(sys.argv[1])
    if not pdf.exists():
        print(f"File non trovato: {pdf}")
        sys.exit(1)

    print(f"\nTest DateCorrector su: {pdf.name}\n")
    corrector = DateCorrector()

    dati_test = {"nome_farmacia": "Farmacia Pomi di dr. Collivasone A. & C. Snc"}
    dati_test = corrector.correggi_esenzione(dati_test, pdf)
    dati_test = corrector.correggi_totale(dati_test, pdf)
    dati_test = corrector.correggi_testo_prescrizione(dati_test, pdf)
    dati_test = corrector.correggi_codice_fiscale(dati_test, pdf)
    dati_test = corrector.correggi_nome_assistito(dati_test, pdf)
    dati_test = corrector.correggi_data_preparazione_dinamico(dati_test, pdf)
    dati_test = corrector.correggi_etichetta_per_farmacia(dati_test, pdf)

    print(f"\nRisultato: {json.dumps(dati_test, indent=2, ensure_ascii=False)}")
