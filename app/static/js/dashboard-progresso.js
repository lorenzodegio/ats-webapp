/* dashboard-progresso.js
   Aggiorna via polling l'avanzamento delle elaborazioni mostrate nel
   pannello "In corso ora" della dashboard. Se un'elaborazione si
   conclude (completata o in errore), ricarica la pagina per aggiornare
   sia il pannello "in corso" sia la panoramica generale sottostante.
*/
(function () {
  async function aggiornaRighe() {
    const righe = Array.from(document.querySelectorAll("#pannello-in-corso .job-attivo-riga"));
    if (righe.length === 0) return;

    let qualcunoConcluso = false;

    await Promise.all(
      righe.map(async (riga) => {
        try {
          const risposta = await fetch(`/jobs/${riga.dataset.jobId}/stato`);
          if (!risposta.ok) return;
          const dati = await risposta.json();

          riga.querySelector(".job-attivo-riga__fase").textContent = dati.etichetta_fase;
          riga.querySelector(".barra-progresso__riempimento").style.width = `${dati.percentuale}%`;

          if (dati.stato === "completato" || dati.stato === "errore") {
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
