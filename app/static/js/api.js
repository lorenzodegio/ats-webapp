/**
 * api.js — Livello di comunicazione con il backend.
 *
 * Questo modulo è l'UNICO punto di contatto tra il frontend e il backend.
 * Il resto dell'app chiama solo Api.preprocessFile() e Api.examineFile(),
 * senza sapere se dietro c'è un vero server o una simulazione: la scelta
 * avviene qui dentro, in base al fatto che siano stati configurati o meno
 * degli endpoint reali.
 *
 * - Endpoint NON configurati  -> viene usato il "backend simulato" (MOCK)
 *   in fondo al file: utile per sviluppare e mostrare il frontend anche
 *   senza un backend reale.
 * - Endpoint configurati (dalle impostazioni avanzate della pagina)
 *   -> le stesse funzioni chiamano fetch() verso i tuoi URL reali.
 *
 * Quando avrai un backend reale, valorizza i due URL qui sotto in
 * BACKEND_CONFIG: la sezione MOCK potrà essere rimossa senza toccare
 * nient'altro nel resto del codice — è questo il punto della
 * separazione frontend/backend.
 */

/**
 * Configurazione del backend.
 * Lasciali vuoti per restare in modalità demo (simulazione locale).
 * Esempio una volta pronto il backend:
 *   preEndpoint:  'http://localhost:3001/api/preprocess',
 *   examEndpoint: 'http://localhost:3001/api/examine',
 */
const BACKEND_CONFIG = {
  preEndpoint: '',
  examEndpoint: '',
};

const Api = (function () {
  const config = {
    preEndpoint: (BACKEND_CONFIG.preEndpoint || '').trim(),
    examEndpoint: (BACKEND_CONFIG.examEndpoint || '').trim(),
  };

  function isLive() {
    return Boolean(config.preEndpoint || config.examEndpoint);
  }

  /* ============================================================
     CONTRATTO PUBBLICO — le uniche due funzioni usate da app.js
  ============================================================ */

  // Restituisce una Promise che si risolve quando il preprocessing
  // è completato, oppure viene rifiutata con un Error in caso di
  // problemi. onProgress(label) è opzionale e serve solo per la UI.
  async function preprocessFile(file, onProgress) {
    if (config.preEndpoint) {
      onProgress && onProgress('invio a endpoint…');
      await postFile(config.preEndpoint, file);
      return;
    }
    return mockPreprocess(file, onProgress);
  }

  // Restituisce una Promise che si risolve con l'oggetto risultato
  // normalizzato (vedi normalizeResult), oppure viene rifiutata con
  // un Error in caso di problemi.
  async function examineFile(file, onProgress) {
    let data;
    if (config.examEndpoint) {
      onProgress && onProgress('invio a endpoint…');
      data = await postFile(config.examEndpoint, file);
    } else {
      data = await mockExamine(file, onProgress);
    }
    return normalizeResult(data);
  }

  /* ============================================================
     CHIAMATA HTTP REALE
  ============================================================ */

  async function postFile(endpoint, file) {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch(endpoint, { method: 'POST', body: fd });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json().catch(() => ({}));
  }

  /*
   * Contratto JSON atteso dalla risposta dell'endpoint di ESAME
   * (vedi anche README.md e backend-example/server.js):
   *
   * {
   *   "paziente":          "Nome Cognome",
   *   "medico":            "Dott. ...",
   *   "farmaci":           "Elenco farmaci / principi attivi",
   *   "dosaggio":          "Posologia",
   *   "data_prescrizione": "YYYY-MM-DD",
   *   "stato":             "valida" | "da_verificare" | "incompleta",
   *   "note":              "Eventuali note"
   * }
   *
   * L'endpoint di PREPROCESSING non deve restituire un formato
   * particolare: è sufficiente una risposta HTTP 2xx per considerare
   * il file pronto per la fase di esame.
   */
  function normalizeResult(data) {
    data = data || {};
    const statoMap = { valida: 'Valida', da_verificare: 'Da verificare', incompleta: 'Dati incompleti' };
    return {
      paziente: data.paziente || 'N/D',
      medico: data.medico || 'N/D',
      farmaci: data.farmaci || 'N/D',
      dosaggio: data.dosaggio || 'N/D',
      data_prescrizione: data.data_prescrizione || 'N/D',
      stato: statoMap[data.stato] || data.stato || 'N/D',
      note: data.note || '',
    };
  }

  /* ============================================================
     BACKEND SIMULATO (MOCK)
     Usato solo in assenza di endpoint reali configurati.
     Da rimuovere quando il backend vero sarà collegato in modo
     stabile: preprocessFile()/examineFile() continueranno a
     funzionare senza bisogno di altre modifiche nel resto dell'app.
  ============================================================ */

  const PREPROCESS_STEPS = ['Verifica formato', 'Controllo integrità', 'Ottimizzazione immagine', 'Normalizzazione contrasto'];
  const EXAMINE_STEPS = ['Estrazione testo (OCR)', 'Identificazione campi', 'Riconoscimento farmaci', 'Validazione dati'];

  function wait(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

  async function mockPreprocess(file, onProgress) {
    for (const step of PREPROCESS_STEPS) {
      onProgress && onProgress(step);
      await wait(280 + Math.random() * 260);
    }
    if (Math.random() < 0.07) {
      throw new Error('file danneggiato o formato non leggibile');
    }
  }

  async function mockExamine(file, onProgress) {
    for (const step of EXAMINE_STEPS) {
      onProgress && onProgress(step);
      await wait(300 + Math.random() * 280);
    }

    const farmaci = ['Principio attivo A 20 mg', 'Principio attivo B 500 mg', 'Principio attivo C 10 mg', 'Principio attivo D 40 mg'];
    const dosaggi = ['1 cpr/die', '2 volte al giorno', '1 cpr ogni 12h', '1/2 cpr al mattino'];
    const roll = Math.random();
    let stato, note;
    if (roll < 0.68) { stato = 'valida'; note = ''; }
    else if (roll < 0.90) { stato = 'da_verificare'; note = 'Firma del medico non chiaramente leggibile.'; }
    else { stato = 'incompleta'; note = 'Campo dosaggio non leggibile — verifica manuale necessaria.'; }

    const d = new Date(Date.now() - Math.floor(Math.random() * 20) * 86400000);

    return {
      paziente: 'Paziente di esempio ' + (Math.floor(Math.random() * 900) + 100),
      medico: 'Dott. Esempio',
      farmaci: farmaci[Math.floor(Math.random() * farmaci.length)],
      dosaggio: dosaggi[Math.floor(Math.random() * dosaggi.length)],
      data_prescrizione: d.toLocaleDateString('it-IT'),
      stato,
      note,
    };
  }

  return { isLive, preprocessFile, examineFile };
})();
