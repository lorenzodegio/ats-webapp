"""
Modelli SQLAlchemy: Utente, Job, Prescrizione, Difformita.
Rispecchiano l'estratto del documento di progetto (Sezione 4).
"""
import enum
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, Enum, Boolean, Text
)
from sqlalchemy.orm import relationship

from app.database import Base


class RuoloUtente(str, enum.Enum):
    operatore = "operatore"
    admin = "admin"


class StatoJob(str, enum.Enum):
    in_coda = "in_coda"
    fase1_preprocessing = "fase1_preprocessing"
    fase2_ocr = "fase2_ocr"
    fase3_excel = "fase3_excel"
    fase4_difformita = "fase4_difformita"
    completato = "completato"
    errore = "errore"


class ModalitaJob(str, enum.Enum):
    full = "full"
    solo_preprocessing = "solo_preprocessing"
    da_ocr_in_poi = "da_ocr_in_poi"


class Gravita(str, enum.Enum):
    bassa = "bassa"
    media = "media"
    alta = "alta"


class Utente(Base):
    __tablename__ = "utenti"

    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    nome_completo = Column(String(255), nullable=False)
    ruolo = Column(Enum(RuoloUtente), default=RuoloUtente.operatore, nullable=False)
    creato_il = Column(DateTime, default=datetime.utcnow)

    job = relationship("Job", back_populates="creato_da")

    @property
    def is_admin(self) -> bool:
        return self.ruolo == RuoloUtente.admin


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    nome_file_origine = Column(String(255), nullable=False)
    stato = Column(Enum(StatoJob), default=StatoJob.in_coda, index=True, nullable=False)
    modalita = Column(Enum(ModalitaJob), default=ModalitaJob.full, nullable=False)
    messaggio_errore = Column(String(500), nullable=True)
    creato_il = Column(DateTime, default=datetime.utcnow)
    aggiornato_il = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creato_da_id = Column(Integer, ForeignKey("utenti.id"))
    creato_da = relationship("Utente", back_populates="job")

    prescrizioni = relationship(
        "Prescrizione", back_populates="job", cascade="all, delete-orphan"
    )

    @property
    def numero_prescrizioni(self) -> int:
        return len(self.prescrizioni)

    @property
    def numero_difformita(self) -> int:
        return sum(len(p.difformita) for p in self.prescrizioni)


class Prescrizione(Base):
    __tablename__ = "prescrizioni"

    id = Column(Integer, primary_key=True)
    barcode = Column(String(64), index=True, nullable=False)
    dati_estratti_json = Column(Text, nullable=True)  # JSON serializzato dei 24 campi
    excel_scritto = Column(Boolean, default=False)
    creato_il = Column(DateTime, default=datetime.utcnow)

    job_id = Column(Integer, ForeignKey("jobs.id"))
    job = relationship("Job", back_populates="prescrizioni")

    difformita = relationship(
        "Difformita", back_populates="prescrizione", cascade="all, delete-orphan"
    )


class Difformita(Base):
    __tablename__ = "difformita"

    id = Column(Integer, primary_key=True)
    tipo = Column(String(120), nullable=False)  # es. "TDL", "TAL", "TOL", ...
    descrizione = Column(String(500), nullable=False)
    gravita = Column(Enum(Gravita), default=Gravita.media, nullable=False)
    creato_il = Column(DateTime, default=datetime.utcnow)

    prescrizione_id = Column(Integer, ForeignKey("prescrizioni.id"))
    prescrizione = relationship("Prescrizione", back_populates="difformita")
