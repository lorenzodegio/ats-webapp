"""
conftest.py — fixture condivise.

Molti moduli (app.storage, app.root_path, app.real_pipeline, app.main)
leggono le variabili d'ambiente UNA VOLTA al momento dell'import (stesso
pattern in tutto il progetto: niente oggetto "settings" centrale). Per
testare scenari diversi (APP_ENV, ROOT_PATH, STORAGE_BACKEND...) senza che
un test contamini il processo per i successivi, i test che hanno bisogno
di una configurazione specifica lanciano un sottoprocesso Python pulito
(vedi esegui_script) invece di affidarsi a importlib.reload — più lento,
ma riproduce esattamente il comportamento reale di un avvio dell'app, che
è quello che vogliamo verificare.
"""
import subprocess
import sys
from pathlib import Path

RADICE_REPO = Path(__file__).resolve().parent.parent


def esegui_script(script: str, env_extra: dict, timeout: float = 60.0) -> subprocess.CompletedProcess:
    """Lancia `script` in un interprete Python pulito, nella radice del
    repository, con SOLO le variabili extra indicate oltre a quelle di
    sistema (PATH, ecc. — necessarie per trovare le DLL/l'interprete
    stesso). Non eredita l'ambiente del processo di test corrente:
    altrimenti un .env o una variabile già impostata per pytest
    inquinerebbe lo scenario che il test vuole controllare."""
    import os

    ambiente_minimo = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PYTHONPATH": str(RADICE_REPO),
        "PYTHONIOENCODING": "utf-8",
    }
    ambiente_minimo.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(RADICE_REPO), env=ambiente_minimo,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )
