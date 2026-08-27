"""
Dizionario farmacie per la pipeline OCR (prompt + matching fuzzy).

La webapp scrive `farmacie.json` in ./dati (montato come /dati nel
container). Se il file non c'e', si usa l'elenco di fallback — le
stesse farmacie storicamente hardcodate nei prompt.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PERCORSI_JSON = (
    Path("/dati/farmacie.json"),
    Path("dati/farmacie.json"),
    Path(__file__).resolve().parent / "dati" / "farmacie.json",
    Path(__file__).resolve().parent / "farmacie.json",
)

# Elenco storico usato finche' Impostazioni non ha ancora un JSON esportato.
FARMACIE_FALLBACK: List[Dict[str, str]] = [
    {"codice": "FAR001", "nome": "Farmacia Tili Snc", "codice_regionale": "TILI"},
    {"codice": "FAR002", "nome": "Farmacia Di Lora Srl", "codice_regionale": "DI LORA"},
    {"codice": "FAR003", "nome": "Farmacia Pomi di dr. Collivasone A. & C. Snc", "codice_regionale": "POMI"},
    {"codice": "FAR004", "nome": "Farmacia Ramella dott.ri G. e A. Sas", "codice_regionale": "RAMELLA"},
    {"codice": "FAR005", "nome": "Farmacia Mazzucchelli F. & C. Snc", "codice_regionale": "MAZZUCCHELLI"},
    {"codice": "FAR006", "nome": "Farmacia Peroni dr Antonio E. & C. Sas", "codice_regionale": "PERONI"},
    {"codice": "FAR007", "nome": "Farmacia Comunale N.2", "codice_regionale": "COMUNALE 2"},
    {"codice": "FAR008", "nome": "Farmacia Stefini & C Sas", "codice_regionale": ""},
    {"codice": "FAR009", "nome": "Farmacia Introini dr. Paolo & C. Sas", "codice_regionale": "INTROINI"},
    {"codice": "FAR010", "nome": "Farmacia Di Crenna", "codice_regionale": ""},
    {"codice": "FAR011", "nome": "Farmacia Ponti", "codice_regionale": "PONTI"},
]

# Refusi OCR osservati sul campo — non sono un campo anagrafico.
ALIAS_NOTI = {
    "FARMACIA POMI SNC DI AVIGNO": "Farmacia Pomi di dr. Collivasone A. & C. Snc",
    "FARMACIA DI AVIGNO": "Farmacia Pomi di dr. Collivasone A. & C. Snc",
    "FARMACIA RAMELLA": "Farmacia Ramella dott.ri G. e A. Sas",
    "FARMACIA INTROINI": "Farmacia Introini dr. Paolo & C. Sas",
    "FARMACIA MAZZUCCHELLI": "Farmacia Mazzucchelli F. & C. Snc",
    "FARMACIA PERONI": "Farmacia Peroni dr Antonio E. & C. Sas",
    "FARMACIA COMUNALE 2": "Farmacia Comunale N.2",
    "FARMACIA COMUNALE N.2": "Farmacia Comunale N.2",
    "M.S. SPA - FARMACIA COMUNALE": "Farmacia Comunale N.2",
    "FARMACIA PILI": "Farmacia Tili Snc",
    "FARMACIA MILI": "Farmacia Tili Snc",
}

_cache: Optional[List[Dict[str, str]]] = None


def _normalizza_riga(raw: dict) -> Dict[str, str]:
    return {
        "codice": str(raw.get("codice") or "").strip(),
        "nome": str(raw.get("nome") or "").strip(),
        "codice_regionale": str(raw.get("codice_regionale") or "").strip(),
    }


def carica_farmacie(forza: bool = False) -> List[Dict[str, str]]:
    global _cache
    if _cache is not None and not forza:
        return _cache

    caricate: List[Dict[str, str]] = []
    for percorso in PERCORSI_JSON:
        if not percorso.exists():
            continue
        try:
            payload = json.loads(percorso.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        elenco = payload.get("farmacie", payload) if isinstance(payload, dict) else payload
        if not isinstance(elenco, list):
            continue
        for raw in elenco:
            if not isinstance(raw, dict):
                continue
            riga = _normalizza_riga(raw)
            if riga["nome"]:
                caricate.append(riga)
        if caricate:
            break

    _cache = caricate or [_normalizza_riga(f) for f in FARMACIE_FALLBACK]
    return _cache


def nomi_canonici() -> List[str]:
    return [f["nome"] for f in carica_farmacie() if f.get("nome")]


def _distanza_levenshtein(a: str, b: str) -> int:
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
                riga_prec[j - 1] + costo,
            )
        riga_prec = riga_corr
    return riga_prec[len_b]


def blocco_prompt_farmacie() -> str:
    """Testo da iniettare nella sezione nome_farmacia del prompt VLLM."""
    righe = []
    for f in carica_farmacie():
        extra = f" ({f['codice_regionale']})" if f.get("codice_regionale") else ""
        righe.append(f"    {f['nome']}{extra}")
    elenco = "\n".join(righe) if righe else "    (nessuna farmacia censita)"
    correzioni = "\n".join(
        f'    "{alias}" -> "{canonico}"' for alias, canonico in ALIAS_NOTI.items()
    )
    return (
        "  Map to closest from this list:\n"
        f"{elenco}\n"
        "  Specific corrections:\n"
        f"{correzioni}\n"
        '  Not mappable -> return name as read. Not present -> "FARMACIA NON RICONOSCIUTA"'
    )


def normalizza_nome_farmacia(letto: str) -> Tuple[str, Optional[str]]:
    """
    Matching deterministico post-OCR.
    Ritorna (nome_canonico, codice) se c'e' un match, altrimenti (letto, None).
    """
    originale = (letto or "").strip()
    if not originale or originale.upper() == "FARMACIA NON RICONOSCIUTA":
        return originale, None

    alias = ALIAS_NOTI.get(originale.upper())
    if alias:
        for f in carica_farmacie():
            if f["nome"] == alias:
                return alias, f.get("codice") or None
        return alias, None

    candidati = carica_farmacie()
    letto_norm = re.sub(r"\s+", " ", originale.lower())

    for f in candidati:
        if letto_norm == f["nome"].lower():
            return f["nome"], f.get("codice") or None
        regionale = (f.get("codice_regionale") or "").lower()
        if regionale and regionale in letto_norm:
            return f["nome"], f.get("codice") or None

    migliore_nome = None
    migliore_codice = None
    migliore_rapporto = None
    for f in candidati:
        d = _distanza_levenshtein(letto_norm, f["nome"].lower())
        rapporto = d / max(len(f["nome"]), 1)
        if migliore_rapporto is None or rapporto < migliore_rapporto:
            migliore_nome = f["nome"]
            migliore_codice = f.get("codice") or None
            migliore_rapporto = rapporto

    if migliore_rapporto is not None and migliore_rapporto <= 0.25:
        return migliore_nome, migliore_codice
    return originale, None
