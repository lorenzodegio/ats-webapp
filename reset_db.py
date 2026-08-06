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