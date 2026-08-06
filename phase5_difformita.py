"""
phase5_difformita.py — Difformità prescrizioni cannabis ATS Insubria
Versione 2.0 — Alessandro Marchinu, Francesco Milani

Riscrittura del modulo difformità del tutor con:
- Correzione nomi campo disallineati con ocr_cannabis.py
- Check deterministici semplificati dove il valore è già normalizzato a monte
- Check 05 reso rigoroso (il fuzzy va fatto in estrazione, non qui)
- Check 09 corretto: rimossi ingredienti generici dalla lista metodi estrattivi
- Check 12 allineato alle avvertenze effettivamente estratte dal prompt
- Check 13 corretto: campi fantasma rimossi, campo mancante aggiunto
- Check 19/20 sostituito con confronto fuzzy nomi (stesso sviluppato in Power Automate)
- NUOVI check semantici via Qwen2.5:7b (solo testo, non multimodale):
    05A — dicitura non responsività (interpretazione libera del testo)
    09B — metodo estrattivo non catalogato (oltre alla lista nota)
    19B — confronto fuzzy nome etichetta vs prescrizione (casi ambigui)

Requisiti:
    pip install pandas openpyxl ollama

Modello richiesto (solo se si vogliono i check semantici):
    ollama pull qwen2.5:7b

Utilizzo:
    from phase5_difformita import run
    run(input_dir=Path("output"), output_dir=Path("output"))

    # oppure singola riga:
    from phase5_difformita import analizza_riga
    difformita = analizza_riga(dati_json)
"""

import logging
import os
import re
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

logger = logging.getLogger("Phase5")

# ─── Configurazione LLM per check semantici ────────────────────────────────────

OLLAMA_MODEL_TESTO = os.getenv("OLLAMA_MODEL_TESTO", "qwen2.5vl:7b")
USA_LLM_SEMANTICO   = os.getenv("USA_LLM_SEMANTICO", "true").lower() == "true"

try:
    import ollama
    OLLAMA_DISPONIBILE = True
except ImportError:
    OLLAMA_DISPONIBILE = False
    logger.warning("Libreria ollama non disponibile — check semantici disabilitati")


# ─── Costanti di dominio ────────────────────────────────────────────────────────

CODICI_ESENZIONE_VALIDI = {"048", "046", "019", "005", "020", "TDL", "L99", "E30"}

FORME_FARMACEUTICHE_VALIDE = {"olio in flacone", "capsule", "cartine"}

# Metodi estrattivi VALIDI — solo nomi propri di metodica, non ingredienti/processi generici
METODI_ESTRATTIVI_NOTI = {
    "ramella", "calvi", "sifap", "sicam", "romano", "hazecamp", "hazekamp", "cannazza"
}

# Parole che indicano un processo generico ma NON un metodo estrattivo catalogato
# (usate per il check deterministico di primo livello, NON per validare come metodo)
INDIZI_PROCESSO_OLEOSO = {
    "olio", "mct", "estrazione oleosa", "estrazione a caldo", "olio di oliva",
    "soluzione oleosa", "contagocce", "estratto vegetale liquido"
}

AVVERTENZE_KEYWORD = {"doping", "sportiva", "guida", "antidoping"}


# ─── Utility di parsing ──────────────────────────────────────────────────────────

def _parse_date(val) -> datetime | None:
    """Converte una stringa data in datetime, gestendo i formati usati dalla pipeline."""
    if not val or str(val).strip().upper() in ("", "NAN", "NONE", "NAT", "OCR_INCERTO"):
        return None
    s = re.sub(r"[^0-9/]", "", str(val).strip())
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d%m%Y", "%d%m%y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _is_true(val) -> bool:
    return str(val).strip().lower() in ("true", "1", "yes", "sì", "si")


def _is_empty(val) -> bool:
    return str(val).strip().upper() in ("", "NAN", "NONE", "NAT")


def _is_incerto(val) -> bool:
    return str(val).strip().upper() == "OCR_INCERTO"


def _is_empty_or_incerto(val) -> bool:
    """Campo vuoto O incerto conta come mancante per le difformità."""
    return _is_empty(val) or _is_incerto(val)


def _norm_cf(val) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(val).upper().strip())


def _norm_nome(val) -> str:
    return re.sub(r"\s+", " ", str(val).strip().lower())


def _distanza_levenshtein(a: str, b: str) -> int:
    """
    Calcola la distanza di Levenshtein tra due stringhe: il numero
    minimo di inserimenti/cancellazioni/sostituzioni per trasformare
    una stringa nell'altra. Usata per il confronto rigoroso dei nomi
    (check_19), analogo alla tolleranza a caratteri gia' usata per il
    confronto del codice fiscale (check_18).
    """
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
            costo_sost = 0 if ca == cb else 1
            riga_corr[j] = min(
                riga_prec[j] + 1,          # cancellazione
                riga_corr[j-1] + 1,        # inserimento
                riga_prec[j-1] + costo_sost  # sostituzione
            )
        riga_prec = riga_corr
    return riga_prec[len_b]


# ─── Check deterministici ────────────────────────────────────────────────────────

def check_01(r: dict) -> bool:
    """R01 — Data prescrizione mancante."""
    return _is_empty_or_incerto(r.get("data_prescrizione", ""))


def check_02(r: dict) -> bool:
    """R02 — Timbro medico assente. (firma_medico e' sempre uguale a timbro_medico)"""
    return not _is_true(r.get("timbro_medico", ""))


def check_03(r: dict) -> bool:
    """R03 — Data emissione (spedizione) mancante."""
    return _is_empty_or_incerto(r.get("data_emissione", ""))


def check_04(r: dict) -> bool:
    """R04 — CF e nome assistito entrambi mancanti."""
    return _is_empty(r.get("codice_fiscale", "")) and \
           _is_empty(r.get("nome_cognome_assistito", ""))


def check_05(r: dict) -> bool:
    """
    R05 — Codice esenzione non valido.
    Match esatto per prima cosa; se non combacia, tolleranza fuzzy
    (distanza di Levenshtein ≤ 1) come rete di sicurezza — copre i casi
    in cui l'estrazione OCR non ha già normalizzato un refuso di lettura
    (es. "TOL" invece di "TDL": la correzione dovrebbe già avvenire in
    ocr_cannabis.py/date_corrector.py, ma qui c'è un secondo livello di
    protezione per i JSON prodotti prima di quel fix, o nel caso residuo
    in cui l'estrazione non abbia normalizzato tutto).
    """
    val = str(r.get("codice_esenzione", "")).strip().upper()
    if _is_empty(val):
        return True
    if val in CODICI_ESENZIONE_VALIDI:
        return False
    for candidato in CODICI_ESENZIONE_VALIDI:
        if _distanza_levenshtein(val, candidato) <= 1:
            return False  # refuso plausibile di un codice valido, va bene
    return True


def check_06(r: dict) -> bool:
    """
    R06 — Forma farmaceutica non valida.
    Match esatto per prima cosa; se non combacia, tolleranza fuzzy
    (distanza di Levenshtein ≤ 2) come rete di sicurezza — stessa
    logica gia' usata per check_05 (codice esenzione), qui applicata
    ai 3 valori ammessi per la forma farmaceutica.
    """
    forma = str(r.get("forma_farmaceutica", "")).strip().lower()
    if _is_empty(forma):
        return True
    if forma in FORME_FARMACEUTICHE_VALIDE:
        return False
    for candidato in FORME_FARMACEUTICHE_VALIDE:
        if _distanza_levenshtein(forma, candidato) <= 2:
            return False  # refuso plausibile di una forma valida, va bene
    return True


def check_07(r: dict) -> bool:
    """
    R07 — Ricetta spedita oltre 30 giorni dalla prescrizione.
    Scatta anche se una delle due date e' assente/incerta (non e'
    possibile verificare la coerenza temporale -> difformita').
    """
    dp = _parse_date(r.get("data_prescrizione"))
    de = _parse_date(r.get("data_emissione"))
    if not dp or not de:
        return True
    return de >= dp + timedelta(days=30)


def check_08(r: dict) -> bool:
    """
    R08 — Codice ATC mancante/errato.
    Match esatto per prima cosa; se non combacia, tolleranza fuzzy
    (distanza di Levenshtein ≤ 3) come rete di sicurezza — soglia più
    ampia rispetto agli altri campi perché qui c'è un SOLO valore valido
    possibile (N02BG10), quindi non c'è rischio di correggere verso il
    codice sbagliato come invece capiterebbe con piu' valori validi tra
    cui scegliere (es. codice_esenzione).
    """
    atc = str(r.get("codice_atc", "")).strip().upper()
    if _is_empty(atc):
        return True
    if atc == "N02BG10":
        return False
    return _distanza_levenshtein(atc, "N02BG10") > 3


def check_09_deterministico(r: dict) -> bool | None:
    """
    R09 — Metodo estrattivo mancante (solo per formulazioni oleose).
    Restituisce:
        True  -> difformita' certa (formulazione oleosa, nessun indizio di metodo)
        False -> nessuna difformita' (metodo noto presente)
        None  -> caso ambiguo, richiede il check semantico LLM (09B)
    """
    forma = str(r.get("forma_farmaceutica", "")).lower()
    testo = str(r.get("testo_prescrizione", "")).lower()
    metodo = str(r.get("metodo_estrattivo_olio", "")).lower().strip()

    e_oleosa = forma == "olio in flacone" or \
               any(i in testo for i in INDIZI_PROCESSO_OLEOSO)

    if not e_oleosa:
        return False  # non e' una formulazione oleosa, check non applicabile

    # Eccezione: produttore industriale (Tilray/Avextra) non richiede
    # un metodo estrattivo specifico — il farmaco e' gia' pronto/standardizzato.
    # Confronto fuzzy parola per parola (distanza 2), non solo match esatto —
    # copre refusi OCR come "Tilary" invece di "Tilray".
    if forma == "olio in flacone" and _is_empty(metodo):
        parole_testo = re.findall(r"[a-z]+", testo)
        for parola in parole_testo:
            if len(parola) < 5:
                continue  # parole troppo corte, rischio di falsi positivi
            if _distanza_levenshtein(parola, "tilray") <= 2 or _distanza_levenshtein(parola, "avextra") <= 2:
                return False

        # Segnale indipendente dal nome specifico del marchio: la frase
        # "(o altre marche in commercio)" accompagna quasi sempre la
        # menzione di un farmaco industriale già pronto — utile quando il
        # nome del marchio stesso è troppo rovinato per il confronto fuzzy
        # sopra, ma questa frase resta leggibile.
        if re.search(r"altre\s*march[ei]\s*in\s*commercio", testo):
            return False

    if metodo in METODI_ESTRATTIVI_NOTI:
        return False  # metodo noto e valido, gia' estratto correttamente

    if not _is_empty(metodo):
        # Prima di considerarlo un metodo "sconosciuto", verifica se e'
        # un refuso di un metodo noto (OCR/battitura) a distanza 1
        # carattere — es. "Ramela" invece di "Ramella", "Sifab" invece
        # di "Sifap". Match esatto gia' escluso sopra.
        for noto in METODI_ESTRATTIVI_NOTI:
            if _distanza_levenshtein(metodo, noto) <= 1:
                return False  # refuso plausibile di un metodo noto, va bene

        # Controllo "contains": il metodo estratto puo' essere una frase
        # piu' lunga che CONTIENE il nome del metodo noto, invece di
        # combaciare esattamente o per un semplice refuso — es. "SIFAP
        # modificata sec. Ramella et Al" contiene "ramella" ma e' troppo
        # diverso nel complesso per un match a distanza 1.
        for noto in METODI_ESTRATTIVI_NOTI:
            if noto in metodo:
                return False  # metodo noto contenuto nella frase, va bene

    if _is_empty(metodo):
        # Campo dedicato vuoto: prima di dichiarare ambiguo, riscansiona
        # testo_prescrizione con la STESSA tolleranza fuzzy (distanza 1)
        # già usata poco sopra per il campo metodo_estrattivo_olio quando
        # NON è vuoto — l'estrazione del campo dedicato può fallire anche
        # quando il testo libero contiene comunque un metodo noto (o un
        # refuso vicino) perfettamente leggibile.
        parole_testo = re.findall(r"[a-zà-ù]+", testo)
        for parola in parole_testo:
            if len(parola) < 5:
                continue  # parole troppo corte, rischio di falsi positivi
            for noto in METODI_ESTRATTIVI_NOTI:
                if _distanza_levenshtein(parola, noto) <= 1:
                    return False  # metodo noto trovato nel testo, anche se il campo dedicato è vuoto

        # Ancora nulla: potrebbe essere un metodo nuovo non catalogato
        # menzionato nel testo, o "Tilray"/"Avextra" scritto in modo
        # troppo diverso anche per la tolleranza sopra -> serve
        # interpretazione semantica
        return None

    return True  # c'e' un valore ma non e' tra quelli noti (nemmeno a distanza 1) — sospetto


def check_10_deterministico(r: dict) -> bool | None:
    """
    R10 — Posologia mancante, verificata in base alla forma farmaceutica:
    - cartine/capsule: richiede "N cartine/capsule/cps" E una frequenza
      (die / al giorno / ogni XX ore)
    - olio in flacone: richiede "N gocce/gtt/mg" E una frequenza
      (die / al giorno / ogni XX ore)
    - forma vuota/non riconosciuta: fallback al controllo generico
      (comportamento precedente, non specifico per forma)

    Restituisce:
        True  -> difformita' certa (testo vuoto)
        False -> nessuna difformita' (pattern trovato)
        None  -> pattern non trovato dal regex, ma potrebbe essere un
                 refuso OCR non ancora coperto -> serve il fallback
                 semantico (check_10B_semantico) prima di dichiarare
                 la difformita' definitiva
    """
    testo = str(r.get("testo_prescrizione", "")).lower()
    forma = str(r.get("forma_farmaceutica", "")).strip().lower()

    if _is_empty(testo):
        return True

    pattern_frequenza = re.compile(
        r"(die\b)|(al\s*giorno)|(ogni\s*\d+\s*ore)|(pr[oò]\s*/?\s*die)"
        r"|(nelle\s*\d+\s*(?:ore|ora|/?\s*h)\b)"
        r"|(\d+\s*/?\s*\d*\s*h\b)"        # "THC/24h", "5-7% ... /24h" — slash+numero+h senza "nelle"
        r"|(al\s*d[iì]\b)"                # "al dì" (con o senza accento)
        r"|(in\s*\d+\s*(?:ore|ora|/?\s*h)\b)"  # "in 24 ore" / "in 24 h" (con "in" invece di "nelle")
        r"|(mattin[oa]|sera\b|pranzo)"     # orario di somministrazione implicito ("mattino e sera")
        r"|(/\s*due\b)"                   # "/due" con slash davanti (refuso OCR di "/die") — MAI "due" isolato
        r"|(\d+\s*ore\b)|(\d+\s*ora\b)",   # "N ore"/"N ora" da sole, senza parola di collegamento —
                                           # recupera i casi dove "nelle"/"ogni"/"in" viene letto in modo
                                           # troppo vario dall'OCR (es. "melle", "NEI/E", "MESE")
        re.IGNORECASE
    )

    # I numeri possono essere scritti a cifre o a lettere (es. "Una cartina",
    # "2 cartine") — accetto entrambi per la quantita'
    NUMERI_LETTERE = r"(?:un|uno|una|due|tre|quattro|cinque|sei|sette|otto|nove|\d+)"

    if forma in ("cartine", "capsule"):
        pattern_quantita = re.compile(rf"{NUMERI_LETTERE}\s*(?:cartine|cartina|capsule|capsula|cps)", re.IGNORECASE)
        if pattern_quantita.search(testo) and pattern_frequenza.search(testo):
            return False
        return None

    if forma == "olio in flacone":
        pattern_quantita = re.compile(r"\d+[.,]?\d*\s*(?:thc\s*|cbd\s*)?(?:gtt|gocce|mg)", re.IGNORECASE)
        if pattern_quantita.search(testo) and pattern_frequenza.search(testo):
            return False
        return None

    # Forma vuota/non riconosciuta: fallback al controllo generico precedente
    pattern_generico = re.compile(
        r"(\d+\s*(?:gtt|gocce|mg|capsule|cps|cartine|soff))"
        r"|(\d+\s*x\s*\d+)"
        r"|(posologia)"
        r"|(\d+\s*mg\s*/\s*die)"
        r"|(pro\s*/?\s*die)"
        r"|(\d+\s*volte\s*al\s*giorno)"
        r"|(ogni\s*\d+\s*ore)"
        r"|(al\s*d[iì])"
        r"|(al\s*giorno)",
        re.IGNORECASE
    )
    if pattern_generico.search(testo):
        return False
    return None


def check_10B_semantico(r: dict) -> bool:
    """
    R10b — Verifica semantica della posologia per i casi lasciati
    ambigui da check_10_deterministico (pattern non riconosciuto dal
    regex, ma potrebbe essere un refuso OCR non ancora coperto — es.
    "nelle" letto come "melle"/"MESE"/"NEI/E", o quantita' scritta in
    un modo non ancora previsto).
    """
    testo = str(r.get("testo_prescrizione", "")).strip()
    if _is_empty(testo):
        return True

    if not USA_LLM_SEMANTICO or not OLLAMA_DISPONIBILE:
        # Nessun modo di risolvere l'ambiguita' senza LLM: prudente,
        # nessuna difformita' (evita falsi positivi su OCR degradato)
        return False

    prompt = f"""Analizza questo testo di una prescrizione medica di cannabis terapeutica, anche se contiene refusi OCR (lettere/cifre scambiate, parole spezzate).

TESTO: "{testo}"

Il testo contiene un'indicazione di POSOLOGIA — cioè una QUANTITA' da assumere (es. gocce, mg, cartine, capsule) E una FREQUENZA di somministrazione (es. al giorno, ogni X ore, mattina e sera, "die")? Interpreta il testo con tolleranza verso refusi OCR: se la quantita' o la frequenza sono scritte in un modo insolito o con errori di battitura/lettura ma il senso è chiaramente presente, considera la posologia PRESENTE.

Rispondi SOLO con questo JSON, nessun altro testo:
{{"posologia_presente": true, "motivazione": "breve spiegazione del ragionamento in una frase"}} oppure {{"posologia_presente": false, "motivazione": "breve spiegazione del ragionamento in una frase"}}"""

    risultato = _chiama_llm_testo(prompt)
    presente = risultato.get("posologia_presente", None)
    motivazione = risultato.get("motivazione", "")
    if motivazione:
        logger.info(f"  [10B] ragionamento: {motivazione}")

    if presente is None:
        logger.warning("  check_10B: risposta LLM non valida, fallback a True (difformita')")
        return True

    return not presente


def check_11(r: dict) -> bool:
    """
    R11 — Data preparazione fuori range (prima della prescrizione o dopo l'emissione).
    Scatta anche se una qualsiasi delle 3 date necessarie e' assente/incerta.
    """
    dprep  = _parse_date(r.get("data_etichetta_preparazione"))
    dpresc = _parse_date(r.get("data_prescrizione"))
    demiss = _parse_date(r.get("data_emissione"))
    if not dprep or not dpresc or not demiss:
        return True
    if dprep < dpresc:
        return True
    if dprep > demiss:
        return True
    return False


def check_12(r: dict) -> bool:
    """
    R12 — Avvertenze doping/guida mancanti.
    Allineato al prompt di estrazione, che esclude esplicitamente
    "DPR 309/90" e "tenere fuori dalla portata dei bambini" come
    boilerplate non significativo — qui si cerca solo il contenuto
    realmente rilevante (doping, attivita' sportiva, guida).
    """
    avv = str(r.get("etichetta_avvertenze", "")).lower()
    if _is_empty(avv):
        return True
    return not any(k in avv for k in AVVERTENZE_KEYWORD)


def check_13(r: dict) -> bool:
    """
    R13 — Prezzi etichetta mancanti.
    Corretto: rimosso il campo fantasma "etichetta_THC" (non esiste,
    il campo si chiama "THC" senza prefisso). Aggiunto "etichetta_prezzo_sost"
    che era stato dimenticato nella versione precedente.
    """
    campi = [
        "etichetta_prezzo_sost", "etichetta_prezzo_on",
        "etichetta_prezzo_rec", "etichetta_prezzo_iva", "etichetta_prezzo_tot", "THC"
    ]
    return any(_is_empty_or_incerto(r.get(c, "")) for c in campi)


def check_14(r: dict) -> bool:
    """
    R14 — Confronto prezzo totale con LORDO_PRESC (file Regione).
    Il campo LORDO_PRESC arriva gia' in formato "nnn,dd" dal file Regione.
    Nessuna difformita' se almeno uno tra:
        totale_prescrizione
        totale_prescrizione + 15
        etichetta_prezzo_tot
        etichetta_prezzo_tot + 15
    corrisponde a LORDO_PRESC (tolleranza 1 euro, per assorbire piccoli
    errori di lettura sui centesimi senza perdere il confronto sostanziale).
    Difformita' se LORDO_PRESC e' assente/vuoto, o se nessuno dei 4
    valori corrisponde.
    """
    lordo_raw = str(r.get("LORDO_PRESC", "")).strip()
    if _is_empty(lordo_raw):
        return True

    try:
        # Gestisce sia formato testo "156,15" sia eventuali artefatti di
        # precisione float se la colonna Excel era numerica ("156.14999...")
        lordo = round(float(lordo_raw.replace(",", ".")), 2)
    except ValueError:
        logger.warning(f"  check_14: LORDO_PRESC non numerico: '{lordo_raw}'")
        return True

    def _to_float(val) -> float | None:
        v = str(val).strip()
        if _is_empty_or_incerto(v):
            return None
        try:
            return round(float(v.replace(",", ".")), 2)
        except ValueError:
            return None

    totale_presc = _to_float(r.get("totale_prescrizione", ""))
    etichetta_tot = _to_float(r.get("etichetta_prezzo_tot", ""))

    candidati = []
    if totale_presc is not None:
        candidati.append(totale_presc)
        candidati.append(totale_presc + 15)
    if etichetta_tot is not None:
        candidati.append(etichetta_tot)
        candidati.append(etichetta_tot + 15)

    if not candidati:
        return True  # nessun valore disponibile per il confronto

    TOLLERANZA = 1.0  # 1 euro di tolleranza (prima 0.01 — troppo stretta, sbagliava sui centesimi)
    for c in candidati:
        if abs(c - lordo) <= TOLLERANZA:
            return False  # corrispondenza trovata, nessuna difformita'

    return True


def check_16(r: dict) -> bool:
    """
    R16 — Data preparazione antecedente alla data prescrizione.
    Scatta anche se una delle due date e' assente/incerta.
    """
    dprep  = _parse_date(r.get("data_etichetta_preparazione"))
    dpresc = _parse_date(r.get("data_prescrizione"))
    if not dprep or not dpresc:
        return True
    return dprep <= dpresc


def check_17(r: dict) -> bool:
    """
    R17 — Codice prescrizione duplicato.
    Popolato altrove nella pipeline (confronto con Excel Regione,
    analogo al controllo barcode gia' sviluppato in Power Automate).
    """
    return _is_true(r.get("CONTROLLO_CODICE_PRESCRIZIONE", ""))


def check_18(r: dict) -> bool:
    """
    R18 — Codice fiscale OCR diverso dal codice fiscale Regione.
    Confronto tramite VERA distanza di Levenshtein (stesso algoritmo usato
    in check_19 per i nomi e per il CF nell'etichetta) — non piu' un
    confronto posizione-per-posizione. Gestisce anche inserimenti/
    cancellazioni, non solo sostituzioni: un singolo carattere perso o
    duplicato conta come 1 sola modifica, invece di far disallineare
    (e quindi contare come sbagliati) tutti i caratteri successivi come
    succedeva col confronto posizionale precedente.

    Scatta anche se il confronto non e' possibile (CF Regione o CF OCR
    assenti — es. merge_regione.py non eseguito, o nessuna corrispondenza
    trovata nel file Excel Regione tramite barcode), o se uno dei due ha
    una lunghezza del tutto implausibile per un codice fiscale (un CF
    vero ha sempre 16 caratteri, ma qui si tollera un margine perche' e'
    proprio quello che la distanza di Levenshtein serve a gestire).
    """
    cf_ocr = _norm_cf(r.get("codice_fiscale", ""))
    cf_reg = _norm_cf(r.get("COD_FISCALE", ""))
    if not cf_ocr or not cf_reg:
        return True  # confronto impossibile -> difformita'

    # Lunghezza plausibile (margine generoso: la distanza di Levenshtein
    # gestisce comunque le differenze di 1-2 caratteri; qui si scarta
    # solo il caso di stringhe palesemente non-CF, troppo corte/lunghe)
    if not (10 <= len(cf_ocr) <= 20) or not (10 <= len(cf_reg) <= 20):
        return True

    diff = _distanza_levenshtein(cf_ocr, cf_reg)
    return diff >= 3


# Soglia massima di caratteri di differenza ammessi tra i due nomi
# (stesso principio del check_18 sul codice fiscale, ma applicato ai nomi)
SOGLIA_DISTANZA_NOME = 3


def _espandi_iniziale_fusa(parole: list) -> list:
    """
    Se una parola è nella forma "x.cognome" (iniziale puntata SENZA spazio
    prima del cognome, es. "f.vigentini" invece di "f. vigentini"), la
    separa in due parole "x." e "cognome" — altrimenti il confronto
    posizionale con l'iniziale fallisce subito per semplice differenza di
    conteggio parole (1 parola fusa contro le 2 dell'altro nome), prima
    ancora di poter verificare se le iniziali combaciano davvero.
    """
    risultato = []
    for p in parole:
        m = re.match(r'^([a-z])\.([a-z]{2,})$', p)
        if m:
            risultato.append(m.group(1) + ".")
            risultato.append(m.group(2))
        else:
            risultato.append(p)
    return risultato


def _nomi_combaciano_con_iniziali(parole_etichetta: list, parole_assistito: list) -> bool:
    """
    Confronta due nomi già normalizzati e divisi in parole, posizione per
    posizione: se una parola nell'etichetta è una singola lettera (es. "m"
    da "M." — il punto resta dopo _norm_nome, viene tolto qui), basta che
    la parola corrispondente nell'assistito INIZI con quella lettera
    (es. "m" combacia con "mario"). Le altre parole richiedono
    corrispondenza esatta. Le due liste devono avere la stessa lunghezza.
    """
    if len(parole_etichetta) != len(parole_assistito) or not parole_etichetta:
        return False
    for p_et, p_ass in zip(parole_etichetta, parole_assistito):
        p_et_pulita = p_et.rstrip(".")
        if len(p_et_pulita) == 1:
            if not p_ass.startswith(p_et_pulita):
                return False
        elif p_et_pulita != p_ass:
            return False
    return True


def check_19_deterministico(r: dict) -> bool | None:
    """
    R19 — Nome paziente etichetta vs nome assistito.
    Confronto RIGOROSO basato sulla distanza di Levenshtein, analogo
    al confronto a tolleranza di caratteri gia' usato per il codice
    fiscale in check_18 — non piu' un semplice "contains" (troppo
    permissivo: bastavano 2-3 lettere in comune per passare).

    Il confronto avviene su:
    - nome completo etichetta vs nome completo assistito
    - nome completo etichetta vs nome assistito con ordine invertito
      (nel caso l'ordine nome/cognome sia scambiato tra i due campi)
    - stesso confronto ma tollerante alle iniziali puntate (es. "M. Rossi"
      vs "Mario Rossi" — l'iniziale deve solo combaciare con la prima
      lettera del nome esteso corrispondente)
    Se la distanza minima tra questi confronti e' entro la soglia
    (SOGLIA_DISTANZA_NOME caratteri), o se c'e' corrispondenza tramite
    iniziali, -> nessuna difformita'.

    Il confronto col codice fiscale (se l'etichetta riporta il CF invece
    del nome) resta un caso speciale gestito a parte: prima un contenimento
    esatto (il CF compare per intero, eventualmente con altro testo
    attorno), poi — se l'etichetta e' quasi interamente il CF — un
    confronto fuzzy con la stessa tolleranza di check_18.

    Restituisce:
        True  -> difformita' certa (etichetta vuota)
        False -> nessuna difformita' (corrispondenza entro soglia)
        None  -> distanza oltre soglia, richiede check semantico 19B
    """
    etichetta = str(r.get("etichetta_nome_cognome_paziente", "")).strip()
    assistito = str(r.get("nome_cognome_assistito", "")).strip()
    cf        = str(r.get("codice_fiscale", "")).strip()

    if _is_empty(etichetta):
        return True

    # 1. Contenimento esatto del CF (eventualmente con altro testo attorno)
    #    — controllato PRIMA del check su assistito vuoto, perche' non
    #    dipende da quel campo: l'etichetta puo' riportare il CF anche
    #    quando nome_cognome_assistito e' vuoto (privacy shortening).
    if cf and cf.upper() in etichetta.upper():
        return False

    # 2. CF a confronto fuzzy (stessa tolleranza di check_18), quando
    #    l'etichetta e' quasi interamente il CF ma con qualche refuso OCR
    cf_norm = _norm_cf(cf)
    etichetta_come_cf = _norm_cf(etichetta)
    if cf_norm and len(cf_norm) == 16 and abs(len(etichetta_come_cf) - len(cf_norm)) <= 3:
        if _distanza_levenshtein(cf_norm, etichetta_come_cf) < 3:
            return False

    if _is_empty(assistito):
        return None  # non possiamo confrontare il nome, serve valutazione semantica

    et_norm  = _norm_nome(etichetta)
    ass_norm = _norm_nome(assistito)

    # 3. Nomi con iniziali puntate (es. "M. Rossi" vs "Mario Rossi"), con
    #    espansione delle parole fuse tipo "f.vigentini" -> "f.", "vigentini"
    parole_et  = _espandi_iniziale_fusa(et_norm.split())
    parole_ass = _espandi_iniziale_fusa(ass_norm.split())
    if _nomi_combaciano_con_iniziali(parole_et, parole_ass):
        return False
    if len(parole_ass) >= 2 and _nomi_combaciano_con_iniziali(parole_et, parole_ass[::-1]):
        return False

    # 4. Controllo "contains": l'etichetta potrebbe contenere il nome
    #    completo dell'assistito insieme ad altro testo attorno, o
    #    viceversa essere un sottoinsieme esatto (es. solo cognome, o
    #    nome completo senza spazi) — confronto sull'INTERA stringa
    #    normalizzata, non su singole parole (un controllo per singola
    #    parola sarebbe troppo permissivo: basterebbe che combaci solo il
    #    cognome, invalidando il controllo sulle iniziali sbagliate fatto
    #    al punto 3).
    if len(ass_norm) >= 3 and ass_norm in et_norm:
        return False
    if len(et_norm) >= 3 and et_norm.replace(".", "").strip() in ass_norm:
        return False

    # 5. Confronto diretto nome completo (Levenshtein)
    distanza = _distanza_levenshtein(et_norm, ass_norm)

    # Confronto con ordine invertito (es. "Rossi Mario" vs "Mario Rossi")
    if len(parole_ass) >= 2:
        ass_invertito = " ".join(parole_ass[::-1])
        distanza_inv = _distanza_levenshtein(et_norm, ass_invertito)
        distanza = min(distanza, distanza_inv)

    if distanza <= SOGLIA_DISTANZA_NOME:
        return False  # entro soglia di tolleranza -> nessuna difformita'

    return None  # distanza oltre soglia — ambiguo, serve valutazione LLM


# ─── Check semantici via Qwen2.5:7b ──────────────────────────────────────────────

def _chiama_llm_testo(prompt: str) -> dict:
    """Chiama il modello di solo testo e restituisce il JSON parsato."""
    if not OLLAMA_DISPONIBILE:
        return {}
    try:
        response = ollama.chat(
            model=OLLAMA_MODEL_TESTO,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0, "num_ctx": 4096}
        )
        testo = response["message"]["content"].strip()
        match = re.search(r'\{[\s\S]*\}', testo)
        if match:
            return json.loads(match.group(0))
    except Exception as e:
        logger.warning(f"Errore chiamata LLM semantico: {e}")
    return {}


def check_05A_semantico(r: dict) -> bool:
    """
    R05A — Verifica semantica della dicitura di non responsivita' ai
    farmaci standard. Sostituisce la lista fissa di keyword con
    interpretazione libera del testo da parte del modello.
    """
    testo = str(r.get("testo_prescrizione", "")).strip()
    if _is_empty(testo):
        return True

    if not USA_LLM_SEMANTICO or not OLLAMA_DISPONIBILE:
        # fallback deterministico su keyword note se LLM non disponibile
        keyword = [
            "non rispondente", "non responsivo", "non responsiva",
            "refrattario", "refrattaria", "resistente", "mancata risposta",
            "non responder", "non risponde", "non rispondente ai", "pz non resp alle tp convenzionali", " Trattamento del dolore"," riprovenzionali",
            "non rispost", "inefficac",
        ]
        return not any(k in testo.lower() for k in keyword)

    prompt = f"""Analizza questo testo di una prescrizione medica di cannabis terapeutica, anche se contiene refusi OCR (lettere/cifre scambiate, parole spezzate o unite, spaziatura irregolare).

TESTO: "{testo}"

Il testo contiene una dicitura che indica che il paziente NON risponde
o non ha risposto adeguatamente ai farmaci/medicinali industriali/convenzionali
disponibili in commercio? Questo puo' essere espresso in molti modi diversi,
ad esempio: "non responsivo/a", "refrattario/a alla terapia", "non ha risposto
ad altre cure", "resistente ai trattamenti standard", "non rispondente",
"mancata risposta", "non responder", "non risponde", "pz non resp alle tp
convenzionali", "non risposto/a/e", "trattamenti inefficaci", ecc.

Interpreta con tolleranza verso i refusi OCR: se una di queste espressioni
è scritta in modo leggermente diverso o con errori di battitura/lettura ma
il senso è chiaramente presente, considera la dicitura PRESENTE.

Rispondi SOLO con questo JSON, nessun altro testo:
{{"dicitura_presente": true, "motivazione": "breve spiegazione del ragionamento in una frase"}} oppure {{"dicitura_presente": false, "motivazione": "breve spiegazione del ragionamento in una frase"}}"""

    risultato = _chiama_llm_testo(prompt)
    presente = risultato.get("dicitura_presente", None)
    motivazione = risultato.get("motivazione", "")
    if motivazione:
        logger.info(f"  [05A] ragionamento: {motivazione}")

    if presente is None:
        # LLM non ha risposto correttamente, fallback prudente: nessuna difformita'
        logger.warning("  check_05A: risposta LLM non valida, fallback a False")
        return False

    return not presente


def check_09B_semantico(r: dict) -> bool:
    """
    R09b — Verifica semantica del metodo estrattivo per i casi ambigui
    lasciati indeterminati da check_09_deterministico.

    Copre due situazioni distinte, entrambe valutate dal modello:
    1. Il testo cita un metodo estrattivo specifico non ancora catalogato
       (nome proprio di metodica diverso da Ramella/Calvi/SIFAP/ecc.)
    2. Il produttore e' industriale (Tilray/Avextra) ma scritto con un
       errore di battitura/OCR che il confronto esatto Python non ha
       riconosciuto (es. "Tylray", "Avextrà", "Tilray0")
    In entrambi i casi -> nessuna difformita'.
    """
    forma   = str(r.get("forma_farmaceutica", "")).strip().lower()
    testo   = str(r.get("testo_prescrizione", "")).strip()
    metodo  = str(r.get("metodo_estrattivo_olio", "")).strip()

    if not USA_LLM_SEMANTICO or not OLLAMA_DISPONIBILE:
        return True  # senza LLM, caso ambiguo -> difformita' prudenziale

    prompt = f"""Analizza questo testo di una prescrizione medica di cannabis terapeutica.

TESTO: "{testo}"
FORMA FARMACEUTICA: "{forma}"
METODO ESTRATTIVO GIA' RILEVATO (puo' essere vuoto): "{metodo}"

Rispondi a DUE domande su questo testo:

DOMANDA A — Il testo menziona "Tilray" o "Avextra" come produttore,
anche se scritto con un possibile errore di battitura o lettura OCR
(es. "Tylray", "Avextrà", "Tilray0", "Avextar")? Questi sono produttori
industriali il cui farmaco e' gia' pronto e non richiede una metodica
estrattiva specifica.

DOMANDA B — Se la risposta ad A e' no: il testo cita comunque un
metodo estrattivo specifico (nome proprio di una metodica brevettata/
pubblicata), anche se diverso da Ramella/Calvi/SIFAP/SICAM/Romano/
Hazecamp/Cannazza? Non contare descrizioni generiche come "estrazione
oleosa" o "olio MCT" — sono ingredienti/processi generici, non un
metodo con nome proprio.

Rispondi SOLO con questo JSON:
{{"produttore_industriale": true|false, "metodo_specifico_presente": true|false, "motivazione": "breve spiegazione del ragionamento in una frase"}}"""

    risultato = _chiama_llm_testo(prompt)
    produttore_industriale = risultato.get("produttore_industriale", None)
    metodo_presente = risultato.get("metodo_specifico_presente", None)
    motivazione = risultato.get("motivazione", "")
    if motivazione:
        logger.info(f"  [09B] ragionamento: {motivazione}")

    if produttore_industriale is None and metodo_presente is None:
        logger.warning("  check_09B: risposta LLM non valida, fallback a True (difformita')")
        return True

    if produttore_industriale:
        return False  # produttore industriale riconosciuto -> nessuna difformita'

    if metodo_presente:
        return False  # metodo specifico riconosciuto -> nessuna difformita'

    return True


def check_19B_semantico(r: dict) -> bool:
    """
    R19b — Confronto semantico nome etichetta vs nome assistito per i
    casi ambigui lasciati indeterminati da check_19_deterministico
    (es. errori OCR tipo Giamoni/Giannoni, iniziali, ordine invertito).
    """
    etichetta = str(r.get("etichetta_nome_cognome_paziente", "")).strip()
    assistito = str(r.get("nome_cognome_assistito", "")).strip()

    if not USA_LLM_SEMANTICO or not OLLAMA_DISPONIBILE:
        return True  # senza LLM, ambiguo -> difformita' prudenziale

    prompt = f"""Confronta questi due nomi che dovrebbero riferirsi alla
stessa persona su una ricetta medica:

NOME NELL'ETICHETTA FARMACIA: "{etichetta}"
NOME NELL'INTESTAZIONE RICETTA: "{assistito}"

NOTA: un confronto automatico ha gia' verificato che i due nomi sono
SIGNIFICATIVAMENTE diversi (troppe lettere di differenza per essere
un semplice errore di battitura o OCR). Sii quindi rigoroso: rispondi
"stessa persona" SOLO se riesci a spiegare la differenza con una causa
specifica e plausibile (es. iniziali puntate al posto del nome completo,
ordine nome/cognome invertito, un singolo carattere OCR ambiguo tipo
0/O, 1/I, o simili). Se i nomi sembrano davvero due persone diverse,
oppure la differenza e' troppo estesa per un errore plausibile,
rispondi che NON sono la stessa persona.

Rispondi SOLO con questo JSON:
{{"stessa_persona": true, "motivazione": "breve spiegazione del ragionamento in una frase"}} oppure {{"stessa_persona": false, "motivazione": "breve spiegazione del ragionamento in una frase"}}"""

    risultato = _chiama_llm_testo(prompt)
    stessa = risultato.get("stessa_persona", None)
    motivazione = risultato.get("motivazione", "")
    if motivazione:
        logger.info(f"  [19B] ragionamento: {motivazione}")

    if stessa is None:
        logger.warning("  check_19B: risposta LLM non valida, fallback a True (difformita')")
        return True

    return not stessa


# ─── Registro check e motivazioni ────────────────────────────────────────────────

CHECKS_DETERMINISTICI = {
    "01": ("Data scadenza etichetta mancante",     check_01),
    "02": ("Timbro medico assente",                check_02),
    "03": ("Data emissione mancante",               check_03),
    "04": ("CF e nome assistito entrambi mancanti", check_04),
    "05": ("Codice esenzione non valido",           check_05),
    "06": ("Forma farmaceutica non valida",         check_06),
    "07": ("Ricetta spedita oltre 30 giorni",       check_07),
    "08": ("Codice ATC mancante/errato",            check_08),
    "11": ("Data preparazione fuori range",         check_11),
    "12": ("Avvertenze doping mancanti",            check_12),
    "13": ("Prezzi etichetta mancanti",             check_13),
    "14": ("Prezzo totale diverso da LORDO_PRESC",  check_14),
    "16": ("Data prep antecedente prescrizione",    check_16),
    "17": ("Codice prescrizione duplicato",         check_17),
    "18": ("CF OCR diverso da CF regione",          check_18),
}

# Descrizioni di TUTTI i codici (deterministici + semantici), condivise da
# run() e da salva_difformita_json() — un unico posto da aggiornare se si
# aggiungono nuovi check in futuro.
DESCRIZIONI_DIFFORMITA = {
    **{k: v[0] for k, v in CHECKS_DETERMINISTICI.items()},
    "09": "Metodo estrattivo mancante (per oli)",
    "10": "Posologia mancante",
    "19": "Nome paziente etichetta diverso da assistito",
    "05A": "Dicitura non responsività assente",
}


def analizza_riga(r: dict) -> list[str]:
    """
    Analizza una singola ricetta (dizionario JSON estratto da ocr_cannabis.py)
    e restituisce la lista dei codici di difformita' rilevati.

    Ordine di valutazione:
    1. Check deterministici semplici
    2. Check 10 — deterministico con possibile fallback semantico
    3. Check 09 — deterministico con possibile fallback semantico
    4. Check 19 — deterministico con possibile fallback semantico
    5. Check 05A — sempre semantico (o fallback keyword)
    """
    difformita = []
    chiamate_ollama = []  # traccia quali check hanno effettivamente chiamato Ollama per QUESTA ricetta

    # Check deterministici semplici
    for codice, (_, fn) in CHECKS_DETERMINISTICI.items():
        try:
            if fn(r):
                difformita.append(codice)
        except Exception as e:
            logger.warning(f"  check {codice}: errore {e}")

    # Check 10 — posologia (deterministico + fallback semantico)
    try:
        esito_10 = check_10_deterministico(r)
        if esito_10 is None:
            if USA_LLM_SEMANTICO and OLLAMA_DISPONIBILE:
                chiamate_ollama.append("10B")
            esito_10 = check_10B_semantico(r)
        if esito_10:
            difformita.append("10")
    except Exception as e:
        logger.warning(f"  check 10: errore {e}")

    # Check 09 — metodo estrattivo (deterministico + fallback semantico)
    try:
        esito_09 = check_09_deterministico(r)
        if esito_09 is None:
            if USA_LLM_SEMANTICO and OLLAMA_DISPONIBILE:
                chiamate_ollama.append("09B")
            esito_09 = check_09B_semantico(r)
        if esito_09:
            difformita.append("09")
    except Exception as e:
        logger.warning(f"  check 09: errore {e}")

    # Check 19 — nome paziente (deterministico + fallback semantico)
    try:
        esito_19 = check_19_deterministico(r)
        if esito_19 is None:
            if USA_LLM_SEMANTICO and OLLAMA_DISPONIBILE:
                chiamate_ollama.append("19B")
            esito_19 = check_19B_semantico(r)
        if esito_19:
            difformita.append("19")
    except Exception as e:
        logger.warning(f"  check 19: errore {e}")

    # Check 05A — dicitura non responsivita' (sempre semantico, se disponibile)
    try:
        if USA_LLM_SEMANTICO and OLLAMA_DISPONIBILE:
            chiamate_ollama.append("05A")
        if check_05A_semantico(r):
            difformita.append("05A")
    except Exception as e:
        logger.warning(f"  check 05A: errore {e}")

    if chiamate_ollama:
        logger.info(f"  Chiamate Ollama per questa ricetta: {', '.join(chiamate_ollama)}")
    else:
        logger.info("  Nessuna chiamata Ollama per questa ricetta (tutti i check risolti in modo deterministico)")

    return difformita


def _descrizione_per_codice(codice: str, dati: dict) -> str:
    """
    Restituisce la descrizione per un codice di difformità, con un caso
    speciale per il codice 18: se il CF Regione non è disponibile del
    tutto (barcode senza corrispondenza, colonna mancante...), la
    descrizione lo dice esplicitamente invece di usare la dicitura
    generica "CF OCR diverso da CF regione", che implicherebbe un vero
    disaccordo tra i due valori piuttosto che un dato mancante a monte.
    """
    if codice == "18":
        cf_reg = str(dati.get("COD_FISCALE", "")).strip()
        if _is_empty(cf_reg):
            return "CF non presente nel file regionale"
    return DESCRIZIONI_DIFFORMITA.get(codice, codice)


def salva_difformita_json(json_path: Path, diffs: list) -> None:
    """
    Scrive nel JSON stesso il risultato dell'analisi difformità:
    - difformita_codici: lista dei codici rilevati (es. ["07", "13", "19"])
    - difformita_descrizioni: le descrizioni leggibili corrispondenti
      (dinamiche per il codice 18, vedi _descrizione_per_codice)
    - ha_difformita: booleano comodo per filtrare velocemente

    Così l'analisi non va rieseguita per consultare i risultati, e questi
    campi saranno la base diretta per la futura scrittura su Excel.
    """
    dati = json.loads(json_path.read_text(encoding="utf-8-sig"))
    dati["difformita_codici"] = diffs
    dati["difformita_descrizioni"] = [_descrizione_per_codice(c, dati) for c in diffs]
    dati["ha_difformita"] = bool(diffs)
    json_path.write_text(
        json.dumps(dati, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


# ─── Esecuzione batch su Excel ───────────────────────────────────────────────────

def run(input_dir: Path, output_dir: Path):
    """Elabora l'ultimo Excel aggiornato ed applica le difformita' a tutte le righe."""
    output_dir.mkdir(parents=True, exist_ok=True)

    excel_files = sorted(output_dir.glob("aggiornato_*.xlsx"))
    if not excel_files:
        logger.error("Nessun Excel aggiornato trovato")
        return

    excel_path = excel_files[-1]
    logger.info(f"[Fase 5] {excel_path.name}")
    df = pd.read_excel(excel_path, dtype=str)

    mask = df.get("nome_cognome_assistito", pd.Series(dtype=str)).apply(
        lambda v: not _is_empty(str(v))
    )
    df_ocr = df[mask]
    logger.info(f"  {len(df_ocr)} righe con OCR")

    if "Difformità" not in df.columns:
        df["Difformità"] = ""

    contatori  = {c: 0 for c in list(CHECKS_DETERMINISTICI.keys()) + ["09", "19", "05A"]}
    # Nota: "14" e' gia' incluso in CHECKS_DETERMINISTICI
    n_difformi = 0

    for idx, row in df_ocr.iterrows():
        diffs = analizza_riga(row.to_dict())
        df.at[idx, "Difformità"] = ", ".join(diffs) if diffs else ""
        if diffs:
            n_difformi += 1
            for d in diffs:
                contatori[d] = contatori.get(d, 0) + 1

    out_path = output_dir / excel_path.name.replace("aggiornato_", "difformita_")
    with pd.ExcelWriter(str(out_path), engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Ricette")
    logger.info(f"[Fase 5] Salvato: {out_path.name}")

    n_ocr = len(df_ocr)
    logger.info(f"\n  Analizzate: {n_ocr}")
    logger.info(f"  Con difformità: {n_difformi} ({round(n_difformi/n_ocr*100,1) if n_ocr else 0}%)")
    logger.info(f"  Senza:          {n_ocr-n_difformi} ({round((n_ocr-n_difformi)/n_ocr*100,1) if n_ocr else 0}%)")
    logger.info(f"\n  Per codice:")

    for codice, cnt in contatori.items():
        if cnt > 0:
            desc = DESCRIZIONI_DIFFORMITA.get(codice, "")
            logger.info(f"    {codice:4s} {desc[:45]:45s}: {cnt:4d} ({round(cnt/n_ocr*100,1) if n_ocr else 0}%)")

    return out_path


def elabora_cartella(cartella: Path):
    """
    Elabora tutti i JSON in una cartella (esclude riepilogo.json) e
    produce un riepilogo finale con conteggio per codice di difformita'.

    Durante l'esecuzione viene creato un file di log dedicato
    ("log_ragionamento_fase5.txt", dentro la stessa cartella) che
    raccoglie TUTTO quello che questa fase logga — incluse le
    motivazioni/ragionamenti restituiti dai check semantici via Ollama
    (05A, 09B, 10B, 19B) — pensato per essere scaricabile/ispezionabile
    a posteriori, separatamente dal log a schermo/GUI.
    """
    percorso_log_ragionamento = cartella / "log_ragionamento_fase5.txt"
    handler_file = logging.FileHandler(percorso_log_ragionamento, mode="w", encoding="utf-8")
    handler_file.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
    handler_file.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler_file)

    try:
        json_files = sorted(cartella.glob("*.json"))
        json_files = [f for f in json_files if f.name not in ("riepilogo.json", "riepilogo_difformita.json")]

        if not json_files:
            logger.warning(f"Nessun JSON trovato in {cartella}")
            return

        logger.info(f"Elaborazione di {len(json_files)} ricette...")

        risultati  = {}
        contatori  = {}
        n_difformi = 0
        n_errori   = 0

        for json_path in json_files:
            try:
                dati = json.loads(json_path.read_text(encoding="utf-8-sig"))
                logger.info(f"--- {json_path.name} ---")
                diffs = analizza_riga(dati)
                risultati[json_path.name] = diffs
                salva_difformita_json(json_path, diffs)
                if diffs:
                    n_difformi += 1
                    for d in diffs:
                        contatori[d] = contatori.get(d, 0) + 1
                logger.info(f"  {json_path.name}: {diffs if diffs else 'NESSUNA'}")
            except Exception as e:
                n_errori += 1
                logger.error(f"  {json_path.name}: ERRORE - {e}")

        n_tot = len(json_files)
        logger.info("=" * 50)
        logger.info(f"Totale ricette:     {n_tot}")
        logger.info(f"Con difformita':    {n_difformi} ({round(n_difformi/n_tot*100,1) if n_tot else 0}%)")
        logger.info(f"Senza difformita':  {n_tot - n_difformi - n_errori} ({round((n_tot-n_difformi-n_errori)/n_tot*100,1) if n_tot else 0}%)")
        if n_errori:
            logger.info(f"Errori:             {n_errori}")
        logger.info("Per codice di difformita':")
        for codice, cnt in sorted(contatori.items()):
            logger.info(f"  {codice:4s}: {cnt:4d} ({round(cnt/n_tot*100,1) if n_tot else 0}%)")

        # Salva anche un riepilogo JSON, utile per analisi successive
        out_path = cartella / "riepilogo_difformita.json"
        out_path.write_text(
            json.dumps({
                "totale": n_tot,
                "con_difformita": n_difformi,
                "senza_difformita": n_tot - n_difformi - n_errori,
                "errori": n_errori,
                "per_codice": contatori,
                "risultati": risultati
            }, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        logger.info(f"Riepilogo salvato in: {out_path}")
        logger.info(f"Log ragionamento IA salvato in: {percorso_log_ragionamento}")

    finally:
        logging.getLogger().removeHandler(handler_file)
        handler_file.close()


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if len(sys.argv) > 1:
        target = Path(sys.argv[1])

        if target.is_dir():
            elabora_cartella(target)
        elif target.is_file():
            dati = json.loads(target.read_text(encoding="utf-8-sig"))
            diffs = analizza_riga(dati)
            salva_difformita_json(target, diffs)
            print(f"\nRicetta: {target.name}")
            print(f"Difformità rilevate: {diffs if diffs else 'NESSUNA'}")
        else:
            print(f"Percorso non trovato: {target}")
    else:
        print("Utilizzo:")
        print("  python phase5_difformita.py output/030140923503386.json   # singola ricetta")
        print("  python phase5_difformita.py output                        # tutte le ricette nella cartella")
