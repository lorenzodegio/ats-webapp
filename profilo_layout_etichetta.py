# -*- coding: utf-8 -*-
"""
profilo_layout_etichetta.py — Modello dati del profilo di layout etichetta.

Rappresenta, farmacia per farmacia, cosa definisce l'operatore senza scrivere
codice (vedi "Analisi di fattibilità - profilo di layout etichetta.docx",
sezione 3.1-3.2):
  - le parole-guida che dicono "qui c'è un'etichetta" (Punto 1 della pipeline);
  - per ogni campo, due modi alternativi di localizzarlo e leggerlo:
      Modo A: una parola-guida che il sistema cerca da solo con l'OCR;
      Modo B: un'area fissa che l'operatore disegna su una ricetta di esempio
              (coordinate relative, non pixel assoluti — dipendono dalla
              risoluzione della scansione, non dalla farmacia).
Un campo può avere l'uno, l'altro, o entrambi (Modo A tentato per primo,
Modo B come ripiego se la parola-guida non viene trovata).
"""
import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


class DirezioneRitaglio(str, Enum):
    DESTRA = "destra"
    SOTTO = "sotto"
    ENTRAMBI = "entrambi"


@dataclass
class CoordinateRelative:
    """Area rettangolare normalizzata (0.0-1.0) rispetto a larghezza/altezza
    pagina — indipendente dalla risoluzione della scansione. È quello che
    nasce quando l'operatore disegna un rettangolo su una ricetta di
    esempio (Modo B)."""
    x1: float
    y1: float
    x2: float
    y2: float

    def to_pixel(self, img_w: int, img_h: int) -> Tuple[int, int, int, int]:
        return (
            int(self.x1 * img_w),
            int(self.y1 * img_h),
            int(self.x2 * img_w),
            int(self.y2 * img_h),
        )


@dataclass
class EstensioneRelativa:
    """Quanto si estende il ritaglio a partire dalla parola-guida (Modo A),
    in frazione di larghezza/altezza pagina. Nessun default: va definita
    esplicitamente, non assunta (un'estensione sbagliata per una farmacia
    taglia il valore vero o include campi vicini)."""
    width_rel: float
    height_rel: float
    offset_x_rel: float = 0.0
    offset_y_rel: float = 0.0


@dataclass
class CampoEtichettaConfig:
    """Un singolo campo da leggere (es. data_etichetta_preparazione).

    Deve avere almeno uno tra (parola_guida, rettangolo_fallback) — un
    campo senza nessuno dei due non è localizzabile. Se ha entrambi, Modo A
    viene tentato per primo e Modo B scatta solo come ripiego.
    """
    nome_campo: str
    parola_guida: Optional[str] = None
    direzione: DirezioneRitaglio = DirezioneRitaglio.DESTRA
    estensione: Optional[EstensioneRelativa] = None
    parole_escluse: List[str] = field(default_factory=list)
    rettangolo_fallback: Optional[CoordinateRelative] = None

    def __post_init__(self):
        if not self.parola_guida and not self.rettangolo_fallback:
            raise ValueError(
                f"Campo '{self.nome_campo}': serve almeno una parola-guida (Modo A) "
                f"o un'area disegnata dall'operatore (Modo B, rettangolo_fallback)."
            )


@dataclass
class ProfiloLayoutEtichetta:
    """Il profilo completo di una farmacia — sostituisce, per le farmacie
    migrate, la voce corrispondente in PROFILI_FARMACIA (date_corrector.py)."""
    nome_farmacia: str
    # None: farmacia non ancora profilata (il profilo stesso non esiste).
    # Lista vuota []: profilata ma senza parole di presenza configurate —
    # distinzione voluta, vedi ottieni_profilo_farmacia() e il punto 1
    # della pipeline in etichetta_presence.py.
    parole_guida_presenza: Optional[List[str]] = None
    # Area generale dell'etichetta, disegnata dall'operatore su una ricetta
    # di esempio (coordinate relative alla pagina intera). Sostituisce,
    # per le farmacie profilate, la posizione individuata automaticamente
    # dal confronto SSIM del Punto 1 — che resta usato SOLO per decidere
    # se l'etichetta è presente (sì/no), non più per determinare DOVE
    # ritagliare: è proprio l'affidabilità di quel confronto sui moduli
    # prestampati diversi il problema che questo progetto risolve. Se
    # None, la localizzazione dei campi (Passo 3) ricade sulla fascia
    # inferiore generica della pagina (45%-100%), come oggi.
    rettangolo_etichetta: Optional[CoordinateRelative] = None
    campi: List[CampoEtichettaConfig] = field(default_factory=list)

    def get_campo(self, nome_campo: str) -> Optional[CampoEtichettaConfig]:
        for c in self.campi:
            if c.nome_campo == nome_campo:
                return c
        return None


# Registro profili in memoria, popolato da registra_profilo() (uso diretto/
# test) e da _carica_profili_da_json() (uso pipeline reale, vedi sotto).
REGISTRO_PROFILI_LAYOUT: Dict[str, ProfiloLayoutEtichetta] = {}

# La webapp esporta i profili creati in Impostazioni -> "Prompt farmacie"
# in dati/profili_layout.json (montato come /dati nel container) — stesso
# meccanismo gia' usato per farmacie.json (vedi farmacie_dizionario.py).
# Se il file non c'e' (nessuna farmacia ancora profilata), il registro
# resta vuoto: comportamento identico a prima di questa funzionalita'.
# ATS_DATI_DIR (impostata da app/real_pipeline.py in modalita' nativa,
# PIPELINE_RUNNER=local, server senza Docker) e' la cartella dati REALE
# quando questo file vive in una repo separata da quella della webapp —
# vedi la stessa logica in farmacie_dizionario.py. Controllata per prima.
_ATS_DATI_DIR = os.environ.get("ATS_DATI_DIR", "").strip()

PERCORSI_JSON_PROFILI = (
    (Path(_ATS_DATI_DIR) / "profili_layout.json",) if _ATS_DATI_DIR else ()
) + (
    Path("/dati/profili_layout.json"),
    Path("dati/profili_layout.json"),
    Path(__file__).resolve().parent / "dati" / "profili_layout.json",
    Path(__file__).resolve().parent / "profili_layout.json",
)

_profili_json_caricati = False


def _rettangolo_da_dict(raw: Optional[dict]) -> Optional[CoordinateRelative]:
    if not raw:
        return None
    try:
        return CoordinateRelative(
            x1=float(raw["x1"]), y1=float(raw["y1"]),
            x2=float(raw["x2"]), y2=float(raw["y2"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _carica_profili_da_json(forza: bool = False) -> None:
    """Carica (una sola volta per processo, come carica_farmacie() in
    farmacie_dizionario.py) i profili esportati dalla webapp nel registro
    in memoria. Un profilo già registrato via registra_profilo() con lo
    stesso nome farmacia NON viene sovrascritto, a meno di forza=True —
    permette ai test diretti di continuare a registrare profili ad-hoc
    senza che il JSON di produzione li rimpiazzi a sorpresa."""
    global _profili_json_caricati
    if _profili_json_caricati and not forza:
        return
    _profili_json_caricati = True

    for percorso in PERCORSI_JSON_PROFILI:
        if not percorso.exists():
            continue
        try:
            payload = json.loads(percorso.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning(f"profili_layout.json trovato ma illeggibile ({percorso}): {e}")
            continue

        elenco = payload.get("profili", []) if isinstance(payload, dict) else payload
        if not isinstance(elenco, list):
            continue

        for raw in elenco:
            if not isinstance(raw, dict):
                continue
            nome_farmacia = str(raw.get("nome_farmacia") or "").strip()
            if not nome_farmacia:
                continue
            chiave = nome_farmacia.upper()
            if chiave in REGISTRO_PROFILI_LAYOUT and not forza:
                continue

            campi = []
            for raw_campo in raw.get("campi", []) or []:
                if not isinstance(raw_campo, dict):
                    continue
                nome_campo = str(raw_campo.get("nome_campo") or "").strip()
                if not nome_campo:
                    continue
                parola_guida = (raw_campo.get("parola_guida") or "").strip() or None
                rettangolo_fallback = _rettangolo_da_dict(raw_campo.get("rettangolo_fallback"))
                if not parola_guida and not rettangolo_fallback:
                    log.warning(
                        f"  Profilo '{nome_farmacia}', campo '{nome_campo}': né parola-guida "
                        f"né rettangolo nel JSON — campo saltato (non localizzabile)."
                    )
                    continue
                campi.append(CampoEtichettaConfig(
                    nome_campo=nome_campo,
                    parola_guida=parola_guida,
                    rettangolo_fallback=rettangolo_fallback,
                ))

            REGISTRO_PROFILI_LAYOUT[chiave] = ProfiloLayoutEtichetta(
                nome_farmacia=nome_farmacia,
                parole_guida_presenza=raw.get("parole_guida_presenza"),
                rettangolo_etichetta=_rettangolo_da_dict(raw.get("rettangolo_etichetta")),
                campi=campi,
            )
        log.info(f"Profili di layout etichetta caricati da {percorso}: {len(elenco)}")
        break


def registra_profilo(profilo: ProfiloLayoutEtichetta) -> None:
    REGISTRO_PROFILI_LAYOUT[profilo.nome_farmacia.strip().upper()] = profilo


def ottieni_profilo_farmacia(nome_farmacia: str) -> Optional[ProfiloLayoutEtichetta]:
    if not nome_farmacia:
        return None
    _carica_profili_da_json()
    return REGISTRO_PROFILI_LAYOUT.get(nome_farmacia.strip().upper())
