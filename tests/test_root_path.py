"""
Verifica che l'app funzioni sia alla radice sia sotto un prefisso IIS
(ROOT_PATH) — vedi app/root_path.py. Nessuna route usa url_for(), quindi
il meccanismo nativo di Starlette legato a --root-path da solo non copre
redirect/template/JS: questi test lo verificano end-to-end con un vero
TestClient che simula il reverse proxy (stesso approccio indicato dalla
specifica: "simulalo nei test").
"""
from tests.conftest import esegui_script


def test_redirect_e_asset_prefissati_con_root_path():
    script = r"""
import os
os.environ['ROOT_PATH'] = '/cannabis'
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app, follow_redirects=False)
r = client.get('/')
assert r.headers.get('location') == '/cannabis/login', r.headers.get('location')

r = client.get('/login')
assert r.status_code == 200
assert '/cannabis/static/css/style.css' in r.text
print('OK')
"""
    esito = esegui_script(script, {})
    assert "OK" in esito.stdout, esito.stdout + esito.stderr


def test_nessuna_regressione_senza_root_path():
    script = r"""
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app, follow_redirects=False)
r = client.get('/')
assert r.headers.get('location') == '/login', r.headers.get('location')

r = client.get('/login')
assert '"/static/css/style.css"' in r.text
print('OK')
"""
    esito = esegui_script(script, {})
    assert "OK" in esito.stdout, esito.stdout + esito.stderr


def test_pagine_autenticate_sotto_root_path(tmp_path):
    db_path = tmp_path / "ats_test_root_path.db"
    script = rf"""
import os
os.environ['DATABASE_URL'] = 'sqlite:///{db_path.as_posix()}'
os.environ['ROOT_PATH'] = '/cannabis'

from app.database import Base, engine, SessionLocal
import app.models as m
from app.auth import hash_password
Base.metadata.create_all(bind=engine)

db = SessionLocal()
admin = m.Utente(
    username='admin_test', password_hash=hash_password('prova123456'),
    nome='Admin Test', email='admin_test@example.com',
    ruolo=m.RuoloUtente.amministratore, attivo=True,
)
db.add(admin)
db.commit()
db.close()

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app, base_url='http://testserver/cannabis', root_path='/cannabis', follow_redirects=True)
r = client.post('/login', data={{'username': 'admin_test', 'password': 'prova123456'}})
assert r.status_code == 200 and str(r.url) == 'http://testserver/cannabis/', str(r.url)

for path in ['/', '/lotti', '/archivio', '/impostazioni', '/lotti/nuovo']:
    r = client.get(path)
    assert r.status_code == 200, (path, r.status_code)
    assert '/cannabis/static/css/style.css' in r.text, path
    assert 'href=\"/cannabis/lotti\"' in r.text, path
    assert 'name=\"password\"' not in r.text, f'{{path}} e finito sul login'
print('OK')
"""
    esito = esegui_script(script, {})
    assert "OK" in esito.stdout, esito.stdout + esito.stderr


def test_upload_troppo_grande_redirige_al_wizard_con_errore():
    script = r"""
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app, follow_redirects=False)
r = client.post('/lotti/nuovo', headers={'content-length': str(600 * 1024 * 1024)}, data={})
assert r.status_code == 302
assert r.headers['location'].startswith('/lotti/nuovo?errore='), r.headers.get('location')

r2 = client.post('/qualcosa/altro', headers={'content-length': str(600 * 1024 * 1024)})
assert r2.status_code == 413
print('OK')
"""
    esito = esegui_script(script, {})
    assert "OK" in esito.stdout, esito.stdout + esito.stderr


def test_health_riporta_database_ollama_storage():
    script = r"""
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
r = client.get('/health')
assert r.status_code == 200
corpo = r.json()
assert set(corpo['componenti'].keys()) == {'database', 'ollama', 'storage'}
print('OK')
"""
    esito = esegui_script(script, {})
    assert "OK" in esito.stdout, esito.stdout + esito.stderr
