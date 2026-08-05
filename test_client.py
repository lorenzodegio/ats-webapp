"""
test_client.py
----------------
Client di test a riga di comando per validare le API backend senza
passare dal frontend (documento, Sezione 5.1 e 7.1) — estende
l'esempio già presente nel documento a tutti gli endpoint disponibili.

Uso:
    python test_client.py

Richiede che il server sia già in esecuzione (uvicorn main:app --reload)
e che esista già un utente amministratore (python seed_admin.py).
"""

import sys
import requests

BASE = "http://localhost:8000"


def prova(descrizione, risposta, atteso=None):
    ok = "OK" if (atteso is None or risposta.status_code == atteso) else "FALLITO"
    print(f"[{ok}] {descrizione} -> {risposta.status_code} {risposta.url}")
    return risposta


def main():
    if len(sys.argv) < 3:
        print("Uso: python test_client.py <username> <password>")
        sys.exit(1)

    username, password = sys.argv[1], sys.argv[2]
    s = requests.Session()

    print("=== 1. Accesso senza autenticazione (deve reindirizzare a /login) ===")
    prova("GET / senza login", s.get(f"{BASE}/", allow_redirects=False), atteso=303)

    print()
    print("=== 2. Login ===")
    r = prova("POST /login", s.post(f"{BASE}/login", data={"username": username, "password": password}, allow_redirects=False))
    if r.status_code != 303 or "login" in r.headers.get("location", ""):
        print("Login fallito — credenziali errate o utente non esistente. Interrompo.")
        sys.exit(1)

    print()
    print("=== 3. Dashboard ===")
    prova("GET /", s.get(f"{BASE}/"), atteso=200)

    print()
    print("=== 4. Nuova elaborazione (form) ===")
    prova("GET /jobs/nuovo", s.get(f"{BASE}/jobs/nuovo"), atteso=200)

    print()
    print("=== 5. Lista elaborazioni ===")
    prova("GET /jobs", s.get(f"{BASE}/jobs"), atteso=200)

    print()
    print("=== 6. Archivio ===")
    prova("GET /archivio", s.get(f"{BASE}/archivio"), atteso=200)
    prova("GET /archivio?barcode=0301", s.get(f"{BASE}/archivio", params={"barcode": "0301"}), atteso=200)

    print()
    print("=== 7. Difformità ===")
    prova("GET /difformita", s.get(f"{BASE}/difformita"), atteso=200)

    print()
    print("=== 8. Logout ===")
    prova("GET /logout", s.get(f"{BASE}/logout", allow_redirects=False), atteso=303)

    print()
    print("=== 9. Verifica che dopo il logout /jobs richieda di nuovo il login ===")
    prova("GET /jobs dopo logout", s.get(f"{BASE}/jobs", allow_redirects=False), atteso=303)

    print()
    print("Tutti i controlli completati.")


if __name__ == "__main__":
    main()
