"""
Generatori di dati finti condivisi tra seed_admin.py e fake_pipeline.py.
Nessuna logica applicativa qui dentro, solo dati plausibili per demo/sviluppo.
"""
import random
from datetime import date, timedelta

NOMI_PAZIENTI = ["Mario Rossi", "Anna Bianchi", "Luca Verdi", "Giulia Ferrari", "Marco Colombo",
                 "Sara Ricci", "Davide Marino", "Elena Greco", "Paolo Conti", "Chiara Villa"]

NOMI_MEDICI = ["Dr. Alberto Fumagalli", "Dr.ssa Silvia Brambilla", "Dr. Enrico Riva",
               "Dr.ssa Paola Sala", "Dr. Matteo Longoni"]

NOMI_FARMACIE = [
    "Farmacia Tili Snc",
    "Farmacia Di Lora Srl",
    "Farmacia Pomi di dr. Collivasone A. & C. Snc",
    "Farmacia Ramella dott.ri G. e A. Sas",
    "Farmacia Mazzucchelli F. & C. Snc",
    "Farmacia Peroni dr Antonio E. & C. Sas",
    "Farmacia Comunale N.2",
    "Farmacia Stefini & C Sas",
    "Farmacia Introini dr. Paolo & C. Sas",
    "Farmacia Di Crenna",
    "Farmacia Ponti",
]

FORME_FARMACEUTICHE = ["Infiorescenza", "Olio", "Cartine"]
METODI_ESTRATTIVI = ["Decozione", "Olio oliva a freddo", "Olio oliva a caldo", "N/A"]
CODICI_ATC = ["N02BG10", "A04AD", "N03AX"]

CODICI_DIFFORMITA = [
    ("01", "Dosaggio giornaliero fuori range terapeutico previsto"),
    ("02", "Durata terapia non conforme alla prescrizione autorizzata"),
    ("03", "Quantita totale prescritta non coerente con la posologia"),
    ("05A", "Firma del medico prescrittore non rilevata"),
    ("05B", "Timbro del medico prescrittore non rilevato"),
    ("07", "Codice ATC non corrispondente al principio attivo"),
    ("09", "Codice fiscale paziente illeggibile o mancante"),
]


def genera_barcode() -> str:
    """Barcode numerico a 15 cifre, prefisso 030 come da esempio nello schema."""
    return f"030{random.randint(10**11, 10**12 - 1)}"[:15]


def genera_dati_ocr_finti(nome_farmacia: str = None) -> dict:
    """Simula i 24 campi che il VLLM estrarrebbe davvero (fase 2 / OCR)."""
    data_presc = date.today() - timedelta(days=random.randint(1, 60))
    prezzo_base = round(random.uniform(15, 90), 2)
    return {
        "cognome_nome_assistito": random.choice(NOMI_PAZIENTI),
        "codice_fiscale": "".join(random.choices("ABCDEFGHILMNOPQRSTUVZ", k=6))
        + f"{random.randint(10,99)}{random.choice('ABCDEHLMPRST')}{random.randint(10,99)}"
        + f"{random.choice('ABCDEFGHILMNOPQRSTUVZ')}{random.randint(100,999)}{random.choice('ABCDEFGHILMNOPQRSTUVZ')}",
        "codice_esenzione": random.choice(["", "048", "L01", "E01"]),
        "codice_atc": random.choice(CODICI_ATC),
        "testo_prescrizione": "Cannabis FM2 uso orale, posologia come da indicazione medica",
        "metodo_estrattivo_olio": random.choice(METODI_ESTRATTIVI),
        "forma_farmaceutica": random.choice(FORME_FARMACEUTICHE),
        "data_prescrizione": data_presc,
        "data_etichetta_preparazione": data_presc + timedelta(days=random.randint(1, 5)),
        "data_invio": data_presc + timedelta(days=random.randint(5, 10)),
        "etichetta_data_scadenza": data_presc + timedelta(days=180),
        "timbro_medico": random.random() > 0.08,
        "firma_medico": random.random() > 0.05,
        "etichetta_nome_cognome_medico": random.choice(NOMI_MEDICI),
        "etichetta_nome_cognome_paziente": random.choice(NOMI_PAZIENTI),
        "etichetta_prezzo_sost": prezzo_base,
        "etichetta_prezzo_on": round(prezzo_base * 0.1, 2),
        "etichetta_prezzo_rec": round(prezzo_base * 0.05, 2),
        "etichetta_prezzo_iva": round(prezzo_base * 0.1, 2),
        "etichetta_prezzo_tot": round(prezzo_base * 1.25, 2),
        "totale_prescrizione": round(prezzo_base * 1.25, 2),
        "etichetta_thc": f"{random.uniform(6, 22):.1f}%",
        "nome_farmacia": nome_farmacia or random.choice(NOMI_FARMACIE),
        "etichetta_avvertenze": "Non superare la dose prescritta. Conservare lontano da fonti di calore.",
    }
