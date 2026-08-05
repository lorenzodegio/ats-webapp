import numpy as np
import pytesseract
from PIL import Image
import re
import cv2
import platform
import shutil

# Percorso esplicito dell'eseguibile Tesseract — SOLO su Windows, e
# SOLO se pytesseract non lo trova già da solo. Necessario perché il
# PATH di Windows spesso non include la cartella di installazione di
# Tesseract nemmeno dopo averlo installato. Su Linux (es. dentro il
# container Docker, dove tesseract-ocr è installato con apt-get in un
# percorso già nel PATH), questo blocco non fa nulla — altrimenti il
# percorso Windows qui sotto romperebbe l'esecuzione nel container.
if platform.system() == "Windows" and shutil.which("tesseract") is None:
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Pattern tipici del FRONTE di una prescrizione cannabis ATS
PATTERN_FRONTE = [
    r'codice\s+fiscale',
    r'[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]',
    r'cannabis\s+terapeutica',
    r'N02BG10',
    r'farmacia',
    r'preparazione\s+galenica',
    r'ATS',
    r'THC',
    r'CBD',
    r'mg\s*/\s*die',
    r'onorario',
    r'\d{2}/\d{2}/\d{4}',
]
PATTERN_RETRO = [
    r'vietato\s+cedere',
    r'stupefacente',
    r'tabella\s+II',
    r'decreto\s+ministeriale',
    r'conservare\s+in\s+luogo',
]
def _to_grayscale_array(img) -> np.ndarray:
    if isinstance(img, np.ndarray):
        if len(img.shape) == 3:
            from PIL import Image as PILImage
            pil = PILImage.fromarray(img)
            return np.array(pil.convert('L'))
        return img
    elif isinstance(img, Image.Image):
        return np.array(img.convert('L'))
    elif isinstance(img, str):
        return np.array(Image.open(img).convert('L'))
    else:
        raise TypeError(f"Tipo input non supportato: {type(img)}")
def _densita_testo_pixel(gray: np.ndarray) -> float:
    _, binarized = cv2.threshold(
        gray, 0, 255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    pixel_scuri = np.count_nonzero(binarized)
    totale = gray.size
    return pixel_scuri / totale
def _ocr_leggero(gray: np.ndarray) -> str:
    pil_img = Image.fromarray(gray)
    testo = pytesseract.image_to_string(
        pil_img,
        lang='ita',
        config='--oem 1 --psm 6'
    )
    return testo.lower()
def _conta_pattern(testo: str, patterns: list) -> int:
    return sum(
        1 for p in patterns
        if re.search(p, testo, re.IGNORECASE)
    )
def e_un_fronte(img,
                soglia_densita: float = 0.03,
                soglia_pattern_fronte: int = 2,
                debug: bool = False) -> bool:
    gray = _to_grayscale_array(img)
    densita = _densita_testo_pixel(gray)
    if debug:
        print(f"[e_un_fronte] Densità pixel: {densita:.4f}")
    if densita < soglia_densita:
        if debug:
            print(f"[e_un_fronte] → RETRO (pagina quasi vuota, densità {densita:.4f} < {soglia_densita})")
        return False
    testo = _ocr_leggero(gray)
    n_fronte = _conta_pattern(testo, PATTERN_FRONTE)
    n_retro  = _conta_pattern(testo, PATTERN_RETRO)
    if debug:
        print(f"[e_un_fronte] Pattern fronte: {n_fronte}, Pattern retro: {n_retro}")
        print(f"[e_un_fronte] Testo OCR (primi 200 car): {testo[:200]!r}")
    if n_fronte >= soglia_pattern_fronte and n_fronte > n_retro:
        if debug:
            print("[e_un_fronte] → FRONTE ✓")
        return True
    if n_fronte >= 1 and densita > 0.06 and n_retro == 0:
        if debug:
            print("[e_un_fronte] → FRONTE (caso ambiguo, densità alta + 1 pattern) ✓")
        return True
    if debug:
        print("[e_un_fronte] → RETRO ✗")
    return False
