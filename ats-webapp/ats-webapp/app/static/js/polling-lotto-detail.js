/* polling-lotto-detail.js
   Nella pagina di dettaglio di un lotto, aggiorna avanzamento e log via
   polling finche' l'elaborazione automatica in corso non si conclude,
   poi ricarica la pagina per mostrare lo step successivo del workflow.
*/
(function () {
  const pannello = document.getElementById("pannello-avanzamento-dettaglio");
  if (!pannello) return;

  const lottoId = pannello.dataset.lottoId;
  const barra = document.getElementById("barra-progresso-dettaglio");
  const etichetta = document.getElementById("etichetta-fase-dettaglio");
  const console_ = document.getElementById("console-log");

  async function aggiorna() {
    try {
      const risposta = await fetch(`/lotti/${lottoId}/stato`);
      if (!risposta.ok) return;
      const dati = await risposta.json();

      barra.style.width = `${dati.percentuale}%`;
      etichetta.textContent = dati.etichetta_stato;

      console_.innerHTML = dati.log_recenti
        .map((r) => `<div class="console-log__riga console-log__riga--${r.livello}">${r.timestamp} &middot; ${r.messaggio}</div>`)
        .join("");
      console_.scrollTop = console_.scrollHeight;

      if (!dati.fase_attiva) {
        // l'elaborazione automatica si e' conclusa (completata o eccezione)
        window.location.reload();
      }
    } catch (err) {
      console.error("Polling dettaglio lotto fallito:", err);
    }
  }

  setInterval(aggiorna, 2000);
})();
