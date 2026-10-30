"""
Verifica che lo storage permanente non scriva mai in silenzio nel posto
sbagliato: in produzione un percorso non valido blocca l'avvio, e un
fallimento di scrittura durante la correzione manuale del barcode
propaga invece di essere ingoiato (vedi app/storage.py,
app/real_pipeline.py::correggi_barcode_reale).
"""
from tests.conftest import esegui_script


def test_produzione_si_rifiuta_di_partire_senza_storage_locale_valido():
    script = r"""
import os
os.environ['APP_ENV'] = 'production'
os.environ['SESSION_SECRET_KEY'] = 'verifica-test'
try:
    from app.main import app
    print('FALLITO: avviato senza STORAGE_LOCAL_DIR in produzione')
except Exception as exc:
    assert 'STORAGE_LOCAL_DIR' in str(exc), exc
    print('OK')
"""
    esito = esegui_script(script, {})
    assert "OK" in esito.stdout, esito.stdout + esito.stderr


def test_produzione_parte_con_storage_locale_valido(tmp_path):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    script = rf"""
import os
os.environ['APP_ENV'] = 'production'
os.environ['SESSION_SECRET_KEY'] = 'verifica-test'
os.environ['STORAGE_LOCAL_DIR'] = r"{storage_dir}"
from app.main import app
print('OK', len(app.routes))
"""
    esito = esegui_script(script, {})
    assert "OK" in esito.stdout, esito.stdout + esito.stderr


def test_correzione_barcode_propaga_errore_senza_commit_parziale(tmp_path):
    db_path = tmp_path / "ats_test_barcode_fail.db"
    sp_root = tmp_path / "sharepoint_finto"
    staging = tmp_path / "ricette_staging_pdfs"
    script = rf"""
import os
os.environ['DATABASE_URL'] = 'sqlite:///{db_path.as_posix()}'

from pathlib import Path
import shutil

from app.database import Base, engine, SessionLocal
import app.models as m
Base.metadata.create_all(bind=engine)

import app.real_pipeline as rp
import app.storage as storage

db = SessionLocal()
lotto = m.LottoMensile(nome='LottoTest', mese=1, anno=2026, stato=m.StatoLotto.revisione_barcode, sp_prescrizioni_path='PRESCRIZIONI')
db.add(lotto)
db.commit()
presc = m.Prescrizione(lotto_id=lotto.id, barcode=None, stato_barcode=m.StatoBarcode.undefined, sp_pdf_path='media/lotti/x/pagine/undefined_1.pdf')
db.add(presc)
db.commit()
presc_id = presc.id

staging_dir = Path(r"{staging}")
staging_dir.mkdir(parents=True, exist_ok=True)
storage.RICETTE_STAGING_PDFS = staging_dir
rp.RICETTE_STAGING_PDFS = staging_dir
(staging_dir / 'undefined_1.pdf').write_bytes(b'%PDF-1.4 test')

sp_root = Path(r"{sp_root}")
(sp_root / 'PRESCRIZIONI').mkdir(parents=True, exist_ok=True)
(sp_root / 'PRESCRIZIONI' / 'undefined_1.pdf').write_bytes(b'%PDF-1.4 test')
rp.radice_sharepoint = lambda: sp_root

originale_move = shutil.move
def move_che_fallisce(src, dst):
    if 'PRESCRIZIONI' in str(dst):
        raise OSError('disco pieno (simulato dal test)')
    return originale_move(src, dst)
rp.shutil.move = move_che_fallisce

propagata = False
try:
    rp.correggi_barcode_reale(presc_id, '8012345678905')
except OSError:
    propagata = True
assert propagata, 'OSError non propagata'

db2 = SessionLocal()
presc_ricontrollata = db2.query(m.Prescrizione).filter(m.Prescrizione.id == presc_id).first()
assert presc_ricontrollata.stato_barcode == m.StatoBarcode.undefined
assert presc_ricontrollata.barcode is None
print('OK')
"""
    esito = esegui_script(script, {})
    assert "OK" in esito.stdout, esito.stdout + esito.stderr
