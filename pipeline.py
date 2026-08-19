"""
pipeline.py — Orchestratore completo ATS Insubria
Alessandro Marchinu, Francesco Milani

Esegue in sequenza i tre passaggi della pipeline in un'unica chiamata:
    1. ocr_cannabis.py   -> estrae i dati da tutte le ricette PDF
    2. merge_regione.py  -> arricchisce i JSON con i dati Excel Regione
    3. phase5_difformita.py -> rileva le difformità e le scrive nei JSON

L'ordine è importante e non è invertibile: il passo 2 richiede il barcode
già estratto dal passo 1; il passo 3 richiede i campi COD_FISCALE/
LORDO_PRESC/CONTROLLO_CODICE_PRESCRIZIONE aggiunti dal passo 2 per i
check 14/17/18 (altrimenti quei check segnaleranno difformità per
mancanza di dati, come previsto quando il confronto non è possibile).

Utilizzo:
    python pipeline.py --excel excel_regione\\file_regione_Agosto_2025.xlsx
    python pipeline.py --excel excel_regione\\file_regione_Agosto_2025.xlsx --test
    python pipeline.py --ricette ricette/ --output output/ --excel excel_regione\\file_regione_Agosto_2025.xlsx
    python pipeline.py   # senza --excel: salta il passo 2, i check 14/17/18 segnaleranno difformità
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("Pipeline")

# I tre moduli vanno importati da questa stessa cartella
import ocr_cannabis
import merge_regione
import phase5_difformita
import phase4_excel


def esegui_estrazione(input_dir: Path, output_dir: Path, solo_test: bool,
                       gpu: int, model: str, excel_path: Path = None,
                       on_ricetta_completata=None) -> dict:
    """
    Passo 1 — estrazione OCR. Replica la logica di ocr_cannabis.main(),
    ma come funzione richiamabile (main() usa argparse.parse_args() sui
    veri argomenti da riga di comando, che qui non vogliamo — pipeline.py
    ha i suoi propri argomenti).

    excel_path: percorso del file Regione, usato SOLO per recuperare
    FARMACIA_ID via barcode quando nome_farmacia non viene letto
    dall'OCR (timbro assente/illeggibile) — vedi
    ocr_cannabis.recupera_profilo_farmacia_da_regione().

    on_ricetta_completata: callback OPZIONALE, richiamato dopo OGNI
    ricetta (successo o errore) con (indice, totale) — usato dalla GUI
    per aggiornare la barra di avanzamento e il tempo residuo stimato.
    Nessun effetto sul comportamento esistente se non viene passato.
    """
    ocr_cannabis.NUM_GPU = gpu
    ocr_cannabis.PERCORSO_REGIONE = excel_path
    if model:
        ocr_cannabis.MODELLO_PESANTE = model
        ocr_cannabis.MODELLO_LEGGERO = model
        ocr_cannabis.OLLAMA_MODEL = model
        log.info(f"Modello forzato uniforme su tutti i gruppi: {model}")

    if not input_dir.exists():
        log.error(f"Cartella ricette non trovata: {input_dir}")
        sys.exit(1)

    pdf_files = sorted(input_dir.glob("*.pdf"))
    if not pdf_files:
        log.error(f"Nessun PDF trovato in {input_dir}")
        sys.exit(1)

    if solo_test:
        pdf_files = [pdf_files[0]]
        log.info("Modalità TEST — elaboro solo la prima ricetta")

    log.info(
        f"[1/4] Estrazione OCR — modello: {model or f'{ocr_cannabis.MODELLO_PESANTE} (pesante) / {ocr_cannabis.MODELLO_LEGGERO} (leggero)'} "
        f"| GPU: {gpu if gpu is not None else 'auto (decide Ollama)'} | Ricette: {len(pdf_files)}"
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    risultati, errori = [], []
    inizio = datetime.now()

    for i, pdf in enumerate(pdf_files, 1):
        log.info(f"  [{i}/{len(pdf_files)}] {pdf.name}")
        try:
            dati = ocr_cannabis.estrai_dati(pdf)
            if dati:
                risultati.append(dati)
                out = output_dir / f"{pdf.stem}.json"
                out.write_text(json.dumps(dati, indent=2, ensure_ascii=False), encoding="utf-8")
                log.info(f"    -> {out.name}")
            else:
                errori.append(pdf.name)
                log.warning("    -> Nessun dato estratto")
        except Exception as e:
            errori.append(pdf.name)
            log.error(f"    -> Errore: {e}")

        if on_ricetta_completata is not None:
            try:
                on_ricetta_completata(i, len(pdf_files))
            except Exception as e:
                log.warning(f"  Errore nel callback di avanzamento (ignorato): {e}")

    durata = (datetime.now() - inizio).total_seconds()
    riepilogo = {
        "totale": len(pdf_files),
        "elaborati": len(risultati),
        "errori": len(errori),
        "durata_sec": round(durata, 1),
        "media_sec_per_ricetta": round(durata / len(pdf_files), 1) if pdf_files else 0,
        "file_errore": errori,
        "risultati": risultati,
    }
    (output_dir / "riepilogo.json").write_text(
        json.dumps(riepilogo, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    log.info(f"[1/4] Completato: {len(risultati)}/{len(pdf_files)} ricette | {durata:.0f}s totali")
    if errori:
        log.warning(f"[1/4] Errori su: {errori}")

    return riepilogo


def esegui_merge_regione(excel_path: Path, output_dir: Path) -> bool:
    """Passo 2 — arricchimento con i dati Excel Regione. Restituisce False se saltato."""
    if excel_path is None:
        log.warning(
            "[2/4] Nessun file Excel Regione fornito (--excel) — passo saltato. "
            "I check 14/17/18 in fase 3 segnaleranno difformità per dati mancanti."
        )
        return False

    if not excel_path.exists():
        log.error(f"[2/4] File Excel non trovato: {excel_path} — passo saltato")
        return False

    log.info(f"[2/4] Arricchimento con dati Regione — {excel_path.name}")
    try:
        merge_regione.run(excel_path, output_dir)
    except Exception as e:
        log.error(f"[2/4] Errore durante l'arricchimento Regione: {e}")
        return False

    log.info("[2/4] Completato")
    return True


def esegui_difformita(output_dir: Path) -> None:
    """Passo 3 — rilevamento difformità, scritte direttamente in ciascun JSON."""
    log.info("[3/4] Analisi difformità")
    try:
        phase5_difformita.elabora_cartella(output_dir)
    except Exception as e:
        log.error(f"[3/4] Errore durante l'analisi difformità: {e}")
        return

    log.info("[3/4] Completato")


def esegui_scrittura_excel(excel_path: Path, output_dir: Path, template_path: Path, ricette_dir: Path) -> bool:
    """Passo 4 — scrive tutti i risultati (OCR + Regione + difformità) nel file Excel finale."""
    if excel_path is None:
        log.warning(
            "[4/4] Nessun file Excel Regione fornito (--excel) — passo saltato. "
            "Non c'è nessun Excel su cui scrivere i risultati senza il file di partenza."
        )
        return False

    if template_path is None or not template_path.exists():
        log.warning(
            f"[4/4] Template Excel finale non trovato ({template_path}) — passo saltato. "
            f"Serve --template con il file OUTPUT_FLUSSO_CANNABIS_FINALE.xlsx originale."
        )
        return False

    log.info(f"[4/4] Scrittura risultati nell'Excel finale — {excel_path.name}")
    try:
        phase4_excel.run(excel_path, output_dir, template_path, ricette_dir=ricette_dir)
    except Exception as e:
        log.error(f"[4/4] Errore durante la scrittura Excel: {e}")
        return False

    log.info("[4/4] Completato")
    return True


def main():
    ap = argparse.ArgumentParser(
        description="Pipeline completa ATS Insubria: estrazione OCR + Regione + difformità + Excel finale"
    )
    ap.add_argument("--ricette", type=Path, default=ocr_cannabis.RICETTE_DIR,
                     help="Cartella con i PDF delle ricette (default: ricette/)")
    ap.add_argument("--output", type=Path, default=ocr_cannabis.OUTPUT_DIR,
                     help="Cartella di output per i JSON (default: output/)")
    ap.add_argument("--excel", type=Path, default=None,
                     help="File Excel Regione del mese (es. excel_regione\\file_regione_Agosto_2025.xlsx). "
                          "Se omesso, i passi di arricchimento e scrittura Excel finale vengono saltati.")
    ap.add_argument("--test", action="store_true",
                     help="Elabora solo la prima ricetta trovata (per test rapidi)")
    ap.add_argument("--gpu", type=int, default=ocr_cannabis.NUM_GPU,
                     help="Numero di layer del modello da offloadare su GPU, non un booleano. "
                          "Default: nessun valore forzato, decide Ollama in autonomia in base "
                          "alla VRAM disponibile (consigliato). 0=forza CPU-only (debug).")
    ap.add_argument("--model", type=str, default=None,
                     help=f"Se specificato, forza lo stesso modello su tutti i gruppi "
                          f"di estrazione. Se omesso, usa la strategia mista di default "
                          f"({ocr_cannabis.MODELLO_PESANTE} su identita/etichetta, "
                          f"{ocr_cannabis.MODELLO_LEGGERO} su prescrizione/timbri).")
    ap.add_argument("--salta-difformita", action="store_true",
                     help="Salta l'analisi difformità (passo 3)")
    ap.add_argument("--salta-excel", action="store_true",
                     help="Salta la scrittura del file Excel finale (passo 4)")
    ap.add_argument("--template", type=Path, default=None,
                     help="File template Excel originale per il passo 4 "
                          "(es. OUTPUT_FLUSSO_CANNABIS_FINALE.xlsx) — richiesto se non usi --salta-excel")
    args = ap.parse_args()

    inizio_totale = datetime.now()

    riepilogo_estrazione = esegui_estrazione(
        args.ricette, args.output, args.test, args.gpu, args.model, args.excel
    )

    if riepilogo_estrazione["elaborati"] == 0:
        log.error("Nessuna ricetta elaborata con successo — interrompo la pipeline")
        sys.exit(1)

    regione_eseguito = esegui_merge_regione(args.excel, args.output)

    if not args.salta_difformita:
        esegui_difformita(args.output)
    else:
        log.info("[3/4] Saltato su richiesta (--salta-difformita)")

    excel_scritto = False
    if not args.salta_excel:
        excel_scritto = esegui_scrittura_excel(args.excel, args.output, args.template, args.ricette)
    else:
        log.info("[4/4] Saltato su richiesta (--salta-excel)")

    durata_totale = (datetime.now() - inizio_totale).total_seconds()
    log.info(f"\n{'=' * 60}")
    log.info("PIPELINE COMPLETATA")
    log.info(f"{'=' * 60}")
    log.info(f"Ricette elaborate: {riepilogo_estrazione['elaborati']}/{riepilogo_estrazione['totale']}")
    log.info(f"Arricchimento Regione: {'eseguito' if regione_eseguito else 'saltato'}")
    log.info(f"Analisi difformità: {'saltata' if args.salta_difformita else 'eseguita'}")
    log.info(f"Scrittura Excel finale: {'eseguita' if excel_scritto else 'saltata'}")
    log.info(f"Durata totale: {durata_totale:.0f}s")
    log.info(f"Output: {args.output}")


if __name__ == "__main__":
    main()
