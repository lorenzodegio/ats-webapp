"""
BACKEND FINTO (mock) — sostituisce temporaneamente l'avvio reale dei
container Docker (Sezione "Flusso caricamento -> elaborazione ->
archiviazione" dello schema). Rispetta lo stesso contratto di entita'
(Elaborazione, LogElaborazione, Prescrizione, DatiOcr, Difformita) che
usera' l'integrazione reale.

I file (PDF/PNG/JSON/Excel) vengono comunque scritti per davvero, ma
in una cartella locale ./sharepoint_finto/ invece che sul vero
SharePoint montato: cosi' il principio "SharePoint e' il filesystem,
il DB salva solo percorsi relativi" resta vero anche in sviluppo, senza
bisogno di avere OneDrive sincronizzato sulla macchina.

Quando il backend reale sara' pronto, queste tre funzioni vanno
sostituite dalle chiamate reali ai container Docker (che scriveranno
sul percorso SharePoint vero, letto da Configurazione.sharepoint_base_path).
"""
import json
import os
import random
import time
from datetime import datetime

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import (
    LottoMensile, StatoLotto, Elaborazione, FaseElaborazione, StatoElaborazione,
    LogElaborazione, LivelloLog, Prescrizione, StatoBarcode, DatiOcr,
    Difformita, StatoDifformita,
)
from app.generatore_finto import genera_barcode, genera_dati_ocr_finti, CODICI_DIFFORMITA

FAKE_SP_ROOT = "sharepoint_finto"

# Cartella per i file Excel di output generati al completamento lotto
PERCORSO_CARTELLA_OUTPUT_RECENTI = "ARCHIVIO/ELABORAZIONI RECENTI"

# Probabilita' (0-1) di simulare un'eccezione durante una fase, solo a
# scopo dimostrativo: nel backend reale l'errore sara' quello vero
# sollevato dal container Docker corrispondente.
PROBABILITA_ECCEZIONE_FINTA = 0.08


def _percorso_assoluto(percorso_relativo: str) -> str:
    path = os.path.join(FAKE_SP_ROOT, percorso_relativo)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _scrivi_file_finto(percorso_relativo: str, contenuto: str = "") -> None:
    path = _percorso_assoluto(percorso_relativo)
    with open(path, "w", encoding="utf-8") as f:
        f.write(contenuto)


def _log(db: Session, elaborazione: Elaborazione, messaggio: str, livello: LivelloLog = LivelloLog.info) -> None:
    db.add(LogElaborazione(elaborazione_id=elaborazione.id, messaggio=messaggio, livello=livello))
    db.commit()


class ElaborazioneAnnullata(Exception):
    """Sollevata quando l'operatore annulla un'elaborazione finta in corso."""


def _controlla_pausa_e_annullamento(db: Session, elaborazione: Elaborazione) -> None:
    """
    Da chiamare ad ogni iterazione dei cicli delle 3 fasi finte: legge
    (con una query leggera, non fidandosi della copia in memoria) la
    richiesta di controllo piu' recente. Se e' "pausa", resta in attesa
    qui dentro finche' non viene rimossa o sostituita da "annulla". Se
    e' "annulla", solleva ElaborazioneAnnullata per interrompere il ciclo.
    """
    while True:
        valore = db.query(Elaborazione.richiesta_controllo).filter(Elaborazione.id == elaborazione.id).scalar()
        if valore == "annulla":
            raise ElaborazioneAnnullata()
        if valore != "pausa":
            return
        time.sleep(0.5)


def _gestisci_annullamento(db: Session, lotto_id, elaborazione: Elaborazione) -> None:
    """Marca elaborazione e lotto come annullati dall'operatore (non un errore vero)."""
    messaggio = "Elaborazione annullata dall'operatore"
    _log(db, elaborazione, messaggio, LivelloLog.warning)
    elaborazione.stato = StatoElaborazione.annullata
    elaborazione.richiesta_controllo = None
    elaborazione.finished_at = datetime.utcnow()
    lotto = db.query(LottoMensile).filter(LottoMensile.id == lotto_id).first()
    if lotto:
        lotto.stato = StatoLotto.eccezione
        lotto.note = ((lotto.note or "") + f"\n[{datetime.utcnow():%d/%m %H:%M}] {messaggio}").strip()
        lotto.updated_at = datetime.utcnow()
    db.commit()


def _forse_eccezione(db: Session, lotto: LottoMensile, elaborazione: Elaborazione, fase_label: str) -> bool:
    """Ritorna True se ha simulato un'eccezione (e ha gia' salvato tutto)."""
    if random.random() >= PROBABILITA_ECCEZIONE_FINTA:
        return False
    messaggio = f"Eccezione simulata durante '{fase_label}': container terminato con errore"
    _log(db, elaborazione, messaggio, LivelloLog.error)
    elaborazione.stato = StatoElaborazione.errore
    elaborazione.exit_code = 1
    elaborazione.finished_at = datetime.utcnow()
    lotto.stato = StatoLotto.eccezione
    lotto.note = ((lotto.note or "") + f"\n[{datetime.utcnow():%d/%m %H:%M}] {messaggio}").strip()
    lotto.updated_at = datetime.utcnow()
    db.commit()
    return True


def avvia_preprocessing_fake(lotto_id) -> None:
    """Fase 1: split PDF, lettura barcode, deskew. Crea le Prescrizioni."""
    db: Session = SessionLocal()
    try:
        lotto = db.query(LottoMensile).filter(LottoMensile.id == lotto_id).first()
        if lotto is None:
            return

        elaborazione = Elaborazione(
            lotto_id=lotto.id, fase=FaseElaborazione.preprocessing,
            stato=StatoElaborazione.in_corso,
            comando_docker=f"docker compose run preprocessing --lotto {lotto.nome}",
            started_at=datetime.utcnow(),
        )
        db.add(elaborazione)
        db.commit()

        lotto.stato = StatoLotto.preprocessing
        db.commit()

        _log(db, elaborazione, f"Avvio preprocessing per il lotto '{lotto.nome}'")
        time.sleep(1)
        _log(db, elaborazione, "Split del PDF combinato in singole prescrizioni")
        time.sleep(1.5)

        if _forse_eccezione(db, lotto, elaborazione, "preprocessing"):
            return

        numero_prescrizioni = random.randint(6, 24)
        n_letti = 0
        for i in range(numero_prescrizioni):
            _controlla_pausa_e_annullamento(db, elaborazione)
            barcode_letto = random.random() > 0.12  # ~88% barcode leggibili
            barcode = genera_barcode() if barcode_letto else None
            presc = Prescrizione(
                lotto_id=lotto.id,
                barcode=barcode,
                stato_barcode=StatoBarcode.letto if barcode_letto else StatoBarcode.undefined,
                sp_pdf_path=f"{lotto.sp_output_path}/CARTELLE FARMACIE/{barcode or f'undefined_{i}'}.pdf",
                sp_png_path=f"{lotto.sp_output_path}/CARTELLE FARMACIE/{barcode or f'undefined_{i}'}.png",
            )
            db.add(presc)
            _scrivi_file_finto(presc.sp_pdf_path, f"PDF finto prescrizione {barcode or 'undefined'}")
            if barcode_letto:
                n_letti += 1
            elaborazione.n_processati = i + 1
            _log(db, elaborazione, f"[{i + 1}/{numero_prescrizioni}] {barcode or 'undefined'}.pdf")
            time.sleep(0.1)
        db.commit()

        lotto.n_pdf_caricati = 1
        lotto.n_prescrizioni_totali = numero_prescrizioni
        lotto.n_barcode_letti = n_letti
        lotto.n_barcode_undefined = numero_prescrizioni - n_letti
        lotto.updated_at = datetime.utcnow()

        elaborazione.stato = StatoElaborazione.completata
        elaborazione.finished_at = datetime.utcnow()
        elaborazione.exit_code = 0
        elaborazione.n_processati = numero_prescrizioni
        elaborazione.n_errori = 0

        lotto.stato = StatoLotto.revisione_barcode
        _log(db, elaborazione, f"Preprocessing completato: {numero_prescrizioni} prescrizioni, {n_letti} barcode letti, {numero_prescrizioni - n_letti} da rivedere")
        db.commit()

    except ElaborazioneAnnullata:
        _gestisci_annullamento(db, lotto_id, elaborazione)
    except Exception as exc:  # pragma: no cover - solo per il mock
        lotto = db.query(LottoMensile).filter(LottoMensile.id == lotto_id).first()
        if lotto:
            lotto.stato = StatoLotto.eccezione
            lotto.note = ((lotto.note or "") + f"\n{exc}").strip()
            db.commit()
    finally:
        db.close()


def avvia_ocr_fake(lotto_id) -> None:
    """Fase 2: estrazione VLLM dei 24 campi per ogni prescrizione."""
    db: Session = SessionLocal()
    try:
        lotto = db.query(LottoMensile).filter(LottoMensile.id == lotto_id).first()
        if lotto is None:
            return

        elaborazione = Elaborazione(
            lotto_id=lotto.id, fase=FaseElaborazione.vllm,
            stato=StatoElaborazione.in_corso,
            comando_docker=f"docker compose run ocr-vllm --lotto {lotto.nome}",
            started_at=datetime.utcnow(),
        )
        db.add(elaborazione)
        lotto.stato = StatoLotto.elaborazione_ocr
        db.commit()

        _log(db, elaborazione, "Avvio estrazione OCR (Qwen2.5-VL) sulle prescrizioni")
        time.sleep(1)

        prescrizioni = db.query(Prescrizione).filter(
            Prescrizione.lotto_id == lotto.id,
            Prescrizione.stato_barcode != StatoBarcode.escluso,
        ).all()
        totale = len(prescrizioni)
        n_match = 0
        somma_score = 0.0
        for indice, presc in enumerate(prescrizioni, start=1):
            _controlla_pausa_e_annullamento(db, elaborazione)
            time.sleep(0.15)
            if _forse_eccezione(db, lotto, elaborazione, "elaborazione OCR"):
                return

            dati = genera_dati_ocr_finti()
            score = round(random.uniform(72, 99), 2)
            somma_score += score

            dati_ocr = DatiOcr(
                prescrizione_id=presc.id,
                json_vllm_raw={k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in dati.items()},
                extracted_at=datetime.utcnow(),
                **dati,
            )
            db.add(dati_ocr)

            presc.score_ocr = score
            presc.n_campi_compilati = random.randint(20, 24)
            presc.barcode_in_excel = random.random() > 0.1
            if presc.barcode_in_excel:
                presc.riga_excel = random.randint(2, 300)
                n_match += 1
            presc.sp_json_path = presc.sp_pdf_path.replace(".pdf", ".json") if presc.sp_pdf_path else None
            if presc.sp_json_path:
                _scrivi_file_finto(presc.sp_json_path, json.dumps(dati_ocr.json_vllm_raw, ensure_ascii=False, indent=2))

            elaborazione.n_processati = indice
            _log(db, elaborazione, f"[{indice}/{totale}] {presc.barcode}.pdf")

        db.commit()

        lotto.n_match_excel = n_match
        lotto.score_ocr_medio = round(somma_score / len(prescrizioni), 2) if prescrizioni else None
        lotto.updated_at = datetime.utcnow()

        elaborazione.stato = StatoElaborazione.completata
        elaborazione.finished_at = datetime.utcnow()
        elaborazione.exit_code = 0
        elaborazione.n_processati = len(prescrizioni)

        lotto.stato = StatoLotto.revisione_qualita
        _log(db, elaborazione, f"OCR completato su {len(prescrizioni)} prescrizioni, score medio {lotto.score_ocr_medio}")
        db.commit()

    except ElaborazioneAnnullata:
        _gestisci_annullamento(db, lotto_id, elaborazione)
    except Exception as exc:  # pragma: no cover
        lotto = db.query(LottoMensile).filter(LottoMensile.id == lotto_id).first()
        if lotto:
            lotto.stato = StatoLotto.eccezione
            lotto.note = ((lotto.note or "") + f"\n{exc}").strip()
            db.commit()
    finally:
        db.close()


def avvia_difformita_fake(lotto_id) -> None:
    """Fase 4: analisi di 19 tipologie di non conformita (qui simulate)."""
    db: Session = SessionLocal()
    try:
        lotto = db.query(LottoMensile).filter(LottoMensile.id == lotto_id).first()
        if lotto is None:
            return

        elaborazione = Elaborazione(
            lotto_id=lotto.id, fase=FaseElaborazione.difformita,
            stato=StatoElaborazione.in_corso,
            comando_docker=f"docker compose run difformita --lotto {lotto.nome}",
            started_at=datetime.utcnow(),
        )
        db.add(elaborazione)
        lotto.stato = StatoLotto.analisi_difformita
        db.commit()

        _log(db, elaborazione, "Avvio analisi delle non conformita regolamentari")
        time.sleep(1.5)

        if _forse_eccezione(db, lotto, elaborazione, "analisi difformita"):
            return

        prescrizioni = db.query(Prescrizione).filter(Prescrizione.lotto_id == lotto.id).all()
        totale = len(prescrizioni)
        n_con_difformita = 0
        n_difformita_totali = 0
        for indice, presc in enumerate(prescrizioni, start=1):
            _controlla_pausa_e_annullamento(db, elaborazione)
            time.sleep(0.1)
            if random.random() < 0.25:
                n_di_questa = random.randint(1, 2)
                for _ in range(n_di_questa):
                    codice, descrizione = random.choice(CODICI_DIFFORMITA)
                    db.add(Difformita(
                        prescrizione_id=presc.id, codice=codice, descrizione=descrizione,
                        stato=StatoDifformita.rilevata,
                    ))
                n_con_difformita += 1
                n_difformita_totali += n_di_questa
            elaborazione.n_processati = indice
            _log(db, elaborazione, f"[{indice}/{totale}] {presc.barcode}.pdf controllato")
        db.commit()

        lotto.n_difformita_totali = n_difformita_totali
        lotto.n_prescrizioni_con_difformita = n_con_difformita
        lotto.updated_at = datetime.utcnow()

        elaborazione.stato = StatoElaborazione.completata
        elaborazione.finished_at = datetime.utcnow()
        elaborazione.exit_code = 0
        elaborazione.n_processati = len(prescrizioni)

        lotto.stato = StatoLotto.revisione_difformita
        _log(db, elaborazione, f"Analisi completata: {n_difformita_totali} difformita su {n_con_difformita} prescrizioni")
        db.commit()

    except ElaborazioneAnnullata:
        _gestisci_annullamento(db, lotto_id, elaborazione)
    except Exception as exc:  # pragma: no cover
        lotto = db.query(LottoMensile).filter(LottoMensile.id == lotto_id).first()
        if lotto:
            lotto.stato = StatoLotto.eccezione
            lotto.note = ((lotto.note or "") + f"\n{exc}").strip()
            db.commit()
    finally:
        db.close()
