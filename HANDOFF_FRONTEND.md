# Stato frontend — handoff per Khalil

Ciao Khalil! Qui trovi lo stato di quello che ho fatto finora sul branch
`lorenzo`, così puoi partire senza dover reverse-engineerare tutto dal codice.
Per il contesto completo (architettura, API, ruoli) vedi il documento di
progetto ufficiale; questo file è solo il riepilogo pratico di cosa c'è e
come ci lavori.

## Come avviare il progetto

```powershell
# la prima volta
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python seed_admin.py          # crea utenti admin/operatore + 12 job demo

# ogni volta dopo
.\venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --port 8000
```

Poi apri `http://localhost:8000`. Utenti già pronti dal seed:

| Utente     | Password       | Ruolo      |
|------------|----------------|------------|
| admin      | admin123       | admin      |
| operatore  | operatore123   | operatore  |

Se vuoi ripartire da un DB pulito: cancella `ats_cannabis.db` e rilancia
`python seed_admin.py`.

## Cosa è già pronto

- **Tutte e 5 le pagine + login**, collegate a un vero DB SQLite (non dati
  finti statici in HTML): Dashboard, Nuova elaborazione, Elaborazioni,
  Archivio, Difformità.
- **Login a sessione** con bcrypt (`app/auth.py`), redirect automatico a
  `/login` se non autenticato.
- **Wizard "Nuova elaborazione" a 4 step** (Caricamento → Modalità →
  Conferma → Monitoraggio), drag&drop del PDF, validazione client-side.
  File: `app/templates/nuova_elaborazione.html` +
  `app/static/js/wizard-nuova-elaborazione.js`.
- **Polling stato job** nella pagina Elaborazioni, aggiorna i badge di
  stato ogni 2 secondi senza ricaricare la pagina
  (`app/static/js/polling-jobs.js`).
- **Design system applicato ovunque** in `app/static/css/style.css`
  (palette, Inter + IBM Plex Mono, radius 16px card / 8px controlli) —
  usa le classi già definite (`.card`, `.btn`, `.badge`, `.tabella`,
  ecc.) invece di scriverne di nuove, così restiamo coerenti.
- **Backend finto** (`app/fake_pipeline.py`): simula le 4 fasi della
  pipeline con stati e tempi realistici, così tutto il frontend è
  testabile end-to-end senza aspettare Alessandro e Francesco. Quando
  collegano il backend vero, cambia solo una riga in `routers/jobs.py`.

## Cosa manca / dove puoi mettere le mani

Secondo il documento di progetto (Sez. 7.2), il pattern a step va esteso
ad altre pagine man mano che ne emerge il bisogno. Punti aperti:

1. **Pagina "Impostazioni"** (solo admin) — percorsi OneDrive, gestione
   utenti. Ancora non esiste ne' route ne' template. Se vuoi partire da
   qui, segui lo schema di `app/routers/archivio.py` come esempio più
   semplice di router protetto.
2. **Validazioni più robuste sul wizard** — oggi valido solo estensione
   `.pdf` lato client; manca un feedback più chiaro sugli errori di
   upload (es. file troppo grande).
3. **Stati vuoti e micro-interazioni** — le tabelle hanno già un fallback
   "nessun dato" ma si può migliorare (skeleton loading, animazioni sui
   badge quando cambia stato via polling).
4. **Responsive / mobile** — per ora la UI è pensata per desktop/LAN
   ufficio, non ho ancora testato su schermi piccoli.

## Cosa NON toccare senza parlarne prima

- `app/fake_pipeline.py` — è il mock che verrà rimpiazzato dal backend
  reale di Alessandro/Francesco; se cambi la forma dei dati che restituisce
  (stati, campi di Job/Prescrizione/Difformita) rischi di disallineare il
  contratto con quello che stanno costruendo loro.
- I nomi degli endpoint (`/jobs/nuovo`, `/jobs`, `/archivio`, `/difformita`)
  — sono quelli definiti nel documento di progetto, il backend li aspetta
  identici.

## Struttura rapida

```
app/
|-- routers/          # una route per area (auth, dashboard, jobs, archivio)
|-- templates/         # un .html per pagina, tutte estendono base.html
|-- static/css/style.css   # design system, un solo file
`-- static/js/             # un file JS per comportamento (wizard, polling)
```

Se qualcosa non torna o vuoi che sistemiamo insieme la divisione dei
prossimi task, scrivimi pure.
