"""
phase4_excel.py
---------------
Fase 4 (finale): produce il file Excel finale con tre blocchi di colonne
per ciascuna ricetta (una riga per ricetta), secondo il layout del
template ATS Insubria (OUTPUT_FLUSSO_CANNABIS_FINALE.xlsx):

  1. TABELLA REGIONE  — tutte le colonne del file Excel Regione mensile,
     copiate dalla riga che combacia per BARCODE (stesso barcode già
     estratto in modo affidabile dal nome del file PDF)
  2. TABELLA OCR       — tutti i campi estratti/verificati dalla pipeline
  3. TABELLA DIFFORMITA' — una colonna per ciascun codice (R01, R02, ...)
     più ETICHETTA MANCANTE

Lo script NON apre il file template — ricostruisce l'intestazione da
zero con gli stessi nomi/ordine di colonna, così non serve portarsi
dietro il template ogni volta; basta che i nomi restino questi.

=====================================================================
NOTE CONFERMATE:
- Colonna LINK: lasciata VUOTA per ora (da riempire in un secondo momento,
  contenuto ancora da decidere).
- Marcatore nelle colonne difformità (R01, R02, ...): la DESCRIZIONE
  specifica della difformità (es. "Data preparazione fuori range"), presa
  da difformita_descrizioni nel JSON — non un semplice "X".
- Codici "17" e "19M" prodotti da phase5_difformita.py NON hanno una
  colonna nel template attuale (mancano R17 e R19M) — per istruzione
  esplicita restano semplicemente non scritti da nessuna parte; vengono
  comunque raccolti e segnalati in un riepilogo a fine esecuzione, per
  non perderne traccia silenziosamente.
- La colonna template "R10A" non ha mai un corrispondente prodotto dal
  modulo difformità attuale — resta sempre vuota, per istruzione esplicita.
- Il matching barcode gestisce già correttamente il trattino basso
  iniziale usato nel file Regione (es. "_030160438060982") — la
  normalizzazione toglie qualunque carattere non alfanumerico da
  entrambi i lati del confronto.
=====================================================================

Utilizzo:
    python phase4_excel.py --excel excel_regione\\file_regione_Agosto_2025.xlsx --output output
"""

import argparse
import json
import logging
import re
from pathlib import Path

import pandas as pd
import openpyxl
from openpyxl.utils import range_boundaries, get_column_letter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("Phase4")


# ─── Blocco 1: colonne della TABELLA REGIONE (nomi esatti dal template) ───────
REGIONE_COLONNE = [
    "Data_Elaborazione", "DATA_CONTABILE", "FARMACIA_ID", "FARMACIA_KEY",
    "DATA_SPEDIZIONE", "NUM_RICETTA", "BARCODE", "MEDICO_ID", "ASSISTITO_ID",
    "COD_FISCALE", "FARMACO_ID", "QTA", "PREZZO", "RIC_SISS_ID", "FLAG_SISS",
    "LORDO_PRESC", "NUM_DDD", "LORDO_TOTALE", "TICKET_REGIST",
    "IMPORTO_RIC_EXTRASCONTO", "TRATT_SCONTO", "FLAG_EXTRASCONTO",
    "FLAG_PRESC_SUGG", "TIPO_RICETTA", "CANALE_ID", "FLAG_TESTATA",
    "ASL_ID_ASSTO", "FULL_DISTRETTO_ID_ASSTO", "ASL_ID", "FULL_DISTRETTO_ID",
    "MACRO_ESENZIONE_ID", "CODICE_ESENZIONE_ID", "CODICE_ESENZIONE_KEY",
    "SESSO", "ETA_ANNI", "FULL_DISTRETTO_ID_FARMA", "ASL_ID_FARMA",
    "FLAG_PROTESICO", "ATC_ID", "ENTE_APP_AMM_ID", "SPECIALISTA_ID",
    "STRUTTURA_ID",
]

# ─── Blocco 2: colonne della TABELLA OCR — nome colonna Excel -> chiave JSON ──
# Tre nomi sono disallineati rispetto allo schema JSON reale del progetto,
# stessa correzione già fatta la volta scorsa (vedi cronologia):
#   cognome_nome_assistito -> nome_cognome_assistito
#   data_invio             -> data_emissione
#   etichetta_THC          -> THC
OCR_FIELD_MAP = {
    "cognome_nome_assistito":          "nome_cognome_assistito",
    "barcode":                         "barcode",
    "codice_fiscale":                  "codice_fiscale",
    "codice_esenzione":                "codice_esenzione",
    "codice_atc":                       "codice_atc",
    "testo_prescrizione":              "testo_prescrizione",
    "metodo_estrattivo_olio":          "metodo_estrattivo_olio",
    "forma_farmaceutica":              "forma_farmaceutica",
    "data_etichetta_preparazione":     "data_etichetta_preparazione",
    "data_prescrizione":               "data_prescrizione",
    "data_invio":                      "data_emissione",
    "timbro_medico":                   "timbro_medico",
    "firma_medico":                    "firma_medico",
    "etichetta_avvertenze":            "etichetta_avvertenze",
    "etichetta_data_scadenza":         "etichetta_data_scadenza",
    "etichetta_nome_cognome_medico":   "etichetta_nome_cognome_medico",
    "etichetta_nome_cognome_paziente": "etichetta_nome_cognome_paziente",
    "etichetta_prezzo_sost":           "etichetta_prezzo_sost",
    "etichetta_prezzo_on":             "etichetta_prezzo_on",
    "etichetta_prezzo_rec":            "etichetta_prezzo_rec",
    "etichetta_prezzo_iva":            "etichetta_prezzo_iva",
    "etichetta_prezzo_tot":            "etichetta_prezzo_tot",
    "totale_prescrizione":             "totale_prescrizione",
    "etichetta_THC":                   "THC",
    "nome_farmacia":                   "nome_farmacia",
}

# Colonna aggiuntiva tra OCR e difformità — lasciata vuota per ora, da
# riempire in un secondo momento (contenuto ancora da decidere)
COLONNA_LINK = "LINK"

# ─── Blocco 3: colonne della TABELLA DIFFORMITA' (ordine esatto template) ─────
DIFFORMITA_COLONNE = [
    "R02", "R03", "R04", "R05", "R07", "R05A", "R06", "R08", "R09", "R10",
    "R10A", "R01", "R11", "R12", "R13", "R16", "R19", "R14", "R18",
]
COLONNA_ETICHETTA_MANCANTE = "ETICHETTA MANCANTE"

# Codici prodotti dal modulo difformità ma da escludere COMPLETAMENTE — non
# hanno una colonna nel template e non vanno nemmeno segnalati nel riepilogo
# (per istruzione esplicita, a differenza degli altri codici non mappati
# che restano comunque tracciati per non perderli silenziosamente)
CODICI_ESCLUSI = {"19M"}

INTESTAZIONE_COMPLETA = (
    REGIONE_COLONNE + list(OCR_FIELD_MAP.keys()) + [COLONNA_LINK]
    + DIFFORMITA_COLONNE + [COLONNA_ETICHETTA_MANCANTE]
)


def _normalizza_barcode(value) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"[^0-9A-Za-z]", "", str(value)).strip()


def _serializza(value):
    """Converte liste e booleani in una forma leggibile per una cella Excel."""
    if isinstance(value, list):
        return "; ".join(str(v) for v in value) if value else ""
    if isinstance(value, bool):
        return "Sì" if value else "No"
    return "" if value is None else str(value)


def _carica_excel_regione(excel_path: Path) -> pd.DataFrame:
    df = pd.read_excel(excel_path, dtype=str)
    if "BARCODE" not in df.columns:
        raise ValueError(f"Colonna BARCODE non trovata in {excel_path}")
    df["_barcode_norm"] = df["BARCODE"].apply(_normalizza_barcode)
    return df


def _riga_da_regione(df_regione: pd.DataFrame, barcode: str) -> dict:
    """Trova la riga Regione che combacia per barcode e restituisce SOLO le
    colonne previste da REGIONE_COLONNE (quelle assenti nel file sorgente
    restano vuote, senza bloccare nulla)."""
    norm_target = _normalizza_barcode(barcode)
    match = df_regione[df_regione["_barcode_norm"] == norm_target]

    riga_vuota = {col: "" for col in REGIONE_COLONNE}
    if match.empty:
        return riga_vuota

    riga_sorgente = match.iloc[0]
    for col in REGIONE_COLONNE:
        if col in df_regione.columns:
            valore = riga_sorgente[col]
            riga_vuota[col] = "" if pd.isna(valore) else str(valore)
    return riga_vuota


def _allarga_tabelle_native(ws, ultima_riga_dati: int):
    """
    Il template ha 4 TABELLE EXCEL NATIVE (Tabella_REGIONE, Tabella_OCR,
    Tabella_LINK, Tabella_LLM/difformità), create con un intervallo che
    copre solo la prima riga di dati (es. "A1:AP2"). Scrivere valori nelle
    celle sottostanti NON allarga automaticamente l'intervallo della
    tabella — Excel continuerebbe a riconoscere come "dentro la tabella"
    solo quella prima riga, anche se le celle delle righe successive
    contengono dati veri. Qui si aggiorna il ref di ciascuna tabella fino
    all'ultima riga effettivamente scritta.
    """
    for nome_tabella in list(ws.tables.keys()):
        tabella = ws.tables[nome_tabella]
        min_col, min_row, max_col, max_row = range_boundaries(tabella.ref)
        # Una tabella Excel deve avere almeno una riga dati sotto l'intestazione
        nuovo_max_row = max(ultima_riga_dati, min_row + 1)
        nuovo_ref = f"{get_column_letter(min_col)}{min_row}:{get_column_letter(max_col)}{nuovo_max_row}"
        if nuovo_ref != tabella.ref:
            log.info(f"  Tabella '{nome_tabella}' estesa da {tabella.ref} a {nuovo_ref}")
            tabella.ref = nuovo_ref


def _riga_da_ocr(dati: dict) -> dict:
    """Estrae dal JSON i valori per le colonne della tabella OCR."""
    riga = {}
    for col_excel, chiave_json in OCR_FIELD_MAP.items():
        valore = dati.get(chiave_json, "")
        riga[col_excel] = _serializza(valore) if valore not in (None, "", False, []) else ""
    return riga


def _riga_da_difformita(dati: dict, codici_non_mappati: dict) -> dict:
    """
    Scrive in ciascuna colonna difformità (R01, R02, ...) la DESCRIZIONE
    specifica di quella difformità (es. "Data preparazione fuori range")
    quando presente, cella vuota quando assente — presa dalle liste
    parallele difformita_codici/difformita_descrizioni già nel JSON.

    SOLO la colonna ETICHETTA MANCANTE è booleana esplicita ("Vero"/"Falso"),
    basata sulla presenza CONGIUNTA delle difformità R11+R12+R13+R16 in
    questa ricetta — non più sul rilevamento SSIM automatico
    (_etichetta_rilevata_presente), risultato non abbastanza affidabile
    da solo su farmacie/layout diversi da quello di calibrazione.

    Registra in codici_non_mappati eventuali codici prodotti dal modulo
    difformità che non hanno una colonna corrispondente nel template
    (es. "17") — restano non scritti da nessuna parte, solo segnalati;
    i codici in CODICI_ESCLUSI (es. "19M") sono ignorati anche dalla
    segnalazione.
    """
    codici_lista = dati.get("difformita_codici", []) or []
    descrizioni_lista = dati.get("difformita_descrizioni", []) or []
    codice_to_descrizione = dict(zip(codici_lista, descrizioni_lista))

    riga = {col: "" for col in DIFFORMITA_COLONNE}
    for col in DIFFORMITA_COLONNE:
        codice = col[1:] if col.startswith("R") else col  # "R05A" -> "05A"
        if codice in codice_to_descrizione:
            riga[col] = codice_to_descrizione[codice]

    codici_con_colonna = {col[1:] if col.startswith("R") else col for col in DIFFORMITA_COLONNE}
    for codice in set(codici_lista) - codici_con_colonna - CODICI_ESCLUSI:
        codici_non_mappati.setdefault(codice, 0)
        codici_non_mappati[codice] += 1

    # ETICHETTA MANCANTE = "Vero" solo se TUTTE E QUATTRO le difformità
    # R11+R12+R13+R16 sono presenti insieme in questa ricetta — non più
    # basato sul rilevamento SSIM (_etichetta_rilevata_presente), che si è
    # dimostrato non abbastanza affidabile da solo. Il ragionamento: se
    # l'etichetta manca davvero, tutti questi controlli (che dipendono da
    # dati presenti solo sull'etichetta) dovrebbero scattare insieme come
    # un unico blocco coerente.
    codici_presenti = set(codici_lista)
    codici_richiesti_etichetta_mancante = {"11", "12", "13", "16"}
    riga[COLONNA_ETICHETTA_MANCANTE] = (
        "Vero" if codici_richiesti_etichetta_mancante.issubset(codici_presenti) else "Falso"
    )
    return riga


def run(excel_path: Path, output_dir: Path, template_path: Path, ricette_dir: Path = None):
    """
    Apre il TEMPLATE reale (con la sua formattazione originale — colori,
    raggruppamento visivo delle tabelle) e scrive i valori direttamente
    nelle celle esistenti, riga per ricetta. Non ricrea il file da zero:
    quello perderebbe tutta la formattazione (motivo per cui le 4 tabelle
    non si distinguevano più visivamente nella versione precedente).

    ricette_dir: cartella con i PDF originali delle ricette (default:
    "ricette", stessa convenzione di ocr_cannabis.py). Usata per
    costruire il collegamento ipertestuale nella colonna LINK, che apre
    il PDF della ricetta corrispondente a quella riga.
    """
    if ricette_dir is None:
        ricette_dir = Path("ricette")

    if not template_path.exists():
        log.error(f"Template non trovato: {template_path}")
        return

    try:
        df_regione = _carica_excel_regione(excel_path)
    except Exception as e:
        log.error(f"Errore lettura Excel Regione: {e}")
        return
    log.info(f"Excel Regione caricato: {len(df_regione)} righe")

    json_files = sorted(output_dir.glob("*.json"))
    json_files = [f for f in json_files if f.name not in ("riepilogo.json", "riepilogo_difformita.json")]
    if not json_files:
        log.error(f"Nessun JSON trovato in {output_dir}")
        return
    log.info(f"{len(json_files)} ricette da scrivere")

    wb = openpyxl.load_workbook(template_path)
    ws = wb[wb.sheetnames[0]]

    # Mappa nome_colonna -> indice colonna, letta DAL TEMPLATE stesso (non
    # da una lista fissa nel codice) — così anche se l'ordine delle colonne
    # nel template reale differisse leggermente, scriviamo comunque nel
    # posto giusto per nome.
    intestazione = {}
    for col_idx in range(1, ws.max_column + 1):
        nome = ws.cell(row=1, column=col_idx).value
        if nome:
            intestazione[nome] = col_idx

    righe_template_disponibili = ws.max_row  # righe già preformattate sotto l'intestazione

    non_trovate_in_regione = []
    codici_non_mappati = {}
    riga_excel = 2  # prima riga dati, sotto l'intestazione

    for json_path in json_files:
        try:
            dati = json.loads(json_path.read_text(encoding="utf-8-sig"))
        except Exception as e:
            log.error(f"  Errore lettura JSON {json_path.name}: {e}")
            continue

        barcode = dati.get("barcode", json_path.stem)

        riga_regione = _riga_da_regione(df_regione, barcode)
        if not any(riga_regione.values()):
            non_trovate_in_regione.append(barcode)

        riga_ocr = _riga_da_ocr(dati)
        riga_difformita = _riga_da_difformita(dati, codici_non_mappati)
        riga_completa = {**riga_regione, **riga_ocr, **riga_difformita}

        if riga_excel > righe_template_disponibili:
            log.warning(
                f"  Riga {riga_excel} oltre le {righe_template_disponibili} righe "
                f"preformattate del template — formattazione non garantita per questa riga"
            )

        for nome_colonna, valore in riga_completa.items():
            col_idx = intestazione.get(nome_colonna)
            if col_idx:
                ws.cell(row=riga_excel, column=col_idx, value=valore)

        # Colonna LINK: collegamento ipertestuale cliccabile al PDF
        # originale di QUESTA ricetta (non solo testo) — usa _source_file
        # dal JSON se presente, altrimenti ricostruisce dal barcode
        col_link = intestazione.get(COLONNA_LINK)
        if col_link:
            nome_file_pdf = dati.get("_source_file", f"{barcode}.pdf")
            percorso_pdf = (ricette_dir / nome_file_pdf).resolve()
            cella_link = ws.cell(row=riga_excel, column=col_link)
            cella_link.value = nome_file_pdf
            if percorso_pdf.exists():
                cella_link.hyperlink = str(percorso_pdf)
                cella_link.style = "Hyperlink"
            else:
                log.warning(f"  {barcode}: PDF non trovato per il link ({percorso_pdf})")

        log.info(f"  {barcode}: scritto in riga {riga_excel} ({dati.get('nome_cognome_assistito', '?')})")
        riga_excel += 1

    ultima_riga_dati = riga_excel - 1
    _allarga_tabelle_native(ws, ultima_riga_dati)

    out_path = output_dir / template_path.name
    try:
        wb.save(out_path)
        log.info(f"\nSalvato: {out_path} — {riga_excel - 2}/{len(json_files)} ricette scritte")
    except Exception as e:
        log.error(f"Errore salvataggio Excel: {e}")
        return

    if non_trovate_in_regione:
        log.warning(f"Barcode senza corrispondenza in Regione: {non_trovate_in_regione}")
    if codici_non_mappati:
        log.warning(
            f"Codici difformità prodotti ma SENZA colonna nel template "
            f"(dati non scritti nell'Excel finale): {codici_non_mappati} "
            f"— servono nuove colonne nel template se vanno inclusi"
        )


def main():
    ap = argparse.ArgumentParser(description="Genera il file Excel finale (Regione + OCR + Difformità)")
    ap.add_argument("--excel", type=Path, required=True,
                     help="File Excel Regione (es. excel_regione\\file_regione_Agosto_2025.xlsx)")
    ap.add_argument("--output", type=Path, default=Path("output"),
                     help="Cartella con i JSON prodotti dalla pipeline (default: output)")
    ap.add_argument("--template", type=Path, required=True,
                     help="File template Excel originale (con la formattazione delle 4 tabelle), "
                          "es. OUTPUT_FLUSSO_CANNABIS_FINALE.xlsx — NON viene modificato, "
                          "se ne salva una copia compilata in --output")
    ap.add_argument("--ricette", type=Path, default=Path("ricette"),
                     help="Cartella con i PDF originali delle ricette (default: ricette), "
                          "usata per il collegamento ipertestuale nella colonna LINK")
    args = ap.parse_args()
    run(args.excel, args.output, args.template, ricette_dir=args.ricette)


if __name__ == "__main__":
    main()
