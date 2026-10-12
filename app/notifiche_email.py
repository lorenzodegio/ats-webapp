"""
Notifiche email via SMTP diretto (relay interno ATS) a fine fase e in
caso di errore della pipeline di un lotto (preprocessing, OCR, analisi
difformita', completamento/Excel finale).

Best-effort per design: un problema qui (server SMTP irraggiungibile,
configurazione mancante...) NON deve MAI bloccare la pipeline reale —
ogni chiamata pubblica e' avvolta in try/except e si limita a loggare
un warning.

Nessun dato paziente/sensibile nel corpo delle email: solo nome lotto,
fase, esito, timestamp e un invito testuale ad accedere all'applicazione
per continuare (nessun link cliccabile — un URL relativo non
funzionerebbe in un client email, ed e' comunque richiesto il login). Il
dettaglio di un errore resta solo nella nota del lotto in webapp, mai
nell'email.

Configurazione in .env:
    SMTP_HOST              es. smtp.ats-insubria.it (o l'IP 10.1.1.120)
    SMTP_PORT              default 25
    SMTP_MITTENTE          es. applicazioni_AI@ats-insubria.it
    SMTP_USERNAME          opzionale — solo se il relay richiede login
    SMTP_PASSWORD          opzionale — solo se il relay richiede login

Relay interno confermato: porta 25, nessuna crittografia (niente
STARTTLS/SSL) — coerente con un relay raggiungibile solo dalla rete
interna ATS, non da internet.

I DESTINATARI non sono in .env: sono le email degli Utente con
ruolo == operatore e attivo == True nel database (tabella utenti), lette al momento
dell'invio — aggiungere/disattivare un account aggiorna automaticamente
chi riceve le notifiche, senza toccare la configurazione.

Se una qualunque delle prime tre manca, le notifiche restano
disattivate in silenzio (nessun errore in avvio, nessuna email inviata)
— utile in sviluppo/sulle macchine senza questa configurazione.
"""
import logging
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage

log = logging.getLogger("NotificheEmail")

ETICHETTE_FASE = {
    "preprocessing": "Preprocessing",
    "ocr": "OCR",
    "difformita": "Analisi difformità",
    "excel": "Completamento lotto",
}

_VARIABILI_RICHIESTE = ("SMTP_HOST", "SMTP_MITTENTE")


def _configurazione_completa() -> bool:
    mancanti = [nome for nome in _VARIABILI_RICHIESTE if not os.getenv(nome)]
    if mancanti:
        log.debug(f"Notifiche email disattivate: variabili mancanti in .env: {', '.join(mancanti)}")
        return False
    return True


def _destinatari() -> list:
    from app.database import SessionLocal
    from app.models import RuoloUtente, Utente

    db = SessionLocal()
    try:
        righe = db.query(Utente.email).filter(
            Utente.attivo.is_(True), Utente.ruolo == RuoloUtente.operatore
        ).all()
        return [email for (email,) in righe if email]
    finally:
        db.close()


def _invia(oggetto: str, corpo_html: str) -> None:
    if not _configurazione_completa():
        return
    destinatari = _destinatari()
    if not destinatari:
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = oggetto
        msg["From"] = os.environ["SMTP_MITTENTE"]
        msg["To"] = ", ".join(destinatari)
        msg.set_content("Questa email richiede un client che supporta HTML.")
        msg.add_alternative(corpo_html, subtype="html")

        host = os.environ["SMTP_HOST"]
        port = int(os.environ.get("SMTP_PORT", "25"))
        with smtplib.SMTP(host, port, timeout=15) as server:
            username = os.environ.get("SMTP_USERNAME")
            password = os.environ.get("SMTP_PASSWORD")
            if username and password:
                server.login(username, password)
            server.send_message(msg)
    except Exception:
        log.exception("Invio notifica email fallito (la pipeline prosegue comunque)")


def notifica_fase_completata(lotto, fase: str) -> None:
    """fase: una chiave di ETICHETTE_FASE ('preprocessing', 'ocr', 'difformita', 'excel')."""
    etichetta = ETICHETTE_FASE.get(fase, fase)
    oggetto = f"[ATS Cannabis] {lotto.nome} — {etichetta} completato"
    corpo = (
        f"<p>Lotto: <strong>{lotto.nome}</strong></p>"
        f"<p>Fase completata: <strong>{etichetta}</strong></p>"
        f"<p>Data/ora: {datetime.now():%d/%m/%Y %H:%M}</p>"
        f"<p>Accedere all'applicazione per continuare.</p>"
    )
    _invia(oggetto, corpo)


def notifica_errore(lotto, fase: str) -> None:
    etichetta = ETICHETTE_FASE.get(fase, fase)
    oggetto = f"[ATS Cannabis] {lotto.nome} — ERRORE in {etichetta}"
    corpo = (
        f"<p>Lotto: <strong>{lotto.nome}</strong></p>"
        f"<p>Fase in errore: <strong>{etichetta}</strong></p>"
        f"<p>Data/ora: {datetime.now():%d/%m/%Y %H:%M}</p>"
        f"<p>Il lotto è ora in stato <strong>eccezione</strong> e richiede intervento manuale.</p>"
        f"<p>Accedere all'applicazione per continuare.</p>"
    )
    _invia(oggetto, corpo)
