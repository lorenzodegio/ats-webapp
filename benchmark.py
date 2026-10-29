# -*- coding: utf-8 -*-
"""
benchmark.py — Confronta combinazioni di num_thread/parallelismo per la
fase di estrazione OCR (quella piu' pesante), misurando tempo totale,
tempo medio per ricetta e picco di RAM del processo. Pensato per girare
sia sul server CPU-only sia sulla macchina GPU, per confrontare i due.

Richiede l'ambiente Python della pipeline (docker/pyproject.toml —
torch/easyocr/ollama/ecc.), non quello della webapp.

Uso:
    python benchmark.py --ricette cartella_pdf_anonimizzati\\
    python benchmark.py --ricette cartella_pdf\\ --thread 20,30,38 --parallelismo 1,2
    python benchmark.py --ricette cartella_pdf\\ --excel excel_regione\\file.xlsx

IMPORTANTE: usa solo PDF anonimizzati/di prova, mai ricette reali con dati
di pazienti — questo script non elabora/scrive nulla di diverso dalla
pipeline normale, ma e' pensato per essere lanciato ripetutamente e i suoi
output (benchmark_output\\, CSV dei risultati) restano sul disco per essere
confrontati a mano.

Scope: misura SOLO la fase di estrazione OCR (pipeline.esegui_estrazione —
il grosso del tempo e del traffico verso Ollama). Il controllo thread della
fase difformita (phase5_difformita.py, OLLAMA_NUM_THREAD_LEGGERO) viene
letto una sola volta all'import di quel modulo: non e' pensato per essere
riconfigurato a ogni iterazione come qui, e non e' il collo di bottiglia
che questo benchmark vuole calibrare.
"""
import argparse
import csv
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import psutil

import ocr_cannabis
import pipeline


def _monitora_picco_ram(processo: psutil.Process, ferma: threading.Event, risultato: dict, intervallo: float = 0.5) -> None:
    """Campiona periodicamente RSS del processo corrente + figli (es. thread
    worker, se mai ne nascessero) finche' 'ferma' non viene impostato — un
    sample puntuale a fine elaborazione sottostimerebbe il vero picco."""
    picco = 0
    while not ferma.is_set():
        try:
            rss = processo.memory_info().rss
            for figlio in processo.children(recursive=True):
                try:
                    rss += figlio.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            picco = max(picco, rss)
        except psutil.NoSuchProcess:
            pass
        ferma.wait(intervallo)
    risultato["picco_bytes"] = picco


def esegui_combinazione(ricette_dir: Path, output_dir: Path, num_thread: int, parallelismo: int,
                         excel_path: Path = None) -> dict:
    """Imposta num_thread sia per le chiamate 'principali' (ocr_cannabis.py,
    tramite gli stessi attributi di modulo gia' usati per NUM_GPU — non
    basta l'env var, sono gia' stati letti all'import) sia per quelle di
    date_corrector.py (lette dall'env ad ogni nuova istanza: serve
    resettare la cache del DateCorrector per farle rileggere il valore
    aggiornato — vedi ocr_cannabis.get_date_corrector())."""
    os.environ["OLLAMA_NUM_THREAD_PESANTE"] = str(num_thread)
    os.environ["OLLAMA_NUM_THREAD_LEGGERO"] = str(num_thread)
    ocr_cannabis.NUM_THREAD_PESANTE = num_thread
    ocr_cannabis.NUM_THREAD_LEGGERO = num_thread
    ocr_cannabis._date_corrector = None  # forza una nuova istanza, con _num_thread aggiornato

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    processo_corrente = psutil.Process()
    ferma = threading.Event()
    risultato_ram = {}
    thread_monitor = threading.Thread(
        target=_monitora_picco_ram, args=(processo_corrente, ferma, risultato_ram), daemon=True,
    )
    thread_monitor.start()

    t0 = time.time()
    try:
        riepilogo = pipeline.esegui_estrazione(
            ricette_dir, output_dir, solo_test=False, gpu=None, model=None,
            excel_path=excel_path, parallelismo=parallelismo,
        )
    finally:
        ferma.set()
        thread_monitor.join(timeout=5)
    durata = time.time() - t0

    return {
        "num_thread": num_thread,
        "parallelismo": parallelismo,
        "durata_totale_sec": round(durata, 1),
        "media_sec_per_ricetta": round(durata / riepilogo["totale"], 2) if riepilogo["totale"] else None,
        "totale_ricette": riepilogo["totale"],
        "elaborati": riepilogo["elaborati"],
        "errori": riepilogo["errori"],
        "picco_ram_mb": round(risultato_ram.get("picco_bytes", 0) / (1024 * 1024), 1),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ricette", type=Path, required=True,
                     help="Cartella di PDF anonimizzati/di prova (NON ricette reali)")
    ap.add_argument("--output", type=Path, default=Path("benchmark_output"),
                     help="Cartella di output temporanea, svuotata ad ogni combinazione")
    ap.add_argument("--excel", type=Path, default=None,
                     help="File Excel Regione opzionale (solo per nome_farmacia via barcode)")
    ap.add_argument("--thread", type=str, default="20,30,38",
                     help="Valori di num_thread da confrontare, separati da virgola (default: 20,30,38)")
    ap.add_argument("--parallelismo", type=str, default="1,2",
                     help="Valori di parallelismo da confrontare, separati da virgola (default: 1,2)")
    ap.add_argument("--risultati", type=Path, default=None,
                     help="File CSV di output (default: benchmark_risultati_<timestamp>.csv)")
    args = ap.parse_args()

    if not args.ricette.is_dir() or not list(args.ricette.glob("*.pdf")):
        raise SystemExit(f"Nessun PDF trovato in {args.ricette}")

    valori_thread = [int(v.strip()) for v in args.thread.split(",") if v.strip()]
    valori_parallelismo = [int(v.strip()) for v in args.parallelismo.split(",") if v.strip()]
    risultati_path = args.risultati or Path(f"benchmark_risultati_{datetime.now():%Y%m%d_%H%M%S}.csv")

    n_pdf = len(list(args.ricette.glob("*.pdf")))
    print(f"Benchmark su {n_pdf} PDF — {len(valori_thread)} valori di thread x {len(valori_parallelismo)} di parallelismo "
          f"= {len(valori_thread) * len(valori_parallelismo)} combinazioni\n")

    righe = []
    for num_thread in valori_thread:
        for parallelismo in valori_parallelismo:
            print(f"=== num_thread={num_thread}  parallelismo={parallelismo} ===")
            riga = esegui_combinazione(args.ricette, args.output, num_thread, parallelismo, args.excel)
            print(f"    {riga['durata_totale_sec']}s totali | {riga['media_sec_per_ricetta']}s/ricetta | "
                  f"{riga['elaborati']}/{riga['totale_ricette']} ok | picco RAM {riga['picco_ram_mb']} MB\n")
            righe.append(riga)

    with open(risultati_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=righe[0].keys())
        writer.writeheader()
        writer.writerows(righe)
    print(f"Risultati salvati in {risultati_path}")

    migliore = min((r for r in righe if r["elaborati"] > 0), key=lambda r: r["durata_totale_sec"], default=None)
    if migliore:
        print(f"Combinazione piu' veloce: num_thread={migliore['num_thread']} parallelismo={migliore['parallelismo']} "
              f"({migliore['durata_totale_sec']}s totali, {migliore['picco_ram_mb']} MB di picco)")
    else:
        print("Nessuna combinazione ha elaborato ricette con successo — controlla i log sopra.")


if __name__ == "__main__":
    main()
