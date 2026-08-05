/* polling-jobs.js
   Nella pagina "Elaborazioni" aggiorna via polling lo stato dei job non
   ancora completati, chiamando GET /jobs/{id}/stato ogni 2 secondi.
   Punto di partenza per una futura sostituzione con WebSocket
   (Sezione 9.2, punto 6 del documento di progetto).
*/
(function () {
  const ETICHETTE_STATO = {
    in_coda: { classe: "badge--in-coda", testo: "In coda" },
    fase1_preprocessing: { classe: "badge--in-corso", testo: "In corso &middot; fase 1 preprocessing" },
    fase2_ocr: { classe: "badge--in-corso", testo: "In corso &middot; fase 2 OCR" },
    fase3_excel: { classe: "badge--in-corso", testo: "In corso &middot; fase 3 scrittura Excel" },
    fase4_difformita: { classe: "badge--in-corso", testo: "In corso &middot; fase 4 difformita" },
    completato: { classe: "badge--completato", testo: "Completata" },
    errore: { classe: "badge--errore", testo: "Errore" },
  };

  function aggiornaRiga(riga, dati) {
    const info = ETICHETTE_STATO[dati.stato] || { classe: "badge--in-coda", testo: dati.stato };
    riga.querySelector(".cella-stato").innerHTML =
      `<span class="badge ${info.classe}"><span class="badge__puntino"></span>${info.testo}</span>`;
    riga.querySelector(".cella-prescrizioni").textContent = dati.numero_prescrizioni;
    riga.querySelector(".cella-difformita").textContent = dati.numero_difformita;
    riga.dataset.jobAttivo = dati.stato === "completato" || dati.stato === "errore" ? "false" : "true";
  }

  async function aggiornaJobAttivi() {
    const righe = Array.from(document.querySelectorAll('tr[data-job-attivo="true"]'));
    if (righe.length === 0) return;

    await Promise.all(
      righe.map(async (riga) => {
        try {
          const risposta = await fetch(`/jobs/${riga.dataset.jobId}/stato`);
          if (!risposta.ok) return;
          const dati = await risposta.json();
          aggiornaRiga(riga, dati);
        } catch (err) {
          console.error("Polling job fallito:", err);
        }
      })
    );
  }

  if (document.getElementById("tabella-jobs")) {
    setInterval(aggiornaJobAttivi, 2000);
  }
})();
