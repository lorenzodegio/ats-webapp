"""
Cancella il database SQLite esistente e lo ricrea da zero con
utenti, configurazione e lotti demo (richiama seed_admin.py).

Utile ogni volta che si modifica app/models.py: SQLite non supporta
migrazioni automatiche, quindi lo schema va ricreato da capo. Quando si
passera' a PostgreSQL in produzione, questo script andra' sostituito da
migrazioni vere (es. Alembic).

Uso:
    python reset_db.py
"""
import os
import subprocess
import sys

DB_PATH = "ats_cannabis.db"


def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"Database '{DB_PATH}' cancellato.")
    else:
        print(f"Nessun database '{DB_PATH}' trovato, procedo comunque con il seed.")

    print("Rigenero utenti, configurazione e lotti demo...\n")
    subprocess.run([sys.executable, "seed_admin.py"], check=True)


if __name__ == "__main__":
    main()
