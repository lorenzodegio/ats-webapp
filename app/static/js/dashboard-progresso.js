/* dashboard-progresso.js
   Aggiorna via polling l'avanzamento dei lotti mostrati nel pannello
   "In corso ora" della dashboard. Se un lotto si conclude o va in
   eccezione, ricarica la pagina per aggiornare sia il pannello che la
   panoramica generale sottostante.
*/
(function () {
  async function aggiornaRighe() {
    const righe = Array.from(document.querySelectorAll("#pannello-in-corso .job-attivo-riga"));
    if (righe.length === 0) return;

    let qualcunoConcluso = false;

    await Promise.all(
      righe.map(async (riga) => {
        try {
          const risposta = await fetch(`/lotti/${riga.dataset.lottoId}/stato`);
          if (!risposta.ok) return;
          const dati = await risposta.json();

          const etichettaFase = dati.messaggio_operatore
            ? `${dati.etichetta_stato} · ${dati.messaggio_operatore}`
            : dati.etichetta_stato;
          riga.querySelector(".job-attivo-riga__fase").textContent = etichettaFase;
          riga.querySelector(".barra-progresso__riempimento").style.width = `${dati.percentuale}%`;

          const percentualeEl = document.getElementById(`percentuale-${dati.id}`);
          if (percentualeEl) percentualeEl.textContent = `${dati.percentuale}%`;

          const progressoItemEl = document.getElementById(`progresso-item-${dati.id}`);
          if (progressoItemEl) {
            progressoItemEl.textContent = dati.progresso_item
              ? `${dati.progresso_item.attuale} di ${dati.progresso_item.totale} prescrizioni`
              : "";
          }

          if (["completato", "archiviato", "eccezione"].includes(dati.stato)) {
            qualcunoConcluso = true;
          }
        } catch (err) {
          console.error("Polling dashboard fallito:", err);
        }
      })
    );

    if (qualcunoConcluso) {
      window.location.reload();
    }
  }

  if (document.getElementById("pannello-in-corso")) {
    setInterval(aggiornaRighe, 2500);
  }
})();
