# Esame Prescrizioni — Banco di Lavoro

Applicazione web per caricare in lotto prescrizioni mediche (PDF/immagini),
avviarne il preprocessing, avviarne l'esame automatico e scaricare i
risultati in un foglio Excel.

Il frontend è completamente indipendente dal backend: funziona anche
**senza** un backend reale, usando una simulazione locale, e può essere
collegato a un backend vero semplicemente modificando due righe di
configurazione in `js/api.js`.

## Struttura del progetto

```
esame-prescrizioni/
├── index.html              markup della pagina (nessuno stile o script inline)
├── css/
│   └── style.css            tutti gli stili
├── js/
│   ├── api.js                unico punto di contatto col backend (vero o simulato)
│   ├── ui.js                  solo rendering DOM (nessuna rete, nessuno stato)
│   └── app.js                  stato applicativo, orchestrazione, eventi, export Excel
├── backend-example/
│   ├── server.js               server Node/Express di riferimento (stub)
│   └── package.json
└── README.md
```

Separazione dei ruoli nel frontend:

- **api.js** — comunica con il backend. Espone solo `Api.preprocessFile()`
  ed `Api.examineFile()`. Il resto dell'app non sa (e non deve sapere) se
  dietro c'è un vero server o una simulazione.
- **ui.js** — aggiorna il DOM (liste, stepper, barra di avanzamento,
  tabella risultati). Non fa mai chiamate di rete.
- **app.js** — tiene lo stato (elenco file, fase corrente), collega gli
  eventi dell'interfaccia e coordina `Api` e `UI`.

## Come avviare il frontend

Nessuna build necessaria. Due opzioni:

1. **Apertura diretta** — fai doppio clic su `index.html`.
2. **Server statico locale** (consigliato, evita eventuali limitazioni
   del browser sui file locali):
   ```
   cd esame-prescrizioni
   python3 -m http.server 8080
   # poi apri http://localhost:8080
   ```

Senza un backend configurato, la pagina resta in **modalità demo**:
preprocessing ed esame vengono simulati con dati di esempio chiaramente
etichettati come tali, così puoi vedere l'intero flusso (caricamento →
preprocessing → esame → risultati → export Excel) da subito.

## Come collegare un backend reale

Apri `js/api.js` e valorizza i due URL in cima al file:

```js
const BACKEND_CONFIG = {
  preEndpoint: 'http://localhost:3001/api/preprocess',
  examEndpoint: 'http://localhost:3001/api/examine',
};
```

Da quel momento il frontend userà `fetch()` verso i tuoi endpoint reali
al posto della simulazione, senza bisogno di nessun'altra modifica nel
resto del codice. Il badge in alto a destra passa automaticamente da
"Modalità demo" a "Modalità live".

## Contratto API atteso dal backend

### `POST {preEndpoint}` — preprocessing

- Richiesta: `multipart/form-data` con campo `file`.
- Risposta attesa: qualunque HTTP `2xx` (il corpo non viene letto).
  Un HTTP diverso da 2xx viene trattato come errore per quel file.

### `POST {examEndpoint}` — esame prescrizione

- Richiesta: `multipart/form-data` con campo `file`.
- Risposta attesa: HTTP `2xx` con corpo JSON in questo formato:

```json
{
  "paziente": "Nome Cognome",
  "medico": "Dott. ...",
  "farmaci": "Elenco farmaci / principi attivi",
  "dosaggio": "Posologia",
  "data_prescrizione": "YYYY-MM-DD",
  "stato": "valida",
  "note": "Eventuali note"
}
```

- `stato` deve valere `valida`, `da_verificare` oppure `incompleta`
  (usati per colorare lo stato in tabella ed Excel).
- Qualunque campo mancante viene mostrato come `N/D`: puoi quindi
  restituire solo i campi che il tuo backend è in grado di popolare.

## backend-example/

Un piccolo server Node/Express che implementa esattamente le due route
sopra, restituendo dati segnaposto — utile come punto di partenza per
il backend reale, non come implementazione finale.

```
cd backend-example
npm install
npm start
```

Il server resta in ascolto su `http://localhost:3001`. I due `TODO` nel
file `server.js` indicano dove inserire la vera logica di preprocessing
e di OCR/estrazione campi.

## Export Excel

Il pulsante "Scarica Excel" genera un vero file `.xlsx` (libreria
[SheetJS](https://sheetjs.com/), caricata da CDN) con una riga per ogni
prescrizione esaminata con successo.
