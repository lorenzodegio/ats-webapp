/* polling-job-detail.js
   Nella pagina di dettaglio di una singola elaborazione, aggiorna
   l'avanzamento via polling finche' il job non e' completato o in
   errore, poi ricarica la pagina per mostrare prescrizioni/difformita
   finali e il badge di stato definitivo.
*/
(function () {
  const pannello = document.getElementById("pannello-avanzamento-dettaglio");
  if (!pannello) return;

  const jobId = pannello.dataset.jobId;
  const barra = document.getElementById("barra-progresso-dettaglio");
  const etichetta = document.getElementById("etichetta-fase-dettaglio");

  async function aggiorna() {
    try {
      const risposta = await fetch(`/jobs/${jobId}/stato`);
      if (!risposta.ok) return;
      const dati = await risposta.json();

      barra.style.width = `${dati.percentuale}%`;
      etichetta.textContent = dati.etichetta_fase;

      if (dati.stato === "completato" || dati.stato === "errore") {
        window.location.reload();
      }
    } catch (err) {
      console.error("Polling dettaglio job fallito:", err);
    }
  }

  setInterval(aggiorna, 2000);
})();
