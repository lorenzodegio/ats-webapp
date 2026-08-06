"""
Crea il primo utente amministratore e popola il DB con dati finti
(job, prescrizioni, difformita) cosi' dashboard/archivio/difformita
non sono vuoti durante lo sviluppo del frontend.

Uso:
    python seed_admin.py
"""
import getpass
import json
import random
from datetime import datetime, timedelta

from app.database import Base, engine, SessionLocal
from app.models import Utente, RuoloUtente, Job, Prescrizione, Difformita, StatoJob, ModalitaJob, Gravita
from app.auth import hash_password
from app.fake_pipeline import _genera_barcode, _genera_dati_estratti, TIPI_DIFFORMITA

Base.metadata.create_all(bind=engine)


def crea_admin(db):
    if db.query(Utente).filter(Utente.username == "admin").first():
        print("Utente 'admin' gia' esistente, salto la creazione.")
        return
    password = getpass.getpass("Password per l'utente admin (default 'admin123' se vuoto): ") or "admin123"
    admin = Utente(
        username="admin",
        password_hash=hash_password(password),
        nome_completo="Amministratore ATS",
        ruolo=RuoloUtente.admin,
    )
    db.add(admin)

    # Un operatore di esempio, utile per testare i permessi non-admin
    operatore = Utente(
        username="operatore",
        password_hash=hash_password("operatore123"),
        nome_completo="Operatore Demo",
        ruolo=RuoloUtente.operatore,
    )
    db.add(operatore)
    db.commit()
    print("Creati utenti: admin / operatore (password 'operatore123')")
    return admin


NOMI_ELABORAZIONI_DEMO = [
    "Lotto ASL Varese - luglio 2026", "Lotto ASL Como - luglio 2026",
    "Lotto ASL Sondrio - agosto 2026", "Consegna settimanale distretto 3",
    "Lotto farmacie centro citta'", "Lotto ASL Varese - agosto 2026",
    "Consegna mensile RSA convenzionate", "Lotto distretto Busto Arsizio",
    "Lotto farmacie ospedaliere", "Consegna settimanale distretto 1",
    "Lotto ASL Lecco - agosto 2026", "Lotto farmacie periferia",
]


def popola_dati_demo(db, creato_da):
    if db.query(Job).count() > 0:
        print("Sono gia' presenti dei job, salto la generazione dei dati demo.")
        return

    for i in range(12):
        creato_il = datetime.utcnow() - timedelta(days=random.randint(0, 20), hours=random.randint(0, 23))
        stato = random.choices(
            [StatoJob.completato, StatoJob.errore, StatoJob.fase2_ocr, StatoJob.in_coda],
            weights=[9, 1, 1, 1],
        )[0]

        ultima_fase_completata = None
        messaggio_errore = None
        if stato == StatoJob.completato:
            ultima_fase_completata = StatoJob.completato
        elif stato == StatoJob.errore:
            ultima_fase_completata = StatoJob.fase2_ocr
            messaggio_errore = "Timeout del modello OCR durante l'estrazione dei campi"
        elif stato == StatoJob.fase2_ocr:
            ultima_fase_completata = StatoJob.fase1_preprocessing

        job = Job(
            nome=NOMI_ELABORAZIONI_DEMO[i % len(NOMI_ELABORAZIONI_DEMO)],
            nome_file_origine=f"lotto_prescrizioni_{i+1:03d}.pdf",
            stato=stato,
            ultima_fase_completata=ultima_fase_completata,
            messaggio_errore=messaggio_errore,
            modalita=ModalitaJob.full,
            creato_da_id=creato_da.id,
            creato_il=creato_il,
            aggiornato_il=creato_il,
        )
        db.add(job)
        db.flush()

        if stato in (StatoJob.completato, StatoJob.errore):
            for _ in range(random.randint(2, 8)):
                presc = Prescrizione(
                    job_id=job.id,
                    barcode=_genera_barcode(),
                    dati_estratti_json=json.dumps(_genera_dati_estratti(), ensure_ascii=False),
                    excel_scritto=(stato == StatoJob.completato),
                    creato_il=creato_il,
                )
                db.add(presc)
                db.flush()
                if stato == StatoJob.completato and random.random() < 0.25:
                    tipo, descrizione = random.choice(TIPI_DIFFORMITA)
                    db.add(Difformita(
                        prescrizione_id=presc.id,
                        tipo=tipo,
                        descrizione=descrizione,
                        gravita=random.choice(list(Gravita)),
                        creato_il=creato_il,
                    ))
    db.commit()
    print("Dati demo generati: 12 elaborazioni (con nome), prescrizioni e difformita' finte.")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        admin = crea_admin(db) or db.query(Utente).filter(Utente.username == "admin").first()
        popola_dati_demo(db, admin)
    finally:
        db.close()
