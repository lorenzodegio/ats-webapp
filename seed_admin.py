"""
Crea utenti, configurazione iniziale e censimento farmacie.

Di default: solo admin, niente lotti di prova (primo uso reale).
Utenti demo: SEED_DEMO_UTENTI=1
Lotti finti: SEED_DEMO_LOTTI=1
"""
import getpass
import os
from datetime import datetime, timedelta

from app.database import Base, engine, SessionLocal
from app.models import (
    Utente, RuoloUtente, Configurazione, LottoMensile, StatoLotto,
    Prescrizione, StatoBarcode, DatiOcr, Difformita, StatoDifformita, Farmacia,
)
from app.auth import hash_password
from app.generatore_finto import genera_barcode, genera_dati_ocr_finti, CODICI_DIFFORMITA

Base.metadata.create_all(bind=engine)

CONFIGURAZIONE_INIZIALE = [
    ("sharepoint_base_path", r"C:\OneDrive - ATS Insubria\Sperimentazione IA\Documenti\CANNABIS",
     "Percorso base della struttura SharePoint montata localmente"),
    ("docker_project_path", "~/ocr-cannabis", "Percorso del progetto Docker della pipeline OCR"),
    ("ollama_host", "http://localhost:11434", "Host del servizio Ollama per il VLLM"),
    ("ocr_score_soglia", "80", "Soglia minima di score OCR considerata accettabile"),
    ("pipeline_backend", "reale",
     "Pipeline OCR Docker reale (Ollama/Qwen). Il backend finto non e' piu' usato."),
]

MESI_IT = ["", "GENNAIO", "FEBBRAIO", "MARZO", "APRILE", "MAGGIO", "GIUGNO",
           "LUGLIO", "AGOSTO", "SETTEMBRE", "OTTOBRE", "NOVEMBRE", "DICEMBRE"]


def crea_utenti(db):
    if db.query(Utente).filter(Utente.username == "admin").first():
        print("Utenti gia' esistenti, salto la creazione.")
        return db.query(Utente).filter(Utente.username == "admin").first()

    password = getpass.getpass("Password per l'utente admin (default 'admin123' se vuoto): ") or "admin123"
    admin = Utente(
        username="admin", password_hash=hash_password(password),
        nome="Amministratore ATS", email="admin@ats-insubria.example",
        ruolo=RuoloUtente.amministratore,
    )
    db.add(admin)
    if os.environ.get("SEED_DEMO_UTENTI") == "1":
        operatore = Utente(
            username="operatore", password_hash=hash_password("operatore123"),
            nome="Operatore Demo", email="operatore@ats-insubria.example",
            ruolo=RuoloUtente.operatore,
        )
        revisore = Utente(
            username="revisore", password_hash=hash_password("revisore123"),
            nome="Revisore Demo", email="revisore@ats-insubria.example",
            ruolo=RuoloUtente.revisore,
        )
        db.add_all([operatore, revisore])
        print("Creati utenti: admin, operatore/operatore123, revisore/revisore123")
    else:
        print("Creato solo utente admin (niente operatore/revisore demo).")
    db.commit()
    return admin


def crea_configurazione(db, admin):
    riga_backend = db.query(Configurazione).filter(Configurazione.chiave == "pipeline_backend").first()
    if riga_backend is not None and riga_backend.valore != "reale":
        riga_backend.valore = "reale"
        riga_backend.descrizione = "Pipeline OCR Docker reale (Ollama/Qwen)"
        db.commit()
        print("pipeline_backend aggiornato a 'reale'.")
    if db.query(Configurazione).count() > 0:
        print("Configurazione gia' presente, salto.")
        return
    for chiave, valore, descrizione in CONFIGURAZIONE_INIZIALE:
        db.add(Configurazione(chiave=chiave, valore=valore, descrizione=descrizione, updated_by_id=admin.id))
    db.commit()
    print("Configurazione iniziale creata.")


FARMACIE_INIZIALI = [
    ("FAR001", "Farmacia Tili Snc", "TILI", None, None, None),
    ("FAR002", "Farmacia Di Lora Srl", "DI LORA", None, None, None),
    ("FAR003", "Farmacia Pomi di dr. Collivasone A. & C. Snc", "POMI", None, None, None),
    ("FAR004", "Farmacia Ramella dott.ri G. e A. Sas", "RAMELLA", None, None, None),
    ("FAR005", "Farmacia Mazzucchelli F. & C. Snc", "MAZZUCCHELLI", None, None, None),
    ("FAR006", "Farmacia Peroni dr Antonio E. & C. Sas", "PERONI", None, None, None),
    ("FAR007", "Farmacia Comunale N.2", "COMUNALE 2", "Via Verdi 40", "Cassano Magnago", "VA"),
    ("FAR008", "Farmacia Stefini & C Sas", None, None, None, None),
    ("FAR009", "Farmacia Introini dr. Paolo & C. Sas", "INTROINI", None, None, None),
    ("FAR010", "Farmacia Di Crenna", None, None, None, None),
    ("FAR011", "Farmacia Ponti", "PONTI", None, None, None),
]


def crea_farmacie(db):
    if db.query(Farmacia).count() > 0:
        print("Farmacie gia' presenti, salto.")
        return
    for codice, nome, codice_regionale, indirizzo, comune, provincia in FARMACIE_INIZIALI:
        db.add(Farmacia(
            codice=codice, nome=nome, codice_regionale=codice_regionale,
            indirizzo=indirizzo, comune=comune, provincia=provincia, attiva=True,
        ))
    db.commit()
    print(f"Create {len(FARMACIE_INIZIALI)} farmacie nel censimento.")


def _crea_prescrizioni(db, lotto, n, con_undefined=False, con_ocr=False, con_difformita=False, creato_il=None):
    creato_il = creato_il or datetime.utcnow()
    prescrizioni = []
    for i in range(n):
        barcode_letto = not (con_undefined and i < max(1, n // 6))
        barcode = genera_barcode() if barcode_letto else None
        presc = Prescrizione(
            lotto_id=lotto.id,
            barcode=barcode,
            stato_barcode=StatoBarcode.letto if barcode_letto else StatoBarcode.undefined,
            sp_pdf_path=f"{lotto.sp_output_path}/CARTELLE FARMACIE/{barcode or f'undefined_{i}'}.pdf",
            sp_png_path=f"{lotto.sp_output_path}/CARTELLE FARMACIE/{barcode or f'undefined_{i}'}.png",
            created_at=creato_il,
        )
        db.add(presc)
        db.flush()
        prescrizioni.append(presc)

        if con_ocr and barcode_letto:
            dati = genera_dati_ocr_finti()
            db.add(DatiOcr(
                prescrizione_id=presc.id,
                json_vllm_raw={k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in dati.items()},
                extracted_at=creato_il, **dati,
            ))
            presc.score_ocr = round(__import__("random").uniform(72, 99), 2)
            presc.n_campi_compilati = __import__("random").randint(20, 24)
            presc.barcode_in_excel = True
            presc.riga_excel = i + 2

            if con_difformita and __import__("random").random() < 0.25:
                codice, descrizione = __import__("random").choice(CODICI_DIFFORMITA)
                stato_d = __import__("random").choice(
                    [StatoDifformita.rilevata, StatoDifformita.confermata, StatoDifformita.esclusa]
                )
                db.add(Difformita(prescrizione_id=presc.id, codice=codice, descrizione=descrizione,
                                   stato=stato_d, rilevata_at=creato_il))
    return prescrizioni


def popola_lotti_demo(db, operatore):
    if db.query(LottoMensile).count() > 0:
        print("Sono gia' presenti dei lotti, salto la generazione dei dati demo.")
        return

    def path_lotto(mese, anno):
        cartella = f"{MESI_IT[mese]}_{anno}"
        return (
            f"LAVORO/MESE DI LAVORAZIONE/{cartella}",
            f"LAVORO/MESE DI LAVORAZIONE/{cartella}/PRESCRIZIONI",
            "LAVORO/OUTPUT",
            f"ARCHIVIO/ELABORAZIONI RECENTI/{cartella}",
        )

    scenari = [
        # (mese, anno, stato, giorni_fa)
        (3, 2026, StatoLotto.archiviato, 130),
        (4, 2026, StatoLotto.archiviato, 100),
        (5, 2026, StatoLotto.completato, 60),
        (6, 2026, StatoLotto.revisione_difformita, 15),
        (7, 2026, StatoLotto.revisione_qualita, 8),
        (8, 2026, StatoLotto.revisione_barcode, 2),
        (9, 2026, StatoLotto.eccezione, 1),
    ]

    for mese, anno, stato, giorni_fa in scenari:
        creato_il = datetime.utcnow() - timedelta(days=giorni_fa)
        sp_lavoro, sp_prescrizioni, sp_output, sp_archivio = path_lotto(mese, anno)

        lotto = LottoMensile(
            mese=mese, anno=anno, nome=f"{MESI_IT[mese].capitalize()} {anno}",
            stato=stato, operatore_id=operatore.id,
            sp_lavoro_path=sp_lavoro, sp_prescrizioni_path=sp_prescrizioni,
            sp_output_path=sp_output, sp_archivio_path=sp_archivio,
            excel_input_filename=f"{MESI_IT[mese]}_{anno}.xlsx",
            excel_output_filename=f"aggiornato_{MESI_IT[mese]}_{anno}.xlsx",
            created_at=creato_il, updated_at=creato_il,
        )
        if stato in (StatoLotto.completato, StatoLotto.archiviato):
            lotto.completato_at = creato_il + timedelta(hours=6)
        if stato == StatoLotto.archiviato:
            lotto.archiviato_at = creato_il + timedelta(hours=12)
            lotto.sp_archivio_path = lotto.sp_archivio_path.replace(
                "ARCHIVIO/ELABORAZIONI RECENTI", "ARCHIVIO/ELABORAZIONI PASSATE"
            )
        if stato == StatoLotto.eccezione:
            lotto.note = f"[{creato_il:%d/%m %H:%M}] Eccezione simulata durante 'elaborazione OCR': container terminato con errore"

        db.add(lotto)
        db.flush()

        n_prescrizioni = __import__("random").randint(8, 22)

        if stato == StatoLotto.revisione_barcode:
            _crea_prescrizioni(db, lotto, n_prescrizioni, con_undefined=True, creato_il=creato_il)
        elif stato == StatoLotto.eccezione:
            _crea_prescrizioni(db, lotto, max(2, n_prescrizioni // 3), creato_il=creato_il)
        elif stato == StatoLotto.revisione_qualita:
            _crea_prescrizioni(db, lotto, n_prescrizioni, con_ocr=True, creato_il=creato_il)
        else:  # revisione_difformita, completato, archiviato
            _crea_prescrizioni(db, lotto, n_prescrizioni, con_ocr=True, con_difformita=True, creato_il=creato_il)

        db.flush()
        lotto.n_pdf_caricati = 1
        lotto.n_prescrizioni_totali = len(lotto.prescrizioni)
        lotto.n_barcode_undefined = sum(1 for p in lotto.prescrizioni if p.stato_barcode == StatoBarcode.undefined)
        lotto.n_barcode_letti = lotto.n_prescrizioni_totali - lotto.n_barcode_undefined
        scores = [float(p.score_ocr) for p in lotto.prescrizioni if p.score_ocr]
        lotto.score_ocr_medio = round(sum(scores) / len(scores), 2) if scores else None
        lotto.n_match_excel = sum(1 for p in lotto.prescrizioni if p.barcode_in_excel)
        difformita_tot = [d for p in lotto.prescrizioni for d in p.difformita]
        lotto.n_difformita_totali = len(difformita_tot)
        lotto.n_prescrizioni_con_difformita = len({d.prescrizione_id for d in difformita_tot})

    db.commit()
    print(f"Creati {len(scenari)} lotti demo in vari stati del ciclo di vita.")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        admin = crea_utenti(db)
        crea_configurazione(db, admin)
        crea_farmacie(db)
        if os.environ.get("SEED_DEMO_LOTTI") == "1":
            popola_lotti_demo(db, admin)
        else:
            print("Nessun lotto demo: l'app parte vuota. Per i dati finti usa SEED_DEMO_LOTTI=1.")
    finally:
        db.close()
