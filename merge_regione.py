"""
merge_regione.py — Collegamento dati Regione con JSON estratti da OCR
ATS Insubria — Alessandro Marchinu, Francesco Milani

Legge il file Excel Regione (lo stesso caricato mensilmente in
Power Automate) e arricchisce ogni JSON prodotto da ocr_cannabis.py
con i campi necessari alle difformità 17 e 18:

    COD_FISCALE                    -> per check_18 (confronto CF)
    CONTROLLO_CODICE_PRESCRIZIONE  -> per check_17 (barcode duplicato)

Il collegamento avviene tramite il barcode, esattamente come nel
flusso Power Automate (Elenca righe tabella REGIONE + confronto).

Requisiti:
    pip install pandas openpyxl

Utilizzo:
    python merge_regione.py --excel excel_regione\04_APR_25.xlsx --output output
"""

import argparse
import json
import logging
import re
from pathlib import Path
from collections import Counter

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("MergeRegione")


def normalizza_barcode(val) -> str:
    """Normalizza il barcode per il confronto — rimuove spazi, prefissi non
    numerici (J iniziale, underscore/trattino, come nel file Excel Regione
    dove compare ad es. "_030190166851281")."""
    if pd.isna(val):
        return ""
    s = str(val).strip().replace(" ", "")
    s = s.lstrip("_-")
    if s.upper().startswith("J"):
        s = s[1:]
    return s


def carica_excel_regione(excel_path: Path) -> pd.DataFrame:
    """
    Carica il file Excel Regione. Cerca automaticamente la colonna
    contenente il barcode e quella del codice fiscale tra i nomi
    più comuni usati nei file ATS Insubria.
    """
    df = pd.read_excel(excel_path, dtype=str)
    log.info(f"Excel Regione caricato: {len(df)} righe, colonne: {list(df.columns)[:10]}...")

    # Cerca la colonna barcode tra i nomi possibili
    colonne_barcode_possibili = ["BARCODE", "Barcode", "barcode", "CODICE_BARCODE", "COD_BARCODE"]
    col_barcode = next((c for c in colonne_barcode_possibili if c in df.columns), None)

    if not col_barcode:
        # fallback: cerca qualsiasi colonna che contenga "barcode" nel nome
        col_barcode = next((c for c in df.columns if "barcode" in c.lower()), None)

    if not col_barcode:
        raise ValueError(
            f"Colonna barcode non trovata nell'Excel Regione. "
            f"Colonne disponibili: {list(df.columns)}\n"
            f"Rinomina la colonna in 'BARCODE' oppure aggiorna 'colonne_barcode_possibili' nello script."
        )

    df["_barcode_norm"] = df[col_barcode].apply(normalizza_barcode)
    log.info(f"Colonna barcode identificata: '{col_barcode}'")

    return df


def trova_colonna_cf(df: pd.DataFrame) -> str:
    """Cerca la colonna codice fiscale nel file Regione."""
    possibili = ["COD_FISCALE", "CODICE_FISCALE", "CodiceFiscale", "CF"]
    col = next((c for c in possibili if c in df.columns), None)
    if not col:
        col = next((c for c in df.columns if "fiscal" in c.lower()), None)
    return col


def trova_colonna_lordo(df: pd.DataFrame) -> str:
    """Cerca la colonna LORDO_PRESC nel file Regione."""
    possibili = ["LORDO_PRESC", "LORDO_PRESCRIZIONE", "Lordo_Presc"]
    col = next((c for c in possibili if c in df.columns), None)
    if not col:
        col = next((c for c in df.columns if "lordo" in c.lower()), None)
    return col


def trova_colonna_data_prescrizione(df: pd.DataFrame) -> str:
    """
    Cerca la colonna con la data di prescrizione nel file Regione.
    """
    possibili = [
        "DATA_PRESCR", "DATA_PRESCRIZIONE", "DataPrescrizione",
        "DATA_PRESC", "DATA_RICETTA", "Data_Prescrizione"
    ]
    col = next((c for c in possibili if c in df.columns), None)
    if not col:
        col = next(
            (c for c in df.columns if "presc" in c.lower() and "data" in c.lower()),
            None
        )
    return col


def trova_colonna_data_spedizione(df: pd.DataFrame) -> str:
    """
    Cerca la colonna con la data di spedizione nel file Regione — corrisponde
    al campo data_emissione estratto dal timbro "DATA SPEDIZIONE" sulla ricetta.
    """
    possibili = ["DATA_SPEDIZIONE", "DataSpedizione", "DATA_SPED"]
    col = next((c for c in possibili if c in df.columns), None)
    if not col:
        col = next(
            (c for c in df.columns if "sped" in c.lower() and "data" in c.lower()),
            None
        )
    return col


MESI_ABBR_EN = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def normalizza_data_regione(val) -> str:
    """
    Normalizza il valore data letto dall'Excel Regione nel formato GG/MM/AAAA,
    per poterlo confrontare con i valori GG/MM/AAAA prodotti da Qwen-VL/TrOCR.

    Formati gestiti:
    - già GG/MM/AAAA
    - formato specifico osservato nell'Excel Regione ATS Insubria:
      "25AUG2025:00:00:00" (giorno + mese abbreviato inglese + anno, ":" al
      posto dello spazio prima dell'ora) — pandas.to_datetime NON lo riconosce
      da solo, serve un parsing dedicato
    - fallback generico via pandas per altri formati (ISO, con orario standard, ecc.)
    """
    if pd.isna(val) or not str(val).strip():
        return ""

    s = str(val).strip()

    # Già nel formato GG/MM/AAAA
    m = re.match(r'^(\d{2})/(\d{2})/(\d{4})$', s)
    if m:
        return s

    # Formato specifico Regione: "25AUG2025:00:00:00"
    m2 = re.match(r'^(\d{1,2})([A-Za-z]{3})(\d{4})(?::|\s|$)', s)
    if m2:
        gg, mese_abbr, aaaa = m2.groups()
        mese_num = MESI_ABBR_EN.get(mese_abbr.upper())
        if mese_num:
            return f"{int(gg):02d}/{mese_num:02d}/{aaaa}"
        log.warning(f"  Mese non riconosciuto nel valore Regione: '{s}' (abbreviazione '{mese_abbr}')")
        return ""

    # Fallback: prova a parsare con pandas (gestisce ISO, con orario standard, ecc.)
    try:
        dt = pd.to_datetime(s, dayfirst=True, errors="coerce")
        if pd.isna(dt):
            log.warning(f"  Impossibile interpretare come data il valore Regione: '{s}' (non verrà usato per il confronto)")
            return ""
        return dt.strftime("%d/%m/%Y")
    except Exception as e:
        log.warning(f"  Errore parsing data Regione '{s}': {e}")
        return ""


def calcola_duplicati(df: pd.DataFrame) -> Counter:
    """Conta le occorrenze di ogni barcode per rilevare i duplicati (check_17)."""
    return Counter(df["_barcode_norm"].tolist())


TOLLERANZA_GIORNI = 3  # oltre questa distanza un candidato non è considerato "vicino" alla Regione


def _parse_data(s: str):
    """Converte una stringa GG/MM/AAAA in un oggetto data di pandas, o None se non valida/vuota."""
    if not s or s == "OCR_INCERTO":
        return None
    dt = pd.to_datetime(s, format="%d/%m/%Y", errors="coerce")
    return None if pd.isna(dt) else dt


def sostituisci_data_con_regione(dati: dict, campo: str, valore_regione: str, json_name: str) -> dict:
    """
    Il valore di data_prescrizione/data_emissione viene preso ESCLUSIVAMENTE
    dal file Excel Regione — nessuna lettura OCR (Qwen pagina/crop, TrOCR)
    viene più usata come valore finale per questi due campi, nemmeno come
    ripiego quando la Regione non è disponibile.

    Motivo: la lettura OCR su questi due campi si è dimostrata troppo
    inaffidabile (letture discordanti tra le tre fonti, bias sistematici
    come l'anno letto "2023" invece di "2025") per essere considerata
    utilizzabile anche solo come ripiego.

    Le letture OCR originali (qwen_raw, qwen_crop_raw, trocr_raw, salvate
    da date_corrector.py) restano comunque nel JSON per controllo/audit,
    ma NON decidono mai più il valore finale del campo.

    Comportamento:
    - Dato Regione disponibile -> il campo prende SEMPRE quel valore
    - Dato Regione NON disponibile (barcode senza match, colonna mancante,
      mese sbagliato...) -> il campo diventa "OCR_INCERTO" esplicito,
      non viene lasciato un valore OCR non verificato

    IMPORTANTE — trade-off consapevole: con questo approccio non è più
    possibile rilevare il caso in cui il valore scritto sulla ricetta
    diverga realmente da quanto registrato in Regione (una difformità
    genuina) — quel confronto è stato sacrificato in favore
    dell'affidabilità del dato.
    """
    valore_attuale = dati.get(campo, "")

    if not valore_regione:
        if valore_attuale != "OCR_INCERTO":
            log.warning(
                f"  {json_name}: nessun dato Regione disponibile per {campo} — "
                f"valore OCR ({valore_attuale!r}) NON utilizzato, campo marcato OCR_INCERTO"
            )
        dati[campo] = "OCR_INCERTO"
        dati[f"{campo}_da_regione"] = False
        return dati

    if valore_attuale != valore_regione:
        log.info(
            f"  {json_name}: {campo} preso da Regione "
            f"({valore_regione}, valore OCR era {valore_attuale!r})"
        )
    else:
        log.info(f"  {json_name}: {campo} già combaciava con Regione ({valore_regione})")

    dati[campo] = valore_regione
    dati[f"{campo}_da_regione"] = True
    return dati


def arricchisci_json(json_path: Path, df_regione: pd.DataFrame,
                      col_cf: str, col_lordo: str, col_data_presc: str, col_data_sped: str,
                      contatore_barcode: Counter) -> dict:
    """
    Arricchisce un singolo JSON con i dati Regione corrispondenti,
    trovati tramite match sul barcode.
    """
    dati = json.loads(json_path.read_text(encoding="utf-8-sig"))
    barcode = normalizza_barcode(dati.get("barcode", ""))

    if not barcode:
        log.warning(f"  {json_path.name}: barcode assente, impossibile fare il match")
        dati["COD_FISCALE"] = ""
        dati["CONTROLLO_CODICE_PRESCRIZIONE"] = "false"
        dati["LORDO_PRESC"] = ""
        return dati

    match = df_regione[df_regione["_barcode_norm"] == barcode]

    if match.empty:
        log.warning(f"  {json_path.name}: nessuna corrispondenza in Regione per barcode {barcode}")
        dati["COD_FISCALE"] = ""
        dati["CONTROLLO_CODICE_PRESCRIZIONE"] = "false"
        dati["LORDO_PRESC"] = ""
        return dati

    riga = match.iloc[0]
    dati["COD_FISCALE"] = "" if not col_cf or pd.isna(riga[col_cf]) else str(riga[col_cf])
    dati["LORDO_PRESC"] = "" if not col_lordo or pd.isna(riga[col_lordo]) else str(riga[col_lordo])

    # Duplicato se il barcode compare piu' di una volta nel file Regione
    n_occorrenze = contatore_barcode.get(barcode, 0)
    dati["CONTROLLO_CODICE_PRESCRIZIONE"] = "true" if n_occorrenze > 1 else "false"

    if n_occorrenze > 1:
        log.warning(f"  {json_path.name}: barcode {barcode} duplicato {n_occorrenze} volte in Regione")

    # Sostituzione diretta delle date con quelle Regione (fonte autorevole):
    # data_prescrizione da DATA_PRESCRIZIONE, data_emissione da DATA_SPEDIZIONE.
    # Vedi docstring di sostituisci_data_con_regione per il trade-off scelto.
    if col_data_presc:
        raw_presc = riga[col_data_presc]
        valore_regione_presc = normalizza_data_regione(raw_presc)
        log.info(f"  {json_path.name}: data_prescrizione letta={dati.get('data_prescrizione', '(assente)')} | Regione grezzo={raw_presc!r} -> normalizzato={valore_regione_presc!r}")
        dati = sostituisci_data_con_regione(dati, "data_prescrizione", valore_regione_presc, json_path.name)

    if col_data_sped:
        raw_sped = riga[col_data_sped]
        valore_regione_sped = normalizza_data_regione(raw_sped)
        log.info(f"  {json_path.name}: data_emissione letta={dati.get('data_emissione', '(assente)')} | Regione grezzo={raw_sped!r} -> normalizzato={valore_regione_sped!r}")
        dati = sostituisci_data_con_regione(dati, "data_emissione", valore_regione_sped, json_path.name)

    return dati


def run(excel_path: Path, output_dir: Path):
    """Arricchisce tutti i JSON nella cartella output con i dati Regione."""
    df_regione = carica_excel_regione(excel_path)
    col_cf = trova_colonna_cf(df_regione)
    col_lordo = trova_colonna_lordo(df_regione)

    if not col_cf:
        log.warning("Colonna codice fiscale non trovata in Regione — check_18 sara' sempre negativo")
    else:
        log.info(f"Colonna codice fiscale identificata: '{col_cf}'")

    if not col_lordo:
        log.warning("Colonna LORDO_PRESC non trovata in Regione — check_14 sara' sempre positivo (difformita')")
    else:
        log.info(f"Colonna LORDO_PRESC identificata: '{col_lordo}'")

    col_data_presc = trova_colonna_data_prescrizione(df_regione)
    if not col_data_presc:
        log.warning(
            "Colonna data prescrizione non trovata in Regione — la verifica/"
            "arbitraggio su data_prescrizione sara' saltata. Controlla l'elenco "
            "colonne stampato sopra e aggiorna 'trova_colonna_data_prescrizione'."
        )
    else:
        log.info(f"Colonna data prescrizione identificata: '{col_data_presc}'")

    col_data_sped = trova_colonna_data_spedizione(df_regione)
    if not col_data_sped:
        log.warning(
            "Colonna data spedizione non trovata in Regione — la verifica/"
            "arbitraggio su data_emissione sara' saltata. Controlla l'elenco "
            "colonne stampato sopra e aggiorna 'trova_colonna_data_spedizione'."
        )
    else:
        log.info(f"Colonna data spedizione identificata: '{col_data_sped}'")

    contatore_barcode = calcola_duplicati(df_regione)

    json_files = sorted(output_dir.glob("*.json"))
    json_files = [f for f in json_files if f.name != "riepilogo.json"]

    if not json_files:
        log.error(f"Nessun JSON trovato in {output_dir}")
        return

    log.info(f"Arricchimento di {len(json_files)} ricette con dati Regione...")

    aggiornati = 0
    da_regione = {"data_prescrizione": 0, "data_emissione": 0}
    mancanti_regione = {"data_prescrizione": 0, "data_emissione": 0}
    for json_path in json_files:
        try:
            dati_arricchiti = arricchisci_json(
                json_path, df_regione, col_cf, col_lordo, col_data_presc, col_data_sped, contatore_barcode
            )
            for campo in da_regione:
                if dati_arricchiti.get(f"{campo}_da_regione"):
                    da_regione[campo] += 1
                else:
                    mancanti_regione[campo] += 1
            json_path.write_text(
                json.dumps(dati_arricchiti, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            aggiornati += 1
        except Exception as e:
            log.error(f"  Errore su {json_path.name}: {e}")

    log.info(f"\nCompletato: {aggiornati}/{len(json_files)} JSON arricchiti con dati Regione")
    if col_data_presc:
        log.info(f"data_prescrizione presa da Regione: {da_regione['data_prescrizione']} — rimasta su valore OCR (Regione non disponibile): {mancanti_regione['data_prescrizione']}")
    if col_data_sped:
        log.info(f"data_emissione presa da Regione: {da_regione['data_emissione']} — rimasta su valore OCR (Regione non disponibile): {mancanti_regione['data_emissione']}")


def main():
    ap = argparse.ArgumentParser(description="Collega dati Excel Regione ai JSON estratti da OCR")
    ap.add_argument("--excel", type=Path, required=True,
                     help="Percorso del file Excel Regione (es. excel_regione\\04_APR_25.xlsx)")
    ap.add_argument("--output", type=Path, default=Path("output"),
                     help="Cartella con i JSON prodotti da ocr_cannabis.py (default: output)")
    args = ap.parse_args()

    if not args.excel.exists():
        log.error(f"File Excel non trovato: {args.excel}")
        return

    if not args.output.exists():
        log.error(f"Cartella output non trovata: {args.output}")
        return

    run(args.excel, args.output)


if __name__ == "__main__":
    main()
