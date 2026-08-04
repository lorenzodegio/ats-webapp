"""
Finto frontend per validare il backend in isolamento, senza aspettare
il team frontend. Simula la sequenza di chiamate che l'interfaccia reale
fara: login, creazione job, consultazione stato, archivio, difformita.

Avvio: pip install -r requirements.txt && python test_client.py
"""
import requests

BASE_URL = "http://localhost:8000"


def main():
    sessione = requests.Session()

    print("1. Login...")
    r = sessione.post(f"{BASE_URL}/login", data={"username": "admin", "password": "admin123"})
    print(f"   -> {r.status_code}")

    print("2. Dashboard...")
    r = sessione.get(f"{BASE_URL}/")
    print(f"   -> {r.status_code}")

    print("3. Nuova elaborazione (upload finto)...")
    r = sessione.post(
        f"{BASE_URL}/jobs/nuovo",
        files={"file": ("prescrizione_test.pdf", b"%PDF-1.4 contenuto finto", "application/pdf")},
        data={"modalita": "full"},
    )
    print(f"   -> {r.status_code}")

    print("4. Lista elaborazioni...")
    r = sessione.get(f"{BASE_URL}/jobs")
    print(f"   -> {r.status_code}")

    print("5. Archivio...")
    r = sessione.get(f"{BASE_URL}/archivio")
    print(f"   -> {r.status_code}")

    print("6. Difformita...")
    r = sessione.get(f"{BASE_URL}/difformita")
    print(f"   -> {r.status_code}")

    print("7. Logout...")
    r = sessione.get(f"{BASE_URL}/logout")
    print(f"   -> {r.status_code}")


if __name__ == "__main__":
    main()
