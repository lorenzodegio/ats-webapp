"""
Verifica il runner nativo della pipeline (PIPELINE_RUNNER=local, server
senza Docker/WSL2 — vedi app/real_pipeline.py). Due aspetti distinti:
1. il comando/ambiente costruiti per lanciare run_fase.py sono corretti
   quando la repo pipeline e' separata da questa (vedi Blocco 1);
2. pausa/ripresa/annullamento agiscono sull'INTERO albero di processi
   (psutil), non solo sul processo padre.
"""
import subprocess
import sys
import time

import psutil
import pytest

import app.real_pipeline as rp


def test_comando_e_ambiente_puntano_alla_repo_pipeline_separata(monkeypatch):
    monkeypatch.setattr(rp, "PIPELINE_PYTHON", "C:/fake/python.exe")
    monkeypatch.setattr(rp, "PIPELINE_DIR", "C:/fake/pipeline-repo")

    catturato = {}

    class ProcessoFinto:
        pid = 4242
        stdout = iter([])

        def wait(self):
            return 0

    def popen_finto(comando, **kwargs):
        catturato["comando"] = comando
        catturato["kwargs"] = kwargs
        return ProcessoFinto()

    monkeypatch.setattr(rp.subprocess, "Popen", popen_finto)

    class ElaborazioneFinta:
        nome_container = None
        richiesta_controllo = None

    class DbFinto:
        def commit(self):
            pass

        def refresh(self, _):
            pass

    rp._esegui_processo_locale("ocr", DbFinto(), ElaborazioneFinta())

    comando = catturato["comando"]
    assert comando[0] == "C:/fake/python.exe"
    # run_fase.py si localizza rispetto a se stesso (repo webapp), non a
    # PIPELINE_DIR — vedi Blocco 1: altrimenti un server con solo la repo
    # pipeline clonata non lo troverebbe.
    assert comando[1].endswith("docker\\run_fase.py") or comando[1].endswith("docker/run_fase.py")
    assert "ats-webapp-test" in comando[1] or "app" in comando[1]

    ambiente = catturato["kwargs"]["env"]
    assert ambiente["PIPELINE_DIR"] == "C:/fake/pipeline-repo"
    assert ambiente["ATS_TEMPLATE_EXCEL"] == "C:\\fake\\pipeline-repo\\OUTPUT_FLUSSO_CANNABIS_FINALE.xlsx"
    assert ambiente["PYTHONUTF8"] == "1"
    assert catturato["kwargs"]["cwd"] == "C:/fake/pipeline-repo"


def test_pausa_ripresa_terminazione_agiscono_sull_intero_albero():
    script_figlio = (
        "import time, subprocess, sys;"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']);"
        "print(p.pid, flush=True);"
        "time.sleep(30)"
    )
    padre = subprocess.Popen([sys.executable, "-c", script_figlio], stdout=subprocess.PIPE, text=True)
    try:
        pid_figlio = int(padre.stdout.readline().strip())
        time.sleep(0.5)  # lascia che il processo figlio parta davvero

        # psutil non espone STATUS_SUSPENDED su Windows (niente SIGSTOP
        # POSIX): suspend()/resume() usano NtSuspendProcess/NtResumeProcess,
        # visibili tramite STATUS_STOPPED.
        assert rp._sospendi_albero_locale(padre.pid) is True
        time.sleep(0.3)
        assert psutil.Process(padre.pid).status() == psutil.STATUS_STOPPED
        assert psutil.Process(pid_figlio).status() == psutil.STATUS_STOPPED

        assert rp._riprendi_albero_locale(padre.pid) is True
        time.sleep(0.3)
        assert psutil.Process(padre.pid).status() != psutil.STATUS_STOPPED

        assert rp._termina_albero_locale(padre.pid, timeout=5.0) is True
        time.sleep(0.5)
        assert not psutil.pid_exists(padre.pid) or psutil.Process(padre.pid).status() == psutil.STATUS_ZOMBIE
        assert not psutil.pid_exists(pid_figlio)
    finally:
        for p in (padre.pid, pid_figlio if "pid_figlio" in dir() else None):
            if p and psutil.pid_exists(p):
                try:
                    psutil.Process(p).kill()
                except psutil.NoSuchProcess:
                    pass
