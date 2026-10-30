"""
run_fase.py
------------
Script dispatcher eseguito dentro il container Docker — riceve il
nome della fase come primo argomento, richiama la funzione
corrispondente della pipeline già pronta e testata (pipeline.py,
phase1_preprocess.py), senza modificarne la logica.

Percorsi (cartelle condivise via volume Docker, vedi docker-compose.yml):
    /dati/ricette_raw     — PDF grezzi multi-pagina (input preprocessing)
    /dati/ricette_staging — output intermedio del preprocessing
    /dati/ricette         — PDF singoli (output preprocessing, input OCR)
    /dati/excel_regione   — file Excel Regione (uno solo atteso)
    /dati/output          — JSON + Excel finale
"""

import logging
import os
import sys
import shutil
from pathlib import Path

# Eseguito sia dentro un container Docker (dove questo file vive appiattito
# in /app insieme a tutti gli altri script della pipeline, vedi docker/
# Dockerfile) sia come processo nativo lanciato da app/real_pipeline.py
# (PIPELINE_RUNNER=local, vedi _esegui_processo_locale) — in entrambi i
# casi e' lo stesso file, dentro la repo ats-webapp (docker/run_fase.py),
# mentre gli altri moduli della pipeline (phase1_preprocess.py, pipeline.py,
# ecc.) in produzione vivono nella repo SEPARATA ocr-cannabis-ats (vedi
# CLAUDE.md), clonata altrove sul server — PIPELINE_DIR (env, passata dal
# processo genitore) punta a quella cartella. Python aggiunge solo la
# cartella DI QUESTO file a sys.path, quindi senza questa riga "import
# pipeline" fallirebbe sempre in modalita' nativa. Se PIPELINE_DIR non e'
# impostata (container Docker, o su questa macchina di sviluppo dove i due
# repo sono vendorizzati insieme per comodita') si ricade sul vecchio
# comportamento — genitore di questo file, cioe' la radice della repo
# dove run_fase.py vive davvero: "/" dentro un container (ininfluente,
# sys.path[0]="/app" risolve comunque tutto), la radice della repo stessa
# qui in sviluppo.
_pipeline_dir = os.environ.get("PIPELINE_DIR", "").strip()
sys.path.insert(0, _pipeline_dir if _pipeline_dir else str(Path(__file__).resolve().parent.parent))

# Configurato QUI, centralmente, prima di eseguire qualunque fase: la fase
# "preprocessing" importa solo phase1_preprocess.py, che si limita a
# logging.getLogger("Phase1") senza mai chiamare basicConfig. Le altre 3
# fasi funzionano solo perche' importano pipeline.py, che lo fa come
# effetto collaterale a livello di modulo — per preprocessing quell'effetto
# collaterale non scatta mai, quindi i suoi logger.info() restavano scartati
# in silenzio (livello di default WARNING, nessun handler) e non arrivavano
# nemmeno sullo stdout grezzo del container. logging.basicConfig() e' un
# no-op se il root logger e' gia' configurato, quindi il basicConfig dentro
# pipeline.py per le altre fasi resta innocuo.
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Default invariati (modalita' Docker, dove /dati e /app sono il filesystem
# del container — vedi docker-compose.yml). ATS_DATI_DIR/ATS_TEMPLATE_EXCEL
# li sovrascrivono in modalita' nativa (PIPELINE_RUNNER=local), dove non
# esiste nessun container e questi path devono essere cartelle Windows vere
# — vedi _esegui_processo_locale in app/real_pipeline.py.
DATI = Path(os.environ.get("ATS_DATI_DIR", "/dati"))


def fase_preprocessing():
    import phase1_preprocess

    input_dir = DATI / "ricette_raw"
    images_dir = DATI / "ricette_staging" / "images"
    pdfs_dir = images_dir.parent / "pdfs"
    ricette_dir = DATI / "ricette"
    ricette_dir.mkdir(parents=True, exist_ok=True)

    risultati = phase1_preprocess.run(input_dir, images_dir)

    # Sposta nella cartella finale solo i fronti con barcode letto —
    # i file "undefined" restano in staging per la revisione manuale,
    # gestita a livello di applicazione web, non qui nel container
    for r in risultati:
        if r["barcode"] != "undefined":
            origine = pdfs_dir / f"{r['barcode']}.pdf"
            if origine.exists():
                shutil.move(str(origine), str(ricette_dir / origine.name))

    print(f"Preprocessing completato: {len(risultati)} fronti totali.")


def fase_ocr():
    import pipeline

    ricette_dir = DATI / "ricette"
    output_dir = DATI / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    excel_regione = next((DATI / "excel_regione").glob("*.xlsx"), None)

    riepilogo = pipeline.esegui_estrazione(
        ricette_dir, output_dir, solo_test=False,
        # gpu=None: NON forzare un valore di num_gpu, lascia decidere a
        # Ollama in base alla VRAM realmente disponibile. gpu=1 (valore
        # precedente qui) riproponeva esattamente il bug risolto nella
        # pipeline CLI: num_gpu=1 in Ollama significa "un solo layer su
        # GPU", non "usa la GPU" — forzava quasi tutto il modello su CPU.
        gpu=None, model=None, excel_path=excel_regione,
    )
    print(f"OCR completato: {riepilogo}")
    if riepilogo.get("elaborati", 0) == 0:
        sys.exit(1)

    # Passo 2 della pipeline CLI originale (pipeline.py), mancava del
    # tutto qui — la fase "difformita" richiede i campi COD_FISCALE/
    # LORDO_PRESC/CONTROLLO_CODICE_PRESCRIZIONE aggiunti da questo passo
    # per i controlli 14/17/18. Senza, quei controlli segnalano sempre
    # difformità per "dati mancanti" anche quando la ricetta è corretta
    # — verificato: è esattamente quello che succedeva nei test (codici
    # 14 e 18 comparivano sempre tra le difformità). Non blocca la
    # pipeline se manca/fallisce (stesso comportamento non fatale della
    # CLI originale) — solo logga, i controlli 14/17/18 restano
    # correttamente "dati mancanti" in quel caso, come previsto.
    pipeline.esegui_merge_regione(excel_regione, output_dir)


def fase_difformita():
    import pipeline

    output_dir = DATI / "output"
    pipeline.esegui_difformita(output_dir)
    print("Analisi difformità completata.")


def fase_excel():
    import pipeline

    output_dir = DATI / "output"
    ricette_dir = DATI / "ricette"
    template_path = Path(os.environ.get("ATS_TEMPLATE_EXCEL", "/app/OUTPUT_FLUSSO_CANNABIS_FINALE.xlsx"))
    excel_regione = next((DATI / "excel_regione").glob("*.xlsx"), None)

    if excel_regione is None:
        print("ERRORE: nessun file Regione trovato in /dati/excel_regione")
        sys.exit(1)

    ok = pipeline.esegui_scrittura_excel(excel_regione, output_dir, template_path, ricette_dir)
    if not ok:
        sys.exit(1)
    print("Scrittura Excel finale completata.")


FASI = {
    "preprocessing": fase_preprocessing,
    "ocr": fase_ocr,
    "difformita": fase_difformita,
    "excel": fase_excel,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in FASI:
        print(f"Uso: python run_fase.py <{'|'.join(FASI.keys())}>")
        sys.exit(2)

    FASI[sys.argv[1]]()
