# Checklist test pre-consegna

Compilare una riga per tester. Spuntare `[x]` solo dopo verifica personale.

**Tester:** _______________  
**Data:** _______________  
**Branch / commit:** _______________  
**Macchina:** [ ] ATS GPU (in locale)  [ ] browser da PC proprio su http://10.1.20.20:8000/  [ ] altro: _______________  
**Browser:** _______________

---

## A. Avvio ambiente

- [ ] Rete `ats-pipeline_default` esiste
- [ ] `docker compose -f docker-compose.ollama.yml up -d` e `ollama list` mostra i VL attesi
- [ ] Modelli in `docker-compose.yml` (fase2/fase3) allineati ai pull
- [ ] `python seed_admin.py` (o DB già seedato)
- [ ] uvicorn risponde su porta 8000 (`--host 0.0.0.0`)
- [ ] Da PC proprio: `http://10.1.20.20:8000/` apre il login (senza sedersi sulla ATS)
- [ ] Login admin funziona
- [ ] Log container in fuso `Europe/Rome` (dopo rebuild, non sul job vecchio)

Note:

---

## B. Autenticazione e ruoli

- [ ] Password errata: messaggio, niente accesso
- [ ] Sidebar in fondo: nome + ruolo visibili
- [ ] Click sul blocco utente: compare “Esci e cambia utente”
- [ ] Logout porta a login; secondo utente (operatore/revisore) entra
- [ ] Operatore **non** vede Impostazioni
- [ ] Admin vede Impostazioni

Note:

---

## C. Navigazione a vuoto (DB senza lotti o dopo seed pulito)

- [ ] Dashboard: stato vuoto “nessun lotto in lavorazione” + link nuovo
- [ ] Lotti recenti: vuoto o elenco coerente
- [ ] Archivio: vuoto o ricerca senza crash
- [ ] Nuovo lotto: wizard 6 step visibili, step 4–6 bloccati prima della creazione

Note:

---

## D. Creazione lotto (senza OCR lungo, se serve un PDF piccolo)

- [ ] Submit senza nome: errore
- [ ] File non PDF: errore
- [ ] PDF valido: lotto creato, redirect/dettaglio, preprocessing parte
- [ ] Excel opzionale omesso: lotto parte comunque
- [ ] Excel `.xlsx` allegato: file tracciato

Note:

---

## E. Vincolo “un Docker alla volta”

- [ ] Con un lotto in preprocessing/OCR/difformità Docker: secondo “Nuovo lotto” mostra errore *elaborazione Docker già in corso*
- [ ] Con lotto solo in revisione barcode: si può creare un altro lotto (se questa è la policy accettata)
- [ ] (Opzionale) Durante `completa`/Excel: annotare se il secondo avvio è permesso — edge case

Note:

---

## F. Preprocessing e revisione barcode

- [ ] Log/progresso si aggiornano (polling)
- [ ] Pagine/PDF prescrizione apribili
- [ ] Barcode undefined: assegnazione
- [ ] Esclusione barcode
- [ ] Avvio OCR bloccato se restano undefined
- [ ] Avvio OCR ok quando tutti i barcode sono gestiti
- [ ] Pausa / riprendi / annulla (se si testa in questa fase)

Note:

---

## G. OCR (macchina ATS, PDF reali)

- [ ] Ollama `POST /api/chat` 200 in log
- [ ] Gruppo critico (modello pesante) poi resto (leggero)
- [ ] Completamento ricetta `[i/N]` fino alla fine
- [ ] Nessun hang > 30 min senza log su una singola chiamata (annotare tempi)
- [ ] Pausa/riprendi durante OCR
- [ ] Annulla: lotto non resta “in corso” a vuoto
- [ ] Eccezione artificiale (Ollama spento): stato eccezione + Riprova riparte la fase
- [ ] Mapping campi OCR visibili in revisione qualità
- [ ] PDF originale da tabella

Note:

---

## H. Difformità e chiusura

- [ ] Avvio analisi difformità da revisione qualità
- [ ] Elenco difformità; conferma / scarta (azioni UI)
- [ ] Completa lotto con tutte le difformità gestite
- [ ] Completa fallisce in modo chiaro se Excel non si scrive
- [ ] Download Excel output
- [ ] Export ZIP (lotti completati/archiviati)
- [ ] ZIP organizzato per farmacia (verificare contenuto)
- [ ] Archivia lotto

Note:

---

## I. Dashboard, elenco, archivio

- [ ] Lotto in corso compare in Dashboard con percentuale
- [ ] Filtri dashboard anno/mese/stato
- [ ] Click riga Lotti recenti → dettaglio
- [ ] Archivio raggruppato per lotto
- [ ] Ricerca barcode
- [ ] Ricerca nome lotto
- [ ] Ricerca farmacia

Note:

---

## J. Impostazioni farmacie (admin)

- [ ] Aggiungi farmacia
- [ ] Modifica
- [ ] Disattiva / riattiva
- [ ] Elimina (conferma)
- [ ] Export CSV
- [ ] Tab Sistema visibile, non editabile da UI
- [ ] `dati/farmacie.json` aggiornato all’avvio pipeline (log o file)

Note:

---

## K. Sicurezza minima (non pentest)

- [ ] Pagine interne senza cookie → redirect `/login`
- [ ] Impostazioni da utente non admin → nessun accesso utile
- [ ] Logout invalida la sessione (back del browser non resta autenticato in modo utile)

Note:

---

## L. Consegna demo ATS (smoke)

- [ ] Login con account che daremo ad ATS (password non di default se possibile)
- [ ] Un lotto già completato da mostrare (senza aspettare 2 ore)
- [ ] Un lotto “in corso” o video/log se i tempi OCR sono lunghi
- [ ] Mostrare logout e cambio utente
- [ ] Mostrare Impostazioni farmacie
- [ ] Nessun CF reale su proiettore / foto

Note:

---

## Esito

- [ ] **GO** consegna
- [ ] **GO con riserve** (elenco sotto)
- [ ] **NO-GO**

Riserve / bug aperti:

| ID | Gravità | Pagina | Descrizione | Workaround |
|----|---------|--------|-------------|------------|
|    |         |        |             |            |
