"""
Supporto per far funzionare l'app sia alla radice sia sotto un prefisso
(es. produzione dietro IIS: https://server/cannabis/ -> http://127.0.0.1:8000/,
col prefisso TOLTO da IIS prima di arrivare a uvicorn — vedi ROOT_PATH in
.env.example e --root-path nel comando uvicorn, deploy/windows/install.ps1).

Nessuna route di questa app usa url_for()/request.url_for() (verificato:
zero occorrenze in app/routers), quindi il meccanismo nativo di Starlette
legato a --root-path (che renderebbe automaticamente root_path-aware SOLO
le route con nome) non copre nulla qui — redirect, link nei template,
asset statici e fetch() in JS usano tutti percorsi scritti a mano
("/lotti", "/static/...", ecc.). Questo modulo e' l'UNICO posto che sa
come anteporre il prefisso a quei percorsi:
  - RedirectResponse qui sotto, al posto di quella di fastapi/starlette,
    per tutti i redirect lato Python (vedi i router: cambia solo
    l'import, non le decine di chiamate esistenti);
  - crea_templates() per i template Jinja2 (href/action/src scritti come
    "{{ ROOT_PATH }}/..." nei file .html);
  - ROOT_PATH esposta anche a window.ROOT_PATH in base.html, letta dai
    file JS che chiamano fetch() con percorsi assoluti.

ROOT_PATH vuota (default, sviluppo e macchina GPU attuale) lascia tutto
invariato byte per byte rispetto a prima di questa modifica.
"""
import os

from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse as _RedirectResponseBase

ROOT_PATH = os.environ.get("ROOT_PATH", "").strip().rstrip("/")


class RedirectResponse(_RedirectResponseBase):
    """Come starlette.responses.RedirectResponse, ma antepone ROOT_PATH a
    un url che comincia per "/" (percorso interno di questa app) — un url
    assoluto con schema (http://...) o un percorso relativo non viene
    toccato."""

    def __init__(self, url, *args, **kwargs):
        url = str(url)
        if ROOT_PATH and url.startswith("/"):
            url = ROOT_PATH + url
        super().__init__(url, *args, **kwargs)


def crea_templates(directory: str = "app/templates") -> Jinja2Templates:
    """Jinja2Templates con ROOT_PATH disponibile in ogni pagina (vedi
    RedirectResponse sopra per lo stesso principio sui redirect)."""
    templates = Jinja2Templates(directory=directory)
    templates.env.globals["ROOT_PATH"] = ROOT_PATH
    return templates
