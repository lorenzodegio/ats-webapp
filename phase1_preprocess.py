"""
phase1_preprocess.py
--------------------
Fase 1: PDF combined → PDF/PNG singoli per prescrizione

Logica fronte/retro:
- Barcode letto → FRONTE (certezza assoluta)
- Nessun barcode → fallback front_detector
- Pagine sospette (classificate retro ma con barcode, o fronte senza barcode) → loggate
"""

import logging
import cv2
import numpy as np
import fitz
from pathlib import Path
from PIL import Image
from deskew import determine_skew
from pyzbar.pyzbar import decode as decode_barcode

from front_detector import e_un_fronte

logger = logging.getLogger("Phase1")


def _page_to_gray(page) -> np.ndarray:
    pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    if pix.n >= 3:
        return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    return img


def deskew_image(gray: np.ndarray) -> np.ndarray:
    angle = determine_skew(gray)
    if angle is None or abs(angle) < 0.1 or abs(angle) > 15:
        return gray
    (h, w) = gray.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    cos, sin = abs(M[0, 0]), abs(M[0, 1])
    nW = int((h * sin) + (w * cos))
    nH = int((h * cos) + (w * sin))
    M[0, 2] += (nW / 2) - center[0]
    M[1, 2] += (nH / 2) - center[1]
    return cv2.warpAffine(gray, M, (nW, nH),
                          flags=cv2.INTER_CUBIC, borderValue=255)


def _scan_barcode(gray: np.ndarray) -> str | None:
    def _try(img):
        decoded = decode_barcode(img)
        if not decoded:
            return None
        vals = [b.data.decode("utf-8") for b in decoded
                if b.data.decode("utf-8").isdigit()]
        part_15 = [v for v in vals if len(v) == 15]
        part_5  = [v for v in vals if len(v) == 5]
        part_10 = [v for v in vals if len(v) == 10]
        if part_15: return part_15[0]
        if part_5 and part_10: return part_5[0] + part_10[0]
        return None

    result = _try(gray)
    if result: return result
    h = gray.shape[0]
    result = _try(gray[:int(h * 0.25), :])
    if result: return result
    _, binary = cv2.threshold(gray, 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return _try(binary)


def _gray_to_pil(gray: np.ndarray) -> Image.Image:
    return Image.fromarray(gray).convert("RGB")


def _save_pdf(gray: np.ndarray, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    _gray_to_pil(gray).save(str(path), "PDF", resolution=300.0)


def _save_png(gray: np.ndarray, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(gray).save(str(path))


def run(input_dir: Path, images_dir: Path) -> list[dict]:
    pdfs_dir = images_dir.parent / "pdfs"
    images_dir.mkdir(parents=True, exist_ok=True)
    pdfs_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(input_dir.glob("*.pdf"))
    if not pdf_files:
        logger.warning(f"Nessun PDF in {input_dir}")
        return []

    logger.info(f"[Fase 1] {len(pdf_files)} PDF da elaborare")

    risultati  = []
    sospetti   = []  # pagine anomale da segnalare

    for pdf_path in pdf_files:
        logger.info(f"  PDF: {pdf_path.name}")
        try:
            doc = fitz.open(str(pdf_path))
        except Exception as e:
            logger.error(f"  Errore apertura: {e}")
            continue

        n_pag = len(doc)

        for i in range(n_pag):
            pagina_num = i + 1
            gray       = _page_to_gray(doc[i])
            barcode    = _scan_barcode(gray)
            is_fronte  = e_un_fronte(gray, debug=False)

            # --- Logica decisionale ---
            if barcode:
                # Barcode letto → certamente fronte
                if not is_fronte:
                    msg = (f"  ⚠ SOSPETTO: pag {pagina_num} ha barcode={barcode} "
                           f"ma front_detector dice RETRO — trattata come FRONTE")
                    logger.warning(msg)
                    sospetti.append({
                        "pdf": pdf_path.name,
                        "pagina": pagina_num,
                        "barcode": barcode,
                        "tipo": "barcode_su_retro_classificato",
                    })

                gray = deskew_image(gray)
                nome_out = barcode
                png_path = images_dir / f"{nome_out}.png"
                pdf_out  = pdfs_dir   / f"{nome_out}.pdf"
                _save_png(gray, png_path)
                _save_pdf(gray, pdf_out)

                logger.info(f"    Pag {pagina_num}: FRONTE ✓ barcode={barcode}")
                risultati.append({
                    "barcode":          barcode,
                    "image_path":       png_path,
                    "pdf_path":         pdf_out,
                    "pagina_originale": pagina_num,
                    "file_originale":   pdf_path.name,
                    "metodo":           "barcode",
                })

            elif is_fronte:
                # Nessun barcode ma front_detector dice fronte → undefined
                gray     = deskew_image(gray)
                nome_out = f"undefined_{pdf_path.stem}_p{pagina_num}"
                png_path = images_dir / f"{nome_out}.png"
                pdf_out  = pdfs_dir   / f"{nome_out}.pdf"
                _save_png(gray, png_path)
                _save_pdf(gray, pdf_out)

                msg = (f"    Pag {pagina_num}: FRONTE ⚠ barcode non letto "
                       f"→ salvato come {nome_out}")
                logger.warning(msg)
                sospetti.append({
                    "pdf":     pdf_path.name,
                    "pagina":  pagina_num,
                    "barcode": "undefined",
                    "tipo":    "fronte_senza_barcode",
                })
                risultati.append({
                    "barcode":          "undefined",
                    "image_path":       png_path,
                    "pdf_path":         pdf_out,
                    "pagina_originale": pagina_num,
                    "file_originale":   pdf_path.name,
                    "metodo":           "front_detector_no_barcode",
                })

            else:
                # Nessun barcode + front_detector dice retro → scarta
                logger.info(f"    Pag {pagina_num}: retro → scartata")

        doc.close()

    # Riepilogo sospetti
    bc_ok      = sum(1 for r in risultati if r["barcode"] != "undefined")
    bc_undef   = sum(1 for r in risultati if r["barcode"] == "undefined")
    sosp_bc_r  = sum(1 for s in sospetti if s["tipo"] == "barcode_su_retro_classificato")
    sosp_f_nbc = sum(1 for s in sospetti if s["tipo"] == "fronte_senza_barcode")

    logger.info(f"[Fase 1] Completato: {len(risultati)} fronti totali")
    logger.info(f"  ✓ Con barcode:              {bc_ok}")
    logger.info(f"  ⚠ Senza barcode (undefined): {bc_undef}")
    logger.info(f"  ⚠ Barcode su retro classif.: {sosp_bc_r}")
    logger.info(f"  ⚠ Fronte senza barcode:      {sosp_f_nbc}")

    if sospetti:
        logger.warning(f"[Fase 1] {len(sospetti)} pagine sospette:")
        for s in sospetti:
            logger.warning(f"    [{s['tipo']}] {s['pdf']} pag {s['pagina']} bc={s['barcode']}")

    return risultati
