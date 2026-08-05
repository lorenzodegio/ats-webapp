"""
models.py
----------
Modelli dati SQLAlchemy — Utente, Job, Prescrizione, Difformita, come
da documento di progetto (Sezione 4).

Nota sui nomi: 'creato_da'/'job' come nomi di relazione seguono
esattamente l'estratto di codice già presente nel documento (Sezione
4.1), per restare coerenti con quanto già concordato dal team, anche
se 'job' al plurale ('jobs') sarebbe più naturale per una relazione
uno-a-molti.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, Enum, ForeignKey
)
from sqlalchemy.orm import relationship

from database import Base


class StatoJob(enum.Enum):
    """
    Segue le fasi della pipeline REALMENTE eseguite in questo ordine
    (nota: differisce dall'ordine descritto nel documento, Sezione 1.2
    — lì Excel/03 precede Difformità/04; nella pipeline vera, testata,
    l'Excel finale include le colonne di difformità, quindi la
    difformità DEVE essere calcolata prima di scrivere l'Excel, non
    dopo. Questo enum riflette l'ordine reale, il documento non è
    stato modificato):
    in_coda -> fase1_preprocessing -> fase2_ocr -> fase3_difformita ->
    fase4_excel -> completato, con un ramo errore.
    """
    in_coda = "in_coda"
    fase1_preprocessing = "fase1_preprocessing"
    fase2_ocr = "fase2_ocr"
    fase3_difformita = "fase3_difformita"
    fase4_excel = "fase4_excel"
    completato = "completato"
    errore = "errore"


class Utente(Base):
    __tablename__ = "utenti"

    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    nome_completo = Column(String(255))
    ruolo = Column(String(20), default="operatore")  # "operatore" | "admin"
    creato_il = Column(DateTime, default=datetime.utcnow)

    job = relationship("Job", back_populates="creato_da")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    nome_file_origine = Column(String(255))
    stato = Column(Enum(StatoJob), default=StatoJob.in_coda, index=True)
    modalita = Column(String(30), default="full")
    creato_il = Column(DateTime, default=datetime.utcnow)
    creato_da_id = Column(Integer, ForeignKey("utenti.id"))

    creato_da = relationship("Utente", back_populates="job")
    prescrizioni = relationship("Prescrizione", back_populates="job")


class Prescrizione(Base):
    __tablename__ = "prescrizioni"

    id = Column(Integer, primary_key=True)
    barcode = Column(String(50), index=True)
    dati_estratti_json = Column(Text)  # JSON serializzato dell'output OCR (fase 2)
    excel_scritto = Column(Boolean, default=False)
    job_id = Column(Integer, ForeignKey("jobs.id"))

    job = relationship("Job", back_populates="prescrizioni")
    difformita = relationship("Difformita", back_populates="prescrizione")


class Difformita(Base):
    __tablename__ = "difformita"

    id = Column(Integer, primary_key=True)
    tipo = Column(String(10))       # es. codice difformità (TDL, TAL, ecc. — vedi mockup dashboard)
    descrizione = Column(Text)
    gravita = Column(String(20))    # "bassa" | "media" | "alta"
    prescrizione_id = Column(Integer, ForeignKey("prescrizioni.id"))

    prescrizione = relationship("Prescrizione", back_populates="difformita")
