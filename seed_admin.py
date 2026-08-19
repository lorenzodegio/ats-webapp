"""
seed_admin.py
--------------
Crea il primo utente amministratore. Da lanciare una volta sola,
dalla cartella del progetto:

    python seed_admin.py
"""

import sys
import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "app"))

from dotenv import load_dotenv
load_dotenv()  # prima di importare database.py, che legge le variabili d'ambiente all'import

from database import init_db, SessionLocal
from models import Utente
from auth import hash_password


def crea_admin():
    init_db()
    db = SessionLocal()

    esistente = db.query(Utente).filter_by(ruolo="admin").first()
    if esistente:
        print(f"Esiste già un amministratore: {esistente.username}. Nessuna azione.")
        return

    username = input("Username amministratore: ").strip()
    nome_completo = input("Nome completo: ").strip()
    password = getpass.getpass("Password: ")
    conferma = getpass.getpass("Conferma password: ")

    if password != conferma:
        print("Le password non coincidono. Riprova.")
        return

    admin = Utente(
        username=username,
        password_hash=hash_password(password),
        nome_completo=nome_completo,
        ruolo="admin",
    )
    db.add(admin)
    db.commit()
    print(f"Amministratore '{username}' creato con successo.")


if __name__ == "__main__":
    crea_admin()
