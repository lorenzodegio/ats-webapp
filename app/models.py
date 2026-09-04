"""
Modelli SQLAlchemy — schema con SharePoint come filesystem.

Principio: SharePoint e' il filesystem, il DB e' il cervello.
I file (PDF, PNG, Excel, JSON) vivono su SharePoint; il DB salva
percorsi relativi, stati e metadati (mai dati binari).

Nota sull'autenticazione: lo schema di riferimento non specifica i campi
di login; ho mantenuto `username` e `password_hash` accanto ai campi
richiesti (nome, email, ruolo, attivo, last_login) perche' servono
all'app per il login a sessione gia' in uso. Se l'autenticazione finira'
per passare da AD/SSO aziendale, questi due campi si tolgono facilmente.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Integer, BigInteger, Boolean, DateTime, Date,
    Numeric, Text, JSON, Enum, ForeignKey
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.db_types import GUID


def genera_uuid():
    return uuid.uuid4()


# ============================================================
# Configurazione — percorso base SharePoint e altri parametri
# ============================================================

class Configurazione(Base):
    __tablename__ = "configurazione"

    id = Column(GUID, primary_key=True, default=genera_uuid)
    chiave = Column(String(120), unique=True, nullable=False)
    valore = Column(Text, nullable=False)
    descrizione = Column(String(500), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_id = Column(GUID, ForeignKey("utenti.id"), nullable=True)
    updated_by = relationship("Utente")


# Chiavi precaricate attese dal seed:
#   sharepoint_base_path, docker_project_path, ollama_host, ocr_score_soglia


# ============================================================
# Farmacie — anagrafica delle farmacie convenzionate
# ============================================================

class Farmacia(Base):
    """
    Censimento usato sia dagli operatori (ZIP per farmacia, comunicazioni)
    sia dalla pipeline OCR (dizionario nel prompt + matching fuzzy).
    Il codice_regionale e' il FARMACIA_ID dell'Excel Regione
    (es. "CO0310 - TILI & C."), non un alias interno.
    """
    __tablename__ = "farmacie"

    id = Column(GUID, primary_key=True, default=genera_uuid)
    codice = Column(String(20), unique=True, nullable=False, index=True)
    nome = Column(String(255), nullable=False)
    codice_regionale = Column(String(120), nullable=True, index=True)
    indirizzo = Column(String(500), nullable=True)
    comune = Column(String(120), nullable=True)
    provincia = Column(String(5), nullable=True)
    telefono = Column(String(40), nullable=True)
    email = Column(String(255), nullable=True)
    note = Column(Text, nullable=True)
    attiva = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self) -> str:
        return f"<Farmacia {self.codice} — {self.nome}>"


# ============================================================
# Utenti
# ============================================================

class RuoloUtente(str, enum.Enum):
    amministratore = "amministratore"
    operatore = "operatore"
    revisore = "revisore"


class Utente(Base):
    __tablename__ = "utenti"

    id = Column(GUID, primary_key=True, default=genera_uuid)
    nome = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    ruolo = Column(Enum(RuoloUtente), default=RuoloUtente.operatore, nullable=False)
    attivo = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

    # Campi aggiuntivi per il login a sessione (non nello schema di riferimento)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)

    lotti = relationship("LottoMensile", back_populates="operatore")

    @property
    def is_admin(self) -> bool:
        return self.ruolo == RuoloUtente.amministratore

    @property
    def nome_completo(self) -> str:
        """Alias di comodo per i template esistenti."""
        return self.nome


# ============================================================
# Lotti mensili — cuore del sistema
# ============================================================

class StatoLotto(str, enum.Enum):
    bozza = "bozza"
    caricamento = "caricamento"
    preprocessing = "preprocessing"
    revisione_barcode = "revisione_barcode"
    elaborazione_ocr = "elaborazione_ocr"
    revisione_qualita = "revisione_qualita"
    analisi_difformita = "analisi_difformita"
    revisione_difformita = "revisione_difformita"
    completato = "completato"
    archiviato = "archiviato"
    eccezione = "eccezione"


class LottoMensile(Base):
    __tablename__ = "lotti_mensili"

    id = Column(GUID, primary_key=True, default=genera_uuid)
    mese = Column(Integer, nullable=False)  # 1-12
    anno = Column(Integer, nullable=False)
    nome = Column(String(255), nullable=False)  # es. "LUGLIO 2025"
    stato = Column(Enum(StatoLotto), default=StatoLotto.bozza, index=True, nullable=False)

    operatore_id = Column(GUID, ForeignKey("utenti.id"), nullable=True)
    operatore = relationship("Utente", back_populates="lotti")

    # Percorsi SharePoint (relativi a configurazione.sharepoint_base_path)
    sp_lavoro_path = Column(String(500), nullable=True)
    sp_prescrizioni_path = Column(String(500), nullable=True)
    sp_output_path = Column(String(500), nullable=True)
    sp_archivio_path = Column(String(500), nullable=True)

    # File chiave (solo nome file, non percorso completo)
    excel_input_filename = Column(String(255), nullable=True)
    excel_output_filename = Column(String(255), nullable=True)
    excel_difformita_filename = Column(String(255), nullable=True)

    # Metriche aggregate, aggiornate al termine di ogni fase
    n_pdf_caricati = Column(Integer, default=0)
    n_prescrizioni_totali = Column(Integer, default=0)
    n_barcode_letti = Column(Integer, default=0)
    n_barcode_undefined = Column(Integer, default=0)
    n_match_excel = Column(Integer, default=0)
    score_ocr_medio = Column(Numeric(5, 2), nullable=True)
    n_difformita_totali = Column(Integer, default=0)
    n_prescrizioni_con_difformita = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completato_at = Column(DateTime, nullable=True)
    archiviato_at = Column(DateTime, nullable=True)
    note = Column(Text, nullable=True)

    caricamenti = relationship("CaricamentoFile", back_populates="lotto", cascade="all, delete-orphan")
    elaborazioni = relationship("Elaborazione", back_populates="lotto", cascade="all, delete-orphan")
    prescrizioni = relationship("Prescrizione", back_populates="lotto", cascade="all, delete-orphan")

    @property
    def elaborazione_attiva(self):
        """L'ultima elaborazione non ancora conclusa, se presente."""
        for e in sorted(self.elaborazioni, key=lambda e: e.started_at or datetime.min, reverse=True):
            if e.stato in (StatoElaborazione.in_coda, StatoElaborazione.in_corso):
                return e
        return None


# ============================================================
# Caricamenti file — traccia l'upload iniziale locale -> SharePoint
# ============================================================

class TipoCaricamento(str, enum.Enum):
    pdf_combined = "pdf_combined"
    excel_regione = "excel_regione"


class StatoCaricamento(str, enum.Enum):
    in_caricamento = "in_caricamento"
    completato = "completato"
    errore = "errore"


class CaricamentoFile(Base):
    __tablename__ = "caricamenti_file"

    id = Column(GUID, primary_key=True, default=genera_uuid)
    lotto_id = Column(GUID, ForeignKey("lotti_mensili.id"), nullable=False)
    lotto = relationship("LottoMensile", back_populates="caricamenti")

    tipo = Column(Enum(TipoCaricamento), nullable=False)
    nome_file_locale = Column(String(255), nullable=False)
    nome_file_sp = Column(String(255), nullable=True)
    percorso_sp = Column(String(500), nullable=True)
    dimensione_bytes = Column(BigInteger, nullable=True)
    stato = Column(Enum(StatoCaricamento), default=StatoCaricamento.in_caricamento, nullable=False)

    caricato_da_id = Column(GUID, ForeignKey("utenti.id"), nullable=True)
    caricato_da = relationship("Utente")

    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    errore = Column(Text, nullable=True)


# ============================================================
# Elaborazioni — ogni run della pipeline (una fase alla volta)
# ============================================================

class FaseElaborazione(str, enum.Enum):
    preprocessing = "preprocessing"
    vllm = "vllm"
    difformita = "difformita"
    completa = "completa"


class StatoElaborazione(str, enum.Enum):
    in_coda = "in_coda"
    in_corso = "in_corso"
    completata = "completata"
    errore = "errore"
    annullata = "annullata"


class Elaborazione(Base):
    __tablename__ = "elaborazioni"

    id = Column(GUID, primary_key=True, default=genera_uuid)
    lotto_id = Column(GUID, ForeignKey("lotti_mensili.id"), nullable=False)
    lotto = relationship("LottoMensile", back_populates="elaborazioni")

    fase = Column(Enum(FaseElaborazione), nullable=False)
    stato = Column(Enum(StatoElaborazione), default=StatoElaborazione.in_coda, index=True, nullable=False)

    avviata_da_id = Column(GUID, ForeignKey("utenti.id"), nullable=True)
    avviata_da = relationship("Utente")

    comando_docker = Column(String(1000), nullable=True)
    nome_container = Column(String(150), nullable=True)  # solo backend reale: nome assegnato al container in esecuzione
    richiesta_controllo = Column(String(20), nullable=True)  # None | "pausa" | "annulla"
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    n_processati = Column(Integer, default=0)
    n_errori = Column(Integer, default=0)
    exit_code = Column(Integer, nullable=True)

    log = relationship("LogElaborazione", back_populates="elaborazione", cascade="all, delete-orphan")


class LivelloLog(str, enum.Enum):
    info = "info"
    warning = "warning"
    error = "error"


class LogElaborazione(Base):
    """
    Output Docker riga per riga. Tenuto separato da Elaborazione perche'
    puo' avere migliaia di righe: permette streaming via SSE senza
    caricare tutto in memoria, e purge periodico senza toccare il resto.
    """
    __tablename__ = "log_elaborazioni"

    id = Column(GUID, primary_key=True, default=genera_uuid)
    elaborazione_id = Column(GUID, ForeignKey("elaborazioni.id"), nullable=False)
    elaborazione = relationship("Elaborazione", back_populates="log")

    timestamp = Column(DateTime, default=datetime.utcnow)
    livello = Column(Enum(LivelloLog), default=LivelloLog.info, nullable=False)
    messaggio = Column(Text, nullable=False)


# ============================================================
# Prescrizioni
# ============================================================

class StatoBarcode(str, enum.Enum):
    letto = "letto"
    undefined = "undefined"
    corretto_manuale = "corretto_manuale"
    escluso = "escluso"


class StatoRevisionePrescrizione(str, enum.Enum):
    non_rivisto = "non_rivisto"
    approvato = "approvato"
    da_correggere = "da_correggere"
    corretto = "corretto"


class DecisioneEtichettaMancante(str, enum.Enum):
    confermata = "confermata"  # operatore: l'etichetta manca davvero
    esclusa = "esclusa"        # operatore: falso allarme, l'etichetta e' presente


class Prescrizione(Base):
    __tablename__ = "prescrizioni"

    id = Column(GUID, primary_key=True, default=genera_uuid)
    lotto_id = Column(GUID, ForeignKey("lotti_mensili.id"), nullable=False)
    lotto = relationship("LottoMensile", back_populates="prescrizioni")

    barcode = Column(String(15), index=True, nullable=True)
    stato_barcode = Column(Enum(StatoBarcode), default=StatoBarcode.letto, nullable=False)
    barcode_corretto_da_id = Column(GUID, ForeignKey("utenti.id"), nullable=True)
    barcode_corretto_da = relationship("Utente", foreign_keys=[barcode_corretto_da_id])
    barcode_corretto_at = Column(DateTime, nullable=True)

    # Percorsi SharePoint (relativi)
    sp_png_path = Column(String(500), nullable=True)
    sp_pdf_path = Column(String(500), nullable=True)
    sp_json_path = Column(String(500), nullable=True)

    # Qualita' OCR
    score_ocr = Column(Numeric(5, 2), nullable=True)
    n_campi_compilati = Column(Integer, nullable=True)
    n_campi_totali = Column(Integer, default=22)

    # Stato revisione
    stato_revisione = Column(Enum(StatoRevisionePrescrizione), default=StatoRevisionePrescrizione.non_rivisto, nullable=False)
    rivisto_da_id = Column(GUID, ForeignKey("utenti.id"), nullable=True)
    rivisto_da = relationship("Utente", foreign_keys=[rivisto_da_id])
    rivisto_at = Column(DateTime, nullable=True)

    # Match con Excel regione
    barcode_in_excel = Column(Boolean, nullable=True)
    riga_excel = Column(Integer, nullable=True)

    # Segnalazione "etichetta mancante" in revisione difformita' (vedi
    # gestisci_etichetta_mancante in lotti.py): None finche' l'operatore
    # non si e' espresso sulla segnalazione automatica (basata sulla
    # presenza congiunta delle difformita' 11+12+13+16, stessa logica di
    # phase4_excel.py:COLONNA_ETICHETTA_MANCANTE), poi la sua decisione.
    decisione_etichetta_mancante = Column(Enum(DecisioneEtichettaMancante), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    dati_ocr = relationship("DatiOcr", back_populates="prescrizione", uselist=False, cascade="all, delete-orphan")
    difformita = relationship("Difformita", back_populates="prescrizione", cascade="all, delete-orphan")


# ============================================================
# Dati OCR — 1:1 con prescrizioni (24 campi estratti dal VLLM)
# ============================================================

class DatiOcr(Base):
    __tablename__ = "dati_ocr"

    id = Column(GUID, primary_key=True, default=genera_uuid)
    prescrizione_id = Column(GUID, ForeignKey("prescrizioni.id"), unique=True, nullable=False)
    prescrizione = relationship("Prescrizione", back_populates="dati_ocr")

    cognome_nome_assistito = Column(String(255), nullable=True)
    codice_fiscale = Column(String(16), nullable=True)
    codice_esenzione = Column(String(50), nullable=True)
    codice_atc = Column(String(50), nullable=True)
    testo_prescrizione = Column(Text, nullable=True)
    metodo_estrattivo_olio = Column(String(100), nullable=True)
    forma_farmaceutica = Column(String(100), nullable=True)
    data_prescrizione = Column(Date, nullable=True)
    data_etichetta_preparazione = Column(Date, nullable=True)
    data_invio = Column(Date, nullable=True)
    etichetta_data_scadenza = Column(Date, nullable=True)
    timbro_medico = Column(Boolean, nullable=True)
    firma_medico = Column(Boolean, nullable=True)
    etichetta_nome_cognome_medico = Column(String(255), nullable=True)
    etichetta_nome_cognome_paziente = Column(String(255), nullable=True)
    etichetta_prezzo_sost = Column(Numeric(8, 2), nullable=True)
    etichetta_prezzo_on = Column(Numeric(8, 2), nullable=True)
    etichetta_prezzo_rec = Column(Numeric(8, 2), nullable=True)
    etichetta_prezzo_iva = Column(Numeric(8, 2), nullable=True)
    etichetta_prezzo_tot = Column(Numeric(8, 2), nullable=True)
    totale_prescrizione = Column(Numeric(8, 2), nullable=True)
    etichetta_thc = Column(String(50), nullable=True)
    nome_farmacia = Column(String(255), nullable=True)
    etichetta_avvertenze = Column(Text, nullable=True)

    # Versione raw VLLM (immutabile, per audit) vs versione corretta dall'operatore
    json_vllm_raw = Column(JSON, nullable=True)
    json_corretto = Column(JSON, nullable=True)
    corretto_da_id = Column(GUID, ForeignKey("utenti.id"), nullable=True)
    corretto_da = relationship("Utente")
    corretto_at = Column(DateTime, nullable=True)

    extracted_at = Column(DateTime, default=datetime.utcnow)


# ============================================================
# Difformita
# ============================================================

class StatoDifformita(str, enum.Enum):
    rilevata = "rilevata"
    confermata = "confermata"
    esclusa = "esclusa"
    in_revisione = "in_revisione"


class GravitaDifformita(str, enum.Enum):
    alta = "alta"
    media = "media"
    bassa = "bassa"


class Difformita(Base):
    __tablename__ = "difformita"

    id = Column(GUID, primary_key=True, default=genera_uuid)
    prescrizione_id = Column(GUID, ForeignKey("prescrizioni.id"), nullable=False)
    prescrizione = relationship("Prescrizione", back_populates="difformita")

    codice = Column(String(4), nullable=False)  # '01', '02', '05A', ...
    descrizione = Column(String(500), nullable=False)
    stato = Column(Enum(StatoDifformita), default=StatoDifformita.rilevata, nullable=False)
    rilevata_at = Column(DateTime, default=datetime.utcnow)

    gestita_da_id = Column(GUID, ForeignKey("utenti.id"), nullable=True)
    gestita_da = relationship("Utente")
    gestita_at = Column(DateTime, nullable=True)

    @property
    def gravita(self) -> GravitaDifformita:
        MAPPA_CODICI = {
            "01": GravitaDifformita.alta,
            "02": GravitaDifformita.alta,
            "03": GravitaDifformita.alta,
            "04": GravitaDifformita.alta,
            "05": GravitaDifformita.bassa,
            "05A": GravitaDifformita.alta,
            "06": GravitaDifformita.alta,
            "07": GravitaDifformita.alta,
            "08": GravitaDifformita.bassa,
            "09": GravitaDifformita.alta,
            "10": GravitaDifformita.alta,
            "11": GravitaDifformita.alta,
            "12": GravitaDifformita.bassa,
            "13": GravitaDifformita.bassa,
            "14": GravitaDifformita.bassa,
            "16": GravitaDifformita.alta,
            "17": GravitaDifformita.alta,
            "18": GravitaDifformita.alta,
            "19": GravitaDifformita.alta
        }
        return MAPPA_CODICI.get(self.codice, GravitaDifformita.bassa)


# ============================================================
# Annotazioni — note su difformita o prescrizioni (tabella generica)
# ============================================================

class TipoEntitaAnnotazione(str, enum.Enum):
    difformita = "difformita"
    prescrizione = "prescrizione"


class TipoNota(str, enum.Enum):
    nota = "nota"
    correzione = "correzione"
    comunicazione_farmacia = "comunicazione_farmacia"
    approvazione = "approvazione"


class Annotazione(Base):
    __tablename__ = "annotazioni"

    id = Column(GUID, primary_key=True, default=genera_uuid)
    tipo_entita = Column(Enum(TipoEntitaAnnotazione), nullable=False)
    entita_id = Column(GUID, nullable=False)  # riferimento polimorfico, niente FK reale

    autore_id = Column(GUID, ForeignKey("utenti.id"), nullable=True)
    autore = relationship("Utente")

    testo = Column(Text, nullable=False)
    tipo_nota = Column(Enum(TipoNota), default=TipoNota.nota, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# ============================================================
# Audit log — chi ha fatto cosa
# ============================================================

class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(GUID, primary_key=True, default=genera_uuid)
    utente_id = Column(GUID, ForeignKey("utenti.id"), nullable=True)
    utente = relationship("Utente")

    azione = Column(String(120), nullable=False)  # 'barcode_corretto', 'difformita_esclusa', ...
    entita_tipo = Column(String(120), nullable=False)
    entita_id = Column(GUID, nullable=True)
    payload_before = Column(JSON, nullable=True)
    payload_after = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
